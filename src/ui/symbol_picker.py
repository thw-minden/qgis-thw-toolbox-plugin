"""Taktische Zeichen: Katalog (svgs/), Favoriten/Verlauf und der gemeinsame Zeichen-Picker.

Derselbe Picker (``SymbolPickerWidget``) steckt in der Symbolpalette (Dock), im
Auswahldialog (Marker-Tabelle, Import, Drohnenfoto) und im Popup, das ein
Rechtsklick auf die Karte öffnet. Favoriten, zuletzt verwendete Zeichen und die
letzten Suchen teilen sich alle über ``SymbolPrefs``.
"""

import html
import math
import os
import re
from dataclasses import dataclass, replace

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import (
    QAbstractListModel,
    QEvent,
    QMimeData,
    QModelIndex,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QColor, QDrag, QFont, QGuiApplication, QIcon, QPainter, QPalette, QPen, QPixmap, QPolygonF
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

_PATH_ROLE = Qt.ItemDataRole.UserRole
_FAVORITE_ROLE = Qt.ItemDataRole.UserRole + 1

_GENERAL_GROUP = "Allgemein"
# Diese Organisationen stehen vorn, die übrigen folgen alphabetisch
_GROUP_ORDER = ("THW", _GENERAL_GROUP)
# Kategorie-Auswahl: ganze Organisation statt einzelner Kategorie
_GROUP_DATA_PREFIX = "gruppe:"

_PREFS_PREFIX = "thw_toolbox/zeichen/"
_STAR_COLOR = "#f5a623"

TAB_FAVORITES = "favoriten"
TAB_RECENT = "zuletzt"
TAB_ALL = "alle"


def _natural_key(text: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def _fold(text: str) -> str:
    """Suchform: klein, ä = ae usw., Unterstrich, Bindestrich und Schrägstrich wie Leerzeichen."""
    value = text.casefold()
    for umlaut, plain in (("ä", "ae"), ("ö", "oe"), ("ü", "ue")):
        value = value.replace(umlaut, plain)
    for separator in ("_", "-", "–", "/"):
        value = value.replace(separator, " ")
    return " ".join(value.split())


def _group_and_kind(rel_folder: str) -> tuple[str, str]:
    """``THW_Fahrzeuge`` → (THW, Fahrzeuge), ``Schadenskonten/gelb`` → (Allgemein, Schadenskonten gelb)."""
    parts = rel_folder.split(os.sep)
    head, separator, tail = parts[0].partition("_")
    if separator:
        group, parts = head, [tail, *parts[1:]]
    else:
        group = _GENERAL_GROUP
    return group, " ".join(parts).replace("_", " ")


def _group_rank(group: str):
    return (_GROUP_ORDER.index(group) if group in _GROUP_ORDER else len(_GROUP_ORDER), _natural_key(group))


@dataclass(frozen=True)
class Symbol:
    name: str  # z. B. "GKW I"
    category: str  # z. B. "THW Fahrzeuge"
    path: str  # absoluter Pfad zur SVG
    group: str = _GENERAL_GROUP  # Organisation, z. B. "THW", "Feuerwehr", "Allgemein"
    kind: str = ""  # Art innerhalb der Organisation, z. B. "Fahrzeuge", "Schadenskonten gelb"

    @property
    def label(self) -> str:
        return f"{self.name} – {self.category}"


class SymbolCatalog:
    """Alle SVGs unter ``svgs/``: THW zuerst, dann Allgemein, dann die übrigen Organisationen."""

    def __init__(self, plugin_dir: str):
        self.plugin_dir = plugin_dir
        root = os.path.join(plugin_dir, "svgs")
        symbols = []
        for folder, _dirs, files in os.walk(root):
            rel = os.path.relpath(folder, root)
            if rel == ".":
                category, group, kind = "Sonstiges", _GENERAL_GROUP, "Sonstiges"
            else:
                category = rel.replace(os.sep, " ").replace("_", " ")
                group, kind = _group_and_kind(rel)
            for file in files:
                if file.lower().endswith(".svg"):
                    name = os.path.splitext(file)[0].replace("_", " ")
                    symbols.append(Symbol(name, category, os.path.join(folder, file), group, kind))
        symbols.sort(key=lambda s: (_group_rank(s.group), _natural_key(s.kind), _natural_key(s.name)))
        self.symbols = symbols
        self.categories = list(dict.fromkeys(s.category for s in symbols))
        # [(Organisation, [(Art, Kategorie), …]), …] für die Kategorie-Auswahl
        groups: dict[str, dict[str, str]] = {}
        for symbol in symbols:
            groups.setdefault(symbol.group, {}).setdefault(symbol.kind, symbol.category)
        self.groups = [(group, list(kinds.items())) for group, kinds in groups.items()]
        self._by_path = {self._key(s.path): s for s in symbols}
        # Suchform je Zeichen: Name, Wörter des Namens, Name + Kategorie + Organisation
        self._search_index = {}
        for symbol in symbols:
            name = _fold(symbol.name)
            self._search_index[symbol.path] = (name, name.split(), _fold(f"{symbol.label} {symbol.group}"))
        self._icons: dict[str, QIcon] = {}

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.normpath(path))

    def absolute(self, svg_path: str) -> str:
        """Marker speichern den Pfad relativ zum Plugin-Ordner — hier wieder absolut machen."""
        return svg_path if os.path.isabs(svg_path) else os.path.join(self.plugin_dir, svg_path)

    def storage_key(self, svg_path: str) -> str:
        """Pfad relativ zum Plugin-Ordner mit „/“ – so überstehen Favoriten und Verlauf ein Plugin-Update."""
        path = os.path.normpath(self.absolute(svg_path))
        try:
            rel = os.path.relpath(path, self.plugin_dir)
        except ValueError:  # anderes Laufwerk (Windows)
            rel = ".."
        return (path if rel.startswith("..") else rel).replace(os.sep, "/")

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

    def search(self, text: str, symbols: list[Symbol] | None = None, favorites=frozenset()) -> list[Symbol]:
        """Zeichen, die alle Suchwörter enthalten – die besten Treffer zuerst.

        Reihenfolge: Name genau gleich, Name beginnt mit der Suche, jedes Wort
        beginnt ein Wort im Namen, alle Wörter im Namen, Treffer nur über die
        Kategorie. Bei Gleichstand stehen Favoriten (absolute Pfade) vorn, sonst
        gilt die Katalog-Reihenfolge (THW zuerst). Umlaute dürfen als ae/oe/ue
        geschrieben werden.
        """
        candidates = self.symbols if symbols is None else symbols
        words = _fold(text).split()
        if not words:
            return list(candidates)
        query = " ".join(words)
        ranked = []
        for order, symbol in enumerate(candidates):
            name, name_words, haystack = self._search_index[symbol.path]
            if not all(word in haystack for word in words):
                continue
            if name == query:
                score = 0
            elif name.startswith(query):
                score = 1
            elif all(any(part.startswith(word) for part in name_words) for word in words):
                score = 2
            elif all(word in name for word in words):
                score = 3
            else:
                score = 4
            ranked.append((score, symbol.path not in favorites, order, symbol))
        ranked.sort(key=lambda entry: entry[:3])
        return [entry[3] for entry in ranked]

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


class SymbolPrefs(QObject):
    """Favoriten, zuletzt verwendete Zeichen und die letzten Suchen.

    Liegen in den QGIS-Benutzereinstellungen (``thw_toolbox/zeichen/``) und gelten
    damit in allen Projekten. ``changed`` meldet jede Änderung an alle offenen Picker.
    """

    changed = pyqtSignal()

    MAX_RECENT = 24
    MAX_SEARCHES = 8

    def __init__(self, catalog: SymbolCatalog):
        super().__init__()
        self._catalog = catalog
        self._favorites = self._read("favoriten")
        self._recent = self._read("zuletzt_verwendet")
        self._searches = self._read("zuletzt_gesucht")

    def _read(self, name: str) -> list[str]:
        value = QgsSettings().value(_PREFS_PREFIX + name, [])
        if not value:
            return []
        if isinstance(value, str):
            value = [value]
        result = []
        for entry in value:
            entry = str(entry).strip()
            if entry and entry not in result:
                result.append(entry)
        return result

    def _write(self, name: str, values: list[str]):
        QgsSettings().setValue(_PREFS_PREFIX + name, list(values))
        self.changed.emit()

    def _symbols(self, keys: list[str]) -> list[Symbol]:
        """Gespeicherte Pfade als Zeichen – inzwischen fehlende Dateien fallen weg."""
        symbols = (self._catalog.by_path(key) for key in keys)
        return [symbol for symbol in symbols if symbol is not None]

    # -- Favoriten ---------------------------------------------------------

    def favorites(self) -> list[Symbol]:
        return self._symbols(self._favorites)

    def favorite_paths(self) -> set[str]:
        return {symbol.path for symbol in self.favorites()}

    def is_favorite(self, path: str) -> bool:
        return self._catalog.storage_key(path) in self._favorites

    def toggle_favorite(self, path: str) -> bool:
        """Zeichen merken bzw. vergessen; gibt zurück, ob es jetzt Favorit ist."""
        key = self._catalog.storage_key(path)
        favorite = key not in self._favorites
        if favorite:
            self._favorites.append(key)
        else:
            self._favorites.remove(key)
        self._write("favoriten", self._favorites)
        return favorite

    # -- Zuletzt verwendet -------------------------------------------------

    def recent(self) -> list[Symbol]:
        return self._symbols(self._recent)

    def add_recent(self, path: str):
        key = self._catalog.storage_key(path)
        if self._recent[:1] == [key]:
            return
        self._recent = [key] + [k for k in self._recent if k != key][: self.MAX_RECENT - 1]
        self._write("zuletzt_verwendet", self._recent)

    def remove_recent(self, path: str):
        key = self._catalog.storage_key(path)
        if key in self._recent:
            self._recent.remove(key)
            self._write("zuletzt_verwendet", self._recent)

    def clear_recent(self):
        self._recent = []
        self._write("zuletzt_verwendet", self._recent)

    # -- Zuletzt gesucht ---------------------------------------------------

    def searches(self) -> list[str]:
        return list(self._searches)

    def add_search(self, text: str):
        text = " ".join(text.split())
        if not text:
            return
        searches = [text] + [s for s in self._searches if s.casefold() != text.casefold()]
        searches = searches[: self.MAX_SEARCHES]
        if searches != self._searches:
            self._searches = searches
            self._write("zuletzt_gesucht", self._searches)

    def clear_searches(self):
        self._searches = []
        self._write("zuletzt_gesucht", self._searches)


_prefs: dict[str, SymbolPrefs] = {}


def symbol_prefs(plugin_dir: str) -> SymbolPrefs:
    """Favoriten und Verlauf – ein Objekt für alle Picker, damit sie sich gegenseitig aktualisieren."""
    if plugin_dir not in _prefs:
        _prefs[plugin_dir] = SymbolPrefs(symbol_catalog(plugin_dir))
    return _prefs[plugin_dir]


# ----------------------------------------------------------------------
# Darstellung
# ----------------------------------------------------------------------


def _event_pos(event) -> QPoint:
    return event.position().toPoint() if hasattr(event, "position") else event.pos()


def _paint_star(painter: QPainter, rect: QRectF, filled: bool, color: QColor):
    """Fünfzackiger Stern – gezeichnet statt ★/☆, damit er nicht von der Schriftart abhängt."""
    cx, cy = rect.center().x(), rect.center().y()
    outer = min(rect.width(), rect.height()) / 2.0
    inner = outer * 0.45
    points = []
    for i in range(10):
        radius = outer if i % 2 == 0 else inner
        angle = math.radians(-90 + i * 36)
        points.append(QPointF(cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#c77c00") if filled else color)
    pen.setWidthF(1.2)
    painter.setPen(pen)
    if filled:
        painter.setBrush(color)
    else:
        painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPolygon(QPolygonF(points))
    painter.restore()


def _star_icon() -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    _paint_star(painter, QRectF(2, 2, 28, 28), True, QColor(_STAR_COLOR))
    painter.end()
    return QIcon(pixmap)


def _muted(widget: QWidget, smaller: bool = False):
    """Grauer Text für Hinweise und Zähler – aus der Palette, damit es auch im dunklen Thema passt."""
    palette = widget.palette()
    color = palette.color(QPalette.ColorRole.WindowText)
    color.setAlpha(150)
    palette.setColor(QPalette.ColorRole.WindowText, color)
    widget.setPalette(palette)
    if smaller:
        font = widget.font()
        if font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() * 0.9)
            widget.setFont(font)


_STYLE = """
QToolButton#thwTab {
    border: none;
    border-radius: 11px;
    padding: 3px 10px;
}
QToolButton#thwTab:checked {
    background: palette(highlight);
    color: palette(highlighted-text);
}
QToolButton#thwTab:hover:!checked { background: palette(alternate-base); }
QToolButton#thwChip {
    border: 1px solid palette(mid);
    border-radius: 9px;
    padding: 0px 8px;
}
QToolButton#thwChip:hover { border-color: palette(highlight); }
"""


class _SymbolModel(QAbstractListModel):
    """Die gerade gezeigten Zeichen (Favoriten, Verlauf, Suchtreffer oder eine Kategorie)."""

    def __init__(self, catalog: SymbolCatalog, prefs: SymbolPrefs, parent=None):
        super().__init__(parent)
        self._catalog = catalog
        self._prefs = prefs
        self._symbols: list[Symbol] = []

    def set_symbols(self, symbols: list[Symbol]):
        self.beginResetModel()
        self._symbols = list(symbols)
        self.endResetModel()

    def symbols(self) -> list[Symbol]:
        return self._symbols

    def symbol(self, row: int) -> Symbol | None:
        return self._symbols[row] if 0 <= row < len(self._symbols) else None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._symbols)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        symbol = self.symbol(index.row()) if index.isValid() else None
        if symbol is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return symbol.name
        if role == Qt.ItemDataRole.DecorationRole:
            return self._catalog.icon(symbol.path)
        if role == Qt.ItemDataRole.ToolTipRole:
            return symbol.label + ("\n★ Favorit" if self._prefs.is_favorite(symbol.path) else "")
        if role == _PATH_ROLE:
            return symbol.path
        if role == _FAVORITE_ROLE:
            return self._prefs.is_favorite(symbol.path)
        return None


class _CellDelegate(QStyledItemDelegate):
    """Jedes Zeichen belegt die ganze Rasterzelle; oben rechts sitzt der Favoriten-Stern.

    Qt bemisst ein Icon-Element sonst an der Icon-Breite; der Name hätte dann nur
    diese Breite zum Umbrechen und würde zu „1. …“ gekürzt.
    """

    def sizeHint(self, option, index):
        return self.parent().gridSize() - QSize(8, 6)

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        favorite = bool(index.data(_FAVORITE_ROLE))
        if not favorite and not (option.state & QStyle.StateFlag.State_MouseOver):
            return
        if favorite:
            color = QColor(_STAR_COLOR)
        else:
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
            color = option.palette.color(role)
            color.setAlpha(150)
        _paint_star(painter, QRectF(self.parent().star_rect(option.rect)), favorite, color)


class _SymbolGrid(QListView):
    """Icon-Raster, dessen Spalten die ganze Breite füllen und Namen mehrzeilig zeigen.

    Der Stern oben rechts in jeder Zelle ist bei Favoriten gefüllt und erscheint
    beim Überfahren als Umriss; ein Klick darauf merkt das Zeichen bzw. vergisst es.
    """

    star_clicked = pyqtSignal(str)
    # Zeichen wurde per Drag & Drop abgelegt (Karte, Marker-Tabelle)
    dropped = pyqtSignal(str)

    _TEXT_LINES = 3
    _STAR_PX = 16
    _STAR_GRAB_PX = 4

    def __init__(self, cell_width: int, icon_size: int, draggable: bool, parent=None):
        super().__init__(parent)
        self._min_cell_width = cell_width
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        # Ohne Auslassungspunkte umbrechen — sonst bleibt von „1. Bergungsgruppe ASH“ nur „1. …“
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setItemDelegate(_CellDelegate(self))
        # Immer sichtbar, damit die Spaltenbreite beim Filtern nicht springt
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setDragEnabled(draggable)
        self.setDragDropMode(
            QAbstractItemView.DragDropMode.DragOnly if draggable else QAbstractItemView.DragDropMode.NoDragDrop
        )
        self._cell_height = icon_size + self._TEXT_LINES * self.fontMetrics().lineSpacing() + 18
        self.setGridSize(QSize(cell_width, self._cell_height))
        self._star_press: int | None = None

        self._empty = QLabel(self.viewport())
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        _muted(self._empty)
        self._empty.hide()

    def set_empty_text(self, text: str):
        """Hinweis mitten im Raster, solange keine Zeichen da sind (leer = keiner)."""
        self._empty.setText(text)
        self._empty.setVisible(bool(text))

    def star_rect(self, item_rect: QRect) -> QRect:
        size = self._STAR_PX
        return QRect(item_rect.right() - size - 2, item_rect.top() + 2, size, size)

    def _star_at(self, pos: QPoint) -> QModelIndex | None:
        index = self.indexAt(pos)
        if not index.isValid():
            return None
        grab = self._STAR_GRAB_PX
        if self.star_rect(self.visualRect(index)).adjusted(-grab, -grab, grab, grab).contains(pos):
            return index
        return None

    def resizeEvent(self, event):
        width = self.viewport().width()
        columns = max(1, width // self._min_cell_width)
        # Ein Pixel Luft: füllen die Zellen die Breite genau aus, bricht Qt die letzte Spalte um
        grid = QSize((width - 1) // columns, self._cell_height)
        if grid != self.gridSize():
            self.setGridSize(grid)
        self._empty.setGeometry(self.viewport().rect().adjusted(16, 16, -16, -16))
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._star_at(_event_pos(event))
            if index is not None:
                self._star_press = index.row()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        # Doppelklick auf den Stern schaltet zweimal um, übernimmt aber nichts
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._star_at(_event_pos(event))
            if index is not None:
                self._star_press = index.row()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if self._star_press is not None:
            event.accept()
            return
        on_star = event.buttons() == Qt.MouseButton.NoButton and self._star_at(_event_pos(event)) is not None
        if on_star:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.viewport().unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._star_press is not None:
            row, self._star_press = self._star_press, None
            index = self._star_at(_event_pos(event))
            if index is not None and index.row() == row:
                self.star_clicked.emit(index.data(_PATH_ROLE))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def startDrag(self, supported_actions):
        index = self.currentIndex()
        path = index.data(_PATH_ROLE) if index.isValid() else None
        if not path:
            return
        mime = QMimeData()
        mime.setText(path)
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(index.data(Qt.ItemDataRole.DecorationRole).pixmap(48, 48))
        drag.setHotSpot(QPoint(24, 24))
        if drag.exec(Qt.DropAction.CopyAction) == Qt.DropAction.CopyAction:
            self.dropped.emit(path)


class _SearchHistory(QWidget):
    """„Zuletzt gesucht“ als Chips in einer Zeile; was nicht mehr hineinpasst, fällt weg."""

    picked = pyqtSignal(str)
    cleared = pyqtSignal()

    _GAP = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._label = QLabel("Zuletzt gesucht:", self)
        _muted(self._label)
        self._clear = QToolButton(self)
        self._clear.setText("✕")
        self._clear.setAutoRaise(True)
        self._clear.setToolTip("Suchverlauf löschen")
        self._clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear.clicked.connect(lambda _checked=False: self.cleared.emit())
        self._chips: list[QToolButton] = []

    def set_terms(self, terms: list[str]):
        for chip in self._chips:
            chip.hide()
            chip.deleteLater()
        self._chips = []
        for term in terms:
            chip = QToolButton(self)
            chip.setObjectName("thwChip")
            chip.setText(term)
            chip.setToolTip(f"Nach „{term}“ suchen")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _checked=False, t=term: self.picked.emit(t))
            self._chips.append(chip)
        self.updateGeometry()
        self._relayout()

    def sizeHint(self):
        heights = [self._label.sizeHint().height(), self._clear.sizeHint().height()]
        heights += [chip.sizeHint().height() for chip in self._chips]
        return QSize(240, max(heights))

    def minimumSizeHint(self):
        # Breite 0: die Chips sollen die Symbolpalette nicht breiter machen
        return QSize(0, self.sizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def _place(self, widget: QWidget, x: int) -> int:
        size = widget.sizeHint()
        widget.setGeometry(x, (self.height() - size.height()) // 2, size.width(), size.height())
        return x + size.width() + self._GAP

    def _relayout(self):
        x = self._place(self._label, 0)
        room = self.width() - self._clear.sizeHint().width()
        fits = True
        for chip in self._chips:
            fits = fits and x + chip.sizeHint().width() <= room
            chip.setVisible(fits)
            if fits:
                x = self._place(chip, x)
        self._place(self._clear, x)


@dataclass(frozen=True)
class PickerMode:
    """Wie sich der Picker verhält – je nachdem, wo er steckt."""

    cell_width: int
    icon_size: int
    single_click: bool  # Klick übernimmt (Popup) statt Doppelklick
    draggable: bool  # Zeichen lassen sich herausziehen (Symbolpalette → Karte)
    compact: bool  # schmale Spalte: Kategorie in eigener Zeile
    choose_label: str  # Eintrag „übernehmen“ im Rechtsklick-Menü eines Zeichens
    hint: str


DIALOG_MODE = PickerMode(
    cell_width=150,
    icon_size=56,
    single_click=False,
    draggable=False,
    compact=False,
    choose_label="Übernehmen",
    hint="Doppelklick oder Enter übernimmt das Zeichen · der Stern oben rechts merkt es als Favorit (Strg+D)",
)
POPUP_MODE = PickerMode(
    cell_width=112,
    icon_size=48,
    single_click=True,
    draggable=False,
    compact=False,
    choose_label="Hier setzen",
    hint="Klick oder Enter setzt das Zeichen an die markierte Stelle · Stern: Favorit (Strg+D) · Esc schließt",
)
DOCK_MODE = PickerMode(
    cell_width=96,
    icon_size=40,
    single_click=False,
    draggable=True,
    compact=True,
    choose_label="In die Kartenmitte setzen",
    hint="Auf die Karte ziehen oder doppelklicken (Kartenmitte) · Rechtsklick auf die Karte setzt ein Zeichen dort",
)
# Popup aus der Hotbar: erst das Zeichen wählen, der nächste Klick auf die Karte setzt es
POPUP_TOOL_MODE = replace(
    POPUP_MODE,
    choose_label="Wählen",
    hint="Klick oder Enter wählt das Zeichen, der nächste Klick auf die Karte setzt es · Stern: Favorit (Strg+D) · "
    "Favoriten liegen in der Hotbar auf den Tasten 1–9",
)


class SymbolPickerWidget(QWidget):
    """Gemeinsamer Zeichen-Picker: Suche, Favoriten, zuletzt verwendet, alle Zeichen nach Kategorie.

    Die Reiter wählen, was gezeigt wird; die Kategorie filtert in jedem Reiter.
    Eine Suche läuft immer über alle Zeichen (der gewählten Kategorie) und stellt
    die besten Treffer nach vorn, Favoriten zuerst; ist sie wieder leer, geht es
    zurück in den vorigen Reiter. Pfeiltasten im Suchfeld bewegen die Auswahl,
    Enter übernimmt, Strg+D merkt das Zeichen als Favorit, Esc leert die Suche.

    ``chosen(path)``: Zeichen übernommen (Doppelklick/Enter, im Popup ein Klick) –
    es landet dann auch unter „Zuletzt verwendet“, die Suche unter „Zuletzt gesucht“.
    """

    chosen = pyqtSignal(str)
    current_changed = pyqtSignal(str)  # "" = kein Zeichen

    def __init__(self, catalog: SymbolCatalog, prefs: SymbolPrefs, parent=None, mode: PickerMode = DIALOG_MODE):
        super().__init__(parent)
        self._catalog = catalog
        self._prefs = prefs
        self._mode = mode
        self._tab = TAB_ALL
        self._tab_before_search: str | None = None
        self.setStyleSheet(_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen, z. B. GKW, Zugtrupp, Bergung …")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)

        self.history = _SearchHistory()
        layout.addWidget(self.history)

        tabs = QHBoxLayout()
        tabs.setSpacing(2)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        self._tab_buttons: dict[str, QToolButton] = {}
        for key, text, tooltip in (
            (TAB_FAVORITES, "Favoriten", "Gemerkte Zeichen – Stern an einem Zeichen oder Strg+D"),
            (
                TAB_RECENT,
                "Zuletzt" if mode.compact else "Zuletzt verwendet",
                "Zuletzt gesetzte oder gewählte Zeichen, das neueste zuerst",
            ),
            (TAB_ALL, "Alle", "Alle Zeichen – mit der Kategorie eingrenzen"),
        ):
            button = QToolButton()
            button.setObjectName("thwTab")
            button.setText(text)
            button.setToolTip(tooltip)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if key == TAB_FAVORITES:
                button.setIcon(_star_icon())
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.clicked.connect(lambda _checked=False, k=key: self._on_tab_clicked(k))
            self._tab_group.addButton(button)
            self._tab_buttons[key] = button
            tabs.addWidget(button)
        tabs.addStretch(1)

        self.category = QComboBox()
        self.category.setMaxVisibleItems(24)
        self.category.setToolTip("Kategorie: gilt für Favoriten, Verlauf, alle Zeichen und die Suche")
        self.category.addItem("Alle Kategorien", "")
        bold = QFont(self.category.font())
        bold.setBold(True)
        for group, kinds in catalog.groups:
            self.category.addItem(group, _GROUP_DATA_PREFIX + group)
            self.category.setItemData(self.category.count() - 1, bold, Qt.ItemDataRole.FontRole)
            for kind, category in kinds:
                self.category.addItem(f"{group} · {kind}", category)
        if mode.compact:
            layout.addLayout(tabs)
            layout.addWidget(self.category)
        else:
            self.category.setMinimumWidth(220)
            tabs.addWidget(self.category)
            layout.addLayout(tabs)

        self.grid = _SymbolGrid(mode.cell_width, mode.icon_size, mode.draggable)
        self.model = _SymbolModel(catalog, prefs, self.grid)
        self.grid.setModel(self.model)
        layout.addWidget(self.grid, 1)

        bottom = QHBoxLayout()
        # Voller Name + Kategorie des gewählten Zeichens (im Raster ist dafür nicht immer Platz)
        self.info = QLabel("")
        self.info.setTextFormat(Qt.TextFormat.RichText)
        self.info.setWordWrap(True)
        bottom.addWidget(self.info, 1)
        self.count = QLabel("")
        _muted(self.count)
        bottom.addWidget(self.count, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(bottom)

        self.hint = QLabel(mode.hint)
        self.hint.setWordWrap(True)
        _muted(self.hint, smaller=True)
        layout.addWidget(self.hint)

        self.search.textChanged.connect(self._on_search_changed)
        self.search.installEventFilter(self)
        self.grid.installEventFilter(self)
        self.category.currentIndexChanged.connect(lambda _index: self._refresh())
        self.grid.selectionModel().currentChanged.connect(lambda *_args: self._show_current())
        if mode.single_click:
            self.grid.clicked.connect(self._on_index_chosen)
        else:
            self.grid.doubleClicked.connect(self._on_index_chosen)
        self.grid.star_clicked.connect(self._prefs.toggle_favorite)
        self.grid.dropped.connect(self._remember)
        self.grid.customContextMenuRequested.connect(self._context_menu)
        self.history.picked.connect(self._search_for)
        self.history.cleared.connect(self._prefs.clear_searches)
        prefs.changed.connect(self._on_prefs_changed)

        self.history.set_terms(prefs.searches())
        self._set_tab(self.default_tab())

    # ------------------------------------------------------------------
    # Öffentliche Schnittstelle
    # ------------------------------------------------------------------

    def default_tab(self) -> str:
        """Favoriten, sonst zuletzt verwendet, sonst alle – was am schnellsten zum Ziel führt."""
        if self._prefs.favorites():
            return TAB_FAVORITES
        if self._prefs.recent():
            return TAB_RECENT
        return TAB_ALL

    def focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def selected_symbol(self) -> Symbol | None:
        index = self.grid.currentIndex()
        return self.model.symbol(index.row()) if index.isValid() else None

    def selected_path(self) -> str | None:
        symbol = self.selected_symbol()
        return symbol.path if symbol else None

    def set_current_path(self, path: str | None) -> bool:
        """Zeichen in seiner Kategorie zeigen und auswählen (z. B. das bisherige Zeichen einer Zeile)."""
        symbol = self._catalog.by_path(path) if path else None
        if symbol is None:
            return False
        self._tab_before_search = None
        self._set_search_silently("")
        self._set_category_silently(symbol.category)
        self._set_tab(TAB_ALL)
        self._select_path(symbol.path)
        return True

    def state(self) -> tuple[str, str, str]:
        """Reiter, Kategorie und Suche – damit ein Dialog beim nächsten Öffnen dort weitermacht."""
        return (self._tab_before_search or self._tab, self.category.currentData() or "", self.search.text())

    def restore_state(self, state: tuple[str, str, str]):
        tab, category, text = state
        self._set_category_silently(category)
        self._set_search_silently(text)
        self._tab_before_search = None
        if text.strip() and tab != TAB_ALL:
            self._tab_before_search = tab
            tab = TAB_ALL
        self._set_tab(tab)

    def choose_current(self):
        path = self.selected_path()
        if path:
            self.choose(path)

    def choose(self, path: str):
        self._remember(path)
        self.chosen.emit(path)

    # ------------------------------------------------------------------
    # Anzeige
    # ------------------------------------------------------------------

    def _set_search_silently(self, text: str):
        self.search.blockSignals(True)
        self.search.setText(text)
        self.search.blockSignals(False)

    def _set_category_silently(self, data: str):
        self.category.blockSignals(True)
        self.category.setCurrentIndex(max(0, self.category.findData(data or "")))
        self.category.blockSignals(False)

    def _set_tab(self, key: str, refresh: bool = True):
        self._tab = key
        for tab, button in self._tab_buttons.items():
            button.setChecked(tab == key)
        if refresh:
            self._refresh()

    def _on_tab_clicked(self, key: str):
        self._tab_before_search = None
        if key != TAB_ALL and self.search.text():
            # Favoriten und Verlauf zeigen sich ganz – die Suche gilt für alle Zeichen
            self._set_search_silently("")
        self._set_tab(key)

    def _on_search_changed(self, text: str):
        if text.strip():
            if self._tab != TAB_ALL:
                self._tab_before_search = self._tab
                self._set_tab(TAB_ALL, refresh=False)
        elif self._tab_before_search is not None:
            tab, self._tab_before_search = self._tab_before_search, None
            self._set_tab(tab, refresh=False)
        self._refresh()

    def _search_for(self, term: str):
        self.search.setText(term)
        self.search.setFocus()
        self.search.end(False)

    def _in_category(self, symbols: list[Symbol]) -> list[Symbol]:
        data = self.category.currentData()
        if not data:
            return list(symbols)
        if data.startswith(_GROUP_DATA_PREFIX):
            group = data[len(_GROUP_DATA_PREFIX) :]
            return [s for s in symbols if s.group == group]
        return [s for s in symbols if s.category == data]

    def _refresh(self, keep_current: bool = False):
        keep = self.selected_path() if keep_current else None
        text = self.search.text().strip()
        # Leer wegen der Kategorie? Dann sagen, welche – sie gilt auch in Favoriten und Verlauf
        where = f" in „{self.category.currentText()}“" if self.category.currentData() else ""
        if text:
            symbols = self._catalog.search(text, self._in_category(self._catalog.symbols), self._prefs.favorite_paths())
            empty = f"Keine Zeichen zu „{text}“{where}."
        elif self._tab == TAB_FAVORITES:
            symbols = self._in_category(self._prefs.favorites())
            empty = (
                f"Keine Favoriten{where}."
                if where
                else "Noch keine Favoriten.\n\nKlick auf den Stern oben rechts an einem Zeichen "
                "oder Rechtsklick → „Zu Favoriten hinzufügen“."
            )
        elif self._tab == TAB_RECENT:
            symbols = self._in_category(self._prefs.recent())
            empty = (
                f"Keine zuletzt verwendeten Zeichen{where}."
                if where
                else "Noch keine Zeichen verwendet.\n\nGesetzte und gewählte Zeichen erscheinen hier."
            )
        else:
            symbols = self._in_category(self._catalog.symbols)
            empty = f"Keine Zeichen{where}."

        self.model.set_symbols(symbols)
        self.grid.set_empty_text("" if symbols else empty)
        if symbols:
            row = next((i for i, s in enumerate(symbols) if s.path == keep), 0) if keep else 0
            index = self.model.index(row, 0)
            self.grid.setCurrentIndex(index)
            if row:
                self.grid.scrollTo(index)
            else:
                self.grid.scrollToTop()
        self.count.setText(f"{len(symbols)} Zeichen")
        self.history.setVisible(not text and bool(self._prefs.searches()))
        self._show_current()

    def _select_path(self, path: str):
        for row, symbol in enumerate(self.model.symbols()):
            if symbol.path == path:
                index = self.model.index(row, 0)
                self.grid.setCurrentIndex(index)
                self.grid.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
                break
        self._show_current()

    def _show_current(self):
        symbol = self.selected_symbol()
        if symbol is None:
            self.info.setText("")
        else:
            favorite = " · Favorit" if self._prefs.is_favorite(symbol.path) else ""
            self.info.setText(f"<b>{html.escape(symbol.name)}</b> – {html.escape(symbol.category)}{favorite}")
        self.current_changed.emit(symbol.path if symbol else "")

    def _on_prefs_changed(self):
        searching = bool(self.search.text().strip())
        self.history.set_terms(self._prefs.searches())
        if self._tab in (TAB_FAVORITES, TAB_RECENT) and not searching:
            self._refresh(keep_current=True)
        else:
            # Suchtreffer und Kategorien nicht umsortieren, während man darauf schaut – nur die Sterne
            self.history.setVisible(not searching and bool(self._prefs.searches()))
            self.grid.viewport().update()
            self._show_current()

    # ------------------------------------------------------------------
    # Aktionen
    # ------------------------------------------------------------------

    def _remember(self, path: str):
        text = self.search.text().strip()
        if text:
            self._prefs.add_search(text)
        self._prefs.add_recent(path)

    def _on_index_chosen(self, index: QModelIndex):
        path = index.data(_PATH_ROLE) if index.isValid() else None
        if path:
            self.choose(path)

    def _toggle_current_favorite(self):
        path = self.selected_path()
        if path:
            self._prefs.toggle_favorite(path)

    def _context_menu(self, pos: QPoint):
        index = self.grid.indexAt(pos)
        symbol = self.model.symbol(index.row()) if index.isValid() else None
        if symbol is None:
            return
        self.grid.setCurrentIndex(index)
        menu = QMenu(self.grid)
        choose = menu.addAction(self._mode.choose_label)
        menu.addSeparator()
        favorite = self._prefs.is_favorite(symbol.path)
        star = menu.addAction(
            _star_icon(), "Aus Favoriten entfernen\tStrg+D" if favorite else "Zu Favoriten hinzufügen\tStrg+D"
        )
        forget = clear = None
        if self._tab == TAB_RECENT and not self.search.text().strip():
            menu.addSeparator()
            forget = menu.addAction("Aus „Zuletzt verwendet“ entfernen")
            clear = menu.addAction("„Zuletzt verwendet“ leeren")
        action = menu.exec(self.grid.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action is choose:
            self.choose(symbol.path)
        elif action is star:
            self._prefs.toggle_favorite(symbol.path)
        elif action is forget:
            self._prefs.remove_recent(symbol.path)
        elif action is clear:
            self._prefs.clear_recent()

    def eventFilter(self, watched, event):
        if watched is not self.search and watched is not self.grid:
            return super().eventFilter(watched, event)
        etype = event.type()
        if etype not in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
            return super().eventFilter(watched, event)
        key = event.key()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if ctrl and key == Qt.Key.Key_D:
            # Strg+D gehört hier dem Picker, nicht einem QGIS-Kürzel
            event.accept()
            if etype == QEvent.Type.KeyPress:
                self._toggle_current_favorite()
            return True
        if etype != QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.choose_current()
            return True
        if watched is self.search:
            # Pfeiltasten im Suchfeld bewegen die Auswahl im Raster, ohne dass man das Feld verlassen muss
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                QApplication.sendEvent(self.grid, event)
                return True
            if key == Qt.Key.Key_Escape and self.search.text():
                self.search.clear()
                return True
        return super().eventFilter(watched, event)


class SymbolPickerDialog(QDialog):
    """Auswahldialog (Marker-Tabelle, Import, Drohnenfoto) mit dem gemeinsamen Picker.

    Doppelklick oder Enter übernimmt das Zeichen. Reiter, Kategorie und Suche
    bleiben für die nächste Auswahl erhalten, damit man mehrere ähnliche Zeichen
    schnell hintereinander wählen kann.
    """

    _last_state: tuple[str, str, str] | None = None

    def __init__(self, catalog: SymbolCatalog, parent=None, current_path: str | None = None):
        super().__init__(parent)
        self._path: str | None = None
        self.setWindowTitle("Taktisches Zeichen wählen")
        self.resize(860, 660)

        layout = QVBoxLayout(self)
        self.picker = SymbolPickerWidget(catalog, symbol_prefs(catalog.plugin_dir), self, DIALOG_MODE)
        layout.addWidget(self.picker)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.picker.choose_current)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.picker.current_changed.connect(lambda path: ok.setEnabled(bool(path)))
        self.picker.chosen.connect(self._on_chosen)

        if not self.picker.set_current_path(current_path) and SymbolPickerDialog._last_state:
            self.picker.restore_state(SymbolPickerDialog._last_state)
        ok.setEnabled(bool(self.picker.selected_path()))
        self.picker.focus_search()

    def _on_chosen(self, path: str):
        self._path = path
        SymbolPickerDialog._last_state = self.picker.state()
        self.accept()

    def selected_path(self) -> str | None:
        return self._path

    @classmethod
    def pick(cls, catalog: SymbolCatalog, parent=None, current_path: str | None = None) -> str | None:
        """Zeigt den Picker; gibt den absoluten SVG-Pfad oder None bei Abbruch zurück."""
        dialog = cls(catalog, parent, current_path)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            return dialog.selected_path()
        finally:
            dialog.deleteLater()


def _fit_on_screen(pos: QPoint, size: QSize, above: bool = False) -> QPoint:
    """Popup rechts unter ``pos`` (``above``: darüber); reicht der Platz nicht, auf die andere Seite bzw. nach links."""
    screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
    area = screen.availableGeometry()
    x = min(pos.x(), area.right() - size.width())
    below, over = pos.y(), pos.y() - size.height() - 4
    if above:
        y = over if over >= area.top() else below
    else:
        y = below if below + size.height() <= area.bottom() else over
    return QPoint(max(area.left(), x), max(area.top(), min(y, area.bottom() - size.height())))


class SymbolPickerPopup(QDialog):
    """Schnellauswahl am Mauszeiger, z. B. nach Rechtsklick auf die Karte: ein Klick übernimmt.

    Schließt mit Esc oder einem Klick daneben.
    """

    def __init__(
        self,
        catalog: SymbolCatalog,
        parent=None,
        title: str = "Taktisches Zeichen hinzufügen",
        mode: PickerMode = POPUP_MODE,
    ):
        super().__init__(parent, Qt.WindowType.Popup)
        self._path: str | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame(self)
        frame.setObjectName("thwSymbolPopup")
        frame.setStyleSheet("QFrame#thwSymbolPopup { border: 1px solid palette(mid); background: palette(window); }")
        outer.addWidget(frame)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addWidget(QLabel(f"<b>{html.escape(title)}</b>"))
        self.picker = SymbolPickerWidget(catalog, symbol_prefs(catalog.plugin_dir), frame, mode)
        self.picker.chosen.connect(self._on_chosen)
        layout.addWidget(self.picker)
        self.resize(620, 520)

    def _on_chosen(self, path: str):
        self._path = path
        self.accept()

    @classmethod
    def pick(
        cls,
        catalog: SymbolCatalog,
        global_pos: QPoint,
        parent=None,
        title: str = "Taktisches Zeichen hinzufügen",
        mode: PickerMode = POPUP_MODE,
        above: bool = False,
    ) -> str | None:
        """Popup an ``global_pos`` zeigen (``above``: darüber); gibt den SVG-Pfad oder None (Esc / daneben geklickt)."""
        popup = cls(catalog, parent, title, mode)
        try:
            popup.move(_fit_on_screen(global_pos + QPoint(2, 2), popup.size(), above))
            popup.picker.focus_search()
            if popup.exec() != QDialog.DialogCode.Accepted:
                return None
            return popup._path
        finally:
            popup.deleteLater()
