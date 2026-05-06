"""Screen recording via ADB screenrecord."""
import subprocess
import logging
import time
from pathlib import Path
from datetime import datetime
from PyQt6.QtCore import QObject, pyqtSignal, QTimer

logger = logging.getLogger(__name__)


class RecordingHandler(QObject):
    """Manages screen recording via ADB screenrecord."""

    recording_started = pyqtSignal()
    recording_stopped = pyqtSignal(str, float)  # path, duration_seconds
    recording_error = pyqtSignal(str)
    duration_updated = pyqtSignal(float)  # seconds

    MAX_DURATION = 300  # seconds (5 min)

    def __init__(self, serial: str, adb_manager, output_dir: Path, parent=None):
        super().__init__(parent)
        self.serial = serial
        self._adb = adb_manager
        self._output_dir = output_dir
        self._device_path = "/sdcard/recording.mp4"
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
            # Check if screenrecord exists on the device.
            # Avoids 'which' (not on all Android versions); directly checks known paths.
            check = self._adb.shell(
                self.serial,
                "ls /system/bin/screenrecord /system/xbin/screenrecord /vendor/bin/screenrecord 2>&1",
                timeout=10,
            )
            # ls prints each found path; 'No such file' for each missing one
            logger.debug(f"screenrecord locations check: {check!r}")
            if "No such file" in check or not check.strip():
                msg = (
                    "screenrecord not found on the device. "
                    "This feature requires Android 4.4+ (KitKat) with screenrecord "
                    "at /system/bin/screenrecord. Your device may not support it, "
                    "or you may need a custom ROM."
                )
                logger.error(msg)
                self.recording_error.emit(msg)
                return False

            # 2. Clean up any stale file from a previous session
            try:
                self._adb.shell(self.serial, f"rm -f {self._device_path}", timeout=5)
            except Exception:
                pass

            # 3. Start screenrecord via adb shell (device manages the process)
            #    Use > /dev/null 2>&1 to detach from stdout/stderr on device side.
            #    We keep the adb shell pipe open to detect disconnects.
            screenrecord_cmd = (
                f"screenrecord --time-limit={self.MAX_DURATION // 60} "
                f"--bit-rate=8000000 {self._device_path}"
            )
            proc = subprocess.Popen(
                [
                    self._adb.find_adb(),
                    "-s", self.serial,
                    "shell", screenrecord_cmd,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                ),
            )
            self._proc = proc

            # 4. Wait briefly for the file to appear (confirms screenrecord started)
            for i in range(10):
                time.sleep(0.3)
                try:
                    out = self._adb.shell(self.serial, f"ls {self._device_path}", timeout=3)
                    if out.strip():
                        break
                except Exception:
                    pass
            else:
                # Capture stderr for diagnostics before failing
                proc.terminate()
                try:
                    _, stderr = proc.communicate(timeout=3)
                    stderr_text = stderr.decode("utf-8", errors="replace").strip()
                except Exception:
                    stderr_text = "(unable to read stderr)"
                msg = (
                    f"screenrecord failed to start. "
                    f"Device stderr: {stderr_text!r}"
                )
                logger.error(msg)
                self.recording_error.emit(
                    "Recording start failed. Device may not support screenrecord "
                    "(requires Android 4.4+)."
                )
                return False

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

        # Sync + wait for filesystem to flush
        try:
            self._adb.shell(self.serial, "sync", timeout=5)
        except Exception:
            pass
        time.sleep(1.0)

        try:
            # Verify file exists before pulling
            check = self._adb.shell(
                self.serial, f"ls -la {self._device_path}", timeout=5
            )
            if not check.strip():
                raise FileNotFoundError(
                    f"Recording file not found on device at {self._device_path}"
                )
            logger.debug(f"Device file: {check!r}")

            # Pull
            self._adb.pull(
                self.serial,
                self._device_path,
                str(self._local_path),
                timeout=120,
            )

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

        except FileNotFoundError:
            logger.exception("Recording stop failed")
            self.recording_error.emit(
                "Recording file not found. Make sure the device has enough storage."
            )
            return None
        except Exception as e:
            logger.exception("Recording stop failed")
            self.recording_error.emit(str(e))
            return None
        finally:
            # Clean up the host-side subprocess pipe
            proc = getattr(self, "_proc", None)
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    pass
                self._proc = None
