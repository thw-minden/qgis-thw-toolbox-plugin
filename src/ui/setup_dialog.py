"""Setup-Assistent: Projekt Schritt für Schritt einrichten (CRS, Hintergrundkarte, Zusatzlagen)."""

import os
from typing import Optional

from qgis.gui import QgsCollapsibleGroupBox
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont, QPixmap
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
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
from qgis.utils import iface

from ..logging_utils import get_logger
from ..tools.layer_setup import (
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
    set_visibility_in_project,
    zoom_to_germany,
)
from .setup_common import (
    SETUP_MODE_CLASSIC,
    SETUP_MODE_WIZARD,
    UTM_ZONE_DEFAULT,
    UTM_ZONES,
    StyleLibraryGroup,
    apply_project_crs,
    get_setup_mode,
    set_setup_mode,
)

logger = get_logger(__name__)


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

        mode_row = QHBoxLayout()
        self.wizard_default_cb = QCheckBox("Assistent künftig standardmäßig öffnen")
        self.wizard_default_cb.setChecked(get_setup_mode() == SETUP_MODE_WIZARD)
        self.wizard_default_cb.toggled.connect(
            lambda checked: set_setup_mode(SETUP_MODE_WIZARD if checked else SETUP_MODE_CLASSIC)
        )
        mode_row.addWidget(self.wizard_default_cb, 1)

        self.classic_btn = QPushButton("Zum klassischen Dialog wechseln")
        mode_row.addWidget(self.classic_btn)
        self.layout.addLayout(mode_row)


# ---------------------------------------------------------------------------
# CRS Page
# ---------------------------------------------------------------------------


class CrsPage(QWizardPage):
    EPSGS = UTM_ZONES
    EPSG_DEFAULT = UTM_ZONE_DEFAULT

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
            "Durch Deutschland verlaufen die 3 Zonen 31N bis 33N, die hier zur Auswahl stehen. Für Einsätze im Ausland ist unten rechts in QGIS manuell das korrekte Koordinatenreferenzsystem auszuwählen.<br/><br/>"
            "Was ist UTM?<br/>"
            "Die runde Erde muss auf eine flache Karte projiziert werden. Dafür werden unterschiedliche Systeme verwendet wovon UTM ein im begrenzten Gebiet sehr genaues Verfahren darstellt. Allerdings muss für die UTM-Projektion der passende Ost-West-Abschnitt gewählt werden, um die Fehler durch die Projektion niedrig zu halten."
        )
        details_text.setWordWrap(True)
        details_layout.addWidget(details_text)
        details_layout.addStretch(1)
        details_box.addItem(details, "Details")
        self.layout.addWidget(details_box)

    def get_selected_epsg(self) -> int:
        checked = self.zone_group.checkedButton()
        epsg_str = checked.text() if checked is not None else self.EPSG_DEFAULT
        epsg = self.EPSGS.get(epsg_str)
        if epsg is None:
            logger.warning("Unsupported zone %s received on CRS selection, falling back to default.", epsg_str)
            epsg = self.EPSGS[self.EPSG_DEFAULT]
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
            # visual + project visibility
            f = QFont(name_text.font())
            f.setBold(checked)
            name_text.setFont(f)
            set_visibility_in_project(basemap_layer, checked)

            if checked:
                # when this basemap becomes active, the project checkbox must be on
                add_project_btn.setChecked(True)

        def set_active():
            # make this basemap active and ensure it's in the project
            active_btn.setChecked(True)
            add_project_btn.setChecked(True)

        def remove_from_project_if_deselected():
            # only remove if:
            # - project checkbox is unchecked
            # - and this basemap is not currently active
            if add_project_btn.isChecked():
                return
            if active_btn.isChecked():
                # keep it in the project while active
                return
            if exists_in_project(basemap_layer):
                remove_layer_from_project(basemap_layer)

        def toggle_project():
            # user intent: toggle project membership via third column / checkbox
            # but: if active_btn is checked, never allow the project checkbox to go off
            if active_btn.isChecked():
                # force it to stay checked when active
                add_project_btn.setChecked(True)
                return

            # normal toggle if not active
            new_state = not add_project_btn.isChecked()
            add_project_btn.setChecked(new_state)
            remove_from_project_if_deselected()

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

        # project checkbox uses the same protection as toggle_project:
        # if it gets toggled off while active, immediately force it back on.
        def on_add_project_toggled(checked: bool):
            if active_btn.isChecked() and not checked:
                # re-check to keep consistency with active state
                add_project_btn.setChecked(True)
                return
            remove_from_project_if_deselected()

        add_project_btn.toggled.connect(on_add_project_toggled)
        add_qgis_btn.toggled.connect(remove_from_qgis_if_deselected)

        # Set the states according to the current project status
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
        # clicking the first column also sets the basemap active and keeps project checkbox on
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
        # clicking the third column uses the guarded toggle_project
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

        # Functions triggering on state change
        def remove_visibility_if_deselected():
            set_visibility_in_project(map_layer, active_btn.isChecked())

        def toggle_active():
            # Cannot take the checked keyword since also called by the ClickableCellWidget
            new_state = not active_btn.isChecked()

            f = QFont(name_text.font())
            f.setBold(new_state)
            name_text.setFont(f)
            active_btn.setChecked(new_state)

            if new_state:
                # when layer becomes active, ensure it's in the project
                add_project_btn.setChecked(True)

            remove_visibility_if_deselected()

        def remove_from_project_if_deselected():
            if add_project_btn.isChecked():
                return

            # if project checkbox is off, layer cannot stay active
            if active_btn.isChecked():
                active_btn.setChecked(False)
                remove_visibility_if_deselected()

            if exists_in_project(map_layer):
                remove_layer_from_project(map_layer)

        def toggle_project():
            # if layer is active, keep project checkbox checked
            if active_btn.isChecked():
                add_project_btn.setChecked(True)
                return

            new_state = not add_project_btn.isChecked()
            add_project_btn.setChecked(new_state)
            remove_from_project_if_deselected()

        def remove_from_qgis_if_deselected():
            # for "fun" layers, ignore any attempt to change QGIS state
            if getattr(map_layer, "kind", None) == "fun":
                return
            if add_qgis_btn.isChecked():
                return
            if qgis_connection_exists(map_layer):
                remove_from_qgis(map_layer)

        def toggle_qgis():
            # Completely ignore clicks for "fun" layers so ClickableCellWidget cannot toggle it
            if getattr(map_layer, "kind", None) == "fun":
                return

            new_state = not add_qgis_btn.isChecked()
            add_qgis_btn.setChecked(new_state)
            remove_from_qgis_if_deselected()

        # Disable add_qgis_btn if this is a "fun" layer
        if getattr(map_layer, "kind", None) == "fun":
            add_qgis_btn.setChecked(False)
            add_qgis_btn.setDisabled(
                True
            )  # user cannot interact, and toggle_qgis will also ignore clicks.[web:44][web:40]
        elif qgis_connection_exists(map_layer):
            add_qgis_btn.setChecked(True)
            add_qgis_btn.setDisabled(True)

        # Connect these functions to the buttons
        active_btn.toggled.connect(remove_visibility_if_deselected)

        def on_add_project_toggled(checked: bool):
            if active_btn.isChecked() and not checked:
                add_project_btn.setChecked(True)
                return
            remove_from_project_if_deselected()

        add_project_btn.toggled.connect(on_add_project_toggled)
        add_qgis_btn.toggled.connect(remove_from_qgis_if_deselected)

        # Set the states according to the current project status
        if qgis_connection_exists(map_layer) and getattr(map_layer, "kind", None) != "fun":
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
# Final Page
# ---------------------------------------------------------------------------


class FinalSetupPage(QWizardPage):
    def __init__(self, parent=None, plugin=None):
        super().__init__(parent)
        self._plugin = plugin
        self.setTitle("Sonstige Einstellungen")
        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(12)

        # Collapsable Group Box for advanced options
        self._advanced_box = QgsCollapsibleGroupBox(self)
        self._advanced_box.setTitle("Erweiterte Optionen für Profis")
        self._advanced_box.setCollapsed(True)  # Start collapsed

        advanced_layout = QVBoxLayout(self._advanced_box)
        self._styles_group = StyleLibraryGroup(plugin, self)
        advanced_layout.addWidget(self._styles_group)

        self.layout.addWidget(self._advanced_box)
        self.layout.addStretch(1)


# ---------------------------------------------------------------------------
# SetupDialog
# ---------------------------------------------------------------------------
class SetupDialog(QWizard):
    """Wizard für Projekt-Setup und Karten-Installation.

    ``switch_to`` ist nach dem Schließen gesetzt, wenn der Nutzer zum klassischen Dialog wechseln möchte.
    """

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self._plugin = plugin
        self.switch_to: Optional[str] = None
        self.setWindowTitle("THW Toolbox Setup")
        # Der Windows-Aero-Stil zeichnet die Seiten immer weiß, unabhängig vom QGIS-Theme.
        # ModernStyle verwendet die Palette der Anwendung und bleibt so auch im Dark-Theme lesbar.
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.resize(620, 640)

        # Page builder calls
        self.start_pg = StartPage(parent=self)
        self.crs_pg = CrsPage(parent=self)
        self.base_map_pg = BaseMapPage(parent=self)
        self.add_layer_pg = AddLayerPage(parent=self)
        self.final_setup_pg = FinalSetupPage(parent=self, plugin=self._plugin)

        self.addPage(self.start_pg)
        self.addPage(self.crs_pg)
        self.addPage(self.base_map_pg)
        self.addPage(self.add_layer_pg)
        self.addPage(self.final_setup_pg)

        self.start_pg.classic_btn.clicked.connect(self._on_switch_to_classic)

    def _on_switch_to_classic(self) -> None:
        self.switch_to = SETUP_MODE_CLASSIC
        self.reject()

    def run(self) -> bool:
        """Zeigt den Assistenten modal an und wendet bei "Fertig" die Auswahl auf das Projekt an.

        Returns True if the wizard was completed, False if cancelled.
        """
        logger.debug("Setup-Dialog started.")
        result = self.exec()

        if result != QDialog.DialogCode.Accepted:
            logger.debug("Setup Canceled, No action")
            return False

        logger.debug("Dialog completed")
        # 0. Set the CRS first so that all following steps (layers, zoom) use the final project CRS
        apply_project_crs(self._plugin, self.parentWidget(), self.crs_pg.get_selected_epsg())

        # 1. Add the static basemap connections to the QGIS browser
        for bm in self.base_map_pg.get_qgis_bms():
            if qgis_connection_exists(bm):
                logger.debug("Base Map %s already added to QGIS", bm.name)
            else:
                logger.debug("Adding Basemap %s as permanent connection", bm.name)
                install_qgis_connection(bm)

        # 2. Add the basemaps to the project
        active_bm = self.base_map_pg.get_active_bm()
        for bm in self.base_map_pg.get_project_bms():
            if exists_in_project(bm):
                continue
            add_basemap_to_project(bm, visible=bm == active_bm)

        # 3. Add the additional layer connections to the QGIS browser
        for map_layer in self.add_layer_pg.get_qgis_layers():
            if qgis_connection_exists(map_layer):
                logger.debug("Additional Layer %s already added to QGIS", map_layer.name)
            else:
                logger.debug("Adding Additional Layer %s as permanent connection", map_layer.name)
                install_qgis_connection(map_layer)
        reload_browser()

        # 4. Add the additional layers to the project
        active_layers = self.add_layer_pg.get_active_layers()
        for map_layer in self.add_layer_pg.get_project_layers():
            if exists_in_project(map_layer):
                continue
            add_layer_to_project(map_layer, visible=map_layer in active_layers)

        # 5. Zoom to Germany if the setup is run the first time
        if not self._plugin.action.isChecked():
            zoom_to_germany()
            # 6. Activate the Plugin if not already done
            self._plugin.activate()

        # Collapse all layers in the Layer view
        iface.layerTreeView().collapseAll()
        return True
