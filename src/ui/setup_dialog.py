"""Setup-Dialog: Projekt-Status prüfen + Basemaps installieren."""

import os

from qgis.core import QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import QCoreApplication, Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont, QPixmap
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTableWidget,
    QTabWidget,
    QToolBox,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from ..logging_utils import get_logger
from ..tools import style_library
from ..tools.layer_setup import (
    BASEMAPS,
    MapLayer,
    add_basemap_to_project,
    add_layer_to_project,
    additional_layers_by_category,
    basemaps_by_category,
    exists_in_project,
    get_project_crs,
    install_qgis_connection,
    is_visible_in_project,
    qgis_connection_exists,
    reload_browser,
    remove_from_qgis,
    remove_layer_from_project,
    set_project_crs,
    set_visibility_in_project,
    zoom_to_germany,
)

logger = get_logger(__name__)

_OK_COLOR = "#2e7d32"
_FAIL_COLOR = "#c62828"


class ClickableCellWidget(QWidget):
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Start Page
# ---------------------------------------------------------------------------


class StartPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("THW Toolbox Projekt Setup")
        self.layout = QVBoxLayout(self)

        top_text = QLabel("Dieses Menu unterstützt beim Aufsetzen des QGIS-Projektes.")
        top_text.setWordWrap(True)
        self.layout.addWidget(top_text)

        info_text = QLabel(
            "Auf den folgenden Seiten wird das Projekt mit folgenden Schritten konfiguriert:<br/>"
            "- Koordinatenreferenzsystem<br/>"
            "- Hintergrundkarte<br/>"
            "- Themenspezifische Zusatzlagen<br/>"
            "- Einstellungen für die Druckvorlage<br/>"
            "- Sonstige Projekteinstellungen"
        )
        info_text.setWordWrap(True)
        self.layout.addWidget(info_text)

        self.layout.addStretch(1)


# ---------------------------------------------------------------------------
# CRS Page
# ---------------------------------------------------------------------------


class CrsPage(QWizardPage):
    EPSGS = {
        "31* Nord": 25831,
        "32* Nord": 25832,
        "33* Nord": 25833,
    }
    EPSG_DEFAULT = "32* Nord"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Koordinatenreferenzsystem")
        self.layout = QVBoxLayout(self)

        top_text = QLabel("Bitte die UTM-Zone des relevanten Raumes anhand der Karte auswählen.")
        top_text.setWordWrap(True)
        self.layout.addWidget(top_text)

        content_layout = QHBoxLayout()
        self.layout.addLayout(content_layout)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        left_row_title = QLabel("Koordinatenreferenzsystem")
        left_row_title.setWordWrap(True)
        left_layout.addWidget(left_row_title)

        self.zone_group = QButtonGroup(self)
        self.zone_buttons = {}

        for label in self.EPSGS:
            btn = QRadioButton(label, self)
            self.zone_group.addButton(btn)
            left_layout.addWidget(btn)
            self.zone_buttons[label] = btn
        current_crs = get_project_crs()
        current_crs_key = [key for key, val in self.EPSGS.items() if val == current_crs]
        if current_crs_key == []:
            # Default to the default button if not set already
            default_btn = self.zone_buttons[self.EPSG_DEFAULT]
            if default_btn is None:
                raise ValueError(f"Could not find default zone {self.EPSG_DEFAULT} in {self.zone_buttons}")
            default_btn.setChecked(True)
        else:
            current_btn = self.zone_buttons[current_crs_key[0]]
            current_btn.setChecked(True)

        left_layout.addStretch(1)

        content_layout.addWidget(left_widget, 1)

        image_label = QLabel()
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_label.setMinimumWidth(320)
        image_label.setStyleSheet("QLabel { background-color: white; }")
        image_label.setAutoFillBackground(True)

        img_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "utm_zone_grid.png")

        pixmap = QPixmap(img_path)
        if not pixmap.isNull():
            image_label.setPixmap(pixmap.scaledToWidth(360, Qt.TransformationMode.SmoothTransformation))
        else:
            image_label.setText("Bild konnte nicht geladen werden.\n" + img_path)

        content_layout.addWidget(image_label, 1)

        details_box = QToolBox()
        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_text = QLabel(
            "Durch Deutschland verlaufen die 3 Zonen 31N bis 32N die hier zu Auswahl stehen. Für Einsätze im Ausland ist unten rechts in QGIS manuell das korrekte Koordinatenreferenzsystem auszuwählen.<br/><br/>"
            "Was ist UTM?<br/>"
            "Die runde Erde muss auf eine flache Karte projiziert werden. Dafür werden unterschiedliche Systeme verwendet wovon UTM ein im begrenzten Gebiet sehr genaues Verfahren darstellt. Allerdings muss für die UTM-Projektion der passende Ost-West-Abschnitt gewählt werden, um die Fehler durch die Projektion niedrig zu halten."
        )
        details_text.setWordWrap(True)
        details_layout.addWidget(details_text)
        details_layout.addStretch(1)
        details_box.addItem(details, "Details")
        self.layout.addWidget(details_box)

    def get_selected_epsg(self) -> int:
        epsg_str = self.zone_group.checkedButton().text()
        epsg = self.EPSGS.get(epsg_str)
        if epsg is None:
            logger.warning(f"Unsupported zone {epsg_str} received on CRS selection.")
        return int(epsg)


# ---------------------------------------------------------------------------
# Basemap Page
# ---------------------------------------------------------------------------


class BaseMapPage(QWizardPage):
    MAP_LAYER_PROPERTY_STRING = "basemap_layer"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Hintergrundkarte")
        self.layout = QVBoxLayout(self)

        self._active_button_group = QButtonGroup(self)
        self._active_button_group.setExclusive(True)

        self._project_basemap_button_list = []
        self._qgis_basemap_button_list = []

        top_text = QLabel(
            "Es wird gleichzeitig nur eine Hintergrundkarte angezeigt. Die Karten benötigen eine Internetverbindung um zu laden.<br/>"
            "Die in Spalte Aktiv ausgewählte Karte wird genutzt. Weitere Karten können dem Projekt hinzugefügt werden. Die Spalte QGIS fügt Karten dem QGIS-Browser auch zur Verwendung in anderen Projekten hinzu."
        )
        top_text.setWordWrap(True)
        self.layout.addWidget(top_text)

        tabs = QTabWidget()
        for category, base_map_layers in basemaps_by_category().items():
            tabs.addTab(self._build_category_tab(base_map_layers), category)
        self.layout.addWidget(tabs)

    def _build_category_tab(self, basemap_layers: list) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(8)

        table = QTableWidget(len(basemap_layers), 4, inner)
        table.setHorizontalHeaderLabels(["Karte / Beschreibung", "Aktiv", "Projekt", "QGIS"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        for row, basemap_layer in enumerate(basemap_layers):
            self._build_basemap_row(table, row, basemap_layer)

        inner_layout.addWidget(table)

        scroll.setWidget(inner)
        return scroll

    def _build_basemap_row(self, table: QTableWidget, row: int, basemap_layer: MapLayer):
        active_btn = QRadioButton()
        add_project_btn = QCheckBox()
        add_qgis_btn = QCheckBox()

        self._active_button_group.addButton(active_btn)
        self._project_basemap_button_list.append(add_project_btn)
        self._qgis_basemap_button_list.append(add_qgis_btn)

        active_btn.setProperty(BaseMapPage.MAP_LAYER_PROPERTY_STRING, basemap_layer)
        add_project_btn.setProperty(BaseMapPage.MAP_LAYER_PROPERTY_STRING, basemap_layer)
        add_qgis_btn.setProperty(BaseMapPage.MAP_LAYER_PROPERTY_STRING, basemap_layer)

        name_text = QLabel(basemap_layer.name)

        # Functions triggering on state change
        def toggle_active(checked: bool):
            f = QFont(name_text.font())
            f.setBold(checked)
            name_text.setFont(f)
            set_visibility_in_project(basemap_layer, checked)
            if checked:
                add_project_btn.setChecked(True)

        def set_active():
            active_btn.setChecked(True)
            add_project_btn.setChecked(True)

        def toggle_project():
            new_state = not add_project_btn.isChecked()
            add_project_btn.setChecked(new_state)
            remove_from_project_if_deselected()

        def remove_from_project_if_deselected():
            if add_project_btn.isChecked():  # Trigger if deselected
                return
            if active_btn.isChecked():  # Do not remove when currently active
                return
            if exists_in_project(basemap_layer):  # And it is already added
                remove_layer_from_project(basemap_layer)  # Then remove

        def toggle_qgis():
            new_state = not add_qgis_btn.isChecked()
            add_qgis_btn.setChecked(new_state)
            remove_from_qgis_if_deselected()

        def remove_from_qgis_if_deselected():
            if add_qgis_btn.isChecked():
                return
            if qgis_connection_exists(basemap_layer):
                remove_from_qgis(basemap_layer)

        # Connect these functions to the buttons
        active_btn.toggled.connect(toggle_active)
        add_project_btn.toggled.connect(remove_from_project_if_deselected)
        add_qgis_btn.toggled.connect(remove_from_qgis_if_deselected)

        # Set the states according to the current project status
        # Else, use the default values
        if qgis_connection_exists(basemap_layer):
            add_qgis_btn.setChecked(True)
        if exists_in_project(basemap_layer):
            add_project_btn.setChecked(True)
        elif basemap_layer.default_add_to_project is not None and basemap_layer.default_add_to_project:
            add_project_btn.setChecked(True)
        if is_visible_in_project(basemap_layer):
            set_active()
        elif basemap_layer.default_active is not None and basemap_layer.default_active:
            set_active()

        # Generate the column content
        description_text = QLabel(basemap_layer.description)
        description_text.setStyleSheet("color: gray;")
        description_text.setWordWrap(True)

        first_column = ClickableCellWidget()
        info_layout = QVBoxLayout(first_column)
        name_text.setContentsMargins(0, 0, 0, 0)
        description_text.setContentsMargins(0, 0, 0, 0)
        info_layout.setContentsMargins(4, 0, 4, 4)
        info_layout.setSpacing(0)
        info_layout.addWidget(name_text)
        info_layout.addWidget(description_text)
        first_column.clicked.connect(set_active)
        table.setCellWidget(row, 0, first_column)

        second_column = ClickableCellWidget()
        active_layout = QHBoxLayout(second_column)
        active_layout.setContentsMargins(0, 0, 0, 0)
        active_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        active_layout.addWidget(active_btn)
        second_column.clicked.connect(set_active)
        table.setCellWidget(row, 1, second_column)

        third_column = ClickableCellWidget()
        project_layout = QHBoxLayout(third_column)
        project_layout.setContentsMargins(0, 0, 0, 0)
        project_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        project_layout.addWidget(add_project_btn)
        third_column.clicked.connect(toggle_project)
        table.setCellWidget(row, 2, third_column)

        fourth_column = ClickableCellWidget()
        qgis_layout = QHBoxLayout(fourth_column)
        qgis_layout.setContentsMargins(0, 0, 0, 0)
        qgis_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qgis_layout.addWidget(add_qgis_btn)
        fourth_column.clicked.connect(toggle_qgis)
        table.setCellWidget(row, 3, fourth_column)

        name_text_size = name_text.sizeHint()
        description_text_size = description_text.sizeHint()
        table.setRowHeight(row, name_text_size.height() + description_text_size.height() - 10)

    def get_active_bm(self) -> MapLayer:
        active_btn = self._active_button_group.checkedButton()
        return active_btn.property(BaseMapPage.MAP_LAYER_PROPERTY_STRING)

    def get_project_bms(self) -> list[MapLayer]:
        selected_bms = []
        for project_btn in self._project_basemap_button_list:
            if project_btn.isChecked():
                selected_bms.append(project_btn.property(BaseMapPage.MAP_LAYER_PROPERTY_STRING))
        return selected_bms

    def get_qgis_bms(self) -> list[MapLayer]:
        selected_bms = []
        for qgis_btn in self._qgis_basemap_button_list:
            if qgis_btn.isChecked():
                selected_bms.append(qgis_btn.property(BaseMapPage.MAP_LAYER_PROPERTY_STRING))
        return selected_bms


# ---------------------------------------------------------------------------
# Additional Layer Page
# ---------------------------------------------------------------------------


class AddLayerPage(QWizardPage):
    MAP_LAYER_PROPERTY_STRING = "map_layer"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Themenspezifische Zusatzlagen")
        self.layout = QVBoxLayout(self)

        self._project_add_layer_button_list = []
        self._qgis_add_layer_button_list = []
        self._active_layer_button_list = []

        top_text = QLabel(
            "Es können mehrere Themenspezifische Zusatzlagen eingeblendet werden. Die Karten benötigen eine Internetverbindung um zu laden.<br/>"
            "Die Karten können beliebig dem Projekt hinzugefügt werden. Die Spalte QGIS fügt Karten dem QGIS-Browser auch zur Verwendung in anderen Projekten hinzu."
        )
        top_text.setWordWrap(True)
        self.layout.addWidget(top_text)

        tabs = QTabWidget()
        for category, map_layers in additional_layers_by_category().items():
            tabs.addTab(self._build_category_tab(map_layers), category)
        self.layout.addWidget(tabs)

    def _build_category_tab(self, map_layers: list) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(8)

        table = QTableWidget(len(map_layers), 4, inner)
        table.setHorizontalHeaderLabels(["Karte / Beschreibung", "Aktiv", "Projekt", "QGIS"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        for row, map_layer in enumerate(map_layers):
            self._build_add_layer_row(table, row, map_layer)

        inner_layout.addWidget(table)

        scroll.setWidget(inner)
        return scroll

    def _build_add_layer_row(self, table: QTableWidget, row: int, map_layer: MapLayer):
        active_btn = QCheckBox()
        add_project_btn = QCheckBox()
        add_qgis_btn = QCheckBox()

        self._active_layer_button_list.append(active_btn)
        self._project_add_layer_button_list.append(add_project_btn)
        self._qgis_add_layer_button_list.append(add_qgis_btn)

        active_btn.setProperty(AddLayerPage.MAP_LAYER_PROPERTY_STRING, map_layer)
        add_project_btn.setProperty(AddLayerPage.MAP_LAYER_PROPERTY_STRING, map_layer)
        add_qgis_btn.setProperty(AddLayerPage.MAP_LAYER_PROPERTY_STRING, map_layer)

        name_text = QLabel(map_layer.name)

        # Functions triggerin on state change
        def toggle_active():
            # Cannot take the checked keyword since also called by the ClickableCellWidget
            new_state = not active_btn.isChecked()
            f = QFont(name_text.font())
            f.setBold(new_state)
            name_text.setFont(f)
            active_btn.setChecked(new_state)
            if new_state:
                add_project_btn.setChecked(True)
            remove_visibility_if_deselected()

        def remove_visibility_if_deselected():
            set_visibility_in_project(map_layer, active_btn.isChecked())

        def toggle_project():
            new_state = not add_project_btn.isChecked()
            add_project_btn.setChecked(new_state)
            remove_from_project_if_deselected()

        def remove_from_project_if_deselected():
            if add_project_btn.isChecked():  # Trigger if deselected
                return
            active_btn.setChecked(False)
            # Also remove when currently active
            if exists_in_project(map_layer):  # It is already added
                remove_layer_from_project(map_layer)  # Then Remove
                active_btn.setChecked(False)  # Also set not active if not already set

        def toggle_qgis():
            new_state = not add_qgis_btn.isChecked()
            add_qgis_btn.setChecked(new_state)
            remove_from_qgis_if_deselected()

        def remove_from_qgis_if_deselected():
            if add_qgis_btn.isChecked():
                return
            if qgis_connection_exists(map_layer):
                remove_from_qgis(map_layer)

        if qgis_connection_exists(map_layer):
            add_qgis_btn.setChecked(True)
            add_qgis_btn.setDisabled(True)

        # Connect these functions to the buttons
        active_btn.toggled.connect(remove_visibility_if_deselected)
        add_project_btn.toggled.connect(remove_from_project_if_deselected)
        add_qgis_btn.toggled.connect(remove_from_qgis_if_deselected)

        # Set the states according to the current project status
        # Else, use the default values
        if qgis_connection_exists(map_layer):
            add_qgis_btn.setChecked(True)
        if exists_in_project(map_layer):
            add_project_btn.setChecked(True)
        elif map_layer.default_add_to_project is not None and map_layer.default_add_to_project:
            add_project_btn.setChecked(True)
        if is_visible_in_project(map_layer):
            active_btn.setChecked(True)
            add_project_btn.setChecked(True)
        elif map_layer.default_active is not None and map_layer.default_active:
            active_btn.setChecked(True)
            add_project_btn.setChecked(True)

        # Generate the column content
        description_text = QLabel(map_layer.description)
        description_text.setStyleSheet("color: gray;")
        description_text.setWordWrap(True)

        first_column = ClickableCellWidget()
        info_layout = QVBoxLayout(first_column)
        name_text.setContentsMargins(0, 0, 0, 0)
        description_text.setContentsMargins(0, 0, 0, 0)
        info_layout.setContentsMargins(4, 0, 4, 4)
        info_layout.setSpacing(0)
        info_layout.addWidget(name_text)
        info_layout.addWidget(description_text)
        first_column.clicked.connect(toggle_active)
        table.setCellWidget(row, 0, first_column)

        second_column = ClickableCellWidget()
        active_layout = QHBoxLayout(second_column)
        active_layout.setContentsMargins(0, 0, 0, 0)
        active_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        active_layout.addWidget(active_btn)
        second_column.clicked.connect(toggle_active)
        table.setCellWidget(row, 1, second_column)

        third_column = ClickableCellWidget()
        project_layout = QHBoxLayout(third_column)
        project_layout.setContentsMargins(0, 0, 0, 0)
        project_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        project_layout.addWidget(add_project_btn)
        third_column.clicked.connect(toggle_project)
        table.setCellWidget(row, 2, third_column)

        fourth_column = ClickableCellWidget()
        qgis_layout = QHBoxLayout(fourth_column)
        qgis_layout.setContentsMargins(0, 0, 0, 0)
        qgis_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qgis_layout.addWidget(add_qgis_btn)
        fourth_column.clicked.connect(toggle_qgis)
        table.setCellWidget(row, 3, fourth_column)

        name_text_size = name_text.sizeHint()
        description_text_size = description_text.sizeHint()
        table.setRowHeight(row, name_text_size.height() + description_text_size.height())

    def get_active_layers(self) -> list[MapLayer]:
        active_layers = []
        for active_btn in self._active_layer_button_list:
            if active_btn.isChecked():
                active_layers.append(active_btn.property(AddLayerPage.MAP_LAYER_PROPERTY_STRING))
        return active_layers

    def get_project_layers(self) -> list[MapLayer]:
        selected_layers = []
        for project_btn in self._project_add_layer_button_list:
            if project_btn.isChecked():
                selected_layers.append(project_btn.property(AddLayerPage.MAP_LAYER_PROPERTY_STRING))
        return selected_layers

    def get_qgis_layers(self) -> list[MapLayer]:
        selected_layers = []
        for qgis_btn in self._qgis_add_layer_button_list:
            if qgis_btn.isChecked():
                selected_layers.append(qgis_btn.property(AddLayerPage.MAP_LAYER_PROPERTY_STRING))
        return selected_layers


# ---------------------------------------------------------------------------
# SetupDialog
# ---------------------------------------------------------------------------
class SetupDialog(QWizard):
    """Wizard für Projekt-Setup und Basiskarten-Installation."""

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self._plugin = plugin
        self.setWindowTitle("THW Toolbox Setup")
        self.resize(620, 640)

        # Page builder calls
        self.start_pg = StartPage(parent=self)
        self.crs_pg = CrsPage(parent=self)
        self.base_map_pg = BaseMapPage(parent=self)
        self.add_layer_pg = AddLayerPage(parent=self)

        self.addPage(self.start_pg)
        self.addPage(self.crs_pg)
        self.addPage(self.base_map_pg)
        self.addPage(self.add_layer_pg)

        self._run_dialog()

    def _run_dialog(self):
        logger.debug("Setup-Dialog started.")
        result = self.exec()

        if result == QDialog.DialogCode.Accepted:
            logger.debug("Dialog completed")
            # 1. Add the static basemap connections to the QGIS browser
            for bm in self.base_map_pg.get_qgis_bms():
                if qgis_connection_exists(bm):
                    logger.debug("Base Map %s already added to QGIS", bm.name)
                else:
                    logger.debug("Adding Basemap %s as permanent connection", bm.name)
                    install_qgis_connection(bm)

            # 2. Add the basemaps to the project
            for bm in self.base_map_pg.get_project_bms():
                if exists_in_project(bm):
                    continue
                if bm == self.base_map_pg.get_active_bm():
                    add_basemap_to_project(bm, visible=True)
                else:
                    add_basemap_to_project(bm)

            # 3. Add the additional layer connections to the QGIS browser
            for map_layer in self.add_layer_pg.get_qgis_layers():
                if qgis_connection_exists(map_layer):
                    logger.debug("Additional Layer %s already added to QGIS", bm.name)
                else:
                    logger.debug("Adding Additional Layer %s as permanent connection", bm.name)
                    install_qgis_connection(map_layer)
            reload_browser()

            # 4. Add the additional layers to the project
            for map_layer in self.add_layer_pg.get_project_layers():
                if exists_in_project(map_layer):
                    continue
                if map_layer in self.add_layer_pg.get_active_layers():
                    add_layer_to_project(map_layer, visible=True)
                else:
                    add_layer_to_project(map_layer)

            # 5. Set the CRS
            set_project_crs(self.crs_pg.get_selected_epsg())

            # 6. Zoom to Germany if the setup is run the first time
            if not self._plugin.action.isChecked():
                zoom_to_germany()
                # 7. Activate the Plugin if not already done
                self._plugin.activate()

            # Collapse all layers in the Layer view
            from qgis.utils import iface

            iface.layerTreeView().collapseAll()

        else:
            logger.debug("Setup Canceled, No action")
