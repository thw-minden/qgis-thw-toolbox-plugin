"""Erzeugt die Hotbar-Icons für Texte, Punkte, Linien und Gebiete je Typ (`icons/planning/<art>/`).

Aufruf mit beliebigem Python aus dem Plugin-Ordner (ohne QGIS):

    python scripts/build_planning_icons.py

Die Hotbar zeigt für Text, Punkt, Linie und Gebiet das Icon des gerade gewählten Typs,
bei Linien zusätzlich mit den gewählten Pfeilspitzen. Die Typen und Farben kommen aus
`data/objektplanung.json`; nach einer Änderung dort das Skript erneut ausführen.
Fehlt ein Icon, nimmt die Hotbar das allgemeine (`icons/planning/<art>.svg`).

- `text/<typ>.svg`: „T“ in der Typfarbe mit Schreibmarke
- `point/<typ>.svg`: Punkt mit gestricheltem Radius-Kreis
- `line/<typ>_<spitzen>.svg`: Linie, Spitzen `ohne`, `ende`, `anfang` oder `beide`
- `zone/<typ>.svg`: Fläche, gefüllt oder (``"gefuellt": false``) nur Rand
"""

import json
import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "data", "objektplanung.json")
OUT = os.path.join(ROOT, "icons", "planning")

# Wie src/planning/object_style.py (ARROWS_*)
ARROW_MODES = ("ohne", "ende", "anfang", "beide")
# Heller Rand um jede Form, wie bei den übrigen Hotbar-Icons
BORDER = "#EEF2F6"
# Flächen wie beim Gebiet halb durchscheinend, damit auch Schwarz auf der dunklen Hotbar lesbar bleibt
FILL_OPACITY = 0.5

_HEADER = '<svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">\n'
_FOOTER = "</svg>\n"


def _svg(*elements: str) -> str:
    return _HEADER + "".join(e + "\n" for e in elements) + _FOOTER


def text_icon(color: str) -> str:
    return _svg(
        f'<path d="M7 10H43V21H30.5V54H19.5V21H7Z" fill="{color}" fill-opacity="{FILL_OPACITY}" '
        f'stroke="{BORDER}" stroke-width="3.5" stroke-linejoin="round"/>',
        '<path d="M53 15V53M47.5 12.5C50.5 12.5 53 13.5 53 16.5C53 13.5 55.5 12.5 58.5 12.5'
        'M47.5 55.5C50.5 55.5 53 54.5 53 51.5C53 54.5 55.5 55.5 58.5 55.5" '
        f'stroke="{BORDER}" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>',
    )


def point_icon(color: str) -> str:
    # 15 Striche schließen den Kreis (Umfang 2π·26 ≈ 163,4) ohne Lücke an der Naht
    return _svg(
        f'<circle cx="32" cy="32" r="26" fill="{color}" fill-opacity="0.3" stroke="{color}" '
        'stroke-width="3" stroke-dasharray="6.8 4.1"/>',
        f'<circle cx="32" cy="32" r="12" fill="{color}" stroke="{BORDER}" stroke-width="3.5"/>',
    )


# Linie als Bogen von links unten nach rechts oben. Die Enden liegen fest am Rand des Icons; trägt ein Ende
# eine Spitze, sitzt deren Spitze dort und die Linie endet schon an ihrer Basis
_LINE_START = (10.0, 54.0)
_LINE_C1 = (14.0, 34.0)
_LINE_C2 = (28.0, 20.0)
_LINE_END = (56.0, 14.0)
_HEAD_LENGTH = 15.0
_HEAD_HALF_WIDTH = 9.0


def _unit(dx: float, dy: float) -> tuple[float, float]:
    length = math.hypot(dx, dy)
    return dx / length, dy / length


def _head(tip: tuple[float, float], direction: tuple[float, float], color: str) -> tuple[str, tuple[float, float]]:
    """Dreieck mit der Spitze in ``tip``, zeigend in ``direction``; dazu die Mitte seiner Basis."""
    ux, uy = direction
    base = (tip[0] - ux * _HEAD_LENGTH, tip[1] - uy * _HEAD_LENGTH)
    left = (base[0] - uy * _HEAD_HALF_WIDTH, base[1] + ux * _HEAD_HALF_WIDTH)
    right = (base[0] + uy * _HEAD_HALF_WIDTH, base[1] - ux * _HEAD_HALF_WIDTH)
    points = " ".join(f"{x:.1f} {y:.1f}" for x, y in (tip, left, right))
    path = f'<path d="M{points}Z" fill="{color}" stroke="{BORDER}" stroke-width="3.5" stroke-linejoin="round"/>'
    return path, base


def line_icon(color: str, arrows: str) -> str:
    start, end = _LINE_START, _LINE_END
    (ax, ay), (bx, by) = _LINE_C1, _LINE_C2
    heads = []
    if arrows in ("anfang", "beide"):
        head, start = _head(_LINE_START, _unit(_LINE_START[0] - ax, _LINE_START[1] - ay), color)
        heads.append(head)
    if arrows in ("ende", "beide"):
        head, end = _head(_LINE_END, _unit(_LINE_END[0] - bx, _LINE_END[1] - by), color)
        heads.append(head)
    curve = f"M{start[0]:.1f} {start[1]:.1f}C{ax} {ay} {bx} {by} {end[0]:.1f} {end[1]:.1f}"
    # Erst der helle Rand der Linie, dann die Spitzen, zuletzt die Linie in Farbe darüber
    return _svg(
        f'<path d="{curve}" stroke="{BORDER}" stroke-width="10" stroke-linecap="round"/>',
        *heads,
        f'<path d="{curve}" stroke="{color}" stroke-width="5.5" stroke-linecap="round"/>',
    )


def zone_icon(color: str, filled: bool) -> str:
    outline = "M10 22L30 9L55 19L51 49L20 55Z"
    if filled:
        return _svg(
            f'<path d="{outline}" fill="{color}" fill-opacity="{FILL_OPACITY}" stroke="{BORDER}" '
            'stroke-width="3.5" stroke-linejoin="round"/>'
        )
    # Ohne Füllung: Rand in der Typfarbe auf hellem Grund, innen frei
    return _svg(
        f'<path d="{outline}" stroke="{BORDER}" stroke-width="9" stroke-linejoin="round"/>',
        f'<path d="{outline}" stroke="{color}" stroke-width="4.5" stroke-linejoin="round"/>',
    )


def _write(folder: str, name: str, content: str) -> None:
    os.makedirs(os.path.join(OUT, folder), exist_ok=True)
    with open(os.path.join(OUT, folder, name + ".svg"), "w", encoding="utf-8") as f:
        f.write(content)


def main() -> None:
    with open(CATALOG, encoding="utf-8") as f:
        catalog = json.load(f)
    count = 0
    for entry in catalog.get("texte", []):
        _write("text", entry["id"], text_icon(entry["farbe"]))
        count += 1
    for entry in catalog.get("punkte", []):
        _write("point", entry["id"], point_icon(entry["farbe"]))
        count += 1
    for entry in catalog.get("pfeile", []):
        for arrows in ARROW_MODES:
            _write("line", f"{entry['id']}_{arrows}", line_icon(entry["farbe"], arrows))
            count += 1
    for entry in catalog.get("gebiete", []):
        _write("zone", entry["id"], zone_icon(entry["farbe"], bool(entry.get("gefuellt", True))))
        count += 1
    print(f"{count} Icons nach {OUT} geschrieben")


if __name__ == "__main__":
    main()
