"""Neue Zeichen ans Kartenfenster anpassen: beim Reinziehen nicht riesig, winzige Zeichen heranzoomen."""

from qgis.core import QgsPointXY, QgsRenderContext, QgsUnitTypes

_MM = QgsUnitTypes.RenderUnit.RenderMillimeters
# Neue Zeichen höchstens so breit wie dieser Anteil des Kartenfensters …
MAX_SHARE = 0.5
# … in einem sehr kleinen Kartenfenster aber bis zu dieser Breite auf dem Bildschirm (mm)
MIN_LIMIT_MM = 30.0
# Schmaler als das (mm auf dem Bildschirm) ist ein Zeichen kaum zu sehen – dann zoomt die Karte heran …
TINY_MM = 3.0
# … bis es so breit ist wie dieser Anteil des Kartenfensters
ZOOM_SHARE = 0.1


def fit_size(canvas, size: float, fixed: bool, min_size: float) -> float:
    """``size`` so verkleinern, dass das Zeichen höchstens halb so breit ist wie das Kartenfenster.

    ``fixed``: Größe in mm auf dem Bildschirm, sonst in Karteneinheiten (siehe renderer.size_unit).
    In einem sehr kleinen Kartenfenster darf es bis ``MIN_LIMIT_MM`` breit werden.
    """
    width = _map_width(canvas, size, fixed)
    limit = max(_view_width(canvas) * MAX_SHARE, _mm_to_map(canvas, MIN_LIMIT_MM))
    if limit <= 0 or width <= limit:
        return size
    return max(min_size, round(size * limit / width, 1))


def zoom_if_tiny(canvas, size: float, fixed: bool, center: QgsPointXY) -> bool:
    """Ist ein mit der Karte skaliertes Zeichen kaum zu sehen, auf ``center`` (Karten-CRS) heranzoomen.

    Zeichen mit fester Bildschirmgröße (``fixed``) bleiben beim Zoomen gleich groß, da hilft es nicht.
    """
    if fixed or size <= 0 or size >= _mm_to_map(canvas, TINY_MM):
        return False
    view = _view_width(canvas)
    if view <= 0:
        return False
    canvas.zoomByFactor(size / (view * ZOOM_SHARE), center)
    return True


def _map_width(canvas, size: float, fixed: bool) -> float:
    """Breite eines Zeichens der Größe ``size`` in Karteneinheiten beim aktuellen Maßstab."""
    return _mm_to_map(canvas, size) if fixed else size


def _mm_to_map(canvas, mm: float) -> float:
    context = QgsRenderContext.fromMapSettings(canvas.mapSettings())
    return context.convertToMapUnits(mm, _MM)


def _view_width(canvas) -> float:
    """Breite des Kartenfensters in Karteneinheiten (auch bei gedrehter Karte)."""
    return canvas.mapUnitsPerPixel() * canvas.mapSettings().outputSize().width()
