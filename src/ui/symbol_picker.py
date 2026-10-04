"""Katalog aller Taktischen Zeichen (svgs/) und Icon-Picker zur Auswahl."""

import os
import re
from dataclasses import dataclass

from qgis.PyQt.QtCore import QEvent, QSize, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QStyledItemDelegate,
    QVBoxLayout,
)

_PATH_ROLE = Qt.ItemDataRole.UserRole
_CATEGORY_ROLE = Qt.ItemDataRole.UserRole + 1


def _natural_key(text: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


@dataclass(frozen=True)
class Symbol:
    name: str  # z. B. "GKW I"
    category: str  # z. B. "THW Fahrzeuge"
    path: str  # absoluter Pfad zur SVG

    @property
    def label(self) -> str:
        return f"{self.name} – {self.category}"


class SymbolCatalog:
    """Alle SVGs unter ``svgs/``; THW-Kategorien zuerst, sonst natürlich sortiert."""

    def __init__(self, plugin_dir: str):
        self.plugin_dir = plugin_dir
        root = os.path.join(plugin_dir, "svgs")
        symbols = []
        for folder, _dirs, files in os.walk(root):
            rel = os.path.relpath(folder, root)
            category = rel.replace(os.sep, " ").replace("_", " ") if rel != "." else "Sonstiges"
            for file in files:
                if file.lower().endswith(".svg"):
                    name = os.path.splitext(file)[0].replace("_", " ")
                    symbols.append(Symbol(name, category, os.path.join(folder, file)))
        symbols.sort(key=lambda s: (not s.category.startswith("THW"), _natural_key(s.category), _natural_key(s.name)))
        self.symbols = symbols
        self.categories = list(dict.fromkeys(s.category for s in symbols))
        self._by_path = {self._key(s.path): s for s in symbols}
        self._icons: dict[str, QIcon] = {}

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.normpath(path))

    def absolute(self, svg_path: str) -> str:
        """Marker speichern den Pfad relativ zum Plugin-Ordner — hier wieder absolut machen."""
        return svg_path if os.path.isabs(svg_path) else os.path.join(self.plugin_dir, svg_path)

    def by_path(self, svg_path: str) -> Symbol | None:
        return self._by_path.get(self._key(self.absolute(svg_path))) if svg_path else None

    def find(self, text: str) -> Symbol | None:
        """Zeichen zu einem Namen aus einer Import-Datei suchen.

        Erkannt werden der Name (``GKW I``), „Name – Kategorie“, ``Kategorie/Name`` und
        der Dateipfad — ohne Rücksicht auf Groß-/Kleinschreibung, ``_`` und ``.svg``.
        Bei mehrdeutigen Namen gewinnt das erste Zeichen der Sortierung (THW zuerst).
        """
        wanted = self._search_key(text)
        if not wanted:
            return None
        by_path = self.by_path(text.strip())
        if by_path is not None:
            return by_path
        for symbol in self.symbols:
            if wanted in (
                self._search_key(symbol.name),
                self._search_key(symbol.label),
                self._search_key(f"{symbol.category}/{symbol.name}"),
            ):
                return symbol
        return None

    @staticmethod
    def _search_key(text: str) -> str:
        value = text.strip().lower().replace("\\", "/").replace("_", " ").replace("–", "-")
        if value.endswith(".svg"):
            value = value[:-4]
        if value.startswith("svgs/"):
            value = value[5:]
        return " ".join(value.replace(" - ", "/").split())

    def icon(self, path: str) -> QIcon:
        if path not in self._icons:
            self._icons[path] = QIcon(path)
        return self._icons[path]


_catalogs: dict[str, SymbolCatalog] = {}


def symbol_catalog(plugin_dir: str) -> SymbolCatalog:
    """Katalog einmal pro Sitzung einlesen (975 Dateien — nicht bei jedem Öffnen)."""
    if plugin_dir not in _catalogs:
        _catalogs[plugin_dir] = SymbolCatalog(plugin_dir)
    return _catalogs[plugin_dir]


class _CellDelegate(QStyledItemDelegate):
    """Jedes Zeichen belegt die ganze Rasterzelle.

    Qt bemisst ein Icon-Element sonst an der Icon-Breite; der Name hätte dann nur
    diese Breite zum Umbrechen und würde zu „1. …“ gekürzt.
    """

    def sizeHint(self, option, index):
        return self.parent().gridSize() - QSize(8, 6)


class _SymbolGrid(QListWidget):
    """Icon-Raster, dessen Spalten die ganze Breite füllen und Namen mehrzeilig zeigen."""

    _MIN_CELL_WIDTH = 150
    _ICON_SIZE = 56
    _TEXT_LINES = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setIconSize(QSize(self._ICON_SIZE, self._ICON_SIZE))
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setWordWrap(True)
        # Ohne Auslassungspunkte umbrechen — sonst bleibt von „1. Bergungsgruppe ASH“ nur „1. …“
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setItemDelegate(_CellDelegate(self))
        # Immer sichtbar, damit die Spaltenbreite beim Filtern nicht springt
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._cell_height = self._ICON_SIZE + self._TEXT_LINES * self.fontMetrics().lineSpacing() + 18
        self.setGridSize(QSize(self._MIN_CELL_WIDTH, self._cell_height))

    def resizeEvent(self, event):
        width = self.viewport().width()
        columns = max(1, width // self._MIN_CELL_WIDTH)
        grid = QSize(width // columns, self._cell_height)
        if grid != self.gridSize():
            self.setGridSize(grid)
        super().resizeEvent(event)


class SymbolPickerDialog(QDialog):
    """Icon-Picker: Suche + Kategorie-Filter über einem Raster aller Zeichen.

    Doppelklick oder Enter übernimmt das Zeichen. Suche und Kategorie bleiben
    für die nächste Auswahl erhalten, damit man mehrere ähnliche Zeichen
    schnell hintereinander wählen kann.
    """

    _last_search = ""
    _last_category = ""

    def __init__(self, catalog: SymbolCatalog, parent=None, current_path: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Taktisches Zeichen wählen")
        self.resize(860, 640)

        layout = QVBoxLayout(self)

        filter_layout = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen, z. B. GKW, Zugtrupp, Bergung …")
        self.search.setClearButtonEnabled(True)
        filter_layout.addWidget(self.search, 2)
        self.category = QComboBox()
        self.category.addItem("Alle Kategorien", "")
        for category in catalog.categories:
            self.category.addItem(category, category)
        filter_layout.addWidget(self.category, 1)
        layout.addLayout(filter_layout)

        self.list = _SymbolGrid()
        for symbol in catalog.symbols:
            item = QListWidgetItem(catalog.icon(symbol.path), symbol.name)
            item.setToolTip(symbol.label)
            item.setData(_PATH_ROLE, symbol.path)
            item.setData(_CATEGORY_ROLE, symbol.category)
            self.list.addItem(item)
        layout.addWidget(self.list)

        # Voller Name + Kategorie des gewählten Zeichens (im Raster ist dafür nicht immer Platz)
        self.selected_label = QLabel("")
        self.selected_label.setTextFormat(Qt.TextFormat.RichText)
        self.selected_label.setWordWrap(True)
        layout.addWidget(self.selected_label)

        bottom = QHBoxLayout()
        self.count_label = QLabel("")
        bottom.addWidget(self.count_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)

        self.search.textChanged.connect(self._apply_filter)
        self.category.currentIndexChanged.connect(self._apply_filter)
        self.search.returnPressed.connect(self.accept)
        self.list.itemDoubleClicked.connect(self.accept)
        self.list.currentItemChanged.connect(self._show_selected)
        self.search.installEventFilter(self)

        current = catalog.by_path(current_path) if current_path else None
        if current is not None:
            self.category.setCurrentIndex(max(0, self.category.findData(current.category)))
            self._apply_filter()
            for i in range(self.list.count()):
                if self.list.item(i).data(_PATH_ROLE) == current.path:
                    self.list.setCurrentRow(i)
                    self.list.scrollToItem(self.list.item(i))
                    break
        else:
            self.category.setCurrentIndex(max(0, self.category.findData(SymbolPickerDialog._last_category)))
            self.search.setText(SymbolPickerDialog._last_search)
            self.search.selectAll()
            self._apply_filter()
        self.search.setFocus()

    def _apply_filter(self):
        words = self.search.text().lower().split()
        category = self.category.currentData()
        first_visible = None
        visible = 0
        for i in range(self.list.count()):
            item = self.list.item(i)
            haystack = item.toolTip().lower()
            show = (not category or item.data(_CATEGORY_ROLE) == category) and all(w in haystack for w in words)
            item.setHidden(not show)
            if show:
                visible += 1
                first_visible = first_visible or item
        current = self.list.currentItem()
        if current is None or current.isHidden():
            self.list.setCurrentItem(first_visible)
        self.count_label.setText(f"{visible} Zeichen")
        self._show_selected()

    def _show_selected(self, *_):
        item = self.list.currentItem()
        if item is None or item.isHidden():
            self.selected_label.setText("Kein Zeichen gefunden – Suche oder Kategorie ändern.")
            return
        self.selected_label.setText(f"<b>{item.text()}</b> – {item.data(_CATEGORY_ROLE)}")

    def eventFilter(self, watched, event):
        # Pfeiltasten im Suchfeld bewegen die Auswahl im Raster, ohne dass man das Feld verlassen muss
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                QApplication.sendEvent(self.list, event)
                return True
        return super().eventFilter(watched, event)

    def selected_path(self) -> str | None:
        item = self.list.currentItem()
        if item is None or item.isHidden():
            return None
        return item.data(_PATH_ROLE)

    def accept(self):
        if not self.selected_path():
            return
        SymbolPickerDialog._last_search = self.search.text()
        SymbolPickerDialog._last_category = self.category.currentData()
        super().accept()

    @classmethod
    def pick(cls, catalog: SymbolCatalog, parent=None, current_path: str | None = None) -> str | None:
        """Zeigt den Picker; gibt den absoluten SVG-Pfad oder None bei Abbruch zurück."""
        dialog = cls(catalog, parent, current_path)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.selected_path()
