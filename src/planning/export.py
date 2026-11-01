"""Objektplanung für Drohnen exportieren: Auswahl als KMZ, alles zusammen als MBTiles.

KMZ: Jedes ausgewählte Objekt wird ein Placemark mit seiner Bezeichnung (sonst dem Typ) und seiner
tatsächlichen Gestaltung – Gebiete mit Füllung und Deckkraft (ohne Füllung nur der Umriss), Linien,
Punkte und Texte in ihrer Farbe. Der allgemeine Layer-Export kennt nur die Typfarbe des Symbols.

MBTiles: alle Layer der Objektplanung und die taktischen Zeichen in einer Kachel-Ebene, mit Rand,
damit Beschriftungen, Maße und Radius-Kreise nicht abgeschnitten werden.
"""

import os

from qgis.core import Qgis, QgsCoordinateTransform, QgsGeometry, QgsProject, QgsRectangle, QgsRenderContext
from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QFileDialog, QProgressDialog

from ..export.dji_kml_export import DjiKmlExporter, KmlItem
from ..export.dji_mbtiles_export import DjiMbtilesExporter
from ..export.dji_mbtiles_export import _ZoomDialog as ZoomDialog
from ..util.coordinates import WGS84
from .details import item_name, type_name
from .geometry import MetricFrame
from .layers import OBJECT_STYLE_ROLES, ROLE_MARKERS, ROLE_ZONES
from .object_style import object_style_of
from .zone_style import PATTERN_NONE

_TITLE_KMZ = "KMZ-Export (Drohne)"
_TITLE_MBTILES = "MBTiles-Export (Drohne)"
# Rand um die Objekte (Meter): Beschriftungen und Maße ragen über die Geometrien hinaus
_MBTILES_MARGIN_M = 50.0


def _safe_filename(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_. " else "_" for c in name).strip()
    return cleaned or "Objektplanung"


def _initial_dir() -> str:
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    return desktop if os.path.isdir(desktop) else os.path.expanduser("~")


def _label(role: str, feature) -> str:
    """Name im KMZ: Bezeichnung (mehrzeilig mit „ / “ verbunden), sonst der Typ."""
    lines = [line.strip() for line in item_name(role, feature).splitlines() if line.strip()]
    return " / ".join(lines) if lines else type_name(role, feature)


def _colors(controller, role: str, feature, layer) -> tuple[QColor, QColor]:
    """(Füllung, Linie) so, wie das Objekt auf der Karte aussieht."""
    if role == ROLE_ZONES:
        style = controller._zone_style(feature)
        fill = QColor(style.fill)
        fill.setAlphaF(0.0 if style.pattern == PATTERN_NONE else style.opacity)
        line = QColor(style.stroke or style.fill)
        return fill, line
    if role in OBJECT_STYLE_ROLES:
        style = object_style_of(feature, controller.type_color(role, feature))
        color = QColor(style.color)
        color.setAlphaF(style.opacity)
        return color, color
    # Zelte, Fahrzeuge, Strom, Beleuchtung: Farbe ihres Symbols
    renderer = layer.renderer().clone() if layer.renderer() else None
    color = QColor(255, 140, 0, 160)
    if renderer is not None:
        context = QgsRenderContext()
        renderer.startRender(context, layer.fields())
        symbols = renderer.symbolsForFeature(feature, context)
        renderer.stopRender(context)
        if symbols and symbols[0].color().isValid():
            color = QColor(symbols[0].color())
    return color, QColor(color.red(), color.green(), color.blue())


def kml_items(controller) -> list[KmlItem]:
    """Die ausgewählten Objekte der Objektplanung (ohne taktische Zeichen) als KMZ-Objekte."""
    items = []
    for role, feature in controller.planning_selection():
        if not feature.hasGeometry():
            continue
        layer = controller.layers.layer(role)
        fill, line = _colors(controller, role, feature, layer)
        items.append(KmlItem(feature.geometry(), layer.crs(), _label(role, feature), fill, line))
    return items


def export_selection_kmz(controller, parent) -> bool:
    """Ausgewählte Objekte als KMZ (bzw. KML) speichern; ein Name für die Datei kommt vom ersten Objekt."""
    items = kml_items(controller)
    iface = controller.iface
    if not items:
        iface.messageBar().pushMessage(
            _TITLE_KMZ, "Keine Objekte der Objektplanung ausgewählt.", Qgis.MessageLevel.Warning, 5
        )
        return False
    name = items[0].label if len(items) == 1 else "Objektplanung"
    path, selected = QFileDialog.getSaveFileName(
        parent,
        "KMZ für Drohne speichern",
        os.path.join(_initial_dir(), _safe_filename(name) + ".kmz"),
        "KMZ für Drohne (*.kmz);;KML (*.kml)",
    )
    if not path:
        return False
    if not path.lower().endswith((".kmz", ".kml")):
        path += ".kml" if "(*.kml)" in selected else ".kmz"
    exporter = DjiKmlExporter(
        on_success=lambda p: iface.messageBar().pushMessage(
            _TITLE_KMZ, f"{len(items)} Objekt(e) gespeichert: {p}", Qgis.MessageLevel.Success, 6
        ),
        on_error=lambda title, text, detail: iface.messageBar().pushMessage(
            title, f"{text}: {detail}", Qgis.MessageLevel.Critical, 10
        ),
    )
    return exporter.export_items(items, path, name)


def _export_layers(controller) -> list:
    """Alle Layer der Objektplanung mit Objekten, dazu die taktischen Zeichen."""
    layers = [layer for layer in controller.layers.all_layers() if layer.featureCount() > 0]
    markers = controller.layer(ROLE_MARKERS) or getattr(controller.plugin, "layer", None)
    if markers is not None and markers.featureCount() > 0:
        layers.append(markers)
    return layers


def _extent_wgs84(layers) -> QgsRectangle | None:
    """Gemeinsame Ausdehnung in WGS 84, um ``_MBTILES_MARGIN_M`` erweitert (auch bei einem einzelnen Punkt)."""
    extent = None
    for layer in layers:
        box = layer.extent()
        frame = MetricFrame(layer.crs(), box.center())
        metric = frame.geom_to_m(QgsGeometry.fromRect(box)).boundingBox().buffered(_MBTILES_MARGIN_M)
        grown = frame.geom_from_m(QgsGeometry.fromRect(metric)).boundingBox()
        wgs = QgsCoordinateTransform(layer.crs(), WGS84, QgsProject.instance()).transformBoundingBox(grown)
        if extent is None:
            extent = QgsRectangle(wgs)
        else:
            extent.combineExtentWith(wgs)
    return extent


def export_mbtiles(controller, parent) -> bool:
    """Objektplanung samt Beschriftungen, Maßen und taktischen Zeichen als eine MBTiles-Ebene."""
    iface = controller.iface
    layers = _export_layers(controller)
    if not layers:
        iface.messageBar().pushMessage(
            _TITLE_MBTILES, "Die Objektplanung enthält noch keine Objekte.", Qgis.MessageLevel.Warning, 5
        )
        return False
    zoom = ZoomDialog.ask(parent)
    if zoom is None:
        return False
    path, _ = QFileDialog.getSaveFileName(
        parent,
        "MBTiles für Drohne speichern",
        os.path.join(_initial_dir(), "Objektplanung.mbtiles"),
        "MBTiles (*.mbtiles)",
    )
    if not path:
        return False
    if not path.lower().endswith(".mbtiles"):
        path += ".mbtiles"

    progress = QProgressDialog("Export wird vorbereitet …", "Abbrechen", 0, 100, parent)
    progress.setWindowTitle(_TITLE_MBTILES)
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)

    def on_feedback(percent: int, text: str):
        progress.setValue(max(0, min(100, percent)))
        if text:
            progress.setLabelText(text)
        QCoreApplication.processEvents()

    def cancelled() -> bool:
        QCoreApplication.processEvents()
        return progress.wasCanceled()

    errors = []
    exporter = DjiMbtilesExporter(
        on_success=lambda _p: None,
        on_error=lambda title, text, detail: errors.append(f"{text}: {detail}"),
        on_feedback=on_feedback,
        on_cancel_check=cancelled,
    )
    try:
        ok = exporter.export_layers(layers, path, zoom[0], zoom[1], _extent_wgs84(layers), "Objektplanung")
    finally:
        progress.close()
    if ok:
        iface.messageBar().pushMessage(_TITLE_MBTILES, f"Gespeichert: {path}", Qgis.MessageLevel.Success, 6)
    else:
        iface.messageBar().pushMessage(
            _TITLE_MBTILES, errors[0] if errors else "Abgebrochen.", Qgis.MessageLevel.Warning, 10
        )
    return ok
