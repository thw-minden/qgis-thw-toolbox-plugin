from collections.abc import Callable

from qgis.core import Qgis, QgsCoordinateTransform, QgsDistanceArea, QgsPointLocator, QgsPointXY, QgsProject
from qgis.gui import QgsMapTool, QgsRubberBand, QgsSnapIndicator
from qgis.PyQt.QtCore import QPoint, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QDialog

from ..layer import annotations
from ..logging_utils import get_logger
from ..ui.annotation_dialog import PointCreateDialog, TextCreateDialog
from ..util.units import format_area, format_meters
from .cursor_label import CursorLabel

logger = get_logger(__name__)

MODE_TEXT = "text"
MODE_POINT = "point"
MODE_LINE = "line"
MODE_POLYGON = "polygon"
MODE_POLYGON_FILLED = "polygon_filled"

_HINTS = {
    MODE_TEXT: "Text setzen: Linksklick auf die Karte (der Text steht mittig über dem angeklickten Punkt).",
    MODE_POINT: "Punkt setzen: Linksklick auf die Karte.",
    MODE_LINE: "Linie zeichnen: Linksklick = Stützpunkt, Rechtsklick/Enter = fertig, "
    "Rücktaste = letzten Punkt entfernen, Esc = abbrechen.",
    MODE_POLYGON: "Polygon zeichnen: Linksklick = Eckpunkt, Rechtsklick/Enter = fertig, "
    "Rücktaste = letzten Punkt entfernen, Esc = abbrechen.",
}
_HINTS[MODE_POLYGON_FILLED] = _HINTS[MODE_POLYGON]


# Minimum vertex count per mode before a shape can be finished
_MIN_POINTS = {MODE_LINE: 2, MODE_POLYGON: 3, MODE_POLYGON_FILLED: 3}


class AnnotationTool(QgsMapTool):
    """Map tool drawing one kind of annotation (point, line, polygon) into the annotation layer.

    Colors are read from ``settings`` when a shape is finished, so changes in
    the settings dialog apply to the next annotation without re-creating the tool.
    The tool stays active after finishing a shape so several can be drawn in a row.
    """

    def __init__(
        self,
        canvas,
        mode: str,
        settings,
        show_hint: Callable[[str], None] | None = None,
        on_created: Callable[[], None] | None = None,
    ):
        super().__init__(canvas)
        self.canvas = canvas
        self.mode = mode
        self.settings = settings
        self._show_hint = show_hint
        self._on_created = on_created
        self._points: list[QgsPointXY] = []  # canvas CRS
        self._rubber_band: QgsRubberBand | None = None
        self._snap_indicator = QgsSnapIndicator(canvas)
        self._length_label = CursorLabel(canvas)  # floating length readout next to the cursor
        self.setCursor(Qt.CursorShape.CrossCursor)

    # ------------------------------------------------------------------
    # QgsMapTool overrides
    # ------------------------------------------------------------------

    def activate(self):
        super().activate()
        if self._show_hint:
            self._show_hint(_HINTS[self.mode])

    def deactivate(self):
        self._reset()
        self._snap_indicator.setMatch(QgsPointLocator.Match())
        if self._show_hint:
            self._show_hint("")
        super().deactivate()

    def canvasMoveEvent(self, e):
        point = e.snapPoint()
        self._snap_indicator.setMatch(e.mapPointMatch())
        if self._points:
            self._update_rubber_band(point)
            self._update_length_label(point, e.pos())

    def canvasReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            point = e.snapPoint()
            if self.mode == MODE_POINT:
                self._create_point(point)
                return
            if self.mode == MODE_TEXT:
                self._create_text(point)
                return
            self._points.append(point)
            self._update_rubber_band(point)
            self._update_length_label(point, e.pos())
        elif e.button() == Qt.MouseButton.RightButton:
            self._finish_shape()

    def keyPressEvent(self, e):
        key = e.key()
        if key == Qt.Key.Key_Escape:
            self._reset()
            e.accept()
        elif key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and self._points:
            self._points.pop()
            self._update_rubber_band(None)
            # Shown again with the next mouse move, when the cursor position is known
            self._hide_length_label()
            e.accept()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._points:
            self._finish_shape()
            e.accept()
        else:
            e.ignore()

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _line_color(self) -> QColor:
        return QColor(self.settings.annotation_line_color)

    def _fill_color(self) -> QColor:
        return QColor(self.settings.annotation_fill_color)

    def _ensure_rubber_band(self) -> QgsRubberBand:
        if self._rubber_band is None:
            geom_type = Qgis.GeometryType.Line if self.mode == MODE_LINE else Qgis.GeometryType.Polygon
            rb = QgsRubberBand(self.canvas, geom_type)
            rb.setStrokeColor(self._line_color())
            rb.setWidth(2)
            rb.setFillColor(self._fill_color() if self.mode == MODE_POLYGON_FILLED else QColor(0, 0, 0, 0))
            self._rubber_band = rb
        return self._rubber_band

    def _update_rubber_band(self, hover_point: QgsPointXY | None):
        """Redraw the preview from the fixed vertices plus the point under the cursor."""
        rb = self._ensure_rubber_band()
        rb.reset(Qgis.GeometryType.Line if self.mode == MODE_LINE else Qgis.GeometryType.Polygon)
        preview = self._points + ([hover_point] if hover_point is not None else [])
        for i, p in enumerate(preview):
            rb.addPoint(p, i == len(preview) - 1)

    def _reset(self):
        self._points = []
        self._hide_length_label()
        if self._rubber_band is not None:
            self.canvas.scene().removeItem(self._rubber_band)
            self._rubber_band = None

    def dispose(self):
        """Remove widgets owned by the tool (call on plugin unload)."""
        self._reset()
        self._length_label.dispose()

    # ------------------------------------------------------------------
    # Length readout
    # ------------------------------------------------------------------

    def _measure_m(self, da: QgsDistanceArea, a: QgsPointXY, b: QgsPointXY) -> float:
        return da.convertLengthMeasurement(da.measureLine(a, b), Qgis.DistanceUnit.Meters)

    def _update_length_label(self, hover_point: QgsPointXY, pos: QPoint):
        """Show the current segment length and the total length (lines) or perimeter and area (polygons)."""
        if not self._points:
            self._hide_length_label()
            return
        da = annotations.distance_area(self.canvas.mapSettings().destinationCrs())
        preview = self._points + [hover_point]
        segments = [self._measure_m(da, a, b) for a, b in zip(preview, preview[1:])]
        lines = [f"Segment: {format_meters(segments[-1])}"]
        if self.mode == MODE_LINE:
            if len(segments) > 1:
                lines.append(f"Gesamt: {format_meters(sum(segments))}")
        elif len(preview) >= 3:
            closing = self._measure_m(da, preview[-1], preview[0])
            lines.append(f"Umfang: {format_meters(sum(segments) + closing)}")
            area = da.convertAreaMeasurement(da.measurePolygon(preview + [preview[0]]), Qgis.AreaUnit.SquareMeters)
            lines.append(f"Fläche: {format_area(area)}")

        self._length_label.show_at("\n".join(lines), pos)

    def _hide_length_label(self):
        self._length_label.hide()

    def _to_layer_crs(self, layer, points: list[QgsPointXY]) -> list[QgsPointXY] | None:
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        if canvas_crs == layer.crs():
            return list(points)
        transform = QgsCoordinateTransform(canvas_crs, layer.crs(), QgsProject.instance())
        try:
            return [transform.transform(p) for p in points]
        except Exception:
            logger.exception("Konnte Annotations-Koordinaten nicht transformieren")
            return None

    def _target_layer(self):
        crs = self.canvas.mapSettings().destinationCrs()
        if not crs.isValid():
            return None
        return annotations.get_or_create_annotation_layer(crs)

    def _create_point(self, point: QgsPointXY):
        layer = self._target_layer()
        if layer is None:
            return
        dialog = PointCreateDialog(self.canvas.window())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        pts = self._to_layer_crs(layer, [point])
        if pts:
            annotations.add_point(
                layer,
                pts[0],
                self._line_color(),
                dialog.description(),
                annotations.map_units_per_mm(self.canvas.mapSettings()),
                dialog.radius_m(),
                self.settings.annotation_mgrs_resolution_m,
            )
            self._notify_created()

    def _create_text(self, point: QgsPointXY):
        layer = self._target_layer()
        if layer is None:
            return
        dialog = TextCreateDialog(self.canvas.window())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        pts = self._to_layer_crs(layer, [point])
        if pts and annotations.add_text(
            layer, pts[0], dialog.text(), mgrs_resolution_m=self.settings.annotation_mgrs_resolution_m
        ):
            self._notify_created()

    def _finish_shape(self):
        if self.mode in (MODE_POINT, MODE_TEXT):
            return
        points = self._points
        if len(points) < _MIN_POINTS[self.mode]:
            # Too few vertices for a valid shape — treat as cancel
            self._reset()
            return

        layer = self._target_layer()
        pts = self._to_layer_crs(layer, points) if layer is not None else None
        self._reset()
        if not pts:
            return

        if self.mode == MODE_LINE:
            annotations.add_line(layer, pts, self._line_color(), self.settings.annotation_line_width_mm)
        else:
            fill = self._fill_color() if self.mode == MODE_POLYGON_FILLED else None
            annotations.add_polygon(layer, pts, self._line_color(), fill, self.settings.annotation_line_width_mm)
        self._notify_created()

    def _notify_created(self):
        if self._on_created:
            self._on_created()
