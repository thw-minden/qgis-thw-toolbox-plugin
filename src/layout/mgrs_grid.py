"""Erzeugt einen Memory-Linienlayer mit MGRS-Gitter im UTM-CRS der Kartenzone.

Standardauflösung 1000 m. Labels zeigen die letzten 2 Ziffern der Kilometerposition
(wie auf militärischen Einsatzkarten üblich). Das 100-km-Quadrat und die UTM-Zone
stehen im Layer-Namen, damit der volle MGRS-Bezug auch ohne Kopfzeile lesbar ist.
"""

from __future__ import annotations

import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsLineSymbol,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor, QFont

from .legend import LEGEND_PROPERTY

BAND_LETTERS = "CDEFGHJKLMNPQRSTUVWX"
COL_SETS = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")
ROW_ODD = "ABCDEFGHJKLMNPQRSTUV"
ROW_EVEN = "FGHJKLMNPQRSTUVABCDE"

MAX_LINES = 2000  # Sicherheitsbremse: sonst lähmt ein zu grosser Extent QGIS
EXTENT_BUFFER = 0.40  # 40 % Puffer um die Kartenausdehnung herum


def _latitude_band(lat: float) -> str:
    if lat >= 84:
        return "X"
    if lat < -80:
        return "C"
    return BAND_LETTERS[min(int((lat + 80) // 8), 19)]


def _utm_zone(lon: float) -> int:
    return int((lon + 180) // 6) + 1


def _utm_epsg(zone: int, lat: float) -> int:
    return (32600 if lat >= 0 else 32700) + zone


def _mgrs_100km(zone: int, easting: float, northing: float) -> str:
    col_idx = max(0, min(7, int(easting // 100_000) - 1))
    col = COL_SETS[(zone - 1) % 3][col_idx]
    rowset = ROW_ODD if zone % 2 == 1 else ROW_EVEN
    row = rowset[int((northing % 2_000_000) // 100_000)]
    return col + row


def build_mgrs_grid_layer(
    map_extent: QgsRectangle,
    map_crs: QgsCoordinateReferenceSystem,
    interval: int = 1000,
) -> tuple[QgsVectorLayer | None, str]:
    """Baut einen Memory-Linienlayer. Gibt (Layer, Statusmeldung) zurück."""
    wgs84 = QgsCoordinateReferenceSystem.fromEpsgId(4326)
    to_wgs = QgsCoordinateTransform(map_crs, wgs84, QgsProject.instance())
    center_ll = to_wgs.transform(map_extent.center())
    zone = _utm_zone(center_ll.x())
    band = _latitude_band(center_ll.y())
    epsg = _utm_epsg(zone, center_ll.y())
    utm_crs = QgsCoordinateReferenceSystem.fromEpsgId(epsg)

    to_utm = QgsCoordinateTransform(map_crs, utm_crs, QgsProject.instance())
    utm_extent = to_utm.transformBoundingBox(map_extent)

    # 40 % Puffer, damit das Gitter auch beim Rauszoomen / Pannen trägt
    bx = utm_extent.width() * EXTENT_BUFFER
    by = utm_extent.height() * EXTENT_BUFFER
    utm_extent = QgsRectangle(
        utm_extent.xMinimum() - bx,
        utm_extent.yMinimum() - by,
        utm_extent.xMaximum() + bx,
        utm_extent.yMaximum() + by,
    )

    x_min = math.floor(utm_extent.xMinimum() / interval) * interval
    x_max = math.ceil(utm_extent.xMaximum() / interval) * interval
    y_min = math.floor(utm_extent.yMinimum() / interval) * interval
    y_max = math.ceil(utm_extent.yMaximum() / interval) * interval

    nx = int((x_max - x_min) / interval) + 1
    ny = int((y_max - y_min) / interval) + 1
    if nx > MAX_LINES or ny > MAX_LINES:
        return None, f"Zu viele Gitterlinien ({nx}×{ny}). Zoomen Sie näher heran."

    square_center = _mgrs_100km(zone, (x_min + x_max) / 2, (y_min + y_max) / 2)
    layer_name = f"MGRS {interval} m {zone}{band} {square_center}"
    layer = QgsVectorLayer(f"LineString?crs={utm_crs.authid()}", layer_name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes(
        [
            QgsField("kind", QVariant.String),
            QgsField("label", QVariant.String),
            QgsField("mgrs", QVariant.String),
        ]
    )
    layer.updateFields()

    digits = max(1, 5 - int(round(math.log10(interval))))  # 1000→2, 100→3, 10→4

    features: list[QgsFeature] = []

    x = x_min
    while x <= x_max:
        km_e = int((x % 100_000) // interval)
        label = f"{km_e:0{digits}d}"
        square = _mgrs_100km(zone, max(x, 100_000), (y_min + y_max) / 2)
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x, y_min), QgsPointXY(x, y_max)]))
        f.setAttributes(["east", label, f"{zone}{band} {square} E{label}"])
        features.append(f)
        x += interval

    y = y_min
    while y <= y_max:
        km_n = int((y % 100_000) // interval)
        label = f"{km_n:0{digits}d}"
        square = _mgrs_100km(zone, (x_min + x_max) / 2, y)
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x_min, y), QgsPointXY(x_max, y)]))
        f.setAttributes(["north", label, f"{zone}{band} {square} N{label}"])
        features.append(f)
        y += interval

    provider.addFeatures(features)
    layer.updateExtents()

    symbol = QgsLineSymbol.createSimple({"color": "0,0,0,160", "width": "0.2"})
    layer.renderer().setSymbol(symbol)

    settings = QgsPalLayerSettings()
    settings.fieldName = "label"
    settings.placement = QgsPalLayerSettings.Line
    settings.enabled = True

    text_format = QgsTextFormat()
    text_format.setFont(QFont("Arial", 8))
    text_format.setSize(8)
    text_format.setColor(QColor(0, 0, 0))
    text_format.setOpacity(0.4)
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(0.8)
    buf.setColor(QColor(255, 255, 255))
    buf.setOpacity(0.4)
    text_format.setBuffer(buf)
    settings.setFormat(text_format)

    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    # Markiere als temporär — bleibt nicht im Projekt erhalten nach Speichern/Neuladen.
    layer.setCustomProperty("skipMemoryLayersCheck", 1)
    # Ein Gitter braucht keinen Eintrag in der Zeichenerklärung der Druckvorlagen
    layer.setCustomProperty(LEGEND_PROPERTY, 0)

    return layer, f"Gitter: {zone}{band} {square_center}, {interval} m, {len(features)} Linien"
