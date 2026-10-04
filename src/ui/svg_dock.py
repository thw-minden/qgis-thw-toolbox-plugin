import os
import re

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QMimeData, QPointF, QSize, Qt
from qgis.PyQt.QtGui import QColor, QDrag, QIcon, QPainter, QPen, QPixmap, QPolygonF
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..layer.annotations import (
    ARROWS_BOTH,
    ARROWS_END,
    ARROWS_START,
    KIND_LINE,
    KIND_POINT,
    KIND_POLYGON,
    AnnotationEntry,
)
from ..logging_utils import get_logger

logger = get_logger(__name__)


class SvgDock(QWidget):
    def __init__(
        self,
        plugin_dir,
        select_callback,
        settings_callback,
        layer_provider=None,
        navigate_callback=None,
        progress_callback=None,
        annotation_provider=None,
        annotation_callback=None,
        annotation_export_callback=None,
        annotation_mbtiles_callback=None,
    ):
        super().__init__()
        self.plugin_dir = plugin_dir
        self.select_callback = select_callback
        self.layer_provider = layer_provider
        self.navigate_callback = navigate_callback
        # Callable returning list[AnnotationEntry] (or None if no annotation layer exists)
        self.annotation_provider = annotation_provider
        # Called with the item id when an annotation is clicked
        self.annotation_callback = annotation_callback
        # Called with a polygon's item id to export that polygon (KMZ)
        self.annotation_export_callback = annotation_export_callback
        # Called without arguments to export all annotations as MBTiles
        self.annotation_mbtiles_callback = annotation_mbtiles_callback
        self.icon_cache = {}  # Cache für Icons
        self._initial_progress_callback = progress_callback

        logger.info(f"Initialisiere SvgDock mit Plugin-Verzeichnis: {plugin_dir}")

        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setLayout(root_layout)

        # Navbar (Start | Taktische Zeichen)
        self.navbar = self._build_navbar()
        root_layout.addWidget(self.navbar)

        # Seiten-Stack
        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack)

        # Seite 0: Explorer (SVG-Baum zum Platzieren)
        self.symbols_page = self._build_symbols_page(settings_callback)
        self.stack.addWidget(self.symbols_page)

        # Seite 1: Verwendet (Liste aller Marker auf der Karte)
        self.start_page = self._build_start_page()
        self.stack.addWidget(self.start_page)

        # Seite 2: Annotationen (Punkte, Linien, Polygone)
        self.annotations_page = self._build_annotations_page()
        self.stack.addWidget(self.annotations_page)

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
        self.btn_tab_start = QPushButton("Verwendet")
        self.btn_tab_start.setCheckable(True)
        self.btn_tab_annotations = QPushButton("Annotationen")
        self.btn_tab_annotations.setCheckable(True)
        for btn in (self.btn_tab_symbols, self.btn_tab_start, self.btn_tab_annotations):
            btn.setMinimumHeight(32)

        self.tab_group = QButtonGroup(bar)
        self.tab_group.setExclusive(True)
        self.tab_group.addButton(self.btn_tab_symbols, 0)
        self.tab_group.addButton(self.btn_tab_start, 1)
        self.tab_group.addButton(self.btn_tab_annotations, 2)
        self.tab_group.idClicked.connect(self._select_tab)

        layout.addWidget(self.btn_tab_symbols, 1)
        layout.addWidget(self.btn_tab_start, 1)
        layout.addWidget(self.btn_tab_annotations, 1)
        return bar

    def _select_tab(self, index: int):
        self.stack.setCurrentIndex(index)
        btn = self.tab_group.button(index)
        if btn:
            btn.setChecked(True)
        if index == 1:
            self.refresh_marker_list()
        elif index == 2:
            self.refresh_annotation_list()

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

    def _build_symbols_page(self, settings_callback) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)

        # Suchleiste
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Symbol suchen...")
        self.search_box.textChanged.connect(self.on_search)
        layout.addWidget(self.search_box)

        self.treeWidget = QTreeWidget()
        self.treeWidget.setHeaderLabel("Taktische Zeichen")
        self.treeWidget.setDragEnabled(True)
        self.treeWidget.setIconSize(QSize(48, 48))
        self.treeWidget.setIndentation(16)
        self.treeWidget.setColumnCount(1)
        self.treeWidget.setSortingEnabled(False)
        self.treeWidget.setRootIsDecorated(True)
        self.treeWidget.setStyleSheet("""
            QTreeWidget {
                border: none;
                outline: none;
            }
            QTreeWidget::item {
                padding: 2px 0px;
            }
            QTreeWidget::branch:has-children:!has-siblings:closed,
            QTreeWidget::branch:closed:has-children:has-siblings {
                border-image: none;
            }
            QTreeWidget::branch:open:has-children:!has-siblings,
            QTreeWidget::branch:open:has-children:has-siblings {
                border-image: none;
            }
        """)
        layout.addWidget(self.treeWidget)

        self.populate_root_folders(progress_callback=self._initial_progress_callback)
        self.treeWidget.itemPressed.connect(self.on_item_pressed)
        self.treeWidget.itemExpanded.connect(self.on_item_expanded)

        self.btn_config = QPushButton("Einstellungen")
        self.btn_config.clicked.connect(settings_callback)
        layout.addWidget(self.btn_config)
        return page

    # ------------------------------------------------------------------
    # Start-Seite: Marker-Liste
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

    # ------------------------------------------------------------------
    # Annotationen-Seite
    # ------------------------------------------------------------------

    def _build_annotations_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)

        self.annotation_list = QListWidget()
        self.annotation_list.setIconSize(QSize(24, 24))
        self.annotation_list.itemClicked.connect(self._on_annotation_activated)
        layout.addWidget(self.annotation_list)

        self.btn_refresh_annotations = QPushButton("Aktualisieren")
        self.btn_refresh_annotations.clicked.connect(self.refresh_annotation_list)
        layout.addWidget(self.btn_refresh_annotations)

        self.btn_export_mbtiles = QPushButton("Alle Annotationen als MBTiles exportieren")
        self.btn_export_mbtiles.setToolTip(
            "Alle Punkte, Linien, Polygone und Beschreibungen als Kachel-Ebene (MBTiles) speichern, z. B. für Drohnen"
        )
        self.btn_export_mbtiles.clicked.connect(lambda: self.annotation_mbtiles_callback())
        self.btn_export_mbtiles.setVisible(self.annotation_mbtiles_callback is not None)
        layout.addWidget(self.btn_export_mbtiles)
        return page

    def refresh_annotation_list(self):
        """Listet alle Objekte des Annotations-Layers auf."""
        self.annotation_list.clear()
        entries = self.annotation_provider() if self.annotation_provider else None
        if not entries:
            placeholder = QListWidgetItem("Keine Annotationen auf der Karte")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.annotation_list.addItem(placeholder)
            return

        for entry in entries:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, entry.item_id)
            item.setToolTip(entry.title)
            row = self._annotation_row(entry)
            item.setSizeHint(row.sizeHint())
            self.annotation_list.addItem(item)
            self.annotation_list.setItemWidget(item, row)

    def _annotation_row(self, entry: AnnotationEntry) -> QWidget:
        """Row widget: preview icon, title and — for polygons — a KMZ export button.

        Icon and title ignore the mouse, so clicks on them reach the list (opening the
        edit dialog); the export button handles its own click.
        """
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        icon = QLabel()
        icon.setPixmap(_annotation_icon(entry).pixmap(24, 24))
        icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(icon)

        title = QLabel(entry.title)
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # Long descriptions are clipped instead of widening the dock (full text in the tooltip)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(title, 1)

        if entry.kind == KIND_POLYGON and self.annotation_export_callback is not None:
            button = QToolButton()
            export_icon = QgsApplication.getThemeIcon("/mActionSharingExport.svg")
            if export_icon.isNull():
                button.setText("KMZ")
            else:
                button.setIcon(export_icon)
            button.setAutoRaise(True)
            button.setToolTip("Dieses Polygon als KMZ exportieren (z. B. für Drohnen)")
            button.clicked.connect(
                lambda _checked=False, item_id=entry.item_id: self.annotation_export_callback(item_id)
            )
            layout.addWidget(button)
        return row

    def _on_annotation_activated(self, item: QListWidgetItem):
        item_id = item.data(Qt.ItemDataRole.UserRole)
        if item_id is None or not self.annotation_callback:
            return
        self.annotation_callback(item_id)

    # ------------------------------------------------------------------
    # Symbols-Seite (unverändert gegenüber vorher)
    # ------------------------------------------------------------------

    def _set_category_font(self, item):
        """Setzt die Schrift für Kategorie-Einträge (fett) zur visuellen Unterscheidung."""
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)

    def get_cached_icon(self, path):
        if path not in self.icon_cache:
            self.icon_cache[path] = QIcon(path)
        return self.icon_cache[path]

    def natural_sort_key(self, text):
        """Erstellt einen Sortierschlüssel für natürliche Sortierung (z.B. xx_1, xx_2, xx_10 statt xx_1, xx_10, xx_2)"""

        def convert(text_part):
            return int(text_part) if text_part.isdigit() else text_part.lower()

        return [convert(c) for c in re.split(r"(\d+)", text)]

    def get_category_folders(self):
        categories = {
            "Allgemein": {
                "Einheiten": "Einheiten",
                "Einrichtungen": "Einrichtungen",
                "Fahrzeuge": "Fahrzeuge",
                "Fernmeldewesen": "Fernmeldewesen",
                "Gebäude": "Gebäude",
                "Gefahren": "Gefahren",
                "Führungsstellen": "Führungsstellen",
                "Maßnahmen": "Maßnahmen",
                "Personen": "Personen",
                "Schäden": "Schäden",
                "Schadenskonten": {
                    "gelb": {"folder": "Schadenskonten/gelb", "filter": "Schadenskonto"},
                    "rot": {"folder": "Schadenskonten/rot", "filter": "Schadenskonto"},
                    "weiß": {"folder": "Schadenskonten/weiß", "filter": "Schadenskonto"},
                },
                "Schadensstellen": {
                    "gelb": {"folder": "Schadenskonten/gelb", "filter": "Schadensstelle"},
                    "rot": {"folder": "Schadenskonten/rot", "filter": "Schadensstelle"},
                    "weiß": {"folder": "Schadenskonten/weiß", "filter": "Schadensstelle"},
                },
                "Sonstiges": "Sonstiges",
            },
            "THW": {
                "Einheiten": "THW_Einheiten",
                "Fahrzeuge": "THW_Fahrzeuge",
                "Gebäude": "THW_Gebäude",
                "Personen": "THW_Personen",
            },
            "Weitere Einheiten": {
                "Bundeswehr": {
                    "Einheiten": "Bundeswehr_Einheiten",
                    "Fahrzeuge": "Bundeswehr_Fahrzeuge",
                    "Personen": "Bundeswehr_Personen",
                },
                "Feuerwehr": {
                    "Einheiten": "Feuerwehr_Einheiten",
                    "Fahrzeuge": "Feuerwehr_Fahrzeuge",
                    "Gebäude": "Feuerwehr_Gebäude",
                    "Personen": "Feuerwehr_Personen",
                },
                "Rettungswesen": {
                    "Einheiten": "Rettungswesen_Einheiten",
                    "Einrichtungen": "Rettungswesen_Einrichtungen",
                    "Fahrzeuge": "Rettungswesen_Fahrzeuge",
                    "Personen": "Rettungswesen_Personen",
                },
                "Wasserrettung": {
                    "Einheiten": "Wasserrettung_Einheiten",
                    "Einrichtungen": "Wasserrettung_Einrichtungen",
                    "Fahrzeuge": "Wasserrettung_Fahrzeuge",
                    "Gebäude": "Wasserrettung_Gebäude",
                    "Personen": "Wasserrettung_Personen",
                },
                "Polizei": {"Einheiten": "Polizei_Einheiten", "Fahrzeuge": "Polizei_Fahrzeuge"},
                "Zoll": {"Einheiten": "Zoll_Einheiten", "Fahrzeuge": "Zoll_Fahrzeuge"},
                "Katastrophenschutz": {
                    "Einheiten": "Katastrophenschutz_Einheiten",
                    "Fahrzeuge": "Katastrophenschutz_Fahrzeuge",
                },
            },
        }
        return categories

    def populate_root_folders(self, progress_callback=None):
        self.treeWidget.clear()
        self.treeWidget.setSortingEnabled(False)

        svg_path = os.path.join(self.plugin_dir, "svgs")
        logger.info(f"Plugin-Verzeichnis: {self.plugin_dir}")
        logger.info(f"SVG-Pfad: {svg_path}")

        if not os.path.exists(svg_path):
            logger.error(f"SVG-Pfad existiert nicht: {svg_path}")
            return

        categories = self.get_category_folders()
        # Items in eager-loaded categories (Allgemein/THW) whose SVG files we
        # still need to populate — collected so we can report per-subfolder
        # progress in a second pass.
        pending_loads: list[QTreeWidgetItem] = []

        for category, subcategories in categories.items():
            category_item = QTreeWidgetItem(self.treeWidget)
            category_item.setText(0, category)
            self._set_category_font(category_item)

            if isinstance(subcategories, dict):
                sorted_subcategories = sorted(subcategories.items(), key=lambda x: self.natural_sort_key(x[0]))
                for subcategory, folder_name in sorted_subcategories:
                    subcategory_item = QTreeWidgetItem(category_item)
                    subcategory_item.setText(0, subcategory)

                    if isinstance(folder_name, dict):
                        first_value = list(folder_name.values())[0] if folder_name else ""
                        if isinstance(first_value, dict):
                            base_folder = first_value["folder"].split("/")[0]
                        elif isinstance(first_value, str):
                            base_folder = first_value.split("/")[0] if "/" in first_value else subcategory
                        else:
                            base_folder = subcategory
                        subcategory_item.setData(0, Qt.ItemDataRole.UserRole, base_folder)
                        for subsubcategory, actual_folder in folder_name.items():
                            subsubcategory_item = QTreeWidgetItem(subcategory_item)
                            subsubcategory_item.setText(0, subsubcategory)
                            if isinstance(actual_folder, dict):
                                subsubcategory_item.setData(0, Qt.ItemDataRole.UserRole, actual_folder["folder"])
                                subsubcategory_item.setData(0, Qt.ItemDataRole.UserRole + 1, actual_folder["filter"])
                            else:
                                subsubcategory_item.setData(0, Qt.ItemDataRole.UserRole, actual_folder)
                            placeholder = QTreeWidgetItem(subsubcategory_item)
                            placeholder.setText(0, "Laden...")
                    else:
                        subcategory_item.setData(0, Qt.ItemDataRole.UserRole, folder_name)
                        placeholder = QTreeWidgetItem(subcategory_item)
                        placeholder.setText(0, "Laden...")

            if category in ["Allgemein", "THW"]:
                self.treeWidget.expandItem(category_item)
                for i in range(category_item.childCount()):
                    child = category_item.child(i)
                    if child.childCount() > 0 and child.child(0).text(0) != "Laden...":
                        self.treeWidget.expandItem(child)
                        for j in range(child.childCount()):
                            pending_loads.append(child.child(j))
                    else:
                        folder_name = child.data(0, Qt.ItemDataRole.UserRole)
                        if folder_name:
                            folder_path = os.path.join(self.plugin_dir, "svgs", folder_name)
                            if os.path.exists(folder_path):
                                subdirs = [
                                    d for d in os.listdir(folder_path) if os.path.isdir(os.path.join(folder_path, d))
                                ]
                                if subdirs:
                                    self.populate_subfolders(child, folder_path, subdirs)
                                    self.treeWidget.expandItem(child)
                                    for j in range(child.childCount()):
                                        pending_loads.append(child.child(j))
                                else:
                                    pending_loads.append(child)

        total = len(pending_loads)
        for idx, item in enumerate(pending_loads):
            if progress_callback:
                progress_callback(idx, total, item.text(0))
            self.populate_svg_files(item)
        if progress_callback:
            progress_callback(total, total, "")

    def on_item_expanded(self, item):
        if item.childCount() == 1 and item.child(0).text(0) == "Laden...":
            item.removeChild(item.child(0))
        elif item.childCount() > 0:
            return

        if item.parent() is None:
            if item.text(0) == "Weitere Einheiten":
                self.populate_other_units(item)
            else:
                self.populate_category(item)
        else:
            parent = item.parent()
            if parent and parent.text(0) == "Weitere Einheiten":
                categories = self.get_category_folders()["Weitere Einheiten"]
                subcategory_name = item.text(0)
                if subcategory_name in categories:
                    subfolders = categories[subcategory_name]
                    if item.childCount() == 1 and item.child(0).text(0) == "Laden...":
                        item.removeChild(item.child(0))
                    sorted_subfolders = sorted(subfolders.items(), key=lambda x: self.natural_sort_key(x[0]))
                    for subsubcategory, folder_name in sorted_subfolders:
                        subsubcategory_item = QTreeWidgetItem(item)
                        subsubcategory_item.setText(0, subsubcategory)
                        subsubcategory_item.setData(0, Qt.ItemDataRole.UserRole, folder_name)
                        placeholder = QTreeWidgetItem(subsubcategory_item)
                        placeholder.setText(0, "Laden...")
                return

            folder_name = item.data(0, Qt.ItemDataRole.UserRole)
            if folder_name:
                folder_path = os.path.join(self.plugin_dir, "svgs", folder_name)
                if os.path.exists(folder_path):
                    subdirs = [d for d in os.listdir(folder_path) if os.path.isdir(os.path.join(folder_path, d))]
                    if subdirs:
                        self.populate_subfolders(item, folder_path, subdirs)
                        return
            self.populate_svg_files(item)

    def populate_other_units(self, category_item):
        while category_item.childCount() > 0:
            category_item.removeChild(category_item.child(0))

        categories = self.get_category_folders()["Weitere Einheiten"]
        sorted_categories = sorted(categories.items(), key=lambda x: self.natural_sort_key(x[0]))

        for subcategory, subfolders in sorted_categories:
            subcategory_item = QTreeWidgetItem(category_item)
            subcategory_item.setText(0, subcategory)
            subcategory_item.setData(0, Qt.ItemDataRole.UserRole, None)
            placeholder = QTreeWidgetItem(subcategory_item)
            placeholder.setText(0, "Laden...")

    def populate_category(self, category_item):
        while category_item.childCount() > 0:
            category_item.removeChild(category_item.child(0))

        category_name = category_item.text(0)
        categories = self.get_category_folders()

        if category_name in categories:
            svg_path = os.path.join(self.plugin_dir, "svgs")
            subfolders = categories[category_name]

            if isinstance(subfolders, dict):
                sorted_subfolders = sorted(subfolders.keys(), key=self.natural_sort_key)
            else:
                sorted_subfolders = sorted(subfolders, key=self.natural_sort_key)

            for subfolder in sorted_subfolders:
                folder_name = subfolders[subfolder] if isinstance(subfolders, dict) else subfolder

                if isinstance(folder_name, dict):
                    subfolder_item = QTreeWidgetItem(category_item)
                    subfolder_item.setText(0, subfolder)
                    first_value = list(folder_name.values())[0] if folder_name else ""
                    if isinstance(first_value, dict):
                        base_folder = first_value["folder"].split("/")[0]
                    elif isinstance(first_value, str):
                        base_folder = first_value.split("/")[0] if "/" in first_value else subfolder
                    else:
                        base_folder = subfolder
                    subfolder_item.setData(0, Qt.ItemDataRole.UserRole, base_folder)
                    for subsubcategory, actual_folder in folder_name.items():
                        subsubcategory_item = QTreeWidgetItem(subfolder_item)
                        subsubcategory_item.setText(0, subsubcategory)
                        if isinstance(actual_folder, dict):
                            subsubcategory_item.setData(0, Qt.ItemDataRole.UserRole, actual_folder["folder"])
                            subsubcategory_item.setData(0, Qt.ItemDataRole.UserRole + 1, actual_folder["filter"])
                        else:
                            subsubcategory_item.setData(0, Qt.ItemDataRole.UserRole, actual_folder)
                        placeholder = QTreeWidgetItem(subsubcategory_item)
                        placeholder.setText(0, "Laden...")
                else:
                    folder_path = os.path.join(svg_path, folder_name)
                    if os.path.exists(folder_path):
                        subfolder_item = QTreeWidgetItem(category_item)
                        display_name = folder_name.split("_", 1)[1] if "_" in folder_name else folder_name
                        subfolder_item.setText(0, display_name)
                        subfolder_item.setData(0, Qt.ItemDataRole.UserRole, folder_name)
                        placeholder = QTreeWidgetItem(subfolder_item)
                        placeholder.setText(0, "Laden...")

    def populate_subfolders(self, parent_item, base_folder_path, subdirs):
        """Lädt Unterordner in einem Ordner (z.B. gelb, rot, weiß in Schadenskonten)"""
        while parent_item.childCount() > 0:
            parent_item.removeChild(parent_item.child(0))

        subdirs.sort(key=self.natural_sort_key)

        base_folder_name = parent_item.data(0, Qt.ItemDataRole.UserRole)

        for subdir in subdirs:
            subfolder_item = QTreeWidgetItem(parent_item)
            subfolder_item.setText(0, subdir)
            full_folder_path = f"{base_folder_name}/{subdir}" if base_folder_name else subdir
            subfolder_item.setData(0, Qt.ItemDataRole.UserRole, full_folder_path)
            placeholder = QTreeWidgetItem(subfolder_item)
            placeholder.setText(0, "Laden...")

    def populate_svg_files(self, subfolder_item):
        while subfolder_item.childCount() > 0:
            subfolder_item.removeChild(subfolder_item.child(0))

        folder_name = subfolder_item.data(0, Qt.ItemDataRole.UserRole)
        if not folder_name:
            return

        file_filter = subfolder_item.data(0, Qt.ItemDataRole.UserRole + 1)

        folder_path = os.path.join(self.plugin_dir, "svgs", folder_name)
        logger.info(f"Suche Symbole in: {folder_path}")

        try:
            if os.path.exists(folder_path):
                logger.info(f"Ordner existiert: {folder_path}")
                files = [f for f in os.listdir(folder_path) if f.endswith(".svg")]
                if file_filter:
                    files = [
                        f
                        for f in files
                        if os.path.splitext(f)[0] == file_filter or os.path.splitext(f)[0].startswith(file_filter + "_")
                    ]
                logger.info(f"Gefundene SVG-Dateien: {files}")
                files.sort(key=self.natural_sort_key)

                for file in files:
                    full_path = os.path.join(folder_path, file)
                    symbol_item = QTreeWidgetItem(subfolder_item)
                    display_name = os.path.splitext(file)[0]
                    symbol_item.setText(0, display_name)
                    symbol_item.setIcon(0, self.get_cached_icon(full_path))
                    symbol_item.setData(0, Qt.ItemDataRole.UserRole, full_path)
            else:
                logger.warning(f"Ordner existiert NICHT: {folder_path}")
        except Exception as e:
            logger.error(f"Fehler beim Lesen des Ordners {folder_path}: {str(e)}")

    def on_item_pressed(self, item):
        svg_path = item.data(0, Qt.ItemDataRole.UserRole)
        if svg_path:
            self.select_callback(svg_path)
            drag = QDrag(self)
            mime = QMimeData()
            mime.setText(svg_path)
            drag.setMimeData(mime)
            drag.setPixmap(QPixmap(svg_path).scaled(48, 48))
            drag.exec(Qt.DropAction.CopyAction)

    def on_search(self, text):
        logger.debug("on_search aufgerufen mit: %s", text)
        if not text:
            self.populate_root_folders()
            return

        self.treeWidget.clear()
        self.treeWidget.setSortingEnabled(False)

        svg_path = os.path.join(self.plugin_dir, "svgs")
        thw_treffer = []
        andere_treffer = []

        for root, dirs, files in os.walk(svg_path):
            for file in files:
                if file.endswith(".svg"):
                    display_name = os.path.splitext(file)[0].replace("_", " ")
                    if text.lower() in file.lower() or text.lower() in display_name.lower():
                        full_path = os.path.join(root, file)
                        rel_path = os.path.relpath(root, svg_path)
                        if rel_path.startswith("THW_"):
                            thw_treffer.append((display_name, full_path))
                        else:
                            andere_treffer.append((display_name, full_path))

        for display_name, full_path in thw_treffer + andere_treffer:
            symbol_item = QTreeWidgetItem(self.treeWidget)
            symbol_item.setText(0, display_name)
            symbol_item.setIcon(0, self.get_cached_icon(full_path))
            symbol_item.setData(0, Qt.ItemDataRole.UserRole, full_path)

        if not thw_treffer and not andere_treffer:
            kein_treffer = QTreeWidgetItem(self.treeWidget)
            kein_treffer.setText(0, "Keine Treffer gefunden")
            kein_treffer.setIcon(0, QIcon.fromTheme("dialog-error"))

        self.treeWidget.repaint()


def _annotation_icon(entry: AnnotationEntry) -> QIcon:
    """Kleines Vorschau-Symbol in den Farben der Annotation."""
    size = 24
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(entry.line_color, 2.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

    if entry.kind == KIND_POINT:
        painter.setPen(QPen(QColor(255, 255, 255), 1.5))
        painter.setBrush(entry.line_color)
        painter.drawEllipse(QPointF(size / 2, size / 2), 7, 7)
    elif entry.kind == KIND_LINE:
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF([QPointF(3, 19), QPointF(9, 8), QPointF(15, 15), QPointF(21, 5)]))
        painter.setBrush(entry.line_color)
        painter.setPen(Qt.PenStyle.NoPen)
        if entry.arrows in (ARROWS_END, ARROWS_BOTH):
            painter.drawPolygon(QPolygonF([QPointF(22, 3), QPointF(21.5, 10), QPointF(16, 6)]))
        if entry.arrows in (ARROWS_START, ARROWS_BOTH):
            painter.drawPolygon(QPolygonF([QPointF(2, 21), QPointF(2.5, 14), QPointF(8, 18.5)]))
    elif entry.kind == KIND_POLYGON:
        painter.setPen(pen)
        painter.setBrush(entry.fill_color if entry.fill_color is not None else Qt.BrushStyle.NoBrush)
        painter.drawPolygon(
            QPolygonF([QPointF(4, 7), QPointF(14, 3), QPointF(21, 11), QPointF(16, 21), QPointF(5, 18)])
        )
    else:
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(18)
        painter.setFont(font)
        painter.setPen(entry.line_color)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "T")

    painter.end()
    return QIcon(pixmap)
