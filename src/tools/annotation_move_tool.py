import math
from collections.abc import Callable

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRenderContext,
)
from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker
from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

from ..layer import annotations
from ..layout.mgrs_grid import point_to_mgrs
from ..logging_utils import get_logger
from .cursor_label import CursorLabel

logger = get_logger(__name__)

# Maximum distance between cursor and node/segment to grab it
_PICK_TOLERANCE_PX = 10
# Maximum distance between cursor and a corner of a description frame to grab it for scaling
_CORNER_TOLERANCE_PX = 8
# Just outside a frame corner (up to this distance) the cursor rotates the text — as in Figma
_ROTATE_TOLERANCE_PX = 24
# Shift while rotating snaps the text angle to multiples of this
_ROTATE_SNAP_DEG = 15

_HINT = (
    "Annotation bearbeiten: Punkt/Stützpunkt/Beschreibung/Längenangabe ziehen = verschieben, Schwerpunkt ⊕ ziehen = ganze "
    "Linie/Fläche verschieben, Ecke der Beschreibung ziehen = "
    "Textgröße ändern, knapp außerhalb einer Ecke ziehen = drehen (Umschalt = 15°-Schritte), Doppelklick auf Punkt/Beschreibung/⊕ = bearbeiten, Linksklick auf Linie/Umriss = Stützpunkt "
    "einfügen, Rechtsklick auf Stützpunkt = entfernen, Esc = abbrechen."
)
_HINT_MIN_VERTICES = (
    f"Stützpunkt kann nicht entfernt werden: Linien brauchen mindestens {annotations.MIN_LINE_VERTICES}, "
    f"Polygone mindestens {annotations.MIN_POLYGON_VERTICES} Stützpunkte."
)

_HIGHLIGHT = QColor(0, 120, 215)


def _polygon(points: list[QgsPointXY]) -> QgsGeometry:
    return QgsGeometry.fromPolygonXY([[*points, points[0]]])


def _scaled(points: list[QgsPointXY], pivot: QgsPointXY, factor: float) -> list[QgsPointXY]:
    """``points`` scaled by ``factor`` around ``pivot`` (the point that stays in place)."""
    return [
        QgsPointXY(pivot.x() + (p.x() - pivot.x()) * factor, pivot.y() + (p.y() - pivot.y()) * factor) for p in points
    ]


def _translated(points: list[QgsPointXY], dx: float, dy: float) -> list[QgsPointXY]:
    return [QgsPointXY(p.x() + dx, p.y() + dy) for p in points]


def _corner_cursor(frame: annotations.LabelFrame, corner_index: int):
    """Diagonal resize cursor matching where the corner lies relative to the (rotated) frame's center."""
    corner, center = frame.corners[corner_index], frame.center
    # Map y points up: corners up-right / down-left of the center get the "/" cursor
    rising = (corner.x() - center.x()) * (corner.y() - center.y()) > 0
    return Qt.CursorShape.SizeBDiagCursor if rising else Qt.CursorShape.SizeFDiagCursor


_rotate_cursor_cache: QCursor | None = None


def _rotate_cursor() -> QCursor:
    """Circular-arrow cursor for the rotation zones (Qt has no built-in rotate cursor)."""
    global _rotate_cursor_cache
    if _rotate_cursor_cache is None:
        pixmap = QPixmap(24, 24)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        arc = QPainterPath()
        arc.arcMoveTo(QRectF(5, 5, 14, 14), 60)
        arc.arcTo(QRectF(5, 5, 14, 14), 60, 270)
        head = QPolygonF([QPointF(16.5, 2.5), QPointF(20.5, 8.5), QPointF(13.5, 9.5)])
        for color, width in ((QColor(255, 255, 255), 4.0), (QColor(0, 0, 0), 1.8)):  # white halo, black arrow
            painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(arc)
            painter.setBrush(color)
            painter.drawPolygon(head)
        painter.end()
        _rotate_cursor_cache = QCursor(pixmap, 12, 12)
    return _rotate_cursor_cache


class AnnotationMoveTool(QgsMapTool):
    """Edit annotation geometries: drag points/vertices/descriptions, scale descriptions, insert/remove vertices.

    - Left-drag on a point or vertex moves it (rubber band preview, applied on release).
    - Left-drag on a description or measurement label moves the text independently of its
      object's vertices (a point's description still follows the marker when the point moves;
      measurement labels keep their manual offset to their edge when the object changes).
    - Hovering any description shows its frame — rotated with the text — with four corner
      handles; dragging a corner scales the text size (point descriptions around their
      center, line/polygon descriptions around their anchor). Dragging just outside a corner
      rotates the text around the frame center; Shift snaps to 15° steps.
    - While a description is hovered, moved or scaled, the object it belongs to
      is highlighted.
    - Every line and polygon shows its center of mass (⊕) while the tool is active;
      dragging it moves the whole object, including its description.
    - Double-click on a point, a description or a center of mass opens the edit dialog of its object.
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
        mgrs_resolution: Callable[[], float] | None = None,
    ):
        super().__init__(canvas)
        self.canvas = canvas
        self._show_hint = show_hint
        self._on_moved = on_moved
        # Called with an item id to open the edit dialog of that object
        self._on_edit = on_edit
        # Current MGRS resolution (m) for $POS in point descriptions — read when a point is moved
        self._mgrs_resolution = mgrs_resolution or (lambda: 1.0)
        self._layer = None

        # Hover state — at most one of these is set
        self._hover_node: annotations.NodeHandle | None = None
        # (label_id, frame, corner index) when hovering a frame corner (scaling)
        self._hover_corner: tuple[str, annotations.LabelFrame, int] | None = None
        # (label_id, frame, corner index) when hovering the rotation zone just outside a corner
        self._hover_rotate: tuple[str, annotations.LabelFrame, int] | None = None
        # (label_id, frame) when hovering a description
        self._hover_label: tuple[str, annotations.LabelFrame] | None = None
        # (item_id, point on the outline in layer CRS) when hovering a line/polygon outline
        self._hover_segment: tuple[str, QgsPointXY] | None = None
        # (item_id, center of mass in layer CRS) when hovering a line/polygon's center of mass
        self._hover_centroid: tuple[str, QgsPointXY] | None = None

        # Drag state — at most one of these is set
        self._dragging: annotations.NodeHandle | None = None
        self._drag_target: QgsPointXY | None = None  # layer CRS
        # (label_id, frame, grab point in layer CRS) while moving a description
        self._dragging_label: tuple[str, annotations.LabelFrame, QgsPointXY] | None = None
        self._label_offset: tuple[float, float] | None = None
        # (label_id, frame, pivot, grab distance from pivot, pivot is the frame center) — layer CRS —
        # while scaling a description
        self._scaling_label: tuple[str, annotations.LabelFrame, QgsPointXY, float, bool] | None = None
        self._scale_factor: float | None = None
        # (label_id, frame, pivot = frame center, grab direction in degrees ccw, text angle at start)
        # while rotating a description
        self._rotating_label: tuple[str, annotations.LabelFrame, QgsPointXY, float, float] | None = None
        self._rotation_delta: float | None = None  # clockwise degrees
        # (item_id, geometry, center of mass) — layer CRS — while moving a whole line/polygon
        self._dragging_item: tuple[str, QgsGeometry, QgsPointXY] | None = None
        self._item_offset: tuple[float, float] | None = None

        # Center-of-mass markers (circle + cross = ⊕) of all lines/polygons while the tool is active
        self._centroid_markers: list[QgsVertexMarker] = []
        self._watched_layer = None  # annotation layer whose repaints refresh the markers

        self._rubber_band: QgsRubberBand | None = None
        self._hover_band: QgsRubberBand | None = None  # frame of the hovered description
        self._owner_band: QgsRubberBand | None = None  # object the hovered/edited description belongs to
        self._corner_markers: list[QgsVertexMarker] = []

        self._coordinate_label = CursorLabel(canvas)  # MGRS coordinate of the dragged vertex

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
            # Old point descriptions carry leading spaces / left alignment
            annotations.migrate_legacy_labels(layer, self.canvas.mapSettings())
            # Every edit (here, in the dialog or the dock) repaints the layer — keep the ⊕ markers in sync
            layer.repaintRequested.connect(self._refresh_centroids)
            self._watched_layer = layer
        self._refresh_centroids()

    def deactivate(self):
        self._cancel_drag()
        self._clear_hover()
        self._unwatch_layer()
        self._clear_centroids()
        self._hint("")
        super().deactivate()

    def canvasMoveEvent(self, e):
        if self._rotating_label is not None:
            shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._rotate_label_to(e.mapPoint(), e.pos(), shift)
            return
        if self._dragging_item is not None:
            self._drag_item_to(e.snapPoint(), e.pos())
            return
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
        self._show_drag_coordinate(target, e.pos())
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
        elif self._hover_rotate is not None:
            self._start_rotating(self._hover_rotate[0], self._hover_rotate[1], e.mapPoint())
        elif self._hover_centroid is not None:
            item_id, center = self._hover_centroid
            geom = annotations.entry_geometry(self._layer, item_id)
            if geom is not None:
                self._dragging_item = (item_id, geom, center)
                self._item_offset = None
                self._hover_marker.hide()
        elif self._hover_label is not None:
            label_id, frame = self._hover_label
            grab = self._to_layer_crs(e.mapPoint())
            if grab is not None:
                self._dragging_label = (label_id, frame, grab)
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

        if self._rotating_label is not None:
            label_id, _frame, pivot, _start, _base = self._rotating_label
            delta = self._rotation_delta
            self._cancel_drag()
            if (
                delta is not None
                and abs(delta) > 1e-9
                and annotations.rotate_label(self._layer, label_id, delta, pivot)
            ):
                self._notify()
        elif self._dragging_item is not None:
            item_id, offset = self._dragging_item[0], self._item_offset
            self._cancel_drag()
            if offset is not None and annotations.translate_item(self._layer, item_id, *offset):
                self._notify()
        elif self._scaling_label is not None:
            label_id, _frame, pivot, _grab_dist, around_center = self._scaling_label
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
            if (
                target is not None
                and self._layer is not None
                and annotations.move_node(self._layer, handle, target, self._mgrs_resolution())
            ):
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
        if self._hover_node is not None:
            # Only a point's marker opens the dialog — line/polygon vertices stay drag-only
            if annotations.item_kind(self._layer, self._hover_node.item_id) != annotations.KIND_POINT:
                return
            target_id = self._hover_node.item_id
        elif self._hover_centroid is not None:
            target_id = self._hover_centroid[0]
        else:
            hit = self._hover_label or self._hover_corner or self._hover_rotate
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
        self._unwatch_layer()
        self._clear_centroids()
        self._coordinate_label.dispose()
        self._clear_label_frame()
        self._clear_owner()
        if self._hover_marker is not None:
            self.canvas.scene().removeItem(self._hover_marker)
            self._hover_marker = None

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def _is_dragging(self) -> bool:
        return (
            self._dragging is not None
            or self._dragging_label is not None
            or self._scaling_label is not None
            or self._dragging_item is not None
            or self._rotating_label is not None
        )

    def _start_drag(self, handle: annotations.NodeHandle):
        self._dragging = handle
        self._drag_target = None

    def _start_scaling(self, label_id: str, frame: annotations.LabelFrame, corner_index: int):
        """Begin scaling a description: point descriptions around their center, others around their anchor.

        Line/polygon descriptions are anchored at the bottom center, which keeps
        them sitting on their line/spot while they grow.
        """
        owner_id = annotations.label_owner(self._layer, label_id)
        around_center = owner_id is not None and annotations.item_kind(self._layer, owner_id) == annotations.KIND_POINT
        pivot = frame.center if around_center else frame.anchor
        grab_dist = pivot.distance(frame.corners[corner_index])
        if grab_dist <= 0:
            return
        self._scaling_label = (label_id, frame, pivot, grab_dist, around_center)
        self._scale_factor = None
        self._clear_label_frame()

    def _scale_label_to(self, map_point: QgsPointXY):
        """Preview the scaled text frame: the cursor's distance to the pivot sets the factor."""
        target = self._to_layer_crs(map_point)
        if target is None:
            return
        label_id, frame, pivot, grab_dist, _around_center = self._scaling_label
        factor = annotations.clamp_label_scale(self._layer, label_id, pivot.distance(target) / grab_dist)
        self._scale_factor = factor
        self._show_preview(_polygon(_scaled(frame.corners, pivot, factor)))

    def _start_rotating(self, label_id: str, frame: annotations.LabelFrame, map_point: QgsPointXY):
        """Begin rotating a description around its frame center."""
        grab = self._to_layer_crs(map_point)
        if grab is None:
            return
        pivot = frame.center
        start = math.degrees(math.atan2(grab.y() - pivot.y(), grab.x() - pivot.x()))
        self._rotating_label = (label_id, frame, pivot, start, annotations.label_angle(self._layer, label_id))
        self._rotation_delta = None
        self._clear_label_frame()

    def _rotate_label_to(self, map_point: QgsPointXY, pos, snap: bool):
        """Preview the rotated frame; the angle follows the cursor around the frame center."""
        target = self._to_layer_crs(map_point)
        if target is None:
            return
        _label_id, frame, pivot, start, base_angle = self._rotating_label
        current = math.degrees(math.atan2(target.y() - pivot.y(), target.x() - pivot.x()))
        delta = start - current  # counter-clockwise cursor movement → clockwise text angle
        if snap:
            snapped = round((base_angle + delta) / _ROTATE_SNAP_DEG) * _ROTATE_SNAP_DEG
            delta = snapped - base_angle
        self._rotation_delta = delta
        rotated = [annotations.rotate_point(p, pivot, delta) for p in frame.corners]
        self._show_preview(_polygon(rotated))
        angle = (base_angle + delta + 180) % 360 - 180
        self._coordinate_label.show_at(f"{angle:.0f}°", pos)

    def _show_drag_coordinate(self, layer_point: QgsPointXY, pos):
        """MGRS coordinate of the dragged vertex next to the cursor (project resolution)."""
        try:
            mgrs = point_to_mgrs(layer_point, self._layer.crs(), self._mgrs_resolution())
        except Exception:
            logger.exception("MGRS-Koordinate konnte nicht berechnet werden")
            mgrs = None
        self._coordinate_label.show_at(mgrs or "außerhalb UTM", pos)

    def _drag_item_to(self, map_point: QgsPointXY, pos):
        """Preview the whole line/polygon moved so its center of mass sits at the cursor."""
        target = self._to_layer_crs(map_point)
        if target is None:
            return
        _item_id, geom, center = self._dragging_item
        dx, dy = target.x() - center.x(), target.y() - center.y()
        self._item_offset = (dx, dy)
        moved = QgsGeometry(geom)
        moved.translate(dx, dy)
        self._show_preview(moved)
        self._show_drag_coordinate(target, pos)

    def _drag_label_to(self, map_point: QgsPointXY):
        """Preview the dragged description at the cursor (offset relative to the grab point)."""
        target = self._to_layer_crs(map_point)
        if target is None:
            return
        _label_id, frame, grab = self._dragging_label
        dx, dy = target.x() - grab.x(), target.y() - grab.y()
        self._label_offset = (dx, dy)
        self._show_preview(_polygon(_translated(frame.corners, dx, dy)))

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

        Priority: node, description frame corner (scale), rotation zone outside a corner,
        center of mass, description, line/polygon outline.
        """
        self._layer = annotations.find_annotation_layer()
        self._hover_node = self._hover_corner = self._hover_label = self._hover_segment = None
        self._hover_centroid = self._hover_rotate = None
        if self._layer:
            self._hover_node = self._node_at(pos)
            if self._hover_node is None:
                frames = self._label_frames()
                self._hover_corner = self._corner_at(pos, frames)
                if self._hover_corner is None:
                    self._hover_rotate = self._rotate_zone_at(pos, frames)
                if self._hover_corner is None and self._hover_rotate is None:
                    self._hover_centroid = self._centroid_at(pos)
                    if self._hover_centroid is None:
                        self._hover_label = self._label_at(pos, frames)
                        if self._hover_label is None:
                            self._hover_segment = self._segment_at(pos)

        self._clear_label_frame()
        self._clear_owner()
        if self._hover_node is not None:
            self._show_hover_marker(self._hover_node.point, QgsVertexMarker.IconType.ICON_CIRCLE)
            self.canvas.setCursor(Qt.CursorShape.SizeAllCursor)
        elif self._hover_corner is not None:
            label_id, frame, corner_index = self._hover_corner
            self._hover_marker.hide()
            self._show_owner(label_id)
            self._show_label_frame(frame)
            self.canvas.setCursor(_corner_cursor(frame, corner_index))
        elif self._hover_rotate is not None:
            label_id, frame, _corner_index = self._hover_rotate
            self._hover_marker.hide()
            self._show_owner(label_id)
            self._show_label_frame(frame)
            self.canvas.setCursor(_rotate_cursor())
        elif self._hover_centroid is not None:
            item_id, center = self._hover_centroid
            self._show_hover_marker(center, QgsVertexMarker.IconType.ICON_CIRCLE)
            self._highlight_item(item_id)
            self.canvas.setCursor(Qt.CursorShape.SizeAllCursor)
        elif self._hover_label is not None:
            label_id, frame = self._hover_label
            self._hover_marker.hide()
            self._show_owner(label_id)
            self._show_label_frame(frame)
            self.canvas.setCursor(Qt.CursorShape.SizeAllCursor)
        elif self._hover_segment is not None:
            self._show_hover_marker(self._hover_segment[1], QgsVertexMarker.IconType.ICON_CROSS)
            self.canvas.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self._clear_hover()

    def _clear_hover(self):
        self._hover_node = self._hover_corner = self._hover_label = self._hover_segment = None
        self._hover_centroid = self._hover_rotate = None
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

    def _centroid_at(self, pos) -> tuple[str, QgsPointXY] | None:
        """Nearest line/polygon center of mass within the pick tolerance of ``pos``."""
        best, best_dist = None, _PICK_TOLERANCE_PX
        for item_id, center in annotations.centroids(self._layer):
            dist = self._pixel_distance(center, pos)
            if dist is not None and dist <= best_dist:
                best, best_dist = (item_id, center), dist
        return best

    def _refresh_centroids(self, *_args):
        """Place a ⊕ marker on the center of mass of every line and polygon."""
        self._clear_centroids()
        layer = annotations.find_annotation_layer()
        if layer is None:
            return
        self._layer = layer
        for _item_id, center in annotations.centroids(layer):
            canvas_point = self._to_canvas_crs(center)
            if canvas_point is None:
                continue
            for icon_type in (QgsVertexMarker.IconType.ICON_CIRCLE, QgsVertexMarker.IconType.ICON_CROSS):
                marker = QgsVertexMarker(self.canvas)
                marker.setIconType(icon_type)
                marker.setIconSize(12)
                marker.setPenWidth(2)
                marker.setColor(_HIGHLIGHT)
                marker.setCenter(canvas_point)
                self._centroid_markers.append(marker)

    def _clear_centroids(self):
        for marker in self._centroid_markers:
            self.canvas.scene().removeItem(marker)
        self._centroid_markers = []

    def _unwatch_layer(self):
        if self._watched_layer is not None:
            try:
                self._watched_layer.repaintRequested.disconnect(self._refresh_centroids)
            except (TypeError, RuntimeError):
                pass  # already disconnected or the layer was deleted
            self._watched_layer = None

    def _label_frames(self) -> list[tuple[str, annotations.LabelFrame]]:
        """(label_id, frame rotated with the text) for every description at the current map scale."""
        context = QgsRenderContext.fromMapSettings(self.canvas.mapSettings())
        frames = []
        for label_id in annotations.description_labels(self._layer):
            frame = annotations.label_frame(self._layer, label_id, context)
            if frame is not None:
                frames.append((label_id, frame))
        return frames

    def _corner_at(self, pos, frames) -> tuple[str, annotations.LabelFrame, int] | None:
        """Nearest description frame corner within the corner tolerance of ``pos``."""
        best, best_dist = None, _CORNER_TOLERANCE_PX
        for label_id, frame in frames:
            for index, corner in enumerate(frame.corners):
                dist = self._pixel_distance(corner, pos)
                if dist is not None and dist <= best_dist:
                    best, best_dist = (label_id, frame, index), dist
        return best

    def _rotate_zone_at(self, pos, frames) -> tuple[str, annotations.LabelFrame, int] | None:
        """Frame corner whose rotation zone (just outside the corner, beyond the scale handle) contains ``pos``."""
        cursor = self._to_layer_crs(self.toMapCoordinates(pos))
        if cursor is None:
            return None
        cursor_geom = QgsGeometry.fromPointXY(cursor)
        best, best_dist = None, _ROTATE_TOLERANCE_PX
        for label_id, frame in frames:
            if frame.geometry().contains(cursor_geom):
                continue  # inside the text: moving, not rotating
            for index, corner in enumerate(frame.corners):
                dist = self._pixel_distance(corner, pos)
                if dist is not None and _CORNER_TOLERANCE_PX < dist <= best_dist:
                    best, best_dist = (label_id, frame, index), dist
        return best

    def _label_at(self, pos, frames) -> tuple[str, annotations.LabelFrame] | None:
        """Description whose (rotated) frame plus tolerance contains ``pos``; the smallest one wins."""
        cursor = self._to_layer_crs(self.toMapCoordinates(pos))
        if cursor is None:
            return None
        cursor_geom = QgsGeometry.fromPointXY(cursor)
        tolerance = _PICK_TOLERANCE_PX / 2 * self.canvas.mapUnitsPerPixel()
        best, best_area = None, None
        for label_id, frame in frames:
            geom = frame.geometry()
            if geom.distance(cursor_geom) <= tolerance and (best_area is None or geom.area() < best_area):
                best, best_area = (label_id, frame), geom.area()
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

    def _show_label_frame(self, frame: annotations.LabelFrame):
        """Blue frame around a description (rotated with the text) plus the four scaling handles."""
        if self._hover_band is None:
            self._hover_band = QgsRubberBand(self.canvas, Qgis.GeometryType.Polygon)
            self._hover_band.setStrokeColor(_HIGHLIGHT)
            self._hover_band.setFillColor(QColor(0, 120, 215, 25))
            self._hover_band.setWidth(1)
        self._hover_band.setToGeometry(frame.geometry(), self._layer.crs())

        if not self._corner_markers:
            for _ in range(4):
                marker = QgsVertexMarker(self.canvas)
                marker.setIconType(QgsVertexMarker.IconType.ICON_BOX)
                marker.setIconSize(8)
                marker.setPenWidth(2)
                marker.setColor(_HIGHLIGHT)
                marker.setFillColor(QColor(255, 255, 255))
                self._corner_markers.append(marker)
        for marker, corner in zip(self._corner_markers, frame.corners):
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
        if owner_id:
            self._highlight_item(owner_id)

    def _highlight_item(self, item_id: str):
        """Thick blue outline/fill on a point/line/polygon."""
        geom = annotations.entry_geometry(self._layer, item_id)
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
        self._dragging_item = None
        self._item_offset = None
        self._rotating_label = None
        self._rotation_delta = None
        self._coordinate_label.hide()
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
