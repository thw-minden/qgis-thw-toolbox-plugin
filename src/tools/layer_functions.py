"""
This file contains functions to generate special layers in QGIS that are not provided via a tile service
"""

import os

from qgis.core import (
    Qgis,
    QgsLayerTreeGroup,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsProject,
    QgsProperty,
    QgsRasterLayer,
    QgsRectangle,
    QgsRuleBasedRenderer,
    QgsSvgMarkerSymbolLayer,
    QgsSymbolLayer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor


def setup_thw_dienststellen(label: str) -> QgsVectorLayer:
    plugin_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    dst_json = os.path.join(plugin_dir, "data", "ovs.json")

    svg_map = [
        ("Ortsverband", os.path.join(plugin_dir, "svgs", "THW_Gebäude", "OV_Unterkunft.svg")),
        ("Regionalstelle", os.path.join(plugin_dir, "svgs", "THW_Gebäude", "Regionalstelle.svg")),
        ("Landesverband", os.path.join(plugin_dir, "svgs", "THW_Gebäude", "Landesverband.svg")),
        ("Ausbildungszentrum", os.path.join(plugin_dir, "svgs", "THW_Gebäude", "Ausbildungszentrum.svg")),
        ("Leitung", os.path.join(plugin_dir, "svgs", "THW_Gebäude", "Leitung.svg")),
    ]

    print("JSON path:", dst_json)
    layer = QgsVectorLayer(str(dst_json), label, "ogr")
    if not layer.isValid():
        raise ValueError(f"{label} layer invalid: {layer.error().summary()}")

    root_rule = QgsRuleBasedRenderer.Rule(None)

    for key, svg_path in svg_map:
        symbol = QgsMarkerSymbol.createSimple({})
        svg_layer = QgsSvgMarkerSymbolLayer(svg_path)

        expr_size = (
            "CASE "
            "WHEN @zoom_level >= 10 AND @zoom_level <= 16 THEN scale_linear(@zoom_level, 10, 16, 300, 20) "
            "WHEN @zoom_level > 16 THEN 20 "
            "ELSE 300 "
            "END"
        )
        svg_layer.setDataDefinedProperty(QgsSymbolLayer.Property.Size, QgsProperty.fromExpression(expr_size))
        svg_layer.setSizeUnit(QgsUnitTypes.RenderUnit.RenderMapUnits)

        symbol.changeSymbolLayer(0, svg_layer)

        rule = QgsRuleBasedRenderer.Rule(symbol=symbol, filterExp=f"""\"title\" ILIKE '%{key}%'""", label=key)
        root_rule.appendChild(rule)

    layer.setRenderer(QgsRuleBasedRenderer(root_rule))

    # Labels
    label_settings = QgsPalLayerSettings()
    text_format = QgsTextFormat()
    text_format.setSize(8)
    text_format.setSizeUnit(Qgis.RenderUnit.RenderMapUnits)
    text_format.setColor(QColor("black"))
    font = text_format.font()
    font.setBold(True)
    text_format.setFont(font)

    buffer_settings = QgsTextBufferSettings()
    buffer_settings.setEnabled(True)
    buffer_settings.setSize(1)
    buffer_settings.setSizeUnit(Qgis.RenderUnit.RenderMapUnits)
    buffer_settings.setColor(QColor("white"))
    text_format.setBuffer(buffer_settings)

    label_settings.setFormat(text_format)
    label_settings.fieldName = (
        """replace("title",array('Ortsverband ','Regionalstelle ','Landesverband ','Ausbildungszentrum '),'')"""
    )
    label_settings.isExpression = True

    try:
        # 1) Place labels around the point (cartographic / ordered positions)
        if hasattr(Qgis, "LabelPlacement") and hasattr(Qgis.LabelPlacement, "OverPoint"):
            label_settings.placement = Qgis.LabelPlacement.OverPoint
        else:
            # Fallback for older versions
            label_settings.placement = QgsPalLayerSettings.OverPoint

        # 2) Tell QGIS to put the label in the bottom/below quadrant of the point
        if hasattr(Qgis, "LabelQuadrantPosition"):
            # QGIS >= 3.26: use the new enum
            label_settings.quadOffset = Qgis.LabelQuadrantPosition.Below
        else:
            # Older API: use QuadrantPosition enum from QgsPalLayerSettings
            if hasattr(QgsPalLayerSettings, "BottomMiddle"):
                label_settings.quadOffset = QgsPalLayerSettings.BottomMiddle
            elif hasattr(QgsPalLayerSettings, "Bottom"):
                label_settings.quadOffset = QgsPalLayerSettings.Bottom
            else:
                # last resort: standard bottom-middle index
                label_settings.quadOffset = (
                    QgsPalLayerSettings.BottomMiddle if hasattr(QgsPalLayerSettings, "BottomMiddle") else 0
                )
    except Exception as e:
        print(f"DEBUG: Position festsetzen fehlgeschlagen: {e}")

    try:
        # Offset-Werte in Points , Abstand abhängig von der Größe des Zeichens
        if hasattr(Qgis, "RenderUnit") and hasattr(Qgis.RenderUnit, "Points"):
            label_settings.offsetUnits = Qgis.RenderUnit.RenderMapUnits
        size_property = QgsProperty.fromExpression("array(0,5)")
        label_settings.dataDefinedProperties().setProperty(QgsPalLayerSettings.Property.OffsetXY, size_property)
    except Exception as e:
        print(f"DEBUG: Fehler bei der Festsetzung der Position: {e}")

    label_settings.dataDefinedProperties().setProperty(QgsPalLayerSettings.Property.ScaleVisibility, True)
    label_settings.dataDefinedProperties().setProperty(QgsPalLayerSettings.Property.MinimumScale, 5000)
    label_settings.dataDefinedProperties().setProperty(QgsPalLayerSettings.Property.MaximumScale, 0)

    layer.setLabelsEnabled(True)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(label_settings))

    return layer
