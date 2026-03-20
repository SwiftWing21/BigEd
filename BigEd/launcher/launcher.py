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

# GPU via pynvml (NVIDIA); graceful fallback if unavailable
try:
    import pynvml
    pynvml.nvmlInit()
    _GPU_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
    _GPU_OK = True
except Exception:
    _GPU_OK = False

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
    "generate_icon.py": _SRC_DIR / "generate_icon.py",
}

# ─── Theme ────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
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


def parse_status():
    """Read STATUS.md and return dict with agents + task counts."""
    result = {"agents": [], "tasks": {}, "raw": "", "supervisor_status": "OFFLINE", "dr_ders_status": "OFFLINE"}

    result.update(_check_supervisor_liveness())

    if not STATUS_MD.exists():
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
    if _GPU_OK:
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(_GPU_HANDLE)
            mem  = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
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
            win, text=self._text, font=("Segoe UI", 9),
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

        # ── Tab button strip ──────────────────────────────────────────────────
        self._bar = ctk.CTkFrame(self, fg_color=BG2, height=42, corner_radius=0)
        self._bar.grid(row=0, column=0, sticky="ew")
        self._bar.grid_propagate(False)

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
            font=("Segoe UI", 11),
            fg_color="transparent",
            hover_color=BG3,
            text_color=DIM,
            corner_radius=0,
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
        self._col += 1

    def tab(self, name: str) -> ctk.CTkFrame:
        """Return the content frame for a tab (used when building tab contents)."""
        return self._tab_frames[name]

    def set(self, name: str) -> None:
        """Switch to the named tab."""
        if name not in self._tab_frames:
            return
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


# ─── Main App ─────────────────────────────────────────────────────────────────
class BigEdCC(BootManagerMixin, ctk.CTk):
    def __init__(self):
        super().__init__()

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
                self.geometry(f"{geo['w']}x{geo['h']}+{geo['x']}+{geo['y']}")
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
        self._refresh_status()
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
            self._sse.on("connected", lambda d: setattr(self, '_sse_active', True))
            self._sse.on("disconnected", lambda d: setattr(self, '_sse_active', False))
            self._sse_active = False
            self._sse.start()
        except Exception:
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
        """Soft close: log shutdown, give agents grace period, kill processes, exit."""
        self._log_output("Shutting down fleet (agents wrapping up)...")
        self._shutdown_gui()
        try:
            from ui.boot import _kill_fleet_processes, _kill_ollama
            _kill_fleet_processes()
            time.sleep(1)
            _kill_ollama()
        except Exception:
            pass
        self.destroy()

    def _do_just_close(self):
        """Quick close: keep fleet running in background."""
        self._shutdown_gui()
        self.destroy()

    def _safe_after(self, ms, func):
        """Schedule callback only if window is still alive."""
        if self._alive:
            self.after(ms, func)

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
                     font=("Segoe UI", 12, "bold"), text_color=GOLD).pack(pady=(20, 8))

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
                     font=("Segoe UI", 13, "bold"), text_color=GOLD).pack(pady=(16, 4))
        ctk.CTkLabel(dlg, text="Stop & Exit gives agents a moment to wrap up.\n"
                     "Keep Running leaves the fleet working in the background.",
                     font=("Segoe UI", 10), text_color=DIM).pack(pady=(0, 8))

        # Remember checkbox
        remember_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(dlg, text="Remember my choice (5s countdown next time)",
                        variable=remember_var, font=("Segoe UI", 10),
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
        png = HERE / "brick_banner.png"
        if png.exists():
            try:
                img = Image.open(png)
                return ctk.CTkImage(light_image=img, dark_image=img, size=(60, 80))
            except Exception:
                pass
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

        # ── Logo + title (left group) ──────────────────────────────────
        banner = self._load_banner()
        if banner:
            ctk.CTkLabel(hdr, image=banner, text="").grid(
                row=0, column=0, padx=(12, 4), pady=6)
        else:
            ctk.CTkLabel(hdr, text="🧱", font=("Segoe UI", 22)).grid(
                row=0, column=0, padx=(12, 4), pady=6)

        self._sidebar_btn = ctk.CTkButton(
            hdr, text="≡", font=("Segoe UI", 16), width=28, height=28,
            fg_color="transparent", hover_color=BG2, text_color=DIM,
            corner_radius=4, command=self._toggle_sidebar
        )
        self._sidebar_btn.grid(row=0, column=1, padx=(0, 6), pady=6)

        # Title with version subtitle
        title_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        title_frame.grid(row=0, column=2, padx=(0, 8), pady=6, sticky="w")
        ctk.CTkLabel(title_frame, text="BIGED CC",
                     font=FONT_TITLE, text_color=GOLD).pack(anchor="w")
        ctk.CTkLabel(title_frame, text="alpha 0.31",
                     font=FONT_XS, text_color=DIM).pack(anchor="w", pady=(0, 0))

        # ── System stats (center, in a subtle container) ──────────────
        stats_container = ctk.CTkFrame(hdr, fg_color=BG2, corner_radius=6, height=36)
        stats_container.grid(row=0, column=3, sticky="w", padx=(0, 8), pady=10)
        stats_container.grid_propagate(False)

        stats_inner = ctk.CTkFrame(stats_container, fg_color="transparent")
        stats_inner.pack(expand=True, fill="both", padx=8, pady=4)

        kw = dict(font=FONT_STAT, text_color=DIM)
        self._stat_cpu = ctk.CTkLabel(stats_inner, text="CPU —", **kw)
        self._stat_ram = ctk.CTkLabel(stats_inner, text="RAM —", **kw)
        self._stat_gpu = ctk.CTkLabel(stats_inner, text="GPU —", **kw)
        self._stat_net = ctk.CTkLabel(stats_inner, text="NET —", **kw)
        # Separator dots between stats
        sep_kw = dict(font=FONT_XS, text_color="#444")
        self._stat_cpu.pack(side="left")
        ctk.CTkLabel(stats_inner, text=" · ", **sep_kw).pack(side="left")
        self._stat_ram.pack(side="left")
        ctk.CTkLabel(stats_inner, text=" · ", **sep_kw).pack(side="left")
        self._stat_gpu.pack(side="left")
        ctk.CTkLabel(stats_inner, text=" · ", **sep_kw).pack(side="left")
        self._stat_net.pack(side="left")

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
            right_frame, text="", font=("Segoe UI", 9, "bold"),
            text_color=BG, fg_color=ORANGE,
            corner_radius=10, width=0, cursor="hand2")
        self._action_badge.pack(side="left", padx=(0, 4))
        self._action_badge.bind("<Button-1>", lambda e: self._navigate_to_comm())

        self._update_badge = ctk.CTkButton(
            right_frame, text="", font=("Segoe UI", 9, "bold"),
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
            right_frame, text=badge_text, font=("Segoe UI", 9, "bold"),
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
                font=("Segoe UI", 10, "bold"),
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
        self._btn_dashboard = btn(s, "📊 Dashboard",   self._open_dashboard, "#1a2a3a", "#253545",
            tip="Open the Fleet Dashboard in your browser (localhost:5555)")
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
            text="Claude research decisions",
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
            dev_label = ctk.CTkLabel(sb, text="🔧 Developer Mode", font=("Segoe UI", 8), text_color=DIM)
            dev_label.pack(side="bottom", pady=4)

    # ── Main area ─────────────────────────────────────────────────────────────
    # ── Tabs (primary content area) ──────────────────────────────────────────
    def _build_tabs(self):
        self._db_init()

        tabs = CustomTabBar(self)
        tabs.grid(row=1, column=1, sticky="nsew", padx=0, pady=0)
        self._tabs = tabs

        tab_cfg = load_tab_cfg()

        # Always-on core tabs
        tabs.add("Command Center")
        self._build_tab_cc(tabs.tab("Command Center"))

        tabs.add("Fleet")
        self._build_tab_agents(tabs.tab("Fleet"))

        tabs.add("Fleet Comm")
        self._build_tab_comm(tabs.tab("Fleet Comm"))

        # Load modular tabs via module system
        self._modules = {}
        try:
            from modules import load_modules
            self._modules = load_modules(self, tab_cfg)
            for name, mod in self._modules.items():
                label = getattr(mod, "LABEL", name.title())
                # Check for deprecation banner
                deprecated = False
                try:
                    from modules import _load_manifest
                    manifest = _load_manifest()
                    meta = manifest.get(name, {})
                    deprecated = meta.get("deprecated", False)
                except Exception:
                    pass
                tabs.add(label)
                tab_frame = tabs.tab(label)
                if deprecated:
                    # Show deprecation banner
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
                    # Wrap in sub-frame for module content
                    content = ctk.CTkFrame(tab_frame, fg_color="transparent")
                    content.pack(fill="both", expand=True)
                    mod.build_tab(content)
                else:
                    mod.build_tab(tab_frame)
        except ImportError:
            # Fallback: no module system available, skip modular tabs
            pass

        tabs.set("Command Center")

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
                     font=("Segoe UI", 8, "bold"), text_color=DIM,
                     anchor="w").grid(row=0, column=0, padx=(8, 4), pady=4)

        self._ollama_dot = ctk.CTkLabel(
            ollama_frame, text="●", font=("Consolas", 11), text_color=DIM)
        self._ollama_dot.grid(row=0, column=1, sticky="w", padx=(0, 3))

        self._ollama_lbl = ctk.CTkLabel(
            ollama_frame, text="checking...", font=("Consolas", 9),
            text_color=DIM, anchor="w")
        self._ollama_lbl.grid(row=0, column=2, sticky="w")

        ctk.CTkButton(
            ollama_frame, text="↺", width=20, height=18,
            font=("Segoe UI", 9), fg_color=BG3, hover_color=BG,
            command=self._start_ollama,
        ).grid(row=0, column=3, padx=(3, 6))

        # Agents panel
        agents_frame = ctk.CTkFrame(left, fg_color=BG2, corner_radius=6)
        agents_frame.grid(row=1, column=0, sticky="nsew")
        agents_frame.grid_columnconfigure(0, weight=1)
        agents_frame.grid_rowconfigure(1, weight=1)

        ag_hdr = ctk.CTkFrame(agents_frame, fg_color="transparent")
        ag_hdr.grid(row=0, column=0, sticky="ew")
        ag_hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(ag_hdr, text="AGENTS", font=("Segoe UI", 9, "bold"), text_color=GOLD).grid(row=0, column=0, padx=8, pady=(4, 2), sticky="w")
        
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
                     font=("Segoe UI", 9, "bold"), text_color=GOLD
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
            log_frame, text="LOG — all", font=("Segoe UI", 9, "bold"),
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
                     font=("Segoe UI", 9, "bold"), text_color=GOLD,
                     anchor="w").grid(row=0, column=0, padx=8, pady=(4, 2), sticky="w")

        self._output_text = ctk.CTkTextbox(
            out_frame, font=("Consolas", 10), fg_color=BG2,
            text_color="#c8c8c8", wrap="word", corner_radius=0)
        self._output_text.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))

        copy_btn = ctk.CTkButton(out_frame, text="\u2398", width=28, height=24,
                                  font=("Segoe UI", 10), fg_color=BG3, hover_color=BG2,
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
            ctk.CTkLabel(card, text=label, font=("Segoe UI", 9),
                         text_color=DIM).place(x=10, y=6)
            val_lbl = ctk.CTkLabel(card, text="0", font=("Segoe UI", 20, "bold"),
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
                task_display = task if task and task != "—" else ""
                active_names.add(name)

                # Compute display values
                display_name = themed_name(name)
                if len(display_name) > 18:
                    display_name = display_name[:16] + "\u2026"
                if task_display and len(task_display) > 40:
                    task_display = task_display[:38] + "\u2026"
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
                last_result = agent_last_result.get(name, "")
                is_waiting = name in agent_waiting
                iq = agent_iq_score.get(name)
                if iq is not None:
                    iq_text = f"IQ: {iq:.2f}"
                    iq_color = GREEN if iq >= 0.7 else ORANGE if iq >= 0.4 else RED
                else:
                    iq_text = "IQ: --"
                    iq_color = DIM

                if name in self._agent_cards:
                    # Update existing card
                    c = self._agent_cards[name]
                    c["card"].grid(row=row_idx, column=col_idx, padx=4, pady=4, sticky="nsew")
                    c["dot"].configure(text_color=dot_color)
                    c["name_lbl"].configure(text=display_name, text_color=name_color)
                    c["status_lbl"].configure(text=status_text, text_color=status_color)
                    c["task_lbl"].configure(text=task_display, text_color=GOLD)
                    c["spark_lbl"].configure(text=spark, text_color=spark_color)
                    c["count_lbl"].configure(text=count_text)
                    c["edit_btn"].configure(command=lambda a=ag: self._agents_edit_dialog(a))
                    c["model_lbl"].configure(text=model_text)
                    c["tps_lbl"].configure(text=tps_text)
                    c["last_result_lbl"].configure(text=last_result)
                    c["iq_lbl"].configure(text=iq_text, text_color=iq_color)
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
                        name_color, task_display, spark, spark_color,
                        count_text, ag, model_text, tps_text, last_result,
                        is_waiting, iq_text, iq_color)
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
                    text=f"{n_active} active / {n_disabled} disabled / {n_all} total")

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
                        ctk.CTkLabel(dcard, text=d_name, font=("Segoe UI", 11),
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

    def _create_agent_card(self, parent, row, col, display_name,
                           status_text, status_color, dot_color, name_color,
                           task_display, spark, spark_color, count_text, agent_data,
                           model_text="", tps_text="\u2014 tok/s", last_result="",
                           is_waiting=False, iq_text="IQ: --", iq_color=DIM):
        """Create a single agent dashboard card and return widget dict."""
        border_w = 2 if is_waiting else 0
        border_c = ORANGE if is_waiting else BG2
        card = ctk.CTkFrame(parent, fg_color=BG2, corner_radius=8, height=140,
                            border_width=border_w, border_color=border_c)
        card.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")
        card.grid_propagate(False)

        # Row 0 (y=6): Status dot + Agent name + status text
        dot = ctk.CTkLabel(card, text="\u25cf", font=("Consolas", 14),
                           text_color=dot_color)
        dot.place(x=8, y=8)

        name_lbl = ctk.CTkLabel(card, text=display_name,
                                font=("Segoe UI", 11, "bold"), text_color=name_color)
        name_lbl.place(x=26, y=6)

        status_lbl = ctk.CTkLabel(card, text=status_text,
                                  font=("Consolas", 9), text_color=status_color)
        status_lbl.place(relx=1.0, x=-8, y=8, anchor="ne")

        # Row 1 (y=28): Model + IQ + tok/s — pushed down to clear 11pt bold name
        model_lbl = ctk.CTkLabel(card, text=model_text,
                                 font=("Consolas", 8), text_color=DIM)
        model_lbl.place(x=26, y=28)

        iq_lbl = ctk.CTkLabel(card, text=iq_text,
                               font=("Consolas", 8), text_color=iq_color)
        iq_lbl.place(relx=1.0, x=-70, y=28, anchor="ne")

        tps_lbl = ctk.CTkLabel(card, text=tps_text,
                               font=("Consolas", 8), text_color=DIM)
        tps_lbl.place(relx=1.0, x=-8, y=28, anchor="ne")

        # Row 2 (y=44): Current task
        task_lbl = ctk.CTkLabel(card, text=task_display,
                                font=("Consolas", 9), text_color=GOLD)
        task_lbl.place(x=26, y=44)

        # Row 3 (y=60): Last result preview
        last_result_lbl = ctk.CTkLabel(card, text=last_result,
                                       font=("Consolas", 8), text_color=DIM)
        last_result_lbl.place(x=26, y=60)

        # Row 4 (y=78): Activity sparkline + edit button
        spark_lbl = ctk.CTkLabel(card, text=spark,
                                 font=("Consolas", 10), text_color=spark_color)
        spark_lbl.place(x=8, y=78)

        edit_btn = ctk.CTkButton(
            card, text="\u270e", font=FONT_SM, width=24, height=18,
            fg_color=BG3, hover_color=BG,
            command=lambda a=agent_data: self._agents_edit_dialog(a))
        edit_btn.place(relx=1.0, x=-8, y=78, anchor="ne")

        # Disable button (next to edit)
        disable_btn = ctk.CTkButton(
            card, text="\u2715", font=FONT_SM, width=24, height=18,
            fg_color="#c62828", hover_color="#d32f2f",
            command=lambda n=display_name: self._toggle_agent_disabled(
                n.replace("\u2026", ""), enable=False))
        disable_btn.place(relx=1.0, x=-36, y=78, anchor="ne")

        # Row 5 (y=98): WAITING_HUMAN badge
        waiting_text = "Needs Input" if is_waiting else ""
        waiting_badge = ctk.CTkLabel(card, text=waiting_text,
                                     font=("Consolas", 8, "bold"), text_color=ORANGE)
        waiting_badge.place(x=8, y=98)

        # Row 6 (y=116): Task count (bottom-right)
        count_lbl = ctk.CTkLabel(card, text=count_text,
                                 font=("Consolas", 8), text_color=DIM)
        count_lbl.place(relx=1.0, x=-8, y=116, anchor="ne")

        return {
            "card": card, "dot": dot, "name_lbl": name_lbl,
            "status_lbl": status_lbl, "task_lbl": task_lbl,
            "spark_lbl": spark_lbl, "count_lbl": count_lbl,
            "edit_btn": edit_btn, "disable_btn": disable_btn,
            "model_lbl": model_lbl,
            "tps_lbl": tps_lbl, "last_result_lbl": last_result_lbl,
            "waiting_badge": waiting_badge, "iq_lbl": iq_lbl,
        }

    def _agents_add_dialog(self):
        self._agents_edit_dialog({})

    def _agents_edit_dialog(self, agent: dict):
        win = ctk.CTkToplevel(self)
        win.title("Agent Instance")
        win.geometry("380x280")
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
        """Human-in-the-Loop: agent questions, security advisories, message feed."""
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        # Persist provider selection across refreshes (default: Local — always available)
        if not hasattr(self, "_comm_provider_var"):
            self._comm_provider_var = ctk.StringVar(value="⚡ Local")

        # Header
        hdr = ctk.CTkFrame(parent, fg_color=BG2, height=32, corner_radius=4)
        hdr.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 0))
        hdr.grid_propagate(False)
        ctk.CTkLabel(hdr, text="FLEET COMM", font=("Segoe UI", 10, "bold"),
                     text_color=GOLD).pack(side="left", padx=8, pady=4)
        self._comm_status = ctk.CTkLabel(hdr, text="", font=FONT_SM, text_color=DIM)
        self._comm_status.pack(side="left", padx=8)
        ctk.CTkButton(hdr, text="Refresh", width=60, height=24, font=FONT_SM,
                      fg_color=BG3, hover_color=BG,
                      command=self._refresh_comm).pack(side="right", padx=8, pady=4)
        # AI draft provider toggle — right side of header, left of Refresh
        ctk.CTkSegmentedButton(
            hdr,
            values=["🤖 Claude", "✦ Gemini", "⚡ Local"],
            variable=self._comm_provider_var,
            font=FONT_SM,
            selected_color=ACCENT, selected_hover_color=ACCENT_H,
            unselected_color=BG3, unselected_hover_color=BG2,
            width=220, height=24,
        ).pack(side="right", padx=(0, 4), pady=4)
        ctk.CTkLabel(hdr, text="AI draft:", font=("Segoe UI", 9),
                     text_color=DIM).pack(side="right", padx=(8, 2), pady=4)

        # Scrollable content area
        self._comm_scroll = ctk.CTkScrollableFrame(parent, fg_color=BG, corner_radius=0)
        self._comm_scroll.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        self._comm_scroll.grid_columnconfigure(0, weight=1)

        self._comm_cards = []  # track rendered card widgets

    def _refresh_comm(self):
        """Load WAITING_HUMAN tasks and security advisories into Fleet Comm."""
        def _fetch():
            from data_access import FleetDB
            waiting = FleetDB.waiting_human_tasks(FLEET_DIR / "fleet.db")
            # Mark tasks without questions
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
                            # Extract severity counts and analysis summary
                            summary = ""
                            counts = ""
                            in_analysis = False
                            for ln in lines:
                                if ln.startswith("**Findings:**"):
                                    counts = ln.replace("**Findings:**", "").strip()
                                elif ln.startswith("## Analysis"):
                                    in_analysis = True
                                elif in_analysis and ln.strip() and not ln.startswith("##"):
                                    # Take first non-empty line of analysis
                                    summary = ln.strip("- ").strip()[:120]
                                    in_analysis = False
                                elif ln.startswith("## ") and in_analysis:
                                    in_analysis = False
                            # Also try JSON companion for structured counts
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
            # Clear existing cards
            for w in self._comm_cards:
                try:
                    w.destroy()
                except Exception:
                    pass
            self._comm_cards.clear()

            total = len(waiting) + len(advisories)
            if total:
                self._comm_status.configure(
                    text=f"{total} pending", text_color=ORANGE)
            else:
                self._comm_status.configure(
                    text="All clear", text_color=GREEN)

            if not total:
                lbl = ctk.CTkLabel(self._comm_scroll, text="No pending communications",
                                   font=FONT, text_color=DIM)
                lbl.pack(pady=40)
                self._comm_cards.append(lbl)
                return

            # Render WAITING_HUMAN cards
            for item in waiting:
                # Outer wrapper with orange left accent stripe
                wrapper = ctk.CTkFrame(self._comm_scroll, fg_color=ORANGE, corner_radius=6)
                wrapper.pack(fill="x", padx=4, pady=3)
                self._comm_cards.append(wrapper)
                card = ctk.CTkFrame(wrapper, fg_color=BG2, corner_radius=6)
                card.pack(fill="both", expand=True, padx=(2, 0))

                # Header row: title left, timestamp right
                top = ctk.CTkFrame(card, fg_color="transparent")
                top.pack(fill="x", padx=8, pady=(8, 0))
                # Left side: type + agent stacked
                hdr_left = ctk.CTkFrame(top, fg_color="transparent")
                hdr_left.pack(side="left")
                ctk.CTkLabel(hdr_left, text=item.get("type", "task"),
                             font=("Segoe UI", 10, "bold"), text_color=TEXT).pack(anchor="w")
                ctk.CTkLabel(hdr_left, text=item.get("assigned_to", "?"),
                             font=("Segoe UI", 8), text_color=DIM).pack(anchor="w")
                # Right side: relative timestamp
                ago = self._fmt_ago(item.get("created_at"))
                if ago:
                    ctk.CTkLabel(top, text=ago,
                                 font=("Segoe UI", 8), text_color=DIM).pack(side="right")

                # Question
                ctk.CTkLabel(card, text=item.get("question", ""),
                             font=FONT, text_color=TEXT, wraplength=600,
                             anchor="w", justify="left").pack(fill="x", padx=8, pady=(4, 0))

                # Reply field + Draft + Send
                reply_frame = ctk.CTkFrame(card, fg_color="transparent")
                reply_frame.pack(fill="x", padx=8, pady=(4, 8))
                reply_frame.grid_columnconfigure(0, weight=1)

                reply_var = ctk.StringVar()
                entry = ctk.CTkEntry(reply_frame, textvariable=reply_var,
                                     font=FONT_SM, fg_color=BG3, border_color=ACCENT,
                                     placeholder_text="Type your response or click ✨ to AI-draft…")
                entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

                tid = item["id"]
                question = item.get("question", "")
                agent_name = item.get("assigned_to", "agent")
                entry.bind("<Return>", lambda e, t=tid, v=reply_var: self._send_human_response(t, v.get()))

                # ✨ Draft button — filled by AI, uses selected provider
                draft_btn = ctk.CTkButton(
                    reply_frame, text="✨", width=32, height=28,
                    fg_color=BG3, hover_color=BG2,
                    font=("Segoe UI", 13), text_color=GOLD,
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

            # Render security advisories
            for adv in advisories:
                card = ctk.CTkFrame(self._comm_scroll, fg_color="#2a1a1a", corner_radius=6)
                card.pack(fill="x", padx=4, pady=3)
                self._comm_cards.append(card)

                # Title row + buttons
                top = ctk.CTkFrame(card, fg_color="transparent")
                top.pack(fill="x", padx=8, pady=(8, 0))
                ctk.CTkLabel(top, text=f"\U0001f512 {adv['title']}",
                             font=("Segoe UI", 10, "bold"), text_color=ORANGE).pack(side="left")
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

                # Severity counts (e.g. "2 HIGH, 1 MEDIUM")
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

                # Summary preview
                _summary = adv.get("summary", "")
                if _summary:
                    ctk.CTkLabel(card, text=_summary, font=FONT_SM,
                                 text_color=DIM, wraplength=600,
                                 anchor="w", justify="left",
                                 ).pack(fill="x", padx=12, pady=(2, 8))

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
                     font=("Segoe UI", 9, "bold"), text_color=GOLD,
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

        # Hide labels for models no longer in results
        for model, lbl in self._model_perf_labels.items():
            if model not in current_models:
                for w in lbl.values():
                    w.grid_remove()

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

        # Dynamic roles — show only agents that have been seen (empty at cold start)
        # Only track agents when fleet is actually running to avoid stale ghost rows
        seen = {a["name"]: a for a in agents}
        if self._system_running:
            self._ever_seen_roles.update(seen.keys())
        rows = []
        for role_key in sorted(self._ever_seen_roles):
            if role_key in seen:
                rows.append(seen[role_key])
            else:
                rows.append({"name": role_key, "role": role_key, "status": "OFFLINE"})

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
                    font=("Segoe UI", 9), fg_color=ACCENT, hover_color=ACCENT_H,
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
            for w in self._action_cards:
                try:
                    w.destroy()
                except Exception:
                    pass
            self._action_cards.clear()

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
                             font=("Segoe UI", 10, "bold"), text_color=GOLD,
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
                             font=("Segoe UI", 10, "bold"), text_color=ORANGE
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
        """Open dialog to respond to an agent's HITL request."""
        win = ctk.CTkToplevel(self)
        win.title(f"Respond to Task #{task_id}")
        win.geometry("480x260")
        win.configure(fg_color=BG)
        win.grab_set()
        ctk.CTkLabel(win, text=f"Agent question (Task #{task_id}):",
                     font=("Segoe UI", 10, "bold"), text_color=GOLD,
                     anchor="w").pack(fill="x", padx=14, pady=(12, 4))
        q_text = ctk.CTkTextbox(win, font=FONT_SM, fg_color=BG2,
                                text_color=TEXT, height=80, wrap="word", corner_radius=4)
        q_text.pack(fill="x", padx=14)
        q_text.insert("1.0", question)
        q_text.configure(state="disabled")
        ctk.CTkLabel(win, text="Your response:",
                     font=("Segoe UI", 10, "bold"), text_color=TEXT,
                     anchor="w").pack(fill="x", padx=14, pady=(8, 4))
        resp_entry = ctk.CTkEntry(win, font=FONT_SM, fg_color=BG3,
                                  border_color=ACCENT, text_color=TEXT,
                                  placeholder_text="Type your response...")
        resp_entry.pack(fill="x", padx=14)
        resp_entry.focus_set()

        def _submit():
            response = resp_entry.get().strip()
            if not response:
                return
            win.destroy()
            self._send_human_response(task_id, response)
            self._refresh_action_items()

        ctk.CTkButton(win, text="Submit", font=FONT_SM, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      command=_submit).pack(padx=14, pady=(10, 14), fill="x")
        resp_entry.bind("<Return>", lambda e: _submit())

    def _view_advisory(self, path):
        """Open window showing advisory content."""
        adv_path = Path(path)
        if not adv_path.exists():
            return
        content = adv_path.read_text(encoding="utf-8", errors="replace")
        win = ctk.CTkToplevel(self)
        win.title(f"Advisory: {adv_path.name}")
        win.geometry("580x420")
        win.configure(fg_color=BG)
        win.grab_set()
        ctk.CTkLabel(win, text=adv_path.name,
                     font=("Segoe UI", 11, "bold"), text_color=ORANGE,
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
            self._safe_after(3000, self._schedule_hw)

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
                if _GPU_OK and not eco:
                    try:
                        mem = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
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
                if not ollama_exe:
                    if callback:
                        callback("", "ollama not found on PATH")
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
                if r.returncode == 0:
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
        ctk.CTkLabel(toast, text=message, font=("Segoe UI", 10), text_color="#ffffff",
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
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                capture_output=True, text=True, timeout=5,
                cwd=project_root
            )
            if result.returncode != 0:
                return False, "Not a git repository"

            # Fetch latest
            subprocess.run(
                ["git", "fetch", "--quiet"],
                capture_output=True, timeout=30,
                cwd=project_root
            )

            # Check if behind
            result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD..@{u}"],
                capture_output=True, text=True, timeout=10,
                cwd=project_root
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
                     font=("Segoe UI", 10), text_color="#c8e6c9"
                     ).pack(side="left", padx=12)
        ctk.CTkButton(self._update_banner, text="Update Now", width=90, height=24,
                      font=("Segoe UI", 10, "bold"), fg_color="#2e7d32",
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
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                capture_output=True, text=True, timeout=60,
                cwd=project_root
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
                    cwd=str(Path(project_root) / "fleet")
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


# ─── Settings / Config dialogs (extracted to ui/settings.py — TECH_DEBT 4.1) ─
from ui.settings import SettingsDialog, AgentNamesDialog, KeyManagerDialog


# ─── GPU Thermal / Power Dialog ───────────────────────────────────────────────
class ThermalDialog(ctk.CTkToplevel):
    """
    GPU power-limit and thermal monitor.
    Lower the power limit to reduce heat, noise, and long-term wear.
    Applies via pynvml (no UAC) when the process has the right privileges;
    falls back to nvidia-smi via a UAC-elevated PowerShell call.
    Works on any NVIDIA GPU that exposes power management through NVML.
    """
    REFRESH_MS = 2000

    def __init__(self, parent):
        super().__init__(parent)
        self.title("BigEd CC — GPU Power & Thermal")
        self.geometry("580x480")
        self.configure(fg_color=BG)
        self.grab_set()

        ico = HERE / "brick.ico"
        if ico.exists():
            try: self.iconbitmap(str(ico))
            except Exception: pass

        self._alive        = True
        self._default_w    = 0.0    # default TDP watts
        self._min_w        = 0.0
        self._max_w        = 0.0
        self._current_w    = 0.0
        self._slider_var   = ctk.DoubleVar(value=0)
        self._slider_ready = False

        self._build_ui()
        threading.Thread(target=self._load_limits, daemon=True).start()
        self._schedule_live()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self._alive = False
        self.destroy()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color=BG3, height=52, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="⚡  GPU POWER & THERMAL",
                     font=("Segoe UI", 13, "bold"), text_color=GOLD
                     ).grid(row=0, column=0, padx=14, pady=10, sticky="w")
        self._gpu_name_lbl = ctk.CTkLabel(
            hdr, text="Detecting GPU...", font=("Segoe UI", 9), text_color=DIM)
        self._gpu_name_lbl.grid(row=0, column=1, padx=8, sticky="w")

        # Live stats bar
        stats = ctk.CTkFrame(self, fg_color="#111111", height=28, corner_radius=0)
        stats.grid(row=1, column=0, sticky="ew")
        stats.grid_propagate(False)
        stats.grid_columnconfigure((0, 1, 2, 3), weight=1)
        kw = dict(font=("Consolas", 10), text_color=DIM, anchor="center")
        self._lv_temp  = ctk.CTkLabel(stats, text="Temp —", **kw)
        self._lv_power = ctk.CTkLabel(stats, text="Power —", **kw)
        self._lv_util  = ctk.CTkLabel(stats, text="GPU Util —", **kw)
        self._lv_vram  = ctk.CTkLabel(stats, text="VRAM —", **kw)
        self._lv_temp.grid( row=0, column=0, padx=6, pady=4)
        self._lv_power.grid(row=0, column=1, padx=6, pady=4)
        self._lv_util.grid( row=0, column=2, padx=6, pady=4)
        self._lv_vram.grid( row=0, column=3, padx=6, pady=4)

        # Main control area
        ctrl = ctk.CTkFrame(self, fg_color=BG2, corner_radius=0)
        ctrl.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)
        ctrl.grid_columnconfigure(0, weight=1)

        # Power limit section
        pl_frame = ctk.CTkFrame(ctrl, fg_color=BG3, corner_radius=6)
        pl_frame.grid(row=0, column=0, padx=16, pady=(16, 8), sticky="ew")
        pl_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(pl_frame, text="Power Limit",
                     font=("Segoe UI", 11, "bold"), text_color=GOLD,
                     anchor="w").grid(row=0, column=0, padx=12, pady=(10, 2), sticky="w")
        ctk.CTkLabel(pl_frame,
                     text="Lowering power reduces heat and extends GPU lifespan.\n"
                          "The GPU throttles gracefully — no stability issues.",
                     font=("Segoe UI", 9), text_color=DIM,
                     anchor="w", justify="left"
                     ).grid(row=1, column=0, padx=12, pady=(0, 8), sticky="w")

        slider_row = ctk.CTkFrame(pl_frame, fg_color="transparent")
        slider_row.grid(row=2, column=0, padx=12, pady=(0, 4), sticky="ew")
        slider_row.grid_columnconfigure(0, weight=1)

        self._slider = ctk.CTkSlider(
            slider_row, variable=self._slider_var,
            from_=0, to=100,
            height=18, corner_radius=4,
            fg_color=BG, progress_color=ACCENT, button_color=GOLD,
            button_hover_color="#e0c060",
            command=self._on_slider,
            state="disabled",
        )
        self._slider.grid(row=0, column=0, sticky="ew", pady=4)

        self._slider_lbl = ctk.CTkLabel(
            slider_row, text="— W  (—%)",
            font=("Consolas", 12, "bold"), text_color=TEXT, width=120, anchor="e")
        self._slider_lbl.grid(row=0, column=1, padx=(10, 0))

        # Range labels
        range_row = ctk.CTkFrame(pl_frame, fg_color="transparent")
        range_row.grid(row=3, column=0, padx=12, pady=(0, 6), sticky="ew")
        range_row.grid_columnconfigure(1, weight=1)
        self._min_lbl = ctk.CTkLabel(range_row, text="min", font=("Segoe UI", 8),
                                     text_color=DIM, anchor="w")
        self._min_lbl.grid(row=0, column=0, sticky="w")
        self._max_lbl = ctk.CTkLabel(range_row, text="max", font=("Segoe UI", 8),
                                     text_color=DIM, anchor="e")
        self._max_lbl.grid(row=0, column=2, sticky="e")
        self._default_lbl = ctk.CTkLabel(range_row, text="", font=("Segoe UI", 8),
                                         text_color=DIM, anchor="center")
        self._default_lbl.grid(row=0, column=1)

        # Presets
        preset_frame = ctk.CTkFrame(ctrl, fg_color="transparent")
        preset_frame.grid(row=1, column=0, padx=16, pady=4, sticky="w")
        ctk.CTkLabel(preset_frame, text="Presets:", font=FONT_SM,
                     text_color=DIM).grid(row=0, column=0, padx=(0, 8))
        for col, (label, pct) in enumerate([
            ("Eco  50%", 50), ("Balanced  75%", 75),
            ("Stock  100%", 100), ("Max TDP", 110),
        ]):
            ctk.CTkButton(
                preset_frame, text=label, font=("Segoe UI", 10),
                width=0, height=28, fg_color=BG3, hover_color=BG,
                command=lambda p=pct: self._set_pct(p),
            ).grid(row=0, column=col + 1, padx=4)

        # Status and apply
        bottom = ctk.CTkFrame(self, fg_color=BG3, height=52, corner_radius=0)
        bottom.grid(row=3, column=0, sticky="ew")
        bottom.grid_propagate(False)
        bottom.grid_columnconfigure(1, weight=1)

        self._apply_btn = ctk.CTkButton(
            bottom, text="⚡ Apply Limit", width=120, height=34,
            fg_color=ACCENT, hover_color=ACCENT_H,
            font=("Segoe UI", 11, "bold"),
            state="disabled",
            command=self._apply)
        self._apply_btn.grid(row=0, column=0, padx=(12, 8), pady=9)

        ctk.CTkButton(
            bottom, text="↺ Restore Default", width=130, height=34,
            fg_color=BG2, hover_color=BG,
            command=self._restore_default
        ).grid(row=0, column=1, padx=4, pady=9, sticky="w")

        self._status_lbl = ctk.CTkLabel(
            bottom, text="", font=FONT_SM, text_color=DIM)
        self._status_lbl.grid(row=0, column=2, padx=12, sticky="e")

    # ── Live stats ────────────────────────────────────────────────────────────
    def _schedule_live(self):
        if not self._alive:
            return
        threading.Thread(target=self._sample_live, daemon=True).start()
        self.after(self.REFRESH_MS, self._schedule_live)

    def _sample_live(self):
        if not _GPU_OK:
            return
        try:
            temp  = pynvml.nvmlDeviceGetTemperature(_GPU_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
            power = pynvml.nvmlDeviceGetPowerUsage(_GPU_HANDLE) / 1000
            util  = pynvml.nvmlDeviceGetUtilizationRates(_GPU_HANDLE)
            mem   = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
            self.after(0, lambda: self._apply_live(temp, power, util.gpu, mem))
        except Exception:
            pass

    def _apply_live(self, temp, power, util_pct, mem):
        def temp_color(t):
            return RED if t >= 85 else ORANGE if t >= 70 else GREEN
        def pwr_color(w):
            if self._max_w > 0:
                p = w / self._max_w
                return RED if p >= 0.95 else ORANGE if p >= 0.75 else GREEN
            return DIM
        self._lv_temp.configure( text=f"Temp  {temp}°C",    text_color=temp_color(temp))
        self._lv_power.configure(text=f"Power  {power:.0f}W", text_color=pwr_color(power))
        self._lv_util.configure( text=f"Util  {util_pct}%",  text_color=GREEN if util_pct > 0 else DIM)
        self._lv_vram.configure( text=f"VRAM  {mem.used/1e9:.1f}/{mem.total/1e9:.1f}GB", text_color=DIM)

    # ── Load power limits ─────────────────────────────────────────────────────
    def _load_limits(self):
        if not _GPU_OK:
            self.after(0, lambda: self._status_lbl.configure(
                text="pynvml not available — install nvidia-ml-py", text_color=RED))
            return
        try:
            name = pynvml.nvmlDeviceGetName(_GPU_HANDLE)
            lo, hi = pynvml.nvmlDeviceGetPowerManagementLimitConstraints(_GPU_HANDLE)
            default = pynvml.nvmlDeviceGetPowerManagementDefaultLimit(_GPU_HANDLE)
            current = pynvml.nvmlDeviceGetPowerManagementLimit(_GPU_HANDLE)
            self._min_w     = lo / 1000
            self._max_w     = hi / 1000
            self._default_w = default / 1000
            self._current_w = current / 1000
            self.after(0, lambda: self._init_slider(name))
        except Exception as e:
            self.after(0, lambda: self._status_lbl.configure(
                text=f"NVML error: {e}", text_color=RED))

    def _init_slider(self, gpu_name: str):
        self._gpu_name_lbl.configure(text=gpu_name)
        self._slider.configure(
            from_=self._min_w, to=self._max_w, state="normal")
        self._slider_var.set(self._current_w)
        self._slider_ready = True
        self._update_slider_label(self._current_w)
        self._min_lbl.configure(text=f"min  {self._min_w:.0f}W")
        self._max_lbl.configure(text=f"max  {self._max_w:.0f}W")
        self._default_lbl.configure(text=f"default  {self._default_w:.0f}W")
        self._apply_btn.configure(state="normal")

    # ── Slider / presets ──────────────────────────────────────────────────────
    def _on_slider(self, value):
        if self._slider_ready:
            self._update_slider_label(value)

    def _update_slider_label(self, watts: float):
        pct = (watts / self._default_w * 100) if self._default_w else 0
        color = RED if pct >= 100 else ORANGE if pct >= 75 else GREEN
        self._slider_lbl.configure(
            text=f"{watts:.0f} W  ({pct:.0f}%)", text_color=color)

    def _set_pct(self, pct: int):
        if not self._slider_ready:
            return
        watts = max(self._min_w, min(self._max_w, self._default_w * pct / 100))
        self._slider_var.set(watts)
        self._update_slider_label(watts)

    def _restore_default(self):
        if not self._slider_ready:
            return
        self._set_pct(100)
        self._apply()

    # ── Apply ─────────────────────────────────────────────────────────────────
    def _apply(self):
        watts = self._slider_var.get()
        milliwatts = int(watts * 1000)
        self._status_lbl.configure(text=f"Applying {watts:.0f}W...", text_color=ORANGE)
        threading.Thread(target=self._do_apply, args=(milliwatts,), daemon=True).start()

    def _do_apply(self, milliwatts: int):
        watts = milliwatts / 1000
        # Try pynvml directly first (works if FMA is running as admin)
        if _GPU_OK:
            try:
                pynvml.nvmlDeviceSetPowerManagementLimit(_GPU_HANDLE, milliwatts)
                self._current_w = watts
                self.after(0, lambda: self._status_lbl.configure(
                    text=f"✓ Power limit set to {watts:.0f}W", text_color=GREEN))
                return
            except Exception:
                pass  # fall through to nvidia-smi elevation

        # Fall back: nvidia-smi via UAC-elevated PowerShell
        ps_cmd = (
            f"Start-Process 'nvidia-smi' '-pl {watts:.0f}' "
            f"-Verb RunAs -Wait -WindowStyle Hidden"
        )
        try:
            r = subprocess.run(
                ["powershell", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if r.returncode == 0:
                self._current_w = watts
                self.after(0, lambda: self._status_lbl.configure(
                    text=f"✓ Power limit set to {watts:.0f}W (via nvidia-smi)", text_color=GREEN))
            else:
                err = (r.stderr or r.stdout or "unknown error")[:120]
                self.after(0, lambda: self._status_lbl.configure(
                    text=f"✗ {err}", text_color=RED))
        except Exception as e:
            err_msg = f"✗ {e}"
            self.after(0, lambda m=err_msg: self._status_lbl.configure(
                text=m, text_color=RED))


# ─── LLM Model Selector Dialog ────────────────────────────────────────────────
# (model_id, display_name, vram_gb, description)
OLLAMA_MODELS = [
    ("qwen3:0.6b",       "Qwen3 0.6B",       0.5,  "Ultra-fast, minimal tasks"),
    ("qwen3:1.7b",       "Qwen3 1.7B",       1.0,  "Fast small tasks"),
    ("qwen3:4b",         "Qwen3 4B",         2.5,  "Balanced performance"),
    ("qwen3:8b",         "Qwen3 8B",         5.0,  "Strong reasoning  — fleet default"),
    ("qwen3:14b",        "Qwen3 14B",        9.0,  "Near VRAM ceiling"),
    ("qwen3:30b",        "Qwen3 30B",       19.0,  "Requires 24GB+ VRAM"),
    ("llama3.1:8b",      "Llama 3.1 8B",     5.0,  "Meta flagship 8B"),
    ("llama3.1:70b",     "Llama 3.1 70B",   42.0,  "Requires 48GB+ VRAM"),
    ("mistral:7b",       "Mistral 7B",       4.5,  "Fast instruction model"),
    ("deepseek-r1:7b",   "DeepSeek-R1 7B",   4.5,  "Chain-of-thought reasoning"),
    ("deepseek-r1:14b",  "DeepSeek-R1 14B",  9.0,  "Near VRAM ceiling"),
    ("deepseek-r1:32b",  "DeepSeek-R1 32B", 19.0,  "Requires 24GB+ VRAM"),
    ("phi4:14b",         "Phi-4 14B",        9.0,  "Microsoft 14B"),
    ("gemma3:4b",        "Gemma 3 4B",       3.0,  "Google 4B"),
    ("gemma3:12b",       "Gemma 3 12B",      8.0,  "Google 12B"),
    ("gemma3:27b",       "Gemma 3 27B",     17.0,  "Requires 20GB+ VRAM"),
    ("codellama:7b",     "CodeLlama 7B",     4.5,  "Code-specialized"),
    ("codellama:13b",    "CodeLlama 13B",    8.0,  "Code-specialized 13B"),
]


class ModelSelectorDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("BigEd CC — LLM Model Selector")
        self.geometry("700x520")
        self.configure(fg_color=BG)
        self.grab_set()

        ico = HERE / "brick.ico"
        if ico.exists():
            try: self.iconbitmap(str(ico))
            except Exception: pass

        self._hw         = self._detect_hardware()
        self._vram_total = self._hw["vram_gb"]
        # CPU-only: no GPU VRAM detected; allow small models (≤4B) to remain selectable
        self._vram_safe  = self._vram_total * 0.85 if self._vram_total > 0 else 4.0
        self._vendor     = self._hw["vendor"]
        self._is_apu     = self._hw["is_apu"]
        self._current    = self._read_current_model()
        self._selected   = self._current
        self._row_frames = {}
        mcfg = load_model_cfg()
        self._stack_var  = ctk.StringVar(value=mcfg.get("complex_provider", "claude"))
        self._build_ui()

    def _detect_hardware(self) -> dict:
        """Detect GPU vendor, VRAM, and type via fleet/gpu.py backends.

        Detection order: NVIDIA (pynvml) → AMD (pyamdgpuinfo / rocm-smi)
        → sysfs (Linux — covers Steam Deck APU) → CPU-only fallback.

        Returns a dict with keys:
            vram_gb      – total VRAM in GB (0.0 for CPU-only)
            name         – human-readable GPU name
            vendor       – "nvidia" | "amd" | "amd_sysfs" | "cpu"
            is_apu       – True when VRAM < 4 GB detected via sysfs
                           (heuristic: integrated/APU shares system RAM)
        """
        try:
            fleet_str = str(FLEET_DIR)
            if fleet_str not in sys.path:
                sys.path.insert(0, fleet_str)
            from gpu import detect_gpu, NullBackend, NvidiaBackend, AmdBackend, SysfsBackend  # noqa: PLC0415
            backend, has_gpu = detect_gpu()
            if not has_gpu:
                return {"vram_gb": 0.0, "name": "CPU only (no GPU)", "vendor": "cpu", "is_apu": False}
            mem     = backend.get_memory_info()
            vram_gb = (mem.total_bytes / 1e9) if mem else 0.0
            name    = backend.get_name()
            if isinstance(backend, NvidiaBackend):
                vendor, is_apu = "nvidia", False
            elif isinstance(backend, AmdBackend):
                vendor, is_apu = "amd", False          # discrete AMD (RX 7900 XTX etc.)
            elif isinstance(backend, SysfsBackend):
                vendor = "amd_sysfs"
                is_apu = vram_gb < 4.0                 # Steam Deck / Radeon integrated
            else:
                vendor, is_apu = "unknown", False
            return {"vram_gb": vram_gb, "name": name, "vendor": vendor, "is_apu": is_apu}
        except Exception:
            pass
        # Final fallback — pynvml direct (matches pre-existing behaviour)
        if _GPU_OK:
            try:
                mem  = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
                raw  = pynvml.nvmlDeviceGetName(_GPU_HANDLE)
                name = raw if isinstance(raw, str) else raw.decode()
                return {"vram_gb": mem.total / 1e9, "name": name, "vendor": "nvidia", "is_apu": False}
            except Exception:
                pass
        return {"vram_gb": 12.0, "name": "Unknown GPU", "vendor": "unknown", "is_apu": False}

    def _read_current_model(self) -> str:
        return load_model_cfg().get("local", "qwen3:8b")

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color=BG3, height=52, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="🧠  LLM MODEL SELECTOR",
                     font=("Segoe UI", 13, "bold"), text_color=GOLD
                     ).grid(row=0, column=0, padx=14, pady=10, sticky="w")
        gpu_label = self._hw.get("name", "Unknown GPU")
        vram_str = (f"{gpu_label}  |  "
                    f"{self._vram_total:.1f} GB total  |  "
                    f"safe limit: {self._vram_safe:.1f} GB  |  "
                    f"grayed = won't fit")
        ctk.CTkLabel(hdr, text=vram_str,
                     font=("Segoe UI", 9), text_color=DIM
                     ).grid(row=0, column=1, padx=8, sticky="w")

        # Vendor-specific advisory — shown as a second row inside the header frame
        if self._is_apu:
            warn_text = ("⚠  Integrated / APU GPU (Steam Deck or similar) — "
                         "VRAM is shared system RAM. Keep models ≤ 4B for stable performance.")
        elif self._vendor == "amd":
            warn_text = ("⚠  AMD GPU — Ollama needs ROCm for GPU acceleration. "
                         "Without ROCm, models run CPU-only regardless of VRAM.")
        elif self._vendor == "cpu":
            warn_text = ("⚠  No GPU detected — CPU-only mode. "
                         "Stick to 4B models or smaller for usable speed.")
        else:
            warn_text = None

        if warn_text:
            hdr.configure(height=72)          # expand header to fit warning row
            ctk.CTkLabel(hdr, text=warn_text,
                         font=("Segoe UI", 8), text_color=ORANGE,
                         anchor="w"
                         ).grid(row=1, column=0, columnspan=2, padx=14, pady=(0, 6), sticky="w")

        # Stack Mode bar
        stack_bar = ctk.CTkFrame(self, fg_color=BG2, height=48, corner_radius=0)
        stack_bar.grid(row=1, column=0, sticky="ew")
        stack_bar.grid_propagate(False)
        stack_bar.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(stack_bar, text="Stack Mode:", font=("Segoe UI", 10, "bold"),
                     text_color=DIM).grid(row=0, column=0, padx=(14, 8), pady=12)
        ctk.CTkSegmentedButton(
            stack_bar,
            values=["local", "claude", "gemini"],
            variable=self._stack_var,
            font=FONT_SM,
            selected_color=ACCENT, selected_hover_color=ACCENT_H,
        ).grid(row=0, column=1, padx=4, pady=8)
        descriptions = {
            "local":  "Full local — Ollama for all tasks",
            "claude": "Local + Claude Sonnet for complex tasks",
            "gemini": "Local + Gemini Flash for complex tasks (free tier)",
        }
        self._stack_desc = ctk.CTkLabel(
            stack_bar, text=descriptions.get(self._stack_var.get(), ""),
            font=("Segoe UI", 9), text_color=DIM)
        self._stack_desc.grid(row=0, column=2, padx=12, sticky="w")
        self._stack_var.trace_add("write", lambda *_: self._stack_desc.configure(
            text=descriptions.get(self._stack_var.get(), "")))

        # Model list
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG2, corner_radius=0)
        scroll.grid(row=2, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        # Column headers
        hrow = ctk.CTkFrame(scroll, fg_color=BG3, corner_radius=4)
        hrow.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        hrow.grid_columnconfigure(1, weight=1)
        hrow.grid_columnconfigure(4, weight=1)
        for col, (txt, w) in enumerate([
            ("",         18), ("Model ID", 0), ("VRAM",  68),
            ("Status",  110), ("Description", 0)
        ]):
            ctk.CTkLabel(hrow, text=txt, font=("Segoe UI", 9, "bold"),
                         text_color=DIM, width=w, anchor="w"
                         ).grid(row=0, column=col, padx=6, pady=4, sticky="w")

        for i, (model_id, _name, vram, desc) in enumerate(OLLAMA_MODELS):
            fits        = vram <= self._vram_safe
            borderline  = fits and vram > self._vram_safe * 0.75
            is_current  = model_id == self._current

            if fits:
                dot_color  = ORANGE if borderline else GREEN
                status_txt = f"fits ({vram:.1f} GB)"
                status_col = ORANGE if borderline else GREEN
                row_fg     = "#1e2e1e" if is_current else (BG if i % 2 else "#1e1e1e")
                text_col   = GREEN if is_current else TEXT
                desc_col   = DIM
            else:
                dot_color  = "#444444"
                status_txt = f"need {vram:.0f} GB"
                status_col = "#555555"
                row_fg     = "#141414" if i % 2 else "#121212"
                text_col   = "#555555"
                desc_col   = "#444444"

            row = ctk.CTkFrame(scroll, fg_color=row_fg, corner_radius=3)
            row.grid(row=i + 1, column=0, sticky="ew", padx=8, pady=1)
            row.grid_columnconfigure(1, weight=1)
            row.grid_columnconfigure(4, weight=1)

            ctk.CTkLabel(row, text="●", font=("Consolas", 12),
                         text_color=dot_color, width=18
                         ).grid(row=0, column=0, padx=(8, 2), pady=8)

            label_txt = model_id + ("  ◀ current" if is_current else "")
            name_lbl = ctk.CTkLabel(row, text=label_txt, font=("Consolas", 10),
                                    text_color=text_col, anchor="w")
            name_lbl.grid(row=0, column=1, padx=4, sticky="w")

            ctk.CTkLabel(row, text=f"{vram:.1f} GB", font=("Consolas", 10),
                         text_color=text_col, width=65, anchor="w"
                         ).grid(row=0, column=2, padx=4)
            ctk.CTkLabel(row, text=status_txt, font=("Segoe UI", 9),
                         text_color=status_col, width=105, anchor="w"
                         ).grid(row=0, column=3, padx=4)
            ctk.CTkLabel(row, text=desc, font=("Segoe UI", 9),
                         text_color=desc_col, anchor="w"
                         ).grid(row=0, column=4, padx=(4, 8), sticky="w")

            if fits:
                for widget in [row] + row.winfo_children():
                    widget.bind("<Button-1>",
                                lambda e, m=model_id, r=row, idx=i: self._select(m, r, idx))

            self._row_frames[model_id] = (row, i)

        # Bottom bar
        bar = ctk.CTkFrame(self, fg_color=BG3, height=50, corner_radius=0)
        bar.grid(row=3, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(2, weight=1)

        self._sel_lbl = ctk.CTkLabel(
            bar, text=f"Selected: {self._selected}",
            font=("Consolas", 10), text_color=GOLD)
        self._sel_lbl.grid(row=0, column=0, padx=14, pady=12, sticky="w")

        self._status_lbl = ctk.CTkLabel(bar, text="", font=FONT_SM, text_color=DIM)
        self._status_lbl.grid(row=0, column=2, padx=8)

        self._apply_btn = ctk.CTkButton(
            bar, text="✓ Apply", width=100, height=32,
            fg_color=ACCENT, hover_color=ACCENT_H,
            font=("Segoe UI", 11, "bold"),
            command=self._apply)
        self._apply_btn.grid(row=0, column=3, padx=(8, 6), pady=9)

        ctk.CTkButton(bar, text="Cancel", width=80, height=32,
                      fg_color=BG2, hover_color=BG,
                      command=self.destroy
                      ).grid(row=0, column=4, padx=(0, 12), pady=9)

    def _select(self, model_id: str, row_frame, idx: int):
        # Restore previous selection
        if self._selected and self._selected in self._row_frames:
            prev_frame, prev_idx = self._row_frames[self._selected]
            if self._selected == self._current:
                prev_frame.configure(fg_color="#1e2e1e")
            else:
                prev_frame.configure(fg_color=BG if prev_idx % 2 else "#1e1e1e")
        self._selected = model_id
        row_frame.configure(fg_color="#1e1e2e")
        self._sel_lbl.configure(text=f"Selected: {model_id}")

    def _apply(self):
        if not self._selected:
            return
        try:
            doc = tomlkit.parse(FLEET_TOML.read_text(encoding="utf-8"))
            provider = self._stack_var.get()
            # Map stack mode to the complex model string
            complex_models = {
                "claude": "claude-sonnet-4-6",
                "gemini": "gemini-2.0-flash",
                "local":  self._selected,
            }
            complex_val = complex_models.get(provider, "claude-sonnet-4-6")
            models = doc.setdefault("models", {})
            models["local"] = self._selected
            models["complex"] = complex_val
            models["complex_provider"] = provider
            FLEET_TOML.write_text(tomlkit.dumps(doc), encoding="utf-8")
            self._status_lbl.configure(
                text="✓ Saved — restart fleet to apply", text_color=GREEN)
            self._current = self._selected
            self._apply_btn.configure(state="disabled")
        except Exception as e:
            self._status_lbl.configure(text=f"✗ {e}", text_color=RED)


# ─── Console classes (extracted to ui/consoles.py — TECH_DEBT 4.1) ───────────
from ui.consoles import ClaudeConsole, GeminiConsole, LocalConsole


# ─── Review Settings Dialog ───────────────────────────────────────────────────
class ReviewDialog(ctk.CTkToplevel):
    """
    Configure the evaluator-optimizer review pass.
    - Enable/Disable: when disabled, skill outputs bypass review entirely.
    - Provider: 'api' uses Anthropic API key (billed); 'subscription' uses Gemini free tier.
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.title("BigEd CC — Review Settings")
        self.geometry("420x360")
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.grab_set()

        ico = HERE / "brick.ico"
        if ico.exists():
            try: self.iconbitmap(str(ico))
            except Exception: pass

        self._cfg = self._load()
        self._build_ui()

    def _load(self) -> dict:
        defaults = {"enabled": False, "provider": "api",
                    "claude_model": "claude-sonnet-4-6",
                    "gemini_model": "gemini-2.0-flash",
                    "local_model": "qwen3:8b",
                    "local_ctx": 16384,
                    "local_think": True}
        try:
            import tomllib
            with open(FLEET_TOML, "rb") as f:
                data = tomllib.load(f)
            return {**defaults, **data.get("review", {})}
        except Exception:
            return defaults

    def _save(self):
        try:
            doc = tomlkit.parse(FLEET_TOML.read_text(encoding="utf-8"))
            review = doc.setdefault("review", {})
            review["enabled"] = self._enabled_var.get()
            review["provider"] = self._provider_var.get()
            review["claude_model"] = self._cfg["claude_model"]
            review["gemini_model"] = self._cfg["gemini_model"]
            review["local_model"] = self._local_model_var.get()
            review["local_ctx"] = self._cfg["local_ctx"]
            review["local_think"] = self._think_var.get()
            FLEET_TOML.write_text(tomlkit.dumps(doc), encoding="utf-8")
            self._status.configure(text="Saved.", text_color=GREEN)
        except Exception as e:
            self._status.configure(text=f"Error: {e}", text_color=RED)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(self, fg_color=BG3, height=46, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        ctk.CTkLabel(hdr, text="🧪  REVIEW SETTINGS",
                     font=("Segoe UI", 13, "bold"), text_color=GOLD
                     ).grid(row=0, column=0, padx=14, pady=10, sticky="w")

        body = ctk.CTkFrame(self, fg_color=BG2, corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        body.grid_columnconfigure(1, weight=1)

        # Enable / Disable
        ctk.CTkLabel(body, text="Review pass", font=FONT_SM,
                     text_color=TEXT).grid(row=0, column=0, padx=16, pady=(18, 6), sticky="w")
        self._enabled_var = ctk.BooleanVar(value=self._cfg["enabled"])
        sw = ctk.CTkSwitch(body, text="", variable=self._enabled_var,
                           onvalue=True, offvalue=False,
                           progress_color=ACCENT, button_color=TEXT)
        sw.grid(row=0, column=1, padx=16, pady=(18, 6), sticky="w")
        ctk.CTkLabel(body, text="When OFF, skill outputs skip review entirely.",
                     font=("Segoe UI", 9), text_color=DIM
                     ).grid(row=1, column=0, columnspan=2, padx=16, pady=(0, 10), sticky="w")

        # Provider
        ctk.CTkLabel(body, text="Provider", font=FONT_SM,
                     text_color=TEXT).grid(row=2, column=0, padx=16, pady=6, sticky="w")
        self._provider_var = ctk.StringVar(value=self._cfg["provider"])
        seg = ctk.CTkSegmentedButton(
            body, values=["api", "subscription", "local"],
            variable=self._provider_var,
            font=FONT_SM, selected_color=ACCENT, selected_hover_color=ACCENT_H,
            command=self._on_provider_change,
        )
        seg.grid(row=2, column=1, padx=16, pady=6, sticky="w")

        desc_frame = ctk.CTkFrame(body, fg_color=BG3, corner_radius=4)
        desc_frame.grid(row=3, column=0, columnspan=2, padx=16, pady=(4, 8), sticky="ew")
        ctk.CTkLabel(desc_frame,
                     text="api — Claude Sonnet via Anthropic API key (billed)\n"
                          "subscription — Gemini 2.0 Flash free tier\n"
                          "local — Ollama model with extended thinking (offline)",
                     font=("Segoe UI", 9), text_color=DIM, justify="left"
                     ).pack(padx=10, pady=8, anchor="w")

        # Local model options (shown only when provider=local)
        self._local_frame = ctk.CTkFrame(body, fg_color="transparent")
        self._local_frame.grid(row=4, column=0, columnspan=2, padx=16, pady=(0, 8), sticky="ew")
        self._local_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self._local_frame, text="Model", font=FONT_SM,
                     text_color=TEXT).grid(row=0, column=0, padx=(0, 10), pady=4, sticky="w")
        self._local_model_var = ctk.StringVar(value=self._cfg["local_model"])
        ctk.CTkEntry(self._local_frame, textvariable=self._local_model_var,
                     font=FONT_SM, fg_color=BG, height=28
                     ).grid(row=0, column=1, pady=4, sticky="ew")

        self._think_var = ctk.BooleanVar(value=self._cfg["local_think"])
        ctk.CTkCheckBox(self._local_frame, text="Extended thinking (/think prefix)",
                        variable=self._think_var, font=("Segoe UI", 9),
                        text_color=DIM, checkbox_width=16, checkbox_height=16,
                        ).grid(row=1, column=0, columnspan=2, pady=(2, 0), sticky="w")

        self._on_provider_change(self._provider_var.get())

        # Footer
        foot = ctk.CTkFrame(self, fg_color=BG3, height=46, corner_radius=0)
        foot.grid(row=2, column=0, sticky="ew")
        foot.grid_propagate(False)
        foot.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(foot, text="Save", width=90, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self._save).grid(row=0, column=0, padx=12, pady=8)
        self._status = ctk.CTkLabel(foot, text="", font=("Segoe UI", 9), text_color=DIM)
        self._status.grid(row=0, column=1, padx=8, sticky="w")

    def _on_provider_change(self, value: str):
        if value == "local":
            self._local_frame.grid()
        else:
            self._local_frame.grid_remove()


# ─── Walkthrough ─────────────────────────────────────────────────────────────

def _detect_system_profile():
    """Probe hardware and return a profile dict with recommended settings.

    Returns dict with keys: ram_gb, cpu_cores, gpu_name, vram_gb,
    tier ("minimal"|"light"|"standard"|"full"), and recommended config values.
    """
    ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 1)
    cpu_cores = psutil.cpu_count(logical=False) or psutil.cpu_count() or 2

    gpu_name = None
    vram_gb = 0.0
    if _GPU_OK:
        try:
            gpu_name = pynvml.nvmlDeviceGetName(_GPU_HANDLE)
            if isinstance(gpu_name, bytes):
                gpu_name = gpu_name.decode()
            mem = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
            vram_gb = round(mem.total / (1024 ** 3), 1)
        except Exception:
            pass

    # Determine tier
    has_gpu = vram_gb >= 2.0
    if has_gpu and vram_gb >= 8.0 and ram_gb >= 16:
        tier = "full"
    elif has_gpu and vram_gb >= 4.0 and ram_gb >= 12:
        tier = "standard"
    elif ram_gb >= 8:
        tier = "light"
    else:
        tier = "minimal"

    # Recommended settings per tier
    profiles = {
        "minimal": {
            "eco_mode": True,
            "max_workers": min(3, max(2, cpu_cores // 2)),
            "model": "qwen3:0.6b",
            "conductor_model": "qwen3:0.6b",
            "model_tiers": {"default": "qwen3:0.6b", "mid": "qwen3:0.6b",
                            "low": "qwen3:0.6b", "critical": "qwen3:0.6b"},
        },
        "light": {
            "eco_mode": not has_gpu,
            "max_workers": min(5, max(3, cpu_cores // 2)),
            "model": "qwen3:1.7b" if not has_gpu else "qwen3:4b",
            "conductor_model": "qwen3:0.6b",
            "model_tiers": {"default": "qwen3:1.7b" if not has_gpu else "qwen3:4b",
                            "mid": "qwen3:1.7b", "low": "qwen3:0.6b",
                            "critical": "qwen3:0.6b"},
        },
        "standard": {
            "eco_mode": False,
            "max_workers": min(7, max(4, cpu_cores - 2)),
            "model": "qwen3:4b" if vram_gb < 6 else "qwen3:8b",
            "conductor_model": "qwen3:0.6b",
            "model_tiers": {"default": "qwen3:4b" if vram_gb < 6 else "qwen3:8b",
                            "mid": "qwen3:4b", "low": "qwen3:1.7b",
                            "critical": "qwen3:0.6b"},
        },
        "full": {
            "eco_mode": False,
            "max_workers": min(10, max(5, cpu_cores - 2)),
            "model": "qwen3:8b",
            "conductor_model": "qwen3:4b",
            "model_tiers": {"default": "qwen3:8b", "mid": "qwen3:8b",
                            "low": "qwen3:1.7b", "critical": "qwen3:0.6b"},
        },
    }

    rec = profiles[tier]
    return {
        "ram_gb": ram_gb,
        "cpu_cores": cpu_cores,
        "gpu_name": gpu_name,
        "vram_gb": vram_gb,
        "tier": tier,
        **rec,
    }


def _apply_system_profile(profile: dict):
    """Write auto-detected profile recommendations to fleet.toml."""
    try:
        doc = tomlkit.parse(FLEET_TOML.read_text(encoding="utf-8"))

        # [fleet]
        fleet = doc.setdefault("fleet", {})
        fleet["eco_mode"] = profile["eco_mode"]
        fleet["max_workers"] = profile["max_workers"]

        # [models]
        models = doc.setdefault("models", {})
        models["local"] = profile["model"]
        models["complex"] = profile["model"]
        models["conductor_model"] = profile["conductor_model"]

        # [models.tiers]
        tiers = models.setdefault("tiers", {})
        for k, v in profile["model_tiers"].items():
            tiers[k] = v

        # [gpu]
        gpu = doc.setdefault("gpu", {})
        gpu["mode"] = "full" if not profile["eco_mode"] else "eco"

        # Record what we detected
        detected = doc.setdefault("system_detected", {})
        detected["ram_gb"] = profile["ram_gb"]
        detected["cpu_cores"] = profile["cpu_cores"]
        detected["gpu_name"] = profile.get("gpu_name") or "none"
        detected["vram_gb"] = profile["vram_gb"]
        detected["tier"] = profile["tier"]
        detected["detected_at"] = datetime.now().isoformat(timespec="seconds")

        FLEET_TOML.write_text(tomlkit.dumps(doc), encoding="utf-8")
        return True
    except Exception:
        return False


class WalkthroughDialog(ctk.CTkToplevel):
    """First-run walkthrough — 7-step guided setup with skip/skip-all.

    Step 2 (System Detection) probes RAM, CPU, and GPU via psutil/pynvml
    and auto-adjusts fleet.toml for the best experience on the user's hardware.
    Critical for non-GPU users and systems with < 8 GB RAM.
    """

    STEPS = [
        {
            "title": "Welcome to BigEd CC",
            "desc": (
                "BigEd CC is an autonomous AI agent fleet that runs entirely on your machine.\n\n"
                "Your fleet has 74 skills across 10+ specialized worker roles — researchers, "
                "coders, analysts, security auditors, and more — coordinated by a dual-supervisor "
                "system. Workers share a task queue, communicate via messages, and build "
                "a local knowledge base over time.\n\n"
                "Everything stays private. Nothing leaves your machine unless you enable cloud AI.\n\n"
                "This walkthrough will help you get set up. You can skip any step."
            ),
        },
        {
            "title": "System Detection",
            "desc": (
                "BigEd CC can detect your hardware and auto-adjust settings for the best "
                "experience on your system.\n\n"
                "This checks your RAM, CPU cores, and GPU/VRAM to set the right model size, "
                "worker count, and eco mode. It is especially important for systems with less "
                "than 8 GB RAM or no dedicated GPU.\n\n"
                "Click 'Detect & Adjust' to scan your hardware now, or skip to use defaults."
            ),
            "action_label": "Detect & Adjust",
            "has_auto_detect": True,
        },
        {
            "title": "API Keys (Optional)",
            "desc": (
                "BigEd CC works fully offline with local models. Cloud AI is optional.\n\n"
                "If you add API keys, the fleet gains access to Claude, Gemini, and web search "
                "(Brave, Tavily). The system uses intelligent fallback: Claude > Gemini > Local, "
                "so if one provider is down, tasks route to the next automatically.\n\n"
                "Keys are stored in ~/.secrets (never committed to git).\n"
                "You can manage them anytime from Settings > Key Manager."
            ),
            "action_label": "Open Key Manager",
        },
        {
            "title": "Fleet Profile",
            "desc": (
                "Choose a deployment profile that matches your use case:\n\n"
                "  minimal    — Ingestion + Outputs (lightweight, personal use)\n"
                "  research   — Same + RAG pipeline and research workflows\n"
                "  consulting — CRM, Onboarding, Customers, Accounts + research\n"
                "  full       — All modules enabled\n\n"
                "You can change your profile anytime in Settings. Modules can also be "
                "toggled individually from the sidebar."
            ),
        },
        {
            "title": "Ollama Setup",
            "desc": (
                "Ollama is the local AI engine that powers your fleet workers.\n\n"
                "The default model is qwen3:8b (~6.9 GB VRAM). Dr. Ders, the hardware "
                "supervisor, monitors your GPU in real-time and automatically scales between "
                "model tiers if needed:\n\n"
                "  Default  — qwen3:8b (best quality, needs GPU)\n"
                "  Low      — qwen3:1.7b (lighter, less VRAM)\n"
                "  Critical — qwen3:0.6b (CPU-only fallback)\n\n"
                "No GPU? Enable Eco Mode in fleet.toml to run everything on CPU."
            ),
        },
        {
            "title": "Dispatch Your First Task",
            "desc": (
                "Use the task bar at the bottom of the main window to dispatch work.\n\n"
                "Try one of these:\n"
                '  summarize — "Summarize the key ideas in transformer architecture"\n'
                '  web_search — "Find recent open-source local AI projects"\n'
                '  flashcard — "Create flashcards on Python async patterns"\n\n'
                "The fleet automatically routes each task to the best-matching worker "
                "based on skill affinity. Results appear in the Knowledge tab."
            ),
        },
        {
            "title": "Consoles & Fleet Comm",
            "desc": (
                "BigEd CC has 3 interactive consoles in the sidebar:\n\n"
                "  Claude Console — Cloud AI (Anthropic API key required)\n"
                "  Gemini Console — Cloud AI (Google API key required)\n"
                "  Local Console  — Free, powered by Ollama\n\n"
                "Each console can dispatch fleet tasks mid-conversation.\n\n"
                "Fleet Comm (bottom panel) shows real-time worker activity, task results, "
                "and system events. You're all set — close this to start using BigEd CC."
            ),
        },
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.title("BigEd CC — Setup Walkthrough")
        self.geometry("560x480")
        self.resizable(False, False)
        self.configure(fg_color=BG2)
        self.grab_set()
        self.lift()
        self._parent = parent
        self._step = 0
        self._skipped = []
        self._detected_profile = None  # filled by auto-detect

        ico = HERE / "brick.ico"
        if ico.exists():
            try: self.iconbitmap(str(ico))
            except Exception: pass

        self._build_ui()
        self._show_step()

    def _build_ui(self):
        # Progress bar at top
        self._progress = ctk.CTkProgressBar(self, width=520, height=6,
                                             fg_color=BG3, progress_color=ACCENT)
        self._progress.pack(padx=20, pady=(16, 0))

        self._step_label = ctk.CTkLabel(self, text="", font=("Segoe UI", 10),
                                         text_color=DIM)
        self._step_label.pack(pady=(4, 0))

        # Title
        self._title = ctk.CTkLabel(self, text="", font=("Segoe UI", 16, "bold"),
                                    text_color=GOLD)
        self._title.pack(padx=20, pady=(12, 0), anchor="w")

        # Description
        self._desc = ctk.CTkLabel(self, text="", font=FONT, text_color=TEXT,
                                   wraplength=520, justify="left", anchor="nw")
        self._desc.pack(padx=20, pady=(8, 0), fill="both", expand=True, anchor="nw")

        # Detection result area (shown only on system detection step)
        self._detect_result = ctk.CTkLabel(self, text="", font=("Consolas", 10),
                                            text_color=DIM, wraplength=520,
                                            justify="left", anchor="nw")
        # Not packed by default

        # Action button (optional, shown for some steps)
        self._action_btn = ctk.CTkButton(self, text="", height=30,
                                          fg_color=ACCENT, hover_color=ACCENT_H,
                                          command=self._on_action)
        # Not packed by default

        # Bottom bar
        bottom = ctk.CTkFrame(self, fg_color=BG3, height=54, corner_radius=0)
        bottom.pack(side="bottom", fill="x")
        bottom.pack_propagate(False)

        # Skip All
        ctk.CTkButton(bottom, text="Skip All", width=80, height=30,
                      fg_color=BG, hover_color=BG2, text_color=DIM,
                      command=self._skip_all).pack(side="left", padx=12, pady=12)

        # Don't show again checkbox
        self._no_show_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(bottom, text="Don't show again", variable=self._no_show_var,
                        font=("Segoe UI", 9), text_color=DIM,
                        fg_color=ACCENT, checkmark_color=TEXT,
                        checkbox_width=16, checkbox_height=16,
                        ).pack(side="left", padx=(8, 0), pady=12)

        # Next / Finish
        self._next_btn = ctk.CTkButton(bottom, text="Next →", width=90, height=30,
                                        fg_color=ACCENT, hover_color=ACCENT_H,
                                        command=self._next)
        self._next_btn.pack(side="right", padx=12, pady=12)

        # Skip this step
        self._skip_btn = ctk.CTkButton(bottom, text="Skip", width=70, height=30,
                                        fg_color=BG, hover_color=BG2, text_color=DIM,
                                        command=self._skip_step)
        self._skip_btn.pack(side="right", padx=(0, 4), pady=12)

    def _show_step(self):
        total = len(self.STEPS)
        step = self.STEPS[self._step]
        self._progress.set((self._step + 1) / total)
        self._step_label.configure(text=f"Step {self._step + 1} of {total}")
        self._title.configure(text=step["title"])

        # If system detection ran, update Ollama step to reflect detected model
        desc = step["desc"]
        if step["title"] == "Ollama Setup" and self._detected_profile:
            p = self._detected_profile
            if p["eco_mode"]:
                desc = (
                    "Ollama is the local AI engine that powers your fleet workers.\n\n"
                    f"Based on your system ({p['ram_gb']} GB RAM, "
                    f"{'no GPU' if not p['gpu_name'] else p['gpu_name']}), "
                    f"Eco Mode has been enabled with {p['model']} as the default model.\n\n"
                    "Dr. Ders, the hardware supervisor, monitors your system in real-time "
                    "and will scale between model tiers automatically if needed.\n\n"
                    "You can adjust these settings later in fleet.toml."
                )
            elif p["tier"] != "full":
                desc = (
                    "Ollama is the local AI engine that powers your fleet workers.\n\n"
                    f"Based on your system ({p['ram_gb']} GB RAM, "
                    f"{p['gpu_name']} with {p['vram_gb']} GB VRAM), "
                    f"the default model has been set to {p['model']}.\n\n"
                    "Dr. Ders, the hardware supervisor, monitors your GPU in real-time "
                    "and automatically scales between model tiers if needed.\n\n"
                    "You can adjust these settings later in fleet.toml."
                )
        self._desc.configure(text=desc)

        # Detection result area
        if step.get("has_auto_detect") and self._detected_profile:
            self._detect_result.pack(padx=20, pady=(6, 0), anchor="w")
        else:
            self._detect_result.pack_forget()

        # Action button
        if "action_label" in step:
            self._action_btn.configure(text=step["action_label"])
            self._action_btn.pack(padx=20, pady=(8, 0), anchor="w")
        else:
            self._action_btn.pack_forget()

        # Last step: change "Next" to "Finish"
        if self._step == total - 1:
            self._next_btn.configure(text="Finish ✓")
            self._skip_btn.pack_forget()
        else:
            self._next_btn.configure(text="Next →")

    def _next(self):
        if self._step >= len(self.STEPS) - 1:
            self._finish()
        else:
            self._step += 1
            self._show_step()

    def _skip_step(self):
        self._skipped.append(self._step + 1)
        self._next()

    def _skip_all(self):
        self._skipped.extend(range(self._step + 1, len(self.STEPS) + 1))
        self._finish()

    def _on_action(self):
        step = self.STEPS[self._step]
        if step.get("action_label") == "Open Key Manager":
            try:
                KeyManagerDialog(self._parent)
            except Exception:
                pass
        elif step.get("action_label") == "Detect & Adjust":
            self._run_auto_detect()

    def _run_auto_detect(self):
        """Run hardware detection and show results."""
        self._action_btn.configure(state="disabled", text="Detecting...")
        self.update_idletasks()
        try:
            profile = _detect_system_profile()
            self._detected_profile = profile

            gpu_str = f"{profile['gpu_name']} ({profile['vram_gb']} GB VRAM)" if profile["gpu_name"] else "None detected"
            tier_labels = {"minimal": "Minimal", "light": "Light", "standard": "Standard", "full": "Full"}

            result_text = (
                f"  RAM: {profile['ram_gb']} GB  |  CPU: {profile['cpu_cores']} cores  |  GPU: {gpu_str}\n"
                f"  Tier: {tier_labels.get(profile['tier'], profile['tier'])}\n"
                f"  Model: {profile['model']}  |  Workers: {profile['max_workers']}"
                f"  |  Eco: {'ON' if profile['eco_mode'] else 'OFF'}"
            )

            applied = _apply_system_profile(profile)
            if applied:
                result_text += "\n  Settings applied to fleet.toml"
                self._detect_result.configure(text=result_text, text_color=GREEN)
            else:
                result_text += "\n  Could not write to fleet.toml — apply manually"
                self._detect_result.configure(text=result_text, text_color=ORANGE)

            self._detect_result.pack(padx=20, pady=(6, 0), anchor="w")
            self._action_btn.configure(text="Re-detect", state="normal")
        except Exception as e:
            self._detect_result.configure(
                text=f"  Detection failed: {e}", text_color=RED)
            self._detect_result.pack(padx=20, pady=(6, 0), anchor="w")
            self._action_btn.configure(text="Retry", state="normal")

    def _finish(self):
        if self._no_show_var.get():
            self._persist_completed()
        self.destroy()

    def _persist_completed(self):
        """Write [walkthrough] completed = true to fleet.toml."""
        try:
            from datetime import datetime
            doc = tomlkit.parse(FLEET_TOML.read_text(encoding="utf-8"))
            now = datetime.now().isoformat(timespec="seconds")
            wt = doc.setdefault("walkthrough", {})
            wt["completed"] = True
            wt["skipped_steps"] = list(self._skipped) if self._skipped else []
            wt["completed_at"] = now
            FLEET_TOML.write_text(tomlkit.dumps(doc), encoding="utf-8")
        except Exception:
            pass


def _should_show_walkthrough() -> bool:
    """Check if walkthrough should be shown on launch."""
    try:
        import tomllib
        with open(FLEET_TOML, "rb") as f:
            data = tomllib.load(f)
        return not data.get("walkthrough", {}).get("completed", False)
    except Exception:
        return True


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
        if _GPU_OK:
            mem = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
            temp = pynvml.nvmlDeviceGetTemperature(_GPU_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
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
