import os

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsMapUnitScale,
    QgsMarkerSymbol,
    QgsMarkerSymbolLayer,
    QgsRendererCategory,
    QgsSimpleMarkerSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsSvgMarkerSymbolLayer,
    QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QPointF, Qt
from qgis.PyQt.QtGui import QColor

from ..util.temp_files import create_temp_svg_from_content
from .background import DEFAULT_SCALE, Background, background_of

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
_SHAPE = QgsSimpleMarkerSymbolLayer.Shape
_BG_SHAPES = {
    "circle": _SHAPE.Circle,
    "square": _SHAPE.Square,
    "rounded": getattr(_SHAPE, "RoundedSquare", _SHAPE.Square),
    "diamond": _SHAPE.Diamond,
}
# Rand des Hintergrunds (Millimeter)
_BG_STROKE_MM = 0.4
# Richtung, in die der Hintergrund rückt, damit er trotz Ankerpunkt am Rand mittig hinter dem Zeichen liegt
_H_SHIFT = {
    QgsMarkerSymbolLayer.HorizontalAnchorPoint.Left: -1,
    QgsMarkerSymbolLayer.HorizontalAnchorPoint.Right: 1,
}
_V_SHIFT = {
    QgsMarkerSymbolLayer.VerticalAnchorPoint.Top: -1,
    QgsMarkerSymbolLayer.VerticalAnchorPoint.Bottom: 1,
}


def size_unit(scale_with_map) -> QgsUnitTypes.RenderUnit:
    """Render unit of a feature's `size`, derived from its `scale_with_map` attribute.

    The stored flag is historically inverted and kept that way so existing
    GeoPackages render unchanged: True = millimetres (constant size on screen),
    False = map units (the symbol grows and shrinks with the map).
    """
    return QgsUnitTypes.RenderUnit.RenderMillimeters if scale_with_map else QgsUnitTypes.RenderUnit.RenderMapUnits


def scale_limits(feat) -> QgsMapUnitScale:
    """Wie weit ein mit der Karte skaliertes Zeichen mitwächst bzw. mitschrumpft.

    Über den Maßstab ``max_scale`` hinaus reingezoomt wird es nicht mehr größer, über
    ``min_scale`` hinaus rausgezoomt nicht mehr kleiner (Maßstabszahlen, leer = unbegrenzt).
    Wirkt nur bei Größen in Karteneinheiten; Hintergrund und Versatz frieren beim selben Maßstab ein.
    """
    return QgsMapUnitScale(_scale_attr(feat, "min_scale"), _scale_attr(feat, "max_scale"))


def _scale_attr(feat, name: str) -> float:
    idx = feat.fields().indexFromName(name)
    value = feat.attribute(idx) if idx >= 0 else None
    if value is None or (hasattr(value, "isNull") and value.isNull()):
        return 0.0
    return max(0.0, float(value))


def apply_renderer(layer: QgsVectorLayer, plugin_dir: str) -> None:
    """Build a per-feature categorized renderer and apply it to `layer`.

    Each feature gets its own category keyed by `unique_id`, with a symbol
    that combines an optional background shape (layer.background) + SVG
    marker, sized and rotated per feature attributes.

    SVG resolution priority: inline `svg_content` → absolute `svg_path` →
    relative path joined with `plugin_dir`.

    Falls back to a default marker if the layer has no features.
    """
    if not layer:
        return

    field_names = [field.name() for field in layer.fields()]
    has_svg_content = "svg_content" in field_names
    has_rotation = "rotation" in field_names
    has_origin_x = "origin_x" in field_names
    has_origin_y = "origin_y" in field_names

    categories = []
    for feat in layer.getFeatures():
        sym = _build_feature_symbol(
            feat,
            plugin_dir,
            svg_content=feat.attribute("svg_content") if has_svg_content else "",
            background=background_of(feat),
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
    """Apply `size`/`rotation` the same way `_build_feature_symbol` does.

    The background keeps its size ratio to the SVG (layer.background scale).
    """
    svg = svg_symbol_layer(symbol)
    old_size = svg.size() if svg is not None else 0.0
    for symbol_layer in symbol.symbolLayers():
        if symbol_layer.layerType() == "SvgMarker":
            symbol_layer.setSize(size)
        else:
            ratio = symbol_layer.size() / old_size if old_size > 0 else DEFAULT_SCALE
            symbol_layer.setSize(size * ratio)
            center_background(symbol_layer, size)
        symbol_layer.setAngle(rotation)


def center_background(bg_layer: QgsMarkerSymbolLayer, svg_size: float) -> None:
    """Hintergrund mittig hinter das Zeichen rücken, auch wenn der Ankerpunkt am Rand liegt.

    Beide Ebenen hängen am selben Ankerpunkt; der größere Hintergrund würde sonst
    über eine Seite hinausragen. QGIS dreht den Versatz mit dem Zeichen mit.
    """
    extra = (bg_layer.size() - svg_size) / 2
    bg_layer.setOffset(
        QPointF(
            _H_SHIFT.get(bg_layer.horizontalAnchorPoint(), 0) * extra,
            _V_SHIFT.get(bg_layer.verticalAnchorPoint(), 0) * extra,
        )
    )


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
    background: Background,
    rotation: float,
    origin_x: int,
    origin_y: int,
) -> QgsMarkerSymbol:
    """Compose the QgsMarkerSymbol for one feature: optional background shape + SVG."""
    svg_path = feat.attribute("svg_path")
    size = feat.attribute("size")
    scale_with_map = feat.attribute("scale_with_map")

    sym = QgsMarkerSymbol.createSimple({})

    bg_layer = None
    if background.visible:
        bg_layer = _background_layer(background, size, size_unit(scale_with_map))
        sym.changeSymbolLayer(0, bg_layer)

    svg_layer = _resolve_svg_layer(plugin_dir, svg_path, svg_content, feat.id(), size)
    svg_layer.setSizeUnit(size_unit(scale_with_map))
    svg_layer.setAngle(rotation)
    limits = scale_limits(feat)
    svg_layer.setSizeMapUnitScale(limits)
    if bg_layer is not None:
        bg_layer.setSizeMapUnitScale(limits)
        bg_layer.setOffsetMapUnitScale(limits)

    h = _H_ANCHOR.get(origin_x, QgsMarkerSymbolLayer.HorizontalAnchorPoint.HCenter)
    v = _V_ANCHOR.get(origin_y, QgsMarkerSymbolLayer.VerticalAnchorPoint.VCenter)
    svg_layer.setHorizontalAnchorPoint(h)
    svg_layer.setVerticalAnchorPoint(v)
    if bg_layer is not None:
        bg_layer.setHorizontalAnchorPoint(h)
        bg_layer.setVerticalAnchorPoint(v)
        # Eckige Hintergründe drehen mit dem Zeichen
        bg_layer.setAngle(rotation)
        center_background(bg_layer, size)
        sym.appendSymbolLayer(svg_layer)
    else:
        sym.changeSymbolLayer(0, svg_layer)

    return sym


def _background_layer(background: Background, size: float, unit) -> QgsSimpleMarkerSymbolLayer:
    shape = _BG_SHAPES.get(background.shape, _SHAPE.Circle)
    layer = QgsSimpleMarkerSymbolLayer(shape, size * background.scale, 0)
    fill = QColor(background.color)
    fill.setAlphaF(background.opacity)
    layer.setColor(fill)
    if background.stroke:
        layer.setStrokeColor(QColor(background.stroke))
        layer.setStrokeWidth(_BG_STROKE_MM)
        layer.setStrokeWidthUnit(QgsUnitTypes.RenderUnit.RenderMillimeters)
    else:
        layer.setStrokeStyle(Qt.PenStyle.NoPen)
    layer.setSizeUnit(unit)
    layer.setOffsetUnit(unit)
    return layer
