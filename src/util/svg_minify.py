"""SVG-Text für die Ablage im Projekt verkleinern.

Die Katalog-SVGs (``svgs/``) tragen je eine eingebettete Roboto-Slab-Schrift als
``@font-face``-Regel (~25 KB), die Qt beim Zeichnen ignoriert. Ohne sie, ohne
DOCTYPE, ``<title>``, Kommentare und Einrückung bleibt rund ein Zwanzigstel übrig
und rendert pixelidentisch. Das Ergebnis landet in ``svg_content`` und als
``base64:``-Pfad in jedem Symbol des Projekts.
"""

import re

# Die @font-face-Regel samt Datenblock; die Regel `text { font-family: ... }` bleibt stehen
_FONT_FACE = re.compile(r"@font-face\s*\{[^}]*\}\s*", re.IGNORECASE)
# DOCTYPE, ggf. mit interner Teilmenge in eckigen Klammern
_DOCTYPE = re.compile(r"<!DOCTYPE[^>\[]*(?:\[[^\]]*\])?[^>]*>\s*", re.IGNORECASE | re.DOTALL)
_TITLE = re.compile(r"<title\b[^>]*?(?:/>|>.*?</title\s*>)\s*", re.IGNORECASE | re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Nur Zeilenumbrüche samt Einrückung zwischen Tags; ein einzelnes Leerzeichen
# zwischen Textelementen (z. B. zwei <tspan>) kann sichtbar sein und bleibt
_LINE_BREAKS_BETWEEN_TAGS = re.compile(r">[ \t]*(?:\r?\n|\r)\s*<")


def minify_svg(text: str) -> str:
    """``text`` ohne eingebettete Schrift, DOCTYPE, Titel, Kommentare und Zeilenumbrüche zwischen Tags.

    ``<defs>``, ``<clipPath>`` und ``<style>`` bleiben inhaltlich unangetastet.
    Im Zweifel (kein SVG erkennbar, unerwarteter Fehler) kommt der Text unverändert zurück.
    """
    if not isinstance(text, str) or "<svg" not in text:
        return text
    try:
        result = _COMMENT.sub("", text)
        result = _DOCTYPE.sub("", result)
        result = _TITLE.sub("", result)
        result = _FONT_FACE.sub("", result)
        result = _LINE_BREAKS_BETWEEN_TAGS.sub("><", result).strip()
    except Exception:
        return text
    return result if "<svg" in result else text
