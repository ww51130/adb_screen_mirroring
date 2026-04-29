#!/usr/bin/env python3
"""Cross-platform build helper — run this on Linux to build the binary.

Usage:
    python3 build_linux.py

Requirements (install via apt if missing):
    sudo apt install python3 python3-pip python3-dev libqt6gui6 libqt6widgets6

The script uses sys.executable (the Python running this script) so it works
correctly whether invoked as 'python3', 'python', or via an absolute path.
"""

import subprocess
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def run(cmd, **kwargs):
    print(f"\n$ {' '.join(cmd)}")
    r = subprocess.run(cmd, **kwargs)
    if r.returncode != 0:
        print(f"FAILED (exit {r.returncode})")
        sys.exit(r.returncode)
    print("OK")


def main():
    print("=== Screen Mirroring — Linux Build ===\n")

    # 1. Install runtime deps
    run([sys.executable, "-m", "pip", "install", "PyQt6", "Pillow", "psutil"])

    # 2. Install build tool
    run([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # 3. Build
    spec = os.path.join(HERE, "screen_mirroring_linux.spec")
    run([sys.executable, "-m", "PyInstaller", spec])

    print("\n=== Build complete ===")
    print("Binary: dist/screen_mirroring/screen_mirroring")


if __name__ == "__main__":
    main()
