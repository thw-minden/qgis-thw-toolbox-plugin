"""Setup-Dialog: Projekt-Status prüfen + Basemaps installieren."""

import os

from qgis.core import QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import pyqtSignal, QCoreApplication, Qt
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
    QHeaderView,
    QHBoxLayout,
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
from ..tools.basemap_setup import (
    TARGET_CRS,
    TARGET_CRS_LABEL,
    Basemap,
    add_basemap_to_project,
    any_basemap_loaded,
    basemap_loaded_in_project,
    connection_exists,
    current_project_crs_auth_id,
    install_and_add,
    install_connection,
    project_crs_is_target,
    set_project_crs_to_target,
    zoom_to_germany,
)
from ..tools.layer_setup import (
    BASEMAPS, 
    MapLayer, 
    basemaps_by_category, 
    additional_layers_by_category,
    qgis_connection_exists,
    install_qgis_connection,
    add_basemap_to_project,
    add_layer_to_project,
    set_project_crs,
)

logger = get_logger(__name__)

_OK_COLOR = "#2e7d32"
_FAIL_COLOR = "#c62828"

class ClickableCellWidget(QWidget):
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


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
        default_btn = self.zone_buttons[self.EPSG_DEFAULT]
        if default_btn is None:
            raise ValueError(f"Could not find default zone {self.EPSG_DEFAULT} in {self.zone_buttons}")
        default_btn.setChecked(True)

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

    def get_selected_epsg(self)->int:
        epsg_str = self.zone_group.checkedButton().text()
        epsg = self.EPSGS.get(epsg_str)
        if epsg is None:
            logger.warning(f"Unsupported zone {epsg_str} received on CRS selection.")
        return int(epsg)


class BaseMapPage(QWizardPage):
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
        for category, items in basemaps_by_category().items():
            tabs.addTab(self._build_category_tab(items), category)
        self.layout.addWidget(tabs)

    def _build_category_tab(self, items: list) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(8)

        table = QTableWidget(len(items), 4, inner)
        table.setHorizontalHeaderLabels(["Karte / Beschreibung", "Aktiv", "Projekt", "QGIS"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        for row, bm in enumerate(items):
            self._build_basemap_row(table, row, bm)

        inner_layout.addWidget(table)

        scroll.setWidget(inner)
        return scroll

    def _build_basemap_row(self, table: QTableWidget, row: int, bm: MapLayer):
        active_btn = QRadioButton()
        add_project_btn = QCheckBox()
        add_qgis_btn = QCheckBox()

        self._active_button_group.addButton(active_btn)
        self._project_basemap_button_list.append(add_project_btn)
        self._qgis_basemap_button_list.append(add_qgis_btn)

        if qgis_connection_exists(bm):
            add_qgis_btn.setChecked(True)
            add_qgis_btn.setDisabled(True)

        active_btn.setProperty("bm", bm)
        add_project_btn.setProperty("bm", bm)
        add_qgis_btn.setProperty("bm", bm)

        def set_active():
            active_btn.setChecked(True)
            add_project_btn.setChecked(True)

        def toggle_project():
            if not active_btn.isChecked():
                add_project_btn.setChecked(not add_project_btn.isChecked())

        def toggle_qgis():
            add_qgis_btn.setChecked(not add_qgis_btn.isChecked())
            if add_qgis_btn.isChecked():
                add_project_btn.setChecked(True)


        name_text = QLabel(bm.name)
        def update_name_style(checked: bool):
            f = QFont(name_text.font())
            f.setBold(checked)
            name_text.setFont(f)
        active_btn.toggled.connect(update_name_style)

        if bm.default_active is not None and bm.default_active:
            set_active()

        if bm.default_add_to_project is not None and bm.default_add_to_project:
            toggle_project()

        description_text = QLabel(bm.description)
        description_text.setStyleSheet("color: gray;")
        description_text.setWordWrap(True)

        first_column = ClickableCellWidget()
        info_layout = QVBoxLayout(first_column)
        name_text.setContentsMargins(0,0,0,0)
        description_text.setContentsMargins(0,0,0,0)
        info_layout.setContentsMargins(4,0,4,4)
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
        table.setRowHeight(row, name_text_size.height() + description_text_size.height()-10)
    
    def get_active_bm(self)->MapLayer:
        active_btn = self._active_button_group.checkedButton()
        return active_btn.property("bm")

    def get_project_bms(self)->list[MapLayer]:
        selected_bms = []
        for project_btn in self._project_basemap_button_list:
            if project_btn.isChecked():
                selected_bms.append(project_btn.property("bm"))
        return selected_bms

    def get_qgis_bms(self)->list[MapLayer]:
        selected_bms = []
        for qgis_btn in self._qgis_basemap_button_list:
            if qgis_btn.isChecked():
                selected_bms.append(qgis_btn.property("bm"))
        return selected_bms

class AddLayerPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Themenspezifische Zusatzlagen")
        self.layout = QVBoxLayout(self)

        self._project_add_layer_button_list = []
        self._qgis_add_layer_button_list = []

        top_text = QLabel(
            "Es können mehrere Themenspezifische Zusatzlagen eingeblendet werden. Die Karten benötigen eine Internetverbindung um zu laden.<br/>"
            "Die Karten können beliebig dem Projekt hinzugefügt werden. Die Spalte QGIS fügt Karten dem QGIS-Browser auch zur Verwendung in anderen Projekten hinzu."
        )
        top_text.setWordWrap(True)
        self.layout.addWidget(top_text)

        tabs = QTabWidget()
        for category, items in additional_layers_by_category().items():
            tabs.addTab(self._build_category_tab(items), category)
        self.layout.addWidget(tabs)

    def _build_category_tab(self, items: list) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(8)

        table = QTableWidget(len(items), 4, inner)
        table.setHorizontalHeaderLabels(["Karte / Beschreibung", "Aktiv", "Projekt", "QGIS"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        for row, bm in enumerate(items):
            self._build_add_layer_row(table, row, bm)

        inner_layout.addWidget(table)

        scroll.setWidget(inner)
        return scroll

    def _build_add_layer_row(self, table: QTableWidget, row: int, bm: MapLayer):
        active_btn = QCheckBox()
        add_project_btn = QCheckBox()
        add_qgis_btn = QCheckBox()


        def toggle_active():
            active_btn.setChecked(not active_btn.isChecked())
            add_project_btn.setChecked(active_btn.isChecked())

        def toggle_project():
            if not active_btn.isChecked():
                add_project_btn.setChecked(not add_project_btn.isChecked())

        def toggle_qgis():
            add_qgis_btn.setChecked(not add_qgis_btn.isChecked())
            if add_qgis_btn.isChecked():
                add_project_btn.setChecked(True)
        
        self._project_add_layer_button_list.append(add_project_btn)
        self._qgis_add_layer_button_list.append(add_qgis_btn)

        if qgis_connection_exists(bm):
            add_qgis_btn.setChecked(True)
            add_qgis_btn.setDisabled(True)

        name_text = QLabel(bm.name)
        def update_name_style(checked: bool):
            f = QFont(name_text.font())
            f.setBold(checked)
            name_text.setFont(f)
        active_btn.toggled.connect(update_name_style)

        if bm.default_active is not None and bm.default_active:
            toggle_active()

        if bm.default_add_to_project is not None and bm.default_add_to_project:
            toggle_project()

        add_project_btn.setProperty("bm", bm)
        add_qgis_btn.setProperty("bm", bm)

        description_text = QLabel(bm.description)
        description_text.setStyleSheet("color: gray;")
        description_text.setWordWrap(True)

        first_column = ClickableCellWidget()
        info_layout = QVBoxLayout(first_column)
        name_text.setContentsMargins(0,0,0,0)
        description_text.setContentsMargins(0,0,0,0)
        info_layout.setContentsMargins(4,0,4,4)
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
    
    def get_project_layers(self)->list[MapLayer]:
        selected_layers = []
        for project_btn in self._project_add_layer_button_list:
            if project_btn.isChecked():
                selected_layers.append(project_btn.property("bm"))
        return selected_layers

    def get_qgis_layers(self)->list[MapLayer]:
        selected_layers = []
        for qgis_btn in self._qgis_add_layer_button_list:
            if qgis_btn.isChecked():
                selected_layers.append(qgis_btn.property("bm"))
        return selected_layers

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

            # 4. Add the additional basemaps to the project
            for map_layer in self.add_layer_pg.get_project_layers():
                add_layer_to_project(map_layer)

            # 5. Set the CRS
            set_project_crs(self.crs_pg.get_selected_epsg())

            # 6. Zoom to Germany
            zoom_to_germany()

            # Collapse all layers in the Layer view
            from qgis.utils import iface
            iface.layerTreeView().collapseAll()

        else:
            logger.debug("Setup Canceled, No action")


        # outer = QVBoxLayout(self)
        # outer.setContentsMargins(12, 12, 12, 12)
        # outer.setSpacing(10)

        # scroll = QScrollArea()
        # scroll.setWidgetResizable(True)
        # scroll.setFrameShape(QFrame.Shape.NoFrame)
        # container = QWidget()
        # self._content_layout = QVBoxLayout(container)
        # self._content_layout.setContentsMargins(0, 0, 0, 0)
        # self._content_layout.setSpacing(12)
        # scroll.setWidget(container)
        # outer.addWidget(scroll, 1)

        # # self._status_group = self._build_status_group()
        # # self._content_layout.addWidget(self._status_group)

        # # self._styles_group = self._build_styles_group()
        # # self._content_layout.addWidget(self._styles_group)

        # # self._basemaps_group = self._build_basemaps_group()
        # # self._content_layout.addWidget(self._basemaps_group)

        # self._content_layout.addStretch(1)

        # buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        # buttons.rejected.connect(self.reject)
        # buttons.accepted.connect(self.accept)
        # outer.addWidget(buttons)

        # self._refresh_all()
        # self._crs_label = QLabel()
        # self._crs_fix_btn = QPushButton(f"Auf {TARGET_CRS} setzen")
        # self._basemap_label = QLabel()
        # self._basemap_fix_btn = QPushButton("OSM laden")
        # self._zoom_btn = QPushButton("Auf Deutschland zoomen")

        # self._zoom_label = QLabel("Kartenansicht auf Deutschland zentrieren")
        # self._styles_status = QLabel()
        # self._styles_remove_btn = QPushButton("Stile entfernen")
        # self._styles_import_btn = QPushButton("Stile importieren")

    # ---------------------------------------------------------------- status

    def _build_status_group(self) -> QGroupBox:
        box = QGroupBox("Projekt-Status")
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        self._crs_label.setWordWrap(True)
        self._crs_fix_btn.clicked.connect(self._fix_crs)

        self._basemap_label.setWordWrap(True)
        self._basemap_fix_btn.clicked.connect(self._fix_basemap)

        self._zoom_label.setWordWrap(True)
        self._zoom_btn.clicked.connect(self._zoom_germany)

        row = 0
        grid.addWidget(self._title_label("Koordinatensystem"), row, 0)
        grid.addWidget(self._crs_label, row, 1)
        grid.addWidget(self._crs_fix_btn, row, 2)
        row += 1
        grid.addWidget(self._title_label("Basiskarte im Projekt"), row, 0)
        grid.addWidget(self._basemap_label, row, 1)
        grid.addWidget(self._basemap_fix_btn, row, 2)
        row += 1
        grid.addWidget(self._title_label("Kartenausschnitt"), row, 0)
        grid.addWidget(self._zoom_label, row, 1)
        grid.addWidget(self._zoom_btn, row, 2)

        box.setLayout(grid)
        return box

    def _title_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        f = QFont(lbl.font())
        f.setBold(True)
        lbl.setFont(f)
        return lbl

    def _refresh_status(self) -> None:
        if project_crs_is_target():
            self._set_status(self._crs_label, True, f"{TARGET_CRS} ({TARGET_CRS_LABEL})")
            self._crs_fix_btn.setEnabled(False)
        else:
            current = current_project_crs_auth_id() or "nicht gesetzt"
            self._set_status(
                self._crs_label,
                False,
                f"Aktuell: {current} – erwartet {TARGET_CRS} ({TARGET_CRS_LABEL})",
            )
            self._crs_fix_btn.setEnabled(True)

        if any_basemap_loaded():
            self._set_status(self._basemap_label, True, "Basiskarte geladen")
            self._basemap_fix_btn.setEnabled(False)
        else:
            self._set_status(self._basemap_label, False, "Keine bekannte Basiskarte im Projekt")
            self._basemap_fix_btn.setEnabled(True)

    @staticmethod
    def _set_status(label: QLabel, ok: bool, text: str) -> None:
        prefix = "✓" if ok else "✗"
        color = _OK_COLOR if ok else _FAIL_COLOR
        label.setText(f"<span style='color:{color}; font-weight:bold;'>{prefix}</span> {text}")

    def _fix_crs(self) -> None:
        if not self._project_has_user_content():
            set_project_crs_to_target()
            self._refresh_all()
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Koordinatensystem ändern")
        box.setText(
            f"Projekt-CRS wird auf {TARGET_CRS} ({TARGET_CRS_LABEL}) umgestellt.\n\n"
            "Im Projekt sind bereits Daten vorhanden. Bestehende Taktische "
            "Zeichen können sich dadurch sichtbar verschieben."
        )
        box.setInformativeText(
            "Migrieren: THW-Zeichen werden in das neue CRS umgerechnet und "
            "neu gespeichert (Position bleibt erhalten).\n"
            "Einfach ausführen: Nur Projekt-CRS ändern. Bestehende Zeichen "
            "werden von QGIS on-the-fly reprojiziert."
        )
        cancel_btn = box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
        migrate_btn = box.addButton("Migrieren", QMessageBox.ButtonRole.AcceptRole)
        force_btn = box.addButton("Einfach ausführen", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(cancel_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked is cancel_btn:
            return
        if clicked is migrate_btn:
            new_layer = (
                self._plugin.layer_manager.reproject_to(
                    TARGET_CRS, log=lambda msg, critical=False: self._log_message(msg, critical)
                )
                if self._plugin.layer_manager
                else None
            )
            if new_layer is None:
                return
            self._plugin.on_layer_replaced(new_layer)
        elif clicked is not force_btn:
            return

        set_project_crs_to_target()
        self._refresh_all()

    def _project_has_user_content(self) -> bool:
        """True if the project has marker features or any non-marker vector layer."""
        marker_layer = self._plugin.layer_manager.layer if self._plugin.layer_manager else None
        if marker_layer and marker_layer.featureCount() > 0:
            return True
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr is marker_layer:
                continue
            if isinstance(lyr, QgsVectorLayer):
                return True
        return False

    def _fix_basemap(self) -> None:
        osm = next((b for b in BASEMAPS if b.key == "osm"), None)
        if osm:
            add_basemap_to_project(osm, log=self._log_message)
        self._refresh_all()

    def _zoom_germany(self) -> None:
        if not zoom_to_germany():
            self._log_message("Konnte Kartenausschnitt nicht setzen.", critical=True)

    # ------------------------------------------------------------- basemaps

    def _build_basemaps_group(self) -> QGroupBox:
        box = QGroupBox("Basiskarten & Fachdaten installieren")
        vbox = QVBoxLayout()
        vbox.setSpacing(6)

        hint = QLabel(
            "'Zum Projekt hinzufügen' fügt die Karte als Layer hinzu und legt "
            "zusätzlich eine dauerhafte Verbindung im QGIS-Browser an. "
            "'Nur Verbindung' legt lediglich die Verbindung an."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        vbox.addWidget(hint)

        self._basemap_rows: dict[str, dict] = {}
        tabs = QTabWidget()
        for category, items in basemaps_by_category().items():
            tabs.addTab(self._build_category_tab(items), category)
        vbox.addWidget(tabs)

        box.setLayout(vbox)
        return box

    def _build_category_tab(self, items: list) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 6, 0, 0)
        page_layout.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(4)
        for bm in items:
            inner_layout.addWidget(self._build_basemap_row(bm))
        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        page_layout.addWidget(scroll, 1)
        return page

    def _build_basemap_row(self, bm: Basemap) -> QWidget:
        row = QFrame()
        row.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QGridLayout(row)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(8)
        layout.setColumnStretch(0, 1)

        name = QLabel(bm.name)
        f = QFont(name.font())
        f.setBold(True)
        name.setFont(f)
        layout.addWidget(name, 0, 0)

        status = QLabel()
        status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(status, 0, 1)

        desc = QLabel(bm.description)
        desc.setStyleSheet("color: gray;")
        desc.setWordWrap(True)
        layout.addWidget(desc, 1, 0, 1, 2)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        add_btn = QPushButton("Zum Projekt hinzufügen")
        add_btn.clicked.connect(lambda _=False, b=bm: self._on_install_and_add(b))
        conn_btn = QPushButton("Nur Verbindung")
        conn_btn.clicked.connect(lambda _=False, b=bm: self._on_install_connection_only(b))
        btn_row.addStretch(1)
        btn_row.addWidget(conn_btn)
        btn_row.addWidget(add_btn)
        layout.addLayout(btn_row, 2, 0, 1, 2)

        self._basemap_rows[bm.key] = {"status": status, "add": add_btn, "conn": conn_btn}
        return row

    def _refresh_basemaps(self) -> None:
        for bm in BASEMAPS:
            refs = self._basemap_rows.get(bm.key)
            if not refs:
                continue
            in_project = basemap_loaded_in_project(bm)
            has_conn = connection_exists(bm)
            parts = []
            if in_project:
                parts.append(f"<span style='color:{_OK_COLOR};'>✓ im Projekt</span>")
            if has_conn:
                parts.append(f"<span style='color:{_OK_COLOR};'>✓ Verbindung</span>")
            if not parts:
                parts.append(f"<span style='color:{_FAIL_COLOR};'>nicht installiert</span>")
            refs["status"].setText(" · ".join(parts))
            refs["add"].setEnabled(not in_project)

    def _on_install_and_add(self, bm: Basemap) -> None:
        ok = install_and_add(bm, log=self._log_message)
        if not ok:
            self._log_message(f"Fehler beim Hinzufügen von '{bm.name}'.", critical=True)
        self._refresh_all()

    def _on_install_connection_only(self, bm: Basemap) -> None:
        install_connection(bm)
        self._refresh_all()

    # ---------------------------------------------------------------- styles

    def _build_styles_group(self) -> QGroupBox:
        box = QGroupBox("Symbolbibliothek")
        vbox = QVBoxLayout()
        vbox.setSpacing(6)

        hint = QLabel("Macht die Taktischen Zeichen projektübergreifend im Symbol-Auswahldialog verfügbar.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        vbox.addWidget(hint)

        row = QHBoxLayout()
        self._styles_status.setWordWrap(True)
        row.addWidget(self._styles_status, 1)

        self._styles_remove_btn.clicked.connect(self._on_remove_styles)
        row.addWidget(self._styles_remove_btn)

        self._styles_import_btn.clicked.connect(self._on_import_styles)
        row.addWidget(self._styles_import_btn)

        vbox.addLayout(row)
        box.setLayout(vbox)
        return box

    def _refresh_styles(self) -> None:
        present, total = style_library.status(self._plugin.plugin_dir)
        if total == 0:
            self._set_status(self._styles_status, False, "Keine SVGs gefunden")
            self._styles_import_btn.setEnabled(False)
            self._styles_remove_btn.setEnabled(False)
            return
        if present == total:
            self._set_status(self._styles_status, True, f"{present} von {total} Symbolen importiert")
        elif present == 0:
            self._set_status(self._styles_status, False, f"0 von {total} Symbolen importiert")
        else:
            self._set_status(self._styles_status, False, f"{present} von {total} Symbolen importiert")
        self._styles_import_btn.setEnabled(True)
        self._styles_remove_btn.setEnabled(present > 0)

    def _on_import_styles(self) -> None:
        _, total = style_library.status(self._plugin.plugin_dir)
        if total == 0:
            self._log_message("Keine SVGs gefunden.", critical=True)
            return

        progress = QProgressDialog("Symbole werden importiert …", "Abbrechen", 0, total, self)
        progress.setWindowTitle("Stilbibliothek")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        QCoreApplication.processEvents()

        def on_progress(done: int, total_count: int) -> bool:
            progress.setValue(done)
            progress.setLabelText(f"Symbole werden importiert … ({done}/{total_count})")
            QCoreApplication.processEvents()
            return not progress.wasCanceled()

        written, total_done = style_library.import_styles(self._plugin.plugin_dir, on_progress=on_progress)
        progress.close()

        if progress.wasCanceled():
            self._log_message(f"Import abgebrochen. {written} Symbole bereits geschrieben.")
        else:
            self._log_message(
                f"{written} von {total_done} Symbolen zur Stilbibliothek hinzugefügt."
                " Hinweis: Symbol-Auswahldialog ggf. neu öffnen.",
                critical=written == 0,
            )
        self._refresh_all()

    def _on_remove_styles(self) -> None:
        removed = style_library.remove_styles(self._plugin.plugin_dir)
        self._log_message(f"{removed} Symbole aus der Stilbibliothek entfernt.")
        self._refresh_all()

    # ---------------------------------------------------------------- util

    def _log_message(self, msg: str, critical: bool = False) -> None:
        try:
            from qgis.core import Qgis
            from qgis.utils import iface

            level = Qgis.MessageLevel.Critical if critical else Qgis.MessageLevel.Info
            iface.messageBar().pushMessage("THW Setup", msg, level=level)
        except Exception as e:
            logger.debug("Konnte Setup-Meldung nicht anzeigen: %s", e)

    def _refresh_all(self) -> None:
        pass
        # self._refresh_status()
        # self._refresh_styles()
        # self._refresh_basemaps()
