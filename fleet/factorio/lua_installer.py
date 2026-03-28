# fleet/factorio/lua_installer.py
"""Detect Factorio install and copy the Lua mod."""
import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger("biged.factorio.lua_install")

_LUA_MOD_DIR = Path(__file__).parent / "lua_mod"

_SEARCH_PATHS: list[Path] = []

if sys.platform == "win32":
    _SEARCH_PATHS = [
        Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"))
        / "Steam" / "steamapps" / "common" / "Factorio",
        Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")) / "Factorio",
    ]
    _MODS_CANDIDATES = [
        Path(os.environ.get("APPDATA", "")) / "Factorio" / "mods",
    ]
elif sys.platform == "darwin":
    _SEARCH_PATHS = [
        Path.home() / "Library" / "Application Support" / "factorio",
    ]
    _MODS_CANDIDATES = [_SEARCH_PATHS[0] / "mods"] if _SEARCH_PATHS else []
else:
    _SEARCH_PATHS = [
        Path.home() / ".factorio",
        Path.home() / ".steam" / "steam" / "steamapps" / "common" / "Factorio",
    ]
    _MODS_CANDIDATES = [Path.home() / ".factorio" / "mods"]


def get_lua_mod_source() -> Path:
    return _LUA_MOD_DIR


def detect_factorio_path() -> Path | None:
    for p in _SEARCH_PATHS:
        if p.exists() and p.is_dir():
            return p
    return None


def detect_mods_dir() -> Path | None:
    for p in _MODS_CANDIDATES:
        if p.exists() and p.is_dir():
            return p
    return None


def install_lua_mod(mode: str = "manual", mods_dir: str | None = None) -> dict:
    source = get_lua_mod_source()
    if not source.exists():
        return {"mode": mode, "error": "Lua mod source not found", "success": False}

    if mode == "manual":
        return {
            "mode": "manual",
            "success": True,
            "source": str(source),
            "instructions": (
                f"Copy the folder '{source}' into your Factorio mods directory.\n"
                f"Typical locations:\n"
                f"  Windows: %APPDATA%\\Factorio\\mods\\biged-bridge\\\n"
                f"  Linux:   ~/.factorio/mods/biged-bridge/\n"
                f"  macOS:   ~/Library/Application Support/factorio/mods/biged-bridge/\n"
                f"Then restart Factorio."
            ),
        }

    if mode == "assisted":
        target_dir = Path(mods_dir) if mods_dir else detect_mods_dir()
        if not target_dir:
            return {
                "mode": "assisted",
                "success": False,
                "error": "Could not detect Factorio mods directory. Set it manually.",
            }
        dest = target_dir / "biged-bridge"
        try:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(source, dest)
            log.info("Lua mod installed to %s", dest)
            return {"mode": "assisted", "success": True, "installed_to": str(dest)}
        except Exception as e:
            log.warning("Lua mod copy failed: %s", e, exc_info=True)
            return {"mode": "assisted", "success": False, "error": f"Copy failed: {e}"}

    return {"mode": mode, "error": f"Unknown mode: {mode}", "success": False}
