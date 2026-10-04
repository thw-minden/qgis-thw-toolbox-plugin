import os
import uuid
from collections.abc import Callable

from qgis.core import (
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsRenderContext,
    QgsUnitTypes,
    QgsVectorLayer,
)

from .fields import field_types_dict

# Default size used when adaptive sizing has no zoom info
_BASE_SIZE = 30.0
# Bounds for the adaptive zoom-aware initial size
_ADAPTIVE_MIN = 50.0
_ADAPTIVE_MAX = 500.0
# Smallest size a unit conversion may produce (matches the dock's spinbox minimum)
_MIN_SIZE = 1.0


class FeatureOperations:
    """All single- and multi-attribute mutations on the toolbox layer.

    The layer is fetched lazily via `layer_provider` because the plugin
    can swap the layer at project-save time. After every successful
    write, one of the dirty callbacks fires so the renderer or labeling
    can refresh.

    Attribute writes go through `_update_attribute`, which encapsulates
    the QGIS startEditing → changeAttributeValue → commitChanges dance.
    """

    def __init__(
        self,
        layer_provider: Callable[[], QgsVectorLayer | None],
        settings,
        plugin_dir: str,
        canvas,
        error_alert: Callable[[str, str, str | None], None],
        on_renderer_dirty: Callable[[], None],
        on_labeling_dirty: Callable[[], None],
    ):
        self._layer_provider = layer_provider
        self._settings = settings
        self._plugin_dir = plugin_dir
        self._canvas = canvas
        self._error_alert = error_alert
        self._on_renderer_dirty = on_renderer_dirty
        self._on_labeling_dirty = on_labeling_dirty
        # (size, scale_with_map) of the symbol the user last resized or rescaled —
        # newly placed symbols copy it so they come out the same size.
        self._last_size_style: tuple[float, bool] | None = None

    @property
    def layer(self) -> QgsVectorLayer | None:
        return self._layer_provider()

    def forget_last_size(self) -> None:
        """New symbols fall back to the configured default size again."""
        self._last_size_style = None

    # ------------------------------------------------------------------
    # Single-attribute setters
    # ------------------------------------------------------------------

    def resize(self, fid, size) -> None:
        self._update_attribute(fid, "size", size, dirty="renderer")
        layer = self.layer
        feat = layer.getFeature(fid) if layer else None
        if feat is not None and feat.isValid():
            self._last_size_style = (float(size), bool(feat.attribute("scale_with_map")))

    def set_scales_with_map(self, fid, scales: bool) -> float | None:
        """Switch between map-unit size (`scales`) and fixed screen size.

        The size is converted at the current zoom so the symbol keeps its
        on-screen size instead of jumping (e.g. 50 m → 50 mm). Returns the
        new size, or None if the feature could not be updated.
        """
        layer = self.layer
        feat = layer.getFeature(fid) if layer else None
        if feat is None or not feat.isValid():
            return None

        # Stored flag is inverted: True = fixed screen size (see renderer.size_unit)
        was_fixed = bool(feat.attribute("scale_with_map"))
        fixed = not scales
        size = float(feat.attribute("size") or _BASE_SIZE)
        if was_fixed != fixed:
            context = QgsRenderContext.fromMapSettings(self._canvas.mapSettings())
            mm = QgsUnitTypes.RenderUnit.RenderMillimeters
            size = context.convertFromMapUnits(size, mm) if fixed else context.convertToMapUnits(size, mm)
            size = max(_MIN_SIZE, round(size, 1))

        self._update_attributes(fid, {"scale_with_map": fixed, "size": size}, dirty="renderer")
        self._last_size_style = (size, fixed)
        return size

    def toggle_white_background(self, fid, value) -> None:
        self._update_attribute(fid, "white_background", value, dirty="renderer")

    def rotate(self, fid, degrees) -> None:
        self._update_attribute(fid, "rotation", degrees, dirty="renderer")

    def set_label(self, fid, text) -> None:
        self._update_attribute(fid, "label", text, dirty="labeling")

    def toggle_label(self, fid, value) -> None:
        self._update_attribute(fid, "show_label", value, dirty="labeling")

    def update_origin(self, fid, origin_x, origin_y) -> None:
        """Set both origin_x and origin_y in one edit transaction."""
        layer = self.layer
        if not layer:
            return
        idx_x = layer.fields().indexFromName("origin_x")
        idx_y = layer.fields().indexFromName("origin_y")
        if idx_x < 0 or idx_y < 0:
            return
        if not layer.isEditable():
            layer.startEditing()
        layer.changeAttributeValue(fid, idx_x, origin_x)
        layer.changeAttributeValue(fid, idx_y, origin_y)
        layer.commitChanges()
        layer.triggerRepaint()
        self._on_renderer_dirty()

    def change_symbol(self, fid, svg_path: str) -> bool:
        """Swap the feature's SVG (name, path, embedded content); label and placement stay."""
        layer = self.layer
        if not layer:
            return False
        svg_content = self._read_svg(svg_path)
        if svg_content is None:
            return False
        values = {
            "name": os.path.basename(svg_path),
            "svg_path": self._relative_to_plugin(svg_path),
            "svg_content": svg_content,
        }
        if not layer.isEditable():
            layer.startEditing()
        for field_name, value in values.items():
            idx = layer.fields().indexFromName(field_name)
            if idx >= 0:
                layer.changeAttributeValue(fid, idx, value)
        layer.commitChanges()
        layer.triggerRepaint()
        self._on_renderer_dirty()
        return True

    def move(self, fid, point: QgsPointXY) -> bool:
        """Set the feature's point (layer CRS) — this is where its anchor point sits."""
        layer = self.layer
        if not layer:
            return False
        if not layer.isEditable():
            layer.startEditing()
        moved = layer.changeGeometry(fid, QgsGeometry.fromPointXY(point))
        layer.commitChanges()
        layer.triggerRepaint()
        return moved

    def _update_attribute(self, fid, field_name: str, value, dirty: str) -> None:
        """Generic single-attribute write with renderer or labeling refresh."""
        self._update_attributes(fid, {field_name: value}, dirty)

    def _update_attributes(self, fid, values: dict, dirty: str) -> None:
        """Write several attributes in one edit transaction, then refresh."""
        layer = self.layer
        if not layer:
            return
        indexed = [(layer.fields().indexFromName(name), value) for name, value in values.items()]
        indexed = [(idx, value) for idx, value in indexed if idx >= 0]
        if not indexed:
            return
        if not layer.isEditable():
            layer.startEditing()
        for idx, value in indexed:
            layer.changeAttributeValue(fid, idx, value)
        layer.commitChanges()
        layer.triggerRepaint()
        if dirty == "renderer":
            self._on_renderer_dirty()
        elif dirty == "labeling":
            self._on_labeling_dirty()

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete(self, fid) -> bool:
        """Delete the feature from the layer. Returns True on success."""
        layer = self.layer
        if not layer:
            return False
        # Only the ogr (GeoPackage) path is supported — LayerManager always
        # creates GeoPackages, so the legacy memory-layer fallback was unreachable.
        if layer.providerType() != "ogr":
            return False
        layer.startEditing()
        layer.deleteFeature(fid)
        layer.commitChanges()
        self._on_renderer_dirty()
        return True

    # ------------------------------------------------------------------
    # Duplicate
    # ------------------------------------------------------------------

    def duplicate(self, fids, geometries=None) -> list:
        """Copy the features `fids`; returns the fids of the copies.

        `geometries` ({fid: QgsGeometry}, layer CRS) places a copy somewhere
        else than its original. Every copy gets its own `unique_id`, since the
        renderer keeps one symbol per id.
        """
        layer = self.layer
        if not layer or not fids:
            return []
        geometries = geometries or {}
        fid_idx = layer.fields().indexOf("fid")
        copies = []
        for feat in layer.getFeatures(QgsFeatureRequest().setFilterFids(list(fids))):
            copy = QgsFeature(feat)
            copy.setId(-1)
            if fid_idx >= 0:
                copy.setAttribute(fid_idx, None)
            copy.setAttribute("unique_id", str(uuid.uuid4()))
            if feat.id() in geometries:
                copy.setGeometry(geometries[feat.id()])
            copies.append(copy)
        if not copies:
            return []
        if layer.isEditable():
            layer.commitChanges()
        before = set(layer.allFeatureIds())
        layer.dataProvider().addFeatures(copies)
        layer.updateExtents()
        self._on_renderer_dirty()
        return sorted(set(layer.allFeatureIds()) - before)

    # ------------------------------------------------------------------
    # Place
    # ------------------------------------------------------------------

    def place_feature(self, svg_path: str, point: QgsPointXY) -> QgsFeature | None:
        """Insert a new feature at `point` from an SVG file.

        Auto-migrates the layer schema if newer fields are missing,
        embeds the SVG content into `svg_content`, computes an adaptive
        initial size based on the current zoom, and returns the inserted
        feature (refreshed from the layer so it has the persisted fid).

        Returns None if the SVG is unreadable or the layer is not ready.
        Caller is responsible for any UI follow-up (selection, dock).
        """
        layer = self.layer
        if not layer:
            return None

        self._ensure_schema(layer)

        svg_content = self._read_svg(svg_path)
        if svg_content is None:
            return None

        relative_path = self._relative_to_plugin(svg_path)
        if self._last_size_style is not None:
            icon_size, scale_with_map = self._last_size_style
        else:
            icon_size, scale_with_map = self._initial_size(layer), self._settings.new_icon_scaling_with_map
        default_label = os.path.splitext(os.path.basename(svg_path))[0].replace("_", " ")

        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromPointXY(point))
        f.setAttribute("name", os.path.basename(svg_path))
        f.setAttribute("svg_path", relative_path)
        f.setAttribute("svg_content", svg_content)
        f.setAttribute("size", icon_size)
        f.setAttribute("scale_with_map", scale_with_map)
        f.setAttribute("unique_id", str(uuid.uuid4()))
        f.setAttribute("label", default_label)
        f.setAttribute("show_label", False)
        f.setAttribute("white_background", False)
        f.setAttribute("rotation", 0.0)
        origin_x, origin_y = 1, 1
        if os.path.basename(svg_path).startswith("Schadensstelle"):
            origin_y = 0
        f.setAttribute("origin_x", origin_x)
        f.setAttribute("origin_y", origin_y)

        layer.startEditing()
        added = layer.dataProvider().addFeature(f)
        layer.commitChanges()
        layer.updateExtents()

        self._on_renderer_dirty()

        if not added:
            return None

        # Look the feature back up — its persisted fid differs from the in-memory one
        return self._find_inserted_feature(layer, f, point)

    # ------------------------------------------------------------------
    # place_feature internals
    # ------------------------------------------------------------------

    def _ensure_schema(self, layer: QgsVectorLayer) -> None:
        """Add any newer fields that are missing on this layer.

        Surfaces a clear error if a field can't be added — silent failure
        here cascades into a confusing KeyError on `setAttribute` later.
        """
        existing = {field.name() for field in layer.fields()}
        missing = [(n, QgsField(n, t)) for n, t in field_types_dict().items() if n not in existing]
        if not missing:
            return

        if not layer.startEditing():
            self._error_alert(
                "Layer-Schema-Fehler",
                "Layer kann nicht in den Bearbeitungsmodus versetzt werden.",
                f"Fehlende Felder: {[n for n, _ in missing]}",
            )
            return

        failed = [n for n, field in missing if not layer.addAttribute(field)]
        if not layer.commitChanges():
            failed = [n for n, _ in missing]

        layer.updateFields()

        still_missing = [n for n, _ in missing if layer.fields().indexFromName(n) < 0]
        if still_missing:
            self._error_alert(
                "Layer-Schema-Fehler",
                "Konnte fehlende Felder nicht zum Layer hinzufügen.",
                f"Fehlende Felder: {still_missing}\n"
                f"addAttribute-Fehler: {failed}\n"
                "Vermutete Ursache: Qt6/QGIS-4 Inkompatibilität bei Feldtypen.",
            )

    def _read_svg(self, svg_path: str) -> str | None:
        """Read SVG file contents; surfaces errors via the alert callback."""
        try:
            if not os.path.exists(svg_path):
                self._error_alert(
                    "SVG-Datei nicht gefunden",
                    f"Die SVG-Datei konnte nicht gefunden werden: {svg_path}",
                    f"Pfad: {svg_path}",
                )
                return None
            with open(svg_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            self._error_alert("SVG-Lesefehler", "Konnte SVG-Datei nicht lesen", f"Pfad: {svg_path}\nFehler: {e}")
            return None

    def _relative_to_plugin(self, svg_path: str) -> str:
        if svg_path.startswith(self._plugin_dir):
            return os.path.relpath(svg_path, self._plugin_dir)
        return svg_path

    def _initial_size(self, layer: QgsVectorLayer) -> float:
        """Compute the initial symbol size: fixed from settings, or zoom-adaptive."""
        if self._settings.new_icon_fixed_size:
            return self._settings.new_icon_size

        # Zoom-adaptive: more zoom (smaller mu/px) → larger symbols
        map_units_per_pixel = self._canvas.mapUnitsPerPixel()
        size = _BASE_SIZE * (1.0 / max(map_units_per_pixel, 0.001))

        # Don't go smaller than the smallest existing symbol — keeps mixed maps consistent
        if layer.featureCount() > 0:
            min_existing = float("inf")
            for feature in layer.getFeatures():
                feat_size = feature.attribute("size")
                if feat_size and feat_size > 0:
                    min_existing = min(min_existing, feat_size)
            if min_existing != float("inf"):
                size = max(size, min_existing)

        return max(_ADAPTIVE_MIN, min(_ADAPTIVE_MAX, size))

    def _find_inserted_feature(self, layer: QgsVectorLayer, draft: QgsFeature, point: QgsPointXY) -> QgsFeature | None:
        """Look up the just-inserted feature by unique_id (or path+location fallback)."""
        target_geom = QgsGeometry.fromPointXY(point)
        for feature in layer.getFeatures():
            if feature.attribute("unique_id") == draft.attribute("unique_id"):
                return feature
            if (
                feature.attribute("svg_path") == draft.attribute("svg_path")
                and feature.geometry().distance(target_geom) < 0.1
            ):
                return feature
        return None
