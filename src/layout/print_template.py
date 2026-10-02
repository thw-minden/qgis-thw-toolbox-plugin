"""Laden der THW-Druckvorlagen (.qpt) mit Ortsverband-/Einsatzdaten und einheitlicher Legende.

Die Vorlagen der THW-Leitung bleiben unverändert im Ordner `templates/`, damit
neue Versionen einfach ersetzt werden können. Alle Anpassungen passieren erst
nach dem Laden am fertigen Layout:

- Platzhalter in Textfeldern (z. B. "Ortsverband XXXXXX") werden durch
  Layout-Variablen (`@thw_ov`, ...) ersetzt. Die Werte lassen sich danach in den
  Layout-Eigenschaften unter "Variablen" ändern.
- Relative Bildpfade (`./GRAFIKEN/LAYOUT/...`) werden auf `templates/assets/`
  umgebogen, falls das Projekt die Grafiken nicht selbst mitbringt.
- Die Legende zeigt jedes taktische Zeichen nur einmal, ungedreht und in
  einheitlicher Größe.
"""

import json
import os
import re
from dataclasses import dataclass

from qgis.core import (
    QgsExpressionContextUtils,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemPicture,
    QgsMapLayerLegendUtils,
    QgsMarkerSymbolLayer,
    QgsPrintLayout,
    QgsProject,
    QgsReadWriteContext,
    QgsSettings,
    QgsSymbolLegendNode,
    QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.PyQt.QtXml import QDomDocument

from ..layer.renderer import _BG_SCALE
from ..logging_utils import get_logger

logger = get_logger(__name__)

_SETTINGS_PREFIX = "THWToolbox/print/"
_PROJECT_SCOPE = "THWToolbox"
_PROJECT_EINSATZ_KEY = "print_einsatz"

EINSTUFUNG_OFFEN = "nicht klassifiziert"
EINSTUFUNG_VSNFD = "VS-NfD"

# Element-IDs aus den THW-Vorlagen
_ITEM_IDS_OFFEN = ("nicht klassifiziert - Rahmen", "nicht klassifiziert - Text")
_ITEM_IDS_VSNFD = ("VS-NfD",)

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
    bearbeiter: str = ""
    einsatz: str = ""
    einstufung: str = EINSTUFUNG_OFFEN
    uniform_legend: bool = True

    @classmethod
    def load(cls) -> "PrintInfo":
        """OV/Einheit/Bearbeiter aus den Benutzereinstellungen, Einsatz aus dem Projekt."""
        s = QgsSettings()
        d = cls()
        project = QgsProject.instance()

        einsatz, ok = project.readEntry(_PROJECT_SCOPE, _PROJECT_EINSATZ_KEY, "")
        if not ok or not einsatz:
            einsatz = project.title() or project.baseName()

        return cls(
            ortsverband=s.value(_SETTINGS_PREFIX + "ortsverband", d.ortsverband),
            einheit=s.value(_SETTINGS_PREFIX + "einheit", d.einheit),
            einheit_kurz=s.value(_SETTINGS_PREFIX + "einheit_kurz", d.einheit_kurz),
            bearbeiter=s.value(_SETTINGS_PREFIX + "bearbeiter", "") or project.metadata().author(),
            einsatz=einsatz,
            einstufung=s.value(_SETTINGS_PREFIX + "einstufung", d.einstufung),
            uniform_legend=s.value(_SETTINGS_PREFIX + "uniform_legend", d.uniform_legend, type=bool),
        )

    def save(self) -> None:
        s = QgsSettings()
        s.setValue(_SETTINGS_PREFIX + "ortsverband", self.ortsverband)
        s.setValue(_SETTINGS_PREFIX + "einheit", self.einheit)
        s.setValue(_SETTINGS_PREFIX + "einheit_kurz", self.einheit_kurz)
        s.setValue(_SETTINGS_PREFIX + "bearbeiter", self.bearbeiter)
        s.setValue(_SETTINGS_PREFIX + "einstufung", self.einstufung)
        s.setValue(_SETTINGS_PREFIX + "uniform_legend", self.uniform_legend)
        QgsProject.instance().writeEntry(_PROJECT_SCOPE, _PROJECT_EINSATZ_KEY, self.einsatz)


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


def load_print_template(path: str, info: PrintInfo, assets_dir: str) -> QgsPrintLayout:
    """Lädt `path` als neues Layout und wendet `info` an. Wirft `ValueError` bei Fehlern."""
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
    _apply_einstufung(layout, info.einstufung)
    _fix_picture_paths(layout, assets_dir)
    if info.uniform_legend:
        for legend in _items_of_type(layout, QgsLayoutItemLegend):
            apply_uniform_legend(legend)

    layout.refresh()
    return layout


def _items_of_type(layout: QgsPrintLayout, cls):
    return [item for item in layout.items() if isinstance(item, cls)]


def _apply_variables(layout: QgsPrintLayout, info: PrintInfo) -> None:
    variables = {
        "thw_ov": info.ortsverband,
        "thw_einheit": info.einheit,
        "thw_einheit_kurz": info.einheit_kurz,
        "thw_bearbeiter": info.bearbeiter,
        "thw_einsatz": info.einsatz,
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


def _apply_einstufung(layout: QgsPrintLayout, einstufung: str) -> None:
    show_vsnfd = einstufung == EINSTUFUNG_VSNFD
    for item in layout.items():
        item_id = getattr(item, "id", lambda: None)()
        if item_id in _ITEM_IDS_VSNFD:
            item.setVisibility(show_vsnfd)
        elif item_id in _ITEM_IDS_OFFEN:
            item.setVisibility(not show_vsnfd)


def _fix_picture_paths(layout: QgsPrintLayout, assets_dir: str) -> None:
    """Fehlende Bilder (relativ zum Projekt gedacht) durch die mitgelieferten ersetzen."""
    for picture in _items_of_type(layout, QgsLayoutItemPicture):
        current = picture.picturePath()
        if current and os.path.exists(current):
            continue
        fallback = os.path.join(assets_dir, os.path.basename(current.replace("\\", "/")))
        if os.path.exists(fallback):
            picture.setPicturePath(fallback)
        else:
            logger.warning("Bild der Druckvorlage nicht gefunden: %s", current)


def _is_marker_layer(layer) -> bool:
    if not isinstance(layer, QgsVectorLayer):
        return False
    names = layer.fields().names()
    return "svg_path" in names and "unique_id" in names


def apply_uniform_legend(legend: QgsLayoutItemLegend) -> None:
    """Taktische Zeichen in der Legende einmalig, ungedreht und gleich groß darstellen.

    Der Renderer legt pro Feature eine Kategorie mit eigener Größe/Drehung an.
    Hier wird pro Zeichen nur der erste Eintrag behalten und mit einem
    eigenen Symbol fester Größe (in mm) überschrieben. Die Kartendarstellung
    bleibt unverändert.
    """
    # Eigenes Legendenmodell statt automatischem Spiegel des Projekt-Layerbaums
    legend.setAutoUpdateModel(False)
    model = legend.model()

    # Vorlage gibt pro Format max. Symbolgröße vor (A4: ~4,2 mm, A0: ~17 mm)
    size_mm = legend.maximumSymbolSize() or legend.symbolHeight()

    for node_layer in model.rootGroup().findLayers():
        if not _is_marker_layer(node_layer.layer()):
            continue

        # Vorherige Reihenfolge verwerfen (eine leere Liste würde alle Einträge ausblenden)
        node_layer.removeCustomProperty("legend/node-order")
        model.refreshLayerLegend(node_layer)

        seen = set()
        order = []
        for index, node in enumerate(model.layerOriginalLegendNodes(node_layer)):
            if not isinstance(node, QgsSymbolLegendNode) or node.symbol() is None:
                continue
            key = node.symbolLabel()
            if key in seen:
                continue
            seen.add(key)
            order.append(index)
            QgsMapLayerLegendUtils.setLegendNodeCustomSymbol(node_layer, index, _legend_symbol(node.symbol(), size_mm))

        QgsMapLayerLegendUtils.setLegendNodeOrder(node_layer, order)
        model.refreshLayerLegend(node_layer)

    legend.updateLegend()


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
