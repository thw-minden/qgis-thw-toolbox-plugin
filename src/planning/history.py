"""Rückgängig / Wiederherstellen für die Objektplanung (Strg+Z / Strg+Y).

Jede Änderung über ``PlanningLayers`` (hinzufügen, löschen, Geometrie bzw. Attribute ändern) wird
mitgeschrieben, ebenso die der taktischen Zeichen (``layer.marker_history``, als eigener Schreiber
registriert). Was in einem Durchlauf der Ereignisschleife geschieht, ist ein Schritt – ein
Verschieben, Duplizieren oder eine Koordinaten-Eingabe wird also als Ganzes rückgängig gemacht,
auch wenn es mehrere Layer betrifft.

Gelöschte Objekte kommen mit ihrer alten ID zurück (Primärschlüssel ``fid`` des GeoPackages), damit
spätere Schritte, die sich auf sie beziehen, weiter passen. Layer im QGIS-Bearbeitungsmodus haben
ihr eigenes Rückgängig; Änderungen dort werden hier nicht mitgeschrieben.
"""

from dataclasses import dataclass, field

from qgis.core import QgsFeature, QgsFeatureRequest, QgsGeometry
from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal

# So viele Schritte bleiben zum Rückgängigmachen
MAX_STEPS = 100


def _copy(feature: QgsFeature) -> QgsFeature:
    """Kopie mit gesetztem ``fid``-Attribut: so legt das GeoPackage es unter derselben ID wieder an."""
    copy = QgsFeature(feature)
    if copy.fields().indexFromName("fid") >= 0:
        copy.setAttribute("fid", feature.id())
    return copy


@dataclass
class _Change:
    """Vorher/Nachher einer Änderung: je Feature-ID Geometrie (None = unverändert) und geänderte Attribute."""

    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)


@dataclass
class _Op:
    role: str
    kind: str  # add | delete | change
    features: list = field(default_factory=list)
    change: _Change | None = None


class PlanningHistory(QObject):
    """``changed`` meldet, dass sich verfügbare Schritte geändert haben (für Knöpfe und Hinweise)."""

    changed = pyqtSignal()

    def __init__(self, layers, parent=None):
        super().__init__(parent)
        self._layers = layers
        self._undo: list[list[_Op]] = []
        self._redo: list[list[_Op]] = []
        self._open: list[_Op] | None = None
        self._replaying = False
        # Eigene Schreiber je Rolle (taktische Zeichen); alle übrigen schreiben über PlanningLayers
        self._writers: dict = {}

    def register_writer(self, role: str, writer) -> None:
        """``writer`` mit ``add(features)``, ``delete(fids)``, ``change(geometries, attributes)`` für ``role``."""
        self._writers[role] = writer

    # -- Mitschreiben (aus PlanningLayers) ----------------------------------

    @property
    def recording(self) -> bool:
        return not self._replaying

    def _step(self) -> list[_Op]:
        if self._open is None:
            self._open = []
            # Alles bis zum Ende dieses Durchlaufs gehört zum selben Schritt
            QTimer.singleShot(0, self._close)
        return self._open

    def _close(self):
        step, self._open = self._open, None
        if not step:
            return
        self._undo.append(step)
        del self._undo[:-MAX_STEPS]
        self._redo.clear()
        self.changed.emit()

    def record_add(self, role: str, features: list[QgsFeature]):
        if self.recording and features:
            self._step().append(_Op(role, "add", [_copy(f) for f in features]))

    def record_delete(self, role: str, features: list[QgsFeature]):
        if self.recording and features:
            self._step().append(_Op(role, "delete", [_copy(f) for f in features]))

    def record_change(self, role: str, layer, geometries: dict, attributes: dict):
        """Vor dem Schreiben aufrufen: merkt sich den bisherigen Stand der betroffenen Features."""
        if not self.recording or not (geometries or attributes):
            return
        change = _Change()
        # Der alte Stand steht im Datenprovider – im Bearbeitungsmodus zeigt der Layer schon den neuen
        fids = [fid for fid in set(geometries) | set(attributes) if fid >= 0]
        old_features = {f.id(): f for f in layer.dataProvider().getFeatures(QgsFeatureRequest().setFilterFids(fids))}
        for fid in fids:
            old = old_features.get(fid)
            if old is None:
                continue
            names = list(attributes.get(fid, {}))
            change.before[fid] = (
                QgsGeometry(old.geometry()) if fid in geometries else None,
                {name: old.attribute(name) for name in names if old.fields().indexFromName(name) >= 0},
            )
            change.after[fid] = (
                QgsGeometry(geometries[fid]) if fid in geometries else None,
                dict(attributes.get(fid, {})),
            )
        if change.before:
            self._step().append(_Op(role, "change", change=change))

    # -- Rückgängig / Wiederherstellen -------------------------------------

    def can_undo(self) -> bool:
        return bool(self._undo) or bool(self._open)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def counts(self) -> tuple[int, int]:
        return len(self._undo), len(self._redo)

    def undo(self) -> bool:
        self._close()
        if not self._undo:
            return False
        step = self._undo.pop()
        self._replay(step, reverse=True)
        self._redo.append(step)
        self.changed.emit()
        return True

    def redo(self) -> bool:
        self._close()
        if not self._redo:
            return False
        step = self._redo.pop()
        self._replay(step, reverse=False)
        self._undo.append(step)
        self.changed.emit()
        return True

    def clear(self):
        self._open = None
        self._undo.clear()
        self._redo.clear()
        self.changed.emit()

    def _replay(self, step: list[_Op], reverse: bool):
        self._replaying = True
        try:
            for op in reversed(step) if reverse else step:
                self._apply(op, reverse)
        finally:
            self._replaying = False

    def _apply(self, op: _Op, reverse: bool):
        layers = self._writers.get(op.role) or _PlanningWriter(self._layers, op.role)
        # Rückgängig eines Hinzufügens = Löschen und umgekehrt
        if op.kind in ("add", "delete"):
            remove = (op.kind == "add") == reverse
            if remove:
                layers.delete([f.id() for f in op.features])
            else:
                layers.add([QgsFeature(f) for f in op.features])
            return
        state = op.change.before if reverse else op.change.after
        geometries = {fid: QgsGeometry(geom) for fid, (geom, _attrs) in state.items() if geom is not None}
        attributes = {fid: dict(attrs) for fid, (_geom, attrs) in state.items() if attrs}
        layers.change(geometries, attributes)


class _PlanningWriter:
    """Schreiber für die Objektplanungs-Layer einer Rolle (über ``PlanningLayers``)."""

    def __init__(self, layers, role: str):
        self._layers = layers
        self._role = role

    def add(self, features):
        self._layers.add_features(self._role, features)

    def delete(self, fids):
        self._layers.delete_features(self._role, fids)

    def change(self, geometries, attributes):
        self._layers.change_features(self._role, geometries, attributes)
