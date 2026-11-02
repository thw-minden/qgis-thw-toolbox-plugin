"""Taktische Zeichen in der Rückgängig-Historie der Objektplanung (Strg+Z / Strg+Y).

Die Zeichen werden an vielen Stellen geschrieben (Verschieben-Werkzeug, Details, Drehen, Skalieren,
Beschriftung, Löschen). Die meisten gehen über den Bearbeitungspuffer des Layers: Kurz vor dem Speichern
(``beforeCommitChanges``) stehen dort die neuen Werte, im Datenprovider noch die alten – so wird
jede dieser Änderungen mitgeschrieben, ohne die einzelnen Stellen anzufassen. Neu angelegte Features
meldet ``committedFeaturesAdded`` mit ihrer endgültigen ID.

Drei Wege schreiben am Puffer vorbei direkt in den Provider (Setzen, Duplizieren, Verschieben einer
gemischten Auswahl); sie melden sich über ``record_added`` bzw. ``record_moved``.
"""

from collections.abc import Callable

from qgis.core import QgsFeature, QgsFeatureRequest, QgsVectorLayer
from qgis.PyQt.QtCore import QObject


class MarkerHistory(QObject):
    def __init__(self, history, role: str, on_changed: Callable[[list[int]], None], parent=None):
        super().__init__(parent)
        self._history = history
        self._role = role
        # Nach Rückgängig/Wiederherstellen: Renderer (ein Symbol je Zeichen) und Ansichten auffrischen
        self._on_changed = on_changed
        self._layer: QgsVectorLayer | None = None
        history.register_writer(role, self)

    # -- Layer --------------------------------------------------------------

    def attach(self, layer: QgsVectorLayer | None):
        """Dem (neuen) Marker-Layer folgen; nach einem Austausch passen alte Feature-IDs nicht mehr."""
        if layer is self._layer:
            return
        self.detach()
        self._layer = layer
        if layer is None:
            return
        layer.beforeCommitChanges.connect(self._before_commit)
        layer.committedFeaturesAdded.connect(self._committed_added)

    def detach(self):
        layer, self._layer = self._layer, None
        if layer is None:
            return
        # Nach einem Projektwechsel ist der alte Layer schon gelöscht – schon der Zugriff auf sein Signal wirft
        for signal, slot in (
            ("beforeCommitChanges", self._before_commit),
            ("committedFeaturesAdded", self._committed_added),
        ):
            try:
                getattr(layer, signal).disconnect(slot)
            except (TypeError, RuntimeError):
                pass

    # -- Mitschreiben --------------------------------------------------------

    def _before_commit(self, *_args):
        layer = self._layer
        if layer is None or not self._history.recording:
            return
        buffer = layer.editBuffer()
        if buffer is None:
            return
        fields = layer.fields()
        # Nur bestehende Features (neue haben im Puffer negative IDs, sie meldet _committed_added)
        geometries = {fid: geom for fid, geom in buffer.changedGeometries().items() if fid >= 0}
        attributes = {
            fid: {fields.at(idx).name(): value for idx, value in values.items()}
            for fid, values in buffer.changedAttributeValues().items()
            if fid >= 0
        }
        deleted = [fid for fid in buffer.deletedFeatureIds() if fid >= 0]
        self._history.record_change(self._role, layer, geometries, attributes)
        if deleted:
            self._history.record_delete(self._role, self._provider_features(deleted))

    def _committed_added(self, _layer_id, features):
        if self._history.recording:
            self._history.record_add(self._role, list(features))

    def record_added(self, fids):
        """Am Puffer vorbei angelegte Zeichen (Setzen, Duplizieren) – nach dem Schreiben aufrufen."""
        if self._layer is not None and fids and self._history.recording:
            self._history.record_add(self._role, self._provider_features(fids))

    def record_moved(self, geometries: dict):
        """Am Puffer vorbei verschobene Zeichen (gemischte Auswahl) – vor dem Schreiben aufrufen."""
        if self._layer is not None and geometries:
            self._history.record_change(self._role, self._layer, geometries, {})

    def _provider_features(self, fids) -> list[QgsFeature]:
        request = QgsFeatureRequest().setFilterFids(list(fids))
        return list(self._layer.dataProvider().getFeatures(request))

    # -- Rückgängig / Wiederherstellen (Schreiber der Historie) -------------

    def add(self, features):
        provider = self._provider()
        if provider is not None:
            provider.addFeatures(features)
            self._done([f.id() for f in features])

    def delete(self, fids):
        provider = self._provider()
        if provider is not None:
            provider.deleteFeatures(list(fids))
            self._done(list(fids))

    def change(self, geometries, attributes):
        provider = self._provider()
        if provider is None:
            return
        if geometries:
            provider.changeGeometryValues(geometries)
        if attributes:
            fields = self._layer.fields()
            provider.changeAttributeValues(
                {
                    fid: {fields.indexOf(name): value for name, value in values.items() if fields.indexOf(name) >= 0}
                    for fid, values in attributes.items()
                }
            )
        self._done(list(set(geometries) | set(attributes)))

    def _provider(self):
        layer = self._layer
        if layer is None:
            return None
        # Rückgängig gilt nur ohne Bearbeitungsmodus (siehe UndoShortcutFilter) – sicherheitshalber übernehmen
        if layer.isEditable():
            layer.commitChanges()
        return layer.dataProvider()

    def _done(self, fids):
        self._layer.updateExtents()
        self._layer.triggerRepaint()
        self._on_changed(fids)
