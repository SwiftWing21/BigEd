"""BigEd CC UI Theme — single source of truth for colors, fonts, and themes."""
import ctypes
import json as _json
import sys
from pathlib import Path

# ─── Custom Font Loader ──────────────────────────────────────────────────────
_FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
_FONTS_LOADED = False

# Font family names (registered in TTF metadata)
RS_PLAIN_11 = "RuneScape Plain 11"
RS_PLAIN_12 = "RuneScape Plain 12"
RS_BOLD_12  = "RuneScape Bold 12"

def load_custom_fonts():
    """Load bundled TTF fonts into the Windows font table (private, session-only)."""
    global _FONTS_LOADED
    if _FONTS_LOADED or sys.platform != "win32":
        return
    try:
        FR_PRIVATE = 0x10
        for ttf in _FONT_DIR.glob("*.ttf"):
            ctypes.windll.gdi32.AddFontResourceExW(str(ttf), FR_PRIVATE, 0)
        _FONTS_LOADED = True
    except Exception as e:
        print(f"[WARN] Custom font loading failed: {e}", file=sys.stderr)

# Fonts loaded lazily — call load_custom_fonts() after window creation

# ─── Theme Presets ──────────────────────────────────────────────────────────
THEME_PRESETS = {
    "Classic": {
        "BG": "#1a1a1a", "BG2": "#242424", "BG3": "#2d2d2d",
        "ACCENT": "#b22222", "ACCENT_H": "#8b0000", "GOLD": "#c8a84b",
        "TEXT": "#e2e2e2", "DIM": "#888888",
        "CARD_RADIUS": 8, "BTN_RADIUS": 4,
        "GLASS_BG": "#0f0f0f", "GLASS_NAV": "#141414",
        "GLASS_PANEL": "#181818", "GLASS_HOVER": "#222222",
        "GLASS_SEL": "#1a1a2e", "GLASS_BORDER": "#2a2a2a",
    },
    "Modern": {
        "BG": "#0f1117", "BG2": "#1a1e2e", "BG3": "#252a3a",
        "ACCENT": "#c0392b", "ACCENT_H": "#96281b", "GOLD": "#d4a84b",
        "TEXT": "#ecf0f1", "DIM": "#7f8c8d",
        "CARD_RADIUS": 12, "BTN_RADIUS": 8,
        "GLASS_BG": "#0a0d14", "GLASS_NAV": "#111520",
        "GLASS_PANEL": "#161b28", "GLASS_HOVER": "#1e2436",
        "GLASS_SEL": "#182040", "GLASS_BORDER": "#2a3045",
    },
    "Figma": {
        "BG": "#1e1e1e", "BG2": "#2c2c2c", "BG3": "#383838",
        "ACCENT": "#7c3aed", "ACCENT_H": "#6d28d9", "GOLD": "#d4a84b",
        "TEXT": "#e0e0e0", "DIM": "#a0a0a0",
        "CARD_RADIUS": 12, "BTN_RADIUS": 8,
        "GLASS_BG": "#1a1a1a", "GLASS_NAV": "#232323",
        "GLASS_PANEL": "#2a2a2a", "GLASS_HOVER": "#333333",
        "GLASS_SEL": "#3a3a3a", "GLASS_BORDER": "#404040",
    },
}

def _load_theme_pref() -> str:
    """Load saved theme preference from settings.json (same pattern as _load_font_pref)."""
    try:
        settings_file = Path(__file__).resolve().parent.parent / "data" / "settings.json"
        if settings_file.exists():
            data = _json.loads(settings_file.read_text(encoding="utf-8"))
            pref = data.get("theme_preset", "Figma")
            if pref in THEME_PRESETS:
                return pref
    except Exception:
        pass
    return "Figma"  # default for fresh installs (v0.170+)

_active_theme_name = _load_theme_pref()
_theme = THEME_PRESETS[_active_theme_name]

# Backgrounds (resolved from active theme)
BG       = _theme["BG"]
BG2      = _theme["BG2"]
BG3      = _theme["BG3"]

# Accents (resolved from active theme)
ACCENT   = _theme["ACCENT"]
ACCENT_H = _theme["ACCENT_H"]
GOLD     = _theme["GOLD"]
BRAND    = "#00bcd4"  # BigEd brand color (teal) — header, icon, primary accent

# Text (resolved from active theme)
TEXT     = _theme["TEXT"]
DIM      = _theme["DIM"]

# Status
GREEN    = "#4caf50"
ORANGE   = "#ff9800"
RED      = "#f44336"

# Provider colors (for Fleet Comm unified console)
PROVIDER_LOCAL    = "#d4a84b"   # gold — Ollama
PROVIDER_CLAUDE   = "#6b8afd"  # blue — Anthropic
PROVIDER_GEMINI   = "#4caf50"  # green — Google
PROVIDER_OAUTH    = "#9c7cfc"   # purple — VS Code OAuth
PROVIDER_BG_LOCAL  = "#2a2010"
PROVIDER_BG_CLAUDE = "#0d0d2a"
PROVIDER_BG_GEMINI = "#0d1a0d"

# ─── Font Presets ────────────────────────────────────────────────────────────
FONT_PRESETS = {
    "System Default": {
        "family": "Segoe UI" if sys.platform == "win32" else "Helvetica",
        "mono": "Consolas" if sys.platform == "win32" else "Menlo" if sys.platform == "darwin" else "Monospace",
        "bold": "Segoe UI" if sys.platform == "win32" else "Helvetica",
    },
    "RuneScape": {
        "family": RS_PLAIN_12,
        "mono": "Consolas" if sys.platform == "win32" else "Menlo" if sys.platform == "darwin" else "Monospace",
        "bold": RS_BOLD_12,
    },
    "Consolas": {
        "family": "Consolas" if sys.platform == "win32" else "Menlo" if sys.platform == "darwin" else "Monospace",
        "mono": "Consolas" if sys.platform == "win32" else "Menlo" if sys.platform == "darwin" else "Monospace",
        "bold": "Consolas" if sys.platform == "win32" else "Menlo" if sys.platform == "darwin" else "Monospace",
    },
    "Courier New": {
        "family": "Courier New",
        "mono": "Courier New",
        "bold": "Courier New",
    },
}

def _load_font_pref() -> str:
    """Load saved font preference from settings."""
    try:
        import json
        settings_file = Path(__file__).resolve().parent.parent / "data" / "settings.json"
        if settings_file.exists():
            data = json.loads(settings_file.read_text(encoding="utf-8"))
            return data.get("font_preset", "System Default")
    except Exception:
        pass
    return "System Default"

_active_preset = _load_font_pref()
_preset = FONT_PRESETS.get(_active_preset, FONT_PRESETS["System Default"])

# Fonts (resolved from active preset)
MONO     = (_preset["mono"], 11)
FONT     = (_preset["family"], 12)
FONT_SM  = (_preset["family"], 11)
FONT_H   = (_preset["bold"], 14, "bold")

# Status colors (counter cards, agent cards)
BLUE     = "#4fc3f7"
CYAN     = "#00bcd4"
YELLOW   = "#ffd54f"

# Subtle backgrounds (sidebar buttons, hover states)
BG_START   = "#1e3a1e"   # fleet start button
BG_START_H = "#2a4a2a"   # fleet start hover
BG_DASH    = "#1a2a3a"   # dashboard button
BG_DASH_H  = "#253545"   # dashboard hover
BG_DANGER  = "#5a2020"   # uninstall/destructive
BG_DANGER_H = "#6a2828"  # destructive hover

# Sidebar (modernized — accent bars, rounded buttons, active state)
SB_HOVER      = "#2a2a3a"   # button hover (subtle purple-gray)
SB_ACTIVE_BG  = "#252535"   # active item background tint
SB_BTN_RADIUS = 6           # rounded button corners
SB_BTN_HEIGHT = 30          # standard sidebar button height
FONT_SB_SECTION = (_preset["bold"], 11, "bold")  # section header (slightly larger)

# Glass palette (settings dialog — resolved from active theme)
GLASS_BG     = _theme["GLASS_BG"]
GLASS_NAV    = _theme["GLASS_NAV"]
GLASS_PANEL  = _theme["GLASS_PANEL"]
GLASS_HOVER  = _theme["GLASS_HOVER"]
GLASS_SEL    = _theme["GLASS_SEL"]
GLASS_BORDER = _theme["GLASS_BORDER"]

# Counter card colors
COUNTER_COLORS = {
    "total": BLUE,
    "idle": GREEN,
    "busy": ORANGE,
    "pending": YELLOW,
    "done": DIM,
    "waiting": ORANGE,
    "models": CYAN,
}

# Font hierarchy (resolved from active preset)
FONT_XS    = (_preset["family"], 9)           # timestamps, metadata
FONT_STAT  = (_preset["mono"], 10)            # stats, numbers (keep mono)
FONT_MONO  = (_preset["mono"], 11)            # code, values (keep mono)
FONT_BOLD  = (_preset["bold"], 12, "bold")    # agent names, emphasis
FONT_TITLE = (_preset["bold"], 15, "bold")    # section titles

# Dimensions (radius values resolved from active theme)
CARD_RADIUS   = _theme["CARD_RADIUS"]
BTN_RADIUS    = _theme["BTN_RADIUS"]
CARD_PAD      = 4      # standard card padding
BTN_HEIGHT    = 28     # standard button height
BTN_HEIGHT_SM = 20     # small inline buttons
TAB_HEIGHT    = 42     # tab bar height
HEADER_HEIGHT = 60     # main header height

# ─── UI Scale ────────────────────────────────────────────────────────────────
import customtkinter as ctk

_scale: float = 1.0

def apply_scale(factor: float):
    """Clamp factor to [0.75, 1.5] and apply globally via CTK widget scaling."""
    global _scale
    _scale = max(0.75, min(1.5, factor))
    ctk.set_widget_scaling(_scale)

def scaled_font(name: str, size: int, *weight: str) -> tuple:
    """Return a font tuple with size adjusted by current scale factor."""
    return (name, int(size * _scale), *weight)
