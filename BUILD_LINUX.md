# Linux Build

## Requirements

- Python 3.10+
- pip
- `pyinstaller`
- Linux desktop with X11 and a working display (`$DISPLAY` must be set)

## Dependencies

```bash
pip install PyQt6 Pillow psutil
pip install pyinstaller
```

## Build

```bash
pyinstaller screen_mirroring_linux.spec
```

The resulting binary is in `dist/screen_mirroring`.

## Dependencies on the Android/Linux device

The app connects to the device via `adb`. Make sure `adb` is in your PATH on the host machine.

For Android devices: no extra setup needed on the device.

For Linux devices: the following packages must be installed on the Linux device (accessible via `adb shell`):

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

## Run

```bash
./dist/screen_mirroring/screen_mirroring
```

Or install system-wide:
```bash
sudo cp dist/screen_mirroring/screen_mirroring /usr/local/bin/
```
