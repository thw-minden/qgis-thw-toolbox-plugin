import functools
import json
import math
from contextlib import contextmanager
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
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsDistanceArea,
    QgsExpression,
    QgsFeature,
    QgsFillSymbol,
    QgsGeometry,
    QgsLineString,
    QgsLineSymbol,
    QgsMarkerLineSymbolLayer,
    QgsMarkerSymbol,
    QgsPoint,
    QgsPointXY,
    QgsPolygon,
    QgsProject,
    QgsRectangle,
    QgsRenderContext,
    QgsRendererCategory,
    QgsSimpleMarkerSymbolLayer,
    QgsTextFormat,
    QgsTextRenderer,
    QgsVectorLayer,
    QgsVertexId,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor

from ..layout.mgrs_grid import point_to_mgrs
from ..logging_utils import get_logger
from ..util.units import format_meters
from .fields import string_field

logger = get_logger(__name__)

# Display name in the QGIS layer panel
ANNOTATION_LAYER_DISPLAY_NAME = "THW Toolbox Annotationen"
# Custom property marking our annotation layer (survives renaming by the user)
_LAYER_PROPERTY = "thw_toolbox/annotation_layer"
# Custom property holding per-item metadata as JSON:
# {item_id: {"n": running number, "name": legacy list-only name, "label_id": linked description item,
#            "radius_m": radius circle of a point in meters (optional),
#            "show_dims": show radius / edge lengths on the map, "dim_ids": the generated length labels,
#            "desc_template": point description as entered, with POSITION_PLACEHOLDER (optional),
#            "arrows": arrowheads of a line, one of ARROWS_* (optional),
#            "arrow_size_mm": arrowhead size, 0 = automatic from the line width (optional),
#            "dim_size_mm": text size of the measurement labels (optional),
#            "dim_overrides": {label index: {"dx", "dy": manual offset in layer units, "size": manual mm}},
#            "dim_count": number of measurement labels the overrides refer to}}

# Placeholder in point descriptions, replaced on the map by the point's MGRS coordinate
POSITION_PLACEHOLDER = "$POS"
_POSITION_UNAVAILABLE = "(Position außerhalb UTM)"
META_PROPERTY = "thw_toolbox/annotation_items"

# Fallback when an item's line width cannot be read (e.g. restyled with native QGIS tools)
DEFAULT_LINE_WIDTH_MM = 0.8
MARKER_SIZE_MM = 3.0
TEXT_SIZE_MM = 3.5
# Size of the generated measurement labels (radius, edge lengths)
DIMENSION_TEXT_SIZE_MM = 2.5
# Allowed range when scaling descriptions with the move tool
MIN_TEXT_SIZE_MM = 1.0
MAX_TEXT_SIZE_MM = 50.0

# Opacity of a point's radius circle fill relative to the point color
_RADIUS_FILL_OPACITY = 0.25

# Gap between the marker edge and a new point description (placed centered above the marker)
_POINT_LABEL_GAP_MM = 1.0

# Earlier versions pushed point descriptions away from the marker with two
# leading en spaces. They are removed by ``migrate_legacy_labels``.
_LEGACY_INDENT = "  "

# Vertices that must remain so editing never deletes a whole line/polygon
MIN_LINE_VERTICES = 2
MIN_POLYGON_VERTICES = 3

# Draw order inside the annotation layer: areas below lines below points
_Z_POLYGON = 0
_Z_LINE = 1
_Z_MARKER = 2
_Z_TEXT = 3

# Arrowheads on lines
ARROWS_NONE = "none"
ARROWS_END = "end"
ARROWS_START = "start"
ARROWS_BOTH = "both"
ARROW_MODES = (ARROWS_NONE, ARROWS_END, ARROWS_START, ARROWS_BOTH)

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
    fill_color: QColor | None  # polygon fill or a point's radius circle fill; None if unfilled / not applicable
    line_width: float | None = None  # mm; only for lines and polygons
    radius_m: float | None = None  # radius circle in meters; only for points (0 = none)
    show_dimensions: bool = False  # radius / edge lengths shown on the map
    map_text: str = ""  # description as shown on the map ($POS resolved); empty = same as name
    arrows: str = ARROWS_NONE  # arrowheads of a line (ARROWS_*)
    arrow_size_mm: float = 0.0  # arrowhead size; 0 = automatic (see arrow_size_for)
    dimension_size_mm: float = 2.5  # text size of the radius / edge length labels

    @property
    def short_title(self) -> str:
        return f"{_KIND_TITLES[self.kind]} {self.number}"

    @property
    def title(self) -> str:
        # Multi-line descriptions are shown on one line in lists
        text = self.map_text or self.name
        name = " / ".join(line.strip() for line in text.splitlines() if line.strip())
        return f"{self.short_title}: {name}" if name else self.short_title


@dataclass
class Measurements:
    """Real-world dimensions of an object; None where not applicable (e.g. no area for lines)."""

    length_m: float | None = None
    perimeter_m: float | None = None
    area_m2: float | None = None


@dataclass
class LabelFrame:
    """Box around a text item, rotated with the text (layer CRS).

    ``corners`` are top-left, top-right, bottom-right, bottom-left in the text's own
    orientation; ``angle`` is the text angle relative to the map axes (degrees, clockwise).
    """

    corners: list[QgsPointXY]
    anchor: QgsPointXY
    angle: float

    @property
    def center(self) -> QgsPointXY:
        return QgsPointXY(sum(p.x() for p in self.corners) / 4, sum(p.y() for p in self.corners) / 4)

    def geometry(self) -> QgsGeometry:
        return QgsGeometry.fromPolygonXY([[*self.corners, self.corners[0]]])


def rotate_point(point: QgsPointXY, pivot: QgsPointXY, clockwise_deg: float) -> QgsPointXY:
    """``point`` rotated around ``pivot`` by ``clockwise_deg`` (map coordinates, y pointing up)."""
    t = math.radians(clockwise_deg)
    dx, dy = point.x() - pivot.x(), point.y() - pivot.y()
    return QgsPointXY(pivot.x() + dx * math.cos(t) + dy * math.sin(t), pivot.y() - dx * math.sin(t) + dy * math.cos(t))


@dataclass
class NodeHandle:
    """A draggable node: a point marker, or one vertex of a line/polygon."""

    item_id: str
    vertex_id: QgsVertexId
    point: QgsPointXY  # layer CRS


# ----------------------------------------------------------------------
# Undo / redo
# ----------------------------------------------------------------------

_history = None  # AnnotationHistory, set by the plugin (see set_history)
# Current MGRS resolution (m) for $POS where callers do not pass one (e.g. moving a text)
_mgrs_resolution = lambda: 1.0  # noqa: E731


def set_mgrs_resolution_provider(provider) -> None:
    """Callable returning the project's MGRS resolution in meters (used to keep $POS texts current)."""
    global _mgrs_resolution
    _mgrs_resolution = provider or (lambda: 1.0)


_history_depth = 0  # > 0 while an undo step is being recorded — nested changes join it


def set_history(history) -> None:
    """Record every following change in ``history`` (an ``AnnotationHistory``; None disables recording)."""
    global _history
    _history = history


@contextmanager
def undo_step(layer: QgsAnnotationLayer, label: str):
    """Group all changes inside the ``with`` block into one undo step called ``label``."""
    global _history_depth
    if _history is None or _history_depth > 0 or layer is None:
        yield
        return
    before = _history.capture(layer, label)
    _history_depth += 1
    try:
        yield
    finally:
        _history_depth -= 1
        _history.commit(layer, before)


def _undoable(label: str):
    """Decorator: the call (whose first argument is the annotation layer) becomes one undo step."""

    def decorate(func):
        @functools.wraps(func)
        def wrapper(layer, *args, **kwargs):
            with undo_step(layer, label):
                return func(layer, *args, **kwargs)

        return wrapper

    return decorate


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


@_undoable("Punkt setzen")
def add_point(
    layer: QgsAnnotationLayer,
    point: QgsPointXY,
    color: QColor,
    description: str = "",
    units_per_mm: float = 0.0,
    radius_m: float = 0.0,
    mgrs_resolution_m: float = 1.0,
) -> None:
    """Add a marker at ``point`` (layer CRS), optionally with a text description and a radius circle.

    ``units_per_mm`` (map units per millimetre at the current scale, see
    ``map_units_per_mm``) places the description next to instead of on the marker.
    ``radius_m`` > 0 adds a true-to-scale circle with the default fill.
    ``$POS`` in the description is replaced by the MGRS coordinate at ``mgrs_resolution_m``.
    """
    radius = radius_m if radius_m > 0 else 0.0
    marker = QgsAnnotationMarkerItem(QgsPoint(point))
    marker.setSymbol(_marker_symbol(color, radius))
    marker.setZIndex(_Z_MARKER)
    marker_id = layer.addItem(marker)

    meta = _read_meta(layer)
    _register(meta, marker_id)
    meta[marker_id]["radius_m"] = radius
    _set_description(layer, meta, marker_id, KIND_POINT, description, units_per_mm, mgrs_resolution_m)
    _write_meta(layer, meta)
    _finish(layer)


@_undoable("Linie zeichnen")
def add_line(layer: QgsAnnotationLayer, points: list[QgsPointXY], color: QColor, width_mm: float) -> None:
    """Add a polyline through ``points`` (layer CRS) with a line width of ``width_mm``."""
    item = QgsAnnotationLineItem(QgsLineString([QgsPoint(p) for p in points]))
    item.setSymbol(_line_symbol(color, width_mm))
    item.setZIndex(_Z_LINE)
    _register_and_finish(layer, layer.addItem(item))


@_undoable("Polygon zeichnen")
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


@_undoable("Text setzen")
def add_text(
    layer: QgsAnnotationLayer,
    point: QgsPointXY,
    text: str,
    color: QColor | None = None,
    mgrs_resolution_m: float = 1.0,
) -> str | None:
    """Place a standalone text; ``point`` (layer CRS) is its anchor at the bottom center of the text.

    ``$POS`` in the text is replaced by the MGRS coordinate of the anchor and kept up to date
    when the text is moved. Returns the new item id (None for an empty text).
    """
    text = text.strip()
    if not text:
        return None
    fmt = _description_format()
    if color is not None:
        fmt.setColor(color)
    text_item = QgsAnnotationPointTextItem(text, point)
    text_item.setFormat(fmt)
    text_item.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    text_item.setZIndex(_Z_TEXT)
    item_id = layer.addItem(text_item)

    meta = _read_meta(layer)
    _register(meta, item_id)
    _set_text_with_position(layer, meta, item_id, text, mgrs_resolution_m)
    _write_meta(layer, meta)
    _finish(layer)
    return item_id


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
    # Measurement labels of objects deleted outside the plugin would otherwise show up as stray texts
    orphaned = [d for item_id, m in meta.items() if item_id not in items for d in m.get("dim_ids", []) if d in items]
    for dim_id in orphaned:
        layer.removeItem(dim_id)
    if orphaned:
        items = layer.items()
        _finish(layer)
    changed = _prune_meta(meta, items)

    hidden = {m.get("label_id") for m in meta.values() if m.get("label_id")} | _dimension_ids(meta)
    entries = []
    for item_id, item in items.items():
        if item_id in hidden:
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


@_undoable("Annotation bearbeiten")
def update_entry(
    layer: QgsAnnotationLayer,
    item_id: str,
    name: str,
    line_color: QColor,
    fill_color: QColor | None,
    line_width: float | None = None,
    units_per_mm: float = 0.0,
    radius_m: float | None = None,
    show_dimensions: bool | None = None,
    mgrs_resolution_m: float = 1.0,
    arrows: str | None = None,
    arrow_size_mm: float | None = None,
    dimension_size_mm: float | None = None,
) -> None:
    """Apply new colors, line width, radius, text and measurement display to an object.

    Replaces the item's symbol with the plugin's default style. ``line_width``
    (mm) only applies to lines and polygons; None keeps the default width.
    ``radius_m`` only applies to points: a true-to-scale circle around the
    marker (0/None = none), filled with ``fill_color`` (None = derived from the
    point color). ``units_per_mm`` positions a newly created point description
    (see ``add_point``). ``show_dimensions`` toggles the radius / edge length
    labels on the map (None keeps the current setting). ``$POS`` in a point
    description is replaced by the MGRS coordinate at ``mgrs_resolution_m``.
    ``arrows`` (ARROWS_*) sets the arrowheads of a line and ``arrow_size_mm`` their
    size (0 = automatic); None keeps the current setting. A changed
    ``dimension_size_mm`` sets the size of the measurement labels and resets labels
    that were resized by hand (manually moved positions are kept).
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
        radius = radius_m if radius_m and radius_m > 0 else 0.0
        item.setSymbol(_marker_symbol(line_color, radius, fill_color))
        meta[item_id]["radius_m"] = radius
        _set_description(layer, meta, item_id, kind, name, units_per_mm, mgrs_resolution_m)
    elif kind == KIND_LINE:
        if arrows in ARROW_MODES:
            meta[item_id]["arrows"] = arrows
        if arrow_size_mm is not None:
            meta[item_id]["arrow_size_mm"] = max(0.0, float(arrow_size_mm))
        item.setSymbol(
            _line_symbol(
                line_color,
                width_mm,
                meta[item_id].get("arrows", ARROWS_NONE),
                meta[item_id].get("arrow_size_mm", 0.0),
            )
        )
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
        _set_text_with_position(layer, meta, item_id, name, mgrs_resolution_m)
        fmt = item.format()
        fmt.setColor(line_color)
        item.setFormat(fmt)
    if kind != KIND_TEXT:
        if show_dimensions is not None:
            meta[item_id]["show_dims"] = bool(show_dimensions)
        current_size = meta[item_id].get("dim_size_mm", DIMENSION_TEXT_SIZE_MM)
        if dimension_size_mm is not None and abs(dimension_size_mm - current_size) > 1e-6:
            meta[item_id]["dim_size_mm"] = float(dimension_size_mm)
            for override in meta[item_id].get("dim_overrides", {}).values():
                override.pop("size", None)
        _refresh_dimensions(layer, meta, item_id)
    _write_meta(layer, meta)
    _finish(layer)


@_undoable("Annotation löschen")
def delete_entry(layer: QgsAnnotationLayer, item_id: str) -> None:
    """Remove an object, including its linked description and measurement labels."""
    meta = _read_meta(layer)
    item_meta = meta.get(item_id, {})
    for linked_id in [item_meta.get("label_id"), *item_meta.get("dim_ids", [])]:
        if linked_id and layer.item(linked_id):
            layer.removeItem(linked_id)
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
# Export
# ----------------------------------------------------------------------


def polygon_export_layer(layer: QgsAnnotationLayer, item_ids: list[str] | None = None) -> QgsVectorLayer | None:
    """Temporary (not added to the project) polygon layer with annotation polygons, for exporters.

    ``item_ids`` limits the layer to these polygons (default: all). Each feature is named
    like its dock list entry (e.g. "Polygon 2: Sammelraum") and styled in its own color —
    the fill color, or for unfilled polygons the outline color with a fully transparent
    fill. A single polygon also names the layer (and thus the suggested file name).
    None if there are no matching polygons.
    """
    entries = [e for e in list_entries(layer) if e.kind == KIND_POLYGON and (item_ids is None or e.item_id in item_ids)]
    if not entries:
        return None

    layer_name = entries[0].title if len(entries) == 1 else "THW Annotationen Polygone"
    export = QgsVectorLayer("Polygon", layer_name, "memory")
    export.setCrs(layer.crs())
    provider = export.dataProvider()
    provider.addAttributes([string_field("name"), string_field("item_id")])
    export.updateFields()

    features = []
    categories = []
    for entry in entries:
        geom = entry_geometry(layer, entry.item_id)
        if geom is None or geom.isEmpty():
            continue
        feature = QgsFeature(export.fields())
        feature.setGeometry(geom)
        feature.setAttributes([entry.title, entry.item_id])
        features.append(feature)

        if entry.fill_color is not None:
            color = QColor(entry.fill_color)
        else:
            color = QColor(entry.line_color)
            color.setAlpha(0)
        symbol = QgsFillSymbol.createSimple({})
        symbol.setColor(color)
        categories.append(QgsRendererCategory(entry.item_id, symbol, entry.title))

    provider.addFeatures(features)
    export.updateExtents()
    # Exporters take per-feature colors from the renderer and labels from the display field
    export.setRenderer(QgsCategorizedSymbolRenderer("item_id", categories))
    export.setDisplayExpression(QgsExpression.quotedColumnRef("name"))
    return export


def export_extent(layer: QgsAnnotationLayer, margin_m: float = 50.0) -> QgsRectangle | None:
    """Area (layer CRS) covering all annotations, for rendering exports such as MBTiles.

    QGIS counts a marker or text only by its anchor point, so radius circles are added
    explicitly and everything is padded by ``margin_m`` (plus 5 %) for descriptions.
    None if the layer is empty.
    """
    items = layer.items()
    if not items:
        return None
    meta = _read_meta(layer)
    da = distance_area(layer.crs())

    extent = QgsRectangle()
    extent.setNull()
    for item_id, item in items.items():
        geom = entry_geometry(layer, item_id) if hasattr(item, "geometry") else None
        if geom is None and isinstance(item, QgsAnnotationPointTextItem):
            geom = QgsGeometry.fromPointXY(QgsPointXY(item.point()))
        if geom is None or geom.isEmpty():
            continue
        box = geom.boundingBox()
        radius = meta.get(item_id, {}).get("radius_m") or 0.0
        if radius > 0:
            box.grow(radius * _layer_units_per_meter(da, box.center()))
        extent.combineExtentWith(box)
    if extent.isNull():
        return None

    padding = margin_m * _layer_units_per_meter(da, extent.center())
    padding += 0.05 * max(extent.width(), extent.height())
    extent.grow(padding)
    return extent


# ----------------------------------------------------------------------
# Measuring
# ----------------------------------------------------------------------


def distance_area(crs: QgsCoordinateReferenceSystem) -> QgsDistanceArea:
    """Ellipsoidal measurement for geometries in ``crs``, using the project's ellipsoid setting."""
    project = QgsProject.instance()
    da = QgsDistanceArea()
    da.setSourceCrs(crs, project.transformContext())
    da.setEllipsoid(project.ellipsoid())
    return da


def measure_entry(layer: QgsAnnotationLayer, item_id: str) -> Measurements:
    """Length of a line, perimeter and area of a polygon or a point's radius circle (meters / m²).

    Empty for texts and points without radius.
    """
    kind = item_kind(layer, item_id)
    if kind == KIND_POINT:
        radius = _read_meta(layer).get(item_id, {}).get("radius_m") or 0.0
        if radius <= 0:
            return Measurements()
        return Measurements(perimeter_m=2 * math.pi * radius, area_m2=math.pi * radius**2)
    geom = entry_geometry(layer, item_id) if kind in (KIND_LINE, KIND_POLYGON) else None
    if geom is None or geom.isEmpty():
        return Measurements()

    da = distance_area(layer.crs())
    if kind == KIND_LINE:
        return Measurements(length_m=da.convertLengthMeasurement(da.measureLength(geom), Qgis.DistanceUnit.Meters))
    return Measurements(
        perimeter_m=da.convertLengthMeasurement(da.measurePerimeter(geom), Qgis.DistanceUnit.Meters),
        area_m2=da.convertAreaMeasurement(da.measureArea(geom), Qgis.AreaUnit.SquareMeters),
    )


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


@_undoable("Punkt verschieben")
def move_node(
    layer: QgsAnnotationLayer, handle: NodeHandle, new_point: QgsPointXY, mgrs_resolution_m: float = 1.0
) -> bool:
    """Move ``handle`` to ``new_point`` (layer CRS).

    A point's description moves along, keeping its (possibly user-adjusted)
    offset; descriptions of lines/polygons stay where they are — they are
    positioned independently of the vertices. A ``$POS`` in a point's
    description is updated to the new position (at ``mgrs_resolution_m``).
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
        _update_position_text(layer, handle.item_id, mgrs_resolution_m)
    _update_dimensions(layer, handle.item_id)
    _finish(layer)
    return True


def map_units_per_mm(map_settings) -> float:
    """Map units per millimetre at the scale of ``map_settings`` (e.g. the canvas)."""
    context = QgsRenderContext.fromMapSettings(map_settings)
    return context.convertToMapUnits(1.0, Qgis.RenderUnit.Millimeters)


def migrate_legacy_labels(layer: QgsAnnotationLayer, map_settings) -> bool:
    """Bring point descriptions of older versions up to date without moving them visually.

    - Drop the leading en spaces older versions put in front of point descriptions;
      the text is shifted right by the width the spaces took up.
    - Switch left-aligned point descriptions to centered text; the anchor moves
      right by half the text width.

    Widths are measured at the scale of ``map_settings``. Returns True if anything changed.
    """
    context = QgsRenderContext.fromMapSettings(map_settings)
    changed = _remove_legacy_indents(layer, context)
    changed = _center_point_labels(layer, context) or changed
    if changed:
        _finish(layer)
    return changed


def _remove_legacy_indents(layer: QgsAnnotationLayer, context: QgsRenderContext) -> bool:
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
    return changed


def _center_point_labels(layer: QgsAnnotationLayer, context: QgsRenderContext) -> bool:
    meta = _read_meta(layer)
    point_labels = [
        m["label_id"] for item_id, m in meta.items() if m.get("label_id") and item_kind(layer, item_id) == KIND_POINT
    ]
    changed = False
    for label_id in point_labels:
        item = layer.item(label_id)
        if not isinstance(item, QgsAnnotationPointTextItem):
            continue
        if item.alignment() & Qt.AlignmentFlag.AlignHCenter:
            continue
        width_px = QgsTextRenderer.textWidth(context, item.format(), item.text().split("\n"))
        point = QgsPointXY(item.point())
        shift = context.convertToMapUnits(width_px / 2, Qgis.RenderUnit.Pixels)
        item.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        item.setPoint(QgsPointXY(point.x() + shift, point.y()))
        changed = True
    return changed


def label_frame(layer: QgsAnnotationLayer, label_id: str, context: QgsRenderContext) -> LabelFrame | None:
    """The text's box at the scale of ``context``, rotated with the text (unlike ``label_bounds``).

    Mirrors QgsAnnotationPointTextItem::boundingBox(): the unrotated box spans the text
    width (positioned by the horizontal alignment) and height above the baseline at the
    anchor; it is then rotated by the text angle around the anchor.
    """
    item = layer.item(label_id)
    if not isinstance(item, QgsAnnotationPointTextItem):
        return None
    fmt = item.format()
    lines = [item.text()] if fmt.allowHtmlFormatting() else item.text().split("\n")
    width = context.convertToMapUnits(QgsTextRenderer.textWidth(context, fmt, lines), Qgis.RenderUnit.Pixels)
    height = context.convertToMapUnits(QgsTextRenderer.textHeight(context, fmt, lines), Qgis.RenderUnit.Pixels)
    if width <= 0 or height <= 0:
        return None

    alignment = item.alignment()
    if alignment & Qt.AlignmentFlag.AlignRight:
        left = -width
    elif alignment & Qt.AlignmentFlag.AlignHCenter:
        left = -width / 2
    else:
        left = 0.0

    angle = item.angle()
    if item.rotationMode() == Qgis.SymbolRotationMode.IgnoreMapRotation:
        # Drawn at a fixed screen angle — relative to the (rotated) map axes that is angle - map rotation
        angle -= context.mapToPixel().mapRotation()

    anchor = QgsPointXY(item.point())
    offsets = ((left, height), (left + width, height), (left + width, 0.0), (left, 0.0))
    corners = [rotate_point(QgsPointXY(anchor.x() + dx, anchor.y() + dy), anchor, angle) for dx, dy in offsets]
    return LabelFrame(corners, anchor, angle)


@_undoable("Beschriftung drehen")
def rotate_label(layer: QgsAnnotationLayer, label_id: str, clockwise_deg: float, pivot: QgsPointXY) -> bool:
    """Rotate a text by ``clockwise_deg`` around ``pivot`` (layer CRS).

    The text itself always turns around its anchor, so the anchor is moved around the
    pivot by the same angle — together that rotates the whole text around the pivot.
    """
    item = layer.item(label_id)
    if not isinstance(item, QgsAnnotationPointTextItem):
        return False
    item.setAngle(_normalize_angle(item.angle() + clockwise_deg))
    anchor = QgsPointXY(item.point())
    moved = rotate_point(anchor, pivot, clockwise_deg)
    layer.applyEditV2(
        QgsAnnotationItemEditOperationTranslateItem(label_id, moved.x() - anchor.x(), moved.y() - anchor.y()),
        QgsAnnotationItemEditContext(),
    )
    _record_dimension_override(layer, label_id)
    _update_position_text(layer, label_id)
    _finish(layer)
    return True


def label_angle(layer: QgsAnnotationLayer, label_id: str) -> float:
    """Current text angle (degrees, clockwise) as set on the item."""
    item = layer.item(label_id)
    return item.angle() if isinstance(item, QgsAnnotationPointTextItem) else 0.0


def _normalize_angle(angle: float) -> float:
    """Angle in (-180, 180]."""
    angle = math.fmod(angle, 360.0)
    if angle > 180:
        angle -= 360
    elif angle <= -180:
        angle += 360
    return angle


def label_bounds(layer: QgsAnnotationLayer, label_id: str, context: QgsRenderContext) -> QgsRectangle | None:
    """Extent of the rendered text (layer CRS) for the map scale of ``context``."""
    item = layer.item(label_id)
    return item.boundingBox(context) if item is not None else None


def description_labels(layer: QgsAnnotationLayer) -> list[str]:
    """Ids of all texts that can be moved and scaled with the move tool.

    Descriptions, standalone texts and the measurement labels (radius / edge lengths) —
    manual changes to the latter are kept as overrides, see ``_record_dimension_override``.
    """
    return [item_id for item_id, item in layer.items().items() if isinstance(item, QgsAnnotationPointTextItem)]


def label_owner(layer: QgsAnnotationLayer, label_id: str) -> str | None:
    """Id of the point/line/polygon a description or measurement label belongs to (None: standalone text)."""
    for item_id, item_meta in _read_meta(layer).items():
        linked = item_meta.get("label_id") == label_id or label_id in item_meta.get("dim_ids", [])
        if linked and layer.item(item_id) is not None:
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


@_undoable("Beschriftung skalieren")
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
    _record_dimension_override(layer, label_id)
    _update_position_text(layer, label_id)
    _finish(layer)
    return True


@_undoable("Beschriftung verschieben")
def translate_label(layer: QgsAnnotationLayer, label_id: str, dx: float, dy: float) -> bool:
    """Shift a text item by ``dx``/``dy`` (layer CRS units)."""
    result = layer.applyEditV2(
        QgsAnnotationItemEditOperationTranslateItem(label_id, dx, dy), QgsAnnotationItemEditContext()
    )
    if result != Qgis.AnnotationItemEditOperationResult.Success:
        logger.warning("Beschreibung %s konnte nicht verschoben werden: %s", label_id, result)
        return False
    _record_dimension_override(layer, label_id)
    _update_position_text(layer, label_id)  # a moved standalone text keeps its $POS current
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


@_undoable("Stützpunkt einfügen")
def add_node(layer: QgsAnnotationLayer, item_id: str, point: QgsPointXY) -> bool:
    """Insert a vertex at ``point`` (layer CRS) into the nearest segment of a line/polygon."""
    result = layer.applyEditV2(
        QgsAnnotationItemEditOperationAddNode(item_id, QgsPoint(point)), QgsAnnotationItemEditContext()
    )
    if result != Qgis.AnnotationItemEditOperationResult.Success:
        logger.warning("Stützpunkt konnte nicht eingefügt werden (%s): %s", item_id, result)
        return False
    _update_dimensions(layer, item_id)
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


@_undoable("Stützpunkt entfernen")
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
    _update_dimensions(layer, handle.item_id)
    _finish(layer)
    return True


def centroids(layer: QgsAnnotationLayer) -> list[tuple[str, QgsPointXY]]:
    """(item_id, center of mass in layer CRS) of every line and polygon.

    Lines use the length-weighted center, polygons the area centroid (which can lie
    outside a strongly concave polygon).
    """
    result = []
    for item_id, geom in edge_geometries(layer):
        center = geom.centroid()
        if center is not None and not center.isEmpty():
            result.append((item_id, center.asPoint()))
    return result


@_undoable("Objekt verschieben")
def translate_item(layer: QgsAnnotationLayer, item_id: str, dx: float, dy: float) -> bool:
    """Shift a whole line/polygon by ``dx``/``dy`` (layer CRS units).

    Its description moves along; edge length labels are rebuilt.
    """
    if item_kind(layer, item_id) not in (KIND_LINE, KIND_POLYGON):
        return False
    context = QgsAnnotationItemEditContext()
    result = layer.applyEditV2(QgsAnnotationItemEditOperationTranslateItem(item_id, dx, dy), context)
    if result != Qgis.AnnotationItemEditOperationResult.Success:
        logger.warning("Verschieben der Annotation %s fehlgeschlagen: %s", item_id, result)
        return False
    label_id = _read_meta(layer).get(item_id, {}).get("label_id")
    if label_id and layer.item(label_id):
        layer.applyEditV2(QgsAnnotationItemEditOperationTranslateItem(label_id, dx, dy), context)
    _update_dimensions(layer, item_id)
    _finish(layer)
    return True


def min_vertices(kind: str) -> int:
    """Smallest vertex count an object of ``kind`` may have (0 for kinds without editable vertices)."""
    return {KIND_POINT: 1, KIND_TEXT: 1, KIND_LINE: MIN_LINE_VERTICES, KIND_POLYGON: MIN_POLYGON_VERTICES}.get(kind, 0)


def vertex_points(layer: QgsAnnotationLayer, item_id: str) -> list[QgsPointXY]:
    """Vertices of a point/line/polygon in layer CRS (polygon ring without the repeated closing vertex).

    A standalone text has one "vertex": its anchor.
    """
    kind = item_kind(layer, item_id)
    if kind == KIND_TEXT:
        position = _item_position(layer, item_id)
        return [position] if position is not None else []
    geom = entry_geometry(layer, item_id) if kind in (KIND_POINT, KIND_LINE, KIND_POLYGON) else None
    if geom is None or geom.isEmpty():
        return []
    if kind == KIND_POINT:
        return [geom.asPoint()]
    if kind == KIND_LINE:
        return list(geom.asPolyline())
    ring = list(geom.asPolygon()[0])
    return ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring


@_undoable("Koordinaten ändern")
def set_vertices(
    layer: QgsAnnotationLayer, item_id: str, points: list[QgsPointXY], mgrs_resolution_m: float = 1.0
) -> bool:
    """Replace the vertices of a point/line/polygon (layer CRS).

    Refuses (returns False, nothing changed) if fewer than ``min_vertices`` are given.
    Points are moved like with the move tool, so their description and ``$POS`` follow.
    """
    kind = item_kind(layer, item_id)
    if kind is None or len(points) < max(1, min_vertices(kind)):
        return False

    if kind in (KIND_POINT, KIND_TEXT):
        current = vertex_points(layer, item_id)
        if not current:
            return False
        if kind == KIND_TEXT:
            dx, dy = points[0].x() - current[0].x(), points[0].y() - current[0].y()
            return translate_label(layer, item_id, dx, dy)
        return move_node(layer, NodeHandle(item_id, QgsVertexId(0, 0, 0), current[0]), points[0], mgrs_resolution_m)

    # Replace the item by an edited copy, so the layer's spatial index is updated as well
    new_item = layer.item(item_id).clone()
    if kind == KIND_LINE:
        new_item.setGeometry(QgsLineString([QgsPoint(p) for p in points]))
    else:
        polygon = QgsPolygon()
        polygon.setExteriorRing(QgsLineString([QgsPoint(p) for p in [*points, points[0]]]))
        new_item.setGeometry(polygon)
    layer.replaceItem(item_id, new_item)
    _update_dimensions(layer, item_id)
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
    radius_m = None
    if kind == KIND_POINT:
        # The marker dot is the top-most layer; a radius circle (if any) sits below it
        symbol = item.symbol()
        dot = symbol.symbolLayer(symbol.symbolLayerCount() - 1) if symbol else None
        if dot is not None and hasattr(dot, "fillColor"):
            line_color = dot.fillColor()
        radius_m = item_meta.get("radius_m") or 0.0
        if symbol is not None and symbol.symbolLayerCount() > 1 and hasattr(symbol.symbolLayer(0), "fillColor"):
            fill_color = symbol.symbolLayer(0).fillColor()
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
    map_text = ""
    if kind == KIND_TEXT:
        line_color = item.format().color()
        name = _strip_indent(item.text())
        if item_meta.get("desc_template"):
            map_text, name = name, item_meta["desc_template"]

    if kind != KIND_TEXT:
        label = layer.item(item_meta.get("label_id", "")) if item_meta.get("label_id") else None
        if label is not None:
            name = _strip_indent(label.text())
            if item_meta.get("desc_template"):
                # Edit the template ($POS), list/show the resolved text
                map_text, name = name, item_meta["desc_template"]

    return AnnotationEntry(
        item_id,
        kind,
        item_meta["n"],
        name,
        line_color,
        fill_color,
        line_width,
        radius_m,
        bool(item_meta.get("show_dims")),
        map_text,
        item_meta.get("arrows", ARROWS_NONE) if kind == KIND_LINE else ARROWS_NONE,
        item_meta.get("arrow_size_mm", 0.0) if kind == KIND_LINE else 0.0,
        item_meta.get("dim_size_mm", DIMENSION_TEXT_SIZE_MM),
    )


def _set_description(
    layer,
    meta,
    item_id: str,
    kind: str,
    description: str,
    units_per_mm: float = 0.0,
    mgrs_resolution_m: float = 1.0,
) -> None:
    """Create, update or remove the description text linked to an item.

    For points, ``$POS`` is kept in ``desc_template`` and replaced on the map by
    the MGRS coordinate (``mgrs_resolution_m``), so it can be updated later.

    All descriptions use centered text. New point descriptions sit centered
    above the marker (offset by the marker radius plus a gap, converted with
    ``units_per_mm``); descriptions of lines and polygons are centered on the
    line's midpoint or inside the area.
    """
    description = description.strip()
    label_id = meta[item_id].get("label_id", "")
    label = layer.item(label_id) if label_id else None

    if not description:
        if label is not None:
            layer.removeItem(label_id)
        meta[item_id]["label_id"] = ""
        meta[item_id].pop("desc_template", None)
        return

    if kind == KIND_POINT and POSITION_PLACEHOLDER in description:
        meta[item_id]["desc_template"] = description
        description = _resolve_position(layer, item_id, description, mgrs_resolution_m)
    else:
        meta[item_id].pop("desc_template", None)

    if label is not None:
        label.setText(description)
        return

    anchor = _label_anchor(layer, item_id, kind)
    if anchor is None:
        return
    if kind == KIND_POINT:
        # The anchor is the baseline of the last line, so the text extends upwards from here
        offset = (MARKER_SIZE_MM / 2 + _POINT_LABEL_GAP_MM) * units_per_mm
        anchor = QgsPointXY(anchor.x(), anchor.y() + offset)
    text_item = QgsAnnotationPointTextItem(description, anchor)
    text_item.setFormat(_description_format())
    text_item.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    text_item.setZIndex(_Z_TEXT)
    meta[item_id]["label_id"] = layer.addItem(text_item)


def _resolve_position(layer, item_id: str, template: str, mgrs_resolution_m: float) -> str:
    """``template`` with ``$POS`` replaced by the MGRS coordinate of ``item_id``.

    The position is the point of a point marker, or the anchor of a standalone text.
    """
    position = _item_position(layer, item_id)
    mgrs = None
    if position is not None:
        try:
            mgrs = point_to_mgrs(position, layer.crs(), mgrs_resolution_m)
        except Exception:
            logger.exception("MGRS-Koordinate konnte nicht berechnet werden")
    return template.replace(POSITION_PLACEHOLDER, mgrs or _POSITION_UNAVAILABLE)


def _item_position(layer, item_id: str) -> QgsPointXY | None:
    item = layer.item(item_id)
    if isinstance(item, QgsAnnotationPointTextItem):
        return QgsPointXY(item.point())
    geom = entry_geometry(layer, item_id)
    return geom.asPoint() if geom is not None and not geom.isEmpty() else None


def _set_text_with_position(layer, meta, item_id: str, text: str, mgrs_resolution_m: float) -> None:
    """Set a standalone text, keeping a ``$POS`` template in the metadata."""
    if POSITION_PLACEHOLDER in text:
        meta[item_id]["desc_template"] = text
        text = _resolve_position(layer, item_id, text, mgrs_resolution_m)
    else:
        meta[item_id].pop("desc_template", None)
    layer.item(item_id).setText(text)


def _update_position_text(layer, item_id: str, mgrs_resolution_m: float | None = None) -> None:
    """Re-render a ``$POS`` text (after a move or resolution change).

    ``item_id`` is a point (its linked description is updated) or a standalone text
    (updated itself). No-op for objects without a ``$POS`` template.
    """
    if mgrs_resolution_m is None:
        mgrs_resolution_m = _mgrs_resolution()
    item_meta = _read_meta(layer).get(item_id, {})
    template = item_meta.get("desc_template")
    if not template:
        return
    item = layer.item(item_id)
    if isinstance(item, QgsAnnotationPointTextItem):
        target = item
    else:
        target = layer.item(item_meta.get("label_id", "")) if item_meta.get("label_id") else None
    if target is not None:
        target.setText(_resolve_position(layer, item_id, template, mgrs_resolution_m))


def refresh_positions(layer: QgsAnnotationLayer, mgrs_resolution_m: float) -> None:
    """Re-render all ``$POS`` descriptions, e.g. after the MGRS resolution setting changed."""
    templated = [item_id for item_id, m in _read_meta(layer).items() if m.get("desc_template")]
    for item_id in templated:
        _update_position_text(layer, item_id, mgrs_resolution_m)
    if templated:
        _finish(layer)


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


def _dimension_ids(meta: dict) -> set[str]:
    return {dim_id for m in meta.values() for dim_id in m.get("dim_ids", [])}


def _update_dimensions(layer, item_id: str) -> None:
    """Rebuild the measurement labels of ``item_id`` after its geometry changed (no-op if not shown)."""
    meta = _read_meta(layer)
    item_meta = meta.get(item_id)
    if item_meta and (item_meta.get("show_dims") or item_meta.get("dim_ids")):
        _refresh_dimensions(layer, meta, item_id)
        _write_meta(layer, meta)


def _refresh_dimensions(layer, meta: dict, item_id: str) -> None:
    """Recreate the measurement labels of an item according to its "show_dims" flag.

    Points get their radius above the circle, lines and polygons the length of
    every edge at its midpoint, rotated along the edge (always readable upright).
    """
    item_meta = meta.get(item_id)
    if item_meta is None:
        return
    for dim_id in item_meta.get("dim_ids", []):
        if layer.item(dim_id):
            layer.removeItem(dim_id)
    item_meta["dim_ids"] = []
    if not item_meta.get("show_dims"):
        return

    kind = item_kind(layer, item_id)
    specs = _dimension_specs(layer, item_id, item_meta)
    # Overrides are keyed by label index — after inserting/removing vertices they no longer match
    if item_meta.get("dim_count") != len(specs):
        item_meta["dim_overrides"] = {}
        item_meta["dim_count"] = len(specs)
    overrides = item_meta.get("dim_overrides", {})
    base_size = item_meta.get("dim_size_mm", DIMENSION_TEXT_SIZE_MM)

    for index, (text, anchor, angle) in enumerate(specs):
        override = overrides.get(str(index), {})
        anchor = QgsPointXY(anchor.x() + override.get("dx", 0.0), anchor.y() + override.get("dy", 0.0))
        text_item = QgsAnnotationPointTextItem(text, anchor)
        text_item.setFormat(_dimension_format(override.get("size", base_size)))
        text_item.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        text_item.setAngle(override.get("angle", angle))
        if kind != KIND_POINT:
            # The angle is computed in map coordinates; keep it aligned with the edge on a rotated map
            text_item.setRotationMode(Qgis.SymbolRotationMode.RespectMapRotation)
        text_item.setZIndex(_Z_TEXT)
        item_meta["dim_ids"].append(layer.addItem(text_item))


def _dimension_specs(layer, item_id: str, item_meta: dict) -> list[tuple[str, QgsPointXY, float]]:
    """(text, automatic anchor in layer CRS, angle) of every measurement label of an item."""
    kind = item_kind(layer, item_id)
    geom = entry_geometry(layer, item_id)
    if geom is None or geom.isEmpty():
        return []
    da = distance_area(layer.crs())

    labels: list[tuple[str, QgsPointXY, float]] = []
    if kind == KIND_POINT:
        radius = item_meta.get("radius_m") or 0.0
        center = geom.asPoint()
        units_per_meter = _layer_units_per_meter(da, center)
        if radius > 0 and units_per_meter > 0:
            top = QgsPointXY(center.x(), center.y() + radius * units_per_meter)
            labels.append((f"r = {format_meters(radius)}", top, 0.0))
    elif kind in (KIND_LINE, KIND_POLYGON):
        vertices = geom.asPolyline() if kind == KIND_LINE else geom.asPolygon()[0]
        for a, b in zip(vertices, vertices[1:]):
            length = da.convertLengthMeasurement(da.measureLine(a, b), Qgis.DistanceUnit.Meters)
            middle = QgsPointXY((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)
            labels.append((format_meters(length), middle, _upright_angle(a, b)))
    return labels


def _record_dimension_override(layer, label_id: str) -> None:
    """Remember a manual move/resize/rotation of a measurement label so it survives rebuilding the labels.

    The offset is stored relative to the automatic position (so the label follows its edge
    when vertices move); size and angle only if they differ from the automatic values.
    """
    meta = _read_meta(layer)
    owner = next((item_id for item_id, m in meta.items() if label_id in m.get("dim_ids", [])), None)
    item = layer.item(label_id)
    if owner is None or not isinstance(item, QgsAnnotationPointTextItem):
        return
    item_meta = meta[owner]
    index = item_meta["dim_ids"].index(label_id)
    specs = _dimension_specs(layer, owner, item_meta)
    if index >= len(specs):
        return

    anchor, auto_angle = specs[index][1], specs[index][2]
    point = QgsPointXY(item.point())
    override = {"dx": point.x() - anchor.x(), "dy": point.y() - anchor.y()}
    size = item.format().size()
    if abs(size - item_meta.get("dim_size_mm", DIMENSION_TEXT_SIZE_MM)) > 1e-6:
        override["size"] = size
    if abs(_normalize_angle(item.angle() - auto_angle)) > 1e-6:
        override["angle"] = item.angle()
    item_meta.setdefault("dim_overrides", {})[str(index)] = override
    item_meta["dim_count"] = len(specs)
    _write_meta(layer, meta)


def _layer_units_per_meter(da: QgsDistanceArea, point: QgsPointXY) -> float:
    """Local scale at ``point``: how many layer CRS units one meter on the ground spans (east-west)."""
    meters_per_unit = da.convertLengthMeasurement(
        da.measureLine(point, QgsPointXY(point.x() + 1, point.y())), Qgis.DistanceUnit.Meters
    )
    return 1 / meters_per_unit if meters_per_unit > 0 else 0.0


def _upright_angle(a: QgsPointXY, b: QgsPointXY) -> float:
    """Text angle along segment a→b (degrees, clockwise as point-text items expect), never upside down."""
    angle = math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x()))  # counter-clockwise from east
    if angle > 90:
        angle -= 180
    elif angle < -90:
        angle += 180
    return -angle


def _dimension_format(size_mm: float = DIMENSION_TEXT_SIZE_MM) -> QgsTextFormat:
    fmt = QgsTextFormat()
    fmt.setSize(size_mm)
    fmt.setSizeUnit(Qgis.RenderUnit.Millimeters)
    fmt.setColor(QColor(30, 30, 30))
    buffer = fmt.buffer()
    buffer.setEnabled(True)
    buffer.setSize(0.6 * size_mm / DIMENSION_TEXT_SIZE_MM)  # halo grows with the text
    buffer.setSizeUnit(Qgis.RenderUnit.Millimeters)
    buffer.setColor(QColor(255, 255, 255))
    fmt.setBuffer(buffer)
    return fmt


def _strip_indent(text: str) -> str:
    return text[len(_LEGACY_INDENT) :] if text.startswith(_LEGACY_INDENT) else text


def _read_meta(layer) -> dict:
    raw = layer.customProperty(META_PROPERTY, "")
    try:
        meta = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        logger.warning("Ungültige Annotations-Metadaten, werden zurückgesetzt")
        meta = {}
    return meta if isinstance(meta, dict) else {}


def _write_meta(layer, meta: dict) -> None:
    layer.setCustomProperty(META_PROPERTY, json.dumps(meta))


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


def default_radius_fill(color: QColor) -> QColor:
    """Fill of a point's radius circle unless chosen explicitly: the point color, mostly transparent."""
    fill = QColor(color)
    fill.setAlphaF(color.alphaF() * _RADIUS_FILL_OPACITY)
    return fill


def _marker_symbol(color: QColor, radius_m: float = 0.0, fill_color: QColor | None = None) -> QgsMarkerSymbol:
    """Marker dot in ``color``; with ``radius_m`` > 0 plus a true-to-scale circle of that radius below it.

    The circle is outlined in ``color`` and filled with ``fill_color`` (default: ``default_radius_fill``).
    """
    symbol = QgsMarkerSymbol.createSimple(
        {
            "name": "circle",
            "size": str(MARKER_SIZE_MM),
            "outline_color": "255,255,255,255",
            "outline_width": "0.4",
        }
    )
    symbol.symbolLayer(0).setFillColor(color)

    if radius_m > 0:
        circle = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Circle, 2 * radius_m)
        # Meters on the ground, independent of the layer CRS and the map scale
        circle.setSizeUnit(Qgis.RenderUnit.MetersInMapUnits)
        circle.setFillColor(fill_color if fill_color is not None else default_radius_fill(color))
        circle.setStrokeColor(color)
        circle.setStrokeWidth(DEFAULT_LINE_WIDTH_MM)
        circle.setStrokeWidthUnit(Qgis.RenderUnit.Millimeters)
        symbol.insertSymbolLayer(0, circle)
    return symbol


def arrow_size_for(width_mm: float, arrow_size_mm: float = 0.0) -> float:
    """Effective arrowhead size (mm): the chosen size, or automatic from the line width."""
    return arrow_size_mm if arrow_size_mm > 0 else max(3.0, width_mm * 4)


def _line_symbol(
    color: QColor, width_mm: float, arrows: str = ARROWS_NONE, arrow_size_mm: float = 0.0
) -> QgsLineSymbol:
    """Line in ``color``/``width_mm``, optionally with arrowheads (ARROWS_*) at its ends.

    The filled arrowhead is a triangle with its tip on the end vertex, as long as half its
    size and as wide as its size. Without trimming, the line (and its round cap) would
    run through the arrowhead up to the tip and poke out there; so the line is trimmed
    under each arrowhead by one line width — far enough for the triangle to cover the
    rounded line end completely.
    """
    symbol = QgsLineSymbol.createSimple({"line_width": str(width_mm), "capstyle": "round", "joinstyle": "round"})
    line_layer = symbol.symbolLayer(0)
    line_layer.setColor(color)
    if arrows == ARROWS_NONE or arrows not in ARROW_MODES:
        return symbol

    size = arrow_size_for(width_mm, arrow_size_mm)
    trim = min(width_mm, 0.9 * size / 2)  # stay inside the arrowhead even for very small arrows
    if arrows in (ARROWS_END, ARROWS_BOTH):
        line_layer.setTrimDistanceEnd(trim)
        line_layer.setTrimDistanceEndUnit(Qgis.RenderUnit.Millimeters)
        symbol.appendSymbolLayer(_arrowhead_layer(color, size, Qgis.MarkerLinePlacement.LastVertex, 0))
    if arrows in (ARROWS_START, ARROWS_BOTH):
        line_layer.setTrimDistanceStart(trim)
        line_layer.setTrimDistanceStartUnit(Qgis.RenderUnit.Millimeters)
        # Markers are rotated along the line direction — turn the start arrow around
        symbol.appendSymbolLayer(_arrowhead_layer(color, size, Qgis.MarkerLinePlacement.FirstVertex, 180))
    return symbol


def _arrowhead_layer(color: QColor, size_mm: float, placement, angle: float) -> QgsMarkerLineSymbolLayer:
    """Filled arrowhead on the first/last vertex; its tip sits exactly on the vertex."""
    head = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.ArrowHeadFilled, size_mm)
    head.setColor(color)
    head.setStrokeStyle(Qt.PenStyle.NoPen)
    head.setAngle(angle)
    marker_line = QgsMarkerLineSymbolLayer(True)  # rotate the marker along the line
    marker_line.setPlacements(placement)
    marker_line.setSubSymbol(QgsMarkerSymbol([head]))
    return marker_line


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
