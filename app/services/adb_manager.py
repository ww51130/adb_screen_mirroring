"""ADB device discovery and command execution."""
import subprocess
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class DeviceState(Enum):
    DEVICE = "device"
    UNAUTHORIZED = "unauthorized"
    OFFLINE = "offline"


class Transport(Enum):
    USB = "usb"
    WIFI = "wifi"
    EMULATOR = "emulator"


@dataclass
class AdbDevice:
    serial: str
    state: DeviceState
    model: str | None = None
    product: str | None = None
    transport: Transport = Transport.USB
    address: str | None = None

    @property
    def display_name(self) -> str:
        if self.model:
            return f"{self.model} ({self.serial})"
        return self.serial


class AdbError(Exception):
    """Base exception for ADB operations."""
    pass


class AdbNotFoundError(AdbError):
    """ADB executable not found."""
    pass


class DeviceNotFoundError(AdbError):
    """Requested device not connected."""
    pass


class AdbManager:
    _instance: "AdbManager | None" = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._cached_devices: list[AdbDevice] = []
        self._listeners: list[Callable[[list[AdbDevice]], None]] = []
        self._poll_thread: threading.Thread | None = None
        self._stop_poll = threading.Event()

    # ── ADB executable ───────────────────────────────────────────────

    @staticmethod
    def find_adb() -> str:
        """Return path to adb executable. Raises AdbNotFoundError if missing."""
        import shutil
        adb_path = shutil.which("adb")
        if adb_path:
            return adb_path
        raise AdbNotFoundError(
            "ADB not found in PATH. Please install Android SDK platform-tools "
            "and add it to your PATH environment variable."
        )

    def _run(self, args: list[str], timeout: int = 15) -> str:
        """Run an ADB command and return stdout."""
        cmd = [self.find_adb()] + args
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip()
                # Ignore non-zero exits for cleanup/status commands (empty stderr = no real error)
                if stderr and ("error:" in stderr.lower() or "failed" in stderr.lower()):
                    raise AdbError(f"ADB error: {stderr}")
                # return stdout even on non-zero exit (e.g. pkill finds nothing)
                return result.stdout
            return result.stdout
        except FileNotFoundError:
            raise AdbNotFoundError("ADB executable not found.")
        except subprocess.TimeoutExpired:
            raise AdbError(f"ADB command timed out after {timeout}s: {' '.join(args)}")

    def _run_shell(self, serial: str | None, cmd: str, timeout: int = 15) -> str:
        args = []
        if serial:
            args += ["-s", serial]
        args += ["shell", cmd]
        return self._run(args, timeout=timeout)

    # ── Device discovery ──────────────────────────────────────────────

    def refresh_devices(self) -> list[AdbDevice]:
        """Query connected devices and return list."""
        output = self._run(["devices", "-l"]).strip()
        devices = self._parse_devices(output)
        self._cached_devices = devices
        return devices

    def _parse_devices(self, output: str) -> list[AdbDevice]:
        devices = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices"):
                continue
            m = re.match(r'^([^\s]+)\s+(device|unauthorized|offline)\s*(.*)$', line)
            if not m:
                continue
            serial, state_str, props_str = m.groups()
            props = {}
            for part in props_str.split():
                if ':' in part:
                    key, val = part.split(':', 1)
                    props[key] = val

            if ':' in serial and serial.count(':') == 1 and serial.split(':')[-1].isdigit():
                transport = Transport.WIFI
                address = serial
            elif serial.startswith("emulator-"):
                transport = Transport.EMULATOR
                address = None
            else:
                transport = Transport.USB
                address = None

            devices.append(AdbDevice(
                serial=serial,
                state=DeviceState(state_str),
                model=props.get("model"),
                product=props.get("product"),
                transport=transport,
                address=address,
            ))
        return devices

    # ── Auto-polling ──────────────────────────────────────────────────

    def start_polling(self, interval: float = 3.0):
        """Start background polling for device changes."""
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._stop_poll.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, args=(interval,), daemon=True
        )
        self._poll_thread.start()

    def stop_polling(self):
        self._stop_poll.set()

    def _poll_loop(self, interval: float):
        last_devices: list[AdbDevice] = []
        while not self._stop_poll.wait(interval):
            try:
                current = self.refresh_devices()
                if current != last_devices:
                    last_devices = current
                    for cb in self._listeners:
                        cb(current)
            except Exception:
                pass

    def add_listener(self, cb: Callable[[list[AdbDevice]], None]):
        self._listeners.append(cb)

    def remove_listener(self, cb: Callable[[list[AdbDevice]], None]):
        if cb in self._listeners:
            self._listeners.remove(cb)

    # ── Device info ───────────────────────────────────────────────────

    def get_device_model(self, serial: str) -> str | None:
        try:
            out = self._run_shell(serial, "getprop ro.product.model").strip()
            return out if out else None
        except AdbError:
            return None

    def get_device_abi(self, serial: str) -> str | None:
        try:
            out = self._run_shell(serial, "getprop ro.product.cpu.abi").strip()
            return out if out else None
        except AdbError:
            return None

    def get_device_resolution(self, serial: str) -> tuple[int, int] | None:
        try:
            out = self._run_shell(serial, "wm size").strip()
            # Output: "Physical size: 1080x2400"
            m = re.search(r'(\d+)x(\d+)', out)
            if m:
                return int(m.group(1)), int(m.group(2))
        except AdbError:
            pass
        return None

    # ── File operations ────────────────────────────────────────────────

    def push(self, serial: str, local_path: str, device_path: str, timeout: int = 60):
        self._run(["-s", serial, "push", local_path, device_path], timeout=timeout)

    def pull(self, serial: str, device_path: str, local_path: str, timeout: int = 60):
        self._run(["-s", serial, "pull", device_path, local_path], timeout=timeout)

    def shell(self, serial: str | None, cmd: str, timeout: int = 30) -> str:
        return self._run_shell(serial, cmd, timeout=timeout)

    def forward(self, serial: str, local_port: int, remote: str) -> bool:
        try:
            self._run(["-s", serial, "forward", f"tcp:{local_port}", remote], timeout=5)
            return True
        except AdbError:
            return False

    def forward_remove(self, local_port: int) -> bool:
        try:
            self._run(["forward", "--remove", f"tcp:{local_port}"], timeout=5)
            return True
        except AdbError:
            return False

    def kill_server(self):
        try:
            self._run(["kill-server"], timeout=5)
        except AdbError:
            pass
