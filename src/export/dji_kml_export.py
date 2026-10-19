import html
import os
import zipfile
from collections.abc import Callable

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsProject,
    QgsRenderContext,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QApplication, QFileDialog

from ..logging_utils import get_logger

logger = get_logger(__name__)

_KML_CRS = QgsCoordinateReferenceSystem("EPSG:4326")
_DEFAULT_COLOR = QColor(255, 140, 0, 160)  # Orange, semi-transparent


class DjiKmlExporter:
    """Export a vector layer as a drone-compatible KMZ map overlay (Flugrouten).

    Target clients (e.g. DJI Pilot 2 on M30) require KML with `lon,lat,alt`
    triples and `clampToGround` altitude mode; OGR's KML writer emits
    `lon,lat` only and is rejected. We therefore build the KML by hand.

    Per-feature fill color is taken from the layer's current QGIS symbology
    so flight sectors render with the same color scheme as in QGIS.
    """

    def __init__(
        self,
        on_success: Callable[[str], None],
        on_error: Callable[[str, str, str], None],
        on_progress: Callable[[str], None] | None = None,
    ):
        self._on_success = on_success
        self._on_error = on_error
        self._on_progress = on_progress or (lambda _msg: None)

    def prompt_and_export(self, layer: QgsVectorLayer, parent_widget) -> bool:
        if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
            self._on_error(
                "Flugrouten-Export (Drohne)",
                "Ungültiger Layer",
                "Der gewählte Layer ist kein gültiger Vektorlayer.",
            )
            return False

        safe_base = _safe_filename(layer.name())
        initial_dir = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(initial_dir):
            initial_dir = os.path.expanduser("~")
        initial_path = os.path.join(initial_dir, safe_base + ".kmz")

        target_path, selected_filter = QFileDialog.getSaveFileName(
            parent_widget,
            "Flugrouten-Datei für Drohne speichern",
            initial_path,
            "KMZ für Drohne (*.kmz);;KML (*.kml)",
        )
        if not target_path:
            return False

        lower = target_path.lower()
        if not (lower.endswith(".kmz") or lower.endswith(".kml")):
            target_path += (
                ".kml" if "kml" in selected_filter.lower() and "kmz" not in selected_filter.lower() else ".kmz"
            )

        return self.export(layer, target_path)

    def export(self, layer: QgsVectorLayer, target_path: str) -> bool:
        fmt = "KMZ" if target_path.lower().endswith(".kmz") else "KML"
        self._on_progress(f"Exportiere Layer „{layer.name()}“ als {fmt} …")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            try:
                kml_text = self._build_kml(layer)
            except Exception as e:
                logger.exception("KML-Erzeugung fehlgeschlagen")
                self._on_error(
                    "Flugrouten-Export (Drohne)",
                    "KML konnte nicht erzeugt werden",
                    f"Layer: {layer.name()}\nFehler: {e}",
                )
                return False

            try:
                if target_path.lower().endswith(".kmz"):
                    with zipfile.ZipFile(target_path, "w", zipfile.ZIP_DEFLATED) as zf:
                        zf.writestr("doc.kml", kml_text)
                else:
                    with open(target_path, "w", encoding="utf-8") as f:
                        f.write(kml_text)
            except OSError as e:
                self._on_error(
                    "Flugrouten-Export (Drohne)",
                    "Datei konnte nicht geschrieben werden",
                    f"Ziel: {target_path}\nFehler: {e}",
                )
                return False
        finally:
            QApplication.restoreOverrideCursor()

        self._on_success(target_path)
        return True

    def _build_kml(self, layer: QgsVectorLayer) -> str:
        transform = _reproject_transform(layer.crs())
        display_field = layer.displayField() or ""
        renderer = layer.renderer().clone() if layer.renderer() else None
        render_ctx = QgsRenderContext()
        if renderer is not None:
            renderer.startRender(render_ctx, layer.fields())

        placemarks: list[str] = []
        styles: dict[str, QColor] = {}

        try:
            for feat in layer.getFeatures(QgsFeatureRequest()):
                geom = feat.geometry()
                if geom is None or geom.isEmpty():
                    continue

                if not transform.isShortCircuited():
                    geom = QgsGeometry(geom)
                    try:
                        geom.transform(transform)
                    except Exception as e:
                        logger.warning("Transform fehlgeschlagen für Feature %s: %s", feat.id(), e)
                        continue

                color = _feature_color(renderer, feat, render_ctx)
                style_id = _register_color(styles, color)

                label = _feature_label(feat, display_field)
                placemarks.extend(_geometry_to_placemarks(geom, style_id, label))
        finally:
            if renderer is not None:
                renderer.stopRender(render_ctx)

        style_blocks = "\n".join(_style_block(sid, col) for sid, col in styles.items())
        doc_name = html.escape(layer.name() or "Layer")
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
            "<Document>\n"
            f"<name>{doc_name}</name>\n"
            f"{style_blocks}\n" + "\n".join(placemarks) + "\n</Document>\n</kml>\n"
        )


def _reproject_transform(source_crs: QgsCoordinateReferenceSystem) -> QgsCoordinateTransform:
    if not source_crs.isValid() or source_crs == _KML_CRS:
        return QgsCoordinateTransform()
    return QgsCoordinateTransform(source_crs, _KML_CRS, QgsProject.instance())


def _feature_color(renderer, feat, render_ctx) -> QColor:
    """Pull the fill color for `feat` from the active renderer; fall back to default."""
    if renderer is None:
        return QColor(_DEFAULT_COLOR)
    try:
        symbols = renderer.symbolsForFeature(feat, render_ctx)
        if symbols:
            c = symbols[0].color()
            if c.isValid():
                return QColor(c)
    except Exception as e:
        logger.debug("Farb-Extraktion fehlgeschlagen: %s", e)
    return QColor(_DEFAULT_COLOR)


def _register_color(styles: dict[str, QColor], color: QColor) -> str:
    key = f"s_{color.red():02x}{color.green():02x}{color.blue():02x}{color.alpha():02x}"
    if key not in styles:
        styles[key] = color
    return key


def _style_block(style_id: str, color: QColor) -> str:
    # KML color format is aabbggrr (alpha, blue, green, red) — yes, reversed.
    kml_color = f"{color.alpha():02x}{color.blue():02x}{color.green():02x}{color.red():02x}"
    # Stroke: opaque version of the same color, slightly darker would be ideal
    # but keeping it identical keeps the style block small and predictable.
    line_color = f"ff{color.blue():02x}{color.green():02x}{color.red():02x}"
    return (
        f'<Style id="{style_id}">'
        f"<LineStyle><color>{line_color}</color><width>2</width></LineStyle>"
        f"<PolyStyle><color>{kml_color}</color><fill>1</fill><outline>1</outline></PolyStyle>"
        f"<IconStyle><color>{line_color}</color></IconStyle>"
        "</Style>"
    )


def _feature_label(feat, display_field: str) -> str:
    if display_field:
        value = feat.attribute(display_field)
        if value not in (None, ""):
            return str(value)
    return f"Feature {feat.id()}"


def _geometry_to_placemarks(geom: QgsGeometry, style_id: str, label: str) -> list[str]:
    """Emit one Placemark per simple geometry — split multi-geoms into parts."""
    wkb_type = geom.wkbType()
    flat = QgsWkbTypes.flatType(wkb_type)
    placemarks: list[str] = []

    if flat == QgsWkbTypes.Type.Point:
        p = geom.asPoint()
        placemarks.append(_placemark(label, style_id, _kml_point(p.x(), p.y())))
    elif flat == QgsWkbTypes.Type.MultiPoint:
        for p in geom.asMultiPoint():
            placemarks.append(_placemark(label, style_id, _kml_point(p.x(), p.y())))
    elif flat == QgsWkbTypes.Type.LineString:
        placemarks.append(_placemark(label, style_id, _kml_linestring(geom.asPolyline())))
    elif flat == QgsWkbTypes.Type.MultiLineString:
        for line in geom.asMultiPolyline():
            placemarks.append(_placemark(label, style_id, _kml_linestring(line)))
    elif flat == QgsWkbTypes.Type.Polygon:
        placemarks.append(_placemark(label, style_id, _kml_polygon(geom.asPolygon())))
    elif flat == QgsWkbTypes.Type.MultiPolygon:
        # Pilot 2 dislikes nested/multi geometries — split into separate Placemarks.
        for poly in geom.asMultiPolygon():
            placemarks.append(_placemark(label, style_id, _kml_polygon(poly)))
    else:
        logger.warning("Unbekannter WKB-Typ %s — Feature übersprungen", wkb_type)

    return placemarks


def _placemark(label: str, style_id: str, geom_xml: str) -> str:
    name = html.escape(label)
    return f"<Placemark><name>{name}</name><styleUrl>#{style_id}</styleUrl>{geom_xml}</Placemark>"


def _coord(x: float, y: float) -> str:
    # KML wants lon,lat,alt — Pilot 2 rejects files without the altitude slot.
    return f"{x:.8f},{y:.8f},0"


def _kml_point(x: float, y: float) -> str:
    return f"<Point><altitudeMode>clampToGround</altitudeMode><coordinates>{_coord(x, y)}</coordinates></Point>"


def _kml_linestring(points) -> str:
    coords = " ".join(_coord(p.x(), p.y()) for p in points)
    return (
        "<LineString><altitudeMode>clampToGround</altitudeMode>"
        "<tessellate>1</tessellate>"
        f"<coordinates>{coords}</coordinates></LineString>"
    )


def _kml_polygon(rings) -> str:
    if not rings:
        return ""
    outer = " ".join(_coord(p.x(), p.y()) for p in rings[0])
    inner_rings = "".join(
        "<innerBoundaryIs><LinearRing><coordinates>"
        + " ".join(_coord(p.x(), p.y()) for p in ring)
        + "</coordinates></LinearRing></innerBoundaryIs>"
        for ring in rings[1:]
    )
    return (
        "<Polygon><altitudeMode>clampToGround</altitudeMode>"
        "<outerBoundaryIs><LinearRing>"
        f"<coordinates>{outer}</coordinates>"
        "</LinearRing></outerBoundaryIs>"
        f"{inner_rings}"
        "</Polygon>"
    )


def _safe_filename(name: str) -> str:
    keep = "-_. "
    cleaned = "".join(c if c.isalnum() or c in keep else "_" for c in name).strip()
    return cleaned or "layer"
