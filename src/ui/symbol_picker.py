"""Katalog aller Taktischen Zeichen (svgs/) und Icon-Picker zur Auswahl."""

import os
import re
from dataclasses import dataclass

from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
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
        self.resize(700, 520)

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

        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setIconSize(QSize(48, 48))
        self.list.setGridSize(QSize(112, 92))
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(True)
        for symbol in catalog.symbols:
            item = QListWidgetItem(catalog.icon(symbol.path), symbol.name)
            item.setToolTip(symbol.label)
            item.setData(_PATH_ROLE, symbol.path)
            item.setData(_CATEGORY_ROLE, symbol.category)
            self.list.addItem(item)
        layout.addWidget(self.list)

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
