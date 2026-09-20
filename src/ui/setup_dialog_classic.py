"""Klassischer Setup-Dialog: Projekt-Status prüfen + Karten installieren (alles auf einer Seite)."""

from typing import Optional

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..logging_utils import get_logger
from ..tools.layer_setup import (
    ADD_LAYERS,
    BASEMAPS,
    MapLayer,
    add_basemap_to_project,
    add_layer_to_project,
    additional_layers_by_category,
    basemaps_by_category,
    exists_in_project,
    get_project_crs,
    install_qgis_connection,
    qgis_connection_exists,
    reload_browser,
    zoom_to_germany,
)
from .setup_common import (
    EPSG_DEFAULT,
    FAIL_COLOR,
    OK_COLOR,
    SETUP_MODE_CLASSIC,
    SETUP_MODE_WIZARD,
    StyleLibraryGroup,
    apply_project_crs,
    crs_label_for_epsg,
    get_setup_mode,
    push_message,
    set_setup_mode,
    set_status,
)

logger = get_logger(__name__)

_TARGET_EPSG = EPSG_DEFAULT
_TARGET_AUTHID = f"EPSG:{_TARGET_EPSG}"
_TARGET_LABEL = crs_label_for_epsg(_TARGET_EPSG)


class ClassicSetupDialog(QDialog):
    """Dialog für Projekt-Setup und Karten-Installation.

    ``switch_to`` ist nach dem Schließen gesetzt, wenn der Nutzer zum Assistenten wechseln möchte.
    """

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self._plugin = plugin
        self.switch_to: Optional[str] = None
        self.setWindowTitle("THW Toolbox Setup")
        self.resize(620, 680)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        self._content_layout = QVBoxLayout(container)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(12)
        scroll.setWidget(container)
        outer.addWidget(scroll, 1)

        self._status_group = self._build_status_group()
        self._content_layout.addWidget(self._status_group)

        self._styles_group = StyleLibraryGroup(plugin, self)
        self._content_layout.addWidget(self._styles_group)

        self._maps_group = self._build_maps_group()
        self._content_layout.addWidget(self._maps_group)

        self._content_layout.addWidget(self._build_wizard_group())
        self._content_layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        outer.addWidget(buttons)

        self._refresh_all()

    # ---------------------------------------------------------------- status

    def _build_status_group(self) -> QGroupBox:
        box = QGroupBox("Projekt-Status")
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        self._crs_label = QLabel()
        self._crs_label.setWordWrap(True)
        self._crs_fix_btn = QPushButton(f"Auf {_TARGET_AUTHID} setzen")
        self._crs_fix_btn.clicked.connect(self._fix_crs)

        self._basemap_label = QLabel()
        self._basemap_label.setWordWrap(True)
        self._basemap_fix_btn = QPushButton("OSM laden")
        self._basemap_fix_btn.clicked.connect(self._fix_basemap)

        self._zoom_label = QLabel("Kartenansicht auf Deutschland zentrieren")
        self._zoom_label.setWordWrap(True)
        self._zoom_btn = QPushButton("Auf Deutschland zoomen")
        self._zoom_btn.clicked.connect(zoom_to_germany)

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

    @staticmethod
    def _title_label(text: str) -> QLabel:
        lbl = QLabel(text)
        f = QFont(lbl.font())
        f.setBold(True)
        lbl.setFont(f)
        return lbl

    def _refresh_status(self) -> None:
        current = get_project_crs()
        if current == _TARGET_EPSG:
            set_status(self._crs_label, True, f"{_TARGET_AUTHID} ({_TARGET_LABEL})")
            self._crs_fix_btn.setEnabled(False)
        else:
            current_text = f"EPSG:{current}" if current is not None else "nicht gesetzt / kein EPSG"
            set_status(self._crs_label, False, f"Aktuell: {current_text} – erwartet {_TARGET_AUTHID} ({_TARGET_LABEL})")
            self._crs_fix_btn.setEnabled(True)

        if any(exists_in_project(bm) for bm in BASEMAPS):
            set_status(self._basemap_label, True, "Basiskarte geladen")
            self._basemap_fix_btn.setEnabled(False)
        else:
            set_status(self._basemap_label, False, "Keine bekannte Basiskarte im Projekt")
            self._basemap_fix_btn.setEnabled(True)

    def _fix_crs(self) -> None:
        apply_project_crs(self._plugin, self, _TARGET_EPSG)
        self._refresh_all()

    def _fix_basemap(self) -> None:
        osm = next((b for b in BASEMAPS if b.key == "osm"), None)
        if osm is not None and not exists_in_project(osm):
            add_basemap_to_project(osm, visible=True)
        self._refresh_all()

    # ------------------------------------------------------------------ maps

    def _build_maps_group(self) -> QGroupBox:
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

        self._map_rows: dict[str, dict] = {}
        tabs = QTabWidget()
        for category, items in basemaps_by_category().items():
            tabs.addTab(self._build_category_tab(items, is_basemap=True), category)
        for category, items in additional_layers_by_category().items():
            tabs.addTab(self._build_category_tab(items, is_basemap=False), category)
        vbox.addWidget(tabs)

        box.setLayout(vbox)
        return box

    def _build_category_tab(self, items: list, is_basemap: bool) -> QWidget:
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
        for map_layer in items:
            inner_layout.addWidget(self._build_map_row(map_layer, is_basemap))
        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        page_layout.addWidget(scroll, 1)
        return page

    def _build_map_row(self, map_layer: MapLayer, is_basemap: bool) -> QWidget:
        row = QFrame()
        row.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QGridLayout(row)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(8)
        layout.setColumnStretch(0, 1)

        name = QLabel(map_layer.name)
        f = QFont(name.font())
        f.setBold(True)
        name.setFont(f)
        layout.addWidget(name, 0, 0)

        status = QLabel()
        status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(status, 0, 1)

        desc = QLabel(map_layer.description)
        desc.setStyleSheet("color: gray;")
        desc.setWordWrap(True)
        layout.addWidget(desc, 1, 0, 1, 2)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        add_btn = QPushButton("Zum Projekt hinzufügen")
        add_btn.clicked.connect(lambda _=False, m=map_layer, b=is_basemap: self._on_install_and_add(m, b))
        btn_row.addStretch(1)
        conn_btn = None
        if map_layer.kind != "fun":
            conn_btn = QPushButton("Nur Verbindung")
            conn_btn.clicked.connect(lambda _=False, m=map_layer: self._on_install_connection_only(m))
            btn_row.addWidget(conn_btn)
        btn_row.addWidget(add_btn)
        layout.addLayout(btn_row, 2, 0, 1, 2)

        self._map_rows[map_layer.key] = {"status": status, "add": add_btn, "conn": conn_btn}
        return row

    def _refresh_maps(self) -> None:
        for map_layer in BASEMAPS + ADD_LAYERS:
            refs = self._map_rows.get(map_layer.key)
            if not refs:
                continue
            in_project = exists_in_project(map_layer)
            has_conn = qgis_connection_exists(map_layer)
            parts = []
            if in_project:
                parts.append(f"<span style='color:{OK_COLOR};'>✓ im Projekt</span>")
            if has_conn:
                parts.append(f"<span style='color:{OK_COLOR};'>✓ Verbindung</span>")
            if not parts:
                parts.append(f"<span style='color:{FAIL_COLOR};'>nicht installiert</span>")
            refs["status"].setText(" · ".join(parts))
            refs["add"].setEnabled(not in_project)

    def _on_install_and_add(self, map_layer: MapLayer, is_basemap: bool) -> None:
        try:
            if map_layer.kind != "fun" and not qgis_connection_exists(map_layer):
                install_qgis_connection(map_layer)
            if not exists_in_project(map_layer):
                if is_basemap:
                    add_basemap_to_project(map_layer, visible=True)
                else:
                    add_layer_to_project(map_layer, visible=True)
            if not exists_in_project(map_layer):
                push_message(f"Fehler beim Hinzufügen von '{map_layer.name}'.", critical=True)
        except Exception as e:
            logger.error("Fehler beim Hinzufügen von %s: %s", map_layer.name, e)
            push_message(f"Fehler beim Hinzufügen von '{map_layer.name}': {e}", critical=True)
        self._refresh_all()

    def _on_install_connection_only(self, map_layer: MapLayer) -> None:
        install_qgis_connection(map_layer)
        reload_browser()
        self._refresh_all()

    # ---------------------------------------------------------------- wizard

    def _build_wizard_group(self) -> QGroupBox:
        box = QGroupBox("Einrichtungs-Assistent")
        vbox = QVBoxLayout()
        vbox.setSpacing(6)

        hint = QLabel(
            "Der Assistent führt Schritt für Schritt durch Koordinatensystem, Hintergrundkarte und Zusatzlagen."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        vbox.addWidget(hint)

        row = QHBoxLayout()
        self._wizard_default_cb = QCheckBox("Assistent künftig standardmäßig öffnen")
        self._wizard_default_cb.setChecked(get_setup_mode() == SETUP_MODE_WIZARD)
        self._wizard_default_cb.toggled.connect(self._on_wizard_default_toggled)
        row.addWidget(self._wizard_default_cb, 1)

        open_btn = QPushButton("Assistent jetzt starten")
        open_btn.clicked.connect(self._on_switch_to_wizard)
        row.addWidget(open_btn)

        vbox.addLayout(row)
        box.setLayout(vbox)
        return box

    @staticmethod
    def _on_wizard_default_toggled(checked: bool) -> None:
        set_setup_mode(SETUP_MODE_WIZARD if checked else SETUP_MODE_CLASSIC)

    def _on_switch_to_wizard(self) -> None:
        self.switch_to = SETUP_MODE_WIZARD
        self.accept()

    # ------------------------------------------------------------------ misc

    def _refresh_all(self) -> None:
        self._refresh_status()
        self._styles_group.refresh()
        self._refresh_maps()
