"""Details zur Auswahl: ein oder mehrere Objekte der Objektplanung, auch gemischt mit taktischen Zeichen.

Liegt als Seite im Details-Dock (``ui.feature_dock.FeatureDock``). Ein einzelnes
taktisches Zeichen zeigt das Dock mit seinen eigenen Einstellungen.
"""

import os
from collections import Counter

from qgis.core import QgsCoordinateTransform, QgsProject
from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..ui.font_size_box import FontSizeBox
from ..ui.object_style_popup import KIND_ARROW, KIND_POINT, KIND_TEXT, ObjectStyleButton
from ..ui.theme import muted_style
from ..ui.zone_style_popup import ZoneStyleButton
from ..util.coordinates import WGS84, to_mgrs
from .catalog import fmt_m
from .geometry import MetricFrame
from .layers import (
    FOOTPRINT_ROLES,
    NOTE_ROLES,
    ROLE_ARROWS,
    ROLE_CABLES,
    ROLE_DISTRIBUTORS,
    ROLE_GENERATORS,
    ROLE_LIGHTS,
    ROLE_MARKERS,
    ROLE_POINTS,
    ROLE_TENTS,
    ROLE_TEXTS,
    ROLE_VEHICLES,
    ROLE_ZONES,
    SIGN_ROLES,
    label_visible,
    sign_visible,
)

ROLE_TITLES = {
    ROLE_TENTS: "Zelt",
    ROLE_VEHICLES: "Fahrzeug",
    ROLE_CABLES: "Leitung",
    ROLE_DISTRIBUTORS: "Verteiler",
    ROLE_GENERATORS: "Stromerzeuger",
    ROLE_LIGHTS: "Leuchte",
    ROLE_ZONES: "Gebiet",
    ROLE_ARROWS: "Linie",
    ROLE_POINTS: "Punkt",
    ROLE_TEXTS: "Text",
    ROLE_MARKERS: "Taktisches Zeichen",
}
_LABEL_HINT = "z.B. Zelt 1, Küche, NEA Nord"
_NOTE_HINTS = {
    ROLE_POINTS: "Beschreibung auf der Karte, z.B. Sammelstelle – mehrzeilig möglich",
    ROLE_TEXTS: "Text auf der Karte – mehrzeilig möglich",
}
_STYLE_KINDS = {ROLE_ARROWS: KIND_ARROW, ROLE_POINTS: KIND_POINT, ROLE_TEXTS: KIND_TEXT}


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
        # Wie bei den Zeichen: UTMREF und Breite/Länge (Mitte des Objekts)
        utmref, latlon = _coords(feature.geometry().centroid().asPoint(), crs)
        rows.append(("UTMREF", utmref))
        rows.append(("Breite/Länge", latlon))
        if role == ROLE_ZONES:
            for number, (utmref, latlon) in enumerate(zone_points(feature, crs), 1):
                rows.append((f"Punkt {number}", f"{utmref}\n{latlon}"))
    return rows


def zone_points(feature, crs) -> list[tuple[str, str]]:
    """Eckpunkte eines Gebiets (Außenrand, ohne den schließenden Punkt) als (UTMREF, „Breite Länge“)."""
    geom = feature.geometry()
    if geom is None or geom.isEmpty():
        return []
    polygons = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
    points = []
    for polygon in polygons:
        if not polygon:
            continue
        ring = list(polygon[0])
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring.pop()
        points.extend(_coords(point, crs) for point in ring)
    return points


def zone_points_text(feature, crs) -> str:
    """Eckpunkte zum Kopieren: je Zeile Nummer, UTMREF und Breite/Länge, durch Tabulator getrennt."""
    return "\n".join(
        f"{number}\t{utmref}\t{latlon}" for number, (utmref, latlon) in enumerate(zone_points(feature, crs), 1)
    )


def _coords(point, crs) -> tuple[str, str]:
    """Punkt als UTMREF und „Breite Länge“ (WGS 84)."""
    try:
        wgs = QgsCoordinateTransform(crs, WGS84, QgsProject.instance()).transform(point)
    except Exception:
        return "–", "–"
    try:
        utmref = to_mgrs(wgs.y(), wgs.x()) or "–"
    except ValueError:
        utmref = "–"
    return utmref, f"{wgs.y():.6f} {wgs.x():.6f}"


def _show_states(box: QCheckBox, states: list[bool]):
    """An, aus oder – bei gemischter Auswahl – teils."""
    mixed = len(set(states)) > 1
    box.blockSignals(True)
    box.setTristate(mixed)
    if mixed:
        box.setCheckState(Qt.CheckState.PartiallyChecked)
    else:
        box.setChecked(all(states))
    box.blockSignals(False)


def _clicked_state(box: QCheckBox) -> bool:
    """Neuer Zustand nach einem Klick; aus „teils“ führt er zu „an“ (für alle)."""
    visible = box.checkState() != Qt.CheckState.Unchecked
    box.setTristate(False)
    box.setChecked(visible)
    return visible


class _NoteEdit(QPlainTextEdit):
    """Mehrzeilige Beschreibung bzw. Text: Enter beginnt eine neue Zeile, Strg+Enter oder Verlassen übernimmt."""

    edited = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabChangesFocus(True)
        self.setFixedHeight(self.fontMetrics().lineSpacing() * 4 + 12)
        # Feste Höhe allein reicht nicht: mit der Standard-Policy „Expanding“ verteilt das Formular den freien
        # Platz des Docks auf die Zeilen, und die Schriftgröße landet mitten in einer riesigen Lücke
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._dirty = False
        self.textChanged.connect(self._on_changed)

    def _on_changed(self):
        self._dirty = True

    def set_text(self, text: str):
        """Anzeigen, ohne ``edited`` auszulösen."""
        self.blockSignals(True)
        self.setPlainText(text)
        self.blockSignals(False)
        self._dirty = False

    def _commit(self):
        if self._dirty:
            self._dirty = False
            self.edited.emit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._commit()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        self._commit()
        super().focusOutEvent(event)


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
        self._label_row = form.labelForField(self._label_edit)
        # Punkte und Texte: mehrzeilig, die Beschreibung bzw. der Text steht so auf der Karte
        self._note_edit = _NoteEdit()
        self._note_edit.edited.connect(self._on_note_edited)
        form.addRow("Text:", self._note_edit)
        self._note_label = form.labelForField(self._note_edit)
        self._labels_shown = QCheckBox("Beschriftung auf der Karte")
        self._labels_shown.setToolTip("Bezeichnung bzw. Typ dieses Objekts auf der Karte zeigen (je Objekt schaltbar)")
        self._labels_shown.clicked.connect(self._on_labels_shown_clicked)
        form.addRow("", self._labels_shown)
        self._font_size = FontSizeBox()
        self._font_size.size_chosen.connect(controller.set_selection_label_size)
        font_size_row = QHBoxLayout()
        font_size_row.addWidget(self._font_size)
        font_size_row.addWidget(QLabel("pt"))
        font_size_row.addStretch()
        form.addRow("Schriftgröße:", font_size_row)
        self._signs_shown = QCheckBox("Taktisches Zeichen")
        self._signs_shown.setToolTip(
            "Taktisches Zeichen dieses Fahrzeugs, Stromerzeugers bzw. dieser Leuchte zeigen (je Objekt schaltbar)"
        )
        self._signs_shown.clicked.connect(self._on_signs_shown_clicked)
        # Ohne Beschriftung legt QFormLayout kein Label an – zum Ausblenden reicht die Checkbox
        form.addRow("", self._signs_shown)
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
        # Gebiete: Muster bzw. Schraffur, Farbe, Deckkraft und Rand im Popup (wie der Hintergrund der Zeichen)
        self._zone_style = ZoneStyleButton()
        self._zone_style.changed.connect(controller.update_selection_zone_styles)
        self._zone_style.reset.connect(controller.reset_selection_zone_styles)
        zone_style_row = QHBoxLayout()
        zone_style_row.addWidget(self._zone_style)
        zone_style_row.addStretch()
        form.addRow("Gestaltung:", zone_style_row)
        self._zone_style_label = form.labelForField(zone_style_row)
        # Pfeile, Punkte und Texte: Farbe (Standard: Typfarbe) und Feinheiten im Popup
        self._object_style = ObjectStyleButton()
        self._object_style.changed.connect(controller.update_selection_object_styles)
        self._object_style.reset.connect(controller.reset_selection_object_styles)
        object_style_row = QHBoxLayout()
        object_style_row.addWidget(self._object_style)
        object_style_row.addStretch()
        form.addRow("Gestaltung:", object_style_row)
        self._object_style_label = form.labelForField(object_style_row)
        self._radius = QDoubleSpinBox()
        self._radius.setRange(0.0, 100_000.0)
        self._radius.setDecimals(1)
        self._radius.setSingleStep(10.0)
        self._radius.setSuffix(" m")
        self._radius.setSpecialValueText("kein Radius")
        self._radius.setKeyboardTracking(False)
        self._radius.setToolTip("Maßstabsgetreuer Kreis um den Punkt, z. B. für Gefahren- oder Sperrbereiche")
        self._radius.valueChanged.connect(controller.set_selection_radius)
        form.addRow("Radius:", self._radius)
        self._radius_label = form.labelForField(self._radius)
        # Radius-Kreis eines Punkts: Muster, Füllung und Rand wie bei einem Gebiet
        self._circle_style = ZoneStyleButton(
            title="Gestaltung des Radius-Kreises",
            reset_tooltip="Eigene Gestaltung entfernen: der Kreis folgt wieder der Farbe des Punkts",
        )
        self._circle_style.changed.connect(controller.update_selection_circle_styles)
        self._circle_style.reset.connect(controller.reset_selection_circle_styles)
        circle_row = QHBoxLayout()
        circle_row.addWidget(self._circle_style)
        circle_row.addStretch()
        form.addRow("Radius-Kreis:", circle_row)
        self._circle_label = form.labelForField(circle_row)
        # Linien: Pfeilspitzen direkt schaltbar (Breite, Farbe und Größe der Spitzen unter „Gestaltung“)
        self._start_arrow = QCheckBox("am Anfang")
        self._start_arrow.clicked.connect(lambda: self._on_arrow_clicked(False))
        self._end_arrow = QCheckBox("am Ende")
        self._end_arrow.clicked.connect(lambda: self._on_arrow_clicked(True))
        arrows_row = QHBoxLayout()
        arrows_row.addWidget(self._start_arrow)
        arrows_row.addWidget(self._end_arrow)
        arrows_row.addStretch()
        form.addRow("Pfeilspitze:", arrows_row)
        self._arrows_label = form.labelForField(arrows_row)
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
        # Ein Text ist nur seine Beschriftung – ausgeblendet bliebe nichts mehr auf der Karte
        only_texts = all(role == ROLE_TEXTS for role, _f in items)
        self._labels_shown.setVisible(not only_texts)
        _show_states(self._labels_shown, [label_visible(role, f) for role, f in items if role != ROLE_TEXTS])
        self._font_size.set_sizes(self.controller.selection_label_sizes())
        # Nur Fahrzeuge, Stromerzeuger und Leuchten tragen ein taktisches Zeichen
        signs = [sign_visible(f) for role, f in items if role in SIGN_ROLES]
        self._signs_shown.setVisible(bool(signs))
        if signs:
            _show_states(self._signs_shown, signs)
        self._rotation.setVisible(footprint)
        self._rotation_label.setVisible(footprint)
        # Gestaltung nur, wenn ausschließlich Gebiete ausgewählt sind
        zone_styles = self.controller.selection_zone_styles() if all(r == ROLE_ZONES for r, _f in items) else []
        self._zone_style.setVisible(bool(zone_styles))
        self._zone_style_label.setVisible(bool(zone_styles))
        if zone_styles:
            self._zone_style.set_style(zone_styles[0])
        style_role = self.controller.selection_style_role()
        object_styles = self.controller.selection_object_styles() if style_role else []
        self._object_style.setVisible(bool(object_styles))
        self._object_style_label.setVisible(bool(object_styles))
        if object_styles:
            self._object_style.set_style(object_styles[0], _STYLE_KINDS[style_role])
        lines = style_role == ROLE_ARROWS
        for box, label in ((self._start_arrow, None), (self._end_arrow, self._arrows_label)):
            box.setVisible(lines)
            if label is not None:
                label.setVisible(lines)
        if lines:
            _show_states(self._start_arrow, self.controller.selection_arrows(at_end=False))
            _show_states(self._end_arrow, self.controller.selection_arrows(at_end=True))
        radii = self.controller.selection_radii() if style_role == ROLE_POINTS else []
        self._radius.setVisible(bool(radii))
        self._radius_label.setVisible(bool(radii))
        if radii:
            self._radius.blockSignals(True)
            self._radius.setValue(radii[0])
            self._radius.blockSignals(False)
        # Die Gestaltung des Kreises erst, wenn ein Punkt einen Radius hat
        circle = any(radius > 0 for radius in radii)
        self._circle_style.setVisible(circle)
        self._circle_label.setVisible(circle)
        if circle:
            self._circle_style.set_style(self.controller.selection_circle_styles()[0])
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
            if role == ROLE_ZONES and feature.hasGeometry():
                points = zone_points_text(feature, self._crs(role))
                copy = QPushButton("Eckpunkte kopieren")
                copy.setToolTip("Alle Eckpunkte (UTMREF und Breite/Länge) in die Zwischenablage kopieren")
                copy.clicked.connect(lambda _checked=False, text=points: QApplication.clipboard().setText(text))
                self._props.addRow("", copy)

        self._fill_list(items if single is None else [])
        self._rotate.setEnabled(any(role in FOOTPRINT_ROLES for role, _f in items))

    def _crs(self, role: str):
        layer = self.controller.layer(role)
        return layer.crs() if layer is not None else QgsProject.instance().crs()

    def _show_label(self, planning):
        """Bezeichnung gilt nur für Objekte der Objektplanung; Zeichen haben ihre Beschriftung.

        Nur Punkte bzw. nur Texte ausgewählt: statt der einzeiligen Bezeichnung das mehrzeilige Textfeld.
        """
        roles = {role for role, _f in planning}
        notes = len(roles) == 1 and next(iter(roles)) in NOTE_ROLES and len(planning) == len(
            self.controller.selection()
        )
        self._label_edit.setVisible(not notes)
        self._label_row.setVisible(not notes)
        self._note_edit.setVisible(notes)
        self._note_label.setVisible(notes)
        if notes:
            role = next(iter(roles))
            self._note_label.setText("Text:" if role == ROLE_TEXTS else "Beschreibung:")
            texts = {(f.attribute("bezeichnung") or "") for _role, f in planning}
            text = next(iter(texts)) if len(texts) == 1 else ""
            if self._note_edit.toPlainText() != text:
                self._note_edit.set_text(text)
            self._note_edit.setPlaceholderText(
                _NOTE_HINTS[role] if len(texts) <= 1 else "unterschiedlich – Eingabe gilt für alle"
            )
            return
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

    def _on_labels_shown_clicked(self):
        self.controller.set_selection_labels_visible(_clicked_state(self._labels_shown))

    def _on_signs_shown_clicked(self):
        self.controller.set_selection_signs_visible(_clicked_state(self._signs_shown))

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

    def _on_arrow_clicked(self, at_end: bool):
        box = self._end_arrow if at_end else self._start_arrow
        self.controller.set_selection_arrows(at_end, _clicked_state(box))

    def _on_note_edited(self):
        text = self._note_edit.toPlainText().strip()
        # Ein leerer Text wäre auf der Karte nicht mehr zu sehen (und nicht mehr anzuklicken)
        if not text and self.controller.selection_style_role() == ROLE_TEXTS:
            self.refresh()
            return
        self.controller.set_selection_label(text)

    def _on_rotation_edited(self, value: float):
        delta = (value - self._rotation_value + 180.0) % 360.0 - 180.0
        if abs(delta) >= 0.5:
            self._rotation_value = value
            self.controller.rotate_selection(delta)

    def focus_label(self):
        if not self._note_edit.isHidden():
            self._note_edit.setFocus()
            self._note_edit.selectAll()
        elif self._label_edit.isEnabled():
            self._label_edit.setFocus()
            self._label_edit.selectAll()
