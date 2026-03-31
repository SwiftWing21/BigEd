#!/usr/bin/env python3
"""Copy control.lua from lua_mod/ to server_data/mods/biged-bridge/."""
import shutil
from pathlib import Path

SRC = Path(__file__).parent / "lua_mod" / "control.lua"
DST = Path(__file__).parent / "server_data" / "mods" / "biged-bridge" / "control.lua"

def sync():
    if not SRC.exists():
        print(f"ERROR: Source not found: {SRC}")
        return False
    DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(SRC), str(DST))
    print(f"Synced: {SRC} -> {DST}")
    return True

if __name__ == "__main__":
    sync()
