"""Linux input controller — sends mouse/keyboard events to a Linux device via ADB + xdotool.

Architecture:
  Host sends input commands over a dedicated TCP socket.  The receive loop
  on the device side parses them and executes xdotool commands.

  The socket connection is established in both directions:
    - Host:  adb forward tcp:LOCAL_INPUT  tcp:REMOTE_INPUT   (device listens)
    - Device: nc -l -p REMOTE_INPUT | bash script that runs xdotool
  or simpler:
    - Host:  adb reverse tcp:LOCAL_INPUT  tcp:REMOTE_INPUT   (device is TCP server)

  Simpler approach: each event is sent as a short ADB shell command (synchronous,
  no extra socket needed).  Latency is higher but it works universally.
"""
import logging
import threading
import socket
import struct
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Linux-specific keycodes for xdotool
_XDOTOOL_KEYS = {
    3:   "key Return",          # HOME -> Return (mapped to Enter)
    4:   "key Escape",          # BACK
    24:  "keyup volumeup",      # VOLUME_UP
    25:  "keyup volumedown",    # VOLUME_DOWN
    26:  "keyup power",         # POWER
    61:  "key Tab",             # TAB
    66:  "key Return",          # ENTER
    82:  "key Menu",            # MENU
    91:  "keyup XF86AudioMute",  # MUTE
}


@dataclass
class Point:
    x: int
    y: int


@dataclass
class DeviceSize:
    width: int
    height: int


class LinuxInputController:
    """Sends input events to a Linux device via ADB shell + xdotool.

    Works on any Linux device with xdotool installed (and a running X11 session).
    xdotool sends input events directly to the X11 server, giving full mouse
    and keyboard control of the Linux desktop.
    """

    def __init__(self, serial: str, adb_manager, device_size: DeviceSize | None = None):
        self.serial = serial
        self._adb = adb_manager
        self._device_size = device_size or DeviceSize(1920, 1080)
        self._lock = threading.Lock()

    def set_device_size(self, width: int, height: int):
        with self._lock:
            self._device_size = DeviceSize(width, height)

    # ── Coordinate mapping ─────────────────────────────────────────────────

    def _map_to_device(self, widget_x: int, widget_y: int,
                       widget_w: int, widget_h: int) -> Point:
        """Convert widget pixel position to device screen coordinates.

        Applies aspect-ratio-preserving (letterboxed) mapping so clicks
        land on the correct physical screen position regardless of window size.
        """
        dw, dh = self._device_size.width, self._device_size.height
        ww, wh = widget_w, widget_h

        # Compute the visible area (letterboxed)
        scale_x = ww / dw
        scale_y = wh / dh
        if scale_x < scale_y:
            # Letterboxed vertically — image fits in width
            display_w = ww
            display_h = int(dh * scale_x)
            offset_x = 0
            offset_y = (wh - display_h) // 2
        else:
            # Letterboxed horizontally — image fits in height
            display_h = wh
            display_w = int(dw * scale_y)
            offset_x = (ww - display_w) // 2
            offset_y = 0

        # Undo scaling + offset
        rel_x = widget_x - offset_x
        rel_y = widget_y - offset_y
        scale = min(scale_x, scale_y)
        dev_x = int(rel_x / scale)
        dev_y = int(rel_y / scale)

        # Clamp to device bounds
        dev_x = max(0, min(dw - 1, dev_x))
        dev_y = max(0, min(dh - 1, dev_y))
        return Point(dev_x, dev_y)

    # ── ADB shell helper ──────────────────────────────────────────────────

    def _run_input(self, cmd: str):
        """Run an xdotool command via ADB shell. Timeout short for responsiveness."""
        try:
            self._adb.shell(self.serial, f"xdotool {cmd}", timeout=3)
        except Exception as e:
            logger.warning(f"xdotool command failed: {e}")

    # ── Key events ────────────────────────────────────────────────────────

    def press_key(self, keycode: int, action: int = 0):
        """Send a key press/release event."""
        key_cmd = _XDOTOOL_KEYS.get(keycode)
        if key_cmd:
            if "keyup" in key_cmd:
                if action == 0:   # DOWN
                    pass
                else:             # UP
                    self._run_input(key_cmd)
            else:
                self._run_input(key_cmd)
        else:
            # Fallback: try generic key event
            self._run_input(f"key {keycode}")

    def release_key(self, keycode: int):
        self.press_key(keycode, action=1)

    def key_press(self, keycode: int):
        """Send a complete key press (down + up)."""
        key_cmd = _XDOTOOL_KEYS.get(keycode)
        if key_cmd:
            if "keyup" not in key_cmd:
                self._run_input(key_cmd)
        else:
            self._run_input(f"key {keycode}")

    # ── Text input ─────────────────────────────────────────────────────────

    def inject_text(self, text: str):
        """Send a text string via xdotool type."""
        escaped = text.replace("'", "'\"'\"'")
        self._run_input(f"type '{escaped}'")

    # ── Touch / mouse events ──────────────────────────────────────────────

    def touch_down(
        self,
        widget_x: int, widget_y: int,
        widget_w: int, widget_h: int,
        pointer_id: int = 0,
        pressure: float = 1.0,
    ):
        pt = self._map_to_device(widget_x, widget_y, widget_w, widget_h)
        # --sync ensures mousedown fires AFTER the cursor reaches (x,y)
        self._run_input(f"mousemove --sync {pt.x} {pt.y} mousedown 1")

    def touch_move(
        self,
        widget_x: int, widget_y: int,
        widget_w: int, widget_h: int,
        pointer_id: int = 0,
        pressure: float = 1.0,
    ):
        pt = self._map_to_device(widget_x, widget_y, widget_w, widget_h)
        self._run_input(f"mousemove {pt.x} {pt.y}")

    def touch_up(
        self,
        widget_x: int, widget_y: int,
        widget_w: int, widget_h: int,
        pointer_id: int = 0,
    ):
        pt = self._map_to_device(widget_x, widget_y, widget_w, widget_h)
        self._run_input(f"mousemove --sync {pt.x} {pt.y} mouseup 1")

    def touch_cancel(self, pointer_id: int = 0):
        self._run_input("mouseup 1")

    # ── Scroll ─────────────────────────────────────────────────────────────

    def inject_scroll(
        self,
        widget_x: int, widget_y: int,
        widget_w: int, widget_h: int,
        h_scroll: int = 0,
        v_scroll: int = -1,
        pointer_id: int = 0,
    ):
        """Send scroll via xdotool click. Negative v_scroll = scroll up (towards user)."""
        pt = self._map_to_device(widget_x, widget_y, widget_w, widget_h)
        if v_scroll != 0:
            btn = "4" if v_scroll > 0 else "5"
            self._run_input(f"mousemove --sync {pt.x} {pt.y} click {btn}")
        if h_scroll != 0:
            btn = "6" if h_scroll > 0 else "7"
            self._run_input(f"mousemove --sync {pt.x} {pt.y} click {btn}")

    # ── Power / system ────────────────────────────────────────────────────

    def power_on(self):
        pass  # Linux desktop always on

    def power_off(self):
        pass

    def back_or_turn_screen_on(self):
        self._run_input("key Escape")

    def expand_notification_panel(self):
        self._run_input("key Super_L+a")

    def expand_settings_panel(self):
        pass

    def collapse_panels(self):
        pass

    # ── Clipboard ─────────────────────────────────────────────────────────

    def set_clipboard(self, text: str):
        escaped = text.replace("'", "'\"'\"'")
        self._run_input(f"set_clipboard '{escaped}'")

    def get_clipboard(self) -> str | None:
        return None

    # ── Device rotation ───────────────────────────────────────────────────

    def rotate_device(self):
        # Could rotate X11 display orientation if supported
        pass
