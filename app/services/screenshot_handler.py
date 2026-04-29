"""Screenshot capture via ADB."""
import subprocess
import logging
import tempfile
from pathlib import Path
from datetime import datetime
from PyQt6.QtCore import QThread, pyqtSignal, QObject
from PyQt6.QtGui import QPixmap

logger = logging.getLogger(__name__)


class ScreenshotHandler(QObject):
    """Captures a screenshot from an ADB device."""

    screenshot_ready = pyqtSignal(QPixmap, str)  # pixmap, timestamp
    screenshot_error = pyqtSignal(str)           # error message

    def __init__(self, serial: str, adb_manager, parent=None):
        super().__init__(parent)
        self.serial = serial
        self._adb = adb_manager
        self._device_path = "/sdcard/screen.png"

    def capture(self, output_dir: Path | None = None) -> QPixmap | None:
        """Capture screenshot synchronously. Returns QPixmap or None on error."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = f"__{self.serial.replace(':', '_')}___{timestamp}.png"

        try:
            temp_dir = output_dir or Path(tempfile.gettempdir()) / "screen_mirroring"
            temp_dir.mkdir(parents=True, exist_ok=True)
            local_path = temp_dir / f"screenshot{suffix}"

            device_path = self._device_path

            # Step 1: Take screenshot on device
            logger.info("Taking screenshot...")
            self._adb.shell(self.serial, f"screencap -p {device_path}", timeout=10)

            # Step 2: Pull to local
            logger.info("Pulling screenshot...")
            self._adb.pull(self.serial, device_path, str(local_path), timeout=30)

            # Step 3: Delete device file
            try:
                self._adb.shell(self.serial, f"rm {device_path}", timeout=5)
            except Exception as e:
                logger.warning(f"Failed to cleanup device screenshot: {e}")

            # Step 4: Load into QPixmap
            if not local_path.exists():
                raise FileNotFoundError("Pulled screenshot file not found.")

            pixmap = QPixmap(str(local_path))
            local_path.unlink(missing_ok=True)

            if pixmap.isNull():
                raise ValueError("Screenshot is null (possibly empty screen).")

            self.screenshot_ready.emit(pixmap, timestamp)
            return pixmap

        except Exception as e:
            logger.exception("Screenshot failed")
            self.screenshot_error.emit(str(e))
            return None

    def save_to_file(self, pixmap: QPixmap, path: Path) -> bool:
        """Save pixmap to a file. Returns True on success."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return pixmap.save(str(path))
        except Exception as e:
            logger.error(f"Failed to save screenshot: {e}")
            return False
