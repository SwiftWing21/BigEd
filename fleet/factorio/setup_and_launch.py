"""
Factorio Sandbox — One-click setup and launch.

Usage:
    python fleet/factorio/setup_and_launch.py
    python fleet/factorio/setup_and_launch.py --password mypass
    python fleet/factorio/setup_and_launch.py --dry-run
"""
import logging
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

FLEET_DIR = Path(__file__).resolve().parent.parent
TOML_PATH = FLEET_DIR / "fleet.toml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
log = logging.getLogger("factorio.setup")


def generate_password(length: int = 16) -> str:
    """Generate a random RCON password."""
    return secrets.token_urlsafe(length)


def update_fleet_toml(password: str) -> None:
    """Enable [factorio] and set RCON password in fleet.toml."""
    text = TOML_PATH.read_text(encoding="utf-8")

    # Enable factorio
    text = re.sub(
        r"(\[factorio\]\s*\n(?:.*\n)*?enabled\s*=\s*)false",
        r"\g<1>true",
        text,
        count=1,
    )

    # Set RCON password
    text = re.sub(
        r'(rcon_password\s*=\s*)"[^"]*"',
        f'\\1"{password}"',
        text,
        count=1,
    )

    with open(TOML_PATH, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    log.info("fleet.toml updated: enabled=true, rcon_password set")


def install_lua_mod() -> dict:
    """Copy the Lua mod to Factorio's mods directory."""
    sys.path.insert(0, str(FLEET_DIR))
    from factorio.lua_installer import install_lua_mod as _install, detect_mods_dir

    mods_dir = detect_mods_dir()
    if not mods_dir:
        # Try creating the default mods directory
        default = Path(os.environ.get("APPDATA", "")) / "Factorio" / "mods"
        if default.parent.exists():
            default.mkdir(exist_ok=True)
            mods_dir = default

    result = _install(mode="assisted", mods_dir=str(mods_dir) if mods_dir else None)
    if result.get("success"):
        log.info("Lua mod installed to: %s", result.get("installed_to"))
    else:
        log.warning("Lua mod install issue: %s", result.get("error", result.get("instructions")))

    # Also install to server_data/mods/ so the isolated headless server has the mod
    server_mods = Path(__file__).parent / "server_data" / "mods"
    server_mods.mkdir(parents=True, exist_ok=True)
    _install(mode="assisted", mods_dir=str(server_mods))

    return result


def find_factorio_exe() -> Path | None:
    """Find the Factorio executable."""
    candidates = []
    if sys.platform == "win32":
        candidates = [
            # Standalone headless install FIRST (raw UDP, spectator co-play works)
            # NEVER use Steam version — it routes multiplayer through Steam relay
            Path("F:\\Factorio\\bin\\x64\\factorio.exe"),
            Path(os.environ.get("PROGRAMFILES", "C:\\Program Files"))
            / "Factorio" / "bin" / "x64" / "factorio.exe",
        ]
    else:
        candidates = [
            Path.home() / ".steam" / "steam" / "steamapps" / "common" / "Factorio" / "bin" / "x64" / "factorio",
            Path.home() / ".factorio" / "bin" / "x64" / "factorio",
            Path("/opt/factorio/bin/x64/factorio"),
        ]

    for p in candidates:
        if p.exists():
            return p
    return None


def _write_map_gen_settings(path: Path, biters: bool = False) -> None:
    """Write map generation settings JSON. Biters off by default for training."""
    import json as _json
    settings = {
        "autoplace_controls": {
            "iron-ore": {"frequency": "very-high", "size": "very-big", "richness": "very-good"},
            "copper-ore": {"frequency": "very-high", "size": "very-big", "richness": "very-good"},
            "coal": {"frequency": "very-high", "size": "very-big", "richness": "very-good"},
            "stone": {"frequency": "very-high", "size": "big", "richness": "very-good"},
            "uranium-ore": {"frequency": "normal", "size": "normal", "richness": "normal"},
            "crude-oil": {"frequency": "normal", "size": "normal", "richness": "very-good"},
            "trees": {"frequency": "normal", "size": "normal", "richness": "normal"},
            "enemy-base": {
                "frequency": "normal" if biters else "none",
                "size": "normal" if biters else "none",
                "richness": "normal" if biters else "none",
            },
        },
        "property_expression_names": {
            "control-setting:iron-ore:frequency:multiplier": "2",
            "control-setting:copper-ore:frequency:multiplier": "2",
            "control-setting:coal:frequency:multiplier": "2",
        },
        "starting_area": "very-big",
        "peaceful_mode": not biters,
        "enemy_expansion": {"enabled": biters},
    }
    path.write_text(_json.dumps(settings, indent=2), encoding="utf-8")
    log.info("Map gen settings written (biters=%s): %s", biters, path)


def find_or_create_save(factorio_exe: Path, biters: bool = False) -> Path:
    """Find an existing save or create a new one.

    Args:
        biters: If True, create save with enemies enabled. Default False (training).
    """
    if sys.platform == "win32":
        saves_dir = Path(os.environ.get("APPDATA", "")) / "Factorio" / "saves"
    else:
        saves_dir = Path.home() / ".factorio" / "saves"

    sandbox_save = saves_dir / "biged-sandbox.zip"
    if sandbox_save.exists():
        log.info("Using existing save: %s", sandbox_save)
        return sandbox_save

    # Create a new save with map gen settings
    log.info("Creating new sandbox save (biters=%s)...", biters)
    saves_dir.mkdir(parents=True, exist_ok=True)

    map_gen_path = saves_dir / "biged-map-gen-settings.json"
    _write_map_gen_settings(map_gen_path, biters=biters)

    proc = subprocess.run(
        [str(factorio_exe), "--create", str(sandbox_save),
         "--map-gen-settings", str(map_gen_path)],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=300,
    )
    if proc.returncode != 0:
        log.error("Failed to create save: %s", proc.stderr[:500])
        raise RuntimeError(f"Save creation failed: {proc.stderr[:200]}")

    log.info("Save created: %s", sandbox_save)
    return sandbox_save


def recreate_save(factorio_exe: Path, biters: bool = False) -> Path:
    """Delete existing save and create a fresh one. Used when toggling biters."""
    if sys.platform == "win32":
        saves_dir = Path(os.environ.get("APPDATA", "")) / "Factorio" / "saves"
    else:
        saves_dir = Path.home() / ".factorio" / "saves"

    sandbox_save = saves_dir / "biged-sandbox.zip"
    if sandbox_save.exists():
        # Rename old save as backup
        backup = sandbox_save.with_suffix(f".backup.zip")
        sandbox_save.rename(backup)
        log.info("Backed up old save to %s", backup)

    return find_or_create_save(factorio_exe, biters=biters)


def start_headless_server(factorio_exe: Path, save: Path, port: int, password: str) -> subprocess.Popen:
    """Launch Factorio headless server with RCON enabled.

    Uses a separate config directory so the headless server doesn't hold
    the AppData/Roaming/Factorio/.lock — allowing the GUI client to run
    simultaneously as a spectator.
    """
    # Isolate server config so GUI client can co-exist
    server_config_dir = Path(__file__).parent / "server_data"
    server_config_dir.mkdir(exist_ok=True)
    cmd = [
        str(factorio_exe),
        "--start-server", str(save),
        "--rcon-port", str(port),
        "--rcon-password", password,
        "--config", str(server_config_dir / "config.ini"),
        "--server-settings", str(server_config_dir / "server-settings.json"),
    ]
    log.info("Starting Factorio server: %s", " ".join(cmd[:3]) + " ...")

    # Send stdout/stderr to devnull — PIPE causes buffer stall which blocks RCON
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    # Wait a moment and check it didn't crash immediately
    time.sleep(3)
    if proc.poll() is not None:
        raise RuntimeError(f"Factorio server exited immediately (code {proc.returncode})")

    log.info("Factorio server running (PID %d) on RCON port %d", proc.pid, port)
    return proc


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Factorio Sandbox — Setup and Launch")
    parser.add_argument("--password", default=None, help="RCON password (auto-generated if omitted)")
    parser.add_argument("--port", type=int, default=27015, help="RCON port (default: 27015)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without doing it")
    parser.add_argument("--skip-server", action="store_true", help="Configure only, don't start Factorio")
    args = parser.parse_args()

    password = args.password or generate_password()

    print("=" * 60)
    print("  Factorio Sandbox — Setup and Launch")
    print("=" * 60)

    # Step 1: Find Factorio
    print("\n[1/5] Finding Factorio installation...")
    factorio_exe = find_factorio_exe()
    if factorio_exe:
        print(f"  Found: {factorio_exe}")
    else:
        print("  ERROR: Factorio not found. Install it or set factorio_path in fleet.toml")
        sys.exit(1)

    if args.dry_run:
        print(f"\n[DRY RUN] Would:")
        print(f"  - Set fleet.toml enabled=true, rcon_password='{password[:4]}...'")
        print(f"  - Copy Lua mod to Factorio mods dir")
        print(f"  - Create save if needed")
        print(f"  - Start headless server on RCON port {args.port}")
        print(f"  - Start BigEd bridge (self-contained, no supervisor needed)")
        return

    # Step 2: Update fleet.toml
    print("\n[2/5] Configuring fleet.toml...")
    update_fleet_toml(password)
    print(f"  RCON password: {password[:4]}{'*' * (len(password) - 4)}")

    # Step 3: Install Lua mod
    print("\n[3/5] Installing Lua mod...")
    result = install_lua_mod()
    if result.get("success"):
        print(f"  Installed to: {result.get('installed_to', 'OK')}")
    else:
        print(f"  Warning: {result.get('error', 'check manually')}")
        if result.get("instructions"):
            print(f"  {result['instructions'][:200]}")

    if args.skip_server:
        print("\n[DONE] Config complete. Start Factorio manually, then run the supervisor.")
        print(f"  Factorio: {factorio_exe} --start-server <save> --rcon-port {args.port} --rcon-password {password}")
        return

    # Step 4: Find or create save
    print("\n[4/5] Preparing save file...")
    try:
        save = find_or_create_save(factorio_exe)
        print(f"  Save: {save}")
    except RuntimeError as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    # Step 5: Start server
    print("\n[5/6] Starting Factorio headless server...")
    try:
        server_proc = start_headless_server(factorio_exe, save, args.port, password)
    except RuntimeError as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    # Step 6: Start bridge
    print("\n[6/6] Starting BigEd bridge...")
    bridge_env = {
        **os.environ,
        "PYTHONPATH": str(FLEET_DIR),
        "BIGED_RCON_PASSWORD": password,
    }
    bridge_proc = subprocess.Popen(
        [sys.executable, "-m", "factorio.bridge"],
        cwd=str(FLEET_DIR),
        env=bridge_env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    time.sleep(3)
    if bridge_proc.poll() is not None:
        print(f"  WARNING: Bridge exited (may need fleet running). Check logs.")
    else:
        log.info("Bridge running (PID %d) on port 27016", bridge_proc.pid)
        print(f"  Bridge PID: {bridge_proc.pid}")

    # Write PID file so dashboard can manage processes
    pid_file = Path(__file__).parent / "server_data" / "pids.json"
    import json as _json
    pid_file.write_text(_json.dumps({
        "server_pid": server_proc.pid,
        "bridge_pid": bridge_proc.pid,
        "rcon_port": args.port,
        "rcon_password": password,
        "started_at": time.time(),
    }))

    print("\n" + "=" * 60)
    print("  READY!")
    print("=" * 60)
    print(f"  Factorio server PID: {server_proc.pid}")
    print(f"  Bridge PID: {bridge_proc.pid}")
    print(f"  RCON: localhost:{args.port}")
    print(f"  Bridge API: http://127.0.0.1:27016")
    print(f"  PIDs saved to: {pid_file}")
    print(f"")
    print(f"  setup_and_launch exiting — processes run independently.")
    print(f"  Use dashboard or lead_client to stop/restart.")


if __name__ == "__main__":
    main()
