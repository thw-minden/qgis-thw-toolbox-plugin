"""Details zur Auswahl: ein oder mehrere Objekte der Objektplanung, auch gemischt mit taktischen Zeichen.

Liegt als Seite im Details-Dock (``ui.feature_dock.FeatureDock``). Ein einzelnes
taktisches Zeichen zeigt das Dock mit seinen eigenen Einstellungen.
"""

import os
from collections import Counter

from qgis.core import QgsCoordinateTransform, QgsProject
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..ui.theme import muted_style
from ..util.coordinates import WGS84, to_mgrs
from .catalog import fmt_m
from .geometry import MetricFrame
from .layers import (
    FOOTPRINT_ROLES,
    ROLE_ARROWS,
    ROLE_CABLES,
    ROLE_DISTRIBUTORS,
    ROLE_GENERATORS,
    ROLE_LIGHTS,
    ROLE_MARKERS,
    ROLE_TENTS,
    ROLE_VEHICLES,
    ROLE_ZONES,
)

ROLE_TITLES = {
    ROLE_TENTS: "Zelt",
    ROLE_VEHICLES: "Fahrzeug",
    ROLE_CABLES: "Leitung",
    ROLE_DISTRIBUTORS: "Verteiler",
    ROLE_GENERATORS: "Stromerzeuger",
    ROLE_LIGHTS: "Leuchte",
    ROLE_ZONES: "Gebiet",
    ROLE_ARROWS: "Pfeil",
    ROLE_MARKERS: "Taktisches Zeichen",
}
_LABEL_HINT = "z.B. Zelt 1, Küche, NEA Nord"


def _num(feature, field: str) -> float:
    try:
        return float(feature.attribute(field) or 0)
    except (KeyError, TypeError, ValueError):
        return 0.0


def _area(m2: float) -> str:
    return f"{fmt_m(round(m2 / 10000, 2))} ha" if m2 >= 10000 else f"{fmt_m(round(m2))} m²"


def _watts(w: float) -> str:
    return f"{fmt_m(round(w / 1000, 1))} kW" if w >= 1000 else f"{fmt_m(round(w))} W"


def _metric(feature, crs):
    geom = feature.geometry()
    return MetricFrame(crs, geom.centroid().asPoint()).geom_to_m(geom)


def type_name(role: str, feature) -> str:
    """Typ des Objekts, z. B. „Zelt SG 300“; bei Zeichen der Name des Symbols."""
    if role == ROLE_MARKERS:
        path = feature.attribute("svg_path") or ""
        return os.path.splitext(os.path.basename(path))[0].replace("_", " ") or ROLE_TITLES[role]
    return feature.attribute("typ") or ROLE_TITLES.get(role, role)


def item_name(role: str, feature) -> str:
    """Bezeichnung des Objekts (bei Zeichen die Beschriftung), sonst leer."""
    try:
        return (feature.attribute("label" if role == ROLE_MARKERS else "bezeichnung") or "").strip()
    except KeyError:
        return ""


def _measure(role: str, feature, crs) -> float | None:
    """Kennzahl zum Aufsummieren: Fläche, Länge oder Leistung."""
    if role in FOOTPRINT_ROLES:
        return _num(feature, "laenge") * _num(feature, "breite")
    if role == ROLE_CABLES:
        return _num(feature, "laenge")
    if role == ROLE_GENERATORS:
        return _num(feature, "leistung_kva")
    if role == ROLE_LIGHTS:
        return _num(feature, "leistung_w")
    if role in (ROLE_ZONES, ROLE_ARROWS) and feature.hasGeometry():
        geom = _metric(feature, crs)
        return geom.area() if role == ROLE_ZONES else geom.length()
    return None


_MEASURE_FORMATS = {
    ROLE_TENTS: _area,
    ROLE_VEHICLES: _area,
    ROLE_ZONES: _area,
    ROLE_CABLES: lambda v: f"{fmt_m(round(v, 1))} m",
    ROLE_ARROWS: lambda v: f"{fmt_m(round(v))} m",
    ROLE_GENERATORS: lambda v: f"{fmt_m(v)} kVA",
    ROLE_LIGHTS: _watts,
}


def measure_text(role: str, features, crs) -> str:
    """Summe der Kennzahl über ``features``, z. B. „60 m²“ für zwei Zelte."""
    values = [v for f in features if (v := _measure(role, f, crs)) is not None]
    fmt = _MEASURE_FORMATS.get(role)
    return fmt(sum(values)) if values and fmt else ""


def properties(role: str, feature, crs) -> list[tuple[str, str]]:
    """Angaben zu einem einzelnen Objekt."""
    rows = []
    if role in FOOTPRINT_ROLES:
        rows.append(("Maße", f"{fmt_m(_num(feature, 'laenge'))} × {fmt_m(_num(feature, 'breite'))} m"))
        if role == ROLE_TENTS:
            rows.append(("Abspannung", f"{fmt_m(_num(feature, 'abspannung'))} m"))
        rows.append(("Grundfläche", measure_text(role, [feature], crs)))
    elif role == ROLE_CABLES:
        drum = _num(feature, "max_laenge")
        length = measure_text(role, [feature], crs)
        rows.append(("Länge", f"{length} von {fmt_m(drum)} m" if drum else length))
    elif role == ROLE_GENERATORS:
        rows.append(("Leistung", measure_text(role, [feature], crs)))
    elif role == ROLE_LIGHTS:
        rows.append(("Leistung", measure_text(role, [feature], crs)))
        rows.append(("Ausleuchtung", f"{fmt_m(_num(feature, 'radius'))} m Radius"))
    elif role == ROLE_ZONES and feature.hasGeometry():
        geom = _metric(feature, crs)
        rows.append(("Fläche", _area(geom.area())))
        rows.append(("Umfang", f"{fmt_m(round(geom.constGet().perimeter()))} m"))
    elif role == ROLE_ARROWS:
        rows.append(("Länge", measure_text(role, [feature], crs)))
    if feature.hasGeometry():
        rows.append(("Position", _utmref(feature, crs)))
    return rows


def _utmref(feature, crs) -> str:
    try:
        center = feature.geometry().centroid().asPoint()
        wgs = QgsCoordinateTransform(crs, WGS84, QgsProject.instance()).transform(center)
        return to_mgrs(wgs.y(), wgs.x()) or "–"
    except Exception:
        return "–"


class SelectionDetails(QWidget):
    """Objektseite der Details: Angaben, Bezeichnung und Aktionen zur Auswahl.

    Bei mehreren Objekten listet sie die Auswahl nach Typ gruppiert; ein Klick
    auf einen Eintrag wählt nur dieses Objekt aus.
    """

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._rotation_value = 0.0
        layout = QVBoxLayout(self)

        self._title = QLabel()
        self._title.setWordWrap(True)
        font = self._title.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.25)
        self._title.setFont(font)
        layout.addWidget(self._title)
        self._subtitle = QLabel()
        self._subtitle.setWordWrap(True)
        self._subtitle.setStyleSheet(muted_style())
        layout.addWidget(self._subtitle)

        form = QFormLayout()
        self._label_edit = QLineEdit()
        self._label_edit.editingFinished.connect(self._on_label_edited)
        form.addRow("Bezeichnung:", self._label_edit)
        self._rotation = QDoubleSpinBox()
        self._rotation.setRange(0.0, 359.0)
        self._rotation.setDecimals(0)
        self._rotation.setSingleStep(15.0)
        self._rotation.setSuffix(" °")
        self._rotation.setWrapping(True)
        # Pfeile drehen sofort, getippte Werte erst mit Enter bzw. beim Verlassen des Felds
        self._rotation.setKeyboardTracking(False)
        self._rotation.valueChanged.connect(self._on_rotation_edited)
        form.addRow("Drehung:", self._rotation)
        self._rotation_label = form.labelForField(self._rotation)
        layout.addLayout(form)

        # Angaben zum einzelnen Objekt – werden bei jeder Aktualisierung neu aufgebaut
        self._props = QFormLayout()
        layout.addLayout(self._props)

        self._list = QTreeWidget()
        self._list.setHeaderLabels(["Objekt", "Angabe"])
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list, 1)
        self._list_hint = QLabel("Klick auf einen Eintrag wählt nur dieses Objekt aus.")
        self._list_hint.setWordWrap(True)
        self._list_hint.setStyleSheet(muted_style())
        layout.addWidget(self._list_hint)

        row = QHBoxLayout()
        self._duplicate = QPushButton("Duplizieren")
        self._duplicate.setToolTip("Duplizieren (D, Alt+Ziehen)")
        self._duplicate.clicked.connect(lambda: controller.duplicate_selection())
        self._rotate = QPushButton("Drehen 15°")
        self._rotate.setToolTip("Zelte und Fahrzeuge um 15° drehen (R, Shift+R zurück)")
        self._rotate.clicked.connect(lambda: controller.rotate_selection(15))
        self._delete = QPushButton("Löschen")
        self._delete.setToolTip("Löschen (Entf)")
        self._delete.clicked.connect(lambda: controller.delete_selection())
        for button in (self._duplicate, self._rotate, self._delete):
            row.addWidget(button)
        layout.addLayout(row)
        layout.addStretch()

        # Eine Auswahl feuert je Layer ein Signal: gesammelt einmal aktualisieren
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._refresh_if_shown)
        controller.layers.selection_changed.connect(self._timer.start)
        controller.layers.changed.connect(self._timer.start)

    def _refresh_if_shown(self):
        if self.isVisible():
            self.refresh()

    def refresh(self):
        items = self.controller.selection()
        if not items:
            return
        planning = [(role, f) for role, f in items if role != ROLE_MARKERS]
        single = items[0] if len(items) == 1 else None
        footprint = single is not None and single[0] in FOOTPRINT_ROLES

        if single is not None:
            role, feature = single
            name = item_name(role, feature)
            self._title.setText(name or type_name(role, feature))
            self._subtitle.setText(type_name(role, feature) if name else ROLE_TITLES.get(role, ""))
        else:
            counts = Counter(type_name(role, f) for role, f in items)
            self._title.setText(f"{len(items)} ausgewählt")
            self._subtitle.setText(", ".join(f"{n}× {name}" if n > 1 else name for name, n in counts.most_common()))

        self._show_label(planning)
        self._rotation.setVisible(footprint)
        self._rotation_label.setVisible(footprint)
        if footprint:
            self._rotation_value = _num(single[1], "rotation") % 360.0
            self._rotation.blockSignals(True)
            self._rotation.setValue(round(self._rotation_value))
            self._rotation.blockSignals(False)

        while self._props.rowCount():
            self._props.removeRow(0)
        if single is not None and single[0] != ROLE_MARKERS:
            role, feature = single
            for label, value in properties(role, feature, self._crs(role)):
                text = QLabel(value)
                text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                self._props.addRow(f"{label}:", text)

        self._fill_list(items if single is None else [])
        self._rotate.setEnabled(any(role in FOOTPRINT_ROLES for role, _f in items))

    def _crs(self, role: str):
        layer = self.controller.layer(role)
        return layer.crs() if layer is not None else QgsProject.instance().crs()

    def _show_label(self, planning):
        """Bezeichnung gilt nur für Objekte der Objektplanung; Zeichen haben ihre Beschriftung."""
        self._label_edit.setEnabled(bool(planning))
        labels = {item_name(role, f) for role, f in planning}
        uniform = len(labels) <= 1
        text = next(iter(labels)) if len(labels) == 1 else ""
        # Nur bei Änderung setzen: setText hebt sonst die Markierung von focus_label auf
        if self._label_edit.text() != text:
            self._label_edit.blockSignals(True)
            self._label_edit.setText(text)
            self._label_edit.setModified(False)
            self._label_edit.blockSignals(False)
        if not planning:
            self._label_edit.setPlaceholderText("Nur für Objekte der Objektplanung")
        else:
            self._label_edit.setPlaceholderText(_LABEL_HINT if uniform else "unterschiedlich – Eingabe gilt für alle")

    def _fill_list(self, items):
        self._list.clear()
        self._list.setVisible(bool(items))
        self._list_hint.setVisible(bool(items))
        groups: dict[tuple[str, str], list] = {}
        for role, feature in items:
            groups.setdefault((role, type_name(role, feature)), []).append(feature)
        for (role, name), features in groups.items():
            crs = self._crs(role)
            parent = QTreeWidgetItem([f"{name} ({len(features)})", measure_text(role, features, crs)])
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            for feature in features:
                child = QTreeWidgetItem(
                    [item_name(role, feature) or "ohne Bezeichnung", measure_text(role, [feature], crs)]
                )
                child.setData(0, Qt.ItemDataRole.UserRole, (role, feature.id()))
                parent.addChild(child)
            self._list.addTopLevelItem(parent)
            parent.setExpanded(True)
        for col in range(2):
            self._list.resizeColumnToContents(col)

    def _on_item_clicked(self, item, _column):
        hit = item.data(0, Qt.ItemDataRole.UserRole)
        if hit:
            # Erst nach dem Klick auswählen: die neue Auswahl baut die Liste neu auf
            QTimer.singleShot(0, lambda: self.controller.select([tuple(hit)]))

    def _on_label_edited(self):
        if not self._label_edit.isModified():
            return
        self._label_edit.setModified(False)
        self.controller.set_selection_label(self._label_edit.text().strip())

    def _on_rotation_edited(self, value: float):
        delta = (value - self._rotation_value + 180.0) % 360.0 - 180.0
        if abs(delta) >= 0.5:
            self._rotation_value = value
            self.controller.rotate_selection(delta)

    def focus_label(self):
        if self._label_edit.isEnabled():
            self._label_edit.setFocus()
            self._label_edit.selectAll()
