"""Zeichenerklärung der Druckvorlagen: nur zeigen, was die Karte erklärt.

In die Legende gehört, was auf die Grundkarte gezeichnet wurde: taktische
Zeichen, Objektplanung und eigene Vektorlayer. Hintergrundkarten und Gitter
stehen unter „Quellen“ bzw. „Gitter“ und bleiben draußen. Wer davon abweichen
will, hakt einzelne Layer im Druckvorlagen-Dialog an oder ab; die Abweichung
wird als Layer-Eigenschaft im Projekt gespeichert.

Die Legende wird einmalig aufgebaut und folgt dem Projekt danach nicht mehr von
selbst. Der Button „Legende aktualisieren“ in der Toolbar des Designers (auch im
Layout-Menü) baut sie neu auf; sein Menü stellt ein, was in der Legende steht.
"""

from functools import partial

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateTransform,
    QgsCsException,
    QgsFeatureRequest,
    QgsLayerTree,
    QgsLayout,
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
from qgis.PyQt.QtCore import QPointF, Qt
from qgis.PyQt.QtWidgets import QAction, QMainWindow, QMenu, QToolBar, QToolButton

from ..layer.background import DEFAULT_SCALE
from ..layer.renderer import category_value, svg_symbol_layer
from ..planning.layers import ROLE_PROPERTY

# Layer-Eigenschaft: 1/0 überstimmt die Regel aus `_default_in_legend`
LEGEND_PROPERTY = "thw_toolbox/legende"
# Layout-Eigenschaft: 1 = Überschriften über den Layern der Toolbox (Taktische Zeichen, Fahrzeuge …)
TITLES_PROPERTY = "thw_toolbox/legende_ueberschriften"
# Überschrift der taktischen Zeichen statt des Layernamens
MARKER_TITLE = "Taktische Zeichen"


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


def titles_shown(layout: QgsLayout) -> bool:
    return str(layout.customProperty(TITLES_PROPERTY, "")).lower() in ("1", "true")


def set_titles_shown(layout: QgsLayout, show: bool) -> None:
    layout.setCustomProperty(TITLES_PROPERTY, int(show))


def _take_project_tree(legend: QgsLayoutItemLegend) -> None:
    """Eigenes Legendenmodell mit dem aktuellen Stand des Projekt-Layerbaums, das ihm danach nicht mehr folgt.

    Das Hin- und Herschalten verwirft auch Verweise auf Layer, die es nicht mehr gibt.
    """
    if hasattr(legend, "setSyncMode"):  # QGIS 4
        legend.setSyncMode(Qgis.LegendSyncMode.AllProjectLayers)
        legend.setSyncMode(Qgis.LegendSyncMode.Manual)
    else:
        legend.setAutoUpdateModel(True)
        legend.setAutoUpdateModel(False)


def apply_legend(legend: QgsLayoutItemLegend) -> None:
    """Legende auf das Nötige reduzieren. Die Kartendarstellung bleibt unverändert.

    Es bleiben die eingeschalteten Layer laut `in_legend`, ohne Gruppen. Der
    Renderer der taktischen Zeichen legt pro Feature eine Kategorie mit eigener
    Größe/Drehung an; hier bleibt pro Zeichen ein Eintrag mit einem eigenen
    Symbol fester Größe (in mm). Die Layer der Toolbox stehen ohne Überschrift
    da, ihre Zeichen erklären sich selbst – außer `titles_shown` ist am Layout gesetzt.
    """
    _take_project_tree(legend)
    show_titles = legend.layout() is not None and titles_shown(legend.layout())
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
            node.setCustomProperty("legend/title-label", MARKER_TITLE)
        if not show_titles and (_is_marker_layer(layer) or layer.customProperty(ROLE_PROPERTY)):
            QgsLegendRenderer.setNodeLegendStyle(node, QgsLegendStyle.Style.Hidden)

    legend.updateLegend()


def legends_of(layout: QgsPrintLayout) -> list[QgsLayoutItemLegend]:
    return [item for item in layout.items() if isinstance(item, QgsLayoutItemLegend)]


def refresh_legends(layout: QgsPrintLayout) -> int:
    """Alle Legenden des Layouts neu aufbauen; gibt ihre Anzahl zurück."""
    legends = legends_of(layout)
    for legend in legends:
        apply_legend(legend)
    return len(legends)


def add_legend_action(designer) -> QAction:
    """„Legende aktualisieren“ im Layout-Designer: Eintrag im Layout-Menü und Button in einer eigenen Toolbar.

    Der Pfeil am Button öffnet ein Menü, das einstellt, was in der Legende steht.
    """
    window = designer.window()
    action = QAction(QgsApplication.getThemeIcon("/mActionRefresh.svg"), "Legende aktualisieren (THW Toolbox)", window)
    action.setIconText("Legende aktualisieren")
    action.setToolTip(
        "Zeichenerklärung neu aufbauen: nur gewählte Layer, jedes Zeichen einmal.\n"
        "Pfeil daneben: einstellen, was in der Legende steht."
    )
    action.triggered.connect(lambda: _refresh_from_designer(designer, notify=True))
    designer.layoutMenu().addAction(action)

    if isinstance(window, QMainWindow):
        menu = QMenu(window)
        menu.setToolTipsVisible(True)
        menu.aboutToShow.connect(lambda: _fill_menu(menu, designer, action))
        toolbar = QToolBar("THW Toolbox", window)
        toolbar.setObjectName("THWToolboxLayoutToolbar")
        button = QToolButton(toolbar)
        button.setDefaultAction(action)
        button.setMenu(menu)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.addWidget(button)
        window.addToolBar(toolbar)
        # Das Plugin räumt beim Entladen nur die Aktion ab – die Toolbar geht mit
        action.destroyed.connect(toolbar.deleteLater)
    return action


def _refresh_from_designer(designer, notify: bool = False) -> None:
    count = refresh_legends(designer.layout())
    if count == 0:
        designer.messageBar().pushMessage("Legende", "Das Layout enthält keine Legende.", Qgis.MessageLevel.Info, 4)
    elif notify:
        designer.messageBar().pushMessage("Legende", "Zeichenerklärung aktualisiert.", Qgis.MessageLevel.Success, 3)


def _fill_menu(menu: QMenu, designer, refresh_action: QAction) -> None:
    """Menü beim Öffnen neu füllen, damit es Layerbaum und Legende im aktuellen Stand zeigt."""
    menu.clear()
    menu.addAction(refresh_action)
    layout = designer.layout()
    legends = legends_of(layout)
    if not legends:
        _add_note(menu, "Das Layout enthält keine Legende.")
        return

    menu.addSeparator()
    _add_note(menu, "In der Legende (gilt für alle Layouts):")
    candidates = legend_candidates()
    if not candidates:
        _add_note(menu, "Keine eingeschalteten Layer")
    for layer in candidates:
        item = menu.addAction(layer.name())
        item.setCheckable(True)
        item.setChecked(in_legend(layer))
        item.toggled.connect(partial(_set_layer_shown, designer, layer.id()))

    menu.addSeparator()
    in_map = menu.addAction("Nur Inhalte des Kartenausschnitts")
    in_map.setCheckable(True)
    in_map.setChecked(all(legend.legendFilterByMapEnabled() for legend in legends))
    in_map.setToolTip("Aus: alle Zeichen und Typen der Layer, auch außerhalb der Karte")
    in_map.toggled.connect(partial(_set_filter_by_map, designer))

    titles = menu.addAction("Überschriften (Taktische Zeichen, Fahrzeuge …)")
    titles.setCheckable(True)
    titles.setChecked(titles_shown(layout))
    titles.setToolTip("Überschrift über jedem Layer der Toolbox; Gruppen bleiben ohne Überschrift")
    titles.toggled.connect(partial(_set_titles_shown, designer))

    menu.addSeparator()
    more = menu.addAction("Weitere Einstellungen …")
    more.setToolTip(
        "Eigenschaften der Legende in QGIS (Spalten, Schrift, einzelne Einträge).\n"
        "„Legende aktualisieren“ baut die Einträge danach wieder neu auf."
    )
    more.triggered.connect(lambda: _show_legend_options(designer, legends[0]))


def _add_note(menu: QMenu, text: str) -> None:
    note = menu.addAction(text)
    note.setEnabled(False)


def _set_layer_shown(designer, layer_id: str, show: bool) -> None:
    layer = QgsProject.instance().mapLayer(layer_id)
    if layer is not None:
        set_in_legend(layer, show)
        _refresh_from_designer(designer)


def _set_filter_by_map(designer, enabled: bool) -> None:
    for legend in legends_of(designer.layout()):
        legend.setLegendFilterByMapEnabled(enabled)
    _refresh_from_designer(designer)


def _set_titles_shown(designer, show: bool) -> None:
    set_titles_shown(designer.layout(), show)
    _refresh_from_designer(designer)


def _show_legend_options(designer, legend: QgsLayoutItemLegend) -> None:
    designer.selectItems([legend])
    designer.showItemOptions(legend, True)


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
    svg = svg_symbol_layer(sym)
    svg_size = svg.size() if svg is not None else 0.0
    for symbol_layer in sym.symbolLayers():
        if symbol_layer.layerType() == "SvgMarker":
            scale = 1.0
        else:
            # Hintergrund im selben Verhältnis zum Zeichen wie auf der Karte
            scale = symbol_layer.size() / svg_size if svg_size > 0 else DEFAULT_SCALE
        symbol_layer.setSize(size_mm * scale)
        symbol_layer.setSizeUnit(QgsUnitTypes.RenderUnit.RenderMillimeters)
        symbol_layer.setAngle(0)
        # Ursprungspunkt (z. B. Fußpunkt) würde das Zeichen aus dem Legendenfeld schieben
        symbol_layer.setHorizontalAnchorPoint(QgsMarkerSymbolLayer.HorizontalAnchorPoint.HCenter)
        symbol_layer.setVerticalAnchorPoint(QgsMarkerSymbolLayer.VerticalAnchorPoint.VCenter)
        symbol_layer.setOffset(QPointF())
    return sym
