#!/usr/bin/env python3
"""
BigEd CC — cross-platform build script.

Replaces build.bat. Auto-detects platform for:
  - PyInstaller --add-data separator (';' on Windows, ':' elsewhere)
  - Icon format (.ico on Windows, .icns on macOS, .png on Linux)
  - pynvml hidden-import (skip on macOS — no NVIDIA GPU)
  - Process termination (taskkill on Windows, pkill elsewhere)

Usage:
    python build.py              # build all (launcher, updater, setup)
    python build.py --launcher   # build launcher only
    python build.py --updater    # build updater only
    python build.py --setup      # build setup/installer only
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
SEP = ";" if sys.platform == "win32" else ":"


def _kill_process(name: str):
    """Kill a running process by name (platform-aware)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/f", "/im", f"{name}.exe"],
                       capture_output=True)
    else:
        subprocess.run(["pkill", "-f", name], capture_output=True)


def _run(cmd: list, label: str):
    """Run a command, exit on failure."""
    print(f"\n== {label} ==")
    result = subprocess.run(cmd, cwd=str(HERE))
    if result.returncode != 0:
        print(f"FAILED: {label}")
        sys.exit(1)


def _pyinstaller_cmd(name: str, script: str, icon: str = "brick.ico",
                     add_data: list = None, hidden_imports: list = None,
                     windowed: bool = True) -> list:
    """Build a PyInstaller command with platform-aware flags."""
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", name,
        "--icon", icon,
        "--collect-all", "customtkinter",
    ]
    if windowed:
        cmd.append("--windowed")

    # Add data files
    for src in (add_data or []):
        cmd.extend(["--add-data", f"{src}{SEP}."])

    # Hidden imports — skip pynvml on macOS (no NVIDIA)
    for imp in (hidden_imports or []):
        if imp == "pynvml" and sys.platform == "darwin":
            continue
        cmd.extend(["--hidden-import", imp])

    cmd.append(script)
    return cmd


def build_launcher():
    """Build BigEdCC executable."""
    _kill_process("BigEdCC")
    _run(
        _pyinstaller_cmd(
            "BigEdCC", "launcher.py",
            add_data=["brick_banner.png", "brick.ico"],
            hidden_imports=["psutil", "pynvml"],
        ),
        "Building BigEdCC",
    )


def build_updater():
    """Build Updater executable."""
    _kill_process("Updater")
    _run(
        _pyinstaller_cmd(
            "Updater", "updater.py",
            add_data=["brick.ico"],
        ),
        "Building Updater",
    )


def build_setup():
    """Build Setup/Installer executable."""
    _kill_process("Setup")
    _run(
        _pyinstaller_cmd(
            "Setup", "installer.py",
            add_data=["brick.ico", "brick_banner.png"],
        ),
        "Building Setup",
    )


def main():
    parser = argparse.ArgumentParser(description="BigEd CC Build System")
    parser.add_argument("--launcher", action="store_true", help="Build launcher only")
    parser.add_argument("--updater", action="store_true", help="Build updater only")
    parser.add_argument("--setup", action="store_true", help="Build setup only")
    parser.add_argument("--production", action="store_true",
                        help="Production build — set BIGED_PRODUCTION=1 env in exe")
    args = parser.parse_args()

    build_all = not (args.launcher or args.updater or args.setup)

    print(f"Platform: {sys.platform}")
    print(f"Separator: '{SEP}'")
    print(f"Python: {sys.executable}")

    # Install deps
    if build_all:
        _run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
             "Installing dependencies")

    # Generate icons
    icon_script = HERE / "generate_icon.py"
    if icon_script.exists() and build_all:
        _run([sys.executable, str(icon_script)], "Generating icons")

    # Build targets
    if build_all or args.launcher:
        build_launcher()
    if build_all or args.updater:
        build_updater()
    if build_all or args.setup:
        build_setup()

    # Production marker — launcher reads this to hide dev features
    dist = HERE / "dist"
    if args.production and dist.exists():
        marker = dist / "_production_marker"
        marker.write_text("1")
        print(f"Production marker written: {marker}")

    print("\n== Done ==")
    if dist.exists():
        for f in sorted(dist.iterdir()):
            print(f"  {f.name}")


if __name__ == "__main__":
    main()
