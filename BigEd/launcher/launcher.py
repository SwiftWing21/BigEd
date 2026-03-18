"""
BigEd CC — GUI launcher for the Education agent fleet.
Dark mode, brick theme. Runs WSL commands via wsl.exe.
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
import urllib.request
from pathlib import Path

import tkinter as tk
import customtkinter as ctk
from PIL import Image, ImageTk
import psutil

# GPU via pynvml (NVIDIA); graceful fallback if unavailable
try:
    import pynvml
    pynvml.nvmlInit()
    _GPU_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
    _GPU_OK = True
except Exception:
    _GPU_OK = False

# ─── Paths ────────────────────────────────────────────────────────────────────
FLEET_DIR    = Path(r"C:\Users\max\Projects\Education\fleet")
STATUS_MD    = FLEET_DIR / "STATUS.md"
FLEET_TOML   = FLEET_DIR / "fleet.toml"
LOGS_DIR     = FLEET_DIR / "logs"
HW_STATE_JSON = FLEET_DIR / "hw_state.json"
PENDING_DIR  = FLEET_DIR / "knowledge" / "security" / "pending"
REPORTS_DIR  = FLEET_DIR / "knowledge" / "reports"
LEADS_DIR    = FLEET_DIR / "knowledge" / "leads"
DATA_DIR     = Path(__file__).parent / "data"
DB_PATH      = DATA_DIR / "tools.db"

# PyInstaller bundles assets into sys._MEIPASS; fall back to script dir
if getattr(sys, "frozen", False):
    HERE     = Path(sys._MEIPASS)
    _SRC_DIR  = Path(sys.executable).parent.parent   # launcher/
    _DIST_DIR = Path(sys.executable).parent          # dist/
else:
    HERE     = Path(__file__).parent
    _SRC_DIR  = Path(__file__).parent
    _DIST_DIR = Path(__file__).parent / "dist"

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

BG       = "#1a1a1a"
BG2      = "#242424"
BG3      = "#2d2d2d"
ACCENT   = "#b22222"
ACCENT_H = "#8b0000"
GOLD     = "#c8a84b"
TEXT     = "#e2e2e2"
DIM      = "#888888"
GREEN    = "#4caf50"
ORANGE   = "#ff9800"
RED      = "#f44336"
MONO     = ("Consolas", 11)
FONT     = ("Segoe UI", 11)
FONT_SM  = ("Segoe UI", 10)
FONT_H   = ("Segoe UI", 13, "bold")


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


# ─── WSL helpers ──────────────────────────────────────────────────────────────
def wsl(cmd: str, capture=False, timeout=60):
    """Run a bash command in WSL Ubuntu."""
    full = f'source ~/.secrets 2>/dev/null; cd /mnt/c/Users/max/Projects/Education/fleet; {cmd}'
    args = ["wsl", "-d", "Ubuntu", "/bin/bash", "-lc", full]
    if capture:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return r.stdout.strip(), r.stderr.strip()
    else:
        subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW)
        return "", ""


def wsl_bg(cmd: str, callback=None, timeout=60):
    """Run WSL command in a thread; call callback(stdout, stderr) when done."""
    def _run():
        try:
            out, err = wsl(cmd, capture=True, timeout=timeout)
        except Exception as e:
            out, err = "", str(e)
        if callback:
            callback(out, err)
    threading.Thread(target=_run, daemon=True).start()


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


# ─── Status parser ────────────────────────────────────────────────────────────
def parse_status():
    """Read STATUS.md and return dict with agents + task counts."""
    result = {"agents": [], "tasks": {}, "raw": "", "supervisor_status": "OFFLINE", "hw_supervisor_status": "OFFLINE"}
    
    if HW_STATE_JSON.exists():
        try:
            mtime = HW_STATE_JSON.stat().st_mtime
            age = time.time() - mtime
            if age < 30:
                hw_data = json.loads(HW_STATE_JSON.read_text(encoding="utf-8"))
                if hw_data.get("status") == "transitioning":
                    result["hw_supervisor_status"] = "TRANSIT"
                else:
                    result["hw_supervisor_status"] = "ONLINE"
            elif age < 120:
                result["hw_supervisor_status"] = "HUNG"
            else:
                result["hw_supervisor_status"] = "OFFLINE"
        except Exception:
            result["hw_supervisor_status"] = "OFFLINE"

    if not STATUS_MD.exists():
        return result
    try:
        mtime = STATUS_MD.stat().st_mtime
        age = time.time() - mtime
        if age < 30:
            result["supervisor_status"] = "ONLINE"
        elif age < 120:
            result["supervisor_status"] = "HUNG"
        else:
            result["supervisor_status"] = "OFFLINE"
            
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
            if in_agents and line.startswith("|") and not line.startswith("| Name"):
                parts = [p.strip() for p in line.split("|")[1:-1]]
                if len(parts) >= 3:
                    result["agents"].append({
                        "name": parts[0], "role": parts[1], "status": parts[2]
                    })
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
    f = LOGS_DIR / f"{agent}.log"
    if not f.exists():
        return f"[no log: {agent}.log]"
    try:
        lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(lines[-n:])
    except Exception as e:
        return f"[read error: {e}]"


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
    net_str = "ETH —"
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
            net_str = f"ETH  ↑{fmt(tx)}  ↓{fmt(rx)}"

    new_prev = {name: c for name, c in counters.items()}
    return cpu_str, ram_str, gpu_str, net_str, new_prev, now


def count_pending_advisories() -> int:
    if not PENDING_DIR.exists():
        return 0
    return len(list(PENDING_DIR.glob("advisory_*.md")))


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


# ─── Main App ─────────────────────────────────────────────────────────────────
class BigEdCC(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("BigEd CC")
        self.geometry("1050x960")
        self.minsize(800, 720)
        self.configure(fg_color=BG)

        # Load agent name theme + custom names
        global _active_theme, _custom_names
        _active_theme = _load_theme_preference()
        _custom_names = _load_custom_names()

        self._net_prev    = None
        self._net_time    = None
        self._ollama_up   = None   # None = unknown, True/False after first check
        self._ollama_restart_count = 0  # cap auto-restarts to 3
        self._system_running           = False
        self._system_intentional_stop  = False
        self._last_keepalive = 0.0  # epoch time of last keepalive ping
        self._sidebar_visible = True
        # Activity sparkline: per-agent rolling history (last 10 samples @ 1s each)
        self._agent_activity = {}  # role -> deque of booleans (True=BUSY)
        psutil.cpu_percent(interval=None)  # prime the cpu sampler

        self._set_icon()
        self._build_ui()
        self._current_log_agent = "supervisor"
        self._refresh_status()
        self._schedule_refresh()
        self._schedule_agent_tick()
        self._schedule_hw()
        self._schedule_ollama_watch()
        threading.Thread(target=self._check_for_updates, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        """Ask whether to stop background processes before exiting."""
        dlg = ctk.CTkToplevel(self)
        dlg.title("Close BigEd CC")
        dlg.geometry("380x160")
        dlg.resizable(False, False)
        dlg.configure(fg_color=BG2)
        dlg.grab_set()
        dlg.lift()

        ctk.CTkLabel(dlg, text="Stop fleet + Ollama on exit?",
                     font=("Segoe UI", 13, "bold"), text_color=GOLD).pack(pady=(22, 6))
        ctk.CTkLabel(dlg, text="Leave running to continue working in the background.",
                     font=("Segoe UI", 10), text_color=DIM).pack()

        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(side="bottom", fill="x", padx=20, pady=16)

        def _stop_and_close():
            dlg.destroy()
            self._stop_system()
            self.after(2000, self.destroy)

        def _just_close():
            dlg.destroy()
            self.destroy()

        ctk.CTkButton(btn_row, text="Stop & Exit", width=110, height=32,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      command=_stop_and_close).pack(side="right")
        ctk.CTkButton(btn_row, text="Exit (keep running)", width=140, height=32,
                      fg_color=BG3, hover_color=BG,
                      command=_just_close).pack(side="right", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Cancel", width=80, height=32,
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
        hdr = ctk.CTkFrame(self, fg_color=BG3, height=44, corner_radius=0)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(3, weight=1)

        banner = self._load_banner()
        if banner:
            ctk.CTkLabel(hdr, image=banner, text="").grid(
                row=0, column=0, padx=(10, 2), pady=2)
        else:
            ctk.CTkLabel(hdr, text="🧱", font=("Segoe UI", 22)).grid(
                row=0, column=0, padx=(10, 2), pady=2)

        self._sidebar_btn = ctk.CTkButton(
            hdr, text="≡", font=("Segoe UI", 16), width=30, height=30,
            fg_color="transparent", hover_color=BG2, text_color=TEXT,
            command=self._toggle_sidebar
        )
        self._sidebar_btn.grid(row=0, column=1, padx=(2, 6))

        ctk.CTkLabel(hdr, text="BIGED CC",
                     font=("Segoe UI", 14, "bold"),
                     text_color=GOLD).grid(row=0, column=2, padx=4, sticky="w")

        # Inline stats
        stats_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        stats_frame.grid(row=0, column=3, sticky="ew", padx=(8, 0))
        stats_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)
        kw = dict(font=("Consolas", 9), text_color=DIM)
        self._stat_cpu = ctk.CTkLabel(stats_frame, text="CPU —", anchor="w", **kw)
        self._stat_ram = ctk.CTkLabel(stats_frame, text="RAM —", anchor="w", **kw)
        self._stat_gpu = ctk.CTkLabel(stats_frame, text="GPU —", anchor="w", **kw)
        self._stat_net = ctk.CTkLabel(stats_frame, text="ETH —", anchor="w", **kw)
        self._stat_cpu.grid(row=0, column=0, padx=4, sticky="ew")
        self._stat_ram.grid(row=0, column=1, padx=4, sticky="ew")
        self._stat_gpu.grid(row=0, column=2, padx=4, sticky="ew")
        self._stat_net.grid(row=0, column=3, padx=4, sticky="ew")

        self._status_pills = ctk.CTkLabel(
            hdr, text="● loading...", font=("Consolas", 9), text_color=DIM)
        self._status_pills.grid(row=0, column=4, padx=8, sticky="e")

        self._advisory_badge = ctk.CTkLabel(
            hdr, text="", font=("Segoe UI", 9, "bold"),
            text_color="#1a1a1a", fg_color=ORANGE,
            corner_radius=8, width=0)
        self._advisory_badge.grid(row=0, column=5, padx=(0, 4), pady=10)

        self._update_badge = ctk.CTkButton(
            hdr, text="", font=("Segoe UI", 9, "bold"),
            text_color=TEXT, fg_color="transparent",
            hover_color="#2a4a2a", corner_radius=8, width=0,
            command=self._launch_auto_update)
        self._update_badge.grid(row=0, column=6, padx=(0, 8), pady=10)

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
                sb, text=label, font=FONT_SM, height=30,
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
        btn(s, "↺  Recover All", self._recover_all, "#2a2a10", "#3a3a18",
            tip="Kill and cleanly restart Ollama + supervisor + all workers")

        # ── SECURITY ──────────────────────────────────────────────────────────
        s = section("SECURITY")
        btn(s, "🔍 Audit",      self._run_audit,
            tip="Queue a security audit across all fleet skills and configs")
        btn(s, "🌐 Pen Test",   self._run_pentest,
            tip="Run a network service scan against the local environment")
        btn(s, "📂 Advisories", self._open_advisories,
            tip="View and apply pending security advisories")

        # ── RESEARCH ──────────────────────────────────────────────────────────
        s = section("RESEARCH")
        btn(s, "🔎 Web Search",    self._open_search_dialog,
            tip="Dispatch a web search task to the researcher worker")
        btn(s, "📊 Results",       self._show_results,
            tip="View autoresearch training experiment results")
        self._btn_marathon = btn(s, "🏃 Marathon",  self._start_marathon,
            tip="Start an 8-hour discussion + lead research + synthesis run")
        btn(s, "📋 Marathon Log",  self._show_marathon_log,
            tip="Tail marathon.log — see current phase and last output")
        btn(s, "⏹  Stop Marathon", self._stop_marathon, "#2a1a1a", "#3a2020",
            tip="Kill the running marathon process")
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
        s = section("IDLE MODE", default_open=False)
        self._idle_enabled = False
        self._btn_idle_toggle = btn(s, "✅ Enable Idle", self._toggle_idle,
                                    "#1e2e1e", "#2a3e2a",
                                    tip="Allow workers to run background curriculum tasks when idle")

        # ── SETTINGS (single entry point) ──────────────────────────────────
        s = section("CONFIG")
        btn(s, "⚙  Settings",       self._open_settings,
            tip="Open the unified settings panel")

        # ── CONSOLES ─────────────────────────────────────────────────────────
        s = section("CONSOLES", default_open=False)
        btn(s, "🤖 Claude Console", self._open_claude_console, "#1a1a2e", "#252540",
            tip="Open an interactive Claude API chat with fleet dispatch support")
        btn(s, "✦  Gemini Console", self._open_gemini_console, "#1a2a1a", "#253525",
            tip="Open an interactive Gemini chat with fleet dispatch support")
        btn(s, "⚡ Local Console",  self._open_local_console, "#2a2010", "#3a3020",
            tip="Open an interactive Ollama chat — free, no API key needed")

        # ── BUILD ──────────────────────────────────────────────────────────────
        s = section("BUILD", default_open=False)
        btn(s, "🔄 Run Update",        self._launch_auto_update, "#1a3a1a", "#2a4a2a",
            tip="Run Updater.exe in auto mode and relaunch BigEd CC")
        btn(s, "▶  Run BigEd CC", self._run_fleet_control,  "#1a2a10", "#2a3a18",
            tip="Launch the compiled BigEd CC from dist/")
        btn(s, "🔨 Rebuild All",       self._rebuild_all,        "#2a1a10", "#3a2a18",
            tip="Recompile the app via PyInstaller (build.bat)")

        # ── LOGS ──────────────────────────────────────────────────────────────
        s = section("LOGS")
        agents = ["supervisor", "hw_supervisor", "researcher", "security", "sales",
                  "analyst", "archivist", "onboarding", "implementation", "planner"]
        self._log_agent_var = ctk.StringVar(value="supervisor")
        menu = ctk.CTkOptionMenu(
            sb, values=agents, variable=self._log_agent_var,
            font=FONT_SM, fg_color=BG3, button_color=ACCENT,
            button_hover_color=ACCENT_H, height=28, command=self._switch_log,
        )
        menu.pack(fill="x", padx=10, pady=4)
        s["widgets"].append(menu)

    # ── Main area ─────────────────────────────────────────────────────────────
    # ── Tabs (primary content area) ──────────────────────────────────────────
    def _build_tabs(self):
        self._db_init()

        tabs = ctk.CTkTabview(
            self,
            fg_color=BG,
            segmented_button_fg_color=BG2,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_H,
            segmented_button_unselected_color=BG3,
            segmented_button_unselected_hover_color=BG2,
            text_color=TEXT,
            corner_radius=0,
        )
        tabs.grid(row=1, column=1, sticky="nsew", padx=0, pady=0)

        for name in ("Command Center", "Agents", "CRM",
                     "Onboarding", "Customers", "Accounts", "Ingestion", "Outputs"):
            tabs.add(name)

        self._build_tab_cc(tabs.tab("Command Center"))
        self._build_tab_agents(tabs.tab("Agents"))
        self._build_tab_crm(tabs.tab("CRM"))
        self._build_tab_onboarding(tabs.tab("Onboarding"))
        self._build_tab_customers(tabs.tab("Customers"))
        self._build_tab_accounts(tabs.tab("Accounts"))
        self._build_tab_ingest(tabs.tab("Ingestion"))
        self._build_tab_outputs(tabs.tab("Outputs"))
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

        self._hw_sup_status_lbl = ctk.CTkLabel(ag_hdr, text="HW Sup: —", font=("Consolas", 9, "bold"), text_color=DIM)
        self._hw_sup_status_lbl.grid(row=0, column=2, padx=8, pady=(4, 2), sticky="e")

        self._agents_frame_inner = ctk.CTkFrame(agents_frame, fg_color=BG2)
        self._agents_frame_inner.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))

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
            log_frame, text="LOG — supervisor", font=("Segoe UI", 9, "bold"),
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

    # ── Tab 1: Agents ─────────────────────────────────────────────────────────
    def _build_tab_agents(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        # Header row
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="Fleet workers — internal team & customer instances",
                     font=FONT_SM, text_color=DIM).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text="＋ Add Instance", font=FONT_SM, height=26,
                      width=110, fg_color=BG3, hover_color=BG,
                      command=self._agents_add_dialog
                      ).grid(row=0, column=2, sticky="e")

        # Scrollable agent list
        self._agents_scroll = ctk.CTkScrollableFrame(
            parent, fg_color=BG2, corner_radius=4)
        self._agents_scroll.grid(row=1, column=0, sticky="nsew")
        self._agents_scroll.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

        # Column headers
        for col, (txt, anchor) in enumerate([
            ("Name", "w"), ("Role", "w"), ("Type", "center"),
            ("Status", "center"), ("", "center"),
        ]):
            ctk.CTkLabel(self._agents_scroll, text=txt, font=("Segoe UI", 9, "bold"),
                         text_color=DIM, anchor=anchor
                         ).grid(row=0, column=col, padx=6, pady=(2, 4), sticky="ew")

        self._agents_rows = []
        self._agents_tab_refresh()

    def _agents_tab_refresh(self):
        for w in self._agents_rows:
            w.destroy()
        self._agents_rows.clear()

        status = parse_status()
        agents = status.get("agents", [])
        con = self._db_conn()
        rows = con.execute("SELECT name, role, type, customer, notes FROM agents").fetchall()
        con.close()
        stored = [dict(r) for r in rows]
        # Merge fleet DB agents + stored custom instances
        seen = {a["name"] for a in agents}
        all_agents = list(agents) + [a for a in stored if a["name"] not in seen]

        for i, ag in enumerate(all_agents):
            row = i + 1
            bg = BG3 if i % 2 == 0 else BG2
            name = ag.get("name", "—")
            role = ag.get("role", "—")
            ag_type = ag.get("type", "Internal")
            st = ag.get("status", "—")
            st_color = GREEN if st == "IDLE" else ORANGE if st == "BUSY" else RED

            widgets = []
            for col, (txt, anchor, color) in enumerate([
                (name, "w", TEXT),
                (role, "w", DIM),
                (ag_type, "center", GOLD if ag_type != "Internal" else DIM),
                (st, "center", st_color),
            ]):
                lbl = ctk.CTkLabel(self._agents_scroll, text=txt, font=FONT_SM,
                                   text_color=color, anchor=anchor, fg_color=bg)
                lbl.grid(row=row, column=col, padx=6, pady=2, sticky="ew")
                widgets.append(lbl)

            edit_btn = ctk.CTkButton(
                self._agents_scroll, text="✎", font=FONT_SM,
                width=28, height=22, fg_color=bg, hover_color=BG3,
                command=lambda a=ag: self._agents_edit_dialog(a))
            edit_btn.grid(row=row, column=4, padx=4, pady=2)
            widgets.append(edit_btn)
            self._agents_rows.extend(widgets)

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

    # ── Tab 2: CRM ────────────────────────────────────────────────────────────
    def _build_tab_crm(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        hdr.grid_columnconfigure(0, weight=1)

        self._crm_search_var = ctk.StringVar()
        self._crm_search_var.trace_add("write", lambda *_: self._crm_refresh())
        ctk.CTkEntry(hdr, textvariable=self._crm_search_var, font=FONT_SM,
                     fg_color=BG2, border_color="#444", text_color=TEXT,
                     placeholder_text="Search companies / contacts..."
                     ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(hdr, text="＋ Add", font=FONT_SM, height=26, width=70,
                      fg_color=BG3, hover_color=BG,
                      command=self._crm_add_dialog
                      ).grid(row=0, column=1, sticky="e", padx=(0, 4))
        ctk.CTkButton(hdr, text="🔍 Prospect", font=FONT_SM, height=26, width=90,
                      fg_color="#1a2a3a", hover_color="#253545",
                      command=self._crm_prospect_dialog
                      ).grid(row=0, column=2, sticky="e", padx=(0, 4))
        ctk.CTkButton(hdr, text="📥 Import Leads", font=FONT_SM, height=26, width=110,
                      fg_color="#1a3a1a", hover_color="#253a25",
                      command=self._crm_import_leads_dialog
                      ).grid(row=0, column=3, sticky="e")

        # Scrollable list
        self._crm_scroll = ctk.CTkScrollableFrame(parent, fg_color=BG2, corner_radius=4)
        self._crm_scroll.grid(row=1, column=0, sticky="nsew")
        self._crm_scroll.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

        for col, txt in enumerate(["Company", "Industry", "Contact", "Stage", ""]):
            ctk.CTkLabel(self._crm_scroll, text=txt, font=("Segoe UI", 9, "bold"),
                         text_color=DIM, anchor="w"
                         ).grid(row=0, column=col, padx=6, pady=(2, 4), sticky="ew")

        self._crm_rows = []
        self._crm_refresh()

    def _crm_refresh(self):
        for w in self._crm_rows:
            w.destroy()
        self._crm_rows.clear()

        query = self._crm_search_var.get().lower() if hasattr(self, "_crm_search_var") else ""
        con = self._db_conn()
        rows = con.execute(
            "SELECT company, industry, contact, email, phone, stage, notes FROM crm").fetchall()
        con.close()
        records = [dict(r) for r in rows]
        STAGE_COLORS = {
            "Lead": DIM, "Prospect": ORANGE, "Active": GREEN,
            "Churned": RED, "Partner": GOLD,
        }
        displayed = 0
        for rec in records:
            if query and not any(query in str(v).lower() for v in rec.values()):
                continue
            row = displayed + 1
            bg = BG3 if displayed % 2 == 0 else BG2
            displayed += 1
            stage = rec.get("stage", "Lead")
            for col, (key, anchor) in enumerate([
                ("company", "w"), ("industry", "w"),
                ("contact", "w"), ("stage", "center"),
            ]):
                txt = rec.get(key, "—")
                color = STAGE_COLORS.get(txt, DIM) if key == "stage" else TEXT
                lbl = ctk.CTkLabel(self._crm_scroll, text=txt, font=FONT_SM,
                                   text_color=color, anchor=anchor, fg_color=bg)
                lbl.grid(row=row, column=col, padx=6, pady=2, sticky="ew")
                self._crm_rows.append(lbl)

            btn = ctk.CTkButton(self._crm_scroll, text="✎", font=FONT_SM,
                                width=28, height=22, fg_color=bg, hover_color=BG3,
                                command=lambda r=rec: self._crm_edit_dialog(r))
            btn.grid(row=row, column=4, padx=4, pady=2)
            self._crm_rows.append(btn)

    def _crm_add_dialog(self):
        self._crm_edit_dialog({})

    def _crm_edit_dialog(self, rec: dict):
        win = ctk.CTkToplevel(self)
        win.title("CRM — Contact")
        win.geometry("400x340")
        win.configure(fg_color=BG)
        win.grab_set()

        fields = [
            ("Company",  rec.get("company", "")),
            ("Industry", rec.get("industry", "")),
            ("Contact",  rec.get("contact", "")),
            ("Email",    rec.get("email", "")),
            ("Phone",    rec.get("phone", "")),
            ("Stage",    rec.get("stage", "Lead")),
            ("Notes",    rec.get("notes", "")),
        ]
        entries = {}
        for i, (lbl, val) in enumerate(fields):
            ctk.CTkLabel(win, text=lbl, font=FONT_SM, text_color=DIM,
                         anchor="w").grid(row=i, column=0, padx=(14, 6), pady=3, sticky="w")
            e = ctk.CTkEntry(win, font=FONT_SM, fg_color=BG2,
                             border_color="#444", text_color=TEXT)
            e.insert(0, val)
            e.grid(row=i, column=1, padx=(0, 14), pady=3, sticky="ew")
            entries[lbl] = e
        win.grid_columnconfigure(1, weight=1)

        def _save():
            new = {k.lower(): v.get() for k, v in entries.items()}
            con = self._db_conn()
            con.execute("DELETE FROM crm WHERE company=?", (rec.get("company", ""),))
            if new.get("company"):
                con.execute(
                    "INSERT INTO crm (company, industry, contact, email, phone, stage, notes)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (new.get("company", ""), new.get("industry", ""), new.get("contact", ""),
                     new.get("email", ""), new.get("phone", ""),
                     new.get("stage", "Lead"), new.get("notes", "")))
            con.commit()
            con.close()
            self._crm_refresh()
            win.destroy()

        ctk.CTkButton(win, text="Save", font=FONT_SM, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_H, command=_save
                      ).grid(row=len(fields), column=0, columnspan=2,
                             padx=14, pady=(10, 14), sticky="ew")

    # ── CRM Prospecting ───────────────────────────────────────────────────────
    def _crm_prospect_dialog(self):
        """Dispatch a lead_research fleet task from the CRM tab."""
        win = ctk.CTkToplevel(self)
        win.title("Prospect — Find Leads")
        win.geometry("360x260")
        win.configure(fg_color=BG)
        win.grab_set()

        fields = [
            ("Industry",  "healthcare"),
            ("City",      "Watsonville CA"),
            ("Zip Code",  "95076"),
        ]
        entries = {}
        for i, (lbl, placeholder) in enumerate(fields):
            ctk.CTkLabel(win, text=lbl, font=FONT_SM, text_color=DIM,
                         anchor="w").grid(row=i, column=0, padx=(14, 6), pady=4, sticky="w")
            e = ctk.CTkEntry(win, font=FONT_SM, fg_color=BG2, border_color="#444",
                             text_color=TEXT, placeholder_text=placeholder)
            e.grid(row=i, column=1, padx=(0, 14), pady=4, sticky="ew")
            entries[lbl] = e
        win.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(win, text="Dispatches to sales agent via fleet task queue.",
                     font=("Segoe UI", 9), text_color=DIM, wraplength=300
                     ).grid(row=len(fields), column=0, columnspan=2, padx=14, pady=(6, 2))
        ctk.CTkLabel(win, text='Results → Import Leads after task completes.',
                     font=("Segoe UI", 9), text_color=DIM, wraplength=300
                     ).grid(row=len(fields)+1, column=0, columnspan=2, padx=14, pady=(0, 6))

        def _dispatch():
            industry = entries["Industry"].get().strip() or "healthcare"
            city     = entries["City"].get().strip() or "Watsonville CA"
            zip_code = entries["Zip Code"].get().strip() or "95076"
            payload_json = json.dumps(
                {"industry": industry, "city": city, "zip_code": zip_code})
            b64 = base64.b64encode(payload_json.encode()).decode()
            cmd = (
                f'~/.local/bin/uv run python -c "'
                f'import sys,base64; sys.path.insert(0,"."); import db; db.init_db(); '
                f'p=base64.b64decode("{b64}").decode(); '
                f'tid=db.post_task(\\"lead_research\\",p,priority=8,'
                f'assigned_to=\\"sales\\"); print(\\"Lead research task\\",tid,\\"queued\\")"'
            )
            wsl_bg(cmd, lambda o, e: self.after(0, lambda: self._log_output(o or e or "Prospecting task queued")))
            self._log_output(f"Prospecting: {industry} / {city} {zip_code}")
            win.destroy()

        ctk.CTkButton(win, text="🔍 Dispatch", font=FONT_SM, height=30,
                      fg_color="#1a2a3a", hover_color="#253545", command=_dispatch
                      ).grid(row=len(fields)+2, column=0, columnspan=2,
                             padx=14, pady=(6, 14), sticky="ew")

    def _crm_import_leads_dialog(self):
        """Read knowledge/leads/*.jsonl and let user bulk-import into CRM."""
        leads_dir = LEADS_DIR
        jsonl_files = sorted(leads_dir.glob("*.jsonl"), reverse=True)

        raw_leads = []
        for f in jsonl_files[:5]:  # last 5 files
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        raw_leads.append(json.loads(line))
            except Exception:
                continue

        if not raw_leads:
            self._log_output("No lead files found in knowledge/leads/. Run Prospect first.")
            return

        # Dedupe by title
        seen_titles = set()
        leads = []
        for r in raw_leads:
            t = r.get("title", "").strip()
            if t and t not in seen_titles:
                seen_titles.add(t)
                leads.append(r)

        win = ctk.CTkToplevel(self)
        win.title(f"Import Leads ({len(leads)} found)")
        win.geometry("600x480")
        win.configure(fg_color=BG)
        win.grab_set()

        ctk.CTkLabel(win, text="Select leads to import as CRM contacts (stage: Lead):",
                     font=FONT_SM, text_color=DIM
                     ).pack(padx=14, pady=(10, 4), anchor="w")

        scroll = ctk.CTkScrollableFrame(win, fg_color=BG2, corner_radius=4)
        scroll.pack(fill="both", expand=True, padx=10, pady=4)
        scroll.grid_columnconfigure(1, weight=1)

        check_vars = []
        for i, lead in enumerate(leads):
            var = ctk.BooleanVar(value=False)
            check_vars.append((var, lead))
            bg = BG3 if i % 2 == 0 else BG2
            ctk.CTkCheckBox(scroll, text="", variable=var, fg_color=ACCENT,
                            hover_color=ACCENT_H, width=20
                            ).grid(row=i, column=0, padx=(6, 2), pady=2, sticky="w")
            label_text = f"{lead.get('title', '—')}  [{lead.get('sector','?')} · {lead.get('zip','')}]"
            ctk.CTkLabel(scroll, text=label_text, font=FONT_SM, text_color=TEXT,
                         anchor="w", fg_color=bg, wraplength=460
                         ).grid(row=i, column=1, padx=4, pady=2, sticky="ew")

        def _select_all():
            for v, _ in check_vars:
                v.set(True)

        def _do_import():
            selected = [(v, lead) for v, lead in check_vars if v.get()]
            if not selected:
                return
            con = self._db_conn()
            imported = 0
            for _, lead in selected:
                title   = lead.get("title", "").strip()
                sector  = lead.get("sector", "")
                snippet = lead.get("snippet", "")
                url     = lead.get("url", "")
                notes   = f"{snippet}\n{url}".strip() if snippet or url else ""
                if not title:
                    continue
                try:
                    con.execute(
                        "INSERT OR IGNORE INTO crm (company, industry, stage, notes)"
                        " VALUES (?,?,?,?)",
                        (title, sector, "Lead", notes))
                    imported += 1
                except Exception:
                    pass
            con.commit()
            con.close()
            self._crm_refresh()
            self._log_output(f"Imported {imported} leads into CRM.")
            win.destroy()

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(4, 10))
        btn_row.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(btn_row, text="Select All", font=FONT_SM, height=28, width=90,
                      fg_color=BG3, hover_color=BG, command=_select_all
                      ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(btn_row, text="📥 Import Selected", font=FONT_SM, height=28,
                      fg_color="#1a3a1a", hover_color="#253a25", command=_do_import
                      ).grid(row=0, column=1, sticky="e")

    # ── Tab 3: Onboarding ─────────────────────────────────────────────────────
    def _build_tab_onboarding(self, parent):
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        # Customer selector
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(4, 6))
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(top, text="Customer:", font=FONT_SM,
                     text_color=DIM).grid(row=0, column=0, padx=(0, 8))

        con = self._db_conn()
        custs = [r[0] for r in con.execute(
            "SELECT DISTINCT customer FROM onboarding ORDER BY customer").fetchall()]
        con.close()
        customers = custs or ["(no customers)"]
        self._ob_customer_var = ctk.StringVar(value=customers[0])
        self._ob_menu = ctk.CTkOptionMenu(
            top, values=customers, variable=self._ob_customer_var,
            font=FONT_SM, fg_color=BG3, button_color=ACCENT,
            button_hover_color=ACCENT_H, height=26, width=160,
            command=lambda _: self._ob_refresh())
        self._ob_menu.grid(row=0, column=1, sticky="w")

        ctk.CTkButton(top, text="＋ Customer", font=FONT_SM, height=26, width=100,
                      fg_color=BG3, hover_color=BG,
                      command=self._ob_add_customer
                      ).grid(row=0, column=2, padx=(8, 0))

        self._ob_progress = ctk.CTkProgressBar(
            top, height=8, corner_radius=4, fg_color=BG3, progress_color=GREEN)
        self._ob_progress.set(0)
        self._ob_progress.grid(row=0, column=3, padx=(12, 0), sticky="ew")
        top.grid_columnconfigure(3, weight=1)

        # Checklist
        self._ob_scroll = ctk.CTkScrollableFrame(parent, fg_color=BG2, corner_radius=4)
        self._ob_scroll.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self._ob_scroll.grid_columnconfigure(1, weight=1)

        self._ob_rows = []
        self._ob_refresh()

    _OB_DEFAULT_STEPS = [
        ("Setup",       ["Create WSL environment", "Install Python + uv", "Clone fleet repo"]),
        ("Config",      ["Set API keys in ~/.secrets", "Configure fleet.toml", "Test Ollama connectivity"]),
        ("Training",    ["Run first autoresearch experiment", "Review results with analyst worker"]),
        ("Go-Live",     ["Start supervisor", "Verify all agents IDLE", "Deliver handoff doc"]),
    ]

    def _ob_refresh(self):
        for w in self._ob_rows:
            w.destroy()
        self._ob_rows.clear()

        customer = self._ob_customer_var.get() if hasattr(self, "_ob_customer_var") else ""
        con = self._db_conn()
        rows = con.execute(
            "SELECT category, step, done FROM onboarding WHERE customer=? ORDER BY id",
            (customer,)).fetchall()
        con.close()
        steps = {}
        for cat, step, done in rows:
            steps.setdefault(cat, {})[step] = bool(done)
        if not steps:
            steps = {cat: {s: False for s in items}
                     for cat, items in self._OB_DEFAULT_STEPS}

        row = 0
        total = done = 0
        for cat, items in steps.items():
            lbl = ctk.CTkLabel(self._ob_scroll, text=cat,
                               font=("Segoe UI", 10, "bold"), text_color=GOLD, anchor="w")
            lbl.grid(row=row, column=0, columnspan=2, padx=6, pady=(8, 2), sticky="w")
            self._ob_rows.append(lbl)
            row += 1
            for step, checked in items.items():
                total += 1
                if checked:
                    done += 1
                var = ctk.BooleanVar(value=checked)

                def _on_toggle(v=var, c=customer, ca=cat, s=step):
                    con = self._db_conn()
                    con.execute(
                        "UPDATE onboarding SET done=? WHERE customer=? AND category=? AND step=?",
                        (int(v.get()), c, ca, s))
                    con.commit()
                    con.close()
                    self._ob_refresh()

                cb = ctk.CTkCheckBox(
                    self._ob_scroll, text=step, variable=var,
                    font=FONT_SM, text_color=TEXT if not checked else DIM,
                    fg_color=ACCENT, hover_color=ACCENT_H,
                    command=_on_toggle)
                cb.grid(row=row, column=1, padx=(20, 6), pady=1, sticky="w")
                self._ob_rows.append(cb)
                row += 1

        if total and hasattr(self, "_ob_progress"):
            self._ob_progress.set(done / total)

    def _ob_add_customer(self):
        win = ctk.CTkToplevel(self)
        win.title("Add Customer")
        win.geometry("300x120")
        win.configure(fg_color=BG)
        win.grab_set()
        ctk.CTkLabel(win, text="Customer name:", font=FONT_SM,
                     text_color=DIM).pack(padx=14, pady=(14, 4), anchor="w")
        entry = ctk.CTkEntry(win, font=FONT_SM, fg_color=BG2,
                             border_color="#444", text_color=TEXT)
        entry.pack(padx=14, fill="x")

        def _add():
            name = entry.get().strip()
            if not name:
                return
            con = self._db_conn()
            exists = con.execute(
                "SELECT 1 FROM onboarding WHERE customer=?", (name,)).fetchone()
            if not exists:
                con.executemany(
                    "INSERT OR IGNORE INTO onboarding (customer, category, step, done)"
                    " VALUES (?,?,?,0)",
                    [(name, cat, step)
                     for cat, items in self._OB_DEFAULT_STEPS for step in items])
            custs = [r[0] for r in con.execute(
                "SELECT DISTINCT customer FROM onboarding ORDER BY customer").fetchall()]
            con.commit()
            con.close()
            self._ob_menu.configure(values=custs)
            self._ob_customer_var.set(name)
            self._ob_refresh()
            win.destroy()

        ctk.CTkButton(win, text="Add", font=FONT_SM, height=28,
                      fg_color=ACCENT, hover_color=ACCENT_H, command=_add
                      ).pack(padx=14, pady=8, fill="x")

    # ── Tab 4: Active Customers ───────────────────────────────────────────────
    def _build_tab_customers(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="Connected deployments — 🔒 = air-gapped / no remote access",
                     font=FONT_SM, text_color=DIM).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text="＋ Add", font=FONT_SM, height=26, width=70,
                      fg_color=BG3, hover_color=BG,
                      command=self._customers_add_dialog
                      ).grid(row=0, column=2, sticky="e")
        ctk.CTkButton(hdr, text="↻ Ping All", font=FONT_SM, height=26, width=80,
                      fg_color=BG3, hover_color=BG,
                      command=self._customers_ping_all
                      ).grid(row=0, column=3, padx=(6, 0), sticky="e")

        self._cust_scroll = ctk.CTkScrollableFrame(parent, fg_color=BG2, corner_radius=4)
        self._cust_scroll.grid(row=1, column=0, sticky="nsew")
        self._cust_scroll.grid_columnconfigure((0, 1, 2, 3, 4, 5), weight=1)

        for col, txt in enumerate(["Customer", "Status", "Last Ping",
                                   "Fleet Ver", "Isolation", ""]):
            ctk.CTkLabel(self._cust_scroll, text=txt, font=("Segoe UI", 9, "bold"),
                         text_color=DIM, anchor="w"
                         ).grid(row=0, column=col, padx=6, pady=(2, 4), sticky="ew")

        self._cust_rows = []
        self._customers_refresh()

    def _customers_refresh(self):
        for w in self._cust_rows:
            w.destroy()
        self._cust_rows.clear()

        con = self._db_conn()
        rows = con.execute(
            "SELECT name, fleet_version, contact, notes, air_gapped, status, last_ping"
            " FROM customers").fetchall()
        con.close()
        records = [dict(r) for r in rows]
        STATUS_COLORS = {"Online": GREEN, "Degraded": ORANGE,
                         "Offline": RED, "Unknown": DIM}
        for i, rec in enumerate(records):
            row = i + 1
            bg = BG3 if i % 2 == 0 else BG2
            st = rec.get("status", "Unknown")
            airgap = rec.get("air_gapped", False)

            cols = [
                (rec.get("name", "—"),        TEXT,                   "w"),
                (f"● {st}",                   STATUS_COLORS.get(st, DIM), "w"),
                (rec.get("last_ping", "—"),   DIM,                    "center"),
                (rec.get("fleet_version", "—"), DIM,                  "center"),
                ("🔒 Air-Gapped" if airgap else "● Connected",
                 ORANGE if airgap else GREEN,  "center"),
            ]
            widgets = []
            for col, (txt, color, anchor) in enumerate(cols):
                lbl = ctk.CTkLabel(self._cust_scroll, text=txt, font=FONT_SM,
                                   text_color=color, anchor=anchor, fg_color=bg)
                lbl.grid(row=row, column=col, padx=6, pady=2, sticky="ew")
                widgets.append(lbl)

            btn = ctk.CTkButton(self._cust_scroll, text="✎", font=FONT_SM,
                                width=28, height=22, fg_color=bg, hover_color=BG3,
                                command=lambda r=rec: self._customers_edit_dialog(r))
            btn.grid(row=row, column=5, padx=4, pady=2)
            widgets.append(btn)
            self._cust_rows.extend(widgets)

    def _customers_add_dialog(self):
        self._customers_edit_dialog({})

    def _customers_edit_dialog(self, rec: dict):
        win = ctk.CTkToplevel(self)
        win.title("Customer Deployment")
        win.geometry("420x340")
        win.configure(fg_color=BG)
        win.grab_set()

        fields = [
            ("Name",          rec.get("name", "")),
            ("Fleet Version", rec.get("fleet_version", "")),
            ("Contact",       rec.get("contact", "")),
            ("Notes",         rec.get("notes", "")),
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

        row_idx = len(fields)
        airgap_var = ctk.BooleanVar(value=rec.get("air_gapped", False))
        ctk.CTkCheckBox(win, text="🔒 Air-Gapped (no remote access)",
                        variable=airgap_var, font=FONT_SM, text_color=TEXT,
                        fg_color=ORANGE, hover_color="#cc7700"
                        ).grid(row=row_idx, column=0, columnspan=2,
                               padx=14, pady=6, sticky="w")

        def _save():
            new = {k.lower().replace(" ", "_"): v.get() for k, v in entries.items()}
            con = self._db_conn()
            con.execute("DELETE FROM customers WHERE name=?", (rec.get("name", ""),))
            if new.get("name"):
                con.execute(
                    "INSERT INTO customers"
                    " (name, fleet_version, contact, notes, air_gapped, status, last_ping)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (new.get("name", ""), new.get("fleet_version", ""),
                     new.get("contact", ""), new.get("notes", ""),
                     int(airgap_var.get()),
                     rec.get("status", "Unknown"), rec.get("last_ping", "—")))
            con.commit()
            con.close()
            self._customers_refresh()
            win.destroy()

        ctk.CTkButton(win, text="Save", font=FONT_SM, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_H, command=_save
                      ).grid(row=row_idx + 1, column=0, columnspan=2,
                             padx=14, pady=(10, 14), sticky="ew")

    def _customers_ping_all(self):
        """Placeholder — future: ping each non-air-gapped customer fleet endpoint."""
        self._log_output("Ping All: not yet implemented — will hit each customer fleet API.")

    # ── Tab 5: Accounts ────────────────────────────────────────────────────────
    def _build_tab_accounts(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="Service accounts — usage vs free tier limits",
                     font=FONT_SM, text_color=DIM).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text="＋ Add", font=FONT_SM, height=26, width=70,
                      fg_color=BG3, hover_color=BG,
                      command=self._accounts_add_dialog
                      ).grid(row=0, column=2, sticky="e")
        ctk.CTkButton(hdr, text="Review Upgrades", font=FONT_SM, height=26, width=120,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self._accounts_review_dispatch
                      ).grid(row=0, column=3, padx=(6, 0), sticky="e")

        self._acct_scroll = ctk.CTkScrollableFrame(parent, fg_color=BG2, corner_radius=4)
        self._acct_scroll.grid(row=1, column=0, sticky="nsew")
        self._acct_scroll.grid_columnconfigure((0, 1, 2, 3, 4, 5, 6), weight=1)

        for col, txt in enumerate(["Service", "Category", "Tier", "Usage",
                                   "Free Limit", "Cost/mo", ""]):
            ctk.CTkLabel(self._acct_scroll, text=txt, font=("Segoe UI", 9, "bold"),
                         text_color=DIM, anchor="w"
                         ).grid(row=0, column=col, padx=6, pady=(2, 4), sticky="ew")

        self._acct_rows = []
        self._accounts_refresh()

    def _accounts_refresh(self):
        for w in self._acct_rows:
            w.destroy()
        self._acct_rows.clear()

        con = self._db_conn()
        rows = con.execute(
            "SELECT * FROM accounts ORDER BY upgrade_priority DESC, category, service"
        ).fetchall()
        con.close()
        records = [dict(r) for r in rows]

        for i, rec in enumerate(records):
            row = i + 1
            bg = BG3 if i % 2 == 0 else BG2
            tier = rec.get("tier", "free")
            usage = rec.get("usage_pct", 0) or 0
            cost  = rec.get("monthly_cost", 0.0) or 0.0

            # Tier color
            if tier == "paid":
                tier_color = GREEN
            elif tier == "local":
                tier_color = ORANGE
            else:
                tier_color = DIM

            # Usage bar text + color
            if usage >= 90:
                usage_color = RED
                usage_txt = f"⚠ {usage}%"
            elif usage >= 70:
                usage_color = ORANGE
                usage_txt = f"▲ {usage}%"
            else:
                usage_color = DIM
                usage_txt = f"{usage}%"

            cost_txt = f"${cost:.2f}" if cost > 0 else "—"

            cols = [
                (rec.get("service", "—"),    TEXT,        "w"),
                (rec.get("category", "—"),   DIM,         "w"),
                (tier.upper(),               tier_color,  "center"),
                (usage_txt,                  usage_color, "center"),
                (rec.get("free_limit", "—"), DIM,         "w"),
                (cost_txt,                   TEXT,        "center"),
            ]
            widgets = []
            for col, (txt, color, anchor) in enumerate(cols):
                lbl = ctk.CTkLabel(self._acct_scroll, text=txt, font=FONT_SM,
                                   text_color=color, anchor=anchor, fg_color=bg)
                lbl.grid(row=row, column=col, padx=6, pady=2, sticky="ew")
                widgets.append(lbl)

            btn = ctk.CTkButton(self._acct_scroll, text="✎", font=FONT_SM,
                                width=28, height=22, fg_color=bg, hover_color=BG3,
                                command=lambda r=rec: self._accounts_edit_dialog(r))
            btn.grid(row=row, column=6, padx=4, pady=2)
            widgets.append(btn)
            self._acct_rows.extend(widgets)

    def _accounts_add_dialog(self):
        self._accounts_edit_dialog({})

    def _accounts_edit_dialog(self, rec: dict):
        win = ctk.CTkToplevel(self)
        win.title("Service Account")
        win.geometry("460x520")
        win.configure(fg_color=BG)
        win.grab_set()

        fields = [
            ("Service",          rec.get("service", "")),
            ("Category",         rec.get("category", "")),
            ("Account Email",    rec.get("account_email", "")),
            ("Free Limit",       rec.get("free_limit", "")),
            ("Reset Date",       rec.get("reset_date", "")),
            ("Notes",            rec.get("notes", "")),
            ("Signup URL",       rec.get("signup_url", "")),
            ("Upgrade Reason",   rec.get("upgrade_reason", "")),
        ]
        entries = {}
        for i, (lbl, val) in enumerate(fields):
            ctk.CTkLabel(win, text=lbl, font=FONT_SM, text_color=DIM,
                         anchor="w").grid(row=i, column=0, padx=(14, 6), pady=3, sticky="w")
            e = ctk.CTkEntry(win, font=FONT_SM, fg_color=BG2,
                             border_color="#444", text_color=TEXT)
            e.insert(0, val)
            e.grid(row=i, column=1, padx=(0, 14), pady=3, sticky="ew")
            entries[lbl] = e
        win.grid_columnconfigure(1, weight=1)

        row_idx = len(fields)

        # Tier selector
        ctk.CTkLabel(win, text="Tier", font=FONT_SM, text_color=DIM,
                     anchor="w").grid(row=row_idx, column=0, padx=(14, 6), pady=3, sticky="w")
        tier_var = ctk.StringVar(value=rec.get("tier", "free"))
        ctk.CTkOptionMenu(win, values=["free", "paid", "local"],
                          variable=tier_var, font=FONT_SM,
                          fg_color=BG3, button_color=BG3,
                          ).grid(row=row_idx, column=1, padx=(0, 14), pady=3, sticky="ew")
        row_idx += 1

        # Usage slider
        ctk.CTkLabel(win, text="Usage %", font=FONT_SM, text_color=DIM,
                     anchor="w").grid(row=row_idx, column=0, padx=(14, 6), pady=3, sticky="w")
        usage_var = ctk.IntVar(value=rec.get("usage_pct", 0) or 0)
        usage_lbl = ctk.CTkLabel(win, text=f"{usage_var.get()}%", font=FONT_SM, text_color=TEXT)
        usage_lbl.grid(row=row_idx, column=1, padx=(0, 14), pady=3, sticky="w")
        row_idx += 1
        ctk.CTkSlider(win, from_=0, to=100, variable=usage_var, number_of_steps=100,
                      command=lambda v: usage_lbl.configure(text=f"{int(v)}%")
                      ).grid(row=row_idx, column=0, columnspan=2, padx=14, pady=(0, 4), sticky="ew")
        row_idx += 1

        # Monthly cost
        ctk.CTkLabel(win, text="Cost/mo $", font=FONT_SM, text_color=DIM,
                     anchor="w").grid(row=row_idx, column=0, padx=(14, 6), pady=3, sticky="w")
        cost_entry = ctk.CTkEntry(win, font=FONT_SM, fg_color=BG2,
                                  border_color="#444", text_color=TEXT)
        cost_entry.insert(0, str(rec.get("monthly_cost", 0.0) or 0.0))
        cost_entry.grid(row=row_idx, column=1, padx=(0, 14), pady=3, sticky="ew")
        row_idx += 1

        # Priority
        ctk.CTkLabel(win, text="Upgrade Priority", font=FONT_SM, text_color=DIM,
                     anchor="w").grid(row=row_idx, column=0, padx=(14, 6), pady=3, sticky="w")
        pri_var = ctk.IntVar(value=rec.get("upgrade_priority", 0) or 0)
        pri_lbl = ctk.CTkLabel(win, text=str(pri_var.get()), font=FONT_SM, text_color=TEXT)
        pri_lbl.grid(row=row_idx, column=1, padx=(0, 14), pady=3, sticky="w")
        row_idx += 1
        ctk.CTkSlider(win, from_=0, to=10, variable=pri_var, number_of_steps=10,
                      command=lambda v: pri_lbl.configure(text=str(int(v)))
                      ).grid(row=row_idx, column=0, columnspan=2, padx=14, pady=(0, 4), sticky="ew")
        row_idx += 1

        def _save():
            try:
                cost = float(cost_entry.get() or 0)
            except ValueError:
                cost = 0.0
            svc = entries["Service"].get().strip()
            if not svc:
                return
            con = self._db_conn()
            con.execute("DELETE FROM accounts WHERE service=?", (rec.get("service", ""),))
            con.execute(
                "INSERT INTO accounts"
                " (service, category, tier, monthly_cost, free_limit, usage_pct,"
                "  reset_date, account_email, notes, upgrade_priority, upgrade_reason, signup_url)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (svc,
                 entries["Category"].get(),
                 tier_var.get(),
                 cost,
                 entries["Free Limit"].get(),
                 int(usage_var.get()),
                 entries["Reset Date"].get(),
                 entries["Account Email"].get(),
                 entries["Notes"].get(),
                 int(pri_var.get()),
                 entries["Upgrade Reason"].get(),
                 entries["Signup URL"].get(),
                 ))
            con.commit()
            con.close()
            self._accounts_refresh()
            win.destroy()

        def _delete():
            svc = rec.get("service", "")
            if svc:
                con = self._db_conn()
                con.execute("DELETE FROM accounts WHERE service=?", (svc,))
                con.commit()
                con.close()
            self._accounts_refresh()
            win.destroy()

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.grid(row=row_idx, column=0, columnspan=2, padx=14, pady=(10, 14), sticky="ew")
        btn_row.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(btn_row, text="Save", font=FONT_SM, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_H, command=_save
                      ).grid(row=0, column=0, sticky="ew")
        if rec.get("service"):
            ctk.CTkButton(btn_row, text="Delete", font=FONT_SM, height=30, width=70,
                          fg_color="#5c1010", hover_color="#3a0808", command=_delete
                          ).grid(row=0, column=1, padx=(8, 0))

    def _accounts_review_dispatch(self):
        import json as _json
        payload = _json.dumps({"focus": "upgrades", "threshold": 60})
        self._dispatch_raw("account_review", payload, assigned_to="account_manager",
                           msg="Account review queued → account_manager agent")

    # ── DB helpers ─────────────────────────────────────────────────────────────
    def _db_conn(self):
        DATA_DIR.mkdir(exist_ok=True)
        con = sqlite3.connect(str(DB_PATH))
        con.row_factory = sqlite3.Row
        return con

    def _db_init(self):
        con = self._db_conn()
        con.executescript("""
            CREATE TABLE IF NOT EXISTS agents (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                name     TEXT UNIQUE NOT NULL,
                role     TEXT,
                type     TEXT DEFAULT 'Internal',
                customer TEXT,
                notes    TEXT
            );
            CREATE TABLE IF NOT EXISTS crm (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                company  TEXT UNIQUE NOT NULL,
                industry TEXT,
                contact  TEXT,
                email    TEXT,
                phone    TEXT,
                stage    TEXT DEFAULT 'Lead',
                notes    TEXT
            );
            CREATE TABLE IF NOT EXISTS onboarding (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                customer TEXT NOT NULL,
                category TEXT NOT NULL,
                step     TEXT NOT NULL,
                done     INTEGER DEFAULT 0,
                UNIQUE(customer, category, step)
            );
            CREATE TABLE IF NOT EXISTS customers (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT UNIQUE NOT NULL,
                fleet_version TEXT,
                contact       TEXT,
                notes         TEXT,
                air_gapped    INTEGER DEFAULT 0,
                status        TEXT DEFAULT 'Unknown',
                last_ping     TEXT DEFAULT '—'
            );
            CREATE TABLE IF NOT EXISTS accounts (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                service          TEXT UNIQUE NOT NULL,
                category         TEXT,
                tier             TEXT DEFAULT 'free',
                monthly_cost     REAL DEFAULT 0.0,
                free_limit       TEXT DEFAULT '',
                usage_pct        INTEGER DEFAULT 0,
                reset_date       TEXT DEFAULT '',
                account_email    TEXT DEFAULT '',
                notes            TEXT DEFAULT '',
                upgrade_priority INTEGER DEFAULT 0,
                upgrade_reason   TEXT DEFAULT '',
                signup_url       TEXT DEFAULT ''
            );
        """)
        # Seed default services if table is empty
        count = con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        if count == 0:
            _SEED = [
                # (service, category, tier, free_limit, signup_url)
                ("Anthropic API",  "AI / LLM",       "free",  "Free $5 credit",          "https://console.anthropic.com"),
                ("Google Gemini",  "AI / LLM",       "free",  "60 req/min Flash free",   "https://aistudio.google.com"),
                ("Stability AI",   "AI / Image",     "free",  "25 credits/day",          "https://platform.stability.ai"),
                ("Replicate",      "AI / Video+Img", "free",  "Pay-as-you-go, no limit", "https://replicate.com"),
                ("Brave Search",   "Search",         "free",  "2,000 queries/mo",        "https://api.search.brave.com"),
                ("Tavily Search",  "Search",         "free",  "1,000 searches/mo",       "https://tavily.com"),
                ("Jina AI",        "Search / Embed", "free",  "1M tokens/mo",            "https://jina.ai"),
                ("HuggingFace",    "AI / Models",    "free",  "Unlimited public models", "https://huggingface.co"),
                ("GitHub",         "Dev",            "free",  "2,000 Actions min/mo",    "https://github.com"),
                ("Ollama",         "AI / Local",     "local", "Unlimited (local)",       "https://ollama.com"),
            ]
            for s in _SEED:
                con.execute(
                    "INSERT OR IGNORE INTO accounts (service, category, tier, free_limit, signup_url)"
                    " VALUES (?,?,?,?,?)", s)
        con.commit()
        con.close()

    # ── Tab: Ingestion ───────────────────────────────────────────────────────
    def _build_tab_ingest(self, parent):
        """File import browser — pick files from configured path, ingest into RAG."""
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        # Header bar
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        hdr.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(hdr, text="Source:", font=FONT_SM,
                     text_color=DIM).grid(row=0, column=0, padx=(0, 6))

        default_downloads = str(Path.home() / "Downloads")
        ingest_path = _load_settings().get("ingest_path", default_downloads)
        self._ingest_source_var = ctk.StringVar(value=ingest_path)
        ctk.CTkOptionMenu(
            hdr, values=["Downloads", "Custom..."],
            font=FONT_SM, fg_color=BG3, button_color=ACCENT,
            button_hover_color=ACCENT_H, height=26, width=120,
            command=self._ingest_source_change,
        ).grid(row=0, column=1, sticky="w")

        self._ingest_path_label = ctk.CTkLabel(
            hdr, text=ingest_path, font=("Consolas", 9), text_color=DIM, anchor="w")
        self._ingest_path_label.grid(row=0, column=2, padx=(8, 0), sticky="w")

        ctk.CTkButton(hdr, text="↻ Refresh", font=FONT_SM, height=26, width=80,
                      fg_color=BG3, hover_color=BG,
                      command=self._ingest_refresh_files
                      ).grid(row=0, column=3, padx=(8, 0), sticky="e")

        # Content: file list (left) + info/actions (right)
        content = ctk.CTkFrame(parent, fg_color=BG)
        content.grid(row=1, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=2)
        content.grid_columnconfigure(1, weight=3)
        content.grid_rowconfigure(0, weight=1)

        # File list with checkboxes
        left = ctk.CTkFrame(content, fg_color=BG2, corner_radius=4)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)

        # Select all / none bar
        sel_bar = ctk.CTkFrame(left, fg_color=BG3, corner_radius=0)
        sel_bar.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(sel_bar, text="Select All", font=("Segoe UI", 9),
                      width=70, height=22, fg_color="transparent", hover_color=BG2,
                      text_color=DIM, command=self._ingest_select_all
                      ).pack(side="left", padx=4, pady=2)
        ctk.CTkButton(sel_bar, text="Select None", font=("Segoe UI", 9),
                      width=75, height=22, fg_color="transparent", hover_color=BG2,
                      text_color=DIM, command=self._ingest_select_none
                      ).pack(side="left", padx=0, pady=2)
        self._ingest_count_lbl = ctk.CTkLabel(
            sel_bar, text="", font=("Segoe UI", 9), text_color=DIM)
        self._ingest_count_lbl.pack(side="right", padx=8)

        self._ingest_file_list = ctk.CTkScrollableFrame(
            left, fg_color=BG2, corner_radius=0)
        self._ingest_file_list.grid(row=1, column=0, sticky="nsew")
        self._ingest_file_list.grid_columnconfigure(0, weight=1)

        self._ingest_checks = []  # list of (BooleanVar, Path)
        self._ingest_widgets = []

        # Right panel: info + ingest button
        right = ctk.CTkFrame(content, fg_color=BG2, corner_radius=4)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)

        # Tag field
        tag_frame = ctk.CTkFrame(right, fg_color="transparent")
        tag_frame.pack(fill="x", padx=12, pady=(12, 4))
        ctk.CTkLabel(tag_frame, text="Import tag:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._ingest_tag_var = ctk.StringVar(value="import")
        ctk.CTkEntry(tag_frame, textvariable=self._ingest_tag_var,
                     font=FONT_SM, fg_color=BG, border_color="#444",
                     text_color=TEXT, height=28, width=160
                     ).pack(side="left", padx=(6, 0))

        # Max file size
        max_frame = ctk.CTkFrame(right, fg_color="transparent")
        max_frame.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(max_frame, text="Max file size (MB):", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._ingest_maxmb_var = ctk.StringVar(value="50")
        ctk.CTkEntry(max_frame, textvariable=self._ingest_maxmb_var,
                     font=FONT_SM, fg_color=BG, border_color="#444",
                     text_color=TEXT, height=28, width=60
                     ).pack(side="left", padx=(6, 0))

        # Supported formats info
        info_frame = ctk.CTkFrame(right, fg_color=BG3, corner_radius=6)
        info_frame.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(info_frame, text="Supported formats", font=("Segoe UI", 10, "bold"),
                     text_color=GOLD).pack(padx=10, pady=(8, 2), anchor="w")
        ctk.CTkLabel(info_frame,
                     text="Text:   .md .txt .rst .log .toml .yaml .cfg .ini\n"
                          "Code:  .py .js .ts .go .rs .java .c .cpp .cs .rb .sh\n"
                          "Data:   .json .csv .tsv .xml .html\n"
                          "Docs:  .pdf .docx\n"
                          "Zip:     .zip (auto-extracted, nested supported)",
                     font=("Consolas", 9), text_color=DIM, justify="left"
                     ).pack(padx=10, pady=(0, 8), anchor="w")

        # Ingest button
        self._ingest_btn = ctk.CTkButton(
            right, text="⬇  Ingest Selected", font=("Segoe UI", 11, "bold"),
            height=36, fg_color=ACCENT, hover_color=ACCENT_H,
            command=self._run_ingest)
        self._ingest_btn.pack(padx=12, pady=(8, 4), fill="x")

        # Status log
        self._ingest_status = ctk.CTkTextbox(
            right, font=("Consolas", 9), fg_color=BG,
            text_color="#aaa", height=120, corner_radius=4)
        self._ingest_status.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self._ingest_status.insert("end", "Select files and click Ingest to import into RAG.\n")
        self._ingest_status.configure(state="disabled")

        self._ingest_refresh_files()

    def _ingest_source_change(self, choice: str):
        if choice == "Custom...":
            from tkinter import filedialog
            chosen = filedialog.askdirectory(
                initialdir=self._ingest_source_var.get())
            if chosen:
                self._ingest_source_var.set(chosen)
                self._ingest_path_label.configure(text=chosen)
                self._ingest_refresh_files()
        else:
            default_downloads = str(Path.home() / "Downloads")
            path = _load_settings().get("ingest_path", default_downloads)
            self._ingest_source_var.set(path)
            self._ingest_path_label.configure(text=path)
            self._ingest_refresh_files()

    def _ingest_refresh_files(self):
        for w in self._ingest_widgets:
            w.destroy()
        self._ingest_widgets.clear()
        self._ingest_checks.clear()

        source = Path(self._ingest_source_var.get())
        if not source.exists():
            lbl = ctk.CTkLabel(self._ingest_file_list, text="Path not found",
                               font=FONT_SM, text_color=DIM)
            lbl.grid(row=0, column=0, padx=8, pady=20)
            self._ingest_widgets.append(lbl)
            self._ingest_count_lbl.configure(text="0 files")
            return

        # Supported extensions for display
        supported = {
            ".md", ".txt", ".rst", ".log", ".cfg", ".ini", ".toml", ".yaml", ".yml",
            ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp",
            ".h", ".hpp", ".cs", ".rb", ".sh", ".bat", ".ps1", ".sql",
            ".json", ".csv", ".tsv", ".xml", ".html", ".htm",
            ".pdf", ".docx", ".zip",
        }

        files = []
        for f in sorted(source.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.is_file() and f.suffix.lower() in supported:
                files.append(f)
            if len(files) >= 200:
                break

        # Also list subdirectories (for folder ingest)
        dirs = []
        for d in sorted(source.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                dirs.append(d)
                if len(dirs) >= 50:
                    break

        row = 0
        for d in dirs:
            var = ctk.BooleanVar(value=False)
            bg = BG3 if row % 2 == 0 else BG2
            cb = ctk.CTkCheckBox(
                self._ingest_file_list,
                text=f"📁 {d.name}/",
                variable=var, font=("Consolas", 9),
                text_color=GOLD, fg_color=ACCENT, hover_color=ACCENT_H,
                checkbox_width=16, checkbox_height=16, corner_radius=3,
            )
            cb.grid(row=row, column=0, sticky="ew", padx=4, pady=1)
            self._ingest_checks.append((var, d))
            self._ingest_widgets.append(cb)
            row += 1

        for f in files:
            var = ctk.BooleanVar(value=False)
            ext = f.suffix.lower()
            # Color code by type
            if ext == ".zip":
                color = ORANGE
            elif ext in (".pdf", ".docx"):
                color = "#7aa2f7"
            elif ext in (".py", ".js", ".ts", ".go", ".rs", ".java"):
                color = GREEN
            else:
                color = TEXT

            cb = ctk.CTkCheckBox(
                self._ingest_file_list,
                text=f"  {f.name}  ({f.stat().st_size / 1024:.0f} KB)",
                variable=var, font=("Consolas", 9),
                text_color=color, fg_color=ACCENT, hover_color=ACCENT_H,
                checkbox_width=16, checkbox_height=16, corner_radius=3,
            )
            cb.grid(row=row, column=0, sticky="ew", padx=4, pady=1)
            self._ingest_checks.append((var, f))
            self._ingest_widgets.append(cb)
            row += 1

        total = len(dirs) + len(files)
        self._ingest_count_lbl.configure(text=f"{total} items")

        if total == 0:
            lbl = ctk.CTkLabel(self._ingest_file_list, text="No supported files found",
                               font=FONT_SM, text_color=DIM)
            lbl.grid(row=0, column=0, padx=8, pady=20)
            self._ingest_widgets.append(lbl)

    def _ingest_select_all(self):
        for var, _ in self._ingest_checks:
            var.set(True)

    def _ingest_select_none(self):
        for var, _ in self._ingest_checks:
            var.set(False)

    def _ingest_log(self, msg: str):
        self._ingest_status.configure(state="normal")
        self._ingest_status.insert("end", msg + "\n")
        self._ingest_status.see("end")
        self._ingest_status.configure(state="disabled")

    def _run_ingest(self):
        selected = [path for var, path in self._ingest_checks if var.get()]
        if not selected:
            self._ingest_log("No files selected.")
            return

        tag = self._ingest_tag_var.get().strip() or "import"
        try:
            max_mb = int(self._ingest_maxmb_var.get())
        except ValueError:
            max_mb = 50

        self._ingest_btn.configure(state="disabled", text="Ingesting...")
        self._ingest_log(f"Starting ingest: {len(selected)} items, tag='{tag}'")

        def _do_ingest():
            total_files = 0
            total_chunks = 0
            errors = []

            for path in selected:
                try:
                    self.after(0, lambda p=path: self._ingest_log(f"  Processing: {p.name}"))
                    payload = {
                        "path": str(path),
                        "tag": tag,
                        "max_file_mb": max_mb,
                        "recursive": True,
                    }
                    # Import and run directly
                    import importlib.util
                    spec = importlib.util.spec_from_file_location(
                        "ingest", str(FLEET_DIR / "skills" / "ingest.py"))
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    result = mod.run(payload, {})

                    if "error" in result:
                        errors.append(f"{path.name}: {result['error']}")
                        self.after(0, lambda e=result['error']: self._ingest_log(f"    Error: {e}"))
                    else:
                        fi = result.get("files_ingested", 0)
                        ch = result.get("chunks_indexed", 0)
                        total_files += fi
                        total_chunks += ch
                        self.after(0, lambda f=fi, c=ch: self._ingest_log(
                            f"    Indexed {f} files, {c} chunks"))

                except Exception as e:
                    errors.append(f"{path.name}: {e}")
                    self.after(0, lambda e=e: self._ingest_log(f"    Error: {e}"))

            summary = f"Done: {total_files} files, {total_chunks} chunks indexed"
            if errors:
                summary += f", {len(errors)} errors"
            self.after(0, lambda s=summary: self._ingest_log(s))
            self.after(0, lambda: self._ingest_btn.configure(
                state="normal", text="⬇  Ingest Selected"))

        threading.Thread(target=_do_ingest, daemon=True).start()

    # ── Tab: Outputs ─────────────────────────────────────────────────────────
    def _build_tab_outputs(self, parent):
        """Browse fleet knowledge outputs — reviews, reports, drafts, security."""
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        # Header with category filter
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        hdr.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(hdr, text="Category:", font=FONT_SM,
                     text_color=DIM).grid(row=0, column=0, padx=(0, 6))

        categories = ["All", "Code Reviews", "Security", "Quality",
                       "Drafts", "Reports", "Chains", "FMA Reviews"]
        self._outputs_cat_var = ctk.StringVar(value="All")
        ctk.CTkOptionMenu(
            hdr, values=categories, variable=self._outputs_cat_var,
            font=FONT_SM, fg_color=BG3, button_color=ACCENT,
            button_hover_color=ACCENT_H, height=26, width=140,
            command=lambda _: self._outputs_refresh()
        ).grid(row=0, column=1, sticky="w")

        ctk.CTkButton(hdr, text="↻ Refresh", font=FONT_SM, height=26, width=80,
                      fg_color=BG3, hover_color=BG,
                      command=self._outputs_refresh
                      ).grid(row=0, column=2, sticky="e")

        # Split: file list (left) + preview (right)
        content = ctk.CTkFrame(parent, fg_color=BG)
        content.grid(row=1, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=3)
        content.grid_rowconfigure(0, weight=1)

        self._outputs_list = ctk.CTkScrollableFrame(content, fg_color=BG2, corner_radius=4)
        self._outputs_list.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self._outputs_list.grid_columnconfigure(0, weight=1)

        preview_frame = ctk.CTkFrame(content, fg_color=BG2, corner_radius=4)
        preview_frame.grid(row=0, column=1, sticky="nsew")
        preview_frame.grid_rowconfigure(1, weight=1)
        preview_frame.grid_columnconfigure(0, weight=1)

        self._outputs_preview_label = ctk.CTkLabel(
            preview_frame, text="Select a file to preview", font=FONT_SM,
            text_color=DIM, anchor="w")
        self._outputs_preview_label.grid(row=0, column=0, padx=8, pady=(4, 2), sticky="w")

        self._outputs_preview = ctk.CTkTextbox(
            preview_frame, font=("Consolas", 10), fg_color=BG2,
            text_color="#c8c8c8", wrap="word", corner_radius=0)
        self._outputs_preview.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))

        self._outputs_items = []
        self._outputs_refresh()

    _OUTPUTS_DIRS = {
        "All": None,
        "Code Reviews": "code_reviews",
        "Security": "security",
        "Quality": "quality",
        "Drafts": "code_drafts",
        "Reports": "reports",
        "Chains": "chains",
        "FMA Reviews": "fma_reviews",
    }

    def _outputs_refresh(self):
        for w in self._outputs_items:
            w.destroy()
        self._outputs_items.clear()

        cat = self._outputs_cat_var.get() if hasattr(self, "_outputs_cat_var") else "All"
        knowledge = FLEET_DIR / "knowledge"
        subdir = self._OUTPUTS_DIRS.get(cat)

        files = []
        if subdir:
            target = knowledge / subdir
            if target.exists():
                files = sorted(target.rglob("*.md"), key=lambda f: f.stat().st_mtime,
                               reverse=True)[:50]
        else:
            # All — scan all subdirs
            if knowledge.exists():
                files = sorted(knowledge.rglob("*.md"), key=lambda f: f.stat().st_mtime,
                               reverse=True)[:50]

        for i, f in enumerate(files):
            rel = f.relative_to(knowledge)
            bg = BG3 if i % 2 == 0 else BG2
            btn = ctk.CTkButton(
                self._outputs_list, text=str(rel), font=("Consolas", 9),
                fg_color=bg, hover_color=ACCENT, text_color=TEXT,
                anchor="w", height=22, corner_radius=2,
                command=lambda path=f: self._outputs_show_file(path))
            btn.grid(row=i, column=0, sticky="ew", padx=2, pady=1)
            self._outputs_items.append(btn)

        if not files:
            lbl = ctk.CTkLabel(self._outputs_list, text="No files found",
                               font=FONT_SM, text_color=DIM)
            lbl.grid(row=0, column=0, padx=8, pady=20)
            self._outputs_items.append(lbl)

    def _outputs_show_file(self, path: Path):
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")[:8000]
        except Exception as e:
            content = f"Error reading file: {e}"
        self._outputs_preview_label.configure(text=path.name)
        self._outputs_preview.configure(state="normal")
        self._outputs_preview.delete("1.0", "end")
        self._outputs_preview.insert("end", content)
        self._outputs_preview.see("1.0")
        self._outputs_preview.configure(state="disabled")

    # ── Task bar ──────────────────────────────────────────────────────────────
    def _build_taskbar(self):
        bar = ctk.CTkFrame(self, fg_color=BG3, corner_radius=0)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew")
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

    # ── Refresh ───────────────────────────────────────────────────────────────
    def _refresh_status(self):
        status = parse_status()
        self._update_pills(status)
        self._update_agents_table(status)
        self._refresh_log()
        self._update_advisory_badge()

    def _refresh_agents_fast(self):
        """Fast-path: update agent sparklines + pills only (no log I/O)."""
        status = parse_status()
        self._update_pills(status)
        self._update_agents_table(status)

    def _check_status(self):
        """Refresh UI + show Ollama status + dump STATUS.md in one pass."""
        self._refresh_status()
        if STATUS_MD.exists():
            self._log_output(STATUS_MD.read_text())
        wsl_bg(
            "pgrep -x ollama > /dev/null "
            "&& echo 'Ollama running' "
            "&& curl -s http://localhost:11434/api/tags | python3 -c "
            "\"import sys,json; d=json.load(sys.stdin); "
            "print('Models:', ', '.join(m['name'] for m in d.get('models',[])))\" "
            "|| echo 'Ollama not running'",
            lambda o, e: self.after(0, lambda: self._log_output(o or e)))

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

    def _update_agents_table(self, status):
        for widget in self._agents_frame_inner.winfo_children():
            widget.destroy()

        agents     = status.get("agents", [])
        pending    = status.get("tasks", {}).get("Pending", 0)

        sup_status = status.get("supervisor_status", "OFFLINE")
        if sup_status == "ONLINE":
            self._sup_status_lbl.configure(text="Task Sup: ONLINE", text_color=GREEN)
            if not self._system_running and not self._system_intentional_stop:
                self._system_running = True
                self._btn_system_toggle.configure(text="■  Stop", fg_color="#3a1e1e", hover_color="#4a2a2a")
        elif sup_status == "HUNG":
            self._sup_status_lbl.configure(text="Task Sup: HUNG", text_color=ORANGE)
        else:
            self._sup_status_lbl.configure(text="Task Sup: OFFLINE", text_color=RED)
            if self._system_running:
                self._system_running = False
                self._btn_system_toggle.configure(text="▶  Start", fg_color="#1e3a1e", hover_color="#2a4a2a")

        hw_status = status.get("hw_supervisor_status", "OFFLINE")
        if hw_status == "ONLINE":
            self._hw_sup_status_lbl.configure(text="HW Sup: ONLINE", text_color=GREEN)
        elif hw_status == "TRANSIT":
            self._hw_sup_status_lbl.configure(text="HW Sup: SCALING", text_color=ORANGE)
        elif hw_status == "HUNG":
            self._hw_sup_status_lbl.configure(text="HW Sup: HUNG", text_color=ORANGE)
        else:
            self._hw_sup_status_lbl.configure(text="HW Sup: OFFLINE", text_color=RED)

        # Record activity for sparklines
        self._record_agent_activity(agents)

        # All known roles — show offline ones too
        all_roles = ["researcher", "coder", "archivist", "analyst",
                     "sales", "onboarding", "implementation", "security"]

        seen = {a["name"]: a for a in agents}
        rows = []
        for role in all_roles:
            if role in seen:
                rows.append(seen[role])
            else:
                rows.append({"name": role, "status": "OFFLINE"})

        for i, a in enumerate(rows):
            color, label = self._agent_bubble_color(a, pending)
            row_frame = ctk.CTkFrame(self._agents_frame_inner, fg_color="transparent")
            row_frame.grid(row=i, column=0, sticky="ew", padx=4, pady=1)
            row_frame.grid_columnconfigure(1, weight=1)

            # Coloured status dot
            ctk.CTkLabel(row_frame, text="●", font=("Consolas", 11),
                         text_color=color, width=14).grid(row=0, column=0, padx=(2, 3))

            # Agent name
            display_name = themed_name(a['name'])
            ctk.CTkLabel(row_frame,
                         text=display_name,
                         font=("Consolas", 10), text_color=TEXT if label != "SLEEPING" else DIM,
                         anchor="w", width=110).grid(row=0, column=1, sticky="w")

            # Activity sparkline
            spark, spark_color = self._spark_text(a["name"])
            ctk.CTkLabel(row_frame, text=spark, font=("Consolas", 9),
                         text_color=spark_color, width=70
                         ).grid(row=0, column=2, padx=(2, 4))

            # Status label
            ctk.CTkLabel(row_frame, text=label, font=("Consolas", 9),
                         text_color=color, anchor="e", width=85
                         ).grid(row=0, column=3, sticky="e", padx=(0, 2))

            # Recover button for offline agents
            if label == "SLEEPING":
                ctk.CTkButton(
                    row_frame, text="↺", width=22, height=18,
                    font=("Segoe UI", 9), fg_color=ACCENT, hover_color=ACCENT_H,
                    command=lambda r=a["name"]: self._recover_agent(r),
                ).grid(row=0, column=4, padx=(2, 2))

    def _refresh_log(self):
        agent = self._log_agent_var.get()
        tail = read_log_tail(agent, 80)
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.insert("end", tail)
        self._log_text.see("end")
        self._log_text.configure(state="disabled")
        self._log_label.configure(text=f"LOG — {agent}")

    def _update_advisory_badge(self):
        n = count_pending_advisories()
        if n > 0:
            self._advisory_badge.configure(
                text=f"  ⚠ {n} advisory{'s' if n > 1 else ''}  ",
                fg_color=ORANGE, text_color="#1a1a1a")
        else:
            self._advisory_badge.configure(text="", fg_color="transparent")

    def _schedule_hw(self):
        def _sample():
            try:
                cpu_s, ram_s, gpu_s, net_s, new_prev, now = get_hw_stats(
                    self._net_prev, self._net_time)
                self._net_prev = new_prev
                self._net_time = now
                self.after(0, lambda: self._apply_hw(cpu_s, ram_s, gpu_s, net_s))
            except Exception:
                pass
        threading.Thread(target=_sample, daemon=True).start()
        self.after(2000, self._schedule_hw)

    def _apply_hw(self, cpu_s, ram_s, gpu_s, net_s):
        def color(pct_str, warn=70, crit=90):
            try:
                v = float(pct_str.rstrip("%"))
                return RED if v >= crit else ORANGE if v >= warn else GREEN
            except Exception:
                return DIM
        cpu_pct = cpu_s.split()[1] if len(cpu_s.split()) > 1 else "0%"
        ram_pct = ram_s.split()[-1] if ram_s.split() else "0%"
        gpu_pct = gpu_s.split()[1] if _GPU_OK and len(gpu_s.split()) > 1 else "0%"
        self._stat_cpu.configure(text=cpu_s, text_color=color(cpu_pct))
        self._stat_ram.configure(text=ram_s, text_color=color(ram_pct))
        self._stat_gpu.configure(text=gpu_s, text_color=color(gpu_pct) if _GPU_OK else DIM)
        self._stat_net.configure(text=net_s, text_color=DIM)

    def _schedule_refresh(self):
        """Full refresh every 6s (includes log tail + advisories)."""
        self._refresh_status()
        self.after(6000, self._schedule_refresh)

    def _schedule_agent_tick(self):
        """Fast agent status tick every 2s for responsive sparklines."""
        self._refresh_agents_fast()
        self.after(2000, self._schedule_agent_tick)

    def _switch_log(self, agent):
        self._refresh_log()

    # ── Ollama status + watchdog ──────────────────────────────────────────────
    def _poll_ollama(self) -> tuple:
        """
        Check Ollama API. Returns (up, detail, model_loaded).
        Polls /api/ps for actively loaded models — distinguishes idle-unloaded from crashed.
        """
        try:
            with urllib.request.urlopen(
                "http://localhost:11434/api/tags", timeout=2
            ) as r:
                json.loads(r.read())  # just confirm server is up
        except Exception:
            return False, "not reachable", False

        # Server is up — check if a model is currently loaded in VRAM
        try:
            with urllib.request.urlopen(
                "http://localhost:11434/api/ps", timeout=2
            ) as r:
                data = json.loads(r.read())
                running = [m["name"] for m in data.get("models", [])]
                if running:
                    vram_str = ""
                    if _GPU_OK:
                        try:
                            mem = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
                            vram_str = f"  {mem.used/1e9:.1f}GB VRAM"
                        except Exception:
                            pass
                    return True, f"{running[0]}{vram_str}", True
                else:
                    return True, "idle — model unloaded", False
        except Exception:
            return True, "up", False

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
            text = FLEET_TOML.read_text(encoding="utf-8")
            if use_claude:
                # Read the configured Claude model (last saved via Claude console or model selector)
                m = re.search(r'^claude_model\s*=\s*["\']([^"\']+)["\']', text, re.M)
                claude_model = m.group(1) if m else "claude-sonnet-4-6"
                provider  = "claude"
                complex_v = claude_model
            else:
                m = re.search(r'^local\s*=\s*["\']([^"\']+)["\']', text, re.M)
                local_model = m.group(1) if m else "qwen3:8b"
                provider  = "local"
                complex_v = local_model
            text = re.sub(r'^(complex_provider\s*=\s*)["\'][^"\']*["\']',
                          f'\\g<1>"{provider}"', text, flags=re.M)
            text = re.sub(r'^(complex\s*=\s*)["\'][^"\']*["\']',
                          f'\\g<1>"{complex_v}"', text, flags=re.M)
            FLEET_TOML.write_text(text, encoding="utf-8")
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
            out, _ = wsl("pgrep -f 'train\\.py' > /dev/null && echo yes || echo no", capture=True)
            return out.strip() == "yes"
        except Exception:
            return False

    def _send_keepalive(self, model: str):
        """Ping Ollama with keep_alive=-1 to prevent model unload."""
        try:
            body = json.dumps({"model": model, "prompt": "", "keep_alive": "-1"}).encode()
            req = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
        except Exception:
            pass

    def _schedule_ollama_watch(self):
        def _check():
            up, detail, loaded = self._poll_ollama()
            # Keepalive: GPU mode, no training, model loaded, every 4 min
            if up and loaded and not self._is_eco_mode():
                now = time.time()
                if now - self._last_keepalive >= 240:
                    model = detail.split()[0] if detail else ""
                    if model and model != "up":
                        self._send_keepalive(model)
                        self._last_keepalive = now
            self.after(0, lambda: self._apply_ollama_status(up, detail, loaded))
        threading.Thread(target=_check, daemon=True).start()
        self.after(8000, self._schedule_ollama_watch)

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
        self.after(4000, self._recover_offline_agents)

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
        """Build the ollama-start bash script content."""
        prefix = "CUDA_VISIBLE_DEVICES=-1 " if self._is_eco_mode() else ""
        return (
            "#!/bin/bash\n"
            "pgrep -x ollama > /dev/null && echo 'Ollama already running' && exit 0\n"
            f"nohup {prefix}ollama serve >> /tmp/ollama.log 2>&1 &\n"
            "disown\n"
            "for i in $(seq 1 15); do\n"
            "    curl -sf http://localhost:11434/api/tags > /dev/null"
            " && echo 'Ollama started OK' && exit 0\n"
            "    sleep 2\n"
            "done\n"
            "echo 'Ollama start timed out - check /tmp/ollama.log'\n"
        )

    def _run_ollama_start(self, callback=None):
        """Write start script to a temp file and execute via WSL — avoids arg quoting issues."""
        tmp = Path(tempfile.gettempdir()) / "fleet_ollama_start.sh"
        tmp.write_text(self._ollama_script(), encoding="utf-8", newline="\n")
        drive = tmp.drive.rstrip(":").lower()
        rest = str(tmp).replace("\\", "/")[2:]
        wsl_path = f"/mnt/{drive}{rest}"
        args = ["wsl", "-d", "Ubuntu", "/bin/bash", wsl_path]
        def _run():
            try:
                r = subprocess.run(args, capture_output=True, text=True, timeout=60,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                out, err = r.stdout.strip(), r.stderr.strip()
            except Exception as e:
                out, err = "", str(e)
            if callback:
                callback(out, err)
        threading.Thread(target=_run, daemon=True).start()

    def _start_ollama(self):
        self._log_output("Starting Ollama...")
        self._run_ollama_start(
            lambda o, e: self.after(0, lambda: self._log_output(o or e or "Ollama start attempted"))
        )

    def _stop_ollama(self):
        self._log_output("Stopping Ollama...")
        wsl_bg("pkill -x ollama && echo 'Ollama stopped' || echo 'Ollama not running'",
               lambda o, e: self.after(0, lambda: self._log_output(o or e)))

    def _ollama_status(self):
        wsl_bg(
            "pgrep -x ollama > /dev/null "
            "&& echo 'Ollama running' "
            "&& curl -s http://localhost:11434/api/tags | python3 -c "
            "\"import sys,json; d=json.load(sys.stdin); "
            "print('Models:', ', '.join(m['name'] for m in d.get('models',[])))\" "
            "|| echo 'Ollama not running'",
            lambda o, e: self.after(0, lambda: self._log_output(o or e)))

    # ── Fleet commands ────────────────────────────────────────────────────────
    def _recover_agent(self, role: str):
        """Restart a single crashed worker."""
        self._log_output(f"Recovering {role}...")
        cmd = (f"~/.local/bin/uv run python worker.py --role {role} "
               f">> logs/{role}.log 2>&1 &")
        wsl_bg(cmd, lambda o, e: self.after(0, lambda: self._log_output(
            f"↺ {role} restarted" if not e else f"↺ {role} error: {e}")))

    def _toggle_system(self):
        if self._system_running:
            self._stop_system()
        else:
            self._start_system()

    def _start_system(self):
        self._system_intentional_stop = False
        self._system_running = True
        self._btn_system_toggle.configure(
            text="■  Stop", fg_color="#3a1e1e", hover_color="#4a2a2a")
        self._log_output("Starting Ollama + fleet...")
        fleet_cmd = (
            "pkill -f supervisor.py 2>/dev/null; sleep 1; "
            "mkdir -p logs knowledge/summaries knowledge/reports && "
            "nohup ~/.local/bin/uv run python supervisor.py "
            ">> logs/supervisor.log 2>&1 & disown && echo 'Fleet started'"
        )
        self._ensure_ollama_and_run(
            fleet_cmd, lambda o, e: self._log_output(f"Fleet started. {o or e}"))

    def _stop_system(self):
        self._system_intentional_stop = True
        self._system_running = False
        self._btn_system_toggle.configure(
            text="▶  Start", fg_color="#1e3a1e", hover_color="#2a4a2a")
        self._log_output("Stopping fleet + Ollama...")
        stop_cmd = (
            "pkill -f supervisor.py 2>/dev/null; "
            "pkill -f hw_supervisor.py 2>/dev/null; "
            "pkill -f 'worker.py' 2>/dev/null; "
            "sleep 1; "
            "pkill -x ollama 2>/dev/null; "
            "echo 'System stopped'"
        )
        wsl_bg(stop_cmd, lambda o, e: self.after(0, lambda: self._log_output(o or e or "System stopped")))

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
        """Start Ollama via temp script, then run fleet_cmd."""
        def _after_ollama(out, err):
            self.after(0, lambda: self._log_output(out or err or "Ollama check done"))
            wsl_bg(fleet_cmd, lambda o, e: self.after(0, lambda: callback(o, e)), timeout=60)
        self._run_ollama_start(_after_ollama)

    def _recover_all(self):
        """Kill supervisor, restart everything cleanly."""
        self._log_output("Recovering fleet (stop + restart)...")
        fleet_cmd = (
            "pkill -f supervisor.py; pkill -f hw_supervisor.py; sleep 2; "
            "mkdir -p logs knowledge/summaries knowledge/reports && "
            "nohup ~/.local/bin/uv run python hw_supervisor.py >> logs/hw_supervisor.log 2>&1 & "
            "nohup ~/.local/bin/uv run python supervisor.py "
            ">> logs/supervisor.log 2>&1 & echo \"PID: $!\""
        )
        self._ensure_ollama_and_run(
            fleet_cmd, lambda o, e: self._log_output(f"Fleet recovered. {o}"))

    def _start_fleet(self):
        self._log_output("Starting fleet...")
        fleet_cmd = (
            "pkill -f supervisor.py 2>/dev/null; sleep 1; "
            "mkdir -p logs knowledge/summaries knowledge/reports && "
            "nohup ~/.local/bin/uv run python supervisor.py "
            ">> logs/supervisor.log 2>&1 & echo \"PID: $!\""
        )
        self._ensure_ollama_and_run(
            fleet_cmd, lambda o, e: self._log_output(f"Fleet started. {o}"))

    def _stop_fleet(self):
        self._log_output("Stopping fleet...")
        wsl_bg("pkill -f supervisor.py && echo stopped",
               lambda o, e: self.after(0, lambda: self._log_output("Fleet stopped." if o else f"Error: {e}")))

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
        # Check if already running first
        def _after_check(out, err):
            pid = out.strip()
            if pid:
                self.after(0, lambda: self._log_output(
                    f"Marathon already running (PID {pid}).\n"
                    f"Use '📋 Marathon Log' to see progress, or '⏹ Stop Marathon' first."))
                return
            # Not running — launch it
            cmd = ("nohup ~/.local/bin/uv run python dispatch_marathon.py "
                   ">> logs/marathon.log 2>&1 & echo \"PID: $!\"")
            wsl_bg(cmd, lambda o, e: self.after(0, lambda: self._log_output(
                f"Marathon launched (PID: {o.strip()}).\n"
                f"Phases: wait-for-idle → 8 discussion rounds (40 min apart) "
                f"→ lead research → synthesis.\n"
                f"Use '📋 Marathon Log' to monitor progress.")))

        wsl_bg("pgrep -f dispatch_marathon.py | head -1", _after_check)

    def _show_marathon_log(self):
        """Tail the marathon log and show it in the output panel."""
        def _show(out, err):
            if not out.strip():
                self.after(0, lambda: self._log_output("marathon.log is empty or not found.\n"
                                 "Start marathon first, or check fleet/logs/marathon.log."))
                return
            # Extract key status lines + last 20 lines
            lines = out.strip().splitlines()
            key = [l for l in lines if any(
                kw in l for kw in ("Phase", "round", "Round", "Task", "Waiting",
                                   "Sleeping", "synthesis", "Marathon", "=====",
                                   "Error", "Traceback", "✓"))]
            summary = "\n".join(key[-10:]) if key else ""
            tail    = "\n".join(lines[-20:])
            sep     = "─" * 40
            self.after(0, lambda: self._log_output(
                f"{'=' * 40}\nMARATHON LOG\n{'=' * 40}\n"
                + (f"[Key events]\n{summary}\n\n{sep}\n" if summary else "")
                + f"[Last 20 lines]\n{tail}"))

        wsl_bg("tail -80 logs/marathon.log 2>/dev/null || echo ''", _show)

    def _stop_marathon(self):
        def _done(out, err):
            self.after(0, lambda: self._log_output(out.strip() or err.strip() or "Marathon process not found."))
        wsl_bg(
            "pid=$(pgrep -f dispatch_marathon.py) && "
            "kill $pid && echo \"Stopped marathon PID $pid\" || "
            "echo 'No marathon process found'",
            _done,
        )

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

        b64_text = base64.b64encode(text.encode()).decode()
        cmd = (f'~/.local/bin/uv run python lead_client.py task '
               f'"$(echo {b64_text} | base64 -d)" --wait')
        self._log_output(f"→ {text}")

        def _done(out, err):
            def _update():
                result = out or err or "(no output)"
                self._log_output(f"← {result[:1200]}")
                self._task_status.configure(text="✓ done", text_color=GREEN)
                self.after(3000, lambda: self._task_status.configure(text=""))
            self.after(0, _update)

        wsl_bg(cmd, _done, timeout=300)

    def _dispatch_raw(self, skill: str, payload_json: str, assigned_to=None, msg=None):
        if msg:
            self._log_output(msg)
        safe_skill = _shell_safe(skill)
        safe_assign = f", assigned_to='{_shell_safe(assigned_to)}'" if assigned_to else ""
        # Base64-encode the payload to avoid all shell/quote escaping issues.
        # base64 output is [A-Za-z0-9+/=] — safe in any shell context.
        b64 = base64.b64encode(payload_json.encode()).decode()
        cmd = (
            f"~/.local/bin/uv run python -c \""
            f"import sys,base64; sys.path.insert(0,'.'); import db; db.init_db(); "
            f"p=base64.b64decode('{b64}').decode(); "
            f"tid=db.post_task('{safe_skill}',p,priority=9{safe_assign}); "
            f"print('Task',tid,'queued')\""
        )
        wsl_bg(cmd, lambda o, e: self.after(0, lambda: self._log_output(o or e)))

    def _log_output(self, text: str):
        """Write to the task output box."""
        self._output_text.configure(state="normal")
        self._output_text.insert("end", text.strip() + "\n")
        self._output_text.see("end")
        self._output_text.configure(state="disabled")

    def _open_settings(self):
        SettingsDialog(self)

    def _change_agent_theme(self, choice: str):
        global _active_theme
        _active_theme = choice
        _save_theme_preference(choice)
        self._log_output(f"Agent theme changed to: {choice}")
        if hasattr(self, "_refresh_agents_fast"):
            self._refresh_agents_fast()

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
        """Background thread: compare source file hashes to stored manifest."""
        try:
            manifest = {}
            if UPDATE_MANIFEST.exists():
                manifest = json.loads(UPDATE_MANIFEST.read_text())
            changed = [
                name for name, path in _UPDATE_TRACKED.items()
                if path.exists()
                and hashlib.md5(path.read_bytes()).hexdigest() != manifest.get(name, "")
            ]
            if changed:
                self.after(0, lambda: self._show_update_badge(changed))
        except Exception:
            pass

    def _show_update_badge(self, changed: list):
        names = ", ".join(changed)
        self._update_badge.configure(
            text=f"  🔄 {len(changed)} file(s) changed ({names}) — click to rebuild  ",
            fg_color="#1a3a1a", hover_color="#2a4a2a")

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


# ─── Unified Settings Dialog ─────────────────────────────────────────────────
# Dark glass aesthetic — gradient header, nav sidebar, content panel.

_SETTINGS_NAV = [
    ("General",   "general"),
    ("Models",    "models"),
    ("Hardware",  "hardware"),
    ("API Keys",  "keys"),
    ("Review",    "review"),
]

# Glass palette
_GLASS_BG    = "#0f0f0f"
_GLASS_NAV   = "#141414"
_GLASS_PANEL = "#181818"
_GLASS_HOVER = "#222222"
_GLASS_SEL   = "#1a1a2e"
_GLASS_BORDER = "#2a2a2a"


class SettingsDialog(ctk.CTkToplevel):
    """Unified settings panel — dark glass look with left nav + content area."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("BigEd CC — Settings")
        self.geometry("820x580")
        self.minsize(700, 480)
        self.configure(fg_color=_GLASS_BG)
        self.grab_set()
        self._parent = parent
        self._nav_buttons = {}
        self._panels = {}
        self._active_section = None

        ico = HERE / "brick.ico"
        if ico.exists():
            try: self.iconbitmap(str(ico))
            except Exception: pass

        # Load current settings
        self._settings = _load_settings()

        self._build_ui()
        self._show_section("general")

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0)  # nav
        self.grid_columnconfigure(1, weight=1)  # content
        self.grid_rowconfigure(1, weight=1)

        # ── Gradient header ──────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color="#111118", height=50, corner_radius=0)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="⚙  SETTINGS",
                     font=("Segoe UI", 15, "bold"), text_color=GOLD
                     ).grid(row=0, column=0, padx=18, pady=12, sticky="w")
        ctk.CTkLabel(hdr, text="BigEd CC configuration",
                     font=("Segoe UI", 9), text_color="#555555"
                     ).grid(row=0, column=1, padx=8, sticky="w")

        # ── Left nav ────────────────────────────────────────────────────
        nav = ctk.CTkFrame(self, fg_color=_GLASS_NAV, width=160, corner_radius=0)
        nav.grid(row=1, column=0, sticky="nsew")
        nav.grid_propagate(False)

        for i, (label, key) in enumerate(_SETTINGS_NAV):
            b = ctk.CTkButton(
                nav, text=f"  {label}", font=("Segoe UI", 11),
                fg_color="transparent", hover_color=_GLASS_HOVER,
                text_color=DIM, anchor="w", height=38, corner_radius=0,
                command=lambda k=key: self._show_section(k),
            )
            b.pack(fill="x", padx=0, pady=(1 if i else 8, 0))
            self._nav_buttons[key] = b

        # ── Content area ────────────────────────────────────────────────
        self._content = ctk.CTkFrame(self, fg_color=_GLASS_PANEL, corner_radius=0)
        self._content.grid(row=1, column=1, sticky="nsew")
        self._content.grid_columnconfigure(0, weight=1)
        self._content.grid_rowconfigure(0, weight=1)

        # Pre-build all panels
        self._build_general_panel()
        self._build_models_panel()
        self._build_hardware_panel()
        self._build_keys_panel()
        self._build_review_panel()

    def _show_section(self, key: str):
        if self._active_section == key:
            return
        # Update nav highlighting
        for k, b in self._nav_buttons.items():
            if k == key:
                b.configure(fg_color=_GLASS_SEL, text_color=GOLD)
            else:
                b.configure(fg_color="transparent", text_color=DIM)
        # Show/hide panels
        for k, panel in self._panels.items():
            if k == key:
                panel.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
            else:
                panel.grid_forget()
        self._active_section = key

    # ── General Panel ────────────────────────────────────────────────────
    def _build_general_panel(self):
        panel = ctk.CTkScrollableFrame(self._content, fg_color=_GLASS_PANEL)
        self._panels["general"] = panel

        # Section: Agent Theme
        self._section_header(panel, "Agent Theme")
        theme_frame = ctk.CTkFrame(panel, fg_color=_GLASS_BG, corner_radius=6)
        theme_frame.pack(fill="x", padx=16, pady=(0, 12))
        theme_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(theme_frame, text="Theme", font=FONT_SM,
                     text_color=TEXT).grid(row=0, column=0, padx=12, pady=10, sticky="w")
        self._theme_var = ctk.StringVar(value=_active_theme)
        ctk.CTkOptionMenu(
            theme_frame, values=list(AGENT_THEMES.keys()),
            variable=self._theme_var, font=FONT_SM,
            fg_color=BG3, button_color=ACCENT, button_hover_color=ACCENT_H,
            height=30, width=160,
            command=self._on_theme_change,
        ).grid(row=0, column=1, padx=12, pady=10, sticky="w")

        ctk.CTkLabel(theme_frame,
                     text="Themes change how agent roles are displayed throughout the UI.",
                     font=("Segoe UI", 9), text_color="#555555"
                     ).grid(row=1, column=0, columnspan=2, padx=12, pady=(0, 10), sticky="w")

        # Section: Custom Agent Names
        self._section_header(panel, "Custom Agent Names")
        names_frame = ctk.CTkFrame(panel, fg_color=_GLASS_BG, corner_radius=6)
        names_frame.pack(fill="x", padx=16, pady=(0, 12))
        names_frame.grid_columnconfigure(1, weight=1)

        self._name_entries = {}
        all_roles = [
            "supervisor", "researcher", "coder", "coder_1", "coder_2", "coder_3",
            "archivist", "analyst", "sales", "onboarding", "implementation",
            "security", "planner",
        ]
        for i, role in enumerate(all_roles):
            theme_map = AGENT_THEMES.get(_active_theme, AGENT_THEMES["default"])
            base = re.sub(r'_\d+$', '', role)
            suffix = role[len(base):]
            theme_default = theme_map.get(base, base.title())
            if suffix:
                theme_default += f" {suffix.lstrip('_')}"

            ctk.CTkLabel(names_frame, text=f"{role}:", font=("Consolas", 10),
                         text_color=DIM, anchor="e", width=110
                         ).grid(row=i, column=0, padx=(10, 6), pady=2, sticky="e")
            entry = ctk.CTkEntry(names_frame, font=FONT_SM, fg_color="#111111",
                                 border_color=_GLASS_BORDER, text_color=TEXT,
                                 placeholder_text=theme_default, height=28)
            entry.grid(row=i, column=1, sticky="ew", padx=(0, 10), pady=2)
            current = _custom_names.get(role, "")
            if current:
                entry.insert(0, current)
            self._name_entries[role] = entry

        name_btn_frame = ctk.CTkFrame(names_frame, fg_color="transparent")
        name_btn_frame.grid(row=len(all_roles), column=0, columnspan=2,
                            sticky="ew", padx=10, pady=(6, 10))
        ctk.CTkButton(name_btn_frame, text="Save Names", font=FONT_SM,
                      width=100, height=28, fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self._save_names).pack(side="right", padx=4)
        ctk.CTkButton(name_btn_frame, text="Clear All", font=FONT_SM,
                      width=80, height=28, fg_color=BG3, hover_color=BG2,
                      command=self._clear_names).pack(side="right", padx=4)
        self._names_status = ctk.CTkLabel(name_btn_frame, text="", font=("Segoe UI", 9),
                                          text_color=DIM)
        self._names_status.pack(side="left", padx=8)

        # Section: Fleet Behavior
        self._section_header(panel, "Fleet Behavior")
        behavior_frame = ctk.CTkFrame(panel, fg_color=_GLASS_BG, corner_radius=6)
        behavior_frame.pack(fill="x", padx=16, pady=(0, 12))

        self._claude_research_var2 = ctk.BooleanVar(
            value=self._parent._get_complex_provider() == "claude")
        ctk.CTkSwitch(
            behavior_frame, text="  Claude for research decisions",
            variable=self._claude_research_var2,
            font=FONT_SM, text_color=TEXT,
            progress_color=ACCENT, button_color=TEXT,
            command=self._on_claude_research_toggle,
        ).pack(padx=12, pady=(12, 4), anchor="w")
        ctk.CTkLabel(behavior_frame,
                     text="When ON, complex analysis routes through Claude API instead of local LLM.",
                     font=("Segoe UI", 9), text_color="#555555"
                     ).pack(padx=12, pady=(0, 12), anchor="w")

        # Section: Ingestion
        self._section_header(panel, "File Ingestion")
        ingest_frame = ctk.CTkFrame(panel, fg_color=_GLASS_BG, corner_radius=6)
        ingest_frame.pack(fill="x", padx=16, pady=(0, 12))
        ingest_frame.grid_columnconfigure(1, weight=1)

        default_downloads = str(Path.home() / "Downloads")
        ingest_path = self._settings.get("ingest_path", default_downloads)

        ctk.CTkLabel(ingest_frame, text="Default import path:", font=FONT_SM,
                     text_color=TEXT).grid(row=0, column=0, padx=12, pady=(12, 4), sticky="w")
        self._ingest_path_var = ctk.StringVar(value=ingest_path)
        ctk.CTkEntry(ingest_frame, textvariable=self._ingest_path_var,
                     font=("Consolas", 9), fg_color="#111111",
                     border_color=_GLASS_BORDER, text_color=TEXT, height=28
                     ).grid(row=1, column=0, columnspan=2, sticky="ew",
                            padx=12, pady=(0, 4))

        btn_row = ctk.CTkFrame(ingest_frame, fg_color="transparent")
        btn_row.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))
        ctk.CTkButton(btn_row, text="Browse", font=FONT_SM,
                      width=70, height=26, fg_color=BG3, hover_color=BG2,
                      command=self._browse_ingest_path).pack(side="left")
        ctk.CTkButton(btn_row, text="Save", font=FONT_SM,
                      width=60, height=26, fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self._save_ingest_path).pack(side="left", padx=4)
        self._ingest_path_status = ctk.CTkLabel(
            btn_row, text="", font=("Segoe UI", 9), text_color=DIM)
        self._ingest_path_status.pack(side="left", padx=8)

        ctk.CTkLabel(ingest_frame,
                     text="Files from this folder appear in the Ingestion tab for import into RAG.",
                     font=("Segoe UI", 9), text_color="#555555"
                     ).grid(row=3, column=0, columnspan=2, padx=12, pady=(0, 10), sticky="w")

    # ── Models Panel ─────────────────────────────────────────────────────
    def _build_models_panel(self):
        panel = ctk.CTkScrollableFrame(self._content, fg_color=_GLASS_PANEL)
        self._panels["models"] = panel

        # LLM Model button
        self._section_header(panel, "LLM Model")
        llm_frame = ctk.CTkFrame(panel, fg_color=_GLASS_BG, corner_radius=6)
        llm_frame.pack(fill="x", padx=16, pady=(0, 12))

        current_model = load_model_cfg().get("local", "qwen3:8b")
        ctk.CTkLabel(llm_frame, text=f"Current: {current_model}",
                     font=("Consolas", 10), text_color=TEXT
                     ).pack(padx=12, pady=(12, 4), anchor="w")
        ctk.CTkLabel(llm_frame,
                     text="Select the Ollama model used by fleet workers for local inference.",
                     font=("Segoe UI", 9), text_color="#555555"
                     ).pack(padx=12, pady=(0, 6), anchor="w")
        ctk.CTkButton(llm_frame, text="Open Model Selector", font=FONT_SM,
                      width=160, height=30, fg_color=BG3, hover_color=BG2,
                      command=lambda: ModelSelectorDialog(self._parent)
                      ).pack(padx=12, pady=(0, 12), anchor="w")

        # Diffusion Models
        self._section_header(panel, "Image Generation (Stable Diffusion)")
        diff_frame = ctk.CTkFrame(panel, fg_color=_GLASS_BG, corner_radius=6)
        diff_frame.pack(fill="x", padx=16, pady=(0, 12))

        diff_settings = self._settings.get("diffusion", {})

        # SD 1.5 toggle
        sd15_row = ctk.CTkFrame(diff_frame, fg_color="transparent")
        sd15_row.pack(fill="x", padx=12, pady=(12, 0))
        sd15_row.grid_columnconfigure(1, weight=1)

        self._sd15_var = ctk.BooleanVar(value=diff_settings.get("sd15_enabled", True))
        ctk.CTkSwitch(
            sd15_row, text="  SD 1.5  —  GPU (fp16)",
            variable=self._sd15_var, font=FONT_SM, text_color=TEXT,
            progress_color=GREEN, button_color=TEXT,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(sd15_row, text="~4 GB VRAM  |  ~30s/image  |  512x512",
                     font=("Consolas", 9), text_color="#555555"
                     ).grid(row=0, column=1, padx=(12, 0), sticky="w")

        sd15_detail = ctk.CTkFrame(diff_frame, fg_color="transparent")
        sd15_detail.pack(fill="x", padx=24, pady=(2, 0))
        ctk.CTkLabel(sd15_detail,
                     text="Fast local generation on GPU. Good for iteration and drafts.",
                     font=("Segoe UI", 9), text_color="#444444"
                     ).pack(anchor="w")

        # SDXL toggle
        sdxl_row = ctk.CTkFrame(diff_frame, fg_color="transparent")
        sdxl_row.pack(fill="x", padx=12, pady=(12, 0))
        sdxl_row.grid_columnconfigure(1, weight=1)

        self._sdxl_var = ctk.BooleanVar(value=diff_settings.get("sdxl_enabled", False))
        ctk.CTkSwitch(
            sdxl_row, text="  SDXL  —  CPU (fp32)",
            variable=self._sdxl_var, font=FONT_SM, text_color=TEXT,
            progress_color=ORANGE, button_color=TEXT,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(sdxl_row, text="~12 GB RAM  |  ~10-15 min/image  |  768x768",
                     font=("Consolas", 9), text_color="#555555"
                     ).grid(row=0, column=1, padx=(12, 0), sticky="w")

        sdxl_detail = ctk.CTkFrame(diff_frame, fg_color="transparent")
        sdxl_detail.pack(fill="x", padx=24, pady=(2, 0))
        ctk.CTkLabel(sdxl_detail,
                     text="Higher quality output on CPU. Slow but no VRAM cost.",
                     font=("Segoe UI", 9), text_color="#444444"
                     ).pack(anchor="w")

        # Default model selector
        default_row = ctk.CTkFrame(diff_frame, fg_color="transparent")
        default_row.pack(fill="x", padx=12, pady=(12, 0))
        ctk.CTkLabel(default_row, text="Default model:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._diff_default_var = ctk.StringVar(
            value=diff_settings.get("default_model", "sd15"))
        ctk.CTkOptionMenu(
            default_row, values=["sd15", "sdxl"],
            variable=self._diff_default_var, font=FONT_SM,
            fg_color=BG3, button_color=ACCENT, button_hover_color=ACCENT_H,
            height=28, width=100,
        ).pack(side="left", padx=(8, 0))

        # Steps / guidance
        params_row = ctk.CTkFrame(diff_frame, fg_color="transparent")
        params_row.pack(fill="x", padx=12, pady=(10, 0))

        ctk.CTkLabel(params_row, text="Steps:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._diff_steps_var = ctk.StringVar(
            value=str(diff_settings.get("default_steps", 30)))
        ctk.CTkEntry(params_row, textvariable=self._diff_steps_var,
                     font=FONT_SM, fg_color="#111111", border_color=_GLASS_BORDER,
                     text_color=TEXT, width=50, height=28
                     ).pack(side="left", padx=(4, 16))

        ctk.CTkLabel(params_row, text="Guidance:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._diff_guidance_var = ctk.StringVar(
            value=str(diff_settings.get("default_guidance", 7.5)))
        ctk.CTkEntry(params_row, textvariable=self._diff_guidance_var,
                     font=FONT_SM, fg_color="#111111", border_color=_GLASS_BORDER,
                     text_color=TEXT, width=50, height=28
                     ).pack(side="left", padx=(4, 0))

        # ── Upscale section ───────────────────────────────────────────
        self._section_header(panel, "Upscale Pipeline (SD 1.5)")
        up_frame = ctk.CTkFrame(panel, fg_color=_GLASS_BG, corner_radius=6)
        up_frame.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(up_frame,
                     text="Apply after base 512x512 generation to increase resolution.",
                     font=("Segoe UI", 9), text_color="#555555"
                     ).pack(padx=12, pady=(10, 6), anchor="w")

        # Upscale method
        method_row = ctk.CTkFrame(up_frame, fg_color="transparent")
        method_row.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkLabel(method_row, text="Method:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._upscale_var = ctk.StringVar(
            value=diff_settings.get("default_upscale", "none"))
        ctk.CTkSegmentedButton(
            method_row, values=["none", "refine", "x4"],
            variable=self._upscale_var, font=FONT_SM,
            selected_color=ACCENT, selected_hover_color=ACCENT_H,
        ).pack(side="left", padx=(8, 0))

        # Method descriptions
        desc_frame = ctk.CTkFrame(up_frame, fg_color="#111111", corner_radius=4)
        desc_frame.pack(fill="x", padx=12, pady=(4, 8))
        ctk.CTkLabel(desc_frame,
                     text="none     — output at base resolution (512x512)\n"
                          "refine   — img2img re-pass at higher res (~30s/pass, same model)\n"
                          "x4       — SD upscaler 512→2048 (~90s, ~3 GB extra download)",
                     font=("Consolas", 9), text_color="#555555", justify="left"
                     ).pack(padx=10, pady=8, anchor="w")

        # Refine params
        refine_row = ctk.CTkFrame(up_frame, fg_color="transparent")
        refine_row.pack(fill="x", padx=12, pady=(0, 4))

        ctk.CTkLabel(refine_row, text="Passes:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._upscale_passes_var = ctk.StringVar(
            value=str(diff_settings.get("default_upscale_passes", 1)))
        ctk.CTkEntry(refine_row, textvariable=self._upscale_passes_var,
                     font=FONT_SM, fg_color="#111111", border_color=_GLASS_BORDER,
                     text_color=TEXT, width=40, height=28
                     ).pack(side="left", padx=(4, 14))

        ctk.CTkLabel(refine_row, text="Scale:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._upscale_factor_var = ctk.StringVar(
            value=str(diff_settings.get("default_upscale_factor", 1.5)))
        ctk.CTkEntry(refine_row, textvariable=self._upscale_factor_var,
                     font=FONT_SM, fg_color="#111111", border_color=_GLASS_BORDER,
                     text_color=TEXT, width=50, height=28
                     ).pack(side="left", padx=(4, 14))

        ctk.CTkLabel(refine_row, text="Strength:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._upscale_strength_var = ctk.StringVar(
            value=str(diff_settings.get("default_upscale_strength", 0.35)))
        ctk.CTkEntry(refine_row, textvariable=self._upscale_strength_var,
                     font=FONT_SM, fg_color="#111111", border_color=_GLASS_BORDER,
                     text_color=TEXT, width=50, height=28
                     ).pack(side="left", padx=(4, 0))

        # Pipeline preview
        preview_frame = ctk.CTkFrame(up_frame, fg_color="#111111", corner_radius=4)
        preview_frame.pack(fill="x", padx=12, pady=(4, 10))
        self._pipeline_preview = ctk.CTkLabel(
            preview_frame, text="", font=("Consolas", 9), text_color=GOLD, anchor="w")
        self._pipeline_preview.pack(padx=10, pady=6, anchor="w")
        self._update_pipeline_preview()

        # Bind updates to preview
        self._upscale_var.trace_add("write", lambda *_: self._update_pipeline_preview())
        self._upscale_passes_var.trace_add("write", lambda *_: self._update_pipeline_preview())
        self._upscale_factor_var.trace_add("write", lambda *_: self._update_pipeline_preview())

        # Save button
        save_row = ctk.CTkFrame(diff_frame, fg_color="transparent")
        save_row.pack(fill="x", padx=12, pady=(12, 12))
        ctk.CTkButton(save_row, text="Save Diffusion Settings", font=FONT_SM,
                      width=160, height=30, fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self._save_diffusion).pack(side="right")
        self._diff_status = ctk.CTkLabel(save_row, text="", font=("Segoe UI", 9),
                                         text_color=DIM)
        self._diff_status.pack(side="left", padx=8)

        # First-run notice
        notice = ctk.CTkFrame(panel, fg_color="#1a1a10", corner_radius=6)
        notice.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkLabel(notice,
                     text="Models download from HuggingFace on first use (~5 GB for SD1.5, ~7 GB for SDXL, ~3 GB x4 upscaler).\n"
                          "Requires: pip install diffusers transformers accelerate torch",
                     font=("Segoe UI", 9), text_color=ORANGE, justify="left"
                     ).pack(padx=12, pady=10, anchor="w")

    # ── Hardware Panel ───────────────────────────────────────────────────
    def _build_hardware_panel(self):
        panel = ctk.CTkFrame(self._content, fg_color=_GLASS_PANEL)
        self._panels["hardware"] = panel
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)

        # GPU Power section
        self._section_header_grid(panel, "GPU Power & Thermal", row=0)
        gpu_frame = ctk.CTkFrame(panel, fg_color=_GLASS_BG, corner_radius=6)
        gpu_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        ctk.CTkLabel(gpu_frame,
                     text="Control GPU power limits and monitor thermals.",
                     font=("Segoe UI", 9), text_color="#555555"
                     ).pack(padx=12, pady=(10, 4), anchor="w")
        ctk.CTkButton(gpu_frame, text="Open GPU Power Manager", font=FONT_SM,
                      width=180, height=30, fg_color=BG3, hover_color=BG2,
                      command=lambda: ThermalDialog(self._parent)
                      ).pack(padx=12, pady=(0, 10), anchor="w")

        # Hardware Details section
        self._section_header_grid(panel, "Hardware Details", row=2)
        hw_text = ctk.CTkTextbox(panel, font=("Consolas", 10),
                                 fg_color=_GLASS_BG, text_color=TEXT,
                                 wrap="none", corner_radius=6)
        hw_text.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 12))
        hw_text.insert("end", "Loading hardware info...")
        hw_text.configure(state="disabled")
        self._hw_text = hw_text

        bar = ctk.CTkFrame(panel, fg_color="transparent", height=36)
        bar.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 12))
        ctk.CTkButton(bar, text="↻ Refresh", font=FONT_SM, width=90, height=28,
                      fg_color=BG3, hover_color=BG2,
                      command=lambda: threading.Thread(
                          target=self._load_hw_info, daemon=True).start()
                      ).pack(side="left")

        threading.Thread(target=self._load_hw_info, daemon=True).start()

    # ── Keys Panel ───────────────────────────────────────────────────────
    def _build_keys_panel(self):
        panel = ctk.CTkFrame(self._content, fg_color=_GLASS_PANEL)
        self._panels["keys"] = panel
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(0, weight=1)

        # Embed a simple message + launch button (full KeyManager is complex)
        inner = ctk.CTkFrame(panel, fg_color=_GLASS_BG, corner_radius=6)
        inner.place(relx=0.5, rely=0.4, anchor="center")

        ctk.CTkLabel(inner, text="🔑", font=("Segoe UI", 32)
                     ).pack(pady=(24, 8))
        ctk.CTkLabel(inner, text="API Key Manager",
                     font=("Segoe UI", 14, "bold"), text_color=GOLD
                     ).pack(pady=(0, 4))
        ctk.CTkLabel(inner,
                     text="Add, rotate, and manage API keys for Anthropic, Gemini,\n"
                          "Stability AI, Replicate, and other services.",
                     font=("Segoe UI", 10), text_color="#555555", justify="center"
                     ).pack(padx=24, pady=(0, 12))
        ctk.CTkButton(inner, text="Open Key Manager", font=("Segoe UI", 11),
                      width=160, height=34, fg_color=ACCENT, hover_color=ACCENT_H,
                      command=lambda: KeyManagerDialog(self._parent)
                      ).pack(pady=(0, 24))

    # ── Review Panel ─────────────────────────────────────────────────────
    def _build_review_panel(self):
        panel = ctk.CTkFrame(self._content, fg_color=_GLASS_PANEL)
        self._panels["review"] = panel
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(0, weight=1)

        inner = ctk.CTkFrame(panel, fg_color=_GLASS_BG, corner_radius=6)
        inner.place(relx=0.5, rely=0.4, anchor="center")

        ctk.CTkLabel(inner, text="🧪", font=("Segoe UI", 32)
                     ).pack(pady=(24, 8))
        ctk.CTkLabel(inner, text="Review Settings",
                     font=("Segoe UI", 14, "bold"), text_color=GOLD
                     ).pack(pady=(0, 4))
        ctk.CTkLabel(inner,
                     text="Configure the evaluator-optimizer review pass.\n"
                          "Enable/disable reviews and choose provider (API, subscription, local).",
                     font=("Segoe UI", 10), text_color="#555555", justify="center"
                     ).pack(padx=24, pady=(0, 12))
        ctk.CTkButton(inner, text="Open Review Settings", font=("Segoe UI", 11),
                      width=170, height=34, fg_color=ACCENT, hover_color=ACCENT_H,
                      command=lambda: ReviewDialog(self._parent)
                      ).pack(pady=(0, 24))

    # ── Helpers ──────────────────────────────────────────────────────────
    def _section_header(self, parent, text: str):
        ctk.CTkLabel(parent, text=text, font=("Segoe UI", 12, "bold"),
                     text_color=GOLD).pack(padx=16, pady=(16, 6), anchor="w")

    def _section_header_grid(self, parent, text: str, row: int):
        ctk.CTkLabel(parent, text=text, font=("Segoe UI", 12, "bold"),
                     text_color=GOLD).grid(row=row, column=0, padx=16,
                                           pady=(16, 6), sticky="w")

    def _on_theme_change(self, choice: str):
        self._parent._change_agent_theme(choice)
        # Update placeholder text in name entries
        theme_map = AGENT_THEMES.get(choice, AGENT_THEMES["default"])
        for role, entry in self._name_entries.items():
            base = re.sub(r'_\d+$', '', role)
            suffix = role[len(base):]
            theme_default = theme_map.get(base, base.title())
            if suffix:
                theme_default += f" {suffix.lstrip('_')}"
            entry.configure(placeholder_text=theme_default)

    def _save_names(self):
        global _custom_names
        names = {}
        for role, entry in self._name_entries.items():
            val = entry.get().strip()
            if val:
                names[role] = val
        _custom_names = names
        _save_custom_names(names)
        if hasattr(self._parent, "_refresh_agents_fast"):
            self._parent._refresh_agents_fast()
        count = len(names)
        self._names_status.configure(
            text=f"Saved ({count} override{'s' if count != 1 else ''})",
            text_color=GREEN)

    def _clear_names(self):
        for entry in self._name_entries.values():
            entry.delete(0, "end")

    def _browse_ingest_path(self):
        from tkinter import filedialog
        chosen = filedialog.askdirectory(initialdir=self._ingest_path_var.get())
        if chosen:
            self._ingest_path_var.set(chosen)

    def _save_ingest_path(self):
        data = _load_settings()
        data["ingest_path"] = self._ingest_path_var.get()
        _save_settings(data)
        self._ingest_path_status.configure(text="Saved.", text_color=GREEN)

    def _update_pipeline_preview(self):
        method = self._upscale_var.get()
        if method == "none":
            text = "512x512  (~30s)"
        elif method == "refine":
            try:
                passes = int(self._upscale_passes_var.get())
            except ValueError:
                passes = 1
            try:
                factor = float(self._upscale_factor_var.get())
            except ValueError:
                factor = 1.5
            w, h = 512, 512
            stages = ["512x512"]
            time_est = 30
            for _ in range(passes):
                w = (int(w * factor) // 8) * 8
                h = (int(h * factor) // 8) * 8
                stages.append(f"{w}x{h}")
                time_est += 30
            text = " → ".join(stages) + f"  (~{time_est}s)"
        elif method == "x4":
            text = "512x512 → 2048x2048  (~2 min)"
        else:
            text = ""
        if hasattr(self, "_pipeline_preview"):
            self._pipeline_preview.configure(text=f"Pipeline: {text}")

    def _save_diffusion(self):
        try:
            steps = int(self._diff_steps_var.get())
        except ValueError:
            steps = 30
        try:
            guidance = float(self._diff_guidance_var.get())
        except ValueError:
            guidance = 7.5

        try:
            upscale_passes = int(self._upscale_passes_var.get())
        except ValueError:
            upscale_passes = 1
        try:
            upscale_factor = float(self._upscale_factor_var.get())
        except ValueError:
            upscale_factor = 1.5
        try:
            upscale_strength = float(self._upscale_strength_var.get())
        except ValueError:
            upscale_strength = 0.35

        data = _load_settings()
        data["diffusion"] = {
            "sd15_enabled": self._sd15_var.get(),
            "sdxl_enabled": self._sdxl_var.get(),
            "default_model": self._diff_default_var.get(),
            "default_steps": steps,
            "default_guidance": guidance,
            "default_upscale": self._upscale_var.get(),
            "default_upscale_passes": upscale_passes,
            "default_upscale_factor": upscale_factor,
            "default_upscale_strength": upscale_strength,
        }
        _save_settings(data)
        self._diff_status.configure(text="Saved.", text_color=GREEN)

    def _on_claude_research_toggle(self):
        # Sync with parent's toggle logic
        use_claude = self._claude_research_var2.get()
        try:
            text = FLEET_TOML.read_text(encoding="utf-8")
            if use_claude:
                m = re.search(r'^claude_model\s*=\s*["\']([^"\']+)["\']', text, re.M)
                claude_model = m.group(1) if m else "claude-sonnet-4-6"
                provider, complex_v = "claude", claude_model
            else:
                m = re.search(r'^local\s*=\s*["\']([^"\']+)["\']', text, re.M)
                local_model = m.group(1) if m else "qwen3:8b"
                provider, complex_v = "local", local_model
            text = re.sub(r'^(complex_provider\s*=\s*)["\'][^"\']*["\']',
                          f'\\g<1>"{provider}"', text, flags=re.M)
            text = re.sub(r'^(complex\s*=\s*)["\'][^"\']*["\']',
                          f'\\g<1>"{complex_v}"', text, flags=re.M)
            FLEET_TOML.write_text(text, encoding="utf-8")
            # Update parent's checkbox if it exists
            if hasattr(self._parent, "_claude_research_var"):
                self._parent._claude_research_var.set(use_claude)
        except Exception:
            pass

    def _load_hw_info(self):
        lines = []
        lines.append("── CPU ─────────────────────────────────────────────────")
        try:
            cpu = psutil.cpu_freq()
            try:
                name = subprocess.check_output(
                    ["wmic", "cpu", "get", "Name"],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    text=True, timeout=5).strip().split("\n")[-1].strip()
            except Exception:
                name = "Unknown"
            lines.append(f"  Name        : {name}")
            lines.append(f"  Cores       : {psutil.cpu_count(logical=False)} physical  "
                         f"{psutil.cpu_count(logical=True)} logical")
            if cpu:
                lines.append(f"  Frequency   : {cpu.current:.0f} MHz  "
                             f"(max {cpu.max:.0f} MHz)")
            lines.append(f"  Usage       : {psutil.cpu_percent(interval=1):.1f}%")
        except Exception as e:
            lines.append(f"  Error: {e}")

        lines.append("")
        lines.append("── RAM ─────────────────────────────────────────────────")
        try:
            vm = psutil.virtual_memory()
            lines.append(f"  Total       : {vm.total/1e9:.1f} GB")
            lines.append(f"  Used        : {vm.used/1e9:.1f} GB  ({vm.percent:.1f}%)")
            lines.append(f"  Available   : {vm.available/1e9:.1f} GB")
        except Exception as e:
            lines.append(f"  Error: {e}")

        lines.append("")
        lines.append("── GPU ─────────────────────────────────────────────────")
        if _GPU_OK:
            try:
                name = pynvml.nvmlDeviceGetName(_GPU_HANDLE)
                mem = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
                util = pynvml.nvmlDeviceGetUtilizationRates(_GPU_HANDLE)
                temp = pynvml.nvmlDeviceGetTemperature(
                    _GPU_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
                power = pynvml.nvmlDeviceGetPowerUsage(_GPU_HANDLE) / 1000
                lines.append(f"  Name        : {name}")
                lines.append(f"  VRAM Total  : {mem.total/1e9:.1f} GB")
                lines.append(f"  VRAM Used   : {mem.used/1e9:.2f} GB  "
                             f"({mem.used*100//mem.total}%)")
                lines.append(f"  VRAM Free   : {mem.free/1e9:.2f} GB")
                lines.append(f"  GPU Usage   : {util.gpu}%")
                lines.append(f"  Temp        : {temp}°C")
                lines.append(f"  Power       : {power:.1f} W")
            except Exception as e:
                lines.append(f"  Error: {e}")
        else:
            lines.append("  No NVIDIA GPU detected via NVML")

        result = "\n".join(lines)
        self.after(0, lambda: self._update_hw_text(result))

    def _update_hw_text(self, text: str):
        self._hw_text.configure(state="normal")
        self._hw_text.delete("1.0", "end")
        self._hw_text.insert("end", text)
        self._hw_text.configure(state="disabled")


# ─── Agent Names Dialog ───────────────────────────────────────────────────────
class AgentNamesDialog(ctk.CTkToplevel):
    """Let the user assign custom names to individual agents."""

    ALL_ROLES = [
        "supervisor", "researcher", "coder", "coder_1", "coder_2", "coder_3",
        "archivist", "analyst", "sales", "onboarding", "implementation",
        "security", "planner",
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.title("BigEd CC — Agent Names")
        self.geometry("500x560")
        self.configure(fg_color=BG)
        self.grab_set()
        self._parent = parent

        ico = HERE / "brick.ico"
        if ico.exists():
            try: self.iconbitmap(str(ico))
            except Exception: pass

        self._entries = {}
        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color=BG3, height=48, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(hdr, text="  Custom Agent Names", font=FONT_H,
                     text_color=GOLD).pack(side="left", padx=12, pady=10)
        ctk.CTkLabel(hdr, text="Leave blank to use theme name", font=FONT_SM,
                     text_color=DIM).pack(side="right", padx=12)

        # Scrollable form
        form = ctk.CTkScrollableFrame(self, fg_color=BG)
        form.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        form.grid_columnconfigure(1, weight=1)

        for i, role in enumerate(self.ALL_ROLES):
            # Role label (themed fallback)
            theme_map = AGENT_THEMES.get(_active_theme, AGENT_THEMES["default"])
            base = re.sub(r'_\d+$', '', role)
            suffix = role[len(base):]
            theme_default = theme_map.get(base, base.title())
            if suffix:
                theme_default += f" {suffix.lstrip('_')}"

            ctk.CTkLabel(form, text=f"{role}:", font=MONO,
                         text_color=DIM, anchor="e", width=120
                         ).grid(row=i, column=0, padx=(4, 8), pady=3, sticky="e")

            entry = ctk.CTkEntry(form, font=FONT, fg_color=BG2, border_color=BG3,
                                 text_color=TEXT, placeholder_text=theme_default,
                                 height=30)
            entry.grid(row=i, column=1, sticky="ew", padx=(0, 4), pady=3)

            # Pre-fill existing custom name
            current = _custom_names.get(role, "")
            if current:
                entry.insert(0, current)

            self._entries[role] = entry

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color=BG)
        btn_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(5, 10))

        ctk.CTkButton(btn_frame, text="Save", font=FONT, width=100, height=32,
                       fg_color=ACCENT, hover_color=ACCENT_H,
                       command=self._save).pack(side="right", padx=4)
        ctk.CTkButton(btn_frame, text="Clear All", font=FONT, width=100, height=32,
                       fg_color=BG3, hover_color=BG2,
                       command=self._clear_all).pack(side="right", padx=4)
        ctk.CTkButton(btn_frame, text="Cancel", font=FONT, width=80, height=32,
                       fg_color=BG3, hover_color=BG2,
                       command=self.destroy).pack(side="right", padx=4)

    def _save(self):
        global _custom_names
        names = {}
        for role, entry in self._entries.items():
            val = entry.get().strip()
            if val:
                names[role] = val
        _custom_names = names
        _save_custom_names(names)
        if hasattr(self._parent, "_refresh_agents_fast"):
            self._parent._refresh_agents_fast()
        if hasattr(self._parent, "_log_output"):
            count = len(names)
            self._parent._log_output(
                f"Custom agent names saved ({count} override{'s' if count != 1 else ''})")
        self.destroy()

    def _clear_all(self):
        for entry in self._entries.values():
            entry.delete(0, "end")


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

        self._vram_total = self._detect_vram()
        self._vram_safe  = min(self._vram_total * 0.85, 10.0)
        self._current    = self._read_current_model()
        self._selected   = self._current
        self._row_frames = {}
        mcfg = load_model_cfg()
        self._stack_var  = ctk.StringVar(value=mcfg.get("complex_provider", "claude"))
        self._build_ui()

    def _detect_vram(self) -> float:
        if _GPU_OK:
            try:
                mem = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
                return mem.total / 1e9
            except Exception:
                pass
        return 12.0  # known hardware fallback

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
        vram_str = (f"GPU VRAM: {self._vram_total:.1f} GB total  |  "
                    f"safe limit: {self._vram_safe:.1f} GB  |  "
                    f"grayed = won't fit")
        ctk.CTkLabel(hdr, text=vram_str,
                     font=("Segoe UI", 9), text_color=DIM
                     ).grid(row=0, column=1, padx=8, sticky="w")

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
            text = FLEET_TOML.read_text(encoding="utf-8")
            provider = self._stack_var.get()
            # Map stack mode to the complex model string
            complex_models = {
                "claude": "claude-sonnet-4-6",
                "gemini": "gemini-2.0-flash",
                "local":  self._selected,
            }
            complex_val = complex_models.get(provider, "claude-sonnet-4-6")
            text = re.sub(r'^(local\s*=\s*)["\'][^"\']*["\']',
                          f'\\g<1>"{self._selected}"', text, flags=re.M)
            text = re.sub(r'^(complex\s*=\s*)["\'][^"\']*["\']',
                          f'\\g<1>"{complex_val}"', text, flags=re.M)
            text = re.sub(r'^(complex_provider\s*=\s*)["\'][^"\']*["\']',
                          f'\\g<1>"{provider}"', text, flags=re.M)
            FLEET_TOML.write_text(text, encoding="utf-8")
            self._status_lbl.configure(
                text="✓ Saved — restart fleet to apply", text_color=GREEN)
            self._current = self._selected
            self._apply_btn.configure(state="disabled")
        except Exception as e:
            self._status_lbl.configure(text=f"✗ {e}", text_color=RED)


# ─── API Key Manager Dialog ────────────────────────────────────────────────────
class KeyManagerDialog(ctk.CTkToplevel):
    REGISTRY_FILE = FLEET_DIR / "keys_registry.toml"
    SECRETS_FILE  = Path.home() / ".wsl_secrets_cache"  # local cache from WSL read

    def __init__(self, parent):
        super().__init__(parent)
        self.title("BigEd CC — API Key Manager")
        self.geometry("780x540")
        self.configure(fg_color=BG)
        self.grab_set()

        ico = HERE / "brick.ico"
        if ico.exists():
            try: self.iconbitmap(str(ico))
            except Exception: pass

        self._rows = {}   # key_name -> {label, value_label, dot}
        self._build_ui()
        self._load_keys()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color=BG3, height=46, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="🔑  API KEY MANAGER",
                     font=("Segoe UI", 13, "bold"), text_color=GOLD
                     ).grid(row=0, column=0, padx=14, pady=10, sticky="w")
        ctk.CTkLabel(hdr, text="Keys are stored in WSL ~/.secrets  |  masked values shown",
                     font=("Segoe UI", 9), text_color=DIM
                     ).grid(row=0, column=1, padx=8, sticky="w")

        # Scrollable key table
        self._scroll = ctk.CTkScrollableFrame(self, fg_color=BG2, corner_radius=0)
        self._scroll.grid(row=1, column=0, sticky="nsew")
        self._scroll.grid_columnconfigure(0, weight=1)

        # Column headers
        hrow = ctk.CTkFrame(self._scroll, fg_color=BG3, corner_radius=4)
        hrow.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        hrow.grid_columnconfigure(1, weight=1)
        for col, (txt, w) in enumerate([("", 18), ("Key / Label", 0),
                                         ("Tier", 70), ("Status", 110), ("Value", 160), ("", 80)]):
            ctk.CTkLabel(hrow, text=txt, font=("Segoe UI", 9, "bold"),
                         text_color=DIM, width=w, anchor="w"
                         ).grid(row=0, column=col, padx=6, pady=4, sticky="w")

        # Bottom toolbar
        bar = ctk.CTkFrame(self, fg_color=BG3, height=46, corner_radius=0)
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_propagate(False)

        ctk.CTkButton(bar, text="↻ Refresh", font=FONT_SM, width=100, height=30,
                      fg_color=BG2, hover_color=BG, command=self._load_keys
                      ).grid(row=0, column=0, padx=(10, 6), pady=8)
        ctk.CTkButton(bar, text="🔍 Scan Skills", font=FONT_SM, width=120, height=30,
                      fg_color=BG2, hover_color=BG, command=self._scan_skills
                      ).grid(row=0, column=1, padx=6, pady=8)
        ctk.CTkButton(bar, text="+ Add Custom Key", font=FONT_SM, width=140, height=30,
                      fg_color=BG2, hover_color=BG, command=self._add_custom_key
                      ).grid(row=0, column=2, padx=6, pady=8)

        self._scan_lbl = ctk.CTkLabel(bar, text="", font=FONT_SM, text_color=DIM)
        self._scan_lbl.grid(row=0, column=3, padx=12, sticky="e")

    def _load_keys(self):
        # Clear existing rows (keep header)
        for w in list(self._scroll.winfo_children())[1:]:
            w.destroy()
        self._rows = {}

        registry = self._read_registry()
        secrets  = self._read_secrets_via_wsl()

        for i, info in enumerate(registry):
            name    = info.get("env_var", "")
            label   = info.get("label", name)
            purpose = info.get("purpose", "")
            tier    = info.get("tier", "")
            masked  = secrets.get(name, "")
            is_set  = bool(masked) and masked not in ("EMPTY", "not set")

            dot_color  = GREEN  if is_set  else RED
            dot_text   = "●"
            status_txt = "SET"  if is_set  else "MISSING"
            status_col = GREEN  if is_set  else RED
            tier_col   = {"free": DIM, "freemium": ORANGE, "paid": "#4488ff"}.get(tier, DIM)

            row = ctk.CTkFrame(self._scroll, fg_color=BG if i % 2 else "#1e1e1e",
                               corner_radius=3)
            row.grid(row=i + 1, column=0, sticky="ew", padx=8, pady=1)
            row.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(row, text=dot_text, font=("Consolas", 13),
                         text_color=dot_color, width=18).grid(row=0, column=0, padx=(8,2), pady=6)

            name_frame = ctk.CTkFrame(row, fg_color="transparent")
            name_frame.grid(row=0, column=1, sticky="w", padx=4)
            ctk.CTkLabel(name_frame, text=name, font=("Consolas", 10, "bold"),
                         text_color=TEXT, anchor="w").pack(anchor="w")
            ctk.CTkLabel(name_frame, text=label, font=("Segoe UI", 9),
                         text_color=DIM, anchor="w").pack(anchor="w")

            ctk.CTkLabel(row, text=tier, font=("Segoe UI", 9),
                         text_color=tier_col, width=70).grid(row=0, column=2, padx=4)
            ctk.CTkLabel(row, text=status_txt, font=("Segoe UI", 10, "bold"),
                         text_color=status_col, width=90).grid(row=0, column=3, padx=4)
            ctk.CTkLabel(row, text=masked or "—", font=("Consolas", 9),
                         text_color=DIM, width=160, anchor="w").grid(row=0, column=4, padx=4)
            ctk.CTkButton(row, text="Edit", font=FONT_SM, width=60, height=24,
                          fg_color=ACCENT, hover_color=ACCENT_H,
                          command=lambda n=name, lbl=label: self._edit_key(n, lbl)
                          ).grid(row=0, column=5, padx=(4, 8), pady=4)

            # Purpose tooltip row
            ctk.CTkLabel(row, text=f"  {purpose[:90]}", font=("Segoe UI", 9),
                         text_color=DIM, anchor="w"
                         ).grid(row=1, column=1, columnspan=5, sticky="w", padx=4, pady=(0, 6))

    def _read_registry(self):
        if not self.REGISTRY_FILE.exists():
            return []
        try:
            import tomllib
            with open(self.REGISTRY_FILE, "rb") as f:
                return tomllib.load(f).get("key", [])
        except Exception:
            return []

    def _read_secrets_via_wsl(self):
        """Read masked key values from WSL ~/.secrets."""
        masked = {}
        try:
            out, _ = wsl("cat ~/.secrets 2>/dev/null", capture=True)
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line or line.startswith("#"):
                    continue
                k, _, v = line.partition("=")
                k = k.strip(); v = v.strip().strip('"').strip("'")
                if v and v != "REPLACE_ME":
                    masked[k] = v[:6] + "..." + v[-4:] if len(v) > 12 else "***set***"
                else:
                    masked[k] = "EMPTY"
        except Exception:
            pass
        return masked

    def _edit_key(self, key_name: str, label: str):
        dialog = ctk.CTkInputDialog(
            text=f"Enter value for {label}:\n({key_name})\n\nLeave blank to cancel.",
            title=f"Set {key_name}")
        value = dialog.get_input()
        if not value or not value.strip():
            return
        value = value.strip()
        # Write via WSL — sanitize key_name to prevent shell injection
        safe_name = _shell_safe(key_name)
        escaped = value.replace("'", "'\\''")
        cmd = (f"grep -v '^export {safe_name}=' ~/.secrets > /tmp/_s_tmp && "
               f"echo \"export {safe_name}='{escaped}'\" >> /tmp/_s_tmp && "
               f"mv /tmp/_s_tmp ~/.secrets && echo ok")
        def _on_key_saved(o, e):
            self.after(0, lambda: (
                self._scan_lbl.configure(
                    text=f"✓ {key_name} saved" if "ok" in o else f"✗ {e[:40]}",
                    text_color=GREEN if "ok" in o else RED),
                self.after(400, self._load_keys)
            ))
        wsl_bg(cmd, _on_key_saved)

    def _add_custom_key(self):
        name_dialog = ctk.CTkInputDialog(
            text="Enter env var name (e.g. MY_API_KEY):", title="Add Key")
        name = name_dialog.get_input()
        if not name or not name.strip():
            return
        name = _shell_safe(name.strip().upper())
        # Trigger inference via fleet
        self._scan_lbl.configure(text=f"Inferring {name}...", text_color=ORANGE)
        cmd = (f"~/.local/bin/uv run python -c \""
               f"import sys,json; sys.path.insert(0,'.'); import db; db.init_db(); "
               f"tid=db.post_task('key_manager',"
               f"json.dumps({{\\\"action\\\":\\\"infer\\\",\\\"key_name\\\":\\\"{name}\\\"}})"
               f",priority=9); print('Task',tid,'queued')\"")
        wsl_bg(cmd, lambda o, e: self.after(0, lambda: self._scan_lbl.configure(
            text=f"Inference queued → check reports/key_scan.md", text_color=DIM)))
        # Still open edit dialog
        self._edit_key(name, name)

    def _scan_skills(self):
        self._scan_lbl.configure(text="Scanning...", text_color=ORANGE)
        cmd = (f"~/.local/bin/uv run python -c \""
               f"import sys,json; sys.path.insert(0,'.'); import db; db.init_db(); "
               f"tid=db.post_task('key_manager',"
               f"json.dumps({{\\\"action\\\":\\\"scan\\\"}})"
               f",priority=9); print('Task',tid,'queued')\"")
        wsl_bg(cmd, lambda o, e: self.after(0, lambda: self._scan_lbl.configure(
            text="Scan queued → knowledge/reports/key_scan.md", text_color=GREEN)))


# ─── Hardware Info Dialog ──────────────────────────────────────────────────────
class HardwareDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("BigEd CC — Hardware")
        self.geometry("600x480")
        self.configure(fg_color=BG)
        self.grab_set()

        ico = HERE / "brick.ico"
        if ico.exists():
            try: self.iconbitmap(str(ico))
            except Exception: pass

        self._build_ui()
        threading.Thread(target=self._load_hw, daemon=True).start()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(self, fg_color=BG3, height=46, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        ctk.CTkLabel(hdr, text="🖥  HARDWARE DETAILS",
                     font=("Segoe UI", 13, "bold"), text_color=GOLD
                     ).grid(row=0, column=0, padx=14, pady=10, sticky="w")

        self._text = ctk.CTkTextbox(self, font=("Consolas", 11),
                                    fg_color=BG2, text_color=TEXT,
                                    wrap="none", corner_radius=0)
        self._text.grid(row=1, column=0, sticky="nsew")
        self._text.insert("end", "Loading hardware info...")
        self._text.configure(state="disabled")

        bar = ctk.CTkFrame(self, fg_color=BG3, height=40, corner_radius=0)
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_propagate(False)
        ctk.CTkButton(bar, text="↻ Refresh", font=FONT_SM, width=90, height=28,
                      fg_color=BG2, hover_color=BG,
                      command=lambda: threading.Thread(
                          target=self._load_hw, daemon=True).start()
                      ).grid(row=0, column=0, padx=10, pady=6)

    def _load_hw(self):
        lines = []

        # ── CPU ──────────────────────────────────────────────────────────────
        lines.append("── CPU ─────────────────────────────────────────────────")
        try:
            cpu = psutil.cpu_freq()
            lines.append(f"  Name        : {self._cpu_name()}")
            lines.append(f"  Cores       : {psutil.cpu_count(logical=False)} physical  "
                         f"{psutil.cpu_count(logical=True)} logical")
            if cpu:
                lines.append(f"  Frequency   : {cpu.current:.0f} MHz  "
                              f"(max {cpu.max:.0f} MHz)")
            lines.append(f"  Usage       : {psutil.cpu_percent(interval=1):.1f}%")
        except Exception as e:
            lines.append(f"  Error: {e}")

        # ── RAM ───────────────────────────────────────────────────────────────
        lines.append("")
        lines.append("── RAM ─────────────────────────────────────────────────")
        try:
            vm = psutil.virtual_memory()
            sw = psutil.swap_memory()
            lines.append(f"  Total       : {vm.total/1e9:.1f} GB")
            lines.append(f"  Used        : {vm.used/1e9:.1f} GB  ({vm.percent:.1f}%)")
            lines.append(f"  Available   : {vm.available/1e9:.1f} GB")
            lines.append(f"  Swap        : {sw.used/1e9:.1f} / {sw.total/1e9:.1f} GB")
        except Exception as e:
            lines.append(f"  Error: {e}")

        # ── GPU ───────────────────────────────────────────────────────────────
        lines.append("")
        lines.append("── GPU ─────────────────────────────────────────────────")
        if _GPU_OK:
            try:
                name  = pynvml.nvmlDeviceGetName(_GPU_HANDLE)
                mem   = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
                util  = pynvml.nvmlDeviceGetUtilizationRates(_GPU_HANDLE)
                temp  = pynvml.nvmlDeviceGetTemperature(
                    _GPU_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
                power = pynvml.nvmlDeviceGetPowerUsage(_GPU_HANDLE) / 1000
                try:
                    plimit = pynvml.nvmlDeviceGetPowerManagementLimit(_GPU_HANDLE) / 1000
                except Exception:
                    plimit = 0
                driver = pynvml.nvmlSystemGetDriverVersion()
                cc     = pynvml.nvmlDeviceGetCudaComputeCapability(_GPU_HANDLE)
                lines.append(f"  Name        : {name}")
                lines.append(f"  VRAM Total  : {mem.total/1e9:.1f} GB")
                lines.append(f"  VRAM Used   : {mem.used/1e9:.2f} GB  "
                              f"({mem.used*100//mem.total}%)")
                lines.append(f"  VRAM Free   : {mem.free/1e9:.2f} GB")
                lines.append(f"  GPU Usage   : {util.gpu}%")
                lines.append(f"  Temp        : {temp}°C")
                lines.append(f"  Power       : {power:.1f}W"
                              + (f" / {plimit:.0f}W limit" if plimit else ""))
                lines.append(f"  Driver      : {driver}")
                lines.append(f"  CUDA CC     : {cc[0]}.{cc[1]}")
            except Exception as e:
                lines.append(f"  Error: {e}")
        else:
            lines.append("  pynvml not available — install nvidia-ml-py")
            lines.append("  Attempting nvidia-smi fallback...")
            try:
                r = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,"
                     "utilization.gpu,temperature.gpu,driver_version",
                     "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                if r.returncode == 0:
                    for field in r.stdout.strip().split(","):
                        lines.append(f"  {field.strip()}")
                else:
                    lines.append("  nvidia-smi not found")
            except Exception:
                lines.append("  GPU info unavailable")

        # ── Disk ──────────────────────────────────────────────────────────────
        lines.append("")
        lines.append("── DISK ────────────────────────────────────────────────")
        try:
            for part in psutil.disk_partitions():
                if "cdrom" in part.opts or part.fstype == "":
                    continue
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    lines.append(f"  {part.device:<12} {part.mountpoint:<14} "
                                 f"{usage.total/1e9:.0f} GB total  "
                                 f"{usage.used/1e9:.1f} GB used  "
                                 f"({usage.percent:.0f}%)")
                except Exception:
                    pass
        except Exception as e:
            lines.append(f"  Error: {e}")

        # ── Network interfaces ─────────────────────────────────────────────────
        lines.append("")
        lines.append("── NETWORK ─────────────────────────────────────────────")
        try:
            addrs = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
            for iface, addr_list in addrs.items():
                st = stats.get(iface)
                if st and not st.isup:
                    continue
                for addr in addr_list:
                    if addr.family.name == "AF_INET":
                        speed = f"  {st.speed} Mbps" if st and st.speed else ""
                        lines.append(f"  {iface:<18} {addr.address}{speed}")
        except Exception as e:
            lines.append(f"  Error: {e}")

        text = "\n".join(lines)
        self.after(0, lambda: self._set_text(text))

    def _cpu_name(self):
        """Get CPU name from Windows registry or fallback."""
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            winreg.CloseKey(key)
            return name.strip()
        except Exception:
            return platform.processor() or "Unknown"

    def _set_text(self, text: str):
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.insert("end", text)
        self._text.configure(state="disabled")


# ─── Console Base Class ───────────────────────────────────────────────────────
class _ConsoleBase(ctk.CTkToplevel):
    """Shared base for Claude and Gemini chat consoles."""
    SYSTEM_PROMPT = """\
You are an AI advisor integrated into BigEd CC, a local autonomous agent management system.
You help the operator manage, review, and direct the fleet via natural language.

Your capabilities:
- Read and interpret fleet status, agent health, task queue
- Review security advisories and findings
- Dispatch fleet tasks by outputting a JSON block the UI will execute
- Give strategic recommendations on market research, business ops, security posture
- Answer questions about the fleet's agents, skills, and findings

To dispatch a fleet task, output a line in this exact format (the UI parses it):
DISPATCH: {"skill": "skill_name", "payload": {...}}

Keep responses concise. Lead with the most important insight or action.
"""
    # Subclasses override these
    TITLE = ""
    HEADER_LABEL = ""
    HEADER_COLOR = BG3
    HEADER_TEXT_COLOR = GOLD
    CHAT_BG = BG2
    CTX_BTN_FG = BG3
    CTX_BTN_HOVER = BG
    SEND_BTN_FG = ACCENT
    SEND_BTN_HOVER = ACCENT_H
    ROLE_COLORS = {"user": GOLD, "system": DIM}
    ROLE_PREFIXES = {"user": "You", "system": "System"}
    ASSISTANT_ROLE = "assistant"

    def __init__(self, parent):
        super().__init__(parent)
        self.title(self.TITLE)
        self.geometry("820x620")
        self.configure(fg_color=BG)
        self.grab_set()

        ico = HERE / "brick.ico"
        if ico.exists():
            try: self.iconbitmap(str(ico))
            except Exception: pass

        self._history = []
        self._api_key = self._get_api_key()
        self._mcfg = load_model_cfg()
        self._build_ui()
        self._on_init()

    def _get_api_key(self):
        raise NotImplementedError

    def _on_init(self):
        raise NotImplementedError

    def _get_model_display(self) -> str:
        raise NotImplementedError

    def _build_model_widget(self, hdr):
        """Place the model indicator in the header. Override for interactive selectors."""
        self._model_lbl = ctk.CTkLabel(
            hdr, text=self._get_model_display(), font=("Segoe UI", 9), text_color=DIM)
        self._model_lbl.grid(row=0, column=1, padx=8, sticky="e")

    def _get_key_env_name(self) -> str:
        """Return the env var name for this console's API key."""
        raise NotImplementedError

    def _set_key_dialog(self):
        """Inline key entry — sets key for this session and optionally saves to ~/.secrets."""
        win = ctk.CTkToplevel(self)
        win.title("Set API Key")
        win.geometry("480x190")
        win.configure(fg_color=BG)
        win.grab_set()
        win.lift()

        env_name = self._get_key_env_name()
        ctk.CTkLabel(win, text=f"Paste your {env_name}:",
                     font=FONT_SM, text_color=DIM
                     ).pack(padx=14, pady=(14, 4), anchor="w")
        entry = ctk.CTkEntry(win, font=MONO, fg_color=BG2, border_color="#444",
                             text_color=TEXT, show="*")
        entry.pack(padx=14, fill="x")

        save_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(win, text=f"Save to ~/.secrets (export {env_name}=...)",
                        variable=save_var, font=FONT_SM, text_color=DIM,
                        fg_color=ACCENT, hover_color=ACCENT_H
                        ).pack(padx=14, pady=(8, 4), anchor="w")

        def _apply():
            key = entry.get().strip()
            if not key:
                return
            self._api_key = key
            if save_var.get():
                b64_key = base64.b64encode(key.encode()).decode()
                wsl_bg(
                    f"KEY=$(echo {b64_key} | base64 -d) && "
                    f"grep -v '^export {env_name}=' ~/.secrets > /tmp/_s_tmp 2>/dev/null; "
                    f"echo \"export {env_name}=$KEY\" >> /tmp/_s_tmp && "
                    f"mv /tmp/_s_tmp ~/.secrets",
                    lambda o, e: None,
                )
            if hasattr(self, '_init_model'):
                self._init_model()
            self._append("system", f"✓ {env_name} set — ready.")
            win.destroy()

        ctk.CTkButton(win, text="Apply", font=FONT_SM, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_H, command=_apply
                      ).pack(padx=14, pady=(6, 14), fill="x")

    def _get_context_buttons(self) -> list:
        """Return list of (label, callback) for context inject buttons."""
        return [
            ("Fleet Status",      self._inject_status),
            ("Pending Advisories", self._inject_advisories),
            ("Recent Reports",    self._inject_reports),
        ]

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color=self.HEADER_COLOR, height=46, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text=self.HEADER_LABEL,
                     font=("Segoe UI", 13, "bold"), text_color=self.HEADER_TEXT_COLOR
                     ).grid(row=0, column=0, padx=14, pady=10, sticky="w")
        self._build_model_widget(hdr)
        if self._get_key_env_name():
            ctk.CTkButton(
                hdr, text="🔑 Set Key", font=("Segoe UI", 9), width=80, height=26,
                fg_color=BG3, hover_color=BG, command=self._set_key_dialog
            ).grid(row=0, column=2, padx=(0, 10))

        # Chat history
        self._chat = ctk.CTkTextbox(
            self, font=("Segoe UI", 11), fg_color=self.CHAT_BG,
            text_color=TEXT, wrap="word", corner_radius=0)
        self._chat.grid(row=1, column=0, sticky="nsew")
        self._chat.configure(state="disabled")

        # Context inject buttons
        ctx_bar = ctk.CTkFrame(self, fg_color=BG3, height=34, corner_radius=0)
        ctx_bar.grid(row=2, column=0, sticky="ew")
        ctx_bar.grid_propagate(False)
        ctk.CTkLabel(ctx_bar, text="Inject context:", font=("Segoe UI", 9),
                     text_color=DIM).grid(row=0, column=0, padx=(10, 6), pady=6)
        for i, (lbl, fn) in enumerate(self._get_context_buttons()):
            ctk.CTkButton(ctx_bar, text=lbl, font=("Segoe UI", 9), height=22, width=0,
                          fg_color=self.CTX_BTN_FG, hover_color=self.CTX_BTN_HOVER,
                          command=fn).grid(row=0, column=i + 1, padx=3, pady=6)

        # Input bar
        bar = ctk.CTkFrame(self, fg_color=BG3, height=52, corner_radius=0)
        bar.grid(row=3, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)

        self._input = ctk.CTkEntry(
            bar, font=("Segoe UI", 11), fg_color=BG,
            border_color="#334", text_color=TEXT,
            placeholder_text="Type a message...")
        self._input.grid(row=0, column=0, padx=(10, 6), pady=10, sticky="ew")
        self._input.bind("<Return>", lambda e: self._send())

        self._send_btn = ctk.CTkButton(
            bar, text="Send", font=("Segoe UI", 11, "bold"),
            width=80, height=32,
            fg_color=self.SEND_BTN_FG, hover_color=self.SEND_BTN_HOVER,
            command=self._send)
        self._send_btn.grid(row=0, column=1, padx=(0, 10), pady=10)

        self._thinking_lbl = ctk.CTkLabel(
            bar, text="", font=("Segoe UI", 9), text_color=DIM)
        self._thinking_lbl.grid(row=0, column=2, padx=6)

    # ── Context injectors ─────────────────────────────────────────────────────
    def _inject_status(self):
        txt = STATUS_MD.read_text() if STATUS_MD.exists() else "STATUS.md not found"
        self._input.insert("end", f"\n\n[Fleet Status]\n{txt[:800]}")

    def _inject_advisories(self):
        if not PENDING_DIR.exists():
            self._input.insert("end", "\n\n[No pending advisories]")
            return
        files = list(PENDING_DIR.glob("advisory_*.md"))
        content = "\n\n".join(
            f.read_text(encoding="utf-8", errors="ignore")[:400]
            for f in files[:2])
        self._input.insert("end", f"\n\n[Pending Advisories]\n{content}")

    def _inject_reports(self):
        if not REPORTS_DIR.exists():
            self._input.insert("end", "\n\n[No reports]")
            return
        files = sorted(REPORTS_DIR.glob("*.md"), reverse=True)[:2]
        content = "\n\n".join(
            f"--- {f.name} ---\n" + f.read_text(encoding="utf-8", errors="ignore")[:400]
            for f in files)
        self._input.insert("end", f"\n\n[Recent Reports]\n{content}")

    # ── Chat ──────────────────────────────────────────────────────────────────
    # ── Thinking animation ─────────────────────────────────────────────────────
    _DOTS = ("·", "··", "···", "··")

    def _start_thinking_animation(self, label: str, color: str):
        self._thinking_anim_label = label
        self._thinking_anim_color = color
        self._thinking_anim_step  = 0
        self._thinking_anim_id    = None
        self._thinking_anim_tick()

    def _thinking_anim_tick(self):
        dots = self._DOTS[self._thinking_anim_step % len(self._DOTS)]
        self._thinking_lbl.configure(
            text=f"{self._thinking_anim_label}{dots}",
            text_color=self._thinking_anim_color,
        )
        self._thinking_anim_step += 1
        self._thinking_anim_id = self.after(420, self._thinking_anim_tick)

    def _stop_thinking_animation(self):
        if self._thinking_anim_id is not None:
            self.after_cancel(self._thinking_anim_id)
            self._thinking_anim_id = None
        self._thinking_lbl.configure(text="")

    def _thinking_label_for(self, text: str) -> tuple[str, str]:
        """Return (label, color) based on message content. Override in subclasses."""
        return "● drafting", ORANGE

    # ── Chat ──────────────────────────────────────────────────────────────────
    def _send(self):
        text = self._input.get().strip()
        if not text or not self._api_key:
            return
        if not self._can_send():
            return
        self._input.delete(0, "end")
        self._append("user", text)
        self._send_btn.configure(state="disabled")
        label, color = self._thinking_label_for(text)
        self._start_thinking_animation(label, color)
        self._do_send(text)

    def _can_send(self) -> bool:
        return True

    def _do_send(self, text: str):
        raise NotImplementedError

    def _on_reply(self, reply: str):
        self._stop_thinking_animation()
        self._append(self.ASSISTANT_ROLE, reply)
        self._send_btn.configure(state="normal")

        # Parse and execute any DISPATCH: lines
        for m in re.finditer(r'DISPATCH:\s*(\{.*?\})', reply, re.DOTALL):
            try:
                data = json.loads(m.group(1))
                skill   = data.get("skill", "")
                payload = data.get("payload", {})
                if skill:
                    self._execute_dispatch(skill, payload)
            except Exception:
                pass

    def _execute_dispatch(self, skill: str, payload: dict):
        safe_skill = _shell_safe(skill)
        b64 = base64.b64encode(json.dumps(payload).encode()).decode()
        cmd = (f"~/.local/bin/uv run python -c \""
               f"import sys,base64; sys.path.insert(0,'.'); import db; db.init_db(); "
               f"p=base64.b64decode('{b64}').decode(); "
               f"tid=db.post_task('{safe_skill}',p,priority=9); "
               f"print('Dispatched',tid)\"")
        wsl_bg(cmd, lambda o, e: self.after(0, lambda: self._append(
            "system", f"✓ Dispatched {safe_skill} (task {o.split()[-1] if o else '?'})")))

    def _append(self, role: str, text: str):
        prefix = self.ROLE_PREFIXES.get(role, role.title())
        self._chat.configure(state="normal")
        self._chat.insert("end", f"\n{prefix}:\n")
        self._chat.insert("end", f"{text}\n\n")
        self._chat.see("end")
        self._chat.configure(state="disabled")


# ─── Claude Console ───────────────────────────────────────────────────────────
class ClaudeConsole(_ConsoleBase):
    SYSTEM_PROMPT = _ConsoleBase.SYSTEM_PROMPT.replace(
        "Your capabilities:", (
            "You are the executive AI advisor (C-suite) for a local autonomous agent fleet "
            "called BigEd CC.\n\nYour capabilities:"))
    TITLE = "BigEd CC — Claude Console"
    HEADER_LABEL = "🤖  CLAUDE CONSOLE  —  C-Suite Mode"
    HEADER_COLOR = "#0d0d1a"
    HEADER_TEXT_COLOR = "#7b9fff"
    CHAT_BG = "#0f0f1f"
    CTX_BTN_FG = "#1a1a2e"
    CTX_BTN_HOVER = "#252540"
    SEND_BTN_FG = "#334488"
    SEND_BTN_HOVER = "#445599"
    ASSISTANT_ROLE = "claude"
    ROLE_PREFIXES = {"user": "You", "claude": "Claude", "system": "System"}

    def _get_api_key(self):
        try:
            out, _ = wsl("echo $ANTHROPIC_API_KEY", capture=True)
            return out.strip() if out.strip() and not out.strip().startswith("$") else None
        except Exception:
            return os.environ.get("ANTHROPIC_API_KEY", "")

    # Available API models — label: model_id
    _CLAUDE_MODELS = {
        "Haiku 4.5  · fast / cheap":   "claude-haiku-4-5-20251001",
        "Sonnet 4.6 · balanced":        "claude-sonnet-4-6",
        "Opus 4.6   · most capable":    "claude-opus-4-6",
    }

    def _get_key_env_name(self):
        return "ANTHROPIC_API_KEY"

    def _get_model_display(self):
        return self._mcfg["claude_model"]

    def _build_model_widget(self, hdr):
        default_id  = self._mcfg.get("claude_model", "claude-sonnet-4-6")
        # Find the label whose value matches the configured model (fall back to Sonnet)
        default_lbl = next(
            (lbl for lbl, mid in self._CLAUDE_MODELS.items() if mid == default_id),
            "Sonnet 4.6 · balanced",
        )
        self._model_var = ctk.StringVar(value=default_lbl)
        ctk.CTkOptionMenu(
            hdr,
            values=list(self._CLAUDE_MODELS),
            variable=self._model_var,
            font=("Segoe UI", 9),
            fg_color=BG3,
            button_color=BG2,
            button_hover_color=BG,
            dropdown_fg_color=BG2,
            dropdown_hover_color=BG3,
            text_color=TEXT,
            width=200,
            height=26,
            dynamic_resizing=False,
        ).grid(row=0, column=1, padx=8, sticky="e")

    def _on_init(self):
        if not self._api_key:
            self._append("system",
                         "⚠  ANTHROPIC_API_KEY not found in ~/.secrets.\n"
                         "Click 🔑 Set Key in the header to enter your key now.\n\n"
                         "Note: this console requires an Anthropic API key (console.anthropic.com).\n"
                         "Claude.ai subscriptions (claude.ai) are separate — they cannot be used here.")
        else:
            self._append("system", "Claude Console ready — C-suite mode active.\n"
                         "Type a message or ask Claude to manage the fleet.")

    def _get_context_buttons(self):
        return super()._get_context_buttons() + [
            ("Key Status", self._inject_key_status),
        ]

    def _inject_key_status(self):
        try:
            out, _ = wsl("cat ~/.secrets 2>/dev/null | grep -v '^#' | cut -d= -f1", capture=True)
            keys = [l.replace("export ", "").strip() for l in out.splitlines() if l.strip()]
            self._input.insert("end", f"\n\n[Configured Keys]\n" + ", ".join(keys))
        except Exception:
            self._input.insert("end", "\n\n[Could not read key status]")

    def _do_send(self, text: str):
        self._history.append({"role": "user", "content": text})
        threading.Thread(target=self._call_api, daemon=True).start()

    def _call_api(self):
        model_id = self._CLAUDE_MODELS.get(
            self._model_var.get(), self._mcfg.get("claude_model", "claude-sonnet-4-6"))
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._api_key)
            msg = client.messages.create(
                model=model_id,
                max_tokens=1024,
                system=self.SYSTEM_PROMPT,
                messages=self._history[-20:],
            )
            reply = msg.content[0].text
            self._history.append({"role": "assistant", "content": reply})
            self.after(0, lambda: self._on_reply(reply))
        except Exception as e:
            self.after(0, lambda: self._on_reply(f"[API Error] {e}"))


# ─── Gemini Console ───────────────────────────────────────────────────────────
class GeminiConsole(_ConsoleBase):
    TITLE = "BigEd CC — Gemini Console"
    HEADER_LABEL = "✦  GEMINI CONSOLE"
    HEADER_COLOR = "#0d1a0d"
    HEADER_TEXT_COLOR = "#4db86b"
    CHAT_BG = "#0d1a0d"
    CTX_BTN_FG = "#1a2a1a"
    CTX_BTN_HOVER = "#253525"
    SEND_BTN_FG = "#2a5a2a"
    SEND_BTN_HOVER = "#3a6a3a"
    ASSISTANT_ROLE = "gemini"
    ROLE_PREFIXES = {"user": "You", "gemini": "Gemini", "system": "System"}

    def __init__(self, parent):
        self._chat_session = None
        super().__init__(parent)

    def _get_api_key(self):
        try:
            out, _ = wsl("echo $GEMINI_API_KEY", capture=True)
            key = out.strip()
            return key if key and not key.startswith("$") else None
        except Exception:
            return os.environ.get("GEMINI_API_KEY", "") or None

    def _get_key_env_name(self):
        return "GEMINI_API_KEY"

    def _get_model_display(self):
        return self._mcfg["gemini_model"]

    def _on_init(self):
        if not self._api_key:
            self._append("system", "⚠  GEMINI_API_KEY not found in ~/.secrets.\n"
                         "Click 🔑 Set Key in the header to enter your key now.")
        else:
            self._init_model()
            self._append("system", "Gemini Console ready.\nType a message to begin.")

    def _get_context_buttons(self):
        return super()._get_context_buttons() + [
            ("Key Status", self._inject_key_status),
        ]

    def _inject_key_status(self):
        try:
            out, _ = wsl("cat ~/.secrets 2>/dev/null | grep -v '^#' | cut -d= -f1", capture=True)
            keys = [l.replace("export ", "").strip() for l in out.splitlines() if l.strip()]
            self._input.insert("end", "\n\n[Configured Keys]\n" + ", ".join(keys))
        except Exception:
            self._input.insert("end", "\n\n[Could not read key status]")

    def _init_model(self):
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=self._api_key)
            self._chat_session = client.chats.create(
                model=self._mcfg["gemini_model"],
                config=types.GenerateContentConfig(
                    system_instruction=self.SYSTEM_PROMPT,
                    temperature=0.2,
                ),
            )
        except Exception as e:
            self._append("system", f"[Init error] {e}")

    def _thinking_label_for(self, text: str) -> tuple[str, str]:
        if text.strip().lower().startswith("/think"):
            return "◈ extended thinking", "#4db86b"   # green — matches Gemini header colour
        return "● drafting", ORANGE

    def _can_send(self) -> bool:
        return self._chat_session is not None

    def _do_send(self, text: str):
        threading.Thread(target=self._call_api, args=(text,), daemon=True).start()

    def _call_api(self, text: str):
        try:
            response = self._chat_session.send_message(text)
            reply = response.text
            self.after(0, lambda: self._on_reply(reply))
        except Exception as e:
            self.after(0, lambda: self._on_reply(f"[API Error] {e}"))


# ─── Local (Ollama) Console ───────────────────────────────────────────────────
class LocalConsole(_ConsoleBase):
    TITLE = "BigEd CC — Local Console"
    HEADER_LABEL = "⚡  LOCAL CONSOLE  —  Ollama"
    HEADER_COLOR = "#1a1510"
    HEADER_TEXT_COLOR = "#d4a84b"
    CHAT_BG = "#1a1510"
    CTX_BTN_FG = "#2a2010"
    CTX_BTN_HOVER = "#3a3020"
    SEND_BTN_FG = "#6b4c1a"
    SEND_BTN_HOVER = "#8b6c2a"
    ASSISTANT_ROLE = "ollama"
    ROLE_PREFIXES = {"user": "You", "ollama": "Ollama", "system": "System"}

    def _get_api_key(self):
        return "local"  # no key needed

    def _get_key_env_name(self):
        return ""

    def _get_model_display(self):
        return self._mcfg.get("local", "qwen3:8b")

    def _build_model_widget(self, hdr):
        self._model_lbl = ctk.CTkLabel(
            hdr, text=self._get_model_display(), font=("Segoe UI", 9), text_color=DIM)
        self._model_lbl.grid(row=0, column=1, padx=8, sticky="e")

    def _set_key_dialog(self):
        pass  # no key needed for local

    def _on_init(self):
        host = self._mcfg.get("ollama_host", "http://localhost:11434")
        model = self._get_model_display()
        try:
            req = urllib.request.Request(f"{host}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=3) as r:
                data = json.loads(r.read())
                loaded = [m["name"] for m in data.get("models", [])]
            if loaded:
                self._append("system",
                             f"Local Console ready — {model} via Ollama.\n"
                             f"Loaded models: {', '.join(loaded)}\n"
                             "Type a message or ask Ollama to manage the fleet.")
            else:
                self._append("system",
                             f"Ollama is running but no models loaded.\n"
                             f"Model '{model}' will be loaded on first message.")
        except Exception:
            self._append("system",
                         f"⚠ Ollama not reachable at {host}.\n"
                         "Start Ollama from the main panel, then reopen this console.")

    def _do_send(self, text: str):
        self._history.append({"role": "user", "content": text})
        threading.Thread(target=self._call_ollama, daemon=True).start()

    def _call_ollama(self):
        host = self._mcfg.get("ollama_host", "http://localhost:11434")
        model = self._mcfg.get("local", "qwen3:8b")
        messages = [{"role": "system", "content": self.SYSTEM_PROMPT}] + self._history[-20:]
        body = json.dumps({
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": "24h",
        }).encode()
        req = urllib.request.Request(
            f"{host}/api/chat", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            reply = data.get("message", {}).get("content", "(empty response)")
            self._history.append({"role": "assistant", "content": reply})
            self.after(0, lambda: self._on_reply(reply))
        except Exception as e:
            self.after(0, lambda: self._on_reply(f"[Ollama Error] {e}"))


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
            text = FLEET_TOML.read_text(encoding="utf-8")
            enabled = str(self._enabled_var.get()).lower()
            provider = self._provider_var.get()
            # Update or append [review] block
            local_think = str(self._think_var.get()).lower()
            block = (f"[review]\n"
                     f"enabled = {enabled}\n"
                     f"provider = \"{provider}\"\n"
                     f"claude_model = \"{self._cfg['claude_model']}\"\n"
                     f"gemini_model = \"{self._cfg['gemini_model']}\"\n"
                     f"local_model = \"{self._local_model_var.get()}\"\n"
                     f"local_ctx = {self._cfg['local_ctx']}\n"
                     f"local_think = {local_think}\n")
            if re.search(r'^\[review\]', text, re.M):
                text = re.sub(r'\[review\].*?(?=\n\[|\Z)', block, text, flags=re.S)
            else:
                text = text.rstrip() + "\n\n" + block
            FLEET_TOML.write_text(text, encoding="utf-8")
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


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = BigEdCC()
    app.mainloop()
