"""Steuerung der Objektplanung: verbindet Dock, Hotbar, Kartenwerkzeuge und Layer."""

import math

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsSettings,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QObject, Qt, pyqtSignal

from ..logging_utils import get_logger
from .capacity_dialog import CapacityDialog
from .catalog import FootprintType, fmt_m, load_catalog
from .dock import PlanningDock
from .geometry import MetricFrame, align_snap, grid_centers, tent_polygon
from .hotbar import PlanningHotbar
from .layers import (
    ALL_ROLES,
    FOOTPRINT_ROLES,
    POINT_ROLES,
    ROLE_CABLES,
    ROLE_DISTRIBUTORS,
    ROLE_GENERATORS,
    ROLE_LIGHTS,
    ROLE_TENTS,
    ROLE_VEHICLES,
    PlanningLayers,
)
from .tools import AreaTool, CableTool, FootprintTool, PointTool, SelectTool

logger = get_logger(__name__)

_SETTINGS_PREFIX = "thw_toolbox/objektplanung/"
# Mindestabstand: Zelte (Wege, Abspannung) bzw. Fahrzeuge (Türen, Spiegel)
_DEFAULT_GAPS_M = {ROLE_TENTS: 2.0, ROLE_VEHICLES: 1.0}
_GAP_SETTING_KEYS = {ROLE_TENTS: "abstand", ROLE_VEHICLES: "abstand_fahrzeuge"}
_MSG_TITLE = "Objektplanung"
# Nur Nachbarn in diesem Umkreis kommen als Ausrichtungsziel in Frage (Meter)
_ALIGN_SEARCH_M = 60.0
# Leitungsenden und Punkte, die näher beieinander liegen, gelten als verbunden (Meter)
_CONNECT_TOL_M = 0.5
# Grober Leistungsfaktor: so viel kW liefert ein Stromerzeuger je kVA
_POWER_FACTOR = 0.8
_MAX_GRID = 20

TOOL_SELECT = "select"
TOOL_TENT = "tent"
TOOL_VEHICLE = "vehicle"
TOOL_AREA = "area"
TOOL_CABLE = "cable"
TOOL_DISTRIBUTOR = "distributor"
TOOL_GENERATOR = "generator"
TOOL_LIGHT = "light"

POINT_TOOL_ROLES = {TOOL_DISTRIBUTOR: ROLE_DISTRIBUTORS, TOOL_GENERATOR: ROLE_GENERATORS, TOOL_LIGHT: ROLE_LIGHTS}


class PlanningController(QObject):
    # Typ-Auswahl, Drehung oder Raster wurden geändert → Dock und Hotbar abgleichen
    state_changed = pyqtSignal()

    def __init__(self, iface, plugin_dir: str):
        super().__init__()
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.plugin_dir = plugin_dir
        self.catalog = load_catalog(plugin_dir)
        self.catalog.zelte.append(FootprintType("eigen", "Eigenes Zelt", 5.0, 5.0, 1.0))
        self.catalog.fahrzeuge.append(
            FootprintType("eigen", "Eigenes Fahrzeug", 6.0, 2.5, 0.0, "THW_Fahrzeuge/Kraftfahrzeug.svg")
        )
        self.layers = PlanningLayers(plugin_dir, self.catalog)
        self.dock: PlanningDock | None = None
        self.hotbar: PlanningHotbar | None = None
        self._tools: dict[str, object] = {}
        self._capacity_dialog: CapacityDialog | None = None

        settings = QgsSettings()
        self.gaps = {
            role: float(settings.value(_SETTINGS_PREFIX + key, _DEFAULT_GAPS_M[role]))
            for role, key in _GAP_SETTING_KEYS.items()
        }
        self.guy_inside_area = settings.value(_SETTINGS_PREFIX + "abspannung_in_flaeche", True, type=bool)
        self.align_enabled = settings.value(_SETTINGS_PREFIX + "ausrichten", True, type=bool)
        self.rotation = 0.0
        self.grid_rows = 1
        self.grid_cols = 1
        # Aus welcher Dock-Gruppe die Flächenprüfung gestartet wurde (Zelte/Fahrzeuge)
        self.area_role = ROLE_TENTS
        self.selected_ids = {role: types[0].id for role, types in self._types_by_role().items() if types}
        self.reel_id = self.catalog.leitungsroller[0].id if self.catalog.leitungsroller else None
        self.point_ids = {
            role: (types[0].id if types else None)
            for role, types in (
                (ROLE_DISTRIBUTORS, self.catalog.verteiler),
                (ROLE_GENERATORS, self.catalog.stromerzeuger),
                (ROLE_LIGHTS, self.catalog.beleuchtung),
            )
        }

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
        if self.hotbar:
            self.hotbar.dispose()
            self.hotbar = None
        if self.dock:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None

    # ------------------------------------------------------------------
    # Dock und Hotbar
    # ------------------------------------------------------------------

    def toggle_dock(self, visible: bool):
        if self.dock is None:
            self.dock = PlanningDock(self, self.iface.mainWindow())
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
            self.layers.changed.connect(self.dock.refresh_summary)
            self.layers.selection_changed.connect(self.dock.refresh_selection)
        if self.hotbar is None:
            self.hotbar = PlanningHotbar(self, self.plugin_dir, self.canvas)
            self.layers.selection_changed.connect(self.hotbar.refresh_selection)
        self.dock.setVisible(visible)
        self.hotbar.setVisible(visible)
        if not visible:
            self.deactivate_tool()
        if visible:
            self.dock.raise_()
            # Vorhandene Objektplanung im Projekt übernehmen, ohne neue Layer anzulegen
            self.layers._adopt_project_layers()
            self.dock.refresh_summary()
            self.dock.refresh_selection()
            self.hotbar.refresh_selection()
            # Standardwerkzeug ist die Auswahl (Cursor)
            if self.active_tool_kind()[0] is None:
                self.activate_tool(TOOL_SELECT)

    def _sync_ui(self):
        if self.dock:
            self.dock.sync_tool_buttons()
        if self.hotbar:
            self.hotbar.sync_tool_buttons()

    def focus_label(self):
        """Doppelklick auf ein Objekt: Bezeichnung im Dock bearbeiten."""
        if self.dock:
            self.dock.focus_label()

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
        self.state_changed.emit()

    def current_reel(self):
        return self.catalog.reel(self.reel_id) if self.reel_id else None

    def point_types(self, role: str) -> list:
        return {
            ROLE_DISTRIBUTORS: self.catalog.verteiler,
            ROLE_GENERATORS: self.catalog.stromerzeuger,
            ROLE_LIGHTS: self.catalog.beleuchtung,
        }[role]

    def current_point_type(self, role: str):
        types = self.point_types(role)
        return next((t for t in types if t.id == self.point_ids.get(role)), types[0] if types else None)

    def select_point_type(self, role: str, type_id: str):
        self.point_ids[role] = type_id
        self.state_changed.emit()

    def set_rotation(self, value: float):
        self.rotation = float(value) % 360.0
        self.state_changed.emit()

    def set_grid(self, rows: int, cols: int):
        self.grid_rows = max(1, min(_MAX_GRID, int(rows)))
        self.grid_cols = max(1, min(_MAX_GRID, int(cols)))
        self.state_changed.emit()

    def set_gap(self, role: str, value: float):
        self.gaps[role] = max(0.0, float(value))
        QgsSettings().setValue(_SETTINGS_PREFIX + _GAP_SETTING_KEYS[role], self.gaps[role])

    def set_guy_inside_area(self, value: bool):
        self.guy_inside_area = bool(value)
        QgsSettings().setValue(_SETTINGS_PREFIX + "abspannung_in_flaeche", self.guy_inside_area)

    def set_align_enabled(self, value: bool):
        self.align_enabled = bool(value)
        QgsSettings().setValue(_SETTINGS_PREFIX + "ausrichten", self.align_enabled)
        self.state_changed.emit()

    # ------------------------------------------------------------------
    # Werkzeuge
    # ------------------------------------------------------------------

    def activate_tool(self, kind: str, type_id: str | None = None) -> bool:
        # Auswählen braucht keine Layer – beim bloßen Öffnen nichts im Projekt anlegen
        if kind == TOOL_SELECT:
            self.layers._adopt_project_layers()
        elif not self._ensure_layers():
            self._sync_ui()
            return False
        if kind == TOOL_CABLE and type_id:
            self.reel_id = type_id
        elif kind in POINT_TOOL_ROLES and type_id:
            self.point_ids[POINT_TOOL_ROLES[kind]] = type_id
        elif kind == TOOL_AREA and type_id:
            self.area_role = type_id
        elif kind in (TOOL_TENT, TOOL_VEHICLE) and type_id:
            self.selected_ids[ROLE_TENTS if kind == TOOL_TENT else ROLE_VEHICLES] = type_id

        if kind == TOOL_SELECT:
            tool = SelectTool(self.canvas, self)
        elif kind == TOOL_TENT:
            tool = FootprintTool(self.canvas, self, ROLE_TENTS)
        elif kind == TOOL_VEHICLE:
            tool = FootprintTool(self.canvas, self, ROLE_VEHICLES)
        elif kind == TOOL_AREA:
            tool = AreaTool(self.canvas, self)
        elif kind == TOOL_CABLE:
            tool = CableTool(self.canvas, self)
        else:
            tool = PointTool(self.canvas, self, POINT_TOOL_ROLES[kind])
        # Neues Objekt pro Aktivierung: die Rubberbands werden beim Deaktivieren entsorgt
        self._tools[kind] = tool
        self.canvas.setMapTool(tool)
        self.state_changed.emit()
        return True

    def deactivate_tool(self):
        tool = self.canvas.mapTool()
        if tool is not None and tool in self._tools.values():
            self.canvas.unsetMapTool(tool)

    def back_to_select(self):
        """Nach dem Platzieren (Esc / Werkzeug abwählen) zurück zum Auswahlwerkzeug, wie in Figma."""
        if self.active_tool_kind()[0] == TOOL_SELECT:
            self._sync_ui()
            return
        self.activate_tool(TOOL_SELECT)

    def active_tool_kind(self) -> tuple[str | None, str | None]:
        current = self.canvas.mapTool()
        for kind, tool in self._tools.items():
            if tool is current:
                if kind == TOOL_CABLE:
                    return kind, self.reel_id
                if kind in POINT_TOOL_ROLES:
                    return kind, self.point_ids.get(POINT_TOOL_ROLES[kind])
                if kind == TOOL_AREA:
                    return kind, self.area_role
                return kind, None
        return None, None

    def _on_map_tool_set(self, new_tool, old_tool=None):
        self._sync_ui()

    def on_tool_deactivated(self, tool):
        self._sync_ui()

    def _ensure_layers(self) -> bool:
        crs = self.canvas.mapSettings().destinationCrs()
        if not crs.isValid():
            self._message("Bitte zuerst eine Karte mit gültigem Koordinatensystem laden.", Qgis.MessageLevel.Warning)
            return False
        if not self.layers.ensure(crs):
            self._message("Objektplanungs-Layer konnten nicht angelegt werden (siehe Log).", Qgis.MessageLevel.Critical)
            return False
        if self.dock:
            self.dock.refresh_summary()
        return True

    # ------------------------------------------------------------------
    # Zelte und Fahrzeuge
    # ------------------------------------------------------------------

    def footprint_conflict(
        self, role: str, body_m: QgsGeometry, frame: MetricFrame, exclude: dict | None = None
    ) -> str | None:
        """Name des ersten Zeltes/Fahrzeugs näher als der Mindestabstand von ``role`` (sonst None).

        ``exclude``: ``{role: set(fids)}``, die nicht zählen (z.B. gerade verschobene Objekte).
        """
        gap = self.gaps[role]
        for other_role in FOOTPRINT_ROLES:
            layer = self.layers.layer(other_role)
            if layer is None:
                continue
            skip = (exclude or {}).get(other_role, set())
            search = frame.geom_from_m(body_m.buffer(gap + 1.0, 2), layer.crs()).boundingBox()
            for feat in layer.getFeatures(QgsFeatureRequest().setFilterRect(search)):
                if feat.id() in skip:
                    continue
                other = frame.geom_to_m_from(feat.geometry(), layer.crs())
                if other.distance(body_m) < gap - 0.01:
                    return feat.attribute("bezeichnung") or feat.attribute("typ")
        return None

    def grid_bodies_m(self, role: str, center_m: QgsPointXY) -> list[QgsGeometry]:
        """Grundflächen des aktuellen Rasters (Reihen × Spalten) um ``center_m``."""
        obj = self.current_footprint(role)
        centers = grid_centers(
            center_m, self.grid_rows, self.grid_cols, obj.laenge, obj.breite, self.gaps[role], self.rotation
        )
        return [tent_polygon(c, obj.laenge, obj.breite, self.rotation) for c in centers]

    def place_footprint(self, role: str, center: QgsPointXY) -> bool:
        """Zelt/Fahrzeug bzw. Raster setzen. Zu geringer Abstand wird nur gemeldet."""
        layer = self.layers.layer(role)
        if layer is None:
            return False
        obj = self.current_footprint(role)
        frame = MetricFrame(layer.crs(), center)
        center_m = frame.point_to_m(center)
        centers = grid_centers(
            center_m, self.grid_rows, self.grid_cols, obj.laenge, obj.breite, self.gaps[role], self.rotation
        )
        conflict = next(
            (
                c
                for c in (
                    self.footprint_conflict(role, tent_polygon(p, obj.laenge, obj.breite, self.rotation), frame)
                    for p in centers
                )
                if c
            ),
            None,
        )
        ok = self.add_footprints(role, obj, centers, self.rotation, frame)
        if ok and conflict:
            self._message(
                f"Achtung: Mindestabstand {fmt_m(self.gaps[role])} m zu „{conflict}“ unterschritten.",
                Qgis.MessageLevel.Warning,
            )
        return ok

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
        return [g for _role, _fid, g in self._footprints_m(frame, within_m)]

    def _footprints_m(self, frame: MetricFrame, within_m: QgsGeometry | None = None, exclude: dict | None = None):
        result = []
        for role in FOOTPRINT_ROLES:
            layer = self.layers.layer(role)
            if layer is None:
                continue
            skip = (exclude or {}).get(role, set())
            request = QgsFeatureRequest()
            if within_m is not None:
                request.setFilterRect(frame.geom_from_m(within_m, layer.crs()).boundingBox())
            for f in layer.getFeatures(request):
                if f.hasGeometry() and f.id() not in skip:
                    result.append((role, f.id(), frame.geom_to_m_from(f.geometry(), layer.crs())))
        return result

    def align(self, moving_m: QgsGeometry, frame: MetricFrame, rotation: float, gap: float, tol_m: float, exclude=None):
        """Hilfslinien: ``moving_m`` an benachbarten Zelten/Fahrzeugen ausrichten (siehe ``align_snap``)."""
        if not self.align_enabled or moving_m is None or moving_m.isEmpty():
            return None
        search = moving_m.buffer(_ALIGN_SEARCH_M, 2)
        others = [g for _r, _f, g in self._footprints_m(frame, search, exclude)]
        return align_snap(moving_m, others, rotation, gap, tol_m)

    # ------------------------------------------------------------------
    # Auswahl (QGIS-Auswahl in den Objektplanungs-Layern)
    # ------------------------------------------------------------------

    def selection(self) -> list[tuple[str, QgsFeature]]:
        result = []
        for role in ALL_ROLES:
            layer = self.layers.layer(role)
            if layer is not None:
                result.extend((role, f) for f in layer.selectedFeatures())
        return result

    def selection_ids(self) -> dict[str, set[int]]:
        result = {}
        for role in ALL_ROLES:
            layer = self.layers.layer(role)
            if layer is not None and layer.selectedFeatureCount():
                result[role] = set(layer.selectedFeatureIds())
        return result

    def clear_selection(self):
        for layer in self.layers.all_layers():
            layer.removeSelection()

    def select(self, hits: list[tuple[str, int]], mode: str = "replace"):
        """``mode``: replace, add oder toggle."""
        by_role: dict[str, set[int]] = {}
        for role, fid in hits:
            by_role.setdefault(role, set()).add(fid)
        for role in ALL_ROLES:
            layer = self.layers.layer(role)
            if layer is None:
                continue
            ids = by_role.get(role, set())
            if mode == "replace":
                layer.selectByIds(list(ids))
            elif mode == "add" and ids:
                layer.selectByIds(list(ids), Qgis.SelectBehavior.AddToSelection)
            elif mode == "toggle" and ids:
                current = set(layer.selectedFeatureIds())
                layer.selectByIds(list(current.symmetric_difference(ids)))

    def delete_selection(self) -> int:
        count = 0
        for role, fids in self.selection_ids().items():
            if self.layers.delete_features(role, list(fids)):
                count += len(fids)
        return count

    def transform_selection(self, geometries: dict[str, dict[int, QgsGeometry]], rotation_delta: float = 0.0):
        """Neue Geometrien (Layer-CRS) schreiben; Zelte/Fahrzeuge bekommen ``rotation`` += Delta."""
        for role, geoms in geometries.items():
            attrs = None
            if rotation_delta and role in FOOTPRINT_ROLES:
                layer = self.layers.layer(role)
                attrs = {}
                for fid in geoms:
                    old = float(layer.getFeature(fid).attribute("rotation") or 0)
                    attrs[fid] = {"rotation": round((old + rotation_delta) % 360.0, 2)}
            self.layers.change_features(role, geoms, attrs)

    def move_selection(self, geometries: dict[str, dict[int, QgsGeometry]]):
        self.transform_selection(geometries)

    def nudge_selection(self, dx_m: float, dy_m: float):
        """Auswahl um ``dx_m``/``dy_m`` Meter (Ost/Nord) verschieben."""
        result = {}
        for role, f in self.selection():
            if not f.hasGeometry():
                continue
            layer = self.layers.layer(role)
            frame = MetricFrame(layer.crs(), f.geometry().centroid().asPoint())
            g = frame.geom_to_m(f.geometry())
            g.translate(dx_m, dy_m)
            result.setdefault(role, {})[f.id()] = frame.geom_from_m(g)
        self.transform_selection(result)

    def nearest_gap(self, moving_m: QgsGeometry, frame: MetricFrame, exclude=None) -> tuple[float, str] | None:
        """Kleinster Abstand (m) von ``moving_m`` zu einem Zelt/Fahrzeug in der Nähe und dessen Name."""
        if moving_m is None or moving_m.isEmpty():
            return None
        best = None
        search = moving_m.buffer(_ALIGN_SEARCH_M, 2)
        for role, fid, geom in self._footprints_m(frame, search, exclude):
            d = geom.distance(moving_m)
            if best is None or d < best[0]:
                feat = self.layers.layer(role).getFeature(fid)
                best = (d, feat.attribute("bezeichnung") or feat.attribute("typ") or "")
        return best

    def selection_overlay(self) -> tuple[list[QgsGeometry], list[QgsPointXY] | None, str]:
        """Auswahl für das Overlay: Geometrien und Rahmen (Canvas-CRS) plus Badge-Text."""
        selected = self.selection()
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        project = QgsProject.instance()
        geoms = []
        for role, f in selected:
            if not f.hasGeometry():
                continue
            g = QgsGeometry(f.geometry())
            g.transform(QgsCoordinateTransform(self.layers.layer(role).crs(), canvas_crs, project))
            geoms.append(g)
        if not geoms:
            return [], None, ""
        if len(selected) == 1 and selected[0][0] in FOOTPRINT_ROLES:
            # Einzelnes Zelt/Fahrzeug: Rahmen dreht mit dem Objekt (wie in Figma)
            f = selected[0][1]
            ring = geoms[0].asPolygon()[0] if not geoms[0].isMultipart() else geoms[0].asMultiPolygon()[0][0]
            corners = [QgsPointXY(p) for p in ring[:4]]
            # Ecke 4/1 = links (hinten), so liegt der Rahmen „aufrecht“ wie das Objekt
            corners = [corners[3], corners[2], corners[1], corners[0]]
            badge = (
                f"{fmt_m(float(f.attribute('laenge') or 0))} × {fmt_m(float(f.attribute('breite') or 0))} m"
                f" · {fmt_m(round(float(f.attribute('rotation') or 0)))}°"
            )
            return geoms, corners, badge
        box = QgsRectangle(geoms[0].boundingBox())
        for g in geoms[1:]:
            box.combineExtentWith(g.boundingBox())
        corners = [
            QgsPointXY(box.xMinimum(), box.yMaximum()),
            QgsPointXY(box.xMaximum(), box.yMaximum()),
            QgsPointXY(box.xMaximum(), box.yMinimum()),
            QgsPointXY(box.xMinimum(), box.yMinimum()),
        ]
        if len(selected) == 1:
            role, f = selected[0]
            badge = f.attribute("bezeichnung") or f.attribute("typ") or ""
        else:
            badge = f"{len(selected)} Objekte"
        return geoms, corners, badge

    def rotate_selection(self, step: float):
        """Zelte und Fahrzeuge der Auswahl jeweils um die eigene Mitte drehen."""
        for role in FOOTPRINT_ROLES:
            layer = self.layers.layer(role)
            if layer is None or not layer.selectedFeatureCount():
                continue
            geoms, attrs = {}, {}
            for f in layer.selectedFeatures():
                if not f.hasGeometry():
                    continue
                center = f.geometry().centroid().asPoint()
                frame = MetricFrame(layer.crs(), center)
                g = frame.geom_to_m(f.geometry())
                g.rotate(step, frame.point_to_m(center))
                geoms[f.id()] = frame.geom_from_m(g)
                attrs[f.id()] = {"rotation": round((float(f.attribute("rotation") or 0) + step) % 360.0, 2)}
            self.layers.change_features(role, geoms, attrs)

    def set_selection_label(self, text: str):
        for role, fids in self.selection_ids().items():
            self.layers.change_features(role, {}, {fid: {"bezeichnung": text} for fid in fids})

    def duplicate_selection(self, offset: bool = True) -> int:
        """Auswahl kopieren, um ihre Breite + Mindestabstand versetzt; die Kopien werden ausgewählt.

        ``offset=False`` legt die Kopien deckungsgleich ab (Alt+Ziehen zieht sie dann weg).
        """
        selected = self.selection()
        if not selected:
            return 0
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        project = QgsProject.instance()
        bbox = None
        for role, f in selected:
            g = QgsGeometry(f.geometry())
            g.transform(QgsCoordinateTransform(self.layers.layer(role).crs(), canvas_crs, project))
            if bbox is None:
                bbox = QgsRectangle(g.boundingBox())
            else:
                bbox.combineExtentWith(g.boundingBox())
        frame = MetricFrame(canvas_crs, bbox.center())
        width_m = frame.geom_to_m(QgsGeometry.fromRect(bbox)).boundingBox().width()
        gap = max(self.gaps.get(role, 0.0) for role, _f in selected)
        shift_m = width_m + max(gap, 1.0) if offset else 0.0

        new_by_role: dict[str, list[QgsFeature]] = {}
        for role, f in selected:
            layer = self.layers.layer(role)
            g = frame.geom_to_m_from(f.geometry(), layer.crs())
            g.translate(shift_m, 0.0)
            copy = QgsFeature(f)
            copy.setId(-1)
            copy.setGeometry(frame.geom_from_m(g, layer.crs()))
            # Primärschlüssel des GeoPackages nicht mitkopieren
            fid_idx = layer.fields().indexOf("fid")
            if fid_idx >= 0:
                copy.setAttribute(fid_idx, None)
            new_by_role.setdefault(role, []).append(copy)

        hits = []
        for role, feats in new_by_role.items():
            layer = self.layers.layer(role)
            before = set(layer.allFeatureIds())
            self.layers.add_features(role, feats)
            hits.extend((role, fid) for fid in set(layer.allFeatureIds()) - before)
        self.select(hits)
        return len(hits)

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
    # Strom und Beleuchtung
    # ------------------------------------------------------------------

    def place_cable(self, points: list[QgsPointXY], length_m: float) -> bool:
        return self.place_cables([(points, length_m)])

    def place_cables(self, pieces: list[tuple[list[QgsPointXY], float]]) -> bool:
        """Eine Strecke aus mehreren Leitungsrollern: je Stück ``(Punkte im Layer-CRS, Länge m)`` ein Feature."""
        reel = self.current_reel()
        if reel is None or not pieces:
            return False
        features = []
        for points, length_m in pieces:
            feat = self.layers.new_feature(ROLE_CABLES)
            feat.setGeometry(QgsGeometry.fromPolylineXY(points))
            feat.setAttribute("typ_id", reel.id)
            feat.setAttribute("typ", reel.name)
            feat.setAttribute("bezeichnung", "")
            feat.setAttribute("max_laenge", reel.laenge)
            feat.setAttribute("laenge", round(length_m, 1))
            features.append(feat)
        return self.layers.add_features(ROLE_CABLES, features)

    def place_point(self, role: str, point: QgsPointXY) -> bool:
        obj = self.current_point_type(role)
        if obj is None:
            return False
        feat = self.layers.new_feature(role)
        feat.setGeometry(QgsGeometry.fromPointXY(point))
        feat.setAttribute("typ_id", obj.id)
        feat.setAttribute("typ", obj.name)
        feat.setAttribute("bezeichnung", "")
        if role == ROLE_GENERATORS:
            feat.setAttribute("leistung_kva", obj.leistung_kva)
        elif role == ROLE_LIGHTS:
            feat.setAttribute("leistung_w", obj.leistung_w)
            feat.setAttribute("radius", obj.radius)
        return self.layers.add_features(role, [feat])

    def snap_target(self, map_point: QgsPointXY, tolerance: float, target_crs) -> QgsPointXY | None:
        """Nächster Stromerzeuger, Verteiler, Leuchte bzw. nächstes Leitungsende im Fangradius (in ``target_crs``)."""
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        project = QgsProject.instance()
        best, best_d = None, tolerance
        for role in (*POINT_ROLES, ROLE_CABLES):
            layer = self.layers.layer(role)
            if layer is None:
                continue
            to_canvas = QgsCoordinateTransform(layer.crs(), canvas_crs, project)
            for pt in _connection_points(layer, role):
                d = to_canvas.transform(pt).distance(map_point)
                if d <= best_d:
                    best, best_d = (pt, layer.crs()), d
        if best is None:
            return None
        pt, crs = best
        return QgsPointXY(QgsCoordinateTransform(crs, target_crs, project).transform(pt))

    def power_networks(self) -> tuple[list[dict], int]:
        """Welche Leuchten hängen (über Leitungen und Verteiler) an welchem Stromerzeuger?

        Liefert ``([{name, kva, last_w, leuchten}], nicht_angeschlossene_leuchten)``.
        Stromerzeuger im selben Netz werden zusammengefasst.
        """
        nodes = []  # (role, feature, metrischer Punkt)
        cables = []  # Liste von Endpunkt-Paaren
        frame = None
        for role in (*POINT_ROLES, ROLE_CABLES):
            layer = self.layers.layer(role)
            if layer is None:
                continue
            for f in layer.getFeatures():
                if not f.hasGeometry() or f.geometry().isEmpty():
                    continue
                pts = _connection_points_of(f.geometry(), role)
                if not pts:
                    continue
                if frame is None:
                    frame = MetricFrame(layer.crs(), pts[0])
                pts_m = [frame.geom_to_m_from(QgsGeometry.fromPointXY(p), layer.crs()).asPoint() for p in pts]
                if role == ROLE_CABLES:
                    cables.append(pts_m)
                else:
                    nodes.append((role, f, pts_m[0]))

        # Union-Find über Punkt-Objekte und Leitungsenden
        parent = list(range(len(nodes) + 2 * len(cables)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        points = [n[2] for n in nodes]
        for a, b in cables:
            points.extend([a, b])
        for c in range(len(cables)):
            union(len(nodes) + 2 * c, len(nodes) + 2 * c + 1)
        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                if math.hypot(points[i].x() - points[j].x(), points[i].y() - points[j].y()) <= _CONNECT_TOL_M:
                    union(i, j)

        nets: dict[int, dict] = {}
        for i, (role, f, _p) in enumerate(nodes):
            if role == ROLE_GENERATORS:
                net = nets.setdefault(find(i), {"name": [], "kva": 0.0, "last_w": 0.0, "leuchten": 0})
                net["name"].append(f.attribute("bezeichnung") or f.attribute("typ") or "?")
                net["kva"] += float(f.attribute("leistung_kva") or 0)
        unconnected = 0
        for i, (role, f, _p) in enumerate(nodes):
            if role != ROLE_LIGHTS:
                continue
            net = nets.get(find(i))
            if net is None:
                unconnected += 1
                continue
            net["leuchten"] += 1
            net["last_w"] += float(f.attribute("leistung_w") or 0)
        result = []
        for net in nets.values():
            net["name"] = " + ".join(net["name"])
            net["auslastung"] = net["last_w"] / 1000.0 / (net["kva"] * _POWER_FACTOR) if net["kva"] > 0 else 0.0
            result.append(net)
        return sorted(result, key=lambda n: n["name"]), unconnected

    # ------------------------------------------------------------------
    # Bilanz
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """Zählt Zelte, Fahrzeuge, Leitungen, Verteiler, Stromerzeuger und Leuchten je Typ."""
        result = {
            "zelte": {},
            "fahrzeuge": {},
            "leitungen": {},
            "verteiler": {},
            "stromerzeuger": {},
            "beleuchtung": {},
        }
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
        for key, role, value_field in (
            ("verteiler", ROLE_DISTRIBUTORS, None),
            ("stromerzeuger", ROLE_GENERATORS, "leistung_kva"),
            ("beleuchtung", ROLE_LIGHTS, "leistung_w"),
        ):
            layer = self.layers.layer(role)
            if layer is None:
                continue
            for f in layer.getFeatures():
                entry = result[key].setdefault(f.attribute("typ") or "?", {"anzahl": 0, "leistung": 0.0})
                entry["anzahl"] += 1
                if value_field:
                    entry["leistung"] += float(f.attribute(value_field) or 0)
        result["netze"], result["nicht_angeschlossen"] = self.power_networks()
        return result

    def _message(self, text: str, level=Qgis.MessageLevel.Info):
        self.iface.messageBar().pushMessage(_MSG_TITLE, text, level, 6)


def _connection_points_of(geom: QgsGeometry, role: str) -> list[QgsPointXY]:
    if role == ROLE_CABLES:
        line = geom.asMultiPolyline()[0] if geom.isMultipart() else geom.asPolyline()
        return [line[0], line[-1]] if line else []
    return [geom.asPoint()]


def _connection_points(layer, role: str) -> list[QgsPointXY]:
    result = []
    for feat in layer.getFeatures():
        geom = feat.geometry()
        if not geom.isEmpty():
            result.extend(_connection_points_of(geom, role))
    return result
