"""Unified screen capture manager — auto-detects Android vs Linux device."""
import re
import logging
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage
from app.services.frame_receiver import FrameReceiver

logger = logging.getLogger(__name__)


class CaptureManager(QObject):
    """Manages screen capture, auto-detecting device type.

    IMPORTANT: This object must be created in a thread with a Qt event loop
    (i.e. the main thread). The internal FrameReceiver lives here too so
    queued cross-thread signals from the capture's recv daemon thread are
    delivered correctly.
    """

    DEVICE_TYPE_UNKNOWN = "unknown"
    DEVICE_TYPE_ANDROID = "android"
    DEVICE_TYPE_LINUX   = "linux"

    frame_ready  = pyqtSignal(QImage)   # decoded QImage
    fps_updated  = pyqtSignal(float)
    error_occurred = pyqtSignal(str)
    connected    = pyqtSignal(bool)
    device_type_detected = pyqtSignal(str)  # emit device type when detected
    device_meta = pyqtSignal(tuple)          # (width, height) when known
    input_ready = pyqtSignal(object)          # InputController when control socket is up

    def __init__(self, serial: str, adb_manager):
        super().__init__()
        self.serial = serial
        self._adb = adb_manager
        self._capture = None          # AndroidCapture or LinuxScreenCapture
        self._device_type = self.DEVICE_TYPE_UNKNOWN
        self._pending_frame_cb = None  # stored callback from ScrcpyManager-style init

        # Create FrameReceiver here (main thread) so its signals are
        # processed by the main event loop, even when handle_frame() is
        # called from the capture's recv daemon thread.
        self._frame_receiver = FrameReceiver()
        self._frame_receiver.frame_ready.connect(self._on_frame_received)
        self._frame_receiver.fps_updated.connect(self.fps_updated)

    def _on_frame_received(self, qimage):
        """Bridge slot to convert FrameReceiver signal type."""
        self.frame_ready.emit(qimage)

    # ── Device type detection ─────────────────────────────────────────

    def detect_device_type(self) -> str:
        """Probe the device to determine if it's Android or Linux."""
        try:
            out = self._adb.shell(self.serial, "getprop ro.build.version.sdk").strip()
            if out and out.isdigit():
                logger.info(f"Device {self.serial}: Android SDK={out}")
                return self.DEVICE_TYPE_ANDROID
        except Exception:
            pass

        try:
            out = self._adb.shell(self.serial, "ls /system/bin/screencap").strip()
            if "No such file" not in out and out:
                logger.info(f"Device {self.serial}: has screencap -> Android")
                return self.DEVICE_TYPE_ANDROID
        except Exception:
            pass

        try:
            out = self._adb.shell(self.serial, "gst-inspect-1.0 ximagesrc 2>/dev/null").strip()
            if "ximagesrc" in out:
                logger.info(f"Device {self.serial}: has ximagesrc -> Linux")
                return self.DEVICE_TYPE_LINUX
        except Exception:
            pass

        try:
            out = self._adb.shell(self.serial, "ls /bin/bash 2>/dev/null").strip()
            if out and "bash" in out:
                logger.info(f"Device {self.serial}: has /bin/bash -> Linux")
                return self.DEVICE_TYPE_LINUX
        except Exception:
            pass

        try:
            out = self._adb.shell(self.serial, "cat /proc/version 2>/dev/null").strip()
            if "Linux" in out or "kernel" in out.lower():
                logger.info(f"Device {self.serial}: /proc/version says Linux")
                return self.DEVICE_TYPE_LINUX
        except Exception:
            pass

        logger.warning(f"Device {self.serial}: unknown type, defaulting to Android")
        return self.DEVICE_TYPE_ANDROID

    # ── Capture lifecycle ─────────────────────────────────────────────

    def start(self):
        """Detect type and start appropriate capture."""
        self._device_type = self.detect_device_type()
        self.device_type_detected.emit(self._device_type)

        if self._device_type == self.DEVICE_TYPE_LINUX:
            self._start_linux()
        else:
            self._start_android()

    def _start_android(self):
        from app.services.android_capture import AndroidCapture

        self._capture = AndroidCapture(
            serial=self.serial,
            adb_manager=self._adb,
            on_frame=self._frame_receiver.handle_frame,
            on_error=lambda e: self.error_occurred.emit(e),
        )
        self._capture.connected.connect(self.connected)
        self._capture.device_meta.connect(self._on_android_meta)
        self._capture.input_ready.connect(self.input_ready)

        try:
            self._capture.start()
            self._frame_receiver.start_fps_counter()
        except Exception as e:
            logger.error(f"Android capture start failed: {e}")
            raise

    def _on_android_meta(self, size_tuple: tuple):
        """Forward device screen size from AndroidCapture to MainWindow."""
        self.device_meta.emit(size_tuple)

    def _start_linux(self):
        from app.services.linux_capture import LinuxScreenCapture

        self._capture = LinuxScreenCapture(
            serial=self.serial,
            adb_manager=self._adb,
            on_frame=self._frame_receiver.handle_frame,
            on_error=lambda e: self.error_occurred.emit(e),
        )
        self._capture.connected.connect(self.connected)
        # LinuxInputController is created inside LinuxScreenCapture for future use,
        # but input control is disabled in the UI for now.
        # TODO: enable input control once a non-conflicting approach is available
        # self._capture.input_ready.connect(self.input_ready)
        self._capture.device_meta.connect(self.device_meta)

        try:
            self._capture.start()
            self._frame_receiver.start_fps_counter()
        except Exception as e:
            logger.error(f"Linux capture start failed: {e}")
            raise

    async def stop(self):
        """Stop capture."""
        if self._capture:
            await self._capture.stop()
            self._capture = None
        if self._frame_receiver:
            self._frame_receiver.stop_fps_counter()

    @property
    def device_type(self) -> str:
        return self._device_type
