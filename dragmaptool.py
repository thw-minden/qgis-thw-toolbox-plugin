from qgis.gui import QgsMapToolEmitPoint
from PyQt5.QtGui import QCursor
from PyQt5.QtCore import Qt
from qgis.core import QgsPointXY


class DragDropMapTool(QgsMapToolEmitPoint):
    def __init__(self, canvas, drop_callback):
        super().__init__(canvas)
        self.canvas = canvas
        self.drop_callback = drop_callback
        self.setCursor(QCursor(Qt.OpenHandCursor))

    def canvasReleaseEvent(self, event):
        point = self.toMapCoordinates(event.pos())
        if self.drop_callback:
            self.drop_callback(QgsPointXY(point))
