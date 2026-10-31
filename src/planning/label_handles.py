"""Rahmen mit Anfassern an der Beschriftung des einzeln ausgewählten Texts, Punkts, Gebiets bzw. der Linie.

Im Rahmen ziehen verschiebt die Beschriftung, an einer Ecke ziehen ändert die Schriftgröße, knapp
außerhalb einer Ecke ziehen dreht sie (Shift rastet in 15°-Schritten). Ein Text ist seine Beschriftung:
ihn verschiebt das Auswahlwerkzeug als Ganzes, hier wird er nur skaliert und gedreht.

Auswahl- und Punktbearbeitungswerkzeug reichen ihre Mausereignisse zuerst hierher; ``press`` sagt,
ob die Geste hier beginnt.
"""

import math

from qgis.core import QgsPointXY
from qgis.gui import QgsMapCanvasItem
from qgis.PyQt.QtCore import QPoint, QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QBrush, QColor, QCursor, QPainter, QPen, QPolygonF
from qgis.PyQt.QtWidgets import QToolTip

from ..tools.selection_frame import rotate_cursor
from ..ui.font_size_box import MAX_PT, MIN_PT
from .catalog import fmt_m
from .label_frame import LabelFrame
from .layers import ROLE_TEXTS

_BLUE = QColor(13, 153, 255)
_HALO = QColor(255, 255, 255, 190)
_HANDLE_PX = 7.0
# Greifbereich der Ecken und Drehzone außerhalb (Pixel)
_GRAB_PX = _HANDLE_PX / 2.0 + 3.0
_ROTATE_PX = 20.0
_ROTATE_STEP_DEG = 15.0
# Mausweg, ab dem ein Druck als Ziehen gilt (Pixel)
_DRAG_PX = 3


def _event_pos(event) -> QPointF:
    return QPointF(event.pos())


class LabelHandles(QgsMapCanvasItem):
    def __init__(self, canvas, controller):
        super().__init__(canvas)
        self._canvas = canvas
        self.controller = controller
        self.setZValue(110)
        self._target: tuple[str, int] | None = None
        self._frame: LabelFrame | None = None
        self._preview: LabelFrame | None = None
        self._gesture: str | None = None
        self._press_px: QPointF | None = None
        self._press_map: QgsPointXY | None = None
        self._started = False

    # -- Lebenszyklus -------------------------------------------------------

    def dispose(self):
        scene = self._canvas.scene()
        if scene is not None:
            scene.removeItem(self)

    def refresh(self):
        """Rahmen an die aktuelle Auswahl und die zuletzt gezeichnete Beschriftung anpassen."""
        if self._gesture is not None:
            return
        target = self.controller.label_target()
        self._target = (target[0], target[1].id()) if target else None
        self._frame = self.controller.label_frame(*target) if target else None
        self._preview = None
        self.prepareGeometryChange()
        self.update()

    @property
    def active(self) -> bool:
        return self._gesture is not None

    # -- Geometrie ----------------------------------------------------------

    def boundingRect(self):
        size = self._canvas.size()
        return QRectF(-50, -50, size.width() + 100, size.height() + 100)

    def _px(self, point: QgsPointXY) -> QPointF:
        p = self._canvas.getCoordinateTransform().transform(point)
        return QPointF(p.x(), p.y())

    def _corners_px(self, frame: LabelFrame) -> list[QPointF]:
        return [self._px(p) for p in frame.corners]

    def hit(self, pos: QPointF) -> str | None:
        """``scale`` an einer Ecke, ``rotate`` knapp außerhalb, ``move`` im Rahmen (nicht bei Texten), sonst None."""
        if self._frame is None:
            return None
        corners = self._corners_px(self._frame)
        for c in corners:
            if math.dist((pos.x(), pos.y()), (c.x(), c.y())) <= _GRAB_PX:
                return "scale"
        inside = QPolygonF(corners).containsPoint(pos, Qt.FillRule.OddEvenFill)
        if inside:
            return None if self._target[0] == ROLE_TEXTS else "move"
        for c in corners:
            if math.dist((pos.x(), pos.y()), (c.x(), c.y())) <= _ROTATE_PX:
                return "rotate"
        return None

    def cursor_for(self, hit: str):
        if hit == "rotate":
            return rotate_cursor()
        if hit == "scale":
            return QCursor(Qt.CursorShape.SizeFDiagCursor)
        return QCursor(Qt.CursorShape.SizeAllCursor)

    # -- Gesten -------------------------------------------------------------

    def press(self, event) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        pos = _event_pos(event)
        gesture = self.hit(pos)
        if gesture is None:
            return False
        self._gesture = gesture
        self._press_px = pos
        self._press_map = QgsPointXY(event.mapPoint())
        self._started = False
        return True

    def move(self, event) -> bool:
        if self._gesture is None:
            return False
        pos = _event_pos(event)
        if not self._started:
            if max(abs(pos.x() - self._press_px.x()), abs(pos.y() - self._press_px.y())) < _DRAG_PX:
                return True
            self._started = True
        frame = self._frame
        center = self._px(frame.center)
        if self._gesture == "move":
            map_point = QgsPointXY(event.mapPoint())
            self._preview = frame.moved(map_point.x() - self._press_map.x(), map_point.y() - self._press_map.y())
            tip = ""
        elif self._gesture == "scale":
            start = math.dist((self._press_px.x(), self._press_px.y()), (center.x(), center.y()))
            now = math.dist((pos.x(), pos.y()), (center.x(), center.y()))
            factor = now / start if start > 0 else 1.0
            factor = min(max(factor, MIN_PT / frame.size), MAX_PT / frame.size)
            self._preview = frame.scaled(factor)
            tip = f"{fmt_m(round(self._preview.size, 1))} pt"
        else:

            def angle(p: QPointF) -> float:
                # Bildschirm-y zeigt nach unten: positive Winkel drehen im Uhrzeigersinn
                return math.degrees(math.atan2(p.y() - center.y(), p.x() - center.x()))

            delta = (angle(pos) - angle(self._press_px) + 180.0) % 360.0 - 180.0
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                delta = round((frame.angle + delta) / _ROTATE_STEP_DEG) * _ROTATE_STEP_DEG - frame.angle
            self._preview = frame.rotated(delta)
            shown = (self._preview.angle + 180.0) % 360.0 - 180.0
            tip = f"{fmt_m(round(shown))}°"
        if tip:
            QToolTip.showText(self._canvas.mapToGlobal(QPoint(int(pos.x()), int(pos.y())) + QPoint(16, 16)), tip)
        self.update()
        return True

    def release(self, event) -> bool:
        if self._gesture is None:
            return False
        preview, target = self._preview, self._target
        self._gesture = None
        self._preview = None
        QToolTip.hideText()
        if self._started and preview is not None and target is not None:
            self.controller.set_label_placement(target[0], target[1], preview.center, preview.angle, preview.size)
        self.refresh()
        return True

    def cancel(self):
        self._gesture = None
        self._preview = None
        self.update()

    # -- Zeichnen -----------------------------------------------------------

    def paint(self, painter, option=None, widget=None):
        frame = self._preview or self._frame
        if frame is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        corners = self._corners_px(frame)
        polygon = QPolygonF(corners)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(_HALO, 3.0))
        painter.drawPolygon(polygon)
        painter.setPen(QPen(_BLUE, 1.0, Qt.PenStyle.DashLine))
        painter.drawPolygon(polygon)
        angle = math.degrees(math.atan2(corners[1].y() - corners[0].y(), corners[1].x() - corners[0].x()))
        half = _HANDLE_PX / 2.0
        painter.setPen(QPen(_BLUE, 1.5))
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        for corner in corners:
            painter.save()
            painter.translate(corner)
            painter.rotate(angle)
            painter.drawRect(QRectF(-half, -half, _HANDLE_PX, _HANDLE_PX))
            painter.restore()
