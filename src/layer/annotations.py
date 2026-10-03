import json
from dataclasses import dataclass

from qgis.core import (
    Qgis,
    QgsAnnotationLayer,
    QgsAnnotationLineItem,
    QgsAnnotationMarkerItem,
    QgsAnnotationPointTextItem,
    QgsAnnotationPolygonItem,
    QgsCoordinateReferenceSystem,
    QgsFillSymbol,
    QgsGeometry,
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
# Custom property holding per-item metadata as JSON:
# {item_id: {"n": running number, "name": list name, "label_id": linked description item}}
_META_PROPERTY = "thw_toolbox/annotation_items"

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

KIND_POINT = "point"
KIND_LINE = "line"
KIND_POLYGON = "polygon"
KIND_TEXT = "text"

_KIND_TITLES = {KIND_POINT: "Punkt", KIND_LINE: "Linie", KIND_POLYGON: "Polygon", KIND_TEXT: "Text"}


@dataclass
class AnnotationEntry:
    """One user-visible annotation object (a point and its description count as one)."""

    item_id: str
    kind: str
    number: int
    name: str  # list name for lines/polygons; the description for points/texts
    line_color: QColor
    fill_color: QColor | None  # None for unfilled polygons and non-polygons

    @property
    def short_title(self) -> str:
        return f"{_KIND_TITLES[self.kind]} {self.number}"

    @property
    def title(self) -> str:
        return f"{self.short_title}: {self.name}" if self.name else self.short_title


# ----------------------------------------------------------------------
# Layer
# ----------------------------------------------------------------------


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


# ----------------------------------------------------------------------
# Creating items
# ----------------------------------------------------------------------


def add_point(layer: QgsAnnotationLayer, point: QgsPointXY, color: QColor, description: str = "") -> None:
    """Add a marker at ``point`` (layer CRS), optionally with a text description."""
    marker = QgsAnnotationMarkerItem(QgsPoint(point))
    marker.setSymbol(_marker_symbol(color))
    marker.setZIndex(_Z_MARKER)
    marker_id = layer.addItem(marker)

    meta = _read_meta(layer)
    _register(meta, marker_id)
    _set_point_description(layer, meta, marker_id, description)
    _write_meta(layer, meta)
    _finish(layer)


def add_line(layer: QgsAnnotationLayer, points: list[QgsPointXY], color: QColor) -> None:
    """Add a polyline through ``points`` (layer CRS)."""
    item = QgsAnnotationLineItem(QgsLineString([QgsPoint(p) for p in points]))
    item.setSymbol(_line_symbol(color))
    item.setZIndex(_Z_LINE)
    _register_and_finish(layer, layer.addItem(item))


def add_polygon(
    layer: QgsAnnotationLayer, points: list[QgsPointXY], line_color: QColor, fill_color: QColor | None
) -> None:
    """Add a polygon with outline ``line_color``; filled with ``fill_color`` unless it is None."""
    ring = [QgsPoint(p) for p in points]
    ring.append(QgsPoint(points[0]))
    polygon = QgsPolygon()
    polygon.setExteriorRing(QgsLineString(ring))

    item = QgsAnnotationPolygonItem(polygon)
    item.setSymbol(_fill_symbol(line_color, fill_color))
    item.setZIndex(_Z_POLYGON)
    _register_and_finish(layer, layer.addItem(item))


# ----------------------------------------------------------------------
# Listing / editing existing items
# ----------------------------------------------------------------------


def list_entries(layer: QgsAnnotationLayer) -> list[AnnotationEntry]:
    """All annotation objects of ``layer``, ordered by their running number.

    Description texts linked to a point are folded into that point's entry.
    Items without metadata (e.g. drawn with the native QGIS annotation tools)
    get a number assigned on first listing.
    """
    items = layer.items()
    meta = _read_meta(layer)
    changed = _prune_meta(meta, items)

    linked_labels = {m.get("label_id") for m in meta.values() if m.get("label_id")}
    entries = []
    for item_id, item in items.items():
        if item_id in linked_labels:
            continue
        kind = _kind_of(item)
        if kind is None:
            continue
        if item_id not in meta:
            _register(meta, item_id)
            changed = True
        entries.append(_entry_for(layer, meta, item_id, item, kind))

    if changed:
        _write_meta(layer, meta)
    entries.sort(key=lambda e: e.number)
    return entries


def get_entry(layer: QgsAnnotationLayer, item_id: str) -> AnnotationEntry | None:
    return next((e for e in list_entries(layer) if e.item_id == item_id), None)


def update_entry(
    layer: QgsAnnotationLayer, item_id: str, name: str, line_color: QColor, fill_color: QColor | None
) -> None:
    """Apply new colors and text to an object. Replaces the item's symbol with the plugin's default style."""
    item = layer.item(item_id)
    kind = _kind_of(item) if item else None
    if kind is None:
        return

    meta = _read_meta(layer)
    _register(meta, item_id)
    name = name.strip()
    if kind == KIND_POINT:
        item.setSymbol(_marker_symbol(line_color))
        _set_point_description(layer, meta, item_id, name)
    elif kind == KIND_LINE:
        item.setSymbol(_line_symbol(line_color))
        meta[item_id]["name"] = name
    elif kind == KIND_POLYGON:
        item.setSymbol(_fill_symbol(line_color, fill_color))
        meta[item_id]["name"] = name
    elif kind == KIND_TEXT:
        if not name:
            delete_entry(layer, item_id)
            return
        # Keep the indent only where it was (orphaned descriptions), not on native QGIS texts
        indent = _DESCRIPTION_INDENT if item.text().startswith(_DESCRIPTION_INDENT) else ""
        item.setText(indent + name)
        fmt = item.format()
        fmt.setColor(line_color)
        item.setFormat(fmt)
    _write_meta(layer, meta)
    _finish(layer)


def delete_entry(layer: QgsAnnotationLayer, item_id: str) -> None:
    """Remove an object, including a point's linked description."""
    meta = _read_meta(layer)
    label_id = meta.get(item_id, {}).get("label_id")
    if label_id and layer.item(label_id):
        layer.removeItem(label_id)
    layer.removeItem(item_id)
    meta.pop(item_id, None)
    _write_meta(layer, meta)
    _finish(layer)


def entry_geometry(layer: QgsAnnotationLayer, item_id: str) -> QgsGeometry | None:
    """Geometry of an item in layer CRS, e.g. for flashing it on the canvas."""
    item = layer.item(item_id)
    if item is None or not hasattr(item, "geometry"):
        return None
    geometry = item.geometry()
    # Marker items return a plain QgsPointXY, line/polygon items an abstract geometry
    if isinstance(geometry, QgsPointXY):
        return QgsGeometry.fromPointXY(geometry)
    return QgsGeometry(geometry.clone())


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


def _kind_of(item) -> str | None:
    if isinstance(item, QgsAnnotationMarkerItem):
        return KIND_POINT
    if isinstance(item, QgsAnnotationLineItem):
        return KIND_LINE
    if isinstance(item, QgsAnnotationPolygonItem):
        return KIND_POLYGON
    if isinstance(item, QgsAnnotationPointTextItem):
        return KIND_TEXT
    return None


def _entry_for(layer, meta, item_id, item, kind) -> AnnotationEntry:
    item_meta = meta[item_id]
    name = item_meta.get("name", "")
    line_color = QColor(0, 0, 0)
    fill_color = None

    symbol_layer = item.symbol().symbolLayer(0) if hasattr(item, "symbol") and item.symbol() else None
    if kind == KIND_POINT:
        if symbol_layer is not None and hasattr(symbol_layer, "fillColor"):
            line_color = symbol_layer.fillColor()
        label = layer.item(item_meta.get("label_id", "")) if item_meta.get("label_id") else None
        name = _strip_indent(label.text()) if label else ""
    elif kind == KIND_LINE:
        if symbol_layer is not None:
            line_color = symbol_layer.color()
    elif kind == KIND_POLYGON:
        if symbol_layer is not None and hasattr(symbol_layer, "strokeColor"):
            line_color = symbol_layer.strokeColor()
            if symbol_layer.brushStyle() != Qt.BrushStyle.NoBrush:
                fill_color = symbol_layer.fillColor()
    elif kind == KIND_TEXT:
        line_color = item.format().color()
        name = _strip_indent(item.text())

    return AnnotationEntry(item_id, kind, item_meta["n"], name, line_color, fill_color)


def _set_point_description(layer, meta, marker_id: str, description: str) -> None:
    """Create, update or remove the description text linked to a marker."""
    description = description.strip()
    label_id = meta[marker_id].get("label_id", "")
    label = layer.item(label_id) if label_id else None

    if not description:
        if label is not None:
            layer.removeItem(label_id)
        meta[marker_id]["label_id"] = ""
        return

    if label is not None:
        label.setText(_DESCRIPTION_INDENT + description)
        return

    marker = layer.item(marker_id)
    text_item = QgsAnnotationPointTextItem(_DESCRIPTION_INDENT + description, QgsPointXY(marker.geometry()))
    text_item.setFormat(_description_format())
    text_item.setAlignment(Qt.AlignmentFlag.AlignLeft)
    text_item.setZIndex(_Z_TEXT)
    meta[marker_id]["label_id"] = layer.addItem(text_item)


def _strip_indent(text: str) -> str:
    return text[len(_DESCRIPTION_INDENT) :] if text.startswith(_DESCRIPTION_INDENT) else text


def _read_meta(layer) -> dict:
    raw = layer.customProperty(_META_PROPERTY, "")
    try:
        meta = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        logger.warning("Ungültige Annotations-Metadaten, werden zurückgesetzt")
        meta = {}
    return meta if isinstance(meta, dict) else {}


def _write_meta(layer, meta: dict) -> None:
    layer.setCustomProperty(_META_PROPERTY, json.dumps(meta))


def _register(meta: dict, item_id: str) -> None:
    """Ensure ``item_id`` has a metadata entry with a stable running number."""
    if item_id in meta:
        return
    next_n = max((m.get("n", 0) for m in meta.values()), default=0) + 1
    meta[item_id] = {"n": next_n, "name": "", "label_id": ""}


def _prune_meta(meta: dict, items: dict) -> bool:
    """Drop metadata of items deleted outside the plugin. Returns True if anything changed."""
    changed = False
    for item_id in list(meta):
        if item_id not in items:
            del meta[item_id]
            changed = True
        elif meta[item_id].get("label_id") and meta[item_id]["label_id"] not in items:
            meta[item_id]["label_id"] = ""
            changed = True
    return changed


def _register_and_finish(layer, item_id: str) -> None:
    meta = _read_meta(layer)
    _register(meta, item_id)
    _write_meta(layer, meta)
    _finish(layer)


def _marker_symbol(color: QColor) -> QgsMarkerSymbol:
    symbol = QgsMarkerSymbol.createSimple(
        {
            "name": "circle",
            "size": str(MARKER_SIZE_MM),
            "outline_color": "255,255,255,255",
            "outline_width": "0.4",
        }
    )
    symbol.symbolLayer(0).setFillColor(color)
    return symbol


def _line_symbol(color: QColor) -> QgsLineSymbol:
    symbol = QgsLineSymbol.createSimple({"line_width": str(LINE_WIDTH_MM), "capstyle": "round", "joinstyle": "round"})
    symbol.symbolLayer(0).setColor(color)
    return symbol


def _fill_symbol(line_color: QColor, fill_color: QColor | None) -> QgsFillSymbol:
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
    return symbol


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
