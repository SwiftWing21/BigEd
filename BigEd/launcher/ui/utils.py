"""UI utility functions and small widgets extracted from launcher.py.

Phase 1 of Hybrid ViewPort refactor (TECH_DEBT 4.1).
Contains module-level helpers, settings persistence, Tooltip widget,
shell/bridge wrappers, and agent-theme utilities.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import tkinter as tk
import customtkinter as ctk


# ─── Paths (computed independently — utils.py lives in ui/) ──────────────────
# PyInstaller bundles assets into sys._MEIPASS; fall back to script dir
if getattr(sys, "frozen", False):
    _LAUNCHER_DIR = Path(sys._MEIPASS)
else:
    _LAUNCHER_DIR = Path(__file__).parent.parent  # ui/ -> launcher/

DATA_DIR = _LAUNCHER_DIR / "data"


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


# ─── Time helpers ─────────────────────────────────────────────────────────────

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


# ─── Settings persistence ────────────────────────────────────────────────────

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


# ─── Agent themed names ──────────────────────────────────────────────────────

def themed_name(role: str) -> str:
    """Get the display name for an agent role.

    Priority: custom name > theme name > title-cased role.

    Accesses ``_active_theme`` and ``_custom_names`` from the launcher module
    via lazy import to avoid circular dependencies.
    """
    import launcher
    _custom_names = launcher._custom_names
    _active_theme = launcher._active_theme

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


# ─── Shell helpers ────────────────────────────────────────────────────────────

def _shell_safe(s: str) -> str:
    """Sanitize a string for safe shell interpolation (alphanumeric, _, -, . only)."""
    return re.sub(r'[^a-zA-Z0-9_.\-:]', '', s)


def wsl(cmd: str, capture=False, timeout=60):
    """Run a command in the fleet environment (WSL on Windows, native on Linux/macOS).

    Accesses ``_HAS_BRIDGE`` and ``_bridge`` from the launcher module
    via lazy import to avoid circular dependencies.
    """
    import launcher
    if not launcher._HAS_BRIDGE or launcher._bridge is None:
        return "" if capture else None
    return launcher._bridge.run(cmd, capture=capture, timeout=timeout)


def wsl_bg(cmd: str, callback=None, timeout=60):
    """Run fleet command in a background thread; call callback(stdout, stderr) when done.

    Accesses ``_HAS_BRIDGE`` and ``_bridge`` from the launcher module
    via lazy import to avoid circular dependencies.
    """
    import launcher
    if not launcher._HAS_BRIDGE or launcher._bridge is None:
        if callback:
            callback("", "fleet_bridge not available")
        return
    launcher._bridge.run_bg(cmd, callback=callback, timeout=timeout)


# ─── Context preview dialog ──────────────────────────────────────────────────

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
    ctk.CTkLabel(tos_frame, text=f"\u26a0  {tos}",
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
