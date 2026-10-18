from qgis.gui import QgsMapToolIdentify
from qgis.PyQt.QtCore import Qt

from ..logging_utils import get_logger
from ._feature_search import find_nearest_feature

logger = get_logger(__name__)


class IdentifyTool(QgsMapToolIdentify):
    """Map tool that opens a feature's detail panel on left-click.

    Uses the plugin's details dock (shared with the planning selection).
    `layer_manager` is the THWToolboxPlugin instance (legacy name); we use
    it for `.layer`, `.move_tool` and `.details_dock()`.
    """

    def __init__(self, canvas, layer_manager):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer_manager = layer_manager
        self.layer = layer_manager.layer
        self.setCursor(Qt.CursorShape.ArrowCursor)

        self.feature_dock = layer_manager.details_dock()

    def canvasReleaseEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        if self.layer is None:
            return
        try:
            self.layer.id()
        except RuntimeError:
            self.layer = None
            return
        try:
            point = self.canvas.getCoordinateTransform().toMapCoordinates(ev.pos().x(), ev.pos().y())
            closest = find_nearest_feature(self.layer, self.canvas, point)

            if closest:
                self.feature_dock.show_feature(closest, self.layer_manager)
                if hasattr(self.layer_manager, "move_tool"):
                    self.layer_manager.move_tool.moving_feature = closest
            else:
                self.layer_manager.show_marker_placeholder()

        except Exception as e:
            logger.exception("Fehler beim Identifizieren")
            self.layer_manager._show_error_alert(
                "Identifizierungsfehler", "Fehler beim Identifizieren von Features", f"Fehler: {e}"
            )
