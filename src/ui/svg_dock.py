import os
import re

from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..logging_utils import get_logger
from .symbol_picker import DOCK_MODE, SymbolPickerWidget, symbol_catalog, symbol_prefs

logger = get_logger(__name__)


class SvgDock(QWidget):
    """Symbolpalette: der gemeinsame Zeichen-Picker (Explorer) und die Zeichen auf der Karte.

    Zeichen werden aus dem Picker auf die Karte gezogen; ein Doppelklick ruft
    ``place_callback(svg_path)`` auf (die Toolbox setzt das Zeichen in die Kartenmitte).
    """

    def __init__(
        self,
        plugin_dir,
        place_callback,
        settings_callback,
        layer_provider=None,
        navigate_callback=None,
    ):
        super().__init__()
        self.plugin_dir = plugin_dir
        self.layer_provider = layer_provider
        self.navigate_callback = navigate_callback
        self.icon_cache = {}  # Cache für Icons

        logger.info(f"Initialisiere SvgDock mit Plugin-Verzeichnis: {plugin_dir}")

        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setLayout(root_layout)

        # Navbar (Explorer | Auf der Karte)
        self.navbar = self._build_navbar()
        root_layout.addWidget(self.navbar)

        # Seiten-Stack
        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack)

        # Seite 0: Explorer (Zeichen-Picker zum Platzieren)
        self.symbols_page = self._build_symbols_page(place_callback, settings_callback)
        self.stack.addWidget(self.symbols_page)

        # Seite 1: Auf der Karte (Liste aller Marker)
        self.start_page = self._build_start_page()
        self.stack.addWidget(self.start_page)

        # Standard-Ansicht: Explorer
        self._select_tab(0)

    # ------------------------------------------------------------------
    # Navbar / Seitenaufbau
    # ------------------------------------------------------------------

    def _build_navbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("thwNavbar")
        bar.setStyleSheet("""
            QWidget#thwNavbar {
                border-bottom: 1px solid palette(mid);
            }
            QPushButton {
                border: none;
                padding: 8px 12px;
                background: transparent;
                font-weight: normal;
            }
            QPushButton:checked {
                border-bottom: 2px solid palette(highlight);
                font-weight: bold;
            }
            QPushButton:hover:!checked {
                background: palette(alternate-base);
            }
        """)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.btn_tab_symbols = QPushButton("Explorer")
        self.btn_tab_symbols.setCheckable(True)
        # „Verwendet“ hieß das früher – zu leicht mit „Zuletzt verwendet“ im Picker zu verwechseln
        self.btn_tab_start = QPushButton("Auf der Karte")
        self.btn_tab_start.setCheckable(True)
        for btn in (self.btn_tab_symbols, self.btn_tab_start):
            btn.setMinimumHeight(32)

        self.tab_group = QButtonGroup(bar)
        self.tab_group.setExclusive(True)
        self.tab_group.addButton(self.btn_tab_symbols, 0)
        self.tab_group.addButton(self.btn_tab_start, 1)
        self.tab_group.idClicked.connect(self._select_tab)

        layout.addWidget(self.btn_tab_symbols, 1)
        layout.addWidget(self.btn_tab_start, 1)
        return bar

    def _select_tab(self, index: int):
        self.stack.setCurrentIndex(index)
        btn = self.tab_group.button(index)
        if btn:
            btn.setChecked(True)
        if index == 1:
            self.refresh_marker_list()

    def _build_start_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)

        self.marker_list = QListWidget()
        self.marker_list.setIconSize(QSize(32, 32))
        self.marker_list.itemActivated.connect(self._on_marker_activated)
        self.marker_list.itemClicked.connect(self._on_marker_activated)
        layout.addWidget(self.marker_list)

        self.btn_refresh_markers = QPushButton("Aktualisieren")
        self.btn_refresh_markers.clicked.connect(self.refresh_marker_list)
        layout.addWidget(self.btn_refresh_markers)
        return page

    def _build_symbols_page(self, place_callback, settings_callback) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)

        self.picker = SymbolPickerWidget(
            symbol_catalog(self.plugin_dir), symbol_prefs(self.plugin_dir), page, DOCK_MODE
        )
        self.picker.chosen.connect(place_callback)
        layout.addWidget(self.picker)

        self.btn_config = QPushButton("Einstellungen")
        self.btn_config.clicked.connect(settings_callback)
        layout.addWidget(self.btn_config)
        return page

    # ------------------------------------------------------------------
    # Seite „Auf der Karte“: Marker-Liste
    # ------------------------------------------------------------------

    def refresh_marker_list(self):
        """Liest alle Features aus dem aktiven Layer und listet sie auf."""
        self.marker_list.clear()
        if not self.layer_provider:
            return
        layer = self.layer_provider()
        if not layer:
            placeholder = QListWidgetItem("Kein Marker-Layer verfügbar")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.marker_list.addItem(placeholder)
            return

        features = list(layer.getFeatures())
        if not features:
            placeholder = QListWidgetItem("Keine Marker auf der Karte")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.marker_list.addItem(placeholder)
            return

        # Natürliche Sortierung nach angezeigtem Namen
        def sort_key(feat):
            return self.natural_sort_key(self._marker_display_name(feat))

        features.sort(key=sort_key)

        for feat in features:
            item = QListWidgetItem(self._marker_display_name(feat))
            icon = self._marker_icon(feat)
            if icon is not None:
                item.setIcon(icon)
            item.setData(Qt.ItemDataRole.UserRole, feat.id())
            self.marker_list.addItem(item)

    def _marker_display_name(self, feat) -> str:
        for attr in ("label", "name"):
            try:
                value = feat.attribute(attr)
            except (KeyError, IndexError):
                value = None
            if value:
                return str(value)
        svg_path = feat.attribute("svg_path") if "svg_path" in feat.fields().names() else None
        if svg_path:
            return os.path.splitext(os.path.basename(svg_path))[0]
        return f"Marker {feat.id()}"

    def _marker_icon(self, feat):
        try:
            svg_path = feat.attribute("svg_path")
        except (KeyError, IndexError):
            return None
        if not svg_path:
            return None
        if not os.path.isabs(svg_path):
            candidate = os.path.join(self.plugin_dir, svg_path)
            if os.path.exists(candidate):
                svg_path = candidate
        if not os.path.exists(svg_path):
            return None
        return self.get_cached_icon(svg_path)

    def _on_marker_activated(self, item: QListWidgetItem):
        fid = item.data(Qt.ItemDataRole.UserRole)
        if fid is None or not self.navigate_callback:
            return
        self.navigate_callback(fid)

    def get_cached_icon(self, path):
        if path not in self.icon_cache:
            self.icon_cache[path] = QIcon(path)
        return self.icon_cache[path]

    def natural_sort_key(self, text):
        """Erstellt einen Sortierschlüssel für natürliche Sortierung (z.B. xx_1, xx_2, xx_10 statt xx_1, xx_10, xx_2)"""

        def convert(text_part):
            return int(text_part) if text_part.isdigit() else text_part.lower()

        return [convert(c) for c in re.split(r"(\d+)", text)]
