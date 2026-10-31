"""Gestaltung einer Linie, eines Punkts oder Texts: Farbe und Deckkraft, bei Linien Breite und Pfeilspitzen.

Gespeichert als JSON im Feld ``gestaltung`` (wie bei den Gebieten, siehe ``zone_style``). Leer heißt:
Darstellung nach Typ – Typfarbe, deckend, Pfeilspitze am Ende. So sehen auch ältere Pfeile aus.
Der Radius-Kreis eines Punkts ist gestaltet wie ein Gebiet (``circle``); ohne eigene Angabe folgt er
der Farbe des Punkts.
"""

import json
from dataclasses import asdict, dataclass, replace

from qgis.PyQt.QtGui import QColor

from .zone_style import STYLE_FIELD, ZoneStyle, zone_style_from_dict

ARROWS_NONE = "ohne"
ARROWS_END = "ende"
ARROWS_START = "anfang"
ARROWS_BOTH = "beide"
# Pfeilspitzen → Anzeigename (Reihenfolge = Reihenfolge im Popup)
ARROW_MODES = {
    ARROWS_END: "Spitze am Ende",
    ARROWS_START: "Spitze am Anfang",
    ARROWS_BOTH: "Spitzen an beiden Enden",
    ARROWS_NONE: "Ohne Spitze (Linie)",
}

# Wie die bisherige Darstellung der Pfeile: 1,2 mm breit, Spitze automatisch (≈ 5 mm)
DEFAULT_LINE_WIDTH = 1.2
MIN_LINE_WIDTH, MAX_LINE_WIDTH = 0.2, 5.0
# 0 = automatisch: die Spitze wächst mit der Linienbreite
MAX_ARROW_SIZE = 30.0
MIN_OPACITY = 0.05
# Radius-Kreis ohne eigene Gestaltung: Punktfarbe, größtenteils durchscheinend, dünner Rand
CIRCLE_OPACITY = 0.25
CIRCLE_STROKE_WIDTH = 0.5


def arrow_mode(start: bool, end: bool) -> str:
    """Pfeilspitzen am Anfang und/oder Ende als Wert für ``ObjectStyle.arrows``."""
    if start and end:
        return ARROWS_BOTH
    if start:
        return ARROWS_START
    return ARROWS_END if end else ARROWS_NONE


def arrow_size_for(width_mm: float, arrow_size_mm: float = 0.0) -> float:
    """Größe der Pfeilspitze (mm): die gewählte oder automatisch nach der Linienbreite."""
    return arrow_size_mm if arrow_size_mm > 0 else max(3.0, width_mm * 4.0)


@dataclass(frozen=True)
class ObjectStyle:
    color: str
    opacity: float = 1.0
    # Nur Pfeile: Linienbreite (mm), Pfeilspitzen, Größe der Spitzen (mm, 0 = automatisch)
    width: float = DEFAULT_LINE_WIDTH
    arrows: str = ARROWS_END
    arrow_size: float = 0.0
    # Nur Punkte: eigene Gestaltung des Radius-Kreises; None = in der Farbe des Punkts
    circle: ZoneStyle | None = None

    @classmethod
    def default(cls, color: str) -> "ObjectStyle":
        """Darstellung nach Typ: Typfarbe, deckend."""
        return cls(color=QColor(color).name())

    def to_json(self) -> str:
        data = asdict(self)
        if self.circle is None:
            del data["circle"]
        return json.dumps(data)

    def updated(self, **changes) -> "ObjectStyle":
        """Nur die genannten Angaben ändern – bei mehreren Objekten behält jedes z. B. seine Farbe."""
        return replace(self, **changes)

    def circle_style(self) -> ZoneStyle:
        """Gestaltung des Radius-Kreises; ohne eigene die Darstellung in der Farbe des Punkts."""
        if self.circle is not None:
            return self.circle
        return ZoneStyle(
            fill=self.color, opacity=CIRCLE_OPACITY, stroke=self.color, stroke_width=CIRCLE_STROKE_WIDTH
        )


def parse_object_style(text) -> ObjectStyle | None:
    """``ObjectStyle`` aus dem gespeicherten JSON; None bei leerem oder ungültigem Wert."""
    if not text or not isinstance(text, str):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    color = QColor(str(data.get("color", "")))
    if not color.isValid():
        return None
    try:
        opacity = min(max(float(data.get("opacity", 1.0)), MIN_OPACITY), 1.0)
        width = min(max(float(data.get("width", DEFAULT_LINE_WIDTH)), MIN_LINE_WIDTH), MAX_LINE_WIDTH)
        arrow_size = min(max(float(data.get("arrow_size", 0.0)), 0.0), MAX_ARROW_SIZE)
    except (TypeError, ValueError):
        return None
    arrows = data.get("arrows")
    return ObjectStyle(
        color=color.name(),
        opacity=opacity,
        width=width,
        arrows=arrows if arrows in ARROW_MODES else ARROWS_END,
        arrow_size=arrow_size,
        circle=zone_style_from_dict(data.get("circle")),
    )


def object_style_of(feature, type_color: str) -> ObjectStyle:
    """Gestaltung eines Pfeils, Punkts oder Texts; ohne eigene Angabe die Darstellung nach Typ."""
    if feature.fields().indexFromName(STYLE_FIELD) >= 0:
        parsed = parse_object_style(feature.attribute(STYLE_FIELD))
        if parsed is not None:
            return parsed
    return ObjectStyle.default(type_color)
