"""Layout maßstabstreu auf mehrere A4-Blätter verteilt als PDF ausgeben.

Im Einsatz steht meist nur ein A4-Drucker zur Verfügung. Das Layout wird
deshalb in Originalgröße in A4-Seiten zerlegt, die nach dem Druck
zusammengeklebt werden. Weil Drucker den Blattrand nicht bedrucken, wiederholt
jedes Folgeblatt einen Streifen des vorherigen (`overlap`) und beginnt erst
hinter dem Druckrand (`margin`); dort wird eine Schnittlinie gezeichnet.

Die Vorlage `Toolbox_A3_QUER_2xA4` (400 × 297 mm) ist so bemessen, dass sie
genau auf zwei A4-Blätter im Hochformat passt.
"""

import math
import os
from dataclasses import dataclass

from qgis.core import QgsLayoutExporter, QgsPrintLayout, QgsUnitTypes
from qgis.PyQt.QtCore import QMarginsF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen
from qgis.PyQt.QtWidgets import QAction, QFileDialog, QMessageBox

A4_SHORT = 210.0
A4_LONG = 297.0
DEFAULT_MARGIN = 5.0
DEFAULT_OVERLAP = 10.0


@dataclass
class TilePlan:
    """Aufteilung einer Layoutseite auf A4-Blätter, alle Maße in mm."""

    landscape: bool
    x_offsets: list[float]
    y_offsets: list[float]

    @property
    def sheet_width(self) -> float:
        return A4_LONG if self.landscape else A4_SHORT

    @property
    def sheet_height(self) -> float:
        return A4_SHORT if self.landscape else A4_LONG

    @property
    def count(self) -> int:
        return len(self.x_offsets) * len(self.y_offsets)


def _offsets(length: float, sheet: float, margin: float, overlap: float) -> list[float]:
    """Linke/obere Kanten der Blätter im Layout.

    Das erste Blatt liegt bündig am Layoutrand, der äußere Layoutrand darf also
    im nicht bedruckbaren Bereich liegen. Jedes weitere Blatt setzt `overlap`
    vor dem bedruckbaren Ende des vorherigen an.
    """
    step = sheet - 2 * margin - overlap
    count = 1 + max(0, math.ceil((length - sheet) / step - 1e-9))
    return [i * step for i in range(count)]


def plan_tiles(
    width: float, height: float, margin: float = DEFAULT_MARGIN, overlap: float = DEFAULT_OVERLAP
) -> TilePlan:
    """Blattaufteilung mit den wenigsten A4-Blättern (Hoch- oder Querformat)."""
    plans = [
        TilePlan(
            landscape,
            _offsets(width, A4_LONG if landscape else A4_SHORT, margin, overlap),
            _offsets(height, A4_SHORT if landscape else A4_LONG, margin, overlap),
        )
        for landscape in (False, True)
    ]
    return min(plans, key=lambda plan: plan.count)


def export_tiled_pdf(
    layout: QgsPrintLayout,
    path: str,
    margin: float = DEFAULT_MARGIN,
    overlap: float = DEFAULT_OVERLAP,
    dpi: int = 300,
) -> int:
    """Schreibt die erste Seite von `layout` als mehrseitiges A4-PDF, gibt die Blattzahl zurück."""
    page = layout.pageCollection().page(0)
    size = layout.convertToLayoutUnits(page.pageSize())
    to_mm = layout.convertFromLayoutUnits(1, QgsUnitTypes.LayoutUnit.LayoutMillimeters).length()
    plan = plan_tiles(size.width() * to_mm, size.height() * to_mm, margin, overlap)

    writer = QPdfWriter(path)
    writer.setResolution(dpi)
    writer.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Landscape if plan.landscape else QPageLayout.Orientation.Portrait,
            QMarginsF(0, 0, 0, 0),
        )
    )
    writer.setTitle(layout.name())

    painter = QPainter()
    if not painter.begin(writer):
        raise ValueError(f"PDF konnte nicht geschrieben werden:\n{path}")

    px = dpi / 25.4
    cut_pen = QPen(QColor(120, 120, 120), 0.15 * px, Qt.PenStyle.DashLine)
    exporter = QgsLayoutExporter(layout)
    try:
        first = True
        for row, y in enumerate(plan.y_offsets):
            for col, x in enumerate(plan.x_offsets):
                if not first:
                    writer.newPage()
                first = False
                # Der Ausschnitt in Blattgröße füllt die ganze Seite, der Maßstab bleibt 1:1
                region = QRectF(x / to_mm, y / to_mm, plan.sheet_width / to_mm, plan.sheet_height / to_mm)
                exporter.renderRegion(painter, region)

                # Rand zum Vorgängerblatt freistellen und Schnittlinie ziehen
                page_w = plan.sheet_width * px
                page_h = plan.sheet_height * px
                edge = margin * px
                painter.save()
                painter.setPen(cut_pen)
                if col > 0:
                    painter.fillRect(QRectF(0, 0, edge, page_h), Qt.GlobalColor.white)
                    painter.drawLine(int(edge), 0, int(edge), int(page_h))
                if row > 0:
                    painter.fillRect(QRectF(0, 0, page_w, edge), Qt.GlobalColor.white)
                    painter.drawLine(0, int(edge), int(page_w), int(edge))
                painter.restore()
    finally:
        painter.end()
    return plan.count


def add_designer_action(designer) -> QAction:
    """Menüeintrag „Als A4-Blätter exportieren“ im Layout-Designer anlegen."""
    action = QAction("Als A4-Blätter exportieren (PDF) …", designer.window())
    action.setToolTip("Layout maßstabstreu auf A4-Blätter zum Zusammenkleben verteilen")
    action.triggered.connect(lambda: _export_from_designer(designer))
    designer.layoutMenu().addAction(action)
    return action


def _export_from_designer(designer) -> None:
    layout = designer.layout()
    parent = designer.window()
    page = layout.pageCollection().page(0)
    size = layout.convertToLayoutUnits(page.pageSize())
    to_mm = layout.convertFromLayoutUnits(1, QgsUnitTypes.LayoutUnit.LayoutMillimeters).length()
    plan = plan_tiles(size.width() * to_mm, size.height() * to_mm)

    default = os.path.join(os.path.expanduser("~"), f"{layout.name()}_A4.pdf")
    path, _ = QFileDialog.getSaveFileName(parent, f"Als {plan.count} A4-Blätter exportieren", default, "PDF (*.pdf)")
    if not path:
        return
    try:
        count = export_tiled_pdf(layout, path)
    except ValueError as e:
        QMessageBox.critical(parent, "Fehler", str(e))
        return
    QMessageBox.information(
        parent,
        "Export abgeschlossen",
        f"{count} A4-Blätter ({'quer' if plan.landscape else 'hoch'}) geschrieben.\n\n"
        "Mit „Tatsächliche Größe“ (100 %) drucken, Folgeblätter an der gestrichelten Linie "
        f"abschneiden und {DEFAULT_OVERLAP:.0f} mm überlappend aufkleben.",
    )
