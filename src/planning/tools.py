"""Kartenwerkzeuge der Objektplanung."""

import math

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)
from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker
from qgis.PyQt.QtCore import QPoint, QPointF, Qt
from qgis.PyQt.QtGui import QColor, QCursor, QPolygonF
from qgis.PyQt.QtWidgets import QMenu, QToolTip

from ..logging_utils import get_logger
from ..tools.selection_frame import rotate_cursor
from .catalog import fmt_m
from .geometry import MetricFrame, circle_polygon, split_line
from .layers import (
    FOOTPRINT_ROLES,
    ROLE_CABLES,
    ROLE_DISTRIBUTORS,
    ROLE_GENERATORS,
    ROLE_LIGHTS,
    ROLE_TENTS,
    ROLE_VEHICLES,
)
from .selection_overlay import HANDLE_PX, SelectionOverlay

logger = get_logger(__name__)

_OK_FILL = QColor(76, 175, 80, 90)
_OK_LINE = QColor(27, 94, 32)
_BAD_FILL = QColor(229, 57, 53, 90)
_BAD_LINE = QColor(183, 28, 28)
_HINT_LINE = QColor(66, 66, 66, 160)
_GUIDE_LINE = QColor(233, 30, 99)
# Fangradius für Verteiler / Leitungsenden, Auswahl und Hilfslinien (Pixel)
_SNAP_PX = 12
_ALIGN_PX = 8
_ROTATE_STEP_DEG = 15
_FINE_ROTATE_STEP_DEG = 5
# Mausweg, ab dem ein Klick auf ein Objekt als Ziehen gilt (Pixel)
_DRAG_THRESHOLD_PX = 3
# Greifbereich der Eckpunkte und Drehzone außerhalb der Ecken (Pixel, wie bei den taktischen Zeichen)
_HANDLE_GRAB_PX = HANDLE_PX / 2.0 + 3.0
_ROTATE_ZONE_PX = 22.0
# Pfeiltasten verschieben die Auswahl um so viele Meter (mit Shift: groß)
_NUDGE_M = 0.5
_NUDGE_BIG_M = 5.0
# Rundungstoleranz bei Abstandsprüfungen (Meter)
_EPS_M = 0.01
# Trefferreihenfolge: Punkte und Linien zuerst, sonst wären sie auf Zelten nicht greifbar
_PICK_ORDER = (ROLE_GENERATORS, ROLE_DISTRIBUTORS, ROLE_LIGHTS, ROLE_CABLES, ROLE_VEHICLES, ROLE_TENTS)


def _polygon_type():
    return Qgis.GeometryType.Polygon


def _line_type():
    return Qgis.GeometryType.Line


def _band(canvas, geom_type, fill=None, line=None, width=2, dashed=False) -> QgsRubberBand:
    band = QgsRubberBand(canvas, geom_type)
    if fill is not None:
        band.setFillColor(fill)
    if line is not None:
        band.setStrokeColor(line)
    band.setWidth(width)
    if dashed:
        band.setLineStyle(Qt.PenStyle.DashLine)
    return band


def _collect(geoms: list[QgsGeometry]) -> QgsGeometry:
    geoms = [g for g in geoms if g is not None and not g.isEmpty()]
    if not geoms:
        return QgsGeometry()
    return geoms[0] if len(geoms) == 1 else QgsGeometry.collectGeometry(geoms)


class _PlanningTool(QgsMapTool):
    """Gemeinsame Basis: Rubberbands aufräumen, Tooltip, Esc führt zurück zur Auswahl."""

    def __init__(self, canvas, controller):
        super().__init__(canvas)
        self.canvas = canvas
        self.controller = controller
        self._bands: list[QgsRubberBand] = []
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def _new_band(self, *args, **kwargs) -> QgsRubberBand:
        band = _band(self.canvas, *args, **kwargs)
        self._bands.append(band)
        return band

    def _dispose_bands(self):
        for band in self._bands:
            try:
                self.canvas.scene().removeItem(band)
            except RuntimeError:
                pass
        self._bands = []

    def _tooltip(self, event, text: str):
        self._tooltip_at(event.pos(), text)

    def _tooltip_at(self, pos: QPoint, text: str):
        QToolTip.showText(self.canvas.mapToGlobal(pos), text, self.canvas)

    def _pixel_tolerance(self) -> float:
        return _SNAP_PX * self.canvas.mapUnitsPerPixel()

    def _pixels_in_m(self, frame: MetricFrame, map_point: QgsPointXY, pixels: float) -> float:
        """Wie viele Meter ``pixels`` Bildschirmpixel an ``map_point`` entsprechen."""
        canvas_frame = MetricFrame(self.canvas.mapSettings().destinationCrs(), map_point)
        offset = QgsPointXY(map_point.x() + pixels * self.canvas.mapUnitsPerPixel(), map_point.y())
        a, b = canvas_frame.point_to_m(map_point), canvas_frame.point_to_m(offset)
        return math.hypot(b.x() - a.x(), b.y() - a.y())

    def deactivate(self):
        self._dispose_bands()
        QToolTip.hideText()
        super().deactivate()
        self.controller.on_tool_deactivated(self)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
            event.accept()
            return
        event.ignore()

    def cancel(self):
        self.controller.back_to_select()


# ----------------------------------------------------------------------
# Zelt / Fahrzeug platzieren (einzeln oder als Raster)
# ----------------------------------------------------------------------


class FootprintTool(_PlanningTool):
    """Zelt oder Fahrzeug (``role``) maßstabsgetreu platzieren, ggf. als Raster.

    Vorschau folgt der Maus (grün = passt, rot = Mindestabstand unterschritten,
    gesetzt wird trotzdem). R / Shift+R dreht um ±15°, Strg+Mausrad um ±5°,
    Rechtsklick um 90°. Hilfslinien richten an Nachbarn aus, Strg gedrückt
    halten schaltet das Einrasten ab. Bei Fahrzeugen markiert eine dicke
    Kante die Front.
    """

    def __init__(self, canvas, controller, role: str):
        super().__init__(canvas, controller)
        self.role = role
        self._body = self._new_band(_polygon_type(), _OK_FILL, _OK_LINE, 2)
        self._guy = self._new_band(_polygon_type(), QColor(0, 0, 0, 0), _HINT_LINE, 1, dashed=True)
        self._front = self._new_band(_line_type(), None, _OK_LINE, 5)
        self._guides = self._new_band(_line_type(), None, _GUIDE_LINE, 1, dashed=True)
        self._last_map_point: QgsPointXY | None = None
        # Nur die Pixelposition merken, nie das Event selbst: Qt löscht das
        # C++-Objekt nach dem Handler, späterer Zugriff stürzt QGIS ab.
        self._last_pos: QPoint | None = None
        self._no_align = False
        self._snapped_center: QgsPointXY | None = None  # Layer-CRS

    def activate(self):
        super().activate()
        self._body.show()
        self._guy.show()

    def canvasMoveEvent(self, event):
        self._last_map_point = QgsPointXY(event.mapPoint())
        self._last_pos = QPoint(event.pos())
        self._no_align = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        self._update_preview()

    def _update_preview(self):
        layer = self.controller.layers.layer(self.role)
        if layer is None or self._last_map_point is None:
            return
        obj = self.controller.current_footprint(self.role)
        rotation = self.controller.rotation
        center = self.toLayerCoordinates(layer, self._last_map_point)
        frame = MetricFrame(layer.crs(), center)
        center_m = frame.point_to_m(center)
        bodies = self.controller.grid_bodies_m(self.role, center_m)

        guides = []
        if not self._no_align:
            tol = self._pixels_in_m(frame, self._last_map_point, _ALIGN_PX)
            snap = self.controller.align(_collect(bodies), frame, rotation, self.controller.gaps[self.role], tol)
            if snap and (snap.dx or snap.dy):
                center_m = QgsPointXY(center_m.x() + snap.dx, center_m.y() + snap.dy)
                bodies = self.controller.grid_bodies_m(self.role, center_m)
            if snap:
                guides = snap.guides
        self._snapped_center = frame.point_from_m(center_m)

        conflict = next(
            (c for c in (self.controller.footprint_conflict(self.role, b, frame) for b in bodies) if c), None
        )

        self._body.setFillColor(_BAD_FILL if conflict else _OK_FILL)
        self._body.setStrokeColor(_BAD_LINE if conflict else _OK_LINE)
        self._body.setToGeometry(frame.geom_from_m(_collect(bodies)), layer)
        if obj.abspannung > 0:
            self._guy.setToGeometry(frame.geom_from_m(_collect([b.buffer(obj.abspannung, 2) for b in bodies])), layer)
        else:
            self._guy.reset(_polygon_type())
        if self.role == ROLE_VEHICLES:
            fronts = []
            for b in bodies:
                ring = b.asPolygon()[0]
                fronts.append(QgsGeometry.fromPolylineXY([ring[1], ring[2]]))
            self._front.setStrokeColor(_BAD_LINE if conflict else _OK_LINE)
            self._front.setToGeometry(frame.geom_from_m(_collect(fronts)), layer)
        if guides:
            self._guides.setToGeometry(frame.geom_from_m(_collect(guides)), layer)
        else:
            self._guides.reset(_line_type())

        if self._last_pos is not None:
            text = f"{obj.label()} · {fmt_m(rotation)}°"
            if self.controller.grid_rows * self.controller.grid_cols > 1:
                text += f" · Raster {self.controller.grid_rows} × {self.controller.grid_cols}"
            if conflict:
                gap = fmt_m(self.controller.gaps[self.role])
                text += f"\n⚠ Mindestabstand {gap} m unterschritten ({conflict})"
            self._tooltip_at(self._last_pos, text)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.controller.set_rotation(self.controller.rotation + 90)
            self._update_preview()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        layer = self.controller.layers.layer(self.role)
        if layer is None:
            return
        if self._snapped_center is None:
            self._last_map_point = QgsPointXY(event.mapPoint())
            self._update_preview()
        self.controller.place_footprint(self.role, self._snapped_center)
        self._update_preview()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            step = _FINE_ROTATE_STEP_DEG if event.angleDelta().y() > 0 else -_FINE_ROTATE_STEP_DEG
            self.controller.set_rotation(self.controller.rotation + step)
            self._update_preview()
            event.accept()
            return
        event.ignore()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_R:
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.controller.set_rotation(self.controller.rotation + (-_ROTATE_STEP_DEG if shift else _ROTATE_STEP_DEG))
            self._update_preview()
            event.accept()
            return
        super().keyPressEvent(event)


# ----------------------------------------------------------------------
# Punkt-Objekte: Verteiler, Stromerzeuger, Beleuchtung
# ----------------------------------------------------------------------


class PointTool(_PlanningTool):
    """Verteiler, Stromerzeuger oder Leuchte per Klick setzen.

    Bei Leuchten zeigt ein Kreis den ausgeleuchteten Bereich.
    """

    def __init__(self, canvas, controller, role: str):
        super().__init__(canvas, controller)
        self.role = role
        self._reach = self._new_band(_polygon_type(), QColor(255, 235, 59, 50), QColor(249, 168, 37), 1, dashed=True)

    def canvasMoveEvent(self, event):
        obj = self.controller.current_point_type(self.role)
        layer = self.controller.layers.layer(self.role)
        if obj is None or layer is None:
            return
        text = f"{obj.name} setzen"
        if self.role == ROLE_LIGHTS:
            point = self.toLayerCoordinates(layer, event.mapPoint())
            frame = MetricFrame(layer.crs(), point)
            self._reach.setToGeometry(frame.geom_from_m(circle_polygon(frame.point_to_m(point), obj.radius)), layer)
            text += f" · {fmt_m(obj.leistung_w)} W · Radius {fmt_m(obj.radius)} m"
        elif self.role == ROLE_GENERATORS:
            text += f" · {fmt_m(obj.leistung_kva)} kVA"
        self._tooltip(event, text)

    def canvasReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        layer = self.controller.layers.layer(self.role)
        if layer is not None:
            self.controller.place_point(self.role, self.toLayerCoordinates(layer, event.mapPoint()))


# ----------------------------------------------------------------------
# Leitungsroller verlegen
# ----------------------------------------------------------------------


class CableTool(_PlanningTool):
    """Leitung verlegen, beliebig lang: die Strecke wird in Leitungsroller aufgeteilt.

    Klick setzt Stützpunkte (fängt Stromerzeuger, Verteiler, Leuchten und
    Leitungsenden). Ist eine Trommel leer, geht es automatisch mit der
    nächsten weiter – Kupplungspunkte (◆) zeigen, wo eine Trommel endet.
    Rechtsklick / Enter / Doppelklick schließt ab, Rücktaste entfernt den
    letzten Punkt. Der gestrichelte Kreis zeigt, wie weit die letzte
    Trommel noch reicht.
    """

    def __init__(self, canvas, controller):
        super().__init__(canvas, controller)
        self._line = self._new_band(_line_type(), None, QColor(239, 108, 0), 3)
        self._reach = self._new_band(_polygon_type(), QColor(0, 0, 0, 0), _HINT_LINE, 1, dashed=True)
        self._couplings = self._new_band(Qgis.GeometryType.Point, QColor(255, 255, 255), QColor(33, 33, 33), 2)
        self._couplings.setIcon(QgsRubberBand.IconType.ICON_FULL_DIAMOND)
        self._couplings.setIconSize(11)
        self._snap_marker = QgsVertexMarker(canvas)
        self._snap_marker.setIconType(QgsVertexMarker.IconType.ICON_CIRCLE)
        self._snap_marker.setColor(QColor(13, 153, 255))
        self._snap_marker.setIconSize(14)
        self._snap_marker.setPenWidth(2)
        self._snap_marker.hide()
        self._points: list[QgsPointXY] = []  # Layer-CRS
        self._frame: MetricFrame | None = None
        self._skip_release = False

    def activate(self):
        super().activate()
        reel = self.controller.current_reel()
        if reel:
            self._line.setStrokeColor(QColor(reel.farbe))

    def deactivate(self):
        try:
            self.canvas.scene().removeItem(self._snap_marker)
        except RuntimeError:
            pass
        self._points = []
        super().deactivate()

    def _layer(self):
        return self.controller.layers.cables

    def _snapped(self, map_point) -> tuple[QgsPointXY, bool]:
        """Layer-Koordinate des Cursors, ggf. auf Stromerzeuger/Verteiler/Leitungsende gefangen."""
        layer = self._layer()
        point = self.toLayerCoordinates(layer, map_point)
        target = self.controller.snap_target(map_point, self._pixel_tolerance(), layer.crs())
        if target is not None:
            self._snap_marker.setCenter(self.toMapCoordinates(layer, target))
            self._snap_marker.show()
            return target, True
        self._snap_marker.hide()
        return point, False

    def _length_m(self, points: list[QgsPointXY]) -> float:
        return _chunk_length([self._frame.point_to_m(p) for p in points])

    def canvasMoveEvent(self, event):
        layer = self._layer()
        reel = self.controller.current_reel()
        if layer is None or reel is None:
            return
        cursor, _ = self._snapped(event.mapPoint())
        if not self._points:
            self._tooltip(event, f"{reel.name}: Startpunkt setzen (Stromerzeuger und Verteiler werden gefangen)")
            return

        route = self._points + [cursor]
        route_m = [self._frame.point_to_m(p) for p in route]
        total = _chunk_length(route_m)
        count = reel_count(total, reel.laenge)
        rest = count * reel.laenge - total

        self._line.setToGeometry(QgsGeometry.fromPolylineXY(route), layer)
        self._couplings.reset(Qgis.GeometryType.Point)
        for chunk in split_line(route_m, reel.laenge)[1:]:
            self._couplings.addPoint(self.toMapCoordinates(layer, self._frame.point_from_m(chunk[0])))
        self._reach.setToGeometry(self._frame.geom_from_m(circle_polygon(route_m[-1], rest)), layer)

        text = f"{fmt_m(round(total, 1))} m · {count} × {reel.name}"
        text += f"\nRest auf der letzten Trommel: {fmt_m(round(rest, 1))} m"
        self._tooltip(event, text)

    def canvasReleaseEvent(self, event):
        layer = self._layer()
        reel = self.controller.current_reel()
        if layer is None or reel is None:
            return
        if self._skip_release:
            # Release nach einem Doppelklick, der die Leitung bereits abgeschlossen hat
            self._skip_release = False
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._finish()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        point, _ = self._snapped(event.mapPoint())
        if not self._points:
            self._frame = MetricFrame(layer.crs(), point)
            self._points = [point]
            return
        if self._length_m([self._points[-1], point]) <= _EPS_M:
            return
        self._points.append(point)

    def canvasDoubleClickEvent(self, event):
        self._finish()
        self._skip_release = True

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Backspace and self._points:
            self._points.pop()
            if not self._points:
                self._reset()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self._points:
            self._reset()
            event.accept()
            return
        super().keyPressEvent(event)

    def _finish(self):
        reel = self.controller.current_reel()
        if len(self._points) >= 2 and reel is not None:
            route_m = [self._frame.point_to_m(p) for p in self._points]
            pieces = [
                ([self._frame.point_from_m(p) for p in chunk], _chunk_length(chunk))
                for chunk in split_line(route_m, reel.laenge)
            ]
            self.controller.place_cables(pieces)
        self._reset()

    def _reset(self):
        self._points = []
        self._frame = None
        for band, typ in (
            (self._line, _line_type()),
            (self._reach, _polygon_type()),
            (self._couplings, Qgis.GeometryType.Point),
        ):
            band.reset(typ)
        self._snap_marker.hide()


def reel_count(total_m: float, reel_m: float) -> int:
    """So viele Trommeln braucht eine Strecke von ``total_m`` Metern (mindestens eine)."""
    return max(1, math.ceil((total_m - _EPS_M) / reel_m))


def _chunk_length(points_m: list[QgsPointXY]) -> float:
    return sum(math.hypot(b.x() - a.x(), b.y() - a.y()) for a, b in zip(points_m, points_m[1:]))


# ----------------------------------------------------------------------
# Fläche zeichnen (Kapazität prüfen)
# ----------------------------------------------------------------------


class AreaTool(_PlanningTool):
    """Fläche als Polygon zeichnen. Rechtsklick / Enter / Doppelklick schließt ab."""

    def __init__(self, canvas, controller):
        super().__init__(canvas, controller)
        self._band = self._new_band(_polygon_type(), QColor(13, 153, 255, 50), QColor(13, 153, 255), 2)
        self._points: list[QgsPointXY] = []  # Canvas-CRS
        self._skip_release = False

    def deactivate(self):
        self._points = []
        super().deactivate()

    def _geometry(self, extra: QgsPointXY | None = None) -> QgsGeometry:
        pts = self._points + ([extra] if extra is not None else [])
        if len(pts) < 3:
            return QgsGeometry.fromPolylineXY(pts)
        return QgsGeometry.fromPolygonXY([pts + [pts[0]]])

    def canvasMoveEvent(self, event):
        if not self._points:
            self._tooltip(event, "Fläche zeichnen: Eckpunkte anklicken, Rechtsklick schließt ab")
            return
        geom = self._geometry(event.mapPoint())
        self._band.setToGeometry(geom, None)
        if geom.type() == _polygon_type():
            area = self.controller.area_m2(geom, self.canvas.mapSettings().destinationCrs())
            self._tooltip(event, f"{area:,.0f} m²".replace(",", "."))

    def canvasReleaseEvent(self, event):
        if self._skip_release:
            self._skip_release = False
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._finish()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._points.append(QgsPointXY(event.mapPoint()))

    def canvasDoubleClickEvent(self, event):
        self._finish()
        self._skip_release = True

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Backspace and self._points:
            self._points.pop()
            self._band.setToGeometry(self._geometry(), None)
            event.accept()
            return
        super().keyPressEvent(event)

    def _finish(self):
        # Doppelklick erzeugt zuvor schon einen Release → doppelte Punkte entfernen
        pts = []
        for p in self._points:
            if not pts or p.distance(pts[-1]) > self.canvas.mapUnitsPerPixel():
                pts.append(p)
        self._points = []
        self._band.reset(_polygon_type())
        if len(pts) < 3:
            return
        geom = QgsGeometry.fromPolygonXY([pts + [pts[0]]]).makeValid()
        self.controller.evaluate_area(geom, self.canvas.mapSettings().destinationCrs())


# ----------------------------------------------------------------------
# Auswählen und bearbeiten wie in Figma
# ----------------------------------------------------------------------


class SelectTool(_PlanningTool):
    """Objekte auswählen und bearbeiten, wie die taktischen Zeichen (Figma-Stil).

    Klick wählt aus, Shift+Klick ergänzt/entfernt, Ziehen auf freier Fläche
    zieht einen Auswahlrahmen auf. Die Auswahl bekommt einen blauen Rahmen
    mit Eckpunkten: Ziehen im Rahmen verschiebt (Hilfslinien richten aus,
    Strg schaltet das ab, Alt+Ziehen zieht eine Kopie heraus), Ziehen an
    oder knapp außerhalb einer Ecke dreht (Shift rastet in 15°-Schritten).
    Pfeiltasten schieben um 0,5 m (Shift: 5 m), Entf löscht, R dreht,
    D dupliziert, Enter / Doppelklick bearbeitet die Bezeichnung,
    Rechtsklick öffnet ein Kontextmenü, Esc hebt die Auswahl auf.
    """

    def __init__(self, canvas, controller):
        super().__init__(canvas, controller)
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.overlay: SelectionOverlay | None = None
        self._gesture: str | None = None  # move | rotate | marquee
        self._started = False
        self._alt = False
        self._press_pos: QPoint | None = None
        self._press_map: QgsPointXY | None = None
        self._frame: MetricFrame | None = None  # metrisches System der laufenden Geste
        self._items: list[tuple[str, int, QgsGeometry]] = []  # Auswahl, metrisch
        self._frame_m: list[QgsPointXY] = []  # Rahmenecken, metrisch
        self._pivot_m: QgsPointXY | None = None
        self._pivot_px: QPointF | None = None
        self._base_rotation: float | None = None
        self._result: dict[str, dict[int, QgsGeometry]] = {}
        self._rotation_delta = 0.0

    # -- Lebenszyklus --------------------------------------------------------

    def activate(self):
        super().activate()
        self.overlay = SelectionOverlay(self.canvas)
        self.controller.layers.selection_changed.connect(self._refresh_overlay)
        self.controller.layers.changed.connect(self._refresh_overlay)
        self._refresh_overlay()

    def deactivate(self):
        for signal in (self.controller.layers.selection_changed, self.controller.layers.changed):
            try:
                signal.disconnect(self._refresh_overlay)
            except (TypeError, RuntimeError):
                pass
        if self.overlay is not None:
            self.overlay.dispose()
            self.overlay = None
        super().deactivate()

    def _refresh_overlay(self):
        if self.overlay is None or (self._started and self._gesture in ("move", "rotate")):
            return
        geoms, frame, badge = self.controller.selection_overlay()
        self.overlay.selected = geoms
        self.overlay.frame = frame
        self.overlay.badge = badge
        self.overlay.ghosts = []
        self.overlay.guides = []
        self.overlay.refresh()

    # -- Treffer -------------------------------------------------------------

    def _find(self, map_point: QgsPointXY):
        tol = self._pixel_tolerance()
        rect = QgsRectangle(map_point.x() - tol, map_point.y() - tol, map_point.x() + tol, map_point.y() + tol)
        for role in _PICK_ORDER:
            layer = self.controller.layers.layer(role)
            if layer is None:
                continue
            layer_rect = self.toLayerCoordinates(layer, rect)
            probe = QgsGeometry.fromPointXY(self.toLayerCoordinates(layer, map_point))
            best, best_d = None, None
            for feat in layer.getFeatures(QgsFeatureRequest().setFilterRect(layer_rect)):
                geom = feat.geometry()
                if role in FOOTPRINT_ROLES:
                    if not geom.contains(probe):
                        continue
                    d = 0.0
                else:
                    d = geom.distance(probe)
                if best_d is None or d < best_d:
                    best, best_d = feat, d
            if best is not None:
                return role, best
        return None

    def _in_rect(self, rect: QgsRectangle) -> list[tuple[str, int]]:
        hits = []
        for role in _PICK_ORDER:
            layer = self.controller.layers.layer(role)
            if layer is None:
                continue
            layer_rect = self.toLayerCoordinates(layer, rect)
            layer_box = QgsGeometry.fromRect(layer_rect)
            for feat in layer.getFeatures(QgsFeatureRequest().setFilterRect(layer_rect)):
                if feat.geometry().intersects(layer_box):
                    hits.append((role, feat.id()))
        return hits

    def _frame_hit(self, pos: QPointF) -> str | None:
        """``rotate`` an/knapp außerhalb einer Ecke, ``move`` im Rahmen, sonst None."""
        corners = self.overlay.frame_px() if self.overlay else None
        if not corners:
            return None
        handles = self.overlay.frame_has_handles()
        if handles:
            for c in corners:
                if max(abs(pos.x() - c.x()), abs(pos.y() - c.y())) <= _HANDLE_GRAB_PX:
                    return "rotate"
        if QPolygonF(corners).containsPoint(pos, Qt.FillRule.OddEvenFill):
            return "move"
        if handles:
            for c in corners:
                if math.dist((pos.x(), pos.y()), (c.x(), c.y())) <= _ROTATE_ZONE_PX:
                    return "rotate"
        return None

    def _to_canvas(self, role: str, geom: QgsGeometry) -> QgsGeometry:
        layer = self.controller.layers.layer(role)
        g = QgsGeometry(geom)
        g.transform(
            QgsCoordinateTransform(layer.crs(), self.canvas.mapSettings().destinationCrs(), QgsProject.instance())
        )
        return g

    # -- Gesten --------------------------------------------------------------

    def _prepare(self):
        """Auswahl und Rahmen für Verschieben/Drehen metrisch vormerken."""
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        self._frame = MetricFrame(canvas_crs, self._press_map)
        self._items = []
        self._base_rotation = None
        selected = self.controller.selection()
        for role, feat in selected:
            if feat.hasGeometry():
                layer = self.controller.layers.layer(role)
                self._items.append((role, feat.id(), self._frame.geom_to_m_from(feat.geometry(), layer.crs())))
        if len(selected) == 1 and selected[0][0] in FOOTPRINT_ROLES:
            self._base_rotation = float(selected[0][1].attribute("rotation") or 0)
        _geoms, corners, _badge = self.controller.selection_overlay()
        self._frame_m = [self._frame.point_to_m(c) for c in corners] if corners else []
        if self._frame_m:
            cx = sum(p.x() for p in self._frame_m) / len(self._frame_m)
            cy = sum(p.y() for p in self._frame_m) / len(self._frame_m)
            self._pivot_m = QgsPointXY(cx, cy)
            self._pivot_px = self.overlay.to_px(self._frame.point_from_m(self._pivot_m))
        self._result = {}
        self._rotation_delta = 0.0

    def canvasPressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.overlay is None:
            return
        self._press_pos = QPoint(event.pos())
        self._press_map = QgsPointXY(event.mapPoint())
        self._started = False
        mods = event.modifiers()
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        self._alt = bool(mods & Qt.KeyboardModifier.AltModifier)

        frame_hit = None if shift else self._frame_hit(QPointF(event.pos()))
        if frame_hit == "rotate":
            self._gesture = "rotate"
            self._prepare()
            return
        hit = self._find(self._press_map)
        if hit is None:
            self._gesture = "move" if frame_hit == "move" else "marquee"
            if self._gesture == "move":
                self._prepare()
            return
        role, feat = hit
        if shift:
            self.controller.select([(role, feat.id())], "toggle")
            self._gesture = None
            return
        if feat.id() not in self.controller.selection_ids().get(role, set()):
            self.controller.select([(role, feat.id())])
        self._gesture = "move"
        self._prepare()

    def _past_threshold(self, pos: QPoint) -> bool:
        if self._press_pos is None:
            return False
        return max(abs(pos.x() - self._press_pos.x()), abs(pos.y() - self._press_pos.y())) >= _DRAG_THRESHOLD_PX

    def canvasMoveEvent(self, event):
        if self.overlay is None:
            return
        pos = QPoint(event.pos())
        map_point = QgsPointXY(event.mapPoint())
        mods = event.modifiers()
        if self._gesture and (self._started or self._past_threshold(pos)):
            if not self._started:
                self._started = True
                self.overlay.hover = None
                if self._gesture == "move" and self._alt:
                    # Alt+Ziehen: Kopie liegt deckungsgleich, gezogen wird die Kopie
                    self.controller.duplicate_selection(offset=False)
                    self._prepare()
            if self._gesture == "move":
                self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
                self._update_move(map_point, bool(mods & Qt.KeyboardModifier.ControlModifier))
            elif self._gesture == "rotate":
                self._update_rotate(QPointF(pos), bool(mods & Qt.KeyboardModifier.ShiftModifier))
            else:
                self.overlay.marquee = (self._press_map, map_point)
                self.overlay.refresh()
            return
        if event.buttons() != Qt.MouseButton.NoButton:
            return

        # Hover: Rahmen-Ecke → Drehen-Cursor, Objekt → dünner blauer Umriss
        frame_hit = self._frame_hit(QPointF(pos))
        hit = self._find(map_point)
        hover = None
        if hit is not None:
            role, feat = hit
            if feat.id() not in self.controller.selection_ids().get(role, set()):
                hover = self._to_canvas(role, feat.geometry())
        if frame_hit == "rotate":
            self.setCursor(rotate_cursor())
        elif hit is not None or frame_hit == "move":
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor if frame_hit == "move" else Qt.CursorShape.ArrowCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        if (hover is None) != (self.overlay.hover is None) or hover is not None:
            self.overlay.hover = hover
            self.overlay.refresh()

    def _show_ghosts(self, geoms_m: list[tuple[str, int, QgsGeometry]], frame_m: list[QgsPointXY], badge: str):
        frame = self._frame
        self._result = {}
        ghosts = []
        for role, fid, g in geoms_m:
            layer = self.controller.layers.layer(role)
            self._result.setdefault(role, {})[fid] = frame.geom_from_m(g, layer.crs())
            ghosts.append(frame.geom_from_m(g))
        self.overlay.ghosts = ghosts
        self.overlay.frame = [frame.point_from_m(p) for p in frame_m] or None
        self.overlay.badge = badge
        self.overlay.refresh()

    def _update_move(self, map_point: QgsPointXY, no_align: bool):
        frame = self._frame
        a, b = frame.point_to_m(self._press_map), frame.point_to_m(map_point)
        dx, dy = b.x() - a.x(), b.y() - a.y()
        exclude = self.controller.selection_ids()

        footprints = [(r, g) for r, _fid, g in self._items if r in FOOTPRINT_ROLES]
        guides = []
        if footprints and not no_align:
            moved = []
            for _r, g in footprints:
                g = QgsGeometry(g)
                g.translate(dx, dy)
                moved.append(g)
            rotation = self._base_rotation or 0.0
            gap = max(self.controller.gaps.get(r, 0.0) for r, _g in footprints)
            tol = self._pixels_in_m(frame, map_point, _ALIGN_PX)
            snap = self.controller.align(_collect(moved), frame, rotation, gap, tol, exclude=exclude)
            if snap:
                dx, dy = dx + snap.dx, dy + snap.dy
                guides = snap.guides

        moved_items = []
        for role, fid, g in self._items:
            g = QgsGeometry(g)
            g.translate(dx, dy)
            moved_items.append((role, fid, g))
        frame_m = [QgsPointXY(p.x() + dx, p.y() + dy) for p in self._frame_m]

        badge = f"{fmt_m(round(math.hypot(dx, dy), 1))} m verschoben"
        if footprints:
            moved_fp = _collect([g for r, _f, g in moved_items if r in FOOTPRINT_ROLES])
            near = self.controller.nearest_gap(moved_fp, frame, exclude)
            if near is not None:
                gap = max(self.controller.gaps.get(r, 0.0) for r, _g in footprints)
                warn = "⚠ " if near[0] < gap - _EPS_M else ""
                badge = f"{warn}Abstand {fmt_m(round(near[0], 1))} m zu {near[1]}"
        self.overlay.guides = [frame.geom_from_m(g) for g in guides]
        self._show_ghosts(moved_items, frame_m, badge)

    def _update_rotate(self, pos: QPointF, snap: bool):
        pivot = self._pivot_px
        if pivot is None:
            return

        def angle(p: QPointF) -> float:
            return math.degrees(math.atan2(p.y() - pivot.y(), p.x() - pivot.x()))

        press = QPointF(self._press_pos)
        # Bildschirm-y zeigt nach unten → positive Winkel drehen im Uhrzeigersinn (wie QgsGeometry.rotate)
        delta = (angle(pos) - angle(press) + 180.0) % 360.0 - 180.0
        if snap:
            base = self._base_rotation or 0.0
            delta = round((base + delta) / _ROTATE_STEP_DEG) * _ROTATE_STEP_DEG - base
        self._rotation_delta = delta

        rotated = []
        for role, fid, g in self._items:
            g = QgsGeometry(g)
            g.rotate(delta, self._pivot_m)
            rotated.append((role, fid, g))
        frame_m = []
        for p in self._frame_m:
            pg = QgsGeometry.fromPointXY(p)
            pg.rotate(delta, self._pivot_m)
            frame_m.append(pg.asPoint())
        if self._base_rotation is not None:
            badge = f"{fmt_m(round((self._base_rotation + delta) % 360.0))}°"
        else:
            badge = f"{'+' if delta >= 0 else ''}{fmt_m(round(delta))}°"
        self._show_ghosts(rotated, frame_m, badge)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._context_menu(QPoint(event.pos()), QgsPointXY(event.mapPoint()))
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        gesture, started = self._gesture, self._started
        if started and gesture == "move" and self._result:
            self.controller.transform_selection(self._result)
        elif started and gesture == "rotate" and self._result and abs(self._rotation_delta) > 1e-6:
            self.controller.transform_selection(self._result, self._rotation_delta)
        elif gesture == "marquee":
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if started:
                hits = self._in_rect(QgsRectangle(self._press_map, QgsPointXY(event.mapPoint())))
                self.controller.select(hits, "add" if shift else "replace")
            elif not shift:
                self.controller.clear_selection()
        self._end_gesture()

    def canvasDoubleClickEvent(self, event):
        if self._find(QgsPointXY(event.mapPoint())) is not None:
            self.controller.focus_label()

    def _end_gesture(self):
        self._gesture = None
        self._started = False
        self._press_pos = None
        self._items = []
        self._result = {}
        self._rotation_delta = 0.0
        if self.overlay is not None:
            self.overlay.marquee = None
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self._refresh_overlay()

    def _context_menu(self, pos: QPoint, map_point: QgsPointXY):
        hit = self._find(map_point)
        if hit is not None and hit[1].id() not in self.controller.selection_ids().get(hit[0], set()):
            self.controller.select([(hit[0], hit[1].id())])
        if not self.controller.selection_ids():
            return
        menu = QMenu(self.canvas)
        menu.addAction("Bezeichnung bearbeiten …\tEnter", self.controller.focus_label)
        menu.addAction("Duplizieren\tD", self.controller.duplicate_selection)
        menu.addAction("Drehen 90°", lambda: self.controller.rotate_selection(90))
        menu.addAction("Drehen 15°\tR", lambda: self.controller.rotate_selection(15))
        menu.addSeparator()
        menu.addAction("Löschen\tEntf", self.controller.delete_selection)
        menu.exec(self.canvas.mapToGlobal(pos))

    # -- Tastatur -----------------------------------------------------------

    def keyPressEvent(self, event):
        key = event.key()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        has_selection = bool(self.controller.selection_ids())
        nudge = {
            Qt.Key.Key_Left: (-1, 0),
            Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, 1),
            Qt.Key.Key_Down: (0, -1),
        }
        if key in nudge and has_selection and not ctrl:
            step = _NUDGE_BIG_M if shift else _NUDGE_M
            dx, dy = nudge[key]
            self.controller.nudge_selection(dx * step, dy * step)
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and has_selection:
            self.controller.delete_selection()
        elif key == Qt.Key.Key_R and not ctrl and has_selection:
            self.controller.rotate_selection(-_ROTATE_STEP_DEG if shift else _ROTATE_STEP_DEG)
        elif key == Qt.Key.Key_D and not ctrl and has_selection:
            self.controller.duplicate_selection()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and has_selection:
            self.controller.focus_label()
        elif key == Qt.Key.Key_Escape:
            if self._gesture:
                self._end_gesture()
            elif has_selection:
                self.controller.clear_selection()
        else:
            # Pfeiltasten ohne Auswahl → QGIS verschiebt die Karte
            event.ignore()
            return
        event.accept()
