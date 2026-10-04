"""Erzeugt die Druckvorlagen der THW Toolbox (`templates/Toolbox_*.qpt`).

Aufruf mit dem Python der QGIS-Installation aus dem Plugin-Ordner:

    python-qgis.bat scripts/build_print_templates.py

Auf dem Blatt steht nur, was zum Lesen der Karte nötig ist:

- Titelleiste: Bundeslogo, Kartentitel mit Einsatz/Ort, rechts das taktische Zeichen
  des Trupps UL (abschaltbar)
- Karte: Gitter mit Randbeschriftung, Nordpfeil oben rechts, Maßstab unten links
  in der Karte wie in den Vorlagen der THW-Leitung, beide auf durchscheinendem Grund
- Seitenleiste: Zeichenerklärung, Übersicht, Stand, Gitter, Herausgeber, Quellen

Schwarz auf Weiß ohne Flächenfüllungen, Schrift BundesSans. Jede Vorlage enthält
zwei Gitter, von denen das Plugin beim Laden eines einschaltet: `UTMREF`
(Meldegitter in der UTM-Zone des Ausschnitts) und `LONLAT` (geografisch,
Dezimalgrad). Die Bildpfade werden wie in den Vorlagen der THW-Leitung als
`./GRAFIKEN/LAYOUT/...` gespeichert; das Plugin biegt sie beim Laden auf
`templates/assets/` um.
"""

import os
import sys

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsFillSymbol,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemMapGrid,
    QgsLayoutItemMapOverview,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsLayoutMeasurement,
    QgsLayoutObject,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLegendStyle,
    QgsLineSymbol,
    QgsPrintLayout,
    QgsProject,
    QgsProperty,
    QgsReadWriteContext,
    QgsRectangle,
    QgsScaleBarSettings,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsUnitTypes,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(PLUGIN_DIR, "templates")
ASSETS_DIR = os.path.join(TEMPLATES_DIR, "assets")
ASSET_PREFIX = "./GRAFIKEN/LAYOUT/"
BUNDESLOGO = "logo.svg"  # Bundesadler mit "Bundesanstalt Technisches Hilfswerk"
UNIT_SIGN = "Taktisches_Zeichen-UnbemannteLuftfahrsysteme.svg"  # Trupp UL, Zeichenfläche 264 × 199
UNIT_SIGN_ASPECT = 264 / 199

# (Name, Breite, Höhe, Skalierung von Rand und Schrift)
FORMATS = (
    ("Toolbox_A4_QUER", 297, 210, 1.0),
    ("Toolbox_A3_QUER", 420, 297, 1.25),
    # Für den A4-Drucker: passt maßstabstreu auf zwei A4-Blätter hoch (siehe src/layout/tile_export.py)
    ("Toolbox_A3_QUER_2xA4", 400, 297, 1.25),
)

FONT_FAMILIES = ["BundesSans", "BundesSans Office", "Segoe UI", "Noto Sans", "Arial"]

INK = QColor(20, 20, 20)
MUTED = QColor(95, 95, 95)
WHITE = QColor(255, 255, 255)
# Grund unter Maßstab und Nordpfeil: die Karte scheint durch, die Schrift bleibt lesbar
BACKDROP = QColor(255, 255, 255, 150)
RED = QColor(200, 16, 46)  # nur für den Kartenausschnitt in der Übersicht

MM = QgsUnitTypes.LayoutUnit.LayoutMillimeters
DD = QgsLayoutObject.DataDefinedProperty

# Sichtbarer Teil von logo.svg in dessen Einheiten (Zeichenfläche 180 × 104, ringsum Schutzraum):
# Adler und Schrift stehen oben, der Bundesfarben-Stab reicht darunter weiter bis LOGO_BOTTOM
LOGO_LEFT, LOGO_TOP, LOGO_RIGHT, LOGO_BOTTOM = 23.2, 21.0, 156.6, 81.8

MAP = "item_variables('Hauptkarte')"
SCALE = f"map_get({MAP}, 'map_scale')"
# Vom Plugin beim Laden gesetzt; ohne Plugin gilt das eingeschaltete UTMREF-Gitter
IS_LONLAT = "coalesce(@thw_gitter, 'UTMREF') = 'LONLAT'"

# Maschenweite des UTMREF-Gitters in Metern: eine Masche misst auf dem Papier 13 bis 133 mm
UTM_INTERVAL = "if({s} < 750, 10, if({s} < 7500, 100, if({s} < 75000, 1000, 10000)))"
UTM_INTERVAL_MAP = UTM_INTERVAL.format(s="@map_scale")
UTM_INTERVAL_LAYOUT = UTM_INTERVAL.format(s=SCALE)
# Randbeschriftung: Stellen innerhalb des 100-km-Quadrats, je nach Maschenweite 1 bis 4 Ziffern
UTM_ANNOTATION = (
    f"with_variable('i', {UTM_INTERVAL_MAP}, "
    "lpad(to_string(round(@grid_number / @i) % round(100000 / @i)), "
    "if(@i = 10, 4, if(@i = 100, 3, if(@i = 1000, 2, 1))), '0'))"
)

# Linienabstand des geografischen Gitters in Dezimalgrad: in der Breite 44 bis 56 mm auf dem Papier
LONLAT_INTERVAL = (
    "if({s} < 750, 0.0002, if({s} < 1750, 0.0005, if({s} < 3750, 0.001, if({s} < 7500, 0.002, "
    "if({s} < 17500, 0.005, if({s} < 37500, 0.01, if({s} < 75000, 0.02, if({s} < 175000, 0.05, "
    "if({s} < 375000, 0.1, if({s} < 750000, 0.2, 0.5))))))))))"
)
LONLAT_INTERVAL_MAP = LONLAT_INTERVAL.format(s="@map_scale")
LONLAT_INTERVAL_LAYOUT = LONLAT_INTERVAL.format(s=SCALE)
# Randbeschriftung "52.515° N": Nachkommastellen passend zum Linienabstand, Dezimalpunkt wie auf GPS-Geräten
LONLAT_ANNOTATION = (
    f"with_variable('i', {LONLAT_INTERVAL_MAP}, "
    "format_number(abs(@grid_number), if(@i < 0.001, 4, if(@i < 0.01, 3, if(@i < 0.1, 2, 1))), 'en') || '° ' || "
    "if(@grid_axis = 'y', if(@grid_number < 0, 'S', 'N'), if(@grid_number < 0, 'W', 'E')))"
)

# Kartenmitte geographisch (@g), UTM-Zone (@z) und Lage in dieser Zone (@c)
_WITH_CENTER = (
    f"with_variable('g', transform(centroid(map_get({MAP}, 'map_extent')), map_get({MAP}, 'map_crs'), 'EPSG:4326'), "
    "with_variable('z', floor((x(@g) + 180) / 6) + 1, "
    "with_variable('c', transform(@g, 'EPSG:4326', 'EPSG:' || to_string(if(y(@g) < 0, 32700, 32600) + @z)), {body})))"
)
# Zone, Breitenband und 100-km-Quadrat, z. B. "32U MC"
_UTMREF_SQUARE = (
    "to_string(@z) || substr('CDEFGHJKLMNPQRSTUVWX', min(floor((y(@g) + 80) / 8) + 1, 20), 1) || ' ' || "
    "substr(array_get(array('STUVWXYZ', 'ABCDEFGH', 'JKLMNPQR'), @z % 3), floor(x(@c) / 100000), 1) || "
    "substr('ABCDEFGHJKLMNPQRSTUV', (floor(y(@c) / 100000) + if(@z % 2 = 0, 5, 0)) % 20 + 1, 1)"
)
# Koordinate der Kartenmitte in der Genauigkeit der Randbeschriftung, z. B. "32U MC 945 935"
_UTMREF_CENTER = (
    f"with_variable('d', if({UTM_INTERVAL_LAYOUT} = 10, 4, 3), {_UTMREF_SQUARE} || ' ' || "
    "lpad(to_string(floor((x(@c) % 100000) / 10 ^ (5 - @d))), @d, '0') || ' ' || "
    "lpad(to_string(floor((y(@c) % 100000) / 10 ^ (5 - @d))), @d, '0'))"
)
_UTM_INTERVAL_TEXT = (
    f"with_variable('i', {UTM_INTERVAL_LAYOUT}, if(@i >= 1000, to_string(@i / 1000) || ' km', to_string(@i) || ' m'))"
)
_LONLAT_CENTER = (
    "format_number(abs(y(@g)), 5, 'en') || '° ' || if(y(@g) < 0, 'S', 'N') || ', ' || "
    "format_number(abs(x(@g)), 5, 'en') || '° ' || if(x(@g) < 0, 'W', 'E')"
)
# Seitenleiste: welches Gitter die Karte trägt und wie eine Koordinate daraus aussieht
GRID_TEXT = _WITH_CENTER.format(
    body=(
        f"if({IS_LONLAT}, "
        f"'Geografisch (WGS 84) · Linien alle ' || to_string({LONLAT_INTERVAL_LAYOUT}) || '°\\n"
        f"Kartenmitte ' || {_LONLAT_CENTER}, "
        f"'UTMREF ' || {_UTMREF_SQUARE} || ' · Linien alle ' || {_UTM_INTERVAL_TEXT} || '\\n"
        f"Kartenmitte ' || {_UTMREF_CENTER})"
    )
)

SCALE_TEXT = f"'1 : ' || regexp_replace(to_string(round({SCALE})), '(\\\\d)(?=(\\\\d{{3}})+$)', '\\\\1 ')"
TITLE = "coalesce(nullif(@thw_kartentitel, ''), @thw_einsatz)"
SUBTITLE = "array_to_string(array_filter(array(@thw_einsatz, @thw_einsatzort), coalesce(@element, '') <> ''), ' · ')"
PUBLISHER = (
    "array_to_string(array_filter(array('THW Ortsverband ' || @thw_ov, @thw_einheit_kurz), "
    "coalesce(@element, '') <> ''), ' · ') || "
    "if(coalesce(@thw_bearbeiter, '') = '', '', '\\nBearbeitung: ' || @thw_bearbeiter)"
)
HAS_SHEET = "coalesce(@thw_blatt, '') <> ''"


def text_format(size: float, color: QColor = INK, bold: bool = False, halo: bool = False) -> QgsTextFormat:
    """Schrift der Vorlagen; `halo` legt einen weißen Saum um Text, der in der Karte steht."""
    font = QFont(FONT_FAMILIES[0])
    font.setBold(bold)
    fmt = QgsTextFormat()
    fmt.setFont(font)
    fmt.setFamilies(FONT_FAMILIES)
    fmt.setNamedStyle("Bold" if bold else "Regular")
    fmt.setSize(size)
    fmt.setSizeUnit(QgsUnitTypes.RenderUnit.RenderPoints)
    fmt.setColor(color)
    if halo:
        buffer = QgsTextBufferSettings()
        buffer.setEnabled(True)
        buffer.setSize(0.5)
        buffer.setColor(WHITE)
        buffer.setOpacity(0.85)
        fmt.setBuffer(buffer)
    return fmt


def rgba(color: QColor) -> str:
    return f"{color.red()},{color.green()},{color.blue()},{color.alpha()}"


class Builder:
    def __init__(self, name: str, width: float, height: float, s: float):
        self.s = s
        self.w = width
        self.h = height
        self.layout = QgsPrintLayout(QgsProject.instance())
        self.layout.initializeDefaults()
        self.layout.setName(name)
        self.layout.pageCollection().page(0).setPageSize(QgsLayoutSize(width, height, MM))

    # --- Bausteine ---

    def _place(self, item, x, y, w, h, item_id=""):
        item.attemptMove(QgsLayoutPoint(x, y, MM))
        item.attemptResize(QgsLayoutSize(w, h, MM))
        if item_id:
            item.setId(item_id)
        self.layout.addLayoutItem(item)
        return item

    def rect(self, x, y, w, h, fill, item_id=""):
        shape = QgsLayoutItemShape(self.layout)
        shape.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        shape.setSymbol(QgsFillSymbol.createSimple({"color": rgba(fill), "outline_style": "no"}))
        return self._place(shape, x, y, w, h, item_id)

    def label(self, text, x, y, w, h, size, color=INK, bold=False, halign="left", valign="top", item_id="", halo=False):
        item = QgsLayoutItemLabel(self.layout)
        item.setText(text)
        item.setTextFormat(text_format(size * self.s, color, bold, halo))
        item.setHAlign(
            {
                "left": Qt.AlignmentFlag.AlignLeft,
                "center": Qt.AlignmentFlag.AlignHCenter,
                "right": Qt.AlignmentFlag.AlignRight,
            }[halign]
        )
        item.setVAlign(
            {
                "top": Qt.AlignmentFlag.AlignTop,
                "middle": Qt.AlignmentFlag.AlignVCenter,
                "bottom": Qt.AlignmentFlag.AlignBottom,
            }[valign]
        )
        item.setMarginX(0)
        item.setMarginY(0)
        return self._place(item, x, y, w, h, item_id)

    def field(self, caption, text, x, bottom, w, lines, item_id, size=6.5, color=INK, overflow=0.0):
        """Angabe mit kleiner Beschriftung darüber, von `bottom` nach oben gesetzt. Gibt die Oberkante zurück.

        Längerer Text als `lines` Zeilen darf um `overflow` über `bottom` hinauslaufen.
        """
        s = self.s
        body_h = lines * size * 0.43 * s
        self.label(text, x, bottom - body_h, w, body_h + overflow, size, color, item_id=item_id)
        top = bottom - body_h - 2.7 * s
        self.label(caption, x, top, w, 2.7 * s, 5.6, MUTED)
        return top

    def picture(self, filename, x, y, w, h, item_id=""):
        item = QgsLayoutItemPicture(self.layout)
        item.setPicturePath(os.path.join(ASSETS_DIR, filename))
        item.setResizeMode(QgsLayoutItemPicture.ResizeMode.Zoom)
        return self._place(item, x, y, w, h, item_id)

    # --- Aufbau ---

    def build(self) -> QgsPrintLayout:
        s = self.s
        m = 6.0  # Seitenrand
        header_h = 16.0 * s
        side_w = 50.0 * s
        # Platz für die Gitterbeschriftung um die Karte: seitlich bis zu vier Ziffern
        anno_x = 6.5 * s
        anno_y = 4.5 * s

        x0, x1 = m, self.w - m
        y0, y1 = m, self.h - m
        side_x = x1 - side_w

        map_x = x0 + anno_x
        map_y = y0 + header_h + anno_y
        map_w = side_x - 1.5 * s - anno_x - map_x
        map_h = y1 - anno_y - map_y

        main_map = self._main_map(map_x, map_y, map_w, map_h)
        self._north_arrow(main_map, map_x + map_w, map_y)
        self._scale(main_map, map_x, map_y + map_h)
        self._header(map_x, y0, side_x, side_w)
        self._side(side_x, map_y, side_w, map_y + map_h, main_map, overflow=anno_y)
        return self.layout

    def _main_map(self, x, y, w, h):
        s = self.s
        item = QgsLayoutItemMap(self.layout)
        self._place(item, x, y, w, h, "Hauptkarte")
        crs = QgsCoordinateReferenceSystem("EPSG:25832")
        item.setCrs(crs)
        scale = 25000
        cx, cy = 494500, 5793500
        item.setExtent(
            QgsRectangle(cx - w * scale / 2000, cy - h * scale / 2000, cx + w * scale / 2000, cy + h * scale / 2000)
        )
        item.setFrameEnabled(True)
        item.setFrameStrokeColor(INK)
        item.setFrameStrokeWidth(QgsLayoutMeasurement(0.35 * s, MM))

        direction = QgsLayoutItemMapGrid.AnnotationDirection
        utmref = self._grid(item, "UTMREF", crs, 1000, UTM_INTERVAL_MAP, UTM_ANNOTATION)
        lonlat = self._grid(
            item,
            "LONLAT",
            QgsCoordinateReferenceSystem("EPSG:4326"),
            0.01,
            LONLAT_INTERVAL_MAP,
            LONLAT_ANNOTATION,
            # Die langen Gradzahlen passen seitlich nur hochkant in den Rand
            left=direction.Vertical,
            right=direction.VerticalDescending,
        )
        lonlat.setEnabled(False)
        item.grids().addGrid(utmref)
        item.grids().addGrid(lonlat)
        return item

    def _grid(self, map_item, name, crs, interval, interval_expr, annotation_expr, left=None, right=None):
        s = self.s
        grid = QgsLayoutItemMapGrid(name, map_item)
        grid.setCrs(crs)
        grid.setIntervalX(interval)
        grid.setIntervalY(interval)
        for prop in (DD.MapGridIntervalX, DD.MapGridIntervalY):
            grid.dataDefinedProperties().setProperty(prop, QgsProperty.fromExpression(interval_expr))
        grid.setStyle(QgsLayoutItemMapGrid.GridStyle.Solid)
        grid.setLineSymbol(
            QgsLineSymbol.createSimple({"color": "0,0,0,105", "width": str(0.1 * s), "capstyle": "flat"})
        )
        grid.setFrameStyle(QgsLayoutItemMapGrid.FrameStyle.ExteriorTicks)
        grid.setFrameWidth(1.0 * s)
        grid.setFramePenSize(0.25 * s)
        grid.setFramePenColor(INK)
        grid.setAnnotationEnabled(True)
        grid.setAnnotationFormat(QgsLayoutItemMapGrid.AnnotationFormat.CustomFormat)
        grid.setAnnotationExpression(annotation_expr)
        grid.setAnnotationTextFormat(text_format(6.5 * s, INK, bold=True))
        grid.setAnnotationFrameDistance(0.8 * s)
        border = QgsLayoutItemMapGrid.BorderSide
        horizontal = QgsLayoutItemMapGrid.AnnotationDirection.Horizontal
        directions = {border.Left: left or horizontal, border.Right: right or horizontal}
        for side in (border.Left, border.Right, border.Top, border.Bottom):
            vertical = side in (border.Left, border.Right)
            grid.setAnnotationPosition(QgsLayoutItemMapGrid.AnnotationPosition.OutsideMapFrame, side)
            grid.setAnnotationDirection(directions.get(side, horizontal), side)
            # Schräg laufende Lon/Lat-Linien treffen auch den Nachbarrand: dort weder Zahl noch Strich
            display = QgsLayoutItemMapGrid.DisplayMode
            only = display.LatitudeOnly if vertical else display.LongitudeOnly
            grid.setAnnotationDisplay(only, side)
            grid.setFrameDivisions(only, side)
        return grid

    def _north_arrow(self, main_map, map_right, map_top):
        """Nordpfeil der THW-Vorlage, oben rechts in der Karte; zeigt nach Geographisch Nord."""
        s = self.s
        w, h = 5.5 * s, 7.2 * s
        arrow = self.picture("Nordpfeil.svg", map_right - w - 2 * s, map_top + 2 * s, w, h, "Nordpfeil")
        arrow.setBackgroundEnabled(True)
        arrow.setBackgroundColor(BACKDROP)
        arrow.setLinkedMap(main_map)
        arrow.setNorthMode(QgsLayoutItemPicture.NorthMode.TrueNorth)

    def _scale(self, main_map, map_left, map_bottom):
        """Maßstab unten links in der Karte wie in den Vorlagen der THW-Leitung, auf durchscheinendem Grund."""
        s = self.s
        inset = 0.2 * s  # der Kartenrahmen bleibt frei
        # Vier Abschnitte von höchstens 10 mm ergeben bei den gängigen Maßstäben runde Werte,
        # deshalb wächst der Balken nicht mit dem Papierformat
        bar_min, bar_max = 20.0, 40.5
        w, h = 34 * s + bar_max, 8.4 * s
        x, y = map_left + inset, map_bottom - inset - h
        self.rect(x, y, w, h, BACKDROP, item_id="Maßstab - Feld")
        self.label(
            f"[% {SCALE_TEXT} %]",
            x + 2.5 * s,
            y,
            22 * s,
            h,
            9.5,
            bold=True,
            valign="middle",
            item_id="Maßstab große Karte - Text",
            halo=True,
        )
        # Bis 1 : 40 000 in Metern, darüber in Kilometern; der jeweils andere Balken ist durchsichtig
        units = QgsUnitTypes.DistanceUnit
        for item_id, unit, unit_label, visible in (
            ("Maßstabsanzeiger", units.DistanceMeters, "m", f"{SCALE} < 40000"),
            ("Maßstabsanzeiger (km)", units.DistanceKilometers, "km", f"{SCALE} >= 40000"),
        ):
            bar = QgsLayoutItemScaleBar(self.layout)
            bar.setStyle("Single Box")
            bar.setLinkedMap(main_map)
            bar.setUnits(unit)
            bar.setUnitLabel(unit_label)
            bar.setNumberOfSegments(4)
            bar.setNumberOfSegmentsLeft(0)
            bar.setSegmentSizeMode(QgsScaleBarSettings.SegmentSizeMode.SegmentSizeFitWidth)
            bar.setMinimumBarWidth(bar_min)
            bar.setMaximumBarWidth(bar_max)
            bar.setHeight(1.2 * s)
            bar.setBoxContentSpace(0)
            bar.setLabelBarSpace(0.8 * s)
            bar.setTextFormat(text_format(5.6 * s, INK, halo=True))
            bar.setFillSymbol(QgsFillSymbol.createSimple({"color": rgba(INK), "outline_style": "no"}))
            bar.setAlternateFillSymbol(QgsFillSymbol.createSimple({"color": rgba(WHITE), "outline_style": "no"}))
            bar.setLineSymbol(QgsLineSymbol.createSimple({"color": rgba(INK), "width": str(0.15 * s)}))
            bar.setBackgroundEnabled(False)
            bar.update()
            self._place(bar, x + 26 * s, y + 2.0 * s, w - 28 * s, 5 * s, item_id)
            bar.dataDefinedProperties().setProperty(DD.Opacity, QgsProperty.fromExpression(f"if({visible}, 100, 0)"))

    def _header(self, map_x, y, side_x, side_w):
        s = self.s
        # Bundeslogo bündig mit dem Kartenrahmen; der Schutzraum der Grafik ragt in den Seitenrand
        unit = 0.22 * s
        logo_y = y + 0.4 * s
        self.picture(
            BUNDESLOGO,
            map_x - LOGO_LEFT * unit,
            logo_y - LOGO_TOP * unit,
            180 * unit,
            104 * unit,
            "Logo Bundesadler",
        )

        # Titel im Abstand einer Adlerbreite neben dem Logo, Oberkante auf Höhe der Logoschrift
        title_x = map_x + (LOGO_RIGHT - LOGO_LEFT) * unit + 8 * s
        title_w = side_x - 4 * s - title_x
        self.label(f"[% {TITLE} %]", title_x, y - 0.2 * s, title_w, 7.5 * s, 16, bold=True, item_id="Titel")
        self.label(f"[% {SUBTITLE} %]", title_x, y + 7.8 * s, title_w, 4.5 * s, 8.5, item_id="Untertitel")

        # Rechts oben, als Gegenstück zum Logo: taktisches Zeichen der Einheit, im Dialog abschaltbar
        sign_h = 12.5 * s
        sign_w = sign_h * UNIT_SIGN_ASPECT
        self.picture(UNIT_SIGN, side_x + side_w - sign_w, logo_y, sign_w, sign_h, "Taktisches Zeichen Einheit")

    def _side(self, x, top, w, bottom, main_map, overflow):
        s = self.s
        gap = 2.4 * s

        # Von unten: Quellen, Herausgeber, Gitter, Stand, darüber die Übersicht. Die Quellen enden
        # bei drei Zeilen bündig mit der Karte, längere laufen in den Rand darunter
        y = self.field(
            "Quellen",
            "[% array_to_string(map_credits('Hauptkarte'), ' · ') %]",
            x,
            bottom,
            w,
            lines=3,
            item_id="Quellen",
            size=5.2,
            color=MUTED,
            overflow=overflow,
        )
        y = self.field("Herausgeber", f"[% {PUBLISHER} %]", x, y - gap, w, lines=2, item_id="Herausgeber")
        y = self.field("Gitter", f"[% {GRID_TEXT} %]", x, y - gap, w, lines=2, item_id="Gitter")
        stand_bottom = y - gap
        y = self.field(
            "Stand",
            "[% format_date(now(), 'dd.MM.yyyy · HH:mm') %] Uhr",
            x,
            stand_bottom,
            w * 0.62,
            lines=1,
            item_id="Stand",
        )
        # Blattnummer nur, wenn im Dialog eine angegeben wurde
        self.field(
            f"[% if({HAS_SHEET}, 'Blatt', '') %]",
            f"[% if({HAS_SHEET}, @thw_blatt, '') %]",
            x + w * 0.66,
            stand_bottom,
            w * 0.34,
            lines=1,
            item_id="Blattnummer",
        )

        ov_h = 30.0 * s
        ov_y = y - gap - 0.6 * s - ov_h
        self._overview(main_map, x, ov_y, w, ov_h, 40, "Übersicht")

        # Von oben: Zeichenerklärung, wächst mit ihrem Inhalt nach unten
        self.label("Zeichenerklärung", x, top - 0.9 * s, w, 4 * s, 8, bold=True, item_id="Titel Legende")
        legend = QgsLayoutItemLegend(self.layout)
        legend.setLinkedMap(main_map)
        legend.setLegendFilterByMapEnabled(True)
        legend.setTitle("")
        legend.setColumnCount(1)
        legend.setBoxSpace(0)
        legend.setSymbolWidth(6.5 * s)
        legend.setSymbolHeight(3.6 * s)
        legend.setMaximumSymbolSize(5.8 * s)
        legend.setAutoWrapLinesAfter(w - 10 * s)
        legend.setBackgroundEnabled(False)
        legend.setFrameEnabled(False)
        side = QgsLegendStyle.Side
        for style, size, bold, margin_top in (
            (QgsLegendStyle.Style.Title, 7, True, 0),
            (QgsLegendStyle.Style.Group, 6.5, True, 2.2),
            (QgsLegendStyle.Style.Subgroup, 6.5, True, 2.2),
            (QgsLegendStyle.Style.Symbol, 6.5, False, 1.3),
            (QgsLegendStyle.Style.SymbolLabel, 6.5, False, 0),
        ):
            legend.rstyle(style).setTextFormat(text_format(size * s, INK, bold))
            legend.rstyle(style).setMargin(side.Top, margin_top * s)
        legend.rstyle(QgsLegendStyle.Style.SymbolLabel).setMargin(side.Left, 2 * s)
        self._place(legend, x, top + 2.0 * s, w, 20 * s, "Legende")

    def _overview(self, main_map, x, y, w, h, factor, item_id):
        """Übersichtskarte im `factor`-fachen Maßstab der Hauptkarte, zentriert auf deren Mitte."""
        s = self.s
        overview = QgsLayoutItemMap(self.layout)
        self._place(overview, x, y, w, h, item_id)
        overview.setCrs(main_map.crs())
        # Gültige Startausdehnung, sonst rechnen die datendefinierten Grenzen mit NaN weiter
        # (setExtent passt die Elementhöhe an, deshalb im Seitenverhältnis des Elements)
        mid = main_map.extent().center()
        dx, dy = w * factor * main_map.scale() / 2000, h * factor * main_map.scale() / 2000
        overview.setExtent(QgsRectangle(mid.x() - dx, mid.y() - dy, mid.x() + dx, mid.y() + dy))
        center = f"centroid(map_get({MAP}, 'map_extent'))"
        half_w = f"{w * factor / 2000} * {SCALE}"
        half_h = f"{h * factor / 2000} * {SCALE}"
        for prop, expr in (
            (DD.MapXMin, f"x({center}) - {half_w}"),
            (DD.MapXMax, f"x({center}) + {half_w}"),
            (DD.MapYMin, f"y({center}) - {half_h}"),
            (DD.MapYMax, f"y({center}) + {half_h}"),
        ):
            overview.dataDefinedProperties().setProperty(prop, QgsProperty.fromExpression(expr))
        overview.setFrameEnabled(True)
        overview.setFrameStrokeColor(INK)
        overview.setFrameStrokeWidth(QgsLayoutMeasurement(0.2 * s, MM))

        extent_box = QgsLayoutItemMapOverview("Kartenausschnitt", overview)
        extent_box.setLinkedMap(main_map)
        extent_box.setFrameSymbol(
            QgsFillSymbol.createSimple({"style": "no", "outline_color": rgba(RED), "outline_width": str(0.5 * s)})
        )
        overview.overviews().addOverview(extent_box)


def save_template(layout: QgsPrintLayout, path: str) -> None:
    if not layout.saveAsTemplate(path, QgsReadWriteContext()):
        raise RuntimeError(f"Vorlage konnte nicht gespeichert werden: {path}")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # Bildpfade wie in den Vorlagen der THW-Leitung relativ ablegen
    for prefix in {ASSETS_DIR + os.sep, ASSETS_DIR.replace("\\", "/") + "/"}:
        content = content.replace(prefix, ASSET_PREFIX)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def main() -> int:
    app = QgsApplication([], False)
    app.initQgis()
    try:
        for name, width, height, scale in FORMATS:
            path = os.path.join(TEMPLATES_DIR, name + ".qpt")
            save_template(Builder(name, width, height, scale).build(), path)
            print("geschrieben:", path)
    finally:
        app.exitQgis()
    return 0


if __name__ == "__main__":
    sys.exit(main())
