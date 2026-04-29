"""Screen recording via ADB screenrecord."""
import subprocess
import logging
import time
from pathlib import Path
from datetime import datetime
from PyQt6.QtCore import QThread, pyqtSignal, QObject
from PyQt6.QtCore import QTimer

logger = logging.getLogger(__name__)


class RecordingHandler(QObject):
    """Manages screen recording via ADB screenrecord."""

    recording_started = pyqtSignal()
    recording_stopped = pyqtSignal(str, float)  # path, duration_seconds
    recording_error = pyqtSignal(str)
    duration_updated = pyqtSignal(float)  # seconds

    # Max recording time in seconds (5 min default)
    MAX_DURATION = 300

    def __init__(self, serial: str, adb_manager, output_dir: Path, parent=None):
        super().__init__(parent)
        self.serial = serial
        self._adb = adb_manager
        self._output_dir = output_dir
        self._device_path = "/sdcard/recording.mp4"
        self._process: subprocess.Popen | None = None
        self._is_recording = False
        self._start_time: float = 0.0
        self._timer: QTimer | None = None

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    def start(self) -> bool:
        """Start recording. Returns True on success."""
        if self._is_recording:
            logger.warning("Already recording.")
            return False

        self._output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"recording__{self.serial.replace(':', '_')}___{timestamp}.mp4"
        self._local_path = self._output_dir / filename

        try:
            # Start screenrecord in background
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            proc = subprocess.Popen(
                [
                    self._adb.find_adb(),
                    "-s", self.serial,
                    "shell", "screenrecord",
                    f"--time-limit={self.MAX_DURATION // 60}",
                    "--bit-rate=8000000",
                    self._device_path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )
            self._process = proc
            self._is_recording = True
            self._start_time = time.time()

            # Duration timer
            self._timer = QTimer()
            self._timer.timeout.connect(self._on_duration_tick)
            self._timer.start(500)

            self.recording_started.emit()
            logger.info("Recording started.")
            return True

        except Exception as e:
            logger.exception("Failed to start recording")
            self._is_recording = False
            self.recording_error.emit(str(e))
            return False

    def _on_duration_tick(self):
        if self._is_recording:
            elapsed = time.time() - self._start_time
            self.duration_updated.emit(elapsed)

            # Auto-stop at max duration
            if elapsed >= self.MAX_DURATION:
                logger.info("Max recording duration reached.")
                self.stop()

    def stop(self) -> str | None:
        """Stop recording and save file. Returns saved path or None."""
        if not self._is_recording:
            return None

        duration = time.time() - self._start_time
        self._is_recording = False

        if self._timer:
            self._timer.stop()
            self._timer = None

        try:
            # Kill screenrecord on device
            self._adb.shell(self.serial, "pkill screenrecord", timeout=5)
        except Exception as e:
            logger.warning(f"pkill screenrecord failed: {e}")

        # Give it a moment to write the file
        time.sleep(0.5)

        try:
            # Pull file
            self._adb.pull(self.serial, self._device_path, str(self._local_path), timeout=120)

            # Cleanup device file
            try:
                self._adb.shell(self.serial, f"rm {self._device_path}", timeout=5)
            except Exception:
                pass

            if self._local_path.exists():
                self.recording_stopped.emit(str(self._local_path), duration)
                logger.info(f"Recording saved: {self._local_path}")
                return str(self._local_path)
            else:
                raise FileNotFoundError("Recording file not found after pull.")

        except Exception as e:
            logger.exception("Recording stop failed")
            self.recording_error.emit(str(e))
            return None
