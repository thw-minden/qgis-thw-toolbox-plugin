"""Gestaltung eines Gebiets: Füllung (Farbe, Deckkraft, Muster bzw. Schraffur) und Rand (Farbe, Breite, Linie).

Gespeichert als JSON im Feld ``gestaltung`` der Gebiete. Leer heißt: Darstellung nach Typ
(Typfarbe, leicht gefüllt, durchgezogener Rand) – so sehen auch ältere Gebiete aus.
"""

import json
from dataclasses import asdict, dataclass, replace

from qgis.PyQt.QtGui import QColor

STYLE_FIELD = "gestaltung"

PATTERN_SOLID = "voll"
PATTERN_NONE = "ohne"
# Muster → Anzeigename (Reihenfolge = Reihenfolge im Popup)
PATTERNS = {
    PATTERN_SOLID: "Voll",
    "schraeg": "Schraffur ╱",
    "schraeg_links": "Schraffur ╲",
    "kreuz": "Kreuzschraffur",
    "waagerecht": "Waagerecht",
    "gitter": "Gitter",
    "punkte": "Punkte",
    PATTERN_NONE: "Ohne Füllung",
}
# Schraffur-Ebenen der Darstellung: Winkel (Grad, gegen den Uhrzeigersinn) → Muster, die sie zeigen
HATCH_LINES = (
    (45.0, ("schraeg", "kreuz")),
    (135.0, ("schraeg_links", "kreuz")),
    (0.0, ("waagerecht", "gitter")),
    (90.0, ("gitter",)),
)
DOT_PATTERNS = ("punkte",)

STROKE_STYLES = {"solid": "Durchgezogen", "dash": "Gestrichelt", "dot": "Gepunktet"}

# Wie die bisherige Darstellung: Typfarbe mit Deckkraft 45/255, Rand 0,8 mm
DEFAULT_OPACITY = 0.18
DEFAULT_STROKE_WIDTH = 0.8
MIN_OPACITY = 0.05
MIN_STROKE_WIDTH, MAX_STROKE_WIDTH = 0.2, 3.0
# Wer zu einer Schraffur wechselt, bekommt kräftige Linien statt der blassen Flächenfüllung
HATCH_MIN_OPACITY = 0.6


@dataclass(frozen=True)
class ZoneStyle:
    fill: str
    opacity: float = DEFAULT_OPACITY
    pattern: str = PATTERN_SOLID
    # Randfarbe, leer = kein Rand
    stroke: str = ""
    stroke_width: float = DEFAULT_STROKE_WIDTH
    stroke_style: str = "solid"

    @classmethod
    def default(cls, color: str) -> "ZoneStyle":
        """Darstellung nach Typ: Füllung und Rand in der Typfarbe."""
        return cls(fill=QColor(color).name(), stroke=QColor(color).name())

    @property
    def hatched(self) -> bool:
        return self.pattern not in (PATTERN_SOLID, PATTERN_NONE)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    def updated(self, **changes) -> "ZoneStyle":
        """Nur die genannten Angaben ändern – bei mehreren Gebieten behält jedes z. B. seine Farbe.

        Beim Wechsel von Fläche zu Schraffur wird eine blasse Deckkraft angehoben, sonst wären
        die dünnen Linien kaum zu sehen.
        """
        style = replace(self, **changes)
        if "opacity" not in changes and style.hatched and not self.hatched and style.opacity < HATCH_MIN_OPACITY:
            style = replace(style, opacity=1.0)
        return style


def parse_zone_style(text) -> ZoneStyle | None:
    """``ZoneStyle`` aus dem gespeicherten JSON; None bei leerem oder ungültigem Wert."""
    if not text or not isinstance(text, str):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    fill = QColor(str(data.get("fill", "")))
    stroke = QColor(str(data.get("stroke") or ""))
    if not fill.isValid():
        return None
    try:
        opacity = min(max(float(data.get("opacity", DEFAULT_OPACITY)), MIN_OPACITY), 1.0)
        width = min(max(float(data.get("stroke_width", DEFAULT_STROKE_WIDTH)), MIN_STROKE_WIDTH), MAX_STROKE_WIDTH)
    except (TypeError, ValueError):
        return None
    pattern = data.get("pattern")
    stroke_style = data.get("stroke_style")
    return ZoneStyle(
        fill=fill.name(),
        opacity=opacity,
        pattern=pattern if pattern in PATTERNS else PATTERN_SOLID,
        stroke=stroke.name() if data.get("stroke") and stroke.isValid() else "",
        stroke_width=width,
        stroke_style=stroke_style if stroke_style in STROKE_STYLES else "solid",
    )


def zone_style_of(feature, type_color: str) -> ZoneStyle:
    """Gestaltung eines Gebiets; ohne eigene Angabe die Darstellung nach Typ."""
    if feature.fields().indexFromName(STYLE_FIELD) >= 0:
        parsed = parse_zone_style(feature.attribute(STYLE_FIELD))
        if parsed is not None:
            return parsed
    return ZoneStyle.default(type_color)
