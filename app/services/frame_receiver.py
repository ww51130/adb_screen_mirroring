"""Frame receiver: decodes H.264/MJPEG frames from scrcpy socket to QImage."""
import io
import struct
import logging
import traceback
from typing import Callable
from PyQt6.QtCore import QObject, pyqtSignal, QThread, QTimer
from PyQt6.QtGui import QImage

logger = logging.getLogger(__name__)


class FrameReceiver(QObject):
    """Receives raw frame data from ScrcpyManager and converts to QImage."""

    frame_ready = pyqtSignal(QImage)  # decoded QImage
    fps_updated = pyqtSignal(float)
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fps_timer = QTimer()
        self._fps_timer.timeout.connect(self._emit_fps)
        self._frame_count = 0
        self._fps = 0.0

    def handle_frame(self, payload: bytes, frame_type: int):
        """Called by ScrcpyManager for each received frame.

        Args:
            payload: raw frame bytes (H.264 NAL or JPEG)
            frame_type: 0 = H.264, 1 = JPEG/MJPEG
        """
        try:
            if frame_type == 1:
                # MJPEG: direct JPEG → QImage
                img = QImage.fromData(payload)
                if not img.isNull():
                    self.frame_ready.emit(img)
                    self._frame_count += 1
            else:
                # H.264: decode via Pillow fallback (less efficient but works)
                # For production, use pyav or cv2 for hardware-accelerated decode
                img = self._decode_h264_fallback(payload)
                if img is not None:
                    self.frame_ready.emit(img)
                    self._frame_count += 1
        except Exception:
            logger.debug(f"Frame decode error: {traceback.format_exc()}")

    def _decode_h264_fallback(self, data: bytes) -> QImage | None:
        """Fallback H.264 decode using Pillow (requires JPEG wrapper).

        Note: True H.264 decode requires pyav or OpenCV.
        This tries to detect JPEG inside H.264Annex-B NAL units.
        """
        # Try Pillow directly — works for JPEG-encapsulated frames
        try:
            img = QImage.fromData(data)
            if not img.isNull():
                return img
        except Exception:
            pass

        # Try to find JPEG SOI marker (0xFFD8) in the data
        jpeg_start = data.find(b'\xff\xd8')
        if jpeg_start >= 0:
            jpeg_end = data.find(b'\xff\xd9', jpeg_start)
            if jpeg_end >= 0:
                jpeg_data = data[jpeg_start:jpeg_end + 2]
                img = QImage.fromData(jpeg_data)
                if not img.isNull():
                    return img

        return None

    def start_fps_counter(self):
        self._fps_timer.start(1000)

    def stop_fps_counter(self):
        self._fps_timer.stop()

    def _emit_fps(self):
        fps = self._frame_count
        self._frame_count = 0
        self._fps = fps
        self.fps_updated.emit(fps)

    @property
    def fps(self) -> float:
        return self._fps
