"""Gedrehter Rahmen einer Beschriftung im Canvas-CRS (für Verschieben, Skalieren und Drehen)."""

import math
from dataclasses import dataclass

from qgis.core import QgsGeometry, QgsPointXY


def rotate_point(point: QgsPointXY, center: QgsPointXY, clockwise_deg: float) -> QgsPointXY:
    """``point`` um ``center`` drehen, im Uhrzeigersinn (Kartenkoordinaten: y zeigt nach oben)."""
    a = math.radians(clockwise_deg)
    dx, dy = point.x() - center.x(), point.y() - center.y()
    return QgsPointXY(
        center.x() + dx * math.cos(a) + dy * math.sin(a),
        center.y() - dx * math.sin(a) + dy * math.cos(a),
    )


def _upright_angle(ring: list[QgsPointXY]) -> float:
    """Drehung (Grad im Uhrzeigersinn) der längsten Kante – so liegt Schrift entlang einer Linie."""
    best, best_len = 0.0, -1.0
    for a, b in zip(ring, ring[1:]):
        length = math.hypot(b.x() - a.x(), b.y() - a.y())
        if length > best_len:
            best_len = length
            best = -math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x()))
    # Schrift steht nie auf dem Kopf: in (-90°, 90°]
    while best > 90.0:
        best -= 180.0
    while best <= -90.0:
        best += 180.0
    return best


@dataclass
class LabelFrame:
    """``corners``: oben links, oben rechts, unten rechts, unten links; ``angle`` Grad im Uhrzeigersinn; ``size`` pt."""

    corners: list[QgsPointXY]
    center: QgsPointXY
    angle: float
    size: float

    @classmethod
    def from_geometry(cls, geom: QgsGeometry, angle: float | None, size: float) -> "LabelFrame | None":
        """Rahmen aus dem Umriss der gezeichneten Beschriftung.

        ``angle``: gespeicherte Drehung; ohne sie (z. B. Schrift entlang einer Linie) aus dem Umriss geschätzt.
        """
        if geom is None or geom.isEmpty():
            return None
        polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
        ring = [QgsPointXY(p) for p in polys[0][0]] if polys and polys[0] else []
        if len(ring) < 4:
            box = geom.boundingBox()
            ring = [
                QgsPointXY(box.xMinimum(), box.yMinimum()),
                QgsPointXY(box.xMaximum(), box.yMinimum()),
                QgsPointXY(box.xMaximum(), box.yMaximum()),
                QgsPointXY(box.xMinimum(), box.yMaximum()),
                QgsPointXY(box.xMinimum(), box.yMinimum()),
            ]
        if angle is None:
            angle = _upright_angle(ring)
        center = geom.centroid().asPoint()
        # Zurückdrehen, achsparallel umschließen, wieder drehen
        upright = [rotate_point(p, center, -angle) for p in ring]
        xs, ys = [p.x() for p in upright], [p.y() for p in upright]
        box = [
            QgsPointXY(min(xs), max(ys)),
            QgsPointXY(max(xs), max(ys)),
            QgsPointXY(max(xs), min(ys)),
            QgsPointXY(min(xs), min(ys)),
        ]
        corners = [rotate_point(p, center, angle) for p in box]
        return cls(corners, center, angle, size)

    def moved(self, dx: float, dy: float) -> "LabelFrame":
        shift = [QgsPointXY(p.x() + dx, p.y() + dy) for p in self.corners]
        return LabelFrame(shift, QgsPointXY(self.center.x() + dx, self.center.y() + dy), self.angle, self.size)

    def scaled(self, factor: float) -> "LabelFrame":
        c = self.center
        corners = [QgsPointXY(c.x() + (p.x() - c.x()) * factor, c.y() + (p.y() - c.y()) * factor) for p in self.corners]
        return LabelFrame(corners, c, self.angle, self.size * factor)

    def rotated(self, clockwise_deg: float) -> "LabelFrame":
        corners = [rotate_point(p, self.center, clockwise_deg) for p in self.corners]
        return LabelFrame(corners, self.center, self.angle + clockwise_deg, self.size)
