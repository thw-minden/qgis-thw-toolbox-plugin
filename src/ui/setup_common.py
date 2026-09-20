"""Gemeinsame Bausteine für den klassischen Setup-Dialog und den Setup-Assistenten:
Modus-Einstellung, CRS-Wechsel mit Migrationsschutz, Statusanzeige und Symbolbibliothek."""

from qgis.core import Qgis, QgsProject, QgsSettings, QgsVectorLayer
from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from qgis.utils import iface

from ..logging_utils import get_logger
from ..tools import style_library
from ..tools.layer_setup import get_project_crs, set_project_crs

logger = get_logger(__name__)

OK_COLOR = "#2e7d32"
FAIL_COLOR = "#c62828"

# ---------------------------------------------------------------------------
# Setup-Modus (global in den QGIS-Einstellungen, nicht im Projekt)
# ---------------------------------------------------------------------------

SETUP_MODE_CLASSIC = "classic"
SETUP_MODE_WIZARD = "wizard"
SETUP_MODE_DEFAULT = SETUP_MODE_WIZARD
_SETUP_MODE_KEY = "THWToolbox/setup_dialog_mode"

# Unterstützte UTM-Zonen für Deutschland
UTM_ZONES = {
    "31* Nord": 25831,
    "32* Nord": 25832,
    "33* Nord": 25833,
}
UTM_ZONE_DEFAULT = "32* Nord"
EPSG_DEFAULT = UTM_ZONES[UTM_ZONE_DEFAULT]


def get_setup_mode() -> str:
    value = QgsSettings().value(_SETUP_MODE_KEY, SETUP_MODE_DEFAULT)
    return value if value in (SETUP_MODE_CLASSIC, SETUP_MODE_WIZARD) else SETUP_MODE_DEFAULT


def set_setup_mode(mode: str) -> None:
    if mode not in (SETUP_MODE_CLASSIC, SETUP_MODE_WIZARD):
        raise ValueError(f"Unknown setup mode {mode!r}")
    QgsSettings().setValue(_SETUP_MODE_KEY, mode)


def crs_label_for_epsg(epsg: int) -> str:
    zone = next((label for label, code in UTM_ZONES.items() if code == epsg), None)
    return f"ETRS89 / UTM Zone {zone}" if zone else f"EPSG:{epsg}"


# ---------------------------------------------------------------------------
# Kleine UI-Helfer
# ---------------------------------------------------------------------------


def push_message(msg: str, critical: bool = False) -> None:
    try:
        level = Qgis.MessageLevel.Critical if critical else Qgis.MessageLevel.Info
        iface.messageBar().pushMessage("THW Setup", msg, level=level)
    except Exception as e:
        logger.debug("Konnte Setup-Meldung nicht anzeigen: %s", e)


def set_status(label: QLabel, ok: bool, text: str) -> None:
    prefix = "✓" if ok else "✗"
    color = OK_COLOR if ok else FAIL_COLOR
    label.setText(f"<span style='color:{color}; font-weight:bold;'>{prefix}</span> {text}")


# ---------------------------------------------------------------------------
# CRS-Wechsel mit Schutz vorhandener Daten
# ---------------------------------------------------------------------------


def project_has_user_content(plugin) -> bool:
    """True if the project has marker features or any non-marker vector layer."""
    layer_manager = getattr(plugin, "layer_manager", None)
    marker_layer = layer_manager.layer if layer_manager else None
    if marker_layer and marker_layer.featureCount() > 0:
        return True
    for lyr in QgsProject.instance().mapLayers().values():
        if lyr is marker_layer:
            continue
        if isinstance(lyr, QgsVectorLayer):
            return True
    return False


def apply_project_crs(plugin, parent: QWidget, epsg: int) -> bool:
    """Sets the project CRS to ``epsg``.

    If the project already contains user data, the user is asked whether the
    tactical markers should be migrated (reprojected and re-saved) into the new
    CRS, whether the CRS should just be switched, or whether to keep the CRS.
    Returns True if the CRS is set afterwards, False if the user cancelled or it failed.
    """
    if get_project_crs() == epsg:
        logger.debug("Project CRS already EPSG:%d, nothing to do", epsg)
        return True

    target_authid = f"EPSG:{epsg}"
    target_label = crs_label_for_epsg(epsg)

    if not project_has_user_content(plugin):
        return set_project_crs(epsg)

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Koordinatensystem ändern")
    box.setText(
        f"Projekt-CRS wird auf {target_authid} ({target_label}) umgestellt.\n\n"
        "Im Projekt sind bereits Daten vorhanden. Bestehende Taktische "
        "Zeichen können sich dadurch sichtbar verschieben."
    )
    box.setInformativeText(
        "Migrieren: THW-Zeichen werden in das neue CRS umgerechnet und "
        "neu gespeichert (Position bleibt erhalten).\n"
        "Einfach ausführen: Nur Projekt-CRS ändern. Bestehende Zeichen "
        "werden von QGIS on-the-fly reprojiziert.\n"
        "Abbrechen: Koordinatensystem bleibt unverändert."
    )
    cancel_btn = box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
    migrate_btn = box.addButton("Migrieren", QMessageBox.ButtonRole.AcceptRole)
    force_btn = box.addButton("Einfach ausführen", QMessageBox.ButtonRole.DestructiveRole)
    box.setDefaultButton(cancel_btn)
    box.exec()

    clicked = box.clickedButton()
    if clicked is cancel_btn:
        logger.debug("CRS change cancelled by user")
        return False
    if clicked is migrate_btn:
        layer_manager = getattr(plugin, "layer_manager", None)
        new_layer = layer_manager.reproject_to(target_authid, log=push_message) if layer_manager else None
        if new_layer is None:
            push_message("Migration fehlgeschlagen, Koordinatensystem bleibt unverändert.", critical=True)
            return False
        plugin.on_layer_replaced(new_layer)
    elif clicked is not force_btn:
        return False

    return set_project_crs(epsg)


# ---------------------------------------------------------------------------
# Symbolbibliothek
# ---------------------------------------------------------------------------


class StyleLibraryGroup(QGroupBox):
    """Gruppe zum Import/Entfernen der Taktischen Zeichen in die QGIS-Stilbibliothek."""

    def __init__(self, plugin, parent=None):
        super().__init__("Symbolbibliothek", parent)
        self._plugin = plugin

        vbox = QVBoxLayout()
        vbox.setSpacing(6)

        hint = QLabel("Macht die Taktischen Zeichen projektübergreifend im Symbol-Auswahldialog verfügbar.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        vbox.addWidget(hint)

        row = QHBoxLayout()
        self._status = QLabel()
        self._status.setWordWrap(True)
        row.addWidget(self._status, 1)

        self._remove_btn = QPushButton("Stile entfernen")
        self._remove_btn.clicked.connect(self._on_remove)
        row.addWidget(self._remove_btn)

        self._import_btn = QPushButton("Stile importieren")
        self._import_btn.clicked.connect(self._on_import)
        row.addWidget(self._import_btn)

        vbox.addLayout(row)
        self.setLayout(vbox)
        self.refresh()

    def refresh(self) -> None:
        present, total = style_library.status(self._plugin.plugin_dir)
        if total == 0:
            set_status(self._status, False, "Keine SVGs gefunden")
            self._import_btn.setEnabled(False)
            self._remove_btn.setEnabled(False)
            return
        set_status(self._status, present == total, f"{present} von {total} Symbolen importiert")
        self._import_btn.setEnabled(True)
        self._remove_btn.setEnabled(present > 0)

    def _on_import(self) -> None:
        _, total = style_library.status(self._plugin.plugin_dir)
        if total == 0:
            push_message("Keine SVGs gefunden.", critical=True)
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
            push_message(f"Import abgebrochen. {written} Symbole bereits geschrieben.")
        else:
            push_message(
                f"{written} von {total_done} Symbolen zur Stilbibliothek hinzugefügt."
                " Hinweis: Symbol-Auswahldialog ggf. neu öffnen.",
                critical=written == 0,
            )
        self.refresh()

    def _on_remove(self) -> None:
        removed = style_library.remove_styles(self._plugin.plugin_dir)
        push_message(f"{removed} Symbole aus der Stilbibliothek entfernt.")
        self.refresh()
