"""Erzeugt die modernen THW-Druckvorlagen (`templates/Toolbox_Modern_*.qpt`).

Aufruf mit dem Python der QGIS-Installation aus dem Plugin-Ordner:

    python-qgis.bat scripts/build_modern_print_templates.py

Der Kartenrand folgt der THW-Ausbildungsunterlage "Kartenkunde":

- oben (von links): Bundeslogo, Maßstab, Kartentitel mit Einsatz/Ort, Blattnummer
- seitlich: Titel des Kartenblattes, Zeichenerklärung, Hinweise, Übersichten, Planzeiger
- unten (von links): graphischer Maßstab, Abweichung Nord zu Gitternord,
  Herausgeber mit Fortführungsstand, Grenzen/Quellen

Druckfreundlich ohne Flächenfüllungen, Blau nur für Titel, Linien und Gitter;
Schrift BundesSans, UTMREF-Gitter nach der Vorlage "OV Minden A4 UTMREF". Die Bildpfade werden wie in den THW-Vorlagen
als `./GRAFIKEN/LAYOUT/...` gespeichert; das Plugin biegt sie beim
Laden auf `templates/assets/` um.
"""

import os
import sys

from qgis.core import (
    QgsApplication,
    QgsCentroidFillSymbolLayer,
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
    QgsMarkerSymbol,
    QgsPrintLayout,
    QgsProject,
    QgsProperty,
    QgsReadWriteContext,
    QgsRectangle,
    QgsScaleBarSettings,
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

# (Name, Breite, Höhe, Skalierung von Rand und Schrift)
FORMATS = (
    ("Toolbox_Modern_A4_QUER", 297, 210, 1.0),
    ("Toolbox_Modern_A3_QUER", 420, 297, 1.25),
    # Für den A4-Drucker: passt maßstabstreu auf zwei A4-Blätter hoch (siehe src/layout/tile_export.py)
    ("Toolbox_Modern_A3_QUER_2xA4", 400, 297, 1.25),
)

FONT_FAMILIES = ["BundesSans", "BundesSans Office", "Segoe UI", "Noto Sans", "Arial"]

THW_BLUE = QColor(0, 51, 153)
TEXT = QColor(26, 26, 26)
MUTED = QColor(90, 98, 112)
WHITE = QColor(255, 255, 255)
RULE = "0,51,153,255"
VS_RED = QColor(200, 16, 46)

MM = QgsUnitTypes.LayoutUnit.LayoutMillimeters
DD = QgsLayoutObject.DataDefinedProperty

MAP = "item_variables('Hauptkarte')"
SCALE = f"map_get({MAP}, 'map_scale')"
# Kartenmitte geographisch (@g) und UTM-Zone (@z) für alle abgeleiteten Angaben
_WITH_CENTER = (
    f"with_variable('g', transform(centroid(map_get({MAP}, 'map_extent')), map_get({MAP}, 'map_crs'), 'EPSG:4326'), "
    "with_variable('z', floor((x(@g) + 180) / 6) + 1, {body}))"
)
UTMREF_CENTER = _WITH_CENTER.format(
    body=(
        "with_variable('c', transform(@g, 'EPSG:4326', 'EPSG:' || to_string(32600 + @z)), "
        "to_string(@z) || substr('CDEFGHJKLMNPQRSTUVWX', floor((y(@g) + 80) / 8) + 1, 1) || ' ' || "
        "substr(array_get(array('STUVWXYZ', 'ABCDEFGH', 'JKLMNPQR'), @z % 3), floor(x(@c) / 100000), 1) || "
        "substr('ABCDEFGHJKLMNPQRSTUV', (floor(y(@c) / 100000) + if(@z % 2 = 0, 5, 0)) % 20 + 1, 1) || ' ' || "
        "lpad(to_string(floor((x(@c) % 100000) / 100)), 3, '0') || ' ' || "
        "lpad(to_string(floor((y(@c) % 100000) / 100)), 3, '0'))"
    )
)
# Meridiankonvergenz: Winkel von Geographisch Nord zu Gitternord, östlich positiv
CONVERGENCE = _WITH_CENTER.format(
    body=(
        "with_variable('k', degrees(atan(tan(radians(x(@g) - (@z * 6 - 183))) * sin(radians(y(@g))))), "
        "if(@k >= 0, '+', '−') || replace(format_number(abs(@k), 2), '.', ',') || '°')"
    )
)
# Maschenweite des UTMREF-Gitters in Metern, abhängig vom Maßstab
GRID_INTERVAL = "if({s} < 12500, 100, if({s} < 125000, 1000, 10000))"
GRID_INTERVAL_MAP = GRID_INTERVAL.format(s="@map_scale")
GRID_INTERVAL_LAYOUT = GRID_INTERVAL.format(s=SCALE)
GRID_ANNOTATION = (
    f"with_variable('i', {GRID_INTERVAL_MAP}, "
    "lpad(to_string(floor(@grid_number / @i) % (100000 / @i)), if(@i = 100, 3, if(@i = 1000, 2, 1)), '0'))"
)
GRID_INTERVAL_TEXT = f"if({GRID_INTERVAL_LAYOUT} >= 1000, to_string({GRID_INTERVAL_LAYOUT} / 1000) || ' km', '100 m')"
SCALE_TEXT = f"'1 : ' || regexp_replace(to_string(round({SCALE})), '(\\\\d)(?=(\\\\d{{3}})+$)', '\\\\1 ')"
DTG = "format_date(now(), 'ddHHmm') || upper(format_date(now(), 'MMMyy'))"
# Kantenlänge einer Gittermasche auf dem Papier in mm
CELL_MM = f"({GRID_INTERVAL_LAYOUT} * 1000.0 / {SCALE})"


def text_format(size: float, color: QColor = TEXT, bold: bool = False) -> QgsTextFormat:
    font = QFont(FONT_FAMILIES[0])
    font.setBold(bold)
    fmt = QgsTextFormat()
    fmt.setFont(font)
    fmt.setFamilies(FONT_FAMILIES)
    fmt.setNamedStyle("Bold" if bold else "Regular")
    fmt.setSize(size)
    fmt.setSizeUnit(QgsUnitTypes.RenderUnit.RenderPoints)
    fmt.setColor(color)
    return fmt


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

    def rect(self, x, y, w, h, fill, outline=None, item_id=""):
        shape = QgsLayoutItemShape(self.layout)
        shape.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        props = {"color": fill, "outline_style": "no"}
        if outline:
            props.update({"outline_style": "solid", "outline_color": outline, "outline_width": "0.2"})
        shape.setSymbol(QgsFillSymbol.createSimple(props))
        return self._place(shape, x, y, w, h, item_id)

    def label(self, text, x, y, w, h, size, color=TEXT, bold=False, halign="left", valign="top", item_id=""):
        item = QgsLayoutItemLabel(self.layout)
        item.setText(text)
        item.setTextFormat(text_format(size * self.s, color, bold))
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

    def caption(self, text, x, y, w):
        return self.label(text.upper(), x, y, w, 3 * self.s, 5.2, THW_BLUE, bold=True)

    def picture(self, filename, x, y, w, h, item_id=""):
        item = QgsLayoutItemPicture(self.layout)
        item.setPicturePath(os.path.join(ASSETS_DIR, filename))
        item.setResizeMode(QgsLayoutItemPicture.ResizeMode.Zoom)
        return self._place(item, x, y, w, h, item_id)

    # --- Aufbau ---

    def build(self) -> QgsPrintLayout:
        s = self.s
        m = 6.0  # Seitenrand
        gap = 2.0 * s
        header_h = 15.0 * s
        rule_h = 0.6 * s
        footer_h = 15.0 * s
        side_w = 48.0 * s
        anno = 4.5 * s  # Platz für die Gitterbeschriftung um die Karte

        x0, x1 = m, self.w - m
        y0, y1 = m, self.h - m
        side_x = x1 - side_w
        body_y = y0 + header_h + rule_h + gap
        footer_y = y1 - footer_h
        footer_w = side_x - gap - x0

        map_x = x0 + anno
        map_y = body_y + anno - gap / 2
        map_w = side_x - gap - anno - map_x
        map_h = footer_y - gap / 2 - anno - map_y

        main_map = self._main_map(map_x, map_y, map_w, map_h)
        self._north_arrow(main_map, map_x + map_w, map_y)
        self._header(x0, y0, x1 - x0, header_h, side_x)
        self.rect(x0, y0 + header_h, x1 - x0, rule_h, RULE, item_id="Kopflinie")
        self._side(side_x, body_y, side_w, y1 - body_y, main_map)
        self._footer(x0, footer_y, footer_w, footer_h, main_map)
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
        item.setFrameStrokeColor(THW_BLUE)
        item.setFrameStrokeWidth(QgsLayoutMeasurement(0.35 * s, MM))

        grid = QgsLayoutItemMapGrid("UTMREF", item)
        grid.setCrs(crs)
        grid.setIntervalX(1000)
        grid.setIntervalY(1000)
        for prop in (DD.MapGridIntervalX, DD.MapGridIntervalY):
            grid.dataDefinedProperties().setProperty(prop, QgsProperty.fromExpression(GRID_INTERVAL_MAP))
        grid.setStyle(QgsLayoutItemMapGrid.GridStyle.Solid)
        grid.setLineSymbol(
            QgsLineSymbol.createSimple({"color": "0,51,153,120", "width": str(0.12 * s), "capstyle": "flat"})
        )
        grid.setFrameStyle(QgsLayoutItemMapGrid.FrameStyle.ExteriorTicks)
        grid.setFrameWidth(1.2 * s)
        grid.setFramePenSize(0.25 * s)
        grid.setFramePenColor(THW_BLUE)
        grid.setAnnotationEnabled(True)
        grid.setAnnotationFormat(QgsLayoutItemMapGrid.AnnotationFormat.CustomFormat)
        grid.setAnnotationExpression(GRID_ANNOTATION)
        grid.setAnnotationTextFormat(text_format(6.5 * s, THW_BLUE, bold=True))
        grid.setAnnotationFrameDistance(1.6 * s)
        border = QgsLayoutItemMapGrid.BorderSide
        for side in (border.Left, border.Right, border.Top, border.Bottom):
            vertical = side in (border.Left, border.Right)
            grid.setAnnotationPosition(QgsLayoutItemMapGrid.AnnotationPosition.OutsideMapFrame, side)
            grid.setAnnotationDirection(QgsLayoutItemMapGrid.AnnotationDirection.Horizontal, side)
            grid.setAnnotationDisplay(
                QgsLayoutItemMapGrid.DisplayMode.LatitudeOnly
                if vertical
                else QgsLayoutItemMapGrid.DisplayMode.LongitudeOnly,
                side,
            )
        item.grids().addGrid(grid)
        return item

    def _north_arrow(self, main_map, map_right, map_top):
        """Nordpfeil der THW-Vorlage, oben rechts in der Karte; zeigt nach Geographisch Nord."""
        s = self.s
        w, h = 5.5 * s, 7.2 * s
        arrow = self.picture("Nordpfeil.svg", map_right - w - 2 * s, map_top + 2 * s, w, h, "Nordpfeil")
        arrow.setBackgroundEnabled(True)
        arrow.setBackgroundColor(QColor(255, 255, 255, 215))
        arrow.setLinkedMap(main_map)
        arrow.setNorthMode(QgsLayoutItemPicture.NorthMode.TrueNorth)

    def _header(self, x, y, w, h, side_x):
        s = self.s
        # Bundeslogo: der Schutzraum der Grafik (20 von 180 Einheiten) ragt in den Seitenrand
        unit = 0.23 * s
        self.picture(BUNDESLOGO, x - 20 * unit, y + 0.5 * s - 19 * unit, 180 * unit, 104 * unit, "Logo Bundesadler")

        scale_x = x + 36 * s
        self.caption("Maßstab", scale_x, y + 3 * s, 30 * s)
        self.label(
            f"[% {SCALE_TEXT} %]",
            scale_x,
            y + 6 * s,
            30 * s,
            6 * s,
            12,
            TEXT,
            bold=True,
            item_id="Maßstab große Karte - Text",
        )

        title_x = x + 68 * s
        title_w = side_x - 2 * s - title_x
        self.label(
            "[% coalesce(nullif(@thw_kartentitel, ''), @thw_einsatz) %]",
            title_x,
            y + 1.2 * s,
            title_w,
            7.5 * s,
            16,
            THW_BLUE,
            bold=True,
            item_id="Titel",
        )
        self.label(
            "[% array_to_string(array_filter(array(@thw_einsatz, @thw_einsatzort), "
            "coalesce(@element, '') <> ''), ' · ') %]",
            title_x,
            y + 9.2 * s,
            title_w,
            4.5 * s,
            8.5,
            TEXT,
            item_id="Untertitel",
        )

        # Blattnummer und Einstufung über der Seitenspalte
        self.caption("Blatt", side_x, y + 3 * s, 14 * s)
        self.label(
            "[% coalesce(@thw_blatt, '1') %]",
            side_x,
            y + 6 * s,
            15 * s,
            6 * s,
            12,
            TEXT,
            bold=True,
            item_id="Blattnummer",
        )
        pill_x = side_x + 16 * s
        for item_id, text, color, filled in (
            ("nicht klassifiziert - Text", "nicht klassifiziert", TEXT, False),
            ("VS-NfD", "VS – NfD", WHITE, True),
        ):
            pill = self.label(
                text,
                pill_x,
                y + 4.5 * s,
                x + w - pill_x,
                6.5 * s,
                6.5,
                color,
                bold=True,
                halign="center",
                valign="middle",
                item_id=item_id,
            )
            if filled:
                pill.setBackgroundEnabled(True)
                pill.setBackgroundColor(VS_RED)
                pill.setVisibility(False)
            else:
                pill.setFrameEnabled(True)
                pill.setFrameStrokeColor(TEXT)
                pill.setFrameStrokeWidth(QgsLayoutMeasurement(0.2 * s, MM))

    def _side(self, x, y, w, h, main_map):
        s = self.s
        gap = 2.0 * s
        bottom = y + h

        # Von unten: Planzeiger (eine Gittermasche im Kartenmaßstab, wächst nach rechts unten)
        plan_max = w
        plan_top = bottom - plan_max
        fits = f"{CELL_MM} * 1.12 <= {plan_max}"
        planzeiger = QgsLayoutItemPicture(self.layout)
        planzeiger.setPicturePath(os.path.join(ASSETS_DIR, "Planzeiger.svg"))
        planzeiger.setResizeMode(QgsLayoutItemPicture.ResizeMode.Stretch)
        self._place(planzeiger, x, plan_top, 40 * 1.12, 40 * 1.12, "Planzeiger")
        size_expr = QgsProperty.fromExpression(f"if({fits}, {CELL_MM} * 1.12, 1)")
        planzeiger.dataDefinedProperties().setProperty(DD.ItemWidth, size_expr)
        planzeiger.dataDefinedProperties().setProperty(DD.ItemHeight, size_expr)
        planzeiger.dataDefinedProperties().setProperty(DD.Opacity, QgsProperty.fromExpression(f"if({fits}, 100, 0)"))
        caption_y = plan_top - 4 * s
        self.caption("Planzeiger", x, caption_y, 20 * s)
        self.label(
            f"[% if({fits}, {SCALE_TEXT} || ' · Teilung ' || to_string({GRID_INTERVAL_LAYOUT} / 10) || ' m', "
            f"'bei ' || {SCALE_TEXT} || ' nicht darstellbar') %]",
            x + 20 * s,
            caption_y,
            w - 20 * s,
            3 * s,
            5.4,
            MUTED,
            halign="right",
        )

        # Übersichten wie in der THW-Vorlage: Nahbereich (25×) und Großraum (150×)
        ov_h = 28.0 * s
        near_y = caption_y - gap - ov_h
        far_y = near_y - gap - ov_h
        self._overview(main_map, x, near_y, w, ov_h, 25, "Karte_Übersicht_25xzuHauptkarte", point=False)
        self._overview(main_map, x, far_y, w, ov_h, 150, "Karte_Übersicht_150xzuHauptkarte", point=True)

        # Hinweise der THW-Vorlage
        note_h = 12.5 * s
        note_y = far_y - gap - note_h
        self.rect(x, note_y, w, note_h, "255,255,255,255", outline="200,16,46,255", item_id="Rahmen")
        self.label("CAUTION", x, note_y + 1.1 * s, w, 4 * s, 9, VS_RED, bold=True, halign="center", item_id="CAUTION")
        self.label(
            "NOT TO BE USED FOR NAVIGATION",
            x,
            note_y + 5.3 * s,
            w,
            3 * s,
            5.6,
            TEXT,
            bold=True,
            halign="center",
            item_id="not to be used for navigation or targeting",
        )
        self.label(
            "Diese Version ersetzt alle vorherigen.",
            x,
            note_y + 8.6 * s,
            w,
            3 * s,
            5.6,
            MUTED,
            halign="center",
            item_id="Diese Version ersetzt alle vorherigen.",
        )

        # Von oben: Titel des Kartenblattes und Zeichenerklärung
        self.label(
            "[% coalesce(nullif(@thw_kartentitel, ''), @thw_einsatz) %]",
            x,
            y,
            w,
            4 * s,
            7.5,
            THW_BLUE,
            bold=True,
            item_id="Titel Seitenrand",
        )
        self.caption("Zeichenerklärung", x, y + 4.6 * s, w)
        legend = QgsLayoutItemLegend(self.layout)
        legend.setLinkedMap(main_map)
        legend.setLegendFilterByMapEnabled(True)
        legend.setTitle("")
        legend.setColumnCount(1)
        legend.setBoxSpace(0)
        legend.setSymbolWidth(5 * s)
        legend.setSymbolHeight(3.2 * s)
        legend.setMaximumSymbolSize(4.5 * s)
        legend.setBackgroundEnabled(False)
        legend.setFrameEnabled(False)
        for style, size, bold in (
            (QgsLegendStyle.Style.Title, 7, True),
            (QgsLegendStyle.Style.Group, 6.5, True),
            (QgsLegendStyle.Style.Subgroup, 6.2, True),
            (QgsLegendStyle.Style.SymbolLabel, 6.2, False),
        ):
            legend.rstyle(style).setTextFormat(text_format(size * s, TEXT, bold))
        self._place(legend, x, y + 8.2 * s, w, 20 * s, "Legende")

    def _overview(self, main_map, x, y, w, h, factor, item_id, point):
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
        overview.setFrameStrokeColor(TEXT)
        overview.setFrameStrokeWidth(QgsLayoutMeasurement(0.2 * s, MM))

        extent_box = QgsLayoutItemMapOverview("Kartenausschnitt", overview)
        extent_box.setLinkedMap(main_map)
        if point:
            # Im Großraum wäre der Rahmen kaum sichtbar, deshalb ein Punkt in der Mitte
            marker = QgsMarkerSymbol.createSimple(
                {"name": "square", "color": "200,16,46,255", "outline_style": "no", "size": str(1.4 * s)}
            )
            centroid = QgsCentroidFillSymbolLayer()
            centroid.setSubSymbol(marker)
            symbol = QgsFillSymbol()
            symbol.changeSymbolLayer(0, centroid)
        else:
            symbol = QgsFillSymbol.createSimple(
                {"style": "no", "outline_color": "200,16,46,255", "outline_width": str(0.6 * s)}
            )
        extent_box.setFrameSymbol(symbol)
        overview.overviews().addOverview(extent_box)

    def _footer(self, x, y, w, h, main_map):
        s = self.s
        self.rect(x, y, w, 0.3 * s, RULE, item_id="Fußlinie")

        shares = (62, 50, 60, 54)
        cells = []
        cx = x
        for share in shares:
            width = w * share / sum(shares)
            cells.append((cx, width - 3 * s))
            cx += width
        top = y + 1.6 * s
        body_top = top + 3.6 * s
        body_h = y + h - body_top
        body = 6.0

        # 1 Maßstab (graphisch) mit Koordinatensystem
        cell_x, cell_w = cells[0]
        self.caption("Maßstab", cell_x, top, cell_w)
        bar = QgsLayoutItemScaleBar(self.layout)
        bar.setStyle("Single Box")
        bar.setLinkedMap(main_map)
        bar.setUnits(QgsUnitTypes.DistanceUnit.DistanceMeters)
        bar.setUnitLabel("m")
        bar.setNumberOfSegments(4)
        bar.setNumberOfSegmentsLeft(0)
        bar.setSegmentSizeMode(QgsScaleBarSettings.SegmentSizeMode.SegmentSizeFitWidth)
        bar.setMinimumBarWidth(cell_w * 0.55)
        bar.setMaximumBarWidth(cell_w - 8 * s)
        bar.setHeight(1.2 * s)
        bar.setBoxContentSpace(0)
        bar.setLabelBarSpace(0.8 * s)
        bar.setTextFormat(text_format(5.6 * s, TEXT))
        bar.setFillColor(THW_BLUE)
        bar.setFillColor2(WHITE)
        bar.setLineColor(THW_BLUE)
        bar.setLineWidth(0.15 * s)
        bar.setBackgroundEnabled(False)
        bar.update()
        self._place(bar, cell_x, body_top, cell_w, 6 * s, "Maßstabsanzeiger")
        self.label(
            f"[% map_get({MAP}, 'map_crs_description') %] · Gitter [% {GRID_INTERVAL_TEXT} %]",
            cell_x,
            body_top + 6.6 * s,
            cell_w,
            3 * s,
            body,
            item_id="Metadaten",
        )

        # 2 Lage und Abweichung Nord zu Gitternord
        cell_x, cell_w = cells[1]
        self.caption("UTMREF · Nord", cell_x, top, cell_w)
        self.label(
            f"Kartenmitte: [% {UTMREF_CENTER} %]\nNordpfeil: Geographisch Nord\n"
            f"Gitternord weicht ab um [% {CONVERGENCE} %]",
            cell_x,
            body_top,
            cell_w,
            body_h,
            body,
            item_id="Meridiankonvergenz",
        )

        # 3 Herausgeber und Fortführungsstand
        cell_x, cell_w = cells[2]
        self.caption("Herausgeber · Stand", cell_x, top, cell_w)
        self.label(
            "THW Ortsverband [% @thw_ov %] · [% @thw_einheit_kurz %]\n"
            f"Stand: [% format_date(now(), 'dd.MM.yyyy HH:mm') %] ([% {DTG} %])\n"
            "Bearbeiter: [% @thw_bearbeiter %]",
            cell_x,
            body_top,
            cell_w,
            body_h,
            body,
            item_id="Herausgeber",
        )

        # 4 Grenzen und Quellen
        cell_x, cell_w = cells[3]
        self.caption("Grenzen · Quellen", cell_x, top, cell_w + 3 * s)
        self.label(
            "[% array_to_string(map_credits('Hauptkarte'), ' · ') %]\nGrenzen lt. Kartengrundlage, ohne Gewähr.",
            cell_x,
            body_top,
            cell_w + 3 * s,
            body_h,
            5.4,
            item_id="Quellen",
        )


def save_template(layout: QgsPrintLayout, path: str) -> None:
    if not layout.saveAsTemplate(path, QgsReadWriteContext()):
        raise RuntimeError(f"Vorlage konnte nicht gespeichert werden: {path}")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # Bildpfade wie in den THW-Vorlagen relativ ablegen
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
