import math
from collections.abc import Callable

from qgis.core import Qgis, QgsCoordinateTransform, QgsPointXY, QgsProject
from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker
from qgis.PyQt.QtCore import QPointF, Qt
from qgis.PyQt.QtGui import QColor

from ..layer import annotations
from ..logging_utils import get_logger

logger = get_logger(__name__)

# Maximum distance between cursor and node/segment to grab it
_PICK_TOLERANCE_PX = 10

_HINT = (
    "Annotation bearbeiten: Punkt/Stützpunkt ziehen = verschieben, Linksklick auf Linie/Umriss = Stützpunkt "
    "einfügen, Rechtsklick auf Stützpunkt = entfernen, Esc = abbrechen."
)
_HINT_MIN_VERTICES = (
    f"Stützpunkt kann nicht entfernt werden: Linien brauchen mindestens {annotations.MIN_LINE_VERTICES}, "
    f"Polygone mindestens {annotations.MIN_POLYGON_VERTICES} Stützpunkte."
)

_HIGHLIGHT = QColor(0, 120, 215)


class AnnotationMoveTool(QgsMapTool):
    """Edit annotation geometries: drag points/vertices, insert and remove vertices.

    - Left-drag on a point or vertex moves it (rubber band preview, applied on release).
    - Left-click on a line or polygon outline inserts a vertex there; keeping the
      button pressed drags the new vertex right away.
    - Right-click on a line/polygon vertex removes it, unless the minimum
      vertex count would be undercut — then nothing happens apart from a status hint.
    """

    def __init__(
        self,
        canvas,
        show_hint: Callable[[str], None] | None = None,
        on_moved: Callable[[], None] | None = None,
    ):
        super().__init__(canvas)
        self.canvas = canvas
        self._show_hint = show_hint
        self._on_moved = on_moved
        self._layer = None
        self._hover_node: annotations.NodeHandle | None = None
        # (item_id, point on the outline in layer CRS) when hovering a segment instead of a node
        self._hover_segment: tuple[str, QgsPointXY] | None = None
        self._dragging: annotations.NodeHandle | None = None
        self._drag_target: QgsPointXY | None = None  # layer CRS
        self._rubber_band: QgsRubberBand | None = None

        self._hover_marker = QgsVertexMarker(canvas)
        self._hover_marker.setIconSize(14)
        self._hover_marker.setPenWidth(2)
        self._hover_marker.setColor(_HIGHLIGHT)
        self._hover_marker.hide()
        self.setCursor(Qt.CursorShape.ArrowCursor)

    # ------------------------------------------------------------------
    # QgsMapTool overrides
    # ------------------------------------------------------------------

    def activate(self):
        super().activate()
        self._hint(_HINT)

    def deactivate(self):
        self._cancel_drag()
        self._clear_hover()
        self._hint("")
        super().deactivate()

    def canvasMoveEvent(self, e):
        if self._dragging is None:
            self._update_hover(e.pos())
            return

        target = self._to_layer_crs(e.snapPoint())
        if target is None:
            return
        self._drag_target = target
        self._hover_marker.setCenter(e.snapPoint())
        geom = annotations.preview_moved_node(self._layer, self._dragging, target)
        if geom is not None:
            self._show_preview(geom)

    def canvasPressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._hover_node is not None:
            self._start_drag(self._hover_node)
        elif self._hover_segment is not None:
            item_id, point = self._hover_segment
            if annotations.add_node(self._layer, item_id, point):
                self._notify()
                # Grab the inserted vertex so the user can keep dragging it
                self._update_hover(e.pos())
                if self._hover_node is not None:
                    self._start_drag(self._hover_node)

    def canvasReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self._delete_hovered_node()
            return
        if e.button() != Qt.MouseButton.LeftButton or self._dragging is None:
            return
        handle, target = self._dragging, self._drag_target
        self._cancel_drag()
        # A click without moving leaves the item untouched
        if target is not None and self._layer is not None and annotations.move_node(self._layer, handle, target):
            self._notify()
        self._update_hover(e.pos())

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self._dragging is not None:
            self._cancel_drag()
            self._clear_hover()
            e.accept()
        else:
            e.ignore()

    def dispose(self):
        """Remove canvas items owned by the tool (call on plugin unload)."""
        self._cancel_drag()
        if self._hover_marker is not None:
            self.canvas.scene().removeItem(self._hover_marker)
            self._hover_marker = None

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def _start_drag(self, handle: annotations.NodeHandle):
        self._dragging = handle
        self._drag_target = None

    def _delete_hovered_node(self):
        handle = self._hover_node
        if handle is None or self._layer is None or self._dragging is not None:
            return
        if not annotations.can_delete_node(self._layer, handle):
            # Points and minimal lines/polygons stay as they are — just tell the user why
            if annotations.item_kind(self._layer, handle.item_id) in (annotations.KIND_LINE, annotations.KIND_POLYGON):
                self._hint(_HINT_MIN_VERTICES)
            return
        if annotations.delete_node(self._layer, handle):
            self._notify()
        self._clear_hover()

    def _notify(self):
        if self._on_moved:
            self._on_moved()

    # ------------------------------------------------------------------
    # Hover / hit testing
    # ------------------------------------------------------------------

    def _update_hover(self, pos):
        """Highlight the node under ``pos``, or else the line/polygon outline under it."""
        self._layer = annotations.find_annotation_layer()
        self._hover_node = self._node_at(pos) if self._layer else None
        self._hover_segment = None
        if self._hover_node is None and self._layer:
            self._hover_segment = self._segment_at(pos)

        if self._hover_node is not None:
            self._show_hover_marker(self._hover_node.point, QgsVertexMarker.IconType.ICON_CIRCLE)
            self.canvas.setCursor(Qt.CursorShape.SizeAllCursor)
        elif self._hover_segment is not None:
            self._show_hover_marker(self._hover_segment[1], QgsVertexMarker.IconType.ICON_CROSS)
            self.canvas.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self._clear_hover()

    def _clear_hover(self):
        self._hover_node = None
        self._hover_segment = None
        if self._hover_marker is not None:
            self._hover_marker.hide()
        self.canvas.setCursor(Qt.CursorShape.ArrowCursor)

    def _show_hover_marker(self, layer_point: QgsPointXY, icon_type):
        center = self._to_canvas_crs(layer_point)
        if center is None:
            return
        self._hover_marker.setIconType(icon_type)
        self._hover_marker.setCenter(center)
        self._hover_marker.show()

    def _node_at(self, pos) -> annotations.NodeHandle | None:
        """Nearest node within the pick tolerance of the screen position ``pos``."""
        map_to_pixel = self.canvas.getCoordinateTransform()
        best, best_dist = None, _PICK_TOLERANCE_PX
        for handle in annotations.node_handles(self._layer):
            pt = self._to_canvas_crs(handle.point)
            if pt is None:
                continue
            px = map_to_pixel.transform(pt)
            dist = (QPointF(px.x(), px.y()) - QPointF(pos)).manhattanLength()
            if dist <= best_dist:
                best, best_dist = handle, dist
        return best

    def _segment_at(self, pos) -> tuple[str, QgsPointXY] | None:
        """Nearest line/polygon outline within the pick tolerance: (item_id, closest point in layer CRS)."""
        cursor = self.toMapCoordinates(pos)
        best, best_dist = None, _PICK_TOLERANCE_PX * self.canvas.mapUnitsPerPixel()
        to_canvas = self._layer_to_canvas_transform()
        for item_id, geom in annotations.edge_geometries(self._layer):
            if to_canvas is not None:
                try:
                    geom.transform(to_canvas)
                except Exception:
                    continue
            sqr_dist, closest, _after_vertex, _left_of = geom.closestSegmentWithContext(cursor)
            if sqr_dist < 0:
                continue
            dist = math.sqrt(sqr_dist)
            if dist <= best_dist:
                best, best_dist = (item_id, closest), dist
        if best is None:
            return None
        point = self._to_layer_crs(best[1])
        return (best[0], point) if point is not None else None

    # ------------------------------------------------------------------
    # Preview / CRS helpers
    # ------------------------------------------------------------------

    def _show_preview(self, geom):
        if self._rubber_band is None:
            self._rubber_band = QgsRubberBand(self.canvas, geom.type())
            self._rubber_band.setStrokeColor(_HIGHLIGHT)
            self._rubber_band.setFillColor(QColor(0, 120, 215, 40))
            self._rubber_band.setWidth(2)
            self._rubber_band.setIcon(QgsRubberBand.IconType.ICON_CIRCLE)
            self._rubber_band.setIconSize(10)
        self._rubber_band.setToGeometry(geom, self._layer.crs())

    def _cancel_drag(self):
        self._dragging = None
        self._drag_target = None
        if self._rubber_band is not None:
            self.canvas.scene().removeItem(self._rubber_band)
            self._rubber_band = None

    def _hint(self, text: str):
        if self._show_hint:
            self._show_hint(text)

    def _layer_to_canvas_transform(self) -> QgsCoordinateTransform | None:
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        if self._layer is None or self._layer.crs() == canvas_crs:
            return None
        return QgsCoordinateTransform(self._layer.crs(), canvas_crs, QgsProject.instance())

    def _to_canvas_crs(self, point: QgsPointXY) -> QgsPointXY | None:
        to_canvas = self._layer_to_canvas_transform()
        if to_canvas is None:
            return point
        try:
            return to_canvas.transform(point)
        except Exception:
            return None

    def _to_layer_crs(self, point: QgsPointXY) -> QgsPointXY | None:
        if self._layer is None:
            return None
        to_canvas = self._layer_to_canvas_transform()
        if to_canvas is None:
            return point
        try:
            return to_canvas.transform(point, Qgis.TransformDirection.Reverse)
        except Exception:
            logger.exception("Konnte Punkt nicht in Layer-KBS transformieren")
            return None
