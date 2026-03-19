"""
FleetBridge — platform abstraction for launcher ↔ fleet communication.

On Windows (WSL):    commands run inside WSL Ubuntu via wsl.exe
On Windows (native): commands run directly in Windows Python (no WSL)
On Linux/macOS:      commands run natively via bash

Usage:
    from fleet_bridge import create_bridge
    bridge = create_bridge(FLEET_DIR)
    bridge.run("uv run python lead_client.py status", capture=True)
    bridge.run_bg("uv run python supervisor.py", callback=on_done)
"""
import os
import re
import shutil
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


class NativeWindowsBridge(FleetBridge):
    """Windows native: runs fleet commands directly in Windows Python (no WSL).

    Use when fleet/ code runs natively on Windows (not inside WSL).
    Translates common bash-isms (nohup, &, ~/ paths) to Windows equivalents.
    Set BIGED_NATIVE_WINDOWS=1 to activate.
    """

    _HAS_UV: Optional[bool] = None  # cached uv availability check

    @classmethod
    def _uv_available(cls) -> bool:
        """Check (and cache) whether the 'uv' tool is on PATH."""
        if cls._HAS_UV is None:
            cls._HAS_UV = shutil.which("uv") is not None
        return cls._HAS_UV

    # -- bash→Windows translation ------------------------------------------------

    @staticmethod
    def _translate_cmd(cmd: str) -> str:
        """Strip/rewrite bash-isms so the command runs under Windows cmd.exe.

        Transformations:
        * Remove 'nohup ' prefix and trailing ' &'
        * Expand '~/' to the user home directory (Windows-style)
        * Remove 'source ~/.secrets 2>/dev/null;' preamble
        * Strip shell redirects to /dev/null
        """
        c = cmd
        # Strip nohup … &
        c = c.replace("nohup ", "")
        c = re.sub(r"\s*&\s*$", "", c)
        # Remove source-secrets preamble (with optional redirect)
        c = re.sub(r"source\s+~/\.secrets\s*[^;]*;\s*", "", c)
        # Expand ~/.local/bin/uv → uv  (Windows puts it on PATH via pip/pipx)
        c = c.replace("~/.local/bin/uv", "uv")
        # Generic ~/ → user home
        c = c.replace("~/", str(Path.home()).replace("\\", "/") + "/")
        # Strip /dev/null redirects (not valid on Windows)
        c = re.sub(r"\s*2>/dev/null", "", c)
        c = re.sub(r"\s*>/dev/null", "", c)
        # Strip bash log file redirects (>> file.log 2>&1)
        c = re.sub(r"\s*>>?\s*\S+\.log\s*2>&1", "", c)
        c = re.sub(r"\s*2>&1", "", c)
        return c.strip()

    def _prepare_cmd(self, cmd: str) -> str:
        """Full pipeline: translate bash-isms, then handle uv fallback."""
        c = self._translate_cmd(cmd)
        # If uv is not available, fall back to plain python
        if not self._uv_available():
            c = re.sub(r"\buv run python\b", "python", c)
            c = re.sub(r"\buv run\b", "python -m", c)
        return c

    # -- FleetBridge interface ----------------------------------------------------

    def fleet_path(self) -> str:
        return str(self.fleet_dir)

    def run(self, cmd: str, capture: bool = False, timeout: int = 60) -> Tuple[str, str]:
        clean = self._prepare_cmd(cmd)
        if capture:
            r = subprocess.run(
                clean, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=str(self.fleet_dir),
                creationflags=_NO_WINDOW,
            )
            return r.stdout.strip(), r.stderr.strip()
        else:
            subprocess.Popen(
                clean, shell=True, cwd=str(self.fleet_dir),
                creationflags=_NO_WINDOW,
            )
            return "", ""

    def is_available(self) -> bool:
        """Check that the fleet directory exists and Python is callable."""
        if not self.fleet_dir.is_dir():
            return False
        try:
            r = subprocess.run(
                "python --version", shell=True, capture_output=True,
                text=True, timeout=5, creationflags=_NO_WINDOW,
            )
            return r.returncode == 0
        except Exception:
            return False


def create_bridge(fleet_dir: Path) -> FleetBridge:
    """Create the appropriate bridge for the current platform.

    Bridge selection order:
    1. BIGED_NATIVE_WINDOWS=1 env var → NativeWindowsBridge  (Windows, no WSL)
    2. sys.platform == "win32"        → WslBridge             (Windows + WSL)
    3. Everything else                → DirectBridge           (Linux / macOS)
    """
    if os.environ.get("BIGED_NATIVE_WINDOWS", "").lower() in ("1", "true"):
        return NativeWindowsBridge(fleet_dir)
    if sys.platform == "win32":
        return WslBridge(fleet_dir)
    else:
        return DirectBridge(fleet_dir)
