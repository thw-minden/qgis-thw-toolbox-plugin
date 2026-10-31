"""Laden von Druckvorlagen (.qpt) mit Ortsverband-/Einsatzdaten, Gitter und aufgeräumter Legende.

Mitgeliefert werden im Ordner `templates/` die THW-Vorlagen
(`THW_*`, unverändert, damit neue Versionen einfach ersetzt werden können) und
die der Toolbox (`Toolbox_*`, erzeugt mit `scripts/build_print_templates.py`).
Eigene Vorlagen liegen im QGIS-Profil. Alle Anpassungen passieren erst nach dem
Laden am fertigen Layout:

- Platzhalter in Textfeldern (z. B. "Ortsverband XXXXXX") werden durch
  Layout-Variablen (`@thw_ov`, ...) ersetzt. Die Werte lassen sich danach in den
  Layout-Eigenschaften unter "Variablen" ändern.
- Relative Bildpfade (`./GRAFIKEN/LAYOUT/...`) werden neben der Vorlage bzw. in
  `templates/assets/` gesucht, falls das Projekt die Grafiken nicht mitbringt;
  gefundene SVGs landen eingebettet (`base64:`) im Layout, damit sich das Projekt
  auch ohne den Plugin-Ordner öffnen lässt.
- Trägt die Karte `Hauptkarte` die Gitter `UTMREF` / `LONLAT`, wird das gewählte
  eingeschaltet; die Karte übernimmt den aktuellen Ausschnitt in dessen UTM-Zone.
- Ein im Dialog gewählter Maßstab der Hauptkarte (Empfehlung je Papierformat in
  `EMPFOHLENE_MASSSTAEBE`) ersetzt den aus Ausschnitt bzw. Vorlage.
- Übersichtskarten zeigen nur die Hintergrundkarte.
- Das taktische Zeichen der Einheit (`Taktisches Zeichen Einheit`) wird nach Wahl ausgetauscht oder ausgeblendet.
- Die Legende wird auf das Nötige reduziert (siehe `legend.py`).
"""

import base64
import functools
import json
import math
import os
import re
import shutil
from dataclasses import dataclass
from xml.etree import ElementTree

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsExpressionContextUtils,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemPicture,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPrintLayout,
    QgsProject,
    QgsReadWriteContext,
    QgsRectangle,
    QgsSettings,
    QgsVectorLayer,
)
from qgis.PyQt.QtXml import QDomDocument

from ..logging_utils import get_logger
from ..paths import profile_dir
from ..tools.layer_setup import GROUP_NAME_BASEMAPS
from ..util.svg_minify import minify_svg
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
GROUP_THW = "THW-Vorlagen"
GROUP_OWN = "Eigene Vorlagen"
_BUNDLED_GROUPS = {"Toolbox": GROUP_TOOLBOX, "THW": GROUP_THW}
_GROUP_ORDER = (GROUP_TOOLBOX, GROUP_THW, GROUP_OWN)
_NAME_PARTS = {"QUER": "quer", "HOCH": "hoch"}

# Element-IDs aus den Vorlagen
_VSNFD_ID = "VS-NfD"
_MAIN_MAP_ID = "Hauptkarte"
_BUNDESLOGO_ID = "Logo Bundesadler"
_UNIT_SIGN_ID = "Taktisches Zeichen Einheit"

# Eingebettete Bilder (base64:) – QGIS erkennt das Format dann nicht am Dateinamen, es muss mitgegeben werden
_EMBEDDED_PREFIX = "base64:"
_PICTURE_SVG = Qgis.PictureFormat.SVG

# Taktisches Zeichen der Einheit oben rechts in den Toolbox-Vorlagen: Schlüssel → (Anzeigename, Datei in
# `templates/assets/`). Die Dateien sind knapp auf das Zeichen zugeschnitten; es erhält die Höhe des Platzes
# in der Vorlage und wächst rechtsbündig in die Breite
EINHEIT_ZEICHEN_KEINS = ""
EINHEIT_ZEICHEN_TRUPP_UL = "trupp_ul"
EINHEIT_ZEICHEN = {
    EINHEIT_ZEICHEN_KEINS: ("Keins", None),
    EINHEIT_ZEICHEN_TRUPP_UL: ("Unbemannte Luftfahrtsysteme", "Taktisches_Zeichen-UnbemannteLuftfahrsysteme.svg"),
    "zugtrupp": ("Zugtrupp", "Taktisches_Zeichen-Zugtrupp.svg"),
}

_STANDARD_SCALES = (500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 200000, 250000, 500000, 1000000)

# Empfohlener Maßstab der Hauptkarte je Papierformat. Wie in den Vorlagen der THW-Leitung zeigt jedes
# Format etwa denselben Ausschnitt (rund 3,3 × 2,6 km), größeres Papier also mehr Einzelheiten.
EMPFOHLENE_MASSSTAEBE = {"A4": 15000, "A3": 10000, "A2": 7500, "A1": 5000, "A0": 3500}
# Zur Auswahl im Druckvorlagen-Dialog, zusätzlich zu den empfohlenen
MASSSTAB_VORSCHLAEGE = tuple(
    sorted({1000, 2500, 5000, 10000, 25000, 50000, 100000, *EMPFOHLENE_MASSSTAEBE.values()})
)

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
    einheit_zeichen: str = EINHEIT_ZEICHEN_TRUPP_UL  # Schlüssel aus `EINHEIT_ZEICHEN`
    bearbeiter: str = ""
    einsatz: str = ""
    kartentitel: str = KARTENTITEL_VORSCHLAEGE[0]
    einsatzort: str = ""
    blatt: str = ""
    gitter: str = GITTER_UTMREF
    tidy_legend: bool = True

    @classmethod
    def load(cls, einsatz_from_title: bool = True) -> "PrintInfo":
        """OV/Einheit/Bearbeiter aus den Benutzereinstellungen, Einsatz aus dem Projekt.

        Ohne gespeicherten Einsatz wird der Projekttitel vorgeschlagen, außer bei
        ``einsatz_from_title=False`` (Setup-Assistent: Einsatz bleibt dann leer).
        Bearbeiter fällt auf den Autor des Projekts bzw. den Namen des Benutzers zurück.
        """
        s = QgsSettings()
        d = cls()
        project = QgsProject.instance()

        einsatz, ok = project.readEntry(_PROJECT_SCOPE, _PROJECT_EINSATZ_KEY, "")
        if (not ok or not einsatz) and einsatz_from_title:
            einsatz = project.title() or project.baseName()
        kartentitel, _ = project.readEntry(_PROJECT_SCOPE, _PROJECT_KARTENTITEL_KEY, d.kartentitel)
        einsatzort, _ = project.readEntry(_PROJECT_SCOPE, _PROJECT_EINSATZORT_KEY, "")
        gitter = s.value(_SETTINGS_PREFIX + "gitter", d.gitter)
        # Früher nur ein Häkchen für das Zeichen des Trupps UL
        zeichen_default = d.einheit_zeichen if s.value(_SETTINGS_PREFIX + "einheit_zeichen", True, type=bool) else ""
        zeichen = s.value(_SETTINGS_PREFIX + "einheit_zeichen_art", zeichen_default)

        return cls(
            ortsverband=s.value(_SETTINGS_PREFIX + "ortsverband", d.ortsverband),
            einheit=s.value(_SETTINGS_PREFIX + "einheit", d.einheit),
            einheit_kurz=s.value(_SETTINGS_PREFIX + "einheit_kurz", d.einheit_kurz),
            einheit_zeichen=zeichen if zeichen in EINHEIT_ZEICHEN else d.einheit_zeichen,
            bearbeiter=s.value(_SETTINGS_PREFIX + "bearbeiter", "")
            or project.metadata().author()
            or QgsApplication.userFullName(),
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
        s.setValue(_SETTINGS_PREFIX + "einheit_zeichen_art", self.einheit_zeichen)
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
    return os.path.join(profile_dir(), "druckvorlagen")


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
        # Varianten ("Modern A4 quer") nach den Standardvorlagen, darin jeweils A4 zuerst
        paper = re.search(r"\bA(\d)\b", entry.name)
        variant = entry.name[: paper.start()] if paper else entry.name
        return (
            _GROUP_ORDER.index(entry.group),
            variant.casefold(),
            -int(paper.group(1)) if paper else 1,
            entry.name.casefold(),
        )

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


@dataclass(frozen=True)
class TemplateInfo:
    """Eckdaten einer Vorlage für den Druckvorlagen-Dialog, ohne sie als Layout zu laden."""

    paper: str | None = None  # ISO-Format der ersten Seite, z. B. "A3"
    map_size_mm: tuple[float, float] | None = None  # Breite, Höhe der Hauptkarte
    canvas_extent: bool = False  # Hauptkarte trägt Toolbox-Gitter und übernimmt den Kartenausschnitt
    unit_sign: bool = False  # Platz für das taktische Zeichen der Einheit (`Taktisches Zeichen Einheit`)

    @property
    def recommended_scale(self) -> int | None:
        return EMPFOHLENE_MASSSTAEBE.get(self.paper)


_LAYOUT_ITEM_PAGE = "65638"
_LAYOUT_ITEM_MAP = "65639"
_UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "pt": 25.4 / 72}


def template_info(path: str) -> TemplateInfo:
    """Papierformat und Größe der Hauptkarte aus der Vorlage `path`; unbekanntes bleibt `None`."""
    try:
        return _template_info(path, os.path.getmtime(path))
    except OSError:
        return TemplateInfo()


@functools.lru_cache(maxsize=32)
def _template_info(path: str, _mtime: float) -> TemplateInfo:
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError) as e:
        logger.warning("Vorlage %s konnte nicht gelesen werden: %s", path, e)
        return TemplateInfo()

    page = next((p for pc in root.iter("PageCollection") for p in pc.iter("LayoutItem")), None)
    page_size = _size_mm(page.get("size")) if page is not None else None
    main_map = next(
        (i for i in root.iter("LayoutItem") if i.get("type") == _LAYOUT_ITEM_MAP and i.get("id") == _MAIN_MAP_ID),
        None,
    )
    unit_sign = any(i.get("id") == _UNIT_SIGN_ID for i in root.iter("LayoutItem"))
    if main_map is None:
        return TemplateInfo(paper=_paper_format(page_size), unit_sign=unit_sign)
    return TemplateInfo(
        paper=_paper_format(page_size),
        map_size_mm=_size_mm(main_map.get("size")),
        canvas_extent=any(g.get("name") in GITTER_NAMEN for g in main_map.iter("ComposerMapGrid")),
        unit_sign=unit_sign,
    )


def _size_mm(size: str | None) -> tuple[float, float] | None:
    """`"220.5,173,mm"` → `(220.5, 173.0)`."""
    try:
        w, h, unit = (size or "").split(",")
        return float(w) * _UNIT_TO_MM[unit], float(h) * _UNIT_TO_MM[unit]
    except (ValueError, KeyError):
        return None


def _paper_format(size_mm: tuple[float, float] | None) -> str | None:
    """Nächstes ISO-A-Format nach der langen Seite (A0 = 1189 mm, je Stufe / √2), z. B. 400 × 297 mm → A3."""
    if not size_mm or max(size_mm) <= 0:
        return None
    n = math.log(1189 / max(size_mm), math.sqrt(2))
    return f"A{round(n)}" if 0 <= round(n) <= 10 and abs(n - round(n)) < 0.25 else None


def load_print_template(
    path: str, info: PrintInfo, assets_dir: str, canvas=None, scale: int | None = None
) -> QgsPrintLayout:
    """Lädt `path` als neues Layout und wendet `info` an. Wirft `ValueError` bei Fehlern.

    Mit `canvas` übernehmen Vorlagen mit UTMREF-/LONLAT-Gitter dessen Kartenausschnitt,
    alle übrigen (z. B. THW-Leitung) werden auf dessen Mitte zentriert.
    Mit `scale` erhält die Hauptkarte diesen Maßstab (um ihre Mitte), statt ihn aus dem
    Kartenausschnitt bzw. der Vorlage zu übernehmen.
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
    _fix_picture_paths(layout, [os.path.dirname(path), assets_dir])
    _apply_unit_sign(layout, info.einheit_zeichen, assets_dir)
    _apply_grid(layout, info.gitter)
    if canvas is not None:
        _apply_canvas_view(layout, canvas)
    if scale:
        _apply_scale(layout, scale)
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


def _apply_unit_sign(layout: QgsPrintLayout, zeichen: str, assets_dir: str) -> None:
    """Taktisches Zeichen der Einheit im Kartenkopf austauschen oder ausblenden.

    Das Zeichen behält Höhe und rechte Kante des Platzes in der Vorlage, die Breite folgt seinen Proportionen.
    """
    filename = EINHEIT_ZEICHEN.get(zeichen, (None, None))[1]
    for picture in _items_of_type(layout, QgsLayoutItemPicture):
        if picture.id() != _UNIT_SIGN_ID:
            continue
        picture.setVisibility(filename is not None)
        if not filename:
            continue
        sign_path = os.path.join(assets_dir, filename)
        # Eingebettet wie die übrigen Bilder der Vorlage – das Projekt braucht den Plugin-Ordner nicht
        _embed_svg(picture, sign_path)
        aspect = _svg_aspect(sign_path)
        size, pos = picture.sizeWithUnits(), picture.positionWithUnits()
        if not aspect or size.height() <= 0 or abs(size.width() / size.height() - aspect) < 0.01:
            continue
        width = size.height() * aspect
        picture.attemptMove(QgsLayoutPoint(pos.x() + size.width() - width, pos.y(), pos.units()))
        picture.attemptResize(QgsLayoutSize(width, size.height(), size.units()))


def _svg_aspect(path: str) -> float | None:
    """Breite / Höhe der SVG-Datei `path` nach ihrer viewBox, `None` wenn sie sich nicht lesen lässt."""
    try:
        view_box = ElementTree.parse(path).getroot().get("viewBox", "")
        _, _, w, h = (float(v) for v in view_box.replace(",", " ").split())
    except (OSError, ElementTree.ParseError, ValueError) as e:
        logger.warning("Taktisches Zeichen %s ohne lesbare viewBox: %s", path, e)
        return None
    return w / h if h > 0 else None


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
    Vorlagen ohne Toolbox-Gitter (THW-Leitung, eigene) behalten Maßstab und KBS und
    werden nur auf die Mitte des Hauptfensters zentriert.
    """
    maps = _items_of_type(layout, QgsLayoutItemMap)
    main_map = next((m for m in maps if m.id() == _MAIN_MAP_ID), None)
    grids = _template_grids(main_map) if main_map is not None else {}
    if not grids:
        _center_maps(maps, canvas)
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


def _center_maps(maps: list, canvas) -> None:
    """Alle Karten (auch die Übersichten, deren Rahmen die Hauptkarte zeigt) auf die
    Mitte des Hauptfensters schieben, jede in ihrem eigenen KBS und Maßstab."""
    canvas_crs = canvas.mapSettings().destinationCrs()
    center = canvas.extent().center()
    for map_item in maps:
        try:
            c = QgsCoordinateTransform(canvas_crs, map_item.crs(), QgsProject.instance()).transform(center)
        except Exception as e:  # QgsCsException außerhalb des Gültigkeitsbereichs des KBS
            logger.warning("Karte %s konnte nicht zentriert werden: %s", map_item.id(), e)
            continue
        extent = map_item.extent()
        half_w, half_h = extent.width() / 2, extent.height() / 2
        map_item.setExtent(QgsRectangle(c.x() - half_w, c.y() - half_h, c.x() + half_w, c.y() + half_h))

    # Die Markierung in den Übersichten (Übersichtsrahmen) folgt der Hauptkarte, solange sie mit ihr
    # verknüpft ist. Ging die Verknüpfung beim Laden verloren, bliebe sie an der alten Stelle stehen.
    main_map = next((m for m in maps if m.id() == _MAIN_MAP_ID), None)
    if main_map is None:
        return
    for map_item in maps:
        if map_item is main_map:
            continue
        for overview in map_item.overviews().asList():
            if overview.linkedMap() is None:
                logger.debug("Übersichtsrahmen in %s neu mit der Hauptkarte verknüpft", map_item.id())
                overview.setLinkedMap(main_map)
        map_item.invalidateCache()


def _apply_scale(layout: QgsPrintLayout, scale: int) -> None:
    """Maßstab der Hauptkarte setzen; die Mitte bleibt. Übersichten der THW-Leitung folgen per Ausdruck."""
    main_map = next((m for m in _items_of_type(layout, QgsLayoutItemMap) if m.id() == _MAIN_MAP_ID), None)
    if main_map is None:
        logger.warning("Vorlage ohne Hauptkarte: Maßstab 1:%s nicht gesetzt", scale)
        return
    main_map.setScale(scale)


def _basemap_layers() -> list:
    """Eingeschaltete Hintergrundkarten: die der Gruppe aus dem Setup, sonst alle Raster- und Kachellayer.

    Vektorlayer zählen nie dazu, auch nicht in der Gruppe: Dort landet z. B. der Layer der
    taktischen Zeichen, wenn er angelegt wird, während die Gruppe im Layerbaum gewählt ist.
    """
    root = QgsProject.instance().layerTreeRoot()
    group = root.findGroup(GROUP_NAME_BASEMAPS)
    candidates = [n for n in root.findLayers() if n.isVisible() and not isinstance(n.layer(), QgsVectorLayer)]
    in_group = [n for n in candidates if group is not None and group.findLayer(n.layerId()) is not None]
    return [n.layer() for n in (in_group or candidates) if n.layer() is not None]


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
    """Bilder der Vorlage finden und SVGs einbetten, damit das Projekt ohne Plugin-Ordner auskommt.

    Fehlende Bilder (relativ zum Projekt gedacht) werden in `search_dirs` gesucht, zuerst mit Unterordner.
    Gefundene SVGs landen als ``base64:`` im Bildelement – rendert in QGIS 3.44 und 4.2 wie die Datei und
    übersteht Speichern/Laden des Projekts. Andere Formate behalten ihren Pfad.
    """
    for picture in _items_of_type(layout, QgsLayoutItemPicture):
        current = picture.picturePath()
        if not current or current.startswith(_EMBEDDED_PREFIX):
            continue
        found = current if os.path.isfile(current) else _find_picture(current, search_dirs)
        if not found:
            logger.warning("Bild der Druckvorlage nicht gefunden: %s", current)
        elif found.lower().endswith(".svg"):
            _embed_svg(picture, found)
        elif found != current:
            picture.setPicturePath(found)


def _find_picture(current: str, search_dirs: list[str]) -> str | None:
    relative = current.replace("\\", "/")
    names = [os.path.basename(relative)] if os.path.isabs(relative) else [relative, os.path.basename(relative)]
    return next(
        (p for name in names for folder in search_dirs if os.path.isfile(p := os.path.join(folder, name))), None
    )


def _embed_svg(picture: QgsLayoutItemPicture, path: str) -> None:
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("Bild der Druckvorlage %s nicht einbettbar, Pfad bleibt: %s", path, e)
        picture.setPicturePath(path)
        return
    data = base64.b64encode(minify_svg(text).encode("utf-8")).decode("ascii")
    picture.setPicturePath(_EMBEDDED_PREFIX + data, _PICTURE_SVG)
