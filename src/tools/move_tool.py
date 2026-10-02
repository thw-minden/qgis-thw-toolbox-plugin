import time

from qgis.core import QgsGeometry, QgsPointXY
from qgis.gui import QgsMapTool
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QApplication

from ._feature_search import find_nearest_feature


class MoveTool(QgsMapTool):
    """Map tool for dragging features and panning the canvas.

    On press: if the click is on a feature, that feature becomes draggable
    (move mode); otherwise standard map panning starts. Dragging starts once
    the mouse moved past the system drag distance, so a plain click never
    nudges the feature; while dragging, the layer is held in editing mode and
    committed on release. Hover detection switches the cursor to indicate
    draggable features.
    """

    def __init__(self, canvas, layer_manager):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer_manager = layer_manager
        self.layer = layer_manager.layer
        self.moving_feature = None
        self.press_pos = None
        self.dragging = False
        self.is_move_mode = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.pan_start = None
        self.is_panning = False
        self.last_center = None
        self.last_pos = None
        self.update_timer = None
        self.last_update_time = 0
        self.update_interval = 100  # ms
        self.update_threshold = 0.1  # Map Units
        self.is_editing = False
        self.last_canvas_update = 0
        self.last_dock_update = 0

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

    def set_move_mode(self, enabled):
        self.is_move_mode = enabled
        if enabled:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.moving_feature = None

    def canvasMoveEvent(self, event):
        if not self._layer_is_usable():
            return
        # Hover detection: throttled to 100ms to avoid scanning the layer every pixel.
        if not self.moving_feature:
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
            if not self.dragging:
                if (
                    self.press_pos is not None
                    and (event.pos() - self.press_pos).manhattanLength() < QApplication.startDragDistance()
                ):
                    return
                self.dragging = True
            point = self.canvas.getCoordinateTransform().toMapCoordinates(event.pos().x(), event.pos().y())
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

                layer_point = self.toLayerCoordinates(self.layer, point)
                self.layer.changeGeometry(self.moving_feature.id(), QgsGeometry.fromPointXY(layer_point))
                self.last_pos = point
                self.last_update_time = current_time

                # Throttle dock updates separately (300ms): UI refresh is more expensive.
                if current_time - self.last_dock_update > 300:
                    if hasattr(self.layer_manager, "ident_tool") and hasattr(
                        self.layer_manager.ident_tool, "feature_dock"
                    ):
                        feature = self.layer.getFeature(self.moving_feature.id())
                        if feature.isValid():
                            self.layer_manager.ident_tool.feature_dock.show_feature(feature, self.layer_manager)
                        self.last_dock_update = current_time

                if current_time - self.last_canvas_update > 150:
                    self.canvas.refresh()
                    self.last_canvas_update = current_time
        elif not self.is_panning and self.pan_start and self.last_center:
            dx = event.pos().x() - self.pan_start.x()
            dy = event.pos().y() - self.pan_start.y()

            map_units_per_pixel = self.canvas.mapUnitsPerPixel()
            new_center_x = self.last_center.x() - (dx * map_units_per_pixel)
            new_center_y = self.last_center.y() + (dy * map_units_per_pixel)

            self.canvas.setCenter(QgsPointXY(new_center_x, new_center_y))
            self.canvas.refresh()

    def canvasPressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self._layer_is_usable():
            return

        point = self.canvas.getCoordinateTransform().toMapCoordinates(event.pos().x(), event.pos().y())
        self.last_pos = point

        closest = find_nearest_feature(self.layer, self.canvas, point)

        if closest:
            self.moving_feature = closest
            self.press_pos = event.pos()
            self.dragging = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if hasattr(self.layer_manager, "ident_tool") and hasattr(self.layer_manager.ident_tool, "feature_dock"):
                self.layer_manager.ident_tool.feature_dock.show_feature(closest, self.layer_manager)
        else:
            self.moving_feature = None
            self.set_move_mode(False)
            if hasattr(self.layer_manager, "ident_tool") and hasattr(self.layer_manager.ident_tool, "feature_dock"):
                self.layer_manager.ident_tool.feature_dock.show_placeholder()
                self.layer_manager.ident_tool.feature_dock.show()
            self.pan_start = event.pos()
            self.is_panning = True
            self.last_center = self.canvas.center()

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.moving_feature:
                if self.is_editing:
                    self.layer.commitChanges()
                    self.is_editing = False

                self.moving_feature = None
                self.last_pos = None
                self.dragging = False
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            elif self.is_panning:
                self.is_panning = False
                self.pan_start = None
                self.last_center = None
