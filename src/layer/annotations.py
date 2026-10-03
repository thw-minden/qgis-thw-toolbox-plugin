from qgis.core import (
    Qgis,
    QgsAnnotationLayer,
    QgsAnnotationLineItem,
    QgsAnnotationMarkerItem,
    QgsAnnotationPointTextItem,
    QgsAnnotationPolygonItem,
    QgsCoordinateReferenceSystem,
    QgsFillSymbol,
    QgsLineString,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPoint,
    QgsPointXY,
    QgsPolygon,
    QgsProject,
    QgsTextFormat,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor

from ..logging_utils import get_logger

logger = get_logger(__name__)

# Display name in the QGIS layer panel
ANNOTATION_LAYER_DISPLAY_NAME = "THW Toolbox Annotationen"
# Custom property marking our annotation layer (survives renaming by the user)
_LAYER_PROPERTY = "thw_toolbox/annotation_layer"

LINE_WIDTH_MM = 0.8
MARKER_SIZE_MM = 3.0
TEXT_SIZE_MM = 3.5

# Point-text items draw their baseline-left corner exactly on the anchor point
# and offer no offset, so the description would overlap the marker. Two en
# spaces push it to the upper right of the marker, independent of map scale.
_DESCRIPTION_INDENT = "  "

# Draw order inside the annotation layer: areas below lines below points
_Z_POLYGON = 0
_Z_LINE = 1
_Z_MARKER = 2
_Z_TEXT = 3


def find_annotation_layer() -> QgsAnnotationLayer | None:
    for lyr in QgsProject.instance().mapLayers().values():
        if isinstance(lyr, QgsAnnotationLayer) and str(lyr.customProperty(_LAYER_PROPERTY, "")).lower() == "true":
            return lyr
    return None


def get_or_create_annotation_layer(crs: QgsCoordinateReferenceSystem) -> QgsAnnotationLayer:
    """Return the plugin's annotation layer, creating it in ``crs`` if missing.

    Annotation layers store their items inside the project file, so no
    GeoPackage handling is needed — the annotations travel with the project.
    """
    layer = find_annotation_layer()
    if layer:
        return layer

    project = QgsProject.instance()
    layer = QgsAnnotationLayer(
        ANNOTATION_LAYER_DISPLAY_NAME, QgsAnnotationLayer.LayerOptions(project.transformContext())
    )
    layer.setCrs(crs)
    layer.setCustomProperty(_LAYER_PROPERTY, True)
    project.addMapLayer(layer)
    logger.debug("Annotations-Layer angelegt: %s", layer.id())
    return layer


def add_point(layer: QgsAnnotationLayer, point: QgsPointXY, color: QColor, description: str = "") -> None:
    """Add a marker at ``point`` (layer CRS), optionally with a text description."""
    symbol = QgsMarkerSymbol.createSimple(
        {
            "name": "circle",
            "size": str(MARKER_SIZE_MM),
            "outline_color": "255,255,255,255",
            "outline_width": "0.4",
        }
    )
    symbol.symbolLayer(0).setFillColor(color)
    marker = QgsAnnotationMarkerItem(QgsPoint(point))
    marker.setSymbol(symbol)
    marker.setZIndex(_Z_MARKER)
    layer.addItem(marker)

    description = description.strip()
    if description:
        text_item = QgsAnnotationPointTextItem(_DESCRIPTION_INDENT + description, point)
        text_item.setFormat(_description_format())
        text_item.setAlignment(Qt.AlignmentFlag.AlignLeft)
        text_item.setZIndex(_Z_TEXT)
        layer.addItem(text_item)

    _finish(layer)


def add_line(layer: QgsAnnotationLayer, points: list[QgsPointXY], color: QColor) -> None:
    """Add a polyline through ``points`` (layer CRS)."""
    symbol = QgsLineSymbol.createSimple({"line_width": str(LINE_WIDTH_MM), "capstyle": "round", "joinstyle": "round"})
    symbol.symbolLayer(0).setColor(color)
    item = QgsAnnotationLineItem(QgsLineString([QgsPoint(p) for p in points]))
    item.setSymbol(symbol)
    item.setZIndex(_Z_LINE)
    layer.addItem(item)
    _finish(layer)


def add_polygon(
    layer: QgsAnnotationLayer, points: list[QgsPointXY], line_color: QColor, fill_color: QColor | None
) -> None:
    """Add a polygon with outline ``line_color``; filled with ``fill_color`` unless it is None."""
    symbol = QgsFillSymbol.createSimple(
        {
            "style": "solid" if fill_color is not None else "no",
            "outline_style": "solid",
            "outline_width": str(LINE_WIDTH_MM),
            "joinstyle": "round",
        }
    )
    fill_layer = symbol.symbolLayer(0)
    fill_layer.setStrokeColor(line_color)
    if fill_color is not None:
        fill_layer.setFillColor(fill_color)

    ring = [QgsPoint(p) for p in points]
    ring.append(QgsPoint(points[0]))
    polygon = QgsPolygon()
    polygon.setExteriorRing(QgsLineString(ring))

    item = QgsAnnotationPolygonItem(polygon)
    item.setSymbol(symbol)
    item.setZIndex(_Z_POLYGON)
    layer.addItem(item)
    _finish(layer)


def _description_format() -> QgsTextFormat:
    fmt = QgsTextFormat()
    fmt.setSize(TEXT_SIZE_MM)
    fmt.setSizeUnit(Qgis.RenderUnit.Millimeters)
    fmt.setColor(QColor(0, 0, 0))
    buffer = fmt.buffer()
    buffer.setEnabled(True)
    buffer.setSize(0.8)
    buffer.setSizeUnit(Qgis.RenderUnit.Millimeters)
    buffer.setColor(QColor(255, 255, 255))
    fmt.setBuffer(buffer)
    return fmt


def _finish(layer: QgsAnnotationLayer) -> None:
    layer.triggerRepaint()
    QgsProject.instance().setDirty(True)
