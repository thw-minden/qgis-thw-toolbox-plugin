import math

from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsPointXY,
    QgsProject,
    QgsRenderContext,
    QgsVectorLayer,
)

from ..layer.renderer import size_unit

# Default symbol size when a feature has no `size` attribute.
_FALLBACK_SIZE = 30.0
# Symbols smaller than this on screen still get a clickable box of this size (px).
_MIN_HIT_PX = 16.0
# Extra margin around the symbol box (px) so edge clicks still hit.
_HIT_PADDING_PX = 4.0


def _attr(feature: QgsFeature, names: list[str], name: str, default):
    if name not in names:
        return default
    value = feature[name]
    return default if value is None else value


def _symbol_size_px(feature: QgsFeature, names: list[str], context: QgsRenderContext) -> float:
    """On-screen symbol size in pixels — mirrors the size unit logic in layer/renderer.py."""
    size = float(_attr(feature, names, "size", _FALLBACK_SIZE) or _FALLBACK_SIZE)
    return context.convertToPainterUnits(size, size_unit(_attr(feature, names, "scale_with_map", False)))


def _hit_distance(dx: float, dy: float, feature: QgsFeature, names: list[str], size_px: float) -> float | None:
    """Distance from the click to the symbol centre (px), or None if the click misses the symbol.

    (dx, dy) is the click offset from the feature point in screen pixels (y down).
    The symbol box is shifted by its anchor point and rotated around it like QGIS draws it.
    """
    half = max(size_px, _MIN_HIT_PX) / 2.0
    origin_x = int(_attr(feature, names, "origin_x", 1))
    origin_y = int(_attr(feature, names, "origin_y", 1))
    rotation = math.radians(float(_attr(feature, names, "rotation", 0.0)))

    # Klick in das unrotierte Symbol-Koordinatensystem zurückdrehen
    cos_r, sin_r = math.cos(rotation), math.sin(rotation)
    local_x = dx * cos_r + dy * sin_r
    local_y = -dx * sin_r + dy * cos_r

    # Anker links/oben → Symbol liegt rechts/unterhalb des Punkts
    center_x = (1 - origin_x) * size_px / 2.0
    center_y = (1 - origin_y) * size_px / 2.0
    off_x = local_x - center_x
    off_y = local_y - center_y
    if abs(off_x) > half + _HIT_PADDING_PX or abs(off_y) > half + _HIT_PADDING_PX:
        return None
    return math.hypot(off_x, off_y)


def find_nearest_feature(layer: QgsVectorLayer, canvas, point: QgsPointXY) -> QgsFeature | None:
    """Return the feature whose drawn symbol lies under `point` (canvas CRS).

    Used by IdentifyTool (click), MoveTool (hover), and MoveTool (press)
    so all three behave consistently. The hit test runs in screen pixels,
    so it works at every zoom level, for map-unit and millimetre sizes,
    with any anchor point and rotation, and when the layer CRS differs
    from the project CRS. If several symbols overlap, the one whose
    centre is closest to the click wins.
    """
    map_settings = canvas.mapSettings()
    context = QgsRenderContext.fromMapSettings(map_settings)
    to_pixel = canvas.getCoordinateTransform()
    click_px = to_pixel.transform(point)

    layer_to_map = None
    if layer.crs() != map_settings.destinationCrs():
        layer_to_map = QgsCoordinateTransform(layer.crs(), map_settings.destinationCrs(), QgsProject.instance())

    request = QgsFeatureRequest()
    request.setFilterRect(map_settings.mapToLayerCoordinates(layer, canvas.extent()))

    names = layer.fields().names()
    closest: QgsFeature | None = None
    min_distance = float("inf")

    for feature in layer.getFeatures(request):
        geom = feature.geometry()
        if not geom or geom.isEmpty():
            continue
        feat_point = geom.asPoint()
        if layer_to_map is not None:
            try:
                feat_point = layer_to_map.transform(feat_point)
            except Exception:
                continue
        feat_px = to_pixel.transform(feat_point)
        size_px = _symbol_size_px(feature, names, context)
        distance = _hit_distance(click_px.x() - feat_px.x(), click_px.y() - feat_px.y(), feature, names, size_px)
        if distance is not None and distance < min_distance:
            min_distance = distance
            closest = feature

    return closest
