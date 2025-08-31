import os
from PyQt5.QtWidgets import QDockWidget, QSizePolicy
from PyQt5.QtCore import Qt
from .thwtoolboxplugin_dock import SvgDock

class DockManager:
    def __init__(self, iface, plugin_dir, select_callback):
        self.iface        = iface
        self.plugin_dir   = plugin_dir
        self.select_cb    = select_callback
        self.dock_widget  = None

    def init_dock(self):
        if self.dock_widget:
            return
        dock = QDockWidget("Symbole", self.iface.mainWindow())
        dock.setAllowedAreas(Qt.RightDockWidgetArea)
        w = SvgDock(self.plugin_dir, self.select_cb)
        w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        dock.setWidget(w)
        dock.setMinimumWidth(30)
        self.iface.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.dock_widget = dock

    def remove(self):
        if self.dock_widget:
            self.iface.removeDockWidget(self.dock_widget)
