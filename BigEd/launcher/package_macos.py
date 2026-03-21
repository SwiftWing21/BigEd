#!/usr/bin/env python3
"""
PT-3: macOS packaging — .app bundle + DMG disk image.

Builds BigEd CC as a macOS .app bundle using PyInstaller (--windowed),
patches Info.plist with version/display metadata, optionally code signs
the bundle, and wraps it in a DMG with drag-to-Applications layout.

Follows the same conventions as build.py (HERE, _run, _pyinstaller_cmd
patterns) and FRAMEWORK_BLUEPRINT.md Section 10.4.

Usage:
    python package_macos.py              # Build .app bundle
    python package_macos.py --dmg        # Build .app + wrap in DMG
    python package_macos.py --sign       # Build + ad-hoc code sign
    python package_macos.py --sign --identity "Developer ID Application: ..."
"""
import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
PROJECT_ROOT = HERE.parent.parent
FLEET_DIR = PROJECT_ROOT / "fleet"
APP_NAME = "BigEdCC"
DISPLAY_NAME = "BigEd CC"
BUNDLE_ID = "com.biged.cc"
VERSION = "0.43"
SEP = ":"  # macOS always uses ':' for PyInstaller --add-data


def _run(cmd: list, label: str):
    """Run a command, exit on failure."""
    print(f"\n== {label} ==")
    result = subprocess.run(cmd, cwd=str(HERE))
    if result.returncode != 0:
        print(f"FAILED: {label}")
        sys.exit(1)


def _pyinstaller_cmd() -> list:
    """Build the PyInstaller command for a macOS .app bundle.

    Mirrors build.py conventions:
      - --collect-all customtkinter
      - --add-data for assets and fleet/
      - --hidden-import psutil (skip pynvml — no NVIDIA on macOS)
      - --windowed produces a .app bundle on macOS
    """
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--windowed",
        "--osx-bundle-identifier", BUNDLE_ID,
        "--collect-all", "customtkinter",
    ]

    # Icon — .icns for macOS (fallback to .ico if .icns not generated)
    icns = HERE / "brick.icns"
    ico = HERE / "brick.ico"
    if icns.exists():
        cmd.extend(["--icon", str(icns)])
    elif ico.exists():
        cmd.extend(["--icon", str(ico)])

    # Add data files (same assets as build.py build_launcher)
    for asset in ["icon_1024.png", "brick.ico"]:
        asset_path = HERE / asset
        if asset_path.exists():
            cmd.extend(["--add-data", f"{asset}{SEP}."])

    # Bundle the fleet/ directory into the app
    cmd.extend(["--add-data", f"{FLEET_DIR}{SEP}fleet"])

    # Hidden imports — skip pynvml on macOS (no NVIDIA GPU)
    cmd.extend(["--hidden-import", "psutil"])

    cmd.append(str(HERE / "launcher.py"))
    return cmd


def build_app_bundle() -> Path:
    """Build a macOS .app bundle via PyInstaller."""
    print(f"Platform: {sys.platform}")
    print(f"Python: {sys.executable}")
    print(f"Building {DISPLAY_NAME}.app v{VERSION}")

    # Kill any running instance
    subprocess.run(["pkill", "-f", APP_NAME], capture_output=True)

    # Install deps
    _run([sys.executable, "-m", "pip", "install", "-r",
          str(HERE / "requirements.txt")],
         "Installing dependencies")

    # Icons: brick.ico + icon_1024.png are locked assets (no generation needed)

    # PyInstaller build
    _run(_pyinstaller_cmd(), f"Building {APP_NAME}.app")

    app_path = HERE / "dist" / f"{APP_NAME}.app"
    if not app_path.exists():
        print(f"ERROR: Expected .app bundle not found at {app_path}")
        sys.exit(1)

    # Patch Info.plist with additional metadata
    _patch_plist(app_path)

    print(f"\n.app bundle: {app_path}")
    return app_path


def _patch_plist(app_path: Path):
    """Patch the Info.plist with display name, version, and macOS metadata."""
    plist_path = app_path / "Contents" / "Info.plist"
    if not plist_path.exists():
        print("WARNING: Info.plist not found — skipping patch")
        return

    with open(plist_path, "rb") as f:
        plist = plistlib.load(f)

    plist.update({
        "CFBundleDisplayName": DISPLAY_NAME,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "13.0",
    })

    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)

    print(f"Patched Info.plist: {plist_path}")


def codesign(app_path: Path, identity: str = "-") -> bool:
    """Code sign the .app bundle.

    Args:
        app_path: Path to the .app bundle.
        identity: Signing identity. Use '-' for ad-hoc (default),
                  or 'Developer ID Application: ...' for distribution.
    Returns:
        True if signing succeeded.
    """
    print(f"\n== Code Signing (identity: {identity}) ==")
    result = subprocess.run(
        ["codesign", "--force", "--deep", "--sign", identity, str(app_path)],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("Code signed successfully")
        # Verify
        verify = subprocess.run(
            ["codesign", "--verify", "--verbose", str(app_path)],
            capture_output=True, text=True,
        )
        if verify.returncode == 0:
            print("Signature verified OK")
        else:
            print(f"Signature verification warning: {verify.stderr}")
    else:
        print(f"Code signing failed: {result.stderr}")
    return result.returncode == 0


def create_dmg(app_path: Path) -> Path:
    """Create a DMG disk image with drag-to-Applications layout.

    Returns:
        Path to the created .dmg file.
    """
    dmg_name = f"{APP_NAME}-{VERSION}.dmg"
    dmg_path = HERE / "dist" / dmg_name
    staging = HERE / "dist" / "dmg_staging"

    print(f"\n== Creating DMG: {dmg_name} ==")

    # Clean previous staging / dmg
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    if dmg_path.exists():
        dmg_path.unlink()

    # Copy .app into staging
    shutil.copytree(app_path, staging / f"{APP_NAME}.app")

    # Symlink /Applications for drag-and-drop install
    os.symlink("/Applications", str(staging / "Applications"))

    # Build DMG via hdiutil
    result = subprocess.run([
        "hdiutil", "create",
        "-volname", DISPLAY_NAME,
        "-srcfolder", str(staging),
        "-ov",
        "-format", "UDZO",  # compressed
        str(dmg_path),
    ], capture_output=True, text=True)

    # Clean staging
    shutil.rmtree(staging, ignore_errors=True)

    if result.returncode == 0:
        size_mb = dmg_path.stat().st_size / (1024 * 1024)
        print(f"DMG created: {dmg_path} ({size_mb:.1f} MB)")
    else:
        print(f"DMG creation failed: {result.stderr}")
        sys.exit(1)

    return dmg_path


def main():
    if sys.platform != "darwin":
        print("ERROR: This script must be run on macOS.")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description=f"{DISPLAY_NAME} macOS Packager (PT-3)")
    parser.add_argument("--dmg", action="store_true",
                        help="Also create DMG disk image")
    parser.add_argument("--sign", action="store_true",
                        help="Code sign the .app bundle")
    parser.add_argument("--identity", default="-",
                        help="Code signing identity (default: ad-hoc '-')")
    args = parser.parse_args()

    app_path = build_app_bundle()

    if args.sign:
        codesign(app_path, args.identity)

    if args.dmg:
        create_dmg(app_path)

    # Summary
    print("\n== Done ==")
    dist = HERE / "dist"
    if dist.exists():
        for f in sorted(dist.iterdir()):
            if f.name == "dmg_staging":
                continue
            print(f"  {f.name}")


if __name__ == "__main__":
    main()
