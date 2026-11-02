"""GeoPackage-Layer der Objektplanung (Zelte, Fahrzeuge, Strom, Beleuchtung, Gebiete, Pfeile, Punkte, Texte)."""

import base64
import os
import time
from dataclasses import dataclass

from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsExpression,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFields,
    QgsFillSymbol,
    QgsFontMarkerSymbolLayer,
    QgsGeometryGeneratorSymbolLayer,
    QgsLinePatternFillSymbolLayer,
    QgsLineSymbol,
    QgsMarkerLineSymbolLayer,
    QgsMarkerSymbol,
    QgsMarkerSymbolLayer,
    QgsPalLayerSettings,
    QgsPointPatternFillSymbolLayer,
    QgsProject,
    QgsProperty,
    QgsRendererCategory,
    QgsSimpleFillSymbolLayer,
    QgsSimpleMarkerSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsSvgMarkerSymbolLayer,
    QgsSymbolLayer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import QObject, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont

from ..layer.fields import _T_BOOL, _T_DOUBLE, _T_STRING
from ..logging_utils import get_logger
from ..paths import in_temp_dir, temp_dir
from ..tools.layer_functions import update_layer_tree_sources
from ..util.svg_minify import minify_svg
from ..util.temp_files import remove_temp_file
from .catalog import LEGACY_ZONE_IDS, Catalog
from .expressions import meters_text_expr, metric_length_expr, note_label_expr
from .history import PlanningHistory
from .object_style import (
    ARROWS_BOTH,
    ARROWS_END,
    ARROWS_START,
    CIRCLE_OPACITY,
    CIRCLE_STROKE_WIDTH,
    DEFAULT_LINE_WIDTH,
)
from .zone_style import DEFAULT_OPACITY, DEFAULT_STROKE_WIDTH, DOT_PATTERNS, HATCH_LINES, STYLE_FIELD

logger = get_logger(__name__)

GPKG_SUFFIX = "_objektplanung"
# Nach einer Änderung höchstens so lange das alte Kartenbild zeigen (ms), siehe PlanningLayers._hold_preview
_HOLD_PREVIEW_MS = 5000
GROUP_NAME = "Objektplanung"
# Custom property, über die wir unsere Layer auch nach Umbenennen wiederfinden
ROLE_PROPERTY = "thw_toolbox/objektplanung"
# Vor der Umbenennung (Lager- → Objektplanung) angelegte Layer weiter erkennen
_LEGACY_ROLE_PROPERTY = "thw_toolbox/lagerplanung"

ROLE_TENTS = "zelte"
ROLE_VEHICLES = "fahrzeuge"
ROLE_CABLES = "leitungen"
ROLE_DISTRIBUTORS = "verteiler"
ROLE_GENERATORS = "stromerzeuger"
ROLE_LIGHTS = "beleuchtung"
ROLE_ZONES = "gebiete"
ROLE_ARROWS = "pfeile"
ROLE_POINTS = "punkte"
ROLE_TEXTS = "texte"
# Kein Objektplanungs-Layer: unter dieser Rolle führt die Auswahl die taktischen Zeichen (Marker-Layer) mit
ROLE_MARKERS = "zeichen"

# Beschriftung je Objekt ein/aus (leer = an); alle Objektplanungs-Layer haben das Feld
LABEL_FIELD = "beschriftung"
_LABEL_SHOW_EXPR = f'coalesce("{LABEL_FIELD}", true)'
_SHOW = getattr(QgsPalLayerSettings, "Property", QgsPalLayerSettings).Show
# QgsPropertyCollection erwartet den Schlüssel als int (unter PyQt6 ist das Enum keiner)
_SHOW_PROPERTY = _SHOW if isinstance(_SHOW, int) else _SHOW.value
# Schriftgröße der Beschriftung je Objekt in pt (leer = Standard)
LABEL_SIZE_FIELD = "schriftgroesse"
LABEL_SIZE_DEFAULT_PT = 8.0
_LABEL_SIZE_EXPR = f'coalesce("{LABEL_SIZE_FIELD}", {LABEL_SIZE_DEFAULT_PT})'
_SIZE = getattr(QgsPalLayerSettings, "Property", QgsPalLayerSettings).Size
_SIZE_PROPERTY = _SIZE if isinstance(_SIZE, int) else _SIZE.value
_COLOR = getattr(QgsPalLayerSettings, "Property", QgsPalLayerSettings).Color
_COLOR_PROPERTY = _COLOR if isinstance(_COLOR, int) else _COLOR.value


def _label_property(name: str):
    """Schlüssel einer datendefinierten Beschriftungs-Eigenschaft als int (unter PyQt6 ist das Enum keiner)."""
    prop = getattr(getattr(QgsPalLayerSettings, "Property", QgsPalLayerSettings), name)
    return prop if isinstance(prop, int) else prop.value


# Beschriftung je Objekt frei platziert (Texte, Punkte, Linien, Gebiete): Versatz der Mitte zum Ankerpunkt
# in Layer-Einheiten und Drehung in Grad im Uhrzeigersinn; leer = Platzierung wie gewohnt
LABEL_DX_FIELD = "beschriftung_dx"
LABEL_DY_FIELD = "beschriftung_dy"
LABEL_ANGLE_FIELD = "beschriftung_winkel"
LABEL_EDIT_ROLES = (ROLE_TEXTS, ROLE_POINTS, ROLE_ARROWS, ROLE_ZONES)
# Einteilung der Darstellung von Punkten, Linien und Gebieten: nach Typ – außer bei Objekten, deren Name nicht auf
# der Karte steht. Die bekommen eine eigene Kategorie mit ihrem Namen, damit die Legende ihn zeigt
OBJECT_KEY_PREFIX = "obj:"
_LEGEND_FIELDS = {LABEL_FIELD, "bezeichnung", "typ_id"}
LEGEND_KEY_EXPR = (
    f"if(coalesce(\"{LABEL_FIELD}\", true) OR coalesce(\"bezeichnung\", '') = '', "
    f"\"typ_id\", '{OBJECT_KEY_PREFIX}' || to_string($id))"
)


def legend_key(role: str, feature) -> tuple[str, str]:
    """(Kategorie, Bezeichnung) eines Objekts wie ``LEGEND_KEY_EXPR``; die Bezeichnung nur bei eigener Kategorie."""
    name = (feature.attribute("bezeichnung") or "").strip()
    if not name or label_visible(role, feature):
        return str(feature.attribute("typ_id") or ""), ""
    lines = [line.strip() for line in name.splitlines() if line.strip()]
    return f"{OBJECT_KEY_PREFIX}{feature.id()}", " / ".join(lines)


# Maße auf der Karte je Objekt (leer = aus): Radius eines Punkts, Segmentlängen einer Linie bzw. eines Gebiets
MEASURE_FIELD = "masse"
MEASURE_ROLES = (ROLE_POINTS, ROLE_ARROWS, ROLE_ZONES)
_MEASURE_ON_EXPR = f'coalesce("{MEASURE_FIELD}", false)'
# Schrift der Maße (mm) und Abstand zur Kante
DIMENSION_SIZE_MM = 2.5
DIMENSION_GAP_MM = 1.6
# Von Hand angepasste Maße je Objekt (JSON): Schlüssel = Nummer des Segments (1, 2, …) bzw. „r“ für den Radius,
# Werte: Versatz ``dx``/``dy`` (Segmente in mm entlang/quer zur Schrift, Radius in Metern), Faktor ``s``,
# ``aus`` = ausgeblendet. Leer = alle Maße an ihrem gewohnten Platz
DIMENSION_ADJUST_FIELD = "masse_anpassung"
RADIUS_DIMENSION_KEY = "r"


def _dimension_adjust(key_expr: str) -> str:
    """Anpassung eines Maßes als Map (oder NULL) – ``key_expr`` ergibt den Schlüssel."""
    return f"map_get(json_to_map(coalesce(\"{DIMENSION_ADJUST_FIELD}\", '{{}}')), {key_expr})"


def _dimension_text(adjust: str, text_expr: str) -> str:
    """Ausgeblendete Maße zeichnen nichts."""
    return f"if(coalesce(map_get({adjust}, 'aus'), false), '', {text_expr})"


def _dimension_size(adjust: str) -> str:
    return f"{DIMENSION_SIZE_MM} * coalesce(map_get({adjust}, 's'), 1)"


_POSITION_PROPERTY = _label_property("PositionPoint")
_ROTATION_PROPERTY = _label_property("LabelRotation")
_HALI_PROPERTY = _label_property("Hali")
_VALI_PROPERTY = _label_property("Vali")


def label_anchor_expr(role: str) -> str:
    """Ankerpunkt der Beschriftung als Ausdruck – muss zu ``controller.label_anchor`` passen."""
    if role == ROLE_ZONES:
        return "centroid($geometry)"
    if role == ROLE_ARROWS:
        return "line_interpolate_point($geometry, length($geometry) / 2)"
    return "$geometry"


# Taktisches Zeichen je Objekt ein/aus (leer = an); nur die Layer, deren Objekte eins tragen, haben das Feld
SIGN_FIELD = "zeichen"
SIGN_ROLES = (ROLE_VEHICLES, ROLE_GENERATORS, ROLE_LIGHTS)
_SIGN_ON_EXPR = f'coalesce("{SIGN_FIELD}", true)'
_LAYER_ENABLED = QgsSymbolLayer.Property.LayerEnabled
_LAYER_ENABLED_KEY = _LAYER_ENABLED if isinstance(_LAYER_ENABLED, int) else _LAYER_ENABLED.value
# Die taktischen Zeichen liegen eingebettet (``base64:``) im Ausdruck des SVG-Markers, nicht als Pfad in den
# Plugin-Ordner – so rendert das Projekt auch ohne Plugin. Ältere Layer werden beim Übernehmen einmal umgestellt
_SVG_NAME = QgsSymbolLayer.Property.Name
_SVG_NAME_KEY = _SVG_NAME if isinstance(_SVG_NAME, int) else _SVG_NAME.value
_EMBEDDED_PREFIX = "base64:"

# Gestaltung je Gebiet (JSON im Feld ``gestaltung``, siehe zone_style); leer = Darstellung nach Typ.
# Ausdrücke, die NULL ergeben, lassen den festen Wert des Symbols (Typfarbe) stehen.
_STYLE = f'json_to_map("{STYLE_FIELD}")'


def _style_get(key: str) -> str:
    return f"map_get({_STYLE}, '{key}')"


@dataclass(frozen=True)
class _AreaExpressions:
    """Ausdrücke für Muster, Füllung und Rand einer Fläche – eines Gebiets oder des Radius-Kreises eines Punkts."""

    pattern: str
    fill: str
    stroke_color: str
    stroke_width: str
    stroke_style: str


def _area_expressions(
    style_map: str,
    filled: bool = True,
    color_fallback: str | None = None,
    opacity: float = DEFAULT_OPACITY,
    stroke_width: float = DEFAULT_STROKE_WIDTH,
) -> _AreaExpressions:
    """``style_map``: Ausdruck der Gestaltung als Map (Schlüssel wie ``ZoneStyle``).

    Ohne eigene Gestaltung entscheidet der Typ: gefüllt („voll“) oder nur der Rand („ohne“).
    ``color_fallback``: Farbe, der Füllung und Rand folgen, solange die Fläche keine eigene hat
    (der Kreis eines Punkts folgt dessen Farbe).
    """

    def get(key: str) -> str:
        return f"map_get({style_map}, '{key}')"

    fill = get("fill") if color_fallback is None else f"coalesce({get('fill')}, {color_fallback})"
    stroke = f"nullif({get('stroke')}, '')"
    if color_fallback is not None:
        stroke = f"if({style_map} IS NULL, {color_fallback}, {stroke})"
    return _AreaExpressions(
        pattern=f"coalesce({get('pattern')}, '{'voll' if filled else 'ohne'}')",
        fill=f"set_color_part({fill}, 'alpha', round(255 * coalesce({get('opacity')}, {opacity})))",
        stroke_color=stroke,
        stroke_width=f"coalesce({get('stroke_width')}, {stroke_width})",
        stroke_style=f"if({get('stroke')} = '', 'no', coalesce({get('stroke_style')}, 'solid'))",
    )


# Gestaltung je Pfeil, Punkt und Text (JSON im Feld ``gestaltung``, siehe object_style); leer = nach Typ
_OBJECT_COLOR_EXPR = (
    f"set_color_part({_style_get('color')}, 'alpha', round(255 * coalesce({_style_get('opacity')}, 1)))"
)
# Radius-Kreis eines Punkts: gestaltet wie ein Gebiet (Schlüssel ``circle``), ohne eigene Angabe in der Punktfarbe
_CIRCLE_EXPRESSIONS = _area_expressions(
    _style_get("circle"),
    color_fallback=_style_get("color"),
    opacity=CIRCLE_OPACITY,
    stroke_width=CIRCLE_STROKE_WIDTH,
)
_LINE_WIDTH_EXPR = f"coalesce({_style_get('width')}, {DEFAULT_LINE_WIDTH})"
_ARROWS_EXPR = f"coalesce({_style_get('arrows')}, '{ARROWS_END}')"
# Wie object_style.arrow_size_for: gewählte Größe oder automatisch nach der Linienbreite
_ARROW_SIZE_EXPR = (
    f"if(coalesce({_style_get('arrow_size')}, 0) > 0, {_style_get('arrow_size')}, max(3, {_LINE_WIDTH_EXPR} * 4))"
)


def _arrow_trim_expr(modes) -> str:
    """Linie unter der Spitze um eine Linienbreite kürzen, sonst ragt ihr rundes Ende vorn heraus."""
    listed = ", ".join(f"'{m}'" for m in modes)
    return f"if({_ARROWS_EXPR} IN ({listed}), min({_LINE_WIDTH_EXPR}, 0.9 * {_ARROW_SIZE_EXPR} / 2), 0)"


# Stand der Darstellung von Gebieten, Linien und Punkten; ältere Layer werden beim Öffnen einmal neu aufgebaut
_STYLE_VERSION_PROPERTY = "thw_toolbox/objektplanung_darstellung"
_STYLE_VERSION = 7  # 7: Kategorie „Weitere Objekte“ mit schlichtem Symbol
# Ob die Darstellung die Maß-Ebenen enthält (nur, solange ein Objekt seine Maße zeigt)
_DIMENSIONS_PROPERTY = "thw_toolbox/objektplanung_masse"
_VERSIONED_ROLES = (ROLE_ZONES, ROLE_ARROWS, ROLE_POINTS)

# Kategorie „alle übrigen Werte“ der Gebiete, Linien und Punkte: Objekte ohne eigene Kategorie – in der
# Attributtabelle geändert, eingefügt, mit einer älteren Version gezeichnet – bleiben sichtbar, bis das
# Plugin ihre Kategorie nachträgt (dann wieder in ihrer Typfarbe)
_FALLBACK_KEY = ""
_FALLBACK_LABEL = "Weitere Objekte"

_PLAIN_LABEL_EXPR = "nullif(\"bezeichnung\", '')"
# Gebiete tragen nur ihre eigene Bezeichnung – außer den früheren Typen (Gefahrenbereich …), deren Farbe nichts
# erklärt: die zeigen ohne Bezeichnung weiter ihren Typ. Frühere Versionen zeigten ihn bei allen Gebieten
_LEGACY_ZONE_IDS_SQL = ", ".join(f"'{type_id}'" for type_id in LEGACY_ZONE_IDS)
_ZONE_LABEL_EXPR = f'coalesce(nullif("bezeichnung", \'\'), if("typ_id" IN ({_LEGACY_ZONE_IDS_SQL}), "typ", NULL))'
_LEGACY_ZONE_LABEL_EXPR = 'coalesce(nullif("bezeichnung", \'\'), "typ")'

# Zelte zeigen ohne eigene Bezeichnung ihren Kurznamen („SG30“); frühere Versionen den ganzen Typ
_LEGACY_TENT_LABEL_EXPR = 'coalesce(nullif("bezeichnung", \'\'), "typ")'
_TENT_LABEL_PREFIX = "coalesce(nullif(\"bezeichnung\", ''), map_get(map("

# Schraffur-Abstand und Linienstärke (mm)
_HATCH_DISTANCE_MM = 2.5
_HATCH_WIDTH_MM = 0.35

# Zelte und Fahrzeuge: Rechteck-Grundflächen mit identischem Schema
_FOOTPRINT_FIELDS = [
    ("typ_id", _T_STRING),
    ("typ", _T_STRING),
    ("bezeichnung", _T_STRING),
    ("laenge", _T_DOUBLE),
    ("breite", _T_DOUBLE),
    ("abspannung", _T_DOUBLE),
    ("rotation", _T_DOUBLE),
]
FOOTPRINT_ROLES = (ROLE_TENTS, ROLE_VEHICLES)
# Punkt-Objekte, die per Klick gesetzt werden (Leitungen fangen an ihnen)
POINT_ROLES = (ROLE_DISTRIBUTORS, ROLE_GENERATORS, ROLE_LIGHTS)
# Frei gezeichnete Formen mit Typ (= Farbe) und Bezeichnung
_SHAPE_FIELDS = [("typ_id", _T_STRING), ("typ", _T_STRING), ("bezeichnung", _T_STRING)]

_LAYER_SPECS = {
    ROLE_TENTS: {"title": "Zelte", "geometry": "Polygon", "fields": _FOOTPRINT_FIELDS},
    ROLE_VEHICLES: {"title": "Fahrzeuge", "geometry": "Polygon", "fields": _FOOTPRINT_FIELDS},
    ROLE_CABLES: {
        "title": "Leitungen",
        "geometry": "LineString",
        "fields": [
            ("typ_id", _T_STRING),
            ("typ", _T_STRING),
            ("bezeichnung", _T_STRING),
            ("max_laenge", _T_DOUBLE),
            ("laenge", _T_DOUBLE),
        ],
    },
    ROLE_DISTRIBUTORS: {
        "title": "Verteiler",
        "geometry": "Point",
        "fields": [
            ("typ_id", _T_STRING),
            ("typ", _T_STRING),
            ("bezeichnung", _T_STRING),
        ],
    },
    ROLE_GENERATORS: {
        "title": "Stromerzeuger",
        "geometry": "Point",
        "fields": [
            ("typ_id", _T_STRING),
            ("typ", _T_STRING),
            ("bezeichnung", _T_STRING),
            ("leistung_kva", _T_DOUBLE),
        ],
    },
    ROLE_LIGHTS: {
        "title": "Beleuchtung",
        "geometry": "Point",
        "fields": [
            ("typ_id", _T_STRING),
            ("typ", _T_STRING),
            ("bezeichnung", _T_STRING),
            ("leistung_w", _T_DOUBLE),
            ("radius", _T_DOUBLE),
        ],
    },
    ROLE_ZONES: {"title": "Gebiete", "geometry": "Polygon", "fields": _SHAPE_FIELDS},
    ROLE_ARROWS: {"title": "Pfeile", "geometry": "LineString", "fields": _SHAPE_FIELDS},
    ROLE_POINTS: {"title": "Punkte", "geometry": "Point", "fields": [*_SHAPE_FIELDS, ("radius", _T_DOUBLE)]},
    ROLE_TEXTS: {"title": "Texte", "geometry": "Point", "fields": _SHAPE_FIELDS},
}
# Rollen mit eigener Gestaltung je Objekt (Feld ``gestaltung``): Gebiete (zone_style), die übrigen object_style
STYLE_ROLES = (ROLE_ZONES, ROLE_ARROWS, ROLE_POINTS, ROLE_TEXTS)
OBJECT_STYLE_ROLES = (ROLE_ARROWS, ROLE_POINTS, ROLE_TEXTS)
# Punkte und Texte: per Klick gesetzt, beschrieben über die Bezeichnung (auch mehrzeilig)
NOTE_ROLES = (ROLE_POINTS, ROLE_TEXTS)
# Schriftgröße neuer Texte (pt) – größer als die Beschriftung der Objekte
TEXT_SIZE_DEFAULT_PT = 10.0
# Reihenfolge im Layerbaum: Punkte oben, Flächen unten
_ROLE_ORDER = (
    ROLE_TEXTS,
    ROLE_POINTS,
    ROLE_GENERATORS,
    ROLE_DISTRIBUTORS,
    ROLE_LIGHTS,
    ROLE_ARROWS,
    ROLE_CABLES,
    ROLE_VEHICLES,
    ROLE_TENTS,
    ROLE_ZONES,
)
ALL_ROLES = _ROLE_ORDER


def build_fields(role: str) -> QgsFields:
    fields = QgsFields()
    for name, typ in _LAYER_SPECS[role]["fields"]:
        fields.append(QgsField(name, typ))
    fields.append(QgsField(LABEL_FIELD, _T_BOOL))
    fields.append(QgsField(LABEL_SIZE_FIELD, _T_DOUBLE))
    if role in SIGN_ROLES:
        fields.append(QgsField(SIGN_FIELD, _T_BOOL))
    if role in STYLE_ROLES:
        fields.append(QgsField(STYLE_FIELD, _T_STRING))
    if role in LABEL_EDIT_ROLES:
        for name in (LABEL_DX_FIELD, LABEL_DY_FIELD, LABEL_ANGLE_FIELD):
            fields.append(QgsField(name, _T_DOUBLE))
    if role in MEASURE_ROLES:
        fields.append(QgsField(MEASURE_FIELD, _T_BOOL))
        fields.append(QgsField(DIMENSION_ADJUST_FIELD, _T_STRING))
    return fields


def _flag(value, default: bool) -> bool:
    """Ja/Nein-Attribut; NULL (None bzw. QVariant-NULL unter QGIS 3) ergibt ``default``."""
    if value is None or (hasattr(value, "isNull") and value.isNull()):
        return default
    return bool(value)


def project_has_planning(project: QgsProject) -> bool:
    """Liegen Layer der Objektplanung im Projekt (auch aus früheren Versionen)?"""
    return any(
        (lyr.customProperty(ROLE_PROPERTY) or lyr.customProperty(_LEGACY_ROLE_PROPERTY)) in _LAYER_SPECS
        for lyr in project.mapLayers().values()
    )


def label_visible(role: str, feature) -> bool:
    """Wird die Beschriftung des Objekts bzw. taktischen Zeichens auf der Karte gezeigt?"""
    field = "show_label" if role == ROLE_MARKERS else LABEL_FIELD
    if feature.fields().indexFromName(field) < 0:
        return role != ROLE_MARKERS
    return _flag(feature.attribute(field), default=role != ROLE_MARKERS)


def label_size(feature) -> float:
    """Schriftgröße der Beschriftung eines Objekts in pt."""
    idx = feature.fields().indexFromName(LABEL_SIZE_FIELD)
    value = feature.attribute(idx) if idx >= 0 else None
    if value is None or (hasattr(value, "isNull") and value.isNull()) or float(value) <= 0:
        return LABEL_SIZE_DEFAULT_PT
    return float(value)


def sign_visible(feature) -> bool:
    """Zeigt das Objekt (Fahrzeug, Stromerzeuger, Leuchte) sein taktisches Zeichen?"""
    if feature.fields().indexFromName(SIGN_FIELD) < 0:
        return True
    return _flag(feature.attribute(SIGN_FIELD), default=True)


class PlanningLayers(QObject):
    """Legt die Objektplanungs-Layer an bzw. findet sie im Projekt wieder.

    Gespeichert wird in ``<projekt>_objektplanung.gpkg`` neben der
    Projektdatei (bzw. im Plugin-``tmp/`` solange das Projekt ungespeichert
    ist). ``changed`` feuert, sobald sich Features ändern — das Dock
    aktualisiert damit seine Bilanz. ``selection_changed`` feuert, wenn sich
    die QGIS-Auswahl in einem der Layer ändert.
    """

    changed = pyqtSignal()
    selection_changed = pyqtSignal()

    def __init__(self, plugin_dir: str, catalog: Catalog, parent=None):
        super().__init__(parent)
        self.plugin_dir = plugin_dir
        self.catalog = catalog
        self._layers: dict[str, QgsVectorLayer] = {}
        # Eingebettete Zeichen je Katalogpfad (``svgs/<rel>``), einmal gelesen
        self._sign_data: dict[str, str] = {}
        # Änderungen gesammelt melden: Mehrere Schreibvorgänge hintereinander (Verschieben einer gemischten
        # Auswahl, Attribute und Geometrie) bauen Details, Dock und Werkzeuge nur einmal neu auf –
        # und erst, nachdem QGIS mit dem Neuzeichnen beginnen konnte
        self._changed_timer = QTimer(self)
        self._changed_timer.setSingleShot(True)
        self._changed_timer.setInterval(0)
        self._changed_timer.timeout.connect(self.changed)
        # Rückgängig / Wiederherstellen: jede Änderung unten wird mitgeschrieben
        self.history = PlanningHistory(self)
        # Karte, deren Zwischenbilder beim Neuzeichnen nach einer Änderung zurückgehalten werden (siehe _hold_preview)
        self.canvas = None
        self._held_interval: int | None = None

    # ------------------------------------------------------------------
    # Zugriff
    # ------------------------------------------------------------------

    def layer(self, role: str) -> QgsVectorLayer | None:
        lyr = self._layers.get(role)
        if lyr is not None:
            try:
                lyr.id()
                return lyr
            except RuntimeError:
                self._layers.pop(role, None)
        return None

    @property
    def tents(self) -> QgsVectorLayer | None:
        return self.layer(ROLE_TENTS)

    @property
    def vehicles(self) -> QgsVectorLayer | None:
        return self.layer(ROLE_VEHICLES)

    @property
    def cables(self) -> QgsVectorLayer | None:
        return self.layer(ROLE_CABLES)

    @property
    def distributors(self) -> QgsVectorLayer | None:
        return self.layer(ROLE_DISTRIBUTORS)

    def all_layers(self) -> list[QgsVectorLayer]:
        return [lyr for lyr in (self.layer(r) for r in _ROLE_ORDER) if lyr is not None]

    # ------------------------------------------------------------------
    # Anlegen / Laden
    # ------------------------------------------------------------------

    def ensure(self, crs: QgsCoordinateReferenceSystem) -> bool:
        """Stellt sicher, dass alle Layer im Projekt sind. False bei Fehler."""
        self._adopt_project_layers()
        missing = [r for r in _ROLE_ORDER if self.layer(r) is None]
        if not missing:
            return True

        proj = QgsProject.instance()
        gpkg = self._gpkg_path(proj)
        for role in missing:
            lyr = self._open_or_create(gpkg, role, crs)
            if lyr is None:
                return False
            self._register(lyr, role)
        return True

    def _adopt_project_layers(self) -> None:
        for lyr in QgsProject.instance().mapLayers().values():
            role = lyr.customProperty(ROLE_PROPERTY) or lyr.customProperty(_LEGACY_ROLE_PROPERTY)
            if role in _LAYER_SPECS and self.layer(role) is None and isinstance(lyr, QgsVectorLayer):
                lyr.setCustomProperty(ROLE_PROPERTY, role)
                _ensure_label_switch(lyr)
                _ensure_label_transform(lyr, role)
                self._ensure_sign_switch(lyr, role)
                self._ensure_style(lyr, role)
                self._layers[role] = lyr
                self._connect(lyr)

    def _gpkg_path(self, proj: QgsProject) -> str:
        pfile = proj.fileName()
        if pfile:
            return os.path.splitext(pfile)[0] + GPKG_SUFFIX + ".gpkg"
        tmp_dir = temp_dir()
        os.makedirs(tmp_dir, exist_ok=True)
        return os.path.join(tmp_dir, f"projekt_{int(time.time())}{GPKG_SUFFIX}.gpkg")

    def _open_or_create(self, gpkg: str, role: str, crs: QgsCoordinateReferenceSystem) -> QgsVectorLayer | None:
        title = _LAYER_SPECS[role]["title"]
        uri = f"{gpkg}|layername={role}"
        if os.path.exists(gpkg):
            lyr = QgsVectorLayer(uri, title, "ogr")
            if lyr.isValid():
                return lyr

        spec = _LAYER_SPECS[role]
        mem = QgsVectorLayer(f"{spec['geometry']}?crs={crs.authid()}", role, "memory")
        mem.dataProvider().addAttributes(build_fields(role).toList())
        mem.updateFields()

        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.layerName = role
        opts.actionOnExistingFile = (
            QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
            if os.path.exists(gpkg)
            else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
        )
        result = QgsVectorFileWriter.writeAsVectorFormatV3(mem, gpkg, QgsProject.instance().transformContext(), opts)
        if result[0] != QgsVectorFileWriter.WriterError.NoError:
            logger.error("Objektplanungs-Layer %s konnte nicht angelegt werden: %s", role, result[1])
            return None
        lyr = QgsVectorLayer(uri, title, "ogr")
        if not lyr.isValid():
            logger.error("Objektplanungs-Layer %s ist ungültig: %s", role, uri)
            return None
        return lyr

    def _register(self, lyr: QgsVectorLayer, role: str) -> None:
        lyr.setCustomProperty(ROLE_PROPERTY, role)
        self.apply_style(lyr, role)
        _ensure_label_switch(lyr)
        _ensure_label_transform(lyr, role)
        self._ensure_sign_switch(lyr, role)
        self._ensure_style(lyr, role)

        proj = QgsProject.instance()
        proj.addMapLayer(lyr, False)
        root = proj.layerTreeRoot()
        group = root.findGroup(GROUP_NAME) or root.insertGroup(0, GROUP_NAME)
        # Position innerhalb der Gruppe nach _ROLE_ORDER
        index = sum(1 for r in _ROLE_ORDER[: _ROLE_ORDER.index(role)] if self.layer(r) is not None)
        group.insertLayer(index, lyr)

        self._layers[role] = lyr
        self._connect(lyr)

    def _connect(self, lyr: QgsVectorLayer) -> None:
        # Auswahl dezent blau statt QGIS-Gelb; den Rahmen zeichnet das Auswahlwerkzeug
        try:
            props = lyr.selectionProperties()
            props.setSelectionColor(QColor(13, 153, 255, 60))
            props.setSelectionRenderingMode(Qgis.SelectionRenderingMode.CustomColor)
        except AttributeError:
            pass
        # Auch Änderungen mit QGIS-eigenen Werkzeugen (Verschieben, Löschen,
        # Attributtabelle) sollen die Bilanz aktualisieren.
        for signal in (lyr.featureAdded, lyr.featureDeleted, lyr.geometryChanged, lyr.afterCommitChanges):
            try:
                signal.connect(self._emit_changed)
            except (TypeError, RuntimeError):
                pass
        try:
            lyr.selectionChanged.connect(self._emit_selection_changed)
        except (TypeError, RuntimeError):
            pass

    def _emit_changed(self, *args):
        self.notify_changed()

    def _hold_preview(self):
        """Bis die Karte nach dieser Änderung fertig gezeichnet ist, das alte Bild stehen lassen.

        Jede Änderung an einem beschrifteten Layer lässt QGIS alle Beschriftungen des Projekts neu setzen.
        Dauert das länger als das Vorschau-Intervall der Karte, zeigt sie zwischendurch ein Bild ohne
        Beschriftungen – Texte und Beschreibungen verschwinden kurz. Mit langem Intervall wechselt das
        Bild erst, wenn es fertig ist.
        """
        canvas = self.canvas
        if canvas is None or self._held_interval is not None:
            return
        self._held_interval = canvas.mapUpdateInterval()
        canvas.setMapUpdateInterval(_HOLD_PREVIEW_MS)
        canvas.mapCanvasRefreshed.connect(self.release_preview)
        # Falls nichts neu gezeichnet wird: spätestens dann wieder wie gewohnt
        QTimer.singleShot(_HOLD_PREVIEW_MS, self.release_preview)

    def release_preview(self):
        canvas, interval = self.canvas, self._held_interval
        if canvas is None or interval is None:
            return
        self._held_interval = None
        try:
            canvas.mapCanvasRefreshed.disconnect(self.release_preview)
        except (TypeError, RuntimeError):
            pass
        canvas.setMapUpdateInterval(interval)

    def notify_changed(self):
        """``changed`` vormerken; es kommt einmal, sobald die laufenden Änderungen durch sind."""
        self._changed_timer.start()

    def _emit_selection_changed(self, *args):
        self.selection_changed.emit()

    # ------------------------------------------------------------------
    # Schreiben
    # ------------------------------------------------------------------

    def add_features(self, role: str, features: list[QgsFeature]) -> bool:
        lyr = self.layer(role)
        if lyr is None or not features:
            return False
        self._ensure_type_categories(
            lyr,
            role,
            {str(f.attribute("typ_id")) for f in features if f.attribute("typ_id")},
            any(_truthy(f.attribute(MEASURE_FIELD)) for f in features if f.fields().indexFromName(MEASURE_FIELD) >= 0),
        )
        if lyr.isEditable():
            ok = lyr.addFeatures(features)
        else:
            ok, added = lyr.dataProvider().addFeatures(features)
            if ok:
                self.history.record_add(role, added)
            lyr.updateExtents()
        # Kopien von Objekten mit verborgenem Namen brauchen ihre eigene Kategorie (Legende)
        self._ensure_type_categories(lyr, role)
        self._hold_preview()
        lyr.triggerRepaint()
        self.notify_changed()
        return bool(ok)

    def delete_feature(self, role: str, fid: int) -> bool:
        lyr = self.layer(role)
        if lyr is None:
            return False
        if lyr.isEditable():
            ok = lyr.deleteFeature(fid)
        else:
            self.history.record_delete(role, _features(lyr, [fid]))
            ok = lyr.dataProvider().deleteFeatures([fid])
        self._ensure_type_categories(lyr, role)
        self._hold_preview()
        lyr.triggerRepaint()
        self.notify_changed()
        return bool(ok)

    def delete_features(self, role: str, fids: list[int]) -> bool:
        lyr = self.layer(role)
        if lyr is None or not fids:
            return False
        lyr.deselect(fids)
        if lyr.isEditable():
            ok = lyr.deleteFeatures(fids)
        else:
            self.history.record_delete(role, _features(lyr, fids))
            ok = lyr.dataProvider().deleteFeatures(fids)
        # Eigene Kategorien gelöschter Objekte verschwinden aus Darstellung und Legende
        self._ensure_type_categories(lyr, role)
        self._hold_preview()
        lyr.triggerRepaint()
        self.notify_changed()
        return bool(ok)

    def change_features(self, role: str, geometries: dict, attributes: dict | None = None) -> bool:
        """Geometrien (``{fid: QgsGeometry}``) und Attribute (``{fid: {feld: wert}}``) schreiben."""
        lyr = self.layer(role)
        if lyr is None:
            return False
        attr_map = {
            fid: {lyr.fields().indexOf(name): value for name, value in values.items()}
            for fid, values in (attributes or {}).items()
        }
        if lyr.isEditable():
            ok = all(lyr.changeGeometry(fid, geom) for fid, geom in geometries.items())
            for fid, values in attr_map.items():
                for idx, value in values.items():
                    ok = lyr.changeAttributeValue(fid, idx, value) and ok
        else:
            self.history.record_change(role, lyr, geometries, attributes or {})
            provider = lyr.dataProvider()
            ok = provider.changeGeometryValues(geometries) if geometries else True
            if attr_map:
                ok = provider.changeAttributeValues(attr_map) and ok
            lyr.updateExtents()
        # Name bzw. Beschriftung geändert: entscheidet, ob das Objekt eine eigene Kategorie (Legende) hat
        if any(_LEGEND_FIELDS & set(values) for values in (attributes or {}).values()):
            self._ensure_type_categories(lyr, role)
        self._hold_preview()
        lyr.triggerRepaint()
        self.notify_changed()
        return bool(ok)

    def new_feature(self, role: str) -> QgsFeature:
        lyr = self.layer(role)
        return QgsFeature(lyr.fields() if lyr else build_fields(role))

    # ------------------------------------------------------------------
    # Projekt speichern: GeoPackage neben die Projektdatei verschieben
    # ------------------------------------------------------------------

    def on_project_write(self, doc=None) -> None:
        """GeoPackage beim Speichern neben die Projektdatei ziehen (wie Marker und Dienststellen).

        Läuft im Signal ``writeProject``, also bevor QGIS die Layer in die Projektdatei schreibt: So steht
        gleich der neue Pfad im Projekt. Ein zweites Speichern danach (früher aus ``projectSaved``) kam bei
        ``.qgz`` nicht an, das Projekt zeigte weiter auf das GeoPackage im tmp-Ordner.
        """
        layers = {r: self.layer(r) for r in _ROLE_ORDER}
        if not any(layers.values()):
            return
        proj = QgsProject.instance()
        pfile = proj.fileName()
        if not pfile:
            return
        target = os.path.splitext(pfile)[0] + GPKG_SUFFIX + ".gpkg"

        moved: list[QgsVectorLayer] = []
        # GeoPackages aus dem tmp-Ordner, die nach dem Umzug keiner mehr braucht
        leftovers: set[str] = set()
        kept: set[str] = set()
        for role, lyr in layers.items():
            if lyr is None or lyr.providerType() != "ogr":
                continue
            current = lyr.source().split("|")[0]
            if os.path.abspath(current) == os.path.abspath(target):
                continue
            if lyr.isEditable():
                lyr.commitChanges()
            opts = QgsVectorFileWriter.SaveVectorOptions()
            opts.driverName = "GPKG"
            opts.layerName = role
            opts.actionOnExistingFile = (
                QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
                if os.path.exists(target)
                else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
            )
            result = QgsVectorFileWriter.writeAsVectorFormatV3(lyr, target, proj.transformContext(), opts)
            if result[0] != QgsVectorFileWriter.WriterError.NoError:
                logger.error("Objektplanung: Layer %s konnte nicht nach %s kopiert werden: %s", role, target, result[1])
                kept.add(current)
                continue
            # setDataSource behält Stil, ID und Position im Layerbaum
            lyr.setDataSource(f"{target}|layername={role}", lyr.name(), "ogr")
            moved.append(lyr)
            if in_temp_dir(current):
                leftovers.add(current)

        # Den Layerbaum hat QGIS schon mit dem alten Pfad ins Projekt-XML geschrieben
        update_layer_tree_sources(doc, proj, moved)
        for path in leftovers - kept:
            remove_temp_file(path)

    # ------------------------------------------------------------------
    # Darstellung
    # ------------------------------------------------------------------

    def apply_style(self, lyr: QgsVectorLayer, role: str) -> None:
        if role in _VERSIONED_ROLES:
            lyr.setCustomProperty(_STYLE_VERSION_PROPERTY, _STYLE_VERSION)
        if role in (ROLE_GENERATORS, ROLE_LIGHTS):
            lyr.setRenderer(QgsSingleSymbolRenderer(self._sign_symbol(role)))
            _set_labels(lyr, 'coalesce(nullif("bezeichnung", \'\'), "typ")')
        elif role == ROLE_TENTS:
            lyr.setRenderer(QgsSingleSymbolRenderer(_tent_symbol()))
            _set_labels(lyr, _tent_label_expr(self.catalog.zelte), polygon=True)
        elif role == ROLE_VEHICLES:
            lyr.setRenderer(QgsSingleSymbolRenderer(self._sign_symbol(role)))
            # Der Typ steckt im taktischen Zeichen – beschriftet wird nur eine eigene Bezeichnung, unter dem Zeichen
            _set_labels(lyr, "nullif(\"bezeichnung\", '')", polygon=True, y_offset_mm=-4.0)
        elif role == ROLE_CABLES:
            categories = [
                QgsRendererCategory(reel.id, _cable_symbol(reel.farbe), reel.name)
                for reel in self.catalog.leitungsroller
            ]
            renderer = QgsCategorizedSymbolRenderer("typ_id", categories)
            renderer.setSourceSymbol(_cable_symbol("#616161"))
            lyr.setRenderer(renderer)
            _set_labels(
                lyr,
                "coalesce(nullif(\"bezeichnung\", ''), \"typ\") || ' – ' || "
                "replace(format_number(\"laenge\", 1), '.', ',') || ' m'",
                line=True,
            )
        elif role == ROLE_DISTRIBUTORS:
            lyr.setRenderer(QgsSingleSymbolRenderer(_distributor_symbol()))
            _set_labels(lyr, 'coalesce(nullif("bezeichnung", \'\'), "typ")')
        elif role == ROLE_ZONES:
            self._set_renderer(lyr, role, self._legend_entries(lyr, role), _dimensions_used(lyr))
            # Gebiete sind groß – ihre Namen sollen auch in der Übersicht lesbar sein.
            # Nur eine eigene Bezeichnung: der Typ („Gebiet rot (gefüllt)“) steckt schon in der Darstellung
            # (außer bei den früheren Typen, siehe _ZONE_LABEL_EXPR)
            _set_labels(lyr, _ZONE_LABEL_EXPR, polygon=True, min_scale=50000)
        elif role == ROLE_ARROWS:
            self._set_renderer(lyr, role, self._legend_entries(lyr, role), _dimensions_used(lyr))
            _set_labels(lyr, "nullif(\"bezeichnung\", '')", line=True, min_scale=50000)
        elif role == ROLE_POINTS:
            self._set_renderer(lyr, role, self._legend_entries(lyr, role), _dimensions_used(lyr))
            # Die Beschreibung gehört zum Punkt: immer zeigen, auch weit herausgezoomt
            _set_labels(lyr, note_label_expr(), dist_mm=1.5, min_scale=0)
        elif role == ROLE_TEXTS:
            lyr.setRenderer(QgsSingleSymbolRenderer(_text_anchor_symbol()))
            type_color = _type_color_expr(self.catalog.texte, "#212121")
            _set_labels(
                lyr,
                note_label_expr(),
                over_point=True,
                min_scale=0,
                color_expr=f"coalesce({_OBJECT_COLOR_EXPR}, {type_color})",
            )
        lyr.triggerRepaint()

    def _ensure_style(self, lyr: QgsVectorLayer, role: str) -> None:
        """Gebiete, Linien und Punkte früherer Versionen nachrüsten: Feld ``gestaltung``, aktuelle Darstellung."""
        if role in NOTE_ROLES:
            # Beschriftung einer früheren Version ohne Platzhalter $POS
            _replace_label_expression(lyr, _PLAIN_LABEL_EXPR, note_label_expr())
        if role == ROLE_TENTS:
            # Ganzer Typ statt Kurzname bzw. Kurznamen eines älteren Katalogs
            _replace_label_expression(lyr, _LEGACY_TENT_LABEL_EXPR, _tent_label_expr(self.catalog.zelte))
            _replace_label_expression(lyr, _TENT_LABEL_PREFIX, _tent_label_expr(self.catalog.zelte), prefix=True)
        if role not in _VERSIONED_ROLES or not _ensure_field(lyr, STYLE_FIELD, _T_STRING):
            return
        if role in MEASURE_ROLES and not (
            _ensure_field(lyr, MEASURE_FIELD) and _ensure_field(lyr, DIMENSION_ADJUST_FIELD, _T_STRING)
        ):
            return
        if role == ROLE_ZONES:
            _replace_label_expression(lyr, _LEGACY_ZONE_LABEL_EXPR, _ZONE_LABEL_EXPR)
            # Zwischenstand vor 2.3.0: nur die Bezeichnung, auch bei den früheren Typen
            _replace_label_expression(lyr, _PLAIN_LABEL_EXPR, _ZONE_LABEL_EXPR)
        current = str(lyr.customProperty(_STYLE_VERSION_PROPERTY, "")) == str(_STYLE_VERSION)
        if current:
            # Aktuell – nur noch dafür sorgen, dass jeder verwendete Typ sein Symbol hat
            self._ensure_type_categories(lyr, role)
            return
        # Darstellung einer früheren Version – neu aufbauen, die Beschriftung bleibt
        self._set_renderer(lyr, role, self._legend_entries(lyr, role), _dimensions_used(lyr))
        lyr.setCustomProperty(_STYLE_VERSION_PROPERTY, _STYLE_VERSION)
        lyr.triggerRepaint()

    def _ensure_type_categories(
        self, lyr: QgsVectorLayer, role: str, extra_ids=(), extra_dimensions: bool = False
    ) -> None:
        """Kategorien so, wie die Objekte sie brauchen – sonst blieben Objekte unsichtbar bzw. die Legende veraltet.

        Die Darstellung enthält bewusst nur, was gebraucht wird: QGIS bereitet bei jedem Neuzeichnen alle
        Ausdrücke aller Kategorien vor, auch ungenutzter, und jede Schrift der Maße. Deshalb je verwendetem
        Typ eine Kategorie (``extra_ids``: gleich hinzukommende), dazu eine je Objekt, dessen Name nicht auf
        der Karte steht – sie trägt den Namen in die Legende (siehe ``LEGEND_KEY_EXPR``). Maß-Ebenen nur,
        solange ein Objekt seine Maße zeigt (bzw. ``extra_dimensions``).
        """
        if role not in _VERSIONED_ROLES:
            return
        entries = self._legend_entries(lyr, role, extra_ids)
        dimensions = extra_dimensions or _dimensions_used(lyr)
        renderer = lyr.renderer()
        current = (
            {(str(c.value()), c.label()) for c in renderer.categories()}
            if isinstance(renderer, QgsCategorizedSymbolRenderer) and renderer.classAttribute() == LEGEND_KEY_EXPR
            else None
        )
        wanted = {(key, label) for key, _type, label in entries} | {(_FALLBACK_KEY, _FALLBACK_LABEL)}
        if current == wanted and dimensions == _has_dimensions(lyr):
            return
        self._set_renderer(lyr, role, entries, dimensions)
        lyr.triggerRepaint()

    def _legend_entries(self, lyr: QgsVectorLayer, role: str, extra_ids=()) -> list[tuple[str, str, str]]:
        """(Kategorie, Typ, Bezeichnung): je verwendetem Typ eine, dazu je Objekt mit verborgenem Namen."""
        names = {t.id: t.name for t in self._types(role)}
        type_ids = {str(i) for i in extra_ids}
        objects = []
        for feature in lyr.getFeatures():
            type_id = str(feature.attribute("typ_id") or "")
            key, label = legend_key(role, feature)
            if key.startswith(OBJECT_KEY_PREFIX):
                objects.append((key, type_id, label))
            else:
                type_ids.add(type_id)
        typed = [(t, t, names[t]) for t in sorted(type_ids) if t in names]
        return typed + [(key, t, label) for key, t, label in objects if t in names]

    def refresh_dimensions(self, role: str) -> None:
        """Nach dem Ein- bzw. Ausschalten der Maße: Maß-Ebenen nur, solange ein Objekt sie zeigt."""
        lyr = self.layer(role)
        if lyr is not None:
            self._ensure_type_categories(lyr, role)

    def _set_renderer(self, lyr: QgsVectorLayer, role: str, entries, dimensions: bool) -> None:
        lyr.setRenderer(self._renderer(role, entries, dimensions))
        lyr.setCustomProperty(_DIMENSIONS_PROPERTY, int(dimensions))

    def _types(self, role: str):
        if role == ROLE_ZONES:
            return self.catalog.zone_types()
        return self.catalog.pfeile if role == ROLE_ARROWS else self.catalog.punkte

    def _renderer(self, role: str, entries, dimensions: bool) -> QgsCategorizedSymbolRenderer:
        """Darstellung für Gebiete, Linien und Punkte: Symbol nach Typ, eingeteilt nach ``LEGEND_KEY_EXPR``."""
        types = {t.id: t for t in self._types(role)}

        def symbol(color: str, filled: bool = True):
            if role == ROLE_ZONES:
                zone = _zone_symbol(color, filled)
                return _with_segment_dimensions(zone) if dimensions else zone
            return (_arrow_symbol if role == ROLE_ARROWS else _point_symbol)(color, dimensions)

        default_color = "#1565c0" if role == ROLE_ZONES else "#c62828"
        categories = [
            QgsRendererCategory(key, symbol(types[t].farbe, types[t].gefuellt), label) for key, t, label in entries
        ]
        categories.append(QgsRendererCategory(_FALLBACK_KEY, _fallback_symbol(role, default_color), _FALLBACK_LABEL))
        renderer = QgsCategorizedSymbolRenderer(LEGEND_KEY_EXPR, categories)
        renderer.setSourceSymbol(symbol(default_color))
        return renderer

    def _sign_symbol(self, role: str):
        """Symbol der Objekte mit taktischem Zeichen (Fahrzeuge, Stromerzeuger, Leuchten)."""
        if role == ROLE_GENERATORS:
            sign = self._sign_expr(self.catalog.stromerzeuger, "Einrichtungen/Elektroversorgung.svg")
            return _generator_symbol(sign)
        if role == ROLE_LIGHTS:
            return _light_symbol(self._sign_expr(self.catalog.beleuchtung, "Maßnahmen/Beleuchten.svg"))
        return _vehicle_symbol(self._sign_expr(self.catalog.fahrzeuge, "THW_Fahrzeuge/Kraftfahrzeug.svg"))

    def _ensure_sign_switch(self, lyr: QgsVectorLayer, role: str) -> None:
        """Ältere Layer nachrüsten: Feld ``zeichen`` ergänzen, das taktische Zeichen daran knüpfen und einbetten.

        Zeigt ein Zeichen noch als Pfad in den Plugin-Ordner, wird die Darstellung einmal neu aufgebaut
        (danach steht ``base64:`` im Ausdruck, ein weiteres Öffnen ändert nichts mehr). Die Beschriftung bleibt.
        """
        if role not in SIGN_ROLES or not _ensure_field(lyr, SIGN_FIELD):
            return
        renderer = lyr.renderer()
        if not isinstance(renderer, QgsSingleSymbolRenderer):
            return
        symbol = renderer.symbol()
        if _has_sign_switch(symbol) and _signs_embedded(symbol):
            return
        # Darstellung aus einer Version ohne Schalter oder mit Zeichen aus dem Plugin-Ordner – neu aufbauen
        lyr.setRenderer(QgsSingleSymbolRenderer(self._sign_symbol(role)))
        lyr.triggerRepaint()

    def _sign_expr(self, types, default: str) -> str:
        """Ausdruck: eingebettetes taktisches Zeichen (``base64:``) je ``typ_id`` aus dem Katalog.

        base64 enthält keine Anführungszeichen; das Escaping bleibt für den Ersatzpfad, falls eine Datei fehlt.
        """

        def path(rel: str) -> str:
            return "'" + self._sign_data_uri(rel).replace("'", "''") + "'"

        pairs = ", ".join(f"'{t.id}', {path(t.zeichen)}" for t in types if t.zeichen)
        lookup = f'map_get(map({pairs}), "typ_id")' if pairs else "NULL"
        return f"coalesce({lookup}, {path(default)})"

    def _sign_data_uri(self, rel: str) -> str:
        """``base64:``-Pfad des verkleinerten Katalog-SVGs ``svgs/<rel>``; fehlt die Datei, ihr Pfad auf der Platte."""
        cached = self._sign_data.get(rel)
        if cached is not None:
            return cached
        full = os.path.join(self.plugin_dir, "svgs", rel)
        try:
            with open(full, encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Taktisches Zeichen %s nicht lesbar, Pfad bleibt: %s", rel, e)
            return full.replace("\\", "/")
        uri = _EMBEDDED_PREFIX + base64.b64encode(minify_svg(text).encode("utf-8")).decode("ascii")
        self._sign_data[rel] = uri
        return uri


def _tent_symbol() -> QgsFillSymbol:
    symbol = QgsFillSymbol.createSimple(
        {
            "color": "76,175,80,110",
            "outline_color": "27,94,32,255",
            "outline_width": "0.5",
            "outline_width_unit": "MM",
        }
    )
    # Abspannbereich als gestrichelte Kontur
    generator = QgsGeometryGeneratorSymbolLayer.create(
        {"geometryModifier": _metric_buffer_expr('coalesce("abspannung", 0)', 2), "SymbolType": "Fill"}
    )
    if generator is not None:
        generator.setSymbolType(Qgis.SymbolType.Fill)
        guy = QgsFillSymbol.createSimple(
            {
                "style": "no",
                "outline_color": "27,94,32,200",
                "outline_style": "dash",
                "outline_width": "0.3",
                "outline_width_unit": "MM",
            }
        )
        generator.setSubSymbol(guy)
        symbol.insertSymbolLayer(0, generator)
    return symbol


# Fahrerhaus: vorderer Teil des Rechtecks (Front = Kante Ecke 2→3), ca. 2,2 m lang.
# Anhänger (typ_id anh_…) haben keins, dort zeigt eine Deichsel die Front.
_CAB_EXPR = (
    "CASE WHEN \"typ_id\" LIKE 'anh%' THEN NULL ELSE "
    "with_variable('f', min(2.2 / max(\"laenge\", 0.1), 0.45),"
    "with_variable('a', point_n($geometry, 1), with_variable('b', point_n($geometry, 2),"
    "with_variable('c', point_n($geometry, 3), with_variable('d', point_n($geometry, 4),"
    "make_polygon(make_line(@b, @c,"
    "make_point(x(@c) + (x(@d) - x(@c)) * @f, y(@c) + (y(@d) - y(@c)) * @f),"
    "make_point(x(@b) + (x(@a) - x(@b)) * @f, y(@b) + (y(@a) - y(@b)) * @f), @b)))))))"
    " END"
)
# Deichsel: von der Mitte der Front 1,2 m nach vorn
_DRAWBAR_EXPR = (
    "CASE WHEN \"typ_id\" LIKE 'anh%' THEN "
    "with_variable('b', point_n($geometry, 2), with_variable('c', point_n($geometry, 3),"
    "with_variable('a', point_n($geometry, 1),"
    "with_variable('m', make_point((x(@b) + x(@c)) / 2, (y(@b) + y(@c)) / 2),"
    "with_variable('k', 1.2 / max(\"laenge\", 0.1),"
    "make_line(@m, make_point(x(@m) + (x(@b) - x(@a)) * @k, y(@m) + (y(@b) - y(@a)) * @k)))))))"
    " ELSE NULL END"
)


def _vehicle_symbol(sign_expr: str) -> QgsFillSymbol:
    """Fahrzeug in Draufsicht: Aufbau hell, Fahrerhaus dunkel, taktisches Zeichen in der Mitte."""
    symbol = QgsFillSymbol.createSimple(
        {
            "color": "227,242,253,230",
            "outline_color": "13,71,161,255",
            "outline_width": "0.4",
            "outline_width_unit": "MM",
            "joinstyle": "round",
        }
    )
    cab = QgsGeometryGeneratorSymbolLayer.create({"geometryModifier": _CAB_EXPR, "SymbolType": "Fill"})
    if cab is not None:
        cab.setSymbolType(Qgis.SymbolType.Fill)
        cab.setSubSymbol(
            QgsFillSymbol.createSimple(
                {
                    "color": "21,101,192,235",
                    "outline_color": "13,71,161,255",
                    "outline_width": "0.4",
                    "outline_width_unit": "MM",
                }
            )
        )
        symbol.appendSymbolLayer(cab)
    drawbar = QgsGeometryGeneratorSymbolLayer.create({"geometryModifier": _DRAWBAR_EXPR, "SymbolType": "Line"})
    if drawbar is not None:
        drawbar.setSymbolType(Qgis.SymbolType.Line)
        drawbar.setSubSymbol(
            QgsLineSymbol.createSimple(
                {"line_color": "13,71,161,255", "line_width": "0.8", "line_width_unit": "MM", "capstyle": "round"}
            )
        )
        symbol.appendSymbolLayer(drawbar)
    # Taktisches Zeichen maßstäblich (wächst beim Reinzoomen mit), immer aufrecht
    sign = QgsGeometryGeneratorSymbolLayer.create({"geometryModifier": "centroid($geometry)", "SymbolType": "Marker"})
    if sign is not None:
        sign.setSymbolType(Qgis.SymbolType.Marker)
        _switch_with_sign(sign)
        marker = QgsMarkerSymbol()
        marker.changeSymbolLayer(0, _svg_layer(sign_expr, 3.0, Qgis.RenderUnit.MetersInMapUnits))
        marker.symbolLayer(0).setDataDefinedProperty(
            QgsSymbolLayer.Property.Size, QgsProperty.fromExpression('coalesce("breite", 2.5) * 1.3')
        )
        sign.setSubSymbol(marker)
        symbol.appendSymbolLayer(sign)
    return symbol


def _svg_layer(path_expr: str, size: float, unit) -> QgsSvgMarkerSymbolLayer:
    layer = QgsSvgMarkerSymbolLayer("", size, 0)
    layer.setSizeUnit(unit)
    layer.setDataDefinedProperty(QgsSymbolLayer.Property.Name, QgsProperty.fromExpression(path_expr))
    return layer


def _switch_with_sign(layer: QgsSymbolLayer, shown: bool = True) -> None:
    """Symbolebene nur zeichnen, solange das Zeichen des Objekts an ist (bzw. bei ``shown=False`` aus)."""
    expression = _SIGN_ON_EXPR if shown else f"NOT {_SIGN_ON_EXPR}"
    layer.setDataDefinedProperty(_LAYER_ENABLED, QgsProperty.fromExpression(expression))


def _has_sign_switch(symbol) -> bool:
    return any(layer.dataDefinedProperties().isActive(_LAYER_ENABLED_KEY) for layer in symbol.symbolLayers())


def _symbol_layers_deep(symbol):
    """Alle Symbolebenen samt Unter-Symbolen (beim Fahrzeug trägt ein Geometrie-Generator das Zeichen)."""
    for layer in symbol.symbolLayers():
        yield layer
        sub = layer.subSymbol()
        if sub is not None:
            yield from _symbol_layers_deep(sub)


def _signs_embedded(symbol) -> bool:
    """True, wenn jeder SVG-Marker sein Zeichen eingebettet trägt statt als Pfad (in den Plugin-Ordner)."""
    for layer in _symbol_layers_deep(symbol):
        if not isinstance(layer, QgsSvgMarkerSymbolLayer):
            continue
        prop = layer.dataDefinedProperties().property(_SVG_NAME_KEY)
        name = prop.expressionString() if prop.isActive() else layer.path()
        if name and _EMBEDDED_PREFIX not in name:
            return False
    return True


def _sign_symbol_layers(symbol: QgsMarkerSymbol, sign_expr: str, size_mm: float, color: str) -> None:
    """Zeichen auf rundem Grund (schon erste Ebene von ``symbol``) plus Ersatzpunkt, wenn es aus ist.

    Ohne Zeichen bliebe vom Objekt sonst nur die Beschriftung – der Punkt hält es auf der Karte auffindbar.
    """
    _switch_with_sign(symbol.symbolLayer(0))
    svg = _svg_layer(sign_expr, size_mm, Qgis.RenderUnit.Millimeters)
    _switch_with_sign(svg)
    symbol.appendSymbolLayer(svg)
    dot = QgsSimpleMarkerSymbolLayer.create(
        {"name": "circle", "color": color, "outline_color": "255,255,255,255", "outline_width": "0.4", "size": "3.5"}
    )
    if dot is not None:
        _switch_with_sign(dot, shown=False)
        symbol.appendSymbolLayer(dot)


def _cable_symbol(color: str) -> QgsLineSymbol:
    symbol = QgsLineSymbol.createSimple(
        {"line_color": color, "line_width": "0.8", "line_width_unit": "MM", "capstyle": "round"}
    )
    # Rollen-Position am Startpunkt
    marker = QgsMarkerSymbol.createSimple(
        {"name": "circle", "color": color, "outline_color": "white", "outline_width": "0.4", "size": "3"}
    )
    try:
        start = QgsMarkerLineSymbolLayer()
        start.setPlacements(Qgis.MarkerLinePlacement.FirstVertex)
        start.setSubSymbol(marker)
        symbol.appendSymbolLayer(start)
    except Exception:
        logger.debug("Startmarker für Leitungen nicht verfügbar", exc_info=True)
    return symbol


def _rgba(color: str, alpha: int = 255) -> str:
    c = QColor(color)
    return f"{c.red()},{c.green()},{c.blue()},{alpha}"


def _fallback_symbol(role: str, color: str):
    """Schlichtes Symbol für „Weitere Objekte“: im Ebenenbaum ein kleines Icon, auf der Karte gut zu sehen.

    Das volle Symbol (Radius-Kreis, Muster, Pfeilspitzen, Maße) zeichnet der Ebenenbaum ohne Objekt riesig.
    """
    if role == ROLE_ZONES:
        return QgsFillSymbol.createSimple(
            {"color": _rgba(color, 80), "outline_color": _rgba(color), "outline_width": "0.6"}
        )
    if role == ROLE_ARROWS:
        return QgsLineSymbol.createSimple({"line_color": _rgba(color), "line_width": "0.8"})
    return QgsMarkerSymbol.createSimple(
        {"name": "circle", "color": _rgba(color), "outline_color": "255,255,255,255", "size": "3"}
    )


def _zone_symbol(
    color: str,
    filled: bool = True,
    expressions: _AreaExpressions | None = None,
    opacity: float = DEFAULT_OPACITY,
    stroke_width: float = DEFAULT_STROKE_WIDTH,
) -> QgsFillSymbol:
    """Gebiet in der Typfarbe; die Gestaltung des Gebiets (Feld ``gestaltung``) überschreibt Füllung und Rand.

    Ebenen: Flächenfüllung, je eine Linienschraffur pro Winkel, Punktraster und zuletzt der Rand –
    welche Füllung zu sehen ist, entscheidet das Muster. ``expressions``: woher die Gestaltung kommt
    (Standard: das Gebiet selbst; der Radius-Kreis eines Punkts liest seine eigene).
    """
    ex = expressions or _area_expressions(_STYLE, filled)
    symbol = QgsFillSymbol.createSimple({"color": _rgba(color, round(255 * opacity)), "outline_style": "no"})
    fill = symbol.symbolLayer(0)
    fill.setDataDefinedProperty(QgsSymbolLayer.Property.FillColor, QgsProperty.fromExpression(ex.fill))
    _enable_for_patterns(fill, ("voll",), ex.pattern)

    for angle, patterns in HATCH_LINES:
        hatch = QgsLinePatternFillSymbolLayer()
        hatch.setLineAngle(angle)
        hatch.setDistance(_HATCH_DISTANCE_MM)
        hatch.setDistanceUnit(Qgis.RenderUnit.Millimeters)
        hatch.setLineWidth(_HATCH_WIDTH_MM)
        hatch.setColor(QColor(color))
        hatch.subSymbol().symbolLayer(0).setDataDefinedProperty(
            QgsSymbolLayer.Property.StrokeColor, QgsProperty.fromExpression(ex.fill)
        )
        _enable_for_patterns(hatch, patterns, ex.pattern)
        symbol.appendSymbolLayer(hatch)

    dots = QgsPointPatternFillSymbolLayer()
    dots.setDistanceX(_HATCH_DISTANCE_MM)
    dots.setDistanceY(_HATCH_DISTANCE_MM)
    dots.setDistanceXUnit(Qgis.RenderUnit.Millimeters)
    dots.setDistanceYUnit(Qgis.RenderUnit.Millimeters)
    # Jede zweite Reihe versetzt, wie ein Punktraster auf Papier
    dots.setDisplacementX(_HATCH_DISTANCE_MM / 2)
    dots.setDisplacementXUnit(Qgis.RenderUnit.Millimeters)
    dot = QgsMarkerSymbol.createSimple({"name": "circle", "size": "0.7", "size_unit": "MM", "outline_style": "no"})
    dot.setColor(QColor(color))
    dot.symbolLayer(0).setDataDefinedProperty(QgsSymbolLayer.Property.FillColor, QgsProperty.fromExpression(ex.fill))
    dots.setSubSymbol(dot)
    _enable_for_patterns(dots, DOT_PATTERNS, ex.pattern)
    symbol.appendSymbolLayer(dots)

    outline = QgsSimpleFillSymbolLayer()
    outline.setBrushStyle(Qt.BrushStyle.NoBrush)
    outline.setStrokeColor(QColor(color))
    outline.setStrokeWidth(stroke_width)
    outline.setStrokeWidthUnit(Qgis.RenderUnit.Millimeters)
    outline.setPenJoinStyle(Qt.PenJoinStyle.RoundJoin)
    for prop, expression in (
        (QgsSymbolLayer.Property.StrokeColor, ex.stroke_color),
        (QgsSymbolLayer.Property.StrokeWidth, ex.stroke_width),
        (QgsSymbolLayer.Property.StrokeStyle, ex.stroke_style),
    ):
        outline.setDataDefinedProperty(prop, QgsProperty.fromExpression(expression))
    symbol.appendSymbolLayer(outline)
    return symbol


def _enable_for_patterns(layer: QgsSymbolLayer, patterns, pattern_expr: str) -> None:
    """Füllebene nur zeichnen, wenn die Fläche eines dieser Muster hat."""
    listed = ", ".join(f"'{p}'" for p in patterns)
    expression = f"{pattern_expr} IN ({listed})"
    layer.setDataDefinedProperty(_LAYER_ENABLED, QgsProperty.fromExpression(expression))


def _replace_label_expression(lyr: QgsVectorLayer, old: str, new: str, prefix: bool = False) -> None:
    """Beschriftung einer früheren Version auf den neuen Ausdruck umstellen; eigene Ausdrücke bleiben.

    Mit ``prefix`` genügt es, dass der bisherige Ausdruck mit ``old`` beginnt.
    """
    labeling = lyr.labeling()
    if not isinstance(labeling, QgsVectorLayerSimpleLabeling):
        return
    settings = labeling.settings()
    matches = settings.fieldName.startswith(old) if prefix else settings.fieldName == old
    if not matches or settings.fieldName == new:
        return
    settings.fieldName = new
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(settings))


def _truthy(value) -> bool:
    """Ja/Nein-Attribut (auch 0/1 aus dem GeoPackage); NULL ist nein."""
    if value is None or (hasattr(value, "isNull") and value.isNull()):
        return False
    return bool(value)


def _dimensions_used(lyr: QgsVectorLayer) -> bool:
    """Zeigt irgendein Objekt des Layers seine Maße?"""
    index = lyr.fields().indexFromName(MEASURE_FIELD)
    return index >= 0 and any(_truthy(v) for v in lyr.uniqueValues(index))


def _has_dimensions(lyr: QgsVectorLayer) -> bool:
    """Enthält die aktuelle Darstellung die Maß-Ebenen?"""
    return str(lyr.customProperty(_DIMENSIONS_PROPERTY, "0")) in ("1", "true")


def _features(lyr: QgsVectorLayer, fids) -> list[QgsFeature]:
    return list(lyr.getFeatures(QgsFeatureRequest().setFilterFids(list(fids))))


def _tent_label_expr(tents) -> str:
    """Ausdruck: eigene Bezeichnung, sonst der Kurzname je ``typ_id`` aus dem Katalog, sonst der Typ."""
    pairs = ", ".join(f"{QgsExpression.quotedString(t.id)}, {QgsExpression.quotedString(t.short_name)}" for t in tents)
    return f'{_TENT_LABEL_PREFIX}{pairs}), "typ_id"), "typ")'


def _type_color_expr(types, default: str) -> str:
    """Ausdruck: Typfarbe je ``typ_id`` aus dem Katalog."""
    pairs = ", ".join(f"'{t.id}', '{QColor(t.farbe).name()}'" for t in types)
    lookup = f'map_get(map({pairs}), "typ_id")' if pairs else "NULL"
    return f"coalesce({lookup}, '{default}')"


def _arrow_symbol(color: str, dimensions: bool = False) -> QgsLineSymbol:
    """Pfeil bzw. Linie in der Typfarbe; die Gestaltung (Feld ``gestaltung``) überschreibt Farbe, Breite und Spitzen.

    Die Spitze ist ein Dreieck, dessen Spitze genau auf dem letzten (bzw. ersten) Punkt sitzt
    und das mit dem Abschnitt dreht. Ohne eigene Gestaltung: Spitze am Ende, wie bisher.
    """
    symbol = QgsLineSymbol.createSimple(
        {
            "line_color": _rgba(color),
            "line_width": str(DEFAULT_LINE_WIDTH),
            "line_width_unit": "MM",
            "capstyle": "round",
            "joinstyle": "round",
        }
    )
    line = symbol.symbolLayer(0)
    line.setTrimDistanceStartUnit(Qgis.RenderUnit.Millimeters)
    line.setTrimDistanceEndUnit(Qgis.RenderUnit.Millimeters)
    for prop, expression in (
        (QgsSymbolLayer.Property.StrokeColor, _OBJECT_COLOR_EXPR),
        (QgsSymbolLayer.Property.StrokeWidth, _LINE_WIDTH_EXPR),
        (QgsSymbolLayer.Property.TrimEnd, _arrow_trim_expr((ARROWS_END, ARROWS_BOTH))),
        (QgsSymbolLayer.Property.TrimStart, _arrow_trim_expr((ARROWS_START, ARROWS_BOTH))),
    ):
        line.setDataDefinedProperty(prop, QgsProperty.fromExpression(expression))

    for placement, angle, modes in (
        (Qgis.MarkerLinePlacement.LastVertex, 0.0, (ARROWS_END, ARROWS_BOTH)),
        # Die Spitze dreht mit dem Abschnitt – am Anfang zeigt sie zurück
        (Qgis.MarkerLinePlacement.FirstVertex, 180.0, (ARROWS_START, ARROWS_BOTH)),
    ):
        head = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.ArrowHeadFilled, 4.8)
        head.setColor(QColor(color))
        head.setStrokeStyle(Qt.PenStyle.NoPen)
        head.setAngle(angle)
        head.setDataDefinedProperty(QgsSymbolLayer.Property.FillColor, QgsProperty.fromExpression(_OBJECT_COLOR_EXPR))
        head.setDataDefinedProperty(QgsSymbolLayer.Property.Size, QgsProperty.fromExpression(_ARROW_SIZE_EXPR))
        marker_line = QgsMarkerLineSymbolLayer(True)
        marker_line.setPlacements(placement)
        marker_line.setSubSymbol(QgsMarkerSymbol([head]))
        listed = ", ".join(f"'{m}'" for m in modes)
        marker_line.setDataDefinedProperty(_LAYER_ENABLED, QgsProperty.fromExpression(f"{_ARROWS_EXPR} IN ({listed})"))
        symbol.appendSymbolLayer(marker_line)
    return _with_segment_dimensions(symbol) if dimensions else symbol


def _point_symbol(color: str, dimensions: bool = False) -> QgsMarkerSymbol:
    """Punkt in der Typfarbe, mit Radius (Feld ``radius``, Meter) zusätzlich ein maßstabsgetreuer Kreis."""
    symbol = QgsMarkerSymbol.createSimple(
        {
            "name": "circle",
            "color": _rgba(color),
            "outline_color": "255,255,255,255",
            "outline_width": "0.4",
            "size": "3",
        }
    )
    symbol.symbolLayer(0).setDataDefinedProperty(
        QgsSymbolLayer.Property.FillColor, QgsProperty.fromExpression(_OBJECT_COLOR_EXPR)
    )
    generator = QgsGeometryGeneratorSymbolLayer.create(
        {"geometryModifier": _metric_buffer_expr('coalesce("radius", 0)', 36), "SymbolType": "Fill"}
    )
    if generator is not None:
        generator.setSymbolType(Qgis.SymbolType.Fill)
        # Muster, Füllung und Rand wie bei einem Gebiet
        circle = _zone_symbol(
            color, expressions=_CIRCLE_EXPRESSIONS, opacity=CIRCLE_OPACITY, stroke_width=CIRCLE_STROKE_WIDTH
        )
        generator.setSubSymbol(circle)
        generator.setDataDefinedProperty(_LAYER_ENABLED, QgsProperty.fromExpression('coalesce("radius", 0) > 0'))
        symbol.insertSymbolLayer(0, generator)
    # Maß: „r = 25 m“ über dem Kreis, mit dem Kreis maßstabsgetreu versetzt
    if not dimensions:
        return symbol
    adjust = _dimension_adjust(f"'{RADIUS_DIMENSION_KEY}'")
    radius_text = "'r = ' || " + meters_text_expr('coalesce("radius", 0)')
    for marker in _dimension_markers(_dimension_text(adjust, radius_text), _dimension_size(adjust)):
        marker.setVerticalAnchorPoint(QgsMarkerSymbolLayer.VerticalAnchorPoint.Bottom)
        # In Metern: über dem Kreis, mit ihm maßstabsgetreu; von Hand verschoben ebenfalls in Metern
        marker.setOffsetUnit(Qgis.RenderUnit.MetersInMapUnits)
        marker.setDataDefinedProperty(
            QgsSymbolLayer.Property.Offset,
            QgsProperty.fromExpression(
                f"to_string(coalesce(map_get({adjust}, 'dx'), 0)) || ',' || "
                f"to_string(-coalesce(\"radius\", 0) + coalesce(map_get({adjust}, 'dy'), 0))"
            ),
        )
        marker.setDataDefinedProperty(
            _LAYER_ENABLED, QgsProperty.fromExpression(f'{_MEASURE_ON_EXPR} AND coalesce("radius", 0) > 0')
        )
        symbol.appendSymbolLayer(marker)
    return symbol


def _dimension_markers(text_expr: str, size_expr: str) -> list[QgsFontMarkerSymbolLayer]:
    """Maß als Schrift: zuerst ein weißer Rand, darüber die dunkle Schrift (wie bei den Beschriftungen)."""
    markers = []
    for halo in (True, False):
        marker = QgsFontMarkerSymbolLayer(QFont().family(), "", DIMENSION_SIZE_MM)
        marker.setSizeUnit(Qgis.RenderUnit.Millimeters)
        marker.setColor(QColor(255, 255, 255) if halo else QColor(33, 33, 33))
        marker.setStrokeColor(QColor(255, 255, 255) if halo else QColor(0, 0, 0, 0))
        marker.setStrokeWidth(0.7 if halo else 0.0)
        marker.setStrokeWidthUnit(Qgis.RenderUnit.Millimeters)
        marker.setDataDefinedProperty(QgsSymbolLayer.Property.Character, QgsProperty.fromExpression(text_expr))
        marker.setDataDefinedProperty(QgsSymbolLayer.Property.Size, QgsProperty.fromExpression(size_expr))
        markers.append(marker)
    return markers


def _with_segment_dimensions(symbol):
    """Länge jedes Segments mittig an die Kante schreiben, entlang der Kante und nie auf dem Kopf."""
    generator = QgsGeometryGeneratorSymbolLayer.create(
        {"geometryModifier": "segments_to_lines($geometry)", "SymbolType": "Line"}
    )
    if generator is None:
        return symbol
    generator.setSymbolType(Qgis.SymbolType.Line)
    # Das gerade gezeichnete Segment: Teil Nr. @geometry_part_num der Segmente
    segment = "geometry_n(segments_to_lines($geometry), @geometry_part_num)"
    flipped = f"x(end_point({segment})) < x(start_point({segment}))"
    adjust = _dimension_adjust("to_string(@geometry_part_num)")
    text = _dimension_text(adjust, meters_text_expr(metric_length_expr(segment)))
    markers = _dimension_markers(text, _dimension_size(adjust))
    for marker in markers:
        marker.setOffsetUnit(Qgis.RenderUnit.Millimeters)
        marker.setDataDefinedProperty(
            QgsSymbolLayer.Property.Angle, QgsProperty.fromExpression(f"if({flipped}, 180, 0)")
        )
        # Über der Kante – nach dem Umdrehen liegt „oben“ auf der anderen Seite; dazu der Versatz von Hand,
        # in der Richtung der Schrift (so wandert er mit, wenn sich die Kante dreht)
        marker.setDataDefinedProperty(
            QgsSymbolLayer.Property.Offset,
            QgsProperty.fromExpression(
                f"to_string(coalesce(map_get({adjust}, 'dx'), 0)) || ',' || "
                f"to_string(if({flipped}, {DIMENSION_GAP_MM}, -{DIMENSION_GAP_MM}) "
                f"+ coalesce(map_get({adjust}, 'dy'), 0))"
            ),
        )
    marker_line = QgsMarkerLineSymbolLayer(True)
    marker_line.setPlacements(Qgis.MarkerLinePlacement.CentralPoint)
    marker_line.setSubSymbol(QgsMarkerSymbol(markers))
    generator.setSubSymbol(QgsLineSymbol([marker_line]))
    generator.setDataDefinedProperty(_LAYER_ENABLED, QgsProperty.fromExpression(_MEASURE_ON_EXPR))
    symbol.appendSymbolLayer(generator)
    return symbol


def _text_anchor_symbol() -> QgsMarkerSymbol:
    """Texte bestehen nur aus ihrer Beschriftung – der Punkt darunter bleibt unsichtbar."""
    return QgsMarkerSymbol.createSimple({"name": "circle", "color": "0,0,0,0", "outline_style": "no", "size": "1"})


def _metric_buffer_expr(distance_expr: str, segments: int) -> str:
    """Puffer in echten Metern (Projekt-CRS kann Grad oder Web-Mercator sein → über UTM puffern)."""
    return (
        "with_variable('m', "
        "'EPSG:' || to_string(32600 + "
        "floor((x(transform(centroid($geometry), @layer_crs, 'EPSG:4326')) + 180) / 6) + 1),"
        f"transform(buffer(transform($geometry, @layer_crs, @m), {distance_expr}, {segments}), @m, @layer_crs))"
    )


def _generator_symbol(sign_expr: str) -> QgsMarkerSymbol:
    """Taktisches Zeichen (SEA: Elektroversorgung, NEA: Anhänger) auf weißem Grund."""
    symbol = QgsMarkerSymbol.createSimple(
        {
            "name": "circle",
            "color": "255,255,255,230",
            "outline_color": "198,40,40,255",
            "outline_width": "0.4",
            "size": "9",
        }
    )
    _sign_symbol_layers(symbol, sign_expr, 9.0, "198,40,40,255")
    return symbol


def _light_symbol(sign_expr: str) -> QgsMarkerSymbol:
    symbol = QgsMarkerSymbol.createSimple(
        {
            "name": "circle",
            "color": "255,253,231,230",
            "outline_color": "249,168,37,255",
            "outline_width": "0.4",
            "size": "8",
        }
    )
    _sign_symbol_layers(symbol, sign_expr, 8.0, "249,168,37,255")
    # Ausgeleuchteter Bereich als Kreis mit dem Radius in Metern
    generator = QgsGeometryGeneratorSymbolLayer.create(
        {"geometryModifier": _metric_buffer_expr('coalesce("radius", 0)', 24), "SymbolType": "Fill"}
    )
    if generator is not None:
        generator.setSymbolType(Qgis.SymbolType.Fill)
        generator.setSubSymbol(
            QgsFillSymbol.createSimple(
                {
                    "color": "255,235,59,45",
                    "outline_color": "249,168,37,180",
                    "outline_style": "dot",
                    "outline_width": "0.3",
                    "outline_width_unit": "MM",
                }
            )
        )
        symbol.insertSymbolLayer(0, generator)
    return symbol


def _distributor_symbol() -> QgsMarkerSymbol:
    return QgsMarkerSymbol.createSimple(
        {"name": "square", "color": "251,192,45,255", "outline_color": "0,0,0,255", "outline_width": "0.5", "size": "4"}
    )


def _set_labels(
    lyr: QgsVectorLayer,
    expression: str,
    polygon: bool = False,
    line: bool = False,
    y_offset_mm: float = 0.0,
    min_scale: float = 2500,
    over_point: bool = False,
    dist_mm: float = 3.0,
    color_expr: str | None = None,
) -> None:
    """Beschriftung nach ``expression``; ``min_scale=0``: in jedem Maßstab und auch bei Überlappung zeigen."""
    settings = QgsPalLayerSettings()
    settings.fieldName = expression
    settings.isExpression = True

    fmt = QgsTextFormat()
    fmt.setSize(8)
    fmt.setSizeUnit(Qgis.RenderUnit.Points)
    fmt.setColor(QColor(Qt.GlobalColor.black))
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(0.8)
    buffer.setSizeUnit(Qgis.RenderUnit.Millimeters)
    buffer.setColor(QColor(Qt.GlobalColor.white))
    fmt.setBuffer(buffer)
    settings.setFormat(fmt)

    try:
        if polygon or over_point:
            settings.placement = Qgis.LabelPlacement.OverPoint
            settings.yOffset = y_offset_mm
        elif line:
            settings.placement = Qgis.LabelPlacement.Line
        else:
            settings.placement = Qgis.LabelPlacement.OrderedPositionsAroundPoint
            # Abstand zum taktischen Zeichen (Ø 8–9 mm) bzw. zum Punkt
            settings.dist = dist_mm
        # Mehrzeilige Texte und Beschreibungen mittig
        settings.multilineAlign = Qgis.LabelMultiLineAlignment.Center
    except AttributeError:
        logger.debug("Label-Platzierung nicht gesetzt", exc_info=True)
    if min_scale:
        # Beschriftungen nur bei nahem Zoom, sonst wird es unübersichtlich
        settings.scaleVisibility = True
        settings.maximumScale = 1
        settings.minimumScale = min_scale
    else:
        # Texte und Beschreibungen sind der Inhalt selbst: nie wegen Überlappung weglassen
        settings.displayAll = True

    _set_label_switch(settings, color_expr)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    lyr.setLabelsEnabled(True)


def _set_label_switch(settings: QgsPalLayerSettings, color_expr: str | None = None) -> None:
    """Beschriftung nur zeigen, solange ``beschriftung`` des Objekts nicht aus ist; Größe aus ``schriftgroesse``.

    ``color_expr``: Schriftfarbe je Objekt (Texte), sonst bleibt sie schwarz.
    """
    properties = settings.dataDefinedProperties()
    properties.setProperty(_SHOW_PROPERTY, QgsProperty.fromExpression(_LABEL_SHOW_EXPR))
    properties.setProperty(_SIZE_PROPERTY, QgsProperty.fromExpression(_LABEL_SIZE_EXPR))
    if color_expr:
        properties.setProperty(_COLOR_PROPERTY, QgsProperty.fromExpression(color_expr))
    settings.setDataDefinedProperties(properties)


def _ensure_field(lyr: QgsVectorLayer, name: str, typ=_T_BOOL) -> bool:
    """Feld ergänzen, falls es fehlt; False, wenn das nicht ging."""
    if lyr.fields().indexFromName(name) >= 0:
        return True
    if not lyr.dataProvider().addAttributes([QgsField(name, typ)]):
        logger.warning("Feld %s konnte in %s nicht angelegt werden", name, lyr.name())
        return False
    lyr.updateFields()
    return True


def _ensure_label_switch(lyr: QgsVectorLayer) -> None:
    """Ältere Layer nachrüsten: Felder ``beschriftung`` und ``schriftgroesse`` ergänzen, Beschriftung daran knüpfen.

    Der übrige Beschriftungsstil (auch eigene Änderungen im Projekt) bleibt erhalten.
    """
    if not (_ensure_field(lyr, LABEL_FIELD) and _ensure_field(lyr, LABEL_SIZE_FIELD, _T_DOUBLE)):
        return
    labeling = lyr.labeling()
    if not isinstance(labeling, QgsVectorLayerSimpleLabeling):
        return
    settings = labeling.settings()
    properties = settings.dataDefinedProperties()
    if properties.isActive(_SHOW_PROPERTY) and properties.isActive(_SIZE_PROPERTY):
        return
    _set_label_switch(settings)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(settings))


def _ensure_label_transform(lyr: QgsVectorLayer, role: str) -> None:
    """Beschriftung frei platzierbar machen: Felder für Versatz und Drehung, Position und Drehung daran knüpfen.

    Ohne Versatz bleibt die gewohnte Platzierung (um den Punkt, entlang der Linie, in der Fläche).
    Texte sind ihre Beschriftung: sie werden als Ganzes verschoben, hier zählt nur die Drehung.
    """
    if role not in LABEL_EDIT_ROLES:
        return
    if not all(_ensure_field(lyr, name, _T_DOUBLE) for name in (LABEL_DX_FIELD, LABEL_DY_FIELD, LABEL_ANGLE_FIELD)):
        return
    labeling = lyr.labeling()
    if not isinstance(labeling, QgsVectorLayerSimpleLabeling):
        return
    settings = labeling.settings()
    properties = settings.dataDefinedProperties()
    if properties.isActive(_ROTATION_PROPERTY) and (role == ROLE_TEXTS or properties.isActive(_POSITION_PROPERTY)):
        return
    properties.setProperty(_ROTATION_PROPERTY, QgsProperty.fromExpression(f'"{LABEL_ANGLE_FIELD}"'))
    if role != ROLE_TEXTS:
        anchor = label_anchor_expr(role)
        dx, dy = f'"{LABEL_DX_FIELD}"', f'"{LABEL_DY_FIELD}"'
        properties.setProperty(
            _POSITION_PROPERTY,
            QgsProperty.fromExpression(
                f"if({dx} IS NULL AND {dy} IS NULL, NULL, "
                f"make_point(x({anchor}) + coalesce({dx}, 0), y({anchor}) + coalesce({dy}, 0)))"
            ),
        )
        # Die Position ist die Mitte der Beschriftung
        properties.setProperty(_HALI_PROPERTY, QgsProperty.fromValue("Center"))
        properties.setProperty(_VALI_PROPERTY, QgsProperty.fromValue("Half"))
    settings.setDataDefinedProperties(properties)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(settings))
