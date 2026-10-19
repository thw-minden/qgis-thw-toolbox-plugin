"""GeoPackage-Layer der Objektplanung (Zelte, Fahrzeuge, Strom, Beleuchtung, Gebiete, Pfeile)."""

import base64
import os
import time

from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsFillSymbol,
    QgsGeometryGeneratorSymbolLayer,
    QgsLinePatternFillSymbolLayer,
    QgsLineSymbol,
    QgsMarkerLineSymbolLayer,
    QgsMarkerSymbol,
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
from qgis.PyQt.QtCore import QObject, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor

from ..layer.fields import _T_BOOL, _T_DOUBLE, _T_STRING
from ..logging_utils import get_logger
from ..util.svg_minify import minify_svg
from .catalog import Catalog
from .zone_style import DEFAULT_OPACITY, DEFAULT_STROKE_WIDTH, DOT_PATTERNS, HATCH_LINES, STYLE_FIELD

logger = get_logger(__name__)

GPKG_SUFFIX = "_objektplanung"
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
_ZONE_STYLE = f'json_to_map("{STYLE_FIELD}")'


def _zone_style_get(key: str) -> str:
    return f"map_get({_ZONE_STYLE}, '{key}')"


_ZONE_PATTERN_EXPR = f"coalesce({_zone_style_get('pattern')}, 'voll')"
_ZONE_FILL_EXPR = (
    f"set_color_part({_zone_style_get('fill')}, 'alpha', "
    f"round(255 * coalesce({_zone_style_get('opacity')}, {DEFAULT_OPACITY})))"
)
_ZONE_STROKE_COLOR_EXPR = f"nullif({_zone_style_get('stroke')}, '')"
_ZONE_STROKE_WIDTH_EXPR = f"coalesce({_zone_style_get('stroke_width')}, {DEFAULT_STROKE_WIDTH})"
_ZONE_STROKE_STYLE_EXPR = (
    f"if({_zone_style_get('stroke')} = '', 'no', coalesce({_zone_style_get('stroke_style')}, 'solid'))"
)
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
}
# Reihenfolge im Layerbaum: Punkte oben, Flächen unten
_ROLE_ORDER = (
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
    if role == ROLE_ZONES:
        fields.append(QgsField(STYLE_FIELD, _T_STRING))
    return fields


def _flag(value, default: bool) -> bool:
    """Ja/Nein-Attribut; NULL (None bzw. QVariant-NULL unter QGIS 3) ergibt ``default``."""
    if value is None or (hasattr(value, "isNull") and value.isNull()):
        return default
    return bool(value)


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
        self._resaving = False
        # Eingebettete Zeichen je Katalogpfad (``svgs/<rel>``), einmal gelesen
        self._sign_data: dict[str, str] = {}

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
                self._ensure_sign_switch(lyr, role)
                self._ensure_zone_style(lyr, role)
                self._layers[role] = lyr
                self._connect(lyr)

    def _gpkg_path(self, proj: QgsProject) -> str:
        pfile = proj.fileName()
        if pfile:
            return os.path.splitext(pfile)[0] + GPKG_SUFFIX + ".gpkg"
        tmp_dir = os.path.join(self.plugin_dir, "tmp")
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
        self._ensure_sign_switch(lyr, role)
        self._ensure_zone_style(lyr, role)

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
        self.changed.emit()

    def _emit_selection_changed(self, *args):
        self.selection_changed.emit()

    # ------------------------------------------------------------------
    # Schreiben
    # ------------------------------------------------------------------

    def add_features(self, role: str, features: list[QgsFeature]) -> bool:
        lyr = self.layer(role)
        if lyr is None or not features:
            return False
        if lyr.isEditable():
            ok = lyr.addFeatures(features)
        else:
            ok, _ = lyr.dataProvider().addFeatures(features)
            lyr.updateExtents()
        lyr.triggerRepaint()
        self.changed.emit()
        return bool(ok)

    def delete_feature(self, role: str, fid: int) -> bool:
        lyr = self.layer(role)
        if lyr is None:
            return False
        if lyr.isEditable():
            ok = lyr.deleteFeature(fid)
        else:
            ok = lyr.dataProvider().deleteFeatures([fid])
        lyr.triggerRepaint()
        self.changed.emit()
        return bool(ok)

    def delete_features(self, role: str, fids: list[int]) -> bool:
        lyr = self.layer(role)
        if lyr is None or not fids:
            return False
        lyr.deselect(fids)
        if lyr.isEditable():
            ok = lyr.deleteFeatures(fids)
        else:
            ok = lyr.dataProvider().deleteFeatures(fids)
        lyr.triggerRepaint()
        self.changed.emit()
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
            provider = lyr.dataProvider()
            ok = provider.changeGeometryValues(geometries) if geometries else True
            if attr_map:
                ok = provider.changeAttributeValues(attr_map) and ok
            lyr.updateExtents()
        lyr.triggerRepaint()
        self.changed.emit()
        return bool(ok)

    def new_feature(self, role: str) -> QgsFeature:
        lyr = self.layer(role)
        return QgsFeature(lyr.fields() if lyr else build_fields(role))

    # ------------------------------------------------------------------
    # Projekt speichern: GeoPackage neben die Projektdatei verschieben
    # ------------------------------------------------------------------

    def on_project_saved(self) -> None:
        if self._resaving:
            return
        layers = {r: self.layer(r) for r in _ROLE_ORDER}
        if not any(layers.values()):
            return
        proj = QgsProject.instance()
        pfile = proj.fileName()
        if not pfile:
            return
        target = os.path.splitext(pfile)[0] + GPKG_SUFFIX + ".gpkg"

        moved = False
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
                continue
            # setDataSource behält Stil, ID und Position im Layerbaum
            lyr.setDataSource(f"{target}|layername={role}", lyr.name(), "ogr")
            moved = True

        if moved:
            # Die gerade geschriebene Projektdatei zeigt noch auf den alten Pfad
            self._resaving = True
            try:
                proj.write()
            finally:
                self._resaving = False

    # ------------------------------------------------------------------
    # Darstellung
    # ------------------------------------------------------------------

    def apply_style(self, lyr: QgsVectorLayer, role: str) -> None:
        if role in (ROLE_GENERATORS, ROLE_LIGHTS):
            lyr.setRenderer(QgsSingleSymbolRenderer(self._sign_symbol(role)))
            _set_labels(lyr, 'coalesce(nullif("bezeichnung", \'\'), "typ")')
        elif role == ROLE_TENTS:
            lyr.setRenderer(QgsSingleSymbolRenderer(_tent_symbol()))
            _set_labels(lyr, 'coalesce(nullif("bezeichnung", \'\'), "typ")', polygon=True)
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
            lyr.setRenderer(self._zone_renderer())
            # Gebiete sind groß – ihre Namen sollen auch in der Übersicht lesbar sein
            _set_labels(lyr, 'coalesce(nullif("bezeichnung", \'\'), "typ")', polygon=True, min_scale=50000)
        elif role == ROLE_ARROWS:
            renderer = QgsCategorizedSymbolRenderer(
                "typ_id", [QgsRendererCategory(t.id, _arrow_symbol(t.farbe), t.name) for t in self.catalog.pfeile]
            )
            renderer.setSourceSymbol(_arrow_symbol("#c62828"))
            lyr.setRenderer(renderer)
            _set_labels(lyr, "nullif(\"bezeichnung\", '')", line=True, min_scale=50000)
        lyr.triggerRepaint()

    def _zone_renderer(self) -> QgsCategorizedSymbolRenderer:
        renderer = QgsCategorizedSymbolRenderer(
            "typ_id", [QgsRendererCategory(t.id, _zone_symbol(t.farbe), t.name) for t in self.catalog.gebiete]
        )
        renderer.setSourceSymbol(_zone_symbol("#1565c0"))
        return renderer

    def _ensure_zone_style(self, lyr: QgsVectorLayer, role: str) -> None:
        """Ältere Gebiete-Layer nachrüsten: Feld ``gestaltung`` ergänzen und die Darstellung daran knüpfen."""
        if role != ROLE_ZONES or not _ensure_field(lyr, STYLE_FIELD, _T_STRING):
            return
        renderer = lyr.renderer()
        source = renderer.sourceSymbol() if isinstance(renderer, QgsCategorizedSymbolRenderer) else None
        if source is not None and _has_sign_switch(source):
            return
        # Darstellung aus einer Version ohne Gestaltung – neu aufbauen, die Beschriftung bleibt
        lyr.setRenderer(self._zone_renderer())
        lyr.triggerRepaint()

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


def _zone_symbol(color: str) -> QgsFillSymbol:
    """Gebiet in der Typfarbe; die Gestaltung des Gebiets (Feld ``gestaltung``) überschreibt Füllung und Rand.

    Ebenen: Flächenfüllung, je eine Linienschraffur pro Winkel, Punktraster und zuletzt der Rand –
    welche Füllung zu sehen ist, entscheidet das Muster.
    """
    symbol = QgsFillSymbol.createSimple({"color": _rgba(color, 45), "outline_style": "no"})
    fill = symbol.symbolLayer(0)
    fill.setDataDefinedProperty(QgsSymbolLayer.Property.FillColor, QgsProperty.fromExpression(_ZONE_FILL_EXPR))
    _enable_for_patterns(fill, ("voll",))

    for angle, patterns in HATCH_LINES:
        hatch = QgsLinePatternFillSymbolLayer()
        hatch.setLineAngle(angle)
        hatch.setDistance(_HATCH_DISTANCE_MM)
        hatch.setDistanceUnit(Qgis.RenderUnit.Millimeters)
        hatch.setLineWidth(_HATCH_WIDTH_MM)
        hatch.setColor(QColor(color))
        hatch.subSymbol().symbolLayer(0).setDataDefinedProperty(
            QgsSymbolLayer.Property.StrokeColor, QgsProperty.fromExpression(_ZONE_FILL_EXPR)
        )
        _enable_for_patterns(hatch, patterns)
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
    dot.symbolLayer(0).setDataDefinedProperty(
        QgsSymbolLayer.Property.FillColor, QgsProperty.fromExpression(_ZONE_FILL_EXPR)
    )
    dots.setSubSymbol(dot)
    _enable_for_patterns(dots, DOT_PATTERNS)
    symbol.appendSymbolLayer(dots)

    outline = QgsSimpleFillSymbolLayer()
    outline.setBrushStyle(Qt.BrushStyle.NoBrush)
    outline.setStrokeColor(QColor(color))
    outline.setStrokeWidth(DEFAULT_STROKE_WIDTH)
    outline.setStrokeWidthUnit(Qgis.RenderUnit.Millimeters)
    outline.setPenJoinStyle(Qt.PenJoinStyle.RoundJoin)
    for prop, expression in (
        (QgsSymbolLayer.Property.StrokeColor, _ZONE_STROKE_COLOR_EXPR),
        (QgsSymbolLayer.Property.StrokeWidth, _ZONE_STROKE_WIDTH_EXPR),
        (QgsSymbolLayer.Property.StrokeStyle, _ZONE_STROKE_STYLE_EXPR),
    ):
        outline.setDataDefinedProperty(prop, QgsProperty.fromExpression(expression))
    symbol.appendSymbolLayer(outline)
    return symbol


def _enable_for_patterns(layer: QgsSymbolLayer, patterns) -> None:
    """Füllebene nur zeichnen, wenn das Gebiet eines dieser Muster hat."""
    listed = ", ".join(f"'{p}'" for p in patterns)
    layer.setDataDefinedProperty(_LAYER_ENABLED, QgsProperty.fromExpression(f"{_ZONE_PATTERN_EXPR} IN ({listed})"))


def _arrow_symbol(color: str) -> QgsLineSymbol:
    symbol = QgsLineSymbol.createSimple(
        {
            "line_color": _rgba(color),
            "line_width": "1.2",
            "line_width_unit": "MM",
            "capstyle": "flat",
            "joinstyle": "round",
            # Die Spitze des Pfeilkopfs sitzt genau auf dem letzten Punkt – die Linie endet schon
            # im Kopf, sonst ragt ihr Ende als Punkt über die Spitze hinaus
            "trim_distance_end": "2",
            "trim_distance_end_unit": "MM",
        }
    )
    # Pfeilspitze am Ende, dreht mit dem letzten Abschnitt
    head = QgsMarkerSymbol.createSimple(
        {
            "name": "filled_arrowhead",
            "color": _rgba(color),
            "outline_style": "no",
            "size": "5",
        }
    )
    try:
        end = QgsMarkerLineSymbolLayer()
        end.setPlacements(Qgis.MarkerLinePlacement.LastVertex)
        end.setSubSymbol(head)
        symbol.appendSymbolLayer(end)
    except Exception:
        logger.debug("Pfeilspitze nicht verfügbar", exc_info=True)
    return symbol


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
) -> None:
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
        if polygon:
            settings.placement = Qgis.LabelPlacement.OverPoint
            settings.yOffset = y_offset_mm
        elif line:
            settings.placement = Qgis.LabelPlacement.Line
        else:
            settings.placement = Qgis.LabelPlacement.OrderedPositionsAroundPoint
            # Abstand zum taktischen Zeichen (Ø 8–9 mm)
            settings.dist = 3.0
    except AttributeError:
        logger.debug("Label-Platzierung nicht gesetzt", exc_info=True)
    # Beschriftungen nur bei nahem Zoom, sonst wird es unübersichtlich
    settings.scaleVisibility = True
    settings.maximumScale = 1
    settings.minimumScale = min_scale

    _set_label_switch(settings)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    lyr.setLabelsEnabled(True)


def _set_label_switch(settings: QgsPalLayerSettings) -> None:
    """Beschriftung nur zeigen, solange ``beschriftung`` des Objekts nicht aus ist; Größe aus ``schriftgroesse``."""
    properties = settings.dataDefinedProperties()
    properties.setProperty(_SHOW_PROPERTY, QgsProperty.fromExpression(_LABEL_SHOW_EXPR))
    properties.setProperty(_SIZE_PROPERTY, QgsProperty.fromExpression(_LABEL_SIZE_EXPR))
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
