from PyQt5.QtCore import QObject, QEvent
from qgis.core import QgsPointXY

class CanvasDropFilter(QObject):
    def __init__(self, canvas, place_feature_callback):
        super().__init__()
        self.canvas = canvas
        self.place_feature = place_feature_callback

    def eventFilter(self, obj, event):
        if event.type() == QEvent.DragEnter:
            if event.mimeData().hasText():
                event.acceptProposedAction()
                return True

        if event.type() == QEvent.Drop:
            svg_path = event.mimeData().text()
            pt = self.canvas.getCoordinateTransform().toMapCoordinates(event.pos().x(), event.pos().y())
            self.place_feature(svg_path, QgsPointXY(pt))
            event.acceptProposedAction()
            return True

        return False
