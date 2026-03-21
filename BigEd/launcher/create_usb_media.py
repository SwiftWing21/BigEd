#!/usr/bin/env python3
"""
BigEd CC — USB Media Creator

Creates portable USB installer media for offline/air-gap deployment.
Packages the full BigEd CC application, fleet, dependencies, and optionally
Ollama + pre-downloaded models onto a removable drive or folder.

Usage:
    python create_usb_media.py                    # GUI mode
    python create_usb_media.py --drive E:         # Direct to drive letter
    python create_usb_media.py --output ./media   # Create folder (no USB needed)
    python create_usb_media.py --iso output.iso   # Create ISO image
    python create_usb_media.py --list-drives      # List removable drives

License: Apache 2.0 — only bundles Apache/MIT/BSD licensed components.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# ── Paths ────────────────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent  # BigEd/launcher -> BigEd -> project root
FLEET_DIR = PROJECT_ROOT / "fleet"
BIGED_DIR = PROJECT_ROOT / "BigEd"
ICON_ICO = HERE / "brick.ico"

APP_NAME = "Big Edge Compute Command"
APP_VERSION = "0.42.00b"
MEDIA_VERSION = "1.0.0"

# Subprocess flag to prevent console window flash on Windows
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Files/dirs to exclude when copying project trees
COPY_IGNORE = shutil.ignore_patterns(
    "*.pyc", "__pycache__", ".git", ".gitignore", ".gitattributes",
    "fleet.db", "rag.db", "*.log", ".venv", "venv", "node_modules",
    ".env", ".env.*", "*.sqlite", "*.sqlite3", "hw_state.json",
    "dist", "build", "*.egg-info", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "*.pem", "*.key", "credentials*", ".mcp.json",
    ".claude", "worktrees",
)

# Python embeddable download URL template (Windows x64)
PYTHON_EMBED_VERSION = "3.12.8"
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_EMBED_VERSION}/"
    f"python-{PYTHON_EMBED_VERSION}-embed-amd64.zip"
)

# Ollama download URLs
OLLAMA_URLS = {
    "win32": "https://ollama.com/download/OllamaSetup.exe",
    "linux": "https://ollama.com/download/ollama-linux-amd64",
    "darwin": "https://ollama.com/download/Ollama-darwin.zip",
}

# Default Ollama models for air-gap deployment
DEFAULT_MODELS = ["qwen3:8b", "qwen3:4b", "qwen3:0.6b"]


# ── Drive Detection ──────────────────────────────────────────────────────────

def detect_removable_drives() -> list[dict]:
    """
    Detect removable drives. Returns list of dicts with keys:
    device, mountpoint, fstype, label, total_gb, free_gb
    """
    drives = []

    if sys.platform == "win32":
        drives = _detect_drives_windows()
    else:
        drives = _detect_drives_posix()

    return drives


def _detect_drives_windows() -> list[dict]:
    """Windows: use wmic to enumerate removable drives."""
    drives = []
    try:
        result = subprocess.run(
            ["wmic", "logicaldisk", "where", "drivetype=2", "get",
             "DeviceID,VolumeName,Size,FreeSpace", "/format:csv"],
            capture_output=True, text=True, timeout=10,
            creationflags=_NO_WINDOW,
        )
        for line in result.stdout.strip().splitlines():
            parts = line.strip().split(",")
            if len(parts) < 5 or parts[1] == "DeviceID":
                continue
            # CSV: Node, DeviceID, FreeSpace, Size, VolumeName
            device_id = parts[1].strip()
            free_space = parts[2].strip()
            size = parts[3].strip()
            label = parts[4].strip() if len(parts) > 4 else ""

            total_gb = int(size) / (1024 ** 3) if size.isdigit() else 0
            free_gb = int(free_space) / (1024 ** 3) if free_space.isdigit() else 0

            drives.append({
                "device": device_id,
                "mountpoint": device_id + "\\",
                "fstype": "",
                "label": label or device_id,
                "total_gb": round(total_gb, 1),
                "free_gb": round(free_gb, 1),
            })
    except Exception:
        pass

    # Fallback: try psutil if wmic failed
    if not drives:
        try:
            import psutil
            for part in psutil.disk_partitions():
                opts = part.opts.lower() if part.opts else ""
                if "removable" in opts or "rw,removable" in opts:
                    try:
                        usage = psutil.disk_usage(part.mountpoint)
                        drives.append({
                            "device": part.device,
                            "mountpoint": part.mountpoint,
                            "fstype": part.fstype,
                            "label": part.device,
                            "total_gb": round(usage.total / (1024 ** 3), 1),
                            "free_gb": round(usage.free / (1024 ** 3), 1),
                        })
                    except Exception:
                        drives.append({
                            "device": part.device,
                            "mountpoint": part.mountpoint,
                            "fstype": part.fstype,
                            "label": part.device,
                            "total_gb": 0, "free_gb": 0,
                        })
        except ImportError:
            pass

    return drives


def _detect_drives_posix() -> list[dict]:
    """Linux/macOS: use mount / lsblk to find removable media."""
    drives = []
    try:
        import psutil
        for part in psutil.disk_partitions():
            mp = part.mountpoint
            # Common removable mount points
            if any(mp.startswith(p) for p in ["/media/", "/mnt/", "/run/media/", "/Volumes/"]):
                try:
                    usage = psutil.disk_usage(mp)
                    drives.append({
                        "device": part.device,
                        "mountpoint": mp,
                        "fstype": part.fstype,
                        "label": Path(mp).name or part.device,
                        "total_gb": round(usage.total / (1024 ** 3), 1),
                        "free_gb": round(usage.free / (1024 ** 3), 1),
                    })
                except Exception:
                    pass
    except ImportError:
        # Fallback without psutil
        try:
            result = subprocess.run(
                ["lsblk", "-J", "-o", "NAME,MOUNTPOINT,SIZE,FSTYPE,RM,LABEL"],
                capture_output=True, text=True, timeout=10,
            )
            data = json.loads(result.stdout)
            for dev in data.get("blockdevices", []):
                if dev.get("rm") and dev.get("mountpoint"):
                    drives.append({
                        "device": f"/dev/{dev['name']}",
                        "mountpoint": dev["mountpoint"],
                        "fstype": dev.get("fstype", ""),
                        "label": dev.get("label") or dev["name"],
                        "total_gb": 0, "free_gb": 0,
                    })
        except Exception:
            pass

    return drives


# ── File Operations ──────────────────────────────────────────────────────────

def estimate_media_size(
    include_python: bool = True,
    include_ollama: bool = True,
    include_models: bool = False,
) -> float:
    """Estimate total media size in GB."""
    base = 0.0

    # Fleet directory (excluding .venv, logs, db, etc.)
    if FLEET_DIR.exists():
        base += _dir_size_gb(FLEET_DIR, exclude={
            ".venv", "venv", "__pycache__", "fleet.db", "rag.db",
            "logs", ".git", "node_modules",
        })

    # BigEd directory
    if BIGED_DIR.exists():
        base += _dir_size_gb(BIGED_DIR, exclude={
            "__pycache__", "dist", "build", ".git",
        })

    if include_python:
        base += 0.015  # ~15 MB for embeddable Python
    if include_ollama:
        base += 0.15  # ~150 MB for Ollama binary
    if include_models:
        # Check ~/.ollama/models/ for actual sizes
        model_dir = _get_ollama_models_dir()
        if model_dir and model_dir.exists():
            base += _dir_size_gb(model_dir)
        else:
            base += 5.0  # Estimate ~5GB for default models

    return round(base, 2)


def _dir_size_gb(path: Path, exclude: set[str] | None = None) -> float:
    """Calculate directory size in GB, skipping excluded names."""
    total = 0
    exclude = exclude or set()
    try:
        for entry in path.rglob("*"):
            if any(part in exclude for part in entry.parts):
                continue
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except Exception:
        pass
    return total / (1024 ** 3)


def _get_ollama_models_dir() -> Optional[Path]:
    """Locate Ollama models directory."""
    # Check OLLAMA_MODELS env var first
    env_dir = os.environ.get("OLLAMA_MODELS")
    if env_dir:
        p = Path(env_dir)
        if p.exists():
            return p

    # Default locations
    if sys.platform == "win32":
        home = Path.home()
        candidates = [
            home / ".ollama" / "models",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "models",
        ]
    elif sys.platform == "darwin":
        candidates = [Path.home() / ".ollama" / "models"]
    else:
        candidates = [
            Path.home() / ".ollama" / "models",
            Path("/usr/share/ollama/.ollama/models"),
        ]

    for c in candidates:
        if c.exists():
            return c
    return None


def _find_ollama_binary() -> Optional[Path]:
    """Find Ollama binary on the system."""
    exe = shutil.which("ollama")
    if exe:
        return Path(exe)

    if sys.platform == "win32":
        for env_var, subpath in [
            ("LOCALAPPDATA", "Programs/Ollama/ollama.exe"),
            ("LOCALAPPDATA", "Ollama/ollama.exe"),
            ("PROGRAMFILES", "Ollama/ollama.exe"),
        ]:
            base = os.environ.get(env_var, "")
            if base:
                p = Path(base) / subpath
                if p.exists():
                    return p
    return None


def _download_file(url: str, dest: Path, progress_cb: Callable | None = None) -> bool:
    """Download a file from URL to dest. Returns True on success."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BigEdCC-MediaCreator/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total > 0:
                        progress_cb(downloaded / total)
        return True
    except Exception:
        return False


# ── Media Builder ────────────────────────────────────────────────────────────

class MediaBuilder:
    """
    Builds USB installer media layout.
    All heavy I/O is designed to run on a background thread.
    """

    def __init__(
        self,
        target_dir: Path,
        include_python_embed: bool = True,
        include_ollama: bool = True,
        include_models: bool = False,
        model_list: list[str] | None = None,
        on_progress: Callable[[float, str], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        on_complete: Callable[[bool, str], None] | None = None,
    ):
        self.target = target_dir
        self.bigdcc_root = target_dir / "BigEdCC"
        self.include_python = include_python_embed
        self.include_ollama = include_ollama
        self.include_models = include_models
        self.model_list = model_list or DEFAULT_MODELS
        self._progress = on_progress or (lambda p, m: None)
        self._log = on_log or (lambda m: None)
        self._complete = on_complete or (lambda ok, m: None)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def build(self):
        """Execute the full media build. Call from a background thread."""
        try:
            self._do_build()
        except Exception as e:
            self._complete(False, f"Build failed: {e}")

    def _do_build(self):
        steps = [
            (0.02, "Preparing target directory...", self._step_prepare),
            (0.10, "Copying fleet directory...", self._step_copy_fleet),
            (0.30, "Copying BigEd directory...", self._step_copy_biged),
            (0.45, "Writing offline configuration...", self._step_write_config),
            (0.50, "Bundling requirements...", self._step_bundle_requirements),
            (0.55, "Writing install scripts...", self._step_write_scripts),
            (0.60, "Writing README and LICENSE...", self._step_write_docs),
        ]

        if self.include_python:
            steps.append((0.68, "Bundling Python embeddable...", self._step_bundle_python))
        if self.include_ollama:
            steps.append((0.78, "Bundling Ollama...", self._step_bundle_ollama))
        if self.include_models:
            steps.append((0.88, "Copying Ollama models...", self._step_copy_models))

        steps += [
            (0.95, "Writing autorun and marker...", self._step_write_markers),
            (0.98, "Verifying media...", self._step_verify),
            (1.00, "Done.", lambda: None),
        ]

        for pct, label, fn in steps:
            if self._cancelled:
                self._complete(False, "Build cancelled by user.")
                return
            self._progress(pct, label)
            self._log(label)
            try:
                fn()
            except PermissionError as e:
                self._complete(False, f"Permission denied: {e}\nTry running as administrator.")
                return
            except OSError as e:
                if "No space left" in str(e) or "not enough space" in str(e).lower():
                    self._complete(False, f"Drive full: {e}")
                    return
                raise

        self._progress(1.0, "Media creation complete.")
        file_count = sum(1 for _ in self.bigdcc_root.rglob("*") if _.is_file())
        total_mb = _dir_size_gb(self.bigdcc_root) * 1024
        self._complete(
            True,
            f"USB media created successfully.\n"
            f"  Location: {self.target}\n"
            f"  Files: {file_count}\n"
            f"  Size: {total_mb:.1f} MB",
        )

    # ── Build Steps ──────────────────────────────────────────────────────────

    def _step_prepare(self):
        """Create target directory, remove existing BigEdCC folder if present."""
        self.target.mkdir(parents=True, exist_ok=True)
        if self.bigdcc_root.exists():
            self._log("  Removing existing BigEdCC/ folder...")
            shutil.rmtree(self.bigdcc_root)
        self.bigdcc_root.mkdir(parents=True, exist_ok=True)

    def _step_copy_fleet(self):
        """Copy fleet/ directory (skills, config, templates)."""
        dest = self.bigdcc_root / "fleet"
        if not FLEET_DIR.exists():
            self._log("  WARNING: fleet/ directory not found, skipping.")
            return
        shutil.copytree(FLEET_DIR, dest, ignore=COPY_IGNORE, dirs_exist_ok=True)
        # Remove databases that get auto-created
        for db_name in ("fleet.db", "rag.db", "fleet.db-journal", "rag.db-journal"):
            db = dest / db_name
            if db.exists():
                db.unlink()
        # Remove log files
        logs_dir = dest / "logs"
        if logs_dir.exists():
            shutil.rmtree(logs_dir, ignore_errors=True)
            logs_dir.mkdir(exist_ok=True)
        self._log(f"  Copied fleet/ ({self._count_files(dest)} files)")

    def _step_copy_biged(self):
        """Copy BigEd/ directory (launcher, modules)."""
        dest = self.bigdcc_root / "BigEd"
        if not BIGED_DIR.exists():
            self._log("  WARNING: BigEd/ directory not found, skipping.")
            return
        shutil.copytree(BIGED_DIR, dest, ignore=COPY_IGNORE, dirs_exist_ok=True)
        self._log(f"  Copied BigEd/ ({self._count_files(dest)} files)")

    def _step_write_config(self):
        """Write fleet.toml pre-configured for offline/air-gap mode."""
        fleet_toml = self.bigdcc_root / "fleet" / "fleet.toml"
        if not fleet_toml.exists():
            self._log("  WARNING: fleet.toml not found in copied fleet/")
            return

        content = fleet_toml.read_text(encoding="utf-8")

        # Patch offline and air-gap settings
        replacements = {
            "offline_mode = false": "offline_mode = true",
            "air_gap_mode = false": "air_gap_mode = true",
            "api_keys_required = true": "api_keys_required = false",
            "discord_bot_enabled = true": "discord_bot_enabled = false",
            "openclaw_enabled = true": "openclaw_enabled = false",
        }
        for old, new in replacements.items():
            content = content.replace(old, new)

        fleet_toml.write_text(content, encoding="utf-8")
        self._log("  fleet.toml patched for offline/air-gap mode")

    def _step_bundle_requirements(self):
        """Copy requirements.txt files into deps/ folder."""
        deps_dir = self.bigdcc_root / "deps"
        deps_dir.mkdir(parents=True, exist_ok=True)

        # Merge requirements from both fleet and launcher
        all_reqs = set()
        for req_file in [
            FLEET_DIR / "requirements.txt",
            HERE / "requirements.txt",
        ]:
            if req_file.exists():
                for line in req_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        all_reqs.add(line)

        # Write merged requirements
        merged = deps_dir / "requirements.txt"
        merged.write_text(
            "# BigEd CC — merged requirements for offline install\n"
            "# Generated by USB Media Creator\n"
            + "\n".join(sorted(all_reqs)) + "\n",
            encoding="utf-8",
        )

        # Create wheels directory placeholder
        wheels_dir = deps_dir / "wheels"
        wheels_dir.mkdir(exist_ok=True)
        (wheels_dir / ".gitkeep").write_text(
            "# Place .whl files here for offline pip install\n"
            "# Generate with: pip download -r requirements.txt -d wheels/\n",
            encoding="utf-8",
        )

        self._log(f"  Bundled {len(all_reqs)} requirements")

    def _step_write_scripts(self):
        """Write install.bat (Windows) and install.sh (Linux/macOS)."""
        self._write_install_bat()
        self._write_install_sh()
        self._log("  Created install.bat and install.sh")

    def _write_install_bat(self):
        """Windows one-click installer batch script."""
        bat = self.bigdcc_root / "install.bat"
        bat.write_text(
            '@echo off\r\n'
            'setlocal EnableDelayedExpansion\r\n'
            'title BigEd CC — Offline Installer\r\n'
            'color 0A\r\n'
            'echo.\r\n'
            'echo  =========================================\r\n'
            'echo   BigEd CC — Offline Installer\r\n'
            'echo   Version: ' + APP_VERSION + '\r\n'
            'echo  =========================================\r\n'
            'echo.\r\n'
            '\r\n'
            'REM --- Check admin rights ---\r\n'
            'net session >nul 2>&1\r\n'
            'if %errorlevel% neq 0 (\r\n'
            '    echo  [!] Administrator rights recommended for system-wide install.\r\n'
            '    echo      Right-click and "Run as administrator" for best results.\r\n'
            '    echo.\r\n'
            ')\r\n'
            '\r\n'
            'REM --- Locate this script ---\r\n'
            'set "MEDIA_DIR=%~dp0"\r\n'
            'set "INSTALL_DIR=%PROGRAMFILES%\\BigEdCC"\r\n'
            'echo  Install source: %MEDIA_DIR%\r\n'
            'echo  Install target: %INSTALL_DIR%\r\n'
            'echo.\r\n'
            '\r\n'
            'REM --- Step 1: Check for Python ---\r\n'
            'echo  [1/6] Checking Python...\r\n'
            'where python >nul 2>&1\r\n'
            'if %errorlevel% equ 0 (\r\n'
            '    for /f "tokens=2" %%V in (\'python --version 2^>^&1\') do set PYVER=%%V\r\n'
            '    echo        Found Python !PYVER!\r\n'
            '    set "PYTHON_CMD=python"\r\n'
            ') else (\r\n'
            '    echo        Python not found on PATH.\r\n'
            '    if exist "%MEDIA_DIR%deps\\python-embed\\python.exe" (\r\n'
            '        echo        Using bundled Python embeddable...\r\n'
            '        set "PYTHON_CMD=%MEDIA_DIR%deps\\python-embed\\python.exe"\r\n'
            '    ) else (\r\n'
            '        echo  [X] Python not found and no embedded Python bundled.\r\n'
            '        echo      Install Python 3.11+ from https://python.org/downloads/\r\n'
            '        echo      or re-create the USB media with "Include Python" checked.\r\n'
            '        pause\r\n'
            '        exit /b 1\r\n'
            '    )\r\n'
            ')\r\n'
            'echo.\r\n'
            '\r\n'
            'REM --- Step 2: Check for Ollama ---\r\n'
            'echo  [2/6] Checking Ollama...\r\n'
            'where ollama >nul 2>&1\r\n'
            'if %errorlevel% equ 0 (\r\n'
            '    echo        Ollama found on PATH.\r\n'
            ') else (\r\n'
            '    if exist "%LOCALAPPDATA%\\Programs\\Ollama\\ollama.exe" (\r\n'
            '        echo        Ollama found at default location.\r\n'
            '    ) else if exist "%MEDIA_DIR%deps\\ollama\\OllamaSetup.exe" (\r\n'
            '        echo        Installing Ollama from media...\r\n'
            '        start /wait "" "%MEDIA_DIR%deps\\ollama\\OllamaSetup.exe" /SILENT\r\n'
            '        echo        Ollama installed.\r\n'
            '    ) else (\r\n'
            '        echo  [!] Ollama not found. Local AI models will not work.\r\n'
            '        echo      Install from https://ollama.com or re-create media.\r\n'
            '    )\r\n'
            ')\r\n'
            'echo.\r\n'
            '\r\n'
            'REM --- Step 3: Create install directory ---\r\n'
            'echo  [3/6] Creating install directory...\r\n'
            'if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"\r\n'
            'echo        %INSTALL_DIR%\r\n'
            'echo.\r\n'
            '\r\n'
            'REM --- Step 4: Copy application files ---\r\n'
            'echo  [4/6] Copying application files...\r\n'
            'xcopy /E /I /Y /Q "%MEDIA_DIR%fleet" "%INSTALL_DIR%\\fleet"\r\n'
            'xcopy /E /I /Y /Q "%MEDIA_DIR%BigEd" "%INSTALL_DIR%\\BigEd"\r\n'
            'if exist "%MEDIA_DIR%deps" xcopy /E /I /Y /Q "%MEDIA_DIR%deps" "%INSTALL_DIR%\\deps"\r\n'
            'if exist "%MEDIA_DIR%models" xcopy /E /I /Y /Q "%MEDIA_DIR%models" "%INSTALL_DIR%\\models"\r\n'
            'echo        Files copied.\r\n'
            'echo.\r\n'
            '\r\n'
            'REM --- Step 5: Install Python packages ---\r\n'
            'echo  [5/6] Installing Python packages...\r\n'
            'if exist "%INSTALL_DIR%\\deps\\wheels" (\r\n'
            '    dir /b "%INSTALL_DIR%\\deps\\wheels\\*.whl" >nul 2>&1\r\n'
            '    if !errorlevel! equ 0 (\r\n'
            '        echo        Installing from bundled wheels (offline)...\r\n'
            '        !PYTHON_CMD! -m pip install -r "%INSTALL_DIR%\\deps\\requirements.txt" '
            '--no-index --find-links "%INSTALL_DIR%\\deps\\wheels" --quiet\r\n'
            '    ) else (\r\n'
            '        echo        No wheel files found. Attempting online install...\r\n'
            '        !PYTHON_CMD! -m pip install -r "%INSTALL_DIR%\\deps\\requirements.txt" --quiet\r\n'
            '    )\r\n'
            ') else (\r\n'
            '    echo        Attempting online install...\r\n'
            '    !PYTHON_CMD! -m pip install -r "%INSTALL_DIR%\\deps\\requirements.txt" --quiet\r\n'
            ')\r\n'
            'echo.\r\n'
            '\r\n'
            'REM --- Step 6: Import Ollama models ---\r\n'
            'echo  [6/6] Checking for bundled Ollama models...\r\n'
            'if exist "%MEDIA_DIR%models" (\r\n'
            '    if exist "%USERPROFILE%\\.ollama\\models" (\r\n'
            '        echo        Ollama models directory exists, copying bundled models...\r\n'
            '    ) else (\r\n'
            '        mkdir "%USERPROFILE%\\.ollama\\models"\r\n'
            '    )\r\n'
            '    xcopy /E /I /Y /Q "%MEDIA_DIR%models" "%USERPROFILE%\\.ollama\\models"\r\n'
            '    echo        Models imported.\r\n'
            ') else (\r\n'
            '    echo        No bundled models found. Pull models manually:\r\n'
            '    echo          ollama pull qwen3:8b\r\n'
            ')\r\n'
            'echo.\r\n'
            '\r\n'
            'REM --- Run cross-platform installer for registry/shortcuts ---\r\n'
            'if exist "%INSTALL_DIR%\\BigEd\\launcher\\installer_cross.py" (\r\n'
            '    echo  Running system registration...\r\n'
            '    !PYTHON_CMD! "%INSTALL_DIR%\\BigEd\\launcher\\installer_cross.py" install\r\n'
            ')\r\n'
            'echo.\r\n'
            '\r\n'
            'echo  =========================================\r\n'
            'echo   Installation complete!\r\n'
            'echo.\r\n'
            'echo   To launch BigEd CC:\r\n'
            'echo     !PYTHON_CMD! "%INSTALL_DIR%\\BigEd\\launcher\\launcher.py"\r\n'
            'echo.\r\n'
            'echo   Fleet is pre-configured for offline mode.\r\n'
            'echo   Edit fleet\\fleet.toml to change settings.\r\n'
            'echo  =========================================\r\n'
            'echo.\r\n'
            'pause\r\n',
            encoding="utf-8",
        )

    def _write_install_sh(self):
        """Linux/macOS one-click installer shell script."""
        sh = self.bigdcc_root / "install.sh"
        sh.write_text(
            '#!/usr/bin/env bash\n'
            'set -euo pipefail\n'
            '\n'
            'echo ""\n'
            'echo "  ========================================="\n'
            'echo "   BigEd CC -- Offline Installer"\n'
            'echo "   Version: ' + APP_VERSION + '"\n'
            'echo "  ========================================="\n'
            'echo ""\n'
            '\n'
            'MEDIA_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
            'INSTALL_DIR="${HOME}/.local/share/BigEdCC"\n'
            '\n'
            'echo "  Install source: ${MEDIA_DIR}"\n'
            'echo "  Install target: ${INSTALL_DIR}"\n'
            'echo ""\n'
            '\n'
            '# --- Step 1: Check Python ---\n'
            'echo "  [1/5] Checking Python..."\n'
            'PYTHON_CMD=""\n'
            'for cmd in python3 python; do\n'
            '    if command -v "$cmd" &>/dev/null; then\n'
            '        ver=$("$cmd" --version 2>&1 | awk \'{print $2}\')\n'
            '        major=$(echo "$ver" | cut -d. -f1)\n'
            '        minor=$(echo "$ver" | cut -d. -f2)\n'
            '        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then\n'
            '            PYTHON_CMD="$cmd"\n'
            '            echo "        Found $cmd $ver"\n'
            '            break\n'
            '        fi\n'
            '    fi\n'
            'done\n'
            '\n'
            'if [ -z "$PYTHON_CMD" ]; then\n'
            '    echo "  [X] Python 3.11+ not found."\n'
            '    echo "      Install Python: https://python.org/downloads/"\n'
            '    exit 1\n'
            'fi\n'
            'echo ""\n'
            '\n'
            '# --- Step 2: Check Ollama ---\n'
            'echo "  [2/5] Checking Ollama..."\n'
            'if command -v ollama &>/dev/null; then\n'
            '    echo "        Ollama found."\n'
            'elif [ -f "${MEDIA_DIR}/deps/ollama/ollama" ]; then\n'
            '    echo "        Installing Ollama from media..."\n'
            '    sudo install -m 755 "${MEDIA_DIR}/deps/ollama/ollama" /usr/local/bin/ollama\n'
            '    echo "        Ollama installed to /usr/local/bin/"\n'
            'else\n'
            '    echo "  [!] Ollama not found. Install from https://ollama.com"\n'
            'fi\n'
            'echo ""\n'
            '\n'
            '# --- Step 3: Create install directory ---\n'
            'echo "  [3/5] Creating install directory..."\n'
            'mkdir -p "${INSTALL_DIR}"\n'
            'echo "        ${INSTALL_DIR}"\n'
            'echo ""\n'
            '\n'
            '# --- Step 4: Copy files ---\n'
            'echo "  [4/5] Copying application files..."\n'
            'cp -r "${MEDIA_DIR}/fleet" "${INSTALL_DIR}/"\n'
            'cp -r "${MEDIA_DIR}/BigEd" "${INSTALL_DIR}/"\n'
            '[ -d "${MEDIA_DIR}/deps" ] && cp -r "${MEDIA_DIR}/deps" "${INSTALL_DIR}/"\n'
            '[ -d "${MEDIA_DIR}/models" ] && cp -r "${MEDIA_DIR}/models" "${INSTALL_DIR}/"\n'
            'echo "        Files copied."\n'
            'echo ""\n'
            '\n'
            '# --- Step 5: Install Python packages ---\n'
            'echo "  [5/5] Installing Python packages..."\n'
            'if [ -d "${INSTALL_DIR}/deps/wheels" ] && ls "${INSTALL_DIR}/deps/wheels/"*.whl '
            '&>/dev/null; then\n'
            '    echo "        Installing from bundled wheels (offline)..."\n'
            '    $PYTHON_CMD -m pip install -r "${INSTALL_DIR}/deps/requirements.txt" \\\n'
            '        --no-index --find-links "${INSTALL_DIR}/deps/wheels" --quiet 2>/dev/null || true\n'
            'else\n'
            '    echo "        Attempting online install..."\n'
            '    $PYTHON_CMD -m pip install -r "${INSTALL_DIR}/deps/requirements.txt" --quiet '
            '2>/dev/null || true\n'
            'fi\n'
            '\n'
            '# Import bundled models\n'
            'if [ -d "${MEDIA_DIR}/models" ]; then\n'
            '    echo "  Importing bundled Ollama models..."\n'
            '    mkdir -p "${HOME}/.ollama/models"\n'
            '    cp -r "${MEDIA_DIR}/models/"* "${HOME}/.ollama/models/" 2>/dev/null || true\n'
            'fi\n'
            '\n'
            '# Run cross-platform installer\n'
            'if [ -f "${INSTALL_DIR}/BigEd/launcher/installer_cross.py" ]; then\n'
            '    echo "  Running system registration..."\n'
            '    $PYTHON_CMD "${INSTALL_DIR}/BigEd/launcher/installer_cross.py" install\n'
            'fi\n'
            '\n'
            'echo ""\n'
            'echo "  ========================================="\n'
            'echo "   Installation complete!"\n'
            'echo ""\n'
            'echo "   To launch BigEd CC:"\n'
            'echo "     $PYTHON_CMD ${INSTALL_DIR}/BigEd/launcher/launcher.py"\n'
            'echo ""\n'
            'echo "   Fleet is pre-configured for offline mode."\n'
            'echo "   Edit fleet/fleet.toml to change settings."\n'
            'echo "  ========================================="\n'
            'echo ""\n',
            encoding="utf-8",
        )
        # Make executable
        try:
            sh.chmod(0o755)
        except Exception:
            pass

    def _step_write_docs(self):
        """Write README.txt and LICENSE."""
        # README
        readme = self.bigdcc_root / "README.txt"
        readme.write_text(
            "BigEd CC — Offline Installer Media\n"
            "===================================\n"
            f"Version: {APP_VERSION}\n"
            f"Created: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            "\n"
            "Quick Start\n"
            "-----------\n"
            "Windows:\n"
            "  1. Open this folder in Explorer\n"
            "  2. Double-click install.bat\n"
            "  3. Follow the on-screen prompts\n"
            "\n"
            "Linux / macOS:\n"
            "  1. Open a terminal in this directory\n"
            "  2. Run: chmod +x install.sh && ./install.sh\n"
            "  3. Follow the on-screen prompts\n"
            "\n"
            "What Gets Installed\n"
            "-------------------\n"
            "  - fleet/     AI agent fleet (80+ skills, local Ollama inference)\n"
            "  - BigEd/     Launcher GUI + configuration\n"
            "  - deps/      Python package dependencies\n"
            "  - models/    Pre-downloaded Ollama models (if bundled)\n"
            "\n"
            "Prerequisites\n"
            "-------------\n"
            "  - Python 3.11+ (bundled embed version may be included)\n"
            "  - Ollama (bundled installer may be included)\n"
            "  - 8 GB RAM minimum (16 GB recommended)\n"
            "\n"
            "Offline Mode\n"
            "------------\n"
            "This media is pre-configured for offline/air-gap deployment.\n"
            "fleet.toml has offline_mode=true and air_gap_mode=true.\n"
            "No internet connection is required after installation.\n"
            "\n"
            "License\n"
            "-------\n"
            "Apache License 2.0 — See LICENSE file.\n"
            "All bundled dependencies are Apache/MIT/BSD licensed.\n",
            encoding="utf-8",
        )

        # LICENSE (Apache 2.0)
        license_file = self.bigdcc_root / "LICENSE"
        license_file.write_text(
            "                              Apache License\n"
            "                        Version 2.0, January 2004\n"
            "                     http://www.apache.org/licenses/\n"
            "\n"
            "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION\n"
            "\n"
            '1. Definitions.\n'
            '\n'
            '   "License" shall mean the terms and conditions for use, reproduction,\n'
            '   and distribution as defined by Sections 1 through 9 of this document.\n'
            '\n'
            '   "Licensor" shall mean the copyright owner or entity authorized by\n'
            '   the copyright owner that is granting the License.\n'
            '\n'
            '   "Legal Entity" shall mean the union of the acting entity and all\n'
            '   other entities that control, are controlled by, or are under common\n'
            '   control with that entity. For the purposes of this definition,\n'
            '   "control" means (i) the power, direct or indirect, to cause the\n'
            '   direction or management of such entity, whether by contract or\n'
            '   otherwise, or (ii) ownership of fifty percent (50%) or more of the\n'
            '   outstanding shares, or (iii) beneficial ownership of such entity.\n'
            '\n'
            '   "You" (or "Your") shall mean an individual or Legal Entity\n'
            '   exercising permissions granted by this License.\n'
            '\n'
            '   "Source" form shall mean the preferred form for making modifications,\n'
            '   including but not limited to software source code, documentation\n'
            '   source, and configuration files.\n'
            '\n'
            '   "Object" form shall mean any form resulting from mechanical\n'
            '   transformation or translation of a Source form, including but\n'
            '   not limited to compiled object code, generated documentation,\n'
            '   and conversions to other media types.\n'
            '\n'
            '   "Work" shall mean the work of authorship, whether in Source or\n'
            '   Object form, made available under the License, as indicated by a\n'
            '   copyright notice that is included in or attached to the work.\n'
            '\n'
            '   "Derivative Works" shall mean any work, whether in Source or Object\n'
            '   form, that is based on (or derived from) the Work and for which the\n'
            '   editorial revisions, annotations, elaborations, or other modifications\n'
            '   represent, as a whole, an original work of authorship. For the purposes\n'
            '   of this License, Derivative Works shall not include works that remain\n'
            '   separable from, or merely link (or bind by name) to the interfaces of,\n'
            '   the Work and Derivative Works thereof.\n'
            '\n'
            '   "Contribution" shall mean any work of authorship, including\n'
            '   the original version of the Work and any modifications or additions\n'
            '   to that Work or Derivative Works thereof, that is intentionally\n'
            '   submitted to the Licensor for inclusion in the Work by the copyright owner\n'
            '   or by an individual or Legal Entity authorized to submit on behalf of\n'
            '   the copyright owner. For the purposes of this definition, "submitted"\n'
            '   means any form of electronic, verbal, or written communication sent\n'
            '   to the Licensor or its representatives, including but not limited to\n'
            '   communication on electronic mailing lists, source code control systems,\n'
            '   and issue tracking systems that are managed by, or on behalf of, the\n'
            '   Licensor for the purpose of discussing and improving the Work, but\n'
            '   excluding communication that is conspicuously marked or otherwise\n'
            '   designated in writing by the copyright owner as "Not a Contribution."\n'
            '\n'
            '   "Contributor" shall mean Licensor and any individual or Legal Entity\n'
            '   on behalf of whom a Contribution has been received by the Licensor and\n'
            '   subsequently incorporated within the Work.\n'
            '\n'
            '2. Grant of Copyright License. Subject to the terms and conditions of\n'
            '   this License, each Contributor hereby grants to You a perpetual,\n'
            '   worldwide, non-exclusive, no-charge, royalty-free, irrevocable\n'
            '   copyright license to reproduce, prepare Derivative Works of,\n'
            '   publicly display, publicly perform, sublicense, and distribute the\n'
            '   Work and such Derivative Works in Source or Object form.\n'
            '\n'
            '3. Grant of Patent License. Subject to the terms and conditions of\n'
            '   this License, each Contributor hereby grants to You a perpetual,\n'
            '   worldwide, non-exclusive, no-charge, royalty-free, irrevocable\n'
            '   (except as stated in this section) patent license to make, have made,\n'
            '   use, offer to sell, sell, import, and otherwise transfer the Work,\n'
            '   where such license applies only to those patent claims licensable\n'
            '   by such Contributor that are necessarily infringed by their\n'
            '   Contribution(s) alone or by combination of their Contribution(s)\n'
            '   with the Work to which such Contribution(s) was submitted. If You\n'
            '   institute patent litigation against any entity (including a\n'
            '   cross-claim or counterclaim in a lawsuit) alleging that the Work\n'
            '   or a Contribution incorporated within the Work constitutes direct\n'
            '   or contributory patent infringement, then any patent licenses\n'
            '   granted to You under this License for that Work shall terminate\n'
            '   as of the date such litigation is filed.\n'
            '\n'
            '4. Redistribution. You may reproduce and distribute copies of the\n'
            '   Work or Derivative Works thereof in any medium, with or without\n'
            '   modifications, and in Source or Object form, provided that You\n'
            '   meet the following conditions:\n'
            '\n'
            '   (a) You must give any other recipients of the Work or\n'
            '       Derivative Works a copy of this License; and\n'
            '\n'
            '   (b) You must cause any modified files to carry prominent notices\n'
            '       stating that You changed the files; and\n'
            '\n'
            '   (c) You must retain, in the Source form of any Derivative Works\n'
            '       that You distribute, all copyright, patent, trademark, and\n'
            '       attribution notices from the Source form of the Work,\n'
            '       excluding those notices that do not pertain to any part of\n'
            '       the Derivative Works; and\n'
            '\n'
            '   (d) If the Work includes a "NOTICE" text file as part of its\n'
            '       distribution, then any Derivative Works that You distribute must\n'
            '       include a readable copy of the attribution notices contained\n'
            '       within such NOTICE file, excluding any notices that do not\n'
            '       pertain to any part of the Derivative Works, in at least one\n'
            '       of the following places: within a NOTICE text file distributed\n'
            '       as part of the Derivative Works; within the Source form or\n'
            '       documentation, if provided along with the Derivative Works; or,\n'
            '       within a display generated by the Derivative Works, if and\n'
            '       wherever such third-party notices normally appear. The contents\n'
            '       of the NOTICE file are for informational purposes only and\n'
            '       do not modify the License. You may add Your own attribution\n'
            '       notices within Derivative Works that You distribute, alongside\n'
            '       or as an addendum to the NOTICE text from the Work, provided\n'
            '       that such additional attribution notices cannot be construed\n'
            '       as modifying the License.\n'
            '\n'
            '   You may add Your own copyright statement to Your modifications and\n'
            '   may provide additional or different license terms and conditions\n'
            '   for use, reproduction, or distribution of Your modifications, or\n'
            '   for any such Derivative Works as a whole, provided Your use,\n'
            '   reproduction, and distribution of the Work otherwise complies with\n'
            '   the conditions stated in this License.\n'
            '\n'
            '5. Submission of Contributions. Unless You explicitly state otherwise,\n'
            '   any Contribution intentionally submitted for inclusion in the Work\n'
            '   by You to the Licensor shall be under the terms and conditions of\n'
            '   this License, without any additional terms or conditions.\n'
            '   Notwithstanding the above, nothing herein shall supersede or modify\n'
            '   the terms of any separate license agreement you may have executed\n'
            '   with Licensor regarding such Contributions.\n'
            '\n'
            '6. Trademarks. This License does not grant permission to use the trade\n'
            '   names, trademarks, service marks, or product names of the Licensor,\n'
            '   except as required for reasonable and customary use in describing the\n'
            '   origin of the Work and reproducing the content of the NOTICE file.\n'
            '\n'
            '7. Disclaimer of Warranty. Unless required by applicable law or\n'
            '   agreed to in writing, Licensor provides the Work (and each\n'
            '   Contributor provides its Contributions) on an "AS IS" BASIS,\n'
            '   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or\n'
            '   implied, including, without limitation, any warranties or conditions\n'
            '   of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A\n'
            '   PARTICULAR PURPOSE. You are solely responsible for determining the\n'
            '   appropriateness of using or redistributing the Work and assume any\n'
            '   risks associated with Your exercise of permissions under this License.\n'
            '\n'
            '8. Limitation of Liability. In no event and under no legal theory,\n'
            '   whether in tort (including negligence), contract, or otherwise,\n'
            '   unless required by applicable law (such as deliberate and grossly\n'
            '   negligent acts) or agreed to in writing, shall any Contributor be\n'
            '   liable to You for damages, including any direct, indirect, special,\n'
            '   incidental, or consequential damages of any character arising as a\n'
            '   result of this License or out of the use or inability to use the\n'
            '   Work (including but not limited to damages for loss of goodwill,\n'
            '   work stoppage, computer failure or malfunction, or any and all\n'
            '   other commercial damages or losses), even if such Contributor\n'
            '   has been advised of the possibility of such damages.\n'
            '\n'
            '9. Accepting Warranty or Additional Liability. While redistributing\n'
            '   the Work or Derivative Works thereof, You may choose to offer,\n'
            '   and charge a fee for, acceptance of support, warranty, indemnity,\n'
            '   or other liability obligations and/or rights consistent with this\n'
            '   License. However, in accepting such obligations, You may act only\n'
            '   on Your own behalf and on Your sole responsibility, not on behalf\n'
            '   of any other Contributor, and only if You agree to indemnify,\n'
            '   defend, and hold each Contributor harmless for any liability\n'
            '   incurred by, or claims asserted against, such Contributor by reason\n'
            '   of your accepting any such warranty or additional liability.\n'
            '\n'
            'END OF TERMS AND CONDITIONS\n'
            '\n'
            f'Copyright {datetime.now().year} Max\'s Home Lab\n'
            '\n'
            'Licensed under the Apache License, Version 2.0 (the "License");\n'
            'you may not use this file except in compliance with the License.\n'
            'You may obtain a copy of the License at\n'
            '\n'
            '    http://www.apache.org/licenses/LICENSE-2.0\n'
            '\n'
            'Unless required by applicable law or agreed to in writing, software\n'
            'distributed under the License is distributed on an "AS IS" BASIS,\n'
            'WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.\n'
            'See the License for the specific language governing permissions and\n'
            'limitations under the License.\n',
            encoding="utf-8",
        )
        self._log("  Created README.txt and LICENSE")

    def _step_bundle_python(self):
        """Bundle Python embeddable (Windows only, or download for target)."""
        deps_dir = self.bigdcc_root / "deps"
        python_dir = deps_dir / "python-embed"
        python_dir.mkdir(parents=True, exist_ok=True)

        if sys.platform == "win32":
            # Try to download the embeddable package
            zip_path = deps_dir / "python-embed.zip"
            self._log(f"  Downloading Python {PYTHON_EMBED_VERSION} embeddable...")
            ok = _download_file(
                PYTHON_EMBED_URL, zip_path,
                progress_cb=lambda p: self._progress(0.60 + p * 0.08, f"Downloading Python... {p*100:.0f}%"),
            )
            if ok and zip_path.exists():
                import zipfile
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(python_dir)
                zip_path.unlink()
                self._log(f"  Extracted Python embeddable ({self._count_files(python_dir)} files)")
            else:
                self._log("  WARNING: Failed to download Python embeddable.")
                self._log("  Users will need Python 3.11+ installed on the target machine.")
        else:
            self._log("  Python embed is Windows-only; Linux/macOS use system Python.")
            (python_dir / "NOTE.txt").write_text(
                "Python embeddable is available for Windows only.\n"
                "On Linux/macOS, install Python 3.11+ via your package manager.\n",
                encoding="utf-8",
            )

    def _step_bundle_ollama(self):
        """Bundle Ollama binary — copy local or download."""
        deps_dir = self.bigdcc_root / "deps"
        ollama_dir = deps_dir / "ollama"
        ollama_dir.mkdir(parents=True, exist_ok=True)

        # First: try to copy locally installed Ollama
        local_ollama = _find_ollama_binary()
        if local_ollama and local_ollama.exists():
            if sys.platform == "win32":
                # On Windows, copy the whole Ollama directory (it has DLLs)
                ollama_parent = local_ollama.parent
                if ollama_parent.name == "Ollama" or ollama_parent.name == "ollama":
                    self._log(f"  Copying Ollama from {ollama_parent}...")
                    for item in ollama_parent.iterdir():
                        if item.is_file():
                            try:
                                shutil.copy2(item, ollama_dir / item.name)
                            except Exception:
                                pass
                else:
                    shutil.copy2(local_ollama, ollama_dir / local_ollama.name)
            else:
                shutil.copy2(local_ollama, ollama_dir / "ollama")
                (ollama_dir / "ollama").chmod(0o755)
            self._log(f"  Copied local Ollama ({self._count_files(ollama_dir)} files)")
            return

        # Fallback: download
        download_url = OLLAMA_URLS.get(sys.platform)
        if download_url:
            if sys.platform == "win32":
                dest = ollama_dir / "OllamaSetup.exe"
            elif sys.platform == "darwin":
                dest = ollama_dir / "Ollama-darwin.zip"
            else:
                dest = ollama_dir / "ollama"

            self._log(f"  Downloading Ollama from {download_url}...")
            ok = _download_file(
                download_url, dest,
                progress_cb=lambda p: self._progress(0.70 + p * 0.08, f"Downloading Ollama... {p*100:.0f}%"),
            )
            if ok:
                if sys.platform not in ("win32", "darwin"):
                    dest.chmod(0o755)
                self._log(f"  Downloaded Ollama ({dest.stat().st_size / (1024*1024):.1f} MB)")
            else:
                self._log("  WARNING: Failed to download Ollama.")
                self._log("  Users will need to install Ollama manually.")
        else:
            self._log(f"  WARNING: No Ollama download URL for platform '{sys.platform}'")

    def _step_copy_models(self):
        """Copy Ollama models from local cache."""
        models_dir = self.bigdcc_root / "models"
        models_dir.mkdir(parents=True, exist_ok=True)

        src = _get_ollama_models_dir()
        if not src or not src.exists():
            self._log("  WARNING: Ollama models directory not found.")
            self._log("  Users will need to pull models manually: ollama pull qwen3:8b")
            (models_dir / ".gitkeep").touch()
            return

        self._log(f"  Copying models from {src}...")
        self._log("  (This may take several minutes for large models)")

        try:
            shutil.copytree(src, models_dir, dirs_exist_ok=True)
            size_gb = _dir_size_gb(models_dir)
            self._log(f"  Copied models ({size_gb:.1f} GB)")
        except Exception as e:
            self._log(f"  WARNING: Model copy failed: {e}")
            self._log("  Users will need to pull models manually.")

    def _step_write_markers(self):
        """Write autorun.inf and .biged-usb-media marker."""
        # autorun.inf (Windows)
        autorun = self.target / "autorun.inf"
        autorun.write_text(
            "[autorun]\r\n"
            "label=BigEd CC Installer\r\n"
            "icon=BigEdCC\\BigEd\\launcher\\brick.ico\r\n"
            "open=BigEdCC\\install.bat\r\n"
            "action=Install BigEd CC\r\n",
            encoding="utf-8",
        )

        # Marker file
        marker = self.target / ".biged-usb-media"
        marker_data = {
            "version": APP_VERSION,
            "media_version": MEDIA_VERSION,
            "created": datetime.now(timezone.utc).isoformat(),
            "created_by": platform.node(),
            "platform": sys.platform,
            "python_bundled": self.include_python,
            "ollama_bundled": self.include_ollama,
            "models_bundled": self.include_models,
            "model_list": self.model_list if self.include_models else [],
        }
        marker.write_text(json.dumps(marker_data, indent=2), encoding="utf-8")
        self._log("  Created autorun.inf and .biged-usb-media marker")

    def _step_verify(self):
        """Verify the media was written correctly."""
        errors = []

        # Check marker exists and is readable
        marker = self.target / ".biged-usb-media"
        if not marker.exists():
            errors.append("Missing .biged-usb-media marker")
        else:
            try:
                data = json.loads(marker.read_text(encoding="utf-8"))
                if data.get("version") != APP_VERSION:
                    errors.append("Marker version mismatch")
            except Exception as e:
                errors.append(f"Marker unreadable: {e}")

        # Check critical directories
        for required_dir in ["fleet", "BigEd"]:
            d = self.bigdcc_root / required_dir
            if not d.exists() or not d.is_dir():
                errors.append(f"Missing directory: {required_dir}/")

        # Check install scripts
        for script in ["install.bat", "install.sh"]:
            s = self.bigdcc_root / script
            if not s.exists():
                errors.append(f"Missing install script: {script}")

        # Check fleet.toml was patched
        fleet_toml = self.bigdcc_root / "fleet" / "fleet.toml"
        if fleet_toml.exists():
            content = fleet_toml.read_text(encoding="utf-8")
            if "offline_mode = true" not in content:
                errors.append("fleet.toml not patched for offline mode")

        if errors:
            for e in errors:
                self._log(f"  VERIFY FAIL: {e}")
            self._log(f"  Verification completed with {len(errors)} error(s)")
        else:
            file_count = sum(1 for _ in self.bigdcc_root.rglob("*") if _.is_file())
            self._log(f"  Verification passed ({file_count} files)")

    def _count_files(self, path: Path) -> int:
        """Count files recursively."""
        try:
            return sum(1 for _ in path.rglob("*") if _.is_file())
        except Exception:
            return 0


# ── ISO Creation ─────────────────────────────────────────────────────────────

def create_iso(source_dir: Path, iso_path: Path, label: str = "BIGEDCC") -> bool:
    """
    Create ISO image from a directory.
    Uses mkisofs/genisoimage on Linux, or PowerShell on Windows.
    Returns True on success.
    """
    if sys.platform == "win32":
        return _create_iso_windows(source_dir, iso_path, label)
    else:
        return _create_iso_posix(source_dir, iso_path, label)


def _create_iso_windows(source_dir: Path, iso_path: Path, label: str) -> bool:
    """Create ISO on Windows using PowerShell and .NET."""
    # Use oscdimg if available (Windows ADK), otherwise use a PowerShell approach
    oscdimg = shutil.which("oscdimg")
    if oscdimg:
        result = subprocess.run(
            [oscdimg, "-l" + label, "-u2", str(source_dir), str(iso_path)],
            capture_output=True, text=True,
            creationflags=_NO_WINDOW,
        )
        return result.returncode == 0

    # Fallback: simple directory-to-ISO via PowerShell using .NET
    # This creates a basic ISO that can be mounted
    ps_script = f'''
$source = "{source_dir}"
$target = "{iso_path}"
$label = "{label}"

# Use IMAPI2 COM object (available on Windows 8+)
try {{
    $fsi = New-Object -ComObject IMAPI2FS.MsftFileSystemImage
    $fsi.FileSystemsToCreate = 4  # FsiFileSystemISO9660 | FsiFileSystemJoliet
    $fsi.VolumeName = $label
    $fsi.Root.AddTree($source, $false)
    $result = $fsi.CreateResultImage()
    $stream = $result.ImageStream

    $writer = [System.IO.File]::Create($target)
    $buffer = New-Object byte[] 65536
    do {{
        $read = $stream.Read($buffer, 0, $buffer.Length)
        if ($read -gt 0) {{ $writer.Write($buffer, 0, $read) }}
    }} while ($read -gt 0)
    $writer.Close()
    [System.Runtime.Interopservices.Marshal]::ReleaseComObject($stream) | Out-Null
    [System.Runtime.Interopservices.Marshal]::ReleaseComObject($result) | Out-Null
    [System.Runtime.Interopservices.Marshal]::ReleaseComObject($fsi) | Out-Null
    Write-Output "ISO_OK"
}} catch {{
    Write-Error $_.Exception.Message
    exit 1
}}
'''
    result = subprocess.run(
        ["powershell", "-NonInteractive", "-Command", ps_script],
        capture_output=True, text=True, timeout=300,
        creationflags=_NO_WINDOW,
    )
    return "ISO_OK" in result.stdout


def _create_iso_posix(source_dir: Path, iso_path: Path, label: str) -> bool:
    """Create ISO on Linux/macOS using mkisofs or genisoimage."""
    for tool in ["mkisofs", "genisoimage", "xorriso"]:
        exe = shutil.which(tool)
        if exe:
            if tool == "xorriso":
                cmd = [exe, "-as", "mkisofs", "-V", label, "-J", "-R",
                       "-o", str(iso_path), str(source_dir)]
            else:
                cmd = [exe, "-V", label, "-J", "-R",
                       "-o", str(iso_path), str(source_dir)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            return result.returncode == 0

    # macOS: hdiutil
    if sys.platform == "darwin":
        result = subprocess.run(
            ["hdiutil", "makehybrid", "-iso", "-joliet",
             "-o", str(iso_path), str(source_dir)],
            capture_output=True, text=True, timeout=300,
        )
        return result.returncode == 0

    return False


# ── GUI ──────────────────────────────────────────────────────────────────────

def run_gui():
    """Launch the GUI media creator."""
    try:
        import customtkinter as ctk
    except ImportError:
        print("ERROR: customtkinter not installed.")
        print("Install with: pip install customtkinter")
        sys.exit(1)

    # Theme colors — import from ui.theme if available, otherwise use defaults
    try:
        sys.path.insert(0, str(HERE))
        from ui.theme import (
            BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
            GREEN, ORANGE, RED, load_custom_fonts,
        )
    except ImportError:
        BG = "#1a1a1a"
        BG2 = "#242424"
        BG3 = "#2d2d2d"
        ACCENT = "#b22222"
        ACCENT_H = "#8b0000"
        GOLD = "#c8a84b"
        TEXT = "#e2e2e2"
        DIM = "#888888"
        GREEN = "#4caf50"
        ORANGE = "#ff9800"
        RED = "#f44336"
        load_custom_fonts = None

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")

    class USBMediaCreator(ctk.CTk):
        def __init__(self):
            super().__init__()

            self.title("BigEd CC \u2014 USB Media Creator")
            self.geometry("620x580")
            self.resizable(False, False)
            self.configure(fg_color=BG)

            if ICON_ICO.exists():
                try:
                    self.iconbitmap(str(ICON_ICO))
                except Exception:
                    pass

            if load_custom_fonts:
                self.after(50, load_custom_fonts)

            # State
            self._drives: list[dict] = []
            self._selected_drive = ctk.StringVar(value="")
            self._include_python = ctk.BooleanVar(value=True)
            self._include_ollama = ctk.BooleanVar(value=True)
            self._include_models = ctk.BooleanVar(value=False)
            self._builder: MediaBuilder | None = None
            self._building = False

            self._build_header()
            self._build_body()
            self._build_footer()

            # Initial drive scan
            self.after(200, self._refresh_drives)

        def _build_header(self):
            hdr = ctk.CTkFrame(self, fg_color=BG3, height=60, corner_radius=0)
            hdr.pack(fill="x", side="top")
            hdr.pack_propagate(False)

            ctk.CTkLabel(
                hdr, text=APP_NAME,
                font=("Segoe UI", 16, "bold"), text_color=GOLD, anchor="w",
            ).pack(side="left", padx=16, pady=10)

            ctk.CTkLabel(
                hdr, text="USB Media Creator",
                font=("Segoe UI", 11), text_color=DIM, anchor="e",
            ).pack(side="right", padx=16)

        def _build_body(self):
            body = ctk.CTkScrollableFrame(
                self, fg_color=BG, corner_radius=0,
                scrollbar_button_color=BG3, scrollbar_button_hover_color="#444",
            )
            body.pack(fill="both", expand=True)

            # --- Drive Selection ---
            drive_frame = ctk.CTkFrame(body, fg_color=BG2, corner_radius=6)
            drive_frame.pack(fill="x", padx=20, pady=(12, 4))

            ctk.CTkLabel(
                drive_frame, text="Target Drive / Folder",
                font=("Segoe UI", 11, "bold"), text_color=GOLD, anchor="w",
            ).pack(padx=12, pady=(10, 4), anchor="w")

            drive_row = ctk.CTkFrame(drive_frame, fg_color="transparent")
            drive_row.pack(fill="x", padx=12, pady=(0, 4))
            drive_row.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(
                drive_row, text="Drive:", font=("Segoe UI", 10),
                text_color=DIM, width=50, anchor="w",
            ).grid(row=0, column=0, padx=(0, 6), sticky="w")

            self._drive_menu = ctk.CTkOptionMenu(
                drive_row, variable=self._selected_drive,
                values=["Scanning..."],
                fg_color=BG, button_color=BG3, button_hover_color="#444",
                text_color=TEXT, font=("Consolas", 10),
                width=300,
            )
            self._drive_menu.grid(row=0, column=1, sticky="ew", padx=4)

            btn_row = ctk.CTkFrame(drive_row, fg_color="transparent")
            btn_row.grid(row=0, column=2, padx=(4, 0))

            ctk.CTkButton(
                btn_row, text="Refresh", width=65, height=28,
                fg_color=BG3, hover_color=BG, font=("Segoe UI", 9),
                command=self._refresh_drives,
            ).pack(side="left", padx=2)

            ctk.CTkButton(
                btn_row, text="Browse", width=65, height=28,
                fg_color=BG3, hover_color=BG, font=("Segoe UI", 9),
                command=self._browse_folder,
            ).pack(side="left", padx=2)

            self._drive_info = ctk.CTkLabel(
                drive_frame, text="",
                font=("Segoe UI", 9), text_color=DIM, anchor="w",
            )
            self._drive_info.pack(padx=16, pady=(0, 8), anchor="w")

            # --- Bundle Options ---
            opt_frame = ctk.CTkFrame(body, fg_color=BG2, corner_radius=6)
            opt_frame.pack(fill="x", padx=20, pady=4)

            ctk.CTkLabel(
                opt_frame, text="Bundle Options",
                font=("Segoe UI", 11, "bold"), text_color=GOLD, anchor="w",
            ).pack(padx=12, pady=(10, 4), anchor="w")

            ctk.CTkCheckBox(
                opt_frame, text="Include Python embeddable (Windows, ~15 MB)",
                variable=self._include_python,
                font=("Segoe UI", 10), text_color=TEXT,
                fg_color=ACCENT, hover_color=ACCENT_H,
                command=self._update_estimate,
            ).pack(padx=20, pady=3, anchor="w")

            ctk.CTkCheckBox(
                opt_frame, text="Include Ollama binary (~150 MB)",
                variable=self._include_ollama,
                font=("Segoe UI", 10), text_color=TEXT,
                fg_color=ACCENT, hover_color=ACCENT_H,
                command=self._update_estimate,
            ).pack(padx=20, pady=3, anchor="w")

            ctk.CTkCheckBox(
                opt_frame, text="Include Ollama models (qwen3:8b, ~5 GB)",
                variable=self._include_models,
                font=("Segoe UI", 10), text_color=TEXT,
                fg_color=ACCENT, hover_color=ACCENT_H,
                command=self._update_estimate,
            ).pack(padx=20, pady=3, anchor="w")

            self._size_estimate = ctk.CTkLabel(
                opt_frame, text="Estimated size: calculating...",
                font=("Segoe UI", 9), text_color=DIM, anchor="w",
            )
            self._size_estimate.pack(padx=20, pady=(4, 10), anchor="w")

            # --- Warning ---
            warn_frame = ctk.CTkFrame(body, fg_color="#2a1a1a", corner_radius=6)
            warn_frame.pack(fill="x", padx=20, pady=4)

            ctk.CTkLabel(
                warn_frame,
                text="This will write to the selected drive. Any existing BigEdCC/ folder\n"
                     "on the target will be replaced. Other files on the drive are not affected.",
                font=("Segoe UI", 9), text_color=ORANGE, justify="left", anchor="w",
            ).pack(padx=12, pady=8, anchor="w")

            # --- Progress ---
            prog_frame = ctk.CTkFrame(body, fg_color=BG2, corner_radius=6)
            prog_frame.pack(fill="x", padx=20, pady=4)

            self._prog_bar = ctk.CTkProgressBar(
                prog_frame, height=12, corner_radius=4,
                fg_color=BG3, progress_color=ACCENT,
            )
            self._prog_bar.set(0)
            self._prog_bar.pack(fill="x", padx=12, pady=(10, 4))

            self._prog_label = ctk.CTkLabel(
                prog_frame, text="Ready",
                font=("Segoe UI", 9), text_color=DIM, anchor="w",
            )
            self._prog_label.pack(padx=16, pady=(0, 2), anchor="w")

            self._log_box = ctk.CTkTextbox(
                prog_frame, font=("Consolas", 9),
                fg_color=BG, text_color="#aaa",
                height=120, corner_radius=4,
            )
            self._log_box.pack(fill="x", padx=12, pady=(2, 10))
            self._log_box.configure(state="disabled")

            # Update size estimate
            self.after(500, self._update_estimate)

        def _build_footer(self):
            footer = ctk.CTkFrame(self, fg_color=BG3, height=50, corner_radius=0)
            footer.pack(fill="x", side="bottom")
            footer.pack_propagate(False)

            self._create_btn = ctk.CTkButton(
                footer, text="Create Media", width=140, height=34,
                fg_color=ACCENT, hover_color=ACCENT_H,
                font=("Segoe UI", 11, "bold"),
                command=self._start_build,
            )
            self._create_btn.pack(side="right", padx=16, pady=8)

            self._cancel_btn = ctk.CTkButton(
                footer, text="Cancel", width=80, height=34,
                fg_color=BG2, hover_color=BG,
                font=("Segoe UI", 10),
                command=self._cancel_build,
                state="disabled",
            )
            self._cancel_btn.pack(side="right", padx=(0, 8), pady=8)

            ctk.CTkButton(
                footer, text="Close", width=80, height=34,
                fg_color=BG2, hover_color=BG,
                font=("Segoe UI", 10),
                command=self.destroy,
            ).pack(side="left", padx=16, pady=8)

        # ── Drive Management ─────────────────────────────────────────────

        def _refresh_drives(self):
            self._drives = detect_removable_drives()
            if self._drives:
                labels = []
                for d in self._drives:
                    lbl = d.get("label") or d.get("device", "?")
                    free = d.get("free_gb", 0)
                    total = d.get("total_gb", 0)
                    labels.append(f"{d['device']}  ({lbl}, {free:.1f}/{total:.1f} GB free)")
                self._drive_menu.configure(values=labels)
                self._selected_drive.set(labels[0])
                self._drive_info.configure(
                    text=f"Found {len(self._drives)} removable drive(s)",
                    text_color=GREEN,
                )
            else:
                self._drive_menu.configure(values=["No removable drives found"])
                self._selected_drive.set("No removable drives found")
                self._drive_info.configure(
                    text="No removable drives detected. Use 'Browse' to select a folder.",
                    text_color=ORANGE,
                )

        def _browse_folder(self):
            from tkinter import filedialog
            chosen = filedialog.askdirectory(title="Select target folder for USB media")
            if chosen:
                self._selected_drive.set(chosen)
                self._drive_info.configure(
                    text=f"Output folder: {chosen}",
                    text_color=TEXT,
                )

        def _get_target_path(self) -> Optional[Path]:
            """Resolve the selected drive/folder to a Path."""
            sel = self._selected_drive.get()
            if not sel or sel.startswith("No removable") or sel.startswith("Scanning"):
                return None

            # Check if it is a direct path (from Browse)
            if os.path.isdir(sel) or (len(sel) > 3 and not sel.startswith("(")):
                return Path(sel)

            # Parse drive letter from "E:  (label, ...)" format
            if ":" in sel:
                drive_part = sel.split("(")[0].strip()
                if drive_part:
                    return Path(drive_part + "\\")

            return None

        # ── Build Management ─────────────────────────────────────────────

        def _update_estimate(self):
            try:
                size_gb = estimate_media_size(
                    include_python=self._include_python.get(),
                    include_ollama=self._include_ollama.get(),
                    include_models=self._include_models.get(),
                )
                if size_gb < 1:
                    self._size_estimate.configure(text=f"Estimated size: {size_gb*1024:.0f} MB")
                else:
                    self._size_estimate.configure(text=f"Estimated size: {size_gb:.1f} GB")
            except Exception:
                self._size_estimate.configure(text="Estimated size: unknown")

        def _start_build(self):
            target = self._get_target_path()
            if not target:
                self._gui_log("ERROR: No valid target selected. Use Browse to pick a folder.")
                return

            # Check free space
            try:
                if target.exists():
                    usage = shutil.disk_usage(target)
                    free_gb = usage.free / (1024 ** 3)
                    needed = estimate_media_size(
                        self._include_python.get(),
                        self._include_ollama.get(),
                        self._include_models.get(),
                    )
                    if free_gb < needed * 1.1:  # 10% safety margin
                        self._gui_log(
                            f"WARNING: Drive has {free_gb:.1f} GB free, "
                            f"estimated need: {needed:.1f} GB"
                        )
            except Exception:
                pass

            self._building = True
            self._create_btn.configure(state="disabled")
            self._cancel_btn.configure(state="normal")
            self._prog_bar.set(0)
            self._prog_label.configure(text="Starting build...")

            self._builder = MediaBuilder(
                target_dir=target,
                include_python_embed=self._include_python.get(),
                include_ollama=self._include_ollama.get(),
                include_models=self._include_models.get(),
                on_progress=self._on_progress,
                on_log=self._on_log,
                on_complete=self._on_complete,
            )

            thread = threading.Thread(target=self._builder.build, daemon=True)
            thread.start()

        def _cancel_build(self):
            if self._builder:
                self._builder.cancel()
                self._gui_log("Cancelling...")

        def _on_progress(self, pct: float, msg: str):
            """Called from builder thread — schedule UI update."""
            self.after(0, lambda: self._gui_progress(pct, msg))

        def _on_log(self, msg: str):
            """Called from builder thread — schedule UI log update."""
            self.after(0, lambda: self._gui_log(msg))

        def _on_complete(self, ok: bool, msg: str):
            """Called from builder thread — schedule UI completion."""
            self.after(0, lambda: self._gui_complete(ok, msg))

        def _gui_progress(self, pct: float, msg: str):
            self._prog_bar.set(pct)
            self._prog_label.configure(text=msg)

        def _gui_log(self, msg: str):
            ts = datetime.now().strftime("%H:%M:%S")
            self._log_box.configure(state="normal")
            self._log_box.insert("end", f"[{ts}] {msg}\n")
            self._log_box.see("end")
            self._log_box.configure(state="disabled")

        def _gui_complete(self, ok: bool, msg: str):
            self._building = False
            self._create_btn.configure(state="normal")
            self._cancel_btn.configure(state="disabled")

            if ok:
                self._prog_bar.set(1.0)
                self._prog_label.configure(text="Complete!", text_color=GREEN)
                self._gui_log(f"SUCCESS: {msg}")
            else:
                self._prog_label.configure(text="Failed", text_color=RED)
                self._gui_log(f"FAILED: {msg}")

    app = USBMediaCreator()
    app.mainloop()


# ── CLI ──────────────────────────────────────────────────────────────────────

def run_cli(args: argparse.Namespace):
    """Run in CLI mode."""
    if args.list_drives:
        drives = detect_removable_drives()
        if not drives:
            print("No removable drives found.")
            return
        print(f"Found {len(drives)} removable drive(s):\n")
        for d in drives:
            label = d.get("label") or d.get("device", "?")
            print(f"  {d['device']}  {label}")
            print(f"    Mount: {d.get('mountpoint', '?')}")
            print(f"    FS:    {d.get('fstype', '?')}")
            print(f"    Size:  {d.get('total_gb', 0):.1f} GB total, {d.get('free_gb', 0):.1f} GB free")
            print()
        return

    # Determine target
    target = None
    if args.drive:
        drive = args.drive.rstrip("\\").rstrip("/")
        if sys.platform == "win32" and len(drive) <= 2:
            drive += "\\"
        target = Path(drive)
    elif args.output:
        target = Path(args.output)
    elif args.iso:
        # Use temp dir, then create ISO
        target = Path(tempfile.mkdtemp(prefix="biged_media_"))
    else:
        # No target specified in CLI mode — error
        print("ERROR: Specify --drive, --output, or --iso")
        sys.exit(1)

    # Estimate size
    est = estimate_media_size(
        include_python=not args.no_python,
        include_ollama=not args.no_ollama,
        include_models=args.include_models,
    )
    print(f"Estimated media size: {est:.1f} GB" if est >= 1 else f"Estimated media size: {est*1024:.0f} MB")
    print(f"Target: {target}")
    print()

    start_time = time.time()

    def on_progress(pct: float, msg: str):
        bar_len = 30
        filled = int(bar_len * pct)
        bar = "#" * filled + "-" * (bar_len - filled)
        print(f"\r  [{bar}] {pct*100:5.1f}% {msg:<50}", end="", flush=True)

    def on_log(msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n  [{ts}] {msg}", end="", flush=True)

    def on_complete(ok: bool, msg: str):
        elapsed = time.time() - start_time
        print(f"\n\n{'='*50}")
        if ok:
            print(f"SUCCESS ({elapsed:.1f}s)")
            print(msg)
        else:
            print(f"FAILED ({elapsed:.1f}s)")
            print(msg)
        print(f"{'='*50}")

    builder = MediaBuilder(
        target_dir=target,
        include_python_embed=not args.no_python,
        include_ollama=not args.no_ollama,
        include_models=args.include_models,
        on_progress=on_progress,
        on_log=on_log,
        on_complete=on_complete,
    )

    # Run synchronously in CLI mode
    builder.build()

    # If --iso, create ISO from the temp dir
    if args.iso:
        iso_path = Path(args.iso)
        print(f"\nCreating ISO: {iso_path}...")
        ok = create_iso(target, iso_path, label="BIGEDCC")
        if ok:
            size_mb = iso_path.stat().st_size / (1024 * 1024)
            print(f"ISO created: {iso_path} ({size_mb:.1f} MB)")
        else:
            print("ERROR: ISO creation failed.")
            print("  Ensure mkisofs, genisoimage, or oscdimg is installed.")
            print(f"  Media files are still available at: {target}")
            sys.exit(1)

        # Cleanup temp dir
        if "biged_media_" in str(target):
            shutil.rmtree(target, ignore_errors=True)


# ── Entry Point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="BigEd CC — USB Media Creator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python create_usb_media.py                    # GUI mode\n"
            "  python create_usb_media.py --drive E:         # Write to USB drive\n"
            "  python create_usb_media.py --output ./media   # Write to folder\n"
            "  python create_usb_media.py --iso biged.iso    # Create ISO image\n"
            "  python create_usb_media.py --list-drives      # List USB drives\n"
        ),
    )
    parser.add_argument("--drive", type=str, help="Target drive letter (e.g., E:)")
    parser.add_argument("--output", type=str, help="Target folder path")
    parser.add_argument("--iso", type=str, help="Create ISO image at this path")
    parser.add_argument("--list-drives", action="store_true", help="List removable drives")
    parser.add_argument("--no-python", action="store_true", help="Skip Python embeddable")
    parser.add_argument("--no-ollama", action="store_true", help="Skip Ollama binary")
    parser.add_argument("--include-models", action="store_true",
                        help="Include Ollama models (~5 GB)")

    args = parser.parse_args()

    # If any CLI arg is given, run CLI mode
    has_cli_args = any([
        args.drive, args.output, args.iso, args.list_drives,
        args.no_python, args.no_ollama, args.include_models,
    ])

    if has_cli_args:
        run_cli(args)
    else:
        run_gui()


if __name__ == "__main__":
    main()
