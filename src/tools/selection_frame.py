import math
import os
from dataclasses import dataclass

from qgis.core import QgsApplication, QgsMarkerSymbol, QgsMarkerSymbolLayer, QgsPointXY, QgsRenderContext
from qgis.gui import QgsMapCanvasItem
from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import (
    QBitmap,
    QBrush,
    QColor,
    QCursor,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRegion,
    QTransform,
)

from ..layer.renderer import svg_symbol_layer

# Figma-like selection blue
_FRAME_COLOR = QColor(13, 153, 255)
# Soft white under-stroke so the frame stays visible on dark basemaps
_HALO_COLOR = QColor(255, 255, 255, 190)
HANDLE_SIZE_PX = 8.0
# Below this edge length the handles would cover the symbol, so only the
# outline is drawn and the symbol can only be moved.
_MIN_HANDLE_FRAME_PX = 24.0
# Resolution used to find the visible part of an SVG
_CONTENT_PROBE_PX = 256
_CONTENT_CACHE_MAX = 256

_HORIZONTAL_SHIFT = {
    QgsMarkerSymbolLayer.HorizontalAnchorPoint.Left: 0.5,
    QgsMarkerSymbolLayer.HorizontalAnchorPoint.Right: -0.5,
}
_VERTICAL_SHIFT = {
    QgsMarkerSymbolLayer.VerticalAnchorPoint.Top: 0.5,
    QgsMarkerSymbolLayer.VerticalAnchorPoint.Bottom: -0.5,
}

_content_bounds_cache: dict[tuple, tuple[float, float, float, float]] = {}


@dataclass(frozen=True)
class MarkerFrame:
    """Screen-space outline of one rendered marker (pixels, y pointing down).

    `corners` are top-left, top-right, bottom-right, bottom-left of the
    visible SVG content in the symbol's own orientation. Every corner's
    offset from `anchor` is proportional to the symbol size, which is what
    makes resizing around the anchor a simple scale factor.
    """

    anchor: QPointF
    corners: tuple[QPointF, QPointF, QPointF, QPointF]
    rotation: float
    anchor_is_center: bool

    @property
    def center(self) -> QPointF:
        top_left, bottom_right = self.corners[0], self.corners[2]
        return QPointF((top_left.x() + bottom_right.x()) / 2.0, (top_left.y() + bottom_right.y()) / 2.0)

    def polygon(self) -> QPolygonF:
        return QPolygonF(list(self.corners))

    def contains(self, pos: QPointF) -> bool:
        return self.polygon().containsPoint(pos, Qt.FillRule.OddEvenFill)

    def has_handles(self) -> bool:
        width = math.dist(_xy(self.corners[0]), _xy(self.corners[1]))
        height = math.dist(_xy(self.corners[0]), _xy(self.corners[3]))
        return min(width, height) >= _MIN_HANDLE_FRAME_PX


def compute_frame(canvas, map_point: QgsPointXY, symbol: QgsMarkerSymbol) -> MarkerFrame | None:
    """Replicate QgsSvgMarkerSymbolLayer's placement for `symbol` at `map_point`.

    `map_point` is in canvas CRS. Size, aspect ratio, anchor and rotation are
    read from the symbol itself, so the frame always matches what the
    renderer draws (including a live preview symbol).
    """
    svg = svg_symbol_layer(symbol)
    if svg is None:
        return None

    context = QgsRenderContext.fromMapSettings(canvas.mapSettings())
    width = context.convertToPainterUnits(svg.size(), svg.sizeUnit(), svg.sizeMapUnitScale())
    aspect = svg.fixedAspectRatio() or svg.updateDefaultAspectRatio() or 1.0
    height = width * aspect

    anchor_xy = canvas.getCoordinateTransform().transform(map_point)
    anchor = QPointF(anchor_xy.x(), anchor_xy.y())

    # QGIS shifts the marker so the anchor sits on the chosen edge, then
    # rotates around the anchor (see QgsMarkerSymbolLayer::markerOffset).
    center_x = _HORIZONTAL_SHIFT.get(svg.horizontalAnchorPoint(), 0.0) * width
    center_y = _VERTICAL_SHIFT.get(svg.verticalAnchorPoint(), 0.0) * height
    left, top, right, bottom = _content_bounds(svg)

    transform = QTransform().translate(anchor.x(), anchor.y()).rotate(svg.angle())

    def corner(u: float, v: float) -> QPointF:
        return transform.map(QPointF(center_x + (u - 0.5) * width, center_y + (v - 0.5) * height))

    corners = (corner(left, top), corner(right, top), corner(right, bottom), corner(left, bottom))
    return MarkerFrame(anchor, corners, svg.angle(), center_x == 0.0 and center_y == 0.0)


def _content_bounds(svg) -> tuple[float, float, float, float]:
    """Visible (non-transparent) part of the SVG as fractions of its viewBox.

    Most tactical symbols sit inside a square 256×256 canvas with generous
    padding; framing the viewBox would leave a large empty margin.
    """
    path = svg.path()
    try:
        stat = os.stat(path)
        key = (path, stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = (path,)
    cached = _content_bounds_cache.get(key)
    if cached is not None:
        return cached

    bounds = (0.0, 0.0, 1.0, 1.0)
    image, _ = QgsApplication.svgCache().svgAsImage(
        path, _CONTENT_PROBE_PX, svg.fillColor(), svg.strokeColor(), svg.strokeWidth(), 1.0
    )
    if not image.isNull() and image.width() > 0 and image.height() > 0:
        rect = QRegion(QBitmap.fromImage(image.createAlphaMask())).boundingRect()
        if not rect.isEmpty():
            bounds = (
                rect.left() / image.width(),
                rect.top() / image.height(),
                (rect.right() + 1) / image.width(),
                (rect.bottom() + 1) / image.height(),
            )
    # The renderer rewrites its temp SVGs on every rebuild (new mtime → new key)
    if len(_content_bounds_cache) >= _CONTENT_CACHE_MAX:
        _content_bounds_cache.clear()
    _content_bounds_cache[key] = bounds
    return bounds


def _xy(point: QPointF) -> tuple[float, float]:
    return point.x(), point.y()


class SelectionFrameItem(QgsMapCanvasItem):
    """Canvas overlay: frame + square corner handles around the selected marker.

    Holds the marker's map position and symbol and recomputes the pixel
    frame whenever the canvas extent changes. Painting happens in canvas
    pixel coordinates; the item itself stays at the scene origin.
    """

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._map_point: QgsPointXY | None = None
        self._symbol: QgsMarkerSymbol | None = None
        self._frame: MarkerFrame | None = None
        self._bounds = QRectF()
        self.setZValue(100)
        self.hide()
        canvas.extentsChanged.connect(self.updatePosition)

    @property
    def frame(self) -> MarkerFrame | None:
        return self._frame if self.isVisible() else None

    def set_marker(self, map_point: QgsPointXY | None, symbol: QgsMarkerSymbol | None) -> None:
        """Show the frame for `symbol` drawn at `map_point` (canvas CRS); None hides it."""
        self._map_point = map_point
        self._symbol = symbol
        self.updatePosition()

    def move_to(self, map_point: QgsPointXY) -> None:
        self._map_point = map_point
        self.updatePosition()

    def dispose(self) -> None:
        try:
            self._canvas.extentsChanged.disconnect(self.updatePosition)
        except (TypeError, RuntimeError):
            pass
        scene = self._canvas.scene()
        if scene is not None:
            scene.removeItem(self)

    def updatePosition(self):
        frame = None
        if self._map_point is not None and self._symbol is not None:
            frame = compute_frame(self._canvas, self._map_point, self._symbol)
        self._frame = frame

        self.prepareGeometryChange()
        if frame is None:
            self._bounds = QRectF()
            self.hide()
            return
        margin = HANDLE_SIZE_PX + 4.0
        anchor_rect = QRectF(frame.anchor.x() - 1, frame.anchor.y() - 1, 2, 2)
        self._bounds = frame.polygon().boundingRect().united(anchor_rect).adjusted(-margin, -margin, margin, margin)
        self.show()
        self.update()

    def boundingRect(self):
        return self._bounds

    def paint(self, painter, option=None, widget=None):
        frame = self._frame
        if frame is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        polygon = frame.polygon()

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(_HALO_COLOR, 3.5))
        painter.drawPolygon(polygon)
        painter.setPen(QPen(_FRAME_COLOR, 1.5))
        painter.drawPolygon(polygon)

        # The anchor is the feature's actual map position and the pivot for
        # resize/rotate — only worth marking when it isn't the centre.
        if not frame.anchor_is_center:
            painter.setPen(QPen(QColor(255, 255, 255), 1.5))
            painter.setBrush(QBrush(_FRAME_COLOR))
            painter.drawEllipse(frame.anchor, 3.5, 3.5)

        if not frame.has_handles():
            return
        half = HANDLE_SIZE_PX / 2.0
        painter.setPen(QPen(_FRAME_COLOR, 1.5))
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        for corner in frame.corners:
            painter.save()
            painter.translate(corner)
            painter.rotate(frame.rotation)
            painter.drawRect(QRectF(-half, -half, HANDLE_SIZE_PX, HANDLE_SIZE_PX))
            painter.restore()


def resize_cursor(direction: QPointF) -> QCursor:
    """Diagonal/straight resize cursor matching a (rotated) handle direction."""
    angle = math.degrees(math.atan2(direction.y(), direction.x())) % 180.0
    shapes = (
        Qt.CursorShape.SizeHorCursor,
        Qt.CursorShape.SizeFDiagCursor,
        Qt.CursorShape.SizeVerCursor,
        Qt.CursorShape.SizeBDiagCursor,
    )
    return QCursor(shapes[int(round(angle / 45.0)) % 4])


_rotate_cursor: QCursor | None = None


def rotate_cursor() -> QCursor:
    """Curved-arrow cursor (Qt has no built-in rotate cursor)."""
    global _rotate_cursor
    if _rotate_cursor is not None:
        return _rotate_cursor

    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # Clockwise arc (Qt angles are counter-clockwise, 0° = 3 o'clock)
    start_deg, sweep_deg = 200.0, -250.0
    arc = QPainterPath()
    arc.arcMoveTo(QRectF(5, 5, 14, 14), start_deg)
    arc.arcTo(QRectF(5, 5, 14, 14), start_deg, sweep_deg)
    end = arc.currentPosition()
    # Arrowhead along the clockwise tangent at the arc's end
    theta = math.radians(start_deg + sweep_deg)
    dx, dy = math.sin(theta), math.cos(theta)
    head = QPolygonF(
        [
            QPointF(end.x() + 4.5 * dx, end.y() + 4.5 * dy),
            QPointF(end.x() - 3.5 * dy - dx, end.y() + 3.5 * dx - dy),
            QPointF(end.x() + 3.5 * dy - dx, end.y() - 3.5 * dx - dy),
        ]
    )

    for color, width in ((QColor(255, 255, 255), 4.0), (QColor(0, 0, 0), 1.6)):
        painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(arc)
        painter.setBrush(QBrush(color))
        painter.drawPolygon(head)
    painter.end()

    _rotate_cursor = QCursor(pixmap, 12, 12)
    return _rotate_cursor
