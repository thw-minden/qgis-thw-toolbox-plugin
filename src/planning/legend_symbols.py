"""Legende der Objektplanung: aufgeräumte Symbole und Namen für Punkte, Linien und Gebiete.

Das Kartensymbol taugt in der Legende nicht: Radius-Kreis, Pfeilspitzen an beiden Enden und Maße hängen am
Objekt (datendefiniert) und werden im Legendenfeld verzerrt oder falsch gezeichnet. Hier entsteht aus der
tatsächlichen Gestaltung des Objekts ein einfaches Symbol in Legendengröße.
"""

from functools import lru_cache

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerLineSymbolLayer,
    QgsMarkerSymbol,
    QgsProject,
)
from qgis.PyQt.QtGui import QColor

from ..util.coordinates import WGS84, to_mgrs
from .catalog import load_catalog
from .expressions import POSITION_PLACEHOLDER
from .layers import ROLE_ARROWS, ROLE_POINTS, ROLE_ZONES
from .object_style import ARROWS_BOTH, ARROWS_END, ARROWS_START, object_style_of
from .zone_style import PATTERN_NONE, zone_style_of

# Muster der Gebiete → einfache Füllung (QgsSimpleFill)
_BRUSHES = {
    "voll": "solid",
    "schraeg": "b_diagonal",
    "schraeg_links": "f_diagonal",
    "kreuz": "diagonal_x",
    "waagerecht": "horizontal",
    "gitter": "cross",
    "punkte": "dense6",
    PATTERN_NONE: "no",
}


@lru_cache(maxsize=1)
def _catalog():
    return load_catalog()


def _type(role: str, feature):
    catalog = _catalog()
    types = {ROLE_ZONES: catalog.zone_types(), ROLE_ARROWS: catalog.pfeile, ROLE_POINTS: catalog.punkte}[role]
    type_id = feature.attribute("typ_id")
    return next((t for t in types if t.id == type_id), types[0] if types else None)


def _rgba(color: QColor) -> str:
    return f"{color.red()},{color.green()},{color.blue()},{color.alpha()}"


def legend_label(role: str, feature, layer) -> str:
    """Name des Objekts für die Legende; ``$POS`` wird wie auf der Karte zur UTMREF."""
    lines = [line.strip() for line in (feature.attribute("bezeichnung") or "").splitlines() if line.strip()]
    return resolve_position(" / ".join(lines), feature, layer)


def resolve_position(text: str, feature, layer) -> str:
    """``$POS`` wie auf der Karte durch die UTMREF des Objekts ersetzen – für Legende und KMZ-Export."""
    if POSITION_PLACEHOLDER not in text or not feature.hasGeometry():
        return text
    point = feature.geometry().centroid().asPoint()
    try:
        wgs = QgsCoordinateTransform(layer.crs(), WGS84, QgsProject.instance()).transform(point)
        return text.replace(POSITION_PLACEHOLDER, to_mgrs(wgs.y(), wgs.x()))
    except ValueError:
        return text


def legend_symbol(role: str, feature):
    """Einfaches Symbol, das aussieht wie das Objekt auf der Karte."""
    obj_type = _type(role, feature)
    color = obj_type.farbe if obj_type is not None else "#c62828"
    if role == ROLE_ZONES:
        style = zone_style_of(feature, color, obj_type.gefuellt if obj_type is not None else True)
        fill = QColor(style.fill)
        fill.setAlphaF(style.opacity)
        return QgsFillSymbol.createSimple(
            {
                "color": _rgba(fill),
                "style": _BRUSHES.get(style.pattern, "solid"),
                "outline_color": _rgba(QColor(style.stroke or style.fill)),
                "outline_style": "solid" if style.stroke else "no",
                "outline_width": "0.5",
                "outline_width_unit": "MM",
            }
        )
    style = object_style_of(feature, color)
    main = QColor(style.color)
    main.setAlphaF(style.opacity)
    if role == ROLE_ARROWS:
        symbol = QgsLineSymbol.createSimple(
            {"line_color": _rgba(main), "line_width": str(min(style.width, 1.0)), "capstyle": "flat"}
        )
        for placement, modes, angle in (
            (Qgis.MarkerLinePlacement.LastVertex, (ARROWS_END, ARROWS_BOTH), "0"),
            (Qgis.MarkerLinePlacement.FirstVertex, (ARROWS_START, ARROWS_BOTH), "180"),
        ):
            if style.arrows not in modes:
                continue
            head = QgsMarkerSymbol.createSimple(
                {"name": "filled_arrowhead", "color": _rgba(main), "outline_style": "no", "size": "2.5", "angle": angle}
            )
            marker_line = QgsMarkerLineSymbolLayer(True)
            marker_line.setPlacements(placement)
            marker_line.setSubSymbol(head)
            symbol.appendSymbolLayer(marker_line)
        return symbol
    # Punkt: Kreis in der Punktfarbe, mit Radius darunter ein größerer Kreis wie der Radius-Kreis
    symbol = QgsMarkerSymbol.createSimple(
        {
            "name": "circle",
            "color": _rgba(main),
            "outline_color": "255,255,255,255",
            "outline_width": "0.3",
            "size": "2.6",
        }
    )
    if float(feature.attribute("radius") or 0) > 0:
        circle = style.circle_style()
        circle_fill = QColor(circle.fill)
        circle_fill.setAlphaF(0.0 if circle.pattern == PATTERN_NONE else circle.opacity)
        ring = QgsMarkerSymbol.createSimple(
            {
                "name": "circle",
                "color": _rgba(circle_fill),
                "outline_color": _rgba(QColor(circle.stroke or circle.fill)),
                "outline_style": "solid" if circle.stroke else "no",
                "outline_width": "0.4",
                "size": "5",
            }
        )
        symbol.insertSymbolLayer(0, ring.symbolLayer(0).clone())
    return symbol
