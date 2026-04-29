"""Android device screen capture via ADB + scrcpy-server."""
import os
import socket
import struct
import threading
import logging
import time
from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

# Control port is one after the video port
_CTRL_PORT_OFFSET = 1


class AndroidCaptureError(Exception):
    """Android screen capture error."""
    pass


class AndroidCapture(QObject):
    """Captures screen from an Android device via ADB + scrcpy-server.

    Architecture:
    1. Push scrcpy-server.jar to /data/local/tmp/
    2. Start server via app_process / dalvikvm
    3. adb forward tcp:VIDEO_PORT localabstract:scrcpy
       adb forward tcp:CTRL_PORT  localabstract:scrcpy
    4. Read H264 frames from video socket, decode to QImage
    5. Send input events (touch, key, scroll) via control socket
    """

    DEFAULT_PORT = 27199

    frame_ready = pyqtSignal(object)
    fps_updated = pyqtSignal(float)
    error_occurred = pyqtSignal(str)
    connected = pyqtSignal(bool)
    device_meta = pyqtSignal(object)   # emit (width, height) when known
    input_ready = pyqtSignal(object)   # emit InputController when control socket is up

    def __init__(
        self,
        serial: str,
        adb_manager,
        on_frame=None,
        on_meta=None,
        on_error=None,
    ):
        super().__init__()
        self.serial = serial
        self._adb = adb_manager
        self._on_frame = on_frame
        self._on_meta = on_meta
        self._on_error = on_error

        self._video_port = self._derive_port(serial)
        self._ctrl_port = self._video_port + _CTRL_PORT_OFFSET
        self._running = False
        self._recv_thread: threading.Thread | None = None
        self._frame_count = 0

        # Control socket + input controller
        self._ctrl_sock: socket.socket | None = None
        self._ctrl_connected = False
        self._input_controller = None
        self._device_width = 1080
        self._device_height = 1920

    def _derive_port(self, serial: str) -> int:
        import hashlib
        h = int(hashlib.md5(serial.encode()).hexdigest()[:6], 16)
        return self.DEFAULT_PORT + (h % 10000)

    # ── scrcpy server ──────────────────────────────────────────────────

    def find_server(self) -> Path | None:
        """Find scrcpy-server JAR on disk."""
        import hashlib
        candidates = [
            "scrcpy-server", "scrcpy-server.jar",
            "scrcpy-server-arm64-v8a", "scrcpy-server-arm64-v8a.jar",
            "scrcpy-server-armeabi-v7a", "scrcpy-server-armeabi-v7a.jar",
            "scrcpy-server-x86_64", "scrcpy-server-x86_64.jar",
        ]
        base = Path(__file__).parent.parent.parent / "resources" / "scrcpy"
        for name in candidates:
            for ext in ("", ".jar"):
                path = base / f"{name}{ext}"
                if path.exists() and path.stat().st_size > 50_000:
                    logger.info(f"Found server: {path.name}")
                    return path
        return None

    def start(self, abi: str | None = None):
        """Start Android screen capture and control channel."""
        if self._running:
            return

        if abi is None:
            try:
                abi = self._adb.get_device_abi(self.serial)
            except Exception:
                pass

        server = self.find_server()
        if not server:
            raise AndroidCaptureError(
                "scrcpy-server not found. Please download scrcpy-server "
                "from https://github.com/Genymobile/scrcpy/releases and "
                "place it in resources/scrcpy/"
            )

        device_path = "/data/local/tmp/scrcpy-server.jar"

        # Push
        logger.info(f"Pushing {server.name} to device...")
        try:
            self._adb.push(self.serial, str(server), device_path, timeout=120)
        except Exception as e:
            raise AndroidCaptureError(f"Failed to push scrcpy-server: {e}")

        # Detect Java runtime
        java_cmd = self._detect_java_runtime()

        # Start server — enable control ( send_device_meta sends screen size info)
        scrcpy_opts = (
            "--max_size=1920 "
            "--bit_rate=8M "
            "--max_fps=60 "
            "--tunnel_forward "
            "--lock_video_orientation=-1 "
            "--send_device_meta=true "
            "--send_frame_meta=true "
        )
        server_cmd = (
            f"CLASSPATH={device_path} "
            f"{java_cmd} "
            f"/ com.genymobile.scrcpy.Server "
            f"{scrcpy_opts}"
        )

        logger.info("Starting scrcpy-server on device...")
        try:
            self._adb.shell(self.serial, server_cmd, timeout=10)
        except Exception as e:
            logger.warning(f"Server start (async): {e}")

        time.sleep(2.0)

        # Forward video port
        logger.info(f"Forwarding video port {self._video_port}...")
        forwarded = self._adb.forward(self.serial, self._video_port, "localabstract:scrcpy")
        if not forwarded:
            raise AndroidCaptureError(f"Failed to forward video port {self._video_port}.")

        # Forward control port (same abstract name = same server)
        logger.info(f"Forwarding control port {self._ctrl_port}...")
        forwarded_ctrl = self._adb.forward(self.serial, self._ctrl_port, f"localabstract:scrcpy\x00")
        if not forwarded_ctrl:
            logger.warning(f"Failed to forward control port {self._ctrl_port} — input may not work")

        # Connect control socket
        self._connect_control()

        # Start video receiver
        self._running = True
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()
        self.connected.emit(True)
        logger.info("Android screen capture started.")

    def _connect_control(self):
        """Connect the control socket to the scrcpy server."""
        try:
            self._ctrl_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._ctrl_sock.settimeout(5.0)
            self._ctrl_sock.connect(("127.0.0.1", self._ctrl_port))
            self._ctrl_connected = True
            logger.info(f"Control socket connected at 127.0.0.1:{self._ctrl_port}")
            # Import here to avoid circular dependency
            from app.services.input_controller import InputController, DeviceSize
            self._input_controller = InputController(
                serial=self.serial,
                sock=self._ctrl_sock,
                device_size=DeviceSize(self._device_width, self._device_height),
            )
            self.input_ready.emit(self._input_controller)
        except Exception as e:
            logger.warning(f"Could not connect control socket: {e}")
            self._ctrl_sock = None
            self._ctrl_connected = False
            self._input_controller = None

    @property
    def input_controller(self):
        """Return the InputController, or None if not connected."""
        return self._input_controller

    def update_device_size(self, width: int, height: int):
        """Update the device physical size for coordinate mapping."""
        self._device_width = width
        self._device_height = height
        if self._input_controller:
            self._input_controller.set_device_size(width, height)

    def _detect_java_runtime(self) -> str:
        """Detect the best available Java runtime on the Android device."""
        java_runtimes = [
            "app_process /",        # standard Android app_process
            "app_process64 /",       # 64-bit variant
            "dalvikvm -XX:+UseJIT -Xmx512m -classpath",  # Dalvik VM
        ]
        # Just return the default - most Android devices have app_process
        return "app_process /"

    # ── Frame receiver ────────────────────────────────────────────────

    def _recv_loop(self):
        """Connect to scrcpy server video socket and read frames.

        Protocol:
          1. Meta header first:
               [4B name_len][name_len bytes][4B type][4B val][4B type][4B val]
             then zero or more frames.
          2. Each frame: [4B length][1B type][payload]
        """
        buf = b""
        meta_parsed = False

        while self._running:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10.0)
                sock.connect(("127.0.0.1", self._video_port))
                logger.info(f"Connected to Android stream at 127.0.0.1:{self._video_port}")

                while self._running:
                    chunk = sock.recv(131072)
                    if not chunk:
                        break
                    buf += chunk

                    if not meta_parsed:
                        if len(buf) < 12:
                            continue

                        name_len = struct.unpack_from(">I", buf, 0)[0]
                        if name_len > 512:
                            # Garbage — skip one byte and retry
                            buf = buf[1:]
                            continue

                        required = 4 + name_len + 8
                        if len(buf) < required:
                            continue

                        name_bytes = buf[4:4 + name_len]
                        meta_vals = struct.unpack_from(">IIII", buf, 4 + name_len)
                        dev_name = name_bytes.decode("utf-8", errors="replace")
                        w = meta_vals[1]
                        h = meta_vals[3]
                        logger.info(f"Device meta: name={dev_name}, {w}x{h}")
                        self._device_width = w
                        self._device_height = h
                        if self._input_controller:
                            self._input_controller.set_device_size(w, h)
                        self.device_meta.emit((w, h))
                        buf = buf[required:]
                        meta_parsed = True
                        continue

                    # Parse frames: [4B length][1B type][payload]
                    while len(buf) >= 5:
                        frame_len = struct.unpack(">I", buf[:4])[0]
                        if len(buf) < 4 + frame_len:
                            break

                        frame_data = buf[4:4 + frame_len]
                        buf = buf[4 + frame_len:]

                        if frame_data and self._on_frame:
                            frame_type = frame_data[0] if frame_data else 0
                            payload = frame_data[1:]
                            self._on_frame(payload, frame_type)
                            self._frame_count += 1

            except socket.timeout:
                continue
            except OSError as e:
                if self._running:
                    logger.debug(f"Reconnecting: {e}")
                    try:
                        sock.close()
                    except Exception:
                        pass
                    time.sleep(1.0)
                    meta_parsed = False
                    continue
                break
            except Exception as e:
                logger.error(f"Frame receiver error: {e}")
                if self._on_error:
                    self._on_error(str(e))
                break

        try:
            sock.close()
        except Exception:
            pass

    async def stop(self):
        """Stop Android screen capture."""
        logger.info("Stopping Android screen capture...")
        self._running = False

        if self._recv_thread and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=3.0)

        # Close control socket
        if self._ctrl_sock:
            try:
                self._ctrl_sock.close()
            except Exception:
                pass
            self._ctrl_sock = None

        # Remove forwards
        try:
            self._adb.forward_remove(self._video_port)
        except Exception:
            pass
        try:
            self._adb.forward_remove(self._ctrl_port)
        except Exception:
            pass

        try:
            self._adb.shell(self.serial, "pkill -f scrcpy", timeout=5)
        except Exception:
            pass

        self.connected.emit(False)
        logger.info("Android screen capture stopped.")
