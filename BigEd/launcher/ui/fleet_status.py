"""Fleet monitoring utility functions — extracted from launcher.py (Phase 1 Hybrid ViewPort).

Module-level functions for fleet status parsing, log reading, hardware stats,
process lifecycle (zombie sweep, graceful shutdown, model unload), and
advisory/HITL counts.

All functions are stateless with respect to the GUI — they read files, query
the database, or inspect running processes.  Imported back into launcher.py
so existing callers (``launcher.parse_status()`` etc.) continue to work.
"""

import json
import os
import time

import psutil

# ---------------------------------------------------------------------------
# Path constants — computed the same way launcher.py does, by importing them.
# We use a lazy accessor so the module can be imported before launcher
# finishes initialising (avoids circular-import issues).
# ---------------------------------------------------------------------------

def _paths():
    """Return (FLEET_DIR, STATUS_MD, LOGS_DIR, HW_STATE_JSON, PENDING_DIR).

    Lazily imports from ``launcher`` on first call so this module can live
    inside ``ui/`` without creating a circular import at load time.
    """
    import launcher as _L
    return _L.FLEET_DIR, _L.STATUS_MD, _L.LOGS_DIR, _L.HW_STATE_JSON, _L.PENDING_DIR


# ---------------------------------------------------------------------------
# Supervisor / Dr. Ders liveness
# ---------------------------------------------------------------------------

def _check_supervisor_liveness():
    """Check supervisor and Dr. Ders liveness via file mtime.

    Returns dict with supervisor_status and dr_ders_status keys.
    Used by both parse_status() and the SSE handler.
    """
    _, STATUS_MD, _, HW_STATE_JSON, _ = _paths()
    result = {"supervisor_status": "OFFLINE", "dr_ders_status": "OFFLINE"}

    if HW_STATE_JSON.exists():
        try:
            mtime = HW_STATE_JSON.stat().st_mtime
            age = time.time() - mtime
            if age < 30:
                hw_data = json.loads(HW_STATE_JSON.read_text(encoding="utf-8"))
                if hw_data.get("status") == "transitioning":
                    result["dr_ders_status"] = "TRANSIT"
                else:
                    result["dr_ders_status"] = "ONLINE"
            elif age < 120:
                result["dr_ders_status"] = "HUNG"
        except Exception:
            pass

    if STATUS_MD.exists():
        try:
            mtime = STATUS_MD.stat().st_mtime
            age = time.time() - mtime
            if age < 30:
                result["supervisor_status"] = "ONLINE"
            elif age < 120:
                result["supervisor_status"] = "HUNG"
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# Zombie sweep
# ---------------------------------------------------------------------------

def _zombie_sweep() -> list:
    """Final sweep: kill any orphaned fleet processes still running.
    Cross-platform — uses psutil. Returns list of killed process names."""
    killed = []
    try:
        import psutil
        fleet_scripts = {"supervisor.py", "hw_supervisor.py", "worker.py",
                         "dashboard.py", "dispatch_marathon.py", "train.py"}
        my_pid = os.getpid()
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if proc.pid == my_pid:
                    continue
                cmdline = ' '.join(proc.info.get('cmdline') or [])
                for script in fleet_scripts:
                    if script in cmdline:
                        proc.kill()
                        killed.append(f"{script}(pid={proc.pid})")
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass
    return killed


# ---------------------------------------------------------------------------
# Graceful shutdown helpers
# ---------------------------------------------------------------------------

def _graceful_save_tasks():
    """Requeue RUNNING tasks to PENDING and mark agents OFFLINE for clean resume.
    Writes a shutdown marker so next boot knows to recover."""
    import sqlite3
    FLEET_DIR, _, _, _, _ = _paths()
    db_path = FLEET_DIR / "fleet.db"
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        # Count tasks to requeue
        running = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='RUNNING'").fetchone()[0]
        if running > 0:
            conn.execute("UPDATE tasks SET status='PENDING', assigned_to=NULL WHERE status='RUNNING'")
        # Mark all agents offline
        conn.execute("UPDATE agents SET status='OFFLINE', current_task_id=NULL")
        # Write shutdown marker
        conn.execute("""
            INSERT OR REPLACE INTO notes (from_agent, to_agent, body_json, channel)
            VALUES ('system', 'system', ?, 'sup')
        """, (json.dumps({
            "type": "graceful_shutdown",
            "timestamp": time.time(),
            "tasks_requeued": running,
        }),))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _unload_all_ollama_models():
    """Unload all Ollama models (keep_alive=0) to free VRAM on app close.
    Ollama stays running — just releases model memory."""
    import urllib.request
    try:
        host = "http://localhost:11434"
        # Get loaded models
        with urllib.request.urlopen(f"{host}/api/ps", timeout=3) as r:
            data = json.loads(r.read())
        models = [m["name"] for m in data.get("models", [])]
        for model in models:
            try:
                body = json.dumps({"model": model, "keep_alive": 0}).encode()
                req = urllib.request.Request(
                    f"{host}/api/generate", data=body, method="POST",
                    headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=5)
            except Exception:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Status parser (cached)
# ---------------------------------------------------------------------------

_status_cache = None
_status_cache_time = 0.0


def parse_status():
    """Read STATUS.md and return dict with agents + task counts.

    Cached with 2s TTL — multiple callers in the same refresh cycle
    share a single file read + parse.
    """
    global _status_cache, _status_cache_time
    _, STATUS_MD, _, _, _ = _paths()
    now = time.time()
    if _status_cache is not None and (now - _status_cache_time) < 2:
        return _status_cache

    result = {"agents": [], "tasks": {}, "raw": "", "supervisor_status": "OFFLINE", "dr_ders_status": "OFFLINE"}

    result.update(_check_supervisor_liveness())

    if not STATUS_MD.exists():
        _status_cache = result
        _status_cache_time = now
        return result
    try:
        text = STATUS_MD.read_text(encoding="utf-8", errors="ignore")
        result["raw"] = text
        lines = text.splitlines()
        in_agents = False
        in_tasks = False
        for line in lines:
            if "## Agents" in line:
                in_agents = True
                in_tasks = False
                continue
            if "## Tasks" in line:
                in_agents = False
                in_tasks = True
                continue
            if line.startswith("## "):
                in_agents = False
                in_tasks = False
            if in_agents and line.startswith("|") and not line.startswith("| Name") and not line.startswith("|--"):
                parts = [p.strip() for p in line.split("|")[1:-1]]
                if len(parts) >= 3:
                    agent = {"name": parts[0], "role": parts[1], "status": parts[2]}
                    if len(parts) >= 4:
                        agent["task"] = parts[3]
                    result["agents"].append(agent)
            if in_tasks and line.strip().startswith("- "):
                for tok in line.split():
                    for key in ("Pending:", "Running:", "Done:", "Failed:"):
                        if tok.startswith(key):
                            try:
                                result["tasks"][key.rstrip(":")] = int(tok[len(key):])
                            except ValueError:
                                pass
    except Exception:
        pass

    _status_cache = result
    _status_cache_time = now
    return result


# ---------------------------------------------------------------------------
# Log reading
# ---------------------------------------------------------------------------

def read_log_tail(agent: str, n=60) -> str:
    _, _, LOGS_DIR, _, _ = _paths()
    if agent == "all":
        return _read_combined_logs(n)
    f = LOGS_DIR / f"{agent}.log"
    if not f.exists():
        return f"[no log: {agent}.log]"
    try:
        lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(lines[-n:])
    except Exception as e:
        return f"[read error: {e}]"


def _read_combined_logs(n=80) -> str:
    """Read recent lines from all log files, sorted by timestamp."""
    _, _, LOGS_DIR, _, _ = _paths()
    all_lines = []
    for f in LOGS_DIR.glob("*.log"):
        try:
            agent_name = f.stem
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines()[-30:]:
                # Prefix with agent name for identification
                all_lines.append((line, f"[{agent_name}] {line}"))
        except Exception:
            continue
    # Sort by the raw line (timestamps at start sort naturally)
    all_lines.sort(key=lambda x: x[0])
    return "\n".join(tagged for _, tagged in all_lines[-n:])


# ---------------------------------------------------------------------------
# Hardware stats
# ---------------------------------------------------------------------------

def get_hw_stats(prev_net, prev_time):
    """Return (cpu_str, ram_str, gpu_str, net_str, net_counters, now)."""
    import launcher as _L

    # CPU
    cpu = psutil.cpu_percent(interval=None)
    cpu_str = f"CPU {cpu:.0f}%"

    # RAM
    vm = psutil.virtual_memory()
    ram_str = f"RAM {vm.used/1e9:.1f}/{vm.total/1e9:.1f} GB  {vm.percent:.0f}%"

    # GPU — call _ensure_gpu() first, then read the (possibly-mutated) globals
    _L._ensure_gpu()
    if _L._GPU_OK:
        try:
            util = _L._pynvml.nvmlDeviceGetUtilizationRates(_L._GPU_HANDLE)
            mem  = _L._pynvml.nvmlDeviceGetMemoryInfo(_L._GPU_HANDLE)
            gpu_str = (f"GPU {util.gpu}%  "
                       f"VRAM {mem.used/1e9:.1f}/{mem.total/1e9:.1f} GB")
        except Exception:
            gpu_str = "GPU err"
    else:
        gpu_str = "GPU N/A"

    # Network — find Ethernet interface with most traffic
    now = time.time()
    net_str = "NET —"
    counters = psutil.net_io_counters(pernic=True)
    eth = None
    for name, c in counters.items():
        if "loopback" in name.lower() or "lo" == name.lower():
            continue
        if "eth" in name.lower() or "ethernet" in name.lower() or "local area" in name.lower():
            eth = (name, c)
            break
    if eth is None and counters:
        # fallback: pick interface with most bytes
        eth = max(counters.items(), key=lambda x: x[1].bytes_sent + x[1].bytes_recv)

    if eth and prev_net and prev_time:
        name, c = eth
        dt = now - prev_time or 1
        prev_c = prev_net.get(name)
        if prev_c:
            tx = (c.bytes_sent - prev_c.bytes_sent) / dt
            rx = (c.bytes_recv - prev_c.bytes_recv) / dt
            def fmt(b):
                if b >= 1e6: return f"{b/1e6:.1f} MB/s"
                if b >= 1e3: return f"{b/1e3:.0f} KB/s"
                return f"{b:.0f} B/s"
            net_str = f"NET  ↑{fmt(tx)}  ↓{fmt(rx)}"

    new_prev = {name: c for name, c in counters.items()}
    return cpu_str, ram_str, gpu_str, net_str, new_prev, now


# ---------------------------------------------------------------------------
# Advisory / HITL counts
# ---------------------------------------------------------------------------

def count_pending_advisories() -> int:
    _, _, _, _, PENDING_DIR = _paths()
    if not PENDING_DIR.exists():
        return 0
    return len(list(PENDING_DIR.glob("advisory_*.md")))


def count_waiting_human() -> int:
    FLEET_DIR, _, _, _, _ = _paths()
    from data_access import FleetDB
    return FleetDB.count_waiting_human(FLEET_DIR / "fleet.db")
