"""Figma-artiges Auswahl-Overlay der Objektplanung (Rahmen, Eckpunkte, Badge, Hover)."""

import math

from qgis.core import Qgis, QgsGeometry, QgsPointXY
from qgis.gui import QgsMapCanvasItem
from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPolygonF

# Gleiches Blau wie der Auswahlrahmen der taktischen Zeichen
_BLUE = QColor(13, 153, 255)
_HALO = QColor(255, 255, 255, 190)
_GHOST_FILL = QColor(13, 153, 255, 45)
_MARQUEE_FILL = QColor(13, 153, 255, 25)
# Hilfslinien beim Ausrichten (wie Figmas Smart Guides)
_GUIDE = QColor(233, 30, 99)
HANDLE_PX = 8.0
# Darunter verdecken die Eckpunkte das Objekt → nur Umriss
MIN_HANDLE_FRAME_PX = 24.0
_BADGE_GAP_PX = 8.0
_POINT_BOX_PX = 7.0


class SelectionOverlay(QgsMapCanvasItem):
    """Zeichnet Auswahl, Hover, Vorschau (Ghosts) und Auswahlrahmen.

    Alle Geometrien liegen im Canvas-CRS und werden beim Zeichnen in
    Pixel umgerechnet, damit Zoomen/Verschieben der Karte ohne Neuberechnung
    funktioniert. ``frame`` sind die vier Ecken (oben links, oben rechts,
    unten rechts, unten links) des Rahmens mit Eckpunkten.
    """

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self.selected: list[QgsGeometry] = []
        self.hover: QgsGeometry | None = None
        self.ghosts: list[QgsGeometry] = []
        self.guides: list[QgsGeometry] = []
        self.frame: list[QgsPointXY] | None = None
        self.badge = ""
        self.marquee: tuple[QgsPointXY, QgsPointXY] | None = None
        self.show_handles = True
        self.setZValue(100)
        canvas.extentsChanged.connect(self.refresh)
        self.refresh()

    def dispose(self):
        try:
            self._canvas.extentsChanged.disconnect(self.refresh)
        except (TypeError, RuntimeError):
            pass
        scene = self._canvas.scene()
        if scene is not None:
            scene.removeItem(self)

    def refresh(self):
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self):
        size = self._canvas.size()
        return QRectF(-50, -50, size.width() + 100, size.height() + 100)

    # ------------------------------------------------------------------
    # Pixel-Geometrie (auch vom Werkzeug für Treffertests genutzt)
    # ------------------------------------------------------------------

    def to_px(self, point: QgsPointXY) -> QPointF:
        p = self._canvas.getCoordinateTransform().transform(point)
        return QPointF(p.x(), p.y())

    def frame_px(self) -> list[QPointF] | None:
        if not self.frame:
            return None
        return [self.to_px(p) for p in self.frame]

    def frame_has_handles(self) -> bool:
        corners = self.frame_px()
        if not corners or not self.show_handles:
            return False
        width = math.dist((corners[0].x(), corners[0].y()), (corners[1].x(), corners[1].y()))
        height = math.dist((corners[0].x(), corners[0].y()), (corners[3].x(), corners[3].y()))
        return min(width, height) >= MIN_HANDLE_FRAME_PX

    # ------------------------------------------------------------------
    # Zeichnen
    # ------------------------------------------------------------------

    def _paths(self, geom: QgsGeometry) -> tuple[list[QPolygonF], list[QPolygonF], list[QPointF]]:
        """Pixel-Ringe (Flächen), Linienzüge und Punkte einer Geometrie."""
        rings, lines, points = [], [], []
        if geom is None or geom.isEmpty():
            return rings, lines, points
        gtype = geom.type()
        if gtype == Qgis.GeometryType.Polygon:
            polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
            for poly in polys:
                rings.extend(QPolygonF([self.to_px(p) for p in ring]) for ring in poly)
        elif gtype == Qgis.GeometryType.Line:
            parts = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
            lines.extend(QPolygonF([self.to_px(p) for p in part]) for part in parts)
        elif gtype == Qgis.GeometryType.Point:
            pts = geom.asMultiPoint() if geom.isMultipart() else [geom.asPoint()]
            points.extend(self.to_px(p) for p in pts)
        return rings, lines, points

    def _outline(self, painter: QPainter, geom: QgsGeometry, width: float, halo: bool, fill: QColor | None = None):
        rings, lines, points = self._paths(geom)
        pens = ([QPen(_HALO, width + 2.0)] if halo else []) + [QPen(_BLUE, width)]
        for pen in pens:
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(QBrush(fill) if fill is not None else Qt.BrushStyle.NoBrush)
            for ring in rings:
                painter.drawPolygon(ring)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for line in lines:
                painter.drawPolyline(line)
            for p in points:
                painter.drawRect(
                    QRectF(p.x() - _POINT_BOX_PX, p.y() - _POINT_BOX_PX, 2 * _POINT_BOX_PX, 2 * _POINT_BOX_PX)
                )

    def paint(self, painter, option=None, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self.hover is not None:
            self._outline(painter, self.hover, 1.0, halo=False)
        for geom in self.ghosts:
            self._outline(painter, geom, 1.5, halo=True, fill=_GHOST_FILL)
        if not self.ghosts:
            for geom in self.selected:
                self._outline(painter, geom, 1.5, halo=True)

        guide_pen = QPen(_GUIDE, 1.0, Qt.PenStyle.DashLine)
        for geom in self.guides:
            _rings, lines, _points = self._paths(geom)
            painter.setPen(guide_pen)
            for line in lines:
                painter.drawPolyline(line)

        corners = self.frame_px()
        if corners:
            polygon = QPolygonF(corners)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(_HALO, 3.0))
            painter.drawPolygon(polygon)
            painter.setPen(QPen(_BLUE, 1.0))
            painter.drawPolygon(polygon)
            if self.frame_has_handles():
                angle = math.degrees(math.atan2(corners[1].y() - corners[0].y(), corners[1].x() - corners[0].x()))
                half = HANDLE_PX / 2.0
                painter.setPen(QPen(_BLUE, 1.5))
                painter.setBrush(QBrush(QColor(255, 255, 255)))
                for corner in corners:
                    painter.save()
                    painter.translate(corner)
                    painter.rotate(angle)
                    painter.drawRect(QRectF(-half, -half, HANDLE_PX, HANDLE_PX))
                    painter.restore()
            if self.badge:
                self._draw_badge(painter, corners)

        if self.marquee is not None:
            a, b = self.to_px(self.marquee[0]), self.to_px(self.marquee[1])
            rect = QRectF(a, b).normalized()
            painter.setPen(QPen(_BLUE, 1.0))
            painter.setBrush(QBrush(_MARQUEE_FILL))
            painter.drawRect(rect)

    def _draw_badge(self, painter: QPainter, corners: list[QPointF]):
        font = QFont(painter.font())
        font.setPixelSize(11)
        font.setBold(True)
        metrics = QFontMetricsF(font)
        width = metrics.horizontalAdvance(self.badge) + 12
        height = metrics.height() + 4
        bottom = max(c.y() for c in corners)
        center_x = sum(c.x() for c in corners) / 4.0
        rect = QRectF(center_x - width / 2.0, bottom + _BADGE_GAP_PX, width, height)
        path = QPainterPath()
        path.addRoundedRect(rect, 4, 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(_BLUE))
        painter.drawPath(path)
        painter.setFont(font)
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.badge)
