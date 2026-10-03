import math
from collections.abc import Callable

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsRenderContext,
)
from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker
from qgis.PyQt.QtCore import QPointF, Qt
from qgis.PyQt.QtGui import QColor

from ..layer import annotations
from ..logging_utils import get_logger

logger = get_logger(__name__)

# Maximum distance between cursor and node/segment to grab it
_PICK_TOLERANCE_PX = 10
# Maximum distance between cursor and a corner of a description frame to grab it for scaling
_CORNER_TOLERANCE_PX = 8

_HINT = (
    "Annotation bearbeiten: Punkt/Stützpunkt/Beschreibung ziehen = verschieben, Ecke der Beschreibung ziehen = "
    "Textgröße ändern, Doppelklick auf Beschreibung = bearbeiten, Linksklick auf Linie/Umriss = Stützpunkt "
    "einfügen, Rechtsklick auf Stützpunkt = entfernen, Esc = abbrechen."
)
_HINT_MIN_VERTICES = (
    f"Stützpunkt kann nicht entfernt werden: Linien brauchen mindestens {annotations.MIN_LINE_VERTICES}, "
    f"Polygone mindestens {annotations.MIN_POLYGON_VERTICES} Stützpunkte."
)

_HIGHLIGHT = QColor(0, 120, 215)

# Frame corners in the order top-left, top-right, bottom-right, bottom-left, with matching resize cursors
_CORNER_CURSORS = (
    Qt.CursorShape.SizeFDiagCursor,
    Qt.CursorShape.SizeBDiagCursor,
    Qt.CursorShape.SizeFDiagCursor,
    Qt.CursorShape.SizeBDiagCursor,
)


def _corners(rect: QgsRectangle) -> list[QgsPointXY]:
    return [
        QgsPointXY(rect.xMinimum(), rect.yMaximum()),
        QgsPointXY(rect.xMaximum(), rect.yMaximum()),
        QgsPointXY(rect.xMaximum(), rect.yMinimum()),
        QgsPointXY(rect.xMinimum(), rect.yMinimum()),
    ]


def _scaled_rect(rect: QgsRectangle, anchor: QgsPointXY, factor: float) -> QgsRectangle:
    """``rect`` scaled by ``factor`` around ``anchor`` (the pivot that stays in place)."""
    return QgsRectangle(
        anchor.x() + (rect.xMinimum() - anchor.x()) * factor,
        anchor.y() + (rect.yMinimum() - anchor.y()) * factor,
        anchor.x() + (rect.xMaximum() - anchor.x()) * factor,
        anchor.y() + (rect.yMaximum() - anchor.y()) * factor,
    )


class AnnotationMoveTool(QgsMapTool):
    """Edit annotation geometries: drag points/vertices/descriptions, scale descriptions, insert/remove vertices.

    - Left-drag on a point or vertex moves it (rubber band preview, applied on release).
    - Left-drag on a description moves the text independently of its object's
      vertices (a point's description still follows the marker when the point moves).
    - Hovering any description shows its frame with four corner handles; dragging
      a corner scales the text size — point descriptions around their center,
      line/polygon descriptions around their anchor (bottom center).
    - While a description is hovered, moved or scaled, the object it belongs to
      is highlighted.
    - Double-click on a description opens the edit dialog of its object.
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
        on_edit: Callable[[str], None] | None = None,
    ):
        super().__init__(canvas)
        self.canvas = canvas
        self._show_hint = show_hint
        self._on_moved = on_moved
        # Called with an item id to open the edit dialog of that object
        self._on_edit = on_edit
        self._layer = None

        # Hover state — at most one of these is set
        self._hover_node: annotations.NodeHandle | None = None
        # (label_id, text extent in layer CRS, corner index) when hovering a frame corner
        self._hover_corner: tuple[str, QgsRectangle, int] | None = None
        # (label_id, text extent in layer CRS) when hovering a description
        self._hover_label: tuple[str, QgsRectangle] | None = None
        # (item_id, point on the outline in layer CRS) when hovering a line/polygon outline
        self._hover_segment: tuple[str, QgsPointXY] | None = None

        # Drag state — at most one of these is set
        self._dragging: annotations.NodeHandle | None = None
        self._drag_target: QgsPointXY | None = None  # layer CRS
        # (label_id, text extent, grab point) — both in layer CRS — while moving a description
        self._dragging_label: tuple[str, QgsRectangle, QgsPointXY] | None = None
        self._label_offset: tuple[float, float] | None = None
        # (label_id, text extent, pivot, grab distance from pivot, pivot is the frame center) — layer CRS —
        # while scaling a description
        self._scaling_label: tuple[str, QgsRectangle, QgsPointXY, float, bool] | None = None
        self._scale_factor: float | None = None

        self._rubber_band: QgsRubberBand | None = None
        self._hover_band: QgsRubberBand | None = None  # frame of the hovered description
        self._owner_band: QgsRubberBand | None = None  # object the hovered/edited description belongs to
        self._corner_markers: list[QgsVertexMarker] = []

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
        layer = annotations.find_annotation_layer()
        if layer is not None:
            # Old point descriptions carry leading spaces that would show up inside the frame
            annotations.remove_legacy_indents(layer, self.canvas.mapSettings())

    def deactivate(self):
        self._cancel_drag()
        self._clear_hover()
        self._hint("")
        super().deactivate()

    def canvasMoveEvent(self, e):
        if self._scaling_label is not None:
            self._scale_label_to(e.mapPoint())
            return
        if self._dragging_label is not None:
            self._drag_label_to(e.mapPoint())
            return
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
        elif self._hover_corner is not None:
            self._start_scaling(*self._hover_corner)
        elif self._hover_label is not None:
            label_id, rect = self._hover_label
            grab = self._to_layer_crs(e.mapPoint())
            if grab is not None:
                self._dragging_label = (label_id, rect, grab)
                self._label_offset = None
                self._clear_label_frame()
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
        if e.button() != Qt.MouseButton.LeftButton:
            return

        if self._scaling_label is not None:
            label_id, _rect, pivot, _grab_dist, around_center = self._scaling_label
            factor = self._scale_factor
            self._cancel_drag()
            if factor is not None and annotations.scale_label(
                self._layer, label_id, factor, pivot if around_center else None
            ):
                self._notify()
        elif self._dragging_label is not None:
            label_id, offset = self._dragging_label[0], self._label_offset
            self._cancel_drag()
            if offset is not None and annotations.translate_label(self._layer, label_id, *offset):
                self._notify()
        elif self._dragging is not None:
            handle, target = self._dragging, self._drag_target
            self._cancel_drag()
            # A click without moving leaves the item untouched
            if target is not None and self._layer is not None and annotations.move_node(self._layer, handle, target):
                self._notify()
        else:
            return
        self._update_hover(e.pos())

    def canvasDoubleClickEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self._on_edit is None:
            return
        # The preceding press/release pair may have started a (zero-length) label drag
        self._cancel_drag()
        self._update_hover(e.pos())
        hit = self._hover_label or self._hover_corner
        if hit is None:
            return
        label_id = hit[0]
        # Descriptions open their point/line/polygon; standalone texts open themselves
        target_id = annotations.label_owner(self._layer, label_id) or label_id
        self._clear_hover()
        self._on_edit(target_id)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self._is_dragging():
            self._cancel_drag()
            self._clear_hover()
            e.accept()
        else:
            e.ignore()

    def dispose(self):
        """Remove canvas items owned by the tool (call on plugin unload)."""
        self._cancel_drag()
        self._clear_label_frame()
        self._clear_owner()
        if self._hover_marker is not None:
            self.canvas.scene().removeItem(self._hover_marker)
            self._hover_marker = None

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def _is_dragging(self) -> bool:
        return self._dragging is not None or self._dragging_label is not None or self._scaling_label is not None

    def _start_drag(self, handle: annotations.NodeHandle):
        self._dragging = handle
        self._drag_target = None

    def _start_scaling(self, label_id: str, rect: QgsRectangle, corner_index: int):
        """Begin scaling a description: point descriptions around their center, others around their anchor.

        Line/polygon descriptions are anchored at the bottom center, which keeps
        them sitting on their line/spot while they grow.
        """
        owner_id = annotations.label_owner(self._layer, label_id)
        around_center = owner_id is not None and annotations.item_kind(self._layer, owner_id) == annotations.KIND_POINT
        pivot = rect.center() if around_center else annotations.label_anchor(self._layer, label_id)
        if pivot is None:
            return
        grab_dist = pivot.distance(_corners(rect)[corner_index])
        if grab_dist <= 0:
            return
        self._scaling_label = (label_id, rect, pivot, grab_dist, around_center)
        self._scale_factor = None
        self._clear_label_frame()

    def _scale_label_to(self, map_point: QgsPointXY):
        """Preview the scaled text frame: the cursor's distance to the pivot sets the factor."""
        target = self._to_layer_crs(map_point)
        if target is None:
            return
        label_id, rect, pivot, grab_dist, _around_center = self._scaling_label
        factor = annotations.clamp_label_scale(self._layer, label_id, pivot.distance(target) / grab_dist)
        self._scale_factor = factor
        self._show_preview(QgsGeometry.fromRect(_scaled_rect(rect, pivot, factor)))

    def _drag_label_to(self, map_point: QgsPointXY):
        """Preview the dragged description at the cursor (offset relative to the grab point)."""
        target = self._to_layer_crs(map_point)
        if target is None:
            return
        _label_id, rect, grab = self._dragging_label
        dx, dy = target.x() - grab.x(), target.y() - grab.y()
        self._label_offset = (dx, dy)
        moved = QgsRectangle(rect.xMinimum() + dx, rect.yMinimum() + dy, rect.xMaximum() + dx, rect.yMaximum() + dy)
        self._show_preview(QgsGeometry.fromRect(moved))

    def _delete_hovered_node(self):
        handle = self._hover_node
        if handle is None or self._layer is None or self._is_dragging():
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
        """Highlight what is under ``pos``.

        Priority: node, then a description frame corner, then a description,
        then a line/polygon outline.
        """
        self._layer = annotations.find_annotation_layer()
        self._hover_node = self._hover_corner = self._hover_label = self._hover_segment = None
        if self._layer:
            self._hover_node = self._node_at(pos)
            if self._hover_node is None:
                frames = self._label_frames()
                self._hover_corner = self._corner_at(pos, frames)
                if self._hover_corner is None:
                    self._hover_label = self._label_at(pos, frames)
                    if self._hover_label is None:
                        self._hover_segment = self._segment_at(pos)

        self._clear_label_frame()
        self._clear_owner()
        if self._hover_node is not None:
            self._show_hover_marker(self._hover_node.point, QgsVertexMarker.IconType.ICON_CIRCLE)
            self.canvas.setCursor(Qt.CursorShape.SizeAllCursor)
        elif self._hover_corner is not None:
            label_id, rect, corner_index = self._hover_corner
            self._hover_marker.hide()
            self._show_owner(label_id)
            self._show_label_frame(rect)
            self.canvas.setCursor(_CORNER_CURSORS[corner_index])
        elif self._hover_label is not None:
            label_id, rect = self._hover_label
            self._hover_marker.hide()
            self._show_owner(label_id)
            self._show_label_frame(rect)
            self.canvas.setCursor(Qt.CursorShape.SizeAllCursor)
        elif self._hover_segment is not None:
            self._show_hover_marker(self._hover_segment[1], QgsVertexMarker.IconType.ICON_CROSS)
            self.canvas.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self._clear_hover()

    def _clear_hover(self):
        self._hover_node = self._hover_corner = self._hover_label = self._hover_segment = None
        self._clear_label_frame()
        self._clear_owner()
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
        best, best_dist = None, _PICK_TOLERANCE_PX
        for handle in annotations.node_handles(self._layer):
            dist = self._pixel_distance(handle.point, pos)
            if dist is not None and dist <= best_dist:
                best, best_dist = handle, dist
        return best

    def _label_frames(self) -> list[tuple[str, QgsRectangle]]:
        """(label_id, text extent in layer CRS) for every description at the current map scale."""
        context = QgsRenderContext.fromMapSettings(self.canvas.mapSettings())
        frames = []
        for label_id in annotations.description_labels(self._layer):
            rect = annotations.label_bounds(self._layer, label_id, context)
            if rect is not None and not rect.isNull():
                frames.append((label_id, rect))
        return frames

    def _corner_at(self, pos, frames) -> tuple[str, QgsRectangle, int] | None:
        """Nearest description frame corner within the corner tolerance of ``pos``."""
        best, best_dist = None, _CORNER_TOLERANCE_PX
        for label_id, rect in frames:
            for index, corner in enumerate(_corners(rect)):
                dist = self._pixel_distance(corner, pos)
                if dist is not None and dist <= best_dist:
                    best, best_dist = (label_id, rect, index), dist
        return best

    def _label_at(self, pos, frames) -> tuple[str, QgsRectangle] | None:
        """Description whose text extent (plus tolerance) contains ``pos``; the smallest one wins."""
        cursor = self.toMapCoordinates(pos)
        tolerance = _PICK_TOLERANCE_PX / 2 * self.canvas.mapUnitsPerPixel()
        to_canvas = self._layer_to_canvas_transform()
        best, best_area = None, None
        for label_id, rect in frames:
            try:
                canvas_rect = to_canvas.transformBoundingBox(rect) if to_canvas else QgsRectangle(rect)
            except Exception:
                continue
            canvas_rect.grow(tolerance)
            if canvas_rect.contains(cursor) and (best_area is None or rect.area() < best_area):
                best, best_area = (label_id, rect), rect.area()
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

    def _pixel_distance(self, layer_point: QgsPointXY, pos) -> float | None:
        """Screen distance (pixels, Manhattan) between a layer-CRS point and the screen position ``pos``."""
        pt = self._to_canvas_crs(layer_point)
        if pt is None:
            return None
        px = self.canvas.getCoordinateTransform().transform(pt)
        return (QPointF(px.x(), px.y()) - QPointF(pos)).manhattanLength()

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

    def _show_label_frame(self, rect: QgsRectangle):
        """Blue frame around a description plus the four scaling handles at its corners."""
        if self._hover_band is None:
            self._hover_band = QgsRubberBand(self.canvas, Qgis.GeometryType.Polygon)
            self._hover_band.setStrokeColor(_HIGHLIGHT)
            self._hover_band.setFillColor(QColor(0, 120, 215, 25))
            self._hover_band.setWidth(1)
        self._hover_band.setToGeometry(QgsGeometry.fromRect(rect), self._layer.crs())

        if not self._corner_markers:
            for _ in range(4):
                marker = QgsVertexMarker(self.canvas)
                marker.setIconType(QgsVertexMarker.IconType.ICON_BOX)
                marker.setIconSize(8)
                marker.setPenWidth(2)
                marker.setColor(_HIGHLIGHT)
                marker.setFillColor(QColor(255, 255, 255))
                self._corner_markers.append(marker)
        for marker, corner in zip(self._corner_markers, _corners(rect)):
            center = self._to_canvas_crs(corner)
            if center is not None:
                marker.setCenter(center)
                marker.show()

    def _clear_label_frame(self):
        if self._hover_band is not None:
            self.canvas.scene().removeItem(self._hover_band)
            self._hover_band = None
        for marker in self._corner_markers:
            self.canvas.scene().removeItem(marker)
        self._corner_markers = []

    def _show_owner(self, label_id: str):
        """Highlight the point/line/polygon the description ``label_id`` belongs to."""
        owner_id = annotations.label_owner(self._layer, label_id)
        geom = annotations.entry_geometry(self._layer, owner_id) if owner_id else None
        if geom is None or geom.isEmpty():
            return
        self._clear_owner()
        band = QgsRubberBand(self.canvas, geom.type())
        band.setStrokeColor(_HIGHLIGHT)
        band.setFillColor(QColor(0, 120, 215, 50))
        band.setWidth(4)
        band.setIcon(QgsRubberBand.IconType.ICON_CIRCLE)
        band.setIconSize(18)
        band.setToGeometry(geom, self._layer.crs())
        self._owner_band = band

    def _clear_owner(self):
        if self._owner_band is not None:
            self.canvas.scene().removeItem(self._owner_band)
            self._owner_band = None

    def _cancel_drag(self):
        self._dragging = None
        self._drag_target = None
        self._dragging_label = None
        self._label_offset = None
        self._scaling_label = None
        self._scale_factor = None
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
