import json
import re
import urllib.parse
import urllib.request

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)
from qgis.PyQt.QtCore import QObject, QSize, Qt, QThread, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "QGIS-THW-Toolbox-Plugin/1.0 (https://github.com/thw-minden/qgis-thw-toolbox-plugin)"
_REQUEST_TIMEOUT = 10
_MIN_QUERY_LEN = 3
_COUNTRY_CODES = "de,at,ch"

# Erkennt z.B. "Pflugstraße 7", "Hauptstr. 12a", optional gefolgt von ", Berlin" oder ", 10117 Berlin"
_HOUSENUMBER_RE = re.compile(r"^\s*(?P<street>.+?)\s+(?P<number>\d+\s*[a-zA-Z]?)\s*(?:,\s*(?P<rest>.+))?\s*$")


def _build_query_variants(query):
    """Liefert eine Liste von (params-dict, label)-Tupeln, die nacheinander probiert werden.

    Bei Adressen mit Hausnummer wird eine strukturierte Suche bevorzugt (zuverlässiger
    bei Haus-Nummern), zusätzlich die Freiform-Suche als Fallback.
    """
    variants = []

    match = _HOUSENUMBER_RE.match(query)
    if match:
        street = match.group("street").strip()
        number = match.group("number").strip().replace(" ", "")
        rest = (match.group("rest") or "").strip()

        # Nominatim erwartet im street-Feld das Format "<hausnummer> <straße>"
        structured = {"street": f"{number} {street}"}
        if rest:
            # rest kann "10117 Berlin" oder "Berlin" sein
            postcode_match = re.match(r"^(\d{4,5})\s+(.+)$", rest)
            if postcode_match:
                structured["postalcode"] = postcode_match.group(1)
                structured["city"] = postcode_match.group(2)
            else:
                structured["city"] = rest
        variants.append((structured, "strukturiert"))

    variants.append(({"q": query}, "freie Suche"))
    return variants


class _SearchWorker(QObject):
    """Führt eine blockierende Nominatim-Anfrage in einem Worker-Thread aus."""

    finished = pyqtSignal(str, object, str)  # query, results-or-None, error-message

    def __init__(self, query):
        super().__init__()
        self._query = query

    def run(self):
        common = {
            "format": "json",
            "limit": "10",
            "addressdetails": "1",
            "accept-language": "de",
            "countrycodes": _COUNTRY_CODES,
            "dedupe": "1",
        }

        merged = []
        seen_keys = set()
        last_error = None

        for variant_params, _label in _build_query_variants(self._query):
            params = {**common, **variant_params}
            url = f"{_NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
            # Defense-in-depth: urlopen accepts file:// and custom schemes by
            # default. Pin to https so a future refactor of _NOMINATIM_URL can't
            # accidentally open a local file or arbitrary scheme.
            if not url.startswith("https://"):
                last_error = "Unsichere URL abgelehnt"
                continue
            try:
                req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:  # nosec B310 - scheme validated to https above
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception as exc:
                last_error = str(exc)
                continue

            for r in data:
                key = r.get("place_id") or (r.get("osm_type"), r.get("osm_id"))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                merged.append(r)

        if not merged and last_error:
            self.finished.emit(self._query, None, last_error)
            return
        self.finished.emit(self._query, merged, "")


_TITLE_ROLE = Qt.ItemDataRole.UserRole + 1
_SUBTITLE_ROLE = Qt.ItemDataRole.UserRole + 2


class _ResultDelegate(QStyledItemDelegate):
    """Zwei-Zeilen-Item: fetter Titel, kleinere Adresszeile, Themen-konforme Selektion."""

    _PADDING = 8
    _SPACING = 4

    def paint(self, painter, option, index):
        painter.save()

        # Hintergrund + Selektion vom Style zeichnen lassen (Theme-konform)
        opt = option
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, widget)

        title = index.data(_TITLE_ROLE) or ""
        subtitle = index.data(_SUBTITLE_ROLE) or ""

        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        title_color = opt.palette.highlightedText().color() if selected else opt.palette.text().color()
        subtitle_color = QColor(title_color)
        subtitle_color.setAlpha(160)

        rect = opt.rect.adjusted(self._PADDING, self._PADDING, -self._PADDING, -self._PADDING)

        title_font = QFont(opt.font)
        title_font.setBold(True)
        title_font.setPointSizeF(opt.font.pointSizeF() + 1)
        painter.setFont(title_font)
        painter.setPen(title_color)
        title_metrics = painter.fontMetrics()
        title_height = title_metrics.height()
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, title)

        if subtitle:
            sub_font = QFont(opt.font)
            sub_font.setPointSizeF(max(opt.font.pointSizeF() - 1, 8))
            painter.setFont(sub_font)
            painter.setPen(subtitle_color)
            sub_rect = rect.adjusted(0, title_height + self._SPACING, 0, 0)
            sub_metrics = painter.fontMetrics()
            elided = sub_metrics.elidedText(subtitle, Qt.TextElideMode.ElideRight, sub_rect.width())
            painter.drawText(sub_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, elided)

        painter.restore()

    def sizeHint(self, option, index):
        title_font = QFont(option.font)
        title_font.setBold(True)
        title_font.setPointSizeF(option.font.pointSizeF() + 1)
        sub_font = QFont(option.font)
        sub_font.setPointSizeF(max(option.font.pointSizeF() - 1, 8))
        from qgis.PyQt.QtGui import QFontMetrics

        h = QFontMetrics(title_font).height() + self._SPACING + QFontMetrics(sub_font).height() + self._PADDING * 2
        return QSize(0, h)


class NominatimSearchDialog(QDialog):
    """Adress-Suchdialog mit Nominatim. Tippen löst Suche aus, Klick navigiert."""

    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self._canvas = canvas
        self.setWindowTitle("Adresse suchen (Nominatim / OpenStreetMap)")
        self.resize(550, 450)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        search_row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("Adresse oder Ort eingeben...")
        self._input.returnPressed.connect(self._search)
        search_row.addWidget(self._input)

        self._search_btn = QPushButton("Suchen")
        self._search_btn.setAutoDefault(False)
        self._search_btn.setDefault(False)
        self._search_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._search_btn.clicked.connect(self._search)
        search_row.addWidget(self._search_btn)
        layout.addLayout(search_row)

        self._status = QLabel("")
        self._status.setStyleSheet("color: gray;")
        layout.addWidget(self._status)

        self._results = QListWidget()
        self._results.setItemDelegate(_ResultDelegate(self._results))
        self._results.setUniformItemSizes(True)
        self._results.itemClicked.connect(self._zoom_to_item)
        layout.addWidget(self._results)

        self._active_thread = None
        self._active_worker = None
        self._active_query = None

    def _search(self):
        query = self._input.text().strip()
        if len(query) < _MIN_QUERY_LEN:
            self._status.setText(f"Bitte mindestens {_MIN_QUERY_LEN} Zeichen eingeben")
            return

        self._status.setText(f"Suche nach: {query}")
        self._search_btn.setEnabled(False)

        self._active_query = query
        thread = QThread(self)
        worker = _SearchWorker(query)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_worker_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        # Referenzen halten, damit Python die Wrapper nicht per GC einsammelt
        self._active_thread = thread
        self._active_worker = worker
        thread.start()

    def _on_worker_finished(self, query, results, error):
        self._search_btn.setEnabled(True)
        # Veraltete Antwort verwerfen
        if query != self._active_query:
            return

        if error:
            self._status.setText(f"Fehler: {error}")
            return

        self._results.clear()
        if not results:
            self._status.setText(f"Keine Treffer für: {query}")
            return

        self._status.setText(f"{len(results)} Treffer für: {query}")
        for r in results:
            title, subtitle = _format_result(r)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, r)
            item.setData(_TITLE_ROLE, title)
            item.setData(_SUBTITLE_ROLE, subtitle)
            self._results.addItem(item)

    def _zoom_to_item(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        try:
            lon = float(data["lon"])
            lat = float(data["lat"])
        except (KeyError, ValueError):
            return

        src_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        dst_crs = self._canvas.mapSettings().destinationCrs()
        transform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
        center = transform.transform(QgsPointXY(lon, lat))

        bbox = data.get("boundingbox")
        if bbox and len(bbox) == 4:
            try:
                south, north, west, east = (float(b) for b in bbox)
                sw = transform.transform(QgsPointXY(west, south))
                ne = transform.transform(QgsPointXY(east, north))
                self._canvas.setExtent(QgsRectangle(sw, ne))
                self._canvas.refresh()
                self.accept()
                return
            except ValueError:
                pass

        buffer = 200
        rect = QgsRectangle(center.x() - buffer, center.y() - buffer, center.x() + buffer, center.y() + buffer)
        self._canvas.setExtent(rect)
        self._canvas.refresh()
        self.accept()


def _format_result(result):
    """Erzeugt Titel + Untertitel. Bevorzugt strukturierte address-Details, damit
    Hausnummer + Straße gemeinsam im Titel landen."""
    address = result.get("address") or {}
    display = result.get("display_name", "?")

    # Straße / Hausnummer aus address bestimmen
    street = address.get("road") or address.get("pedestrian") or address.get("footway")
    house = address.get("house_number")

    if street and house:
        title = f"{street} {house}"
    elif street:
        title = street
    else:
        # Fallback: erstes Element aus display_name. Falls das nur eine Zahl ist
        # (typisch bei strukturierter Suche, die "7, Pflugstraße, ..." liefert),
        # die Zahl mit dem nächsten Bestandteil kombinieren.
        parts = [p.strip() for p in display.split(",") if p.strip()]
        if not parts:
            title = display
        elif parts[0].replace(" ", "").isdigit() or _is_house_number(parts[0]):
            title = f"{parts[1]} {parts[0]}" if len(parts) > 1 else parts[0]
            parts = parts[2:]
            return title, ", ".join(parts)
        else:
            title = parts[0]

    # Untertitel: Ortsteil, PLZ + Ort, Gemeinde, Bundesland, Land
    sub_parts = []
    locality = address.get("city") or address.get("town") or address.get("village") or address.get("municipality")
    if locality:
        postcode = address.get("postcode")
        sub_parts.append(f"{postcode} {locality}" if postcode else locality)
    suburb = address.get("suburb") or address.get("city_district")
    if suburb and suburb != locality:
        sub_parts.insert(0, suburb)
    # Gemeinde + Bundesland, damit gleichnamige Orte unterscheidbar sind
    # (z.B. Minden/NRW vs. Minden/Südeifel)
    municipality = address.get("municipality")
    if municipality and municipality != locality:
        sub_parts.append(municipality)
    state = address.get("state")
    if state:
        sub_parts.append(state)
    country = address.get("country")
    if country:
        sub_parts.append(country)

    subtitle = ", ".join(sub_parts) if sub_parts else display
    return title, subtitle


def _is_house_number(text):
    """Erkennt Strings wie '7', '12a', '42 b'."""
    return bool(re.match(r"^\d+\s*[a-zA-Z]?$", text.replace(" ", "")))
