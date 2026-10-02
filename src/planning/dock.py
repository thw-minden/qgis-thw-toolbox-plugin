"""Dock der Objektplanung: Zelte, Fahrzeuge, Flächen-Kapazität, Strom, Beleuchtung, Auswahl, Bilanz."""

from collections import Counter
from dataclasses import dataclass

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QBrush, QColor
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .catalog import fmt_m
from .layers import ROLE_DISTRIBUTORS, ROLE_GENERATORS, ROLE_LIGHTS, ROLE_TENTS, ROLE_VEHICLES

_PLACE_HINT = (
    "Klick setzt {obj} · R / Shift+R dreht ±15° · Strg+Mausrad ±5° · Rechtsklick 90° · "
    "Hilfslinien richten aus (Strg hält an) · Raster in der Hotbar · Esc beendet"
)
_HINTS = {
    "tent": _PLACE_HINT.format(obj="Zelt"),
    "vehicle": _PLACE_HINT.format(obj="Fahrzeug (dicke Kante = Front)"),
    "area": "Eckpunkte anklicken · Rechtsklick / Enter / Doppelklick schließt ab · Rücktaste entfernt Punkt",
    "cable": "Klick am Verteiler beginnen (wird gefangen) · Stützpunkte setzen · Rechtsklick / Enter beendet · "
    "Rücktaste entfernt Punkt · Esc verwirft",
    "distributor": "Klick setzt Verteiler · Esc beendet",
    "generator": "Klick setzt Stromerzeuger · Leitungen von hier aus verlegen · Esc beendet",
    "light": "Klick setzt Leuchte (Kreis = ausgeleuchteter Bereich) · Esc beendet",
    "select": "Klick wählt aus · Shift+Klick ergänzt · Rahmen aufziehen wählt mehrere · Ziehen verschiebt "
    "(Alt+Ziehen kopiert) · an den Ecken ziehen dreht (Shift: 15°) · Pfeiltasten schieben 0,5 m (Shift: 5 m) · "
    "Entf löscht · D dupliziert · Doppelklick / Enter: Bezeichnung · Rechtsklick: Menü",
}
_ROLE_TITLES = {
    ROLE_TENTS: "Zelt",
    ROLE_VEHICLES: "Fahrzeug",
    ROLE_DISTRIBUTORS: "Verteiler",
    ROLE_GENERATORS: "Stromerzeuger",
    ROLE_LIGHTS: "Leuchte",
}
_WARN_BRUSH = QBrush(QColor(198, 40, 40))


def _spin(minimum: float, maximum: float, step: float, suffix: str, decimals: int = 1) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setSingleStep(step)
    spin.setDecimals(decimals)
    spin.setSuffix(suffix)
    return spin


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: palette(mid);")
    return label


@dataclass
class _FootprintWidgets:
    combo: QComboBox
    length: QDoubleSpinBox
    width: QDoubleSpinBox
    guy: QDoubleSpinBox | None
    rotation: QDoubleSpinBox
    gap: QDoubleSpinBox


class PlanningDock(QDockWidget):
    def __init__(self, controller, parent=None):
        super().__init__("Objektplanung", parent)
        self.setObjectName("THWToolboxObjektplanung")
        self.controller = controller
        self._tool_buttons: list[tuple[QPushButton, str, str | None]] = []
        self._footprints: dict[str, _FootprintWidgets] = {}
        self._point_combos: dict[str, QComboBox] = {}

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(self._build_footprint_group(ROLE_TENTS))
        layout.addWidget(self._build_footprint_group(ROLE_VEHICLES))
        layout.addWidget(self._build_power())
        layout.addWidget(self._build_lights())
        layout.addWidget(self._build_edit())
        layout.addWidget(self._build_summary(), 1)

        self._hint = _muted("")
        layout.addWidget(self._hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.setWidget(scroll)

        for role in self._footprints:
            self._load_footprint(role)
        controller.state_changed.connect(self._on_state_changed)
        self.sync_tool_buttons()

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------

    def _tool_button(self, text: str, kind: str, type_id: str | None = None) -> QPushButton:
        button = QPushButton(text)
        button.setCheckable(True)
        button.clicked.connect(lambda checked, k=kind, t=type_id: self._on_tool_clicked(checked, k, t))
        self._tool_buttons.append((button, kind, type_id))
        return button

    def _build_footprint_group(self, role: str) -> QGroupBox:
        tents = role == ROLE_TENTS
        box = QGroupBox("Zelte" if tents else "Fahrzeuge")
        layout = QVBoxLayout(box)

        combo = QComboBox()
        for obj in self.controller.footprint_types(role):
            combo.addItem(obj.name, obj.id)
        combo.currentIndexChanged.connect(lambda index, r=role: self._on_type_changed(r, index))
        layout.addWidget(combo)

        form = QFormLayout()
        length = _spin(0.5, 100.0, 0.5, " m", 2)
        width = _spin(0.5, 100.0, 0.5, " m", 2)
        dims = QHBoxLayout()
        dims.addWidget(length)
        dims.addWidget(QLabel("×"))
        dims.addWidget(width)
        form.addRow("Länge × Breite:", dims)
        spins = [length, width]
        guy = None
        if tents:
            guy = _spin(0.0, 20.0, 0.5, " m")
            form.addRow("Abspannung:", guy)
            spins.append(guy)
        for spin in spins:
            spin.valueChanged.connect(lambda _v, r=role: self._on_dimensions_changed(r))

        rotation = _spin(0.0, 359.0, 15.0, " °", 0)
        rotation.setWrapping(True)
        rotation.setValue(self.controller.rotation)
        rotation.valueChanged.connect(self.controller.set_rotation)
        form.addRow("Drehung:", rotation)

        gap = _spin(0.0, 50.0, 0.5, " m")
        gap.setValue(self.controller.gaps[role])
        gap.setToolTip(
            "Mindestabstand zwischen zwei Zeltwänden (Wege, Abspannung, Brandschutz)"
            if tents
            else "Mindestabstand zwischen Fahrzeugen (Türen öffnen, Spiegel, Durchgang)"
        )
        gap.valueChanged.connect(lambda value, r=role: self.controller.set_gap(r, value))
        form.addRow("Mindestabstand:", gap)
        layout.addLayout(form)

        layout.addWidget(self._tool_button("Zelt platzieren" if tents else "Fahrzeug platzieren", self._kind(role)))
        row = QHBoxLayout()
        row.addWidget(self._tool_button("Fläche zeichnen …", "area", role))
        selected = QPushButton("Ausgewählte Fläche")
        selected.setToolTip(
            "Berechnet die Kapazität für die ausgewählten Flächen des aktiven Layers (z.B. Wiese, Parkplatz)"
        )
        selected.clicked.connect(lambda _checked=False, r=role: self.controller.evaluate_selected_area(r))
        row.addWidget(selected)
        layout.addLayout(row)
        layout.addWidget(_muted("Wie viele Zelte passen hinein?" if tents else "Wie viele Fahrzeuge passen hinein?"))
        if not tents:
            layout.addWidget(_muted("Richtwerte ohne Spiegel – bitte mit Fahrzeugschein abgleichen."))

        self._footprints[role] = _FootprintWidgets(combo, length, width, guy, rotation, gap)
        return box

    @staticmethod
    def _kind(role: str) -> str:
        return "tent" if role == ROLE_TENTS else "vehicle"

    def _build_power(self) -> QGroupBox:
        box = QGroupBox("Stromversorgung")
        layout = QVBoxLayout(box)
        for reel in self.controller.catalog.leitungsroller:
            layout.addWidget(self._tool_button(reel.name, "cable", reel.id))
        for dist in self.controller.catalog.verteiler:
            layout.addWidget(self._tool_button(dist.name, "distributor", dist.id))
        layout.addLayout(self._point_row(ROLE_GENERATORS, "generator", "Stromerzeuger setzen"))
        layout.addWidget(_muted("Leitungen am Stromerzeuger beginnen – die Bilanz zeigt die Last je Aggregat."))
        return box

    def _build_lights(self) -> QGroupBox:
        box = QGroupBox("Beleuchtung")
        layout = QVBoxLayout(box)
        layout.addLayout(self._point_row(ROLE_LIGHTS, "light", "Leuchte setzen"))
        return box

    def _point_row(self, role: str, kind: str, text: str) -> QHBoxLayout:
        combo = QComboBox()
        for obj in self.controller.point_types(role):
            combo.addItem(obj.name, obj.id)
        combo.currentIndexChanged.connect(
            lambda index, r=role, c=combo: self.controller.select_point_type(r, c.itemData(index))
        )
        self._point_combos[role] = combo
        row = QHBoxLayout()
        row.addWidget(combo, 1)
        row.addWidget(self._tool_button(text, kind))
        return row

    def _build_edit(self) -> QGroupBox:
        box = QGroupBox("Auswahl")
        layout = QVBoxLayout(box)
        layout.addWidget(self._tool_button("Auswählen / Verschieben", "select"))
        self._selection_label = _muted("Nichts ausgewählt")
        layout.addWidget(self._selection_label)
        form = QFormLayout()
        self._label_edit = QLineEdit()
        self._label_edit.setPlaceholderText("z.B. Zelt 1, Küche, NEA Nord")
        self._label_edit.editingFinished.connect(self._on_label_edited)
        form.addRow("Bezeichnung:", self._label_edit)
        layout.addLayout(form)
        row = QHBoxLayout()
        self._sel_buttons = []
        for text, slot in (
            ("Duplizieren", self.controller.duplicate_selection),
            ("Drehen 15°", lambda: self.controller.rotate_selection(15)),
            ("Löschen", self.controller.delete_selection),
        ):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, f=slot: f())
            row.addWidget(button)
            self._sel_buttons.append(button)
        layout.addLayout(row)
        layout.addWidget(_muted("Auswahl mit den QGIS-Auswahlwerkzeugen funktioniert auch (Gruppe „Objektplanung“)."))
        return box

    def _build_summary(self) -> QGroupBox:
        box = QGroupBox("Bilanz")
        layout = QVBoxLayout(box)
        self._summary = QTreeWidget()
        self._summary.setHeaderLabels(["Element", "Anzahl", "Summe"])
        self._summary.setRootIsDecorated(True)
        self._summary.setMinimumHeight(160)
        layout.addWidget(self._summary)
        return box

    # ------------------------------------------------------------------
    # Typ-Auswahl und Maße
    # ------------------------------------------------------------------

    def _load_footprint(self, role: str):
        w = self._footprints[role]
        index = w.combo.findData(self.controller.selected_ids.get(role))
        if index >= 0 and index != w.combo.currentIndex():
            w.combo.blockSignals(True)
            w.combo.setCurrentIndex(index)
            w.combo.blockSignals(False)
        obj = self.controller.current_footprint(role)
        values = [(w.length, obj.laenge), (w.width, obj.breite)]
        if w.guy is not None:
            values.append((w.guy, obj.abspannung))
        for spin, value in values:
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _on_type_changed(self, role: str, index: int):
        self.controller.select_footprint(role, self._footprints[role].combo.itemData(index))
        self._load_footprint(role)

    def _on_dimensions_changed(self, role: str):
        # Änderungen gelten für diese Sitzung; dauerhaft in data/objektplanung.json
        w = self._footprints[role]
        obj = self.controller.current_footprint(role)
        obj.laenge = w.length.value()
        obj.breite = w.width.value()
        if w.guy is not None:
            obj.abspannung = w.guy.value()

    def _on_state_changed(self):
        value = round(self.controller.rotation)
        for role, w in self._footprints.items():
            if w.rotation.value() != value:
                w.rotation.blockSignals(True)
                w.rotation.setValue(value)
                w.rotation.blockSignals(False)
            if w.combo.itemData(w.combo.currentIndex()) != self.controller.selected_ids.get(role):
                self._load_footprint(role)
        for role, combo in self._point_combos.items():
            index = combo.findData(self.controller.point_ids.get(role))
            if index >= 0 and index != combo.currentIndex():
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)
        self.sync_tool_buttons()

    # ------------------------------------------------------------------
    # Auswahl
    # ------------------------------------------------------------------

    def refresh_selection(self):
        selected = self.controller.selection()
        for button in self._sel_buttons:
            button.setEnabled(bool(selected))
        self._label_edit.setEnabled(bool(selected))
        if not selected:
            self._selection_label.setText("Nichts ausgewählt")
            self._label_edit.clear()
            return
        counts = Counter(f.attribute("typ") or _ROLE_TITLES.get(role, role) for role, f in selected)
        parts = [f"{n}× {name}" if n > 1 else name for name, n in counts.most_common()]
        self._selection_label.setText(f"{len(selected)} ausgewählt: " + ", ".join(parts))
        labels = {f.attribute("bezeichnung") or "" for _role, f in selected}
        uniform = len(labels) == 1
        self._label_edit.blockSignals(True)
        self._label_edit.setText(next(iter(labels)) if uniform else "")
        self._label_edit.setPlaceholderText(
            "z.B. Zelt 1, Küche, NEA Nord" if uniform else "unterschiedlich – Eingabe gilt für alle"
        )
        self._label_edit.blockSignals(False)

    def focus_label(self):
        self.show()
        self.raise_()
        self._label_edit.setFocus()
        self._label_edit.selectAll()

    def _on_label_edited(self):
        if not self._label_edit.isModified():
            return
        self._label_edit.setModified(False)
        self.controller.set_selection_label(self._label_edit.text().strip())

    # ------------------------------------------------------------------
    # Werkzeuge
    # ------------------------------------------------------------------

    def _on_tool_clicked(self, checked: bool, kind: str, type_id: str | None):
        if checked:
            self.controller.activate_tool(kind, type_id)
        else:
            self.controller.deactivate_tool()
        self.sync_tool_buttons()

    def sync_tool_buttons(self):
        kind, type_id = self.controller.active_tool_kind()
        for button, b_kind, b_type in self._tool_buttons:
            button.blockSignals(True)
            button.setChecked(b_kind == kind and (b_type is None or b_type == type_id))
            button.blockSignals(False)
        self._hint.setText(_HINTS.get(kind, ""))
        self._hint.setVisible(kind is not None)

    # ------------------------------------------------------------------
    # Bilanz
    # ------------------------------------------------------------------

    def refresh_summary(self):
        data = self.controller.summary()
        self._summary.clear()

        def section(title: str, rows: list[tuple[str, int, str]], total: str):
            count = sum(r[1] for r in rows)
            parent = QTreeWidgetItem([title, str(count), total])
            font = parent.font(0)
            font.setBold(True)
            for col in range(3):
                parent.setFont(col, font)
            for name, n, extra in rows:
                child = QTreeWidgetItem([name, str(n), extra])
                child.setTextAlignment(1, Qt.AlignmentFlag.AlignRight)
                parent.addChild(child)
            parent.setTextAlignment(1, Qt.AlignmentFlag.AlignRight)
            self._summary.addTopLevelItem(parent)
            parent.setExpanded(True)

        for key, title in (("zelte", "Zelte"), ("fahrzeuge", "Fahrzeuge")):
            items = data[key]
            section(
                title,
                [(k, v["anzahl"], f"{fmt_m(round(v['flaeche']))} m²") for k, v in sorted(items.items())],
                f"{fmt_m(round(sum(v['flaeche'] for v in items.values())))} m²",
            )
        cables = data["leitungen"]
        section(
            "Leitungsroller",
            [(k, v["anzahl"], f"{fmt_m(round(v['laenge'], 1))} m verlegt") for k, v in sorted(cables.items())],
            f"{fmt_m(round(sum(v['laenge'] for v in cables.values()), 1))} m",
        )
        dists = data["verteiler"]
        section("Verteiler", [(k, v["anzahl"], "") for k, v in sorted(dists.items())], "")

        gens = data["stromerzeuger"]
        section(
            "Stromerzeuger",
            [(k, v["anzahl"], f"{fmt_m(v['leistung'])} kVA") for k, v in sorted(gens.items())],
            f"{fmt_m(sum(v['leistung'] for v in gens.values()))} kVA",
        )
        lights = data["beleuchtung"]
        section(
            "Beleuchtung",
            [(k, v["anzahl"], f"{fmt_m(round(v['leistung'] / 1000, 1))} kW") for k, v in sorted(lights.items())],
            f"{fmt_m(round(sum(v['leistung'] for v in lights.values()) / 1000, 1))} kW",
        )

        # Last je Stromerzeuger (über Leitungen und Verteiler verbunden)
        nets = data["netze"]
        if nets or data["nicht_angeschlossen"]:
            parent = QTreeWidgetItem(["Last je Stromerzeuger", "", ""])
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            for net in nets:
                load = (
                    f"{fmt_m(round(net['last_w'] / 1000, 1))} kW / {fmt_m(net['kva'])} kVA"
                    f" ({round(net['auslastung'] * 100)} %)"
                )
                child = QTreeWidgetItem([net["name"], f"{net['leuchten']} Leuchten", load])
                if net["auslastung"] > 1.0:
                    for col in range(3):
                        child.setForeground(col, _WARN_BRUSH)
                    child.setToolTip(2, "Überlastet: Last größer als 80 % der Nennleistung (cos φ ≈ 0,8)")
                parent.addChild(child)
            if data["nicht_angeschlossen"]:
                child = QTreeWidgetItem(["Nicht angeschlossen", f"{data['nicht_angeschlossen']} Leuchten", ""])
                for col in range(3):
                    child.setForeground(col, _WARN_BRUSH)
                parent.addChild(child)
            self._summary.addTopLevelItem(parent)
            parent.setExpanded(True)

        for col in range(3):
            self._summary.resizeColumnToContents(col)
