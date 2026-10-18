"""Farben, die im hellen und im dunklen QGIS-Theme lesbar bleiben."""

from qgis.PyQt.QtGui import QColor, QPalette
from qgis.PyQt.QtWidgets import QApplication

# Anteil der Textfarbe an gedämpftem Text, der Rest ist Hintergrund
_MUTED_TEXT_SHARE = 0.62


def muted_color() -> str:
    """Gedämpfte Textfarbe für Hinweise: zwischen Text- und Hintergrundfarbe des aktuellen Themes.

    Feste Grautöne (#666, ``palette(mid)``) verschwinden auf dunklem Hintergrund.
    """
    palette = QApplication.palette()
    text = palette.color(QPalette.ColorRole.WindowText)
    window = palette.color(QPalette.ColorRole.Window)

    def mix(a: int, b: int) -> int:
        return round(a * _MUTED_TEXT_SHARE + b * (1 - _MUTED_TEXT_SHARE))

    return QColor(
        mix(text.red(), window.red()), mix(text.green(), window.green()), mix(text.blue(), window.blue())
    ).name()


def muted_style() -> str:
    """Stylesheet für Hinweistexte, z. B. ``label.setStyleSheet(muted_style())``."""
    return f"color: {muted_color()};"
