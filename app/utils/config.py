"""Application settings persisted via QSettings."""
import os
import sys
from pathlib import Path
from PyQt6.QtCore import QSettings


def get_app_data_dir() -> Path:
    """Cross-platform app data directory."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".local" / "share"
    return base / "ScreenMirroring"


def get_recordings_dir() -> Path:
    d = get_app_data_dir() / "recordings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_temp_screenshot_dir() -> Path:
    import tempfile
    d = Path(tempfile.gettempdir()) / "screen_mirroring"
    d.mkdir(parents=True, exist_ok=True)
    return d


class AppSettings:
    """Settings stored in platform-native config (QSettings)."""

    def __init__(self):
        self._qsettings = QSettings("ScreenMirroring", "ScreenMirroring")

    # Mirroring settings
    def get_max_size(self) -> int:
        return self._qsettings.value("mirroring/max_size", 1920, type=int)

    def set_max_size(self, v: int):
        self._qsettings.setValue("mirroring/max_size", v)

    def get_bit_rate(self) -> int:
        return self._qsettings.value("mirroring/bit_rate", 8_000_000, type=int)

    def set_bit_rate(self, v: int):
        self._qsettings.setValue("mirroring/bit_rate", v)

    def get_max_fps(self) -> int:
        return self._qsettings.value("mirroring/max_fps", 60, type=int)

    def set_max_fps(self, v: int):
        self._qsettings.setValue("mirroring/max_fps", v)

    def get_codec(self) -> str:
        return self._qsettings.value("mirroring/codec", "h264")

    def set_codec(self, v: str):
        self._qsettings.setValue("mirroring/codec", v)

    # Output settings
    def get_output_dir(self) -> Path:
        val = self._qsettings.value("output/recordings_dir")
        if val:
            p = Path(val)
            if p.exists() or p.parent.exists():
                return p
        return get_recordings_dir()

    def set_output_dir(self, v: Path):
        self._qsettings.setValue("output/recordings_dir", str(v))

    # Window settings
    def get_window_geometry(self) -> tuple | None:
        val = self._qsettings.value("window/geometry")
        if val:
            return val  # QByteArray
        return None

    def set_window_geometry(self, geometry: bytes):
        self._qsettings.setValue("window/geometry", geometry)
