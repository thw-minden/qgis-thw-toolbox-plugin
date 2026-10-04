"""Zeichenerklärung der Druckvorlagen: nur zeigen, was die Karte erklärt.

In die Legende gehört, was auf die Grundkarte gezeichnet wurde: taktische
Zeichen, Objektplanung und eigene Vektorlayer. Hintergrundkarten und Gitter
stehen unter „Quellen“ bzw. „Gitter“ und bleiben draußen. Wer davon abweichen
will, hakt einzelne Layer im Druckvorlagen-Dialog an oder ab; die Abweichung
wird als Layer-Eigenschaft im Projekt gespeichert.

Die Legende wird einmalig aufgebaut und folgt dem Projekt danach nicht mehr von
selbst. „Legende aktualisieren“ im Layout-Menü des Designers baut sie neu auf.
"""

from qgis.core import (
    QgsCoordinateTransform,
    QgsCsException,
    QgsFeatureRequest,
    QgsLayerTree,
    QgsLayoutItemLegend,
    QgsLegendRenderer,
    QgsLegendStyle,
    QgsMapLayer,
    QgsMapLayerLegendUtils,
    QgsMarkerSymbolLayer,
    QgsPrintLayout,
    QgsProject,
    QgsSymbolLegendNode,
    QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.PyQt.QtWidgets import QAction

from ..layer.renderer import _BG_SCALE, category_value
from ..planning.layers import ROLE_PROPERTY

# Layer-Eigenschaft: 1/0 überstimmt die Regel aus `_default_in_legend`
LEGEND_PROPERTY = "thw_toolbox/legende"


def _is_marker_layer(layer) -> bool:
    if not isinstance(layer, QgsVectorLayer):
        return False
    names = layer.fields().names()
    return "svg_path" in names and "unique_id" in names


def _default_in_legend(layer: QgsMapLayer) -> bool:
    """Regel ohne Angabe des Nutzers: Vektorlayer ja, Hintergrund- und Rasterkarten nein."""
    return isinstance(layer, QgsVectorLayer) and layer.isSpatial()


def in_legend(layer: QgsMapLayer) -> bool:
    value = str(layer.customProperty(LEGEND_PROPERTY, ""))
    if value in ("0", "1"):
        return value == "1"
    return _default_in_legend(layer)


def set_in_legend(layer: QgsMapLayer, show: bool) -> None:
    """Abweichung von der Regel am Layer merken; entspricht `show` der Regel, entfällt die Eigenschaft."""
    if show == _default_in_legend(layer):
        layer.removeCustomProperty(LEGEND_PROPERTY)
    else:
        layer.setCustomProperty(LEGEND_PROPERTY, int(show))


def legend_candidates() -> list[QgsMapLayer]:
    """Im Layerbaum eingeschaltete Layer in dessen Reihenfolge; nur sie können in der Legende stehen."""
    nodes = QgsProject.instance().layerTreeRoot().findLayers()
    return [node.layer() for node in nodes if node.isVisible() and node.layer() is not None]


def apply_legend(legend: QgsLayoutItemLegend) -> None:
    """Legende auf das Nötige reduzieren. Die Kartendarstellung bleibt unverändert.

    Es bleiben die eingeschalteten Layer laut `in_legend`, ohne Gruppen. Der
    Renderer der taktischen Zeichen legt pro Feature eine Kategorie mit eigener
    Größe/Drehung an; hier bleibt pro Zeichen ein Eintrag mit einem eigenen
    Symbol fester Größe (in mm). Die Layer der Toolbox stehen ohne Überschrift
    da, ihre Zeichen erklären sich selbst.
    """
    # Eigenes Legendenmodell statt automatischem Spiegel des Projekt-Layerbaums;
    # das Ein- und Ausschalten holt dabei den aktuellen Stand des Layerbaums
    legend.setAutoUpdateModel(True)
    legend.setAutoUpdateModel(False)
    model = legend.model()
    root = model.rootGroup()

    for node in root.findLayers():
        layer = node.layer()
        if layer is None or not node.isVisible() or not in_legend(layer):
            node.parent().removeChildNode(node)
    _drop_empty_groups(root)
    for group in _groups(root):
        QgsLegendRenderer.setNodeLegendStyle(group, QgsLegendStyle.Style.Hidden)

    # Vorlage gibt pro Format max. Symbolgröße vor (A4: ~4,2 mm, A0: ~17 mm)
    size_mm = legend.maximumSymbolSize() or legend.symbolHeight()
    for node in root.findLayers():
        layer = node.layer()
        if _is_marker_layer(layer):
            _unify_markers(model, node, size_mm, legend.linkedMap())
        if _is_marker_layer(layer) or layer.customProperty(ROLE_PROPERTY):
            QgsLegendRenderer.setNodeLegendStyle(node, QgsLegendStyle.Style.Hidden)

    legend.updateLegend()


def refresh_legends(layout: QgsPrintLayout) -> None:
    for item in layout.items():
        if isinstance(item, QgsLayoutItemLegend):
            apply_legend(item)


def add_legend_action(designer) -> QAction:
    """Menüeintrag „Legende aktualisieren“ im Layout-Designer anlegen."""
    action = QAction("Legende aktualisieren (THW Toolbox)", designer.window())
    action.setToolTip("Zeichenerklärung neu aufbauen: nur gewählte Layer, jedes Zeichen einmal")
    action.triggered.connect(lambda: refresh_legends(designer.layout()))
    designer.layoutMenu().addAction(action)
    return action


def _groups(group):
    for child in group.children():
        if QgsLayerTree.isGroup(child):
            yield child
            yield from _groups(child)


def _drop_empty_groups(group) -> None:
    for child in list(group.children()):
        if QgsLayerTree.isGroup(child):
            _drop_empty_groups(child)
            if not child.children():
                group.removeChildNode(child)


def _values_in_map(layer: QgsVectorLayer, map_item) -> set[str] | None:
    """Kategorie-Werte der Zeichen im Kartenausschnitt; `None`, wenn er sich nicht bestimmen lässt."""
    if map_item is None:
        return None
    try:
        transform = QgsCoordinateTransform(map_item.crs(), layer.crs(), QgsProject.instance())
        extent = transform.transformBoundingBox(map_item.extent())
    except QgsCsException:
        return None
    return {str(category_value(feat)) for feat in layer.getFeatures(QgsFeatureRequest().setFilterRect(extent))}


def _unify_markers(model, node_layer, size_mm: float, map_item) -> None:
    """Pro taktischem Zeichen einen Eintrag behalten: ungedreht, gleich groß, Name ohne Unterstriche."""
    layer = node_layer.layer()
    # Vorherige Reihenfolge verwerfen (eine leere Liste würde alle Einträge ausblenden)
    node_layer.removeCustomProperty("legend/node-order")
    model.refreshLayerLegend(node_layer)

    in_map = _values_in_map(layer, map_item)
    renderer = layer.renderer()
    values = [str(c.value()) for c in renderer.categories()] if hasattr(renderer, "categories") else []

    # Die Legende blendet Kategorien außerhalb des Kartenausschnitts aus. Als Vertreter eines
    # Zeichens deshalb ein Feature im Ausschnitt wählen, sonst fehlte das Zeichen trotz Treffer.
    chosen: dict[str, tuple[int, bool]] = {}
    nodes = model.layerOriginalLegendNodes(node_layer)
    symbol_index = -1
    for index, node in enumerate(nodes):
        if not isinstance(node, QgsSymbolLegendNode) or node.symbol() is None:
            continue
        symbol_index += 1
        visible = in_map is None or (symbol_index < len(values) and values[symbol_index] in in_map)
        label = node.symbolLabel()
        if label not in chosen or (visible and not chosen[label][1]):
            chosen[label] = (index, visible)

    for label, (index, _) in chosen.items():
        QgsMapLayerLegendUtils.setLegendNodeCustomSymbol(
            node_layer, index, _legend_symbol(nodes[index].symbol(), size_mm)
        )
        if "_" in label:
            QgsMapLayerLegendUtils.setLegendNodeUserLabel(node_layer, index, label.replace("_", " "))
    QgsMapLayerLegendUtils.setLegendNodeOrder(node_layer, sorted(index for index, _ in chosen.values()))
    model.refreshLayerLegend(node_layer)


def _legend_symbol(symbol, size_mm: float):
    sym = symbol.clone()
    for symbol_layer in sym.symbolLayers():
        scale = 1.0 if symbol_layer.layerType() == "SvgMarker" else _BG_SCALE
        symbol_layer.setSize(size_mm * scale)
        symbol_layer.setSizeUnit(QgsUnitTypes.RenderUnit.RenderMillimeters)
        symbol_layer.setAngle(0)
        # Ursprungspunkt (z. B. Fußpunkt) würde das Zeichen aus dem Legendenfeld schieben
        symbol_layer.setHorizontalAnchorPoint(QgsMarkerSymbolLayer.HorizontalAnchorPoint.HCenter)
        symbol_layer.setVerticalAnchorPoint(QgsMarkerSymbolLayer.VerticalAnchorPoint.VCenter)
    return sym
