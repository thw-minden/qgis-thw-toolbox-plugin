import os

from qgis.core import QgsApplication, QgsCoordinateTransform, QgsProject
from qgis.gui import QgsMapToolIdentify, QgsScaleWidget
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QColor, QIcon, QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QDockWidget,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpacerItem,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..layer.background import background_of
from ..layer.labeling import label_size_pt
from ..layer.renderer import embedded_svg_path, scale_limits
from ..logging_utils import get_logger
from ..paths import plugin_root
from ..util.coordinates import WGS84, to_mgrs
from .background_popup import BackgroundButton
from .font_size_box import FontSizeBox
from .origin_point_widget import OriginPointWidget
from .symbol_picker import star_icon, symbol_catalog, symbol_prefs
from .theme import muted_color, muted_style

logger = get_logger(__name__)

# Vorschau des Zeichens oben in den Details
_PREVIEW_HEIGHT_PX = 140
_PREVIEW_ICON_PX = 110


def _load_svg_pixmap(path: str, size: int = 180) -> QPixmap | None:
    """Load `path` as a pixmap. Tries QIcon first (better SVG handling),
    then QPixmap as fallback. Returns None if both fail."""
    icon = QIcon(path)
    pixmap = icon.pixmap(size, size)
    if pixmap.isNull():
        pixmap = QPixmap(path)
    return pixmap if not pixmap.isNull() else None


def _svg_pixmap_from_content(svg_content: str, size: int = 180) -> QPixmap | None:
    """Vorschau direkt aus dem gespeicherten SVG-Text über den SVG-Cache von QGIS, ohne Datei."""
    image, _ = QgsApplication.svgCache().svgAsImage(
        embedded_svg_path(svg_content), float(size), QColor(0, 0, 0), QColor(0, 0, 0), 1.0, 1.0
    )
    if image.isNull():
        return None
    pixmap = QPixmap.fromImage(image)
    return pixmap if not pixmap.isNull() else None


def _scale_or_none(widget: QgsScaleWidget) -> float | None:
    """Maßstabszahl des Felds; leer (bzw. ungültig) = keine Grenze."""
    scale = widget.scale()
    return None if widget.isNull() or not scale > 0 else scale


def _row_widget(*widgets: QWidget, stretch: int | None = None) -> QWidget:
    """Mehrere Elemente in einer Zeile; ``stretch`` (Index) füllt die Breite, sonst Leerraum rechts."""
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    for index, widget in enumerate(widgets):
        layout.addWidget(widget, 1 if index == stretch else 0)
    if stretch is None:
        layout.addStretch()
    return box


class _SectionGrid:
    """Raster in Abschnitten: fette Überschrift über beide Spalten, darunter Beschriftung | Feld.

    Ein gemeinsames Raster statt eines Formulars je Abschnitt, damit alle Felder bündig stehen.
    """

    def __init__(self):
        self.grid = QGridLayout()
        self.grid.setColumnStretch(1, 1)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(6)
        self._row = 0

    def section(self, title: str):
        label = QLabel(title)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        if self._row:
            label.setContentsMargins(0, 10, 0, 0)
        self.grid.addWidget(label, self._row, 0, 1, 2)
        self._row += 1

    def row(self, label: QLabel | str, field: QWidget, top: bool = False) -> list[QWidget]:
        """Zeile anhängen; gibt ihre Widgets zurück (zum gemeinsamen Ein-/Ausblenden)."""
        if isinstance(label, str):
            label = QLabel(label) if label else None
        widgets = [field]
        if label is not None:
            vertical = Qt.AlignmentFlag.AlignTop if top else Qt.AlignmentFlag.AlignVCenter
            self.grid.addWidget(label, self._row, 0, Qt.AlignmentFlag.AlignLeft | vertical)
            widgets.append(label)
        self.grid.addWidget(field, self._row, 1)
        self._row += 1
        return widgets


class _RowGroup:
    """Mehrere Rasterzeilen, die gemeinsam ein- und ausgeblendet werden."""

    def __init__(self, *rows: list[QWidget]):
        self._widgets = [widget for row in rows for widget in row]

    def setVisible(self, visible: bool):
        for widget in self._widgets:
            widget.setVisible(visible)


class FeatureDock(QDockWidget):
    """Details zur Auswahl – für taktische Zeichen und Objektplanung gemeinsam.

    Drei Seiten: Hinweis (nichts ausgewählt), Einstellungen eines einzelnen
    taktischen Zeichens und die Objektseite der Objektplanung (``set_objects_page``)
    für Objekte und Mehrfachauswahl.
    """

    def __init__(self, parent=None):
        super().__init__("Details", parent)
        self.setObjectName("THWToolboxDetails")
        self.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.feat = None
        self.objects_page = None

        self.stack = QStackedWidget()
        self.setWidget(self.stack)
        self._empty_page = self._build_empty_page()
        self.stack.addWidget(self._empty_page)

        # Seite „Zeichen“: Einstellungen eines einzelnen taktischen Zeichens
        self.content_widget = QWidget()
        self.main_layout = QVBoxLayout(self.content_widget)
        self._marker_page = QScrollArea()
        self._marker_page.setWidgetResizable(True)
        self._marker_page.setFrameShape(QFrame.Shape.NoFrame)
        self._marker_page.setWidget(self.content_widget)
        self.stack.addWidget(self._marker_page)

        # SVG-Anzeige (Preview des ausgewählten Markers)
        self.svg_label = QLabel()
        self.svg_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.svg_label.setFixedHeight(_PREVIEW_HEIGHT_PX)
        self.main_layout.addWidget(self.svg_label)

        # Favorit wie mit dem Stern im Zeichen-Picker – liegt dann auch in der Hotbar (Tasten 1–9)
        self.favorite_button = QToolButton()
        self.favorite_button.setCheckable(True)
        self.favorite_button.setAutoRaise(True)
        self.favorite_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.favorite_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.favorite_button.clicked.connect(self.on_favorite_clicked)
        self.main_layout.addWidget(self.favorite_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self._favorite_path = None
        self._prefs = None

        # Alle Einstellungen in einem Raster: Beschriftungen links bündig, Felder rechts,
        # dazwischen Abschnitte (Position, Beschriftung, Darstellung, Maßstab)
        form = _SectionGrid()
        self.main_layout.addLayout(form.grid)

        # Position des Ankerpunkts (UTMREF + Breite/Länge) mit Kopier-Button
        form.section("Position")
        self.utmref_label = QLabel("")
        self.btn_copy_coords = QPushButton("Kopieren")
        self.btn_copy_coords.setToolTip("UTMREF in die Zwischenablage kopieren")
        form.row("UTMREF:", _row_widget(self.utmref_label, self.btn_copy_coords, stretch=0))
        self.latlon_label = QLabel("")
        form.row("Breite/Länge:", self.latlon_label)
        # Koordinaten sind Links: Klick öffnet die Positionseingabe
        for label in (self.utmref_label, self.latlon_label):
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
            label.setCursor(Qt.CursorShape.PointingHandCursor)
            label.setToolTip("Klicken, um die Position per Koordinate zu setzen")
            label.linkActivated.connect(self.on_edit_position)

        form.section("Beschriftung")
        self.label_textfield_label = QLabel("Text:")
        self.label_textfield = QLineEdit()
        self.label_textfield.setPlaceholderText("Beschriftung des Zeichens")
        form.row(self.label_textfield_label, self.label_textfield)
        self.cb_enable_label = QCheckBox("Auf der Karte zeigen")
        form.row("", self.cb_enable_label)
        self.font_size_box = FontSizeBox()
        self.font_size_box.size_chosen.connect(self.on_font_size_chosen)
        form.row("Schriftgröße:", _row_widget(self.font_size_box, QLabel("pt")))

        form.section("Darstellung")
        self.size_label = QLabel("Größe:")
        self.size_spinbox = QSpinBox()
        # Klein genug für Millimeter-Größen nach dem Umschalten von „Mit Karte skalieren“
        self.size_spinbox.setMinimum(1)
        self.size_spinbox.setMaximum(2000)
        self.size_spinbox.setValue(50)
        self.size_spinbox.setSingleStep(1)
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setMinimum(1)  # Minimale Größe
        self.size_slider.setMaximum(200)  # Maximale Größe
        self.size_slider.setValue(50)  # Standardwert
        form.row(self.size_label, _row_widget(self.size_spinbox, self.size_slider, stretch=1))

        self.rotation_label = QLabel("Drehung:")
        self.rotation_slider = QSlider(Qt.Orientation.Horizontal)
        self.rotation_slider.setMinimum(-180)
        self.rotation_slider.setMaximum(180)
        self.rotation_slider.setValue(0)
        self.rotation_slider.setSingleStep(10)
        self.rotation_slider.setPageStep(10)
        self.rotation_value_label = QLabel("0°")
        self.rotation_value_label.setMinimumWidth(40)
        self.rotation_value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.row(self.rotation_label, _row_widget(self.rotation_slider, self.rotation_value_label, stretch=0))

        # Hintergrund: Vorschau-Schaltfläche, Form/Farbe/Rand/Größe im Popup
        self.background_button = BackgroundButton()
        self.background_button.changed.connect(self.on_background_changed)
        form.row("Hintergrund:", _row_widget(self.background_button))

        self.origin_label = QLabel("Ankerpunkt:")
        self.origin_widget = OriginPointWidget()
        form.row(self.origin_label, _row_widget(self.origin_widget), top=True)

        form.section("Maßstab")
        self.scale_checkbox = QCheckBox("Mit Karte skalieren")
        form.row("", self.scale_checkbox)
        # Grenzen fürs Mitskalieren: darüber hinaus behält das Zeichen seine Größe
        self.max_scale_widget = self._scale_limit_widget(
            "Beim Reinzoomen über diesen Maßstab hinaus wird das Zeichen nicht mehr größer.\n"
            "Leer = wächst immer mit. Der Knopf daneben übernimmt den aktuellen Maßstab."
        )
        self.min_scale_widget = self._scale_limit_widget(
            "Beim Rauszoomen über diesen Maßstab hinaus wird das Zeichen nicht mehr kleiner.\n"
            "Leer = schrumpft immer mit. Der Knopf daneben übernimmt den aktuellen Maßstab."
        )
        self.scale_limits_widget = _RowGroup(
            form.row("Wächst bis:", self.max_scale_widget),
            form.row("Schrumpft bis:", self.min_scale_widget),
        )

        # Löschen mit etwas Abstand unter den Einstellungen
        self.button_layout = QHBoxLayout()
        self.button_layout.setContentsMargins(0, 12, 0, 0)
        self.btn_delete = QPushButton("Löschen")
        self.btn_delete.setToolTip("Zeichen löschen (Entf)")
        self.button_layout.addWidget(self.btn_delete)
        self.main_layout.addLayout(self.button_layout)

        # Spacer am Ende hinzufügen, um alles nach oben auszurichten
        spacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        self.main_layout.addItem(spacer)

        # Erst sichtbar, wenn Toolbox oder Objektplanung offen sind
        self.hide()
        self.show_placeholder()

    def _scale_limit_widget(self, tooltip: str) -> QgsScaleWidget:
        widget = QgsScaleWidget()
        widget.setAllowNull(True)
        widget.setNull()
        widget.setShowCurrentScaleButton(True)
        widget.setToolTip(tooltip)
        widget.scaleChanged.connect(self.on_scale_limits_changed)
        return widget

    @staticmethod
    def _build_empty_page() -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Nichts ausgewählt")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        hint = QLabel(
            "• Klicken Sie auf ein taktisches Zeichen oder ein Objekt der Objektplanung<br>"
            "• Shift+Klick oder Rahmen aufziehen wählt mehrere aus<br>"
            "• Oder ziehen Sie ein Symbol aus der Symbolpalette auf die Karte<br>"
            "• Oder klicken Sie mit rechts auf die Karte und wählen Sie ein Zeichen"
        )
        hint.setTextFormat(Qt.TextFormat.RichText)
        hint.setWordWrap(True)
        hint.setStyleSheet(muted_style())
        layout.addWidget(hint)
        layout.addStretch()
        return page

    def set_objects_page(self, page: QWidget):
        """Seite für Objekte der Objektplanung und Mehrfachauswahl (``planning.details.SelectionDetails``)."""
        self.objects_page = page
        self.stack.addWidget(page)

    def show_objects(self):
        if self.objects_page is None:
            return
        self.feat = None
        self.objects_page.refresh()
        self.stack.setCurrentWidget(self.objects_page)

    def showing_marker(self, fid=None) -> bool:
        """Zeigt das Dock gerade die Einstellungen eines Zeichens (``fid``: genau dieses)?"""
        if self.feat is None or self.stack.currentWidget() is not self._marker_page:
            return False
        return fid is None or self.feat.id() == fid

    def showing_placeholder(self) -> bool:
        return self.stack.currentWidget() is self._empty_page

    def show_placeholder(self):
        """Nichts ausgewählt: Hinweise anzeigen."""
        self.feat = None
        self.svg_label.clear()
        self.stack.setCurrentWidget(self._empty_page)

    def _show_position(self, point, source_crs):
        """Zeigt die aktuelle Marker-Position als UTMREF und Breite/Länge an."""
        try:
            wgs = QgsCoordinateTransform(source_crs, WGS84, QgsProject.instance()).transform(point)
        except Exception:
            logger.exception("Konnte Marker-Position nicht nach WGS84 umrechnen")
            self.latlon_label.clear()
            self.utmref_label.setText("Fehler")
            self.current_utm_coords = ""
            return

        self.latlon_label.setText(f'<a href="edit">{wgs.y():.6f} {wgs.x():.6f}</a>')
        try:
            utmref = to_mgrs(wgs.y(), wgs.x())
        except ValueError:
            utmref = ""
        self.utmref_label.setText(f'<a href="edit">{utmref or "–"}</a>')
        self.current_utm_coords = utmref

    def on_edit_position(self, _link=None):
        """Klick auf die Koordinaten: Popup für „Breite Länge“ oder UTMREF öffnen."""
        if not getattr(self, "feat", None):
            return
        self.layer_manager.open_position_dialog(self.feat.id())

    def show_feature(self, feat, layer_manager):
        self.feat = feat
        self.layer_manager = layer_manager

        logger.debug(
            "show_feature id=%s svg_path=%s svg_content_len=%d size=%s",
            feat.id(),
            feat.attribute("svg_path") or "N/A",
            len(feat.attribute("svg_content") or ""),
            feat.attribute("size") or "N/A",
        )

        self.stack.setCurrentWidget(self._marker_page)

        try:
            pixmap = None
            svg_path_feat = feat.attribute("svg_path")
            svg_content_feat = feat.attribute("svg_content") or ""

            if svg_content_feat.strip():
                pixmap = _svg_pixmap_from_content(svg_content_feat)

            # Ältere Zeilen ohne Inhalt: Katalogdatei zum gespeicherten Pfad
            if pixmap is None or pixmap.isNull():
                if not os.path.isabs(svg_path_feat):
                    absolute_path = os.path.join(plugin_root(), svg_path_feat)
                    pixmap = _load_svg_pixmap(absolute_path if os.path.exists(absolute_path) else svg_path_feat)
                else:
                    pixmap = _load_svg_pixmap(svg_path_feat)

            if pixmap is not None and not pixmap.isNull():
                scaled_pixmap = pixmap.scaled(
                    _PREVIEW_ICON_PX,
                    _PREVIEW_ICON_PX,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.svg_label.setPixmap(scaled_pixmap)
                self.svg_label.setStyleSheet(
                    "QLabel { border: 1px solid #2E86AB; border-radius: 6px; background-color: white; }"
                )
            else:
                raise Exception("Pixmap konnte nicht geladen werden")

        except Exception as e:
            logger.warning(
                "Fehler beim Laden des SVG-Previews: %s (svg_path=%s, svg_content_len=%d)",
                e,
                feat.attribute("svg_path") or "N/A",
                len(feat.attribute("svg_content") or ""),
            )
            self.svg_label.setText("SVG konnte nicht geladen werden")
            muted = muted_color()
            self.svg_label.setStyleSheet(f"QLabel {{ border: 2px dashed {muted}; color: {muted}; }}")

        self._favorite_path = str(feat.attribute("svg_path") or "") or None
        self._show_favorite()

        # Koordinaten anzeigen
        if feat.geometry():
            self._show_position(feat.geometry().asPoint(), layer_manager.layer.crs())

        # SpinBox und Schieberegler auf aktuelle Größe setzen
        self._set_size_widgets(feat.attribute("size"))

        # Checkbox auf aktuellen Wert setzen oder Standardwert verwenden.
        # Das gespeicherte Attribut ist invertiert (True = feste Bildschirmgröße),
        # siehe layer.renderer.size_unit. Signale blockieren: Anzeigen ist kein Umschalten.
        try:
            scale_with_map = bool(feat.attribute("scale_with_map"))
        except Exception:
            scale_with_map = False
        self.scale_checkbox.blockSignals(True)
        self.scale_checkbox.setChecked(not scale_with_map)
        self.scale_checkbox.blockSignals(False)
        self._show_scale_limits(feat, layer_manager.canvas)

        # Label-Werte setzen
        try:
            label_text = feat.attribute("label") or ""
            show_label = feat.attribute("show_label") or False
        except Exception:
            label_text = ""
            show_label = False
        self.label_textfield.setText(label_text)
        self.cb_enable_label.setChecked(show_label)
        self.font_size_box.set_sizes([label_size_pt(feat, layer_manager.settings)])

        # Hintergrund anzeigen (ohne ihn zurückzuschreiben)
        self.background_button.set_background(background_of(feat))

        # Rotationswert setzen
        try:
            rotation = feat.attribute("rotation") or 0.0
        except Exception:
            rotation = 0.0
        rotation = ((float(rotation) + 180.0) % 360.0) - 180.0
        # Signale blockieren: sonst würde on_rotation_change (noch verbunden)
        # frei gedrehte Werte auf 10°-Schritte runden und zurückschreiben.
        self.rotation_slider.blockSignals(True)
        self.rotation_slider.setValue(int(rotation))
        self.rotation_slider.blockSignals(False)
        self.rotation_value_label.setText(f"{int(rotation)}°")

        # Ankerpunkt setzen
        try:
            origin_x = feat.attribute("origin_x")
            origin_y = feat.attribute("origin_y")
            if origin_x is None:
                origin_x = 1
            if origin_y is None:
                origin_y = 1
        except Exception:
            origin_x = 1
            origin_y = 1
        self.origin_widget.set_origin(int(origin_x), int(origin_y))

        # Buttons, SpinBox, Schieberegler und Checkbox neu verbinden
        self.btn_delete.clicked.disconnect() if self.btn_delete.receivers(self.btn_delete.clicked) > 0 else None
        self.btn_copy_coords.clicked.disconnect() if self.btn_copy_coords.receivers(
            self.btn_copy_coords.clicked
        ) > 0 else None
        self.size_spinbox.valueChanged.disconnect() if self.size_spinbox.receivers(
            self.size_spinbox.valueChanged
        ) > 0 else None
        self.size_slider.valueChanged.disconnect() if self.size_slider.receivers(
            self.size_slider.valueChanged
        ) > 0 else None
        self.scale_checkbox.stateChanged.disconnect() if self.scale_checkbox.receivers(
            self.scale_checkbox.stateChanged
        ) > 0 else None
        self.label_textfield.textChanged.disconnect() if self.label_textfield.receivers(
            self.label_textfield.textChanged
        ) > 0 else None
        self.cb_enable_label.stateChanged.disconnect() if self.cb_enable_label.receivers(
            self.cb_enable_label.stateChanged
        ) > 0 else None
        self.rotation_slider.valueChanged.disconnect() if self.rotation_slider.receivers(
            self.rotation_slider.valueChanged
        ) > 0 else None
        self.rotation_slider.sliderReleased.disconnect() if self.rotation_slider.receivers(
            self.rotation_slider.sliderReleased
        ) > 0 else None
        try:
            self.origin_widget.origin_changed.disconnect()
        except TypeError:
            pass

        self.btn_delete.clicked.connect(self.on_delete)
        self.btn_copy_coords.clicked.connect(self.on_copy_coords)
        self.size_spinbox.valueChanged.connect(self.on_size_change)
        self.size_slider.valueChanged.connect(self.on_size_change)
        self.scale_checkbox.stateChanged.connect(self.on_scale_toggle)
        self.label_textfield.textChanged.connect(self.on_label_changed)
        self.cb_enable_label.stateChanged.connect(self.on_show_label_toggle)
        self.rotation_slider.valueChanged.connect(self.on_rotation_change)
        self.rotation_slider.sliderReleased.connect(self.on_rotation_slider_released)
        self.origin_widget.origin_changed.connect(self.on_origin_changed)

        # Synchronisation zwischen SpinBox und Schieberegler
        self.size_spinbox.valueChanged.connect(self.on_spinbox_changed)
        self.size_slider.valueChanged.connect(self.on_slider_changed)
        # Zeigen und nach vorn holen entscheidet das Plugin anhand der Auswahl
        # (sonst würde jedes Drag-Update alle 300 ms den Tab nach vorn holen)

    def _symbol_prefs(self):
        """Favoriten des Zeichen-Pickers – erst beim ersten Zeichen holen (liest den Katalog ein)."""
        if self._prefs is None:
            self._prefs = symbol_prefs(plugin_root())
            # Stern im Picker oder in einer anderen Ansicht umgeschaltet: hier mitziehen
            self._prefs.changed.connect(self._show_favorite)
        return self._prefs

    def _show_favorite(self):
        """Stern-Knopf zum gezeigten Zeichen; Zeichen außerhalb der Symbolpalette lassen sich nicht merken."""
        path = self._favorite_path
        known = bool(path) and symbol_catalog(plugin_root()).by_path(path) is not None
        favorite = known and self._symbol_prefs().is_favorite(path)
        self.favorite_button.setEnabled(known)
        self.favorite_button.setChecked(favorite)
        self.favorite_button.setIcon(star_icon(favorite, QColor(muted_color())))
        self.favorite_button.setText("Favorit" if favorite else "Zu Favoriten hinzufügen")
        if not known:
            tip = "Nur Zeichen aus der Symbolpalette lassen sich als Favorit merken"
        elif favorite:
            tip = "Klicken, um das Zeichen aus den Favoriten zu entfernen"
        else:
            tip = "Als Favorit merken: liegt dann in der Hotbar (Tasten 1–9) und im Zeichen-Picker unter „Favoriten“"
        self.favorite_button.setToolTip(tip)

    def dispose(self):
        """Beim Entladen: die Favoriten gelten weiter, sollen aber kein gelöschtes Dock mehr rufen."""
        if self._prefs is not None:
            try:
                self._prefs.changed.disconnect(self._show_favorite)
            except (TypeError, RuntimeError):
                pass
            self._prefs = None

    def on_favorite_clicked(self):
        if self._favorite_path:
            # Meldet ``changed`` – das stellt den Knopf passend ein
            self._symbol_prefs().toggle_favorite(self._favorite_path)

    def on_delete(self):
        fid = self.feat.id()
        tool = getattr(self.layer_manager, "move_tool", None)
        if tool is not None and tool.selected_fid == fid:
            tool.clear_selection()
        self.layer_manager.delete_feature(fid)
        self.layer_manager.show_marker_placeholder()

    def on_copy_coords(self):
        """Kopiert die UTMREF-Koordinate in die Zwischenablage"""
        if getattr(self, "current_utm_coords", ""):
            clipboard = QApplication.clipboard()
            clipboard.setText(self.current_utm_coords)
            # Kurze visuelle Bestätigung
            self.btn_copy_coords.setText("Kopiert!")

            # Nach 1 Sekunde zurücksetzen
            QTimer.singleShot(1000, self.reset_copy_button)

    def reset_copy_button(self):
        """Setzt den Kopier-Button zurück"""
        self.btn_copy_coords.setText("Kopieren")

    def on_size_change(self, value):
        self.layer_manager.resize_feature(self.feat.id(), value)

    def on_scale_toggle(self, state):
        new_size = self.layer_manager.toggle_scale(self.feat.id(), state == Qt.CheckState.Checked)
        # Größe wurde auf die neue Einheit (Meter ↔ Millimeter) umgerechnet
        if new_size is not None:
            self._set_size_widgets(new_size)
        self.scale_limits_widget.setVisible(self.scale_checkbox.isChecked())

    def _show_scale_limits(self, feat, canvas):
        """Grenzen fürs Mitskalieren anzeigen – nur bei „Mit Karte skalieren“ von Belang."""
        limits = scale_limits(feat)
        for widget, scale in ((self.max_scale_widget, limits.maxScale), (self.min_scale_widget, limits.minScale)):
            widget.setMapCanvas(canvas)
            widget.blockSignals(True)
            if scale > 0:
                widget.setScale(scale)
            else:
                widget.setNull()
            widget.blockSignals(False)
        self.scale_limits_widget.setVisible(self.scale_checkbox.isChecked())

    def on_scale_limits_changed(self, *_args):
        if not getattr(self, "feat", None):
            return
        self.layer_manager.set_scale_limits(
            self.feat.id(), _scale_or_none(self.max_scale_widget), _scale_or_none(self.min_scale_widget)
        )

    def on_font_size_chosen(self, size_pt: float):
        if not getattr(self, "feat", None):
            return
        self.layer_manager.set_label_size(self.feat.id(), size_pt)

    def _set_size_widgets(self, size):
        """SpinBox und Schieberegler auf `size` setzen, ohne das Feature zu ändern."""
        value = int(round(float(size or 0)))
        for widget in (self.size_spinbox, self.size_slider):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)

    def on_spinbox_changed(self, value):
        """Synchronisiert den Schieberegler mit der SpinBox"""
        self.size_slider.blockSignals(True)  # Verhindere Endlosschleife
        self.size_slider.setValue(value)
        self.size_slider.blockSignals(False)

    def on_slider_changed(self, value):
        """Synchronisiert die SpinBox mit dem Schieberegler"""
        self.size_spinbox.blockSignals(True)  # Verhindere Endlosschleife
        self.size_spinbox.setValue(value)
        self.size_spinbox.blockSignals(False)

    def on_label_changed(self, text):
        """Wird aufgerufen, wenn der Label-Text geändert wird"""
        if not hasattr(self, "feat") or not self.feat:
            return

        # Label im Feature aktualisieren
        self.layer_manager.update_feature_label(self.feat.id(), text)

        # Feature-Daten aktualisieren
        updated_feat = self.layer_manager.layer.getFeature(self.feat.id())
        if updated_feat.isValid():
            self.feat = updated_feat

    def on_show_label_toggle(self, state):
        """Schaltet die Label-Anzeige ein/aus"""
        if not hasattr(self, "feat") or not self.feat:
            return

        show_label = state == Qt.CheckState.Checked
        self.layer_manager.toggle_label_visibility(self.feat.id(), show_label)

    def on_background_changed(self, background):
        """Hintergrund aus dem Popup übernehmen."""
        if not getattr(self, "feat", None):
            return
        self.layer_manager.set_background(self.feat.id(), background)

    def on_rotation_change(self, value):
        """Wird aufgerufen, wenn der Rotationsschieberegler geändert wird"""
        if not hasattr(self, "feat") or not self.feat:
            return

        snapped = int(round(value / 10.0)) * 10
        if snapped != value:
            self.rotation_slider.blockSignals(True)
            self.rotation_slider.setValue(snapped)
            self.rotation_slider.blockSignals(False)

        self.rotation_value_label.setText(f"{snapped}°")
        self.layer_manager.rotate_feature(self.feat.id(), float(snapped))

    def on_rotation_slider_released(self):
        """Wird aufgerufen, wenn der Rotationsschieberegler losgelassen wird"""
        pass

    def on_origin_changed(self, origin_x, origin_y):
        """Wird aufgerufen, wenn der Ankerpunkt geaendert wird"""
        if not hasattr(self, "feat") or not self.feat:
            return

        self.layer_manager.update_origin(self.feat.id(), origin_x, origin_y)

    def hideEvent(self, event):
        # Verschieben-Modus deaktivieren wenn Dock geschlossen wird
        # Die Details schließen auch mit der Objektplanung – dann ohne MoveTool
        move_tool = getattr(getattr(self, "layer_manager", None), "move_tool", None)
        if move_tool is not None:
            move_tool.set_move_mode(False)
            # Cursor zurücksetzen
            if hasattr(self.layer_manager, "canvas"):
                self.layer_manager.canvas.setCursor(Qt.CursorShape.ArrowCursor)

        # Zeige Platzhalter wenn Dock versteckt wird (löscht auch die Preview-Datei)
        self.show_placeholder()

        super().hideEvent(event)


class IdentifyTool(QgsMapToolIdentify):
    def __init__(self, canvas, layer_manager):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer_manager = layer_manager
        self.layer = layer_manager.layer
        self.setCursor(Qt.CursorShape.ArrowCursor)

        # Dock-Widget erstellen
        self.feature_dock = FeatureDock(canvas.parent())
        canvas.parent().addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.feature_dock)
        # Zeige Dock mit Platzhalter initial
        self.feature_dock.show()
        self.feature_dock.show_placeholder()

    def canvasReleaseEvent(self, event):
        # nur Linksklick
        if event.button() != Qt.MouseButton.LeftButton:
            return

        # Identifiziere erstes Feature unter Maus mit größerem Suchradius
        search_radius = 10  # Suchradius in Pixeln
        results = self.identify(
            event.x(), event.y(), [self.layer], QgsMapToolIdentify.IdentifyMode.TopDownStopAtFirst, search_radius
        )
        if not results:
            self.feature_dock.show_placeholder()
            self.feature_dock.show()
            if hasattr(self.layer_manager, "move_tool"):
                self.layer_manager.move_tool.set_move_mode(False)
            return

        feat = results[0].mFeature
        # Feature im Dock anzeigen
        self.feature_dock.show_feature(feat, self.layer_manager)

        # Automatisch in den Verschiebe-Modus wechseln
        if hasattr(self.layer_manager, "move_tool"):
            self.layer_manager.move_tool.set_move_mode(True)
            # Cursor auf ClosedHand ändern, um anzuzeigen, dass das Feature verschiebbar ist
            self.canvas.setCursor(Qt.CursorShape.ClosedHandCursor)
