"""Marker-Tabelle: mehrere Taktische Zeichen schnell per UTMREF oder Breite/Länge anlegen und bearbeiten."""

import os
from contextlib import contextmanager

from qgis.core import QgsCoordinateTransform, QgsPointXY, QgsProject
from qgis.PyQt.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QColor, QIcon
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..logging_utils import get_logger
from ..util.coordinates import WGS84, parse_position, to_mgrs
from .marker_import_dialog import MarkerImportDialog
from .symbol_picker import SymbolPickerDialog, symbol_catalog

logger = get_logger(__name__)

COL_SYMBOL, COL_LABEL, COL_SHOW, COL_POS = range(4)
FID_ROLE = Qt.ItemDataRole.UserRole  # COL_SYMBOL: Feature-ID, None = neue Zeile
PATH_ROLE = Qt.ItemDataRole.UserRole + 1  # COL_SYMBOL: absoluter SVG-Pfad
POINT_ROLE = Qt.ItemDataRole.UserRole + 2  # COL_POS: (Länge, Breite) in WGS84

FORMAT_UTMREF = "utmref"
FORMAT_LATLON = "latlon"
_INVALID_BG = QColor(255, 215, 215)
_POSITION_HINT = "UTMREF (z. B. 32U MC 68258 82070) oder Breite Länge (z. B. 52.188180 8.535691)"


class _MarkerTable(QTableWidget):
    """Tabelle, die Zeichen aus der Symbolpalette per Drag & Drop annimmt (Zeile -1 = unter den Zeilen)."""

    svg_dropped = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(0, 4, parent)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)

    @staticmethod
    def _svg_path(event) -> str | None:
        mime = event.mimeData()
        if not mime.hasText():
            return None
        path = mime.text().strip()
        return path if path.lower().endswith(".svg") and os.path.exists(path) else None

    def dragEnterEvent(self, event):
        if self._svg_path(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        path = self._svg_path(event)
        if not path:
            event.ignore()
            return
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        event.acceptProposedAction()
        self.svg_dropped.emit(self.rowAt(pos.y()), path)


class MarkerTableDialog(QDialog):
    """Nicht-modales Fenster mit allen Markern als Tabelle.

    Jede Zeile ist ein Marker; Änderungen an Zeichen, Beschriftung und Position
    werden sofort übernommen. In der letzten Zeile (*) legt man neue Marker an:
    sobald Zeichen und Position gültig sind, entsteht der Marker. Änderungen auf
    der Karte (Verschieben, Marker Details) erscheinen automatisch in der Tabelle.
    """

    def __init__(self, plugin, navigate_callback, parent=None):
        super().__init__(parent)
        self._plugin = plugin
        self._navigate = navigate_callback
        self._catalog = symbol_catalog(plugin.plugin_dir)
        self._bound_layer = None
        self._filling = False  # Tabelle wird programmatisch befüllt → itemChanged ignorieren
        self._applying = False  # eigene Layer-Änderung läuft → Commit-Signal ignorieren
        self._refresh_pending = False
        self._refresh_scheduled = False

        self.setWindowTitle("Marker-Tabelle")
        self.resize(780, 480)
        layout = QVBoxLayout(self)

        hint = QLabel(
            "Neue Marker in der Zeile mit * anlegen: Zeichen per Doppelklick wählen (oder aus der "
            "Symbolpalette hineinziehen) und Position als UTMREF oder „Breite Länge“ eingeben. "
            "Änderungen werden sofort auf der Karte übernommen."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        top = QHBoxLayout()
        top.addWidget(QLabel("Position anzeigen als:"))
        self.format_combo = QComboBox()
        self.format_combo.addItem("UTMREF", FORMAT_UTMREF)
        self.format_combo.addItem("Breite Länge", FORMAT_LATLON)
        self.format_combo.currentIndexChanged.connect(self._reformat_positions)
        top.addWidget(self.format_combo)
        top.addStretch()
        layout.addLayout(top)

        self.table = _MarkerTable(self)
        self.table.setHorizontalHeaderLabels(["Zeichen", "Beschriftung", "Anzeigen", "Position"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_SHOW, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_POS, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(COL_SYMBOL, 230)
        self.table.setColumnWidth(COL_LABEL, 170)
        self.table.setIconSize(QSize(28, 28))
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellActivated.connect(self._on_cell_activated)
        self.table.svg_dropped.connect(self._on_svg_dropped)
        self.table.verticalHeader().sectionDoubleClicked.connect(self._show_row_on_map)
        layout.addWidget(self.table)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("QLabel { color: #c0392b; }")
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        btn_pick = QPushButton("Zeichen wählen …")
        btn_pick.clicked.connect(self._pick_symbol_for_current_row)
        buttons.addWidget(btn_pick)
        btn_show = QPushButton("Auf Karte zeigen")
        btn_show.clicked.connect(lambda: self._show_row_on_map(self.table.currentRow()))
        buttons.addWidget(btn_show)
        btn_delete = QPushButton("Löschen")
        btn_delete.clicked.connect(self._delete_selected)
        buttons.addWidget(btn_delete)
        btn_import = QPushButton("Importieren …")
        btn_import.setToolTip("Marker aus einer CSV- oder Excel-Datei übernehmen")
        btn_import.clicked.connect(self._import_file)
        buttons.addWidget(btn_import)
        btn_template = QPushButton("Vorlage speichern …")
        btn_template.setToolTip("Beispieltabelle (Excel oder CSV) zum Ausfüllen und anschließenden Importieren")
        btn_template.clicked.connect(self._save_import_template)
        buttons.addWidget(btn_template)
        buttons.addStretch()
        btn_close = QPushButton("Schließen")
        btn_close.clicked.connect(self.close)
        buttons.addWidget(btn_close)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    # Aufbau / Synchronisation mit dem Layer
    # ------------------------------------------------------------------

    @contextmanager
    def _silent(self):
        previous = self._filling
        self._filling = True
        try:
            yield
        finally:
            self._filling = previous

    def _apply(self, action):
        """Führt eine Layer-Änderung aus, ohne dass deren Commit die Tabelle neu aufbaut."""
        self._applying = True
        try:
            return action()
        finally:
            self._applying = False

    def _bind_layer(self):
        layer = self._plugin.layer
        if layer is not self._bound_layer:
            if self._bound_layer is not None:
                try:
                    self._bound_layer.afterCommitChanges.disconnect(self._on_layer_committed)
                except (TypeError, RuntimeError):
                    pass
            self._bound_layer = layer
            if layer is not None:
                layer.afterCommitChanges.connect(self._on_layer_committed)
        return layer

    def _on_layer_committed(self):
        # Änderung von außerhalb (Karte, Marker Details) → gesammelt neu laden
        if self._applying or self._refresh_scheduled:
            return
        self._refresh_scheduled = True
        QTimer.singleShot(0, self.refresh)

    def refresh(self):
        """Tabelle aus dem Marker-Layer neu aufbauen; angefangene neue Zeilen bleiben erhalten."""
        self._refresh_scheduled = False
        if self.table.state() == QAbstractItemView.State.EditingState:
            self._refresh_pending = True
            return
        self._refresh_pending = False

        layer = self._bind_layer()
        drafts = [self._row_values(r) for r in range(self.table.rowCount()) if self._is_draft_with_content(r)]
        current = (self.table.currentRow(), self.table.currentColumn())
        scroll = self.table.verticalScrollBar().value()

        with self._silent():
            self.table.setRowCount(0)
            if layer is not None:
                names = layer.fields().names()
                to_wgs = QgsCoordinateTransform(layer.crs(), WGS84, QgsProject.instance())
                for feat in layer.getFeatures():
                    point = None
                    if feat.hasGeometry():
                        try:
                            point = to_wgs.transform(feat.geometry().asPoint())
                        except Exception:
                            logger.exception("Konnte Marker %s nicht nach WGS84 umrechnen", feat.id())
                    self._append_row(
                        fid=feat.id(),
                        path=self._catalog.absolute(feat["svg_path"]) if feat["svg_path"] else None,
                        label=feat["label"] if "label" in names else "",
                        show=bool(feat["show_label"]) if "show_label" in names else False,
                        point=point,
                    )
            for values in drafts:
                self._append_row(**values)
        self._ensure_empty_row()

        self.table.setEnabled(layer is not None)
        self.table.verticalScrollBar().setValue(scroll)
        if 0 <= current[0] < self.table.rowCount():
            self.table.setCurrentCell(*current)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow() and self._refresh_pending:
            self.refresh()

    # ------------------------------------------------------------------
    # Zeilen
    # ------------------------------------------------------------------

    def _append_row(self, fid=None, path=None, label="", show=False, point=None, pos_text=""):
        row = self.table.rowCount()
        self.table.insertRow(row)

        symbol_item = QTableWidgetItem()
        symbol_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        symbol_item.setData(FID_ROLE, fid)
        self.table.setItem(row, COL_SYMBOL, symbol_item)
        self._set_symbol(symbol_item, path)

        self.table.setItem(row, COL_LABEL, QTableWidgetItem(label or ""))

        show_item = QTableWidgetItem()
        show_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
        show_item.setCheckState(Qt.CheckState.Checked if show else Qt.CheckState.Unchecked)
        show_item.setToolTip("Beschriftung auf der Karte anzeigen")
        self.table.setItem(row, COL_SHOW, show_item)

        pos_item = QTableWidgetItem()
        self.table.setItem(row, COL_POS, pos_item)
        self._set_position(pos_item, point, pos_text)

        self._update_row_header(row)

    def _set_symbol(self, item, path):
        item.setData(PATH_ROLE, path)
        if not path:
            item.setIcon(QIcon())
            item.setText("")
            item.setToolTip("Doppelklick: Zeichen wählen")
            return
        symbol = self._catalog.by_path(path)
        item.setIcon(self._catalog.icon(path))
        item.setText(symbol.name if symbol else os.path.splitext(os.path.basename(path))[0])
        item.setToolTip((symbol.label if symbol else path) + "\nDoppelklick: anderes Zeichen wählen")

    def _set_position(self, item, point, text=""):
        item.setData(POINT_ROLE, None if point is None else (point.x(), point.y()))
        item.setBackground(QBrush())
        item.setToolTip(_POSITION_HINT)
        item.setText(self._format(point) if point is not None else text)

    def _format(self, point) -> str:
        if self.format_combo.currentData() == FORMAT_UTMREF:
            try:
                return to_mgrs(point.y(), point.x())
            except ValueError:
                pass
        return f"{point.y():.6f} {point.x():.6f}"

    def _reformat_positions(self):
        with self._silent():
            for row in range(self.table.rowCount()):
                point = self._point(row)
                if point is not None:
                    self.table.item(row, COL_POS).setText(self._format(point))

    def _update_row_header(self, row):
        if self._fid(row) is None:
            self.table.setVerticalHeaderItem(row, QTableWidgetItem("*"))
        else:
            self.table.setVerticalHeaderItem(row, QTableWidgetItem(str(row + 1)))

    def _ensure_empty_row(self):
        last = self.table.rowCount() - 1
        if last < 0 or self._fid(last) is not None or self._is_draft_with_content(last):
            with self._silent():
                self._append_row()

    def _fid(self, row):
        return self.table.item(row, COL_SYMBOL).data(FID_ROLE)

    def _path(self, row):
        return self.table.item(row, COL_SYMBOL).data(PATH_ROLE)

    def _point(self, row) -> QgsPointXY | None:
        value = self.table.item(row, COL_POS).data(POINT_ROLE)
        return None if value is None else QgsPointXY(*value)

    def _label(self, row) -> str:
        return self.table.item(row, COL_LABEL).text().strip()

    def _show_checked(self, row) -> bool:
        return self.table.item(row, COL_SHOW).checkState() == Qt.CheckState.Checked

    def _is_draft_with_content(self, row) -> bool:
        if self._fid(row) is not None:
            return False
        return bool(
            self._path(row) or self._label(row) or self.table.item(row, COL_POS).text().strip()
        ) or self._show_checked(row)

    def _row_values(self, row) -> dict:
        return {
            "path": self._path(row),
            "label": self._label(row),
            "show": self._show_checked(row),
            "point": self._point(row),
            "pos_text": self.table.item(row, COL_POS).text(),
        }

    # ------------------------------------------------------------------
    # Bearbeiten
    # ------------------------------------------------------------------

    def _on_item_changed(self, item):
        if self._filling:
            return
        row, col = item.row(), item.column()
        fid = self._fid(row)
        self.status_label.clear()

        if col == COL_POS:
            point = self._read_position_cell(item)
            if point is not None and fid is not None:
                if self._apply(lambda: self._plugin.move_feature_to(fid, point, WGS84, center=False)) is None:
                    self.status_label.setText(f"Zeile {row + 1}: Position konnte nicht übernommen werden.")
        elif col == COL_LABEL and fid is not None:
            self._apply(lambda: self._plugin.update_feature_label(fid, self._label(row)))
            self._plugin.refresh_marker_views([fid])
        elif col == COL_SHOW and fid is not None:
            self._apply(lambda: self._plugin.toggle_label_visibility(fid, self._show_checked(row)))
            self._plugin.refresh_marker_views([fid])

        if fid is None:
            self._try_create(row)
        self._ensure_empty_row()
        if self._refresh_pending:
            QTimer.singleShot(0, self.refresh)

    def _read_position_cell(self, item) -> QgsPointXY | None:
        """Liest die eingegebene Position; ungültige Eingaben werden rot markiert."""
        row = item.row()
        text = item.text().strip()
        previous = self._point(row)
        if not text:
            # Bestehende Marker brauchen eine Position → alten Wert wiederherstellen
            with self._silent():
                self._set_position(item, previous if self._fid(row) is not None else None)
            return None
        try:
            point = parse_position(text)
        except ValueError as e:
            with self._silent():
                if self._fid(row) is None:
                    item.setData(POINT_ROLE, None)
                item.setBackground(QBrush(_INVALID_BG))
                item.setToolTip(str(e))
            self.status_label.setText(f"Zeile {row + 1}: {e}")
            return None
        with self._silent():
            self._set_position(item, point)
        return point

    def _try_create(self, row):
        """Legt den Marker an, sobald eine neue Zeile Zeichen und gültige Position hat."""
        path, point = self._path(row), self._point(row)
        if not path or point is None:
            return
        label = self._label(row)
        feature = self._apply(
            lambda: self._plugin.create_marker(
                path, point, WGS84, label=label or None, show_label=self._show_checked(row)
            )
        )
        if feature is None:
            self.status_label.setText(f"Zeile {row + 1}: Marker konnte nicht angelegt werden.")
            return
        with self._silent():
            self.table.item(row, COL_SYMBOL).setData(FID_ROLE, feature.id())
            if not label:
                self.table.item(row, COL_LABEL).setText(feature["label"] or "")
        self._update_row_header(row)
        self._ensure_empty_row()
        # Weiter mit der nächsten neuen Zeile
        QTimer.singleShot(0, lambda: self.table.setCurrentCell(self.table.rowCount() - 1, COL_SYMBOL))

    def _on_cell_activated(self, row, col):
        if col == COL_SYMBOL:
            self._pick_symbol(row)

    def _row_waiting_for_symbol(self) -> int:
        """Erste angefangene neue Zeile ohne Zeichen, sonst die leere Zeile am Ende."""
        for row in range(self.table.rowCount()):
            if self._is_draft_with_content(row) and not self._path(row):
                return row
        return self.table.rowCount() - 1

    def _pick_symbol_for_current_row(self):
        row = self.table.currentRow()
        self._pick_symbol(row if row >= 0 else self._row_waiting_for_symbol())

    def _pick_symbol(self, row):
        path = SymbolPickerDialog.pick(self._catalog, self, self._path(row))
        if path:
            self._set_row_symbol(row, path)

    def _on_svg_dropped(self, row, path):
        self._set_row_symbol(row if row >= 0 else self._row_waiting_for_symbol(), path)

    def _set_row_symbol(self, row, path):
        self.status_label.clear()
        fid = self._fid(row)
        with self._silent():
            self._set_symbol(self.table.item(row, COL_SYMBOL), path)
        if fid is not None:
            if not self._apply(lambda: self._plugin.change_marker_symbol(fid, path)):
                self.status_label.setText(f"Zeile {row + 1}: Zeichen konnte nicht geändert werden.")
        elif self._point(row) is None:
            # Neue Zeile: direkt zur Positionseingabe springen
            self.table.setCurrentCell(row, COL_POS)
            self.table.editItem(self.table.item(row, COL_POS))
        else:
            self._try_create(row)
        self._ensure_empty_row()

    def _save_import_template(self):
        path = MarkerImportDialog.save_template(self)
        if path:
            self.status_label.setText(f"Vorlage gespeichert: {path} – ausfüllen und über „Importieren …“ einlesen.")

    def _import_file(self):
        """Zeilen aus CSV/Excel übernehmen: vollständige werden Marker, der Rest bleibt als Entwurf stehen."""
        rows = MarkerImportDialog.ask(self._catalog, self)
        if not rows:
            return
        self.status_label.clear()
        created = failed = 0
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            with self._silent():
                last = self.table.rowCount() - 1
                if last >= 0 and self._fid(last) is None and not self._is_draft_with_content(last):
                    self.table.removeRow(last)
                for values in rows:
                    values.pop("unknown", None)
                    path, point, label = values["path"], values["point"], values["label"]
                    feature = None
                    if path and point is not None:
                        feature = self._apply(
                            lambda v=values: self._plugin.create_marker(
                                v["path"], v["point"], WGS84, label=v["label"] or None, show_label=v["show"]
                            )
                        )
                        if feature is None:
                            failed += 1
                    if feature is not None:
                        created += 1
                        self._append_row(
                            fid=feature.id(),
                            path=path,
                            label=label or feature["label"],
                            show=values["show"],
                            point=point,
                        )
                        continue
                    self._append_row(**values)
                    pos_item = self.table.item(self.table.rowCount() - 1, COL_POS)
                    if point is None and values["pos_text"].strip():
                        self._read_position_cell(pos_item)
        finally:
            QApplication.restoreOverrideCursor()
        self._ensure_empty_row()
        self.table.scrollToBottom()

        drafts = len(rows) - created
        text = f"Importierte Marker: {created}."
        if drafts:
            text += f" Noch unvollständige Zeilen: {drafts} (Zeichen oder Position ergänzen)."
        if failed:
            text += f" Nicht angelegt: {failed}."
        self.status_label.setText(text)

    def _delete_selected(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if not rows and self.table.currentRow() >= 0:
            rows = [self.table.currentRow()]
        fids = [self._fid(r) for r in rows if self._fid(r) is not None]
        drafts = [r for r in rows if self._is_draft_with_content(r)]
        if not fids and not drafts:
            return
        if fids:
            answer = QMessageBox.question(self, "Marker löschen", f"{len(fids)} Marker von der Karte löschen?")
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._apply(lambda: self._plugin.delete_markers(fids))
        with self._silent():
            for row in reversed(rows):
                if self._fid(row) in fids or row in drafts:
                    self.table.removeRow(row)
            for row in range(self.table.rowCount()):
                self._update_row_header(row)
        self._ensure_empty_row()

    def _show_row_on_map(self, row):
        if 0 <= row < self.table.rowCount() and self._fid(row) is not None:
            self._navigate(self._fid(row))
