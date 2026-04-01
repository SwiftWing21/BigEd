"""
BigEd CC Update Helper — headless binary swapper.

Spawned by the dashboard after downloading a release update.
Waits for the main process to exit, then swaps binaries and relaunches.

Usage:
    python update_helper.py --swap --pid <PID> --source <dir> --target <dir> [--relaunch <exe>]
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _wait_for_exit(pid: int, timeout: int = 30) -> bool:
    """Wait for a process to exit. Returns True if exited, False on timeout."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            os.kill(pid, 0)  # Check if process exists (signal 0 = no signal, just check)
            time.sleep(0.5)
        except OSError:
            return True  # Process gone
    return False


def _swap_files(source: Path, target: Path):
    """Copy files from source to target with backup/rollback on failure."""
    backup = target / ".update_backup"
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir(parents=True, exist_ok=True)

    copied_files = []
    try:
        for src_file in source.rglob("*"):
            if src_file.is_file():
                rel = src_file.relative_to(source)
                dst = target / rel
                # Backup existing file
                if dst.exists():
                    bak = backup / rel
                    bak.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dst, bak)
                # Copy new file
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst)
                copied_files.append(rel)
    except Exception as e:
        # Rollback on failure
        print(f"ERROR: Copy failed ({e}), rolling back...", file=sys.stderr)
        for rel in copied_files:
            bak = backup / rel
            dst = target / rel
            if bak.exists():
                shutil.copy2(bak, dst)
        raise

    # Success — clean up backup
    shutil.rmtree(backup, ignore_errors=True)
    print(f"Swapped {len(copied_files)} files")


def main():
    parser = argparse.ArgumentParser(description="BigEd CC Update Helper")
    parser.add_argument("--swap", action="store_true", required=True)
    parser.add_argument("--pid", type=int, required=True, help="PID to wait for")
    parser.add_argument("--source", required=True, help="Source directory with new files")
    parser.add_argument("--target", required=True, help="Target installation directory")
    parser.add_argument("--relaunch", default="", help="Exe to relaunch after swap")
    args = parser.parse_args()

    source = Path(args.source)
    target = Path(args.target)

    if not source.is_dir():
        print(f"ERROR: Source directory not found: {source}", file=sys.stderr)
        sys.exit(1)
    if not target.is_dir():
        print(f"ERROR: Target directory not found: {target}", file=sys.stderr)
        sys.exit(1)

    print(f"Waiting for PID {args.pid} to exit...")
    if not _wait_for_exit(args.pid):
        print(f"WARNING: PID {args.pid} did not exit in 30s, proceeding anyway", file=sys.stderr)

    try:
        _swap_files(source, target)
    except PermissionError as e:
        print(f"ERROR: Permission denied — target may require elevation: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"ERROR: Swap failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Clean up source temp dir
    shutil.rmtree(source, ignore_errors=True)

    # Relaunch
    if args.relaunch and Path(args.relaunch).exists():
        print(f"Relaunching: {args.relaunch}")
        _nw = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [args.relaunch],
            creationflags=_nw,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    print("Update helper complete")


if __name__ == "__main__":
    main()
