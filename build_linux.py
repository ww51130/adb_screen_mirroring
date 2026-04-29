#!/usr/bin/env python3
"""Cross-platform build helper — run this on Linux to build the binary.

Usage:
    python3 build_linux.py

Requirements (install via apt if missing):
    sudo apt install python3 python3-pip python3-venv python3-dev \
      libqt6gui6 libqt6widgets6 libqt6core6 \
      libegl1 libxkbcommon0 libdbus-1-3
"""

import subprocess
import sys
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(HERE, ".venv_linux_build")


def find_python():
    """Pick python3, falling back to sys.executable."""
    for cmd in ["python3", "python", sys.executable]:
        if shutil.which(cmd):
            return cmd
    return sys.executable


def run(cmd, **kwargs):
    print(f"\n$ {' '.join(cmd)}")
    r = subprocess.run(cmd, **kwargs)
    if r.returncode != 0:
        print(f"FAILED (exit {r.returncode})")
        sys.exit(r.returncode)
    print("OK")


def main():
    print("=== Screen Mirroring — Linux Build ===\n")

    python = find_python()

    # 1. Create virtual environment
    if os.path.isdir(VENV_DIR):
        print(f"Reusing existing venv: {VENV_DIR}")
    else:
        run([python, "-m", "venv", VENV_DIR])

    venv_python = os.path.join(VENV_DIR, "bin", "python3")
    venv_pip = os.path.join(VENV_DIR, "bin", "pip3")

    # 2. Upgrade pip
    run([venv_python, "-m", "pip", "install", "--upgrade", "pip"])

    # 3. Install runtime deps
    run([venv_pip, "install", "PyQt6", "Pillow", "psutil"])

    # 4. Install build tool
    run([venv_pip, "install", "pyinstaller"])

    # 5. Build
    spec = os.path.join(HERE, "screen_mirroring_linux.spec")
    run([venv_python, "-m", "PyInstaller", spec])

    print("\n=== Build complete ===")
    print(f"Binary: {os.path.join(HERE, 'dist', 'screen_mirroring', 'screen_mirroring')}")


if __name__ == "__main__":
    main()
