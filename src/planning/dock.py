"""Dock der Lagerplanung: Zelte, Fahrzeuge, Flächen-Kapazität, Stromverteilung, Bilanz."""

from dataclasses import dataclass

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .catalog import fmt_m
from .layers import ROLE_TENTS, ROLE_VEHICLES

_PLACE_HINT = (
    "Klick setzt {obj} · R / Shift+R dreht ±15° · Strg+Mausrad ±5° · Rechtsklick 90° · "
    "Shift+Klick ignoriert Mindestabstand · Esc beendet"
)
_HINTS = {
    "tent": _PLACE_HINT.format(obj="Zelt"),
    "vehicle": _PLACE_HINT.format(obj="Fahrzeug (dicke Kante = Front)"),
    "area": "Eckpunkte anklicken · Rechtsklick / Enter / Doppelklick schließt ab · Rücktaste entfernt Punkt",
    "cable": "Klick am Verteiler beginnen (wird gefangen) · Stützpunkte setzen · Rechtsklick / Enter beendet · "
    "Rücktaste entfernt Punkt · Esc verwirft",
    "distributor": "Klick setzt Verteiler · Esc beendet",
    "delete": "Element anklicken zum Löschen · Esc beendet",
}


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
        super().__init__("Lagerplanung", parent)
        self.setObjectName("THWToolboxLagerplanung")
        self.controller = controller
        self._tool_buttons: list[tuple[QPushButton, str, str | None]] = []
        self._footprints: dict[str, _FootprintWidgets] = {}

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(self._build_footprint_group(ROLE_TENTS))
        layout.addWidget(self._build_footprint_group(ROLE_VEHICLES))
        layout.addWidget(self._build_power())
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
        return box

    def _build_edit(self) -> QGroupBox:
        box = QGroupBox("Bearbeiten")
        layout = QVBoxLayout(box)
        layout.addWidget(self._tool_button("Element löschen", "delete"))
        layout.addWidget(
            _muted("Verschieben, Drehen und Beschriften geht auch mit den QGIS-Werkzeugen (Gruppe „Lagerplanung“).")
        )
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
        # Änderungen gelten für diese Sitzung; dauerhaft in data/lagerplanung.json
        w = self._footprints[role]
        obj = self.controller.current_footprint(role)
        obj.laenge = w.length.value()
        obj.breite = w.width.value()
        if w.guy is not None:
            obj.abspannung = w.guy.value()

    def show_rotation(self, value: float):
        for w in self._footprints.values():
            w.rotation.blockSignals(True)
            w.rotation.setValue(round(value))
            w.rotation.blockSignals(False)

    # ------------------------------------------------------------------
    # Werkzeuge
    # ------------------------------------------------------------------

    def _on_tool_clicked(self, checked: bool, kind: str, type_id: str | None):
        if checked:
            self.controller.activate_tool(kind, type_id)
        else:
            tool = self.controller.canvas.mapTool()
            if tool is not None:
                self.controller.canvas.unsetMapTool(tool)
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

        for col in range(3):
            self._summary.resizeColumnToContents(col)
