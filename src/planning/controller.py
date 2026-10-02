"""Steuerung der Lagerplanung: verbindet Dock, Kartenwerkzeuge und Layer."""

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsSettings,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import Qt

from ..logging_utils import get_logger
from .capacity_dialog import CapacityDialog
from .catalog import FootprintType, fmt_m, load_catalog
from .dock import PlanningDock
from .geometry import MetricFrame, tent_polygon
from .layers import FOOTPRINT_ROLES, ROLE_CABLES, ROLE_DISTRIBUTORS, ROLE_TENTS, ROLE_VEHICLES, PlanningLayers
from .tools import AreaTool, CableTool, DeleteTool, DistributorTool, FootprintTool

logger = get_logger(__name__)

_SETTINGS_PREFIX = "thw_toolbox/lagerplanung/"
# Mindestabstand: Zelte (Wege, Abspannung) bzw. Fahrzeuge (Türen, Spiegel)
_DEFAULT_GAPS_M = {ROLE_TENTS: 2.0, ROLE_VEHICLES: 1.0}
_GAP_SETTING_KEYS = {ROLE_TENTS: "abstand", ROLE_VEHICLES: "abstand_fahrzeuge"}
_MSG_TITLE = "Lagerplanung"

TOOL_TENT = "tent"
TOOL_VEHICLE = "vehicle"
TOOL_AREA = "area"
TOOL_CABLE = "cable"
TOOL_DISTRIBUTOR = "distributor"
TOOL_DELETE = "delete"


class PlanningController:
    def __init__(self, iface, plugin_dir: str):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.plugin_dir = plugin_dir
        self.catalog = load_catalog(plugin_dir)
        self.catalog.zelte.append(FootprintType("eigen", "Eigenes Zelt", 5.0, 5.0, 1.0))
        self.catalog.fahrzeuge.append(FootprintType("eigen", "Eigenes Fahrzeug", 6.0, 2.5, 0.0))
        self.layers = PlanningLayers(plugin_dir, self.catalog)
        self.dock: PlanningDock | None = None
        self._tools: dict[str, object] = {}
        self._capacity_dialog: CapacityDialog | None = None

        settings = QgsSettings()
        self.gaps = {
            role: float(settings.value(_SETTINGS_PREFIX + key, _DEFAULT_GAPS_M[role]))
            for role, key in _GAP_SETTING_KEYS.items()
        }
        self.guy_inside_area = settings.value(_SETTINGS_PREFIX + "abspannung_in_flaeche", True, type=bool)
        self.rotation = 0.0
        # Aus welcher Dock-Gruppe die Flächenprüfung gestartet wurde (Zelte/Fahrzeuge)
        self.area_role = ROLE_TENTS
        self.selected_ids = {role: types[0].id for role, types in self._types_by_role().items() if types}
        self.reel_id = self.catalog.leitungsroller[0].id if self.catalog.leitungsroller else None
        self.distributor_id = self.catalog.verteiler[0].id if self.catalog.verteiler else None

        self.canvas.mapToolSet.connect(self._on_map_tool_set)
        QgsProject.instance().projectSaved.connect(self.layers.on_project_saved)

    def unload(self):
        for tool in self._tools.values():
            if self.canvas.mapTool() is tool:
                self.canvas.unsetMapTool(tool)
        self._tools = {}
        try:
            self.canvas.mapToolSet.disconnect(self._on_map_tool_set)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().projectSaved.disconnect(self.layers.on_project_saved)
        except (TypeError, RuntimeError):
            pass
        self._close_capacity_dialog()
        if self.dock:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None

    # ------------------------------------------------------------------
    # Dock
    # ------------------------------------------------------------------

    def toggle_dock(self, visible: bool):
        if self.dock is None:
            self.dock = PlanningDock(self, self.iface.mainWindow())
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
            self.layers.changed.connect(self.dock.refresh_summary)
        self.dock.setVisible(visible)
        if visible:
            self.dock.raise_()
            # Vorhandene Lagerplanung im Projekt übernehmen, ohne neue Layer anzulegen
            self.layers._adopt_project_layers()
            self.dock.refresh_summary()

    # ------------------------------------------------------------------
    # Auswahl / Einstellungen
    # ------------------------------------------------------------------

    def _types_by_role(self) -> dict[str, list[FootprintType]]:
        return {ROLE_TENTS: self.catalog.zelte, ROLE_VEHICLES: self.catalog.fahrzeuge}

    def footprint_types(self, role: str) -> list[FootprintType]:
        return self._types_by_role()[role]

    def current_footprint(self, role: str) -> FootprintType:
        types = self.footprint_types(role)
        return next((t for t in types if t.id == self.selected_ids.get(role)), types[0])

    def select_footprint(self, role: str, type_id: str):
        self.selected_ids[role] = type_id

    def current_reel(self):
        return self.catalog.reel(self.reel_id) if self.reel_id else None

    def current_distributor(self):
        return self.catalog.distributor(self.distributor_id) if self.distributor_id else None

    def set_rotation(self, value: float):
        self.rotation = float(value) % 360.0
        if self.dock:
            self.dock.show_rotation(self.rotation)

    def set_gap(self, role: str, value: float):
        self.gaps[role] = max(0.0, float(value))
        QgsSettings().setValue(_SETTINGS_PREFIX + _GAP_SETTING_KEYS[role], self.gaps[role])

    def set_guy_inside_area(self, value: bool):
        self.guy_inside_area = bool(value)
        QgsSettings().setValue(_SETTINGS_PREFIX + "abspannung_in_flaeche", self.guy_inside_area)

    # ------------------------------------------------------------------
    # Werkzeuge
    # ------------------------------------------------------------------

    def activate_tool(self, kind: str, type_id: str | None = None) -> bool:
        if not self._ensure_layers():
            return False
        if kind == TOOL_CABLE and type_id:
            self.reel_id = type_id
        elif kind == TOOL_DISTRIBUTOR and type_id:
            self.distributor_id = type_id
        elif kind == TOOL_AREA and type_id:
            self.area_role = type_id

        factories = {
            TOOL_TENT: lambda: FootprintTool(self.canvas, self, ROLE_TENTS),
            TOOL_VEHICLE: lambda: FootprintTool(self.canvas, self, ROLE_VEHICLES),
            TOOL_AREA: lambda: AreaTool(self.canvas, self),
            TOOL_CABLE: lambda: CableTool(self.canvas, self),
            TOOL_DISTRIBUTOR: lambda: DistributorTool(self.canvas, self),
            TOOL_DELETE: lambda: DeleteTool(self.canvas, self),
        }
        tool = factories[kind]()
        # Neues Objekt pro Aktivierung: die Rubberbands werden beim Deaktivieren entsorgt
        self._tools[kind] = tool
        self.canvas.setMapTool(tool)
        return True

    def active_tool_kind(self) -> tuple[str | None, str | None]:
        current = self.canvas.mapTool()
        for kind, tool in self._tools.items():
            if tool is current:
                type_id = {
                    TOOL_CABLE: self.reel_id,
                    TOOL_DISTRIBUTOR: self.distributor_id,
                    TOOL_AREA: self.area_role,
                }.get(kind)
                return kind, type_id
        return None, None

    def _on_map_tool_set(self, new_tool, old_tool=None):
        if self.dock:
            self.dock.sync_tool_buttons()

    def on_tool_deactivated(self, tool):
        if self.dock:
            self.dock.sync_tool_buttons()

    def _ensure_layers(self) -> bool:
        crs = self.canvas.mapSettings().destinationCrs()
        if not crs.isValid():
            self._message("Bitte zuerst eine Karte mit gültigem Koordinatensystem laden.", Qgis.MessageLevel.Warning)
            return False
        if not self.layers.ensure(crs):
            self._message("Lagerplanungs-Layer konnten nicht angelegt werden (siehe Log).", Qgis.MessageLevel.Critical)
            return False
        if self.dock:
            self.dock.refresh_summary()
        return True

    # ------------------------------------------------------------------
    # Zelte und Fahrzeuge
    # ------------------------------------------------------------------

    def footprint_conflict(self, role: str, body_m: QgsGeometry, frame: MetricFrame) -> str | None:
        """Name des ersten Zeltes/Fahrzeugs näher als der Mindestabstand von ``role`` (sonst None)."""
        gap = self.gaps[role]
        for other_role in FOOTPRINT_ROLES:
            layer = self.layers.layer(other_role)
            if layer is None:
                continue
            search = frame.geom_from_m(body_m.buffer(gap + 1.0, 2), layer.crs()).boundingBox()
            for feat in layer.getFeatures(QgsFeatureRequest().setFilterRect(search)):
                other = frame.geom_to_m_from(feat.geometry(), layer.crs())
                if other.distance(body_m) < gap - 0.01:
                    return feat.attribute("bezeichnung") or feat.attribute("typ")
        return None

    def place_footprint(self, role: str, center: QgsPointXY, force: bool = False) -> bool:
        layer = self.layers.layer(role)
        if layer is None:
            return False
        obj = self.current_footprint(role)
        frame = MetricFrame(layer.crs(), center)
        body_m = tent_polygon(frame.point_to_m(center), obj.laenge, obj.breite, self.rotation)
        conflict = self.footprint_conflict(role, body_m, frame)
        if conflict and not force:
            self._message(
                f"Zu nah an „{conflict}“ (Mindestabstand {fmt_m(self.gaps[role])} m). Shift+Klick platziert trotzdem.",
                Qgis.MessageLevel.Warning,
            )
            return False
        return self.add_footprints(role, obj, [frame.point_to_m(center)], self.rotation, frame)

    def add_footprints(
        self, role: str, obj: FootprintType, centers_m: list[QgsPointXY], rotation: float, frame: MetricFrame
    ) -> bool:
        layer = self.layers.layer(role)
        if layer is None:
            return False
        features = []
        for c in centers_m:
            feat = self.layers.new_feature(role)
            feat.setGeometry(frame.geom_from_m(tent_polygon(c, obj.laenge, obj.breite, rotation), layer.crs()))
            feat.setAttribute("typ_id", obj.id)
            feat.setAttribute("typ", obj.name)
            feat.setAttribute("bezeichnung", "")
            feat.setAttribute("laenge", obj.laenge)
            feat.setAttribute("breite", obj.breite)
            feat.setAttribute("abspannung", obj.abspannung)
            feat.setAttribute("rotation", round(rotation, 2))
            features.append(feat)
        return self.layers.add_features(role, features)

    def existing_bodies_m(self, frame: MetricFrame, within_m: QgsGeometry | None = None) -> list[QgsGeometry]:
        """Grundflächen aller platzierten Zelte und Fahrzeuge (metrisch) als Hindernisse."""
        bodies = []
        for role in FOOTPRINT_ROLES:
            layer = self.layers.layer(role)
            if layer is None:
                continue
            request = QgsFeatureRequest()
            if within_m is not None:
                request.setFilterRect(frame.geom_from_m(within_m, layer.crs()).boundingBox())
            bodies.extend(
                frame.geom_to_m_from(f.geometry(), layer.crs()) for f in layer.getFeatures(request) if f.hasGeometry()
            )
        return bodies

    # ------------------------------------------------------------------
    # Flächen-Kapazität
    # ------------------------------------------------------------------

    def area_m2(self, geom: QgsGeometry, crs: QgsCoordinateReferenceSystem) -> float:
        if geom is None or geom.isEmpty():
            return 0.0
        frame = MetricFrame(crs, geom.centroid().asPoint())
        return frame.geom_to_m(geom).area()

    def evaluate_area(self, geom: QgsGeometry, crs: QgsCoordinateReferenceSystem):
        if not self._ensure_layers():
            return
        self._close_capacity_dialog()
        self._capacity_dialog = CapacityDialog(self, geom, crs, self.area_role, self.iface.mainWindow())
        self._capacity_dialog.show()

    def _close_capacity_dialog(self):
        if self._capacity_dialog is not None:
            try:
                self._capacity_dialog.close()
            except RuntimeError:
                pass  # schon durch WA_DeleteOnClose gelöscht
            self._capacity_dialog = None

    def evaluate_selected_area(self, role: str = ROLE_TENTS):
        self.area_role = role
        layer = self.iface.activeLayer()
        if (
            not isinstance(layer, QgsVectorLayer)
            or layer.geometryType() != Qgis.GeometryType.Polygon
            or layer.selectedFeatureCount() == 0
        ):
            self._message(
                "Bitte im Layerbaum einen Flächen-Layer aktivieren und darin eine oder mehrere Flächen auswählen.",
                Qgis.MessageLevel.Info,
            )
            return
        geom = QgsGeometry.unaryUnion([f.geometry() for f in layer.selectedFeatures() if f.hasGeometry()])
        self.evaluate_area(geom, layer.crs())

    # ------------------------------------------------------------------
    # Strom
    # ------------------------------------------------------------------

    def place_cable(self, points: list[QgsPointXY], length_m: float) -> bool:
        reel = self.current_reel()
        if reel is None:
            return False
        feat = self.layers.new_feature(ROLE_CABLES)
        feat.setGeometry(QgsGeometry.fromPolylineXY(points))
        feat.setAttribute("typ_id", reel.id)
        feat.setAttribute("typ", reel.name)
        feat.setAttribute("bezeichnung", "")
        feat.setAttribute("max_laenge", reel.laenge)
        feat.setAttribute("laenge", round(length_m, 1))
        return self.layers.add_features(ROLE_CABLES, [feat])

    def place_distributor(self, point: QgsPointXY) -> bool:
        dist = self.current_distributor()
        if dist is None:
            return False
        feat = self.layers.new_feature(ROLE_DISTRIBUTORS)
        feat.setGeometry(QgsGeometry.fromPointXY(point))
        feat.setAttribute("typ_id", dist.id)
        feat.setAttribute("typ", dist.name)
        feat.setAttribute("bezeichnung", "")
        return self.layers.add_features(ROLE_DISTRIBUTORS, [feat])

    def snap_target(self, map_point: QgsPointXY, tolerance: float, target_crs) -> QgsPointXY | None:
        """Nächster Verteiler bzw. nächstes Leitungsende im Fangradius (in ``target_crs``)."""
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        project = QgsProject.instance()
        best, best_d = None, tolerance
        for role in (ROLE_DISTRIBUTORS, ROLE_CABLES):
            layer = self.layers.layer(role)
            if layer is None:
                continue
            to_canvas = QgsCoordinateTransform(layer.crs(), canvas_crs, project)
            for feat in layer.getFeatures():
                geom = feat.geometry()
                if geom.isEmpty():
                    continue
                if role == ROLE_DISTRIBUTORS:
                    candidates = [geom.asPoint()]
                else:
                    line = geom.asMultiPolyline()[0] if geom.isMultipart() else geom.asPolyline()
                    candidates = [line[0], line[-1]] if line else []
                for pt in candidates:
                    p_canvas = to_canvas.transform(pt)
                    d = p_canvas.distance(map_point)
                    if d <= best_d:
                        best, best_d = (pt, layer.crs()), d
        if best is None:
            return None
        pt, crs = best
        return QgsPointXY(QgsCoordinateTransform(crs, target_crs, project).transform(pt))

    # ------------------------------------------------------------------
    # Bilanz
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """Zählt Zelte, Fahrzeuge, Leitungen und Verteiler je Typ."""
        result = {"zelte": {}, "fahrzeuge": {}, "leitungen": {}, "verteiler": {}}
        for key, layer in (("zelte", self.layers.tents), ("fahrzeuge", self.layers.vehicles)):
            if layer is None:
                continue
            for f in layer.getFeatures():
                entry = result[key].setdefault(f.attribute("typ") or "?", {"anzahl": 0, "flaeche": 0.0})
                entry["anzahl"] += 1
                entry["flaeche"] += float(f.attribute("laenge") or 0) * float(f.attribute("breite") or 0)
        if self.layers.cables:
            for f in self.layers.cables.getFeatures():
                entry = result["leitungen"].setdefault(f.attribute("typ") or "?", {"anzahl": 0, "laenge": 0.0})
                entry["anzahl"] += 1
                entry["laenge"] += float(f.attribute("laenge") or 0)
        if self.layers.distributors:
            for f in self.layers.distributors.getFeatures():
                entry = result["verteiler"].setdefault(f.attribute("typ") or "?", {"anzahl": 0})
                entry["anzahl"] += 1
        return result

    def _message(self, text: str, level=Qgis.MessageLevel.Info):
        self.iface.messageBar().pushMessage(_MSG_TITLE, text, level, 6)
