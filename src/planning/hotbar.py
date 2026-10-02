"""Hotbar der Objektplanung: schwebende Werkzeugleiste unten mittig auf der Karte (Figma-Stil)."""

import os
from dataclasses import dataclass

from qgis.PyQt.QtCore import QEvent, QObject, QSize, Qt
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import (
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

from .layers import ROLE_DISTRIBUTORS, ROLE_GENERATORS, ROLE_LIGHTS, ROLE_TENTS, ROLE_VEHICLES

_ICON_PX = 22
_BUTTON_PX = 36
_MARGIN_BOTTOM_PX = 16
_GRID_PRESETS = ((1, 1), (2, 2), (2, 3), (3, 3), (2, 5), (4, 4))

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
QFrame#ObjektplanungHotbar QFrame#sep { background: #3a3a3a; }
"""


@dataclass
class _ToolEntry:
    kind: str
    title: str
    shortcut: str
    button: QToolButton
    menu: QMenu | None


class PlanningHotbar(QFrame):
    """Werkzeuge mit einem Klick; Typ-Auswahl über das kleine ▾ daneben.

    Liegt als Kind-Widget über der Karte und hält sich bei Größenänderung
    unten mittig. Auswahl-Aktionen (Duplizieren, Drehen, Löschen) erscheinen
    nur, wenn etwas ausgewählt ist – wie die Kontext-Leiste in Figma.
    """

    def __init__(self, controller, plugin_dir: str, canvas):
        super().__init__(canvas)
        self.setObjectName("ObjektplanungHotbar")
        self.setStyleSheet(_STYLE)
        self.controller = controller
        self.canvas = canvas
        self._icon_dir = os.path.join(plugin_dir, "icons", "planning")
        self._tools: list[_ToolEntry] = []

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 120))
        self.setGraphicsEffect(shadow)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 6, 8, 6)
        self._layout.setSpacing(2)
        catalog = controller.catalog

        self._add_tool("select", "select.svg", "Auswählen und bearbeiten", "")
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
        self._add_tool(
            "area",
            "area.svg",
            "Fläche zeichnen – wie viel passt hinein?",
            "",
            [(ROLE_TENTS, "Kapazität für Zelte"), (ROLE_VEHICLES, "Kapazität für Fahrzeuge")],
        )
        self._guides = self._button("guides.svg", "Hilfslinien: an Nachbarn ausrichten (Strg beim Ziehen hält an)")
        self._guides.setCheckable(True)
        self._guides.setChecked(controller.align_enabled)
        self._guides.toggled.connect(controller.set_align_enabled)

        # Kontext-Aktionen für die Auswahl
        self._selection_sep = self._separator()
        self._duplicate = self._button("duplicate.svg", "Duplizieren (D, Alt+Ziehen)", controller.duplicate_selection)
        self._rotate = self._button(
            "rotate.svg", "Um 15° drehen (R, Shift+R zurück)", lambda: controller.rotate_selection(15)
        )
        self._delete = self._button("delete.svg", "Löschen (Entf)", controller.delete_selection)
        self._selection_widgets = (self._selection_sep, self._duplicate, self._rotate, self._delete)

        controller.state_changed.connect(self.sync_tool_buttons)
        canvas.installEventFilter(self)
        self.sync_tool_buttons()
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
        button.setToolTip("Raster: Reihen × Spalten auf einen Klick setzen (Abstand = Mindestabstand)")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")

        menu = QMenu(self)
        panel = QWidget(menu)
        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.addWidget(QLabel("Reihen"), 0, 0)
        grid.addWidget(QLabel("Spalten"), 0, 2)
        self._rows = QSpinBox()
        self._cols = QSpinBox()
        for spin in (self._rows, self._cols):
            spin.setRange(1, 20)
            spin.valueChanged.connect(lambda _v: self.controller.set_grid(self._rows.value(), self._cols.value()))
        grid.addWidget(self._rows, 1, 0)
        grid.addWidget(QLabel("×"), 1, 1)
        grid.addWidget(self._cols, 1, 2)
        presets = QHBoxLayout()
        for rows, cols in _GRID_PRESETS:
            preset = QPushButton(f"{rows}×{cols}")
            preset.setFixedWidth(40)
            preset.clicked.connect(lambda _c=False, r=rows, c=cols: self.controller.set_grid(r, c))
            presets.addWidget(preset)
        grid.addLayout(presets, 2, 0, 1, 3)
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

    def _reposition(self):
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
            self.controller.deactivate_tool()

    def _current_type(self, kind: str) -> str | None:
        c = self.controller
        return {
            "tent": c.selected_ids.get(ROLE_TENTS),
            "vehicle": c.selected_ids.get(ROLE_VEHICLES),
            "cable": c.reel_id,
            "distributor": c.point_ids.get(ROLE_DISTRIBUTORS),
            "generator": c.point_ids.get(ROLE_GENERATORS),
            "light": c.point_ids.get(ROLE_LIGHTS),
            "area": c.area_role,
        }.get(kind)

    def sync_tool_buttons(self):
        active, _type = self.controller.active_tool_kind()
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
        rows, cols = self.controller.grid_rows, self.controller.grid_cols
        self._grid_button.setText(f"{rows}×{cols}")
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
        has_selection = bool(self.controller.selection_ids())
        for widget in self._selection_widgets:
            widget.setVisible(has_selection)
        if self.isVisible():
            self._reposition()
