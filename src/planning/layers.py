"""GeoPackage-Layer der Objektplanung (Zelte, Fahrzeuge, Strom, Beleuchtung)."""

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
    QgsLineSymbol,
    QgsMarkerLineSymbolLayer,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsProject,
    QgsProperty,
    QgsRendererCategory,
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

from ..layer.fields import _T_DOUBLE, _T_STRING
from ..logging_utils import get_logger
from .catalog import Catalog

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
}
# Reihenfolge im Layerbaum: Punkte oben, Flächen unten
_ROLE_ORDER = (ROLE_GENERATORS, ROLE_DISTRIBUTORS, ROLE_LIGHTS, ROLE_CABLES, ROLE_VEHICLES, ROLE_TENTS)
ALL_ROLES = _ROLE_ORDER


def build_fields(role: str) -> QgsFields:
    fields = QgsFields()
    for name, typ in _LAYER_SPECS[role]["fields"]:
        fields.append(QgsField(name, typ))
    return fields


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
        if role == ROLE_GENERATORS:
            sign = self._sign_expr(self.catalog.stromerzeuger, "Einrichtungen/Elektroversorgung.svg")
            lyr.setRenderer(QgsSingleSymbolRenderer(_generator_symbol(sign)))
            _set_labels(lyr, 'coalesce(nullif("bezeichnung", \'\'), "typ")')
        elif role == ROLE_LIGHTS:
            sign = self._sign_expr(self.catalog.beleuchtung, "Maßnahmen/Beleuchten.svg")
            lyr.setRenderer(QgsSingleSymbolRenderer(_light_symbol(sign)))
            _set_labels(lyr, 'coalesce(nullif("bezeichnung", \'\'), "typ")')
        elif role == ROLE_TENTS:
            lyr.setRenderer(QgsSingleSymbolRenderer(_tent_symbol()))
            _set_labels(lyr, 'coalesce(nullif("bezeichnung", \'\'), "typ")', polygon=True)
        elif role == ROLE_VEHICLES:
            sign = self._sign_expr(self.catalog.fahrzeuge, "THW_Fahrzeuge/Kraftfahrzeug.svg")
            lyr.setRenderer(QgsSingleSymbolRenderer(_vehicle_symbol(sign)))
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
        lyr.triggerRepaint()

    def _sign_expr(self, types, default: str) -> str:
        """Ausdruck: Pfad des taktischen Zeichens je ``typ_id`` (aus dem Katalog)."""

        def path(rel: str) -> str:
            full = os.path.join(self.plugin_dir, "svgs", rel).replace("\\", "/")
            return "'" + full.replace("'", "''") + "'"

        pairs = ", ".join(f"'{t.id}', {path(t.zeichen)}" for t in types if t.zeichen)
        lookup = f'map_get(map({pairs}), "typ_id")' if pairs else "NULL"
        return f"coalesce({lookup}, {path(default)})"


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
    symbol.appendSymbolLayer(_svg_layer(sign_expr, 9.0, Qgis.RenderUnit.Millimeters))
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
    symbol.appendSymbolLayer(_svg_layer(sign_expr, 8.0, Qgis.RenderUnit.Millimeters))
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
    lyr: QgsVectorLayer, expression: str, polygon: bool = False, line: bool = False, y_offset_mm: float = 0.0
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
    settings.minimumScale = 2500

    lyr.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    lyr.setLabelsEnabled(True)
