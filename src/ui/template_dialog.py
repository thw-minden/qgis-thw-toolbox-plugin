"""Dialog zum Auswählen und Öffnen von Druckvorlagen (.qpt), mitgelieferten wie eigenen."""

import os

from qgis.core import QgsProject, QgsSettings
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)
from qgis.utils import iface

from ..layout.legend import in_legend, legend_candidates, set_in_legend
from ..layout.print_template import (
    GITTER_NAMEN,
    KARTENTITEL_VORSCHLAEGE,
    PrintInfo,
    TemplateEntry,
    add_user_template,
    bundeslogo_items,
    is_logo_confirmed,
    list_templates,
    load_print_template,
    ortsverband_names,
    set_logo_confirmed,
    user_template_path,
)

_LAST_DIR_KEY = "THWToolbox/print/template_import_dir"


class TemplateDialog(QDialog):
    """Listet mitgelieferte und eigene Vorlagen und öffnet die gewählte im Designer.

    Ortsverband, Einheit, Bearbeiter und Gitter werden in den Benutzereinstellungen
    gemerkt, Kartentitel und Einsatz im Projekt, die Legenden-Auswahl an den Layern.
    """

    def __init__(self, plugin_dir: str, parent=None):
        super().__init__(parent)
        self._plugin_dir = plugin_dir
        self._assets_dir = os.path.join(plugin_dir, "templates", "assets")
        self.setWindowTitle("THW Toolbox – Druckvorlagen")
        self.resize(820, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        hint = QLabel(
            "Wählen Sie eine Druckvorlage und öffnen Sie sie im QGIS Layout-Designer. "
            "Die Vorlage wird als neues Layout im Projekt angelegt."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        layout.addWidget(hint)

        info = PrintInfo.load()
        left = QVBoxLayout()
        left.addWidget(self._build_template_box(), 3)
        left.addWidget(self._build_legend_box(info), 2)
        columns = QHBoxLayout()
        columns.addLayout(left, 1)
        columns.addWidget(self._build_info_box(info), 1)
        layout.addLayout(columns, 1)

        btn_row = QHBoxLayout()
        self._open_btn = QPushButton("Im Designer öffnen")
        self._open_btn.setDefault(True)
        self._open_btn.clicked.connect(self._open_selected)
        btn_row.addStretch(1)
        btn_row.addWidget(self._open_btn)
        layout.addLayout(btn_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate()

    def _build_template_box(self) -> QGroupBox:
        box = QGroupBox("Vorlage")
        vbox = QVBoxLayout(box)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(False)
        self._tree.setMinimumHeight(240)  # alle mitgelieferten Vorlagen ohne Scrollen
        self._tree.itemDoubleClicked.connect(lambda *_: self._open_selected())
        self._tree.currentItemChanged.connect(lambda *_: self._update_buttons())
        vbox.addWidget(self._tree, 1)

        row = QHBoxLayout()
        add_btn = QPushButton("Eigene hinzufügen …")
        add_btn.setToolTip(
            "Eigene Layoutvorlagen (.qpt) aufnehmen. Die Angaben aus diesem Dialog stehen darin als "
            "Layout-Variablen bereit, z. B. [% @thw_kartentitel %], [% @thw_ov %], [% @thw_bearbeiter %]."
        )
        add_btn.clicked.connect(self._add_own_templates)
        self._remove_btn = QPushButton("Entfernen")
        self._remove_btn.setToolTip("Gewählte eigene Vorlage entfernen")
        self._remove_btn.clicked.connect(self._remove_own_template)
        row.addWidget(add_btn)
        row.addWidget(self._remove_btn)
        row.addStretch(1)
        vbox.addLayout(row)
        return box

    def _build_legend_box(self, info: PrintInfo) -> QGroupBox:
        self._legend_box = QGroupBox("Zeichenerklärung aufräumen")
        self._legend_box.setCheckable(True)
        self._legend_box.setChecked(info.tidy_legend)
        self._legend_box.setToolTip(
            "Aus: Die Legende der Vorlage bleibt, wie sie ist, und folgt dem Layerbaum des Projekts."
        )
        vbox = QVBoxLayout(self._legend_box)

        hint = QLabel(
            "Angehakte Layer erscheinen in der Legende, jedes Zeichen einmal und gleich groß. "
            "Hintergrundkarten stehen unter „Quellen“. Später im Designer: Layout → Legende aktualisieren."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        vbox.addWidget(hint)

        self._legend_list = QListWidget()
        for layer in legend_candidates():
            item = QListWidgetItem(layer.name())
            item.setData(Qt.ItemDataRole.UserRole, layer.id())
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if in_legend(layer) else Qt.CheckState.Unchecked)
            self._legend_list.addItem(item)
        vbox.addWidget(self._legend_list, 1)
        return self._legend_box

    def _build_info_box(self, info: PrintInfo) -> QGroupBox:
        box = QGroupBox("Angaben im Kartenrand")
        form = QFormLayout(box)

        self._ov_edit = QLineEdit(info.ortsverband)
        self._ov_edit.setPlaceholderText("Name eingeben, z. B. Aachen")
        completer = QCompleter(ortsverband_names(self._plugin_dir), self._ov_edit)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._ov_edit.setCompleter(completer)
        form.addRow("Ortsverband:", self._ov_edit)

        self._einheit_edit = QLineEdit(info.einheit)
        form.addRow("Einheit:", self._einheit_edit)

        self._einheit_kurz_edit = QLineEdit(info.einheit_kurz)
        self._einheit_kurz_edit.setToolTip("Kurzform für den Bildnachweis, z. B. „TrUL“")
        form.addRow("Einheit (Kürzel):", self._einheit_kurz_edit)

        self._zeichen_check = QCheckBox("Taktisches Zeichen Trupp UL oben rechts zeigen")
        self._zeichen_check.setChecked(info.einheit_zeichen)
        self._zeichen_check.setToolTip("In den Vorlagen der THW Toolbox: zeigt, dass die Karte vom Trupp UL stammt")
        form.addRow("", self._zeichen_check)

        self._bearbeiter_edit = QLineEdit(info.bearbeiter)
        form.addRow("Bearbeiter:", self._bearbeiter_edit)

        self._kartentitel_combo = QComboBox()
        self._kartentitel_combo.setEditable(True)
        self._kartentitel_combo.addItems(KARTENTITEL_VORSCHLAEGE)
        self._kartentitel_combo.setCurrentText(info.kartentitel)
        self._kartentitel_combo.setToolTip("Sagt, was die Karte zeigt. Wird im Projekt gespeichert")
        form.addRow("Kartentitel:", self._kartentitel_combo)

        self._einsatz_edit = QLineEdit(info.einsatz)
        self._einsatz_edit.setToolTip("Wird im Projekt gespeichert")
        form.addRow("Einsatz:", self._einsatz_edit)

        self._einsatzort_edit = QLineEdit(info.einsatzort)
        self._einsatzort_edit.setToolTip("Steht mit dem Einsatz unter dem Kartentitel. Wird im Projekt gespeichert")
        form.addRow("Einsatzort:", self._einsatzort_edit)

        self._blatt_edit = QLineEdit(info.blatt)
        self._blatt_edit.setPlaceholderText("nur bei mehreren Blättern, z. B. 2/4")
        form.addRow("Blatt:", self._blatt_edit)

        self._gitter_combo = QComboBox()
        for key, name in GITTER_NAMEN.items():
            self._gitter_combo.addItem(name, key)
        self._gitter_combo.setCurrentIndex(self._gitter_combo.findData(info.gitter))
        self._gitter_combo.setToolTip(
            "Gitter mit Randbeschriftung in den Vorlagen der THW Toolbox: UTMREF-Meldegitter oder "
            "geografische Koordinaten. Welches Gitter die Karte trägt, steht in der Seitenleiste."
        )
        form.addRow("Gitter:", self._gitter_combo)

        return box

    def _current_info(self) -> PrintInfo:
        return PrintInfo(
            ortsverband=self._ov_edit.text().strip(),
            einheit=self._einheit_edit.text().strip(),
            einheit_kurz=self._einheit_kurz_edit.text().strip(),
            einheit_zeichen=self._zeichen_check.isChecked(),
            bearbeiter=self._bearbeiter_edit.text().strip(),
            einsatz=self._einsatz_edit.text().strip(),
            kartentitel=self._kartentitel_combo.currentText().strip(),
            einsatzort=self._einsatzort_edit.text().strip(),
            blatt=self._blatt_edit.text().strip(),
            gitter=self._gitter_combo.currentData(),
            tidy_legend=self._legend_box.isChecked(),
        )

    def _populate(self, select_path: str | None = None) -> None:
        """Vorlagen nach Gruppen auflisten; `select_path` bzw. die erste Vorlage wird ausgewählt."""
        self._tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        selected = None
        for entry in list_templates(self._plugin_dir):
            group = groups.get(entry.group)
            if group is None:
                group = groups[entry.group] = QTreeWidgetItem(self._tree, [entry.group])
                group.setFlags(Qt.ItemFlag.ItemIsEnabled)
                font = group.font(0)
                font.setBold(True)
                group.setFont(0, font)
            item = QTreeWidgetItem(group, [entry.name])
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(0, entry.path)
            if selected is None or entry.path == select_path:
                selected = item
        self._tree.expandAll()

        if selected is None:
            empty = QTreeWidgetItem(self._tree, ["Keine Vorlagen (.qpt) gefunden."])
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
        else:
            self._tree.setCurrentItem(selected)
        self._update_buttons()

    def _current_entry(self) -> TemplateEntry | None:
        item = self._tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _update_buttons(self) -> None:
        entry = self._current_entry()
        self._open_btn.setEnabled(entry is not None)
        self._remove_btn.setEnabled(entry is not None and entry.is_own)

    def _add_own_templates(self) -> None:
        settings = QgsSettings()
        start = settings.value(_LAST_DIR_KEY, os.path.expanduser("~"))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Eigene Druckvorlagen hinzufügen", start, "QGIS-Layoutvorlagen (*.qpt)"
        )
        if not paths:
            return
        settings.setValue(_LAST_DIR_KEY, os.path.dirname(paths[0]))

        added = None
        for path in paths:
            target = user_template_path(path)
            if os.path.exists(target) and os.path.abspath(target) != os.path.abspath(path):
                answer = QMessageBox.question(
                    self,
                    "Vorlage ersetzen?",
                    f"Es gibt bereits eine eigene Vorlage „{os.path.basename(target)}“. Ersetzen?",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    continue
            try:
                added = add_user_template(path)
            except OSError as e:
                QMessageBox.critical(self, "Fehler", f"Vorlage konnte nicht übernommen werden:\n{e}")
        self._populate(added)

    def _remove_own_template(self) -> None:
        entry = self._current_entry()
        if entry is None or not entry.is_own:
            return
        answer = QMessageBox.question(
            self,
            "Vorlage entfernen?",
            f"Die eigene Vorlage „{entry.name}“ wird aus der Toolbox entfernt. "
            "Bereits im Projekt angelegte Layouts bleiben erhalten.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            os.remove(entry.path)
        except OSError as e:
            QMessageBox.critical(self, "Fehler", f"Vorlage konnte nicht entfernt werden:\n{e}")
        self._populate()

    def _save_legend_choice(self) -> None:
        project = QgsProject.instance()
        for row in range(self._legend_list.count()):
            item = self._legend_list.item(row)
            layer = project.mapLayer(item.data(Qt.ItemDataRole.UserRole))
            if layer is not None:
                set_in_legend(layer, item.checkState() == Qt.CheckState.Checked)

    def _open_selected(self) -> None:
        entry = self._current_entry()
        if entry is None:
            return

        info = self._current_info()
        if not info.ortsverband:
            QMessageBox.warning(self, "Ortsverband fehlt", "Bitte den Ortsverband angeben.")
            self._ov_edit.setFocus()
            return
        info.save()
        if info.tidy_legend:
            self._save_legend_choice()

        try:
            layout = load_print_template(entry.path, info, self._assets_dir, iface.mapCanvas())
        except ValueError as e:
            QMessageBox.critical(self, "Fehler", str(e))
            return

        if not self._confirm_logo(layout):
            return

        layout.setName(self._unique_layout_name(entry.layout_name))
        QgsProject.instance().layoutManager().addLayout(layout)
        self.accept()
        # Erst nach dem Schließen öffnen: sonst holt der schließende Dialog das Hauptfenster vor den Designer
        QTimer.singleShot(0, lambda: _show_designer(layout))

    def _confirm_logo(self, layout) -> bool:
        """Berechtigung für das Bundeslogo abfragen. `False`, wenn die Vorlage nicht geöffnet werden soll."""
        logos = bundeslogo_items(layout)
        if not logos or is_logo_confirmed():
            return True

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Bundeslogo verwenden?")
        box.setText(
            "Diese Vorlage trägt das Logo „Bundesanstalt Technisches Hilfswerk“ mit Bundesadler.\n\n"
            "Es darf nur für dienstliche Zwecke des THW verwendet werden. "
            "Sind Sie berechtigt, die Karte mit diesem Logo zu erstellen und weiterzugeben?"
        )
        with_logo = box.addButton("Ja, mit Logo öffnen", QMessageBox.ButtonRole.AcceptRole)
        without_logo = box.addButton("Ohne Logo öffnen", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        remember = QCheckBox("Berechtigung merken und nicht mehr fragen")
        box.setCheckBox(remember)
        box.exec()

        clicked = box.clickedButton()
        if clicked is with_logo:
            if remember.isChecked():
                set_logo_confirmed(True)
            return True
        if clicked is without_logo:
            for logo in logos:
                logo.setVisibility(False)
            return True
        return False

    @staticmethod
    def _unique_layout_name(base: str) -> str:
        manager = QgsProject.instance().layoutManager()
        existing = {lay.name() for lay in manager.layouts()}
        if base not in existing:
            return base
        i = 2
        while f"{base} ({i})" in existing:
            i += 1
        return f"{base} ({i})"


def _show_designer(layout) -> None:
    designer = iface.openLayoutDesigner(layout)
    if designer is not None:
        window = designer.window()
        window.raise_()
        window.activateWindow()
