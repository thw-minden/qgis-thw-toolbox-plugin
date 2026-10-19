"""Gestaltung eines Gebiets im Popup: Muster bzw. Schraffur, Farbe, Deckkraft und Rand (Farbe, Breite, Linie).

Aufbau wie beim Hintergrund der taktischen Zeichen (``background_popup``): eine Schaltfläche
mit Vorschau, das Popup ändert sofort. Gemeldet werden nur die geänderten Angaben, damit bei
mehreren Gebieten jedes seine übrigen Einstellungen (z. B. die eigene Farbe) behält.
"""

from qgis.PyQt.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap
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

from ..planning.zone_style import (
    MAX_STROKE_WIDTH,
    MIN_OPACITY,
    MIN_STROKE_WIDTH,
    PATTERN_NONE,
    PATTERNS,
    STROKE_STYLES,
    ZoneStyle,
)
from .theme import muted_color, muted_style

_COLORS = (
    ("Rot", "#c62828"),
    ("Orange", "#ef6c00"),
    ("Gelb", "#fbc02d"),
    ("Grün", "#2e7d32"),
    ("Blau", "#1565c0"),
    ("Violett", "#6a1b9a"),
    ("Grau", "#616161"),
    ("Schwarz", "#000000"),
)
_ICON_PX = 20
# Vorschau des Musters: Qt-Pinsel, die der Darstellung auf der Karte entsprechen
_BRUSHES = {
    "voll": Qt.BrushStyle.SolidPattern,
    "schraeg": Qt.BrushStyle.BDiagPattern,
    "schraeg_links": Qt.BrushStyle.FDiagPattern,
    "kreuz": Qt.BrushStyle.DiagCrossPattern,
    "waagerecht": Qt.BrushStyle.HorPattern,
    "gitter": Qt.BrushStyle.CrossPattern,
    "punkte": Qt.BrushStyle.Dense6Pattern,
    PATTERN_NONE: Qt.BrushStyle.NoBrush,
}
_PEN_STYLES = {"solid": Qt.PenStyle.SolidLine, "dash": Qt.PenStyle.DashLine, "dot": Qt.PenStyle.DotLine}


def zone_style_icon(style: ZoneStyle, px: int = _ICON_PX) -> QIcon:
    """Vorschau als Icon: Muster in der Füllfarbe, darum der Rand."""
    pixmap = QPixmap(px, px)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(2, 2, px - 4, px - 4)
    fill = QColor(style.fill)
    # Flächen in der Vorschau etwas kräftiger, sonst sind blasse Füllungen nicht zu erkennen
    fill.setAlphaF(max(style.opacity, 0.35) if style.pattern == "voll" else 1.0)
    painter.setBrush(QBrush(fill, _BRUSHES.get(style.pattern, Qt.BrushStyle.SolidPattern)))
    if style.stroke:
        painter.setPen(QPen(QColor(style.stroke), 1.6, _PEN_STYLES.get(style.stroke_style, Qt.PenStyle.SolidLine)))
    else:
        painter.setPen(QPen(QColor(muted_color()), 1, Qt.PenStyle.DotLine))
    painter.drawRect(rect)
    painter.end()
    return QIcon(pixmap)


def _swatch_icon(color: str, px: int = _ICON_PX) -> QIcon:
    if not color:
        # „Ohne Rand“: durchgestrichener, gestrichelter Rahmen
        return zone_style_icon(ZoneStyle("#ffffff", pattern=PATTERN_NONE), px)
    pixmap = QPixmap(px, px)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(QPen(QColor(muted_color()), 1))
    painter.drawRoundedRect(QRectF(2, 2, px - 4, px - 4), 3, 3)
    painter.end()
    return QIcon(pixmap)


def _line_icon(stroke_style: str, px: int = _ICON_PX) -> QIcon:
    pixmap = QPixmap(px, px)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setPen(QPen(QColor(muted_color()).lighter(160), 2, _PEN_STYLES[stroke_style]))
    painter.drawLine(2, px // 2, px - 2, px // 2)
    painter.end()
    return QIcon(pixmap)


def describe(style: ZoneStyle) -> str:
    """Kurzer Text für die Schaltfläche, z. B. „Schraffur ╱, Rot“."""
    color = QColor(style.fill)
    name = next((label for label, hex_color in _COLORS if QColor(hex_color) == color), style.fill)
    if style.pattern == PATTERN_NONE:
        return "Nur Rand" if style.stroke else "Unsichtbar"
    return f"{PATTERNS[style.pattern]}, {name}"


def _tool_button(icon: QIcon, tooltip: str) -> QToolButton:
    button = QToolButton()
    button.setIcon(icon)
    button.setIconSize(QSize(_ICON_PX, _ICON_PX))
    button.setToolTip(tooltip)
    button.setCheckable(True)
    button.setAutoRaise(True)
    return button


class _ZoneStyleEditor(QWidget):
    """Inhalt des Popups; jede Änderung meldet sofort die geänderten Angaben."""

    changed = pyqtSignal(dict)
    reset_requested = pyqtSignal()
    custom_color_requested = pyqtSignal(str)  # "fill" oder "stroke"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._style = ZoneStyle("#1565c0")
        grid = QGridLayout(self)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.addWidget(QLabel("<b>Gestaltung des Gebiets</b>"), 0, 0, 1, 2)

        self._patterns = self._button_row(
            grid,
            1,
            "Muster",
            [(key, name, zone_style_icon(ZoneStyle("#616161", 1.0, key, "#616161"))) for key, name in PATTERNS.items()],
            "pattern",
        )
        self._fills = self._button_row(
            grid, 2, "Farbe", [(h, n, _swatch_icon(h)) for n, h in _COLORS], "fill", custom="fill"
        )

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
        grid.addWidget(QLabel("Deckkraft"), 3, 0)
        grid.addLayout(opacity_row, 3, 1)

        self._strokes = self._button_row(
            grid,
            4,
            "Rand",
            [("", "Ohne Rand", _swatch_icon(""))] + [(h, n, _swatch_icon(h)) for n, h in _COLORS],
            "stroke",
            custom="stroke",
        )

        line_row = QHBoxLayout()
        self._width = QDoubleSpinBox()
        self._width.setRange(MIN_STROKE_WIDTH, MAX_STROKE_WIDTH)
        self._width.setSingleStep(0.2)
        self._width.setDecimals(1)
        self._width.setSuffix(" mm")
        self._width.setKeyboardTracking(False)
        self._width.setToolTip("Breite des Rands")
        self._width.valueChanged.connect(lambda value: self._emit(stroke_width=round(value, 1)))
        line_row.addWidget(self._width)
        self._line_styles = QButtonGroup(self)
        for key, name in STROKE_STYLES.items():
            button = _tool_button(_line_icon(key), name)
            button.setProperty("value", key)
            button.clicked.connect(lambda _c=False, k=key: self._emit(stroke_style=k))
            self._line_styles.addButton(button)
            line_row.addWidget(button)
        line_row.addStretch()
        grid.addWidget(QLabel("Linie"), 5, 0)
        grid.addLayout(line_row, 5, 1)

        reset = QPushButton("Wie Typ")
        reset.setToolTip("Eigene Gestaltung entfernen: wieder Farbe und Darstellung des Gebietstyps")
        reset.clicked.connect(self.reset_requested)
        grid.addWidget(reset, 6, 1, Qt.AlignmentFlag.AlignLeft)

    def _button_row(self, grid, row, title, entries, key, custom: str | None = None) -> QButtonGroup:
        group = QButtonGroup(self)
        layout = QHBoxLayout()
        layout.setSpacing(1)
        for value, name, icon in entries:
            button = _tool_button(icon, name)
            button.setProperty("value", value)
            button.clicked.connect(lambda _c=False, v=value: self._emit(**{key: v}))
            group.addButton(button)
            layout.addWidget(button)
        if custom:
            more = QToolButton()
            more.setText("…")
            more.setToolTip("Eigene Farbe …")
            more.setAutoRaise(True)
            more.clicked.connect(lambda: self.custom_color_requested.emit(custom))
            layout.addWidget(more)
        layout.addStretch()
        grid.addWidget(QLabel(title), row, 0)
        grid.addLayout(layout, row, 1)
        return group

    def set_style(self, style: ZoneStyle):
        self._style = style
        for group, value in (
            (self._patterns, style.pattern),
            (self._fills, QColor(style.fill).name()),
            (self._strokes, style.stroke),
            (self._line_styles, style.stroke_style),
        ):
            # Eigene Farben passen zu keinem Feld: dann ist keins markiert
            group.setExclusive(False)
            for button in group.buttons():
                button.setChecked(button.property("value") == value)
            group.setExclusive(True)
        for widget, value in ((self._opacity, round(style.opacity * 100)), (self._width, style.stroke_width)):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self._opacity_label.setText(f"{self._opacity.value()} %")
        # Rand-Einstellungen nur, wenn es einen Rand gibt
        self._width.setEnabled(bool(style.stroke))
        for button in self._line_styles.buttons():
            button.setEnabled(bool(style.stroke))

    def _on_opacity_moved(self, value: int):
        self._opacity_label.setText(f"{value} %")
        if not self._opacity.isSliderDown():
            self._emit(opacity=value / 100)

    def _emit(self, **changes):
        style = self._style.updated(**changes)
        if style != self._style:
            self.set_style(style)
            self.changed.emit(changes)


class ZoneStyleButton(QToolButton):
    """Zeigt die Gestaltung als Vorschau und öffnet zum Ändern ein Popup.

    ``changed(dict)`` meldet die geänderten Angaben, ``reset`` die Rückkehr zur Darstellung nach Typ.
    """

    changed = pyqtSignal(dict)
    reset = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._style = ZoneStyle("#1565c0")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.setToolTip("Gestaltung: Muster bzw. Schraffur, Farbe, Deckkraft und Rand")

        self._menu = QMenu(self)
        self._editor = _ZoneStyleEditor(self._menu)
        self._editor.changed.connect(self._apply)
        self._editor.reset_requested.connect(self._on_reset)
        self._editor.custom_color_requested.connect(self._on_custom_color)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(self._editor)
        self._menu.addAction(action)
        self.setMenu(self._menu)
        self._update_face()

    def set_style(self, style: ZoneStyle):
        """Anzeigen, ohne ``changed`` auszulösen."""
        self._style = style
        self._editor.set_style(style)
        self._update_face()

    def _apply(self, changes: dict):
        self._style = self._style.updated(**changes)
        self._update_face()
        self.changed.emit(changes)

    def _on_reset(self):
        self._menu.close()
        self.reset.emit()

    def _on_custom_color(self, key: str):
        # Ein modaler Dialog aus dem Popup heraus schlösse es ohnehin – also erst schließen
        self._menu.close()
        QTimer.singleShot(0, lambda: self._pick_custom_color(key))

    def _pick_custom_color(self, key: str):
        current = getattr(self._style, key) or self._style.fill
        title = "Füllfarbe" if key == "fill" else "Randfarbe"
        color = QColorDialog.getColor(QColor(current), self, title)
        if not color.isValid():
            return
        changes = {key: color.name()}
        self._editor.set_style(self._style.updated(**changes))
        self._apply(changes)

    def _update_face(self):
        self.setIcon(zone_style_icon(self._style))
        self.setText(describe(self._style))
