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
