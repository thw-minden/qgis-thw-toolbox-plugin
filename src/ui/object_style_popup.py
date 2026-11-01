"""Gestaltung eines Pfeils, Punkts oder Texts im Popup: Farbe, Deckkraft, bei Pfeilen Linienbreite und Spitzen.

Aufbau wie die Gestaltung der Gebiete (``zone_style_popup``): eine Schaltfläche mit Vorschau,
das Popup ändert sofort. Gemeldet werden nur die geänderten Angaben, damit bei mehreren
Objekten jedes seine übrigen Einstellungen (z. B. die eigene Farbe) behält.
"""

from qgis.PyQt.QtCore import QPointF, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSlider,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from ..planning.object_style import (
    ARROWS_BOTH,
    ARROWS_END,
    ARROWS_NONE,
    ARROWS_START,
    MAX_ARROW_SIZE,
    MAX_LINE_WIDTH,
    MIN_LINE_WIDTH,
    MIN_OPACITY,
    ObjectStyle,
)
from .theme import muted_color, muted_style
from .zone_style_popup import COLORS, color_name, swatch_icon

_ICON_PX = 20

# Was gestaltet wird: bestimmt Titel und welche Angaben das Popup zeigt
KIND_ARROW = "pfeil"
KIND_POINT = "punkt"
KIND_TEXT = "text"
_TITLES = {KIND_ARROW: "Gestaltung der Linie", KIND_POINT: "Gestaltung des Punkts", KIND_TEXT: "Gestaltung des Texts"}
_TYPE_NAMES = {KIND_ARROW: "Linientyps", KIND_POINT: "Punkttyps", KIND_TEXT: "Texttyps"}


def _arrow_icon(arrows: str, color: str | None = None, px: int = _ICON_PX) -> QIcon:
    """Waagerechte Linie mit den Spitzen des Modus."""
    pixmap = QPixmap(px, px)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen_color = QColor(color) if color else QColor(muted_color()).lighter(160)
    painter.setPen(QPen(pen_color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    y = px / 2
    painter.drawLine(QPointF(3, y), QPointF(px - 3, y))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(pen_color)
    head, half = px * 0.3, px * 0.3 / 1.4
    if arrows in (ARROWS_END, ARROWS_BOTH):
        tip = px - 1
        painter.drawPolygon(QPolygonF([QPointF(tip, y), QPointF(tip - head, y - half), QPointF(tip - head, y + half)]))
    if arrows in (ARROWS_START, ARROWS_BOTH):
        painter.drawPolygon(QPolygonF([QPointF(1, y), QPointF(1 + head, y - half), QPointF(1 + head, y + half)]))
    painter.end()
    return QIcon(pixmap)


def _dot_icon(color: str, opacity: float, text: bool, px: int = _ICON_PX) -> QIcon:
    """Vorschau für Punkt (gefüllter Kreis) bzw. Text (Buchstabe) in der Farbe."""
    pixmap = QPixmap(px, px)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    fill = QColor(color)
    fill.setAlphaF(max(opacity, 0.35))
    if text:
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(px - 4)
        painter.setFont(font)
        painter.setPen(fill)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "T")
    else:
        painter.setPen(QPen(QColor("#ffffff"), 1.5))
        painter.setBrush(fill)
        painter.drawEllipse(QPointF(px / 2, px / 2), px / 2 - 4, px / 2 - 4)
    painter.end()
    return QIcon(pixmap)


def object_style_icon(style: ObjectStyle, kind: str, px: int = _ICON_PX) -> QIcon:
    if kind == KIND_ARROW:
        return _arrow_icon(style.arrows, style.color, px)
    return _dot_icon(style.color, style.opacity, kind == KIND_TEXT, px)


def describe(style: ObjectStyle, kind: str) -> str:
    """Kurzer Text für die Schaltfläche, z. B. „Rot, 1,2 mm“."""
    name = color_name(style.color)
    if kind == KIND_ARROW:
        return f"{name}, {style.width:.1f} mm".replace(".", ",")
    return name


def _tool_button(icon: QIcon, tooltip: str) -> QToolButton:
    button = QToolButton()
    button.setIcon(icon)
    button.setIconSize(QSize(_ICON_PX, _ICON_PX))
    button.setToolTip(tooltip)
    button.setCheckable(True)
    button.setAutoRaise(True)
    return button


class _ObjectStyleEditor(QWidget):
    """Inhalt des Popups; jede Änderung meldet sofort die geänderten Angaben."""

    changed = pyqtSignal(dict)
    reset_requested = pyqtSignal()
    custom_color_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._style = ObjectStyle("#c62828")
        grid = QGridLayout(self)
        grid.setContentsMargins(10, 8, 10, 8)
        self._title = QLabel()
        grid.addWidget(self._title, 0, 0, 1, 2)

        self._colors = QButtonGroup(self)
        colors = QHBoxLayout()
        colors.setSpacing(1)
        for name, hex_color in COLORS:
            button = _tool_button(swatch_icon(hex_color), name)
            button.setProperty("value", hex_color)
            button.clicked.connect(lambda _c=False, v=hex_color: self._emit(color=v))
            self._colors.addButton(button)
            colors.addWidget(button)
        more = QToolButton()
        more.setText("…")
        more.setToolTip("Eigene Farbe …")
        more.setAutoRaise(True)
        more.clicked.connect(self.custom_color_requested)
        colors.addWidget(more)
        colors.addStretch()
        grid.addWidget(QLabel("Farbe"), 1, 0)
        grid.addLayout(colors, 1, 1)

        opacity_row = QHBoxLayout()
        self._opacity = QSlider(Qt.Orientation.Horizontal)
        self._opacity.setRange(round(MIN_OPACITY * 100), 100)
        self._opacity.setSingleStep(5)
        self._opacity.setPageStep(10)
        self._opacity_label = QLabel()
        self._opacity_label.setMinimumWidth(36)
        self._opacity_label.setStyleSheet(muted_style())
        self._opacity.valueChanged.connect(self._on_opacity_moved)
        # Beim Ziehen erst beim Loslassen anwenden – jede Änderung schreibt in den Layer
        self._opacity.sliderReleased.connect(lambda: self._emit(opacity=self._opacity.value() / 100))
        opacity_row.addWidget(self._opacity, 1)
        opacity_row.addWidget(self._opacity_label)
        grid.addWidget(QLabel("Deckkraft"), 2, 0)
        grid.addLayout(opacity_row, 2, 1)

        # Nur Pfeile: Linienbreite, Spitzen und deren Größe
        self._width = QDoubleSpinBox()
        self._width.setRange(MIN_LINE_WIDTH, MAX_LINE_WIDTH)
        self._width.setSingleStep(0.2)
        self._width.setDecimals(1)
        self._width.setSuffix(" mm")
        self._width.setKeyboardTracking(False)
        self._width.valueChanged.connect(lambda value: self._emit(width=round(value, 1)))
        self._width_label = QLabel("Linienbreite")
        grid.addWidget(self._width_label, 3, 0)
        width_row = QHBoxLayout()
        width_row.addWidget(self._width)
        width_row.addStretch()
        grid.addLayout(width_row, 3, 1)

        # Ob Spitzen am Anfang bzw. Ende sitzen, schalten die Häkchen in den Details
        self._arrow_size = QDoubleSpinBox()
        self._arrow_size.setRange(0.0, MAX_ARROW_SIZE)
        self._arrow_size.setSingleStep(0.5)
        self._arrow_size.setDecimals(1)
        self._arrow_size.setSuffix(" mm")
        # 0 = automatisch: die Spitze wächst mit der Linienbreite
        self._arrow_size.setSpecialValueText("automatisch")
        self._arrow_size.setKeyboardTracking(False)
        self._arrow_size.setToolTip("Größe der Spitzen; „automatisch“ richtet sich nach der Linienbreite")
        self._arrow_size.valueChanged.connect(lambda value: self._emit(arrow_size=round(value, 1)))
        self._arrow_size_label = QLabel("Größe Spitze")
        grid.addWidget(self._arrow_size_label, 5, 0)
        size_row = QHBoxLayout()
        size_row.addWidget(self._arrow_size)
        size_row.addStretch()
        grid.addLayout(size_row, 5, 1)

        self._reset = QPushButton("Wie Typ")
        self._reset.clicked.connect(self.reset_requested)
        grid.addWidget(self._reset, 6, 1, Qt.AlignmentFlag.AlignLeft)
        self._line_widgets = (self._width_label, self._width, self._arrow_size_label, self._arrow_size)
        self.set_kind(KIND_ARROW)

    def set_kind(self, kind: str):
        self._title.setText(f"<b>{_TITLES[kind]}</b>")
        for widget in self._line_widgets:
            widget.setVisible(kind == KIND_ARROW)
        self._reset.setToolTip(f"Eigene Gestaltung entfernen: wieder Farbe und Darstellung des {_TYPE_NAMES[kind]}")
        self.adjustSize()

    def set_style(self, style: ObjectStyle):
        self._style = style
        # Eigene Farben passen zu keinem Feld: dann ist keins markiert
        self._colors.setExclusive(False)
        for button in self._colors.buttons():
            button.setChecked(button.property("value") == QColor(style.color).name())
        self._colors.setExclusive(True)
        for widget, value in (
            (self._opacity, round(style.opacity * 100)),
            (self._width, style.width),
            (self._arrow_size, style.arrow_size),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self._opacity_label.setText(f"{self._opacity.value()} %")
        self._arrow_size.setEnabled(style.arrows != ARROWS_NONE)

    def _on_opacity_moved(self, value: int):
        self._opacity_label.setText(f"{value} %")
        if not self._opacity.isSliderDown():
            self._emit(opacity=value / 100)

    def _emit(self, **changes):
        style = self._style.updated(**changes)
        if style != self._style:
            self.set_style(style)
            self.changed.emit(changes)


class ObjectStyleButton(QToolButton):
    """Zeigt die Gestaltung als Vorschau und öffnet zum Ändern ein Popup.

    ``changed(dict)`` meldet die geänderten Angaben, ``reset`` die Rückkehr zur Darstellung nach Typ.
    """

    changed = pyqtSignal(dict)
    reset = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._style = ObjectStyle("#c62828")
        self._kind = KIND_ARROW
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(_ICON_PX, _ICON_PX))

        self._menu = QMenu(self)
        self._editor = _ObjectStyleEditor(self._menu)
        self._editor.changed.connect(self._apply)
        self._editor.reset_requested.connect(self._on_reset)
        self._editor.custom_color_requested.connect(self._on_custom_color)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(self._editor)
        self._menu.addAction(action)
        self.setMenu(self._menu)
        self._update_face()

    def set_style(self, style: ObjectStyle, kind: str):
        """Anzeigen, ohne ``changed`` auszulösen."""
        self._style = style
        if kind != self._kind:
            self._kind = kind
            self._editor.set_kind(kind)
        self._editor.set_style(style)
        self._update_face()

    def _apply(self, changes: dict):
        self._style = self._style.updated(**changes)
        self._update_face()
        self.changed.emit(changes)

    def _on_reset(self):
        self._menu.close()
        self.reset.emit()

    def _on_custom_color(self):
        # Ein modaler Dialog aus dem Popup heraus schlösse es ohnehin – also erst schließen
        self._menu.close()
        QTimer.singleShot(0, self._pick_custom_color)

    def _pick_custom_color(self):
        color = QColorDialog.getColor(QColor(self._style.color), self, "Farbe")
        if not color.isValid():
            return
        changes = {"color": color.name()}
        self._editor.set_style(self._style.updated(**changes))
        self._apply(changes)

    def _update_face(self):
        self.setIcon(object_style_icon(self._style, self._kind))
        self.setText(describe(self._style, self._kind))
        if self._kind == KIND_ARROW:
            self.setToolTip("Gestaltung: Farbe, Deckkraft, Linienbreite und Pfeilspitzen")
        else:
            self.setToolTip("Gestaltung: Farbe und Deckkraft")
