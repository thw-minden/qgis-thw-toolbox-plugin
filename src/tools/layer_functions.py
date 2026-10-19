"""
This file contains functions to generate special layers in QGIS that are not provided via a tile service
"""

import base64
import os
import shutil
import time

from qgis.core import (
    Qgis,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsProject,
    QgsProperty,
    QgsProviderRegistry,
    QgsRuleBasedRenderer,
    QgsSvgMarkerSymbolLayer,
    QgsSymbolLayer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsUnitTypes,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QColor

from ..logging_utils import get_logger
from ..util.svg_minify import minify_svg

logger = get_logger(__name__)

# Die Dienststellen liegen als GeoPackage neben der Projektdatei (wie Marker und Objektplanung),
# damit das Projekt ohne den Plugin-Ordner auskommt
GPKG_SUFFIX = "_dienststellen"
GPKG_LAYER_NAME = "dienststellen"

# Zeichen je Art der Dienststelle (Teilwort im Feld ``title``), SVG unter ``svgs/THW_Gebäude``
_SIGNS = (
    ("Ortsverband", "OV_Unterkunft.svg"),
    ("Regionalstelle", "Regionalstelle.svg"),
    ("Landesverband", "Landesverband.svg"),
    ("Ausbildungszentrum", "Ausbildungszentrum.svg"),
    ("Leitung", "Leitung.svg"),
)

# Zeichengröße in Karteneinheiten nach Zoomstufe
_SIZE_EXPR = (
    "CASE "
    "WHEN @zoom_level >= 10 AND @zoom_level <= 16 THEN scale_linear(@zoom_level, 10, 16, 300, 20) "
    "WHEN @zoom_level > 16 THEN 20 "
    "ELSE 300 "
    "END"
)


def _plugin_dir() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def _gpkg_path(proj: QgsProject, plugin_dir: str) -> str:
    """GeoPackage neben der Projektdatei; bei ungespeichertem Projekt im tmp-Ordner des Plugins (wie die Marker)."""
    pfile = proj.fileName()
    if pfile:
        return os.path.splitext(pfile)[0] + GPKG_SUFFIX + ".gpkg"
    tmp_dir = os.path.join(plugin_dir, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    return os.path.join(tmp_dir, f"projekt_{int(time.time())}{GPKG_SUFFIX}.gpkg")


def _embedded_svg(path: str) -> str:
    """``base64:``-Pfad des verkleinerten SVGs – das Zeichen liegt damit im Projekt statt im Plugin-Ordner."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return "base64:" + base64.b64encode(minify_svg(text).encode("utf-8")).decode("ascii")


def _write_gpkg(source: QgsVectorLayer, gpkg: str) -> bool:
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = GPKG_LAYER_NAME
    opts.actionOnExistingFile = (
        QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
        if os.path.exists(gpkg)
        else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
    )
    result = QgsVectorFileWriter.writeAsVectorFormatV3(source, gpkg, QgsProject.instance().transformContext(), opts)
    if result[0] != QgsVectorFileWriter.WriterError.NoError:
        logger.error("Dienststellen konnten nicht nach %s geschrieben werden: %s", gpkg, result[1])
        return False
    return True


def _dienststellen_renderer(plugin_dir: str) -> QgsRuleBasedRenderer:
    """Regelbasiert je Art der Dienststelle, jedes Zeichen als eingebettetes SVG."""
    root_rule = QgsRuleBasedRenderer.Rule(None)
    for key, svg_name in _SIGNS:
        symbol = QgsMarkerSymbol.createSimple({})
        svg_layer = QgsSvgMarkerSymbolLayer(_embedded_svg(os.path.join(plugin_dir, "svgs", "THW_Gebäude", svg_name)))
        svg_layer.setDataDefinedProperty(QgsSymbolLayer.Property.Size, QgsProperty.fromExpression(_SIZE_EXPR))
        svg_layer.setSizeUnit(QgsUnitTypes.RenderUnit.RenderMapUnits)
        symbol.changeSymbolLayer(0, svg_layer)
        rule = QgsRuleBasedRenderer.Rule(symbol=symbol, filterExp=f"""\"title\" ILIKE '%{key}%'""", label=key)
        root_rule.appendChild(rule)
    return QgsRuleBasedRenderer(root_rule)


def setup_thw_dienststellen(label: str) -> QgsVectorLayer:
    """Layer der THW-Dienststellen aus ``data/ovs.json``.

    Die Daten werden in ein GeoPackage neben der Projektdatei geschrieben und von dort geladen, die Zeichen
    liegen eingebettet im Renderer – so öffnet sich das Projekt auch ohne Plugin. Lässt sich das GeoPackage
    nicht schreiben, bleibt der Layer ausnahmsweise an der JSON-Datei im Plugin-Ordner.
    """
    plugin_dir = _plugin_dir()
    src_json = os.path.join(plugin_dir, "data", "ovs.json")

    logger.debug("Loading THW Dienststellen from %s", src_json)
    source = QgsVectorLayer(src_json, label, "ogr")
    if not source.isValid():
        raise ValueError(f"{label} layer invalid: {source.error().summary()}")

    gpkg = _gpkg_path(QgsProject.instance(), plugin_dir)
    layer = None
    if _write_gpkg(source, gpkg) or os.path.exists(gpkg):
        layer = QgsVectorLayer(f"{gpkg}|layername={GPKG_LAYER_NAME}", label, "ogr")
    if layer is None or not layer.isValid():
        logger.warning("Dienststellen bleiben an %s, GeoPackage %s nicht nutzbar", src_json, gpkg)
        layer = source

    layer.setRenderer(_dienststellen_renderer(plugin_dir))

    # Labels
    label_settings = QgsPalLayerSettings()
    text_format = QgsTextFormat()
    text_format.setSize(8)
    text_format.setSizeUnit(Qgis.RenderUnit.RenderMapUnits)
    text_format.setColor(QColor("black"))
    font = text_format.font()
    font.setBold(True)
    text_format.setFont(font)

    buffer_settings = QgsTextBufferSettings()
    buffer_settings.setEnabled(True)
    buffer_settings.setSize(1)
    buffer_settings.setSizeUnit(Qgis.RenderUnit.RenderMapUnits)
    buffer_settings.setColor(QColor("white"))
    text_format.setBuffer(buffer_settings)

    label_settings.setFormat(text_format)
    label_settings.fieldName = (
        """replace("title",array('Ortsverband ','Regionalstelle ','Landesverband ','Ausbildungszentrum '),'')"""
    )
    label_settings.isExpression = True

    try:
        # 1) Place labels around the point (cartographic / ordered positions)
        if hasattr(Qgis, "LabelPlacement") and hasattr(Qgis.LabelPlacement, "OverPoint"):
            label_settings.placement = Qgis.LabelPlacement.OverPoint
        else:
            # Fallback for older versions
            label_settings.placement = QgsPalLayerSettings.OverPoint

        # 2) Tell QGIS to put the label in the bottom/below quadrant of the point
        if hasattr(Qgis, "LabelQuadrantPosition"):
            # QGIS >= 3.26: use the new enum
            label_settings.quadOffset = Qgis.LabelQuadrantPosition.Below
        else:
            # Older API: use QuadrantPosition enum from QgsPalLayerSettings
            if hasattr(QgsPalLayerSettings, "BottomMiddle"):
                label_settings.quadOffset = QgsPalLayerSettings.BottomMiddle
            elif hasattr(QgsPalLayerSettings, "Bottom"):
                label_settings.quadOffset = QgsPalLayerSettings.Bottom
            else:
                # last resort: standard bottom-middle index
                label_settings.quadOffset = (
                    QgsPalLayerSettings.BottomMiddle if hasattr(QgsPalLayerSettings, "BottomMiddle") else 0
                )
    except Exception as e:
        logger.debug("Label placement could not be set: %s", e)

    try:
        # Offset-Werte in Points , Abstand abhängig von der Größe des Zeichens
        if hasattr(Qgis, "RenderUnit") and hasattr(Qgis.RenderUnit, "Points"):
            label_settings.offsetUnits = Qgis.RenderUnit.RenderMapUnits
        size_property = QgsProperty.fromExpression("array(0,5)")
        label_settings.dataDefinedProperties().setProperty(QgsPalLayerSettings.Property.OffsetXY, size_property)
    except Exception as e:
        logger.debug("Label offset could not be set: %s", e)

    label_settings.dataDefinedProperties().setProperty(QgsPalLayerSettings.Property.ScaleVisibility, True)
    label_settings.dataDefinedProperties().setProperty(QgsPalLayerSettings.Property.MinimumScale, 5000)
    label_settings.dataDefinedProperties().setProperty(QgsPalLayerSettings.Property.MaximumScale, 0)

    layer.setLabelsEnabled(True)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(label_settings))

    return layer


# ----------------------------------------------------------------------
# Projekt speichern: GeoPackage neben die Projektdatei ziehen
# ----------------------------------------------------------------------


def _dienststellen_layers(proj: QgsProject) -> list[tuple[QgsVectorLayer, str]]:
    """Dienststellen-Layer des Projekts mit dem Pfad ihres GeoPackages.

    Erkannt wird die Datenquelle ``*_dienststellen.gpkg|layername=dienststellen``, unabhängig vom Layernamen.
    """
    found = []
    for layer in proj.mapLayers().values():
        if not isinstance(layer, QgsVectorLayer) or layer.providerType() != "ogr":
            continue
        parts = QgsProviderRegistry.instance().decodeUri("ogr", layer.source())
        path = str(parts.get("path") or "")
        if path.lower().endswith(f"{GPKG_SUFFIX}.gpkg") and parts.get("layerName") == GPKG_LAYER_NAME:
            found.append((layer, path))
    return found


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _in_plugin_tmp(path: str, plugin_dir: str) -> bool:
    return _same_file(os.path.dirname(path), os.path.join(plugin_dir, "tmp"))


def _move_dienststellen_layer(layer: QgsVectorLayer, current: str, target: str, plugin_dir: str) -> bool:
    """Ein GeoPackage nach ``target`` exportieren (notfalls kopieren) und den Layer darauf umstellen."""
    if not os.path.exists(current):
        logger.warning("Dienststellen-GeoPackage %s fehlt, Layer „%s“ bleibt unverändert", current, layer.name())
        return False
    if layer.isEditable():
        layer.commitChanges()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if not _write_gpkg(layer, target):
        logger.warning("Kopiere %s stattdessen als Datei nach %s", current, target)
        shutil.copy2(current, target)

    # Datenquelle umstellen statt den Layer zu tauschen: Layer-ID, Platz im Layerbaum, Darstellung,
    # Beschriftung und Verweise aus Drucklayouts (Legende, Kartenebenen) bleiben erhalten.
    old_uri = layer.source()
    layer.setDataSource(f"{target}|layername={GPKG_LAYER_NAME}", layer.name(), "ogr")
    if not layer.isValid():
        message = layer.error().message()
        layer.setDataSource(old_uri, layer.name(), "ogr")
        logger.error("Dienststellen-Layer „%s“ nach dem Umzug nach %s ungültig: %s", layer.name(), target, message)
        return False
    logger.info("Dienststellen-GeoPackage von „%s“ liegt jetzt neben dem Projekt: %s", layer.name(), target)

    # Die Datei eines ungespeicherten Projekts aus dem tmp-Ordner wird nicht mehr gebraucht; eine Datei neben
    # einem anderen Projekt („Speichern unter“) bleibt liegen
    if _in_plugin_tmp(current, plugin_dir):
        _remove_tmp_file(current)
    return True


def _remove_tmp_file(path: str) -> None:
    """Alte tmp-Datei löschen (best effort).

    Direkt nach ``setDataSource()`` hält der OGR-Provider die Datei meist noch offen – die alten Verbindungen
    werden erst mit den aufgeschobenen Löschungen der Ereignisschleife geschlossen. Deshalb ein zweiter Versuch
    kurz nach dem Speichern.
    """

    def attempt(final: bool) -> bool:
        try:
            os.remove(path)
            logger.debug("Alte tmp-Datei gelöscht: %s", path)
            return True
        except FileNotFoundError:
            return True
        except OSError as e:
            if final:
                logger.warning("Alte tmp-Datei %s bleibt liegen: %s", path, e)
            return False

    if not attempt(final=False):
        QTimer.singleShot(1500, lambda: attempt(final=True))


def _update_layer_tree_sources(doc, proj: QgsProject, layers: list[QgsVectorLayer]) -> None:
    """``source``-Attribut der umgezogenen Layer im Layerbaum des Projekt-XML nachziehen.

    QGIS schreibt den Layerbaum vor dem Signal ``writeProject`` in das Dokument, die Layer selbst erst danach.
    Ohne Nachziehen stünde im Baum noch der alte Pfad (der Layer wird zwar über die ID gefunden, aber der
    Plugin-Ordner soll nicht im Projekt stehen).
    """
    if doc is None or not layers or not hasattr(doc, "elementsByTagName"):
        return
    by_id = {layer.id(): layer for layer in layers}
    resolver = proj.pathResolver()
    nodes = doc.elementsByTagName("layer-tree-layer")
    for i in range(nodes.count()):
        elem = nodes.at(i).toElement()
        layer = by_id.get(elem.attribute("id"))
        if layer is not None:
            elem.setAttribute("source", resolver.writePath(layer.publicSource()))


def move_dienststellen_next_to_project(doc=None) -> None:
    """Dienststellen-GeoPackages beim Speichern neben die Projektdatei ziehen (wie Marker und Objektplanung).

    Wird aus ``QgsProject.writeProject`` aufgerufen, also bevor QGIS die Layer in die Projektdatei schreibt;
    ``doc`` ist das dabei übergebene Projekt-XML. Liegt das GeoPackage eines Dienststellen-Layers noch nicht
    als ``<projekt>_dienststellen.gpkg`` neben der Projektdatei (ungespeichertes Projekt → tmp-Ordner des
    Plugins, oder „Speichern unter“), wird es dorthin exportiert und die Datenquelle per ``setDataSource()``
    umgestellt; der Layer selbst bleibt. Fehler werden nur protokolliert, nie geworfen – das Speichern des
    Projekts soll daran nicht scheitern.
    """
    try:
        proj = QgsProject.instance()
        pfile = proj.fileName()
        if not pfile:
            return
        target = os.path.splitext(pfile)[0] + GPKG_SUFFIX + ".gpkg"
        plugin_dir = _plugin_dir()
        moved = []
        for layer, current in _dienststellen_layers(proj):
            if _same_file(current, target):
                continue
            try:
                if _move_dienststellen_layer(layer, current, target, plugin_dir):
                    moved.append(layer)
            except Exception:
                logger.exception("Dienststellen-GeoPackage von „%s“ zieht nicht neben das Projekt", layer.name())
        _update_layer_tree_sources(doc, proj, moved)
    except Exception:
        logger.exception("Umzug der Dienststellen-GeoPackages beim Speichern fehlgeschlagen")
