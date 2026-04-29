#!/usr/bin/env python3
"""Cross-platform build helper — run this on Linux to build the binary."""

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
