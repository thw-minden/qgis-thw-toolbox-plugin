"""Hintergrund eines taktischen Zeichens im Popup wählen: Form, Farbe, Deckkraft, Rand und Größe."""

from dataclasses import replace

from qgis.PyQt.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSlider,
    QSpinBox,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from ..layer.background import MAX_SCALE, MIN_OPACITY, MIN_SCALE, SHAPES, Background
from .theme import muted_color, muted_style

_SWATCHES = (
    ("Weiß", "#ffffff"),
    ("Hellgrau", "#e0e0e0"),
    ("Gelb", "#fff176"),
    ("Hellblau", "#bbdefb"),
    ("Hellgrün", "#c8e6c9"),
    ("Hellrot", "#ffcdd2"),
)
_STROKES = (("Ohne Rand", ""), ("Schwarz", "#000000"), ("Blau", "#003399"), ("Rot", "#c62828"))
_ICON_PX = 20
# Form, mit der eine Farbwahl startet, solange noch kein Hintergrund gesetzt ist
_DEFAULT_SHAPE = "circle"


def _draw_shape(painter: QPainter, shape: str, rect: QRectF):
    if shape == "circle":
        painter.drawEllipse(rect)
    elif shape == "rounded":
        painter.drawRoundedRect(rect, rect.width() * 0.25, rect.height() * 0.25)
    elif shape == "diamond":
        c = rect.center()
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(c.x(), rect.top()),
                    QPointF(rect.right(), c.y()),
                    QPointF(c.x(), rect.bottom()),
                    QPointF(rect.left(), c.y()),
                ]
            )
        )
    else:
        painter.drawRect(rect)


def background_icon(background: Background, px: int = _ICON_PX) -> QIcon:
    """Vorschau als Icon; „Ohne“ als gestrichelter, durchgestrichener Rahmen."""
    pixmap = QPixmap(px, px)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(2, 2, px - 4, px - 4)
    muted = QColor(muted_color())
    if not background.visible:
        painter.setPen(QPen(muted, 1.2, Qt.PenStyle.DashLine))
        painter.drawRect(rect)
        painter.drawLine(rect.bottomLeft(), rect.topRight())
    else:
        fill = QColor(background.color)
        fill.setAlphaF(background.opacity)
        painter.setBrush(fill)
        # Ohne Rand eine feine Linie, damit Weiß auch auf hellem Grund erkennbar bleibt
        painter.setPen(QPen(QColor(background.stroke), 2) if background.stroke else QPen(muted, 1))
        _draw_shape(painter, background.shape, rect)
    painter.end()
    return QIcon(pixmap)


def describe(background: Background) -> str:
    """Kurzer Text für die Schaltfläche, z. B. „Kreis, Weiß“."""
    if not background.visible:
        return "Ohne"
    color = QColor(background.color)
    name = next((label for label, hex_color in _SWATCHES if QColor(hex_color) == color), background.color)
    return f"{SHAPES[background.shape]}, {name}"


def _tool_button(icon: QIcon, tooltip: str) -> QToolButton:
    button = QToolButton()
    button.setIcon(icon)
    button.setIconSize(QSize(_ICON_PX, _ICON_PX))
    button.setToolTip(tooltip)
    button.setCheckable(True)
    button.setAutoRaise(True)
    return button


class _BackgroundEditor(QWidget):
    """Inhalt des Popups; jede Änderung meldet sofort einen neuen ``Background``."""

    changed = pyqtSignal(object)
    custom_color_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._background = Background()
        grid = QGridLayout(self)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.addWidget(QLabel("<b>Hintergrund des Zeichens</b>"), 0, 0, 1, 2)

        self._shapes = QButtonGroup(self)
        shapes_row = QHBoxLayout()
        for shape, name in SHAPES.items():
            button = _tool_button(background_icon(Background(shape)), name)
            self._shapes.addButton(button)
            button.clicked.connect(lambda _c=False, s=shape: self._emit(shape=s))
            button.setProperty("shape", shape)
            shapes_row.addWidget(button)
        shapes_row.addStretch()
        grid.addWidget(QLabel("Form"), 1, 0)
        grid.addLayout(shapes_row, 1, 1)

        self._colors = QButtonGroup(self)
        colors_row = QHBoxLayout()
        for name, hex_color in _SWATCHES:
            button = _tool_button(background_icon(Background("square", hex_color)), name)
            self._colors.addButton(button)
            button.clicked.connect(lambda _c=False, h=hex_color: self._emit(color=h))
            button.setProperty("color", hex_color)
            colors_row.addWidget(button)
        custom = QToolButton()
        custom.setText("…")
        custom.setToolTip("Eigene Farbe …")
        custom.setAutoRaise(True)
        custom.clicked.connect(self.custom_color_requested)
        colors_row.addWidget(custom)
        colors_row.addStretch()
        grid.addWidget(QLabel("Farbe"), 2, 0)
        grid.addLayout(colors_row, 2, 1)

        opacity_row = QHBoxLayout()
        self._opacity = QSlider(Qt.Orientation.Horizontal)
        self._opacity.setRange(round(MIN_OPACITY * 100), 100)
        self._opacity.setSingleStep(5)
        self._opacity.setPageStep(10)
        self._opacity_label = QLabel()
        self._opacity_label.setMinimumWidth(36)
        self._opacity_label.setStyleSheet(muted_style())
        self._opacity.valueChanged.connect(self._on_opacity_moved)
        # Beim Ziehen erst beim Loslassen anwenden – jede Änderung baut die Darstellung neu auf
        self._opacity.sliderReleased.connect(lambda: self._emit(opacity=self._opacity.value() / 100))
        opacity_row.addWidget(self._opacity, 1)
        opacity_row.addWidget(self._opacity_label)
        grid.addWidget(QLabel("Deckkraft"), 3, 0)
        grid.addLayout(opacity_row, 3, 1)

        self._strokes = QButtonGroup(self)
        strokes_row = QHBoxLayout()
        for name, hex_color in _STROKES:
            preview = Background("square", "#ffffff", stroke=hex_color) if hex_color else Background()
            button = _tool_button(background_icon(preview), name)
            self._strokes.addButton(button)
            button.clicked.connect(lambda _c=False, h=hex_color: self._emit(stroke=h))
            button.setProperty("stroke", hex_color)
            strokes_row.addWidget(button)
        strokes_row.addStretch()
        grid.addWidget(QLabel("Rand"), 4, 0)
        grid.addLayout(strokes_row, 4, 1)

        self._scale = QSpinBox()
        self._scale.setRange(round(MIN_SCALE * 100), round(MAX_SCALE * 100))
        self._scale.setSingleStep(10)
        self._scale.setSuffix(" %")
        self._scale.setToolTip("Größe des Hintergrunds im Verhältnis zum Zeichen")
        self._scale.setKeyboardTracking(False)
        self._scale.valueChanged.connect(lambda value: self._emit(scale=value / 100))
        grid.addWidget(QLabel("Größe"), 5, 0)
        grid.addWidget(self._scale, 5, 1, Qt.AlignmentFlag.AlignLeft)

        hint = QLabel("Farbe, Rand oder Größe ohne Form gewählt: dann gibt es einen Kreis.")
        hint.setWordWrap(True)
        hint.setStyleSheet(muted_style())
        grid.addWidget(hint, 6, 0, 1, 2)

    def set_background(self, background: Background):
        self._background = background
        for group, key, value in (
            (self._shapes, "shape", background.shape),
            (self._colors, "color", QColor(background.color).name()),
            (self._strokes, "stroke", background.stroke),
        ):
            # Eigene Farben passen zu keinem Feld: dann ist keins markiert
            group.setExclusive(False)
            for button in group.buttons():
                button.setChecked(button.property(key) == value)
            group.setExclusive(True)
        for widget, value in (
            (self._opacity, round(background.opacity * 100)),
            (self._scale, round(background.scale * 100)),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self._opacity_label.setText(f"{self._opacity.value()} %")

    def _on_opacity_moved(self, value: int):
        self._opacity_label.setText(f"{value} %")
        if not self._opacity.isSliderDown():
            self._emit(opacity=value / 100)

    def _emit(self, **changes):
        if "shape" not in changes and not self._background.visible:
            changes["shape"] = _DEFAULT_SHAPE
        background = replace(self._background, **changes)
        if background != self._background:
            self.set_background(background)
            self.changed.emit(background)


class BackgroundButton(QToolButton):
    """Zeigt den Hintergrund als Vorschau und öffnet zum Ändern ein Popup."""

    changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._background = Background()
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.setToolTip("Hintergrund des Zeichens: Form, Farbe, Deckkraft, Rand und Größe")

        self._menu = QMenu(self)
        self._editor = _BackgroundEditor(self._menu)
        self._editor.changed.connect(self._apply)
        self._editor.custom_color_requested.connect(self._on_custom_color)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(self._editor)
        self._menu.addAction(action)
        self.setMenu(self._menu)
        self._update_face()

    def background(self) -> Background:
        return self._background

    def set_background(self, background: Background):
        """Anzeigen, ohne ``changed`` auszulösen."""
        self._background = background
        self._editor.set_background(background)
        self._update_face()

    def _apply(self, background: Background):
        if background == self._background:
            return
        self._background = background
        self._update_face()
        self.changed.emit(background)

    def _on_custom_color(self):
        # Ein modaler Dialog aus dem Popup heraus schlösse es ohnehin – also erst schließen
        self._menu.close()
        QTimer.singleShot(0, self._pick_custom_color)

    def _pick_custom_color(self):
        color = QColorDialog.getColor(QColor(self._background.color), self, "Hintergrundfarbe")
        if not color.isValid():
            return
        shape = self._background.shape if self._background.visible else _DEFAULT_SHAPE
        background = replace(self._background, shape=shape, color=color.name())
        self._editor.set_background(background)
        self._apply(background)

    def _update_face(self):
        self.setIcon(background_icon(self._background))
        self.setText(describe(self._background))
