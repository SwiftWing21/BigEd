"""
BigEd CC — Settings / Config dialogs (SettingsDialog, AgentNamesDialog, KeyManagerDialog).
Extracted from launcher.py to reduce god-object complexity (TECH_DEBT 4.1).

Each dialog is a CTkToplevel window that manages a specific configuration area:
- SettingsDialog:     unified settings with nav sidebar (General, Display, Models, HW, Keys, Review, Ops)
- AgentNamesDialog:   per-agent custom display name editor
- KeyManagerDialog:   API key viewer / editor with WSL secret store

── Tab Sections ──────────────────────────────────────────────────────────
This file contains 7 tab builders. Future refactor: split each into its own module.
1. General Settings    — _build_general_panel
2. Display Settings    — _build_display_panel
3. Models Settings     — _build_models_panel
4. Hardware Settings   — _build_hardware_panel
5. API Keys Settings   — _build_keys_panel
6. Review Settings     — _build_review_panel
7. Operations Settings — _build_operations_panel
"""
import base64
import json
import re
import subprocess
import threading
from pathlib import Path

import customtkinter as ctk
import psutil

# ─── Theme (single source of truth) ──────────────────────────────────────────
from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    GREEN, ORANGE, RED, MONO, FONT, FONT_SM, FONT_H,
    BLUE, FONT_XS, FONT_STAT, FONT_BOLD, FONT_TITLE,
    GLASS_BG, GLASS_NAV, GLASS_PANEL, GLASS_HOVER, GLASS_SEL, GLASS_BORDER,
)

# ─── Lazy imports from launcher ──────────────────────────────────────────────
# These are resolved at runtime to avoid circular imports.

def _launcher():
    """Return the launcher module (import once, cache)."""
    import launcher as _mod
    return _mod


# ─── Settings nav & glass palette ────────────────────────────────────────────

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



# ─── Unified Settings Dialog ────────────────────────────────────────────────

class SettingsDialog(ctk.CTkToplevel):
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

        # Load current settings
        self._settings = L._load_settings()

        self._build_ui()
        self._show_section("general")

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0)  # nav
        self.grid_columnconfigure(1, weight=1)  # content
        self.grid_rowconfigure(1, weight=1)

        # ── Header ────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color=GLASS_BG, height=56, corner_radius=0)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(2, weight=1)

        # Accent stripe on left edge
        stripe = ctk.CTkFrame(hdr, fg_color=GOLD, width=3, corner_radius=0)
        stripe.grid(row=0, column=0, sticky="ns", padx=0)

        # Title block
        title_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        title_frame.grid(row=0, column=1, padx=(12, 0), pady=8, sticky="w")
        ctk.CTkLabel(title_frame, text="SETTINGS",
                     font=FONT_TITLE, text_color=GOLD).pack(anchor="w")
        ctk.CTkLabel(title_frame, text="fleet configuration & preferences",
                     font=FONT_XS, text_color=DIM).pack(anchor="w")

        # Version pill on right
        ctk.CTkLabel(hdr, text=" v0.31 ", font=FONT_XS,
                     text_color=DIM, fg_color=GLASS_NAV,
                     corner_radius=8).grid(row=0, column=2, padx=16, sticky="e")

        # ── Left nav ────────────────────────────────────────────────────
        nav = ctk.CTkFrame(self, fg_color=GLASS_NAV, width=170, corner_radius=0)
        nav.grid(row=1, column=0, sticky="nsew")
        nav.grid_propagate(False)

        # Nav section label
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

        # Pre-build all panels
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
        # Update nav highlighting
        for k, b in self._nav_buttons.items():
            if k == key:
                b.configure(fg_color=GLASS_SEL, text_color=GOLD)
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
        L = _launcher()
        panel = ctk.CTkScrollableFrame(self._content, fg_color=GLASS_PANEL)
        self._panels["general"] = panel

        # Section: Agent Theme
        self._section_header(panel, "Agent Theme")
        theme_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        theme_frame.pack(fill="x", padx=16, pady=(0, 12))
        theme_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(theme_frame, text="Theme", font=FONT_SM,
                     text_color=TEXT).grid(row=0, column=0, padx=12, pady=10, sticky="w")
        self._theme_var = ctk.StringVar(value=L._active_theme)
        ctk.CTkOptionMenu(
            theme_frame, values=list(L.AGENT_THEMES.keys()),
            variable=self._theme_var, font=FONT_SM,
            fg_color=BG3, button_color=ACCENT, button_hover_color=ACCENT_H,
            height=30, width=160,
            command=self._on_theme_change,
        ).grid(row=0, column=1, padx=12, pady=10, sticky="w")

        ctk.CTkLabel(theme_frame,
                     text="Themes change how agent roles are displayed throughout the UI.",
                     font=("Segoe UI", 9), text_color=DIM
                     ).grid(row=1, column=0, columnspan=2, padx=12, pady=(0, 10), sticky="w")

        # Section: Custom Agent Names
        self._section_header(panel, "Custom Agent Names")
        names_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        names_frame.pack(fill="x", padx=16, pady=(0, 12))
        names_frame.grid_columnconfigure(1, weight=1)

        self._name_entries = {}
        all_roles = [
            "supervisor", "researcher", "coder", "coder_1", "coder_2", "coder_3",
            "archivist", "analyst", "sales", "onboarding", "implementation",
            "security", "planner",
        ]
        for i, role in enumerate(all_roles):
            theme_map = L.AGENT_THEMES.get(L._active_theme, L.AGENT_THEMES["default"])
            base = re.sub(r'_\d+$', '', role)
            suffix = role[len(base):]
            theme_default = theme_map.get(base, base.title())
            if suffix:
                theme_default += f" {suffix.lstrip('_')}"

            ctk.CTkLabel(names_frame, text=f"{role}:", font=("Consolas", 10),
                         text_color=DIM, anchor="e", width=110
                         ).grid(row=i, column=0, padx=(10, 6), pady=2, sticky="e")
            entry = ctk.CTkEntry(names_frame, font=FONT_SM, fg_color=GLASS_BG,
                                 border_color=GLASS_BORDER, text_color=TEXT,
                                 placeholder_text=theme_default, height=28)
            entry.grid(row=i, column=1, sticky="ew", padx=(0, 10), pady=2)
            current = L._custom_names.get(role, "")
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
        behavior_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
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
                     font=("Segoe UI", 9), text_color=DIM
                     ).pack(padx=12, pady=(0, 12), anchor="w")

        # Section: Ingestion
        self._section_header(panel, "File Ingestion")
        ingest_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        ingest_frame.pack(fill="x", padx=16, pady=(0, 12))
        ingest_frame.grid_columnconfigure(1, weight=1)

        default_downloads = str(Path.home() / "Downloads")
        ingest_path = self._settings.get("ingest_path", default_downloads)

        ctk.CTkLabel(ingest_frame, text="Default import path:", font=FONT_SM,
                     text_color=TEXT).grid(row=0, column=0, padx=12, pady=(12, 4), sticky="w")
        self._ingest_path_var = ctk.StringVar(value=ingest_path)
        ctk.CTkEntry(ingest_frame, textvariable=self._ingest_path_var,
                     font=("Consolas", 9), fg_color=GLASS_BG,
                     border_color=GLASS_BORDER, text_color=TEXT, height=28
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
                     font=("Segoe UI", 9), text_color=DIM
                     ).grid(row=3, column=0, columnspan=2, padx=12, pady=(0, 10), sticky="w")

        # Section: Visible Tabs
        self._section_header(panel, "Visible Tabs (Requires Restart)")
        tabs_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        tabs_frame.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(tabs_frame, text="Enable or disable modular launcher tabs.",
                     font=("Segoe UI", 9), text_color=DIM).pack(padx=12, pady=(10, 0), anchor="w")

        tab_grid = ctk.CTkFrame(tabs_frame, fg_color="transparent")
        tab_grid.pack(fill="x", padx=12, pady=8)

        self._tab_vars = {}
        tab_cfg = L.load_tab_cfg()

        for i, (tab_key, label) in enumerate([
            ("crm", "CRM"), ("onboarding", "Onboarding"),
            ("customers", "Customers"), ("accounts", "Accounts"),
            ("ingestion", "Ingestion"), ("outputs", "Outputs")
        ]):
            var = ctk.BooleanVar(value=tab_cfg.get(tab_key, False))
            self._tab_vars[tab_key] = var
            cb = ctk.CTkCheckBox(tab_grid, text=label, variable=var, font=FONT_SM,
                                 text_color=TEXT, fg_color=ACCENT, hover_color=ACCENT_H)
            cb.grid(row=i // 2, column=i % 2, padx=(0, 20), pady=6, sticky="w")

        btn_row_tabs = ctk.CTkFrame(tabs_frame, fg_color="transparent")
        btn_row_tabs.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(btn_row_tabs, text="Save Tabs", font=FONT_SM, width=100, height=26,
                      fg_color=BG3, hover_color=BG2, command=self._save_tabs).pack(side="left")
        self._tabs_status = ctk.CTkLabel(btn_row_tabs, text="", font=("Segoe UI", 9), text_color=DIM)
        self._tabs_status.pack(side="left", padx=8)

        # Section: Backup & Restore
        self._section_header(panel, "Backup & Restore")
        backup_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        backup_frame.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(backup_frame, text="Export or import configurations securely.",
                     font=("Segoe UI", 9), text_color=DIM).pack(padx=12, pady=(10, 6), anchor="w")

        btn_row2 = ctk.CTkFrame(backup_frame, fg_color="transparent")
        btn_row2.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(btn_row2, text="Export Config", font=FONT_SM, width=120, height=28,
                      fg_color=BG3, hover_color=BG2, command=self._export_config).pack(side="left")
        ctk.CTkButton(btn_row2, text="Import Config", font=FONT_SM, width=120, height=28,
                      fg_color=BG3, hover_color=BG2, command=self._import_config).pack(side="left", padx=8)

    # ── Display Panel ────────────────────────────────────────────────────
    def _build_display_panel(self):
        L = _launcher()
        panel = ctk.CTkScrollableFrame(self._content, fg_color=GLASS_PANEL)
        self._panels["display"] = panel
        prefs = L._load_settings()

        # Section: UI Scale
        self._section_header(panel, "UI Scale")
        scale_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        scale_frame.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(scale_frame, text="Adjust UI size (75%–150%). Applied on next launch.",
                     font=("Segoe UI", 9), text_color=DIM).pack(padx=12, pady=(10, 6), anchor="w")

        slider_row = ctk.CTkFrame(scale_frame, fg_color="transparent")
        slider_row.pack(fill="x", padx=12, pady=(0, 4))

        cur_scale = prefs.get("ui_scale", 1.0)
        self._scale_var = ctk.DoubleVar(value=cur_scale)
        self._scale_pct_label = ctk.CTkLabel(slider_row, text=f"{int(cur_scale * 100)}%",
                                              font=("Consolas", 11), text_color=TEXT, width=50)
        self._scale_pct_label.pack(side="right", padx=(8, 0))

        self._scale_slider = ctk.CTkSlider(
            slider_row, from_=0.75, to=1.5, number_of_steps=15,
            variable=self._scale_var, command=self._on_scale_preview,
            width=220, fg_color=BG3, progress_color=GOLD, button_color=GOLD,
            button_hover_color=ACCENT_H,
        )
        self._scale_slider.pack(side="left", fill="x", expand=True)

        btn_row_scale = ctk.CTkFrame(scale_frame, fg_color="transparent")
        btn_row_scale.pack(fill="x", padx=12, pady=(4, 10))
        ctk.CTkButton(btn_row_scale, text="Apply & Restart", font=FONT_SM, width=130, height=28,
                      fg_color=BG3, hover_color=BG2, command=self._apply_scale).pack(side="left")

        # Section: Window Behavior
        self._section_header(panel, "Window Behavior")
        win_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        win_frame.pack(fill="x", padx=16, pady=(0, 12))

        self._sidebar_vis_var = ctk.BooleanVar(value=prefs.get("sidebar_visible", True))
        ctk.CTkSwitch(win_frame, text="Start with sidebar expanded",
                      font=FONT_SM, text_color=TEXT, variable=self._sidebar_vis_var,
                      command=self._save_display_prefs,
                      fg_color=BG3, progress_color=GOLD
                      ).pack(padx=12, pady=(10, 4), anchor="w")

        self._on_top_var = ctk.BooleanVar(value=prefs.get("always_on_top", False))
        ctk.CTkSwitch(win_frame, text="Always on top",
                      font=FONT_SM, text_color=TEXT, variable=self._on_top_var,
                      command=self._on_always_on_top_toggle,
                      fg_color=BG3, progress_color=GOLD
                      ).pack(padx=12, pady=4, anchor="w")

        self._remember_pos_var = ctk.BooleanVar(value=prefs.get("remember_position", True))
        ctk.CTkSwitch(win_frame, text="Remember window position & size",
                      font=FONT_SM, text_color=TEXT, variable=self._remember_pos_var,
                      command=self._save_display_prefs,
                      fg_color=BG3, progress_color=GOLD
                      ).pack(padx=12, pady=4, anchor="w")

        self._start_max_var = ctk.BooleanVar(value=prefs.get("start_maximized", False))
        ctk.CTkSwitch(win_frame, text="Start maximized",
                      font=FONT_SM, text_color=TEXT, variable=self._start_max_var,
                      command=self._save_display_prefs,
                      fg_color=BG3, progress_color=GOLD
                      ).pack(padx=12, pady=(4, 10), anchor="w")

        # Section: Density
        self._section_header(panel, "Density")
        density_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        density_frame.pack(fill="x", padx=16, pady=(0, 12))

        self._compact_var = ctk.BooleanVar(value=prefs.get("compact_mode", False))
        ctk.CTkSwitch(density_frame, text="Compact mode (reduce padding)",
                      font=FONT_SM, text_color=TEXT, variable=self._compact_var,
                      command=self._save_display_prefs,
                      fg_color=BG3, progress_color=GOLD
                      ).pack(padx=12, pady=(10, 4), anchor="w")
        ctk.CTkLabel(density_frame, text="Reduces spacing. Takes effect on next launch.",
                     font=("Segoe UI", 9), text_color=DIM).pack(padx=12, pady=(0, 10), anchor="w")

    # ── Models Panel ─────────────────────────────────────────────────────
    def _build_models_panel(self):
        L = _launcher()
        panel = ctk.CTkScrollableFrame(self._content, fg_color=GLASS_PANEL)
        self._panels["models"] = panel

        # LLM Model button
        self._section_header(panel, "LLM Model")
        llm_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        llm_frame.pack(fill="x", padx=16, pady=(0, 12))

        current_model = L.load_model_cfg().get("local", "qwen3:8b")
        ctk.CTkLabel(llm_frame, text=f"Current: {current_model}",
                     font=("Consolas", 10), text_color=TEXT
                     ).pack(padx=12, pady=(12, 4), anchor="w")
        ctk.CTkLabel(llm_frame,
                     text="Select the Ollama model used by fleet workers for local inference.",
                     font=("Segoe UI", 9), text_color=DIM
                     ).pack(padx=12, pady=(0, 6), anchor="w")
        ctk.CTkButton(llm_frame, text="Open Model Selector", font=FONT_SM,
                      width=160, height=30, fg_color=BG3, hover_color=BG2,
                      command=lambda: L.ModelSelectorDialog(self._parent)
                      ).pack(padx=12, pady=(0, 12), anchor="w")

        # Diffusion Models
        self._section_header(panel, "Image Generation (Stable Diffusion)")
        diff_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
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
                     font=("Consolas", 9), text_color=DIM
                     ).grid(row=0, column=1, padx=(12, 0), sticky="w")

        sd15_detail = ctk.CTkFrame(diff_frame, fg_color="transparent")
        sd15_detail.pack(fill="x", padx=24, pady=(2, 0))
        ctk.CTkLabel(sd15_detail,
                     text="Fast local generation on GPU. Good for iteration and drafts.",
                     font=("Segoe UI", 9), text_color=DIM
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
                     font=("Consolas", 9), text_color=DIM
                     ).grid(row=0, column=1, padx=(12, 0), sticky="w")

        sdxl_detail = ctk.CTkFrame(diff_frame, fg_color="transparent")
        sdxl_detail.pack(fill="x", padx=24, pady=(2, 0))
        ctk.CTkLabel(sdxl_detail,
                     text="Higher quality output on CPU. Slow but no VRAM cost.",
                     font=("Segoe UI", 9), text_color=DIM
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
                     font=FONT_SM, fg_color=GLASS_BG, border_color=GLASS_BORDER,
                     text_color=TEXT, width=50, height=28
                     ).pack(side="left", padx=(4, 16))

        ctk.CTkLabel(params_row, text="Guidance:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._diff_guidance_var = ctk.StringVar(
            value=str(diff_settings.get("default_guidance", 7.5)))
        ctk.CTkEntry(params_row, textvariable=self._diff_guidance_var,
                     font=FONT_SM, fg_color=GLASS_BG, border_color=GLASS_BORDER,
                     text_color=TEXT, width=50, height=28
                     ).pack(side="left", padx=(4, 0))

        # ── Upscale section ───────────────────────────────────────────
        self._section_header(panel, "Upscale Pipeline (SD 1.5)")
        up_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        up_frame.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(up_frame,
                     text="Apply after base 512x512 generation to increase resolution.",
                     font=("Segoe UI", 9), text_color=DIM
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
        desc_frame = ctk.CTkFrame(up_frame, fg_color=GLASS_BG, corner_radius=4)
        desc_frame.pack(fill="x", padx=12, pady=(4, 8))
        ctk.CTkLabel(desc_frame,
                     text="none     — output at base resolution (512x512)\n"
                          "refine   — img2img re-pass at higher res (~30s/pass, same model)\n"
                          "x4       — SD upscaler 512→2048 (~90s, ~3 GB extra download)",
                     font=("Consolas", 9), text_color=DIM, justify="left"
                     ).pack(padx=10, pady=8, anchor="w")

        # Refine params
        refine_row = ctk.CTkFrame(up_frame, fg_color="transparent")
        refine_row.pack(fill="x", padx=12, pady=(0, 4))

        ctk.CTkLabel(refine_row, text="Passes:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._upscale_passes_var = ctk.StringVar(
            value=str(diff_settings.get("default_upscale_passes", 1)))
        ctk.CTkEntry(refine_row, textvariable=self._upscale_passes_var,
                     font=FONT_SM, fg_color=GLASS_BG, border_color=GLASS_BORDER,
                     text_color=TEXT, width=40, height=28
                     ).pack(side="left", padx=(4, 14))

        ctk.CTkLabel(refine_row, text="Scale:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._upscale_factor_var = ctk.StringVar(
            value=str(diff_settings.get("default_upscale_factor", 1.5)))
        ctk.CTkEntry(refine_row, textvariable=self._upscale_factor_var,
                     font=FONT_SM, fg_color=GLASS_BG, border_color=GLASS_BORDER,
                     text_color=TEXT, width=50, height=28
                     ).pack(side="left", padx=(4, 14))

        ctk.CTkLabel(refine_row, text="Strength:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._upscale_strength_var = ctk.StringVar(
            value=str(diff_settings.get("default_upscale_strength", 0.35)))
        ctk.CTkEntry(refine_row, textvariable=self._upscale_strength_var,
                     font=FONT_SM, fg_color=GLASS_BG, border_color=GLASS_BORDER,
                     text_color=TEXT, width=50, height=28
                     ).pack(side="left", padx=(4, 0))

        # Pipeline preview
        preview_frame = ctk.CTkFrame(up_frame, fg_color=GLASS_BG, corner_radius=4)
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
        L = _launcher()
        panel = ctk.CTkFrame(self._content, fg_color=GLASS_PANEL)
        self._panels["hardware"] = panel
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)

        # GPU Power section
        self._section_header_grid(panel, "GPU Power & Thermal", row=0)
        gpu_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        gpu_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        ctk.CTkLabel(gpu_frame,
                     text="Control GPU power limits and monitor thermals.",
                     font=("Segoe UI", 9), text_color=DIM
                     ).pack(padx=12, pady=(10, 4), anchor="w")
        ctk.CTkButton(gpu_frame, text="Open GPU Power Manager", font=FONT_SM,
                      width=180, height=30, fg_color=BG3, hover_color=BG2,
                      command=lambda: L.ThermalDialog(self._parent)
                      ).pack(padx=12, pady=(0, 10), anchor="w")

        # Hardware Details section
        self._section_header_grid(panel, "Hardware Details", row=2)
        hw_text = ctk.CTkTextbox(panel, font=("Consolas", 10),
                                 fg_color=GLASS_BG, text_color=TEXT,
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
        panel = ctk.CTkFrame(self._content, fg_color=GLASS_PANEL)
        self._panels["keys"] = panel
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(0, weight=1)

        # Embed a simple message + launch button (full KeyManager is complex)
        inner = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        inner.place(relx=0.5, rely=0.4, anchor="center")

        ctk.CTkLabel(inner, text="🔑", font=("Segoe UI", 32)
                     ).pack(pady=(24, 8))
        ctk.CTkLabel(inner, text="API Key Manager",
                     font=("Segoe UI", 14, "bold"), text_color=GOLD
                     ).pack(pady=(0, 4))
        ctk.CTkLabel(inner,
                     text="Add, rotate, and manage API keys for Anthropic, Gemini,\n"
                          "Stability AI, Replicate, and other services.",
                     font=("Segoe UI", 10), text_color=DIM, justify="center"
                     ).pack(padx=24, pady=(0, 12))
        ctk.CTkButton(inner, text="Open Key Manager", font=("Segoe UI", 11),
                      width=160, height=34, fg_color=ACCENT, hover_color=ACCENT_H,
                      command=lambda: KeyManagerDialog(self._parent)
                      ).pack(pady=(0, 24))

    # ── Review Panel ─────────────────────────────────────────────────────
    def _build_review_panel(self):
        L = _launcher()
        panel = ctk.CTkFrame(self._content, fg_color=GLASS_PANEL)
        self._panels["review"] = panel
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(0, weight=1)

        inner = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        inner.place(relx=0.5, rely=0.4, anchor="center")

        ctk.CTkLabel(inner, text="🧪", font=("Segoe UI", 32)
                     ).pack(pady=(24, 8))
        ctk.CTkLabel(inner, text="Review Settings",
                     font=("Segoe UI", 14, "bold"), text_color=GOLD
                     ).pack(pady=(0, 4))
        ctk.CTkLabel(inner,
                     text="Configure the evaluator-optimizer review pass.\n"
                          "Enable/disable reviews and choose provider (API, subscription, local).",
                     font=("Segoe UI", 10), text_color=DIM, justify="center"
                     ).pack(padx=24, pady=(0, 12))
        ctk.CTkButton(inner, text="Open Review Settings", font=("Segoe UI", 11),
                      width=170, height=34, fg_color=ACCENT, hover_color=ACCENT_H,
                      command=lambda: L.ReviewDialog(self._parent)
                      ).pack(pady=(0, 24))

    # ── Operations Panel ─────────────────────────────────────────────────
    def _build_operations_panel(self):
        panel = ctk.CTkScrollableFrame(self._content, fg_color=GLASS_PANEL)
        self._panels["operations"] = panel

        def _ops_btn(parent, label, cmd, desc=None, color=BG3, hover=BG2):
            frame = ctk.CTkFrame(parent, fg_color="transparent")
            frame.pack(fill="x", padx=0, pady=(0, 2))
            ctk.CTkButton(frame, text=label, font=FONT_SM,
                          width=200, height=30, fg_color=color, hover_color=hover,
                          anchor="w", command=cmd).pack(side="left")
            if desc:
                ctk.CTkLabel(frame, text=desc, font=("Segoe UI", 9),
                             text_color=DIM).pack(side="left", padx=(10, 0))

        # Fleet Recovery
        self._section_header(panel, "Fleet Recovery")
        recover_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        recover_frame.pack(fill="x", padx=16, pady=(0, 12))
        inner = ctk.CTkFrame(recover_frame, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=10)
        _ops_btn(inner, "↺  Recover All", self._parent._recover_all,
                 "Kill and restart Ollama + supervisor + all workers",
                 "#2a2a10", "#3a3a18")

        # Security
        self._section_header(panel, "Security")
        sec_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        sec_frame.pack(fill="x", padx=16, pady=(0, 12))
        inner = ctk.CTkFrame(sec_frame, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=10)
        _ops_btn(inner, "🔍 Security Audit", self._parent._run_audit,
                 "Audit all fleet skills and configs")
        _ops_btn(inner, "🌐 Pen Test", self._parent._run_pentest,
                 "Network service scan of local environment")
        _ops_btn(inner, "📂 Advisories", self._parent._open_advisories,
                 "View and apply pending security advisories")

        # Marathon
        self._section_header(panel, "Marathon")
        marathon_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        marathon_frame.pack(fill="x", padx=16, pady=(0, 12))
        inner = ctk.CTkFrame(marathon_frame, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=10)
        _ops_btn(inner, "🏃 Start Marathon", self._parent._start_marathon,
                 "8-hour discussion + lead research + synthesis run")
        _ops_btn(inner, "📋 Marathon Log", self._parent._show_marathon_log,
                 "Tail marathon.log — current phase and output")
        _ops_btn(inner, "⏹  Stop Marathon", self._parent._stop_marathon,
                 "Kill the running marathon process",
                 "#2a1a1a", "#3a2020")

    # ── Helpers ──────────────────────────────────────────────────────────
    def _section_header(self, parent, text: str):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=16, pady=(16, 6))
        ctk.CTkFrame(frame, fg_color=GOLD, width=3, height=14,
                     corner_radius=1).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(frame, text=text.upper(), font=FONT_BOLD,
                     text_color=GOLD).pack(side="left")

    def _section_header_grid(self, parent, text: str, row: int):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=row, column=0, padx=16, pady=(16, 6), sticky="w")
        ctk.CTkFrame(frame, fg_color=GOLD, width=3, height=14,
                     corner_radius=1).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(frame, text=text.upper(), font=FONT_BOLD,
                     text_color=GOLD).pack(side="left")

    # ── Display panel handlers ────────────────────────────────────────────
    def _on_scale_preview(self, value):
        self._scale_pct_label.configure(text=f"{int(float(value) * 100)}%")

    def _save_display_prefs(self):
        L = _launcher()
        data = L._load_settings()
        data["sidebar_visible"] = self._sidebar_vis_var.get()
        data["always_on_top"] = self._on_top_var.get()
        data["remember_position"] = self._remember_pos_var.get()
        data["start_maximized"] = self._start_max_var.get()
        data["compact_mode"] = self._compact_var.get()
        L._save_settings(data)

    def _apply_scale(self):
        from tkinter import messagebox
        L = _launcher()
        data = L._load_settings()
        data["ui_scale"] = round(self._scale_var.get(), 2)
        L._save_settings(data)
        self._save_display_prefs()
        messagebox.showinfo("UI Scale", "Scale saved. Please restart BigEd CC for changes to take effect.")

    def _on_always_on_top_toggle(self):
        on_top = self._on_top_var.get()
        self._parent.attributes("-topmost", on_top)
        self._save_display_prefs()

    def _on_theme_change(self, choice: str):
        L = _launcher()
        self._parent._change_agent_theme(choice)
        # Update placeholder text in name entries
        theme_map = L.AGENT_THEMES.get(choice, L.AGENT_THEMES["default"])
        for role, entry in self._name_entries.items():
            base = re.sub(r'_\d+$', '', role)
            suffix = role[len(base):]
            theme_default = theme_map.get(base, base.title())
            if suffix:
                theme_default += f" {suffix.lstrip('_')}"
            entry.configure(placeholder_text=theme_default)

    def _save_names(self):
        L = _launcher()
        import launcher as _mod
        names = {}
        for role, entry in self._name_entries.items():
            val = entry.get().strip()
            if val:
                names[role] = val
        _mod._custom_names = names
        L._save_custom_names(names)
        if hasattr(self._parent, "_refresh_status"):
            self._parent._refresh_status()
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
        L = _launcher()
        data = L._load_settings()
        data["ingest_path"] = self._ingest_path_var.get()
        L._save_settings(data)
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
        L = _launcher()
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

        data = L._load_settings()
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
        L._save_settings(data)
        self._diff_status.configure(text="Saved.", text_color=GREEN)

    def _on_claude_research_toggle(self):
        L = _launcher()
        # Sync with parent's toggle logic
        use_claude = self._claude_research_var2.get()
        try:
            text = L.FLEET_TOML.read_text(encoding="utf-8")
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
            L.FLEET_TOML.write_text(text, encoding="utf-8")
            # Update parent's checkbox if it exists
            if hasattr(self._parent, "_claude_research_var"):
                self._parent._claude_research_var.set(use_claude)
        except Exception:
            pass

    def _save_tabs(self):
        L = _launcher()
        try:
            text = ""
            if L.FLEET_TOML.exists():
                text = L.FLEET_TOML.read_text(encoding="utf-8")

            block = ("[launcher.tabs]\n"
                     "command_center = true\n"
                     "agents = true\n")
            for k, v in self._tab_vars.items():
                block += f"{k} = {'true' if str(v.get()).lower() == 'true' else 'false'}\n"

            # Regex reliably overwrites the entire [launcher.tabs] block or appends it.
            if re.search(r'^\[launcher\.tabs\]', text, re.M):
                text = re.sub(r'^\[launcher\.tabs\].*?(?=\n\[|\Z)', block.strip(), text, flags=re.M|re.S)
            else:
                text = text.rstrip() + "\n\n" + block.strip() + "\n"

            L.FLEET_TOML.write_text(text, encoding="utf-8")
            self._tabs_status.configure(text="Saved. Restart app to apply.", text_color=GREEN)
        except Exception as e:
            self._tabs_status.configure(text=f"Error: {e}", text_color=RED)

    def _export_config(self):
        L = _launcher()
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON Backup", "*.json")])
        if not path:
            return

        payload = {"settings": L._load_settings()}
        Path(path).write_text(json.dumps(payload, indent=2))
        if hasattr(self, "_status"):
            self._status.configure(text=f"Exported to {Path(path).name}", text_color=GREEN)

    def _import_config(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(filetypes=[("JSON Backup", "*.json")])
        if not path:
            return

        try:
            data = json.loads(Path(path).read_text())
        except Exception:
            if hasattr(self, "_status"):
                self._status.configure(text="Invalid file format", text_color=RED)
            return

        # Handle legacy nested format or direct settings
        payload = data.get("data", data) if "data" in data else data
        self._apply_import(payload)

    def _apply_import(self, payload):
        L = _launcher()
        import launcher as _mod
        settings = payload.get("settings")
        if settings:
            L._save_settings(settings)
            _mod._active_theme = settings.get("agent_theme", "default")
            _mod._custom_names = settings.get("agent_names", {})

            self._theme_var.set(_mod._active_theme)
            for role, entry in self._name_entries.items():
                entry.delete(0, "end")
                if _mod._custom_names.get(role):
                    entry.insert(0, _mod._custom_names[role])

            if hasattr(self._parent, "_refresh_status"):
                self._parent._refresh_status()

        if hasattr(self, "_status"):
            self._status.configure(text="Import successful.", text_color=GREEN)

    def _load_hw_info(self):
        L = _launcher()
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
        if L._GPU_OK:
            try:
                import pynvml
                name = pynvml.nvmlDeviceGetName(L._GPU_HANDLE)
                mem = pynvml.nvmlDeviceGetMemoryInfo(L._GPU_HANDLE)
                util = pynvml.nvmlDeviceGetUtilizationRates(L._GPU_HANDLE)
                temp = pynvml.nvmlDeviceGetTemperature(
                    L._GPU_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
                power = pynvml.nvmlDeviceGetPowerUsage(L._GPU_HANDLE) / 1000
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

    # ── MCP Servers Panel ─────────────────────────────────────────────────

    def _build_mcp_panel(self):
        """MCP Servers panel — view and manage connected MCP tool servers."""
        panel = ctk.CTkScrollableFrame(self._content, fg_color=GLASS_PANEL)
        self._panels["mcp"] = panel

        # ── Status overview ────────────────────────────────────────────
        self._section_header(panel, "Connected Servers")
        status_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        status_frame.pack(fill="x", padx=16, pady=(0, 12))
        status_frame.grid_columnconfigure(1, weight=1)

        self._mcp_status_area = ctk.CTkFrame(status_frame, fg_color="transparent")
        self._mcp_status_area.pack(fill="x", padx=12, pady=10)

        # Refresh button
        ctk.CTkButton(
            status_frame, text="\u21bb Refresh", font=FONT_SM,
            width=80, height=26, fg_color=BG3, hover_color=BG2,
            command=self._refresh_mcp_status
        ).pack(anchor="w", padx=12, pady=(0, 10))

        # ── Bundled Defaults ───────────────────────────────────────────
        self._section_header(panel, "Bundled Defaults")
        defaults_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        defaults_frame.pack(fill="x", padx=16, pady=(0, 12))

        self._mcp_toggles = {}
        defaults = [
            ("playwright", "Browser Automation", "browser_crawl, web_search fallback", True),
            ("filesystem", "File Operations", "ingest, rag_index, code_index", False),
            ("sequential-thinking", "Multi-Step Reasoning", "plan_workload, lead_research", False),
            ("memory", "Persistent Knowledge", "rag_index, knowledge store", False),
        ]

        for i, (name, desc, skills, default_on) in enumerate(defaults):
            row = ctk.CTkFrame(defaults_frame, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=(8 if i == 0 else 2, 2))
            row.grid_columnconfigure(1, weight=1)

            # Check if currently enabled in .mcp.json
            is_enabled = self._is_mcp_server_enabled(name)

            var = ctk.BooleanVar(value=is_enabled)
            sw = ctk.CTkSwitch(
                row, text="", variable=var, width=40,
                progress_color=GREEN, button_color=ACCENT,
                command=lambda n=name, v=var: self._toggle_mcp_default(n, v.get())
            )
            sw.grid(row=0, column=0, padx=(0, 8))

            ctk.CTkLabel(row, text=name, font=("Segoe UI", 11, "bold"),
                        text_color=TEXT).grid(row=0, column=1, sticky="w")

            ctk.CTkLabel(row, text=desc, font=("Segoe UI", 9),
                        text_color=DIM).grid(row=0, column=2, padx=(8, 0), sticky="w")

            # Status dot
            dot_color = GREEN if is_enabled else "#555"
            dot = ctk.CTkLabel(row, text="\u25cf", font=("Consolas", 11),
                              text_color=dot_color)
            dot.grid(row=0, column=3, padx=(8, 0))

            ctk.CTkLabel(row, text=f"Skills: {skills}", font=("Consolas", 8),
                        text_color="#555").grid(row=1, column=1, columnspan=3, sticky="w", pady=(0, 4))

            self._mcp_toggles[name] = {"var": var, "dot": dot, "switch": sw}

        # ── Integrations (one-click add) ──────────────────────────────
        self._section_header(panel, "Integrations")
        int_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        int_frame.pack(fill="x", padx=16, pady=(0, 12))

        integrations = [
            ("github", "GitHub", "Issues, PRs, code search", "GITHUB_TOKEN"),
            ("brave-search", "Brave Search", "Web search API", "BRAVE_API_KEY"),
            ("fetch", "HTTP Fetch", "Web crawling, API probing", None),
            ("slack", "Slack", "Team notifications", "SLACK_BOT_TOKEN"),
            ("postgres", "PostgreSQL", "Database queries", "POSTGRES_URL"),
        ]

        for i, (name, label, desc, key_name) in enumerate(integrations):
            row = ctk.CTkFrame(int_frame, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=(8 if i == 0 else 2, 2))
            row.grid_columnconfigure(1, weight=1)

            is_enabled = self._is_mcp_server_enabled(name)
            status_text = "Connected" if is_enabled else ("Needs " + key_name) if key_name else "Available"
            status_color = GREEN if is_enabled else ORANGE if key_name else DIM

            ctk.CTkLabel(row, text="\u25cf", font=("Consolas", 11),
                        text_color=status_color).grid(row=0, column=0, padx=(0, 6))
            ctk.CTkLabel(row, text=label, font=("Segoe UI", 11, "bold"),
                        text_color=TEXT).grid(row=0, column=1, sticky="w")
            ctk.CTkLabel(row, text=desc, font=("Segoe UI", 9),
                        text_color=DIM).grid(row=0, column=2, padx=(8, 0), sticky="w")
            ctk.CTkLabel(row, text=status_text, font=("Consolas", 9),
                        text_color=status_color).grid(row=0, column=3, padx=(8, 0))

            if not is_enabled:
                btn_text = "Add" if not key_name else "Configure"
                ctk.CTkButton(
                    row, text=btn_text, font=FONT_SM,
                    width=70, height=22, fg_color=BG3, hover_color=ACCENT_H,
                    command=lambda n=name, k=key_name: self._add_mcp_integration(n, k)
                ).grid(row=0, column=4, padx=(8, 0))
            else:
                ctk.CTkButton(
                    row, text="Remove", font=FONT_SM,
                    width=60, height=22, fg_color="#c62828", hover_color="#d32f2f",
                    command=lambda n=name: self._remove_mcp_server(n)
                ).grid(row=0, column=4, padx=(8, 0))

            ctk.CTkLabel(row, text=f"Key: {key_name}" if key_name else "No key needed",
                        font=("Consolas", 8), text_color="#444"
                        ).grid(row=1, column=1, columnspan=4, sticky="w", pady=(0, 4))

        # ── Custom Server ─────────────────────────────────────────────
        self._section_header(panel, "Custom Server")
        custom_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        custom_frame.pack(fill="x", padx=16, pady=(0, 12))
        custom_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(custom_frame, text="Name", font=FONT_SM,
                    text_color=DIM).grid(row=0, column=0, padx=12, pady=(10, 4), sticky="w")
        self._custom_name = ctk.CTkEntry(
            custom_frame, font=MONO, fg_color=BG, border_color=GLASS_BORDER,
            height=28, placeholder_text="my-server")
        self._custom_name.grid(row=0, column=1, padx=12, pady=(10, 4), sticky="ew")

        ctk.CTkLabel(custom_frame, text="URL", font=FONT_SM,
                    text_color=DIM).grid(row=1, column=0, padx=12, pady=4, sticky="w")
        self._custom_url = ctk.CTkEntry(
            custom_frame, font=MONO, fg_color=BG, border_color=GLASS_BORDER,
            height=28, placeholder_text="http://localhost:8080 or npx -y @org/server")
        self._custom_url.grid(row=1, column=1, padx=12, pady=4, sticky="ew")

        ctk.CTkButton(
            custom_frame, text="Add Custom Server", font=FONT_SM,
            width=140, height=28, fg_color=ACCENT, hover_color=ACCENT_H,
            command=self._add_custom_server
        ).grid(row=2, column=1, padx=12, pady=(4, 10), sticky="w")

        # Initial status load
        self._refresh_mcp_status()

    # ── MCP helper methods ─────────────────────────────────────────────

    def _is_mcp_server_enabled(self, name: str) -> bool:
        """Check if a server is in .mcp.json."""
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "fleet"))
            from mcp_manager import load_mcp_json
            data = load_mcp_json()
            return name in data.get("mcpServers", {})
        except Exception:
            return False

    def _toggle_mcp_default(self, name: str, enable: bool):
        """Toggle a bundled default MCP server."""
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "fleet"))
            from mcp_manager import enable_default, disable_server
            if enable:
                enable_default(name)
            else:
                disable_server(name)
            # Update dot color
            if name in self._mcp_toggles:
                color = GREEN if enable else "#555"
                self._mcp_toggles[name]["dot"].configure(text_color=color)
        except Exception as e:
            import logging
            logging.getLogger("settings").warning("MCP toggle failed: %s", e)

    def _refresh_mcp_status(self):
        """Refresh the status display of all MCP servers."""
        # Clear existing status widgets
        for w in self._mcp_status_area.winfo_children():
            w.destroy()

        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "fleet"))
            from mcp_manager import get_all_server_status
            servers = get_all_server_status()

            if not servers:
                ctk.CTkLabel(self._mcp_status_area,
                            text="No MCP servers configured",
                            font=FONT_SM, text_color=DIM).pack(anchor="w")
                return

            for s in servers:
                row = ctk.CTkFrame(self._mcp_status_area, fg_color="transparent")
                row.pack(fill="x", pady=1)
                row.grid_columnconfigure(1, weight=1)

                status = s.get("status", "unknown")
                color = GREEN if status == "online" else ORANGE if status == "configured" else RED
                ctk.CTkLabel(row, text="\u25cf", font=("Consolas", 11),
                            text_color=color).grid(row=0, column=0, padx=(0, 6))
                ctk.CTkLabel(row, text=s["name"], font=("Segoe UI", 10, "bold"),
                            text_color=TEXT).grid(row=0, column=1, sticky="w")
                ctk.CTkLabel(row, text=s["type"], font=("Consolas", 9),
                            text_color=DIM).grid(row=0, column=2, padx=(8, 0))
                ctk.CTkLabel(row, text=status.upper(), font=("Consolas", 9),
                            text_color=color).grid(row=0, column=3, padx=(8, 0))
        except Exception as e:
            ctk.CTkLabel(self._mcp_status_area,
                        text=f"Error loading status: {e}",
                        font=FONT_SM, text_color=RED).pack(anchor="w")

    def _add_mcp_integration(self, name: str, key_name: str = None):
        """Add an integration server — prompt for API key if needed."""
        if key_name:
            # Show key input dialog
            dialog = ctk.CTkInputDialog(
                text=f"Enter {key_name} for {name}:",
                title=f"Configure {name}")
            key_value = dialog.get_input()
            if not key_value:
                return
            # Store key in environment (fleet.toml security section)
            try:
                import sys
                sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "fleet"))
                from mcp_manager import MCP_INTEGRATIONS, add_server
                server_def = MCP_INTEGRATIONS.get(name, {})
                config = {"type": server_def.get("type", "stdio")}
                if config["type"] == "stdio":
                    config["command"] = server_def.get("command", "npx")
                    config["args"] = server_def.get("args", [])
                    config["env"] = {key_name: key_value}
                elif config["type"] == "http":
                    config["url"] = server_def.get("url", "")
                add_server(name, config)
                self._refresh_mcp_status()
            except Exception as e:
                import logging
                logging.getLogger("settings").warning("MCP add failed: %s", e)
        else:
            # No key needed — just enable
            try:
                import sys
                sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "fleet"))
                from mcp_manager import MCP_INTEGRATIONS, add_server
                server_def = MCP_INTEGRATIONS.get(name, {})
                config = {"type": server_def.get("type", "stdio")}
                if config["type"] == "stdio":
                    config["command"] = server_def.get("command", "npx")
                    config["args"] = server_def.get("args", [])
                add_server(name, config)
                self._refresh_mcp_status()
            except Exception as e:
                import logging
                logging.getLogger("settings").warning("MCP add failed: %s", e)

    def _remove_mcp_server(self, name: str):
        """Remove an MCP server."""
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "fleet"))
            from mcp_manager import disable_server
            disable_server(name)
            self._refresh_mcp_status()
        except Exception as e:
            import logging
            logging.getLogger("settings").warning("MCP remove failed: %s", e)

    def _add_custom_server(self):
        """Add a custom MCP server from the name/URL fields."""
        name = self._custom_name.get().strip()
        url = self._custom_url.get().strip()
        if not name or not url:
            return

        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "fleet"))
            from mcp_manager import add_server

            # Auto-detect transport
            if url.startswith("http"):
                config = {"type": "http", "url": url}
            elif url.startswith("npx "):
                parts = url.split()
                config = {"type": "stdio", "command": parts[0], "args": parts[1:]}
            else:
                config = {"type": "stdio", "command": url, "args": []}

            add_server(name, config)
            self._custom_name.delete(0, "end")
            self._custom_url.delete(0, "end")
            self._refresh_mcp_status()
        except Exception as e:
            import logging
            logging.getLogger("settings").warning("Custom MCP add failed: %s", e)


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

        L = _launcher()
        ico = L.HERE / "brick.ico"
        if ico.exists():
            try: self.iconbitmap(str(ico))
            except Exception: pass

        self._entries = {}
        self._build_ui()

    def _build_ui(self):
        L = _launcher()
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
            theme_map = L.AGENT_THEMES.get(L._active_theme, L.AGENT_THEMES["default"])
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
            current = L._custom_names.get(role, "")
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
        L = _launcher()
        import launcher as _mod
        names = {}
        for role, entry in self._entries.items():
            val = entry.get().strip()
            if val:
                names[role] = val
        _mod._custom_names = names
        L._save_custom_names(names)
        if hasattr(self._parent, "_refresh_status"):
            self._parent._refresh_status()
        if hasattr(self._parent, "_log_output"):
            count = len(names)
            self._parent._log_output(
                f"Custom agent names saved ({count} override{'s' if count != 1 else ''})")
        self.destroy()

    def _clear_all(self):
        for entry in self._entries.values():
            entry.delete(0, "end")


# ─── Key Manager Dialog ─────────────────────────────────────────────────────

class KeyManagerDialog(ctk.CTkToplevel):

    def __init__(self, parent):
        L = _launcher()
        self.REGISTRY_FILE = L.FLEET_DIR / "keys_registry.toml"
        self.SECRETS_FILE  = Path.home() / ".wsl_secrets_cache"  # local cache from WSL read

        super().__init__(parent)
        self.title("BigEd CC — API Key Manager")
        self.geometry("780x540")
        self.configure(fg_color=BG)
        self.grab_set()

        ico = L.HERE / "brick.ico"
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
        ctk.CTkLabel(hdr, text="Keys stored in ~/.secrets  |  masked values shown",
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
        """Read masked key values from ~/.secrets (native, no WSL)."""
        secrets_file = Path.home() / ".secrets"
        masked = {}
        try:
            if not secrets_file.exists():
                return masked
            for line in secrets_file.read_text(encoding="utf-8", errors="ignore").splitlines():
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
        L = _launcher()
        dialog = ctk.CTkInputDialog(
            text=f"Enter value for {label}:\n({key_name})\n\nLeave blank to cancel.",
            title=f"Set {key_name}")
        value = dialog.get_input()
        if not value or not value.strip():
            return
        value = value.strip()
        # Save key natively to ~/.secrets
        def _save_bg():
            try:
                secrets_file = Path.home() / ".secrets"
                lines = []
                found = False
                if secrets_file.exists():
                    for line in secrets_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                        stripped = line.strip()
                        raw = stripped[7:].strip() if stripped.startswith("export ") else stripped
                        if "=" in raw and raw.split("=", 1)[0].strip() == key_name:
                            lines.append(f"export {key_name}={value}")
                            found = True
                        else:
                            lines.append(line)
                if not found:
                    lines.append(f"export {key_name}={value}")
                secrets_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                import os
                os.environ[key_name] = value  # update current process too
                self.after(0, lambda: (
                    self._scan_lbl.configure(text=f"✓ {key_name} saved", text_color=GREEN),
                    self.after(400, self._load_keys)))
            except Exception as e:
                self.after(0, lambda: self._scan_lbl.configure(
                    text=f"✗ {str(e)[:40]}", text_color=RED))
        threading.Thread(target=_save_bg, daemon=True).start()

    def _add_custom_key(self):
        name_dialog = ctk.CTkInputDialog(
            text="Enter env var name (e.g. MY_API_KEY):", title="Add Key")
        name = name_dialog.get_input()
        if not name or not name.strip():
            return
        name = re.sub(r'[^A-Z0-9_]', '', name.strip().upper())
        self._edit_key(name, name)

    def _scan_skills(self):
        L = _launcher()
        self._scan_lbl.configure(text="Scanning...", text_color=ORANGE)
        def _bg():
            try:
                result = subprocess.run(
                    [L._get_fleet_python(), str(L.FLEET_DIR / "lead_client.py"),
                     "dispatch", "key_manager", json.dumps({"action": "scan"}), "--priority", "9"],
                    capture_output=True, text=True, timeout=30, cwd=str(L.FLEET_DIR),
                )
                msg = "Scan queued → knowledge/reports/key_scan.md" if result.returncode == 0 else f"Error: {result.stderr[:40]}"
                self.after(0, lambda: self._scan_lbl.configure(text=msg, text_color=GREEN if result.returncode == 0 else RED))
            except Exception as e:
                self.after(0, lambda: self._scan_lbl.configure(text=f"Error: {str(e)[:40]}", text_color=RED))
        threading.Thread(target=_bg, daemon=True).start()
