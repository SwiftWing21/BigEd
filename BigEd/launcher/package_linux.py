#!/usr/bin/env python3
"""
PT-3: Linux packaging — AppImage + .desktop file generation.

Builds BigEd CC as a portable Linux AppImage with .desktop integration.
Uses the same PyInstaller conventions as build.py (--onedir for AppImage).

Usage:
    python package_linux.py                    # Build AppImage
    python package_linux.py --desktop-only     # Generate .desktop file only
    python package_linux.py --install          # Install .desktop + symlink
"""
import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
PROJECT_ROOT = HERE.parent.parent
FLEET_DIR = PROJECT_ROOT / "fleet"
APP_NAME = "BigEdCC"
APP_ID = "com.biged.cc"
VERSION = "0.43"
ICON_PNG = HERE / "icon_1024.png"
ICON_ICO = HERE / "brick.ico"
SEP = ";" if sys.platform == "win32" else ":"


# ---------------------------------------------------------------------------
# Helpers (matching build.py conventions)
# ---------------------------------------------------------------------------

def _run(cmd: list, label: str, cwd: str = None):
    """Run a command, exit on failure."""
    print(f"\n== {label} ==")
    result = subprocess.run(cmd, cwd=cwd or str(HERE))
    if result.returncode != 0:
        print(f"FAILED: {label}")
        sys.exit(1)


def _check_linux():
    """Guard: this script targets Linux only."""
    if sys.platform != "linux":
        print(f"WARNING: This script targets Linux. Current platform: {sys.platform}")
        print("Proceeding anyway (useful for testing AppDir layout).")


# ---------------------------------------------------------------------------
# .desktop file generation
# ---------------------------------------------------------------------------

def create_desktop_file(install_dir: Path = None) -> Path:
    """Generate a .desktop file for Linux desktop integration.

    Args:
        install_dir: If set, Exec= points to APP_NAME inside this dir.
                     Otherwise defaults to /opt/{APP_NAME}/{APP_NAME}.
    """
    if install_dir:
        exec_path = install_dir / APP_NAME
    else:
        exec_path = Path(f"/opt/{APP_NAME}/{APP_NAME}")

    icon_path = ICON_PNG if ICON_PNG.exists() else ICON_ICO

    desktop = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name=BigEd CC\n"
        f"GenericName=AI Agent Fleet Manager\n"
        f"Comment=AI Agent Fleet Manager — local-first fleet of workers\n"
        f"Exec={exec_path}\n"
        f"Icon={icon_path}\n"
        "Terminal=false\n"
        "Categories=Development;Utility;\n"
        f"StartupWMClass={APP_NAME}\n"
        f"Version={VERSION}\n"
    )

    desktop_path = HERE / f"{APP_ID}.desktop"
    desktop_path.write_text(desktop)
    print(f"Desktop file: {desktop_path}")
    return desktop_path


# ---------------------------------------------------------------------------
# .desktop install
# ---------------------------------------------------------------------------

def install_desktop(desktop_path: Path):
    """Install .desktop file to user's applications directory."""
    target_dir = Path.home() / ".local" / "share" / "applications"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / desktop_path.name
    shutil.copy2(desktop_path, target)
    print(f"Installed: {target}")

    # Copy icon to standard location
    icon_dir = Path.home() / ".local" / "share" / "icons"
    icon_dir.mkdir(parents=True, exist_ok=True)
    if ICON_PNG.exists():
        icon_target = icon_dir / f"{APP_ID}.png"
        shutil.copy2(ICON_PNG, icon_target)
        print(f"Icon installed: {icon_target}")

    # Update desktop database (best-effort)
    try:
        subprocess.run(["update-desktop-database", str(target_dir)],
                       capture_output=True, timeout=10)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# AppImage build
# ---------------------------------------------------------------------------

def build_appimage():
    """Build an AppImage using PyInstaller (--onedir) + appimagetool."""
    _check_linux()
    print(f"\nBuilding {APP_NAME} AppImage v{VERSION}")
    print(f"Platform: {sys.platform}")
    print(f"Python:   {sys.executable}")

    # ---- Step 1: PyInstaller one-dir build --------------------------------
    #
    # AppImage wraps a directory, so we use --onedir (not --onefile like
    # build.py uses for Windows).  Hidden imports match build_launcher().
    dist_dir = HERE / "dist" / APP_NAME

    pyinstaller_cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--onedir",
        "--windowed",
        "--icon", str(ICON_PNG if ICON_PNG.exists() else ICON_ICO),
        "--collect-all", "customtkinter",
        f"--add-data={HERE / 'icon_1024.png'}{SEP}.",
        f"--add-data={HERE / 'brick.ico'}{SEP}.",
        f"--add-data={HERE / 'icon_1024.png'}{SEP}.",
        "--hidden-import", "psutil",
        "--hidden-import", "pynvml",
        str(HERE / "launcher.py"),
    ]

    _run(pyinstaller_cmd, "PyInstaller one-dir build")

    if not dist_dir.exists():
        print(f"ERROR: Expected PyInstaller output not found: {dist_dir}")
        sys.exit(1)

    # ---- Step 2: Assemble AppDir structure --------------------------------
    #
    # AppImage spec:
    #   AppDir/
    #     AppRun          — executable entry point
    #     <appid>.desktop — desktop entry
    #     <appid>.png     — icon
    #     <app>/          — PyInstaller output
    #
    appdir = HERE / "dist" / f"{APP_NAME}.AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    appdir.mkdir(parents=True)

    # AppRun launcher script
    apprun = appdir / "AppRun"
    apprun.write_text(
        "#!/bin/bash\n"
        'SELF=$(readlink -f "$0")\n'
        'HERE=${SELF%/*}\n'
        f'exec "$HERE/{APP_NAME}/{APP_NAME}" "$@"\n'
    )
    apprun.chmod(apprun.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Copy PyInstaller output into AppDir
    target = appdir / APP_NAME
    shutil.copytree(dist_dir, target)

    # .desktop inside AppDir (Exec is relative for AppImage)
    appdir_desktop = appdir / f"{APP_ID}.desktop"
    appdir_desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=BigEd CC\n"
        "GenericName=AI Agent Fleet Manager\n"
        "Comment=AI Agent Fleet Manager — local-first fleet of workers\n"
        f"Exec={APP_NAME}\n"
        f"Icon={APP_ID}\n"
        "Terminal=false\n"
        "Categories=Development;Utility;\n"
        f"StartupWMClass={APP_NAME}\n"
    )

    # Icon inside AppDir
    if ICON_PNG.exists():
        shutil.copy2(ICON_PNG, appdir / f"{APP_ID}.png")
    elif ICON_ICO.exists():
        shutil.copy2(ICON_ICO, appdir / f"{APP_ID}.ico")

    print(f"AppDir ready: {appdir}")

    # ---- Step 3: Run appimagetool -----------------------------------------
    appimage_name = f"{APP_NAME}-{VERSION}-x86_64.AppImage"
    appimage_tool = shutil.which("appimagetool")

    if not appimage_tool:
        print("\nWARNING: appimagetool not found in PATH.")
        print("Install from: https://github.com/AppImage/AppImageKit/releases")
        print(f"Then run manually:")
        print(f"  appimagetool {appdir} dist/{appimage_name}")
        return

    result = subprocess.run(
        [appimage_tool, str(appdir), appimage_name],
        cwd=str(HERE / "dist"),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        final = HERE / "dist" / appimage_name
        print(f"\nAppImage built: {final}")
        print(f"Size: {final.stat().st_size / (1024 * 1024):.1f} MB")
    else:
        print(f"appimagetool failed:\n{result.stderr[-500:]}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=f"{APP_NAME} Linux Packager (PT-3)",
    )
    parser.add_argument(
        "--desktop-only", action="store_true",
        help="Generate .desktop file only (no AppImage build)",
    )
    parser.add_argument(
        "--install", action="store_true",
        help="Install .desktop file + icon to ~/.local/share/",
    )
    args = parser.parse_args()

    if args.desktop_only:
        create_desktop_file()
    elif args.install:
        desktop = create_desktop_file()
        install_desktop(desktop)
    else:
        build_appimage()


if __name__ == "__main__":
    main()
