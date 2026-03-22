"""General settings panel — theme, agent names, fleet behavior, tabs, backup, skill groups."""
import json
import re
import threading

import customtkinter as ctk
from pathlib import Path

from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    GREEN, RED, FONT_SM, FONT_XS, FONT_BOLD,
    GLASS_BG, GLASS_PANEL, GLASS_BORDER, GLASS_HOVER,
)


def _launcher():
    """Return the launcher module (import once, cache)."""
    import launcher as _mod
    return _mod


def _parse_version(v: str) -> tuple:
    """Parse a version string like '0.22' or '1.2.3' into a comparable tuple."""
    parts = re.split(r"[.\-]", str(v or "0"))
    result = []
    for p in parts:
        try:
            result.append(int(p))
        except ValueError:
            result.append(0)
    return tuple(result) if result else (0,)


class GeneralPanelMixin:
    """Mixin providing the General settings panel."""

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
                     font=("RuneScape Plain 11", 9), text_color=DIM
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
        self._names_status = ctk.CTkLabel(name_btn_frame, text="", font=("RuneScape Plain 11", 9),
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
                     font=("RuneScape Plain 11", 9), text_color=DIM
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
            btn_row, text="", font=("RuneScape Plain 11", 9), text_color=DIM)
        self._ingest_path_status.pack(side="left", padx=8)

        ctk.CTkLabel(ingest_frame,
                     text="Files from this folder appear in the Ingestion tab for import into RAG.",
                     font=("RuneScape Plain 11", 9), text_color=DIM
                     ).grid(row=3, column=0, columnspan=2, padx=12, pady=(0, 10), sticky="w")

        # Section: Visible Tabs
        self._section_header(panel, "Visible Tabs (Requires Restart)")
        tabs_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        tabs_frame.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(tabs_frame, text="Enable or disable modular launcher tabs.",
                     font=("RuneScape Plain 11", 9), text_color=DIM).pack(padx=12, pady=(10, 0), anchor="w")

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
        self._tabs_status = ctk.CTkLabel(btn_row_tabs, text="", font=("RuneScape Plain 11", 9), text_color=DIM)
        self._tabs_status.pack(side="left", padx=8)

        # ── Module Hub ──────────────────────────────────────────────────
        self._section_header(panel, "Module Hub")
        hub_frame = ctk.CTkFrame(panel, fg_color="transparent")
        hub_frame.pack(fill="x", padx=16, pady=(0, 12))

        # Hub URL display
        url_row = ctk.CTkFrame(hub_frame, fg_color=GLASS_BG, corner_radius=6)
        url_row.pack(fill="x", pady=3)
        url_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(url_row, text="Hub", font=FONT_SM, text_color=DIM,
                     anchor="w").grid(row=0, column=0, padx=12, pady=8, sticky="w")
        self._hub_url_label = ctk.CTkLabel(
            url_row, text="github.com/SwiftWing21/BigEd-ModuleHub",
            font=("Consolas", 9), text_color=DIM, anchor="w")
        self._hub_url_label.grid(row=0, column=1, padx=4, pady=8, sticky="w")

        # Check for updates button
        ctk.CTkButton(url_row, text="Check Updates", font=FONT_SM,
                      width=100, height=26, fg_color=BG3, hover_color=BG2,
                      command=self._check_hub_updates
                      ).grid(row=0, column=2, padx=(4, 12), pady=8)

        # Available modules list
        self._hub_modules_frame = ctk.CTkFrame(hub_frame, fg_color=GLASS_BG, corner_radius=6)
        self._hub_modules_frame.pack(fill="x", pady=3)
        self._hub_status = ctk.CTkLabel(self._hub_modules_frame, text="Click 'Check Updates' to browse",
                                         font=FONT_SM, text_color=DIM)
        self._hub_status.pack(padx=12, pady=8)

        # ── Skill Routing ────────────────────────────────────────────
        self._section_header(panel, "Skill Routing")
        affinity_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        affinity_frame.pack(fill="x", padx=16, pady=(0, 12))

        # Read current affinity state
        affinity_enabled = True
        try:
            import tomllib
            L = _launcher()
            with open(L.FLEET_TOML, "rb") as f:
                _toml = tomllib.load(f)
            affinity_enabled = _toml.get("affinity", {}).get("enabled", True)
        except Exception:
            pass

        self._affinity_var = ctk.BooleanVar(value=affinity_enabled)
        ctk.CTkSwitch(
            affinity_frame, text="Role-based skill affinity",
            variable=self._affinity_var, font=FONT_SM, text_color=TEXT,
            fg_color=BG3, progress_color=GOLD,
            command=self._on_affinity_toggle,
        ).pack(padx=12, pady=(10, 2), anchor="w")
        ctk.CTkLabel(affinity_frame,
                     text="ON: agents prefer skills matching their role (researcher→web_search, coder→code_review)\n"
                          "OFF: all agents can run all skills — no role preference",
                     font=FONT_XS, text_color=DIM, justify="left"
                     ).pack(padx=12, pady=(0, 10), anchor="w")

        # ── Skill Groups ──────────────────────────────────────────────
        self._section_header(panel, "Skill Groups")
        sg_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        sg_frame.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(sg_frame,
                     text="Manage skill categories used in the Add Agent dialog and prompt queue picker.",
                     font=("RuneScape Plain 11", 9), text_color=DIM
                     ).pack(padx=12, pady=(10, 6), anchor="w")

        # Button row: New Group / Reset to Defaults
        sg_btn_row = ctk.CTkFrame(sg_frame, fg_color="transparent")
        sg_btn_row.pack(fill="x", padx=12, pady=(0, 4))

        ctk.CTkButton(sg_btn_row, text="New Group", font=FONT_SM,
                      width=100, height=28, fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self._sg_new_group).pack(side="left", padx=(0, 4))
        ctk.CTkButton(sg_btn_row, text="Reset to Defaults", font=FONT_SM,
                      width=130, height=28, fg_color=BG3, hover_color=BG2,
                      command=self._sg_reset_defaults).pack(side="left", padx=4)

        self._sg_status = ctk.CTkLabel(sg_btn_row, text="", font=("RuneScape Plain 11", 9),
                                       text_color=DIM)
        self._sg_status.pack(side="left", padx=8)

        # Scrollable group list
        self._sg_list_frame = ctk.CTkScrollableFrame(
            sg_frame, fg_color="transparent", height=220,
            scrollbar_button_color=BG3, scrollbar_button_hover_color=ACCENT,
        )
        self._sg_list_frame.pack(fill="x", padx=8, pady=(4, 10))
        self._sg_list_frame.grid_columnconfigure(0, weight=1)

        self._sg_group_widgets = {}  # group_name -> dict of widgets
        self._sg_rebuild_list()

        # ── Compliance (DITL) ─────────────────────────────────────────
        self._section_header(panel, "Compliance (DITL)")
        ditl_frame = ctk.CTkFrame(panel, fg_color="transparent")
        ditl_frame.pack(fill="x", padx=16, pady=(0, 12))

        # Compliance level selector
        level_row = ctk.CTkFrame(ditl_frame, fg_color=GLASS_BG, corner_radius=6)
        level_row.pack(fill="x", pady=3)
        level_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(level_row, text="Compliance Level", font=FONT_SM,
                     text_color=TEXT, anchor="w").grid(row=0, column=0, padx=12, pady=8, sticky="w")
        self._ditl_level_var = ctk.StringVar(value="none")
        ctk.CTkOptionMenu(
            level_row, variable=self._ditl_level_var,
            values=["none", "soc2", "hipaa"],
            font=FONT_SM, width=100, height=26,
            fg_color=BG3, command=self._on_ditl_level_change
        ).grid(row=0, column=1, padx=8, pady=8, sticky="e")

        # Force local PHI toggle
        self._ditl_local_var = ctk.BooleanVar(value=True)
        local_row = ctk.CTkFrame(ditl_frame, fg_color=GLASS_BG, corner_radius=6)
        local_row.pack(fill="x", pady=3)
        ctk.CTkCheckBox(
            local_row, text="Force local processing for PHI (recommended)",
            variable=self._ditl_local_var, font=FONT_SM, text_color=TEXT,
            fg_color=ACCENT, hover_color=ACCENT_H,
        ).pack(padx=12, pady=8, anchor="w")

        # Disable at own risk
        self._ditl_disable_var = ctk.BooleanVar(value=False)
        risk_row = ctk.CTkFrame(ditl_frame, fg_color=GLASS_BG, corner_radius=6)
        risk_row.pack(fill="x", pady=3)
        ctk.CTkCheckBox(
            risk_row, text="Disable compliance (at own risk)",
            variable=self._ditl_disable_var, font=FONT_SM, text_color=TEXT,
            fg_color="#5a2020", hover_color="#6a2828",
            command=self._on_ditl_disable_toggle,
        ).pack(padx=12, pady=8, anchor="w")
        self._ditl_warning = ctk.CTkLabel(
            risk_row, text="", font=("RuneScape Plain 11", 9), text_color="#f44336")
        self._ditl_warning.pack(padx=24, pady=(0, 6), anchor="w")

        # Section: Backup & Restore
        self._section_header(panel, "Backup & Restore")
        backup_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        backup_frame.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(backup_frame, text="Export or import configurations securely.",
                     font=("RuneScape Plain 11", 9), text_color=DIM).pack(padx=12, pady=(10, 6), anchor="w")

        btn_row2 = ctk.CTkFrame(backup_frame, fg_color="transparent")
        btn_row2.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(btn_row2, text="Export Config", font=FONT_SM, width=120, height=28,
                      fg_color=BG3, hover_color=BG2, command=self._export_config).pack(side="left")
        ctk.CTkButton(btn_row2, text="Import Config", font=FONT_SM, width=120, height=28,
                      fg_color=BG3, hover_color=BG2, command=self._import_config).pack(side="left", padx=8)

        # Section: Help & Documentation
        self._section_header(panel, "Help & Documentation")
        help_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        help_frame.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkButton(help_frame, text="Open VS Code Help Guide", font=FONT_SM,
                      height=28, width=200, fg_color=BG3, hover_color=BG2,
                      command=self._open_vscode_help).pack(padx=12, pady=10, anchor="w")

    # ── General panel handlers ────────────────────────────────────────────

    def _open_vscode_help(self):
        """Open the VS Code README help guide in VS Code."""
        import subprocess
        import shutil
        L = _launcher()
        code_exe = shutil.which("code")
        readme = L.FLEET_DIR / "VSCODE_README.md"
        if code_exe and readme.exists():
            subprocess.Popen(
                [code_exe, "--goto", str(readme)],
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

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

    # ── Affinity handler ──────────────────────────────────────────────

    def _on_affinity_toggle(self):
        enabled = self._affinity_var.get()
        try:
            self._update_toml_value("affinity", "enabled", enabled)
        except Exception:
            pass

    # ── Skill Group handlers ─────────────────────────────────────────

    def _sg_rebuild_list(self):
        """Rebuild the skill groups list from current state."""
        from ui.skill_picker import (
            SKILL_GROUPS, load_custom_groups, save_custom_groups,
            _discover_all_skills,
        )

        # Clear existing widgets
        for w in self._sg_list_frame.winfo_children():
            w.destroy()
        self._sg_group_widgets.clear()

        groups = _discover_all_skills()
        custom = load_custom_groups()
        builtin_names = set(SKILL_GROUPS.keys())

        for group_name, skills in groups.items():
            if not skills:
                continue

            is_custom = group_name not in builtin_names
            is_modified = (
                group_name in builtin_names
                and set(skills) != set(SKILL_GROUPS.get(group_name, []))
            )

            # Group header row
            hdr = ctk.CTkFrame(self._sg_list_frame, fg_color=BG3, corner_radius=4)
            hdr.pack(fill="x", pady=(4, 0))
            hdr.grid_columnconfigure(1, weight=1)

            # Collapse/expand toggle
            collapsed_var = ctk.BooleanVar(value=True)  # start collapsed
            arrow_label = ctk.CTkLabel(hdr, text="\u25b8", font=FONT_SM,
                                       text_color=DIM, width=16)
            arrow_label.grid(row=0, column=0, padx=(8, 0), pady=6)

            # Group name + skill count
            tag = ""
            tag_color = TEXT
            if is_custom:
                tag = "  [custom]"
                tag_color = GOLD
            elif is_modified:
                tag = "  [modified]"
                tag_color = "#ff9800"

            name_label = ctk.CTkLabel(
                hdr, text=f"{group_name} ({len(skills)}){tag}",
                font=FONT_BOLD, text_color=tag_color if tag else TEXT,
                anchor="w",
            )
            name_label.grid(row=0, column=1, padx=4, pady=6, sticky="w")

            # Buttons on the right
            btn_frame = ctk.CTkFrame(hdr, fg_color="transparent")
            btn_frame.grid(row=0, column=2, padx=(4, 8), pady=4, sticky="e")

            if is_custom:
                ctk.CTkButton(
                    btn_frame, text="Delete", font=FONT_XS,
                    width=50, height=22, fg_color="#5a2020", hover_color="#6a2828",
                    command=lambda g=group_name: self._sg_delete_group(g),
                ).pack(side="right", padx=2)

            # Skills container (hidden by default)
            skills_frame = ctk.CTkFrame(self._sg_list_frame, fg_color="transparent")

            # Populate skills inside
            for idx, skill in enumerate(sorted(skills)):
                row_frame = ctk.CTkFrame(skills_frame, fg_color="transparent")
                row_frame.pack(fill="x", padx=4, pady=1)
                row_frame.grid_columnconfigure(0, weight=1)

                ctk.CTkLabel(
                    row_frame, text=f"  {skill}", font=FONT_XS,
                    text_color=TEXT, anchor="w",
                ).grid(row=0, column=0, sticky="w", padx=(16, 4))

                ctk.CTkButton(
                    row_frame, text="Move", font=FONT_XS,
                    width=40, height=20, fg_color=BG3, hover_color=GLASS_HOVER,
                    command=lambda s=skill, g=group_name: self._sg_move_skill(s, g),
                ).grid(row=0, column=1, padx=2)

            # Store references for toggle
            self._sg_group_widgets[group_name] = {
                "header": hdr,
                "skills_frame": skills_frame,
                "collapsed": collapsed_var,
                "arrow": arrow_label,
            }

            # Bind header click for expand/collapse
            def _toggle(gn=group_name):
                w = self._sg_group_widgets[gn]
                is_collapsed = w["collapsed"].get()
                if is_collapsed:
                    # Expand
                    w["skills_frame"].pack(fill="x", pady=(0, 2), after=w["header"])
                    w["arrow"].configure(text="\u25be")
                    w["collapsed"].set(False)
                else:
                    # Collapse
                    w["skills_frame"].pack_forget()
                    w["arrow"].configure(text="\u25b8")
                    w["collapsed"].set(True)

            hdr.bind("<Button-1>", lambda e, gn=group_name: _toggle(gn))
            arrow_label.bind("<Button-1>", lambda e, gn=group_name: _toggle(gn))
            name_label.bind("<Button-1>", lambda e, gn=group_name: _toggle(gn))

    def _sg_new_group(self):
        """Show a dialog to create a new custom skill group."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("BigEd CC — New Skill Group")
        dialog.geometry("340x150")
        dialog.resizable(False, False)
        dialog.configure(fg_color=GLASS_BG)
        dialog.grab_set()
        dialog.focus_force()

        try:
            ico = Path(__file__).resolve().parent.parent.parent / "brick.ico"
            if ico.exists():
                dialog.iconbitmap(str(ico))
        except Exception:
            pass

        # Center on parent
        dialog.update_idletasks()
        if self.winfo_exists():
            px = self.winfo_rootx() + (self.winfo_width() - 340) // 2
            py = self.winfo_rooty() + (self.winfo_height() - 150) // 2
            dialog.geometry(f"+{max(0, px)}+{max(0, py)}")

        ctk.CTkLabel(dialog, text="Group Name:", font=FONT_SM,
                     text_color=TEXT).pack(padx=16, pady=(16, 4), anchor="w")

        name_var = ctk.StringVar()
        entry = ctk.CTkEntry(dialog, textvariable=name_var, font=FONT_SM,
                             fg_color=BG3, border_color=GLASS_BORDER,
                             text_color=TEXT, height=32, width=300)
        entry.pack(padx=16, pady=(0, 8))
        entry.focus_set()

        status_label = ctk.CTkLabel(dialog, text="", font=FONT_XS, text_color=RED)
        status_label.pack(padx=16, anchor="w")

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(4, 12))

        def _create():
            name = name_var.get().strip()
            if not name:
                status_label.configure(text="Name cannot be empty.")
                return
            if len(name) > 40:
                status_label.configure(text="Name too long (max 40 chars).")
                return

            from ui.skill_picker import load_custom_groups, save_custom_groups, SKILL_GROUPS
            # Check for duplicates against built-in + custom
            all_names = set(SKILL_GROUPS.keys())
            custom = load_custom_groups()
            all_names.update(custom.get("custom_groups", {}).keys())
            if name in all_names:
                status_label.configure(text=f"Group '{name}' already exists.")
                return

            custom["custom_groups"][name] = []
            save_custom_groups(custom)
            dialog.grab_release()
            dialog.destroy()
            self._sg_rebuild_list()
            self._sg_status.configure(text=f"Created '{name}'", text_color=GREEN)

        entry.bind("<Return>", lambda e: _create())

        ctk.CTkButton(btn_row, text="Create", font=FONT_SM,
                      width=80, height=28, fg_color=ACCENT, hover_color=ACCENT_H,
                      command=_create).pack(side="right", padx=(4, 0))
        ctk.CTkButton(btn_row, text="Cancel", font=FONT_SM,
                      width=70, height=28, fg_color=BG3, hover_color=BG2,
                      command=lambda: (dialog.grab_release(), dialog.destroy())
                      ).pack(side="right", padx=4)

        dialog.bind("<Escape>", lambda e: (dialog.grab_release(), dialog.destroy()))

    def _sg_delete_group(self, group_name: str):
        """Delete a custom group — skills return to their original built-in group."""
        from ui.skill_picker import (
            SKILL_GROUPS, load_custom_groups, save_custom_groups, _SKILL_TO_GROUP,
        )
        custom = load_custom_groups()

        # Remove the custom group
        custom["custom_groups"].pop(group_name, None)

        # Remove any overrides pointing to this group
        overrides = custom.get("overrides", {})
        to_remove = [s for s, g in overrides.items() if g == group_name]
        for s in to_remove:
            del overrides[s]

        save_custom_groups(custom)
        self._sg_rebuild_list()
        self._sg_status.configure(text=f"Deleted '{group_name}'", text_color=GREEN)

    def _sg_move_skill(self, skill_name: str, current_group: str):
        """Show a dialog to move a skill to a different group."""
        from ui.skill_picker import (
            SKILL_GROUPS, load_custom_groups, save_custom_groups,
            _discover_all_skills,
        )

        groups = _discover_all_skills()
        group_names = [g for g in groups.keys() if g != current_group]

        if not group_names:
            self._sg_status.configure(text="No other groups to move to.", text_color=RED)
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Move '{skill_name}'")
        dialog.geometry("320x180")
        dialog.resizable(False, False)
        dialog.configure(fg_color=GLASS_BG)
        dialog.grab_set()
        dialog.focus_force()

        try:
            ico = Path(__file__).resolve().parent.parent.parent / "brick.ico"
            if ico.exists():
                dialog.iconbitmap(str(ico))
        except Exception:
            pass

        # Center on parent
        dialog.update_idletasks()
        if self.winfo_exists():
            px = self.winfo_rootx() + (self.winfo_width() - 320) // 2
            py = self.winfo_rooty() + (self.winfo_height() - 180) // 2
            dialog.geometry(f"+{max(0, px)}+{max(0, py)}")

        ctk.CTkLabel(dialog, text=f"Move '{skill_name}' from '{current_group}' to:",
                     font=FONT_SM, text_color=TEXT, wraplength=280
                     ).pack(padx=16, pady=(16, 8), anchor="w")

        target_var = ctk.StringVar(value=group_names[0])
        ctk.CTkOptionMenu(
            dialog, values=group_names, variable=target_var,
            font=FONT_SM, fg_color=BG3, button_color=ACCENT,
            button_hover_color=ACCENT_H, height=30, width=280,
        ).pack(padx=16, pady=(0, 8))

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(8, 12))

        def _move():
            target = target_var.get()
            custom = load_custom_groups()
            overrides = custom.setdefault("overrides", {})
            custom_groups = custom.setdefault("custom_groups", {})

            # Record the override
            overrides[skill_name] = target

            # If the target is a custom group, also add to its skill list
            if target in custom_groups:
                if skill_name not in custom_groups[target]:
                    custom_groups[target].append(skill_name)

            # If the source was a custom group, remove from its skill list
            if current_group in custom_groups:
                cg_skills = custom_groups[current_group]
                if skill_name in cg_skills:
                    cg_skills.remove(skill_name)

            save_custom_groups(custom)
            dialog.grab_release()
            dialog.destroy()
            self._sg_rebuild_list()
            self._sg_status.configure(
                text=f"Moved '{skill_name}' to '{target}'", text_color=GREEN)

        ctk.CTkButton(btn_row, text="Move", font=FONT_SM,
                      width=70, height=28, fg_color=ACCENT, hover_color=ACCENT_H,
                      command=_move).pack(side="right", padx=(4, 0))
        ctk.CTkButton(btn_row, text="Cancel", font=FONT_SM,
                      width=70, height=28, fg_color=BG3, hover_color=BG2,
                      command=lambda: (dialog.grab_release(), dialog.destroy())
                      ).pack(side="right", padx=4)

        dialog.bind("<Escape>", lambda e: (dialog.grab_release(), dialog.destroy()))

    def _sg_reset_defaults(self):
        """Reset skill groups to built-in defaults — deletes custom_skill_groups.json."""
        from ui.skill_picker import reset_custom_groups
        reset_custom_groups()
        self._sg_rebuild_list()
        self._sg_status.configure(text="Reset to defaults.", text_color=GREEN)

    # ── DITL handlers ─────────────────────────────────────────────────

    def _on_ditl_level_change(self, value):
        try:
            self._update_toml_value("ditl", "compliance_level", value)
            enabled = value != "none"
            self._update_toml_value("ditl", "enabled", enabled)
        except Exception:
            pass

    def _on_ditl_disable_toggle(self):
        if self._ditl_disable_var.get():
            self._ditl_warning.configure(
                text="WARNING: Disabling compliance removes HIPAA safeguards.\n"
                     "PHI may be sent to cloud APIs without BAA verification.")
            self._update_toml_value("ditl", "disable_at_own_risk", True)
        else:
            self._ditl_warning.configure(text="")
            self._update_toml_value("ditl", "disable_at_own_risk", False)

    # ── Module Hub handlers ────────────────────────────────────────────

    def _get_hub(self):
        """Return a ModuleHub instance (cached import path)."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from modules.hub import ModuleHub
        return ModuleHub()

    def _check_hub_updates(self):
        """Fetch available modules from the hub and render module cards."""
        def _fetch():
            try:
                hub = self._get_hub()
                available = hub.list_available()
                installed_list = hub.list_installed()
                installed = {m["name"]: m for m in installed_list}

                # Read current tab config for enable/disable state
                L = _launcher()
                tab_cfg = L.load_tab_cfg()

                # Count stats
                n_installed = sum(1 for m in available if m.get("name") in installed)
                n_updates = len(hub.get_update_available())
                enterprise_tag = " (enterprise)" if hub.is_enterprise() else ""
                self._hub_status.configure(
                    text=f"Found {len(available)} modules | {n_installed} installed | {n_updates} updates{enterprise_tag}")

                # Clear old content (keep status label)
                for w in self._hub_modules_frame.winfo_children():
                    if w != self._hub_status:
                        w.destroy()

                # Build module cards
                for mod in available:
                    name = mod.get("name", "?")
                    is_installed = name in installed
                    is_enterprise_only = mod.get("enterprise_only", False)
                    hub_version = mod.get("version", "?")
                    local_version = installed.get(name, {}).get("version", "")
                    has_update = (is_installed and _parse_version(hub_version) > _parse_version(local_version))
                    is_enabled = tab_cfg.get(name, mod.get("default_enabled", False))

                    # Card frame
                    card = ctk.CTkFrame(self._hub_modules_frame, fg_color=GLASS_BG, corner_radius=6)
                    card.pack(fill="x", padx=8, pady=3)
                    card.grid_columnconfigure(1, weight=1)

                    # Row 0: name + version + description
                    name_text = name
                    if is_enterprise_only:
                        name_text += "  [enterprise]"
                    ctk.CTkLabel(card, text=name_text, font=FONT_BOLD,
                                 text_color=TEXT, anchor="w"
                                 ).grid(row=0, column=0, padx=(8, 4), pady=(6, 0), sticky="w")

                    ver_text = f"v{hub_version}"
                    if is_installed and local_version:
                        ver_text = f"v{local_version}"
                        if has_update:
                            ver_text += f" -> v{hub_version}"
                    ctk.CTkLabel(card, text=ver_text,
                                 font=("Consolas", 9),
                                 text_color="#ff9800" if has_update else DIM,
                                 anchor="w"
                                 ).grid(row=0, column=1, padx=4, pady=(6, 0), sticky="w")

                    # Row 1: description + tags
                    desc = mod.get("description", "")[:60]
                    tags = ", ".join(mod.get("tags", []))
                    if tags:
                        desc += f"  [{tags}]"
                    ctk.CTkLabel(card, text=desc,
                                 font=("Consolas", 9), text_color=DIM, anchor="w"
                                 ).grid(row=1, column=0, columnspan=2, padx=8, pady=(0, 2), sticky="w")

                    # Row 0-1: action buttons (right side)
                    btn_frame = ctk.CTkFrame(card, fg_color="transparent")
                    btn_frame.grid(row=0, column=2, rowspan=2, padx=(4, 8), pady=4, sticky="e")

                    if is_installed:
                        # Enable/Disable toggle
                        toggle_text = "Disable" if is_enabled else "Enable"
                        toggle_color = "#5c2020" if is_enabled else "#1e3a1e"
                        toggle_hover = "#7a2a2a" if is_enabled else "#2a4a2a"
                        ctk.CTkButton(
                            btn_frame, text=toggle_text, font=FONT_SM,
                            width=60, height=22,
                            fg_color=toggle_color, hover_color=toggle_hover,
                            command=lambda n=name, en=is_enabled: self._toggle_module(n, en)
                        ).pack(side="left", padx=2)

                        if has_update:
                            ctk.CTkButton(
                                btn_frame, text="Update", font=FONT_SM,
                                width=60, height=22,
                                fg_color="#1a3a5c", hover_color="#244a6c",
                                command=lambda n=name: self._install_module(n)
                            ).pack(side="left", padx=2)
                    else:
                        ctk.CTkButton(
                            btn_frame, text="Install", font=FONT_SM,
                            width=60, height=22,
                            fg_color="#1e3a1e", hover_color="#2a4a2a",
                            command=lambda n=name: self._install_module(n)
                        ).pack(side="left", padx=2)

            except Exception as e:
                self._hub_status.configure(text=f"Hub error: {e}", text_color="#f44336")

        self._hub_status.configure(text="Checking hub...", text_color=DIM)
        threading.Thread(target=_fetch, daemon=True).start()

    def _install_module(self, name):
        """Install a module from the hub."""
        def _do_install():
            try:
                hub = self._get_hub()
                result = hub.install_module(name)
                if result.get("installed"):
                    self._hub_status.configure(
                        text=f"Installed {name} v{result.get('version')}. Restart to activate.",
                        text_color="#4caf50")
                    # Refresh the module list to show new state
                    self._check_hub_updates()
                else:
                    self._hub_status.configure(
                        text=f"Install failed: {result.get('error', 'unknown')}",
                        text_color="#f44336")
            except Exception as e:
                self._hub_status.configure(text=f"Install error: {e}", text_color="#f44336")
        threading.Thread(target=_do_install, daemon=True).start()

    def _toggle_module(self, name, currently_enabled):
        """Enable or disable a module via the hub."""
        def _do_toggle():
            try:
                hub = self._get_hub()
                if currently_enabled:
                    result = hub.disable_module(name)
                else:
                    result = hub.enable_module(name)
                if result.get("error"):
                    self._hub_status.configure(
                        text=f"Toggle failed: {result['error']}", text_color="#f44336")
                else:
                    state = "disabled" if currently_enabled else "enabled"
                    self._hub_status.configure(
                        text=f"Module '{name}' {state}. Restart to apply.",
                        text_color="#4caf50")
                    # Refresh cards
                    self._check_hub_updates()
            except Exception as e:
                self._hub_status.configure(text=f"Toggle error: {e}", text_color="#f44336")
        threading.Thread(target=_do_toggle, daemon=True).start()
