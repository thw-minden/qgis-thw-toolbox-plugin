import os
import logging
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QListWidget, QListWidgetItem, 
                           QLabel, QTreeWidget, QTreeWidgetItem, QLineEdit)
from PyQt5.QtGui import QIcon, QDrag, QPixmap
from PyQt5.QtCore import Qt, QSize, QMimeData

# Logging-Konfiguration
logging.basicConfig(
    filename=os.path.join(os.path.dirname(__file__), 'svg_dock.log'),
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class SvgDock(QWidget):
    def __init__(self, plugin_dir, select_callback):
        super().__init__()
        self.plugin_dir = plugin_dir
        self.select_callback = select_callback
        self.icon_cache = {}  # Cache für Icons

        logging.info(f"Initialisiere SvgDock mit Plugin-Verzeichnis: {plugin_dir}")

        layout = QVBoxLayout()
        self.setLayout(layout)

        # Suchleiste hinzufügen
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Symbol suchen...")
        self.search_box.textChanged.connect(self.on_search)
        layout.addWidget(self.search_box)

        self.treeWidget = QTreeWidget()
        self.treeWidget.setHeaderLabel("Taktische Zeichen")
        self.treeWidget.setDragEnabled(True)
        self.treeWidget.setIconSize(QSize(48, 48))
        self.treeWidget.setIndentation(20)
        self.treeWidget.setColumnCount(1)
        self.treeWidget.setSortingEnabled(True)
        self.treeWidget.sortItems(0, Qt.AscendingOrder)  # Sortiere aufsteigend

        layout.addWidget(self.treeWidget)

        # Nur die Hauptordner beim Start laden
        self.populate_root_folders()
        self.treeWidget.itemPressed.connect(self.on_item_pressed)
        self.treeWidget.itemExpanded.connect(self.on_item_expanded)

    def get_cached_icon(self, path):
        if path not in self.icon_cache:
            self.icon_cache[path] = QIcon(path)
        return self.icon_cache[path]

    def get_category_folders(self):
        # Definiere die Hauptkategorien und ihre zugehörigen Ordner
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
                "Schadenskonten": "Schadenskonten",
                "Sonstiges": "Sonstiges"
            },
            "THW": {
                "Einheiten": "THW_Einheiten",
                "Fahrzeuge": "THW_Fahrzeuge",
                "Gebäude": "THW_Gebäude",
                "Personen": "THW_Personen"
            },
            "Weitere Einheiten": {
                "Bundeswehr": {
                    "Einheiten": "Bundeswehr_Einheiten",
                    "Fahrzeuge": "Bundeswehr_Fahrzeuge",
                    "Personen": "Bundeswehr_Personen"
                },
                "Feuerwehr": {
                    "Einheiten": "Feuerwehr_Einheiten",
                    "Fahrzeuge": "Feuerwehr_Fahrzeuge",
                    "Gebäude": "Feuerwehr_Gebäude",
                    "Personen": "Feuerwehr_Personen"
                },
                "Rettungswesen": {
                    "Einheiten": "Rettungswesen_Einheiten",
                    "Einrichtungen": "Rettungswesen_Einrichtungen",
                    "Fahrzeuge": "Rettungswesen_Fahrzeuge",
                    "Personen": "Rettungswesen_Personen"
                },
                "Wasserrettung": {
                    "Einheiten": "Wasserrettung_Einheiten",
                    "Einrichtungen": "Wasserrettung_Einrichtungen",
                    "Fahrzeuge": "Wasserrettung_Fahrzeuge",
                    "Gebäude": "Wasserrettung_Gebäude",
                    "Personen": "Wasserrettung_Personen"
                },
                "Polizei": {
                    "Einheiten": "Polizei_Einheiten",
                    "Fahrzeuge": "Polizei_Fahrzeuge"
                },
                "Zoll": {
                    "Einheiten": "Zoll_Einheiten",
                    "Fahrzeuge": "Zoll_Fahrzeuge"
                },
                "Katastrophenschutz": {
                    "Einheiten": "Katastrophenschutz_Einheiten",
                    "Fahrzeuge": "Katastrophenschutz_Fahrzeuge"
                }
            }
        }
        return categories

    def populate_root_folders(self):
        # Lösche zuerst alle vorhandenen Einträge
        self.treeWidget.clear()
        self.treeWidget.setSortingEnabled(False)  # Deaktiviere Sortierung während des Aufbaus
        
        svg_path = os.path.join(self.plugin_dir, "svgs")
        logging.info(f"Plugin-Verzeichnis: {self.plugin_dir}")
        logging.info(f"SVG-Pfad: {svg_path}")
        
        if not os.path.exists(svg_path):
            logging.error(f"SVG-Pfad existiert nicht: {svg_path}")
            return

        categories = self.get_category_folders()
        
        # Erstelle die Hauptkategorien
        for category, subcategories in categories.items():
            category_item = QTreeWidgetItem(self.treeWidget)
            category_item.setText(0, category)
            category_item.setIcon(0, QIcon.fromTheme("folder"))
            
            # Füge Unterkategorien hinzu
            if isinstance(subcategories, dict):
                for subcategory, folder_name in subcategories.items():
                    subcategory_item = QTreeWidgetItem(category_item)
                    subcategory_item.setText(0, subcategory)
                    subcategory_item.setIcon(0, QIcon.fromTheme("folder"))
                    
                    if isinstance(folder_name, dict):  # Für verschachtelte Kategorien
                        for subsubcategory, actual_folder in folder_name.items():
                            subsubcategory_item = QTreeWidgetItem(subcategory_item)
                            subsubcategory_item.setText(0, subsubcategory)
                            subsubcategory_item.setIcon(0, QIcon.fromTheme("folder"))
                            subsubcategory_item.setData(0, Qt.UserRole, actual_folder)
                            placeholder = QTreeWidgetItem(subsubcategory_item)
                            placeholder.setText(0, "Laden...")
                    else:  # Für direkte Kategorien
                        subcategory_item.setData(0, Qt.UserRole, folder_name)
                        placeholder = QTreeWidgetItem(subcategory_item)
                        placeholder.setText(0, "Laden...")
            
            # Öffne die Ordner "Allgemein" und "THW" automatisch
            if category in ["Allgemein", "THW"]:
                self.treeWidget.expandItem(category_item)
                # Lade auch die Unterordner
                for i in range(category_item.childCount()):
                    child = category_item.child(i)
                    self.populate_svg_files(child)
        
        self.treeWidget.setSortingEnabled(True)  # Aktiviere Sortierung wieder

    def on_item_expanded(self, item):
        # Entferne den Platzhalter
        if item.childCount() == 1 and item.child(0).text(0) == "Laden...":
            item.removeChild(item.child(0))
            
        # Prüfe, ob es sich um eine Hauptkategorie oder einen Unterordner handelt
        if item.parent() is None:  # Hauptkategorie
            if item.text(0) == "Weitere Einheiten":
                self.populate_other_units(item)
            else:
                self.populate_category(item)
        else:  # Unterordner
            self.populate_svg_files(item)

    def populate_other_units(self, category_item):
        # Entferne alle vorhandenen Kinder
        while category_item.childCount() > 0:
            category_item.removeChild(category_item.child(0))
            
        categories = self.get_category_folders()["Weitere Einheiten"]
        
        for subcategory, subfolders in categories.items():
            subcategory_item = QTreeWidgetItem(category_item)
            subcategory_item.setText(0, subcategory)
            subcategory_item.setIcon(0, QIcon.fromTheme("folder"))
            placeholder = QTreeWidgetItem(subcategory_item)
            placeholder.setText(0, "Laden...")

    def populate_category(self, category_item):
        # Entferne alle vorhandenen Kinder
        while category_item.childCount() > 0:
            category_item.removeChild(category_item.child(0))
            
        category_name = category_item.text(0)
        categories = self.get_category_folders()
        
        if category_name in categories:
            svg_path = os.path.join(self.plugin_dir, "svgs")
            subfolders = categories[category_name]
            
            # Sortiere die Unterordner-Namen
            if isinstance(subfolders, dict):
                sorted_subfolders = sorted(subfolders.keys())
            else:
                sorted_subfolders = sorted(subfolders)
            
            for subfolder in sorted_subfolders:
                folder_name = subfolders[subfolder] if isinstance(subfolders, dict) else subfolder
                folder_path = os.path.join(svg_path, folder_name)
                if os.path.exists(folder_path):
                    # Erstelle einen Unterordner-Eintrag
                    subfolder_item = QTreeWidgetItem(category_item)
                    # Entferne den Präfix (z.B. "Bundeswehr_") aus dem Namen
                    display_name = folder_name.split('_', 1)[1] if '_' in folder_name else folder_name
                    subfolder_item.setText(0, display_name)
                    subfolder_item.setIcon(0, QIcon.fromTheme("folder"))
                    subfolder_item.setData(0, Qt.UserRole, folder_name)
                    # Setze einen Platzhalter für die SVG-Dateien
                    placeholder = QTreeWidgetItem(subfolder_item)
                    placeholder.setText(0, "Laden...")

    def populate_svg_files(self, subfolder_item):
        # Entferne alle vorhandenen Kinder
        while subfolder_item.childCount() > 0:
            subfolder_item.removeChild(subfolder_item.child(0))
            
        folder_name = subfolder_item.data(0, Qt.UserRole)
        if not folder_name:
            return
            
        folder_path = os.path.join(self.plugin_dir, "svgs", folder_name)
        logging.info(f"Suche Symbole in: {folder_path}")
        
        try:
            if os.path.exists(folder_path):
                logging.info(f"Ordner existiert: {folder_path}")
                files = [f for f in os.listdir(folder_path) if f.endswith(".svg")]
                logging.info(f"Gefundene SVG-Dateien: {files}")
                files.sort()
                
                for file in files:
                    full_path = os.path.join(folder_path, file)
                    symbol_item = QTreeWidgetItem(subfolder_item)
                    symbol_item.setText(0, os.path.splitext(file)[0])
                    symbol_item.setIcon(0, self.get_cached_icon(full_path))
                    symbol_item.setData(0, Qt.UserRole, full_path)
            else:
                logging.warning(f"Ordner existiert NICHT: {folder_path}")
        except Exception as e:
            logging.error(f"Fehler beim Lesen des Ordners {folder_path}: {str(e)}")

    def on_item_pressed(self, item):
        svg_path = item.data(0, Qt.UserRole)
        if svg_path:
            self.select_callback(svg_path)
            drag = QDrag(self)
            mime = QMimeData()
            mime.setText(svg_path)
            drag.setMimeData(mime)
            drag.setPixmap(QPixmap(svg_path).scaled(48, 48))
            drag.exec_(Qt.CopyAction)

    def on_search(self, text):
        print("on_search wurde aufgerufen mit:", text)
        if not text:
            self.populate_root_folders()
            return

        self.treeWidget.clear()
        self.treeWidget.setSortingEnabled(False)

        svg_path = os.path.join(self.plugin_dir, "svgs")
        treffer = 0

        for root, dirs, files in os.walk(svg_path):
            for file in files:
                if file.endswith(".svg"):
                    display_name = os.path.splitext(file)[0].replace("_", " ")
                    if text.lower() in file.lower() or text.lower() in display_name.lower():
                        full_path = os.path.join(root, file)
                        symbol_item = QTreeWidgetItem(self.treeWidget)
                        symbol_item.setText(0, display_name)
                        symbol_item.setIcon(0, self.get_cached_icon(full_path))
                        symbol_item.setData(0, Qt.UserRole, full_path)
                        treffer += 1

        if treffer == 0:
            kein_treffer = QTreeWidgetItem(self.treeWidget)
            kein_treffer.setText(0, "Keine Treffer gefunden")
            kein_treffer.setIcon(0, QIcon.fromTheme("dialog-error"))

        self.treeWidget.repaint()
        self.treeWidget.setSortingEnabled(True)
