"""Interactive canvas widget for mirrored display — handles mouse and keyboard input.

Replaces QLabel with a custom QWidget that:
- Displays frames via QPixmap (like QLabel)
- Intercepts mouse events and maps them to device coordinates
- Sends touch/click events through InputController
- Supports drag gestures and scroll wheel
- Captures keyboard events and maps them to Android keycodes
"""
import math
import logging
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QPoint, QSize, pyqtSignal
from PyQt6.QtGui import (
    QPixmap, QImage, QPainter, QPen, QColor,
    QKeyEvent, QMouseEvent, QWheelEvent,
    QEnterEvent,
)

logger = logging.getLogger(__name__)

# ── Drag state ────────────────────────────────────────────────────────────────

class DragState:
    """Tracks an active drag gesture for one pointer."""
    __slots__ = ("in_progress", "last_x", "last_y", "start_x", "start_y")

    def __init__(self):
        self.in_progress: bool = False
        self.last_x: int = 0
        self.last_y: int = 0
        self.start_x: int = 0
        self.start_y: int = 0


# ── MirroringCanvas ──────────────────────────────────────────────────────────

class MirroringCanvas(QWidget):
    """Interactive canvas displaying mirrored frames with mouse/keyboard input support.

    Signals:
        input_mode_changed(bool): emitted when input mode is toggled
    """

    # How far the mouse must move between press and release to count as a drag
    DRAG_THRESHOLD = 5

    input_mode_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAcceptDrops(False)

        # ── State ────────────────────────────────────────────────────────
        self._pixmap: QPixmap | None = None
        self._device_width: int = 1080
        self._device_height: int = 1920
        self._input_enabled: bool = False   # off by default; toggle with button
        self._input_controller = None      # injected by main window

        # Drag state per button (left=1, right=2, middle=4)
        self._drag: dict[int, DragState] = {b: DragState() for b in (1, 2, 4)}

        # Cursor style
        self._cursor_hidden: bool = False
        self._set_cursor(True)

        # Click highlight ring
        self._click_ring_pos: QPoint | None = None
        self._click_ring_alpha: int = 0

    # ── Public API ─────────────────────────────────────────────────────────

    def set_input_enabled(self, enabled: bool):
        """Enable or disable mouse/keyboard input."""
        self._input_enabled = enabled
        self._set_cursor(enabled)
        self.setFocus()
        self.update()
        self.input_mode_changed.emit(enabled)
        logger.info(f"Input mode {'enabled' if enabled else 'disabled'}")

    def is_input_enabled(self) -> bool:
        return self._input_enabled

    def set_input_controller(self, controller):
        """Set the InputController that sends events to the device."""
        self._input_controller = controller

    def set_device_size(self, width: int, height: int):
        """Update device resolution for coordinate mapping."""
        self._device_width = width
        self._device_height = height
        logger.info(f"Device size updated: {width}x{height}")

    def set_pixmap(self, pixmap: QPixmap | None):
        """Set the current frame to display."""
        self._pixmap = pixmap
        self.update()

    # ── Coordinate mapping ─────────────────────────────────────────────────

    def _map_to_device(self, wx: int, wy: int) -> tuple[int, int]:
        """Convert widget pixel position to device screen coordinates.

        Applies aspect-ratio-preserving (letterboxed) mapping.
        """
        dev_w = self._device_width
        dev_h = self._device_height
        wid_w = self.width()
        wid_h = self.height()

        scale_x = wid_w / dev_w
        scale_y = wid_h / dev_h
        scale = min(scale_x, scale_y)

        if scale_x <= scale_y:
            # Fit to width — letterbox top/bottom
            display_w = wid_w
            display_h = int(dev_h * scale)
            offset_x = 0
            offset_y = (wid_h - display_h) // 2
        else:
            # Fit to height — letterbox left/right
            display_h = wid_h
            display_w = int(dev_w * scale)
            offset_x = (wid_w - display_w) // 2
            offset_y = 0

        # Undo transform
        rel_x = wx - offset_x
        rel_y = wy - offset_y
        dev_x = int(rel_x / scale)
        dev_y = int(rel_y / scale)

        # Clamp to screen bounds
        dev_x = max(0, min(dev_w - 1, dev_x))
        dev_y = max(0, min(dev_h - 1, dev_y))

        return dev_x, dev_y

    # ── Cursor helpers ─────────────────────────────────────────────────────

    def _set_cursor(self, show: bool):
        if show:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def _show_click_ring(self, x: int, y: int):
        """Briefly show a visual dot at the clicked position."""
        self._click_ring_pos = QPoint(int(x), int(y))
        self._click_ring_alpha = 255
        self.update()

    # ── QWidget paint ─────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Background
        painter.fillRect(self.rect(), QColor("#1e1e1e"))

        if self._pixmap and not self._pixmap.isNull():
            # Scale to fit while keeping aspect ratio
            scaled = self._pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            # Placeholder text
            painter.setPen(QColor("#666666"))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter,
                "Select a device and click Connect\n\n"
                "Make sure USB debugging is enabled on your device."
            )

        # Input mode indicator (top-left corner)
        if self._input_enabled:
            painter.setPen(QColor("#4CAF50"))
            painter.drawText(8, 18, "[ Input ON ]")

        # Fading click ring
        if self._click_ring_alpha > 0:
            alpha = max(0, self._click_ring_alpha)
            color = QColor(0, 200, 100, alpha)
            painter.setPen(QPen(color, 2))
            r = 12
            cx, cy = self._click_ring_pos.x(), self._click_ring_pos.y()
            painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)
            self._click_ring_alpha -= 30
            if self._click_ring_alpha > 0:
                self.update()

    # ── Mouse events ───────────────────────────────────────────────────────

    def _process_mouse(
        self,
        event: QMouseEvent,
        action: str,   # "down" | "move" | "up"
        button: int,   # Qt.LeftButton == 1
    ):
        if not self._input_enabled or not self._input_controller:
            return

        ctrl = self._input_controller
        ds = self._drag[button]
        wx = event.position().x()
        wy = event.position().y()

        if action == "down":
            ds.in_progress = False
            ds.start_x = wx
            ds.start_y = wy
            ds.last_x = wx
            ds.last_y = wy
            ctrl.touch_down(wx, wy, self.width(), self.height())

        elif action == "move":
            if not ds.in_progress:
                dx = wx - ds.start_x
                dy = wy - ds.start_y
                if dx * dx + dy * dy > self.DRAG_THRESHOLD * self.DRAG_THRESHOLD:
                    ds.in_progress = True
                    # Inject a compensatory move to close the gap
                    ctrl.touch_move(ds.last_x, ds.last_y, self.width(), self.height())
            if ds.in_progress:
                ctrl.touch_move(wx, wy, self.width(), self.height())
            ds.last_x = wx
            ds.last_y = wy

        elif action == "up":
            ctrl.touch_up(wx, wy, self.width(), self.height())
            if not ds.in_progress:
                # It was a click — show visual feedback
                self._show_click_ring(wx, wy)
            ds.in_progress = False

    def mousePressEvent(self, event: QMouseEvent):
        self._process_mouse(event, "down", int(event.button().value))

    def mouseMoveEvent(self, event: QMouseEvent):
        buttons_val = int(event.buttons().value)
        if buttons_val == 0:
            return  # No button held — nothing to do
        self._process_mouse(event, "move", buttons_val)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._process_mouse(event, "up", int(event.button().value))

    def wheelEvent(self, event: QWheelEvent):
        if not self._input_enabled or not self._input_controller:
            return

        # Use the cursor position as reference
        wx = event.position().x()
        wy = event.position().y()

        angle_delta = event.angleDelta()
        if event.orientation() == Qt.Orientation.Vertical:
            v = angle_delta.y()
        else:
            v = angle_delta.x()

        self._input_controller.inject_scroll(
            wx, wy, self.width(), self.height(),
            h_scroll=angle_delta.x() if event.orientation() == Qt.Orientation.Horizontal else 0,
            v_scroll=v,
        )

    # ── Keyboard events ───────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        if not self._input_enabled or not self._input_controller:
            super().keyPressEvent(event)
            return

        ctrl = self._input_controller
        key = event.key()
        mod = event.modifiers()

        # Map common shortcuts before falling through to individual keycodes
        handled = self._handle_key_shortcut(key, mod, press=True)
        if handled:
            event.accept()
            return

        # Character input (text)
        if not mod and key >= 32 and key < 0x100000:
            char = event.text()
            if char:
                ctrl.inject_text(char)
                event.accept()
                return

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent):
        if not self._input_enabled or not self._input_controller:
            super().keyReleaseEvent(event)
            return

        ctrl = self._input_controller
        key = event.key()
        mod = event.modifiers()

        handled = self._handle_key_shortcut(key, mod, press=False)
        if handled:
            event.accept()
            return

        super().keyReleaseEvent(event)

    def _handle_key_shortcut(self, key: int, mod: int, press: bool) -> bool:
        """Map keyboard shortcuts to Android actions. Returns True if handled."""
        ctrl = self._input_controller

        if mod & Qt.KeyboardModifier.ControlModifier:
            # Ctrl+A/Z/C/V/X supported via inject_text
            return False

        # ── Single-key shortcuts ─────────────────────────────────────────
        shortcut_map = {
            # (qt_key, android_keycode_or_callable)
            (Qt.Key.Key_Escape,     None): "back_or_screen_on",
            (Qt.Key.Key_Back,       None): "back_or_screen_on",
            (Qt.Key.Key_Return,     None): "home",
            (Qt.Key.Key_Home,       None): "home",
            (Qt.Key.Key_Enter,      None): "enter",
            (Qt.Key.Key_Menu,       None): "menu",
            (Qt.Key.Key_VolumeUp,   None): "volume_up",
            (Qt.Key.Key_VolumeDown, None): "volume_down",
            (Qt.Key.Key_VolumeMute, None): "mute",
            (Qt.Key.Key_PowerOff,   None): "power",
        }

        entry = shortcut_map.get((key, None))
        if entry is not None:
            if not press:
                return True
            if entry == "back_or_screen_on":
                ctrl.back_or_turn_screen_on()
            elif entry == "home":
                ctrl.press_key(3)    # HOME
                ctrl.release_key(3)
            elif entry == "enter":
                ctrl.press_key(66)   # ENTER
                ctrl.release_key(66)
            elif entry == "menu":
                ctrl.press_key(82)   # MENU
                ctrl.release_key(82)
            elif entry == "volume_up":
                ctrl.key_press(24)    # VOLUME_UP
            elif entry == "volume_down":
                ctrl.key_press(25)    # VOLUME_DOWN
            elif entry == "mute":
                ctrl.key_press(91)    # MUTE
            elif entry == "power":
                ctrl.key_press(26)    # POWER
                ctrl.release_key(26)
            return True

        return False

    # ── Size hint ──────────────────────────────────────────────────────────

    def sizeHint(self) -> QSize:
        return QSize(540, 960)
