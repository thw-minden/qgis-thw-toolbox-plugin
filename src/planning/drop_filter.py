"""Drag & Drop aus dem Objektplanungs-Dock auf die Karte."""

from qgis.core import QgsPointXY
from qgis.PyQt.QtCore import QByteArray, QEvent, QMimeData, QObject

# Inhalt: "<werkzeug>|<typ_id>", z.B. "tent|sg300"
MIME_TYPE = "application/x-thw-objektplanung"


def object_mime(kind: str, type_id: str | None) -> QMimeData:
    mime = QMimeData()
    mime.setData(MIME_TYPE, QByteArray(f"{kind}|{type_id or ''}".encode()))
    return mime


class PlanningDropFilter(QObject):
    """Event-Filter auf dem Karten-Viewport: abgelegte Objekte an ``drop_cb(kind, type_id, map_point)`` geben."""

    def __init__(self, canvas, drop_cb):
        super().__init__(canvas)
        self.canvas = canvas
        self.drop_cb = drop_cb

    def eventFilter(self, obj, ev):
        if ev.type() not in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            return False
        mime = ev.mimeData()
        if mime is None or not mime.hasFormat(MIME_TYPE):
            return False
        if ev.type() == QEvent.Type.Drop:
            kind, _, type_id = bytes(mime.data(MIME_TYPE)).decode().partition("|")
            pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
            point = self.canvas.getCoordinateTransform().toMapCoordinates(pos.x(), pos.y())
            self.drop_cb(kind, type_id or None, QgsPointXY(point))
        ev.acceptProposedAction()
        return True
