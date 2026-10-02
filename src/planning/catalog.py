import json
import os
from dataclasses import dataclass, field

from ..logging_utils import get_logger
from ..paths import plugin_root

logger = get_logger(__name__)

_CATALOG_FILE = os.path.join("data", "lagerplanung.json")


@dataclass
class FootprintType:
    """Zelt oder Fahrzeug: rechteckige Grundfläche in Metern.

    ``abspannung`` ist bei Zelten der Raum für die Abspannleinen rund um
    die Grundfläche; bei Fahrzeugen 0.
    """

    id: str
    name: str
    laenge: float
    breite: float
    abspannung: float = 1.0

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
class Catalog:
    zelte: list[FootprintType] = field(default_factory=list)
    fahrzeuge: list[FootprintType] = field(default_factory=list)
    leitungsroller: list[CableReelType] = field(default_factory=list)
    verteiler: list[DistributorType] = field(default_factory=list)

    def tent(self, type_id: str) -> FootprintType | None:
        return next((t for t in self.zelte if t.id == type_id), None)

    def vehicle(self, type_id: str) -> FootprintType | None:
        return next((v for v in self.fahrzeuge if v.id == type_id), None)

    def reel(self, type_id: str) -> CableReelType | None:
        return next((r for r in self.leitungsroller if r.id == type_id), None)

    def distributor(self, type_id: str) -> DistributorType | None:
        return next((d for d in self.verteiler if d.id == type_id), None)


def load_catalog(plugin_dir: str | None = None) -> Catalog:
    """Lädt den Katalog aus data/lagerplanung.json (fehlertolerant pro Eintrag)."""
    path = os.path.join(plugin_dir or plugin_root(), _CATALOG_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        logger.exception("Lagerplanungs-Katalog konnte nicht geladen werden: %s", path)
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
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("Ungültiger Eintrag im Lagerplanungs-Katalog: %s", entry)
    return result


def fmt_m(value: float) -> str:
    """1.0 → '1', 5.6 → '5,6' (deutsche Schreibweise)."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")
