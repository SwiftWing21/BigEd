"""BigEd CC UI Theme — single source of truth for colors and fonts."""

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
FONT     = ("Segoe UI", 11)
FONT_SM  = ("Segoe UI", 10)
FONT_H   = ("Segoe UI", 13, "bold")

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
FONT_XS    = ("Consolas", 8)           # timestamps, metadata
FONT_STAT  = ("Consolas", 10)          # stats, status text
FONT_MONO  = ("Consolas", 11)          # code, values (same as MONO)
FONT_BOLD  = ("Segoe UI", 11, "bold")  # agent names, emphasis
FONT_TITLE = ("Segoe UI", 14, "bold")  # section titles

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
