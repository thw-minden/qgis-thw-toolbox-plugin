"""Fügt einer Druckkarte ein konfiguriertes UTM-Gitter hinzu.

Einmaliger Klick → Gitter im passenden UTM-CRS, Labels außerhalb des Rahmens,
Arial 7pt, Dezimal-Format ohne Nachkommastellen.
"""

from __future__ import annotations

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsLayoutItemMap,
    QgsLayoutItemMapGrid,
    QgsProject,
    QgsTextFormat,
)
from qgis.PyQt.QtGui import QFont

# Zielabstand der Gitterlinien auf dem Papier (in cm)
_TARGET_PAPER_CM = 5.0
# Mögliche Gitter-Intervalle in Metern (auf runde Werte rasten)
_NICE_INTERVALS_M = [
    50,
    100,
    250,
    500,
    1000,
    2500,
    5000,
    10000,
    25000,
    50000,
    100000,
    250000,
]


def _utm_crs_for_extent(map_crs: QgsCoordinateReferenceSystem, extent) -> QgsCoordinateReferenceSystem:
    """UTM-Zone aus dem Kartenmittelpunkt bestimmen (WGS84-basiert)."""
    wgs84 = QgsCoordinateReferenceSystem.fromEpsgId(4326)
    transform = QgsCoordinateTransform(map_crs, wgs84, QgsProject.instance())
    center = transform.transform(extent.center())
    lon, lat = center.x(), center.y()
    zone = int((lon + 180.0) / 6.0) + 1
    epsg = (32600 if lat >= 0 else 32700) + zone
    return QgsCoordinateReferenceSystem.fromEpsgId(epsg)


def _pick_interval(scale: float) -> int:
    """Gitterintervall in Metern passend zum Kartenmaßstab (~5cm auf Papier)."""
    if scale <= 0:
        return 1000
    target_m = _TARGET_PAPER_CM / 100.0 * scale
    return min(_NICE_INTERVALS_M, key=lambda v: abs(v - target_m))


def add_utm_grid_to_map(map_item: QgsLayoutItemMap) -> tuple[int, str]:
    """Konfiguriert ein neues UTM-Gitter auf der Karte. Gibt (Intervall, CRS-AuthID) zurück."""
    crs = _utm_crs_for_extent(map_item.crs(), map_item.extent())
    interval = _pick_interval(map_item.scale())

    grid = QgsLayoutItemMapGrid("UTM", map_item)
    grid.setCrs(crs)
    grid.setEnabled(True)
    grid.setIntervalX(interval)
    grid.setIntervalY(interval)
    grid.setOffsetX(0)
    grid.setOffsetY(0)
    grid.setStyle(QgsLayoutItemMapGrid.GridStyle.Solid)

    # Anmerkungen (Labels) aktivieren + außerhalb des Rahmens platzieren
    grid.setAnnotationEnabled(True)
    grid.setAnnotationFormat(QgsLayoutItemMapGrid.AnnotationFormat.Decimal)
    grid.setAnnotationPrecision(0)

    font = QFont("Arial", 7)
    text_format = QgsTextFormat.fromQFont(font)
    text_format.setSize(7)
    text_format.setSizeUnit(Qgis.RenderUnit.Points)
    grid.setAnnotationTextFormat(text_format)

    outside = QgsLayoutItemMapGrid.AnnotationPosition.OutsideMapFrame
    grid.setAnnotationPosition(outside, QgsLayoutItemMapGrid.BorderSide.Left)
    grid.setAnnotationPosition(outside, QgsLayoutItemMapGrid.BorderSide.Right)
    grid.setAnnotationPosition(outside, QgsLayoutItemMapGrid.BorderSide.Top)
    grid.setAnnotationPosition(outside, QgsLayoutItemMapGrid.BorderSide.Bottom)

    grid.setAnnotationDirection(QgsLayoutItemMapGrid.AnnotationDirection.Vertical, QgsLayoutItemMapGrid.BorderSide.Left)
    grid.setAnnotationDirection(
        QgsLayoutItemMapGrid.AnnotationDirection.VerticalDescending, QgsLayoutItemMapGrid.BorderSide.Right
    )
    grid.setAnnotationDirection(
        QgsLayoutItemMapGrid.AnnotationDirection.Horizontal, QgsLayoutItemMapGrid.BorderSide.Top
    )
    grid.setAnnotationDirection(
        QgsLayoutItemMapGrid.AnnotationDirection.Horizontal, QgsLayoutItemMapGrid.BorderSide.Bottom
    )

    grid.setAnnotationFrameDistance(1.0)

    map_item.grids().addGrid(grid)
    map_item.updateBoundingRect()
    map_item.refresh()

    return interval, crs.authid()
