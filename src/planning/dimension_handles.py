"""Maße des einzeln ausgewählten Punkts, der Linie bzw. des Gebiets einzeln verschieben, skalieren und ausblenden.

Die Maße zeichnet das Symbol (``layers._with_segment_dimensions`` bzw. der Radius in ``_point_symbol``),
QGIS kennt ihre Lage also nicht. Hier wird sie aus denselben Angaben nachgerechnet: Mitte des Segments,
Richtung (umgedreht, wenn die Schrift sonst auf dem Kopf stünde), Abstand zur Kante, Versatz und
Schriftgröße aus ``masse_anpassung``.

Ein Klick auf ein Maß wählt es aus (blauer Rahmen mit Ecken): Ziehen verschiebt, an einer Ecke ziehen
skaliert, Entf blendet es aus. Auswahl- und Punktbearbeitungswerkzeug reichen ihre Ereignisse zuerst hierher.
"""

import json
import math
from dataclasses import dataclass, replace

from qgis.core import QgsCoordinateTransform, QgsGeometry, QgsPointXY, QgsProject
from qgis.gui import QgsMapCanvasItem
from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QBrush, QColor, QCursor, QFont, QFontMetricsF, QPainter, QPen, QPolygonF

from .catalog import fmt_m
from .geometry import MetricFrame
from .layers import (
    DIMENSION_ADJUST_FIELD,
    DIMENSION_GAP_MM,
    DIMENSION_SIZE_MM,
    RADIUS_DIMENSION_KEY,
    ROLE_POINTS,
    ROLE_ZONES,
)

_BLUE = QColor(13, 153, 255)
_HALO = QColor(255, 255, 255, 190)
_HANDLE_PX = 7.0
_GRAB_PX = _HANDLE_PX / 2.0 + 3.0
_DRAG_PX = 3
MIN_SCALE, MAX_SCALE = 0.3, 8.0


def read_adjustments(feature) -> dict:
    """``masse_anpassung`` eines Objekts als dict (leer bei NULL oder ungültigem JSON)."""
    if feature.fields().indexFromName(DIMENSION_ADJUST_FIELD) < 0:
        return {}
    value = feature.attribute(DIMENSION_ADJUST_FIELD)
    if not value or not isinstance(value, str):
        return {}
    try:
        data = json.loads(value)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _meters_text(value: float) -> str:
    """Wie ``expressions.meters_text_expr`` – nur für die Breite des Rahmens."""
    return f"{fmt_m(round(value / 1000, 2))} km" if value >= 1000 else f"{fmt_m(round(value, 1))} m"


@dataclass
class DimensionItem:
    """Ein gezeichnetes Maß in Bildschirm-Pixeln.

    ``angle``: Drehung der Schrift (Bogenmaß, im Uhrzeigersinn); ``unit_px``: Pixel je Einheit des Versatzes
    (Segmente: mm, Radius: Meter); ``rotated``: Versatz in Richtung der Schrift (Segmente) oder achsparallel.
    """

    key: str
    center: QPointF
    angle: float
    width: float
    height: float
    unit_px: float
    rotated: bool
    adjust: dict

    def corners(self) -> list[QPointF]:
        c, s = math.cos(self.angle), math.sin(self.angle)
        hw, hh = self.width / 2.0, self.height / 2.0
        return [
            QPointF(self.center.x() + x * c - y * s, self.center.y() + x * s + y * c)
            for x, y in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))
        ]

    def contains(self, pos: QPointF) -> bool:
        return QPolygonF(self.corners()).containsPoint(pos, Qt.FillRule.OddEvenFill)

    def moved(self, delta: QPointF) -> "DimensionItem":
        """Um ``delta`` Pixel verschoben; der Versatz in ``adjust`` wird mitgeführt."""
        dx, dy = delta.x(), delta.y()
        if self.rotated:
            c, s = math.cos(-self.angle), math.sin(-self.angle)
            dx, dy = dx * c - dy * s, dx * s + dy * c
        adjust = dict(self.adjust)
        adjust["dx"] = round(adjust.get("dx", 0.0) + dx / self.unit_px, 3)
        adjust["dy"] = round(adjust.get("dy", 0.0) + dy / self.unit_px, 3)
        return replace(self, center=self.center + delta, adjust=adjust)

    def scaled(self, factor: float) -> "DimensionItem":
        old = float(self.adjust.get("s", 1.0))
        new = min(max(old * factor, MIN_SCALE), MAX_SCALE)
        adjust = dict(self.adjust)
        adjust["s"] = round(new, 3)
        f = new / old
        return replace(self, width=self.width * f, height=self.height * f, adjust=adjust)


def dimension_items(canvas, layer, role: str, feature) -> list[DimensionItem]:
    """Alle sichtbaren Maße eines Objekts – so, wie das Symbol sie zeichnet."""
    geom = feature.geometry()
    if geom is None or geom.isEmpty():
        return []
    adjustments = read_adjustments(feature)
    px_per_mm = canvas.mapSettings().outputDpi() / 25.4
    to_canvas = QgsCoordinateTransform(layer.crs(), canvas.mapSettings().destinationCrs(), QgsProject.instance())
    pixel = canvas.getCoordinateTransform()

    def px(point: QgsPointXY) -> QPointF:
        p = pixel.transform(to_canvas.transform(point))
        return QPointF(p.x(), p.y())

    def box(text: str, scale: float) -> tuple[float, float]:
        font = QFont(QFont().family())
        font.setPixelSize(max(2, round(DIMENSION_SIZE_MM * scale * px_per_mm)))
        metrics = QFontMetricsF(font)
        return metrics.horizontalAdvance(text) + 4, metrics.height()

    items = []
    if role == ROLE_POINTS:
        radius = float(feature.attribute("radius") or 0)
        adjust = adjustments.get(RADIUS_DIMENSION_KEY, {})
        if radius <= 0 or adjust.get("aus"):
            return []
        center = geom.asPoint()
        frame = MetricFrame(layer.crs(), center)
        c_m = frame.point_to_m(center)
        # Pixel je Meter an diesem Punkt (Versatz des Radius-Maßes ist in Metern)
        east = frame.point_from_m(QgsPointXY(c_m.x() + 1.0, c_m.y()))
        m_px = math.dist((px(center).x(), px(center).y()), (px(east).x(), px(east).y()))
        scale = float(adjust.get("s", 1.0))
        width, height = box("r = " + _meters_text(radius), scale)
        anchor = px(center)
        # Unterkante der Schrift am Versatz (Ankerpunkt unten), darüber der Rahmen
        offset = QPointF(adjust.get("dx", 0.0) * m_px, (-radius + adjust.get("dy", 0.0)) * m_px)
        mid = anchor + offset - QPointF(0, height / 2.0)
        items.append(DimensionItem(RADIUS_DIMENSION_KEY, mid, 0.0, width, height, m_px, False, adjust))
        return items

    if role == ROLE_ZONES:
        polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
        rings = [ring for poly in polys for ring in poly]
    else:
        rings = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
    number = 0
    for ring in rings:
        for a, b in zip(ring, ring[1:]):
            number += 1
            key = str(number)
            adjust = adjustments.get(key, {})
            if adjust.get("aus"):
                continue
            frame = MetricFrame(layer.crs(), a)
            length = frame.geom_to_m(QgsGeometry.fromPolylineXY([a, b])).length()
            pa, pb = px(QgsPointXY(a)), px(QgsPointXY(b))
            flipped = b.x() < a.x()
            angle = math.atan2(pb.y() - pa.y(), pb.x() - pa.x()) + (math.pi if flipped else 0.0)
            scale = float(adjust.get("s", 1.0))
            width, height = box(_meters_text(length), scale)
            base = DIMENSION_GAP_MM if flipped else -DIMENSION_GAP_MM
            ox = adjust.get("dx", 0.0) * px_per_mm
            oy = (base + adjust.get("dy", 0.0)) * px_per_mm
            c, s = math.cos(angle), math.sin(angle)
            mid = QPointF((pa.x() + pb.x()) / 2.0, (pa.y() + pb.y()) / 2.0)
            center = mid + QPointF(ox * c - oy * s, ox * s + oy * c)
            items.append(DimensionItem(key, center, angle, width, height, px_per_mm, True, adjust))
    return items


class DimensionHandles(QgsMapCanvasItem):
    def __init__(self, canvas, controller):
        super().__init__(canvas)
        self._canvas = canvas
        self.controller = controller
        self.setZValue(105)
        self._target: tuple[str, int] | None = None
        self._items: list[DimensionItem] = []
        self._active: str | None = None
        self._preview: DimensionItem | None = None
        self._gesture: str | None = None
        self._press: QPointF | None = None
        self._started = False

    # -- Lebenszyklus -------------------------------------------------------

    def dispose(self):
        scene = self._canvas.scene()
        if scene is not None:
            scene.removeItem(self)

    def refresh(self):
        if self._gesture is not None:
            return
        target = self.controller.dimension_target()
        new_target = (target[0], target[1].id()) if target else None
        if new_target != self._target:
            self._active = None
        self._target = new_target
        self._items = []
        if target is not None:
            layer = self.controller.layers.layer(target[0])
            self._items = dimension_items(self._canvas, layer, target[0], target[1])
        if self._active not in {item.key for item in self._items}:
            self._active = None
        self._preview = None
        self.prepareGeometryChange()
        self.update()

    @property
    def busy(self) -> bool:
        return self._gesture is not None

    @property
    def has_active(self) -> bool:
        return self._active is not None

    def clear_active(self) -> bool:
        if self._active is None:
            return False
        self._active = None
        self.update()
        return True

    # -- Treffer ------------------------------------------------------------

    def _item(self, key: str | None) -> DimensionItem | None:
        return next((item for item in self._items if item.key == key), None)

    def hit(self, pos: QPointF) -> tuple[str, str] | None:
        """(Schlüssel, ``scale`` an einer Ecke des ausgewählten Maßes bzw. ``move`` im Rahmen) oder None."""
        active = self._item(self._active)
        if active is not None:
            for corner in active.corners():
                if math.dist((pos.x(), pos.y()), (corner.x(), corner.y())) <= _GRAB_PX:
                    return active.key, "scale"
        for item in self._items:
            if item.contains(pos):
                return item.key, "move"
        return None

    def cursor_for(self, gesture: str):
        return QCursor(Qt.CursorShape.SizeFDiagCursor if gesture == "scale" else Qt.CursorShape.SizeAllCursor)

    # -- Gesten -------------------------------------------------------------

    def press(self, event) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        pos = QPointF(event.pos())
        hit = self.hit(pos)
        if hit is None:
            return False
        self._active, self._gesture = hit
        self._press = pos
        self._started = False
        self.update()
        return True

    def move(self, event) -> bool:
        if self._gesture is None:
            return False
        pos = QPointF(event.pos())
        if not self._started:
            if max(abs(pos.x() - self._press.x()), abs(pos.y() - self._press.y())) < _DRAG_PX:
                return True
            self._started = True
        item = self._item(self._active)
        if item is None:
            return True
        if self._gesture == "move":
            self._preview = item.moved(pos - self._press)
        else:
            c = item.center
            start = math.dist((self._press.x(), self._press.y()), (c.x(), c.y()))
            now = math.dist((pos.x(), pos.y()), (c.x(), c.y()))
            self._preview = item.scaled(now / start if start > 0 else 1.0)
        self.update()
        return True

    def release(self, event) -> bool:
        if self._gesture is None:
            return False
        preview, target, key = self._preview, self._target, self._active
        self._gesture = None
        self._preview = None
        if self._started and preview is not None and target is not None:
            self.controller.set_dimension_adjustment(target[0], target[1], key, preview.adjust)
        self.refresh()
        return True

    def hide_active(self) -> bool:
        """Entf: das ausgewählte Maß ausblenden."""
        item = self._item(self._active)
        if item is None or self._target is None:
            return False
        adjust = dict(item.adjust)
        adjust["aus"] = True
        self._active = None
        self.controller.set_dimension_adjustment(self._target[0], self._target[1], item.key, adjust)
        return True

    # -- Zeichnen -----------------------------------------------------------

    def boundingRect(self):
        size = self._canvas.size()
        return QRectF(-50, -50, size.width() + 100, size.height() + 100)

    def paint(self, painter, option=None, widget=None):
        if not self._items:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Alle Maße dezent umrandet – so sieht man, was sich greifen lässt
        faint = QPen(QColor(13, 153, 255, 110), 1.0, Qt.PenStyle.DotLine)
        for item in self._items:
            if item.key != self._active:
                painter.setPen(faint)
                painter.drawPolygon(QPolygonF(item.corners()))
        active = self._preview or self._item(self._active)
        if active is None:
            return
        corners = active.corners()
        painter.setPen(QPen(_HALO, 3.0))
        painter.drawPolygon(QPolygonF(corners))
        painter.setPen(QPen(_BLUE, 1.0))
        painter.drawPolygon(QPolygonF(corners))
        half = _HANDLE_PX / 2.0
        painter.setPen(QPen(_BLUE, 1.5))
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        for corner in corners:
            painter.save()
            painter.translate(corner)
            painter.rotate(math.degrees(active.angle))
            painter.drawRect(QRectF(-half, -half, _HANDLE_PX, _HANDLE_PX))
            painter.restore()
