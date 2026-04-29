"""ADB Screen Mirroring — Entry point."""
import sys
import os

# Ensure bundled resources are found when running as exe
if getattr(sys, 'frozen', False):
    app_dir = os.path.dirname(sys.executable)
    os.chdir(app_dir)

from app.main_window import MainWindow
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Screen Mirroring")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("ScreenMirroring")

    # Set app icon if available
    icon_path = os.path.join(os.path.dirname(__file__), "resources", "icons", "app.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
