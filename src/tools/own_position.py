"""Eigener Standort: Position ermitteln, als blauen Punkt anzeigen und dorthin zoomen.

Quellen in dieser Reihenfolge:

1. GPS-Empfänger, den QGIS verbunden hat (Ansicht → Bedienfelder → GPS-Informationen)
2. Standortdienst des Betriebssystems über Qt Positioning – unter Windows die
   Windows-Standortdienste (GPS-Modul, WLAN- oder IP-Ortung, je nach Gerät)

Die Position bleibt nur im Speicher; sie wird nirgends abgelegt oder versendet.
"""

import math
import sys
import time
from dataclasses import dataclass, field

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateTransform,
    QgsCsException,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)
from qgis.gui import QgsMapCanvasItem
from qgis.PyQt.QtCore import QT_VERSION_STR, QObject, QPointF, QRectF, Qt, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QColor, QDesktopServices, QPainter, QPen
from qgis.PyQt.QtWidgets import QApplication, QPushButton

from ..logging_utils import get_logger
from ..util.coordinates import WGS84, to_mgrs

# qgis.PyQt bringt kein QtPositioning mit; manche Linux-Pakete liefern es gar nicht aus
try:
    if QT_VERSION_STR.startswith("5."):
        from PyQt5.QtPositioning import QGeoPositionInfo, QGeoPositionInfoSource
    else:
        from PyQt6.QtPositioning import QGeoPositionInfo, QGeoPositionInfoSource
except ImportError:
    QGeoPositionInfo = QGeoPositionInfoSource = None

logger = get_logger(__name__)

_TITLE = "Eigener Standort"
SOURCE_GPS = "GPS-Empfänger"
SOURCE_SYSTEM = "Windows-Standortdienst" if sys.platform == "win32" else "Standortdienst des Systems"

# Ohne Antwort einer Quelle wird nach dieser Zeit aufgegeben
_TIMEOUT_MS = 20_000
# Danach bewegt sich der Punkt weiter mit
_UPDATE_INTERVAL_MS = 5_000
# Jüngere Positionen werden beim erneuten Klick ohne neue Anfrage angefahren
_MAX_FIX_AGE_S = 30
# So lange nach einer GPS-Position werden ungenauere Positionen des Systems ignoriert
_GPS_PREFERENCE_S = 10
# Kartenausschnitt: Genauigkeitskreis passt hinein, mindestens ± 150 m um den Standort
_MIN_VIEW_RADIUS_M = 150
_VIEW_MARGIN = 1.2
# Ab dieser Ungenauigkeit Hinweis auf WLAN-/IP-Ortung
_COARSE_ACCURACY_M = 250
_METERS_PER_DEGREE = 111_320.0

_DOT = QColor(26, 115, 232)
_DOT_SHADOW = QColor(0, 0, 0, 60)
_ACCURACY_FILL = QColor(26, 115, 232, 38)
_ACCURACY_LINE = QColor(26, 115, 232, 150)
_DOT_RADIUS_PX = 7.0
_RING_PX = 2.5
# Kleinere Genauigkeitskreise verschwinden ohnehin hinter dem Punkt
_MIN_ACCURACY_PX = _DOT_RADIUS_PX + _RING_PX + 3


@dataclass
class _Fix:
    lonlat: QgsPointXY
    accuracy_m: float | None
    source: str
    time: float = field(default_factory=time.monotonic)

    def age(self) -> float:
        return time.monotonic() - self.time


def _offset(lonlat: QgsPointXY, north_m: float, east_m: float) -> QgsPointXY:
    """Punkt um ``north_m``/``east_m`` Meter versetzt (Näherung, für Kreis und Ausschnitt genau genug)."""
    cos_lat = max(math.cos(math.radians(lonlat.y())), 1e-6)
    return QgsPointXY(
        lonlat.x() + east_m / (_METERS_PER_DEGREE * cos_lat),
        lonlat.y() + north_m / _METERS_PER_DEGREE,
    )


def _valid_accuracy(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _format_distance(meters: float) -> str:
    if meters >= 1000:
        return f"{meters / 1000:.1f} km".replace(".", ",")
    return f"{round(meters)} m"


def _format_position(lonlat: QgsPointXY) -> str:
    """UTMREF wie im Rest der Toolbox, außerhalb des UTM-Bereichs Breite/Länge."""
    try:
        return to_mgrs(lonlat.y(), lonlat.x())
    except ValueError:
        return f"{lonlat.y():.6f}, {lonlat.x():.6f}"


class OwnPositionMarker(QgsMapCanvasItem):
    """Blauer Punkt mit Genauigkeitskreis; die Position liegt in WGS84 und wird beim Zeichnen ins Canvas-CRS gerechnet."""

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._lonlat: QgsPointXY | None = None
        self._accuracy_m: float | None = None
        # Über den Zeichen und der Auswahl der Objektplanung
        self.setZValue(110)
        canvas.extentsChanged.connect(self.refresh)
        canvas.destinationCrsChanged.connect(self.refresh)
        self.refresh()

    def dispose(self):
        for signal in (self._canvas.extentsChanged, self._canvas.destinationCrsChanged):
            try:
                signal.disconnect(self.refresh)
            except (TypeError, RuntimeError):
                pass
        scene = self._canvas.scene()
        if scene is not None:
            scene.removeItem(self)

    def set_position(self, lonlat: QgsPointXY, accuracy_m: float | None):
        self._lonlat = lonlat
        self._accuracy_m = accuracy_m
        self.refresh()

    def refresh(self):
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self):
        size = self._canvas.size()
        return QRectF(-50, -50, size.width() + 100, size.height() + 100)

    def _to_px(self, lonlat: QgsPointXY, transform: QgsCoordinateTransform) -> QPointF:
        p = self._canvas.getCoordinateTransform().transform(transform.transform(lonlat))
        return QPointF(p.x(), p.y())

    def paint(self, painter, option=None, widget=None):
        if self._lonlat is None:
            return
        transform = QgsCoordinateTransform(WGS84, self._canvas.mapSettings().destinationCrs(), QgsProject.instance())
        try:
            center = self._to_px(self._lonlat, transform)
            edge = self._to_px(_offset(self._lonlat, self._accuracy_m, 0), transform) if self._accuracy_m else None
        except QgsCsException:
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if edge is not None:
            radius = math.hypot(edge.x() - center.x(), edge.y() - center.y())
            if radius >= _MIN_ACCURACY_PX:
                painter.setPen(QPen(_ACCURACY_LINE, 1.2))
                painter.setBrush(QBrush(_ACCURACY_FILL))
                painter.drawEllipse(center, radius, radius)

        outer = _DOT_RADIUS_PX + _RING_PX
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(_DOT_SHADOW))
        painter.drawEllipse(center + QPointF(0, 1), outer + 1, outer + 1)
        painter.setBrush(QBrush(QColor(Qt.GlobalColor.white)))
        painter.drawEllipse(center, outer, outer)
        painter.setBrush(QBrush(_DOT))
        painter.drawEllipse(center, _DOT_RADIUS_PX, _DOT_RADIUS_PX)


class OwnPositionLocator(QObject):
    """Ermittelt den eigenen Standort, zoomt dorthin und lässt den Punkt danach mitlaufen."""

    # True, solange der Standort gesucht oder auf der Karte angezeigt wird (Button gedrückt)
    active_changed = pyqtSignal(bool)

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self._iface = iface
        self._canvas = iface.mapCanvas()
        self._active = False
        self._marker: OwnPositionMarker | None = None
        self._source = None  # QGeoPositionInfoSource, erst beim ersten Klick angelegt
        self._tracking = False
        self._gps = None  # QgsGpsConnection aus QGIS
        self._fix: _Fix | None = None
        self._zoom_pending = False
        self._system_error = None
        self._message = None
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(_TIMEOUT_MS)
        self._timeout.timeout.connect(self._give_up)

    # ------------------------------------------------------------------
    # Öffentliche Aktionen
    # ------------------------------------------------------------------

    def locate(self):
        """Auf den eigenen Standort zoomen; fragt nur neu an, wenn die letzte Position veraltet ist."""
        self._zoom_pending = True
        self._system_error = None
        self._watch_gps()

        if self._fix is not None and self._fix.age() < _MAX_FIX_AGE_S:
            self._on_fix(self._fix)
            return
        gps_fix = self._gps_fix()
        if gps_fix is not None:
            self._on_fix(gps_fix)
            return

        requested = self._request_system_fix()
        if not requested and self._gps is None:
            self._zoom_pending = False
            self._push(
                "Kein Positionsdienst verfügbar. GPS-Empfänger in QGIS verbinden "
                "(Ansicht → Bedienfelder → GPS-Informationen) oder die Standortdienste des Systems einschalten.",
                Qgis.MessageLevel.Warning,
                8,
            )
            self._update_active()
            return
        self._push("Standort wird ermittelt …", Qgis.MessageLevel.Info)
        self._timeout.start()
        self._update_active()

    def hide(self):
        """Punkt entfernen und keine weiteren Positionen mehr abfragen (bricht auch eine laufende Suche ab)."""
        self._zoom_pending = False
        self._timeout.stop()
        self._clear_message()
        self._stop_tracking()
        self._unwatch_gps()
        self._fix = None
        if self._marker is not None:
            self._marker.dispose()
            self._marker = None
        self._update_active()

    @property
    def active(self) -> bool:
        """Wird der Standort gerade gesucht oder angezeigt?"""
        return self._marker is not None or self._zoom_pending

    def _update_active(self):
        if self.active != self._active:
            self._active = self.active
            self.active_changed.emit(self._active)

    def dispose(self):
        self.hide()
        if self._source is not None:
            self._source.deleteLater()
            self._source = None

    # ------------------------------------------------------------------
    # Positionen
    # ------------------------------------------------------------------

    def _on_fix(self, fix: _Fix):
        # Ein verbundener GPS-Empfänger ist genauer als WLAN-/IP-Ortung
        if (
            fix.source != SOURCE_GPS
            and self._fix is not None
            and self._fix.source == SOURCE_GPS
            and self._fix.age() < _GPS_PREFERENCE_S
        ):
            return
        self._fix = fix

        if self._marker is None:
            self._marker = OwnPositionMarker(self._canvas)
        self._marker.set_position(fix.lonlat, fix.accuracy_m)

        if self._zoom_pending:
            self._zoom_pending = False
            self._timeout.stop()
            if self._zoom_to(fix):
                self._push_found(fix)
        self._start_tracking()
        self._update_active()

    def _zoom_to(self, fix: _Fix) -> bool:
        radius = max(fix.accuracy_m or 0, _MIN_VIEW_RADIUS_M) * _VIEW_MARGIN
        transform = QgsCoordinateTransform(WGS84, self._canvas.mapSettings().destinationCrs(), QgsProject.instance())
        try:
            center = transform.transform(fix.lonlat)
            corners = [transform.transform(_offset(fix.lonlat, d, d)) for d in (-radius, radius)]
        except QgsCsException:
            self._push("Der Standort liegt außerhalb des Koordinatensystems der Karte.", Qgis.MessageLevel.Warning, 8)
            return False
        # Quadrat um den Standort, damit er genau in der Mitte landet
        half = max(max(abs(c.x() - center.x()), abs(c.y() - center.y())) for c in corners)
        self._canvas.setExtent(QgsRectangle(center.x() - half, center.y() - half, center.x() + half, center.y() + half))
        self._canvas.refresh()
        return True

    def _give_up(self):
        if not self._zoom_pending:
            return
        self._zoom_pending = False
        self._timeout.stop()
        self._update_active()

        if self._gps_alive():
            self._push(
                "Der GPS-Empfänger hat noch keine Position (kein Satellitenempfang?). "
                "Bitte unter freiem Himmel erneut versuchen.",
                Qgis.MessageLevel.Warning,
                10,
            )
            return
        error_enum = QGeoPositionInfoSource.Error if QGeoPositionInfoSource is not None else None
        if error_enum is not None and self._system_error in (error_enum.AccessError, error_enum.ClosedError):
            if sys.platform == "win32":
                text = (
                    "Kein Zugriff auf den Standort. In Windows unter Einstellungen → Datenschutz und Sicherheit → "
                    "Standort die Standortdienste und „Desktop-Apps den Zugriff auf Ihren Standort erlauben“ einschalten."
                )
            else:
                text = "Kein Zugriff auf den Standort. Bitte die Standortfreigabe des Betriebssystems prüfen."
            button = None
            if sys.platform == "win32":
                button = QPushButton("Einstellungen öffnen")
                button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("ms-settings:privacy-location")))
            self._push(text, Qgis.MessageLevel.Warning, 0, button)
            return
        self._push(
            f"Kein Standort innerhalb von {_TIMEOUT_MS // 1000} Sekunden ermittelt. Bitte erneut versuchen "
            "oder einen GPS-Empfänger in QGIS verbinden.",
            Qgis.MessageLevel.Warning,
            10,
        )

    # ------------------------------------------------------------------
    # Standortdienst des Systems (Qt Positioning)
    # ------------------------------------------------------------------

    def _system_source(self):
        if self._source is None and QGeoPositionInfoSource is not None:
            source = QGeoPositionInfoSource.createDefaultSource(self)
            if source is None:
                return None
            source.positionUpdated.connect(self._on_system_position)
            # Qt 6: errorOccurred, Qt 5: error
            error_signal = getattr(source, "errorOccurred", None) or source.error
            error_signal.connect(self._on_system_error)
            source.setUpdateInterval(_UPDATE_INTERVAL_MS)
            self._source = source
            logger.debug("Standortdienst: %s", source.sourceName())
        return self._source

    def _request_system_fix(self) -> bool:
        source = self._system_source()
        if source is None:
            return False
        source.requestUpdate(_TIMEOUT_MS)
        return True

    def _start_tracking(self):
        if self._tracking or self._fix is None or self._fix.source != SOURCE_SYSTEM:
            return
        source = self._system_source()
        if source is not None:
            source.startUpdates()
            self._tracking = True

    def _stop_tracking(self):
        if self._tracking and self._source is not None:
            self._source.stopUpdates()
        self._tracking = False

    def _on_system_position(self, info):
        coord = info.coordinate()
        if not coord.isValid():
            return
        accuracy = None
        attribute = QGeoPositionInfo.Attribute.HorizontalAccuracy
        if info.hasAttribute(attribute):
            accuracy = _valid_accuracy(info.attribute(attribute))
        # Ein inzwischen verbundener GPS-Empfänger übernimmt
        self._watch_gps()
        self._on_fix(_Fix(QgsPointXY(coord.longitude(), coord.latitude()), accuracy, SOURCE_SYSTEM))

    def _on_system_error(self, error):
        if error == getattr(QGeoPositionInfoSource.Error, "NoError", None):
            return
        logger.info("Standortdienst meldet Fehler: %s", error)
        self._system_error = error
        # Ein verbundener GPS-Empfänger kann noch liefern – dann entscheidet das Zeitlimit
        if self._zoom_pending and not self._gps_alive():
            self._give_up()

    # ------------------------------------------------------------------
    # GPS-Empfänger aus QGIS
    # ------------------------------------------------------------------

    def _watch_gps(self):
        try:
            connections = QgsApplication.gpsConnectionRegistry().connectionList()
        except (AttributeError, RuntimeError):
            return
        connection = connections[0] if connections else None
        if connection is self._gps:
            return
        self._unwatch_gps()
        if connection is not None:
            connection.stateChanged.connect(self._on_gps_state)
            self._gps = connection

    def _unwatch_gps(self):
        if self._gps is not None:
            try:
                self._gps.stateChanged.disconnect(self._on_gps_state)
            except (TypeError, RuntimeError):
                pass
        self._gps = None

    def _gps_alive(self) -> bool:
        if self._gps is None:
            return False
        try:
            self._gps.status()
        except RuntimeError:  # Verbindung in QGIS getrennt
            self._gps = None
            return False
        return True

    def _gps_fix(self, info=None) -> _Fix | None:
        if not self._gps_alive():
            return None
        if info is None:
            info = self._gps.currentGPSInformation()
        if not info.isValid():
            return None
        return _Fix(QgsPointXY(info.longitude, info.latitude), _valid_accuracy(info.hacc), SOURCE_GPS)

    def _on_gps_state(self, info):
        fix = self._gps_fix(info)
        # Ohne Anzeige und ohne Anfrage nicht von selbst auftauchen
        if fix is not None and (self._marker is not None or self._zoom_pending):
            self._stop_tracking()
            self._on_fix(fix)

    # ------------------------------------------------------------------
    # Meldungen
    # ------------------------------------------------------------------

    def _push_found(self, fix: _Fix):
        position = _format_position(fix.lonlat)
        parts = [position]
        if fix.accuracy_m:
            parts.append(f"Genauigkeit ± {_format_distance(fix.accuracy_m)}")
        parts.append(fix.source)
        text = " · ".join(parts)
        if fix.source == SOURCE_SYSTEM and fix.accuracy_m and fix.accuracy_m > _COARSE_ACCURACY_M:
            text += " – ungenau (WLAN-/IP-Ortung); genauer mit GPS-Empfänger in QGIS"
        button = QPushButton("Kopieren")
        button.setToolTip(f"„{position}“ in die Zwischenablage kopieren")
        button.clicked.connect(lambda: QApplication.clipboard().setText(position))
        self._push(text, Qgis.MessageLevel.Success, 10, button)

    def _push(self, text: str, level, duration: int = 0, button: QPushButton | None = None):
        self._clear_message()
        bar = self._iface.messageBar()
        item = bar.createMessage(_TITLE, text)
        if button is not None:
            item.layout().addWidget(button)
        self._message = bar.pushWidget(item, level, duration)

    def _clear_message(self):
        if self._message is None:
            return
        try:
            self._iface.messageBar().popWidget(self._message)
        except RuntimeError:  # bereits geschlossen
            pass
        self._message = None
