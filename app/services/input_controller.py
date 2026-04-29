"""scrcpy control message sender — implements the scrcpy control protocol over TCP.

This module sends input events (touch, key, text, clipboard) to the scrcpy server
running on the Android device, over the same TCP socket used for video frames.
The scrcpy server forwards them to the Android input subsystem via uinput.
"""
import struct
import socket
import threading
import logging
from typing import Callable
from dataclasses import dataclass
from enum import IntEnum

logger = logging.getLogger(__name__)


# ── scrcpy control message types (opcode) ────────────────────────────────────

class ControlType(IntEnum):
    INJECT_KEYCODE = 0
    INJECT_TEXT = 1
    INJECT_TOUCH_EVENT = 2
    INJECT_SCROLL_EVENT = 3
    BACK_OR_SCREEN_ON = 4
    EXPAND_NOTIFICATION_PANEL = 5
    EXPAND_SETTINGS_PANEL = 6
    COLLAPSE_PANELS = 7
    GET_CLIPBOARD = 8
    SET_CLIPBOARD = 9
    SET_SCREEN_POWER_MODE = 10
    ROTATE_DEVICE = 11


# ── Android keycodes (subset) ────────────────────────────────────────────────

class Keycode(IntEnum):
    # Navigation
    HOME          = 3
    BACK          = 4
    CALL          = 5
    ENDCALL       = 6
    ENTER         = 66
    MENU          = 82
    # Volume
    VOLUME_UP     = 24
    VOLUME_DOWN   = 25
    POWER         = 26
    MUTE          = 91
    # Misc
    TAB           = 61
    ESCAPE        = 82 + 17   # use inject_keycode with META
    # Letters / digits
    A             = 29
    C             = 30
    V             = 47
    X             = 41
    # Modifier
    CTRL_LEFT     = 29 + 17


# ── Android KeyEvent actions ──────────────────────────────────────────────────

class KeyAction(IntEnum):
    DOWN = 0
    UP = 1


# ── Touch actions (MotionEvent) ───────────────────────────────────────────────

class TouchAction(IntEnum):
    DOWN   = 0
    UP     = 1
    MOVE   = 2
    CANCEL = 3


# ── Touch source ─────────────────────────────────────────────────────────────

class TouchSource(IntEnum):
    SCREEN   = 0x0002  # MT stylus, fall back to touchscreen
    MOUSE    = 0x0002  # same value, handled by scrcpy server


# ── Screen power mode ────────────────────────────────────────────────────────

class PowerMode(IntEnum):
    OFF     = 0
    NORMAL  = 2


@dataclass
class Point:
    x: int
    y: int


@dataclass
class DeviceSize:
    width: int
    height: int


class InputController:
    """Sends input events to scrcpy server via TCP control channel.

    Control messages are sent over the same socket used for video frames.
    The scrcpy server interprets them and forwards events to Android.
    """

    def __init__(
        self,
        serial: str,
        sock: socket.socket,
        device_size: DeviceSize | None = None,
    ):
        self.serial = serial
        self._sock = sock
        self._device_size = device_size or DeviceSize(1080, 1920)
        self._lock = threading.Lock()
        # Track pointer position for drag gestures
        self._pointer_position: dict[int, Point] = {}  # pointer_id → Point

    def set_device_size(self, width: int, height: int):
        with self._lock:
            self._device_size = DeviceSize(width, height)

    # ── Raw message sender ─────────────────────────────────────────────────

    def _send_message(self, msg_type: int, data: bytes):
        """Send a control message: [4-byte length][1-byte type][payload]."""
        payload = bytes([msg_type]) + data
        header = struct.pack(">I", len(payload))
        try:
            with self._lock:
                self._sock.sendall(header + payload)
        except OSError as e:
            logger.debug(f"Failed to send control message: {e}")

    # ── Coordinate mapping ─────────────────────────────────────────────────

    def _map_to_device(self, widget_x: int, widget_y: int,
                       widget_w: int, widget_h: int) -> Point:
        """Convert a widget pixel position to device screen coordinates.

        Applies aspect-ratio-preserving mapping so the click always lands
        on the correct physical screen position regardless of window size.
        """
        dw, dh = self._device_size.width, self._device_size.height
        ww, wh = widget_w, widget_h

        # Compute the visible area (letterboxed)
        scale_x = ww / dw
        scale_y = wh / dh
        scale = min(scale_x, scale_y)

        if scale_x < scale_y:
            # Letterboxed top/bottom — image fits in width
            display_w = ww
            display_h = int(dh * scale)
            offset_x = 0
            offset_y = (wh - display_h) // 2
        else:
            # Letterboxed left/right — image fits in height
            display_h = wh
            display_w = int(dw * scale)
            offset_x = (ww - display_w) // 2
            offset_y = 0

        # Undo scaling + offset
        rel_x = widget_x - offset_x
        rel_y = widget_y - offset_y
        dev_x = int(rel_x / scale)
        dev_y = int(rel_y / scale)

        # Clamp to device bounds
        dev_x = max(0, min(dw - 1, dev_x))
        dev_y = max(0, min(dh - 1, dev_y))

        return Point(dev_x, dev_y)

    # ── Key events ────────────────────────────────────────────────────────

    def press_key(self, keycode: int, action: int = KeyAction.DOWN):
        """Send a key press/release event."""
        msg = struct.pack(">ii", action, keycode)
        self._send_message(ControlType.INJECT_KEYCODE, msg)

    def release_key(self, keycode: int):
        self.press_key(keycode, KeyAction.UP)

    def key_press(self, keycode: int):
        """Send a complete key press (down + up)."""
        self.press_key(keycode, KeyAction.DOWN)
        self.press_key(keycode, KeyAction.UP)

    # ── Text input ─────────────────────────────────────────────────────────

    def inject_text(self, text: str):
        """Send a text string (for character-by-character input)."""
        msg = text.encode("utf-8") + b"\x00"
        self._send_message(ControlType.INJECT_TEXT, msg)

    # ── Touch events ──────────────────────────────────────────────────────

    def touch_down(
        self,
        widget_x: int, widget_y: int,
        widget_w: int, widget_h: int,
        pointer_id: int = 0,
        pressure: float = 1.0,
    ):
        """Send a touch-down (click start) at widget position."""
        pt = self._map_to_device(widget_x, widget_y, widget_w, widget_h)
        self._pointer_position[pointer_id] = pt
        self._inject_touch(TouchAction.DOWN, pt.x, pt.y, pointer_id, pressure)

    def touch_move(
        self,
        widget_x: int, widget_y: int,
        widget_w: int, widget_h: int,
        pointer_id: int = 0,
        pressure: float = 1.0,
    ):
        """Send a touch-move at widget position."""
        pt = self._map_to_device(widget_x, widget_y, widget_w, widget_h)
        self._pointer_position[pointer_id] = pt
        self._inject_touch(TouchAction.MOVE, pt.x, pt.y, pointer_id, pressure)

    def touch_up(
        self,
        widget_x: int, widget_y: int,
        widget_w: int, widget_h: int,
        pointer_id: int = 0,
    ):
        """Send a touch-up (click end) at widget position."""
        pt = self._map_to_device(widget_x, widget_y, widget_w, widget_h)
        self._pointer_position[pointer_id] = pt
        self._inject_touch(TouchAction.UP, pt.x, pt.y, pointer_id, 0.0)
        self._pointer_position.pop(pointer_id, None)

    def touch_cancel(self, pointer_id: int = 0):
        """Send a touch cancel (gesture cancelled)."""
        pt = self._pointer_position.get(pointer_id, Point(0, 0))
        self._inject_touch(TouchAction.CANCEL, pt.x, pt.y, pointer_id, 0.0)

    def _inject_touch(
        self,
        action: int,
        x: int, y: int,
        pointer_id: int,
        pressure: float,
        source: int = 0x0002,   # touchscreen / mouse source
        buttons: int = 0x0001,  # primary button (needed for mouse emulation)
    ):
        """Build and send a touch event message."""
        # struct format (all little-endian, signed 64-bit for pointer_id):
        #   action(int32) | x(int32) | y(int32) | pointer_id(int64)
        #   | pressure(float32) | source(int32)
        #   | buttons(int32, for motion events)
        if action == TouchAction.MOVE:
            msg = struct.pack(
                "<iiiqiifii",
                action, x, y, pointer_id, pressure, source, buttons
            )
        else:
            msg = struct.pack(
                "<iiiqiif",
                action, x, y, pointer_id, pressure, source
            )
        self._send_message(ControlType.INJECT_TOUCH_EVENT, msg)

    # ── Scroll (mouse wheel) ──────────────────────────────────────────────

    def inject_scroll(
        self,
        widget_x: int, widget_y: int,
        widget_w: int, widget_h: int,
        h_scroll: int = 0,
        v_scroll: int = -1,
        pointer_id: int = 0,
    ):
        """Send a scroll (mouse wheel) event."""
        pt = self._map_to_device(widget_x, widget_y, widget_w, widget_h)
        # scroll events use 8-fixed-point for smooth scrolling
        h = int(h_scroll * 200)
        v = int(v_scroll * 200)
        msg = struct.pack(
            "<iiiqiif",
            pt.x, pt.y, pointer_id, 0, 1.0, 0x0002, h, v
        )
        self._send_message(ControlType.INJECT_SCROLL_EVENT, msg)

    # ── Power / system ────────────────────────────────────────────────────

    def power_on(self):
        """Wake up the device screen."""
        # action=0 (wake up), power=PowerMode.NORMAL
        msg = struct.pack(">ii", 0, PowerMode.NORMAL)
        self._send_message(ControlType.SET_SCREEN_POWER_MODE, msg)

    def power_off(self):
        msg = struct.pack(">ii", 0, PowerMode.OFF)
        self._send_message(ControlType.SET_SCREEN_POWER_MODE, msg)

    def back_or_turn_screen_on(self):
        """Press Back or turn screen on (if off)."""
        self._send_message(ControlType.BACK_OR_SCREEN_ON, b"")

    def expand_notification_panel(self):
        self._send_message(ControlType.EXPAND_NOTIFICATION_PANEL, b"")

    def expand_settings_panel(self):
        self._send_message(ControlType.EXPAND_SETTINGS_PANEL, b"")

    def collapse_panels(self):
        self._send_message(ControlType.COLLAPSE_PANELS, b"")

    # ── Clipboard ─────────────────────────────────────────────────────────

    def set_clipboard(self, text: str):
        """Set device clipboard text."""
        # flags=0 (copy), content follows
        content = text.encode("utf-8")
        msg = struct.pack(">i", 0) + content + b"\x00"
        self._send_message(ControlType.SET_CLIPBOARD, msg)

    def get_clipboard(self) -> str | None:
        """Request device clipboard text. Returns None (async — use callback)."""
        # copy_id=0
        msg = struct.pack(">i", 0)
        self._send_message(ControlType.GET_CLIPBOARD, msg)
        return None  # scrcpy server pushes clipboard via video socket

    # ── Device rotation ───────────────────────────────────────────────────

    def rotate_device(self):
        self._send_message(ControlType.ROTATE_DEVICE, b"")
