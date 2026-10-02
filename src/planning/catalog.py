import json
import os
from dataclasses import dataclass, field

from ..logging_utils import get_logger
from ..paths import plugin_root

logger = get_logger(__name__)

_CATALOG_FILE = os.path.join("data", "objektplanung.json")


@dataclass
class FootprintType:
    """Zelt oder Fahrzeug: rechteckige Grundfläche in Metern.

    ``abspannung`` ist bei Zelten der Raum für die Abspannleinen rund um
    die Grundfläche; bei Fahrzeugen 0. ``zeichen`` ist das taktische Zeichen
    (Pfad relativ zu ``svgs/``), das in der Grundfläche angezeigt wird.
    """

    id: str
    name: str
    laenge: float
    breite: float
    abspannung: float = 1.0
    zeichen: str = ""

    @property
    def flaeche(self) -> float:
        return self.laenge * self.breite

    def label(self) -> str:
        return f"{self.name} ({fmt_m(self.laenge)} × {fmt_m(self.breite)} m)"


@dataclass
class CableReelType:
    id: str
    name: str
    laenge: float
    farbe: str = "#ef6c00"


@dataclass
class DistributorType:
    id: str
    name: str
    farbe: str = "#fbc02d"


@dataclass
class GeneratorType:
    """Stromerzeuger (SEA tragbar, NEA als Anhänger/Container)."""

    id: str
    name: str
    leistung_kva: float
    farbe: str = "#c62828"
    zeichen: str = ""


@dataclass
class LightType:
    """Leuchte mit Anschlussleistung und grob ausgeleuchtetem Radius."""

    id: str
    name: str
    leistung_w: float
    radius: float
    farbe: str = "#fdd835"
    zeichen: str = ""


@dataclass
class Catalog:
    zelte: list[FootprintType] = field(default_factory=list)
    fahrzeuge: list[FootprintType] = field(default_factory=list)
    leitungsroller: list[CableReelType] = field(default_factory=list)
    verteiler: list[DistributorType] = field(default_factory=list)
    stromerzeuger: list[GeneratorType] = field(default_factory=list)
    beleuchtung: list[LightType] = field(default_factory=list)

    def tent(self, type_id: str) -> FootprintType | None:
        return next((t for t in self.zelte if t.id == type_id), None)

    def vehicle(self, type_id: str) -> FootprintType | None:
        return next((v for v in self.fahrzeuge if v.id == type_id), None)

    def reel(self, type_id: str) -> CableReelType | None:
        return next((r for r in self.leitungsroller if r.id == type_id), None)

    def distributor(self, type_id: str) -> DistributorType | None:
        return next((d for d in self.verteiler if d.id == type_id), None)

    def generator(self, type_id: str) -> GeneratorType | None:
        return next((g for g in self.stromerzeuger if g.id == type_id), None)

    def light(self, type_id: str) -> LightType | None:
        return next((li for li in self.beleuchtung if li.id == type_id), None)


def load_catalog(plugin_dir: str | None = None) -> Catalog:
    """Lädt den Katalog aus data/objektplanung.json (fehlertolerant pro Eintrag)."""
    path = os.path.join(plugin_dir or plugin_root(), _CATALOG_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        logger.exception("Objektplanungs-Katalog konnte nicht geladen werden: %s", path)
        raw = {}

    catalog = Catalog()
    catalog.zelte = _load_footprints(raw.get("zelte", []), default_guy=1.0)
    catalog.fahrzeuge = _load_footprints(raw.get("fahrzeuge", []), default_guy=0.0)
    for entry in raw.get("leitungsroller", []):
        try:
            catalog.leitungsroller.append(
                CableReelType(
                    id=str(entry["id"]),
                    name=str(entry["name"]),
                    laenge=float(entry["laenge"]),
                    farbe=str(entry.get("farbe", "#ef6c00")),
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("Ungültiger Leitungsroller-Eintrag im Katalog: %s", entry)
    for entry in raw.get("verteiler", []):
        try:
            catalog.verteiler.append(
                DistributorType(
                    id=str(entry["id"]),
                    name=str(entry["name"]),
                    farbe=str(entry.get("farbe", "#fbc02d")),
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("Ungültiger Verteiler-Eintrag im Katalog: %s", entry)
    for entry in raw.get("stromerzeuger", []):
        try:
            catalog.stromerzeuger.append(
                GeneratorType(
                    id=str(entry["id"]),
                    name=str(entry["name"]),
                    leistung_kva=float(entry["leistung_kva"]),
                    farbe=str(entry.get("farbe", "#c62828")),
                    zeichen=str(entry.get("zeichen", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("Ungültiger Stromerzeuger-Eintrag im Katalog: %s", entry)
    for entry in raw.get("beleuchtung", []):
        try:
            catalog.beleuchtung.append(
                LightType(
                    id=str(entry["id"]),
                    name=str(entry["name"]),
                    leistung_w=float(entry["leistung_w"]),
                    radius=float(entry["radius"]),
                    farbe=str(entry.get("farbe", "#fdd835")),
                    zeichen=str(entry.get("zeichen", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("Ungültiger Beleuchtungs-Eintrag im Katalog: %s", entry)
    return catalog


def _load_footprints(entries: list, default_guy: float) -> list[FootprintType]:
    result = []
    for entry in entries:
        try:
            result.append(
                FootprintType(
                    id=str(entry["id"]),
                    name=str(entry["name"]),
                    laenge=float(entry["laenge"]),
                    breite=float(entry["breite"]),
                    abspannung=float(entry.get("abspannung", default_guy)),
                    zeichen=str(entry.get("zeichen", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("Ungültiger Eintrag im Objektplanungs-Katalog: %s", entry)
    return result


def fmt_m(value: float) -> str:
    """1.0 → '1', 5.6 → '5,6' (deutsche Schreibweise)."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")
