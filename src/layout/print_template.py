"""Laden von Druckvorlagen (.qpt) mit Ortsverband-/Einsatzdaten, Gitter und aufgeräumter Legende.

Mitgeliefert werden im Ordner `templates/` die Vorlagen der THW-Leitung
(`THW_*`, unverändert, damit neue Versionen einfach ersetzt werden können) und
die der Toolbox (`Toolbox_*`, erzeugt mit `scripts/build_print_templates.py`).
Eigene Vorlagen liegen im QGIS-Profil. Alle Anpassungen passieren erst nach dem
Laden am fertigen Layout:

- Platzhalter in Textfeldern (z. B. "Ortsverband XXXXXX") werden durch
  Layout-Variablen (`@thw_ov`, ...) ersetzt. Die Werte lassen sich danach in den
  Layout-Eigenschaften unter "Variablen" ändern.
- Relative Bildpfade (`./GRAFIKEN/LAYOUT/...`) werden neben der Vorlage bzw. in
  `templates/assets/` gesucht, falls das Projekt die Grafiken nicht mitbringt.
- Trägt die Karte `Hauptkarte` die Gitter `UTMREF` / `LONLAT`, wird das gewählte
  eingeschaltet; die Karte übernimmt den aktuellen Ausschnitt in dessen UTM-Zone.
- Übersichtskarten zeigen nur die Hintergrundkarte.
- Das taktische Zeichen der Einheit (`Taktisches Zeichen Einheit`) lässt sich abschalten.
- Die Legende wird auf das Nötige reduziert (siehe `legend.py`).
"""

import json
import math
import os
import re
import shutil
from dataclasses import dataclass

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsExpressionContextUtils,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemPicture,
    QgsPrintLayout,
    QgsProject,
    QgsReadWriteContext,
    QgsSettings,
    QgsVectorLayer,
)
from qgis.PyQt.QtXml import QDomDocument

from ..logging_utils import get_logger
from ..tools.layer_setup import GROUP_NAME_BASEMAPS
from .legend import apply_legend

logger = get_logger(__name__)

_SETTINGS_PREFIX = "THWToolbox/print/"
_PROJECT_SCOPE = "THWToolbox"
_PROJECT_EINSATZ_KEY = "print_einsatz"
_PROJECT_KARTENTITEL_KEY = "print_kartentitel"
_PROJECT_EINSATZORT_KEY = "print_einsatzort"

# Gängige Kartentitel: der Titel sagt, was die Karte zeigt
KARTENTITEL_VORSCHLAEGE = (
    "Lagekarte",
    "Übersichtskarte",
    "Einsatzabschnitte",
    "Anfahrt und Bereitstellungsraum",
    "Schadensgebiet",
    "Luftbild",
)

# Gitter der Toolbox-Vorlagen; der Schlüssel ist der Name des Gitters an der Hauptkarte
GITTER_UTMREF = "UTMREF"
GITTER_LONLAT = "LONLAT"
GITTER_NAMEN = {GITTER_UTMREF: "UTMREF", GITTER_LONLAT: "Lon/Lat (Dezimalgrad)"}

# Gruppen im Druckvorlagen-Dialog; mitgelieferte Vorlagen ordnet ihr Dateipräfix zu
GROUP_TOOLBOX = "THW Toolbox"
GROUP_THW = "THW-Leitung"
GROUP_OWN = "Eigene Vorlagen"
_BUNDLED_GROUPS = {"Toolbox": GROUP_TOOLBOX, "THW": GROUP_THW}
_GROUP_ORDER = (GROUP_TOOLBOX, GROUP_THW, GROUP_OWN)
_NAME_PARTS = {"QUER": "quer", "HOCH": "hoch"}

# Element-IDs aus den Vorlagen
_VSNFD_ID = "VS-NfD"
_MAIN_MAP_ID = "Hauptkarte"
_BUNDESLOGO_ID = "Logo Bundesadler"
_UNIT_SIGN_ID = "Taktisches Zeichen Einheit"

_STANDARD_SCALES = (500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 200000, 250000, 500000, 1000000)

# (Muster, Ersetzung) für Textfelder der Vorlage
_LABEL_REPLACEMENTS = (
    (re.compile(r"Ortsverband\s+X+"), "Ortsverband [%@thw_ov%]"),
    (re.compile(r"Trupp\s+unbemannte\s+Luftfahrtsysteme\s+\(Tr\s*UL\)"), "[%@thw_einheit%]"),
    (re.compile(r"TrUL\s+X+"), "[%@thw_einheit_kurz%] [%@thw_ov%]"),
    (re.compile(re.escape("[%@project_title%]")), "[%@thw_einsatz%]"),
    (re.compile(re.escape("[%@project_author%]")), "[%@thw_bearbeiter%]"),
)


@dataclass
class PrintInfo:
    ortsverband: str = ""
    einheit: str = "Trupp unbemannte Luftfahrtsysteme (Tr UL)"
    einheit_kurz: str = "TrUL"
    einheit_zeichen: bool = True
    bearbeiter: str = ""
    einsatz: str = ""
    kartentitel: str = KARTENTITEL_VORSCHLAEGE[0]
    einsatzort: str = ""
    blatt: str = ""
    gitter: str = GITTER_UTMREF
    tidy_legend: bool = True

    @classmethod
    def load(cls) -> "PrintInfo":
        """OV/Einheit/Bearbeiter aus den Benutzereinstellungen, Einsatz aus dem Projekt."""
        s = QgsSettings()
        d = cls()
        project = QgsProject.instance()

        einsatz, ok = project.readEntry(_PROJECT_SCOPE, _PROJECT_EINSATZ_KEY, "")
        if not ok or not einsatz:
            einsatz = project.title() or project.baseName()
        kartentitel, _ = project.readEntry(_PROJECT_SCOPE, _PROJECT_KARTENTITEL_KEY, d.kartentitel)
        einsatzort, _ = project.readEntry(_PROJECT_SCOPE, _PROJECT_EINSATZORT_KEY, "")
        gitter = s.value(_SETTINGS_PREFIX + "gitter", d.gitter)

        return cls(
            ortsverband=s.value(_SETTINGS_PREFIX + "ortsverband", d.ortsverband),
            einheit=s.value(_SETTINGS_PREFIX + "einheit", d.einheit),
            einheit_kurz=s.value(_SETTINGS_PREFIX + "einheit_kurz", d.einheit_kurz),
            einheit_zeichen=s.value(_SETTINGS_PREFIX + "einheit_zeichen", d.einheit_zeichen, type=bool),
            bearbeiter=s.value(_SETTINGS_PREFIX + "bearbeiter", "") or project.metadata().author(),
            einsatz=einsatz,
            kartentitel=kartentitel,
            einsatzort=einsatzort,
            gitter=gitter if gitter in GITTER_NAMEN else d.gitter,
            tidy_legend=s.value(_SETTINGS_PREFIX + "tidy_legend", d.tidy_legend, type=bool),
        )

    def save(self) -> None:
        s = QgsSettings()
        s.setValue(_SETTINGS_PREFIX + "ortsverband", self.ortsverband)
        s.setValue(_SETTINGS_PREFIX + "einheit", self.einheit)
        s.setValue(_SETTINGS_PREFIX + "einheit_kurz", self.einheit_kurz)
        s.setValue(_SETTINGS_PREFIX + "einheit_zeichen", self.einheit_zeichen)
        s.setValue(_SETTINGS_PREFIX + "bearbeiter", self.bearbeiter)
        s.setValue(_SETTINGS_PREFIX + "gitter", self.gitter)
        s.setValue(_SETTINGS_PREFIX + "tidy_legend", self.tidy_legend)
        project = QgsProject.instance()
        project.writeEntry(_PROJECT_SCOPE, _PROJECT_EINSATZ_KEY, self.einsatz)
        project.writeEntry(_PROJECT_SCOPE, _PROJECT_KARTENTITEL_KEY, self.kartentitel)
        project.writeEntry(_PROJECT_SCOPE, _PROJECT_EINSATZORT_KEY, self.einsatzort)


def ortsverband_names(plugin_dir: str) -> list[str]:
    """OV-Namen ohne Präfix ("Aachen", ...) aus `data/ovs.json`, für die Autovervollständigung."""
    path = os.path.join(plugin_dir, "data", "ovs.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            features = json.load(f).get("features", [])
    except (OSError, ValueError) as e:
        logger.warning("Ortsverbände konnten nicht geladen werden: %s", e)
        return []

    prefix = "Ortsverband "
    names = {
        title[len(prefix) :].strip()
        for feat in features
        if (title := (feat.get("properties") or {}).get("title") or "").startswith(prefix)
    }
    return sorted(names, key=str.casefold)


@dataclass
class TemplateEntry:
    group: str
    name: str
    path: str

    @property
    def is_own(self) -> bool:
        return self.group == GROUP_OWN

    @property
    def layout_name(self) -> str:
        return self.name if self.is_own else f"{self.group} {self.name}"


def user_templates_dir() -> str:
    """Ordner der eigenen Vorlagen im QGIS-Profil, damit sie Plugin-Updates überstehen."""
    return os.path.normpath(os.path.join(QgsApplication.qgisSettingsDirPath(), "thw_toolbox", "druckvorlagen"))


def user_template_path(source: str) -> str:
    """Wohin `add_user_template` die Datei `source` kopiert."""
    return os.path.join(user_templates_dir(), os.path.basename(source))


def add_user_template(source: str) -> str:
    """Kopiert `source` zu den eigenen Vorlagen und gibt den neuen Pfad zurück. Wirft `OSError`."""
    target = user_template_path(source)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.abspath(source) != os.path.abspath(target):
        shutil.copyfile(source, target)
    return target


def list_templates(plugin_dir: str) -> list[TemplateEntry]:
    """Mitgelieferte und eigene Vorlagen, nach Gruppe und Papierformat (A4 zuerst) sortiert."""
    entries = []
    for path in _qpt_files(os.path.join(plugin_dir, "templates")):
        stem = os.path.splitext(os.path.basename(path))[0]
        prefix, _, rest = stem.partition("_")
        if prefix in _BUNDLED_GROUPS and rest:
            entries.append(TemplateEntry(_BUNDLED_GROUPS[prefix], _display_name(rest), path))
        else:
            entries.append(TemplateEntry(GROUP_TOOLBOX, _display_name(stem), path))
    for path in _qpt_files(user_templates_dir()):
        entries.append(TemplateEntry(GROUP_OWN, os.path.splitext(os.path.basename(path))[0], path))

    def sort_key(entry: TemplateEntry):
        paper = re.match(r"A(\d)\b", entry.name)
        return (_GROUP_ORDER.index(entry.group), -int(paper.group(1)) if paper else 1, entry.name.casefold())

    return sorted(entries, key=sort_key)


def _qpt_files(folder: str) -> list[str]:
    if not os.path.isdir(folder):
        return []
    return [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".qpt")]


def _display_name(stem: str) -> str:
    """Dateiname ohne Präfix als Anzeigename: "A3_QUER_2xA4" wird zu "A3 quer (2 × A4)"."""
    parts = []
    for part in stem.split("_"):
        tiles = re.fullmatch(r"(\d+)x(A\d)", part)
        parts.append(f"({tiles.group(1)} × {tiles.group(2)})" if tiles else _NAME_PARTS.get(part, part))
    return " ".join(parts)


def load_print_template(path: str, info: PrintInfo, assets_dir: str, canvas=None) -> QgsPrintLayout:
    """Lädt `path` als neues Layout und wendet `info` an. Wirft `ValueError` bei Fehlern.

    Mit `canvas` übernehmen Vorlagen mit UTMREF-/LONLAT-Gitter dessen Kartenausschnitt.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        raise ValueError(f"Vorlage konnte nicht gelesen werden:\n{e}") from e

    doc = QDomDocument()
    if not doc.setContent(content):
        raise ValueError("Vorlage ist keine gültige XML-Datei.")

    project = QgsProject.instance()
    layout = QgsPrintLayout(project)
    ok, _ = layout.loadFromTemplate(doc, QgsReadWriteContext())
    if not ok:
        raise ValueError("Vorlage konnte nicht geladen werden.")

    _apply_variables(layout, info)
    _remove_vsnfd(layout)
    _apply_unit_sign(layout, info.einheit_zeichen)
    _fix_picture_paths(layout, [os.path.dirname(path), assets_dir])
    _apply_grid(layout, info.gitter)
    if canvas is not None:
        _apply_canvas_view(layout, canvas)
    _apply_overview_layers(layout)
    if info.tidy_legend:
        for legend in _items_of_type(layout, QgsLayoutItemLegend):
            apply_legend(legend)

    layout.refresh()
    return layout


def _items_of_type(layout: QgsPrintLayout, cls):
    return [item for item in layout.items() if isinstance(item, cls)]


def bundeslogo_items(layout: QgsPrintLayout) -> list:
    """Bildelemente mit dem Bundeslogo (Bundesadler), dessen Verwendung eine Berechtigung voraussetzt."""
    return [p for p in _items_of_type(layout, QgsLayoutItemPicture) if p.id() == _BUNDESLOGO_ID]


def is_logo_confirmed() -> bool:
    return QgsSettings().value(_SETTINGS_PREFIX + "logo_confirmed", False, type=bool)


def set_logo_confirmed(confirmed: bool) -> None:
    QgsSettings().setValue(_SETTINGS_PREFIX + "logo_confirmed", confirmed)


def _apply_variables(layout: QgsPrintLayout, info: PrintInfo) -> None:
    variables = {
        "thw_ov": info.ortsverband,
        "thw_einheit": info.einheit,
        "thw_einheit_kurz": info.einheit_kurz,
        "thw_bearbeiter": info.bearbeiter,
        "thw_einsatz": info.einsatz,
        "thw_kartentitel": info.kartentitel,
        "thw_einsatzort": info.einsatzort,
        "thw_blatt": info.blatt,
        "thw_gitter": info.gitter,
    }
    for name, value in variables.items():
        QgsExpressionContextUtils.setLayoutVariable(layout, name, value)

    for label in _items_of_type(layout, QgsLayoutItemLabel):
        text = label.text()
        new_text = text
        for pattern, replacement in _LABEL_REPLACEMENTS:
            new_text = pattern.sub(replacement, new_text)
        if new_text != text:
            label.setText(new_text)


def _remove_vsnfd(layout: QgsPrintLayout) -> None:
    """Die ausgeblendete VS-NfD-Kennzeichnung der THW-Vorlagen entfernen.

    Eingestufte Inhalte dürfen mit QGIS nicht verarbeitet werden, die Kennzeichnung
    soll sich deshalb auch im Designer nicht einschalten lassen.
    """
    for item in layout.items():
        if isinstance(item, QgsLayoutItem) and item.id() == _VSNFD_ID:
            layout.removeLayoutItem(item)


def _apply_unit_sign(layout: QgsPrintLayout, show: bool) -> None:
    """Taktisches Zeichen der Einheit im Kartenkopf zeigen oder ausblenden."""
    for picture in _items_of_type(layout, QgsLayoutItemPicture):
        if picture.id() == _UNIT_SIGN_ID:
            picture.setVisibility(show)


def _template_grids(map_item: QgsLayoutItemMap) -> dict:
    """Die Gitter der Toolbox-Vorlagen an `map_item`, nach Namen."""
    return {g.name(): g for g in map_item.grids().asList() if g.name() in GITTER_NAMEN}


def _apply_grid(layout: QgsPrintLayout, gitter: str) -> None:
    """Das gewählte Gitter einschalten, das andere aus. Vorlagen ohne dieses Gitter bleiben unberührt."""
    for map_item in _items_of_type(layout, QgsLayoutItemMap):
        grids = _template_grids(map_item)
        if gitter not in grids:
            continue
        for name, grid in grids.items():
            grid.setEnabled(name == gitter)


def _utm_crs(extent, crs) -> QgsCoordinateReferenceSystem:
    """ETRS89/UTM-Zone der Ausschnittsmitte, außerhalb Europas WGS 84/UTM."""
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    center = QgsCoordinateTransform(crs, wgs84, QgsProject.instance()).transform(extent.center())
    zone = min(60, max(1, math.floor((center.x() + 180) / 6) + 1))
    if 28 <= zone <= 38 and center.y() >= 0:
        return QgsCoordinateReferenceSystem(f"EPSG:{25800 + zone}")
    return QgsCoordinateReferenceSystem(f"EPSG:{(32600 if center.y() >= 0 else 32700) + zone}")


def _apply_canvas_view(layout: QgsPrintLayout, canvas) -> None:
    """Kartenausschnitt des Hauptfensters in die Hauptkarte übernehmen, in dessen UTM-Zone.

    Die Karte ist auch mit Lon/Lat-Gitter UTM-projiziert, damit der Maßstab stimmt.
    """
    maps = _items_of_type(layout, QgsLayoutItemMap)
    main_map = next((m for m in maps if m.id() == _MAIN_MAP_ID), None)
    if main_map is None:
        return
    grids = _template_grids(main_map)
    if not grids:
        return

    canvas_crs = canvas.mapSettings().destinationCrs()
    try:
        utm = _utm_crs(canvas.extent(), canvas_crs)
        extent = QgsCoordinateTransform(canvas_crs, utm, QgsProject.instance()).transformBoundingBox(canvas.extent())
    except Exception as e:  # QgsCsException bei Ausschnitten außerhalb des Gültigkeitsbereichs
        logger.warning("Kartenausschnitt konnte nicht übernommen werden: %s", e)
        return

    for map_item in maps:
        map_item.setCrs(utm)
    if GITTER_UTMREF in grids:
        grids[GITTER_UTMREF].setCrs(utm)

    main_map.zoomToExtent(extent)
    # Auf einen gängigen Kartenmaßstab aufrunden, damit die Maschenweite des Gitters passt
    scale = next((s for s in _STANDARD_SCALES if s >= main_map.scale()), None)
    if scale:
        main_map.setScale(scale)


def _basemap_layers() -> list:
    """Eingeschaltete Hintergrundkarten: die der Gruppe aus dem Setup, sonst alle Raster- und Kachellayer."""
    root = QgsProject.instance().layerTreeRoot()
    group = root.findGroup(GROUP_NAME_BASEMAPS)
    nodes = [n for n in (group.findLayers() if group else []) if n.isVisible()]
    if not nodes:
        nodes = [n for n in root.findLayers() if n.isVisible() and not isinstance(n.layer(), QgsVectorLayer)]
    return [n.layer() for n in nodes if n.layer() is not None]


def _apply_overview_layers(layout: QgsPrintLayout) -> None:
    """Übersichtskarten nur mit Hintergrundkarte: Zeichen und Beschriftungen wären dort unlesbar groß."""
    basemaps = _basemap_layers()
    if not basemaps:
        return
    for map_item in _items_of_type(layout, QgsLayoutItemMap):
        if map_item.id() != _MAIN_MAP_ID and map_item.overviews().size() > 0:
            map_item.setLayers(basemaps)
            map_item.setKeepLayerSet(True)


def _fix_picture_paths(layout: QgsPrintLayout, search_dirs: list[str]) -> None:
    """Fehlende Bilder (relativ zum Projekt gedacht) in `search_dirs` suchen, zuerst mit Unterordner."""
    for picture in _items_of_type(layout, QgsLayoutItemPicture):
        current = picture.picturePath()
        if not current or os.path.exists(current):
            continue
        relative = current.replace("\\", "/")
        names = [os.path.basename(relative)] if os.path.isabs(relative) else [relative, os.path.basename(relative)]
        found = next(
            (p for name in names for folder in search_dirs if os.path.isfile(p := os.path.join(folder, name))), None
        )
        if found:
            picture.setPicturePath(found)
        else:
            logger.warning("Bild der Druckvorlage nicht gefunden: %s", current)
