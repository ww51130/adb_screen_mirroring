# ADB Screen Mirroring

A cross-platform desktop app for mirroring Android and Linux device screens via ADB, built with Python + PyQt6 + scrcpy.

![License](https://img.shields.io/badge/license-MIT-blue.svg)

## Features

- **Android devices** — Mirrors screen via scrcpy over ADB (TCP/USB), supports up to 60 fps H.264 streaming
- **Linux devices** — Mirrors screen via GStreamer pipeline forwarded through ADB
- Device discovery via `adb devices`
- Screenshot capture and screen recording (Android only)
- Portrait / landscape rotation
- Mouse / keyboard input control (Android only)
- Cross-platform: Windows and Linux builds supported

## Requirements

- Python 3.10+
- [ADB](https://developer.android.com/studio/command-line/adb) installed and in `PATH`
- (Linux) GStreamer: `gstreamer1.0-tools`, `gstreamer1.0-plugins-base`, `gstreamer1.0-plugins-good`
- (Linux) `xdotool` (for future input support)

## Quick Start

```bash
pip install -r requirements.txt
python main.py
```

Select a device from the dropdown and click **Connect**.

## Building

### Windows

```bash
pip install pyinstaller
pyinstaller screen_mirroring.spec
```

The executable is at `dist/ScreenMirroring/ScreenMirroring.exe`.

### Linux

See [BUILD_LINUX.md](BUILD_LINUX.md) for full instructions.

```bash
pip install pyinstaller
pyinstaller screen_mirroring_linux.spec
# or: python build_linux.py
```

The binary is at `dist/screen_mirroring/screen_mirroring`.

## Project Structure

```
app/
  main_window.py          # Main window, device list, toolbar
  services/
    adb_manager.py        # ADB wrapper (devices, shell, pull, forward)
    android_capture.py    # scrcpy-based capture for Android
    linux_capture.py      # GStreamer-based capture for Linux
    capture_manager.py    # Dispatches to Android/Linux capture
    frame_receiver.py     # Receives & decodes JPEG frames over TCP
    scrcpy_manager.py     # Starts/stops scrcpy server
    screenshot_handler.py # Captures a single frame
    recording_handler.py  # Records screen to MP4 (Android only)
    linux_input_controller.py  # xdotool-based input (future)
  widgets/
    mirroring_canvas.py   # QWidget that displays the mirrored frame
  utils/
    config.py             # App settings via QSettings
    logging.py            # Logging setup
resources/
  scrcpy/                 # Bundled scrcpy server JAR
screen_mirroring.spec     # Windows PyInstaller spec
screen_mirroring_linux.spec
```

## License

MIT
