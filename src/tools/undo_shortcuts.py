from collections.abc import Callable

from qgis.PyQt.QtCore import QEvent, QObject, Qt
from qgis.PyQt.QtWidgets import QAbstractSpinBox, QApplication, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit

_TEXT_INPUTS = (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox)


class UndoShortcutFilter(QObject):
    """Application-wide filter turning Ctrl+Z / Ctrl+Y (and Ctrl+Shift+Z) into annotation undo/redo.

    QGIS binds Ctrl+Z to its own undo (vector layer editing). We claim the keys via a
    ShortcutOverride — and only when it makes sense:

    - the keyboard focus is inside one of ``scopes`` (the map canvas, the THW dock), and not
      in a text input, which keeps its own Ctrl+Z;
    - ``is_available()`` agrees (the plugin checks that no vector layer is being edited, so
      QGIS's undo for vector editing keeps working).
    """

    def __init__(
        self,
        scopes: Callable[[], list],
        is_available: Callable[[], bool],
        on_undo: Callable[[], None],
        on_redo: Callable[[], None],
        parent=None,
    ):
        super().__init__(parent)
        self._scopes = scopes
        self._is_available = is_available
        self._on_undo = on_undo
        self._on_redo = on_redo

    def eventFilter(self, obj, event):
        event_type = event.type()
        if event_type not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            return False
        action = self._action_for(event)
        if action is None or not self._applies():
            return False
        if event_type == QEvent.Type.ShortcutOverride:
            # Accepting the override delivers the key as a normal KeyPress instead of QGIS's shortcut
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

    def _applies(self) -> bool:
        focus = QApplication.focusWidget()
        if focus is None or isinstance(focus, _TEXT_INPUTS):
            return False
        scopes = [w for w in self._scopes() if w is not None]
        if not any(focus is scope or scope.isAncestorOf(focus) for scope in scopes):
            return False
        return self._is_available()
