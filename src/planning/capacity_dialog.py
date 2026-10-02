"""Dialog: Wie viele Zelte bzw. Fahrzeuge passen in eine Fläche?"""

from qgis.core import Qgis, QgsCoordinateTransform, QgsGeometry, QgsProject
from qgis.gui import QgsRubberBand
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .catalog import fmt_m
from .geometry import MetricFrame, pack_rectangles, tent_polygon
from .layers import ROLE_TENTS, ROLE_VEHICLES

_CATEGORIES = ((ROLE_TENTS, "Zelte"), (ROLE_VEHICLES, "Fahrzeuge"))


def _m2(value: float) -> str:
    return f"{value:,.0f} m²".replace(",", ".")


class CapacityDialog(QDialog):
    """Berechnet je Zelt- bzw. Fahrzeugtyp die maximale Anzahl in der Fläche.

    Bereits platzierte Zelte und Fahrzeuge gelten als Hindernis. Eine Zeile
    anklicken zeigt die Belegung als Vorschau auf der Karte, „platzieren“
    übernimmt sie in den Layer.
    """

    def __init__(self, controller, area, area_crs, role: str = ROLE_TENTS, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Flächen-Kapazität")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.controller = controller
        self.canvas = controller.canvas
        self._results: list[tuple] = []  # (FootprintType, PackResult)
        self._role = role

        canvas_crs = self.canvas.mapSettings().destinationCrs()
        area_canvas = QgsGeometry(area)
        if area_crs != canvas_crs:
            area_canvas.transform(QgsCoordinateTransform(area_crs, canvas_crs, QgsProject.instance()))
        self._frame = MetricFrame(canvas_crs, area_canvas.centroid().asPoint())
        self._area_m = self._frame.geom_to_m(area_canvas)

        self._outline = QgsRubberBand(self.canvas, Qgis.GeometryType.Polygon)
        self._outline.setFillColor(QColor(13, 153, 255, 30))
        self._outline.setStrokeColor(QColor(13, 153, 255))
        self._outline.setWidth(2)
        self._outline.setToGeometry(area_canvas, None)
        self._preview = QgsRubberBand(self.canvas, Qgis.GeometryType.Polygon)
        self._preview.setFillColor(QColor(76, 175, 80, 120))
        self._preview.setStrokeColor(QColor(27, 94, 32))
        self._preview.setWidth(1)

        layout = QVBoxLayout(self)
        self._info = QLabel()
        self._info.setWordWrap(True)
        layout.addWidget(self._info)

        opts = QHBoxLayout()
        self._category = QComboBox()
        for cat_role, title in _CATEGORIES:
            self._category.addItem(title, cat_role)
        self._category.setCurrentIndex(self._category.findData(self._role))
        self._category.currentIndexChanged.connect(self._on_category_changed)
        opts.addWidget(self._category)
        opts.addWidget(QLabel("Mindestabstand:"))
        self._gap = QDoubleSpinBox()
        self._gap.setRange(0.0, 50.0)
        self._gap.setSingleStep(0.5)
        self._gap.setDecimals(1)
        self._gap.setSuffix(" m")
        self._gap.setValue(controller.gaps[self._role])
        opts.addWidget(self._gap)
        self._guy_inside = QCheckBox("Abspannung innerhalb der Fläche")
        self._guy_inside.setChecked(controller.guy_inside_area)
        opts.addWidget(self._guy_inside)
        opts.addStretch(1)
        recalc = QPushButton("Neu berechnen")
        recalc.clicked.connect(self._calculate)
        opts.addWidget(recalc)
        layout.addLayout(opts)

        self._table = QTableWidget(0, 4)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.itemSelectionChanged.connect(self._show_preview)
        layout.addWidget(self._table)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._place = QPushButton("Platzieren")
        self._place.setEnabled(False)
        self._place.clicked.connect(self._place_selected)
        buttons.addWidget(self._place)
        close = QPushButton("Schließen")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        self.resize(600, 460)
        self._calculate()

    def _on_category_changed(self, index: int):
        self._role = self._category.itemData(index)
        self._gap.setValue(self.controller.gaps[self._role])
        self._calculate()

    def _calculate(self):
        tents = self._role == ROLE_TENTS
        self._guy_inside.setVisible(tents)
        self._table.setHorizontalHeaderLabels(
            ["Zelttyp" if tents else "Fahrzeug", "Maße", "Anzahl", "Zeltfläche" if tents else "Stellfläche"]
        )
        self.controller.set_gap(self._role, self._gap.value())
        if tents:
            self.controller.set_guy_inside_area(self._guy_inside.isChecked())
        gap = self._gap.value()
        obstacles = self.controller.existing_bodies_m(self._frame, within_m=self._area_m)

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._results = []
            for obj in self.controller.footprint_types(self._role):
                margin = obj.abspannung if tents and self._guy_inside.isChecked() else 0.0
                result = pack_rectangles(self._area_m, obj.laenge, obj.breite, gap, margin, obstacles)
                self._results.append((obj, result))
        finally:
            QApplication.restoreOverrideCursor()

        info = f"Fläche: <b>{_m2(self._area_m.area())}</b> · Mindestabstand {fmt_m(gap)} m"
        if obstacles:
            info += f" · {len(obstacles)} vorhandene Zelte/Fahrzeuge berücksichtigt"
        if any(r.too_large for _, r in self._results):
            info += "<br>Teilweise zu groß zum Berechnen – bitte kleinere Teilflächen zeichnen."
        self._info.setText(info)

        selected_id = self.controller.selected_ids.get(self._role)
        self._table.blockSignals(True)
        self._table.clearSelection()
        self._table.setRowCount(len(self._results))
        select_row = 0
        for row, (obj, result) in enumerate(self._results):
            count = QTableWidgetItem("zu groß" if result.too_large else str(result.count))
            if result.too_large:
                count.setToolTip("Fläche für diesen Typ zu groß – bitte eine kleinere Teilfläche zeichnen")
            count.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            used = QTableWidgetItem(_m2(result.count * obj.flaeche))
            used.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 0, QTableWidgetItem(obj.name))
            self._table.setItem(row, 1, QTableWidgetItem(f"{fmt_m(obj.laenge)} × {fmt_m(obj.breite)} m"))
            self._table.setItem(row, 2, count)
            self._table.setItem(row, 3, used)
            if obj.id == selected_id:
                select_row = row
        self._table.blockSignals(False)
        self._table.selectRow(select_row)
        self._show_preview()

    def _selected(self) -> tuple | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self._results):
            return None
        return self._results[rows[0].row()]

    def _show_preview(self):
        self._preview.reset(Qgis.GeometryType.Polygon)
        sel = self._selected()
        if sel is None:
            self._place.setEnabled(False)
            return
        obj, result = sel
        for c in result.centers:
            self._preview.addGeometry(self._frame.geom_from_m(tent_polygon(c, obj.laenge, obj.breite, result.rotation)))
        self._place.setEnabled(result.count > 0)
        self._place.setText(f"{result.count} × {obj.name} platzieren")

    def _place_selected(self):
        sel = self._selected()
        if sel is None:
            return
        obj, result = sel
        self.controller.add_footprints(self._role, obj, result.centers, result.rotation, self._frame)
        self.close()

    def closeEvent(self, event):
        for band in (self._preview, self._outline):
            try:
                self.canvas.scene().removeItem(band)
            except RuntimeError:
                pass
        super().closeEvent(event)
