import os

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsMarkerSymbol,
    QgsMarkerSymbolLayer,
    QgsRendererCategory,
    QgsSimpleMarkerSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsSvgMarkerSymbolLayer,
    QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor

from ..util.temp_files import create_temp_svg_from_content

# 0=left/top, 1=center, 2=right/bottom — matches OriginPointWidget convention
_H_ANCHOR = {
    0: QgsMarkerSymbolLayer.HorizontalAnchorPoint.Left,
    1: QgsMarkerSymbolLayer.HorizontalAnchorPoint.HCenter,
    2: QgsMarkerSymbolLayer.HorizontalAnchorPoint.Right,
}
_V_ANCHOR = {
    0: QgsMarkerSymbolLayer.VerticalAnchorPoint.Top,
    1: QgsMarkerSymbolLayer.VerticalAnchorPoint.VCenter,
    2: QgsMarkerSymbolLayer.VerticalAnchorPoint.Bottom,
}
# White-background circle is rendered slightly larger than the SVG itself
_BG_SCALE = 1.2


def apply_renderer(layer: QgsVectorLayer, plugin_dir: str) -> None:
    """Build a per-feature categorized renderer and apply it to `layer`.

    Each feature gets its own category keyed by `unique_id`, with a symbol
    that combines (optional) white background circle + SVG marker, sized
    and rotated per feature attributes.

    SVG resolution priority: inline `svg_content` → absolute `svg_path` →
    relative path joined with `plugin_dir`.

    Falls back to a default marker if the layer has no features.
    """
    if not layer:
        return

    field_names = [field.name() for field in layer.fields()]
    has_svg_content = "svg_content" in field_names
    has_white_bg = "white_background" in field_names
    has_rotation = "rotation" in field_names
    has_origin_x = "origin_x" in field_names
    has_origin_y = "origin_y" in field_names

    categories = []
    for feat in layer.getFeatures():
        sym = _build_feature_symbol(
            feat,
            plugin_dir,
            svg_content=feat.attribute("svg_content") if has_svg_content else "",
            white_background=feat.attribute("white_background") if has_white_bg else False,
            rotation=feat.attribute("rotation") if has_rotation else 0.0,
            origin_x=_origin_or_default(feat, "origin_x", has_origin_x),
            origin_y=_origin_or_default(feat, "origin_y", has_origin_y),
        )

        unique_id = category_value(feat)
        svg_path = feat.attribute("svg_path")
        feature_name = feat.attribute("name") or os.path.basename(svg_path)
        display_name = os.path.splitext(feature_name)[0]
        categories.append(QgsRendererCategory(unique_id, sym, display_name))

    if categories:
        layer.setRenderer(QgsCategorizedSymbolRenderer("unique_id", categories))
    else:
        layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({})))

    layer.triggerRepaint()


def category_value(feat) -> str:
    """The renderer category key of a feature."""
    if feat.fields().indexFromName("unique_id") >= 0:
        return feat.attribute("unique_id")
    return str(feat.id())


def feature_symbol(layer: QgsVectorLayer, unique_id: str) -> QgsMarkerSymbol | None:
    """Return a copy of the symbol the renderer currently draws for `unique_id`."""
    renderer = layer.renderer()
    if not isinstance(renderer, QgsCategorizedSymbolRenderer):
        return None
    idx = renderer.categoryIndexForValue(unique_id)
    if idx < 0:
        return None
    # Keep the list alive: each category owns its symbol.
    categories = renderer.categories()
    return categories[idx].symbol().clone()


def replace_feature_symbol(layer: QgsVectorLayer, unique_id: str, symbol: QgsMarkerSymbol) -> None:
    """Swap one feature's symbol in place, without rebuilding the whole renderer.

    Cheap enough for live feedback while dragging; the attribute write on
    release goes through the regular `apply_renderer` rebuild.
    """
    renderer = layer.renderer()
    if not isinstance(renderer, QgsCategorizedSymbolRenderer):
        return
    idx = renderer.categoryIndexForValue(unique_id)
    if idx < 0:
        return
    renderer.updateCategorySymbol(idx, symbol.clone())
    layer.triggerRepaint()


def svg_symbol_layer(symbol: QgsMarkerSymbol) -> QgsSvgMarkerSymbolLayer | None:
    for symbol_layer in symbol.symbolLayers():
        if symbol_layer.layerType() == "SvgMarker":
            return symbol_layer
    return None


def set_symbol_size_and_rotation(symbol: QgsMarkerSymbol, size: float, rotation: float) -> None:
    """Apply `size`/`rotation` the same way `_build_feature_symbol` does."""
    for symbol_layer in symbol.symbolLayers():
        if symbol_layer.layerType() == "SvgMarker":
            symbol_layer.setSize(size)
            symbol_layer.setAngle(rotation)
        else:
            symbol_layer.setSize(size * _BG_SCALE)


def _origin_or_default(feat, field_name: str, present: bool) -> int:
    """Return origin attribute as int (default 1=center). Handles missing/NULL."""
    if not present:
        return 1
    value = feat.attribute(field_name)
    return 1 if value is None else value


def _resolve_svg_layer(
    plugin_dir: str, svg_path: str, svg_content: str, feat_id, size: float
) -> QgsSvgMarkerSymbolLayer:
    """Materialize an SVG marker layer, preferring inline content over file paths."""
    if svg_content and svg_content.strip():
        temp_svg = create_temp_svg_from_content(plugin_dir, svg_content, feat_id)
        if temp_svg:
            return QgsSvgMarkerSymbolLayer(temp_svg, size, 0)
        # Fallback to stored path
        return QgsSvgMarkerSymbolLayer(svg_path, size, 0)

    if not os.path.isabs(svg_path):
        absolute_path = os.path.join(plugin_dir, svg_path)
        if os.path.exists(absolute_path):
            return QgsSvgMarkerSymbolLayer(absolute_path, size, 0)
    return QgsSvgMarkerSymbolLayer(svg_path, size, 0)


def _build_feature_symbol(
    feat,
    plugin_dir: str,
    svg_content: str,
    white_background: bool,
    rotation: float,
    origin_x: int,
    origin_y: int,
) -> QgsMarkerSymbol:
    """Compose the QgsMarkerSymbol for one feature: optional bg circle + SVG."""
    svg_path = feat.attribute("svg_path")
    size = feat.attribute("size")
    scale_with_map = feat.attribute("scale_with_map")

    sym = QgsMarkerSymbol.createSimple({})

    bg_layer = None
    if white_background:
        bg_layer = QgsSimpleMarkerSymbolLayer(QgsSimpleMarkerSymbolLayer.Shape.Circle, size * _BG_SCALE, 0)
        bg_layer.setColor(QColor(255, 255, 255))
        bg_layer.setStrokeColor(QColor(255, 255, 255))
        bg_layer.setStrokeWidth(0)
        if not scale_with_map:
            bg_layer.setSizeUnit(QgsUnitTypes.RenderUnit.RenderMapUnits)
        sym.changeSymbolLayer(0, bg_layer)

    svg_layer = _resolve_svg_layer(plugin_dir, svg_path, svg_content, feat.id(), size)
    if not scale_with_map:
        svg_layer.setSizeUnit(QgsUnitTypes.RenderUnit.RenderMapUnits)
    svg_layer.setAngle(rotation)

    h = _H_ANCHOR.get(origin_x, QgsMarkerSymbolLayer.HorizontalAnchorPoint.HCenter)
    v = _V_ANCHOR.get(origin_y, QgsMarkerSymbolLayer.VerticalAnchorPoint.VCenter)
    svg_layer.setHorizontalAnchorPoint(h)
    svg_layer.setVerticalAnchorPoint(v)
    if bg_layer is not None:
        bg_layer.setHorizontalAnchorPoint(h)
        bg_layer.setVerticalAnchorPoint(v)
        sym.appendSymbolLayer(svg_layer)
    else:
        sym.changeSymbolLayer(0, svg_layer)

    return sym
