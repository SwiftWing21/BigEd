"""BigEd CC UI Theme — single source of truth for colors and fonts."""
import ctypes
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
        import sys
        print(f"[WARN] Custom font loading failed: {e}", file=sys.stderr)

# Load fonts at import time
load_custom_fonts()

# Backgrounds
BG       = "#1a1a1a"
BG2      = "#242424"
BG3      = "#2d2d2d"

# Accents
ACCENT   = "#b22222"
ACCENT_H = "#8b0000"
GOLD     = "#c8a84b"

# Text
TEXT     = "#e2e2e2"
DIM      = "#888888"

# Status
GREEN    = "#4caf50"
ORANGE   = "#ff9800"
RED      = "#f44336"

# Fonts
MONO     = ("Consolas", 11)
FONT     = (RS_PLAIN_12, 12)
FONT_SM  = (RS_PLAIN_11, 11)
FONT_H   = (RS_BOLD_12, 14, "bold")

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

# Glass palette (settings dialog)
GLASS_BG     = "#0f0f0f"
GLASS_NAV    = "#141414"
GLASS_PANEL  = "#181818"
GLASS_HOVER  = "#222222"
GLASS_SEL    = "#1a1a2e"
GLASS_BORDER = "#2a2a2a"

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

# Font hierarchy
FONT_XS    = (RS_PLAIN_11, 9)                # timestamps, metadata
FONT_STAT  = ("Consolas", 10)                # stats, numbers (keep mono)
FONT_MONO  = ("Consolas", 11)                # code, values (keep mono)
FONT_BOLD  = (RS_BOLD_12, 12, "bold")        # agent names, emphasis
FONT_TITLE = (RS_BOLD_12, 15, "bold")        # section titles

# Dimensions
CARD_RADIUS   = 8
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
