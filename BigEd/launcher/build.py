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
import time
from pathlib import Path

HERE = Path(__file__).parent
SEP = ";" if sys.platform == "win32" else ":"

# ─── Colors (ANSI) ────────────────────────────────────────────────────────────
BOLD  = "\033[1m"
DIM   = "\033[2m"
GREEN = "\033[32m"
RED   = "\033[31m"
GOLD  = "\033[33m"
CYAN  = "\033[36m"
RESET = "\033[0m"


def _fmt_time(secs: float) -> str:
    if secs < 60:
        return f"{secs:.1f}s"
    mins, s = divmod(int(secs), 60)
    return f"{mins}m {s:02d}s"


def _fmt_size(path: Path) -> str:
    if not path.exists():
        return "—"
    mb = path.stat().st_size / (1024 * 1024)
    return f"{mb:.1f} MB"


def _kill_process(name: str):
    """Kill a running process by name (platform-aware)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/f", "/im", f"{name}.exe"],
                       capture_output=True)
    else:
        subprocess.run(["pkill", "-f", name], capture_output=True)


def _run(cmd: list, label: str) -> float:
    """Run a command with runtime tracking. Returns elapsed seconds."""
    print(f"\n{BOLD}{CYAN}▸ {label}{RESET}")
    print(f"  {DIM}$ {' '.join(cmd[:5])}{'...' if len(cmd) > 5 else ''}{RESET}")
    t0 = time.time()
    result = subprocess.run(cmd, cwd=str(HERE))
    elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"  {RED}✗ FAILED{RESET} ({_fmt_time(elapsed)})")
        sys.exit(1)
    print(f"  {GREEN}✓{RESET} {_fmt_time(elapsed)}")
    return elapsed


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


def build_launcher() -> float:
    """Build BigEdCC executable."""
    _kill_process("BigEdCC")
    return _run(
        _pyinstaller_cmd(
            "BigEdCC", "launcher.py",
            add_data=["brick_banner.png", "brick.ico"],
            hidden_imports=["psutil", "pynvml"],
        ),
        "Building BigEdCC",
    )


def build_updater() -> float:
    """Build Updater executable."""
    _kill_process("Updater")
    return _run(
        _pyinstaller_cmd(
            "Updater", "updater.py",
            add_data=["brick.ico"],
        ),
        "Building Updater",
    )


def build_setup() -> float:
    """Build Setup/Installer executable."""
    _kill_process("Setup")
    return _run(
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

    # ── Header ────────────────────────────────────────────────────────────
    print(f"\n{BOLD}{GOLD}╔══════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{GOLD}║     BigEd CC — Build System          ║{RESET}")
    print(f"{BOLD}{GOLD}╚══════════════════════════════════════╝{RESET}")
    print(f"  Platform:  {BOLD}{sys.platform}{RESET}")
    print(f"  Python:    {sys.version.split()[0]}  ({sys.executable})")
    print(f"  Target:    {'all' if build_all else ', '.join(f for f in ['launcher', 'updater', 'setup'] if getattr(args, f))}")
    if args.production:
        print(f"  Mode:      {GOLD}PRODUCTION{RESET}")
    print()

    build_start = time.time()
    step_times = {}

    # Install deps
    if build_all:
        t = _run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                 "Installing dependencies")
        step_times["Dependencies"] = t

    # Generate icons
    icon_script = HERE / "generate_icon.py"
    if icon_script.exists() and build_all:
        t = _run([sys.executable, str(icon_script)], "Generating icons")
        step_times["Icons"] = t

    # Build targets
    if build_all or args.launcher:
        step_times["BigEdCC"] = build_launcher()
    if build_all or args.updater:
        step_times["Updater"] = build_updater()
    if build_all or args.setup:
        step_times["Setup"] = build_setup()

    # Production marker — launcher reads this to hide dev features
    dist = HERE / "dist"
    if args.production and dist.exists():
        marker = dist / "_production_marker"
        marker.write_text("1")

    total_elapsed = time.time() - build_start

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{BOLD}{GREEN}{'═' * 50}{RESET}")
    print(f"{BOLD}{GREEN}  BUILD COMPLETE{RESET}  {_fmt_time(total_elapsed)} total")
    print(f"{GREEN}{'═' * 50}{RESET}\n")

    # Step breakdown
    print(f"  {BOLD}Step Timings:{RESET}")
    for step, t in step_times.items():
        bar_len = min(int(t / 2), 30)  # 2s per char, max 30
        bar = "█" * bar_len + "░" * (30 - bar_len)
        print(f"    {step:<14} {DIM}{bar}{RESET}  {_fmt_time(t)}")

    # Output artifacts
    if dist.exists():
        exes = [f for f in sorted(dist.iterdir()) if f.suffix == ".exe"]
        if exes:
            print(f"\n  {BOLD}Artifacts:{RESET}")
            for f in exes:
                print(f"    {f.name:<20} {_fmt_size(f):>10}")
            total_size = sum(f.stat().st_size for f in exes) / (1024 * 1024)
            print(f"    {'─' * 31}")
            print(f"    {'Total':<20} {total_size:>7.1f} MB")

    if args.production:
        print(f"\n  {GOLD}Production marker written{RESET}")

    print()


if __name__ == "__main__":
    main()
