"""Hintergrund taktischer Zeichen: Form, Farbe, Deckkraft, Rand und Größe.

Gespeichert als JSON im Feld ``background``. Ältere Layer kennen nur
``white_background`` (weißer Kreis) – das gilt, solange ``background`` leer ist.
"""

import json
from dataclasses import asdict, dataclass

from qgis.PyQt.QtGui import QColor

SHAPE_NONE = "none"
# Form → Anzeigename (Reihenfolge = Reihenfolge im Popup)
SHAPES = {
    SHAPE_NONE: "Ohne",
    "circle": "Kreis",
    "square": "Quadrat",
    "rounded": "Abgerundet",
    "diamond": "Raute",
}
# Hintergrund im Verhältnis zur Zeichengröße (bisheriger weißer Kreis: 1,2)
DEFAULT_SCALE = 1.2
MIN_SCALE, MAX_SCALE = 1.0, 2.5
MIN_OPACITY = 0.1


@dataclass(frozen=True)
class Background:
    shape: str = SHAPE_NONE
    color: str = "#ffffff"
    opacity: float = 1.0
    # Randfarbe, leer = kein Rand
    stroke: str = ""
    scale: float = DEFAULT_SCALE

    @property
    def visible(self) -> bool:
        return self.shape != SHAPE_NONE

    def to_json(self) -> str:
        return json.dumps(asdict(self))


WHITE_CIRCLE = Background("circle")


def parse_background(text) -> Background | None:
    """``Background`` aus dem gespeicherten JSON; None bei leerem oder ungültigem Wert."""
    if not text or not isinstance(text, str):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    shape = data.get("shape")
    color = QColor(str(data.get("color", "")))
    stroke = QColor(str(data.get("stroke") or ""))
    try:
        opacity = min(max(float(data.get("opacity", 1.0)), MIN_OPACITY), 1.0)
        scale = min(max(float(data.get("scale", DEFAULT_SCALE)), MIN_SCALE), MAX_SCALE)
    except (TypeError, ValueError):
        return None
    return Background(
        shape=shape if shape in SHAPES else SHAPE_NONE,
        color=color.name() if color.isValid() else "#ffffff",
        opacity=opacity,
        stroke=stroke.name() if data.get("stroke") and stroke.isValid() else "",
        scale=scale,
    )


def background_of(feat) -> Background:
    """Hintergrund eines Zeichens; ohne gespeicherten ``background`` gilt ``white_background``."""
    fields = feat.fields()
    if fields.indexFromName("background") >= 0:
        parsed = parse_background(feat.attribute("background"))
        if parsed is not None:
            return parsed
    if fields.indexFromName("white_background") >= 0 and feat.attribute("white_background"):
        return WHITE_CIRCLE
    return Background()
