"""Kartenwerkzeuge der Lagerplanung."""

import math

from qgis.core import (
    Qgis,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsRectangle,
)
from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QCursor
from qgis.PyQt.QtWidgets import QToolTip

from ..logging_utils import get_logger
from .catalog import fmt_m
from .geometry import MetricFrame, circle_polygon, point_along, tent_polygon
from .layers import ROLE_CABLES, ROLE_DISTRIBUTORS, ROLE_TENTS, ROLE_VEHICLES

logger = get_logger(__name__)

_OK_FILL = QColor(76, 175, 80, 90)
_OK_LINE = QColor(27, 94, 32)
_BAD_FILL = QColor(229, 57, 53, 90)
_BAD_LINE = QColor(183, 28, 28)
_HINT_LINE = QColor(66, 66, 66, 160)
# Fangradius für Verteiler / Leitungsenden und Löschen (Pixel)
_SNAP_PX = 12
_ROTATE_STEP_DEG = 15
_FINE_ROTATE_STEP_DEG = 5
# Rundungstoleranz bei Abstandsprüfungen (Meter)
_EPS_M = 0.01


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


class _PlanningTool(QgsMapTool):
    """Gemeinsame Basis: Rubberbands aufräumen, Tooltip, Esc beendet."""

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
        QToolTip.showText(self.canvas.mapToGlobal(event.pos()), text, self.canvas)

    def _pixel_tolerance(self) -> float:
        return _SNAP_PX * self.canvas.mapUnitsPerPixel()

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
        self.canvas.unsetMapTool(self)


# ----------------------------------------------------------------------
# Zelt / Fahrzeug platzieren
# ----------------------------------------------------------------------


class FootprintTool(_PlanningTool):
    """Zelt oder Fahrzeug (``role``) maßstabsgetreu platzieren.

    Vorschau folgt der Maus (grün = passt, rot = Mindestabstand verletzt).
    R / Shift+R dreht um ±15°, Strg+Mausrad um ±5°, Rechtsklick um 90°.
    Shift+Klick platziert auch bei Konflikt. Bei Fahrzeugen markiert eine
    dicke Kante die Front.
    """

    def __init__(self, canvas, controller, role: str):
        super().__init__(canvas, controller)
        self.role = role
        self._body = self._new_band(_polygon_type(), _OK_FILL, _OK_LINE, 2)
        self._guy = self._new_band(_polygon_type(), QColor(0, 0, 0, 0), _HINT_LINE, 1, dashed=True)
        self._front = self._new_band(_line_type(), None, _OK_LINE, 5)
        self._last_map_point = None
        self._last_event = None

    def activate(self):
        super().activate()
        self._body.show()
        self._guy.show()

    def canvasMoveEvent(self, event):
        self._last_map_point = event.mapPoint()
        self._last_event = event
        self._update_preview()

    def _update_preview(self):
        layer = self.controller.layers.layer(self.role)
        if layer is None or self._last_map_point is None:
            return
        obj = self.controller.current_footprint(self.role)
        rotation = self.controller.rotation
        center = self.toLayerCoordinates(layer, self._last_map_point)
        frame = MetricFrame(layer.crs(), center)
        body_m = tent_polygon(frame.point_to_m(center), obj.laenge, obj.breite, rotation)
        conflict = self.controller.footprint_conflict(self.role, body_m, frame)

        self._body.setFillColor(_BAD_FILL if conflict else _OK_FILL)
        self._body.setStrokeColor(_BAD_LINE if conflict else _OK_LINE)
        self._body.setToGeometry(frame.geom_from_m(body_m), layer)
        if obj.abspannung > 0:
            self._guy.setToGeometry(frame.geom_from_m(body_m.buffer(obj.abspannung, 2)), layer)
        else:
            self._guy.reset(_polygon_type())
        if self.role == ROLE_VEHICLES:
            ring = body_m.asPolygon()[0]
            self._front.setStrokeColor(_BAD_LINE if conflict else _OK_LINE)
            self._front.setToGeometry(frame.geom_from_m(QgsGeometry.fromPolylineXY([ring[1], ring[2]])), layer)

        if self._last_event is not None:
            text = f"{obj.label()} · {fmt_m(rotation)}°"
            if conflict:
                gap = fmt_m(self.controller.gaps[self.role])
                text += f"\n⚠ Mindestabstand {gap} m unterschritten ({conflict})"
            self._tooltip(self._last_event, text)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.controller.set_rotation(self.controller.rotation + 90)
            self._update_preview()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        force = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        layer = self.controller.layers.layer(self.role)
        self.controller.place_footprint(self.role, self.toLayerCoordinates(layer, event.mapPoint()), force)
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
# Leitungsroller verlegen
# ----------------------------------------------------------------------


class CableTool(_PlanningTool):
    """Leitung eines Leitungsrollers verlegen, maximal so lang wie die Trommel.

    Klick setzt Stützpunkte (fängt Verteiler und Leitungsenden),
    Rechtsklick / Enter schließt ab, Rücktaste entfernt den letzten Punkt.
    Der gestrichelte Kreis zeigt die Restreichweite.
    """

    def __init__(self, canvas, controller):
        super().__init__(canvas, controller)
        self._line = self._new_band(_line_type(), None, QColor(239, 108, 0), 3)
        self._overflow = self._new_band(_line_type(), None, _BAD_LINE, 2, dashed=True)
        self._reach = self._new_band(_polygon_type(), QColor(0, 0, 0, 0), _HINT_LINE, 1, dashed=True)
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
        """Layer-Koordinate des Cursors, ggf. auf Verteiler/Leitungsende gefangen."""
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
        total = 0.0
        pts = [self._frame.point_to_m(p) for p in points]
        for a, b in zip(pts, pts[1:]):
            total += math.hypot(b.x() - a.x(), b.y() - a.y())
        return total

    def canvasMoveEvent(self, event):
        layer = self._layer()
        reel = self.controller.current_reel()
        if layer is None or reel is None:
            return
        cursor, _ = self._snapped(event.mapPoint())
        if not self._points:
            self._tooltip(event, f"{reel.name}: Startpunkt setzen (Verteiler werden gefangen)")
            return

        used = self._length_m(self._points)
        rest = max(reel.laenge - used, 0.0)
        last_m = self._frame.point_to_m(self._points[-1])
        cursor_m = self._frame.point_to_m(cursor)
        seg = math.hypot(cursor_m.x() - last_m.x(), cursor_m.y() - last_m.y())

        if seg > rest:
            end = self._frame.point_from_m(point_along(last_m, cursor_m, rest))
            self._overflow.setToGeometry(QgsGeometry.fromPolylineXY([end, cursor]), layer)
            self._overflow.show()
            total = reel.laenge
        else:
            end = cursor
            self._overflow.reset(_line_type())
            total = used + seg
        self._line.setToGeometry(QgsGeometry.fromPolylineXY(self._points + [end]), layer)
        self._reach.setToGeometry(self._frame.geom_from_m(circle_polygon(last_m, rest)), layer)

        text = f"{reel.name}: {fmt_m(round(total, 1))} m von {fmt_m(reel.laenge)} m"
        if seg > rest:
            text += f"\n⚠ {fmt_m(round(used + seg - reel.laenge, 1))} m zu kurz"
        else:
            text += f" · Rest {fmt_m(round(reel.laenge - total, 1))} m"
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

        used = self._length_m(self._points)
        rest = reel.laenge - used
        last_m = self._frame.point_to_m(self._points[-1])
        point_m = self._frame.point_to_m(point)
        seg = math.hypot(point_m.x() - last_m.x(), point_m.y() - last_m.y())
        if seg <= _EPS_M:
            return
        if seg >= rest - _EPS_M:
            # Trommel ist leer — Leitung endet hier
            self._points.append(self._frame.point_from_m(point_along(last_m, point_m, rest)))
            self._finish()
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
        if len(self._points) >= 2:
            self.controller.place_cable(list(self._points), self._length_m(self._points))
        self._reset()

    def _reset(self):
        self._points = []
        self._frame = None
        for band, typ in ((self._line, _line_type()), (self._overflow, _line_type()), (self._reach, _polygon_type())):
            band.reset(typ)
        self._snap_marker.hide()


# ----------------------------------------------------------------------
# Verteiler setzen
# ----------------------------------------------------------------------


class DistributorTool(_PlanningTool):
    def canvasMoveEvent(self, event):
        dist = self.controller.current_distributor()
        if dist:
            self._tooltip(event, f"{dist.name} setzen")

    def canvasReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        layer = self.controller.layers.distributors
        if layer is not None:
            self.controller.place_distributor(self.toLayerCoordinates(layer, event.mapPoint()))


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
# Löschen
# ----------------------------------------------------------------------


class DeleteTool(_PlanningTool):
    """Zelt, Leitung oder Verteiler per Klick entfernen (Vorschau rot)."""

    def __init__(self, canvas, controller):
        super().__init__(canvas, controller)
        self._hl_poly = self._new_band(_polygon_type(), _BAD_FILL, _BAD_LINE, 3)
        self._hl_line = self._new_band(_line_type(), None, _BAD_LINE, 5)
        self._hl_point = self._new_band(Qgis.GeometryType.Point, None, _BAD_LINE, 3)
        self._hl_point.setIcon(QgsRubberBand.IconType.ICON_BOX)
        self._hl_point.setIconSize(20)

    def _find(self, map_point):
        tol = self._pixel_tolerance()
        rect = QgsRectangle(map_point.x() - tol, map_point.y() - tol, map_point.x() + tol, map_point.y() + tol)
        # Punkte und Linien zuerst, sonst wären sie auf Zelten nicht greifbar
        for role in (ROLE_DISTRIBUTORS, ROLE_CABLES, ROLE_VEHICLES, ROLE_TENTS):
            layer = self.controller.layers.layer(role)
            if layer is None:
                continue
            layer_rect = self.toLayerCoordinates(layer, rect)
            probe = QgsGeometry.fromPointXY(self.toLayerCoordinates(layer, map_point))
            request = QgsFeatureRequest().setFilterRect(layer_rect)
            best, best_d = None, None
            for feat in layer.getFeatures(request):
                geom = feat.geometry()
                if role in (ROLE_TENTS, ROLE_VEHICLES):
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

    def canvasMoveEvent(self, event):
        hit = self._find(event.mapPoint())
        self._reset_highlight()
        if hit is None:
            QToolTip.hideText()
            return
        role, feat = hit
        band = {ROLE_DISTRIBUTORS: self._hl_point, ROLE_CABLES: self._hl_line}.get(role, self._hl_poly)
        band.setToGeometry(feat.geometry(), self.controller.layers.layer(role))
        name = feat.attribute("bezeichnung") or feat.attribute("typ")
        self._tooltip(event, f"Klick löscht: {name}")

    def canvasReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        hit = self._find(event.mapPoint())
        if hit is None:
            return
        role, feat = hit
        self.controller.layers.delete_feature(role, feat.id())
        self._reset_highlight()

    def _reset_highlight(self):
        self._hl_poly.reset(_polygon_type())
        self._hl_line.reset(_line_type())
        self._hl_point.reset(Qgis.GeometryType.Point)
