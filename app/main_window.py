"""Main application window."""
import sys
import os
import logging
import asyncio
import threading
import traceback
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QStatusBar,
    QMessageBox, QFileDialog, QProgressDialog,
    QSlider, QMenuBar, QMenu, QDialog,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot, QSize
from PyQt6.QtGui import QAction, QKeySequence, QPixmap, QImage
from app.services.adb_manager import AdbManager, AdbNotFoundError, DeviceNotFoundError
from app.services.capture_manager import CaptureManager
from app.services.screenshot_handler import ScreenshotHandler
from app.services.recording_handler import RecordingHandler
from app.services.frame_receiver import FrameReceiver
from app.widgets.mirroring_canvas import MirroringCanvas
from app.utils.config import AppSettings, get_recordings_dir, get_temp_screenshot_dir
from app.utils.logging import setup_logging

logger = setup_logging()
MAIN_LOGGER = logging.getLogger("ScreenMirroring.MainWindow")


def _open_file(path: str | Path):
    """Cross-platform file/folder opener."""
    p = str(path)
    if sys.platform == "win32":
        os.startfile(p)
    else:
        subprocess.run(["xdg-open", p], capture_output=True)


def _open_folder(path: str | Path):
    """Cross-platform folder opener."""
    p = str(path)
    if sys.platform == "win32":
        os.startfile(p)
    else:
        subprocess.run(["xdg-open", p], capture_output=True)


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Screen Mirroring")
        self.setMinimumSize(800, 600)

        # ── Services ──────────────────────────────────────────────────
        self._settings = AppSettings()
        self._adb = AdbManager()
        self._capture_manager: CaptureManager | None = None
        self._frame_receiver: FrameReceiver | None = None
        self._screenshot_handler: ScreenshotHandler | None = None
        self._recording_handler: RecordingHandler | None = None

        # ── State ─────────────────────────────────────────────────────
        self._current_serial: str | None = None
        self._connected = False
        self._is_recording = False
        self._current_fps = 0.0

        # ── Build UI ──────────────────────────────────────────────────
        self._build_menu_bar()
        self._build_ui()
        self._connect_signals()

        # ── Restore geometry ──────────────────────────────────────────
        geom = self._settings.get_window_geometry()
        if geom:
            self.restoreGeometry(geom)

        # ── Check ADB ─────────────────────────────────────────────────
        self._check_adb()

        # ── Device polling ────────────────────────────────────────────
        self._adb.start_polling(interval=3.0)
        self._refresh_devices()

        # ── FPS timer ─────────────────────────────────────────────────
        self._fps_display_timer = QTimer()
        self._fps_display_timer.timeout.connect(self._update_fps_display)
        self._fps_display_timer.start(500)

    # ── UI Construction ──────────────────────────────────────────────

    def _build_menu_bar(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        file_menu.addAction("&Open Recording...", self._on_open_recording)
        file_menu.addSeparator()
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menubar.addMenu("&View")
        self._always_on_top_action = QAction("Always on &Top", self)
        self._always_on_top_action.setCheckable(True)
        self._always_on_top_action.triggered.connect(self._toggle_always_on_top)
        view_menu.addAction(self._always_on_top_action)

        help_menu = menubar.addMenu("&Help")
        help_menu.addAction("&About", self._show_about)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # ── Device bar ────────────────────────────────────────────────
        device_bar = QHBoxLayout()
        self._device_combo = QComboBox()
        self._device_combo.setMinimumWidth(250)
        self._refresh_btn = QPushButton("↻")
        self._refresh_btn.setToolTip("Refresh devices")
        self._refresh_btn.setMaximumWidth(40)
        self._device_info_label = QLabel("No device")
        self._device_info_label.setStyleSheet("color: gray;")
        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setDefault(True)
        self._connect_btn.setMinimumWidth(100)

        device_bar.addWidget(QLabel("Device:"))
        device_bar.addWidget(self._device_combo)
        device_bar.addWidget(self._refresh_btn)
        device_bar.addWidget(self._device_info_label)
        device_bar.addWidget(self._connect_btn)
        device_bar.addStretch()
        layout.addLayout(device_bar)

        # ── Canvas area ───────────────────────────────────────────────
        self._canvas = MirroringCanvas()
        self._canvas.setMinimumSize(320, 240)
        layout.addWidget(self._canvas, 1)

        # ── Control bar ───────────────────────────────────────────────
        ctrl_bar = QHBoxLayout()
        self._screenshot_btn = QPushButton("📷 Screenshot")
        self._screenshot_btn.setEnabled(False)
        self._record_btn = QPushButton("⏺ Record")
        self._record_btn.setEnabled(False)
        self._record_btn.setStyleSheet("QPushButton { min-width: 100px; }")
        self._rotate_btn = QPushButton("↻ Rotate")
        self._rotate_btn.setEnabled(False)
        self._input_btn = QPushButton("🖱 Input")
        self._input_btn.setCheckable(True)
        self._input_btn.setEnabled(False)
        self._input_btn.setToolTip(
            "Input control is temporarily disabled.\n"
            "Mouse/keyboard control will be available in a future update."
        )

        # Zoom slider
        self._zoom_label = QLabel("Zoom: 100%")
        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setRange(25, 400)
        self._zoom_slider.setValue(100)
        self._zoom_slider.setMaximumWidth(150)
        self._zoom_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._zoom_slider.setTickInterval(25)

        self._fps_label = QLabel("FPS: --")

        ctrl_bar.addWidget(self._screenshot_btn)
        ctrl_bar.addWidget(self._record_btn)
        ctrl_bar.addWidget(self._rotate_btn)
        ctrl_bar.addWidget(self._input_btn)
        ctrl_bar.addSpacing(20)
        ctrl_bar.addWidget(self._zoom_label)
        ctrl_bar.addWidget(self._zoom_slider)
        ctrl_bar.addSpacing(20)
        ctrl_bar.addWidget(self._fps_label)
        ctrl_bar.addStretch()
        layout.addLayout(ctrl_bar)

        # ── Status bar ────────────────────────────────────────────────
        self._status_label = QLabel("Ready")
        self._status_recording = QLabel("")
        self._status_recording.setStyleSheet("color: red; font-weight: bold;")
        self.statusBar().addWidget(self._status_label, 1)
        self.statusBar().addPermanentWidget(self._status_recording)

    def _connect_signals(self):
        self._refresh_btn.clicked.connect(self._refresh_devices)
        self._device_combo.currentIndexChanged.connect(self._on_device_selected)
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        self._screenshot_btn.clicked.connect(self._on_screenshot)
        self._record_btn.clicked.connect(self._on_record_toggle)
        self._rotate_btn.clicked.connect(self._on_rotate)
        self._input_btn.clicked.connect(self._on_input_toggle)
        self._zoom_slider.valueChanged.connect(self._on_zoom_changed)

        self._adb.add_listener(self._on_devices_changed)

    # ── ADB ─────────────────────────────────────────────────────────

    def _check_adb(self):
        try:
            self._adb.find_adb()
            self.statusBar().showMessage("ADB ready", 3000)
        except AdbNotFoundError:
            QMessageBox.warning(
                self,
                "ADB Not Found",
                "ADB (Android Debug Bridge) was not found.\n\n"
                "Please install Android SDK platform-tools and add it to your PATH, "
                "or place adb.exe in the application directory.\n\n"
                "Download: https://developer.android.com/studio/releases/platform-tools"
            )
            self._status_label.setText("ADB not found!")
            self._status_label.setStyleSheet("color: red;")
            logger.error("ADB not found in PATH")

    def _refresh_devices(self):
        try:
            devices = self._adb.refresh_devices()
            self._populate_device_list(devices)
        except AdbNotFoundError:
            pass  # Already warned
        except Exception as e:
            logger.exception("Failed to refresh devices")

    def _populate_device_list(self, devices):
        current = self._device_combo.currentData()
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        for dev in devices:
            label = f"{dev.display_name} [{dev.state.value}]"
            self._device_combo.addItem(label, dev.serial)
        if current:
            idx = self._device_combo.findData(current)
            if idx >= 0:
                self._device_combo.setCurrentIndex(idx)
        self._device_combo.blockSignals(False)
        self._update_device_info()

    def _on_devices_changed(self, devices):
        self._pending_devices = devices
        QTimer.singleShot(0, self._refresh_device_list)

    def _refresh_device_list(self):
        devices = getattr(self, "_pending_devices", [])
        self._populate_device_list(devices)

    def _update_device_info(self):
        idx = self._device_combo.currentIndex()
        if idx < 0:
            self._device_info_label.setText("No device")
            self._device_info_label.setStyleSheet("color: gray;")
            return
        serial = self._device_combo.currentData()
        if serial:
            try:
                model = self._adb.get_device_model(serial)
                res = self._adb.get_device_resolution(serial)
                parts = []
                if model:
                    parts.append(model)
                if res:
                    parts.append(f"{res[0]}x{res[1]}")
                self._device_info_label.setText(" | ".join(parts) if parts else "Connected")
                self._device_info_label.setStyleSheet("color: green;")
            except Exception:
                self._device_info_label.setText("Connected")
                self._device_info_label.setStyleSheet("color: green;")

    def _on_device_selected(self, index):
        self._update_device_info()
        if self._connected:
            self._on_disconnect()

    # ── Connect / Disconnect ─────────────────────────────────────────

    def _on_connect_clicked(self):
        if self._connected:
            self._on_disconnect()
        else:
            self._on_connect()

    def _on_connect(self):
        idx = self._device_combo.currentIndex()
        if idx < 0:
            QMessageBox.information(self, "No Device", "Please select a device first.")
            return

        serial = self._device_combo.currentData()
        self._current_serial = serial
        self._set_ui_connecting(True)

        # Create CaptureManager in main thread so its internal QObjects
        # (FrameReceiver) have a Qt event loop for signal delivery.
        self._capture_manager = CaptureManager(serial, self._adb)
        self._capture_manager.frame_ready.connect(self._on_new_frame)
        self._capture_manager.fps_updated.connect(self._on_fps_updated)
        self._capture_manager.connected.connect(self._on_capture_connected)
        self._capture_manager.error_occurred.connect(self._on_scrcpy_error)
        self._capture_manager.device_type_detected.connect(self._on_device_type_detected)
        self._capture_manager.device_meta.connect(self._on_device_meta)
        self._capture_manager.input_ready.connect(self._on_input_ready)

        def work():
            try:
                logger.info(f"Connecting to device {serial}...")

                def set_status(msg: str):
                    self._status_label.setText(msg)
                QTimer.singleShot(0, lambda: set_status(f"Detecting device type for {serial}..."))

                self._capture_manager.start()

            except Exception as e:
                logger.exception("Connection failed")
                QTimer.singleShot(0, lambda: self._set_connected_state(False))
                QTimer.singleShot(0, lambda: self._show_error(str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _on_device_type_detected(self, device_type: str):
        type_names = {
            "android": "Android",
            "linux": "Linux",
            "unknown": "Unknown",
        }
        name = type_names.get(device_type, device_type)
        self._status_label.setText(f"Detected {name} device — connecting...")

    def _on_capture_connected(self, connected: bool):
        if connected:
            # Create per-connection handlers now that we have a serial
            self._screenshot_handler = ScreenshotHandler(self._current_serial, self._adb)
            self._recording_handler = RecordingHandler(
                self._current_serial, self._adb, get_recordings_dir()
            )
            self._set_connected_state(True)
        else:
            self._set_connected_state(False)

    @pyqtSlot(object)
    def _on_input_ready(self, input_controller):
        """Called when the control socket is up and input controller is ready."""
        self._canvas.set_input_controller(input_controller)
        logger.info("Input controller ready and wired to canvas")

    def _on_disconnect(self):
        if self._capture_manager:
            def stop():
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._capture_manager.stop())
                loop.close()
                QTimer.singleShot(0, self._cleanup_after_disconnect)
            threading.Thread(target=stop, daemon=True).start()
        else:
            self._set_connected_state(False)

    def _set_connected_state(self, connected: bool):
        self._connected = connected
        if connected:
            self._connect_btn.setText("Disconnect")
            self._connect_btn.setStyleSheet("QPushButton { font-weight: bold; }")
            self._screenshot_btn.setEnabled(True)
            self._record_btn.setEnabled(True)
            self._rotate_btn.setEnabled(True)
            # Input control only works reliably on Android via scrcpy's control channel.
            # On Linux, xdotool conflicts with the device's own touchscreen, so disabled.
            is_android = (
                self._capture_manager is not None
                and self._capture_manager.device_type == CaptureManager.DEVICE_TYPE_ANDROID
            )
            self._input_btn.setEnabled(is_android)
            self._device_combo.setEnabled(False)
            self._status_label.setText("Connected")
            self._status_label.setStyleSheet("color: green;")
        else:
            self._connect_btn.setText("Connect")
            self._connect_btn.setStyleSheet("")
            self._screenshot_btn.setEnabled(False)
            self._record_btn.setEnabled(False)
            self._rotate_btn.setEnabled(False)
            self._input_btn.setEnabled(False)
            self._input_btn.setChecked(False)
            self._canvas.set_input_enabled(False)
            self._canvas.set_input_controller(None)
            self._canvas.set_pixmap(None)
            self._device_combo.setEnabled(True)
            self._fps_label.setText("FPS: --")
            if self._is_recording:
                self._is_recording = False
                self._status_recording.setText("")
            self._status_label.setText("Disconnected")
            self._status_label.setStyleSheet("color: orange;")

    @pyqtSlot()
    def _cleanup_after_disconnect(self):
        self._capture_manager = None
        self._frame_receiver = None
        self._screenshot_handler = None
        self._recording_handler = None
        self._set_connected_state(False)

    def _set_ui_connecting(self, connecting: bool):
        if connecting:
            self._connect_btn.setEnabled(False)
            self._connect_btn.setText("Connecting...")
            self._status_label.setText("Connecting...")
            self._status_label.setStyleSheet("color: orange;")
        else:
            self._connect_btn.setEnabled(True)

    # ── Frame handling ───────────────────────────────────────────────

    @pyqtSlot(QImage)
    def _on_new_frame(self, qimage: QImage):
        from PyQt6.QtGui import QPixmap
        pixmap = QPixmap.fromImage(qimage)
        if pixmap.isNull():
            logger.error(f"Null pixmap from QImage {qimage.width()}x{qimage.height()}")
            return
        self._canvas.set_pixmap(pixmap)
        logger.debug(f"Frame rendered: {qimage.width()}x{qimage.height()}")

    @pyqtSlot(float)
    def _on_fps_updated(self, fps: float):
        self._current_fps = fps
        logger.debug(f"FPS updated: {fps:.1f}")

    def _update_fps_display(self):
        if self._connected:
            self._fps_label.setText(f"FPS: {self._current_fps:.0f}")

    @pyqtSlot(object)
    def _on_meta(self, meta):
        logger.info(f"Device meta: {meta.device_name} {meta.width}x{meta.height}")

    @pyqtSlot(tuple)
    def _on_device_meta(self, size: tuple):
        w, h = size
        logger.info(f"Device screen size: {w}x{h}")
        self._canvas.set_device_size(w, h)

    @pyqtSlot(str)
    def _on_scrcpy_error(self, error: str):
        self._show_error(error)

    @pyqtSlot(str)
    def _show_error(self, msg: str):
        QMessageBox.critical(self, "Connection Error", msg)
        self._set_connected_state(False)

    # ── Controls ─────────────────────────────────────────────────────

    def _on_screenshot(self):
        if not self._screenshot_handler or not self._current_serial:
            return
        self._screenshot_btn.setEnabled(False)
        self._status_label.setText("Taking screenshot...")

        def work():
            pixmap = self._screenshot_handler.capture()
            self._pending_pixmap = pixmap
            QTimer.singleShot(0, self._on_screenshot_done)

        threading.Thread(target=work, daemon=True).start()

    def _on_screenshot_done(self):
        self._screenshot_btn.setEnabled(True)
        self._status_label.setText("Ready")
        pixmap = self._pending_pixmap
        if pixmap and not pixmap.isNull():
            self._show_screenshot_preview(pixmap)
        else:
            QMessageBox.warning(self, "Screenshot Failed", "Could not capture screenshot.")

    def _show_screenshot_preview(self, pixmap):
        from PyQt6.QtWidgets import QVBoxLayout as QVL, QHBoxLayout as QHL, QLabel as _QL, QPushButton as _PB
        from PyQt6.QtCore import Qt as _Qt

        dialog = QDialog(self)
        dialog.setWindowTitle("Screenshot")
        dialog.setMinimumSize(600, 500)
        layout = QVL(dialog)

        preview_label = _QL()
        preview_label.setAlignment(_Qt.AlignmentFlag.AlignCenter)
        preview_label.setPixmap(pixmap.scaled(
            580, 420, _Qt.AspectRatioMode.KeepAspectRatio,
            _Qt.TransformationMode.SmoothTransformation
        ))
        layout.addWidget(preview_label)

        btn_bar = QHL()
        save_btn = _PB("Save As...")
        open_btn = _PB("Open Folder")
        close_btn = _PB("Close")
        btn_bar.addWidget(save_btn)
        btn_bar.addWidget(open_btn)
        btn_bar.addStretch()
        btn_bar.addWidget(close_btn)
        layout.addLayout(btn_bar)

        def save():
            path, _ = QFileDialog.getSaveFileName(
                dialog, "Save Screenshot",
                str(get_temp_screenshot_dir() / f"screenshot.png"),
                "PNG Files (*.png);;JPEG Files (*.jpg)"
            )
            if path:
                self._screenshot_handler.save_to_file(pixmap, Path(path))
                self._status_label.setText(f"Saved: {Path(path).name}")

        save_btn.clicked.connect(save)
        open_btn.clicked.connect(lambda: _open_folder(get_temp_screenshot_dir()))
        close_btn.clicked.connect(dialog.accept)

        dialog.exec()

    def _on_record_toggle(self):
        if self._is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        if not self._recording_handler:
            return
        ok = self._recording_handler.start()
        if ok:
            self._is_recording = True
            self._record_btn.setText("⏹ Stop")
            self._record_btn.setStyleSheet("QPushButton { color: red; font-weight: bold; }")
            self._status_recording.setText("REC ●")
            self._recording_handler.recording_stopped.connect(self._on_recording_stopped)

    def _stop_recording(self):
        if self._recording_handler:
            self._recording_handler.stop()
        self._is_recording = False
        self._record_btn.setText("⏺ Record")
        self._record_btn.setStyleSheet("")
        self._status_recording.setText("")

    @pyqtSlot(str, float)
    def _on_recording_stopped(self, path: str, duration: float):
        self._is_recording = False
        self._record_btn.setText("⏺ Record")
        self._record_btn.setStyleSheet("")
        self._status_recording.setText("")
        self._status_label.setText(f"Recording saved: {Path(path).name} ({duration:.0f}s)")
        reply = QMessageBox.information(
            self, "Recording Saved",
            f"Recording saved:\n{path}\n\nDuration: {duration:.0f}s\n\nOpen file?",
            QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Close,
        )
        if reply == QMessageBox.StandardButton.Open:
            _open_file(path)

    def _on_input_toggle(self):
        """Toggle mouse/keyboard input mode."""
        enabled = self._input_btn.isChecked()
        self._canvas.set_input_enabled(enabled)

    def _on_rotate(self):
        """Rotate the device screen 90 degrees."""
        ic = self._canvas.input_controller() if callable(self._canvas.input_controller) else self._canvas._input_controller
        # Try to access the input controller from the capture manager
        if self._capture_manager and self._capture_manager._capture:
            capture = self._capture_manager._capture
            ic = getattr(capture, "_input_controller", None)
        if ic:
            ic.rotate_device()
            logger.info("Device rotated")

    def _on_zoom_changed(self, value: int):
        self._zoom_label.setText(f"Zoom: {value}%")
        # Trigger re-render of current frame at new zoom
        # The next frame will apply the zoom automatically

    def _toggle_always_on_top(self, checked: bool):
        flags = self.windowFlags()
        if checked:
            self.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags & ~Qt.WindowType.WindowStaysOnTopHint)
        self.show()

    def _on_open_recording(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Recording",
            str(get_recordings_dir()),
            "MP4 Files (*.mp4);;All Files (*)"
        )
        if path:
            _open_file(path)

    def _show_about(self):
        QMessageBox.about(self, "About Screen Mirroring",
                          "<b>Screen Mirroring</b><br>"
                          "Version 0.1.0<br><br>"
                          "A lightweight screen mirroring tool for ADB-connected "
                          "Android devices.<br><br>"
                          "Powered by scrcpy-server.")

    # ── Window geometry ───────────────────────────────────────────────

    def closeEvent(self, event):
        # Save geometry
        self._settings.set_window_geometry(self.saveGeometry())

        # Stop polling
        self._adb.stop_polling()

        # Disconnect
        if self._connected:
            self._on_disconnect()
            # Wait briefly for disconnect to complete
            import time; time.sleep(0.5)

        # Stop recording if active
        if self._recording_handler and self._recording_handler.is_recording:
            self._stop_recording()

        event.accept()
