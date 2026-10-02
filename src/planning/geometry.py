"""Metrische Hilfsfunktionen für die Lagerplanung.

Alle Konstruktionen (Zelt-Rechtecke, Abstände, Flächenbelegung) laufen in
einer lokalen UTM-Zone, damit Meterangaben unabhängig vom Projekt-CRS
(z.B. EPSG:3857 oder EPSG:4326) stimmen.
"""

import math
from dataclasses import dataclass, field

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)

_WGS84 = "EPSG:4326"
# Zwei Winkel gelten als gleich, wenn sie weniger als das auseinander liegen
_ANGLE_TOLERANCE_DEG = 1.0
# So viele dominante Kantenrichtungen der Fläche werden ausprobiert
_MAX_EDGE_ANGLES = 4
# Verschiebungen des Rasters pro Zeile (x) bzw. global (y)
_X_OFFSET_STEPS = 6
_Y_OFFSET_STEPS = 3
# Obergrenze Rasterfelder pro Zelttyp (≈ einige Sekunden Rechenzeit)
MAX_CELLS = 2000
# Toleranz in Metern gegen Rundungsfehler
_EPS = 0.005


class MetricFrame:
    """Lokales metrisches Bezugssystem (UTM-Zone um einen Referenzpunkt)."""

    def __init__(self, source_crs: QgsCoordinateReferenceSystem, reference: QgsPointXY):
        project = QgsProject.instance()
        to_wgs = QgsCoordinateTransform(source_crs, QgsCoordinateReferenceSystem(_WGS84), project)
        wgs = to_wgs.transform(QgsPointXY(reference))
        self.crs = QgsCoordinateReferenceSystem(f"EPSG:{utm_epsg(wgs.x(), wgs.y())}")
        self.source_crs = source_crs
        self._to_m = QgsCoordinateTransform(source_crs, self.crs, project)
        self._from_m = QgsCoordinateTransform(self.crs, source_crs, project)

    def point_to_m(self, point: QgsPointXY) -> QgsPointXY:
        return QgsPointXY(self._to_m.transform(QgsPointXY(point)))

    def point_from_m(self, point: QgsPointXY) -> QgsPointXY:
        return QgsPointXY(self._from_m.transform(QgsPointXY(point)))

    def geom_to_m(self, geom: QgsGeometry) -> QgsGeometry:
        g = QgsGeometry(geom)
        g.transform(self._to_m)
        return g

    def geom_from_m(self, geom: QgsGeometry, crs: QgsCoordinateReferenceSystem | None = None) -> QgsGeometry:
        """Metrisch → ``crs`` (Standard: das CRS, mit dem der Frame erzeugt wurde)."""
        g = QgsGeometry(geom)
        if crs is None or crs == self.source_crs:
            g.transform(self._from_m)
        else:
            g.transform(QgsCoordinateTransform(self.crs, crs, QgsProject.instance()))
        return g

    def geom_to_m_from(self, geom: QgsGeometry, crs: QgsCoordinateReferenceSystem) -> QgsGeometry:
        """Wie ``geom_to_m``, aber für Geometrien in einem anderen CRS (z.B. anderer Layer)."""
        if crs == self.source_crs:
            return self.geom_to_m(geom)
        g = QgsGeometry(geom)
        g.transform(QgsCoordinateTransform(crs, self.crs, QgsProject.instance()))
        return g


def utm_epsg(lon: float, lat: float) -> int:
    zone = min(60, max(1, int((lon + 180.0) // 6.0) + 1))
    return (32600 if lat >= 0 else 32700) + zone


def tent_polygon(center: QgsPointXY, length: float, width: float, rotation: float) -> QgsGeometry:
    """Rechteck um ``center`` (metrisch). Rotation in Grad im Uhrzeigersinn,
    bei 0° liegt die Länge in Ost-West-Richtung."""
    theta = math.radians(rotation)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    hl, hw = length / 2.0, width / 2.0
    ring = []
    for dx, dy in ((-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw)):
        ring.append(QgsPointXY(center.x() + dx * cos_t + dy * sin_t, center.y() - dx * sin_t + dy * cos_t))
    ring.append(ring[0])
    return QgsGeometry.fromPolygonXY([ring])


def circle_polygon(center: QgsPointXY, radius: float, segments: int = 72) -> QgsGeometry:
    ring = [
        QgsPointXY(
            center.x() + radius * math.cos(2 * math.pi * i / segments),
            center.y() + radius * math.sin(2 * math.pi * i / segments),
        )
        for i in range(segments)
    ]
    ring.append(ring[0])
    return QgsGeometry.fromPolygonXY([ring])


def point_along(start: QgsPointXY, end: QgsPointXY, distance: float) -> QgsPointXY:
    """Punkt im Abstand ``distance`` von ``start`` in Richtung ``end`` (metrisch)."""
    seg = math.hypot(end.x() - start.x(), end.y() - start.y())
    if seg <= 0:
        return QgsPointXY(start)
    f = distance / seg
    return QgsPointXY(start.x() + (end.x() - start.x()) * f, start.y() + (end.y() - start.y()) * f)


@dataclass
class PackResult:
    centers: list[QgsPointXY] = field(default_factory=list)  # metrisch
    rotation: float = 0.0
    too_large: bool = False

    @property
    def count(self) -> int:
        return len(self.centers)


def pack_rectangles(
    area: QgsGeometry,
    length: float,
    width: float,
    gap: float,
    edge_margin: float = 0.0,
    obstacles: list[QgsGeometry] | None = None,
    angles: list[float] | None = None,
) -> PackResult:
    """Ermittelt, wie viele Rechtecke ``length × width`` in ``area`` passen.

    Alle Geometrien metrisch. ``gap`` ist der Mindestabstand zwischen zwei
    Rechtecken (und zu ``obstacles``), ``edge_margin`` der Abstand zum
    Flächenrand (z.B. die Abspannung). Probiert die dominanten
    Kantenrichtungen der Fläche (jeweils längs und quer) und verschiebt
    das Raster pro Zeile, um möglichst viele Zelte unterzubringen.
    """
    if area is None or area.isEmpty() or length <= 0 or width <= 0:
        return PackResult()

    usable = QgsGeometry(area)
    if edge_margin > 0:
        usable = usable.buffer(-edge_margin, 8)
    if obstacles:
        blocked = QgsGeometry.unaryUnion([o.buffer(max(gap, 0.0), 8) for o in obstacles if o and not o.isEmpty()])
        if blocked and not blocked.isEmpty():
            usable = usable.difference(blocked)
    if usable is None or usable.isEmpty() or usable.area() < length * width:
        return PackResult()
    # Rechenzeit wächst mit der Zahl der Rasterfelder — riesige Flächen abweisen
    # statt QGIS minutenlang zu blockieren
    if usable.area() / ((length + gap) * (width + gap)) > MAX_CELLS:
        return PackResult(too_large=True)

    if angles is None:
        angles = candidate_angles(area)

    center = usable.centroid().asPoint()
    best = PackResult()
    for angle in angles:
        rotated = QgsGeometry(usable)
        # Fläche gegen die Zelt-Rotation drehen → Zelte liegen achsparallel
        rotated.rotate(-angle, center)
        found = _pack_axis_aligned(rotated, length, width, gap)
        if len(found) > best.count:
            back = []
            for p in found:
                g = QgsGeometry.fromPointXY(p)
                g.rotate(angle, center)
                back.append(g.asPoint())
            best = PackResult(centers=[QgsPointXY(p) for p in back], rotation=angle % 360)
    return best


def candidate_angles(area: QgsGeometry) -> list[float]:
    """Dominante Kantenrichtungen der Fläche (nach Kantenlänge), jeweils + 90°."""
    weights: dict[float, float] = {}
    simplified = area.simplify(0.5) if area.area() > 0 else area
    for part in _polygon_rings(simplified):
        for a, b in zip(part, part[1:]):
            seg = math.hypot(b.x() - a.x(), b.y() - a.y())
            if seg <= 0:
                continue
            # Kompassrichtung im Uhrzeigersinn, auf [0, 90) reduziert
            ang = math.degrees(math.atan2(-(b.y() - a.y()), b.x() - a.x())) % 90.0
            key = next((k for k in weights if _angle_close(k, ang, 90.0)), None)
            if key is None:
                weights[ang] = seg
            else:
                weights[key] += seg

    base = [a for a, _ in sorted(weights.items(), key=lambda kv: -kv[1])[:_MAX_EDGE_ANGLES]] or [0.0]
    if not any(_angle_close(a, 0.0, 90.0) for a in base):
        base.append(0.0)
    result = []
    for a in base:
        result.extend([round(a, 2), round((a + 90.0) % 180.0, 2)])
    return result


def _angle_close(a: float, b: float, period: float) -> bool:
    d = abs(a - b) % period
    return min(d, period - d) < _ANGLE_TOLERANCE_DEG


def _polygon_rings(geom: QgsGeometry) -> list[list[QgsPointXY]]:
    rings = []
    polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
    for poly in polys:
        rings.extend(poly)
    return rings


def _pack_axis_aligned(usable: QgsGeometry, length: float, width: float, gap: float) -> list[QgsPointXY]:
    engine = QgsGeometry.createGeometryEngine(usable.constGet())
    engine.prepareGeometry()
    bbox = usable.boundingBox()
    px, py = length + gap, width + gap

    best: list[QgsPointXY] = []
    for oy_step in range(_Y_OFFSET_STEPS):
        y = bbox.yMinimum() + py * oy_step / _Y_OFFSET_STEPS
        placed: list[QgsPointXY] = []
        while y + width <= bbox.yMaximum() + 1e-9:
            # Jede Zeile unabhängig horizontal verschieben → passt sich schrägen Rändern an
            row_best: list[QgsPointXY] = []
            for ox_step in range(_X_OFFSET_STEPS):
                x = bbox.xMinimum() + px * ox_step / _X_OFFSET_STEPS
                row: list[QgsPointXY] = []
                while x + length <= bbox.xMaximum() + 1e-9:
                    # Minimal eingerückt, damit Rundungsfehler der Rotation an
                    # exakt passenden Rändern kein Feld kosten
                    cell = QgsGeometry.fromRect(QgsRectangle(x + _EPS, y + _EPS, x + length - _EPS, y + width - _EPS))
                    if engine.contains(cell.constGet()):
                        row.append(QgsPointXY(x + length / 2.0, y + width / 2.0))
                        x += px
                    else:
                        # Nicht passendes Feld: in kleinen Schritten weiter, damit
                        # Lücken in unregelmäßigen Flächen genutzt werden
                        x += px / _X_OFFSET_STEPS
                if len(row) > len(row_best):
                    row_best = row
            placed.extend(row_best)
            y += py
        if len(placed) > len(best):
            best = placed
    return best
