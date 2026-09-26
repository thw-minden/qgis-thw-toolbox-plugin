import math
import time
from dataclasses import dataclass

from qgis.core import QgsGeometry, QgsMarkerSymbol, QgsPointXY
from qgis.gui import QgsMapTool
from qgis.PyQt.QtCore import QPointF, Qt

from ..layer.renderer import (
    category_value,
    feature_symbol,
    replace_feature_symbol,
    set_symbol_size_and_rotation,
    svg_symbol_layer,
)
from ._feature_search import find_nearest_feature
from .selection_frame import HANDLE_SIZE_PX, MarkerFrame, SelectionFrameItem, resize_cursor, rotate_cursor

# Grab distance around a corner handle (pixels)
_HANDLE_GRAB_PX = HANDLE_SIZE_PX / 2.0 + 3.0
# How far outside a corner the rotate zone reaches (pixels)
_ROTATE_ZONE_PX = 22.0
# Below this anchor→handle distance, scaling around the anchor gets too jumpy
_MIN_PIVOT_DISTANCE_PX = 12.0
# Same range as the FeatureDock size spinbox
_MIN_SIZE = 10
_MAX_SIZE = 2000
# Shift + rotate snaps to this step (like Figma)
_ROTATION_SNAP_DEG = 15
# Live preview repaint interval while resizing/rotating
_PREVIEW_INTERVAL_MS = 40
# Pointer travel before a press on a marker counts as a drag
_DRAG_THRESHOLD_PX = 3


@dataclass
class _TransformDrag:
    """An in-progress resize or rotate gesture on the selected marker."""

    mode: str  # "resize" | "rotate"
    fid: int
    unique_id: str
    map_point: QgsPointXY
    symbol: QgsMarkerSymbol  # working copy, mutated while dragging
    frame: MarkerFrame  # frame at press time
    handle: QPointF  # grabbed corner
    press_pos: QPointF
    start_size: float
    start_rotation: float
    size: float
    rotation: float
    last_preview_ms: float = 0.0
    previewed: bool = False


class MoveTool(QgsMapTool):
    """Map tool for selecting, dragging, resizing and rotating markers.

    On press: if the click is on a feature, that feature is selected
    (Figma-style frame with corner handles) and becomes draggable;
    otherwise the selection is cleared and standard map panning starts.
    Dragging a corner handle resizes the marker around its anchor point,
    dragging just outside a corner rotates it (Shift snaps to 15°).
    While moving, the layer is held in editing mode and committed on
    release. Hover detection switches the cursor accordingly.
    """

    def __init__(self, canvas, layer_manager):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer_manager = layer_manager
        self.layer = layer_manager.layer
        self.moving_feature = None
        self.is_move_mode = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.is_panning = False
        self._pan_dragged = False
        self.last_pos = None
        self.update_timer = None
        self.last_update_time = 0
        self.update_interval = 100  # ms
        self.update_threshold = 0.1  # Map Units
        self.is_editing = False
        self.last_canvas_update = 0
        self.last_dock_update = 0
        self.selected_fid = None
        self.selection = SelectionFrameItem(canvas)
        self._transform: _TransformDrag | None = None
        self._press_pos: QPointF | None = None
        # Map offset from the pointer to the grabbed marker's anchor, so the
        # marker doesn't jump to the pointer when grabbed off-centre.
        self._grab_offset = (0.0, 0.0)

    def _layer_is_usable(self):
        """True solange der Layer existiert und sein C++-Objekt nicht gelöscht wurde."""
        if self.layer is None:
            return False
        try:
            self.layer.id()
        except RuntimeError:
            self.layer = None
            return False
        return True

    def _feature_dock(self):
        return getattr(getattr(self.layer_manager, "ident_tool", None), "feature_dock", None)

    def set_move_mode(self, enabled):
        self.is_move_mode = enabled
        if enabled:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.clear_selection()
        self.moving_feature = None

    def deactivate(self):
        self.clear_selection()
        super().deactivate()

    # ------------------------------------------------------------------
    # Selection frame
    # ------------------------------------------------------------------

    def select_feature(self, fid):
        self.selected_fid = fid
        self.refresh_selection()

    def clear_selection(self):
        self.selected_fid = None
        self._transform = None
        self.selection.set_marker(None, None)

    def refresh_selection(self):
        """Re-read the selected marker from the layer after attribute or geometry changes."""
        if self.selected_fid is None or not self._layer_is_usable():
            self.selection.set_marker(None, None)
            return
        feature = self.layer.getFeature(self.selected_fid)
        if not feature.isValid() or not feature.hasGeometry():
            self.clear_selection()
            return
        symbol = feature_symbol(self.layer, category_value(feature))
        map_point = self.canvas.mapSettings().layerToMapCoordinates(self.layer, feature.geometry().asPoint())
        self.selection.set_marker(map_point, symbol)

    def dispose(self):
        """Remove the selection frame from the canvas (plugin unload)."""
        self.clear_selection()
        self.selection.dispose()

    def _hit_frame(self, pos: QPointF):
        """Which part of the selection frame is under `pos`: ("resize"|"rotate", corner), ("move", None) or None."""
        frame = self.selection.frame
        if frame is None:
            return None
        if frame.has_handles():
            for index, corner in enumerate(frame.corners):
                if max(abs(pos.x() - corner.x()), abs(pos.y() - corner.y())) <= _HANDLE_GRAB_PX:
                    return "resize", index
        if frame.contains(pos):
            return "move", None
        if frame.has_handles():
            for index, corner in enumerate(frame.corners):
                if math.dist((pos.x(), pos.y()), (corner.x(), corner.y())) <= _ROTATE_ZONE_PX:
                    return "rotate", index
        return None

    def _cursor_for(self, hit):
        mode, corner_index = hit
        frame = self.selection.frame
        if mode == "resize" and frame is not None:
            corner, center = frame.corners[corner_index], frame.center
            return resize_cursor(QPointF(corner.x() - center.x(), corner.y() - center.y()))
        if mode == "rotate":
            return rotate_cursor()
        return Qt.CursorShape.PointingHandCursor

    # ------------------------------------------------------------------
    # Resize / rotate
    # ------------------------------------------------------------------

    def _begin_transform(self, mode, corner_index, pos: QPointF):
        frame = self.selection.frame
        feature = self.layer.getFeature(self.selected_fid)
        if frame is None or not feature.isValid():
            return
        unique_id = category_value(feature)
        symbol = feature_symbol(self.layer, unique_id)
        svg = svg_symbol_layer(symbol) if symbol is not None else None
        if svg is None:
            return
        map_point = self.canvas.mapSettings().layerToMapCoordinates(self.layer, feature.geometry().asPoint())
        self._transform = _TransformDrag(
            mode=mode,
            fid=feature.id(),
            unique_id=unique_id,
            map_point=map_point,
            symbol=symbol,
            frame=frame,
            handle=frame.corners[corner_index],
            press_pos=pos,
            start_size=svg.size(),
            start_rotation=svg.angle(),
            size=svg.size(),
            rotation=svg.angle(),
        )
        self.setCursor(self._cursor_for((mode, corner_index)))

    def _update_transform(self, pos: QPointF, modifiers):
        drag = self._transform
        if drag.mode == "resize":
            drag.size = self._resized(drag, pos)
        else:
            snap = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
            drag.rotation = self._rotated(drag, pos, snap)

        set_symbol_size_and_rotation(drag.symbol, drag.size, drag.rotation)
        self.selection.set_marker(drag.map_point, drag.symbol)

        now = time.time() * 1000
        if now - drag.last_preview_ms >= _PREVIEW_INTERVAL_MS:
            replace_feature_symbol(self.layer, drag.unique_id, drag.symbol)
            drag.last_preview_ms = now
            drag.previewed = True

    @staticmethod
    def _resized(drag: _TransformDrag, pos: QPointF) -> float:
        """Scale around the anchor so the grabbed corner follows the pointer.

        The anchor is the marker's georeferenced position, so it must not
        move. Each corner's offset from the anchor is proportional to the
        size, so projecting the pointer onto that offset gives the factor.
        """
        pivot = drag.frame.anchor
        vx, vy = drag.handle.x() - pivot.x(), drag.handle.y() - pivot.y()
        if math.hypot(vx, vy) < _MIN_PIVOT_DISTANCE_PX:
            # Grabbed the corner the marker is anchored to — measure from the centre instead
            pivot = drag.frame.center
            vx, vy = drag.handle.x() - pivot.x(), drag.handle.y() - pivot.y()
        factor = ((pos.x() - pivot.x()) * vx + (pos.y() - pivot.y()) * vy) / (vx * vx + vy * vy)
        size = round(drag.start_size * max(factor, 0.0))
        return float(min(max(size, _MIN_SIZE), _MAX_SIZE))

    @staticmethod
    def _rotated(drag: _TransformDrag, pos: QPointF, snap: bool) -> float:
        """Rotate around the anchor (that is where QGIS pivots marker rotation)."""
        pivot = drag.frame.anchor

        def pointer_angle(p: QPointF) -> float:
            return math.degrees(math.atan2(p.y() - pivot.y(), p.x() - pivot.x()))

        rotation = drag.start_rotation + pointer_angle(pos) - pointer_angle(drag.press_pos)
        step = _ROTATION_SNAP_DEG if snap else 1
        rotation = round(rotation / step) * step
        return float(((rotation + 180) % 360) - 180)

    def _finish_transform(self):
        drag, self._transform = self._transform, None
        if drag.mode == "resize" and drag.size != drag.start_size:
            self.layer_manager.resize_feature(drag.fid, drag.size)
        elif drag.mode == "rotate" and drag.rotation != drag.start_rotation:
            self.layer_manager.rotate_feature(drag.fid, drag.rotation)
        elif drag.previewed:
            # Dragged back to the start: undo the preview symbol
            set_symbol_size_and_rotation(drag.symbol, drag.start_size, drag.start_rotation)
            replace_feature_symbol(self.layer, drag.unique_id, drag.symbol)

        self.refresh_selection()
        self._show_selected_in_dock()

    def _drag_target(self, pos) -> QgsPointXY:
        pointer = self.canvas.getCoordinateTransform().toMapCoordinates(pos.x(), pos.y())
        return QgsPointXY(pointer.x() + self._grab_offset[0], pointer.y() + self._grab_offset[1])

    def _show_selected_in_dock(self):
        dock = self._feature_dock()
        if dock is None or self.selected_fid is None:
            return
        feature = self.layer.getFeature(self.selected_fid)
        if feature.isValid():
            dock.show_feature(feature, self.layer_manager)

    # ------------------------------------------------------------------
    # Canvas events
    # ------------------------------------------------------------------

    def canvasMoveEvent(self, event):
        if not self._layer_is_usable():
            return
        pos = QPointF(event.pos())
        if self._transform is not None:
            self._update_transform(pos, event.modifiers())
            return
        if self.is_panning:
            # Same as QgsMapToolPan: shift the rendered map while dragging,
            # re-render once on release.
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._pan_dragged = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.canvas.panAction(event)
            return

        # Hover detection: throttled to 100ms to avoid scanning the layer every pixel.
        if not self.moving_feature:
            hit = self._hit_frame(pos)
            if hit is not None:
                self.setCursor(self._cursor_for(hit))
            else:
                current_time = time.time() * 1000
                if current_time - self.last_update_time > 100:
                    point = self.canvas.getCoordinateTransform().toMapCoordinates(event.pos().x(), event.pos().y())
                    closest = find_nearest_feature(self.layer, self.canvas, point)

                    if closest:
                        self.setCursor(Qt.CursorShape.PointingHandCursor)
                    else:
                        self.setCursor(Qt.CursorShape.ArrowCursor)

                    self.last_update_time = current_time
        else:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

        if self.moving_feature:
            point = self._drag_target(event.pos())
            if self.moving_feature.id() == self.selected_fid:
                self.selection.move_to(point)
            current_time = time.time() * 1000

            should_update = False
            if self.last_pos is None:
                should_update = True
            elif point.distance(self.last_pos) > self.update_threshold:
                if current_time - self.last_update_time > self.update_interval:
                    should_update = True

            if should_update:
                if not self.is_editing:
                    self.layer.startEditing()
                    self.is_editing = True

                self.layer.changeGeometry(self.moving_feature.id(), QgsGeometry.fromPointXY(point))
                self.last_pos = point
                self.last_update_time = current_time

                # Throttle dock updates separately (300ms): UI refresh is more expensive.
                if current_time - self.last_dock_update > 300:
                    dock = self._feature_dock()
                    if dock is not None:
                        feature = self.layer.getFeature(self.moving_feature.id())
                        if feature.isValid():
                            dock.show_feature(feature, self.layer_manager)
                        self.last_dock_update = current_time

                if current_time - self.last_canvas_update > 150:
                    self.canvas.refresh()
                    self.last_canvas_update = current_time

    def canvasPressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self._layer_is_usable():
            return

        pos = QPointF(event.pos())
        hit = self._hit_frame(pos)
        if hit is not None and hit[0] in ("resize", "rotate"):
            self._begin_transform(hit[0], hit[1], pos)
            return

        point = self.canvas.getCoordinateTransform().toMapCoordinates(event.pos().x(), event.pos().y())
        self.last_pos = point
        self._press_pos = pos

        # Like Figma: anywhere inside the frame grabs the selected marker,
        # even if another marker's point is closer.
        if hit is not None:
            selected = self.layer.getFeature(self.selected_fid)
            closest = selected if selected.isValid() else None
        else:
            closest = find_nearest_feature(self.layer, self.canvas, point)

        dock = self._feature_dock()
        if closest:
            self.moving_feature = closest
            anchor = self.canvas.mapSettings().layerToMapCoordinates(self.layer, closest.geometry().asPoint())
            self._grab_offset = (anchor.x() - point.x(), anchor.y() - point.y())
            if closest.id() != self.selected_fid:
                self.select_feature(closest.id())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if dock is not None:
                dock.show_feature(closest, self.layer_manager)
        else:
            self.moving_feature = None
            self.set_move_mode(False)
            if dock is not None:
                dock.show_placeholder()
                dock.show()
            self.is_panning = True
            self._pan_dragged = False

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._transform is not None:
                self._finish_transform()
                hit = self._hit_frame(QPointF(event.pos()))
                self.setCursor(self._cursor_for(hit) if hit is not None else Qt.CursorShape.ArrowCursor)
            elif self.moving_feature:
                pos = QPointF(event.pos())
                dragged = self._press_pos is not None and (
                    max(abs(pos.x() - self._press_pos.x()), abs(pos.y() - self._press_pos.y())) >= _DRAG_THRESHOLD_PX
                )
                if dragged or self.is_editing:
                    # Moves are throttled — write the final position so the
                    # marker ends up exactly where it was dropped.
                    point = self._drag_target(event.pos())
                    if not self.is_editing:
                        self.layer.startEditing()
                        self.is_editing = True
                    self.layer.changeGeometry(self.moving_feature.id(), QgsGeometry.fromPointXY(point))
                if self.is_editing:
                    self.layer.commitChanges()
                    self.is_editing = False
                    self.refresh_selection()
                    self._show_selected_in_dock()

                self.moving_feature = None
                self.last_pos = None
                self._press_pos = None
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            elif self.is_panning:
                self.is_panning = False
                if self._pan_dragged:
                    self._pan_dragged = False
                    self.canvas.panActionEnd(event.pos())
                self.setCursor(Qt.CursorShape.ArrowCursor)
