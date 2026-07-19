"""Installation helper for basemaps and additional layers

Exposes small pure-ish functions the SetupDialog can call. Each basemap is
described as a dataclass; installation writes a permanent QgsSettings
connection (so the source shows up in the QGIS browser) and can optionally
also add a live layer to the current project.
"""

from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import quote

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsLayerTree,
    QgsMapLayer,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsSettings,
    QgsVectorTileLayer,
)
from qgis.gui import QgsBrowserDockWidget
from qgis.PyQt.QtCore import QSettings
from qgis.utils import iface

from ..logging_utils import get_logger

logger = get_logger(__name__)


def _q_settings() -> QgsSettings:
    return QgsSettings()


@dataclass(frozen=True)
class MapLayer:
    key: str
    name: str
    kind: str  # "xyz" | "wms" | "vtile"
    # XYZ: raw URL template like "https://.../{z}/{x}/{y}.png"
    # WMS: GetCapabilities URL + `wms_params` dict used for provider URI
    # VTILE: tile URL + style URL
    url: str
    zmin: int = 0
    zmax: int = 19
    style_url: str = ""
    wms_params: Optional[dict] = None
    description: str = ""
    category: str = "Deutschland"
    default_active: Optional[bool] = False
    default_add_to_project: Optional[bool] = False


_CBM_WORLD = "Allg. Karten Weltweit"
_CBM_AERIAL = "Luftbilder Weltweit"

# ---------------------------------------------------------------------------
# Basemap Definitions
# ---------------------------------------------------------------------------

BASEMAPS: tuple[MapLayer, ...] = (
    MapLayer(
        key="osm",
        name="OpenStreetMap",
        kind="xyz",
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        zmin=0,
        zmax=19,
        description="OpenStreetMap Standard (weltweit)",
        category=_CBM_WORLD,
        default_active=True,
    ),
    MapLayer(
        key="topplus_web",
        name="TopPlusOpen Web (BKG)",
        kind="wms",
        url="https://sgx.geodatenzentrum.de/wms_topplus_open",
        wms_params={"layers": "web", "styles": "", "format": "image/png", "crs": ""},
        description="Amtliche Web-Karte des BKG (WMS)",
        category=_CBM_WORLD,
    ),
    MapLayer(
        key="topplus_grau",
        name="TopPlusOpen Grau (BKG)",
        kind="wms",
        url="https://sgx.geodatenzentrum.de/wms_topplus_open",
        wms_params={"layers": "web_grau", "styles": "", "format": "image/png", "crs": ""},
        description="Graustufen-Variante (gut für Overlays)",
        category=_CBM_WORLD,
    ),
    MapLayer(
        key="basemapde_vektor",
        name="basemap.de Vektor (Farbe)",
        kind="vtile",
        url="https://sgx.geodatenzentrum.de/gdz_basemapde_vektor/tiles/v1/bm_web_vt/{z}/{x}/{y}.pbf",
        style_url="https://sgx.geodatenzentrum.de/gdz_basemapde_vektor/styles/bm_web_col.json",
        zmin=0,
        zmax=15,
        description="Vektorbasiskarte Deutschland (bmd)",
        category=_CBM_WORLD,
    ),
    MapLayer(
        key="basemapde_vektor_grau",
        name="basemap.de Vektor (Grau)",
        kind="vtile",
        url="https://sgx.geodatenzentrum.de/gdz_basemapde_vektor/tiles/v1/bm_web_vt/{z}/{x}/{y}.pbf",
        style_url="https://sgx.geodatenzentrum.de/gdz_basemapde_vektor/styles/bm_web_gry.json",
        zmin=0,
        zmax=15,
        description="Vektorbasiskarte Grau",
        category=_CBM_WORLD,
    ),
    MapLayer(
        key="bkg_dop",
        name="BKG Sentinel-2 Mosaik",
        kind="wms",
        url="https://sgx.geodatenzentrum.de/wms_sen2europe",
        wms_params={"layers": "rgb", "styles": "", "format": "image/png", "crs": ""},
        description="Sentinel-2-Mosaik Europa (BKG, offen)",
        category=_CBM_AERIAL,
    ),
    MapLayer(
        key="esri_world_imagery",
        name="Esri World Imagery",
        kind="xyz",
        url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        zmax=19,
        description="Hochaufgelöste Satellitenbilder (Esri, frei)",
        category=_CBM_AERIAL,
        default_add_to_project=True,
    ),
    MapLayer(
        key="esri_world_topo",
        name="Esri World Topo",
        kind="xyz",
        url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        zmax=19,
        description="Topografische Weltkarte (Esri)",
        category=_CBM_WORLD,
    ),
    MapLayer(
        key="esri_world_street",
        name="Esri World Street",
        kind="xyz",
        url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        zmax=19,
        description="Straßenkarte weltweit (Esri)",
        category=_CBM_WORLD,
    ),
    MapLayer(
        key="cartodb_positron",
        name="CartoDB Positron",
        kind="xyz",
        url="https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
        zmax=20,
        description="Helle, schlichte Basiskarte – gut für Overlays",
        category=_CBM_WORLD,
    ),
    MapLayer(
        key="cartodb_dark",
        name="CartoDB Dark Matter",
        kind="xyz",
        url="https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
        zmax=20,
        description="Dunkle, schlichte Basiskarte",
        category=_CBM_WORLD,
    ),
    MapLayer(
        key="opentopomap",
        name="OpenTopoMap",
        kind="xyz",
        url="https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
        zmax=17,
        description="Topografie mit Höhenlinien (OSM-basiert)",
        category=_CBM_WORLD,
    ),
    MapLayer(
        key="s2cloudless_eox",
        name="Sentinel-2 Cloudless (EOX)",
        kind="wms",
        url="https://tiles.maps.eox.at/wms",
        wms_params={"layers": "s2cloudless-2023", "styles": "", "format": "image/jpeg", "crs": "EPSG:3857"},
        description="Wolkenfreies Sentinel-2-Mosaik (EOX)",
        category=_CBM_AERIAL,
    ),
    MapLayer(
        key="cyclosm",
        name="CyclOSM",
        kind="xyz",
        url="https://a.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png",
        zmax=20,
        description="Radwege-fokussierte OSM-Karte",
        category=_CBM_WORLD,
    ),
)


def basemaps_by_category() -> dict[str, list[MapLayer]]:
    """Gruppiert BASEMAPS nach Kategorie, Reihenfolge stabil."""
    order: list[str] = []
    groups: dict[str, list[MapLayer]] = {}
    for bm in BASEMAPS:
        if bm.category not in groups:
            groups[bm.category] = []
            order.append(bm.category)
        groups[bm.category].append(bm)
    return {cat: groups[cat] for cat in order}


# ---------------------------------------------------------------------------
# Additional Layer Definitions
# ---------------------------------------------------------------------------

_CAL_THEMED = "Fachdaten"
_CAL_AERIAL_STATE = "Luftbilder Länder"
_CAL_DRONE = "Drohne"

ADD_LAYERS: tuple[MapLayer, ...] = (
    MapLayer(
        key="mgrs_grid",
        name="MGRS/UTMRef Gitter",
        kind="wms",
        url="https://geodata.meier-tkn.de/geoserver/ows?version=1.3.0",
        wms_params={
            "layers": "mtkn%3Amgrsgrid",
            "styles": "",
            "format": "image/png",
            "crs": "EPSG:25832",
        },
        description="Fügt beschriftetes UTM-Zonen-Gitter ein",
        category=_CAL_THEMED,
        default_active=True,
    ),
    MapLayer(
        key="osminfra",
        name="OpenInfrastructureMap",
        kind="vtile",
        url="https://openinframap.org/tiles/{z}/{x}/{y}.pbf",
        zmin=0,
        zmax=17,
        description="OpenInfraMap Overlay hebt Infrastrukturobjecte aus der OSM Datenbank hervor.",
        category=_CAL_THEMED,
    ),
    MapLayer(
        key="bfn_schutzgebiete",
        name="Schutzgebiete (BfN)",
        kind="wms",
        url="https://geodienste.bfn.de/ogc/wms/schutzgebiet",
        wms_params={"layers": "Naturschutzgebiete", "styles": "", "format": "image/png", "crs": ""},
        description="INSPIRE-Schutzgebiete (Bundesamt für Naturschutz)",
        category=_CAL_THEMED,
    ),
    MapLayer(
        key="openrailwaymap",
        name="OpenRailwayMap",
        kind="xyz",
        url="https://a.tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png",
        zmax=19,
        description="Eisenbahn-Infrastruktur (OSM-basiert, Overlay)",
        category=_CAL_THEMED,
    ),
    MapLayer(
        key="openseamap",
        name="OpenSeaMap (Overlay)",
        kind="xyz",
        url="https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png",
        zmax=18,
        description="Seezeichen als Overlay – mit OSM darunter kombinieren",
        category=_CAL_THEMED,
    ),
    MapLayer(
        key="waymarked_hiking",
        name="Waymarked Trails – Wandern",
        kind="xyz",
        url="https://tile.waymarkedtrails.org/hiking/{z}/{x}/{y}.png",
        zmax=18,
        description="Wanderwege (Overlay)",
        category=_CAL_THEMED,
    ),
    MapLayer(
        key="waymarked_cycling",
        name="Waymarked Trails – Rad",
        kind="xyz",
        url="https://tile.waymarkedtrails.org/cycling/{z}/{x}/{y}.png",
        zmax=18,
        description="Radrouten (Overlay)",
        category=_CAL_THEMED,
    ),
    MapLayer(
        key="by_dop",
        name="Bayern – DOP20 (Luftbild)",
        kind="wms",
        url="https://geoservices.bayern.de/od/wms/dop/v1/dop40",
        wms_params={"layers": "by_dop40c", "styles": "", "format": "image/png", "crs": ""},
        description="Digitale Orthophotos Bayern (LDBV, Open Data)",
        category=_CAL_AERIAL_STATE,
    ),
    MapLayer(
        key="nw_dop",
        name="NRW – DOP (Luftbild)",
        kind="wms",
        url="https://www.wms.nrw.de/geobasis/wms_nw_dop",
        wms_params={"layers": "nw_dop_rgb", "styles": "", "format": "image/png", "crs": ""},
        description="Digitale Orthophotos NRW (tim-online)",
        category=_CAL_AERIAL_STATE,
    ),
    MapLayer(
        key="nw_dtk",
        name="NRW – DTK",
        kind="wms",
        url="https://www.wms.nrw.de/geobasis/wms_nw_dtk",
        wms_params={"layers": "nw_dtk_col", "styles": "", "format": "image/png", "crs": ""},
        description="Topografische Karte NRW, farbig (tim-online)",
        category=_CAL_AERIAL_STATE,
    ),
    MapLayer(
        key="bw_dop",
        name="BW – DOP (CIR-Luftbild)",
        kind="wms",
        url="https://owsproxy.lgl-bw.de/owsproxy/ows/WMS_LGL-BW_ATKIS_DOP_20_CIR",
        wms_params={"layers": "IMAGES_DOP_20_CIR", "styles": "", "format": "image/png", "crs": ""},
        description="Digitales Orthophoto BW, Color-Infrared (LGL)",
        category=_CAL_AERIAL_STATE,
    ),
    MapLayer(
        key="ni_dop",
        name="Niedersachsen – DOP",
        kind="wms",
        url="https://opendata.lgln.niedersachsen.de/doorman/noauth/dop_wms",
        wms_params={"layers": "ni_dop20", "styles": "", "format": "image/png", "crs": ""},
        description="Orthophotos Niedersachsen (LGLN, Open Data)",
        category=_CAL_AERIAL_STATE,
    ),
    MapLayer(
        key="sn_dop",
        name="Sachsen – DOP",
        kind="wms",
        url="https://geodienste.sachsen.de/wms_geosn_dop-rgb/guest",
        wms_params={"layers": "sn_dop_020", "styles": "", "format": "image/png", "crs": ""},
        description="Digitale Orthophotos Sachsen (GeoSN)",
        category=_CAL_AERIAL_STATE,
    ),
    MapLayer(
        key="sn_webatlas",
        name="Sachsen – Webatlas",
        kind="wms",
        url="https://geodienste.sachsen.de/wms_geosn_webatlas-sn/guest",
        wms_params={
            "layers": "Vegetation,Siedlung,Gewaesser,Verkehr,Administrative_Einheiten,Beschriftung",
            "styles": "",
            "format": "image/png",
            "crs": "",
        },
        description="Topografischer Webatlas Sachsen (Komposit)",
        category=_CAL_AERIAL_STATE,
    ),
    MapLayer(
        key="he_dop",
        name="Hessen – DOP",
        kind="wms",
        url="https://www.gds-srv.hessen.de/cgi-bin/lika-services/ogc-free-images.ows",
        wms_params={"layers": "he_dop20_rgb", "styles": "", "format": "image/png", "crs": ""},
        description="Orthophotos Hessen 20cm RGB (HVBG, Open Data)",
        category=_CAL_AERIAL_STATE,
    ),
    MapLayer(
        key="dipul_alle_luftrecht",
        name="DIPUL – Luftrechtliche Gebiete",
        kind="wms",
        url="https://uas-betrieb.de/geoservices/dipul/wms",
        wms_params={
            "layers": "flugbeschraenkungsgebiete,kontrollzonen,temporaere_betriebseinschraenkungen,flughaefen,flugplaetze,modellflugplaetze,haengegleiter",
            "styles": "",
            "format": "image/png",
            "crs": "EPSG:3857",
        },
        description="Flugbeschränkungsgebiete, Kontrollzonen, temporäre Einschränkungen, Flughäfen/-plätze (kombinierter Layer)",
        category=_CAL_DRONE,
    ),
    MapLayer(
        key="dipul_naturschutz",
        name="DIPUL – Naturschutz",
        kind="wms",
        url="https://uas-betrieb.de/geoservices/dipul/wms",
        wms_params={
            "layers": "naturschutzgebiete,nationalparks,ffh-gebiete,vogelschutzgebiete",
            "styles": "",
            "format": "image/png",
            "crs": "EPSG:3857",
        },
        description="Naturschutz-, FFH- und Vogelschutzgebiete, Nationalparks",
        category=_CAL_DRONE,
    ),
    MapLayer(
        key="dipul_sensible_objekte",
        name="DIPUL – Sensible Objekte",
        kind="wms",
        url="https://uas-betrieb.de/geoservices/dipul/wms",
        wms_params={
            "layers": "krankenhaeuser,polizei,sicherheitsbehoerden,justizvollzugsanstalten,militaerische_anlagen,behoerden,diplomatische_vertretungen,internationale_organisationen",
            "styles": "",
            "format": "image/png",
            "crs": "EPSG:3857",
        },
        description="Krankenhäuser, Polizei, JVA, Militär, Behörden, Diplomatie",
        category=_CAL_DRONE,
    ),
    MapLayer(
        key="dipul_infrastruktur",
        name="DIPUL – Infrastruktur",
        kind="wms",
        url="https://uas-betrieb.de/geoservices/dipul/wms",
        wms_params={
            "layers": "bundesautobahnen,bundesstrassen,bahnanlagen,binnenwasserstrassen,seewasserstrassen,kraftwerke,stromleitungen,umspannwerke,industrieanlagen,windkraftanlagen",
            "styles": "",
            "format": "image/png",
            "crs": "EPSG:3857",
        },
        description="Verkehrswege, Energieversorgung, Industrieanlagen",
        category=_CAL_DRONE,
    ),
)


def additional_layers_by_category() -> dict[str, list[MapLayer]]:
    """Gruppiert ADD_LAYERS nach Kategorie, Reihenfolge stabil."""
    order: list[str] = []
    groups: dict[str, list[MapLayer]] = {}
    for al in ADD_LAYERS:
        if al.category not in groups:
            groups[al.category] = []
            order.append(al.category)
        groups[al.category].append(al)
    return {cat: groups[cat] for cat in order}


# ---------------------------------------------------------------------------
# Maps Helper Function
# ---------------------------------------------------------------------------


def qgis_connection_exists(basemap: MapLayer) -> bool:
    """Checks if the layer was already added to the QGIS browser."""
    s = _q_settings()

    if basemap.kind == "xyz":
        group_path = "connections/xyz/items"
    elif basemap.kind == "wms":
        group_path = "connections/wms"
    elif basemap.kind == "vtile":
        group_path = "connections/vector-tile"
    else:
        return False

    s.beginGroup(group_path)
    connection_groups = s.childGroups()
    s.endGroup()

    return basemap.name in connection_groups


def exists_in_project(basemap: MapLayer) -> bool:
    """Checks if this layer is already somewhere in the project tree"""
    project = QgsProject.instance()
    root = project.layerTreeRoot()

    # Walk through all groups and layers in the tree
    def check_node(node):
        if QgsLayerTree.isLayer(node):
            layer = node.layer()
            if layer.name() == basemap.name:
                return True
        elif QgsLayerTree.isGroup(node):
            for child in node.children():
                if check_node(child):
                    return True
        return False

    return check_node(root)


def is_visible_in_project(basemap: MapLayer) -> bool:
    """Returns wheter the given map is visible in the LayerTree"""
    project = QgsProject.instance()
    root = project.layerTreeRoot()

    # Find the layer by name
    layer_to_find = None
    for layer in project.mapLayers().values():
        if layer.name() == basemap.name:
            layer_to_find = layer
            break

    if layer_to_find is None:
        logger.debug("Layer %s not found in project", basemap.name)
        return False

    # Find the layer node in the tree and check if it's visible
    layer_node = root.findLayer(layer_to_find)
    if layer_node is not None:
        return layer_node.isVisible()

    return False


def set_visibility_in_project(basemap: MapLayer, visible: bool):
    """Sets the visibility in the project as provided."""
    project = QgsProject.instance()
    root = project.layerTreeRoot()

    # Find the layer by name
    layer_to_find = None
    for layer in project.mapLayers().values():
        if layer.name() == basemap.name:
            layer_to_find = layer
            break

    if layer_to_find is None:
        return
    # Find the layer node in the tree and check if it's visible
    layer_node = root.findLayer(layer_to_find)
    if layer_node is not None:
        layer_node.setItemVisibilityChecked(visible)


def remove_layer_from_project(basemap: MapLayer):
    """Removes the layer from the current project tree if it exists."""
    print(f"Removal Process Called for {basemap.name}")
    project = QgsProject.instance()
    root = project.layerTreeRoot()

    # Find the layer by name
    layer_to_remove = None
    for layer in project.mapLayers().values():
        if layer.name() == basemap.name:
            layer_to_remove = layer
            break

    if layer_to_remove is None:
        logger.debug("Layer %s not found in project", basemap.name)
        return False

    # Find and remove from tree group
    layer_node = root.findLayer(layer_to_remove)
    if layer_node is not None:
        layer_node.parent().removeChildNode(layer_node)

    # Remove from project
    project.removeMapLayer(layer_to_remove.id())


def remove_from_qgis(basemap: MapLayer) -> bool:
    """Removes the layer from the QGIS browser if it exists."""
    s = _q_settings()

    if basemap.kind == "xyz":
        group_path = "connections/xyz/items"
    elif basemap.kind == "wms":
        group_path = "connections/wms"
    elif basemap.kind == "vtile":
        group_path = "connections/vector-tile"
    else:
        return False

    s.beginGroup(group_path)
    connection_groups = s.childGroups()
    s.endGroup()

    if basemap.name not in connection_groups:
        return False

    s.beginGroup(group_path)
    s.beginGroup(basemap.name)
    s.remove("")
    s.endGroup()
    s.endGroup()

    reload_browser()
    return True


def reload_browser() -> None:
    """Bittet QGIS, die Browser-Connections neu einzulesen, damit neue
    Einträge sofort in der Browser-Ansicht erscheinen."""
    try:
        if iface is not None:
            # Find the browser dock widget and call refresh on it
            main_window = iface.mainWindow()
            if main_window is not None:
                browser_widgets = main_window.findChildren(QgsBrowserDockWidget)
                for browser_widget in browser_widgets:
                    if browser_widget is not None:
                        # Try multiple refresh methods
                        browser_widget.refresh()
                        break
    except Exception as e:
        logger.debug("Browser refresh failed: %s", e)


def install_qgis_connection(basemap: MapLayer) -> None:
    """Stellt eine projektübergreifende Verbindung im QGIS-Browser her."""
    s = _q_settings()

    if basemap.kind == "xyz":
        base = f"connections/xyz/items/{basemap.name}"
        s.setValue(f"{base}/url", basemap.url)
        s.setValue(f"{base}/zmin", basemap.zmin)
        s.setValue(f"{base}/zmax", basemap.zmax)
        s.setValue(f"{base}/authcfg", "")
        s.setValue(f"{base}/username", "")
        s.setValue(f"{base}/password", "")
        s.setValue(f"{base}/referer", "")
        s.setValue(f"{base}/tile-pixel-ratio", 1)
    elif basemap.kind == "wms":
        base = f"connections/wms/{basemap.name}"
        s.setValue(f"{base}/url", basemap.url)
        s.setValue(f"{base}/ignoreAxisOrientation", False)
        s.setValue(f"{base}/invertAxisOrientation", False)
        s.setValue(f"{base}/ignoreGetFeatureInfoURI", False)
        s.setValue(f"{base}/smoothPixmapTransform", False)
        s.setValue(f"{base}/dpiMode", 7)
    elif basemap.kind == "vtile":
        base = f"connections/vector-tile/{basemap.name}"
        s.setValue(f"{base}/url", basemap.url)
        s.setValue(f"{base}/zmin", basemap.zmin)
        s.setValue(f"{base}/zmax", basemap.zmax)
        s.setValue(f"{base}/styleUrl", basemap.style_url)
        s.setValue(f"{base}/serviceType", "")
        s.setValue(f"{base}/authcfg", "")
        s.setValue(f"{base}/username", "")
        s.setValue(f"{base}/password", "")
        s.setValue(f"{base}/referer", "")

    reload_browser()


def _build_xyz_uri(map_layer: MapLayer) -> str:
    encoded = quote(map_layer.url, safe="")
    return f"type=xyz&url={encoded}&zmin={map_layer.zmin}&zmax={map_layer.zmax}"


def _build_wms_uri(map_layer: MapLayer) -> str:
    params = map_layer.wms_params or {}
    encoded_url = quote(map_layer.url, safe="")
    raw_layers = params.get("layers", "")
    layers = [name.strip() for name in raw_layers.split(",") if name.strip()]
    style = params.get("styles", "")

    parts = [
        "contextualWMSLegend=0",
        f"crs={params.get('crs', 'EPSG:3857')}",
        "dpiMode=7",
        "featureCount=10",
        f"format={params.get('format', 'image/png')}",
    ]
    # Für N Layer braucht der WMS-Provider N `layers=`- und N `styles=`-Einträge.
    for layer in layers:
        parts.append(f"layers={layer}")
        parts.append(f"styles={style}")
    parts.append(f"url={encoded_url}")
    return "&".join(parts)


def _build_vtile_uri(map_layer: MapLayer) -> str:
    encoded_url = quote(map_layer.url, safe="")
    encoded_style = quote(map_layer.style_url, safe="")
    return f"type=xyz&url={encoded_url}&zmin={map_layer.zmin}&zmax={map_layer.zmax}&styleUrl={encoded_style}"


def create_map_layer(map_layer: MapLayer) -> QgsMapLayer:
    """Adds the basemap to the current project"""
    if map_layer.kind == "xyz":
        return QgsRasterLayer(_build_xyz_uri(map_layer), map_layer.name, "wms")
    if map_layer.kind == "wms":
        return QgsRasterLayer(_build_wms_uri(map_layer), map_layer.name, "wms")
    if map_layer.kind == "vtile":
        return QgsVectorTileLayer(_build_vtile_uri(map_layer), map_layer.name)
    return None


GROUP_NAME_BASEMAPS = "Hintergrundkarten"


def add_basemap_to_project(bm: MapLayer, visible: bool = False):
    """Adds the basemap as a layer to the current project.
    Only the layer passed with visible=True will be visible."""
    project = QgsProject.instance()
    root = project.layerTreeRoot()

    layer = create_map_layer(bm)
    if layer is None:
        logger.debug("Basemap layer could not be created: %s", bm.name)
        return

    project.addMapLayer(layer, False)

    bm_group = root.findGroup(GROUP_NAME_BASEMAPS)
    if bm_group is None:
        bm_group = root.addGroup(GROUP_NAME_BASEMAPS)

    group = bm_group.findGroup(bm.category)
    if group is None:
        group = bm_group.addGroup(bm.category)

    group.addLayer(layer)

    node = root.findLayer(layer.id())
    if node is not None:
        node.setItemVisibilityChecked(visible)
        if visible:
            node.setItemVisibilityCheckedParentRecursive(True)
    logger.debug("Basemap layer %s added", bm.name)


GROUP_NAME_ADD_LAYERS = "Zusatzkarten"


def add_layer_to_project(map_layer: MapLayer, visible: bool = False):
    """Adds the map layer to the current project. Only the layer passed with visible=True will be visible."""
    project = QgsProject.instance()
    root = project.layerTreeRoot()

    layer = create_map_layer(map_layer)
    if layer is None:
        logger.debug("Additional layer could not be created: %s", map_layer.name)
        return

    project.addMapLayer(layer, False)

    add_layer_group = root.findGroup(GROUP_NAME_ADD_LAYERS)
    if add_layer_group is None:
        add_layer_group = root.insertGroup(0, GROUP_NAME_ADD_LAYERS)

    group = add_layer_group.findGroup(map_layer.category)
    if group is None:
        group = add_layer_group.addGroup(map_layer.category)

    group.addLayer(layer)

    node = root.findLayer(layer.id())
    if node is not None:
        node.setItemVisibilityChecked(visible)
        if visible:
            node.setItemVisibilityCheckedParentRecursive(True)
    logger.debug("Additional layer %s added", map_layer.name)


# ---------------------------------------------------------------------------
# Set CRS and Zoom Helper Functions
# ---------------------------------------------------------------------------


def set_project_crs(new_crs: int):
    """Sets Coordinate Reference System (CRS) to the provided EPSG ID."""
    crs = QgsCoordinateReferenceSystem.fromEpsgId(new_crs)
    if not crs.isValid():
        logger.error("Invalid CRS for EPSG:%d", new_crs)
    QgsProject.instance().setCrs(crs)


def get_project_crs() -> int:
    """Gets the current Coordinate Reference System (CRS) of the project and returns the EPSG ID."""
    crs = QgsProject.instance().crs()
    authid = crs.authid()  # Returns something like "EPSG:25832"
    return int(authid.split(":")[1])


# Geographische Bounding Box Deutschland (WGS84): ~5.8°E–15.1°E, 47.2°N–55.1°N
GERMANY_BBOX_WGS84 = QgsRectangle(5.8, 47.2, 15.1, 55.1)


def zoom_to_germany():
    """Zoomt die Kartenansicht auf Deutschland (transformiert in das Projekt-CRS)."""
    try:
        from qgis.utils import iface

        if iface is None:
            return
        canvas = iface.mapCanvas()
        project = QgsProject.instance()
        src = QgsCoordinateReferenceSystem("EPSG:4326")
        dst = project.crs()
        transform = QgsCoordinateTransform(src, dst, project)
        extent = transform.transformBoundingBox(GERMANY_BBOX_WGS84)
        canvas.setExtent(extent)
        canvas.refresh()
    except Exception as e:
        logger.error("Error while zooming in: %s", e)
