from qgis.core import QgsPointXY
from qgis.PyQt.QtCore import QEvent, QObject


class CanvasDropFilter(QObject):
    """Event filter on the map canvas that turns SVG drag-drops into placements.

    Installed on the canvas viewport. On Drop, decodes the dragged text as
    an SVG path and invokes `place_cb(svg_path, map_point)`. The plugin
    wires `place_cb` to its feature-placement logic.
    """

    def __init__(self, canvas, place_cb):
        super().__init__(canvas)
        self.canvas = canvas
        self.place_cb = place_cb

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.DragEnter:
            if ev.mimeData().hasText():
                ev.acceptProposedAction()
                return True
        if ev.type() == QEvent.Type.Drop:
            # Andere Drops (z.B. Objekte aus dem Objektplanungs-Dock) gehören nicht hierher
            if not ev.mimeData().hasText():
                return False
            svg = ev.mimeData().text()
            if hasattr(ev, "position"):
                pos = ev.position().toPoint()
            else:
                pos = ev.pos()
            pt = self.canvas.getCoordinateTransform().toMapCoordinates(pos.x(), pos.y())
            self.place_cb(svg, QgsPointXY(pt))
            ev.acceptProposedAction()
            return True
        return False
