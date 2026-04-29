"""scrcpy-server lifecycle management and frame reception."""
import os
import sys
import socket
import struct
import threading
import hashlib
import time
import io
import subprocess
import logging
from pathlib import Path
from typing import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class ScrcpyError(Exception):
    """scrcpy-server startup or runtime error."""
    pass


@dataclass
class ScrcpyOptions:
    max_size: int = 1920
    bit_rate: int = 8_000_000  # 8 Mbps
    max_fps: int = 60
    tunnel_forward: bool = True  # use TCP forward vs adb reverse
    lock_video_orientation: int = -1  # follow device
    send_device_meta: bool = True
    send_frame_meta: bool = True
    sendDummyByte: bool = False
    # Codec: "h264" (default, ~60fps) or "mjpeg" (~15-30fps)
    codec: str = "h264"


@dataclass
class ScrcpyDeviceMeta:
    device_name: str = ""
    width: int = 0
    height: int = 0


# ABI → scrcpy server jar filename mapping
SCRCPY_SERVER_NAMES = {
    "arm64-v8a":  "scrcpy-server-arm64-v8a",
    "armeabi-v7a": "scrcpy-server-armeabi-v7a",
    "x86_64":    "scrcpy-server-x86_64",
    "x86":       "scrcpy-server-x86",
}


class ScrcpyManager:
    DEFAULT_SERVER_PORT = 27199

    def __init__(
        self,
        serial: str,
        on_frame: Callable[[bytes, int], None] | None = None,
        on_meta: Callable[[ScrcpyDeviceMeta], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        self.serial = serial
        self._on_frame = on_frame
        self._on_meta = on_meta
        self._on_error = on_error

        self._opts = ScrcpyOptions()
        self._running = False
        self._recv_thread: threading.Thread | None = None
        self._server_proc: subprocess.Popen | None = None
        self._server_port = self._derive_port(serial)
        self._meta = ScrcpyDeviceMeta()

    def _derive_port(self, serial: str) -> int:
        h = int(hashlib.md5(serial.encode()).hexdigest()[:6], 16)
        return self.DEFAULT_SERVER_PORT + (h % 10000)

    # ── Server binary resolution ───────────────────────────────────────

    @staticmethod
    def find_server(abi: str) -> Path | None:
        candidates = SCRCPY_SERVER_NAMES.get(abi, [])
        if isinstance(candidates, str):
            candidates = [candidates]

        # Fallbacks: abi names, and the generic unversioned name
        candidates = list(candidates) + [abi, "scrcpy-server", "scrcpy-server.jar"]

        base = Path(__file__).parent.parent.parent / "resources" / "scrcpy"

        for name in candidates:
            for ext in ("", ".jar"):
                path = base / f"{name}{ext}"
                if path.exists() and path.stat().st_size > 50_000:
                    logger.info(f"Found scrcpy-server: {path.name} ({path.stat().st_size / 1024:.0f} KB)")
                    return path
        return None

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self, adb_manager, abi: str, server_jar: Path | None = None):
        """Start scrcpy server and begin receiving frames.

        Args:
            adb_manager: AdbManager instance
            abi: Device ABI string (e.g. "arm64-v8a")
            server_jar: Path to scrcpy-server.jar (auto-detected if None)
        """
        if self._running:
            logger.warning("scrcpy already running")
            return

        if server_jar is None:
            server_jar = self.find_server(abi)

        if server_jar is None or not server_jar.exists():
            raise ScrcpyError(
                f"scrcpy-server not found for ABI '{abi}'. "
                f"Please download scrcpy-server from "
                f"https://github.com/Genymobile/scrcpy/releases "
                f"and place it in resources/scrcpy/"
            )

        device_server_path = "/data/local/tmp/scrcpy-server.jar"

        # Step 1: Push server to device
        logger.info(f"Pushing scrcpy-server ({server_jar.name}) to device...")
        try:
            adb_manager.push(self.serial, str(server_jar), device_server_path, timeout=120)
        except Exception as e:
            raise ScrcpyError(f"Failed to push scrcpy-server: {e}")

        # Step 2: Start server on device
        scrcpy_opts = self._build_server_opts()
        server_cmd = (
            f"CLASSPATH={device_server_path} "
            f"app_process / su --context=default_app_process "
            f"/ com.genymobile.scrcpy.Server "
            f"{scrcpy_opts}"
        )

        logger.info("Starting scrcpy-server on device...")
        try:
            # Run in background — we don't wait for it
            adb_manager._run(
                ["-s", self.serial, "shell", server_cmd],
                timeout=5,
            )
        except Exception as e:
            logger.warning(f"Server start command issued (server runs async): {e}")

        # Step 3: Wait for server to bind
        time.sleep(2.0)

        # Step 4: Forward port
        logger.info(f"Forwarding local port {self._server_port}...")
        forwarded = adb_manager.forward(self.serial, self._server_port, "localabstract:scrcpy")
        if not forwarded:
            raise ScrcpyError("Failed to forward scrcpy port.")

        # Step 5: Start receiving frames
        self._running = True
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()
        logger.info("scrcpy frame receiver started.")

    def _build_server_opts(self) -> str:
        opts = self._opts
        return (
            f"--max_size={opts.max_size} "
            f"--bit_rate={opts.bit_rate} "
            f"--max_fps={opts.max_fps} "
            f"{'--tunnel_forward' if opts.tunnel_forward else '--tunnel=true'} "
            f"--lock_video_orientation={opts.lock_video_orientation} "
            f"--send_device_meta={'true' if opts.send_device_meta else 'false'} "
            f"--send_frame_meta={'true' if opts.send_frame_meta else 'false'} "
            f"--send_dummy_byte={'true' if opts.sendDummyByte else 'false'} "
        )

    # ── Frame receiver ────────────────────────────────────────────────

    def _recv_loop(self):
        """Connect to scrcpy server and read frames."""
        buf = b""
        while self._running:
            try:
                sock = socket.socket(socket.AF_INET, socket.SSTREAM)
                sock.settimeout(10.0)
                sock.connect(("127.0.0.1", self._server_port))

                # Send codec preference (for newer scrcpy versions)
                # 0x00 = h264, 0x01 = h265, 0x02 = mjpeg
                try:
                    codec_byte = b"\x02" if self._opts.codec == "mjpeg" else b"\x00"
                    sock.sendall(codec_byte)
                except Exception:
                    pass

                while self._running:
                    # Read at least 12 bytes: 4 (device name len) + 4 (device name) + 4 (h264 meta)
                    while len(buf) < 12:
                        chunk = sock.recv(65536)
                        if not chunk:
                            return
                        buf += chunk

                    # Parse device meta header
                    name_len = struct.unpack_from(">I", buf, 0)[0]
                    required = 4 + name_len + 8
                    while len(buf) < required:
                        chunk = sock.recv(65536)
                        if not chunk:
                            return
                        buf += chunk

                    name_bytes = buf[4:4 + name_len]
                    meta = struct.unpack_from(">II", buf, 4 + name_len)
                    video_meta = struct.unpack_from(">III", buf, 4 + name_len + 8)

                    self._meta.device_name = name_bytes.decode("utf-8", errors="replace")
                    self._meta.width = video_meta[0]
                    self._meta.height = video_meta[1]
                    buf = buf[required:]

                    if self._on_meta:
                        self._on_meta(self._meta)

                    # Read frames: [4-byte length][frame type][payload]
                    while len(buf) < 4:
                        chunk = sock.recv(65536)
                        if not chunk:
                            break
                        buf += chunk

                    if not buf:
                        break

                    frame_len = struct.unpack(">I", buf[:4])[0]
                    buf = buf[4:]

                    while len(buf) < frame_len:
                        chunk = sock.recv(65536)
                        if not chunk:
                            break
                        buf += chunk

                    if len(buf) < frame_len:
                        break

                    frame_data = buf[:frame_len]
                    buf = buf[frame_len:]

                    frame_type = frame_data[0] if frame_data else 0
                    payload = frame_data[1:]

                    if self._on_frame:
                        self._on_frame(payload, frame_type)

                sock.close()
            except socket.timeout:
                logger.debug("Socket timeout, reconnecting...")
                continue
            except OSError as e:
                if self._running:
                    logger.error(f"Socket error: {e}")
                    if self._on_error:
                        self._on_error(str(e))
                break
            except Exception as e:
                logger.exception("Frame receiver error")
                if self._on_error:
                    self._on_error(str(e))
                break

    async def stop(self, adb_manager):
        """Stop scrcpy server and clean up."""
        logger.info("Stopping scrcpy...")
        self._running = False

        if self._recv_thread and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=3.0)

        try:
            adb_manager.forward_remove(self._server_port)
        except Exception:
            pass

        try:
            adb_manager.shell(self.serial, "pkill -f scrcpy", timeout=5)
        except Exception:
            pass

        logger.info("scrcpy stopped.")

    @property
    def meta(self) -> ScrcpyDeviceMeta:
        return self._meta
