"""Strg+Z / Strg+Y für die Objektplanung, ohne QGIS sein eigenes Rückgängig wegzunehmen."""

from collections.abc import Callable

from qgis.PyQt.QtCore import QEvent, QObject, Qt


class UndoShortcutFilter(QObject):
    """Ereignisfilter für ein einzelnes Widget (die Karte): Strg+Z bzw. Strg+Y / Strg+Umschalt+Z → Objektplanung.

    QGIS belegt Strg+Z mit seinem eigenen Rückgängig (Bearbeitungsmodus von Vektorlayern). Hat das
    Widget den Fokus, beansprucht der Filter die Tasten über ein ShortcutOverride – aber nur, solange
    ``is_available()`` zustimmt (kein Vektorlayer im Bearbeitungsmodus, dann gilt das Rückgängig von QGIS).

    Bewusst nicht an der ganzen Anwendung installiert: Ein Python-Filter dort sieht jedes Ereignis
    jedes Objekts in QGIS und brachte QGIS beim Start zum Absturz.
    """

    def __init__(
        self,
        is_available: Callable[[], bool],
        on_undo: Callable[[], None],
        on_redo: Callable[[], None],
        parent=None,
    ):
        super().__init__(parent)
        self._is_available = is_available
        self._on_undo = on_undo
        self._on_redo = on_redo

    def eventFilter(self, _obj, event):
        event_type = event.type()
        if event_type not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            return False
        action = self._action_for(event)
        if action is None or not self._is_available():
            return False
        if event_type == QEvent.Type.ShortcutOverride:
            # Angenommen: die Taste kommt als normales KeyPress statt als QGIS-Kürzel
            event.accept()
            return True
        action()
        return True

    def _action_for(self, event) -> Callable[[], None] | None:
        modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        ctrl = Qt.KeyboardModifier.ControlModifier
        shift = Qt.KeyboardModifier.ShiftModifier
        key = event.key()
        if key == Qt.Key.Key_Z and modifiers == ctrl:
            return self._on_undo
        if (key == Qt.Key.Key_Y and modifiers == ctrl) or (key == Qt.Key.Key_Z and modifiers == ctrl | shift):
            return self._on_redo
        return None
