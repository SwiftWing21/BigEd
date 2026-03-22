"""
BigEd CC — GUI launcher for the Education agent fleet.
Dark mode, brick theme. Native Windows process management via psutil.
"""
import base64
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import platform
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import urllib.request

import tkinter as tk
import customtkinter as ctk
from PIL import Image
import psutil
import tomlkit

from fleet_api import (
    fleet_api as _fleet_api_call, fleet_health, fleet_stop,
    ollama_tags, ollama_ps, ollama_is_running, ollama_keepalive,
)

# GPU via pynvml (NVIDIA); lazy init for faster startup
_GPU_OK = None  # None = not yet checked
_GPU_HANDLE = None
_pynvml = None  # module ref, set by _ensure_gpu()

def _ensure_gpu():
    global _GPU_OK, _GPU_HANDLE, _pynvml
    if _GPU_OK is not None:
        return _GPU_OK
    try:
        import pynvml
        pynvml.nvmlInit()
        _GPU_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
        _pynvml = pynvml
        _GPU_OK = True
    except Exception:
        _GPU_OK = False
    return _GPU_OK

# PyInstaller bundles assets into sys._MEIPASS; fall back to script dir
if getattr(sys, "frozen", False):
    HERE     = Path(sys._MEIPASS)
    _SRC_DIR  = Path(sys.executable).parent.parent   # launcher/
    _DIST_DIR = Path(sys.executable).parent          # dist/
else:
    HERE     = Path(__file__).parent
    _SRC_DIR  = Path(__file__).parent
    _DIST_DIR = Path(__file__).parent / "dist"

# Developer mode — show advanced features (default ON during alpha)
# Set to False for production builds, or use env var BIGED_PRODUCTION=1
DEV_MODE = os.environ.get("BIGED_PRODUCTION", "").lower() not in ("1", "true")
# Production mode: frozen exe with _production_marker OR BIGED_PRODUCTION env var
_PRODUCTION_MARKER = _DIST_DIR / "_production_marker" if getattr(sys, 'frozen', False) else None
DEV_MODE = not (_PRODUCTION_MARKER and _PRODUCTION_MARKER.exists()) and \
           os.environ.get("BIGED_PRODUCTION", "").lower() not in ("1", "true")


def _get_fleet_python():
    """Get Python interpreter for launching fleet scripts.
    When frozen, sys.executable is BigEdCC.exe — we need actual Python.
    """
    import shutil
    if getattr(sys, 'frozen', False):
        # Try to find Python on PATH
        for name in ["python", "python3", "py"]:
            found = shutil.which(name)
            if found and "BigEdCC" not in found:
                return found
        # Try uv
        uv = shutil.which("uv")
        if uv:
            return uv  # caller should use [uv, "run", "python", script]
        return "python"  # hope it's on PATH
    return sys.executable

# ─── Paths ────────────────────────────────────────────────────────────────────
# Dynamically compute project root by walking up until we find fleet/
def _find_fleet_dir():
    """Walk up from _SRC_DIR looking for a sibling 'fleet/' directory."""
    # Environment override for portability / testing
    env = os.environ.get("BIGED_FLEET_DIR")
    if env:
        p = Path(env)
        if p.is_dir():
            return p
            
    # Check settings.json for cached fleet_dir
    settings_file = HERE / "data" / "settings.json"
    if settings_file.exists():
        try:
            p = Path(json.loads(settings_file.read_text()).get("fleet_dir", ""))
            if p.is_dir() and (p / "fleet.toml").exists():
                return p
        except Exception:
            pass

    # Walk up from launcher dir, check each ancestor for fleet/ child
    anchor = _SRC_DIR
    for _ in range(6):  # max 6 levels up
        candidate = anchor.parent / "fleet"
        if candidate.is_dir() and (candidate / "fleet.toml").exists():
            return candidate
        anchor = anchor.parent
        
    # Dev fallback: use USERPROFILE env var if available
    dev_fallback = Path(os.environ.get("USERPROFILE", os.path.expanduser("~"))) / "Projects" / "Education" / "fleet"
    if dev_fallback.is_dir() and (dev_fallback / "fleet.toml").exists():
        return dev_fallback

    # Fallback: original relative assumption
    return _SRC_DIR.parent.parent / "fleet"

FLEET_DIR    = _find_fleet_dir()
STATUS_MD    = FLEET_DIR / "STATUS.md"
FLEET_TOML   = FLEET_DIR / "fleet.toml"
LOGS_DIR     = FLEET_DIR / "logs"
HW_STATE_JSON = FLEET_DIR / "hw_state.json"
PENDING_DIR  = FLEET_DIR / "knowledge" / "security" / "pending"
REPORTS_DIR  = FLEET_DIR / "knowledge" / "reports"
LEADS_DIR    = FLEET_DIR / "knowledge" / "leads"
DATA_DIR     = HERE / "data"
DB_PATH      = DATA_DIR / "tools.db"

UPDATER_EXE     = _DIST_DIR / "Updater.exe"
UPDATE_MANIFEST = _DIST_DIR / ".update_manifest.json"
# Files whose changes should trigger a rebuild prompt
_UPDATE_TRACKED = {
    "launcher.py":      _SRC_DIR / "launcher.py",
    "requirements.txt": _SRC_DIR / "requirements.txt",
}

# ─── Theme ────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, BRAND, TEXT, DIM,
    GREEN, ORANGE, RED, MONO, FONT, FONT_SM, FONT_H,
    BLUE, CYAN, FONT_STAT, FONT_BOLD, FONT_TITLE, FONT_XS,
    HEADER_HEIGHT, BTN_HEIGHT,
)


def _relative_time(iso_str):
    """Convert ISO datetime string to relative time like '2m ago', '1h ago'."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        mins = secs // 60
        if mins < 60:
            return f"{mins}m ago"
        hrs = mins // 60
        if hrs < 24:
            return f"{hrs}h ago"
        return f"{hrs // 24}d ago"
    except Exception:
        return ""


# ─── Agent Name Themes ───────────────────────────────────────────────────────
# Maps internal role names to themed display names.
# "default" uses the internal names as-is.

AGENT_THEMES = {
    "default": {
        "supervisor": "Supervisor",
        "researcher": "Researcher",
        "coder": "Coder",
        "archivist": "Archivist",
        "analyst": "Analyst",
        "sales": "Sales",
        "onboarding": "Onboarding",
        "implementation": "Implementation",
        "security": "Security",
        "planner": "Planner",
    },
    "education": {
        "supervisor": "Headmaster",
        "researcher": "Professor",
        "coder": "Engineer",
        "archivist": "Librarian",
        "analyst": "Examiner",
        "sales": "Recruiter",
        "onboarding": "Orientation",
        "implementation": "Lab Tech",
        "security": "Campus Security",
        "planner": "Dean",
    },
    "space": {
        "supervisor": "Admiral",
        "researcher": "Science Officer",
        "coder": "Engineer",
        "archivist": "Quartermaster",
        "analyst": "Navigator",
        "sales": "Diplomat",
        "onboarding": "Cadet Trainer",
        "implementation": "Ops Chief",
        "security": "Tactical",
        "planner": "Helmsman",
    },
    "forge": {
        "supervisor": "Forgemaster",
        "researcher": "Alchemist",
        "coder": "Artificer",
        "archivist": "Lorekeeper",
        "analyst": "Assayer",
        "sales": "Merchant",
        "onboarding": "Apprentice Master",
        "implementation": "Smith",
        "security": "Sentinel",
        "planner": "Architect",
    },
    "cul-de-sac": {
        "supervisor": "Ed",
        "researcher": "Edd",
        "coder": "Eddie",
        "archivist": "Plank",
        "analyst": "Jimmy",
        "sales": "Kevin",
        "onboarding": "Nazz",
        "implementation": "Rolf",
        "security": "Jonny",
        "planner": "Sarah",
    },
    "robots": {
        "supervisor": "Optimus",
        "researcher": "Data",
        "coder": "Cortana",
        "archivist": "JARVIS",
        "analyst": "GLaDOS",
        "sales": "HAL",
        "onboarding": "Samantha",
        "implementation": "Bender",
        "security": "T-800",
        "planner": "TARS",
    },
}

_active_theme = "default"
_custom_names = {}  # role -> custom name overrides


def _load_settings() -> dict:
    """Load full settings dict."""
    settings_file = DATA_DIR / "settings.json"
    if settings_file.exists():
        try:
            return json.loads(settings_file.read_text())
        except Exception:
            pass
    return {}


def _save_settings(data: dict):
    """Persist settings dict."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings_file = DATA_DIR / "settings.json"
    settings_file.write_text(json.dumps(data, indent=2))


# ─── Apply saved UI scale before any widgets are created ──────────────────────
_saved_scale = _load_settings().get("ui_scale", 1.0)
if _saved_scale != 1.0:
    from ui.theme import apply_scale
    apply_scale(_saved_scale)


def _load_theme_preference() -> str:
    """Load saved theme from settings file."""
    return _load_settings().get("agent_theme", "default")


def _load_custom_names() -> dict:
    """Load individual agent name overrides."""
    return _load_settings().get("agent_names", {})


def _save_theme_preference(theme: str):
    """Persist theme choice."""
    data = _load_settings()
    data["agent_theme"] = theme
    _save_settings(data)


def _save_custom_names(names: dict):
    """Persist individual agent name overrides."""
    data = _load_settings()
    data["agent_names"] = {k: v for k, v in names.items() if v}  # drop blanks
    _save_settings(data)


def themed_name(role: str) -> str:
    """Get the display name for an agent role.

    Priority: custom name > theme name > title-cased role.
    """
    # Custom name takes priority (exact match including numbered agents)
    if role in _custom_names and _custom_names[role]:
        return _custom_names[role]

    theme_map = AGENT_THEMES.get(_active_theme, AGENT_THEMES["default"])
    # Handle numbered agents like coder_1, coder_2
    base_role = re.sub(r'_\d+$', '', role)
    suffix = role[len(base_role):]  # e.g. "_1", "_2", or ""

    # Check custom name for base role too
    if base_role in _custom_names and _custom_names[base_role]:
        display = _custom_names[base_role]
        if suffix:
            display += f" {suffix.lstrip('_')}"
        return display

    display = theme_map.get(base_role, role.replace("_", " ").title())
    if suffix:
        display += f" {suffix.lstrip('_')}"
    return display


def _shell_safe(s: str) -> str:
    """Sanitize a string for safe shell interpolation (alphanumeric, _, -, . only)."""
    return re.sub(r'[^a-zA-Z0-9_.\-:]', '', s)


# ─── Fleet Bridge (cross-platform command execution) ─────────────────────────
try:
    from fleet_bridge import create_bridge
    _HAS_BRIDGE = True
except ImportError:
    _HAS_BRIDGE = False
    create_bridge = None

_bridge = create_bridge(FLEET_DIR) if _HAS_BRIDGE else None


def wsl(cmd: str, capture=False, timeout=60):
    """Run a command in the fleet environment (WSL on Windows, native on Linux/macOS)."""
    if not _HAS_BRIDGE or _bridge is None:
        return "" if capture else None
    return _bridge.run(cmd, capture=capture, timeout=timeout)


def wsl_bg(cmd: str, callback=None, timeout=60):
    """Run fleet command in a background thread; call callback(stdout, stderr) when done."""
    if not _HAS_BRIDGE or _bridge is None:
        if callback:
            callback("", "fleet_bridge not available")
        return
    _bridge.run_bg(cmd, callback=callback, timeout=timeout)


# ─── Central model config ─────────────────────────────────────────────────────
def load_model_cfg() -> dict:
    """Read [models] and [review] from fleet.toml. Returns merged dict with safe defaults."""
    defaults = {
        "local":        "qwen3:8b",
        "complex":      "claude-sonnet-4-6",
        "ollama_host":  "http://localhost:11434",
        "claude_model": "claude-sonnet-4-6",
        "gemini_model": "gemini-2.0-flash",
        "local_model":  "qwen3:8b",
        "local_think":  True,
        "local_ctx":    16384,
    }
    try:
        import tomllib
        with open(FLEET_TOML, "rb") as f:
            data = tomllib.load(f)
        cfg = {**defaults, **data.get("models", {}), **data.get("review", {})}
        return cfg
    except Exception:
        return defaults


def _quick_key_check(env_name: str) -> bool:
    """Fast check if an API key is configured (env var or ~/.secrets).

    Does NOT validate the key — just checks if a non-empty value exists.
    """
    # Check environment variable first (fastest)
    if os.environ.get(env_name, "").strip():
        return True
    # Check ~/.secrets (WSL-style export file)
    secrets = Path.home() / ".secrets"
    if secrets.exists():
        try:
            for line in secrets.read_text(errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                # Handle: export KEY=value or KEY=value
                clean = line.replace("export ", "", 1)
                if clean.startswith(env_name + "="):
                    val = clean.split("=", 1)[1].strip().strip("'\"")
                    if val:
                        return True
        except Exception:
            pass
    return False


def _fleet_mode() -> str:
    """Return 'air_gap', 'offline', or 'online' based on fleet.toml flags."""
    try:
        import tomllib
        with open(FLEET_TOML, "rb") as f:
            data = tomllib.load(f)
        fleet = data.get("fleet", {})
        if fleet.get("air_gap_mode", False):
            return "air_gap"
        if fleet.get("offline_mode", False):
            return "offline"
    except Exception:
        pass
    return "online"


def _get_version() -> str:
    """Read version from install marker, git tag, or fallback."""
    # 1. Installed version file (.bigedcc_version)
    for d in [Path(sys.executable).parent, Path(__file__).parent / "dist"]:
        vf = d / ".bigedcc_version"
        if vf.exists():
            try:
                return vf.read_text(encoding="utf-8").strip().lstrip("v")
            except Exception:
                pass
    # 2. Git describe (source dev mode)
    try:
        r = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=3,
            cwd=str(Path(__file__).parent.parent.parent),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().lstrip("v")
    except Exception:
        pass
    return "dev"


def load_tab_cfg() -> dict:
    """Read [launcher.tabs] from fleet.toml to determine which UI tabs are enabled."""
    defaults = {
        "command_center": True,
        "agents": True,
        "crm": False,
        "onboarding": False,
        "customers": False,
        "accounts": False,
        "ingestion": True,
        "outputs": True,
        "owner_core": False,
        "intelligence": True,
        "manual_mode": True,
    }
    try:
        import tomllib
        with open(FLEET_TOML, "rb") as f:
            data = tomllib.load(f)
        return {**defaults, **data.get("launcher", {}).get("tabs", {})}
    except Exception:
        return defaults


# ─── Status parser ────────────────────────────────────────────────────────────
def _check_supervisor_liveness():
    """Check supervisor and Dr. Ders liveness via file mtime.

    Returns dict with supervisor_status and dr_ders_status keys.
    Used by both parse_status() and the SSE handler.
    """
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


def _graceful_save_tasks():
    """Requeue RUNNING tasks to PENDING and mark agents OFFLINE for clean resume.
    Writes a shutdown marker so next boot knows to recover."""
    import sqlite3
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


_status_cache = None
_status_cache_time = 0.0


def parse_status():
    """Read STATUS.md and return dict with agents + task counts.

    Cached with 2s TTL — multiple callers in the same refresh cycle
    share a single file read + parse.
    """
    global _status_cache, _status_cache_time
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


def read_log_tail(agent: str, n=60) -> str:
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


def get_hw_stats(prev_net, prev_time):
    """Return (cpu_str, ram_str, gpu_str, net_str, net_counters, now)."""
    # CPU
    cpu = psutil.cpu_percent(interval=None)
    cpu_str = f"CPU {cpu:.0f}%"

    # RAM
    vm = psutil.virtual_memory()
    ram_str = f"RAM {vm.used/1e9:.1f}/{vm.total/1e9:.1f} GB  {vm.percent:.0f}%"

    # GPU
    _ensure_gpu()
    if _GPU_OK:
        try:
            util = _pynvml.nvmlDeviceGetUtilizationRates(_GPU_HANDLE)
            mem  = _pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
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


def count_pending_advisories() -> int:
    if not PENDING_DIR.exists():
        return 0
    return len(list(PENDING_DIR.glob("advisory_*.md")))


def count_waiting_human() -> int:
    from data_access import FleetDB
    return FleetDB.count_waiting_human(FLEET_DIR / "fleet.db")


def _ctx_preview_confirm(parent, model: str, file_list: str) -> bool:
    """Show a modal confirmation dialog listing context files and ToS before VS Code launch.

    Returns True if the user clicks Proceed, False if they click Cancel.
    """
    result = [False]
    dlg = ctk.CTkToplevel(parent)
    dlg.title("Launch Session — Context Preview")
    dlg.geometry("480x380")
    dlg.resizable(False, False)
    dlg.configure(fg_color="#1e1e1e")
    dlg.transient(parent)
    dlg.grab_set()

    ctk.CTkLabel(dlg, text=f"Launch {model} session",
                 font=("RuneScape Bold 12", 12, "bold"),
                 text_color="#c8a84b", anchor="w").pack(fill="x", padx=16, pady=(14, 4))
    ctk.CTkLabel(dlg, text="The following context files will be written:",
                 font=("Consolas", 9), text_color="#888888", anchor="w"
                 ).pack(fill="x", padx=16)

    files_frame = ctk.CTkFrame(dlg, fg_color="#242424", corner_radius=6)
    files_frame.pack(fill="x", padx=16, pady=6)
    ctk.CTkLabel(files_frame, text=file_list, font=("Consolas", 9),
                 text_color="#c8c8c8", anchor="w", justify="left"
                 ).pack(padx=10, pady=8, anchor="w")

    # ── What happens next ──
    if "Claude" in model:
        steps = (
            "What happens next:\n"
            "  1. VS Code opens to the project directory\n"
            "  2. Claude Code starts with your task context\n"
            "  3. Review the task and approve to begin"
        )
        tos = "By proceeding you agree to Anthropic's Terms of Service."
    else:
        steps = (
            "What happens next:\n"
            "  1. Google AI Studio opens in your browser\n"
            "  2. Paste your task from task-briefing.md\n"
            "  3. Review the output and copy results back"
        )
        tos = "By proceeding you agree to Google's Terms of Service."

    ctk.CTkLabel(dlg, text=steps, font=("Consolas", 9),
                 text_color="#c8c8c8", anchor="w", justify="left"
                 ).pack(fill="x", padx=16, pady=(4, 2))

    # ── ToS notice ──
    tos_frame = ctk.CTkFrame(dlg, fg_color="#2a2200", corner_radius=4)
    tos_frame.pack(fill="x", padx=16, pady=(4, 6))
    ctk.CTkLabel(tos_frame, text=f"⚠  {tos}",
                 font=("Consolas", 9), text_color="#c8a84b", anchor="w",
                 wraplength=420).pack(padx=10, pady=6, anchor="w")

    btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
    btn_row.pack(fill="x", padx=16, pady=(4, 14))

    def proceed():
        result[0] = True
        dlg.destroy()

    ctk.CTkButton(btn_row, text="Proceed", width=100, height=30,
                  fg_color="#b22222", hover_color="#8b0000", text_color="white",
                  font=("Consolas", 10), command=proceed).pack(side="right")
    ctk.CTkButton(btn_row, text="Cancel", width=80, height=30,
                  fg_color="#2d2d2d", hover_color="#3a3a3a", text_color="#888888",
                  font=("Consolas", 10), command=dlg.destroy).pack(side="right", padx=(0, 8))

    parent.wait_window(dlg)
    return result[0]


# ─── Tooltip ──────────────────────────────────────────────────────────────────
class Tooltip:
    """Hover tooltip for any tkinter/CTk widget. Shows after 500 ms, right of widget."""
    _DELAY = 500

    def __init__(self, widget, text: str):
        self._widget   = widget
        self._text     = text
        self._win      = None
        self._after_id = None
        widget.bind("<Enter>",       self._schedule, add="+")
        widget.bind("<Leave>",       self._hide,     add="+")
        widget.bind("<ButtonPress>", self._hide,     add="+")

    def _schedule(self, _=None):
        self._cancel()
        self._after_id = self._widget.after(self._DELAY, self._show)

    def _cancel(self):
        if self._after_id:
            self._widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self):
        if self._win:
            return
        x = self._widget.winfo_rootx() + self._widget.winfo_width() + 6
        y = self._widget.winfo_rooty() + 4
        self._win = win = tk.Toplevel(self._widget)
        win.wm_overrideredirect(True)
        win.wm_geometry(f"+{x}+{y}")
        win.configure(bg="#3a3a3a")
        tk.Label(
            win, text=self._text, font=("RuneScape Plain 11", 9),
            bg="#3a3a3a", fg="#e2e2e2", padx=8, pady=5,
            justify="left", wraplength=220,
        ).pack()

    def _hide(self, _=None):
        self._cancel()
        if self._win:
            self._win.destroy()
            self._win = None


# ─── Boot sequence (extracted to ui/boot.py — TECH_DEBT 4.1) ─────────────────
from ui.boot import BootManagerMixin, _kill_fleet_processes, _kill_ollama

# ─── Custom Tab Bar ───────────────────────────────────────────────────────────
class CustomTabBar(ctk.CTkFrame):
    """Icon + label tab switcher — drop-in API replacement for CTkTabview.

    Provides .add(name), .tab(name), .set(name), .get() to match the
    CTkTabview interface used throughout the app.

    Active tab: gold text + 3-px ACCENT indicator beneath the button.
    Inactive tab: dim text, transparent bg, BG3 on hover.
    """

    _ICONS: dict[str, str] = {
        "Command Center": "🖥",
        "Fleet":          "⚡",
        "Fleet Comm":     "💬",
        "Accounts":       "📋",
        "CRM":            "🤝",
        "Customers":      "👥",
        "Ingestion":      "📥",
        "Onboarding":     "🎓",
        "Outputs":        "📤",
        "Owner":          "👤",
        "Intelligence":   "🧠",
        "Manual Mode":    "🔧",
    }

    def __init__(self, master, **kwargs):
        # Strip any CTkTabview colour kwargs callers might pass — we manage colours ourselves
        for _k in ("fg_color", "segmented_button_fg_color", "segmented_button_selected_color",
                   "segmented_button_selected_hover_color", "segmented_button_unselected_color",
                   "segmented_button_unselected_hover_color", "text_color", "corner_radius"):
            kwargs.pop(_k, None)
        super().__init__(master, fg_color="transparent", corner_radius=0, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)   # 0=bar strip, 1=separator, 2=content

        # ── Tab button strip (scrollable) ────────────────────────────────────
        bar_container = ctk.CTkFrame(self, fg_color=BG2, height=42, corner_radius=0)
        bar_container.grid(row=0, column=0, sticky="ew")
        bar_container.grid_propagate(False)
        bar_container.grid_columnconfigure(1, weight=1)

        # Left scroll chevron
        self._scroll_left_btn = ctk.CTkButton(
            bar_container, text="\u25C0", width=20, height=38,
            font=FONT_SM, fg_color="transparent", hover_color=BG3,
            text_color=DIM, corner_radius=0,
            command=lambda: self._scroll_tabs(-1))
        self._scroll_left_btn.grid(row=0, column=0, sticky="ns")

        # Inner frame for tab buttons
        self._bar = ctk.CTkFrame(bar_container, fg_color=BG2, height=42, corner_radius=0)
        self._bar.grid(row=0, column=1, sticky="nsew")
        self._bar.grid_propagate(False)

        # Right scroll chevron
        self._scroll_right_btn = ctk.CTkButton(
            bar_container, text="\u25B6", width=20, height=38,
            font=FONT_SM, fg_color="transparent", hover_color=BG3,
            text_color=DIM, corner_radius=0,
            command=lambda: self._scroll_tabs(1))
        self._scroll_right_btn.grid(row=0, column=2, sticky="ns")

        self._tab_scroll_offset = 0
        self._all_tab_cells: list = []
        self._tab_names_order: list[str] = []
        self._tab_widths: dict[str, int] = {}  # actual tab widths stored at creation time
        self._tabs_ready = False  # set True after all tabs registered — prevents premature scroll

        # Mouse-wheel horizontal scroll (Windows + Linux)
        for _widget in (self._bar, bar_container):
            _widget.bind("<MouseWheel>", self._on_mousewheel)
            _widget.bind("<Button-4>", self._on_mousewheel)
            _widget.bind("<Button-5>", self._on_mousewheel)

        # Recalculate visible tabs when bar gets its real size (debounced, only after ready)
        self._configure_after_id = None
        def _on_bar_configure(e):
            if not self._tabs_ready:
                return  # tabs still being added — don't scroll yet
            if self._configure_after_id:
                self.after_cancel(self._configure_after_id)
            self._configure_after_id = self.after(100, lambda: self._scroll_tabs(0))
        self._bar.bind("<Configure>", _on_bar_configure)

        # Full-width 1-px separator beneath the strip
        self._sep = ctk.CTkFrame(self, fg_color=BG3, height=1, corner_radius=0)
        self._sep.grid(row=1, column=0, sticky="ew")

        # ── Content area ──────────────────────────────────────────────────────
        self._content = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self._content.grid(row=2, column=0, sticky="nsew")
        self._content.grid_columnconfigure(0, weight=1)
        self._content.grid_rowconfigure(0, weight=1)

        self._tab_frames:    dict[str, ctk.CTkFrame]  = {}
        self._tab_buttons:   dict[str, ctk.CTkButton] = {}
        self._tab_indicators: dict[str, ctk.CTkFrame] = {}
        self._tab_badges:    dict[str, ctk.CTkLabel]  = {}
        self._tab_cells:     dict[str, ctk.CTkFrame]  = {}
        self._active: str = ""
        self._col: int = 0

    # ── Public API (mirrors CTkTabview) ───────────────────────────────────────

    def add(self, name: str) -> None:
        """Register a new tab — creates a button in the strip and a content frame."""
        icon = self._ICONS.get(name, "▸")

        # Cell: stacks button (row 0) + accent indicator (row 1)
        cell = ctk.CTkFrame(self._bar, fg_color="transparent", corner_radius=0)
        cell.grid(row=0, column=self._col, sticky="ns", padx=0)
        cell.grid_rowconfigure(0, weight=1)

        btn = ctk.CTkButton(
            cell,
            text=f"{icon}  {name}",
            font=FONT_SM,
            fg_color="transparent",
            hover_color=BG3,
            text_color=DIM,
            corner_radius=0,
            width=max(70, len(name) * 7 + 24),  # compact tabs — fit more in bar
            height=38,
            anchor="center",
            command=lambda n=name: self.set(n),
        )
        btn.grid(row=0, column=0, sticky="nsew")

        # 3-px accent indicator — transparent until this tab is active
        indicator = ctk.CTkFrame(cell, fg_color="transparent", height=3, corner_radius=0)
        indicator.grid(row=1, column=0, sticky="ew")
        indicator.grid_propagate(False)

        # Content frame (hidden until selected)
        content = ctk.CTkFrame(self._content, fg_color=BG, corner_radius=0)
        content.grid(row=0, column=0, sticky="nsew")
        content.grid_remove()

        self._tab_frames[name]     = content
        self._tab_buttons[name]    = btn
        self._tab_indicators[name] = indicator
        self._tab_cells[name]      = cell

        # Badge overlay (hidden by default) — shows pending HITL count
        badge_lbl = ctk.CTkLabel(
            cell, text="",
            font=("Consolas", 7, "bold"),
            text_color="white", fg_color="#d32f2f",
            corner_radius=8, width=16, height=16)
        self._tab_badges[name] = badge_lbl
        # Not placed yet — shown only when count > 0

        self._all_tab_cells.append(cell)
        self._tab_names_order.append(name)
        self._tab_widths[name] = max(70, len(name) * 7 + 24)  # matches button width formula
        self._col += 1
        # Don't scroll during add() — all tabs are visible by default.
        # Scroll only happens on user click, chevron press, or after init via Configure.

    def set_badge(self, name: str, count: int) -> None:
        """Show/hide a red count badge on a tab. count=0 hides it."""
        lbl = self._tab_badges.get(name)
        if not lbl:
            return
        if count > 0:
            lbl.configure(text=str(count) if count < 100 else "99+")
            lbl.place(relx=1.0, rely=0.0, x=-12, y=4, anchor="ne")
        else:
            lbl.place_forget()

    def tab(self, name: str) -> ctk.CTkFrame:
        """Return the content frame for a tab (used when building tab contents)."""
        return self._tab_frames[name]

    def set(self, name: str) -> None:
        """Switch to the named tab (lazy-builds deferred tabs on first view)."""
        if name not in self._tab_frames:
            return
        # Auto-scroll tab bar to keep the selected tab visible
        if name in self._tab_names_order:
            idx = self._tab_names_order.index(name)
            bar_width = self._bar.winfo_width()
            if bar_width <= 1:
                _vc = 5
            else:
                _vc, _tw = 0, 0
                for n in self._tab_names_order:
                    w = self._tab_widths.get(n, 80)
                    if _tw + w <= bar_width:
                        _vc += 1
                        _tw += w
                    else:
                        break
                _vc = max(3, _vc)
            if idx < self._tab_scroll_offset or idx >= self._tab_scroll_offset + _vc:
                self._tab_scroll_offset = max(0, idx - 2)
                self._scroll_tabs(0)
        # Build lazy tab content on first view
        app = self.winfo_toplevel()
        if hasattr(app, '_lazy_tabs') and name in app._lazy_tabs and name not in app._built_tabs:
            app._lazy_tabs[name](self._tab_frames[name])
            app._built_tabs.add(name)
        # Deactivate previous
        if self._active and self._active in self._tab_buttons:
            self._tab_buttons[self._active].configure(
                text_color=DIM, fg_color="transparent")
            self._tab_indicators[self._active].configure(fg_color="transparent")
            self._tab_frames[self._active].grid_remove()
        # Activate new
        self._active = name
        self._tab_buttons[name].configure(text_color=GOLD, fg_color=BG3)
        self._tab_indicators[name].configure(fg_color=ACCENT)
        self._tab_frames[name].grid()

    def get(self) -> str:
        """Return the name of the currently active tab."""
        return self._active

    # ── Scroll support ───────────────────────────────────────────────────────

    def _scroll_tabs(self, direction: int) -> None:
        """Scroll tab bar left (-1) or right (+1). 0 = refresh in place."""
        bar_width = self._bar.winfo_width()
        if bar_width <= 1:
            bar_width = 800  # reasonable default before render
        # Calculate how many tabs fit using stored widths
        visible_count = 0
        total_w = 0
        for name in self._tab_names_order:
            tw = self._tab_widths.get(name, 80)
            if total_w + tw <= bar_width:
                visible_count += 1
                total_w += tw
            else:
                break
        visible_count = max(3, visible_count)  # always show at least 3
        max_offset = max(0, len(self._all_tab_cells) - visible_count)
        self._tab_scroll_offset = max(0, min(
            self._tab_scroll_offset + direction, max_offset))
        # Re-grid: show all tabs — let the bar clip naturally rather than hiding
        for i, cell in enumerate(self._all_tab_cells):
            if self._tab_scroll_offset <= i < self._tab_scroll_offset + visible_count:
                cell.grid(row=0, column=i - self._tab_scroll_offset, sticky="ns", padx=0)
            else:
                cell.grid_remove()
        # Update chevron colours: visible when scrollable, hidden when at limit
        self._scroll_left_btn.configure(
            text_color=TEXT if self._tab_scroll_offset > 0 else DIM)
        self._scroll_right_btn.configure(
            text_color=TEXT if self._tab_scroll_offset < max_offset else DIM)

    def _on_mousewheel(self, event) -> None:
        """Scroll tab bar on mouse wheel (Windows <MouseWheel>, Linux <Button-4/5>)."""
        if event.num == 4 or (hasattr(event, "delta") and event.delta > 0):
            self._scroll_tabs(-1)
        elif event.num == 5 or (hasattr(event, "delta") and event.delta < 0):
            self._scroll_tabs(1)


# ─── Main App ─────────────────────────────────────────────────────────────────
class BigEdCC(BootManagerMixin, ctk.CTk):
    def __init__(self):
        super().__init__()

        from ui.theme import load_custom_fonts
        load_custom_fonts()  # After window exists, before UI build

        self.title("BigEd CC")
        self.geometry("1050x960")
        self.minsize(800, 720)
        self.configure(fg_color=BG)

        # Restore saved window geometry
        self._geometry_file = Path(HERE) / "data" / "window_geometry.json"
        self._saved_geo = None
        try:
            if self._geometry_file.exists():
                geo = json.loads(self._geometry_file.read_text())
                saved_x, saved_y = geo['x'], geo['y']
                saved_w, saved_h = geo['w'], geo['h']
                screen_w = self.winfo_screenwidth()
                screen_h = self.winfo_screenheight()
                # Only restore if window would be visible on screen
                if (saved_x + saved_w > 0 and saved_x < screen_w
                        and saved_y + saved_h > 0 and saved_y < screen_h):
                    self.geometry(f"{saved_w}x{saved_h}+{saved_x}+{saved_y}")
                    self._saved_geo = geo
        except Exception:
            pass

        # Load agent name theme + custom names
        global _active_theme, _custom_names
        _active_theme = _load_theme_preference()
        _custom_names = _load_custom_names()

        self._alive = True  # cleared on close; guards _safe_after() timer callbacks

        self._net_prev    = None
        self._net_time    = None
        self._ollama_up   = None   # None = unknown, True/False after first check
        self._ollama_restart_count = 0  # cap auto-restarts to 3
        self._system_running           = False
        self._system_intentional_stop  = False
        self._last_keepalive = 0.0  # epoch time of last keepalive ping
        self._sidebar_visible = _load_settings().get("sidebar_visible", True)
        # Activity sparkline: per-agent rolling history (last 10 samples @ 1s each)
        self._agent_activity = {}  # role -> deque of booleans (True=BUSY)
        # Cached agent row widgets — prevents flicker from destroy/recreate cycle
        self._agent_rows = {}  # role -> {frame, dot, name, spark, status, recover, task}
        self._ever_seen_roles = set()  # dynamic — agents appear as they register
        self._model_perf_labels = {}  # model -> {tps, calls, avg_ms} label widgets
        # Staged boot progress
        self._boot_active = False
        self._boot_widgets = []   # [{frame, dot, label, status}] per stage
        self._boot_abort = threading.Event()
        # Stats color hysteresis — require 2 consecutive samples above/below threshold
        self._hw_prev_colors = {"cpu": DIM, "ram": DIM, "gpu": DIM}
        self._hw_prev_values = {"cpu": 0.0, "ram": 0.0, "gpu": 0.0}
        psutil.cpu_percent(interval=None)  # prime the cpu sampler

        self._set_icon()
        self._build_ui()
        self._bind_shortcuts()

        # ── Apply display preferences after UI is built ───────────────────
        _disp_prefs = _load_settings()
        if _disp_prefs.get("always_on_top", False):
            self.attributes("-topmost", True)
        if _disp_prefs.get("start_maximized", False) or (
                self._saved_geo and self._saved_geo.get("maximized", False)):
            self.state("zoomed")
        if not self._sidebar_visible:
            self._sidebar.grid_remove()
            self._sidebar_btn.configure(text=">")
        if _disp_prefs.get("compact_mode", False):
            self._header.configure(height=44)
            self._sidebar.configure(width=130)

        self._current_log_agent = "Dr. Ders"  # show Dr. Ders log during boot
        self._safe_after(100, self._refresh_status)
        self._schedule_refresh()
        self._schedule_hw()
        self._schedule_ollama_watch()
        threading.Thread(target=self._check_for_updates, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # v0.45: SSE for reactive updates (falls back to polling if dashboard unavailable)
        try:
            from ui.sse_client import create_tk_sse_bridge
            self._sse = create_tk_sse_bridge(self)
            self._sse.on("status", self._handle_sse_status)
            self._sse.on("connected", lambda d: self.after(0, lambda: setattr(self, '_sse_active', True)))
            self._sse.on("disconnected", lambda d: self.after(0, lambda: setattr(self, '_sse_active', False)))
            self._sse_active = False
            self._sse.start()
        except Exception as e:
            import sys as _sys
            print(f"[WARN] SSE client failed, using polling: {e}", file=_sys.stderr)
            self._sse = None
            self._sse_active = False

        # First-run walkthrough — show after UI is fully built
        if _should_show_walkthrough():
            self._safe_after(500, lambda: WalkthroughDialog(self))

        # Auto-start fleet on launch — skip if first-run walkthrough is pending
        if not _should_show_walkthrough():
            self._safe_after(1000, self._start_system)

    _CLOSE_PREFS_FILE = DATA_DIR / "close_preferences.json"

    def _load_close_prefs(self):
        try:
            if self._CLOSE_PREFS_FILE.exists():
                return json.loads(self._CLOSE_PREFS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_close_prefs(self, prefs):
        try:
            self._CLOSE_PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._CLOSE_PREFS_FILE.write_text(json.dumps(prefs), encoding="utf-8")
        except Exception:
            pass

    def _shutdown_gui(self):
        """Stop all timers and background threads before destroy."""
        # Save window geometry (gated by remember_position preference)
        try:
            if _load_settings().get("remember_position", True):
                self._geometry_file.parent.mkdir(parents=True, exist_ok=True)
                self._geometry_file.write_text(json.dumps({
                    "w": self.winfo_width(), "h": self.winfo_height(),
                    "x": self.winfo_x(), "y": self.winfo_y(),
                    "maximized": self.state() == "zoomed",
                }))
        except Exception:
            pass

        # Signal all timers to stop
        self._boot_active = False
        self._system_running = False

        # Stop SSE client
        if getattr(self, '_sse', None):
            try:
                self._sse.stop()
            except Exception:
                pass

        # Close modules (DAL connections, etc.)
        for mod in getattr(self, "_modules", {}).values():
            try:
                mod.on_close()
            except Exception:
                pass

    def _do_stop_and_close(self):
        """Graceful close: save task queue, unload models, kill agents, exit.

        Runs blocking work on a background thread to keep the UI responsive.
        """
        self._shutdown_gui()

        # Show a shutdown overlay so the window stays responsive
        self._shutdown_overlay = ctk.CTkFrame(self, fg_color="#1a1a1a")
        self._shutdown_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        ctk.CTkLabel(self._shutdown_overlay, text="Shutting down fleet...",
                     font=FONT_TITLE, text_color=GOLD).place(relx=0.5, rely=0.4, anchor="center")
        self._shutdown_status = ctk.CTkLabel(
            self._shutdown_overlay, text="Saving tasks...",
            font=FONT_SM, text_color=DIM)
        self._shutdown_status.place(relx=0.5, rely=0.48, anchor="center")
        self.update_idletasks()

        def _bg_shutdown():
            # 1. Save task queue
            try:
                self._safe_after(0, lambda: self._shutdown_status.configure(text="Saving task queue..."))
                _graceful_save_tasks()
            except Exception:
                pass

            # 2. Unload all models to free VRAM
            try:
                self._safe_after(0, lambda: self._shutdown_status.configure(text="Unloading models..."))
                _unload_all_ollama_models()
            except Exception:
                pass

            # 3. Kill fleet processes
            try:
                self._safe_after(0, lambda: self._shutdown_status.configure(text="Stopping fleet processes..."))
                from ui.boot import _kill_fleet_processes
                _kill_fleet_processes()
            except Exception:
                pass

            # 4. Brief pause + zombie sweep
            try:
                time.sleep(0.5)
                _zombie_sweep()
            except Exception:
                pass

            # Destroy on main thread
            self._safe_after(0, self.destroy)

        t = threading.Thread(target=_bg_shutdown, daemon=True)
        t.start()

        # Safety net: if background thread hangs, force-close after 8 seconds
        def _force_close():
            if self.winfo_exists():
                self.destroy()
        self.after(8000, _force_close)

    def _do_just_close(self):
        """Quick close: keep fleet running in background."""
        self._shutdown_gui()
        self.destroy()

    def _safe_after(self, ms, func):
        """Schedule callback only if window is still alive."""
        try:
            if self.winfo_exists():
                self.after(ms, func)
        except Exception:
            pass

    def _on_close(self):
        """Smart close dialog with remember-choice + countdown."""
        self._alive = False
        prefs = self._load_close_prefs()
        remembered = prefs.get("action")  # "stop" or "keep" or None

        # If user previously chose "remember", auto-execute with 5s countdown
        if remembered:
            self._show_countdown_close(remembered)
            return

        # First time or reset — show full dialog
        self._show_close_dialog()

    def _show_countdown_close(self, action):
        """Auto-close with 5s countdown — user can click to change preferences."""
        dlg = ctk.CTkToplevel(self)
        dlg.title("Closing BigEd CC")
        dlg.geometry("340x140")
        dlg.resizable(False, False)
        dlg.configure(fg_color=BG2)
        dlg.grab_set()
        dlg.lift()
        dlg.attributes("-topmost", True)

        action_text = "Stopping fleet + closing" if action == "stop" else "Closing (fleet stays running)"
        countdown_var = ctk.StringVar(value=f"{action_text} in 5s...")

        ctk.CTkLabel(dlg, textvariable=countdown_var,
                     font=("RuneScape Bold 12", 12, "bold"), text_color=GOLD).pack(pady=(20, 8))

        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(side="bottom", fill="x", padx=16, pady=12)

        cancelled = [False]

        def _cancel():
            cancelled[0] = True
            dlg.destroy()
            self._show_close_dialog()  # show full dialog instead

        def _close_now():
            cancelled[0] = True
            dlg.destroy()
            if action == "stop":
                self._do_stop_and_close()
            else:
                self._do_just_close()

        ctk.CTkButton(btn_row, text="Close Now", width=100, height=28,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      command=_close_now).pack(side="right")
        ctk.CTkButton(btn_row, text="Change Preferences", width=140, height=28,
                      fg_color=BG3, hover_color=BG,
                      command=_cancel).pack(side="right", padx=(0, 8))

        # Countdown timer
        def _tick(remaining):
            if cancelled[0] or not dlg.winfo_exists():
                return
            if remaining <= 0:
                dlg.destroy()
                if action == "stop":
                    self._do_stop_and_close()
                else:
                    self._do_just_close()
                return
            countdown_var.set(f"{action_text} in {remaining}s...")
            self._safe_after(1000, lambda: _tick(remaining - 1))

        _tick(5)

    def _show_close_dialog(self):
        """Full close dialog with remember checkbox."""
        dlg = ctk.CTkToplevel(self)
        dlg.title("Close BigEd CC")
        dlg.geometry("400x200")
        dlg.resizable(False, False)
        dlg.configure(fg_color=BG2)
        dlg.grab_set()
        dlg.lift()

        ctk.CTkLabel(dlg, text="How should BigEd CC close?",
                     font=("RuneScape Bold 12", 13, "bold"), text_color=GOLD).pack(pady=(16, 4))
        ctk.CTkLabel(dlg, text="Stop & Exit gives agents a moment to wrap up.\n"
                     "Keep Running leaves the fleet working in the background.",
                     font=("RuneScape Plain 11", 10), text_color=DIM).pack(pady=(0, 8))

        # Remember checkbox
        remember_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(dlg, text="Remember my choice (5s countdown next time)",
                        variable=remember_var, font=("RuneScape Plain 11", 10),
                        text_color=TEXT, fg_color=BG3, hover_color=BG,
                        checkmark_color=GREEN).pack(pady=(0, 8))

        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(side="bottom", fill="x", padx=16, pady=12)

        def _stop_exit():
            if remember_var.get():
                self._save_close_prefs({"action": "stop"})
            dlg.destroy()
            self._do_stop_and_close()

        def _keep_running():
            if remember_var.get():
                self._save_close_prefs({"action": "keep"})
            dlg.destroy()
            self._do_just_close()

        def _cancel():
            dlg.destroy()

        ctk.CTkButton(btn_row, text="Stop & Exit", width=100, height=32,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      command=_stop_exit).pack(side="right")
        ctk.CTkButton(btn_row, text="Keep Running", width=110, height=32,
                      fg_color=BG3, hover_color=BG,
                      command=_keep_running).pack(side="right", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Cancel", width=70, height=32,
                      fg_color=BG3, hover_color=BG,
                      command=dlg.destroy).pack(side="right", padx=(0, 8))

    # ── Icon ──────────────────────────────────────────────────────────────────
    def _set_icon(self):
        ico = HERE / "brick.ico"
        if ico.exists():
            try:
                self.iconbitmap(str(ico))
            except Exception:
                pass

    def _load_banner(self):
        """Load app icon for header display. Tries icon_1024.png first, falls back to brick.ico."""
        for name in ["icon_1024.png", "brick.ico"]:
            path = HERE / name
            if path.exists():
                try:
                    img = Image.open(path)
                    return ctk.CTkImage(light_image=img, dark_image=img, size=(42, 42))
                except Exception:
                    continue
        return None

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0)   # sidebar
        self.grid_columnconfigure(1, weight=1)   # main content
        self.grid_rowconfigure(1, weight=1)      # tabs fill vertical space

        self._build_header()   # row 0 (full width — header + stats merged)
        self._build_sidebar()  # row 1 (col 0)
        self._build_tabs()     # row 1 (col 1) — all content lives in tabs
        self._build_taskbar()  # row 2 (full width)

    # ── Header ────────────────────────────────────────────────────────────────
    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color=BG3, height=HEADER_HEIGHT, corner_radius=0)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(3, weight=1)
        self._header = hdr

        # ── Logo ──────────────────────────────────────────────────────
        banner = self._load_banner()
        if banner:
            ctk.CTkLabel(hdr, image=banner, text="").grid(
                row=0, column=0, padx=(10, 2), pady=6)
        else:
            ctk.CTkLabel(hdr, text="B", font=FONT_TITLE, text_color=GOLD).grid(
                row=0, column=0, padx=(10, 2), pady=6)

        self._sidebar_btn = ctk.CTkButton(
            hdr, text="≡", font=FONT_TITLE, width=40, height=40,
            fg_color="transparent", hover_color=BG2, text_color=TEXT,
            corner_radius=4, command=self._toggle_sidebar
        )
        self._sidebar_btn.grid(row=0, column=1, padx=(0, 6), pady=6)

        # Title — single line with dim version
        title_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        title_frame.grid(row=0, column=2, padx=(0, 8), pady=6, sticky="w")
        ctk.CTkLabel(title_frame, text="BigEd CC",
                     font=FONT_TITLE, text_color=ACCENT).pack(side="left")
        ctk.CTkLabel(title_frame, text=f"  {_get_version()}",
                     font=FONT_XS, text_color=DIM).pack(side="left", pady=(6, 0))

        # ── System stats (inline, no container — let them breathe) ────
        stats_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        stats_frame.grid(row=0, column=3, sticky="w", padx=(4, 0), pady=8)
        kw = dict(font=FONT_STAT, text_color=DIM)
        self._stat_cpu = ctk.CTkLabel(stats_frame, text="CPU —", **kw)
        self._stat_ram = ctk.CTkLabel(stats_frame, text="RAM —", **kw)
        self._stat_gpu = ctk.CTkLabel(stats_frame, text="GPU —", **kw)
        self._stat_net = ctk.CTkLabel(stats_frame, text="NET —", **kw)
        self._stat_cpu.pack(side="left", padx=(0, 10))
        self._stat_ram.pack(side="left", padx=(0, 10))
        self._stat_gpu.pack(side="left", padx=(0, 10))
        self._stat_net.pack(side="left", padx=(0, 10))

        # ── Right side: Dr. Ders + status + badges ────────────────────
        right_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        right_frame.grid(row=0, column=4, sticky="e", padx=(0, 10), pady=6)

        self._dr_ders_hdr = ctk.CTkLabel(
            right_frame, text="Dr.Ders —", font=FONT_XS, text_color=DIM)
        self._dr_ders_hdr.pack(side="left", padx=(0, 4))

        self._status_pills = ctk.CTkLabel(
            right_frame, text="● loading...", font=FONT_STAT, text_color=DIM)
        self._status_pills.pack(side="left", padx=(0, 6))

        self._action_badge = ctk.CTkLabel(
            right_frame, text="", font=("RuneScape Bold 12", 9, "bold"),
            text_color=BG, fg_color=ORANGE,
            corner_radius=10, width=0, cursor="hand2")
        self._action_badge.pack(side="left", padx=(0, 4))
        self._action_badge.bind("<Button-1>", lambda e: self._navigate_to_comm())

        self._update_badge = ctk.CTkButton(
            right_frame, text="", font=("RuneScape Bold 12", 9, "bold"),
            text_color=TEXT, fg_color="transparent",
            hover_color=BG3, corner_radius=10, width=0,
            command=self._launch_auto_update)
        self._update_badge.pack(side="left", padx=(0, 4))

        # Offline / Air-Gap mode badge
        mode = _fleet_mode()
        badge_text = ""
        badge_fg = "transparent"
        if mode == "air_gap":
            badge_text = " AIR-GAP "
            badge_fg = RED
        elif mode == "offline":
            badge_text = " OFFLINE "
            badge_fg = ORANGE
        self._mode_badge = ctk.CTkLabel(
            right_frame, text=badge_text, font=("RuneScape Bold 12", 9, "bold"),
            text_color=BG if badge_text else TEXT,
            fg_color=badge_fg, corner_radius=10, width=0)
        self._mode_badge.pack(side="left")

    # ── Sidebar ───────────────────────────────────────────────────────────────
    def _toggle_sidebar(self):
        if self._sidebar_visible:
            self._sidebar.grid_remove()
            self._sidebar_btn.configure(text=">")
            self._sidebar_visible = False
        else:
            self._sidebar.grid()
            self._sidebar_btn.configure(text="≡")
            self._sidebar_visible = True

    def _build_sidebar(self):
        self._sidebar = ctk.CTkScrollableFrame(self, fg_color=BG2, width=155, corner_radius=0)
        self._sidebar.grid(row=1, column=0, sticky="nsew")
        sb = self._sidebar

        # ── Collapsible section + button helpers ──────────────────────────────
        def section(label, default_open=True):
            state = {"open": default_open, "widgets": []}
            def toggle():
                state["open"] = not state["open"]
                hdr.configure(text=f"  {'▾' if state['open'] else '▸'}  {label}")
                if state["open"]:
                    prev = hdr
                    for w in state["widgets"]:
                        w.pack(fill="x", padx=10, pady=2, after=prev)
                        prev = w
                else:
                    for w in state["widgets"]:
                        w.pack_forget()
            hdr = ctk.CTkButton(
                sb, text=f"  {'▾' if default_open else '▸'}  {label}",
                font=("RuneScape Bold 12", 10, "bold"),
                fg_color="transparent", hover_color=BG3,
                text_color=DIM, anchor="w", height=26, corner_radius=0,
                command=toggle,
            )
            hdr.pack(fill="x", padx=0, pady=(8, 0))
            return state

        def btn(s, label, cmd, color=BG3, hover=None, tip=None):
            b = ctk.CTkButton(
                sb, text=label, font=FONT_SM, height=28,
                fg_color=color, hover_color=hover or BG,
                text_color=TEXT, anchor="w", corner_radius=4, command=cmd,
            )
            b.pack(fill="x", padx=10, pady=2)
            if not s["open"]:
                b.pack_forget()
            s["widgets"].append(b)
            if tip:
                Tooltip(b, tip)
            return b

        # ── FLEET ─────────────────────────────────────────────────────────────
        s = section("FLEET")
        self._btn_system_toggle = btn(s, "▶  Start", self._toggle_system,
                                      "#1e3a1e", "#2a4a2a",
                                      tip="Start Ollama + all fleet workers (or stop everything)")
        btn(s, "↻  Status",      self._check_status,
            tip="Refresh agent status, show Ollama models and fleet log")
        # Dashboard — prominent button (larger, distinct color)
        self._btn_dashboard = ctk.CTkButton(
            sb, text="📊  Dashboard", font=("RuneScape Bold 12", 13, "bold"), height=36,
            fg_color="#1a3a5a", hover_color="#254565",
            text_color="#7ec8e3", anchor="w", corner_radius=4,
            command=self._open_dashboard,
        )
        self._btn_dashboard.pack(fill="x", padx=10, pady=(4, 2))
        if not s["open"]:
            self._btn_dashboard.pack_forget()
        s["widgets"].append(self._btn_dashboard)
        Tooltip(self._btn_dashboard, "Open the Fleet Dashboard in your browser (localhost:5555)")
        if _fleet_mode() == "air_gap":
            self._btn_dashboard.configure(state="disabled", text="📊 Dashboard (air-gap)")

        # ── RESEARCH ──────────────────────────────────────────────────────────
        s = section("RESEARCH")
        btn(s, "🔎 Web Search",    self._open_search_dialog,
            tip="Dispatch a web search task to the researcher worker")
        btn(s, "📊 Results",       self._show_results,
            tip="View autoresearch training experiment results")
        # Claude research assist — checked = fleet uses Claude (settings model) for
        # deep analysis decisions before handing back to local LLM
        self._claude_research_var = ctk.BooleanVar(
            value=self._get_complex_provider() == "claude")
        self._claude_research_cb  = ctk.CTkCheckBox(
            sb,
            text="Claude research",
            variable=self._claude_research_var,
            font=FONT_SM,
            text_color=TEXT,
            fg_color=ACCENT,
            hover_color=ACCENT_H,
            checkmark_color=TEXT,
            command=self._toggle_claude_research,
        )
        self._claude_research_cb.pack(fill="x", padx=18, pady=(4, 2))
        if not s["open"]:
            self._claude_research_cb.pack_forget()
        s["widgets"].append(self._claude_research_cb)

        # ── IDLE MODE ─────────────────────────────────────────────────────────
        if DEV_MODE:
            s = section("IDLE MODE", default_open=False)
            self._idle_enabled = False
            self._btn_idle_toggle = btn(s, "✅ Enable Idle", self._toggle_idle,
                                        "#1e2e1e", "#2a3e2a",
                                        tip="Allow workers to run background curriculum tasks when idle")

        # ── SETTINGS (single entry point) ──────────────────────────────────
        s = section("CONFIG")
        btn(s, "⚙  Settings",       self._open_settings,
            tip="Open the unified settings panel")
        if DEV_MODE:
            btn(s, "📋 Setup Walkthrough", lambda: WalkthroughDialog(self),
                tip="Re-run the first-time setup walkthrough")
            btn(s, "🐛 Report Issue", self._open_report_issue,
                tip="Generate a debug report and export for issue submission")

        # ── CONSOLES ─────────────────────────────────────────────────────────
        s = section("CONSOLES", default_open=False)
        _mode = _fleet_mode()
        _api_disabled = _mode in ("offline", "air_gap")
        _has_claude = _quick_key_check("ANTHROPIC_API_KEY")
        _has_gemini = _quick_key_check("GEMINI_API_KEY")

        if _api_disabled:
            _c_text, _c_tip, _c_state = "🤖 Claude (offline)", "Disabled — offline mode", "disabled"
            _g_text, _g_tip, _g_state = "✦  Gemini (offline)", "Disabled — offline mode", "disabled"
        elif not _has_claude:
            _c_text = "🤖 Claude (no key)"
            _c_tip = "Set ANTHROPIC_API_KEY in ~/.secrets or Key Manager to enable"
            _c_state = "disabled"
        else:
            _c_text, _c_tip, _c_state = "🤖 Claude Console", "Open an interactive Claude API chat with fleet dispatch support", "normal"

        if not _api_disabled and not _has_gemini:
            _g_text = "✦  Gemini (no key)"
            _g_tip = "Set GEMINI_API_KEY in ~/.secrets or Key Manager to enable"
            _g_state = "disabled"
        elif not _api_disabled:
            _g_text, _g_tip, _g_state = "✦  Gemini Console", "Open an interactive Gemini chat with fleet dispatch support", "normal"

        self._btn_claude_console = btn(s, _c_text, self._open_claude_console, "#1a1a2e", "#252540", tip=_c_tip)
        self._btn_gemini_console = btn(s, _g_text, self._open_gemini_console, "#1a2a1a", "#253525", tip=_g_tip)
        if _c_state == "disabled":
            self._btn_claude_console.configure(state="disabled")
        if _g_state == "disabled":
            self._btn_gemini_console.configure(state="disabled")
        btn(s, "⚡ Local Console",  self._open_local_console, "#2a2010", "#3a3020",
            tip="Open an interactive Ollama chat — free, no API key needed")

        # ── BUILD ──────────────────────────────────────────────────────────────
        if DEV_MODE:
            s = section("BUILD", default_open=False)
            btn(s, "🔄 Run Update",        self._launch_auto_update, "#1a3a1a", "#2a4a2a",
                tip="Run Updater.exe in auto mode and relaunch BigEd CC")
            btn(s, "▶  Launch Big Edge Compute Command", self._run_fleet_control,  "#1a2a10", "#2a3a18",
                tip="Launch the compiled Big Edge Compute Command from dist/")
            btn(s, "🔨 Rebuild All",       self._rebuild_all,        "#2a1a10", "#3a2a18",
                tip="Recompile the app via PyInstaller (build.bat)")
            btn(s, "💾 USB Media Creator", self._launch_usb_media,   "#1a1a2a", "#2a2a3a",
                tip="Create a portable USB installer for offline deployment")

        # ── LOGS ──────────────────────────────────────────────────────────────
        s = section("LOGS")
        agents = ["supervisor", "hw_supervisor", "researcher", "coder",
                  "security", "sales", "analyst", "archivist", "onboarding",
                  "implementation", "planner", "legal", "account_manager"]
        # Display-friendly names (hw_supervisor → Dr. Ders)
        _LOG_DISPLAY = {"hw_supervisor": "Dr. Ders"}
        _LOG_REVERSE = {"Dr. Ders": "hw_supervisor"}
        display_agents = [_LOG_DISPLAY.get(a, a) for a in agents]
        if DEV_MODE:
            agents.insert(0, "all")
        default_log = "all" if DEV_MODE else "supervisor"
        self._log_agent_var = ctk.StringVar(value=default_log)
        self._log_reverse = _LOG_REVERSE  # store for _refresh_log lookups
        menu = ctk.CTkOptionMenu(
            sb, values=display_agents, variable=self._log_agent_var,
            font=FONT_SM, fg_color=BG3, button_color=ACCENT,
            button_hover_color=ACCENT_H, height=28, command=self._switch_log,
        )
        menu.pack(fill="x", padx=10, pady=4)
        s["widgets"].append(menu)

        # ── Developer mode indicator ─────────────────────────────────────────
        if DEV_MODE:
            dev_label = ctk.CTkLabel(sb, text="🔧 Developer Mode", font=("RuneScape Plain 11", 8), text_color=DIM)
            dev_label.pack(side="bottom", pady=4)

    # ── Main area ─────────────────────────────────────────────────────────────
    # ── Tabs (primary content area) ──────────────────────────────────────────
    def _build_tabs(self):
        self._db_init()

        tabs = CustomTabBar(self)
        tabs.grid(row=1, column=1, sticky="nsew", padx=0, pady=0)
        self._tabs = tabs

        # Lazy-load infrastructure: deferred tabs build on first view
        self._lazy_tabs = {}
        self._built_tabs = set()

        tab_cfg = load_tab_cfg()

        # Always-on core tabs (built immediately — visible at startup)
        tabs.add("Command Center")
        self._build_tab_cc(tabs.tab("Command Center"))

        # Fleet — deferred until first click
        tabs.add("Fleet")
        self._lazy_tabs["Fleet"] = lambda p: self._build_tab_agents(p)

        # Fleet Comm — deferred until first click
        tabs.add("Fleet Comm")
        self._lazy_tabs["Fleet Comm"] = lambda p: self._build_tab_comm(p)

        # Load modular tabs via module system
        self._modules = {}
        try:
            from modules import load_modules, _load_manifest
            self._modules = load_modules(self, tab_cfg)
        except Exception as _mod_err:
            import sys
            print(f"[WARN] Module system failed to load: {_mod_err}", file=sys.stderr)
            import traceback; traceback.print_exc(file=sys.stderr)
            self._safe_after(1000, lambda e=str(_mod_err): self._log_output(f"\u26a0 Module load error: {e}"))

        for name, mod in self._modules.items():
            try:
                label = getattr(mod, "LABEL", name.title())
                deprecated = False
                try:
                    manifest = _load_manifest()
                    meta = manifest.get(name, {})
                    deprecated = meta.get("deprecated", False)
                except Exception:
                    pass
                tabs.add(label)
                # Defer module tab builds until first view
                self._lazy_tabs[label] = self._make_module_builder(mod, label, deprecated,
                                                                    meta if deprecated else {})
            except Exception as e:
                import sys
                print(f"[WARN] Module '{name}' failed to register tab: {e}", file=sys.stderr)
                self._safe_after(500, lambda n=name, err=str(e):
                    self._log_output(f"\u26a0 Module '{n}' failed: {err}"))

        # All tabs registered — enable scroll system and show initial tab
        tabs._tabs_ready = True
        tabs._scroll_tabs(0)  # initial layout with all tabs present
        tabs.set("Command Center")

    def _make_module_builder(self, mod, label, deprecated, meta):
        """Return a callable that builds a module tab on first view."""
        def _builder(tab_frame):
            try:
                if deprecated:
                    banner = ctk.CTkFrame(tab_frame, fg_color="#3a2a00", corner_radius=4)
                    banner.pack(fill="x", padx=4, pady=(4, 0))
                    since = meta.get("deprecated_since", "")
                    sunset = meta.get("sunset_version", "")
                    notes = meta.get("migration_notes", "")
                    msg = f"DEPRECATED (since {since})"
                    if sunset:
                        msg += f" - will be removed in {sunset}"
                    if notes:
                        msg += f"\n{notes}"
                    ctk.CTkLabel(banner, text=msg, font=FONT_SM,
                                 text_color=ORANGE, wraplength=600
                                 ).pack(padx=8, pady=4)
                    content = ctk.CTkFrame(tab_frame, fg_color="transparent")
                    content.pack(fill="both", expand=True)
                    mod.build_tab(content)
                else:
                    mod.build_tab(tab_frame)
            except Exception as e:
                import sys
                print(f"[WARN] Module '{label}' failed to build tab: {e}", file=sys.stderr)
                # Show error in the tab itself
                err_frame = ctk.CTkFrame(tab_frame, fg_color="transparent")
                err_frame.pack(fill="both", expand=True)
                ctk.CTkLabel(err_frame, text=f"\u26a0 Module '{label}' failed to load",
                             font=FONT_BOLD, text_color=ORANGE).pack(pady=(40, 8))
                ctk.CTkLabel(err_frame, text=str(e),
                             font=FONT_SM, text_color=DIM, wraplength=500).pack(pady=4)
                ctk.CTkButton(err_frame, text="Retry", font=FONT_SM, width=100,
                              fg_color=BG3, hover_color=BG2,
                              command=lambda: self._retry_module_build(label, mod, tab_frame)
                              ).pack(pady=8)
        return _builder

    def _retry_module_build(self, label, mod, tab_frame):
        """Clear a failed module tab and retry its build."""
        for w in tab_frame.winfo_children():
            w.destroy()
        try:
            mod.build_tab(tab_frame)
            self._built_tabs.add(label)
        except Exception as e:
            ctk.CTkLabel(tab_frame, text=f"\u26a0 Retry failed: {e}",
                         font=FONT_SM, text_color=RED).pack(pady=20)

    # ── Tab: Command Center (default) ────────────────────────────────────────
    def _build_tab_cc(self, parent):
        """Main view: agents + ollama status (left), log + I/O output (right)."""
        parent.grid_columnconfigure(0, weight=2)
        parent.grid_columnconfigure(1, weight=5)
        parent.grid_rowconfigure(0, weight=1)

        # ── Left column: Ollama + Agents ─────────────────────────────────────
        left = ctk.CTkFrame(parent, fg_color=BG, corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)
        left.grid_rowconfigure(2, weight=0)

        # Ollama status bar
        ollama_frame = ctk.CTkFrame(left, fg_color=BG2, height=28, corner_radius=6)
        ollama_frame.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ollama_frame.grid_propagate(False)
        ollama_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(ollama_frame, text="OLLAMA",
                     font=FONT_XS, text_color=DIM,
                     anchor="w").grid(row=0, column=0, padx=(8, 4), pady=4)

        self._ollama_dot = ctk.CTkLabel(
            ollama_frame, text="●", font=FONT_SM, text_color=DIM)
        self._ollama_dot.grid(row=0, column=1, sticky="w", padx=(0, 3))

        self._ollama_lbl = ctk.CTkLabel(
            ollama_frame, text="checking...", font=FONT_XS,
            text_color=DIM, anchor="w")
        self._ollama_lbl.grid(row=0, column=2, sticky="w")

        # Quick model switch dropdown — shows default model, populates from Ollama
        default_model = "qwen3:8b"
        try:
            cfg = load_model_cfg()
            default_model = cfg.get("local", "qwen3:8b")
        except Exception:
            pass
        self._model_switch_var = ctk.StringVar(value=default_model)
        self._model_switch = ctk.CTkOptionMenu(
            ollama_frame, variable=self._model_switch_var,
            values=[default_model],
            font=FONT_XS, width=110, height=18,
            fg_color=BG3, button_color=BG2, dropdown_fg_color=BG2,
            command=self._quick_model_switch,
        )
        self._model_switch.grid(row=0, column=3, padx=(3, 2))
        # Populate dropdown with installed models in background
        self._safe_after(2000, self._populate_model_dropdown)

        # Strategy presets
        self._strategy_var = ctk.StringVar(value="balanced")
        self._strategy_menu = ctk.CTkOptionMenu(
            ollama_frame, variable=self._strategy_var,
            values=["performance", "balanced", "training", "eco"],
            font=("Consolas", 8), width=80, height=18,
            fg_color=BG3, button_color=BG2, dropdown_fg_color=BG2,
            command=self._apply_strategy,
        )
        self._strategy_menu.grid(row=0, column=4, padx=(2, 2))
        Tooltip(self._strategy_menu,
                "Strategy presets:\n"
                "  performance: 8b GPU, max workers\n"
                "  balanced: 8b GPU, standard workers\n"
                "  training: 4b GPU + autoresearch\n"
                "  eco: 0.6b, min workers, low power")

        ctk.CTkButton(
            ollama_frame, text="↺", width=20, height=18,
            font=("RuneScape Plain 11", 9), fg_color=BG3, hover_color=BG,
            command=self._start_ollama,
        ).grid(row=0, column=5, padx=(2, 6))

        # Agents panel
        agents_frame = ctk.CTkFrame(left, fg_color=BG2, corner_radius=6)
        agents_frame.grid(row=1, column=0, sticky="nsew")
        agents_frame.grid_columnconfigure(0, weight=1)
        agents_frame.grid_rowconfigure(1, weight=1)

        ag_hdr = ctk.CTkFrame(agents_frame, fg_color="transparent")
        ag_hdr.grid(row=0, column=0, sticky="ew")
        ag_hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(ag_hdr, text="AGENTS", font=("RuneScape Bold 12", 9, "bold"), text_color=GOLD).grid(row=0, column=0, padx=8, pady=(4, 2), sticky="w")
        
        self._sup_status_lbl = ctk.CTkLabel(ag_hdr, text="Task Sup: —", font=("Consolas", 9, "bold"), text_color=DIM)
        self._sup_status_lbl.grid(row=0, column=1, padx=8, pady=(4, 2), sticky="e")

        self._hw_sup_status_lbl = ctk.CTkLabel(ag_hdr, text="Dr. Ders: —", font=("Consolas", 9, "bold"), text_color=DIM)
        self._hw_sup_status_lbl.grid(row=0, column=2, padx=8, pady=(4, 2), sticky="e")

        self._agents_frame_inner = ctk.CTkFrame(agents_frame, fg_color=BG2)
        self._agents_frame_inner.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))

        # Model Performance panel (below agents)
        self._build_model_perf_panel(left)

        # ── Actions panel (HITL + advisories) ────────────────────────────────
        actions_frame = ctk.CTkFrame(left, fg_color=BG2, corner_radius=6)
        actions_frame.grid(row=3, column=0, sticky="sew", pady=(4, 0))
        actions_frame.grid_columnconfigure(0, weight=1)
        actions_frame.grid_rowconfigure(1, weight=1)

        act_hdr = ctk.CTkFrame(actions_frame, fg_color="transparent")
        act_hdr.grid(row=0, column=0, sticky="ew")
        act_hdr.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(act_hdr, text="ACTIONS",
                     font=("RuneScape Bold 12", 9, "bold"), text_color=GOLD
                     ).grid(row=0, column=0, padx=(8, 2), pady=(4, 2), sticky="w")
        ctk.CTkLabel(act_hdr, text="(R to refresh)",
                     font=("Consolas", 8), text_color=DIM
                     ).grid(row=0, column=1, padx=(0, 4), pady=(4, 2), sticky="w")
        self._actions_count_lbl = ctk.CTkLabel(
            act_hdr, text="", font=("Consolas", 9), text_color=DIM)
        self._actions_count_lbl.grid(row=0, column=2, padx=8, pady=(4, 2), sticky="e")

        self._actions_scroll = ctk.CTkScrollableFrame(
            actions_frame, fg_color=BG2, corner_radius=0, height=180)
        self._actions_scroll.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self._actions_scroll.grid_columnconfigure(0, weight=1)

        self._action_cards = []
        self._actions_empty_lbl = ctk.CTkLabel(
            self._actions_scroll, text="No pending actions",
            font=FONT_SM, text_color=DIM)
        self._actions_empty_lbl.pack(pady=12)

        # ── Right column: Log + Task Output ──────────────────────────────────
        right = ctk.CTkFrame(parent, fg_color=BG, corner_radius=0)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=3)
        right.grid_rowconfigure(1, weight=2)

        # Log panel
        log_frame = ctk.CTkFrame(right, fg_color=BG2, corner_radius=6)
        log_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
        log_frame.grid_rowconfigure(1, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        self._log_label = ctk.CTkLabel(
            log_frame, text="LOG — all", font=("RuneScape Bold 12", 9, "bold"),
            text_color=GOLD, anchor="w")
        self._log_label.grid(row=0, column=0, padx=8, pady=(4, 2), sticky="w")

        self._log_text = ctk.CTkTextbox(
            log_frame, font=("Consolas", 10), fg_color=BG2,
            text_color="#c8c8c8", wrap="word", corner_radius=0)
        self._log_text.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))

        # Task output panel
        out_frame = ctk.CTkFrame(right, fg_color=BG2, corner_radius=6)
        out_frame.grid(row=1, column=0, sticky="nsew")
        out_frame.grid_rowconfigure(1, weight=1)
        out_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(out_frame, text="OUTPUT",
                     font=("RuneScape Bold 12", 9, "bold"), text_color=GOLD,
                     anchor="w").grid(row=0, column=0, padx=8, pady=(4, 2), sticky="w")

        self._output_text = ctk.CTkTextbox(
            out_frame, font=("Consolas", 10), fg_color=BG2,
            text_color="#c8c8c8", wrap="word", corner_radius=0)
        self._output_text.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))

        copy_btn = ctk.CTkButton(out_frame, text="\u2398", width=28, height=24,
                                  font=("RuneScape Plain 11", 10), fg_color=BG3, hover_color=BG2,
                                  command=self._copy_output)
        copy_btn.place(relx=1.0, x=-4, y=4, anchor="ne")

    # ── Tab 1: Agents ─────────────────────────────────────────────────────────
    def _build_tab_agents(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        # Header row
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 2))
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="Fleet workers — internal team & customer instances",
                     font=FONT_SM, text_color=DIM).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text="＋ Add Instance", font=FONT_SM, height=26,
                      width=110, fg_color=BG3, hover_color=BG,
                      command=self._agents_add_dialog
                      ).grid(row=0, column=2, sticky="e")

        # ── Task counter cards row ────────────────────────────────────────────
        counter_frame = ctk.CTkFrame(parent, fg_color=BG, corner_radius=0)
        counter_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(4, 4))
        counter_frame.grid_columnconfigure((0, 1, 2, 3, 4, 5, 6), weight=1)

        counters = [
            ("TOTAL", "#4fc3f7", "total"),
            ("IDLE",  "#66bb6a", "idle"),
            ("BUSY",  "#ff9800", "busy"),
            ("PENDING", "#ffd54f", "pending"),
            ("DONE",  DIM, "done"),
            ("WAITING", ORANGE, "waiting"),
            ("MODELS", "#00bcd4", "models"),
        ]
        self._task_counters = {}
        for i, (label, color, key) in enumerate(counters):
            card = ctk.CTkFrame(counter_frame, fg_color=BG2, corner_radius=6, height=60)
            card.grid(row=0, column=i, padx=3, pady=2, sticky="nsew")
            card.grid_propagate(False)
            ctk.CTkLabel(card, text=label, font=("RuneScape Plain 11", 9),
                         text_color=DIM).place(x=10, y=6)
            val_lbl = ctk.CTkLabel(card, text="0", font=("RuneScape Bold 12", 20, "bold"),
                                   text_color=color)
            val_lbl.place(x=10, y=24)
            self._task_counters[key] = val_lbl

        # ── Agent grid (scrollable cards) ─────────────────────────────────────
        self._agent_grid_frame = ctk.CTkScrollableFrame(
            parent, fg_color=BG, corner_radius=0)
        self._agent_grid_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=4)
        self._agent_grid_frame.grid_columnconfigure((0, 1, 2), weight=1)

        self._agent_cards = {}   # name -> dict of card widgets
        self._agents_tab_cache = {}  # kept for DB-based instance list

        # ── Disabled agents section ─────────────────────────────────────────
        self._disabled_frame = ctk.CTkFrame(parent, fg_color=BG, corner_radius=0)
        self._disabled_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 4))
        self._disabled_frame.grid_columnconfigure(0, weight=1)

        # Summary bar
        self._agent_summary = ctk.CTkLabel(
            self._disabled_frame, text="",
            font=("Consolas", 10), text_color=DIM, anchor="w")
        self._agent_summary.grid(row=0, column=0, sticky="w", padx=4, pady=(4, 0))

        # Toggle button
        self._disabled_toggle = ctk.CTkButton(
            self._disabled_frame, text="\u25b6 Disabled (0)", font=("Consolas", 10),
            height=24, width=140, fg_color=BG2, hover_color=BG3,
            text_color=DIM, command=self._toggle_disabled_section)
        self._disabled_toggle.grid(row=1, column=0, sticky="w", padx=4, pady=2)
        Tooltip(self._disabled_toggle,
                "Disabled agents are excluded from fleet boot.\n"
                "To remove permanently: delete from fleet.toml\n"
                "[fleet] disabled_agents list and remove the\n"
                "affinity entry from [affinity] section.")

        # Collapsed grid for disabled agents
        self._disabled_grid = ctk.CTkFrame(self._disabled_frame, fg_color=BG, corner_radius=0)
        self._disabled_grid.grid_columnconfigure((0, 1, 2), weight=1)
        # Start collapsed
        self._disabled_expanded = False
        self._disabled_cards = {}

        self._agents_tab_refresh()

    def _agents_tab_refresh(self):
        """Refresh the Agents tab grid — merges live STATUS.md agents with DB instances."""
        status = parse_status()
        agents = status.get("agents", [])
        tasks = status.get("tasks", {})

        def _fetch(con):
            rows = con.execute("SELECT name, role, type, customer, notes FROM agents").fetchall()
            return [dict(r) for r in rows]

        def _render(stored):
            stored = stored or []
            seen = {a["name"] for a in agents}
            all_agents = list(agents) + [a for a in stored if a["name"] not in seen]
            # Filter out supervisors and legacy ghost agents from the grid
            _EXCLUDE = {"supervisor", "hw_supervisor", "dr_ders", "coder"}
            all_agents = [a for a in all_agents if a.get("name") not in _EXCLUDE
                          and a.get("role") != "supervisor"]

            # Read disabled agents from fleet.toml config
            disabled_agents = set()
            try:
                import tomllib
                toml_path = FLEET_DIR / "fleet.toml"
                if toml_path.exists():
                    with open(toml_path, "rb") as f:
                        full_cfg = tomllib.load(f)
                    disabled_agents = set(full_cfg.get("fleet", {}).get("disabled_agents", []))
            except Exception:
                pass

            # Split into active and disabled lists
            active_agents = [a for a in all_agents if a.get("name") not in disabled_agents]
            disabled_list = [a for a in all_agents if a.get("name") in disabled_agents]
            # Add disabled agents that aren't currently registered (they won't be running)
            for d_name in disabled_agents:
                if not any(a.get("name") == d_name for a in disabled_list):
                    disabled_list.append({"name": d_name, "role": d_name, "status": "DISABLED"})

            # Query fleet.db for per-agent task counts + enhanced data
            # Wrapped in try/except so DB schema mismatches don't kill card rendering
            agent_task_counts = {}
            agent_tok_speed = {}
            agent_last_result = {}
            n_waiting_human = 0
            agent_waiting = set()
            agent_iq_score = {}      # name -> avg intelligence_score (float or None)
            agent_recent = {}        # name -> list of recent task dicts
            agent_expertise = {}     # name -> list of top skill dicts
            agent_tph = {}           # name -> tasks per hour (float)
            try:
                from data_access import FleetDB
                db_path = FLEET_DIR / "fleet.db"
                agent_task_counts = FleetDB.agent_task_counts(db_path)
                agent_tok_speed = FleetDB.agent_token_speeds(db_path)
                agent_names = [a.get("name", "") for a in all_agents]
                agent_last_result = FleetDB.agent_last_results(db_path, agent_names)
                n_waiting_human, agent_waiting = FleetDB.waiting_human_by_agent(db_path)
                try:
                    import sqlite3 as _sq
                    conn = _sq.connect(str(db_path), timeout=2)
                    conn.row_factory = _sq.Row
                    for row in conn.execute(
                        "SELECT assigned_to, AVG(intelligence_score) as avg_iq "
                        "FROM tasks WHERE intelligence_score IS NOT NULL "
                        "AND created_at > datetime('now', '-24 hours') "
                        "GROUP BY assigned_to"
                    ).fetchall():
                        if row["assigned_to"]:
                            agent_iq_score[row["assigned_to"]] = round(row["avg_iq"], 2)
                    conn.close()
                except Exception:
                    pass
                # Recent tasks + top skills per agent (for richer cards)
                try:
                    for aname in agent_names:
                        if not aname:
                            continue
                        agent_recent[aname] = FleetDB.agent_recent_tasks(db_path, aname, limit=3)
                        agent_expertise[aname] = FleetDB.agent_top_skills(db_path, aname, limit=3)
                except Exception:
                    pass
                # Tasks per hour: count / hours since earliest task (24h window)
                try:
                    import sqlite3 as _sq2
                    conn2 = _sq2.connect(str(db_path), timeout=2)
                    conn2.row_factory = _sq2.Row
                    for row in conn2.execute(
                        "SELECT assigned_to, COUNT(*) as cnt, "
                        "MIN(created_at) as first_at "
                        "FROM tasks WHERE created_at > datetime('now', '-24 hours') "
                        "GROUP BY assigned_to"
                    ).fetchall():
                        aname = row["assigned_to"]
                        if not aname:
                            continue
                        cnt = row["cnt"]
                        first_at = row["first_at"]
                        if first_at and cnt > 0:
                            try:
                                from datetime import datetime as _dt, timezone as _tz
                                first_dt = _dt.fromisoformat(
                                    first_at.replace("Z", "+00:00"))
                                if first_dt.tzinfo is None:
                                    first_dt = first_dt.replace(tzinfo=_tz.utc)
                                hours = (
                                    _dt.now(_tz.utc) - first_dt
                                ).total_seconds() / 3600.0
                                if hours > 0.01:
                                    agent_tph[aname] = round(cnt / hours, 1)
                            except Exception:
                                pass
                    conn2.close()
                except Exception:
                    pass
            except Exception as _enrich_err:
                import logging
                logging.getLogger("launcher").warning("Fleet DB enrichment failed: %s", _enrich_err)
            n_unique_models = 0      # unique loaded Ollama models

            # Count unique loaded Ollama models from hw_state.json
            try:
                hw_state_path = FLEET_DIR / "hw_state.json"
                if hw_state_path.exists():
                    import json as _json
                    hw = _json.loads(hw_state_path.read_text(encoding="utf-8", errors="replace"))
                    loaded = hw.get("models_loaded", [])
                    if isinstance(loaded, list):
                        n_unique_models = len(set(loaded))
                    elif isinstance(loaded, int):
                        n_unique_models = loaded
            except Exception:
                pass

            # Read default model from fleet.toml
            default_model = "qwen3:8b"
            try:
                cfg = load_model_cfg()
                default_model = cfg.get("local", "qwen3:8b")
            except Exception:
                pass

            # Update task counter cards (use active_agents for counters)
            n_total = len(active_agents)
            n_idle = sum(1 for a in active_agents if a.get("status") == "IDLE")
            n_busy = sum(1 for a in active_agents if a.get("status") == "BUSY")
            n_pending = tasks.get("Pending", 0)
            n_done = tasks.get("Done", 0)
            if hasattr(self, '_task_counters'):
                self._task_counters["total"].configure(text=str(n_total))
                self._task_counters["idle"].configure(text=str(n_idle))
                self._task_counters["busy"].configure(text=str(n_busy))
                self._task_counters["pending"].configure(text=str(n_pending))
                self._task_counters["done"].configure(text=str(n_done))
                self._task_counters["waiting"].configure(text=str(n_waiting_human))
                self._task_counters["models"].configure(text=str(n_unique_models))

            # Update agent cards grid
            active_names = set()
            for i, ag in enumerate(active_agents):
              try:
                row_idx = i // 3
                col_idx = i % 3
                name = ag.get("name", "?")
                role = ag.get("role", "?")
                st = ag.get("status", "OFFLINE")
                task = ag.get("task", "")
                active_names.add(name)

                # Compute display values
                display_name = themed_name(name)
                if len(display_name) > 18:
                    display_name = display_name[:16] + "\u2026"
                dot_color = GREEN if st in ("IDLE", "BUSY") else RED
                if st == "BUSY":
                    status_text, status_color = "ACTIVE", GREEN
                elif st == "IDLE":
                    status_text, status_color = "IDLE", "#4fc3f7"
                else:
                    status_text, status_color = "OFFLINE", RED
                name_color = TEXT if st != "OFFLINE" else DIM
                spark, spark_color = self._spark_text(name)
                count = agent_task_counts.get(name, 0)
                count_text = f"{count} task{'s' if count != 1 else ''}"

                # Enhanced data
                model_text = default_model
                tps = agent_tok_speed.get(name)
                tps_text = f"{tps} tok/s" if tps is not None else "\u2014 tok/s"
                last_result = self._humanize_result(agent_last_result.get(name, ""))
                is_waiting = name in agent_waiting
                iq = agent_iq_score.get(name)
                if iq is not None:
                    iq_text = f"IQ: {iq:.2f}"
                    iq_color = GREEN if iq >= 0.7 else ORANGE if iq >= 0.4 else RED
                else:
                    iq_text = "IQ: --"
                    iq_color = DIM

                # Tasks per hour
                tph = agent_tph.get(name)
                tph_text = f"{tph}/hr" if tph is not None else ""

                # Activity text: "Running: skill_name" when BUSY, "Idle" when IDLE
                task_raw = task if task and task != "\u2014" else ""
                if st == "BUSY" and task_raw:
                    # Extract skill name (strip leading verbs like "Running ")
                    skill_name = task_raw.split("(")[0].strip()
                    if len(skill_name) > 35:
                        skill_name = skill_name[:33] + "\u2026"
                    activity_text = f"Running: {skill_name}"
                    activity_color = GOLD
                elif st == "IDLE":
                    activity_text = "Idle"
                    activity_color = DIM
                else:
                    activity_text = ""
                    activity_color = DIM

                # Recent task outcome icons from agent_recent_tasks
                recent_icons = []
                try:
                    recents = agent_recent.get(name, [])
                    for rt in recents:
                        rst = rt.get("status", "")
                        if rst == "DONE":
                            recent_icons.append("\u2713")   # green check
                        elif rst == "FAILED":
                            recent_icons.append("\u2715")   # red cross
                        else:
                            recent_icons.append("\u2026")   # dim ellipsis
                except Exception:
                    recent_icons = []

                # Top expertise skills as compact tags
                expertise_text = ""
                try:
                    top_skills = agent_expertise.get(name, [])
                    if top_skills:
                        skill_names = [s.get("skill", "?") for s in top_skills]
                        expertise_text = "Skills: " + " \u2022 ".join(skill_names)
                        if len(expertise_text) > 45:
                            expertise_text = expertise_text[:43] + "\u2026"
                except Exception:
                    expertise_text = ""

                if name in self._agent_cards:
                    # Update existing card
                    c = self._agent_cards[name]
                    c["card"].grid(row=row_idx, column=col_idx, padx=4, pady=4, sticky="nsew")
                    c["dot"].configure(text_color=dot_color)
                    c["name_lbl"].configure(text=display_name, text_color=name_color)
                    c["status_lbl"].configure(text=status_text, text_color=status_color)
                    c["spark_lbl"].configure(text=spark, text_color=spark_color)
                    c["count_lbl"].configure(text=count_text)
                    c["edit_btn"].configure(command=lambda a=ag: self._agents_edit_dialog(a))
                    c["model_lbl"].configure(text=model_text)
                    c["tps_lbl"].configure(text=tps_text)
                    c["iq_lbl"].configure(text=iq_text, text_color=iq_color)
                    c["tph_lbl"].configure(text=tph_text)
                    # Activity row
                    c["activity_lbl"].configure(text=activity_text, text_color=activity_color)
                    # Recent results row — humanized text + outcome icons
                    _ri_suffix = ""
                    if recent_icons:
                        _ri_suffix = "  " + " ".join(recent_icons)
                    recent_display = (last_result + _ri_suffix) if last_result else _ri_suffix.strip()
                    if len(recent_display) > 50:
                        recent_display = recent_display[:48] + "\u2026"
                    c["recent_lbl"].configure(text=recent_display)
                    # Expertise row
                    c["expertise_lbl"].configure(text=expertise_text)
                    # WAITING_HUMAN indicator
                    if is_waiting:
                        c["waiting_badge"].configure(text="Needs Input")
                        c["card"].configure(border_color=ORANGE, border_width=2)
                    else:
                        c["waiting_badge"].configure(text="")
                        c["card"].configure(border_color=BG2, border_width=0)
                else:
                    # Create new agent card
                    self._agent_cards[name] = self._create_agent_card(
                        self._agent_grid_frame, row_idx, col_idx,
                        display_name, status_text, status_color, dot_color,
                        name_color, "", spark, spark_color,
                        count_text, ag, model_text, tps_text, last_result,
                        is_waiting, iq_text, iq_color,
                        tph_text=tph_text,
                        activity_text=activity_text, activity_color=activity_color,
                        recent_text=last_result, recent_icons=recent_icons,
                        expertise_text=expertise_text)
              except Exception as _card_err:
                import logging
                logging.getLogger("launcher").warning("Agent card render failed for %s: %s",
                                                       ag.get("name", "?"), _card_err)

            # Hide stale cards
            for key, c in self._agent_cards.items():
                if key not in active_names:
                    c["card"].grid_remove()

            # ── Disabled agents summary & grid ──────────────────────────────
            n_active = len(active_agents)
            n_disabled = len(disabled_agents)
            n_all = n_active + n_disabled
            if hasattr(self, '_agent_summary'):
                self._agent_summary.configure(
                    text=f"{n_active} active / {n_all} total")

            # Only show disabled agents section in dev mode
            if DEV_MODE:
                if hasattr(self, '_disabled_toggle'):
                    arrow = "\u25bc" if self._disabled_expanded else "\u25b6"
                    self._disabled_toggle.configure(text=f"{arrow} Disabled ({n_disabled})")

                # Render disabled agent cards
                if hasattr(self, '_disabled_grid'):
                    disabled_active_names = set()
                    for i, dag in enumerate(disabled_list):
                        d_name = dag.get("name", "?")
                        disabled_active_names.add(d_name)
                        r = i // 3
                        c = i % 3
                        if d_name in self._disabled_cards:
                            self._disabled_cards[d_name]["card"].grid(
                                row=r, column=c, padx=4, pady=2, sticky="nsew")
                        else:
                            dcard = ctk.CTkFrame(
                                self._disabled_grid, fg_color=BG2, corner_radius=8,
                                height=60, border_width=1, border_color="#333")
                            dcard.grid(row=r, column=c, padx=4, pady=2, sticky="nsew")
                            dcard.grid_propagate(False)
                            ctk.CTkLabel(dcard, text="\u25cf", font=("Consolas", 14),
                                         text_color="#555").place(x=8, y=8)
                            ctk.CTkLabel(dcard, text=d_name, font=("RuneScape Plain 12", 11),
                                         text_color="#666").place(x=26, y=6)
                            ctk.CTkLabel(dcard, text="DISABLED", font=("Consolas", 9),
                                         text_color="#555").place(
                                             relx=1.0, x=-8, y=8, anchor="ne")
                            enable_btn = ctk.CTkButton(
                                dcard, text="Enable", font=("Consolas", 9),
                                height=20, width=60,
                                fg_color="#2e7d32", hover_color="#388e3c",
                                command=lambda n=d_name: self._toggle_agent_disabled(
                                    n, enable=True))
                            enable_btn.place(relx=1.0, x=-8, y=34, anchor="ne")
                            self._disabled_cards[d_name] = {"card": dcard}
                    # Hide stale disabled cards
                    for key, dc in self._disabled_cards.items():
                        if key not in disabled_active_names:
                            dc["card"].grid_remove()
            else:
                # Production: hide disabled section entirely
                if hasattr(self, '_disabled_toggle'):
                    self._disabled_toggle.grid_remove()
                if hasattr(self, '_disabled_grid'):
                    self._disabled_grid.grid_remove()

        self._db_query_bg(_fetch, _render)

    def _toggle_disabled_section(self):
        """Toggle visibility of disabled agents grid."""
        self._disabled_expanded = not self._disabled_expanded
        if self._disabled_expanded:
            self._disabled_grid.grid(row=2, column=0, sticky="ew", padx=0, pady=2)
        else:
            self._disabled_grid.grid_remove()
        if hasattr(self, '_disabled_toggle'):
            n = len(self._disabled_cards)
            arrow = "\u25bc" if self._disabled_expanded else "\u25b6"
            self._disabled_toggle.configure(text=f"{arrow} Disabled ({n})")

    def _toggle_agent_disabled(self, agent_name, enable=False):
        """Toggle agent disabled state via dashboard API."""
        try:
            action = "enable" if enable else "disable"
            url = f"http://localhost:5555/api/fleet/worker/{agent_name}/{action}"
            req = urllib.request.Request(url, method="POST", data=b"{}",
                                        headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
            self._agents_tab_refresh()
        except Exception as e:
            import logging
            logging.getLogger("launcher").warning(
                "Toggle agent %s failed: %s", agent_name, e)

    @staticmethod
    def _humanize_result(result_json_str, task_type=""):
        """Convert raw JSON task result into human-readable summary."""
        if not result_json_str:
            return ""
        try:
            data = (json.loads(result_json_str)
                    if isinstance(result_json_str, str) else result_json_str)
        except Exception:
            return result_json_str[:50] if result_json_str else ""

        # Skill-specific parsing
        if isinstance(data, dict):
            # Evolution results
            if "evolved" in data:
                skill = data.get("skill_name", "skill")
                return f"Evolved {skill}" if data["evolved"] else f"No evolution for {skill}"
            # Review results
            if "passed" in data:
                if data["passed"]:
                    return "Review passed"
                errors = data.get("errors", [])
                return f"Review: {len(errors)} issue{'s' if len(errors) != 1 else ''}"
            # Search results
            if "results" in data and isinstance(data["results"], list):
                return f"Found {len(data['results'])} results"
            # Code output
            if "code" in data or "output" in data:
                return "Code generated"
            # Error
            if "error" in data:
                return f"Error: {str(data['error'])[:40]}"
            # Gaps/quality
            if "gaps" in data:
                gaps = data["gaps"]
                if isinstance(gaps, list):
                    return f"Found {len(gaps)} gap{'s' if len(gaps) != 1 else ''}"
                return "Quality check done"
            # Summary/status fallback (common in fleet results)
            for key in ("summary", "status", "message"):
                if key in data and isinstance(data[key], str):
                    val = data[key]
                    return val[:45] + "..." if len(val) > 45 else val
            # Generic dict — show first key
            first_key = next(iter(data), "")
            if first_key:
                first_val = data[first_key]
                if isinstance(first_val, bool):
                    return f"{first_key}: {'yes' if first_val else 'no'}"
                if isinstance(first_val, (int, float)):
                    return f"{first_key}: {first_val}"
                return f"{first_key}: {str(first_val)[:30]}"
        return str(data)[:50]

    def _create_agent_card(self, parent, row, col, display_name,
                           status_text, status_color, dot_color, name_color,
                           task_display, spark, spark_color, count_text, agent_data,
                           model_text="", tps_text="\u2014 tok/s", last_result="",
                           is_waiting=False, iq_text="IQ: --", iq_color=DIM,
                           tph_text="", activity_text="", activity_color=None,
                           recent_text="", recent_icons=None,
                           expertise_text=""):
        """Create a single agent dashboard card and return widget dict."""
        if activity_color is None:
            activity_color = DIM
        if recent_icons is None:
            recent_icons = []
        border_w = 2 if is_waiting else 0
        border_c = ORANGE if is_waiting else BG2
        card = ctk.CTkFrame(parent, fg_color=BG2, corner_radius=8, height=160,
                            border_width=border_w, border_color=border_c)
        card.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")
        card.grid_propagate(False)

        # Row 0 (y=6): Status dot + Agent name + status text
        dot = ctk.CTkLabel(card, text="\u25cf", font=FONT_SM,
                           text_color=dot_color)
        dot.place(x=8, y=8)

        name_lbl = ctk.CTkLabel(card, text=display_name,
                                font=FONT_BOLD, text_color=name_color)
        name_lbl.place(x=26, y=6)

        status_lbl = ctk.CTkLabel(card, text=status_text,
                                  font=FONT_XS, text_color=status_color)
        status_lbl.place(relx=1.0, x=-8, y=8, anchor="ne")

        # Row 1 (y=26): Model + IQ + tasks/hr + tok/s
        model_lbl = ctk.CTkLabel(card, text=model_text,
                                 font=FONT_STAT, text_color=DIM)
        model_lbl.place(x=26, y=26)

        iq_lbl = ctk.CTkLabel(card, text=iq_text,
                               font=FONT_STAT, text_color=iq_color)
        iq_lbl.place(x=110, y=26)

        tph_lbl = ctk.CTkLabel(card, text=tph_text,
                                font=FONT_STAT, text_color=DIM)
        tph_lbl.place(relx=1.0, x=-80, y=26, anchor="ne")

        tps_lbl = ctk.CTkLabel(card, text=tps_text,
                               font=FONT_STAT, text_color=DIM)
        tps_lbl.place(relx=1.0, x=-8, y=26, anchor="ne")

        # Row 2 (y=44): Activity — "Running: skill_name" or "Idle"
        activity_lbl = ctk.CTkLabel(card, text=activity_text,
                                    font=FONT_XS, text_color=activity_color)
        activity_lbl.place(x=26, y=44)

        # Row 3 (y=62): Last result — humanized text + recent outcome icons
        _recent_suffix = ""
        if recent_icons:
            _recent_suffix = "  " + " ".join(recent_icons)
        recent_display = (last_result + _recent_suffix) if last_result else _recent_suffix.strip()
        if len(recent_display) > 50:
            recent_display = recent_display[:48] + "\u2026"
        recent_lbl = ctk.CTkLabel(card, text=recent_display,
                                  font=FONT_XS, text_color=DIM)
        recent_lbl.place(x=26, y=62)

        # Row 4 (y=82): Expertise — top skills as tags
        expertise_lbl = ctk.CTkLabel(card, text=expertise_text,
                                     font=FONT_XS, text_color=DIM)
        expertise_lbl.place(x=26, y=82)

        # Row 5 (y=100): Activity sparkline + edit/disable buttons
        spark_lbl = ctk.CTkLabel(card, text=spark,
                                 font=FONT_STAT, text_color=spark_color)
        spark_lbl.place(x=8, y=100)

        edit_btn = ctk.CTkButton(
            card, text="\u270e", font=FONT_SM, width=24, height=18,
            fg_color=BG3, hover_color=BG,
            command=lambda a=agent_data: self._agents_edit_dialog(a))
        edit_btn.place(relx=1.0, x=-8, y=100, anchor="ne")

        # Disable button (next to edit)
        disable_btn = ctk.CTkButton(
            card, text="\u2715", font=FONT_SM, width=24, height=18,
            fg_color="#c62828", hover_color="#d32f2f",
            command=lambda n=display_name: self._toggle_agent_disabled(
                n.replace("\u2026", ""), enable=False))
        disable_btn.place(relx=1.0, x=-36, y=100, anchor="ne")

        # WAITING_HUMAN badge (below sparkline row)
        waiting_text = "Needs Input" if is_waiting else ""
        waiting_badge = ctk.CTkLabel(card, text=waiting_text,
                                     font=FONT_XS, text_color=ORANGE)
        waiting_badge.place(x=8, y=118)

        # Task count (bottom-right)
        count_lbl = ctk.CTkLabel(card, text=count_text,
                                 font=FONT_STAT, text_color=DIM)
        count_lbl.place(relx=1.0, x=-8, y=138, anchor="ne")

        return {
            "card": card, "dot": dot, "name_lbl": name_lbl,
            "status_lbl": status_lbl,
            "activity_lbl": activity_lbl, "recent_lbl": recent_lbl,
            "expertise_lbl": expertise_lbl,
            "spark_lbl": spark_lbl, "count_lbl": count_lbl,
            "edit_btn": edit_btn, "disable_btn": disable_btn,
            "model_lbl": model_lbl, "tph_lbl": tph_lbl,
            "tps_lbl": tps_lbl,
            "waiting_badge": waiting_badge, "iq_lbl": iq_lbl,
        }

    def _agents_add_dialog(self):
        self._agents_edit_dialog({})

    def _agents_edit_dialog(self, agent: dict):
        win = ctk.CTkToplevel(self)
        win.title("Agent Instance")
        win.geometry("380x280")
        win.resizable(False, False)
        win.configure(fg_color=BG)
        win.grab_set()

        fields = [
            ("Name",         agent.get("name", "")),
            ("Role",         agent.get("role", "")),
            ("Type",         agent.get("type", "Internal")),
            ("Customer",     agent.get("customer", "")),
            ("Notes",        agent.get("notes", "")),
        ]
        entries = {}
        for i, (lbl, val) in enumerate(fields):
            ctk.CTkLabel(win, text=lbl, font=FONT_SM, text_color=DIM,
                         anchor="w").grid(row=i, column=0, padx=(14, 6), pady=4, sticky="w")
            e = ctk.CTkEntry(win, font=FONT_SM, fg_color=BG2,
                             border_color="#444", text_color=TEXT)
            e.insert(0, val)
            e.grid(row=i, column=1, padx=(0, 14), pady=4, sticky="ew")
            entries[lbl] = e
        win.grid_columnconfigure(1, weight=1)

        def _save():
            new_name = entries["Name"].get().strip()
            con = self._db_conn()
            con.execute("DELETE FROM agents WHERE name=?", (agent.get("name", ""),))
            if new_name:
                con.execute(
                    "INSERT OR REPLACE INTO agents (name, role, type, customer, notes) VALUES (?,?,?,?,?)",
                    (new_name, entries["Role"].get(), entries["Type"].get(),
                     entries["Customer"].get(), entries["Notes"].get()))
            con.commit()
            con.close()
            self._agents_tab_refresh()
            win.destroy()

        ctk.CTkButton(win, text="Save", font=FONT_SM, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_H, command=_save
                      ).grid(row=len(fields), column=0, columnspan=2,
                             padx=14, pady=(10, 14), sticky="ew")

    # ── DB helpers ─────────────────────────────────────────────────────────────
    def _db_conn(self):
        DATA_DIR.mkdir(exist_ok=True)
        con = sqlite3.connect(str(DB_PATH))
        con.row_factory = sqlite3.Row
        return con

    def _db_query_bg(self, query_fn, callback):
        """Run query_fn(conn) in a background thread, call callback(results) on UI thread.
        query_fn receives a sqlite3.Connection and should return serializable data.
        callback receives the return value of query_fn (or None on error)."""
        def _run():
            try:
                con = self._db_conn()
                result = query_fn(con)
                con.close()
            except Exception:
                result = None
            self.after(0, lambda: callback(result))
        threading.Thread(target=_run, daemon=True).start()

    def _db_init(self):
        """Initialize launcher database using DAL (single source of truth)."""
        try:
            from data_access import DataAccess
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            dal = DataAccess(DB_PATH)
            dal.init_launcher_db()
        except Exception as e:
            print(f"[DB] init error: {e}")


    # ── Fleet Comm helpers ──────────────────────────────────────────────────
    @staticmethod
    def _fmt_ago(iso_ts):
        """Return relative timestamp like '3m ago' from an ISO timestamp string."""
        if not iso_ts:
            return ""
        try:
            from datetime import datetime, timezone
            ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            delta = (now - ts).total_seconds()
            if delta < 60:
                return f"{int(delta)}s ago"
            if delta < 3600:
                return f"{int(delta // 60)}m ago"
            if delta < 86400:
                return f"{int(delta // 3600)}h ago"
            return f"{int(delta // 86400)}d ago"
        except Exception:
            return ""

    # ── Fleet Comm tab ─────────────────────────────────────────────────────
    def _build_tab_comm(self, parent):
        """Fleet Comm: Agent Requests (dominant) + Manual Chat / VS Code guide."""
        parent.grid_rowconfigure(1, weight=1)   # request list (dominant — takes most space)
        parent.grid_rowconfigure(2, weight=0)   # manual chat (compact at bottom)
        parent.grid_columnconfigure(0, weight=1)

        # Persist provider selection across refreshes (default: Local — always available)
        if not hasattr(self, "_comm_provider_var"):
            self._comm_provider_var = ctk.StringVar(value="⚡ Local")

        # ── Agent Requests header (collapsible) ──────────────────────────
        req_header = ctk.CTkFrame(parent, fg_color=BG2, height=36, corner_radius=0)
        req_header.grid(row=0, column=0, sticky="ew")
        req_header.grid_propagate(False)

        self._comm_requests_collapsed = True
        self._comm_pin = False

        self._comm_req_label = ctk.CTkLabel(
            req_header, text="\u25b8 0 agent requests", font=FONT_BOLD,
            text_color=DIM, anchor="w", cursor="hand2")
        self._comm_req_label.pack(side="left", padx=12)
        self._comm_req_label.bind("<Button-1>", lambda e: self._toggle_comm_requests())
        self._comm_req_label.bind("<Enter>", lambda e: self._expand_comm_requests())

        # Pin button
        self._comm_pin_btn = ctk.CTkButton(
            req_header, text="\U0001f4cc", width=28, height=28, font=FONT_SM,
            fg_color="transparent", hover_color=BG3,
            command=self._toggle_comm_pin)
        self._comm_pin_btn.pack(side="right", padx=4)

        # Refresh button
        ctk.CTkButton(req_header, text="Refresh", width=60, height=24, font=FONT_SM,
                      fg_color=BG3, hover_color=BG,
                      command=self._refresh_comm).pack(side="right", padx=4, pady=4)

        # AI draft provider toggle
        ctk.CTkSegmentedButton(
            req_header,
            values=["\U0001f916 Claude", "\u2726 Gemini", "\u26a1 Local"],
            variable=self._comm_provider_var,
            font=FONT_SM,
            selected_color=ACCENT, selected_hover_color=ACCENT_H,
            unselected_color=BG3, unselected_hover_color=BG2,
            width=220, height=24,
        ).pack(side="right", padx=(0, 4), pady=4)

        # ── Request list (scrollable, starts collapsed) ──────────────────
        self._comm_request_frame = ctk.CTkScrollableFrame(
            parent, fg_color=BG, corner_radius=0, height=0)
        self._comm_request_frame.grid_columnconfigure(0, weight=1)
        # Start collapsed — do not grid yet
        self._comm_request_cards = []
        self._comm_cards = []  # track rendered card widgets (compat)

        # ── Manual Chat / VS Code Guide section ────────────────────────
        self._chat_container = ctk.CTkFrame(parent, fg_color=BG, corner_radius=0)
        self._chat_container.grid(row=2, column=0, sticky="nsew")

        # Chat header with mode selector
        chat_hdr = ctk.CTkFrame(self._chat_container, fg_color=BG2, height=36, corner_radius=0)
        chat_hdr.pack(fill="x")
        chat_hdr.pack_propagate(False)
        self._chat_mode_label = ctk.CTkLabel(chat_hdr, text="Local Console", font=FONT_BOLD,
                     text_color=GOLD, anchor="w")
        self._chat_mode_label.pack(side="left", padx=12)

        self._manual_model_var = ctk.StringVar(value="Local (Ollama)")
        ctk.CTkOptionMenu(
            chat_hdr, variable=self._manual_model_var,
            values=["Local (Ollama)", "Claude Code (VS Code)", "Gemini CLI (VS Code)"],
            font=FONT_SM, width=180, height=26,
            fg_color=BG3,
            command=self._on_manual_model_change,
        ).pack(side="right", padx=8, pady=5)

        # ── Local console (shown when Local selected) ────────────────
        self._local_chat_frame = ctk.CTkFrame(self._chat_container, fg_color="transparent")
        self._local_chat_frame.pack(fill="both", expand=True)

        self._manual_chat_display = ctk.CTkTextbox(
            self._local_chat_frame, font=FONT_STAT, fg_color=BG2,
            text_color=TEXT, corner_radius=4)
        self._manual_chat_display.pack(fill="both", expand=True, padx=8, pady=4)
        self._manual_chat_display.configure(state="disabled")

        input_row = ctk.CTkFrame(self._local_chat_frame, fg_color="transparent")
        input_row.pack(fill="x", padx=8, pady=(0, 8))
        input_row.grid_columnconfigure(0, weight=1)

        self._manual_chat_entry = ctk.CTkEntry(
            input_row, font=FONT, fg_color=BG2,
            placeholder_text="Type a message or select an agent request above...")
        self._manual_chat_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._manual_chat_entry.bind("<Return>", self._on_chat_enter)

        ctk.CTkButton(
            input_row, text="Send", width=70, height=30, font=FONT_SM,
            fg_color=ACCENT, hover_color=ACCENT_H,
            command=self._send_manual_chat,
        ).grid(row=0, column=1)

        # Microphone button (voice input)
        self._mic_btn = ctk.CTkButton(
            input_row, text="\U0001f3a4", width=36, height=30, font=("Segoe UI", 14),
            fg_color=BG3, hover_color=BG2,
            command=self._voice_input)
        self._mic_btn.grid(row=0, column=2, padx=(4, 0))

        # ── VS Code guide (shown when Claude/Gemini selected) ────────
        self._vscode_guide_frame = ctk.CTkFrame(self._chat_container, fg_color="transparent")
        # NOT packed initially — shown when VS Code mode selected

        guide_inner = ctk.CTkFrame(self._vscode_guide_frame, fg_color=BG2, corner_radius=8)
        guide_inner.pack(fill="both", expand=True, padx=8, pady=8)

        ctk.CTkLabel(guide_inner, text="VS Code Manual Mode",
                     font=FONT_TITLE, text_color=GOLD).pack(pady=(16, 4))
        ctk.CTkLabel(guide_inner, text="Open VS Code to work with Claude or Gemini directly",
                     font=FONT_SM, text_color=DIM).pack(pady=(0, 12))

        # How it works
        guide_text = ctk.CTkTextbox(guide_inner, font=FONT_SM, fg_color=BG3,
                                     text_color=TEXT, corner_radius=4, height=180)
        guide_text.pack(fill="x", padx=16, pady=(0, 8))
        guide_text.insert("1.0",
            "How it works:\n"
            "  1. Type your task below and click 'Open VS Code'\n"
            "  2. BigEd writes task-briefing.md with fleet context\n"
            "  3. VS Code opens with the briefing file\n"
            "  4. Start Claude Code or Gemini CLI in the terminal\n\n"
            "Agent interaction:\n"
            "  - Fleet agents remain active while you work in VS Code\n"
            "  - Agent HITL requests appear above in Agent Requests\n"
            "  - Claude/Gemini read CLAUDE.md, fleet knowledge, MCP tools\n"
            "  - Results are visible to fleet agents via shared files\n\n"
            "Starting Claude Code:\n"
            "  Ctrl+Shift+P → 'Claude: Open'  (or run 'claude' in terminal)\n\n"
            "Starting Gemini CLI:\n"
            "  Open terminal → run 'gemini'\n"
            "  Toggle agent mode: /agent-mode (approval-gated ↔ autonomous)\n\n"
            "Terms of Service:\n"
            "  Prompts are sent to the selected provider (Anthropic/Google).\n"
            "  Do not include PHI, credentials, or secrets."
        )
        guide_text.configure(state="disabled")

        # VS Code launch row
        vscode_row = ctk.CTkFrame(guide_inner, fg_color="transparent")
        vscode_row.pack(fill="x", padx=16, pady=(0, 12))
        vscode_row.grid_columnconfigure(0, weight=1)

        self._vscode_task_entry = ctk.CTkEntry(
            vscode_row, font=FONT, fg_color=BG3,
            placeholder_text="Describe your task for VS Code session...")
        self._vscode_task_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._vscode_task_entry.bind("<Return>", lambda e: self._launch_vscode_from_guide())

        ctk.CTkButton(
            vscode_row, text="Open VS Code", width=120, height=30, font=FONT_SM,
            fg_color=ACCENT, hover_color=ACCENT_H,
            command=self._launch_vscode_from_guide,
        ).grid(row=0, column=1)

    def _toggle_comm_requests(self):
        """Toggle collapsible Agent Requests panel."""
        self._comm_requests_collapsed = not self._comm_requests_collapsed
        self._update_comm_request_view()

    def _expand_comm_requests(self):
        """Expand Agent Requests on hover (unless pinned open)."""
        if self._comm_requests_collapsed and not self._comm_pin:
            self._comm_requests_collapsed = False
            self._update_comm_request_view()

    def _toggle_comm_pin(self):
        """Pin/unpin the Agent Requests panel open."""
        self._comm_pin = not self._comm_pin
        color = GOLD if self._comm_pin else DIM
        self._comm_pin_btn.configure(text_color=color)

    def _update_comm_request_view(self):
        """Show/hide the collapsible request list and update arrow."""
        n = len(self._comm_request_cards)
        arrow = "\u25b8" if self._comm_requests_collapsed else "\u25be"
        color = ORANGE if n else GREEN
        self._comm_req_label.configure(
            text=f"{arrow} {n} agent request{'s' if n != 1 else ''}",
            text_color=color)
        if self._comm_requests_collapsed:
            self._comm_request_frame.grid_remove()
        else:
            h = min(300, max(60, n * 60))
            self._comm_request_frame.configure(height=h)
            self._comm_request_frame.grid(row=1, column=0, sticky="ew")

    # ── Manual Chat helpers ──────────────────────────────────────────────

    def _launch_vscode_from_guide(self):
        """Launch VS Code session from the guide panel entry."""
        text = self._vscode_task_entry.get().strip()
        if not text:
            return
        model = self._manual_model_var.get()
        self._vscode_task_entry.delete(0, "end")
        # Reuse the existing launch flow
        self._manual_chat_display.configure(state="normal")
        self._manual_chat_display.insert("end", f"\nYou: {text}\n")
        self._manual_chat_display.configure(state="disabled")
        self._launch_oauth_session(model, text)

    def _on_manual_model_change(self, choice):
        """Swap between Local console and VS Code guide."""
        if "VS Code" in choice:
            # Show VS Code guide, hide local console
            self._local_chat_frame.pack_forget()
            self._vscode_guide_frame.pack(fill="both", expand=True)
            self._chat_mode_label.configure(text="VS Code Manual Mode")
        else:
            # Show local console, hide VS Code guide
            self._vscode_guide_frame.pack_forget()
            self._local_chat_frame.pack(fill="both", expand=True)
            self._chat_mode_label.configure(text="Local Console")
            return

        if "Claude" in choice:
            guidance = (
                "━━━ Claude Code (VS Code) ━━━\n"
                "How it works:\n"
                "  1. Type your task in the chat box and press Send\n"
                "  2. BigEd writes task-briefing.md with your request + fleet context\n"
                "  3. VS Code opens to the project with task-briefing.md visible\n"
                "  4. Claude Code starts with your task pre-loaded (if CLI available)\n\n"
                "Agent interaction:\n"
                "  • Fleet agents remain active — HITL requests flow to Fleet Comm\n"
                "  • Claude Code reads fleet knowledge, CLAUDE.md, and MCP tools\n"
                "  • Results from Claude Code are visible to fleet agents via shared files\n\n"
                "Terms of Service:\n"
                "  • Prompts sent to Anthropic — subject to Anthropic ToS\n"
                "  • Do not include PHI, credentials, or secrets in your prompt\n\n"
                "Type your task below and press Send to begin.\n"
            )
        else:
            guidance = (
                "━━━ Gemini CLI (VS Code) ━━━\n"
                "How it works:\n"
                "  1. Type your task in the chat box and press Send\n"
                "  2. BigEd writes task-briefing.md with your request + fleet context\n"
                "  3. VS Code opens to the project with task-briefing.md visible\n"
                "  4. Use Gemini CLI in the VS Code terminal: gemini\n\n"
                "Agent interaction:\n"
                "  • Fleet agents remain active — HITL requests flow to Fleet Comm\n"
                "  • Gemini reads the same project context as Claude Code\n"
                "  • Toggle agent mode: approval-gated writes (default) or un-gated\n"
                "    In Gemini CLI: /agent-mode to toggle autonomous writes\n\n"
                "Terms of Service:\n"
                "  • Prompts sent to Google — subject to Google ToS\n"
                "  • Do not include PHI, credentials, or secrets in your prompt\n\n"
                "Type your task below and press Send to begin.\n"
            )
        self._manual_chat_display.configure(state="normal")
        self._manual_chat_display.insert("end", f"\nSystem:\n{guidance}\n")
        self._manual_chat_display.configure(state="disabled")
        self._manual_chat_display.see("end")

    def _on_chat_enter(self, event):
        """Enter sends chat; Shift+Enter does nothing (single-line entry)."""
        if event.state & 0x1:  # Shift held
            return  # ignore — single-line entry, no newline needed
        self._send_manual_chat()
        return "break"

    def _send_manual_chat(self):
        """Send a message from the Manual Chat input."""
        text = self._manual_chat_entry.get().strip()
        if not text:
            return
        model = self._manual_model_var.get()
        self._manual_chat_entry.delete(0, "end")

        # Append user message to display
        self._manual_chat_display.configure(state="normal")
        self._manual_chat_display.insert("end", f"\nYou: {text}\n")
        self._manual_chat_display.configure(state="disabled")
        self._manual_chat_display.see("end")

        # If a HITL task is loaded, route the response back to the waiting agent
        hitl_id = getattr(self, '_active_hitl_task_id', None)
        hitl_agent = getattr(self, '_active_hitl_agent', None)
        if hitl_id is not None:
            self._send_human_response(hitl_id, text)
            self._active_hitl_task_id = None
            self._active_hitl_agent = None
            # Visual feedback: confirm HITL loop closure in chat display
            agent_label = hitl_agent or "agent"
            self._manual_chat_display.configure(state="normal")
            self._manual_chat_display.insert(
                "end",
                f"\n─── Response sent to {agent_label} (task #{hitl_id}) ───\n"
            )
            self._manual_chat_display.configure(state="disabled")
            self._manual_chat_display.see("end")
            # Refresh comm to update badge/card list after HITL close
            self._safe_after(500, self._refresh_comm)

        if "VS Code" in model:
            # If HITL is active, do NOT auto-close the HITL loop yet —
            # the operator will send the final response after reviewing VS Code output.
            # Restore the HITL context so it stays armed.
            if hitl_id is not None:
                self._active_hitl_task_id = hitl_id
                self._active_hitl_agent = hitl_agent
                self._manual_chat_display.configure(state="normal")
                self._manual_chat_display.delete("end-3l", "end")  # remove premature close msg
                self._manual_chat_display.insert(
                    "end",
                    f"\n─── Launching VS Code for analysis — HITL #{hitl_id} still active ───\n"
                    f"Send your final response after reviewing the VS Code output.\n"
                )
                self._manual_chat_display.configure(state="disabled")
                self._manual_chat_display.see("end")
            self._launch_oauth_session(model, text)
        else:
            threading.Thread(target=self._local_chat, args=(text,), daemon=True).start()

    def _local_chat(self, prompt):
        """Send prompt to local Ollama and stream response."""
        try:
            mcfg = load_model_cfg()
            host = mcfg.get("ollama_host", "http://localhost:11434")
            model = mcfg.get("local", "qwen3:8b")
            body = json.dumps({
                "model": model, "prompt": prompt,
                "stream": False, "options": {"num_gpu": 99},
            }).encode()
            req = urllib.request.Request(
                f"{host}/api/generate", data=body, method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.loads(r.read())
            response = resp.get("response", "No response")
            self._safe_after(0, lambda r=response: self._append_chat_response(r))
        except Exception as e:
            self._safe_after(0, lambda e=str(e): self._append_chat_response(f"Error: {e}"))

    def _append_chat_response(self, text):
        """Append an assistant response to the Manual Chat display."""
        self._manual_chat_display.configure(state="normal")
        self._manual_chat_display.insert("end", f"\nAssistant: {text}\n")
        self._manual_chat_display.configure(state="disabled")
        self._manual_chat_display.see("end")

    def _voice_input(self):
        """Capture voice and transcribe to chat input."""
        self._mic_btn.configure(text="\u23fa", fg_color="#5a2020")
        self._append_chat_response("[Listening... 5 seconds]")

        def _record():
            try:
                import sys as _sys
                if str(FLEET_DIR) not in _sys.path:
                    _sys.path.insert(0, str(FLEET_DIR))
                from skills.speech_to_text import run as stt_run
                config = {}
                try:
                    from config import load_config
                    config = load_config()
                except Exception:
                    pass

                import logging
                log = logging.getLogger("stt")
                result = stt_run({"action": "listen", "duration_secs": 5}, config, log)

                if "text" in result and result["text"]:
                    self._safe_after(0, lambda t=result["text"]: self._manual_chat_entry.insert(0, t))
                    self._safe_after(0, lambda: self._append_chat_response(
                        f"[Transcribed: {result.get('backend', '?')}]"))
                else:
                    self._safe_after(0, lambda: self._append_chat_response(
                        f"[STT: {result.get('error', 'no text')}]"))
            except Exception as e:
                self._safe_after(0, lambda e=str(e): self._append_chat_response(f"[Voice error: {e}]"))
            finally:
                self._safe_after(0, lambda: self._mic_btn.configure(text="\U0001f3a4", fg_color=BG3))

        threading.Thread(target=_record, daemon=True).start()

    def _launch_oauth_session(self, model, context):
        """Write context files and launch VS Code session (Claude Code or Gemini CLI)."""
        # Context preview — show files to be written before proceeding
        briefing = FLEET_DIR / "task-briefing.md"
        files_preview = [str(briefing)]

        # Check DITL mode for compliance rules file
        ditl_enabled = False
        try:
            import tomllib
            with open(FLEET_TOML, "rb") as f:
                toml_data = tomllib.load(f)
            ditl_enabled = toml_data.get("ditl", {}).get("enabled", False)
        except Exception:
            pass
        compliance_rule = FLEET_DIR.parent / ".claude" / "rules" / "compliance.md"
        if ditl_enabled:
            files_preview.append(str(compliance_rule))

        # If an active HITL task is loaded, note the response will also be sent back
        hitl_id = getattr(self, '_active_hitl_task_id', None)
        if hitl_id:
            files_preview.append(f"  → HITL task #{hitl_id} response will be sent on submit")
        file_list = "\n".join(f"  • {f}" for f in files_preview)
        confirm = _ctx_preview_confirm(self, model, file_list)
        if not confirm:
            return

        # ── Write rich task-briefing.md ──────────────────────────────────
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Gather recent agent activity for context
        recent = ""
        try:
            import sqlite3
            db_path = FLEET_DIR / "fleet.db"
            if db_path.exists():
                conn = sqlite3.connect(str(db_path), timeout=5)
                try:
                    rows = conn.execute(
                        "SELECT type, status, result_json FROM tasks "
                        "WHERE status='DONE' "
                        "AND created_at >= datetime('now', '-1 hour') "
                        "ORDER BY created_at DESC LIMIT 5"
                    ).fetchall()
                    if rows:
                        recent = "\n## Recent Agent Activity\n"
                        for r in rows:
                            recent += f"- {r[0]}: {r[1]}\n"
                finally:
                    conn.close()
        except Exception:
            pass

        fleet_status = "running" if self._system_running else "stopped"
        selected_model = self._manual_model_var.get()

        ditl_line = f"- DITL: {'enabled' if ditl_enabled else 'disabled'}"

        # DITL compliance: filter PHI from context sent to external providers
        if ditl_enabled:
            try:
                if str(FLEET_DIR) not in sys.path:
                    sys.path.insert(0, str(FLEET_DIR))
                from phi_deidentify import deidentify_text
                context = deidentify_text(context)
            except Exception:
                pass  # PHI filter not available — proceed with raw context

        # Check for MCP availability
        mcp_available = (FLEET_DIR.parent / ".mcp.json").exists()
        mcp_line = "- MCP: available (.mcp.json configured)" if mcp_available else "- MCP: not configured"

        content = (
            f"# Manual Chat Session Briefing\n"
            f"Generated: {ts} by BigEd CC\n\n"
            f"## User Request\n{context}\n\n"
            f"## System Context\n"
            f"- Fleet: {fleet_status}\n"
            f"- Model: {selected_model}\n"
            f"{ditl_line}\n"
            f"{mcp_line}\n"
            f"{recent}\n"
            f"## Efficiency Notes\n"
            f"- Use `cache_control: {{type: \"ephemeral\"}}` on stable prompt prefixes\n"
            f"- Use MCP tools for fleet DB access, file operations, and knowledge queries\n"
            f"- Batch related operations to reduce round-trips\n"
            f"- Read CLAUDE.md for project conventions before making changes\n\n"
            f"## Suggested Approach\n"
            f"Review the request above and use the fleet knowledge base for context.\n"
        )
        briefing.write_text(content, encoding="utf-8")

        # Write audit-results.md with recent fleet findings
        try:
            audit_results_path = FLEET_DIR / "audit-results.md"
            import sqlite3 as _sq
            _conn = _sq.connect(str(FLEET_DIR / "fleet.db"), timeout=5)
            _conn.row_factory = _sq.Row
            try:
                recent = _conn.execute(
                    "SELECT type, status, result_json, created_at, assigned_to FROM tasks "
                    "WHERE status IN ('DONE','FAILED') AND created_at >= datetime('now', '-24 hours') "
                    "ORDER BY created_at DESC LIMIT 20"
                ).fetchall()
                audit_md = f"# Audit Results\nGenerated: {ts} by BigEd CC API audit system\n\n"
                for r in recent:
                    preview = (r["result_json"] or "")[:300]
                    audit_md += f"### {r['type']} ({r['status']})\n"
                    audit_md += f"Agent: {r['assigned_to'] or '?'} | {r['created_at']}\n"
                    audit_md += f"```\n{preview}\n```\n\n"
                audit_results_path.write_text(audit_md, encoding="utf-8")
            finally:
                _conn.close()
        except Exception:
            pass

        # ── Write .claude/rules/compliance.md if DITL mode enabled ───────
        if ditl_enabled:
            compliance_rule.parent.mkdir(parents=True, exist_ok=True)
            compliance_rule.write_text(
                "---\n"
                "paths:\n"
                '  - "training-files/**/*.md"\n'
                '  - "training-files/**/*.pdf"\n'
                '  - "training-files/**/*.docx"\n'
                "---\n"
                "# Training file compliance rules\n\n"
                "- Every training file MUST contain: title, version, effective date,\n"
                "  review date, author, and learning objectives\n"
                '- Flag files where review date is in the past as "OVERDUE REVIEW"\n'
                '- Flag files missing any required metadata as "INCOMPLETE METADATA"\n'
                "- Check that all external links are formatted correctly\n"
                "- Verify assessment criteria match stated learning objectives\n"
                "- Output findings in this format:\n\n"
                "  ## [filename]\n"
                "  - **Status:** Compliant / Non-compliant / Needs review\n"
                "  - **Issues:** [list specific problems]\n"
                "  - **Suggested actions:** [concrete next steps]\n",
                encoding="utf-8",
            )

        # ── Both Claude and Gemini share VS Code as the launch environment ──
        import shutil
        code_exe = shutil.which("code")
        if not code_exe and sys.platform == "win32":
            for p in [
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "Code.exe",
                Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft VS Code" / "Code.exe",
                Path(os.environ.get("USERPROFILE", "")) / "AppData" / "Local" / "Programs" / "Microsoft VS Code" / "Code.exe",
            ]:
                if p.exists():
                    code_exe = str(p)
                    break
        if not code_exe and sys.platform == "darwin":
            for p in [
                Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
                Path.home() / "Applications" / "Visual Studio Code.app" / "Contents" / "Resources" / "app" / "bin" / "code",
            ]:
                if p.exists():
                    code_exe = str(p)
                    break
        if not code_exe and sys.platform == "linux":
            for p in [
                Path("/usr/bin/code"),
                Path("/usr/share/code/bin/code"),
                Path("/snap/bin/code"),
                Path.home() / ".local" / "bin" / "code",
            ]:
                if p.exists():
                    code_exe = str(p)
                    break

        if not code_exe:
            import webbrowser
            webbrowser.open("https://vscode.dev")
            self._append_chat_response(
                "VS Code not found locally. Opened vscode.dev in browser.\n"
                "Context written to task-briefing.md.")
            return

        try:
            # Open VS Code to project dir + auto-open the briefing file
            subprocess.Popen(
                [code_exe, str(FLEET_DIR.parent), "--goto", str(briefing)],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self._append_chat_response(f"Could not open VS Code: {e}")
            return

        if "Claude" in model:
            # Try to auto-start Claude Code CLI
            claude_exe = shutil.which("claude")
            if claude_exe:
                escaped = context.replace('"', '\\"')
                subprocess.Popen(
                    [claude_exe, "--print",
                     f"Read fleet/task-briefing.md and execute the task described in it. "
                     f"User request: {escaped}"],
                    cwd=str(FLEET_DIR.parent),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self._append_chat_response(
                    "VS Code opened with task-briefing.md.\n"
                    "Claude Code started with your task pre-loaded.\n"
                    "Fleet agents are listening — HITL requests will appear in Fleet Comm.")
            else:
                self._append_chat_response(
                    "VS Code opened with task-briefing.md.\n"
                    "Start Claude Code: Ctrl+Shift+P → 'Claude: Open'\n"
                    "Fleet agents are listening — HITL requests will appear in Fleet Comm.")
        elif "Gemini" in model:
            # Gemini uses the same VS Code environment
            gemini_exe = shutil.which("gemini")
            if gemini_exe:
                subprocess.Popen(
                    [gemini_exe],
                    cwd=str(FLEET_DIR.parent),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self._append_chat_response(
                    "VS Code opened with task-briefing.md.\n"
                    "Gemini CLI started in the project directory.\n"
                    "Toggle agent mode: /agent-mode (approval-gated ↔ autonomous)\n"
                    "Fleet agents are listening — HITL requests will appear in Fleet Comm.")
            else:
                self._append_chat_response(
                    "VS Code opened with task-briefing.md.\n"
                    "Start Gemini: open VS Code terminal → run 'gemini'\n"
                    "Toggle agent mode: /agent-mode (approval-gated ↔ autonomous)\n"
                    "Fleet agents are listening — HITL requests will appear in Fleet Comm.")

    # ── Fleet Comm — data refresh ────────────────────────────────────────

    def _refresh_comm(self):
        """Load WAITING_HUMAN tasks and security advisories into Fleet Comm."""
        # Guard: skip if Fleet Comm tab hasn't been built yet (lazy-load)
        if "Fleet Comm" not in getattr(self, '_built_tabs', set()):
            return
        def _fetch():
            from data_access import FleetDB
            waiting = FleetDB.waiting_human_tasks(FLEET_DIR / "fleet.db")
            for item in waiting:
                if not item.get("question"):
                    item["question"] = "(no question)"
            advisories = []
            try:
                if PENDING_DIR.exists():
                    for f in sorted(PENDING_DIR.glob("advisory_*.md"))[:10]:
                        try:
                            text = f.read_text(encoding="utf-8", errors="replace")
                            lines = text.splitlines()
                            title = lines[0].strip("# ").strip() if lines else f.name
                            summary = ""
                            counts = ""
                            in_analysis = False
                            for ln in lines:
                                if ln.startswith("**Findings:**"):
                                    counts = ln.replace("**Findings:**", "").strip()
                                elif ln.startswith("## Analysis"):
                                    in_analysis = True
                                elif in_analysis and ln.strip() and not ln.startswith("##"):
                                    summary = ln.strip("- ").strip()[:120]
                                    in_analysis = False
                                elif ln.startswith("## ") and in_analysis:
                                    in_analysis = False
                            json_path = f.with_suffix(".json")
                            if not counts and json_path.exists():
                                try:
                                    jdata = json.loads(json_path.read_text())
                                    c = jdata.get("counts", {})
                                    parts = []
                                    for sev in ("HIGH", "MEDIUM", "LOW"):
                                        if c.get(sev, 0):
                                            parts.append(f"{c[sev]} {sev}")
                                    counts = ", ".join(parts)
                                    if not summary:
                                        summary = (jdata.get("analysis", "") or "")[:120]
                                except Exception:
                                    pass
                            advisories.append({
                                "file": f.name, "title": title[:80],
                                "path": str(f), "counts": counts,
                                "summary": summary,
                            })
                        except Exception:
                            pass
            except Exception:
                pass
            return waiting, advisories

        def _render(data):
            waiting, advisories = data
            # Clear existing request cards
            cards = list(self._comm_request_cards)
            self._comm_request_cards.clear()
            self._comm_cards.clear()
            for w in cards:
                try:
                    w.destroy()
                except Exception:
                    pass

            total = len(waiting) + len(advisories)
            scroll = self._comm_request_frame

            # Update Fleet Comm tab badge with pending HITL count
            n = len(waiting)
            tab_text = "\U0001f4ac  Fleet Comm" + (f" ({n})" if n > 0 else "")
            if hasattr(self, '_tabs') and "Fleet Comm" in self._tabs._tab_buttons:
                self._tabs._tab_buttons["Fleet Comm"].configure(text=tab_text)

            # Notify via toast if new HITL requests appeared
            n_waiting = n
            if n_waiting > getattr(self, '_prev_hitl_count', 0):
                new_count = n_waiting - getattr(self, '_prev_hitl_count', 0)
                self._show_toast(f"{new_count} new agent request(s)", ORANGE, duration=5000)
            self._prev_hitl_count = n_waiting

            if not total:
                self._update_comm_request_view()
                return

            # Render WAITING_HUMAN cards
            for item in waiting:
                wrapper = ctk.CTkFrame(scroll, fg_color=ORANGE, corner_radius=6)
                wrapper.pack(fill="x", padx=4, pady=3)
                self._comm_request_cards.append(wrapper)
                self._comm_cards.append(wrapper)
                card = ctk.CTkFrame(wrapper, fg_color=BG2, corner_radius=6)
                card.pack(fill="both", expand=True, padx=(2, 0))

                top = ctk.CTkFrame(card, fg_color="transparent")
                top.pack(fill="x", padx=8, pady=(8, 0))
                hdr_left = ctk.CTkFrame(top, fg_color="transparent")
                hdr_left.pack(side="left")
                ctk.CTkLabel(hdr_left, text=item.get("type", "task"),
                             font=("RuneScape Bold 12", 10, "bold"), text_color=TEXT).pack(anchor="w")
                ctk.CTkLabel(hdr_left, text=item.get("assigned_to", "?"),
                             font=("RuneScape Plain 11", 8), text_color=DIM).pack(anchor="w")
                ago = self._fmt_ago(item.get("created_at"))
                if ago:
                    ctk.CTkLabel(top, text=ago,
                                 font=("RuneScape Plain 11", 8), text_color=DIM).pack(side="right")

                ctk.CTkLabel(card, text=item.get("question", ""),
                             font=FONT, text_color=TEXT, wraplength=600,
                             anchor="w", justify="left").pack(fill="x", padx=8, pady=(4, 0))

                reply_frame = ctk.CTkFrame(card, fg_color="transparent")
                reply_frame.pack(fill="x", padx=8, pady=(4, 8))
                reply_frame.grid_columnconfigure(0, weight=1)

                reply_var = ctk.StringVar()
                entry = ctk.CTkEntry(reply_frame, textvariable=reply_var,
                                     font=FONT_SM, fg_color=BG3, border_color=ACCENT,
                                     placeholder_text="Type your response or click \u2728 to AI-draft\u2026")
                entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

                tid = item["id"]
                question = item.get("question", "")
                agent_name = item.get("assigned_to", "agent")
                entry.bind("<Return>", lambda e, t=tid, v=reply_var: self._send_human_response(t, v.get()))

                draft_btn = ctk.CTkButton(
                    reply_frame, text="\u2728", width=32, height=28,
                    fg_color=BG3, hover_color=BG2,
                    font=("RuneScape Plain 12", 13), text_color=GOLD,
                )
                draft_btn.grid(row=0, column=1, padx=(0, 4))
                draft_btn.configure(
                    command=lambda q=question, ag=agent_name, en=entry, db=draft_btn:
                        self._draft_comm_response(q, ag, en, db))

                ctk.CTkButton(
                    reply_frame, text="Send", width=60, height=28,
                    fg_color=ACCENT, hover_color=ACCENT_H,
                    command=lambda t=tid, v=reply_var: self._send_human_response(t, v.get()),
                ).grid(row=0, column=2)

                # "Load to Chat" button: pre-fills Manual Chat and sets active HITL context
                task_type = item.get("type", "task")
                ctk.CTkButton(
                    card, text="↓ Load to Chat", width=110, height=22,
                    fg_color=BG3, hover_color=BG2, font=FONT_XS, text_color=DIM,
                    command=lambda t=tid, q=question, ag=agent_name, tt=task_type:
                        self._load_hitl_to_chat(t, q, ag, tt),
                ).pack(anchor="e", padx=8, pady=(0, 6))

            # Render security advisories
            for adv in advisories:
                card = ctk.CTkFrame(scroll, fg_color="#2a1a1a", corner_radius=6)
                card.pack(fill="x", padx=4, pady=3)
                self._comm_request_cards.append(card)
                self._comm_cards.append(card)

                top = ctk.CTkFrame(card, fg_color="transparent")
                top.pack(fill="x", padx=8, pady=(8, 0))
                ctk.CTkLabel(top, text=f"\U0001f512 {adv['title']}",
                             font=("RuneScape Bold 12", 10, "bold"), text_color=ORANGE).pack(side="left")
                ctk.CTkButton(
                    top, text="Approve", width=70, height=24,
                    fg_color=GREEN, hover_color="#388e3c",
                    font=FONT_SM, text_color="#ffffff",
                    command=lambda p=adv["path"]: self._approve_advisory(p),
                ).pack(side="right", padx=(4, 0))
                ctk.CTkButton(
                    top, text="Dismiss", width=70, height=24,
                    fg_color=BG3, hover_color=BG,
                    font=FONT_SM, text_color=DIM,
                    command=lambda p=adv["path"]: self._dismiss_advisory(p),
                ).pack(side="right")

                _counts = adv.get("counts", "")
                if _counts:
                    _sev_colors = {"HIGH": RED, "MEDIUM": ORANGE, "LOW": DIM}
                    counts_frame = ctk.CTkFrame(card, fg_color="transparent")
                    counts_frame.pack(fill="x", padx=12, pady=(4, 0), anchor="w")
                    for part in _counts.split(", "):
                        _color = DIM
                        for sev, col in _sev_colors.items():
                            if sev in part:
                                _color = col
                                break
                        ctk.CTkLabel(counts_frame, text=part.strip(),
                                     font=("Consolas", 9, "bold"), text_color=_color,
                                     ).pack(side="left", padx=(0, 8))

                _summary = adv.get("summary", "")
                if _summary:
                    ctk.CTkLabel(card, text=_summary, font=FONT_SM,
                                 text_color=DIM, wraplength=600,
                                 anchor="w", justify="left",
                                 ).pack(fill="x", padx=12, pady=(2, 8))

            # Update collapsible view after cards rendered
            self._update_comm_request_view()

        # Run async
        def _bg():
            data = _fetch()
            self._safe_after(0, lambda: _render(data))
        threading.Thread(target=_bg, daemon=True).start()

    def _send_human_response(self, task_id, response):
        """Send operator response to a WAITING_HUMAN task."""
        if not response.strip():
            return
        def _bg():
            try:
                from data_access import FleetDB
                ok = FleetDB.send_human_response(FLEET_DIR / "fleet.db", task_id, response)
                if ok:
                    self._safe_after(0, lambda: (
                        self._log_output(f"Response sent to task #{task_id}"),
                        self._refresh_comm()
                    ))
                else:
                    self._safe_after(0, lambda: self._log_output(f"Task #{task_id} not found"))
            except Exception as e:
                self._safe_after(0, lambda: self._log_output(f"Send error: {e}"))
        threading.Thread(target=_bg, daemon=True).start()

    def _load_hitl_to_chat(self, task_id: int, question: str,
                           agent_name: str, task_type: str) -> None:
        """Pre-fill Manual Chat with HITL request context and arm the HITL loop."""
        # Switch to Fleet Comm tab if not already there
        try:
            self._tabs.set("Fleet Comm")
        except Exception:
            pass
        # Build a concise context string for the entry field (truncated)
        entry_text = question[:200]
        try:
            self._manual_chat_entry.delete(0, "end")
            self._manual_chat_entry.insert(0, entry_text)
            self._manual_chat_entry.focus_set()
        except Exception:
            return
        # Store active HITL task ID so _send_manual_chat can close the loop
        self._active_hitl_task_id = task_id
        self._active_hitl_agent = agent_name
        # Visual cue in chat display — full context
        self._manual_chat_display.configure(state="normal")
        self._manual_chat_display.insert(
            "end",
            f"\n─── Loaded: {agent_name} Task #{task_id} ({task_type}) ───\n"
            f"{question}\n"
            f"─── Type your response below and press Send ───\n"
        )
        self._manual_chat_display.configure(state="disabled")
        self._manual_chat_display.see("end")

    # ── Fleet Comm — AI-assisted response drafting ───────────────────────────

    def _draft_comm_response(self, question: str, agent: str,
                             entry: ctk.CTkEntry, btn: ctk.CTkButton) -> None:
        """Route ✨ Draft to whichever AI provider the operator has selected."""
        provider = getattr(self, "_comm_provider_var", None)
        provider = provider.get() if provider else "⚡ Local"

        prompt = (
            f"You are BigEd — an AI fleet management system.\n"
            f"An autonomous agent named '{agent}' is waiting for operator input.\n\n"
            f"Agent's question:\n{question}\n\n"
            f"Draft a concise, actionable response the operator can send back. "
            f"1-3 sentences maximum. Output only the draft text — no preamble or sign-off."
        )

        btn.configure(text="…", state="disabled")

        def _on_result(text: str) -> None:
            self._safe_after(0, lambda: (
                entry.delete(0, "end"),
                entry.insert(0, text.strip()),
                btn.configure(text="✨", state="normal"),
            ))

        def _on_error(err: str) -> None:
            self._safe_after(0, lambda: (
                self._log_output(f"AI draft error ({provider}): {err}"),
                btn.configure(text="✨", state="normal"),
            ))

        if "Claude" in provider:
            threading.Thread(target=self._draft_via_claude,
                             args=(prompt, _on_result, _on_error), daemon=True).start()
        elif "Gemini" in provider:
            threading.Thread(target=self._draft_via_gemini,
                             args=(prompt, _on_result, _on_error), daemon=True).start()
        else:
            threading.Thread(target=self._draft_via_local,
                             args=(prompt, _on_result, _on_error), daemon=True).start()

    def _draft_via_claude(self, prompt: str, on_result, on_error) -> None:
        try:
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                try:
                    out, _ = self.wsl("echo $ANTHROPIC_API_KEY", capture=True)
                    key = out.strip()
                except Exception:
                    pass
            if not key or key.startswith("$"):
                on_error("ANTHROPIC_API_KEY not set — open Claude Console to configure it.")
                return
            import anthropic
            mcfg = load_model_cfg()
            model = mcfg.get("claude_model", "claude-haiku-4-5")
            client = anthropic.Anthropic(api_key=key)
            msg = client.messages.create(
                model=model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            on_result(msg.content[0].text)
        except Exception as e:
            on_error(str(e))

    def _draft_via_gemini(self, prompt: str, on_result, on_error) -> None:
        try:
            key = os.environ.get("GEMINI_API_KEY", "")
            if not key:
                try:
                    out, _ = self.wsl("echo $GEMINI_API_KEY", capture=True)
                    key = out.strip()
                except Exception:
                    pass
            if not key or key.startswith("$"):
                on_error("GEMINI_API_KEY not set — open Gemini Console to configure it.")
                return
            from google import genai
            mcfg = load_model_cfg()
            client = genai.Client(api_key=key)
            resp = client.models.generate_content(
                model=mcfg.get("gemini_model", "gemini-2.0-flash"),
                contents=prompt,
            )
            on_result(resp.text)
        except Exception as e:
            on_error(str(e))

    def _draft_via_local(self, prompt: str, on_result, on_error) -> None:
        try:
            mcfg = load_model_cfg()
            host  = mcfg.get("ollama_host", "http://localhost:11434")
            model = mcfg.get("local", "qwen3:8b")
            body = json.dumps({
                "model": model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": "5m",
            }).encode()
            req = urllib.request.Request(
                f"{host}/api/generate", data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
            on_result(data.get("response", "(empty response)"))
        except Exception as e:
            on_error(str(e))

    def _approve_advisory(self, path):
        """Approve a security advisory — dispatch security_apply."""
        try:
            adv_path = Path(path)
            if adv_path.exists():
                self._log_output(f"Dispatching security_apply for {adv_path.name}")
                payload = json.dumps({"advisory_file": adv_path.name})
                b64 = base64.b64encode(payload.encode()).decode()
                self._dispatch_raw("security_apply", payload, "security",
                                   None)
        except Exception as e:
            self._log_output(f"Approve error: {e}")

    def _dismiss_advisory(self, path):
        """Move advisory to dismissed/ subfolder."""
        try:
            adv_path = Path(path)
            if adv_path.exists():
                dismissed_dir = adv_path.parent / "dismissed"
                dismissed_dir.mkdir(exist_ok=True)
                adv_path.rename(dismissed_dir / adv_path.name)
                self._log_output(f"Dismissed {adv_path.name}")
                self._refresh_comm()
        except Exception as e:
            self._log_output(f"Dismiss error: {e}")

    # ── Task bar ──────────────────────────────────────────────────────────────
    def _build_taskbar(self):
        bar = ctk.CTkFrame(self, fg_color=BG3, corner_radius=0)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew")
        self._taskbar = bar  # v0.44: store ref for update banner row shift
        bar.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(bar, text="▶ Task:", font=FONT_SM,
                     text_color=DIM).grid(row=0, column=0, padx=(12, 6), pady=8,
                                          sticky="n")

        self._task_entry = ctk.CTkTextbox(
            bar, font=MONO, fg_color=BG, border_color="#444", border_width=1,
            text_color=TEXT, wrap="word", corner_radius=4, height=34)
        self._task_entry.grid(row=0, column=1, padx=4, pady=6, sticky="ew")
        # Enter dispatches, Shift+Enter inserts newline
        self._task_entry.bind("<Return>", self._on_task_enter)
        self._task_entry.bind("<KeyRelease>", self._auto_resize_task_entry)

        btn_col = ctk.CTkFrame(bar, fg_color="transparent")
        btn_col.grid(row=0, column=2, padx=(4, 8), pady=6, sticky="n")

        ctk.CTkButton(
            btn_col, text="Dispatch", font=FONT_SM, width=90, height=30,
            fg_color=ACCENT, hover_color=ACCENT_H,
            command=self._dispatch_task,
        ).pack(pady=(0, 2))

        self._task_status = ctk.CTkLabel(
            btn_col, text="", font=FONT_SM, text_color=DIM)
        self._task_status.pack()

        # P3 polish — Ctrl+K hint for command palette discoverability
        ctk.CTkLabel(bar, text="Ctrl+K  command palette", font=FONT_XS,
                     text_color=DIM).grid(row=1, column=1, sticky="e", padx=(0, 8), pady=(0, 4))

    def _on_task_enter(self, event):
        """Enter dispatches; Shift+Enter inserts newline."""
        if event.state & 0x1:  # Shift held
            return  # let default insert newline
        self._dispatch_task()
        return "break"  # prevent newline insertion

    def _auto_resize_task_entry(self, _event=None):
        """Grow/shrink the task textbox to fit content (1–4 lines)."""
        content = self._task_entry.get("1.0", "end-1c")
        line_count = max(1, min(4, content.count("\n") + 1))
        new_h = 20 + line_count * 18
        self._task_entry.configure(height=new_h)

    # ── Model Performance Panel ─────────────────────────────────────────────
    def _build_model_perf_panel(self, parent):
        """Build the MODEL PERFORMANCE panel showing tok/s per model."""
        frame = ctk.CTkFrame(parent, fg_color=BG2, corner_radius=6)
        frame.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        frame.grid_columnconfigure(0, weight=1)
        self._model_perf_frame = frame

        # Header
        ctk.CTkLabel(frame, text="\u26a1 MODEL PERFORMANCE",
                     font=("RuneScape Bold 12", 9, "bold"), text_color=GOLD,
                     anchor="w").grid(row=0, column=0, padx=8, pady=(4, 2),
                                      sticky="w", columnspan=5)

        # Column headers
        hdr_frame = ctk.CTkFrame(frame, fg_color="transparent")
        hdr_frame.grid(row=1, column=0, sticky="ew", padx=4)
        for col_idx, (hdr_text, width, anchor) in enumerate([
            ("Model", 100, "w"), ("tok/s", 55, "e"), ("IQ", 40, "e"),
            ("Calls", 45, "e"), ("Avg ms", 55, "e"),
        ]):
            ctk.CTkLabel(hdr_frame, text=hdr_text, font=("Consolas", 8),
                         text_color=DIM, anchor=anchor, width=width
                         ).grid(row=0, column=col_idx, padx=2, pady=(0, 1))

        # Data area — holds model rows
        self._model_perf_data_frame = ctk.CTkFrame(frame, fg_color="transparent")
        self._model_perf_data_frame.grid(row=2, column=0, sticky="ew", padx=4,
                                          pady=(0, 4))

        # Initial "No data yet" label
        self._model_perf_empty = ctk.CTkLabel(
            self._model_perf_data_frame, text="No data yet",
            font=("Consolas", 9), text_color=DIM)
        self._model_perf_empty.grid(row=0, column=0, padx=8, pady=2)

    def _refresh_model_perf(self):
        """Query fleet.db for per-model tok/s metrics and update the panel."""
        from data_access import FleetDB
        rows = FleetDB.model_performance(FLEET_DIR / "fleet.db")
        if rows is None:
            return

        # Update UI on main thread
        if not rows:
            # Show empty state
            for widgets in self._model_perf_labels.values():
                for w in widgets.values():
                    w.grid_remove()
            self._model_perf_empty.grid(row=0, column=0, padx=8, pady=2)
            return

        self._model_perf_empty.grid_remove()

        # Determine best tok/s for highlighting
        best_tps = max(r["avg_tps"] for r in rows) if rows else 0

        current_models = set()
        for i, r in enumerate(rows[:5]):  # max 5 rows
            model = r["model"]
            current_models.add(model)
            tps_val = r["avg_tps"] or 0
            calls_val = r["calls"] or 0
            avg_ms_val = int(r["avg_ms"] or 0)
            iq_val = r.get("avg_iq")
            tps_color = GREEN if tps_val == best_tps else TEXT
            if iq_val is not None:
                iq_text = f"{iq_val:.2f}"
                iq_color = GREEN if iq_val >= 0.7 else (ORANGE if iq_val >= 0.4 else RED)
            else:
                iq_text, iq_color = "--", DIM

            if model in self._model_perf_labels:
                # Update existing labels
                lbl = self._model_perf_labels[model]
                lbl["name"].configure(text=model)
                lbl["name"].grid(row=i, column=0, padx=2, pady=1)
                lbl["tps"].configure(text=f"{tps_val:.1f}", text_color=tps_color)
                lbl["tps"].grid(row=i, column=1, padx=2, pady=1)
                lbl["iq"].configure(text=iq_text, text_color=iq_color)
                lbl["iq"].grid(row=i, column=2, padx=2, pady=1)
                lbl["calls"].configure(text=str(calls_val))
                lbl["calls"].grid(row=i, column=3, padx=2, pady=1)
                lbl["avg_ms"].configure(text=str(avg_ms_val))
                lbl["avg_ms"].grid(row=i, column=4, padx=2, pady=1)
            else:
                # Create new row labels
                parent = self._model_perf_data_frame
                name_lbl = ctk.CTkLabel(parent, text=model, font=("Consolas", 9),
                                         text_color=TEXT, anchor="w", width=100)
                name_lbl.grid(row=i, column=0, padx=2, pady=1)
                tps_lbl = ctk.CTkLabel(parent, text=f"{tps_val:.1f}",
                                        font=("Consolas", 9, "bold"),
                                        text_color=tps_color, anchor="e", width=55)
                tps_lbl.grid(row=i, column=1, padx=2, pady=1)
                iq_lbl = ctk.CTkLabel(parent, text=iq_text,
                                       font=("Consolas", 9),
                                       text_color=iq_color, anchor="e", width=40)
                iq_lbl.grid(row=i, column=2, padx=2, pady=1)
                calls_lbl = ctk.CTkLabel(parent, text=str(calls_val),
                                          font=("Consolas", 9),
                                          text_color=TEXT, anchor="e", width=45)
                calls_lbl.grid(row=i, column=3, padx=2, pady=1)
                ms_lbl = ctk.CTkLabel(parent, text=str(avg_ms_val),
                                       font=("Consolas", 9),
                                       text_color=TEXT, anchor="e", width=55)
                ms_lbl.grid(row=i, column=4, padx=2, pady=1)
                self._model_perf_labels[model] = {
                    "name": name_lbl, "tps": tps_lbl, "iq": iq_lbl,
                    "calls": calls_lbl, "avg_ms": ms_lbl,
                }

        # Destroy labels for models no longer in results (prevent memory leak)
        for model in list(self._model_perf_labels.keys()):
            if model not in current_models:
                for w in self._model_perf_labels[model].values():
                    try:
                        w.destroy()
                    except Exception:
                        pass
                del self._model_perf_labels[model]

    # ── Refresh ───────────────────────────────────────────────────────────────
    def _refresh_status(self):
        status = parse_status()
        self._update_pills(status)
        self._update_agents_table(status)
        self._refresh_model_perf()
        self._refresh_action_items()
        self._refresh_log()
        self._update_action_badge()

    def _check_status(self):
        """Refresh UI + show Ollama status + dump STATUS.md in one pass."""
        self._refresh_status()
        if STATUS_MD.exists():
            self._log_output(STATUS_MD.read_text())
        # Check Ollama status natively via HTTP API
        def _check_ollama():
            data = ollama_tags()
            if data:
                models = [m["name"] for m in data.get("models", [])]
                msg = f"Ollama running\nModels: {', '.join(models)}"
            else:
                msg = "Ollama not running"
            self._safe_after(0, lambda: self._log_output(msg))
        threading.Thread(target=_check_ollama, daemon=True).start()

    def _open_dashboard(self):
        """Open Fleet Dashboard in the default browser."""
        import webbrowser
        try:
            text = FLEET_TOML.read_text(encoding="utf-8")
            m = re.search(r'^port\s*=\s*(\d+)', text, re.M)
            port = int(m.group(1)) if m else 5555
        except Exception:
            port = 5555
        webbrowser.open(f"http://localhost:{port}")

    def _update_pills(self, status):
        t = status.get("tasks", {})
        agents = status.get("agents", [])
        busy = sum(1 for a in agents if a["status"] == "BUSY")
        idle = sum(1 for a in agents if a["status"] == "IDLE")
        pending = t.get("Pending", 0)
        running = t.get("Running", 0)
        done    = t.get("Done", 0)
        failed  = t.get("Failed", 0)
        pill = (f"● {idle} IDLE  ● {busy} BUSY  │  "
                f"⏳{pending} pending  ▶{running} running  "
                f"✓{done} done  ✗{failed} failed")
        self._status_pills.configure(text=pill, text_color=GREEN if busy > 0 else DIM)

    def _agent_bubble_color(self, agent: dict, pending: int) -> tuple:
        """
        Return (dot_color, status_label) based on agent state + queue depth.
        ● Green  — BUSY (actively running a task)
        ● Yellow — IDLE, queue is empty
        ● Blue   — IDLE, work queued
        ● Red    — not seen in STATUS.md (offline / crashed)
        """
        status = agent.get("status", "OFFLINE")
        if status == "BUSY":
            return GREEN, "ACTIVE"
        if status == "IDLE":
            if pending > 0:
                return "#4488ff", f"RESTING ({pending}q)"
            return "#cccc00", "RESTING"
        return RED, "SLEEPING"

    def _record_agent_activity(self, agents: list):
        """Record current BUSY/IDLE state into rolling history."""
        from collections import deque
        seen = {a["name"]: a.get("status", "OFFLINE") for a in agents}
        all_tracked = set(seen.keys()) | set(self._agent_activity.keys())
        for name in all_tracked:
            if name not in self._agent_activity:
                self._agent_activity[name] = deque(maxlen=10)
            is_busy = seen.get(name) == "BUSY"
            self._agent_activity[name].append(is_busy)

    def _spark_text(self, role: str) -> tuple:
        """Return (sparkline_str, color) for an agent's recent activity.

        Uses thin unicode bars: ▁ (idle) and ▇ (busy).
        """
        history = self._agent_activity.get(role)
        if not history:
            return "▁" * 10, DIM
        bars = []
        for active in history:
            bars.append("▇" if active else "▁")
        # Pad left if fewer than 10 samples
        while len(bars) < 10:
            bars.insert(0, "▁")
        text = "".join(bars)
        # Color: green if any recent activity, dim if all idle
        has_recent = any(history)
        return text, GREEN if has_recent else "#555555"

    def _update_supervisor_labels(self, status):
        """Update Task Sup and Dr. Ders status labels from liveness dict."""
        sup_status = status.get("supervisor_status", "OFFLINE")
        if sup_status == "ONLINE":
            self._sup_status_lbl.configure(text="Task Sup: ONLINE", text_color=GREEN)
        elif sup_status == "HUNG":
            self._sup_status_lbl.configure(text="Task Sup: HUNG", text_color=ORANGE)
        else:
            self._sup_status_lbl.configure(text="Task Sup: OFFLINE", text_color=RED)

        hw_status = status.get("dr_ders_status", "OFFLINE")
        if hw_status == "ONLINE":
            self._hw_sup_status_lbl.configure(text="Dr. Ders: ONLINE", text_color=GREEN)
        elif hw_status == "TRANSIT":
            self._hw_sup_status_lbl.configure(text="Dr. Ders: SCALING", text_color=ORANGE)
        elif hw_status == "HUNG":
            self._hw_sup_status_lbl.configure(text="Dr. Ders: HUNG", text_color=ORANGE)
        else:
            self._hw_sup_status_lbl.configure(text="Dr. Ders: OFFLINE", text_color=RED)

    def _update_agents_table(self, status):
        if self._boot_active:
            return  # boot progress occupies the agents panel
        agents     = status.get("agents", [])
        pending    = status.get("tasks", {}).get("Pending", 0)

        # Update supervisor status labels
        self._update_supervisor_labels(status)

        hw_status = status.get("dr_ders_status", "OFFLINE")

        # Dr. Ders header — compact GPU/model/thermal from hw_state.json
        try:
            if HW_STATE_JSON.exists():
                _hw = json.loads(HW_STATE_JSON.read_text(encoding="utf-8"))
                _model = (_hw.get("model") or "—").split(":")[0]
                _thermal = _hw.get("thermal", {})
                _temp = _thermal.get("gpu_temp_c", 0)
                _vram_pct = _thermal.get("vram_pct", 0)
                _vram_gb = _thermal.get("vram_used_gb", 0)
                _mem = _hw.get("memory", {})
                _rss = _mem.get("hw_sup_rss_mb", 0)
                # Build compact string
                parts = []
                if _model != "—":
                    parts.append(_model)
                if _temp:
                    tc = GREEN if _temp < 70 else ORANGE if _temp < 80 else RED
                    parts.append(f"{_temp}°C")
                if _vram_gb:
                    parts.append(f"{_vram_gb:.1f}GB")
                hdr_text = f"Dr.Ders {' | '.join(parts)}" if parts else "Dr.Ders —"
                hdr_color = GREEN if hw_status == "ONLINE" else ORANGE if hw_status in ("TRANSIT", "HUNG") else RED
                self._dr_ders_hdr.configure(text=hdr_text, text_color=hdr_color)
            else:
                self._dr_ders_hdr.configure(text="Dr.Ders —", text_color=DIM)
        except Exception:
            pass

        # Record activity for sparklines
        self._record_agent_activity(agents)

        # Dynamic roles — only show agents actively running (heartbeat this session)
        # No OFFLINE ghosts from previous sessions
        seen = {a["name"]: a for a in agents}
        if self._system_running:
            self._ever_seen_roles.update(seen.keys())
        else:
            # Destroy all cached agent row widgets when system stops
            for cached in self._agent_rows.values():
                try:
                    cached['frame'].destroy()
                except Exception:
                    pass
            self._agent_rows.clear()
            self._ever_seen_roles.clear()
        # Only show agents currently present — no ghost OFFLINE rows
        rows = [seen[role_key] for role_key in sorted(self._ever_seen_roles) if role_key in seen]

        active_roles = set()
        for i, a in enumerate(rows):
            role_key = a["name"]
            active_roles.add(role_key)
            color, label = self._agent_bubble_color(a, pending)
            display_name = themed_name(a['name'])
            spark, spark_color = self._spark_text(a["name"])
            name_color = TEXT if label != "SLEEPING" else DIM
            task_text = a.get("task", "—")
            task_display = task_text if task_text != "—" else ""

            if role_key in self._agent_rows:
                # Update existing row widgets — no destroy/recreate
                cached = self._agent_rows[role_key]
                cached["frame"].grid(row=i, column=0, sticky="ew", padx=4, pady=1)
                cached["dot"].configure(text_color=color)
                cached["name"].configure(text=display_name, text_color=name_color)
                cached["task"].configure(text=task_display)
                cached["spark"].configure(text=spark, text_color=spark_color)
                cached["status"].configure(text=label, text_color=color)
                # Show/hide recover button
                if label == "SLEEPING":
                    cached["recover"].grid(row=0, column=5, padx=(2, 2))
                else:
                    cached["recover"].grid_remove()
            else:
                # Create row for the first time
                row_frame = ctk.CTkFrame(self._agents_frame_inner, fg_color="transparent")
                row_frame.grid(row=i, column=0, sticky="ew", padx=4, pady=1)
                row_frame.grid_columnconfigure(1, weight=1)

                dot_lbl = ctk.CTkLabel(row_frame, text="●", font=("Consolas", 11),
                             text_color=color, width=14)
                dot_lbl.grid(row=0, column=0, padx=(2, 3))

                name_lbl = ctk.CTkLabel(row_frame, text=display_name,
                             font=("Consolas", 10), text_color=name_color,
                             anchor="w", width=110)
                name_lbl.grid(row=0, column=1, sticky="w")

                task_lbl = ctk.CTkLabel(row_frame, text=task_display,
                             font=("Consolas", 8), text_color=DIM,
                             anchor="w", width=80)
                task_lbl.grid(row=0, column=2, padx=(2, 2))

                spark_lbl = ctk.CTkLabel(row_frame, text=spark, font=("Consolas", 9),
                             text_color=spark_color, width=70)
                spark_lbl.grid(row=0, column=3, padx=(2, 4))

                status_lbl = ctk.CTkLabel(row_frame, text=label, font=("Consolas", 9),
                             text_color=color, anchor="e", width=85)
                status_lbl.grid(row=0, column=4, sticky="e", padx=(0, 2))

                recover_btn = ctk.CTkButton(
                    row_frame, text="↺", width=22, height=18,
                    font=("RuneScape Plain 11", 9), fg_color=ACCENT, hover_color=ACCENT_H,
                    command=lambda r=role_key: self._recover_agent(r),
                )
                if label == "SLEEPING":
                    recover_btn.grid(row=0, column=5, padx=(2, 2))

                self._agent_rows[role_key] = {
                    "frame": row_frame, "dot": dot_lbl, "name": name_lbl,
                    "task": task_lbl, "spark": spark_lbl, "status": status_lbl,
                    "recover": recover_btn,
                }

        # Hide rows for roles no longer present
        for role_key, cached in self._agent_rows.items():
            if role_key not in active_roles:
                cached["frame"].grid_remove()

        # Also update Agents tab dashboard counter cards (if built)
        if hasattr(self, '_task_counters') and self._task_counters:
            t = status.get("tasks", {})
            n_total = len(rows)
            n_idle = sum(1 for a in rows if a.get("status") == "IDLE")
            n_busy = sum(1 for a in rows if a.get("status") == "BUSY")
            self._task_counters["total"].configure(text=str(n_total))
            self._task_counters["idle"].configure(text=str(n_idle))
            self._task_counters["busy"].configure(text=str(n_busy))
            self._task_counters["pending"].configure(text=str(t.get("Pending", 0)))
            self._task_counters["done"].configure(text=str(t.get("Done", 0)))

    def _refresh_log(self):
        display_agent = self._log_agent_var.get()
        # Reverse-map display name to log filename (e.g. "Dr. Ders" → "hw_supervisor")
        agent = getattr(self, '_log_reverse', {}).get(display_agent, display_agent)
        tail = read_log_tail(agent, 80)
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.insert("end", tail)
        self._log_text.see("end")
        self._log_text.configure(state="disabled")
        self._log_label.configure(text=f"LOG — {display_agent}")

    # ── HITL Action Panel ──────────────────────────────────────────────────
    def _refresh_action_items(self):
        """Fetch HITL tasks and advisories in background, update action cards."""
        def _fetch():
            from data_access import FleetDB
            waiting = FleetDB.waiting_human_tasks(FLEET_DIR / "fleet.db")
            advisories = []
            try:
                if PENDING_DIR.exists():
                    for f in sorted(PENDING_DIR.glob("advisory_*.md"))[:10]:
                        try:
                            text = f.read_text(encoding="utf-8", errors="replace")
                            title = text.split("\n")[0].strip("# ").strip() if text else f.name
                            advisories.append({
                                "file": f.name, "title": title[:80], "path": str(f)})
                        except Exception:
                            pass
            except Exception:
                pass
            return waiting, advisories

        def _render(data):
            if not hasattr(self, '_action_cards'):
                return
            waiting, advisories = data
            cards = list(self._action_cards)
            self._action_cards.clear()
            for w in cards:
                try:
                    w.destroy()
                except Exception:
                    pass

            total = len(waiting) + len(advisories)
            self._actions_count_lbl.configure(
                text=f"{total} pending" if total else "")

            if not total:
                self._actions_empty_lbl.pack(pady=12)
                return
            self._actions_empty_lbl.pack_forget()

            for item in waiting:
                card = ctk.CTkFrame(self._actions_scroll, fg_color=BG3, corner_radius=6)
                card.pack(fill="x", padx=2, pady=(1, 1))
                self._action_cards.append(card)
                agent_name = item.get("assigned_to", "?")
                card_hdr = ctk.CTkFrame(card, fg_color="transparent")
                card_hdr.pack(fill="x", padx=6, pady=(4, 0))
                card_hdr.grid_columnconfigure(0, weight=1)
                ctk.CTkLabel(card_hdr, text=f"\U0001f916 {agent_name} — Task #{item['id']}",
                             font=("RuneScape Bold 12", 10, "bold"), text_color=GOLD,
                             anchor="w").grid(row=0, column=0, sticky="w")
                rel = _relative_time(item.get("created_at", ""))
                if rel:
                    ctk.CTkLabel(card_hdr, text=rel, font=("Consolas", 8),
                                 text_color=DIM, anchor="e"
                                 ).grid(row=0, column=1, sticky="e")
                question = item.get("question", "")[:120]
                ctk.CTkLabel(card, text=question, font=FONT_SM,
                             text_color=TEXT, wraplength=280, anchor="w", justify="left"
                             ).pack(fill="x", padx=6, pady=(2, 0))
                tid = item["id"]
                q_full = item.get("question", "")
                ctk.CTkButton(
                    card, text="Respond", width=70, height=22, font=FONT_SM,
                    fg_color=ACCENT, hover_color=ACCENT_H,
                    command=lambda t=tid, q=q_full: self._respond_to_agent(t, q),
                ).pack(anchor="e", padx=6, pady=(2, 4))

            for adv in advisories:
                card = ctk.CTkFrame(self._actions_scroll, fg_color="#2a1a1a", corner_radius=6)
                card.pack(fill="x", padx=2, pady=(1, 1))
                self._action_cards.append(card)
                top = ctk.CTkFrame(card, fg_color="transparent")
                top.pack(fill="x", padx=6, pady=(4, 4))
                top.grid_columnconfigure(1, weight=1)
                ctk.CTkLabel(top, text=f"\U0001f512 {adv['title'][:50]}",
                             font=("RuneScape Bold 12", 10, "bold"), text_color=ORANGE
                             ).grid(row=0, column=0, sticky="w")
                btn_frame = ctk.CTkFrame(top, fg_color="transparent")
                btn_frame.grid(row=0, column=2, sticky="e")
                ctk.CTkButton(
                    btn_frame, text="View", width=50, height=22, font=FONT_SM,
                    fg_color=BG3, hover_color=BG,
                    command=lambda p=adv["path"]: self._view_advisory(p),
                ).pack(side="left", padx=(0, 3))
                ctk.CTkButton(
                    btn_frame, text="Dismiss", width=60, height=22, font=FONT_SM,
                    fg_color=BG3, hover_color=BG,
                    command=lambda p=adv["path"]: self._dismiss_advisory_inline(p),
                ).pack(side="left")

        def _bg():
            data = _fetch()
            self._safe_after(0, lambda: _render(data))
        threading.Thread(target=_bg, daemon=True).start()

    def _respond_to_agent(self, task_id, question):
        """Open structured response dialog for an agent's HITL request."""
        dlg = ctk.CTkToplevel(self)
        dlg.title(f"Agent Request #{task_id}")
        dlg.geometry("550x480")
        dlg.resizable(False, False)
        dlg.configure(fg_color=BG2)
        dlg.transient(self)
        dlg.grab_set()

        # Question display
        ctk.CTkLabel(dlg, text="Agent is asking:", font=FONT_BOLD,
                     text_color=GOLD).pack(padx=16, pady=(12, 4), anchor="w")
        q_frame = ctk.CTkFrame(dlg, fg_color=BG3, corner_radius=4)
        q_frame.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(q_frame, text=question, font=FONT_SM, text_color=TEXT,
                     wraplength=500, justify="left", anchor="w"
                     ).pack(padx=8, pady=8, anchor="w")

        # Response type selector
        ctk.CTkLabel(dlg, text="Your response:", font=FONT_BOLD,
                     text_color=TEXT).pack(padx=16, pady=(4, 4), anchor="w")

        response_type = ctk.StringVar(value="approve")
        types_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        types_frame.pack(fill="x", padx=16)

        options = [
            ("approve", "Approve", "Accept the recommendation as-is"),
            ("reject", "Reject", "Reject with reason (agent learns from feedback)"),
            ("more_info", "Need More Info", "Ask agent to research further"),
            ("feedback", "Provide Feedback", "Give context for agent to re-process"),
            ("discuss", "Open Discussion", "Start multi-agent debate on this topic"),
        ]

        for val, label, desc in options:
            row = ctk.CTkFrame(types_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkRadioButton(row, text=label, variable=response_type, value=val,
                               font=FONT_SM, text_color=TEXT,
                               fg_color=ACCENT, hover_color=ACCENT_H
                               ).pack(side="left", padx=(0, 8))
            ctk.CTkLabel(row, text=desc, font=FONT_XS, text_color=DIM
                         ).pack(side="left")

        # Response text
        response_text = ctk.CTkTextbox(dlg, font=FONT, fg_color=BG,
                                       height=100, corner_radius=4)
        response_text.pack(fill="x", padx=16, pady=8)
        response_text.insert("1.0", "")

        # Buttons
        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 12))

        def submit():
            rtype = response_type.get()
            text = response_text.get("1.0", "end").strip()

            # Build structured response
            response = json.dumps({
                "type": rtype,
                "text": text,
                "timestamp": time.time(),
            })

            try:
                from data_access import FleetDB
                FleetDB.send_human_response(FLEET_DIR / "fleet.db", task_id, response)

                # For "more_info" and "discuss" — create follow-up task
                if rtype == "more_info":
                    self._create_followup_task(task_id, "research_loop",
                        f"Agent needs more information: {text}")
                elif rtype == "discuss":
                    self._create_followup_task(task_id, "discuss",
                        f"Discussion requested by operator: {text}")

                self._log_output(f"Response sent to task #{task_id} ({rtype})")
            except Exception as e:
                self._log_output(f"Response failed: {e}")

            dlg.destroy()
            self._refresh_action_items_now()

        ctk.CTkButton(btn_row, text="Send Response", font=FONT_SM,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      width=120, height=32, command=submit
                      ).pack(side="right")
        ctk.CTkButton(btn_row, text="Cancel", font=FONT_SM,
                      fg_color=BG3, hover_color=BG,
                      width=80, height=32, command=dlg.destroy
                      ).pack(side="right", padx=(0, 8))

    def _create_followup_task(self, parent_task_id, skill, prompt):
        """Create a follow-up task linked to the original HITL request."""
        try:
            import sqlite3
            conn = sqlite3.connect(str(FLEET_DIR / "fleet.db"), timeout=5)
            try:
                conn.execute(
                    "INSERT INTO tasks (type, status, priority, payload_json, parent_id, created_at) "
                    "VALUES (?, 'PENDING', 5, ?, ?, datetime('now'))",
                    (skill, json.dumps({"prompt": prompt, "hitl_followup": True}), parent_task_id)
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def _refresh_action_items_now(self):
        """Immediately refresh action items and comm panel."""
        self._refresh_action_items()
        try:
            self._refresh_comm()
        except Exception:
            pass

    def _view_advisory(self, path):
        """Open window showing advisory content."""
        adv_path = Path(path)
        if not adv_path.exists():
            return
        content = adv_path.read_text(encoding="utf-8", errors="replace")
        win = ctk.CTkToplevel(self)
        win.title(f"Advisory: {adv_path.name}")
        win.geometry("580x420")
        win.minsize(580, 420)
        win.resizable(False, False)
        win.configure(fg_color=BG)
        win.grab_set()
        ctk.CTkLabel(win, text=adv_path.name,
                     font=("RuneScape Bold 12", 11, "bold"), text_color=ORANGE,
                     anchor="w").pack(fill="x", padx=14, pady=(12, 4))
        text_box = ctk.CTkTextbox(win, font=("Consolas", 10), fg_color=BG2,
                                  text_color=TEXT, wrap="word", corner_radius=4)
        text_box.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        text_box.insert("1.0", content)
        text_box.configure(state="disabled")
        btn_frame = ctk.CTkFrame(win, fg_color="transparent")
        btn_frame.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkButton(btn_frame, text="Dismiss", width=100, height=30, font=FONT_SM,
                      fg_color=BG3, hover_color=BG,
                      command=lambda: (win.destroy(), self._dismiss_advisory_inline(path)),
                      ).pack(side="right", padx=(4, 0))
        ctk.CTkButton(btn_frame, text="Close", width=80, height=30, font=FONT_SM,
                      fg_color=BG3, hover_color=BG, command=win.destroy).pack(side="right")

    def _dismiss_advisory_inline(self, path):
        """Move advisory to archived/ and refresh."""
        try:
            adv_path = Path(path)
            if adv_path.exists():
                archived_dir = adv_path.parent / "archived"
                archived_dir.mkdir(exist_ok=True)
                adv_path.rename(archived_dir / adv_path.name)
                json_path = adv_path.with_suffix(".json")
                if json_path.exists():
                    json_path.rename(archived_dir / json_path.name)
                self._refresh_action_items()
                self._update_action_badge()
        except Exception:
            pass

    def _update_action_badge(self):
        adv_count = count_pending_advisories()
        hitl_count = count_waiting_human()
        total = adv_count + hitl_count

        if total > 0:
            msgs = []
            if hitl_count: msgs.append(f"{hitl_count} agent msg")
            if adv_count: msgs.append(f"{adv_count} advisory")
            self._action_badge.configure(
                text=f"  ⚠ {' | '.join(msgs)}  ",
                fg_color=ORANGE, text_color="#1a1a1a")
        else:
            self._action_badge.configure(text="", fg_color="transparent")

        # Update Fleet Comm tab badge overlay
        if hasattr(self, '_tabs'):
            self._tabs.set_badge("Fleet Comm", hitl_count)

    def _navigate_to_comm(self):
        """Switch to Fleet Comm tab and refresh (called from badge click)."""
        try:
            self._tabs.set("Fleet Comm")
            self._refresh_comm()
        except Exception:
            pass

    def _bind_shortcuts(self):
        """Bind global keyboard shortcuts after UI is built."""
        self.bind("<Control-k>", lambda e: self._open_omnibox())
        self.bind("<F5>", lambda e: self._refresh_status())
        self.bind("<r>", lambda e: self._on_r_refresh())
        self.bind("<R>", lambda e: self._on_r_refresh())
        self.bind("<Control-Key-1>", lambda e: self._tabs.set("Command Center"))
        self.bind("<Control-Key-2>", lambda e: self._tabs.set("Fleet"))
        self.bind("<Control-Key-3>", lambda e: self._tabs.set("Fleet Comm"))

    def _open_omnibox(self):
        """Open the Ctrl+K command palette."""
        try:
            from ui.omnibox import OmniBox
            OmniBox(self)
        except Exception as e:
            self._log_output(f"Omnibox error: {e}")

    def _on_r_refresh(self):
        """Refresh on R key — skip if a text entry widget has focus."""
        try:
            focused = self.focus_get()
            if focused and isinstance(focused, (ctk.CTkEntry, ctk.CTkTextbox, tk.Entry, tk.Text)):
                return
            self._refresh_status()
        except Exception:
            pass

    def _schedule_hw(self):
        try:
            def _sample():
                try:
                    cpu_s, ram_s, gpu_s, net_s, new_prev, now = get_hw_stats(
                        self._net_prev, self._net_time)
                    self._net_prev = new_prev
                    self._net_time = now
                    self._safe_after(0, lambda: self._apply_hw(cpu_s, ram_s, gpu_s, net_s))
                except Exception:
                    pass
            threading.Thread(target=_sample, daemon=True).start()
        except Exception as e:
            self._log_output(f"HW stats error: {e}")
        finally:
            self._safe_after(5000, self._schedule_hw)

    def _apply_hw(self, cpu_s, ram_s, gpu_s, net_s):
        def _target_color(pct_str, warn=70, crit=90):
            try:
                v = float(pct_str.rstrip("%"))
                return RED if v >= crit else ORANGE if v >= warn else GREEN
            except Exception:
                return DIM
        def _hysteresis(key, pct_str, warn=70, crit=90):
            """Only change color if 2 consecutive samples agree (prevents flicker)."""
            target = _target_color(pct_str, warn, crit)
            try:
                val = float(pct_str.rstrip("%"))
            except Exception:
                val = 0.0
            prev_color = self._hw_prev_colors.get(key, DIM)
            prev_val = self._hw_prev_values.get(key, 0.0)
            # Check if both current and previous sample agree on the target color
            prev_target = _target_color(f"{prev_val}%", warn, crit)
            self._hw_prev_values[key] = val
            if target == prev_target:
                self._hw_prev_colors[key] = target
                return target
            return prev_color  # hold previous color during transition

        cpu_pct = cpu_s.split()[1] if len(cpu_s.split()) > 1 else "0%"
        ram_pct = ram_s.split()[-1] if ram_s.split() else "0%"
        gpu_pct = gpu_s.split()[1] if _GPU_OK and len(gpu_s.split()) > 1 else "0%"
        self._stat_cpu.configure(text=cpu_s, text_color=_hysteresis("cpu", cpu_pct))
        self._stat_ram.configure(text=ram_s, text_color=_hysteresis("ram", ram_pct))
        self._stat_gpu.configure(text=gpu_s, text_color=_hysteresis("gpu", gpu_pct) if _GPU_OK else DIM)
        self._stat_net.configure(text=net_s, text_color=DIM)

    def _handle_sse_status(self, data):
        """Handle SSE status push — update agents and task counts without polling.

        SSE data contains agents/tasks but NOT supervisor liveness, so we
        merge in file-based supervisor/dr_ders status from parse_status().
        """
        try:
            payload = data.get("data", {})
            agents = payload.get("agents", [])
            tasks = payload.get("tasks", {})

            # SSE doesn't carry supervisor liveness — probe files for that
            sup_status = _check_supervisor_liveness()

            status = {
                "agents": agents,
                "tasks": tasks,
                **sup_status,
            }
            self._update_pills(status)
            self._update_agents_table(status)
        except Exception:
            pass

        # Refresh HITL badge on each SSE push (DB read is fast)
        def _sse_badge_update():
            self._safe_after(0, self._update_action_badge)
            # Also update Fleet Comm tab text with WAITING_HUMAN count from SSE payload
            try:
                n_waiting = len([
                    a for a in payload.get("agents", [])
                    if a.get("status") == "WAITING_HUMAN"
                ])
                def _update_tab_text():
                    try:
                        if hasattr(self, '_tabs') and "Fleet Comm" in self._tabs._tab_buttons:
                            tab_text = "\U0001f4ac  Fleet Comm" + (f" ({n_waiting})" if n_waiting > 0 else "")
                            self._tabs._tab_buttons["Fleet Comm"].configure(text=tab_text)
                    except Exception:
                        pass
                self._safe_after(0, _update_tab_text)
            except Exception:
                pass
        threading.Thread(target=_sse_badge_update, daemon=True).start()

    def _schedule_refresh(self):
        """Unified refresh every 4s — pills + agents + log/advisory (threaded I/O).
        When SSE is active, agent/task polling is skipped (SSE handles it);
        only module refreshes and log tailing run, at a slower 8s interval.
        """
        # Determine next poll interval (SSE active = 8s, fallback = 4s)
        next_interval = 4000
        try:
            # If SSE is active, skip file-based polling for agent/task data
            if getattr(self, '_sse_active', False):
                next_interval = 8000  # slower poll when SSE active
                # SSE handles agent/task updates — but supervisor liveness
                # must still be polled from file mtimes (SSE doesn't carry it)
                self._refresh_counter = getattr(self, '_refresh_counter', 0) + 1
                self._update_supervisor_labels(_check_supervisor_liveness())
                # Log tail + action badge in background thread
                def _bg_io_sse():
                    try:
                        self._safe_after(0, self._refresh_log)
                        self._safe_after(0, self._update_action_badge)
                        self._safe_after(0, self._refresh_model_perf)
                    except Exception:
                        pass
                threading.Thread(target=_bg_io_sse, daemon=True).start()
                # Refresh modules at reduced frequency
                active_tab = self._tabs.get()
                if active_tab == "Fleet Comm" and self._refresh_counter % 3 == 0:
                    self._refresh_comm()
                for name, mod in self._modules.items():
                    if getattr(mod, "LABEL", name.title()) == active_tab:
                        try:
                            mod.on_refresh()
                        except Exception:
                            pass
                        break
            else:
                # Fallback: full file-based polling when SSE is not connected
                status = parse_status()
                self._update_pills(status)
                self._update_agents_table(status)
                self._refresh_model_perf()
                # Refresh Agents tab every 8s (every other cycle) — uses cache, no flicker
                self._refresh_counter = getattr(self, '_refresh_counter', 0) + 1
                if self._refresh_counter % 2 == 0:
                    self._agents_tab_refresh()
                # Log tail + action badge in background thread to avoid blocking main thread
                def _bg_io():
                    try:
                        self._safe_after(0, self._refresh_log)
                        self._safe_after(0, self._update_action_badge)
                    except Exception:
                        pass
                threading.Thread(target=_bg_io, daemon=True).start()
                # Refresh the active module tab (only the visible one to avoid unnecessary DB work)
                active_tab = self._tabs.get()
                if active_tab == "Fleet Comm" and self._refresh_counter % 3 == 0:
                    self._refresh_comm()
                for name, mod in self._modules.items():
                    if getattr(mod, "LABEL", name.title()) == active_tab:
                        try:
                            mod.on_refresh()
                        except Exception:
                            pass
                        break
        except Exception as e:
            self._log_output(f"Refresh error: {e}")
        finally:
            self._safe_after(next_interval, self._schedule_refresh)

    def _switch_log(self, agent):
        self._refresh_log()

    # ── Ollama status + watchdog ──────────────────────────────────────────────
    def _poll_ollama(self) -> tuple:
        """
        Check Ollama API. Returns (up, detail, model_loaded).
        detail format: "model GPU(queued) VRAM | conductor" or similar.
        Reads hw_state.json for conductor status when available.
        """
        if not ollama_is_running():
            return False, "not reachable", False

        # Get queued task count from fleet.db
        from data_access import FleetDB
        queued = FleetDB.queued_task_count(FLEET_DIR / "fleet.db")
        queue_str = f"({queued})" if queued else ""

        # Determine CPU/GPU mode
        eco = self._is_eco_mode()
        mode_str = "CPU" if eco else "GPU"

        # Read conductor status from hw_state.json (written by hw_supervisor)
        conductor_suffix = ""
        try:
            if HW_STATE_JSON.exists():
                hw = json.loads(HW_STATE_JSON.read_text(encoding="utf-8"))
                cs = hw.get("conductor", "")
                if cs == "loaded":
                    conductor_suffix = " +chat"
                elif cs == "unloaded":
                    conductor_suffix = " -chat"
        except Exception:
            pass

        # Server is up — check if a model is currently loaded in VRAM
        ps_data = ollama_ps()
        if ps_data is not None:
            models = ps_data.get("models", [])
            if models:
                names = [m["name"].split(":")[0] for m in models]
                vram_str = ""
                if _ensure_gpu() and not eco:
                    try:
                        mem = _pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
                        vram_str = f" {mem.used/1e9:.1f}GB"
                    except Exception:
                        pass
                model_list = "+".join(names) if len(names) <= 2 else f"{names[0]}+{len(names)-1}"
                return True, f"{model_list} {mode_str}{queue_str}{vram_str}{conductor_suffix}", True
            else:
                return True, f"idle {mode_str}{queue_str} — unloaded{conductor_suffix}", False
        return True, f"up {mode_str}{queue_str}{conductor_suffix}", False

    def _get_complex_provider(self) -> str:
        try:
            text = FLEET_TOML.read_text(encoding="utf-8")
            m = re.search(r'^complex_provider\s*=\s*["\']([^"\']+)["\']', text, re.M)
            return m.group(1) if m else "local"
        except Exception:
            return "local"

    def _toggle_claude_research(self):
        use_claude = self._claude_research_var.get()
        try:
            doc = tomlkit.parse(FLEET_TOML.read_text(encoding="utf-8"))
            models = doc.setdefault("models", {})
            if use_claude:
                # Read the configured Claude model (last saved via Claude console or model selector)
                claude_model = models.get("claude_model", "claude-sonnet-4-6")
                provider  = "claude"
                complex_v = claude_model
            else:
                local_model = models.get("local", "qwen3:8b")
                provider  = "local"
                complex_v = local_model
            models["complex_provider"] = provider
            models["complex"] = complex_v
            FLEET_TOML.write_text(tomlkit.dumps(doc), encoding="utf-8")
            state = f"Claude ({complex_v})" if use_claude else f"local ({complex_v})"
            self._log_output(f"Research decisions → {state}  (fleet picks up on next task)")
        except Exception as e:
            self._log_output(f"Could not update fleet.toml: {e}")
            self._claude_research_var.set(not use_claude)  # revert checkbox on failure

    def _is_eco_mode(self) -> bool:
        try:
            text = FLEET_TOML.read_text(encoding="utf-8")
            m = re.search(r'^eco_mode\s*=\s*(true|false)', text, re.M | re.I)
            return m.group(1).lower() == "true" if m else False
        except Exception:
            return False

    def _is_training_active(self) -> bool:
        try:
            return any('train.py' in ' '.join(p.info.get('cmdline') or [])
                       for p in psutil.process_iter(['cmdline']))
        except Exception:
            return False

    def _send_keepalive(self, model: str):
        """Ping Ollama with keep_alive=-1 to prevent model unload."""
        ollama_keepalive(model)

    def _schedule_ollama_watch(self):
        try:
            def _check():
                try:
                    up, detail, loaded = self._poll_ollama()
                    # Keepalive: GPU mode, no training, model loaded, every 4 min
                    if up and loaded and not self._is_eco_mode():
                        now = time.time()
                        if now - self._last_keepalive >= 240:
                            model = detail.split()[0] if detail else ""
                            if model and model != "up":
                                self._send_keepalive(model)
                                self._last_keepalive = now
                    self._safe_after(0, lambda: self._apply_ollama_status(up, detail, loaded))
                except Exception:
                    pass
            threading.Thread(target=_check, daemon=True).start()
        except Exception as e:
            self._log_output(f"Ollama watch error: {e}")
        finally:
            self._safe_after(8000, self._schedule_ollama_watch)

    def _apply_ollama_status(self, up: bool, detail: str, loaded: bool = True):
        if up and loaded:
            self._ollama_dot.configure(text="●", text_color=GREEN)
            self._ollama_lbl.configure(text=detail, text_color=DIM)
            self._ollama_restart_count = 0
        elif up:
            self._ollama_dot.configure(text="●", text_color=ORANGE)
            self._ollama_lbl.configure(text=detail, text_color=ORANGE)
            self._ollama_restart_count = 0
        else:
            self._ollama_dot.configure(text="●", text_color=RED)
            self._ollama_lbl.configure(text="offline", text_color=RED)

        # Watchdog: was up, now down → auto-relaunch + recover workers (max 3)
        # Suppressed when the user deliberately stopped the system.
        if self._ollama_up is True and not up and not self._system_intentional_stop:
            if self._ollama_restart_count >= 3:
                self._log_output("Ollama offline — restart cap reached (3). Restart manually.")
                self._ollama_lbl.configure(text="offline (restart cap)", text_color=RED)
            else:
                self._ollama_restart_count += 1
                self._log_output(
                    f"Ollama went offline — relaunching (attempt {self._ollama_restart_count}/3)..."
                )
                self._ollama_lbl.configure(text="relaunching...", text_color=ORANGE)
                self._ollama_dot.configure(text_color=ORANGE)
                self._run_ollama_start(
                    lambda o, e: self._on_ollama_recovered(o, e)
                )

        self._ollama_up = up

    def _on_ollama_recovered(self, out: str, err: str):
        self._log_output(f"Ollama restarted: {out or err}")
        # Give workers time to detect the new Ollama instance, then recover offline ones
        self._safe_after(4000, self._recover_offline_agents)

    def _recover_offline_agents(self):
        """Restart any agents currently showing as OFFLINE."""
        status = parse_status()
        seen = {a["name"] for a in status.get("agents", [])}
        all_roles = ["researcher", "coder", "archivist", "analyst",
                     "sales", "onboarding", "implementation", "security"]
        offline = [r for r in all_roles if r not in seen]
        if offline:
            self._log_output(f"Auto-recovering offline agents: {', '.join(offline)}")
            for role in offline:
                self._recover_agent(role)

    # ── Ollama helpers ────────────────────────────────────────────────────────
    def _ollama_script(self) -> str:
        """Build the ollama-start bash script content (WSL fallback only)."""
        prefix = "CUDA_VISIBLE_DEVICES=-1 " if self._is_eco_mode() else ""
        return (
            "#!/bin/bash\n"
            "curl -sf http://localhost:11434/api/tags > /dev/null"
            " && echo 'Ollama already running' && exit 0\n"
            f"nohup {prefix}ollama serve >> /tmp/ollama.log 2>&1 &\n"
            "disown\n"
            "for i in $(seq 1 15); do\n"
            "    curl -sf http://localhost:11434/api/tags > /dev/null"
            " && echo 'Ollama started OK' && exit 0\n"
            "    sleep 2\n"
            "done\n"
            "echo 'Ollama start timed out - check /tmp/ollama.log'\n"
        )

    def _is_ollama_running(self) -> bool:
        """Check if Ollama is running via HTTP API (cross-platform)."""
        return ollama_is_running()

    def _run_ollama_start(self, callback=None):
        """Start Ollama natively on Windows, poll until responsive."""
        import shutil
        def _run():
            try:
                # Already running?
                if self._is_ollama_running():
                    if callback:
                        callback("Ollama already running", "")
                    return
                # Find ollama executable
                ollama_exe = shutil.which("ollama")
                if not ollama_exe and sys.platform == "win32":
                    for _p in [
                        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
                        Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "ollama.exe",
                        Path(os.environ.get("PROGRAMFILES", "")) / "Ollama" / "ollama.exe",
                    ]:
                        if _p.exists():
                            ollama_exe = str(_p)
                            break
                if not ollama_exe:
                    if callback:
                        callback("", "ollama not found — install from https://ollama.com")
                    return
                # Set eco mode env if needed
                env = os.environ.copy()
                if self._is_eco_mode():
                    env["CUDA_VISIBLE_DEVICES"] = "-1"
                # Launch ollama serve as background process
                subprocess.Popen(
                    [ollama_exe, "serve"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=env,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                # Poll until responsive (30s)
                for _ in range(15):
                    if self._is_ollama_running():
                        if callback:
                            callback("Ollama started OK", "")
                        return
                    time.sleep(2)
                if callback:
                    callback("", "Ollama start timed out")
            except Exception as e:
                if callback:
                    callback("", str(e))
        threading.Thread(target=_run, daemon=True).start()

    def _populate_model_dropdown(self):
        """Fetch installed Ollama models and update the dropdown values."""
        def _fetch():
            try:
                host = load_model_cfg().get("ollama_host", "http://localhost:11434")
                with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as r:
                    data = json.loads(r.read())
                models = sorted(set(m["name"] for m in data.get("models", [])))
                if models:
                    self._safe_after(0, lambda: self._model_switch.configure(values=models))
            except Exception:
                pass
        threading.Thread(target=_fetch, daemon=True).start()

    def _quick_model_switch(self, model_name):
        """Switch the active Ollama model from the Command Center dropdown."""
        self._log_output(f"Switching to {model_name}...")

        def _switch():
            try:
                host = load_model_cfg().get("ollama_host", "http://localhost:11434")
                # Unload current model
                import urllib.request
                try:
                    with urllib.request.urlopen(f"{host}/api/ps", timeout=3) as r:
                        data = json.loads(r.read())
                    for m in data.get("models", []):
                        body = json.dumps({"model": m["name"], "keep_alive": 0}).encode()
                        req = urllib.request.Request(f"{host}/api/generate", data=body,
                              method="POST", headers={"Content-Type": "application/json"})
                        urllib.request.urlopen(req, timeout=5)
                except Exception:
                    pass
                # Load new model on GPU
                body = json.dumps({"model": model_name, "prompt": "", "keep_alive": "30m",
                                   "options": {"num_gpu": 99}}).encode()
                req = urllib.request.Request(f"{host}/api/generate", data=body,
                      method="POST", headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=60)
                # Update fleet.toml
                import re
                toml_path = FLEET_TOML
                text = toml_path.read_text(encoding="utf-8")
                text = re.sub(r'^(\s*local\s*=\s*).*$', rf'\1"{model_name}"', text, flags=re.MULTILINE)
                toml_path.write_text(text, encoding="utf-8")
                self._safe_after(0, lambda: self._log_output(f"Switched to {model_name} (GPU)"))
            except Exception as e:
                self._safe_after(0, lambda: self._log_output(f"Switch failed: {e}"))
        threading.Thread(target=_switch, daemon=True).start()

    def _apply_strategy(self, strategy):
        """Apply a fleet strategy preset."""
        STRATEGIES = {
            "performance": {
                "model": "qwen3:8b", "max_workers": 10, "eco_mode": False,
                "idle_enabled": True, "desc": "Max speed — 8b on GPU, full fleet",
            },
            "balanced": {
                "model": "qwen3:8b", "max_workers": 6, "eco_mode": False,
                "idle_enabled": True, "desc": "Standard — 8b on GPU, balanced workers",
            },
            "training": {
                "model": "qwen3:4b", "max_workers": 4, "eco_mode": False,
                "idle_enabled": False, "desc": "Training mode — 4b on GPU, VRAM for autoresearch",
            },
            "eco": {
                "model": "qwen3:0.6b", "max_workers": 2, "eco_mode": True,
                "idle_enabled": False, "desc": "Eco mode — minimal power, smallest model",
            },
        }
        preset = STRATEGIES.get(strategy)
        if not preset:
            return
        self._log_output(f"Strategy: {strategy} — {preset['desc']}")
        # Switch model
        self._model_switch_var.set(preset["model"])
        self._quick_model_switch(preset["model"])
        # Update fleet.toml settings
        try:
            import re
            text = FLEET_TOML.read_text(encoding="utf-8")
            text = re.sub(r'^(\s*max_workers\s*=\s*).*$', rf"\1{preset['max_workers']}", text, flags=re.MULTILINE)
            text = re.sub(r'^(\s*eco_mode\s*=\s*).*$', rf"\1{'true' if preset['eco_mode'] else 'false'}", text, flags=re.MULTILINE)
            text = re.sub(r'^(\s*idle_enabled\s*=\s*).*$', rf"\1{'true' if preset['idle_enabled'] else 'false'}", text, flags=re.MULTILINE)
            FLEET_TOML.write_text(text, encoding="utf-8")
        except Exception:
            pass

    def _start_ollama(self):
        self._log_output("Starting Ollama...")
        self._run_ollama_start(
            lambda o, e: self._safe_after(0, lambda: self._log_output(o or e or "Ollama start attempted"))
        )

    def _stop_ollama(self):
        self._log_output("Stopping Ollama...")
        def _bg():
            result = _kill_ollama()
            msg = "Ollama stopped" if result else "Ollama not running"
            self._safe_after(0, lambda: self._log_output(msg))
        threading.Thread(target=_bg, daemon=True).start()

    def _ollama_status(self):
        def _bg():
            data = ollama_tags()
            if data:
                models = [m["name"] for m in data.get("models", [])]
                msg = f"Ollama running\nModels: {', '.join(models)}"
            else:
                msg = "Ollama not running"
            self._safe_after(0, lambda: self._log_output(msg))
        threading.Thread(target=_bg, daemon=True).start()

    # ── REST API helpers (delegated to fleet_api.py — TECH_DEBT 4.3) ────────
    def _fleet_api(self, endpoint, method="GET", json_data=None):
        """Call fleet dashboard REST API. Returns dict or None on failure."""
        return _fleet_api_call(endpoint, method=method, json_data=json_data)

    def _check_fleet_health(self):
        """Quick health check via REST API."""
        return fleet_health()

    # ── Fleet commands ────────────────────────────────────────────────────────
    def _recover_agent(self, role: str):
        """Restart a single crashed worker."""
        self._log_output(f"Recovering {role}...")
        def _bg():
            try:
                import shutil
                uv = shutil.which("uv")
                if uv:
                    cmd = [uv, "run", "python", "worker.py", "--role", role]
                else:
                    cmd = [_get_fleet_python(), "worker.py", "--role", role]
                log_path = FLEET_DIR / "logs" / f"{role}.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(log_path, "a") as log_f:
                    subprocess.Popen(
                        cmd, cwd=str(FLEET_DIR),
                        stdout=log_f, stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    )
                self._safe_after(0, lambda: self._log_output(f"↺ {role} restarted"))
            except Exception as e:
                self._safe_after(0, lambda: self._log_output(f"↺ {role} error: {e}"))
        threading.Thread(target=_bg, daemon=True).start()

    def _toggle_system(self):
        if self._system_running:
            self._stop_system()
        else:
            self._start_system()

    # ── Staged boot system (extracted to ui/boot.py — TECH_DEBT 4.1) ────────
    # Methods provided by BootManagerMixin:
    #   _read_fleet_models, _show_boot_progress, _boot_spin, _boot_update,
    #   _hide_boot_progress, _start_system, _boot_sequence, _boot_ollama,
    #   _boot_hw_supervisor, _boot_model, _boot_supervisor, _boot_workers,
    #   _stop_system (overridden below to try REST API first)

    def _stop_system(self):
        """v0.45: Try REST API for clean shutdown first, fall back to psutil kill."""
        # Try REST API first (clean shutdown)
        result = self._fleet_api("/api/fleet/stop", method="POST")
        if result and result.get("status") == "stopping":
            self._log_output("Fleet stop signal sent via API")
            # Still do the UI state updates from the mixin
            self._system_intentional_stop = True
            self._system_running = False
            self._boot_abort.set()
            if self._boot_active:
                self._hide_boot_progress()
            self._btn_system_toggle.configure(
                text="▶  Start", fg_color="#1e3a1e", hover_color="#2a4a2a")
            # Also stop Ollama natively (API doesn't cover Ollama)
            def _stop_ollama_bg():
                time.sleep(2)
                _kill_ollama()
                self._safe_after(0, lambda: self._log_output("System stopped"))
            threading.Thread(target=_stop_ollama_bg, daemon=True).start()
        else:
            # Fallback to psutil kill (BootManagerMixin._stop_system)
            super()._stop_system()

    def _toggle_idle(self):
        if self._idle_enabled:
            self._disable_idle()
            self._idle_enabled = False
            self._btn_idle_toggle.configure(
                text="✅ Enable Idle", fg_color="#1e2e1e", hover_color="#2a3e2a")
        else:
            self._enable_idle()
            self._idle_enabled = True
            self._btn_idle_toggle.configure(
                text="⛔ Disable Idle", fg_color="#3a1e1e", hover_color="#4a2a2a")

    def _ensure_ollama_and_run(self, fleet_cmd: str, callback):
        """Start Ollama natively, then run fleet_cmd via bridge."""
        def _after_ollama(out, err):
            self._safe_after(0, lambda: self._log_output(out or err or "Ollama check done"))
            if _HAS_BRIDGE and _bridge:
                _bridge.run_bg(fleet_cmd, lambda o, e: self._safe_after(0, lambda: callback(o, e)), timeout=60)
            elif callback:
                callback("", "fleet_bridge not available")
        self._run_ollama_start(_after_ollama)

    def _recover_all(self):
        """Kill everything, restart via staged boot."""
        self._log_output("Recovering fleet (full staged restart)...")
        def _bg():
            _kill_fleet_processes()
            time.sleep(1)
            self._safe_after(0, self._start_system)
        threading.Thread(target=_bg, daemon=True).start()

    def _start_fleet(self):
        self._log_output("Starting fleet...")
        def _bg():
            # Kill existing supervisor
            _kill_fleet_processes(["supervisor.py"])
            time.sleep(1)
            # Ensure directories
            for d in ["logs", "knowledge/summaries", "knowledge/reports"]:
                (FLEET_DIR / d).mkdir(parents=True, exist_ok=True)
            # Start supervisor natively
            import shutil
            uv = shutil.which("uv")
            if uv:
                cmd = [uv, "run", "python", "supervisor.py"]
            else:
                cmd = [_get_fleet_python(), "supervisor.py"]
            log_path = FLEET_DIR / "logs" / "supervisor.log"
            with open(log_path, "a") as log_f:
                proc = subprocess.Popen(
                    cmd, cwd=str(FLEET_DIR),
                    stdout=log_f, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
            self._safe_after(0, lambda: self._log_output(f"Fleet started. PID: {proc.pid}"))
        def _after_ollama(out, err):
            self._safe_after(0, lambda: self._log_output(out or err or "Ollama check done"))
            threading.Thread(target=_bg, daemon=True).start()
        self._run_ollama_start(_after_ollama)

    def _stop_fleet(self):
        self._log_output("Stopping fleet...")
        # v0.45: Try REST API first (clean shutdown)
        result = self._fleet_api("/api/fleet/stop", method="POST")
        if result and result.get("status") == "stopping":
            self._log_output("Fleet stop signal sent via API")
        else:
            # Fallback to psutil kill
            def _bg():
                killed = _kill_fleet_processes(["supervisor.py"])
                msg = "Fleet stopped." if killed else "Fleet not running."
                self._safe_after(0, lambda: self._log_output(msg))
            threading.Thread(target=_bg, daemon=True).start()

    def _show_status_tab(self):
        self._log_output(STATUS_MD.read_text() if STATUS_MD.exists()
                         else "STATUS.md not found.")

    def _run_audit(self):
        self._dispatch_raw("security_audit", '{"scope":"on_demand","source":"gui"}',
                           "security", "Running security audit...")

    def _run_pentest(self):
        self._dispatch_raw("pen_test", '{"target":"auto","scan_type":"service","source":"gui"}',
                           "security", "Running pen test (this may take a minute)...")

    def _open_advisories(self):
        n = count_pending_advisories()
        if n == 0:
            self._log_output("No pending advisories.")
            return
        files = list(PENDING_DIR.glob("advisory_*.md"))
        text = "\n\n" + "─" * 60 + "\n\n"
        for f in files[:3]:
            text += f.read_text(encoding="utf-8", errors="ignore")[:800] + "\n\n"
        self._log_output(f"{n} pending advisory/-ies (showing first 3):\n{text}")

    def _open_search_dialog(self):
        dialog = ctk.CTkInputDialog(
            text="Search query:", title="Web Search")
        q = dialog.get_input()
        if q:
            self._dispatch_raw("web_search", json.dumps({"query": q, "source": "gui"}),
                               None, f"Searching: {q}")

    def _show_results(self):
        if not REPORTS_DIR.exists():
            self._log_output("No reports yet.")
            return
        files = sorted(REPORTS_DIR.glob("*.md"), reverse=True)[:3]
        if not files:
            self._log_output("No reports in knowledge/reports/")
            return
        text = ""
        for f in files:
            text += f"\n{'─'*60}\n{f.name}\n{'─'*60}\n"
            text += f.read_text(encoding="utf-8", errors="ignore")[:600] + "\n"
        self._log_output(text)

    def _start_marathon(self):
        # Check if already running first via psutil
        def _bg():
            marathon_pid = None
            for proc in psutil.process_iter(['pid', 'cmdline']):
                try:
                    cmdline = proc.info.get('cmdline') or []
                    if 'dispatch_marathon.py' in ' '.join(cmdline):
                        marathon_pid = proc.info['pid']
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if marathon_pid:
                self._safe_after(0, lambda: self._log_output(
                    f"Marathon already running (PID {marathon_pid}).\n"
                    f"Use '📋 Marathon Log' to see progress, or '⏹ Stop Marathon' first."))
                return
            # Not running — launch it natively
            try:
                import shutil
                uv = shutil.which("uv")
                if uv:
                    cmd = [uv, "run", "python", "dispatch_marathon.py"]
                else:
                    cmd = [_get_fleet_python(), "dispatch_marathon.py"]
                log_path = FLEET_DIR / "logs" / "marathon.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(log_path, "a") as log_f:
                    proc = subprocess.Popen(
                        cmd, cwd=str(FLEET_DIR),
                        stdout=log_f, stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    )
                self._safe_after(0, lambda: self._log_output(
                    f"Marathon launched (PID: {proc.pid}).\n"
                    f"Phases: wait-for-idle → 8 discussion rounds (40 min apart) "
                    f"→ lead research → synthesis.\n"
                    f"Use '📋 Marathon Log' to monitor progress."))
            except Exception as e:
                self._safe_after(0, lambda: self._log_output(f"Marathon launch error: {e}"))
        threading.Thread(target=_bg, daemon=True).start()

    def _show_marathon_log(self):
        """Tail the marathon log and show it in the output panel (native file read)."""
        def _bg():
            log_path = FLEET_DIR / "logs" / "marathon.log"
            try:
                if not log_path.exists():
                    raise FileNotFoundError
                text = log_path.read_text(encoding="utf-8", errors="ignore")
                lines = text.strip().splitlines()[-80:]  # last 80 lines
            except Exception:
                self._safe_after(0, lambda: self._log_output(
                    "marathon.log is empty or not found.\n"
                    "Start marathon first, or check fleet/logs/marathon.log."))
                return
            if not lines:
                self._safe_after(0, lambda: self._log_output(
                    "marathon.log is empty or not found.\n"
                    "Start marathon first, or check fleet/logs/marathon.log."))
                return
            key = [l for l in lines if any(
                kw in l for kw in ("Phase", "round", "Round", "Task", "Waiting",
                                   "Sleeping", "synthesis", "Marathon", "=====",
                                   "Error", "Traceback", "✓"))]
            summary = "\n".join(key[-10:]) if key else ""
            tail    = "\n".join(lines[-20:])
            sep     = "─" * 40
            self._safe_after(0, lambda: self._log_output(
                f"{'=' * 40}\nMARATHON LOG\n{'=' * 40}\n"
                + (f"[Key events]\n{summary}\n\n{sep}\n" if summary else "")
                + f"[Last 20 lines]\n{tail}"))
        threading.Thread(target=_bg, daemon=True).start()

    def _stop_marathon(self):
        def _bg():
            killed = _kill_fleet_processes(["dispatch_marathon.py"])
            if killed:
                msg = f"Stopped marathon: {', '.join(killed)}"
            else:
                msg = "No marathon process found"
            self._safe_after(0, lambda: self._log_output(msg))
        threading.Thread(target=_bg, daemon=True).start()

    def _enable_idle(self):
        self._dispatch_raw("summarize", '{"description":"idle enabled confirmation"}',
                           None, None)
        self._log_output("Enable idle: edit fleet.toml → set idle_enabled = true, then restart fleet.")

    def _disable_idle(self):
        self._log_output("Idle already disabled by default (idle_enabled = false in fleet.toml).")

    # ── Task dispatch ─────────────────────────────────────────────────────────
    def _dispatch_task(self):
        text = self._task_entry.get("1.0", "end-1c").strip()
        if not text:
            return
        self._task_entry.delete("1.0", "end")
        self._task_status.configure(text="⏳ dispatching...", text_color=ORANGE)
        self._log_output(f"→ {text}")

        def _bg():
            r = None
            try:
                import shutil
                uv = shutil.which("uv")
                if uv:
                    cmd = [uv, "run", "python", "lead_client.py", "task", text, "--wait"]
                else:
                    cmd = [_get_fleet_python(), "lead_client.py", "task", text, "--wait"]
                r = subprocess.run(
                    cmd, cwd=str(FLEET_DIR),
                    capture_output=True, text=True, timeout=300,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                out, err = r.stdout.strip(), r.stderr.strip()
            except Exception as e:
                out, err = "", str(e)
            def _update():
                result = out or err or "(no output)"
                self._log_output(f"← {result[:1200]}")
                self._task_status.configure(text="✓ done", text_color=GREEN)
                self._safe_after(3000, lambda: self._task_status.configure(text=""))
                # Toast notification for task completion
                if r and r.returncode == 0:
                    self._show_toast(f"✓ Task done: {text[:40]}", GREEN)
                else:
                    self._show_toast(f"✗ Task failed: {text[:40]}", RED, duration=8000)
            self._safe_after(0, _update)
        threading.Thread(target=_bg, daemon=True).start()

    def _dispatch_raw(self, skill: str, payload_json: str, assigned_to=None, msg=None):
        if msg:
            self._log_output(msg)
        b64 = base64.b64encode(payload_json.encode()).decode()
        def _bg():
            try:
                import shutil
                uv = shutil.which("uv")
                base = uv if uv else _get_fleet_python()
                cmd = [base] + (["run", "python"] if uv else [])
                cmd += ["lead_client.py", "dispatch", skill, b64, "--b64", "--priority", "9"]
                if assigned_to:
                    cmd += ["--assigned-to", assigned_to]
                r = subprocess.run(
                    cmd, cwd=str(FLEET_DIR),
                    capture_output=True, text=True, timeout=60,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                out, err = r.stdout.strip(), r.stderr.strip()
            except Exception as e:
                out, err = "", str(e)
            self._safe_after(0, lambda: self._log_output(out or err))
        threading.Thread(target=_bg, daemon=True).start()

    def _show_toast(self, message, color=None, duration=5000):
        """Show a non-intrusive toast notification in the top-right corner."""
        if color is None:
            color = GREEN
        toast = ctk.CTkFrame(self, fg_color=color, corner_radius=8, height=36)
        toast.place(relx=1.0, x=-20, y=60, anchor="ne")
        ctk.CTkLabel(toast, text=message, font=("RuneScape Plain 11", 10), text_color="#ffffff",
                     padx=12, pady=6).pack()
        # Auto-dismiss after duration
        self._safe_after(duration, lambda: toast.place_forget() if toast.winfo_exists() else None)
    def _copy_output(self):
        """Copy OUTPUT panel contents to clipboard."""
        try:
            text = self._output_text.get("1.0", "end").strip()
            self.clipboard_clear()
            self.clipboard_append(text)
            # Brief visual feedback via button flash (no toast dependency)
            self._log_output("Copied to clipboard.")
        except Exception:
            pass

    def _log_output(self, text: str):
        """Write to the task output box + ring buffer for debug reports."""
        self._output_text.configure(state="normal")
        self._output_text.insert("end", text.strip() + "\n")
        self._output_text.see("end")
        self._output_text.configure(state="disabled")
        # Ring buffer for debug reports (last 200 lines)
        if not hasattr(self, "_log_ring"):
            from collections import deque
            self._log_ring = deque(maxlen=200)
        self._log_ring.append(text.strip())

    def _open_report_issue(self):
        """Generate debug report and offer to save/export."""
        try:
            path = generate_debug_report(app=self)
            self._log_output(f"Debug report saved: {path}")
            # Offer to open the report location
            import webbrowser
            webbrowser.open(str(path.parent))
        except Exception as e:
            self._log_output(f"Report generation failed: {e}")

    def _open_settings(self):
        SettingsDialog(self)

    def _change_agent_theme(self, choice: str):
        global _active_theme
        _active_theme = choice
        _save_theme_preference(choice)
        self._log_output(f"Agent theme changed to: {choice}")
        if hasattr(self, "_refresh_status"):
            self._refresh_status()

    def _open_claude_console(self):
        ClaudeConsole(self)

    def _open_gemini_console(self):
        GeminiConsole(self)

    def _open_local_console(self):
        LocalConsole(self)

    def _open_review_dialog(self):
        ReviewDialog(self)

    # ── Self-update ───────────────────────────────────────────────────────────
    def _check_for_updates(self):
        """v0.44: Check for updates via git and apply if available."""
        try:
            project_root = str(_SRC_DIR.parent.parent)
            # Check if we're in a git repo
            _nw = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                capture_output=True, text=True, timeout=5,
                cwd=project_root, creationflags=_nw,
            )
            if result.returncode != 0:
                return False, "Not a git repository"

            # Fetch latest
            subprocess.run(
                ["git", "fetch", "--quiet"],
                capture_output=True, timeout=30,
                cwd=project_root, creationflags=_nw,
            )

            # Check if behind
            result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD..@{u}"],
                capture_output=True, text=True, timeout=10,
                cwd=project_root, creationflags=_nw,
            )
            behind = int(result.stdout.strip()) if result.returncode == 0 else 0

            if behind == 0:
                return False, "Up to date"

            msg = f"{behind} commits behind"
            self._safe_after(0, lambda: self._show_update_banner(msg))
            return True, msg
        except Exception as e:
            return False, f"Update check failed: {e}"

    def _show_update_banner(self, msg):
        """v0.44: Show a non-intrusive update available banner."""
        if hasattr(self, '_update_banner'):
            return  # already showing
        self._update_banner = ctk.CTkFrame(self, fg_color="#1b5e20", height=32, corner_radius=0)
        self._update_banner.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._update_banner.grid_propagate(False)
        ctk.CTkLabel(self._update_banner, text=f"Update available ({msg})",
                     font=FONT_SM, text_color="#c8e6c9"
                     ).pack(side="left", padx=12)
        ctk.CTkButton(self._update_banner, text="Update Now", width=90, height=24,
                      font=FONT_BOLD, fg_color="#2e7d32",
                      hover_color="#388e3c", command=lambda: threading.Thread(
                          target=self._apply_update, daemon=True).start()
                      ).pack(side="right", padx=12, pady=4)
        # Shift existing content down to make room for banner
        self._header.grid_configure(row=1)
        self._sidebar.grid_configure(row=2)
        self._tabs.grid_configure(row=2)
        self._taskbar.grid_configure(row=3)
        self.grid_rowconfigure(1, weight=0)
        self.grid_rowconfigure(2, weight=1)

    def _apply_update(self):
        """v0.44: Pull updates and hot-reload via os.execv."""
        project_root = str(_SRC_DIR.parent.parent)

        try:
            self._log_output("Pulling updates...")
            _nw = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                capture_output=True, text=True, timeout=60,
                cwd=project_root, creationflags=_nw,
            )
            if result.returncode != 0:
                self._log_output(f"Pull failed: {result.stderr.strip()}")
                return False

            self._log_output(result.stdout.strip())

            # Sync dependencies if uv is available
            import shutil
            if shutil.which("uv"):
                self._log_output("Syncing dependencies...")
                subprocess.run(
                    ["uv", "sync"],
                    capture_output=True, timeout=120,
                    cwd=str(Path(project_root) / "fleet"),
                    creationflags=_nw,
                )

            # Hot-reload: restart ourselves
            self._log_output("Restarting BigEd CC...")
            if sys.platform != "win32":
                # Unix: os.execv replaces current process
                os.execv(sys.executable, [sys.executable] + sys.argv)
            else:
                # Windows: start new process, exit current
                subprocess.Popen([sys.executable] + sys.argv)
                sys.exit(0)

        except Exception as e:
            self._log_output(f"Update failed: {e}")
            return False

    def _launch_auto_update(self):
        """Launch Updater.exe in auto mode then close BigEdCC."""
        if not UPDATER_EXE.exists():
            self._log_output(f"Updater.exe not found at {UPDATER_EXE}")
            return
        subprocess.Popen([str(UPDATER_EXE), "--auto"])
        self.destroy()

    def _run_fleet_control(self):
        exe = _DIST_DIR / "BigEdCC.exe"
        if not exe.exists():
            self._log_output(f"BigEdCC.exe not found at {exe}")
            return
        subprocess.Popen([str(exe)], cwd=str(_DIST_DIR))
        self._log_output("Launched BigEd CC from dist/")

    def _rebuild_all(self):
        build_bat = HERE.parent / "build.bat" if getattr(sys, "frozen", False) else HERE / "build.bat"
        if not build_bat.exists():
            self._log_output(f"build.bat not found at {build_bat}")
            return
        self._log_output(f"Launching full rebuild (build.bat)...\nClose Updater.exe first if it's open.")
        subprocess.Popen(
            ["cmd", "/c", "start", "cmd", "/k", str(build_bat)],
            cwd=str(build_bat.parent),
        )

    def _launch_usb_media(self):
        """Launch the USB Media Creator tool."""
        # Check for compiled .exe first (frozen context)
        if getattr(sys, "frozen", False):
            exe = Path(sys.executable).parent / "USBMedia.exe"
            if exe.exists():
                subprocess.Popen(
                    [str(exe)],
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                self._log_output("Launched USB Media Creator (USBMedia.exe)")
                return
        # Fall back to running the Python script directly
        script = HERE / "create_usb_media.py"
        if script.exists():
            subprocess.Popen(
                [sys.executable, str(script)],
                cwd=str(HERE),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            self._log_output("Launched USB Media Creator")
        else:
            self._log_output("USB Media Creator not found (create_usb_media.py)")


# ─── Settings / Config dialogs (extracted to ui/settings.py — TECH_DEBT 4.1) ─
from ui.settings import SettingsDialog, AgentNamesDialog, KeyManagerDialog


# ─── Dialogs (extracted to ui/dialogs/ — TECH_DEBT 4.2) ──────────────────────
from ui.dialogs import (
    ThermalDialog, ModelSelectorDialog, OLLAMA_MODELS,
    ReviewDialog, WalkthroughDialog,
    _detect_system_profile, _apply_system_profile, _should_show_walkthrough,
)
from ui.dialogs.thermal import _init_gpu_refs as _init_thermal_refs
from ui.dialogs.model_selector import _init_model_selector_refs
from ui.dialogs.review import _init_review_refs
from ui.dialogs.walkthrough import _init_walkthrough_refs

# Inject late-bound refs into dialog modules (avoids circular imports)
_ensure_gpu()
_pynvml_mod = _pynvml if _GPU_OK else None
_init_thermal_refs(HERE, _GPU_OK, _GPU_HANDLE if _GPU_OK else None, _pynvml_mod)
_init_model_selector_refs(HERE, FLEET_DIR, FLEET_TOML, _GPU_OK,
                          _GPU_HANDLE if _GPU_OK else None, _pynvml_mod, load_model_cfg)
_init_review_refs(HERE, FLEET_TOML)
try:
    from ui.settings.keys import KeyManagerDialog as _KMD
except ImportError:
    _KMD = None
_init_walkthrough_refs(HERE, FLEET_TOML, _GPU_OK,
                       _GPU_HANDLE if _GPU_OK else None, _pynvml_mod, _KMD)


# ── (end of dialog imports) ──────────────────────────────────────────────────

# ─── Console classes (extracted to ui/consoles.py — TECH_DEBT 4.1) ───────────
from ui.consoles import ClaudeConsole, GeminiConsole, LocalConsole


# ─── Debug Reports ───────────────────────────────────────────────────────────

def generate_debug_report(app=None, error=None, traceback_str=None):
    """Generate a structured debug report with system state.

    Returns path to saved report file.
    """
    import traceback as tb_mod
    from datetime import datetime

    report = {
        "timestamp": datetime.now().isoformat(),
        "platform": {
            "os": sys.platform,
            "python": sys.version,
            "platform": platform.platform(),
        },
        "hardware": {},
        "fleet": {},
        "error": {},
        "logs": [],
    }

    # Hardware
    try:
        report["hardware"]["cpu_percent"] = psutil.cpu_percent()
        report["hardware"]["ram_percent"] = psutil.virtual_memory().percent
        if _ensure_gpu():
            mem = _pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
            temp = _pynvml.nvmlDeviceGetTemperature(_GPU_HANDLE, _pynvml.NVML_TEMPERATURE_GPU)
            report["hardware"]["gpu_temp_c"] = temp
            report["hardware"]["vram_used_gb"] = round(mem.used / 1e9, 2)
            report["hardware"]["vram_total_gb"] = round(mem.total / 1e9, 2)
    except Exception:
        pass

    # Fleet state
    try:
        if FLEET_DIR.exists():
            report["fleet"]["fleet_dir"] = str(FLEET_DIR)
            if (FLEET_DIR / "fleet.toml").exists():
                import tomllib
                with open(FLEET_DIR / "fleet.toml", "rb") as f:
                    cfg = tomllib.load(f)
                report["fleet"]["eco_mode"] = cfg.get("fleet", {}).get("eco_mode")
                report["fleet"]["offline_mode"] = cfg.get("fleet", {}).get("offline_mode")
                report["fleet"]["profile"] = cfg.get("launcher", {}).get("profile")
            if HW_STATE_JSON.exists():
                report["fleet"]["hw_state"] = json.loads(
                    HW_STATE_JSON.read_text(encoding="utf-8"))
    except Exception:
        pass

    # Error
    if error:
        report["error"]["message"] = str(error)
    if traceback_str:
        report["error"]["traceback"] = traceback_str
    elif error:
        report["error"]["traceback"] = tb_mod.format_exc()

    # Logs from ring buffer
    if app and hasattr(app, "_log_ring"):
        report["logs"] = list(app._log_ring)

    # Sanitize — remove API keys from report
    report_str = json.dumps(report, indent=2, default=str)
    import re
    report_str = re.sub(r'(sk-[a-zA-Z0-9_-]{10})[a-zA-Z0-9_-]+', r'\1...REDACTED', report_str)
    report_str = re.sub(r'(AIza[a-zA-Z0-9_-]{10})[a-zA-Z0-9_-]+', r'\1...REDACTED', report_str)

    # Save
    reports_dir = DATA_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = reports_dir / f"debug_{ts}.json"
    report_path.write_text(report_str, encoding="utf-8")
    return report_path


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        app = BigEdCC()
        app.mainloop()
    except Exception as e:
        import traceback
        tb_str = traceback.format_exc()
        try:
            path = generate_debug_report(
                app=locals().get("app"),
                error=e,
                traceback_str=tb_str,
            )
            print(f"Crash report saved to: {path}")
        except Exception:
            pass
        raise
