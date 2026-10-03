import json
from dataclasses import dataclass

from qgis.core import (
    Qgis,
    QgsAnnotationItemEditContext,
    QgsAnnotationItemEditOperationAddNode,
    QgsAnnotationItemEditOperationDeleteNode,
    QgsAnnotationItemEditOperationMoveNode,
    QgsAnnotationItemEditOperationTranslateItem,
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
    QgsRectangle,
    QgsRenderContext,
    QgsTextFormat,
    QgsTextRenderer,
    QgsVertexId,
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
# {item_id: {"n": running number, "name": legacy list-only name, "label_id": linked description item}}
_META_PROPERTY = "thw_toolbox/annotation_items"

# Fallback when an item's line width cannot be read (e.g. restyled with native QGIS tools)
DEFAULT_LINE_WIDTH_MM = 0.8
MARKER_SIZE_MM = 3.0
TEXT_SIZE_MM = 3.5
# Allowed range when scaling descriptions with the move tool
MIN_TEXT_SIZE_MM = 1.0
MAX_TEXT_SIZE_MM = 50.0

# Gap between the marker edge and the start of a new point description
_POINT_LABEL_GAP_MM = 1.0

# Earlier versions pushed point descriptions away from the marker with two
# leading en spaces. They are removed by ``remove_legacy_indents``.
_LEGACY_INDENT = "  "

# Vertices that must remain so editing never deletes a whole line/polygon
MIN_LINE_VERTICES = 2
MIN_POLYGON_VERTICES = 3

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
    """One user-visible annotation object (an item and its linked description count as one)."""

    item_id: str
    kind: str
    number: int
    name: str  # the description shown on the map
    line_color: QColor
    fill_color: QColor | None  # None for unfilled polygons and non-polygons
    line_width: float | None = None  # mm; only for lines and polygons

    @property
    def short_title(self) -> str:
        return f"{_KIND_TITLES[self.kind]} {self.number}"

    @property
    def title(self) -> str:
        return f"{self.short_title}: {self.name}" if self.name else self.short_title


@dataclass
class NodeHandle:
    """A draggable node: a point marker, or one vertex of a line/polygon."""

    item_id: str
    vertex_id: QgsVertexId
    point: QgsPointXY  # layer CRS


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


def add_point(
    layer: QgsAnnotationLayer,
    point: QgsPointXY,
    color: QColor,
    description: str = "",
    units_per_mm: float = 0.0,
) -> None:
    """Add a marker at ``point`` (layer CRS), optionally with a text description.

    ``units_per_mm`` (map units per millimetre at the current scale, see
    ``map_units_per_mm``) places the description next to instead of on the marker.
    """
    marker = QgsAnnotationMarkerItem(QgsPoint(point))
    marker.setSymbol(_marker_symbol(color))
    marker.setZIndex(_Z_MARKER)
    marker_id = layer.addItem(marker)

    meta = _read_meta(layer)
    _register(meta, marker_id)
    _set_description(layer, meta, marker_id, KIND_POINT, description, units_per_mm)
    _write_meta(layer, meta)
    _finish(layer)


def add_line(layer: QgsAnnotationLayer, points: list[QgsPointXY], color: QColor, width_mm: float) -> None:
    """Add a polyline through ``points`` (layer CRS) with a line width of ``width_mm``."""
    item = QgsAnnotationLineItem(QgsLineString([QgsPoint(p) for p in points]))
    item.setSymbol(_line_symbol(color, width_mm))
    item.setZIndex(_Z_LINE)
    _register_and_finish(layer, layer.addItem(item))


def add_polygon(
    layer: QgsAnnotationLayer,
    points: list[QgsPointXY],
    line_color: QColor,
    fill_color: QColor | None,
    width_mm: float,
) -> None:
    """Add a polygon with outline ``line_color``/``width_mm``; filled with ``fill_color`` unless it is None."""
    ring = [QgsPoint(p) for p in points]
    ring.append(QgsPoint(points[0]))
    polygon = QgsPolygon()
    polygon.setExteriorRing(QgsLineString(ring))

    item = QgsAnnotationPolygonItem(polygon)
    item.setSymbol(_fill_symbol(line_color, fill_color, width_mm))
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
    layer: QgsAnnotationLayer,
    item_id: str,
    name: str,
    line_color: QColor,
    fill_color: QColor | None,
    line_width: float | None = None,
    units_per_mm: float = 0.0,
) -> None:
    """Apply new colors, line width and text to an object.

    Replaces the item's symbol with the plugin's default style. ``line_width``
    (mm) only applies to lines and polygons; None keeps the default width.
    ``units_per_mm`` positions a newly created point description (see ``add_point``).
    """
    width_mm = line_width if line_width is not None else DEFAULT_LINE_WIDTH_MM
    item = layer.item(item_id)
    kind = _kind_of(item) if item else None
    if kind is None:
        return

    meta = _read_meta(layer)
    _register(meta, item_id)
    name = name.strip()
    if kind == KIND_POINT:
        item.setSymbol(_marker_symbol(line_color))
        _set_description(layer, meta, item_id, kind, name, units_per_mm)
    elif kind == KIND_LINE:
        item.setSymbol(_line_symbol(line_color, width_mm))
        _set_description(layer, meta, item_id, kind, name)
        meta[item_id]["name"] = ""  # superseded by the map description
    elif kind == KIND_POLYGON:
        item.setSymbol(_fill_symbol(line_color, fill_color, width_mm))
        _set_description(layer, meta, item_id, kind, name)
        meta[item_id]["name"] = ""
    elif kind == KIND_TEXT:
        if not name:
            delete_entry(layer, item_id)
            return
        item.setText(name)
        fmt = item.format()
        fmt.setColor(line_color)
        item.setFormat(fmt)
    _write_meta(layer, meta)
    _finish(layer)


def delete_entry(layer: QgsAnnotationLayer, item_id: str) -> None:
    """Remove an object, including its linked description."""
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
# Moving nodes
# ----------------------------------------------------------------------


def node_handles(layer: QgsAnnotationLayer) -> list[NodeHandle]:
    """All draggable nodes of points, lines and polygons. Texts are moved via ``description_labels`` instead."""
    context = QgsAnnotationItemEditContext()
    handles = []
    for item_id, item in layer.items().items():
        if _kind_of(item) in (None, KIND_TEXT):
            continue
        for node in item.nodesV2(context):
            # Skip e.g. callout handles — only real vertices are movable here
            if node.type() == Qgis.AnnotationItemNodeType.VertexHandle:
                handles.append(NodeHandle(item_id, node.id(), node.point()))
    return handles


def preview_moved_node(layer: QgsAnnotationLayer, handle: NodeHandle, new_point: QgsPointXY) -> QgsGeometry | None:
    """Geometry of the item (layer CRS) as it would look after moving ``handle`` to ``new_point``."""
    item = layer.item(handle.item_id)
    if item is None:
        return None
    results = item.transientEditResultsV2(_move_operation(handle, new_point), QgsAnnotationItemEditContext())
    return results.representativeGeometry() if results else None


def move_node(layer: QgsAnnotationLayer, handle: NodeHandle, new_point: QgsPointXY) -> bool:
    """Move ``handle`` to ``new_point`` (layer CRS).

    A point's description moves along, keeping its (possibly user-adjusted)
    offset; descriptions of lines/polygons stay where they are — they are
    positioned independently of the vertices.
    """
    context = QgsAnnotationItemEditContext()
    result = layer.applyEditV2(_move_operation(handle, new_point), context)
    if result != Qgis.AnnotationItemEditOperationResult.Success:
        logger.warning("Verschieben der Annotation %s fehlgeschlagen: %s", handle.item_id, result)
        return False

    label_id = _read_meta(layer).get(handle.item_id, {}).get("label_id")
    if label_id and layer.item(label_id) and item_kind(layer, handle.item_id) == KIND_POINT:
        dx = new_point.x() - handle.point.x()
        dy = new_point.y() - handle.point.y()
        layer.applyEditV2(QgsAnnotationItemEditOperationTranslateItem(label_id, dx, dy), context)
    _finish(layer)
    return True


def map_units_per_mm(map_settings) -> float:
    """Map units per millimetre at the scale of ``map_settings`` (e.g. the canvas)."""
    context = QgsRenderContext.fromMapSettings(map_settings)
    return context.convertToMapUnits(1.0, Qgis.RenderUnit.Millimeters)


def remove_legacy_indents(layer: QgsAnnotationLayer, map_settings) -> bool:
    """Drop the leading en spaces older versions put in front of point descriptions.

    The text is shifted right by the width the spaces took up at the scale of
    ``map_settings``, so it stays where it was visually. Returns True if anything changed.
    """
    context = QgsRenderContext.fromMapSettings(map_settings)
    changed = False
    for item in layer.items().values():
        if not isinstance(item, QgsAnnotationPointTextItem) or not item.text().startswith(_LEGACY_INDENT):
            continue
        text = item.text()
        stripped = text[len(_LEGACY_INDENT) :]
        fmt = item.format()
        # Width difference instead of measuring the spaces alone, which some renderers trim
        indent_px = QgsTextRenderer.textWidth(context, fmt, [text]) - QgsTextRenderer.textWidth(
            context, fmt, [stripped]
        )
        item.setText(stripped)
        if indent_px > 0 and item.alignment() & Qt.AlignmentFlag.AlignLeft:
            point = QgsPointXY(item.point())
            item.setPoint(
                QgsPointXY(point.x() + context.convertToMapUnits(indent_px, Qgis.RenderUnit.Pixels), point.y())
            )
        changed = True
    if changed:
        _finish(layer)
    return changed


def label_bounds(layer: QgsAnnotationLayer, label_id: str, context: QgsRenderContext) -> QgsRectangle | None:
    """Extent of the rendered text (layer CRS) for the map scale of ``context``."""
    item = layer.item(label_id)
    return item.boundingBox(context) if item is not None else None


def description_labels(layer: QgsAnnotationLayer) -> list[str]:
    """Ids of all texts that can be moved and scaled — every description plus standalone texts."""
    return [item_id for item_id, item in layer.items().items() if isinstance(item, QgsAnnotationPointTextItem)]


def label_owner(layer: QgsAnnotationLayer, label_id: str) -> str | None:
    """Id of the point/line/polygon a description belongs to, or None for standalone texts."""
    for item_id, item_meta in _read_meta(layer).items():
        if item_meta.get("label_id") == label_id and layer.item(item_id) is not None:
            return item_id
    return None


def label_anchor(layer: QgsAnnotationLayer, label_id: str) -> QgsPointXY | None:
    """Anchor point of a text (layer CRS) — the point that stays fixed while scaling."""
    item = layer.item(label_id)
    return QgsPointXY(item.point()) if isinstance(item, QgsAnnotationPointTextItem) else None


def clamp_label_scale(layer: QgsAnnotationLayer, label_id: str, factor: float) -> float:
    """Limit ``factor`` so the resulting text size stays within MIN/MAX_TEXT_SIZE_MM."""
    item = layer.item(label_id)
    if not isinstance(item, QgsAnnotationPointTextItem) or item.format().size() <= 0:
        return 1.0
    size = item.format().size()
    return max(MIN_TEXT_SIZE_MM / size, min(MAX_TEXT_SIZE_MM / size, factor))


def scale_label(layer: QgsAnnotationLayer, label_id: str, factor: float, pivot: QgsPointXY | None = None) -> bool:
    """Multiply the text size (and its halo) of a description by ``factor`` (clamped).

    Text always grows around its anchor point. With a ``pivot`` (layer CRS),
    the anchor is shifted so the text scales around the pivot instead.
    """
    item = layer.item(label_id)
    if not isinstance(item, QgsAnnotationPointTextItem):
        return False
    factor = clamp_label_scale(layer, label_id, factor)
    fmt = item.format()
    fmt.setSize(fmt.size() * factor)
    buffer = fmt.buffer()
    buffer.setSize(buffer.size() * factor)
    fmt.setBuffer(buffer)
    item.setFormat(fmt)

    if pivot is not None:
        # Every text point p is drawn at anchor + (p - anchor) * factor; moving the anchor to
        # pivot + (anchor - pivot) * factor keeps the pivot in place.
        anchor = QgsPointXY(item.point())
        dx = (anchor.x() - pivot.x()) * (factor - 1)
        dy = (anchor.y() - pivot.y()) * (factor - 1)
        layer.applyEditV2(QgsAnnotationItemEditOperationTranslateItem(label_id, dx, dy), QgsAnnotationItemEditContext())
    _finish(layer)
    return True


def translate_label(layer: QgsAnnotationLayer, label_id: str, dx: float, dy: float) -> bool:
    """Shift a text item by ``dx``/``dy`` (layer CRS units)."""
    result = layer.applyEditV2(
        QgsAnnotationItemEditOperationTranslateItem(label_id, dx, dy), QgsAnnotationItemEditContext()
    )
    if result != Qgis.AnnotationItemEditOperationResult.Success:
        logger.warning("Beschreibung %s konnte nicht verschoben werden: %s", label_id, result)
        return False
    _finish(layer)
    return True


def edge_geometries(layer: QgsAnnotationLayer) -> list[tuple[str, QgsGeometry]]:
    """(item_id, geometry in layer CRS) of all lines and polygons — the items new vertices can be added to."""
    result = []
    for item_id, item in layer.items().items():
        if _kind_of(item) in (KIND_LINE, KIND_POLYGON):
            geom = entry_geometry(layer, item_id)
            if geom is not None and not geom.isEmpty():
                result.append((item_id, geom))
    return result


def add_node(layer: QgsAnnotationLayer, item_id: str, point: QgsPointXY) -> bool:
    """Insert a vertex at ``point`` (layer CRS) into the nearest segment of a line/polygon."""
    result = layer.applyEditV2(
        QgsAnnotationItemEditOperationAddNode(item_id, QgsPoint(point)), QgsAnnotationItemEditContext()
    )
    if result != Qgis.AnnotationItemEditOperationResult.Success:
        logger.warning("Stützpunkt konnte nicht eingefügt werden (%s): %s", item_id, result)
        return False
    _finish(layer)
    return True


def can_delete_node(layer: QgsAnnotationLayer, handle: NodeHandle) -> bool:
    """True if removing ``handle`` keeps the item valid.

    QGIS clears a line/ring completely when it drops below its minimum vertex
    count, which would remove the whole annotation — so that case is refused here.
    Single points can never be removed via their node.
    """
    item = layer.item(handle.item_id)
    kind = _kind_of(item) if item else None
    if kind == KIND_LINE:
        return item.geometry().numPoints() > MIN_LINE_VERTICES
    if kind == KIND_POLYGON:
        polygon = item.geometry()
        ring_index = handle.vertex_id.ring
        ring = polygon.exteriorRing() if ring_index == 0 else polygon.interiorRing(ring_index - 1)
        # Rings are closed: the start vertex is stored twice
        return ring is not None and ring.numPoints() - 1 > MIN_POLYGON_VERTICES
    return False


def delete_node(layer: QgsAnnotationLayer, handle: NodeHandle) -> bool:
    """Remove a line/polygon vertex. Returns False (without changing anything) if not allowed."""
    if not can_delete_node(layer, handle):
        return False
    result = layer.applyEditV2(
        QgsAnnotationItemEditOperationDeleteNode(handle.item_id, handle.vertex_id, QgsPoint(handle.point)),
        QgsAnnotationItemEditContext(),
    )
    if result != Qgis.AnnotationItemEditOperationResult.Success:
        logger.warning("Stützpunkt konnte nicht entfernt werden (%s): %s", handle.item_id, result)
        return False
    _finish(layer)
    return True


def _move_operation(handle: NodeHandle, new_point: QgsPointXY) -> QgsAnnotationItemEditOperationMoveNode:
    return QgsAnnotationItemEditOperationMoveNode(
        handle.item_id, handle.vertex_id, QgsPoint(handle.point), QgsPoint(new_point)
    )


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


def item_kind(layer: QgsAnnotationLayer, item_id: str) -> str | None:
    """KIND_* of an item, or None if it does not exist or is of an unsupported type."""
    item = layer.item(item_id)
    return _kind_of(item) if item is not None else None


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
    line_width = None

    symbol_layer = item.symbol().symbolLayer(0) if hasattr(item, "symbol") and item.symbol() else None
    if kind == KIND_POINT:
        if symbol_layer is not None and hasattr(symbol_layer, "fillColor"):
            line_color = symbol_layer.fillColor()
    elif kind == KIND_LINE:
        line_width = DEFAULT_LINE_WIDTH_MM
        if symbol_layer is not None:
            line_color = symbol_layer.color()
            if hasattr(symbol_layer, "width"):
                line_width = symbol_layer.width()
    elif kind == KIND_POLYGON:
        line_width = DEFAULT_LINE_WIDTH_MM
        if symbol_layer is not None and hasattr(symbol_layer, "strokeColor"):
            line_color = symbol_layer.strokeColor()
            line_width = symbol_layer.strokeWidth()
            if symbol_layer.brushStyle() != Qt.BrushStyle.NoBrush:
                fill_color = symbol_layer.fillColor()
    elif kind == KIND_TEXT:
        line_color = item.format().color()
        name = _strip_indent(item.text())

    if kind != KIND_TEXT:
        label = layer.item(item_meta.get("label_id", "")) if item_meta.get("label_id") else None
        if label is not None:
            name = _strip_indent(label.text())

    return AnnotationEntry(item_id, kind, item_meta["n"], name, line_color, fill_color, line_width)


def _set_description(layer, meta, item_id: str, kind: str, description: str, units_per_mm: float = 0.0) -> None:
    """Create, update or remove the description text linked to an item.

    New point descriptions start to the upper right of the marker (offset by
    the marker radius plus a gap, converted with ``units_per_mm``); descriptions
    of lines and polygons are centered on the line's midpoint or inside the area.
    """
    description = description.strip()
    label_id = meta[item_id].get("label_id", "")
    label = layer.item(label_id) if label_id else None

    if not description:
        if label is not None:
            layer.removeItem(label_id)
        meta[item_id]["label_id"] = ""
        return

    if label is not None:
        label.setText(description)
        return

    anchor = _label_anchor(layer, item_id, kind)
    if anchor is None:
        return
    if kind == KIND_POINT:
        offset = (MARKER_SIZE_MM / 2 + _POINT_LABEL_GAP_MM) * units_per_mm
        anchor = QgsPointXY(anchor.x() + offset, anchor.y())
    text_item = QgsAnnotationPointTextItem(description, anchor)
    text_item.setFormat(_description_format())
    text_item.setAlignment(Qt.AlignmentFlag.AlignLeft if kind == KIND_POINT else Qt.AlignmentFlag.AlignHCenter)
    text_item.setZIndex(_Z_TEXT)
    meta[item_id]["label_id"] = layer.addItem(text_item)


def _label_anchor(layer, item_id: str, kind: str) -> QgsPointXY | None:
    """Initial description position (layer CRS) for a new label."""
    geom = entry_geometry(layer, item_id)
    if geom is None or geom.isEmpty():
        return None
    if kind == KIND_LINE:
        geom = geom.interpolate(geom.length() / 2)
    elif kind == KIND_POLYGON:
        geom = geom.pointOnSurface()
    if geom is None or geom.isEmpty():
        return None
    return geom.asPoint()


def _strip_indent(text: str) -> str:
    return text[len(_LEGACY_INDENT) :] if text.startswith(_LEGACY_INDENT) else text


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


def _line_symbol(color: QColor, width_mm: float) -> QgsLineSymbol:
    symbol = QgsLineSymbol.createSimple({"line_width": str(width_mm), "capstyle": "round", "joinstyle": "round"})
    symbol.symbolLayer(0).setColor(color)
    return symbol


def _fill_symbol(line_color: QColor, fill_color: QColor | None, width_mm: float) -> QgsFillSymbol:
    symbol = QgsFillSymbol.createSimple(
        {
            "style": "solid" if fill_color is not None else "no",
            "outline_style": "solid",
            "outline_width": str(width_mm),
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
