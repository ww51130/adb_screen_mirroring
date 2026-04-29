# scrcpy Server Binaries

This folder should contain the scrcpy server JAR file(s).

## Download

1. Go to: https://github.com/Genymobile/scrcpy/releases
2. Download the latest release (e.g. `scrcpy-win64-v3.x.zip`)
3. Extract the zip — inside you'll find `scrcpy-server`
4. Rename it to one of the filenames listed below and place it here.

## Required Files

| ABI | Filename | Notes |
|-----|----------|-------|
| `arm64-v8a` | `scrcpy-server-arm64-v8a.jar` | Most modern phones |
| `armeabi-v7a` | `scrcpy-server-armeabi-v7a.jar` | Older 32-bit phones |
| `x86_64` | `scrcpy-server-x86_64.jar` | Emulators, Chromebooks |
| `x86` | `scrcpy-server-x86.jar` | Legacy emulators |

The app will automatically detect your device's ABI and use the matching file.

## Size

Each server JAR is typically 80-120 KB.
