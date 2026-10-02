"""GeoPackage-Layer der Lagerplanung (Zelte, Leitungen, Verteiler)."""

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
    QgsRendererCategory,
    QgsSingleSymbolRenderer,
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

GPKG_SUFFIX = "_lagerplanung"
GROUP_NAME = "Lagerplanung"
# Custom property, über die wir unsere Layer auch nach Umbenennen wiederfinden
ROLE_PROPERTY = "thw_toolbox/lagerplanung"

ROLE_TENTS = "zelte"
ROLE_VEHICLES = "fahrzeuge"
ROLE_CABLES = "leitungen"
ROLE_DISTRIBUTORS = "verteiler"

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
}
# Reihenfolge im Layerbaum: Punkte oben, Flächen unten
_ROLE_ORDER = (ROLE_DISTRIBUTORS, ROLE_CABLES, ROLE_VEHICLES, ROLE_TENTS)


def build_fields(role: str) -> QgsFields:
    fields = QgsFields()
    for name, typ in _LAYER_SPECS[role]["fields"]:
        fields.append(QgsField(name, typ))
    return fields


class PlanningLayers(QObject):
    """Legt die drei Lagerplanungs-Layer an bzw. findet sie im Projekt wieder.

    Gespeichert wird in ``<projekt>_lagerplanung.gpkg`` neben der
    Projektdatei (bzw. im Plugin-``tmp/`` solange das Projekt ungespeichert
    ist). ``changed`` feuert, sobald sich Features ändern — das Dock
    aktualisiert damit seine Bilanz.
    """

    changed = pyqtSignal()

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
        """Stellt sicher, dass alle drei Layer im Projekt sind. False bei Fehler."""
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
            role = lyr.customProperty(ROLE_PROPERTY)
            if role in _LAYER_SPECS and self.layer(role) is None and isinstance(lyr, QgsVectorLayer):
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
            logger.error("Lagerplanungs-Layer %s konnte nicht angelegt werden: %s", role, result[1])
            return None
        lyr = QgsVectorLayer(uri, title, "ogr")
        if not lyr.isValid():
            logger.error("Lagerplanungs-Layer %s ist ungültig: %s", role, uri)
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
        # Auch Änderungen mit QGIS-eigenen Werkzeugen (Verschieben, Löschen,
        # Attributtabelle) sollen die Bilanz aktualisieren.
        for signal in (lyr.featureAdded, lyr.featureDeleted, lyr.geometryChanged, lyr.afterCommitChanges):
            try:
                signal.connect(self._emit_changed)
            except (TypeError, RuntimeError):
                pass

    def _emit_changed(self, *args):
        self.changed.emit()

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
                logger.error("Lagerplanung: Layer %s konnte nicht nach %s kopiert werden: %s", role, target, result[1])
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
        if role == ROLE_TENTS:
            lyr.setRenderer(QgsSingleSymbolRenderer(_tent_symbol()))
            _set_labels(lyr, 'coalesce(nullif("bezeichnung", \'\'), "typ")', polygon=True)
        elif role == ROLE_VEHICLES:
            lyr.setRenderer(QgsSingleSymbolRenderer(_vehicle_symbol()))
            # Nur die Kurzbezeichnung („GKW“ statt „GKW (Gerätekraftwagen)“)
            _set_labels(
                lyr,
                "coalesce(nullif(\"bezeichnung\", ''), trim(regexp_replace(\"typ\", '[(].*', '')))",
                polygon=True,
            )
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


def _tent_symbol() -> QgsFillSymbol:
    symbol = QgsFillSymbol.createSimple(
        {
            "color": "76,175,80,110",
            "outline_color": "27,94,32,255",
            "outline_width": "0.5",
            "outline_width_unit": "MM",
        }
    )
    # Abspannbereich als gestrichelte Kontur, Abstand in echten Metern
    # (Projekt-CRS kann Grad oder Web-Mercator sein → über UTM puffern).
    expr = (
        "with_variable('m', "
        "'EPSG:' || to_string(32600 + "
        "floor((x(transform(centroid($geometry), @layer_crs, 'EPSG:4326')) + 180) / 6) + 1),"
        'transform(buffer(transform($geometry, @layer_crs, @m), coalesce("abspannung", 0), 2), @m, @layer_crs))'
    )
    generator = QgsGeometryGeneratorSymbolLayer.create({"geometryModifier": expr, "SymbolType": "Fill"})
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


def _vehicle_symbol() -> QgsFillSymbol:
    symbol = QgsFillSymbol.createSimple(
        {
            "color": "100,149,237,140",
            "outline_color": "13,71,161,255",
            "outline_width": "0.4",
            "outline_width_unit": "MM",
        }
    )
    # Fahrzeugfront: Rechtecke werden mit der Front an der Kante Ecke 2→3 erzeugt
    generator = QgsGeometryGeneratorSymbolLayer.create(
        {"geometryModifier": "make_line(point_n($geometry, 2), point_n($geometry, 3))", "SymbolType": "Line"}
    )
    if generator is not None:
        generator.setSymbolType(Qgis.SymbolType.Line)
        generator.setSubSymbol(
            QgsLineSymbol.createSimple(
                {"line_color": "13,71,161,255", "line_width": "1.2", "line_width_unit": "MM", "capstyle": "flat"}
            )
        )
        symbol.appendSymbolLayer(generator)
    return symbol


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


def _distributor_symbol() -> QgsMarkerSymbol:
    return QgsMarkerSymbol.createSimple(
        {"name": "square", "color": "251,192,45,255", "outline_color": "0,0,0,255", "outline_width": "0.5", "size": "4"}
    )


def _set_labels(lyr: QgsVectorLayer, expression: str, polygon: bool = False, line: bool = False) -> None:
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
        elif line:
            settings.placement = Qgis.LabelPlacement.Line
        else:
            settings.placement = Qgis.LabelPlacement.OrderedPositionsAroundPoint
    except AttributeError:
        logger.debug("Label-Platzierung nicht gesetzt", exc_info=True)
    # Beschriftungen nur bei nahem Zoom, sonst wird es unübersichtlich
    settings.scaleVisibility = True
    settings.maximumScale = 1
    settings.minimumScale = 2500

    lyr.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    lyr.setLabelsEnabled(True)
