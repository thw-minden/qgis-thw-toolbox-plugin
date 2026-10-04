"""Kartenwerkzeuge der Objektplanung."""

import math

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsFeature,
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
    ROLE_ARROWS,
    ROLE_CABLES,
    ROLE_DISTRIBUTORS,
    ROLE_GENERATORS,
    ROLE_LIGHTS,
    ROLE_MARKERS,
    ROLE_TENTS,
    ROLE_VEHICLES,
    ROLE_ZONES,
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
# Trefferreihenfolge: Punkte und Linien zuerst, sonst wären sie auf Zelten nicht greifbar.
# Gebiete zuletzt und nur am Umriss, damit man in ihnen weiter auswählen und Rahmen aufziehen kann.
_PICK_ORDER = (
    ROLE_GENERATORS,
    ROLE_DISTRIBUTORS,
    ROLE_LIGHTS,
    ROLE_ARROWS,
    ROLE_CABLES,
    ROLE_VEHICLES,
    ROLE_TENTS,
    ROLE_ZONES,
)


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


def _boundary(geom: QgsGeometry) -> QgsGeometry:
    """Umriss einer Fläche als Linien."""
    polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
    return QgsGeometry.fromMultiPolylineXY([ring for poly in polys for ring in poly])


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
# Frei zeichnen: Gebiete, Pfeile, Fläche für die Kapazitätsprüfung
# ----------------------------------------------------------------------


class _DrawTool(_PlanningTool):
    """Fläche (oder Linienzug) zeichnen: Klick setzt Punkte.

    Rechtsklick / Enter / Doppelklick schließt ab, Rücktaste entfernt den
    letzten Punkt, Esc verwirft die angefangene Form.
    """

    polygon = True
    hint = ""

    def __init__(self, canvas, controller, color: QColor):
        super().__init__(canvas, controller)
        fill = QColor(color)
        fill.setAlpha(50)
        self._band = self._new_band(_polygon_type(), fill, color, 2 if self.polygon else 3)
        self._points: list[QgsPointXY] = []  # Canvas-CRS
        self._skip_release = False

    def deactivate(self):
        self._points = []
        super().deactivate()

    def _commit(self, geom: QgsGeometry):
        raise NotImplementedError

    def _geometry(self, extra: QgsPointXY | None = None) -> QgsGeometry:
        pts = self._points + ([extra] if extra is not None else [])
        if not self.polygon or len(pts) < 3:
            return QgsGeometry.fromPolylineXY(pts)
        return QgsGeometry.fromPolygonXY([pts + [pts[0]]])

    def canvasMoveEvent(self, event):
        if not self._points:
            self._tooltip(event, self.hint)
            return
        geom = self._geometry(event.mapPoint())
        self._band.setToGeometry(geom, None)
        crs = self.canvas.mapSettings().destinationCrs()
        if geom.type() == _polygon_type():
            area = self.controller.area_m2(geom, crs)
            self._tooltip(event, f"{area:,.0f} m²".replace(",", "."))
        elif not self.polygon:
            length = MetricFrame(crs, self._points[0]).geom_to_m(geom).length()
            self._tooltip(event, f"{fmt_m(round(length, 1))} m")

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
        if event.key() == Qt.Key.Key_Escape and self._points:
            self._points = []
            self._band.reset(_polygon_type())
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
        if len(pts) < (3 if self.polygon else 2):
            return
        if self.polygon:
            geom = QgsGeometry.fromPolygonXY([pts + [pts[0]]]).makeValid()
        else:
            geom = QgsGeometry.fromPolylineXY(pts)
        self._commit(geom)


class AreaTool(_DrawTool):
    """Fläche zeichnen und prüfen, wie viele Zelte bzw. Fahrzeuge hineinpassen."""

    hint = "Fläche zeichnen: Eckpunkte anklicken, Rechtsklick schließt ab"

    def __init__(self, canvas, controller):
        super().__init__(canvas, controller, QColor(13, 153, 255))

    def _commit(self, geom: QgsGeometry):
        self.controller.evaluate_area(geom, self.canvas.mapSettings().destinationCrs())


class ZoneTool(_DrawTool):
    """Gebiet (z.B. Einsatzabschnitt, Gefahrenbereich) als Fläche zeichnen; der Typ bestimmt die Farbe."""

    def __init__(self, canvas, controller):
        obj = controller.current_point_type(ROLE_ZONES)
        super().__init__(canvas, controller, QColor(obj.farbe))
        self.hint = f"{obj.name}: Eckpunkte anklicken · Rechtsklick / Doppelklick schließt ab"

    def _commit(self, geom: QgsGeometry):
        self.controller.place_shape(ROLE_ZONES, geom, self.canvas.mapSettings().destinationCrs())


class ArrowTool(_DrawTool):
    """Pfeil zeichnen: Start anklicken, beliebig viele Knickpunkte, die Spitze sitzt am letzten Punkt."""

    polygon = False

    def __init__(self, canvas, controller):
        obj = controller.current_point_type(ROLE_ARROWS)
        super().__init__(canvas, controller, QColor(obj.farbe))
        self.hint = f"{obj.name}: Start anklicken, dann Verlauf · Rechtsklick / Doppelklick setzt die Spitze"

    def _commit(self, geom: QgsGeometry):
        self.controller.place_shape(ROLE_ARROWS, geom, self.canvas.mapSettings().destinationCrs())


# ----------------------------------------------------------------------
# Punkte eines Gebiets / Pfeils bearbeiten
# ----------------------------------------------------------------------


class VertexTool(_PlanningTool):
    """In ein Gebiet oder einen Pfeil „hineingehen“ und die Form an ihren Punkten bearbeiten.

    Eckpunkte (□) ziehen verschiebt sie, die kleinen Punkte (○) auf den
    Kantenmitten ziehen fügt dort einen neuen Eckpunkt ein – so wird ein
    Gebiet ergänzt. Rechtsklick auf einen Eckpunkt oder Entf löscht ihn.
    Jede Änderung wird sofort gespeichert. Klick daneben, Enter oder Esc
    führt zurück zur Auswahl.
    """

    def __init__(self, canvas, controller, role: str, fid: int):
        super().__init__(canvas, controller)
        self.role = role
        self.fid = fid
        self.polygon = role == ROLE_ZONES
        self._min_points = 3 if self.polygon else 2
        blue = QColor(13, 153, 255)
        white = QColor(255, 255, 255)
        self._shape = self._new_band(_polygon_type() if self.polygon else _line_type(), QColor(13, 153, 255, 35), blue)
        self._mids = self._new_band(Qgis.GeometryType.Point, white, blue, 2)
        self._mids.setIcon(QgsRubberBand.IconType.ICON_CIRCLE)
        self._mids.setIconSize(8)
        self._corners = self._new_band(Qgis.GeometryType.Point, white, blue, 2)
        self._corners.setIcon(QgsRubberBand.IconType.ICON_FULL_BOX)
        self._corners.setIconSize(10)
        self._current = self._new_band(Qgis.GeometryType.Point, blue, blue, 2)
        self._current.setIcon(QgsRubberBand.IconType.ICON_FULL_BOX)
        self._current.setIconSize(12)
        self._points: list[QgsPointXY] = []  # Canvas-CRS, Fläche ohne den schließenden Punkt
        self._active: int | None = None  # zuletzt angefasster Eckpunkt (Entf löscht ihn)
        self._drag: int | None = None
        self._moved = False
        self._exit_on_release = False

    # -- Lebenszyklus --------------------------------------------------------

    def activate(self):
        super().activate()
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.controller.layers.changed.connect(self._reload)
        self._reload()

    def deactivate(self):
        try:
            self.controller.layers.changed.disconnect(self._reload)
        except (TypeError, RuntimeError):
            pass
        super().deactivate()

    def _layer(self):
        return self.controller.layers.layer(self.role)

    def _reload(self):
        """Punkte aus dem Layer lesen (auch nach Rückgängig); ist die Form weg, zurück zur Auswahl."""
        if self._drag is not None:
            return
        layer = self._layer()
        feat = layer.getFeature(self.fid) if layer is not None else None
        if feat is None or not feat.isValid() or not feat.hasGeometry():
            self._points = []
            self._redraw()
            self.controller.back_to_select()
            return
        geom = QgsGeometry(feat.geometry())
        geom.transform(
            QgsCoordinateTransform(layer.crs(), self.canvas.mapSettings().destinationCrs(), QgsProject.instance())
        )
        if self.polygon:
            polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
            ring = polys[0][0] if polys and polys[0] else []
            self._points = [QgsPointXY(p) for p in ring[:-1]]
        else:
            parts = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
            self._points = [QgsPointXY(p) for p in (parts[0] if parts else [])]
        if self._active is not None and self._active >= len(self._points):
            self._active = None
        self._redraw()

    # -- Darstellung ---------------------------------------------------------

    def _geometry(self) -> QgsGeometry:
        if self.polygon and len(self._points) >= 3:
            return QgsGeometry.fromPolygonXY([self._points + [self._points[0]]])
        return QgsGeometry.fromPolylineXY(self._points)

    def _edges(self) -> list[tuple[int, QgsPointXY]]:
        """Kantenmitten mit dem Index, an dem ein dort eingefügter Punkt landet."""
        pts = self._points
        pairs = list(zip(pts, pts[1:]))
        if self.polygon and len(pts) >= 3:
            pairs.append((pts[-1], pts[0]))
        return [(i + 1, QgsPointXY((a.x() + b.x()) / 2.0, (a.y() + b.y()) / 2.0)) for i, (a, b) in enumerate(pairs)]

    def _redraw(self):
        self._shape.setToGeometry(self._geometry(), None)
        for band in (self._mids, self._corners, self._current):
            band.reset(Qgis.GeometryType.Point)
        # Beim Ziehen stören die Kantenmitten nur
        if self._drag is None:
            for _index, mid in self._edges():
                self._mids.addPoint(mid)
        for i, p in enumerate(self._points):
            (self._current if i == self._active else self._corners).addPoint(p)

    # -- Treffer -------------------------------------------------------------

    def _px(self, point: QgsPointXY) -> QPointF:
        p = self.canvas.getCoordinateTransform().transform(point)
        return QPointF(p.x(), p.y())

    def _nearest(self, pos: QPoint, points: list[QgsPointXY]) -> int | None:
        best, best_d = None, _SNAP_PX
        for i, p in enumerate(points):
            px = self._px(p)
            d = math.hypot(px.x() - pos.x(), px.y() - pos.y())
            if d <= best_d:
                best, best_d = i, d
        return best

    def _hit(self, pos: QPoint) -> tuple[str, int] | None:
        """``("corner", Index)`` oder ``("mid", Einfüge-Index)``."""
        corner = self._nearest(pos, self._points)
        if corner is not None:
            return "corner", corner
        edges = self._edges()
        mid = self._nearest(pos, [m for _i, m in edges])
        if mid is not None:
            return "mid", edges[mid][0]
        return None

    # -- Bearbeiten ----------------------------------------------------------

    def _commit(self):
        layer = self._layer()
        if layer is None or len(self._points) < self._min_points:
            return
        geom = self._geometry()
        geom.transform(
            QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), layer.crs(), QgsProject.instance())
        )
        self.controller.layers.change_features(self.role, {self.fid: geom})

    def _remove(self, index: int):
        if len(self._points) <= self._min_points:
            kind = "Ein Gebiet braucht mindestens drei" if self.polygon else "Ein Pfeil braucht mindestens zwei"
            self._tooltip_at(self.canvas.mapFromGlobal(QCursor.pos()), f"{kind} Punkte")
            return
        del self._points[index]
        self._active = None
        self._commit()
        self._redraw()

    def canvasPressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        hit = self._hit(QPoint(event.pos()))
        self._exit_on_release = hit is None
        if hit is None:
            return
        kind, index = hit
        if kind == "mid":
            self._points.insert(index, QgsPointXY(event.mapPoint()))
        self._drag = self._active = index
        # Ein eingefügter Punkt zählt auch ohne Mausweg als Änderung
        self._moved = kind == "mid"
        self._redraw()

    def canvasMoveEvent(self, event):
        if self._drag is not None:
            self._points[self._drag] = QgsPointXY(event.mapPoint())
            self._moved = True
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            self._redraw()
            return
        hit = self._hit(QPoint(event.pos()))
        if hit is None:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            QToolTip.hideText()
        elif hit[0] == "corner":
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            self._tooltip(event, "Ziehen verschiebt · Rechtsklick / Entf löscht den Punkt")
        else:
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            self._tooltip(event, "Ziehen fügt hier einen Punkt ein")

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            hit = self._hit(QPoint(event.pos()))
            if hit is not None and hit[0] == "corner":
                self._remove(hit[1])
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._drag is not None:
            self._drag = None
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            if self._moved:
                self._commit()
            self._redraw()
        elif self._exit_on_release:
            self.controller.back_to_select()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._active is not None and self._drag is None:
                self._remove(self._active)
            event.accept()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.cancel()
            event.accept()
            return
        super().keyPressEvent(event)


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

    Derselbe Cursor bedient auch die taktischen Zeichen: Ein Klick auf ein
    einzelnes Zeichen reicht die Geste an das MoveTool weiter (Rahmen mit
    Eckpunkten zum Skalieren und Drehen). In einer Mehrfachauswahl –
    Auswahlrahmen oder Shift+Klick – laufen Zeichen unter ``ROLE_MARKERS``
    mit und lassen sich gemeinsam mit den Objekten verschieben und löschen.
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
        # MoveTool, an das die laufende Geste auf einem taktischen Zeichen weitergereicht wird
        self._marker_gesture = None

    # -- Lebenszyklus --------------------------------------------------------

    def activate(self):
        super().activate()
        self.overlay = SelectionOverlay(self.canvas)
        self.controller.layers.selection_changed.connect(self._refresh_overlay)
        self.controller.layers.changed.connect(self._refresh_overlay)
        self.canvas.extentsChanged.connect(self._on_extent_changed)
        self._refresh_overlay()

    def deactivate(self):
        for signal, slot in (
            (self.controller.layers.selection_changed, self._refresh_overlay),
            (self.controller.layers.changed, self._refresh_overlay),
            (self.canvas.extentsChanged, self._on_extent_changed),
        ):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._marker_gesture = None
        self.controller.release_markers()
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
        # Mit Zeichen in der Auswahl wird nur verschoben – ohne Eckpunkte kein Drehen
        self.overlay.show_handles = ROLE_MARKERS not in self.controller.selection_ids()
        self.overlay.ghosts = []
        self.overlay.guides = []
        self.overlay.refresh()

    def _on_extent_changed(self):
        # Zeichen mit fester Bildschirmgröße: ihr Umriss in Karteneinheiten ändert sich beim Zoomen
        if self.controller.marker_sel:
            self._refresh_overlay()

    # -- Taktische Zeichen ----------------------------------------------------

    def _markers(self):
        """MoveTool der taktischen Zeichen (None, wenn die Toolbox aus ist); sein Cursor gilt hier mit."""
        tool = self.controller.marker_tool()
        if tool is not None and tool.cursor_proxy != self._proxy_cursor:
            tool.cursor_proxy = self._proxy_cursor
        return tool

    def _proxy_cursor(self, cursor):
        try:
            self.setCursor(QCursor(cursor))
        except RuntimeError:
            pass

    def _delegate_press(self, tool, event):
        """Geste auf einem einzelnen Zeichen: Verschieben, Skalieren und Drehen übernimmt das MoveTool."""
        self._gesture = "marker"
        self._marker_gesture = tool
        if self.overlay.hovers:
            self.overlay.hovers = []
            self.overlay.refresh()
        tool.canvasPressEvent(event)
        self.controller.marker_clicked()

    # -- Treffer -------------------------------------------------------------

    def _find(self, map_point: QgsPointXY):
        markers = self._markers()
        if markers is not None:
            feat = markers.marker_at(map_point)
            if feat is not None:
                return ROLE_MARKERS, feat
        tol = self._pixel_tolerance()
        rect = QgsRectangle(map_point.x() - tol, map_point.y() - tol, map_point.x() + tol, map_point.y() + tol)
        for role in _PICK_ORDER:
            layer = self.controller.layers.layer(role)
            if layer is None:
                continue
            layer_rect = self.toLayerCoordinates(layer, rect)
            layer_tol = max(layer_rect.width(), layer_rect.height()) / 2.0
            probe = QgsGeometry.fromPointXY(self.toLayerCoordinates(layer, map_point))
            best, best_d = None, None
            for feat in layer.getFeatures(QgsFeatureRequest().setFilterRect(layer_rect)):
                geom = feat.geometry()
                if role in FOOTPRINT_ROLES:
                    if not geom.contains(probe):
                        continue
                    d = 0.0
                else:
                    # Der Rechteckfilter prüft nur die Bounding-Box – schräge Linien genau nachmessen
                    d = (_boundary(geom) if role == ROLE_ZONES else geom).distance(probe)
                    if d > layer_tol:
                        continue
                if best_d is None or d < best_d:
                    best, best_d = feat, d
            if best is not None:
                return role, best
        return None

    def _in_rect(self, rect: QgsRectangle) -> list[tuple[str, QgsFeature]]:
        hits = []
        self._markers()
        for role in (ROLE_MARKERS, *_PICK_ORDER):
            layer = self.controller.layer(role)
            if layer is None:
                continue
            layer_rect = self.toLayerCoordinates(layer, rect)
            layer_box = QgsGeometry.fromRect(layer_rect)
            for feat in layer.getFeatures(QgsFeatureRequest().setFilterRect(layer_rect)):
                geom = feat.geometry()
                # Ein Gebiet zählt erst, wenn der Rahmen seinen Umriss erfasst – nicht schon, wenn er darin liegt
                if (_boundary(geom) if role == ROLE_ZONES else geom).intersects(layer_box):
                    hits.append((role, feat))
        return hits

    def _display_geom(self, role: str, feat: QgsFeature) -> QgsGeometry:
        """Umriss für Hover/Erfassen im Canvas-CRS (bei Zeichen der Rahmen ihres Symbols)."""
        if role == ROLE_MARKERS:
            return self.controller.marker_outline(feat)
        return self._to_canvas(role, feat.geometry())

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
                layer = self.controller.layer(role)
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

        # Rahmen des einzeln ausgewählten Zeichens: Verschieben, Skalieren, Drehen
        markers = self._markers()
        if markers is not None and not shift and markers.frame_hit(QPointF(event.pos())) is not None:
            self._delegate_press(markers, event)
            return

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
        selected = feat.id() in self.controller.selection_ids().get(role, set())
        if role == ROLE_MARKERS and not selected:
            # Einzelnes Zeichen: die bisherige Auswahl geht, das MoveTool wählt aus und zieht
            self.controller.clear_selection(keep_single_marker=True)
            self._delegate_press(markers, event)
            return
        if not selected:
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
        if self._gesture == "marker":
            tool = self._marker_gesture
            moving = getattr(tool, "moving_feature", None)
            if self._alt and moving is not None and self._past_threshold(QPoint(event.pos())):
                # Alt+Ziehen: eine Kopie bleibt an der alten Stelle liegen, das Zeichen wird weggezogen
                self._alt = False
                self.controller.plugin.duplicate_markers([moving.id()])
            tool.canvasMoveEvent(event)
            return
        pos = QPoint(event.pos())
        map_point = QgsPointXY(event.mapPoint())
        mods = event.modifiers()
        if self._gesture and (self._started or self._past_threshold(pos)):
            if not self._started:
                self._started = True
                self.overlay.hovers = []
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
                # Wie in Figma: schon beim Aufziehen markieren, was der Rahmen erfasst
                self.overlay.frame = None  # Badge hängt solange am Auswahlrechteck
                self.overlay.marquee = (self._press_map, map_point)
                hits = self._in_rect(QgsRectangle(self._press_map, map_point))
                self.overlay.hovers = [self._display_geom(role, feat) for role, feat in hits]
                self.overlay.badge = f"{len(hits)} erfasst" if hits else ""
                self.overlay.refresh()
            return
        if event.buttons() != Qt.MouseButton.NoButton:
            return

        # Hover über dem Rahmen des einzeln ausgewählten Zeichens: dessen Cursor (Skalieren, Drehen, Greifen)
        markers = self._markers()
        marker_hit = markers.frame_hit(QPointF(pos)) if markers is not None else None
        if marker_hit is not None:
            self.setCursor(QCursor(markers.cursor_for(marker_hit)))
            if self.overlay.hovers:
                self.overlay.hovers = []
                self.overlay.refresh()
            return

        # Hover: Rahmen-Ecke → Drehen-Cursor, Objekt → dünner blauer Umriss
        frame_hit = self._frame_hit(QPointF(pos))
        hit = self._find(map_point)
        hover = None
        if hit is not None:
            role, feat = hit
            if feat.id() not in self.controller.selection_ids().get(role, set()):
                hover = self._display_geom(role, feat)
        if frame_hit == "rotate":
            self.setCursor(rotate_cursor())
        elif hit is not None and hit[0] == ROLE_MARKERS:
            self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        elif hit is not None or frame_hit == "move":
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor if frame_hit == "move" else Qt.CursorShape.ArrowCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        if hover is not None or self.overlay.hovers:
            self.overlay.hovers = [hover] if hover is not None else []
            self.overlay.refresh()

    def _show_ghosts(self, geoms_m: list[tuple[str, int, QgsGeometry]], frame_m: list[QgsPointXY], badge: str):
        frame = self._frame
        self._result = {}
        ghosts = []
        for role, fid, g in geoms_m:
            layer = self.controller.layer(role)
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
        if self._gesture == "marker":
            if event.button() == Qt.MouseButton.LeftButton:
                tool, self._marker_gesture, self._gesture = self._marker_gesture, None, None
                tool.canvasReleaseEvent(event)
            return
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
                self.controller.select([(role, feat.id()) for role, feat in hits], "add" if shift else "replace")
            elif not shift:
                self.controller.clear_selection()
        self._end_gesture()

    def canvasDoubleClickEvent(self, event):
        hit = self._find(QgsPointXY(event.mapPoint()))
        if hit is None:
            return
        if hit[0] in (ROLE_ZONES, ROLE_ARROWS):
            # Wie in Figma: Doppelklick geht in die Form hinein, ihre Punkte werden bearbeitbar
            self._end_gesture()
            self.controller.edit_vertices(hit[0], hit[1].id())
        # Zeichen haben ihre eigene Beschriftung in den Marker-Details
        elif hit[0] != ROLE_MARKERS:
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
            self.overlay.hovers = []
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self._refresh_overlay()

    def _context_menu(self, pos: QPoint, map_point: QgsPointXY):
        hit = self._find(map_point)
        if hit is not None and hit[1].id() not in self.controller.selection_ids().get(hit[0], set()):
            self.controller.select([(hit[0], hit[1].id())])
        if not self.controller.selection_ids():
            return
        menu = QMenu(self.canvas)
        if self.controller.editable_shape() is not None:
            menu.addAction("Punkte bearbeiten\tDoppelklick", self.controller.edit_vertices)
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
        single_marker = self.controller.single_marker() is not None
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
        elif self._gesture == "marker":
            # Mitten im Ziehen eines Zeichens nichts verändern
            pass
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and (has_selection or single_marker):
            self.controller.delete_selection()
        elif key == Qt.Key.Key_R and not ctrl and has_selection:
            self.controller.rotate_selection(-_ROTATE_STEP_DEG if shift else _ROTATE_STEP_DEG)
        elif key == Qt.Key.Key_D and not ctrl and (has_selection or single_marker):
            self.controller.duplicate_selection()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and has_selection:
            self.controller.focus_label()
        elif key == Qt.Key.Key_Escape:
            if self._gesture:
                self._end_gesture()
            elif has_selection or single_marker:
                self.controller.clear_selection()
        else:
            # Pfeiltasten ohne Auswahl → QGIS verschiebt die Karte
            event.ignore()
            return
        event.accept()
