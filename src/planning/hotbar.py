"""Hotbar: schwebende Werkzeugleiste unten mittig auf der Karte (Figma-Stil).

Gilt für taktische Zeichen und Objektplanung gemeinsam – der Cursor wählt beides aus.
"""

import math
import os
from dataclasses import dataclass

from qgis.PyQt.QtCore import QEvent, QMimeData, QObject, QPoint, QRect, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QDrag, QIcon, QPainter
from qgis.PyQt.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSpinBox,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from ..ui.symbol_picker import POPUP_TOOL_MODE, SymbolPickerPopup, symbol_catalog, symbol_prefs
from .layers import (
    ROLE_ARROWS,
    ROLE_DISTRIBUTORS,
    ROLE_GENERATORS,
    ROLE_LIGHTS,
    ROLE_TENTS,
    ROLE_VEHICLES,
    ROLE_ZONES,
)

_ICON_PX = 22
_BUTTON_PX = 36
_MARKER_ICON_PX = 28
_MARGIN_BOTTOM_PX = 16
# Mindestabstand der Hotbar zum linken/rechten Kartenrand
_MARGIN_SIDE_PX = 16
_GRID_PRESETS = ((1, 2), (2, 2), (2, 3), (3, 3), (2, 5), (4, 4))

_STYLE = """
QFrame#ObjektplanungHotbar {
    background: #1e1e1e;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 12px;
}
QFrame#ObjektplanungHotbar QToolButton {
    background: transparent;
    border: none;
    border-radius: 8px;
    color: #eef2f6;
    padding: 0px;
}
QFrame#ObjektplanungHotbar QToolButton:hover { background: #333333; }
QFrame#ObjektplanungHotbar QToolButton:checked { background: #0d99ff; }
QFrame#ObjektplanungHotbar QToolButton#chevron {
    color: #9aa4ae;
    font-size: 12px;
    border-radius: 4px;
}
QFrame#ObjektplanungHotbar QToolButton#chevron:hover { color: #ffffff; background: #333333; }
QFrame#ObjektplanungHotbar QToolButton#grid { padding: 0px 6px; font-weight: bold; }
QFrame#ObjektplanungHotbar QToolButton#grid[armed="true"] { background: #0d99ff; }
QFrame#ObjektplanungHotbar QFrame#sep { background: #3a3a3a; }
QFrame#ObjektplanungHotbar QToolButton#marker {
    background: #eef2f6;
    border: 2px solid transparent;
    border-radius: 8px;
}
QFrame#ObjektplanungHotbar QToolButton#marker:hover { background: #ffffff; }
QFrame#ObjektplanungHotbar QToolButton#marker:checked { background: #ffffff; border-color: #0d99ff; }
"""


@dataclass
class _ToolEntry:
    kind: str
    title: str
    shortcut: str
    button: QToolButton
    menu: QMenu | None


def _event_pos(event) -> QPoint:
    return event.position().toPoint() if hasattr(event, "position") else event.pos()


class _MarkerButton(QToolButton):
    """Ein taktisches Zeichen in der Hotbar: Klick nimmt es in die Hand, Ziehen legt es auf die Karte.

    Die Ziffer oben links ist die Taste, mit der es sich auch ohne Maus greifen lässt.
    """

    # Zeichen wurde per Drag & Drop abgelegt
    dropped = pyqtSignal(str)

    def __init__(self, slot: int, path: str, icon: QIcon, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.path = path
        self._press_pos: QPoint | None = None
        self.setObjectName("marker")
        self.setIcon(icon)
        self.setIconSize(QSize(_MARKER_ICON_PX, _MARKER_ICON_PX))
        self.setFixedSize(_BUTTON_PX, _BUTTON_PX)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = _event_pos(event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if (_event_pos(event) - self._press_pos).manhattanLength() >= QApplication.startDragDistance():
                # Ziehen statt Klick: der Button bleibt oben, ``clicked`` kommt nicht
                self._press_pos = None
                self.setDown(False)
                self._drag()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def _drag(self):
        mime = QMimeData()
        mime.setText(self.path)
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.icon().pixmap(48, 48))
        drag.setHotSpot(QPoint(24, 24))
        if drag.exec(Qt.DropAction.CopyAction) == Qt.DropAction.CopyAction:
            self.dropped.emit(self.path)

    def paintEvent(self, event):
        super().paintEvent(event)
        # Tasten-Badge unten rechts, dunkel hinterlegt – lesbar auf jedem Zeichen
        badge = QRect(self.width() - 14, self.height() - 14, 12, 12)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(30, 30, 30, 215))
        painter.drawRoundedRect(badge, 3, 3)
        font = painter.font()
        font.setPixelSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(self.slot))
        painter.end()


class PlanningHotbar(QFrame):
    """Werkzeuge mit einem Klick; Typ-Auswahl über das kleine ▾ daneben.

    Liegt als Kind-Widget über der Karte und hält sich bei Größenänderung
    unten mittig. Auswahl-Aktionen (Duplizieren, Drehen, Löschen) erscheinen
    nur, wenn etwas ausgewählt ist – wie die Kontext-Leiste in Figma.

    Gleich nach der Auswahl liegen die taktischen Zeichen: die Favoriten aus
    dem Zeichen-Picker (ohne Favoriten die zuletzt verwendeten) auf den
    Tasten 1–9, dahinter „Alle Zeichen …“. Ist die Karte zu schmal, fallen
    hintere Zeichen weg – ihre Tasten gelten weiter.
    """

    def __init__(self, controller, plugin_dir: str, canvas):
        super().__init__(canvas)
        self.setObjectName("ObjektplanungHotbar")
        self.setStyleSheet(_STYLE)
        self.controller = controller
        self.canvas = canvas
        self._plugin_dir = plugin_dir
        self._icon_dir = os.path.join(plugin_dir, "icons", "planning")
        self._tools: list[_ToolEntry] = []
        self._marker_buttons: list[_MarkerButton] = []
        self._prefs = symbol_prefs(plugin_dir)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 120))
        self.setGraphicsEffect(shadow)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 6, 8, 6)
        self._layout.setSpacing(2)
        catalog = controller.catalog

        self._add_tool("select", "select.svg", "Auswählen und bearbeiten – taktische Zeichen und Objekte", "")
        self._separator()
        # Taktische Zeichen: die Buttons der Favoriten setzt refresh_markers() davor
        self._all_symbols = self._button("symbols.svg", "", self._open_symbol_picker)
        self._all_symbols.setCheckable(True)
        self._separator()
        self._add_tool("tent", "tent.svg", "Zelt platzieren", "", [(t.id, t.name) for t in catalog.zelte])
        self._add_tool("vehicle", "vehicle.svg", "Fahrzeug platzieren", "", [(v.id, v.name) for v in catalog.fahrzeuge])
        self._add_grid()
        self._separator()
        self._add_tool(
            "cable", "cable.svg", "Leitungsroller verlegen", "", [(r.id, r.name) for r in catalog.leitungsroller]
        )
        self._add_tool(
            "distributor", "distributor.svg", "Verteiler setzen", "", [(d.id, d.name) for d in catalog.verteiler]
        )
        self._add_tool(
            "generator", "generator.svg", "Stromerzeuger setzen", "", [(g.id, g.name) for g in catalog.stromerzeuger]
        )
        self._add_tool("light", "light.svg", "Beleuchtung setzen", "", [(li.id, li.name) for li in catalog.beleuchtung])
        self._separator()
        self._add_tool("zone", "zone.svg", "Gebiet zeichnen", "", [(z.id, z.name) for z in catalog.gebiete])
        self._add_tool("arrow", "arrow.svg", "Pfeil zeichnen", "", [(a.id, a.name) for a in catalog.pfeile])
        self._separator()
        self._guides = self._button("guides.svg", "Hilfslinien: an Nachbarn ausrichten (Strg beim Ziehen hält an)")
        self._guides.setCheckable(True)
        self._guides.setChecked(controller.align_enabled)
        self._guides.toggled.connect(controller.set_align_enabled)

        # Kontext-Aktionen für die Auswahl
        self._selection_sep = self._separator()
        self._vertices = self._button("vertices.svg", "Punkte bearbeiten: Pfeil umformen und ergänzen (Doppelklick)")
        self._vertices.setCheckable(True)
        self._vertices.clicked.connect(self._on_vertices)
        self._duplicate = self._button("duplicate.svg", "Duplizieren (D, Alt+Ziehen)", controller.duplicate_selection)
        self._rotate = self._button(
            "rotate.svg", "Um 15° drehen (R, Shift+R zurück)", lambda: controller.rotate_selection(15)
        )
        self._delete = self._button("delete.svg", "Löschen (Entf)", controller.delete_selection)
        self._selection_widgets = (self._selection_sep, self._duplicate, self._rotate, self._delete)

        controller.state_changed.connect(self.sync_tool_buttons)
        self._prefs.changed.connect(self.refresh_markers)
        canvas.installEventFilter(self)
        self.refresh_markers()
        self.refresh_selection()

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------

    def _icon(self, name: str) -> QIcon:
        return QIcon(os.path.join(self._icon_dir, name))

    def _separator(self) -> QFrame:
        sep = QFrame(self)
        sep.setObjectName("sep")
        sep.setFixedSize(1, 24)
        wrapper = QWidget(self)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.addWidget(sep)
        self._layout.addWidget(wrapper)
        return wrapper

    def _button(self, icon: str, tooltip: str, slot=None) -> QToolButton:
        button = QToolButton(self)
        button.setIcon(self._icon(icon))
        button.setIconSize(QSize(_ICON_PX, _ICON_PX))
        button.setFixedSize(_BUTTON_PX, _BUTTON_PX)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if slot is not None:
            button.clicked.connect(lambda _checked=False: slot())
        self._layout.addWidget(button)
        return button

    def _add_tool(self, kind: str, icon: str, title: str, shortcut: str, types: list[tuple[str, str]] | None = None):
        button = self._button(icon, title)
        button.setCheckable(True)
        button.clicked.connect(lambda checked, k=kind: self._on_tool(checked, k))
        menu = None
        if types and len(types) > 1:
            menu = QMenu(self)
            for type_id, name in types:
                item = menu.addAction(name)
                item.setCheckable(True)
                item.setData(type_id)
                item.triggered.connect(lambda _checked=False, k=kind, t=type_id: self.controller.activate_tool(k, t))
            chevron = QToolButton(self)
            chevron.setObjectName("chevron")
            chevron.setText("▾")
            chevron.setFixedSize(14, _BUTTON_PX)
            chevron.setToolTip(f"{title}: Typ wählen")
            chevron.setCursor(Qt.CursorShape.PointingHandCursor)
            chevron.setMenu(menu)
            chevron.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            # Kein Standard-Menüpfeil, das ▾ ist der Pfeil
            chevron.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")
            self._layout.addWidget(chevron)
        self._tools.append(_ToolEntry(kind, title, shortcut, button, menu))

    def _add_grid(self):
        button = QToolButton(self)
        button.setObjectName("grid")
        button.setIcon(self._icon("grid.svg"))
        button.setIconSize(QSize(_ICON_PX, _ICON_PX))
        button.setFixedHeight(_BUTTON_PX)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setToolTip(
            "Mehrere auf einmal: der nächste Klick setzt ein Raster aus Reihen × Spalten "
            "(Abstand = Mindestabstand), danach wieder einzeln"
        )
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")

        menu = QMenu(self)

        def choose(rows: int, cols: int):
            self.controller.set_grid(rows, cols)
            menu.close()

        panel = QWidget(menu)
        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 8, 10, 8)
        title = QLabel("<b>Mehrere auf einmal setzen</b><br>Der nächste Klick setzt ein Raster, danach wieder einzeln.")
        grid.addWidget(title, 0, 0, 1, 3)
        single = QPushButton("Einzeln setzen")
        single.clicked.connect(lambda _c=False: choose(1, 1))
        grid.addWidget(single, 1, 0, 1, 3)
        presets = QHBoxLayout()
        for rows, cols in _GRID_PRESETS:
            preset = QPushButton(f"{rows}×{cols}")
            preset.setFixedWidth(40)
            preset.clicked.connect(lambda _c=False, r=rows, c=cols: choose(r, c))
            presets.addWidget(preset)
        grid.addLayout(presets, 2, 0, 1, 3)
        grid.addWidget(QLabel("Reihen"), 3, 0)
        grid.addWidget(QLabel("Spalten"), 3, 2)
        self._rows = QSpinBox()
        self._cols = QSpinBox()
        for spin in (self._rows, self._cols):
            spin.setRange(1, 20)
            spin.valueChanged.connect(lambda _v: self.controller.set_grid(self._rows.value(), self._cols.value()))
        grid.addWidget(self._rows, 4, 0)
        grid.addWidget(QLabel("×"), 4, 1)
        grid.addWidget(self._cols, 4, 2)
        action = QWidgetAction(menu)
        action.setDefaultWidget(panel)
        menu.addAction(action)
        button.setMenu(menu)
        self._grid_button = button
        self._layout.addWidget(button)

    # ------------------------------------------------------------------
    # Position über der Karte
    # ------------------------------------------------------------------

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.canvas and event.type() == QEvent.Type.Resize:
            self._reposition()
        return False

    def _fit_markers(self):
        """So viele Zeichen-Buttons zeigen, wie auf die Karte passen – hintere fallen zuerst weg."""
        buttons = self._marker_buttons
        if not buttons:
            return
        step = _BUTTON_PX + self._layout.spacing()
        self._layout.invalidate()
        hidden = sum(1 for button in buttons if button.isHidden())
        full = self._layout.sizeHint().width() + hidden * step
        excess = full - (self.canvas.width() - 2 * _MARGIN_SIDE_PX)
        drop = min(len(buttons), max(0, math.ceil(excess / step)))
        for i, button in enumerate(buttons):
            wanted = i < len(buttons) - drop
            if button.isHidden() == wanted:
                button.setVisible(wanted)

    def _reposition(self):
        self._fit_markers()
        self.adjustSize()
        x = max(0, (self.canvas.width() - self.width()) // 2)
        y = max(0, self.canvas.height() - self.height() - _MARGIN_BOTTOM_PX)
        self.move(x, y)
        self.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        self._reposition()

    def dispose(self):
        try:
            self.canvas.removeEventFilter(self)
        except RuntimeError:
            pass
        try:
            self.controller.state_changed.disconnect(self.sync_tool_buttons)
        except (TypeError, RuntimeError):
            pass
        try:
            self._prefs.changed.disconnect(self.refresh_markers)
        except (TypeError, RuntimeError):
            pass
        self.hide()
        self.setParent(None)
        self.deleteLater()

    # ------------------------------------------------------------------
    # Zustand
    # ------------------------------------------------------------------

    def _on_tool(self, checked: bool, kind: str):
        if checked:
            self.controller.activate_tool(kind)
        else:
            self.controller.back_to_select()

    def refresh_markers(self):
        """Zeichen-Buttons nach Favoriten bzw. Verlauf neu aufbauen (bei jeder Änderung im Zeichen-Picker)."""
        for button in self._marker_buttons:
            self._layout.removeWidget(button)
            button.hide()
            button.deleteLater()
        self._marker_buttons = []
        symbols, favorites = self.controller.marker_slots()
        catalog = symbol_catalog(self._plugin_dir)
        source = "Favorit" if favorites else "Zuletzt verwendet"
        base = self._layout.indexOf(self._all_symbols)
        for i, symbol in enumerate(symbols):
            button = _MarkerButton(i + 1, symbol.path, catalog.icon(symbol.path), self)
            button.setToolTip(
                f"{symbol.label}\n{source} · Taste {i + 1}\nKlick: auf die Karte setzen · Ziehen: direkt ablegen"
            )
            button.clicked.connect(lambda checked, p=symbol.path: self._on_marker(checked, p))
            button.dropped.connect(self._prefs.add_recent)
            self._layout.insertWidget(base + i, button)
            self._marker_buttons.append(button)
        tip = "Alle taktischen Zeichen: wählen, dann auf die Karte klicken"
        if not favorites:
            tip += "\nMit dem Stern im Zeichen-Picker gemerkte Favoriten liegen hier auf den Tasten 1–9"
        self._all_symbols.setToolTip(tip)
        self.sync_tool_buttons()

    def _on_marker(self, checked: bool, path: str):
        if checked:
            self.controller.activate_tool("marker", path)
        else:
            self.controller.back_to_select()

    def _open_symbol_picker(self):
        """„Alle Zeichen …“: Picker über dem Button; das gewählte Zeichen setzt der nächste Klick auf die Karte."""
        path = SymbolPickerPopup.pick(
            symbol_catalog(self._plugin_dir),
            self._all_symbols.mapToGlobal(QPoint(0, 0)),
            self.canvas,
            "Taktisches Zeichen setzen",
            POPUP_TOOL_MODE,
            above=True,
        )
        if path:
            self.controller.activate_tool("marker", path)
        self.sync_tool_buttons()

    def _on_vertices(self, checked: bool):
        if checked:
            self.controller.edit_vertices()
        else:
            self.controller.back_to_select()
        self.sync_tool_buttons()

    def _current_type(self, kind: str) -> str | None:
        c = self.controller
        return {
            "tent": c.selected_ids.get(ROLE_TENTS),
            "vehicle": c.selected_ids.get(ROLE_VEHICLES),
            "cable": c.reel_id,
            "distributor": c.point_ids.get(ROLE_DISTRIBUTORS),
            "generator": c.point_ids.get(ROLE_GENERATORS),
            "light": c.point_ids.get(ROLE_LIGHTS),
            "zone": c.point_ids.get(ROLE_ZONES),
            "arrow": c.point_ids.get(ROLE_ARROWS),
        }.get(kind)

    def sync_tool_buttons(self):
        active, active_type = self.controller.active_tool_kind()
        for entry in self._tools:
            entry.button.blockSignals(True)
            entry.button.setChecked(entry.kind == active)
            entry.button.blockSignals(False)
            name = None
            if entry.menu is not None:
                current = self._current_type(entry.kind)
                for item in entry.menu.actions():
                    item.setChecked(item.data() == current)
                    if item.data() == current:
                        name = item.text()
            tip = f"{entry.title}: {name}" if name else entry.title
            entry.button.setToolTip(f"{tip} ({entry.shortcut})" if entry.shortcut else tip)
        # Zeichen in der Hand: sein Button – oder „Alle Zeichen …“, wenn es dort gewählt wurde
        holding = active_type if active == "marker" else None
        in_bar = False
        for button in self._marker_buttons:
            button.blockSignals(True)
            button.setChecked(button.path == holding)
            button.blockSignals(False)
            in_bar = in_bar or button.path == holding
        self._all_symbols.blockSignals(True)
        self._all_symbols.setChecked(holding is not None and not in_bar)
        self._all_symbols.blockSignals(False)
        rows, cols = self.controller.grid_rows, self.controller.grid_cols
        self._vertices.blockSignals(True)
        self._vertices.setChecked(active == "edit")
        self._vertices.blockSignals(False)
        # Das Raster gehört zu Zelt und Fahrzeug – nur dort zeigen; blau, solange es scharf ist
        multiple = rows * cols > 1
        self._grid_button.setVisible(active in ("tent", "vehicle"))
        self._grid_button.setText(f"{rows}×{cols}" if multiple else "Einzeln")
        if self._grid_button.property("armed") != multiple:
            self._grid_button.setProperty("armed", multiple)
            self._grid_button.style().unpolish(self._grid_button)
            self._grid_button.style().polish(self._grid_button)
        for spin, value in ((self._rows, rows), (self._cols, cols)):
            if spin.value() != value:
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
        if self._guides.isChecked() != self.controller.align_enabled:
            self._guides.blockSignals(True)
            self._guides.setChecked(self.controller.align_enabled)
            self._guides.blockSignals(False)
        if self.isVisible():
            self._reposition()

    def refresh_selection(self):
        has_selection = any(self.controller.selection_ids().values())
        for widget in self._selection_widgets:
            widget.setVisible(has_selection)
        # Ein Gebiet ist ausgewählt immer in der Punktbearbeitung – der Schalter gilt nur noch für Pfeile
        shape = self.controller.editable_shape()
        self._vertices.setVisible(shape is not None and shape[0] == ROLE_ARROWS)
        if self.isVisible():
            self._reposition()
