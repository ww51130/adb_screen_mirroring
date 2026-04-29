# Linux Build

## Requirements (install via apt if missing)

```bash
sudo apt install python3 python3-pip python3-dev \
  libqt6gui6 libqt6widgets6 libqt6core6 \
  libegl1 libxkbcommon0 libdbus-1-3
```

## Build

### Option 1: Automated (recommended)

```bash
python3 build_linux.py
```

### Option 2: Manual

```bash
pip3 install PyQt6 Pillow psutil pyinstaller
python3 -m PyInstaller screen_mirroring_linux.spec
```

The resulting binary is in `dist/screen_mirroring/screen_mirroring`.

## Run

```bash
./dist/screen_mirroring/screen_mirroring
```

Or install system-wide:
```bash
sudo cp dist/screen_mirroring/screen_mirroring /usr/local/bin/
```

## Device Dependencies

The app connects to the device via `adb`. Make sure `adb` is in your PATH on the host machine.

**Android devices** — no extra setup needed on the device.

**Linux devices** — the following packages must be installed on the Linux device (accessible via `adb shell`):

```bash
sudo apt install gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good xdotool
```

Required GStreamer elements: `ximagesrc`, `jpegenc`, `videoconvert`, `tcpserversink`.

Verify:
```bash
gst-inspect-1.0 ximagesrc
gst-inspect-1.0 jpegenc
```
