"""
FleetBridge — platform abstraction for launcher ↔ fleet communication.

On Windows: commands run inside WSL Ubuntu via wsl.exe
On Linux/macOS: commands run natively via bash

Usage:
    from fleet_bridge import create_bridge
    bridge = create_bridge(FLEET_DIR)
    bridge.run("uv run python lead_client.py status", capture=True)
    bridge.run_bg("uv run python supervisor.py", callback=on_done)
"""
import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Optional, Tuple


class FleetBridge(ABC):
    """Abstract communication layer between launcher (GUI) and fleet (backend)."""

    def __init__(self, fleet_dir: Path):
        self.fleet_dir = fleet_dir

    @abstractmethod
    def run(self, cmd: str, capture: bool = False, timeout: int = 60) -> Tuple[str, str]:
        """Run a command in the fleet environment.
        Returns (stdout, stderr) if capture=True, else ("", "").
        """
        ...

    @abstractmethod
    def fleet_path(self) -> str:
        """Return the fleet directory path as understood by the execution environment."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the bridge's execution environment is reachable."""
        ...

    def run_bg(self, cmd: str, callback: Optional[Callable] = None, timeout: int = 60):
        """Run a command in a background thread; call callback(stdout, stderr) when done."""
        def _run():
            try:
                out, err = self.run(cmd, capture=True, timeout=timeout)
            except Exception as e:
                out, err = "", str(e)
            if callback:
                callback(out, err)
        threading.Thread(target=_run, daemon=True).start()


# Platform-conditional subprocess flags
_NO_WINDOW = 0
if sys.platform == "win32":
    _NO_WINDOW = subprocess.CREATE_NO_WINDOW


class WslBridge(FleetBridge):
    """Windows: runs fleet commands inside WSL Ubuntu."""

    def fleet_path(self) -> str:
        p = str(self.fleet_dir).replace("\\", "/")
        if len(p) > 1 and p[1] == ":":
            return f"/mnt/{p[0].lower()}{p[2:]}"
        return p

    def run(self, cmd: str, capture: bool = False, timeout: int = 60) -> Tuple[str, str]:
        fleet = self.fleet_path()
        full = f'source ~/.secrets 2>/dev/null; cd "{fleet}" || exit 1; {cmd}'
        args = ["wsl", "-d", "Ubuntu", "/bin/bash", "-lc", full]
        if capture:
            r = subprocess.run(
                args, capture_output=True, text=True, timeout=timeout,
                creationflags=_NO_WINDOW,
            )
            return r.stdout.strip(), r.stderr.strip()
        else:
            subprocess.Popen(args, creationflags=_NO_WINDOW)
            return "", ""

    def is_available(self) -> bool:
        try:
            r = subprocess.run(
                ["wsl", "echo", "ok"], capture_output=True, text=True, timeout=5,
                creationflags=_NO_WINDOW,
            )
            return r.stdout.strip() == "ok"
        except Exception:
            return False


class DirectBridge(FleetBridge):
    """Linux/macOS: runs fleet commands natively in the same OS."""

    def fleet_path(self) -> str:
        return str(self.fleet_dir)

    def run(self, cmd: str, capture: bool = False, timeout: int = 60) -> Tuple[str, str]:
        fleet = self.fleet_path()
        full = f'cd "{fleet}" || exit 1; {cmd}'
        if capture:
            r = subprocess.run(
                ["bash", "-c", full],
                capture_output=True, text=True, timeout=timeout,
            )
            return r.stdout.strip(), r.stderr.strip()
        else:
            subprocess.Popen(["bash", "-c", full])
            return "", ""

    def is_available(self) -> bool:
        return True  # Native environment — always available


def create_bridge(fleet_dir: Path) -> FleetBridge:
    """Create the appropriate bridge for the current platform."""
    if sys.platform == "win32":
        return WslBridge(fleet_dir)
    else:
        return DirectBridge(fleet_dir)
