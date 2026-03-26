"""PID file manager — daemon-style process identity for fleet components.

Each core process (supervisor, hw_supervisor, workers) writes a PID file on start
and checks for existing instances before spawning. Prevents duplicate processes.

Usage:
    from pid_manager import acquire_pid, release_pid, is_running, cleanup_stale

    # On startup — returns True if acquired, False if another instance is alive
    if not acquire_pid("supervisor"):
        print("Supervisor already running")
        sys.exit(0)

    # On shutdown
    release_pid("supervisor")

    # Before spawning a child
    if is_running("hw_supervisor"):
        print("Dr. Ders already alive, skipping spawn")
    else:
        spawn_hw_supervisor()

PID files: fleet/pids/<name>.pid (gitignored)
"""
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

FLEET_DIR = Path(__file__).resolve().parent
PID_DIR = FLEET_DIR / "pids"

# Fixed IDs for core components — used as PID file names
CORE_IDS = {
    "supervisor": "supervisor",
    "hw_supervisor": "hw_supervisor",
    "dr_ders": "hw_supervisor",  # alias
}


def _ensure_pid_dir():
    """Create pids/ directory if missing."""
    PID_DIR.mkdir(parents=True, exist_ok=True)


def _pid_path(name: str) -> Path:
    """Return path to PID file for a component."""
    return PID_DIR / f"{name}.pid"


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    if pid <= 0:
        return False
    try:
        import psutil
        p = psutil.Process(pid)
        return p.is_running() and p.status() != "zombie"
    except Exception:
        # Fallback without psutil
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False


def acquire_pid(name: str) -> bool:
    """Write PID file for this process. Returns False if another instance is alive.

    Call on startup. If returns False, exit — another instance owns this role.
    """
    _ensure_pid_dir()
    pid_file = _pid_path(name)

    # Check for existing instance
    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text().strip())
            if _is_process_alive(existing_pid):
                log.info("PID %s: process %d already running, skipping", name, existing_pid)
                return False
            else:
                log.info("PID %s: stale PID file (process %d dead), taking over", name, existing_pid)
        except (ValueError, OSError):
            log.warning("PID %s: corrupt PID file, overwriting", name)

    # Write our PID
    pid_file.write_text(str(os.getpid()))
    log.info("PID %s: acquired (pid=%d)", name, os.getpid())
    return True


def release_pid(name: str):
    """Remove PID file on clean shutdown."""
    pid_file = _pid_path(name)
    try:
        if pid_file.exists():
            stored_pid = int(pid_file.read_text().strip())
            if stored_pid == os.getpid():
                pid_file.unlink()
                log.info("PID %s: released", name)
            else:
                log.warning("PID %s: not ours (stored=%d, us=%d), leaving", name, stored_pid, os.getpid())
    except Exception as e:
        log.warning("PID %s: release failed: %s", name, e)


def is_running(name: str) -> bool:
    """Check if a component is running (PID file exists and process alive)."""
    pid_file = _pid_path(name)
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        return _is_process_alive(pid)
    except (ValueError, OSError):
        return False


def get_pid(name: str) -> int | None:
    """Get the PID of a running component, or None."""
    pid_file = _pid_path(name)
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
        return pid if _is_process_alive(pid) else None
    except (ValueError, OSError):
        return None


def cleanup_stale():
    """Remove all PID files for dead processes. Call on boot."""
    _ensure_pid_dir()
    cleaned = 0
    for pid_file in PID_DIR.glob("*.pid"):
        try:
            pid = int(pid_file.read_text().strip())
            if not _is_process_alive(pid):
                pid_file.unlink()
                cleaned += 1
                log.info("PID cleanup: removed stale %s (pid=%d)", pid_file.stem, pid)
        except (ValueError, OSError):
            pid_file.unlink()
            cleaned += 1
    return cleaned


def list_running() -> dict[str, int]:
    """Return dict of {name: pid} for all running components."""
    _ensure_pid_dir()
    running = {}
    for pid_file in PID_DIR.glob("*.pid"):
        try:
            pid = int(pid_file.read_text().strip())
            if _is_process_alive(pid):
                running[pid_file.stem] = pid
        except (ValueError, OSError):
            pass
    return running


def acquire_worker_pid(role: str) -> bool:
    """Acquire PID for a worker by role name (e.g., 'coder_1', 'researcher')."""
    return acquire_pid(f"worker_{role}")


def release_worker_pid(role: str):
    """Release PID for a worker."""
    release_pid(f"worker_{role}")


def is_worker_running(role: str) -> bool:
    """Check if a specific worker is running."""
    return is_running(f"worker_{role}")
