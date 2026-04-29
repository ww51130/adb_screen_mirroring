"""Linux-native screen capture via GStreamer + H264 streaming over adb reverse."""
import os
import re
import socket
import struct
import threading
import logging
import time
import subprocess
import io
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from PyQt6.QtGui import QImage

from app.services.linux_input_controller import LinuxInputController, DeviceSize
logger = logging.getLogger(__name__)


@dataclass
class LinuxDeviceInfo:
    display: str = ":0"
    resolution: tuple[int, int] = (1920, 1080)
    has_hardware_encoder: bool = False
    has_ximagesrc: bool = False
    has_mpph264enc: bool = False


class LinuxCaptureError(Exception):
    """Linux screen capture error."""
    pass


class LinuxScreenCapture(QObject):
    """Captures screen from a Linux device via ADB + GStreamer pipeline.

    Architecture:
    1. adb reverse tcp:PORT -> device tcp:PORT  (tunnel)
    2. Device: GStreamer ximagesrc -> H264 encoder -> tcpserversink (device-side TCP server)
    3. Host: read raw H264 from 127.0.0.1:PORT, decode, emit QImage frames
    """

    PORT_BASE = 58000

    frame_ready = pyqtSignal(QImage)
    fps_updated = pyqtSignal(float)
    error_occurred = pyqtSignal(str)
    connected = pyqtSignal(bool)
    input_ready = pyqtSignal(object)   # LinuxInputController when control is ready
    device_meta = pyqtSignal(tuple)     # (width, height) when resolution is known

    def __init__(
        self,
        serial: str,
        adb_manager,
        on_frame: Callable[[bytes, int], None] | None = None,
        on_meta: Callable | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        super().__init__()
        self.serial = serial
        self._adb = adb_manager
        self._on_frame = on_frame
        self._on_meta = on_meta
        self._on_error = on_error

        self._device_info = LinuxDeviceInfo()
        self._port = self._derive_port(serial)
        self._running = False
        self._recv_thread: threading.Thread | None = None
        self._gst_proc: subprocess.Popen | None = None
        self._frame_count = 0
        self._fps = 0.0
        self._device_width = 1920
        self._device_height = 1080
        self._input_controller: LinuxInputController | None = None

    def _derive_port(self, serial: str) -> int:
        import hashlib
        h = int(hashlib.md5(serial.encode()).hexdigest()[:6], 16)
        return self.PORT_BASE + (h % 1000)

    # ── Device detection ─────────────────────────────────────────────

    def detect_device(self) -> LinuxDeviceInfo:
        """Detect Linux device capabilities."""
        info = LinuxDeviceInfo()

        # Check display
        try:
            out = self._adb.shell(self.serial, "echo $DISPLAY").strip()
            info.display = out if out else ":0"
        except Exception:
            info.display = ":0"

        # Check resolution
        try:
            out = self._adb.shell(self.serial, "xrandr 2>/dev/null | grep -i screen").strip()
            m = re.search(r'(\d+)x(\d+)', out)
            if m:
                info.resolution = (int(m.group(1)), int(m.group(2)))
        except Exception:
            pass

        # Check GStreamer elements
        for element in ["ximagesrc", "ximagesink"]:
            try:
                out = self._adb.shell(self.serial, f"gst-inspect-1.0 {element} 2>/dev/null").strip()
                if element in out:
                    setattr(info, f"has_{element.replace('-', '_')}", True)
            except Exception:
                pass

        # Check hardware encoder
        for encoder in ["mpph264enc", "openh264enc", "x264enc"]:
            try:
                out = self._adb.shell(self.serial, f"gst-inspect-1.0 {encoder} 2>/dev/null").strip()
                if encoder in out:
                    info.has_hardware_encoder = True
                    info.has_mpph264enc = (encoder == "mpph264enc")
                    break
            except Exception:
                pass

        logger.info(f"Linux device info: {info}")
        return info

    # ── Capture ────────────────────────────────────────────────────────

    def start(self):
        """Start capture. Raises LinuxCaptureError on failure."""
        if self._running:
            return

        # Step 1: Detect capabilities
        self._device_info = self.detect_device()

        # Step 2: Set up adb reverse tunnel
        logger.info(f"Setting up adb reverse on port {self._port}...")
        try:
            self._adb._run(["reverse", "--remove", f"tcp:{self._port}"], timeout=5)
        except Exception:
            pass

        try:
            ok = self._adb.forward(self.serial, self._port, f"tcp:{self._port}")
            if not ok:
                raise LinuxCaptureError(f"Failed to set up adb reverse on port {self._port}")
        except Exception as e:
            raise LinuxCaptureError(f"adb reverse failed: {e}")

        # Step 3: Start GStreamer pipeline on device
        logger.info("[start] Starting GStreamer pipeline...")
        self._start_gstreamer_pipeline()
        logger.info("[start] GStreamer pipeline started, about to start recv thread...")

        # Step 4: Start receiving frames
        self._running = True
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()

        # Step 5: Create input controller for Linux device
        w, h = self._device_info.resolution
        self._device_width = w
        self._device_height = h
        self._input_controller = LinuxInputController(
            serial=self.serial,
            adb_manager=self._adb,
            device_size=DeviceSize(w, h),
        )
        self.input_ready.emit(self._input_controller)
        self.device_meta.emit((w, h))
        self.connected.emit(True)
        logger.info("Linux screen capture started.")

    def _build_pipeline(self) -> str:
        """Build GStreamer pipeline string for the device.

        Uses JPEG encoding + TCP streaming — frames are directly decodable
        by QImage.fromData() without extra codec setup.
        """
        disp = self._device_info.display

        # pipeline: ximagesrc (raw RGB) -> jpegenc (compress) -> tcpserversink (TCP stream)
        # tcpserversink sends raw JPEG bytes which we parse by SOI/EOI markers
        pipeline = (
            f"ximagesrc display-name={disp} show-pointer=false ! "
            f"video/x-raw,framerate=30/1 ! "
            f"videoconvert ! "
            f"jpegenc quality=85 ! "
            f"tcpserversink host=127.0.0.1 port={self._port} sync=false"
        )
        return pipeline

    def _start_gstreamer_pipeline(self):
        """Start GStreamer pipeline as a detached background process on device.

        Uses setsid to create a new session — much more reliable than nohup &
        in ADB shell context.
        """
        pipeline = self._build_pipeline()
        disp = self._device_info.display

        # Kill any stale GStreamer processes from previous runs
        try:
            self._adb.shell(self.serial, "pkill -f 'gst-launch.*ximagesrc' 2>/dev/null || true", timeout=5)
        except Exception:
            pass

        # setsid creates a new session so the process survives ADB shell disconnect
        # Redirect stdin from /dev/null to prevent blocking on tty input
        cmd = (
            f"export DISPLAY={disp}; "
            f"setsid gst-launch-1.0 {pipeline} "
            f"> /tmp/gst_capture.log 2>&1 < /dev/null & "
            f"echo STARTED"
        )

        logger.info(f"Starting GStreamer pipeline via setsid...")

        try:
            # setsid returns immediately; give it a moment
            self._adb.shell(self.serial, cmd, timeout=3)

            # Poll log until PLAYING or ERROR
            for attempt in range(15):
                time.sleep(0.5)
                log = self._adb.shell(self.serial, "cat /tmp/gst_capture.log 2>/dev/null").strip()
                if "PLAYING" in log or "PREROLL" in log:
                    logger.info(f"GStreamer pipeline running (took {attempt * 0.5:.1f}s)")
                    return
                if "ERROR" in log:
                    raise LinuxCaptureError(f"GStreamer error: {log}")
                if "cannot link" in log.lower() or "not found" in log.lower():
                    raise LinuxCaptureError(f"GStreamer link error: {log}")

            # If pipeline started but log hasn't shown PLAYING yet, give it more time
            log = self._adb.shell(self.serial, "cat /tmp/gst_capture.log 2>/dev/null").strip()
            if "ERROR" in log:
                raise LinuxCaptureError(f"GStreamer error: {log}")
            logger.info(f"Pipeline log: {log[:200]}")

        except LinuxCaptureError:
            raise
        except Exception as e:
            raise LinuxCaptureError(f"Failed to start GStreamer pipeline: {e}")

    # ── Frame receiver ────────────────────────────────────────────────

    def _recv_loop(self):
        """Connect to device's TCP server and receive JPEG frames.

        tcpserversink sends raw JPEG bytes. We find SOI (0xFFD8FF) and
        EOI (0xFFD9) markers to extract complete frames.
        """
        buf = b""

        while self._running:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10.0)
                sock.connect(("127.0.0.1", self._port))
                logger.info(f"Connected to MJPEG stream at 127.0.0.1:{self._port}")

                while self._running:
                    try:
                        data = sock.recv(65536)
                        if not data:
                            break
                        buf += data

                        # Extract complete JPEG frames
                        while True:
                            soi = buf.find(b'\xff\xd8')
                            if soi < 0:
                                # No SOI marker, discard old buffer
                                buf = b""
                                break

                            eoi = buf.find(b'\xff\xd9', soi + 2)
                            if eoi < 0:
                                # Incomplete frame, keep what we have
                                if soi > 0:
                                    buf = buf[soi:]
                                break

                            jpeg = buf[soi:eoi + 2]
                            buf = buf[eoi + 2:]

                            if self._on_frame and len(jpeg) > 100:
                                self._on_frame(jpeg, 1)  # type=1 = JPEG
                                self._frame_count += 1

                    except socket.timeout:
                        continue
                    except Exception as e:
                        logger.debug(f"Socket read error: {e}")
                        break

                sock.close()

            except OSError as e:
                if self._running:
                    logger.debug(f"TCP connect failed, retrying: {e}")
                    time.sleep(1.0)
                    continue
                break
            except Exception as e:
                if self._running:
                    logger.error(f"Frame receiver error: {e}")
                    self.error_occurred.emit(str(e))
                    time.sleep(1.0)
                    continue
                break

    async def stop(self):
        """Stop capture and clean up."""
        logger.info("Stopping Linux screen capture...")
        self._running = False

        if self._recv_thread and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=3.0)

        # Kill GStreamer pipeline
        try:
            self._adb.shell(self.serial, "pkill -f 'gst-launch.*ximagesrc' 2>/dev/null || true", timeout=5)
        except Exception:
            pass

        # Remove adb reverse
        try:
            self._adb.forward_remove(self._port)
        except Exception:
            pass

        self.connected.emit(False)
        logger.info("Linux screen capture stopped.")

    @property
    def device_info(self) -> LinuxDeviceInfo:
        return self._device_info
