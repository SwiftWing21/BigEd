"""
BigEd CC — Settings package.
Split from monolithic settings.py into focused panel modules (0.31.01).
"""
import base64
import json
import re
import subprocess
import threading
from pathlib import Path

import customtkinter as ctk
import psutil

from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    GREEN, ORANGE, RED, MONO, FONT, FONT_SM, FONT_H,
    BLUE, FONT_XS, FONT_STAT, FONT_BOLD, FONT_TITLE,
    GLASS_BG, GLASS_NAV, GLASS_PANEL, GLASS_HOVER, GLASS_SEL, GLASS_BORDER,
)


def _launcher():
    """Return the launcher module (import once, cache)."""
    import launcher as _mod
    return _mod


_SETTINGS_NAV = [
    ("General",    "general"),
    ("Display",    "display"),
    ("Models",     "models"),
    ("Hardware",   "hardware"),
    ("API Keys",   "keys"),
    ("Review",     "review"),
    ("Operations", "operations"),
    ("MCP Servers", "mcp"),
]


# Import panel mixins
from ui.settings.general import GeneralPanelMixin
from ui.settings.display import DisplayPanelMixin
from ui.settings.models import ModelsPanelMixin
from ui.settings.hardware import HardwarePanelMixin
from ui.settings.keys import KeysPanelMixin
from ui.settings.review import ReviewPanelMixin
from ui.settings.operations import OperationsPanelMixin
from ui.settings.mcp import McpPanelMixin, MCPWizardDialog
from ui.settings.names import AgentNamesDialog
from ui.settings.keys import KeyManagerDialog


class SettingsDialog(
    GeneralPanelMixin,
    DisplayPanelMixin,
    ModelsPanelMixin,
    HardwarePanelMixin,
    KeysPanelMixin,
    ReviewPanelMixin,
    OperationsPanelMixin,
    McpPanelMixin,
    ctk.CTkToplevel,
):
    """Unified settings panel — dark glass look with left nav + content area."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("BigEd CC — Settings")
        self.geometry("820x580")
        self.minsize(700, 480)
        self.configure(fg_color=GLASS_BG)
        self.grab_set()
        self._parent = parent
        self._nav_buttons = {}
        self._panels = {}
        self._active_section = None

        L = _launcher()
        ico = L.HERE / "brick.ico"
        if ico.exists():
            try: self.iconbitmap(str(ico))
            except Exception: pass

        self._settings = L._load_settings()

        self._build_ui()
        self._show_section("general")

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── Header ────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color=GLASS_BG, height=56, corner_radius=0)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(2, weight=1)

        stripe = ctk.CTkFrame(hdr, fg_color=GOLD, width=3, corner_radius=0)
        stripe.grid(row=0, column=0, sticky="ns", padx=0)

        title_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        title_frame.grid(row=0, column=1, padx=(12, 0), pady=8, sticky="w")
        ctk.CTkLabel(title_frame, text="SETTINGS",
                     font=FONT_TITLE, text_color=GOLD).pack(anchor="w")
        ctk.CTkLabel(title_frame, text="fleet configuration & preferences",
                     font=FONT_XS, text_color=DIM).pack(anchor="w")

        ctk.CTkLabel(hdr, text=" v0.31 ", font=FONT_XS,
                     text_color=DIM, fg_color=GLASS_NAV,
                     corner_radius=8).grid(row=0, column=2, padx=16, sticky="e")

        # ── Left nav ────────────────────────────────────────────────────
        nav = ctk.CTkFrame(self, fg_color=GLASS_NAV, width=170, corner_radius=0)
        nav.grid(row=1, column=0, sticky="nsew")
        nav.grid_propagate(False)

        ctk.CTkLabel(nav, text="SECTIONS", font=FONT_XS,
                     text_color="#444").pack(padx=14, pady=(12, 6), anchor="w")

        for i, (label, key) in enumerate(_SETTINGS_NAV):
            b = ctk.CTkButton(
                nav, text=f"  {label}", font=FONT_SM,
                fg_color="transparent", hover_color=GLASS_HOVER,
                text_color=DIM, anchor="w", height=34, corner_radius=4,
                command=lambda k=key: self._show_section(k),
            )
            b.pack(fill="x", padx=6, pady=1)
            self._nav_buttons[key] = b

        # ── Content area ────────────────────────────────────────────────
        self._content = ctk.CTkFrame(self, fg_color=GLASS_PANEL, corner_radius=0)
        self._content.grid(row=1, column=1, sticky="nsew")
        self._content.grid_columnconfigure(0, weight=1)
        self._content.grid_rowconfigure(0, weight=1)

        # Build all panels (methods from mixins)
        self._build_general_panel()
        self._build_display_panel()
        self._build_models_panel()
        self._build_hardware_panel()
        self._build_keys_panel()
        self._build_review_panel()
        self._build_operations_panel()
        self._build_mcp_panel()

    def _show_section(self, key: str):
        if self._active_section == key:
            return
        for k, b in self._nav_buttons.items():
            if k == key:
                b.configure(fg_color=GLASS_SEL, text_color=GOLD)
            else:
                b.configure(fg_color="transparent", text_color=DIM)
        for k, panel in self._panels.items():
            if k == key:
                panel.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
            else:
                panel.grid_forget()
        self._active_section = key

    # ── Section header helpers (used by all panels) ───────────────────
    def _section_header(self, parent, text: str):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=16, pady=(16, 6))
        ctk.CTkFrame(frame, fg_color=GOLD, width=3, height=14,
                     corner_radius=1).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(frame, text=text.upper(), font=FONT_BOLD,
                     text_color=GOLD).pack(side="left")

    def _update_toml_value(self, section: str, key: str, value):
        """Update a single value in fleet.toml [section].key."""
        L = _launcher()
        import tomllib
        try:
            with open(L.FLEET_TOML, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            data = {}
        data.setdefault(section, {})[key] = value
        # Write back — tomllib is read-only, use manual serialization
        lines = []
        try:
            lines = L.FLEET_TOML.read_text(encoding="utf-8").splitlines()
        except Exception:
            pass
        # Simple approach: find [section] and key, or append
        in_section = False
        found = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == f"[{section}]":
                in_section = True
                continue
            if in_section and stripped.startswith("["):
                # Left the section without finding key — insert before next section
                if isinstance(value, bool):
                    val_str = "true" if value else "false"
                elif isinstance(value, str):
                    val_str = f'"{value}"'
                else:
                    val_str = str(value)
                lines.insert(i, f"{key} = {val_str}")
                found = True
                break
            if in_section and stripped.startswith(f"{key} "):
                if isinstance(value, bool):
                    val_str = "true" if value else "false"
                elif isinstance(value, str):
                    val_str = f'"{value}"'
                else:
                    val_str = str(value)
                # Preserve inline comment if any
                comment = ""
                if "#" in line:
                    comment = "  " + line[line.index("#"):]
                lines[i] = f"{key} = {val_str}{comment}"
                found = True
                break
        if not found:
            # Append section + key if section doesn't exist
            if not any(l.strip() == f"[{section}]" for l in lines):
                lines.append(f"\n[{section}]")
            if isinstance(value, bool):
                val_str = "true" if value else "false"
            elif isinstance(value, str):
                val_str = f'"{value}"'
            else:
                val_str = str(value)
            lines.append(f"{key} = {val_str}")
        L.FLEET_TOML.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _section_header_grid(self, parent, text: str, row: int):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=row, column=0, padx=16, pady=(16, 6), sticky="w")
        ctk.CTkFrame(frame, fg_color=GOLD, width=3, height=14,
                     corner_radius=1).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(frame, text=text.upper(), font=FONT_BOLD,
                     text_color=GOLD).pack(side="left")
