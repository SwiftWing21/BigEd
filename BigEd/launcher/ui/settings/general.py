"""General settings panel — theme, agent names, fleet behavior, tabs, backup."""
import json
import re

import customtkinter as ctk
from pathlib import Path

from ui.theme import (
    BG2, BG3, ACCENT, ACCENT_H, TEXT, DIM,
    GREEN, RED, FONT_SM, FONT_BOLD,
    GLASS_BG, GLASS_PANEL, GLASS_BORDER,
)


def _launcher():
    """Return the launcher module (import once, cache)."""
    import launcher as _mod
    return _mod


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

    # ── General panel handlers ────────────────────────────────────────────

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

    # ── Module Hub handlers ────────────────────────────────────────────

    def _check_hub_updates(self):
        """Fetch available modules from the hub."""
        import threading
        def _fetch():
            try:
                import sys
                sys.path.insert(0, str(Path(__file__).parent.parent))
                from modules.hub import ModuleHub
                hub = ModuleHub()  # reads config from fleet.toml
                available = hub.list_available()
                installed = {m["name"] for m in hub.list_installed()}

                self._hub_status.configure(text=f"Found {len(available)} modules")

                # Clear old content
                for w in self._hub_modules_frame.winfo_children():
                    if w != self._hub_status:
                        w.destroy()

                for mod in available:
                    row = ctk.CTkFrame(self._hub_modules_frame, fg_color="transparent")
                    row.pack(fill="x", padx=8, pady=2)
                    row.grid_columnconfigure(1, weight=1)

                    name = mod.get("name", "?")
                    is_installed = name in installed

                    ctk.CTkLabel(row, text=name, font=FONT_BOLD,
                                 text_color=TEXT, anchor="w"
                                 ).grid(row=0, column=0, padx=(4, 8), sticky="w")
                    ctk.CTkLabel(row, text=mod.get("description", "")[:50],
                                 font=("Consolas", 9), text_color=DIM, anchor="w"
                                 ).grid(row=0, column=1, sticky="w")

                    if is_installed:
                        ctk.CTkLabel(row, text="Installed", font=FONT_SM,
                                     text_color="#4caf50"
                                     ).grid(row=0, column=2, padx=(4, 8))
                    else:
                        ctk.CTkButton(row, text="Install", font=FONT_SM,
                                      width=60, height=22, fg_color="#1e3a1e",
                                      hover_color="#2a4a2a",
                                      command=lambda n=name: self._install_module(n)
                                      ).grid(row=0, column=2, padx=(4, 8))
            except Exception as e:
                self._hub_status.configure(text=f"Hub error: {e}", text_color="#f44336")

        self._hub_status.configure(text="Checking hub...", text_color=DIM)
        threading.Thread(target=_fetch, daemon=True).start()

    def _install_module(self, name):
        """Install a module from the hub."""
        import threading
        def _do_install():
            try:
                import sys
                sys.path.insert(0, str(Path(__file__).parent.parent))
                from modules.hub import ModuleHub
                hub = ModuleHub()
                result = hub.install_module(name)
                if result.get("installed"):
                    self._hub_status.configure(
                        text=f"Installed {name} v{result.get('version')}. Restart to activate.",
                        text_color="#4caf50")
                else:
                    self._hub_status.configure(
                        text=f"Install failed: {result.get('error', 'unknown')}",
                        text_color="#f44336")
            except Exception as e:
                self._hub_status.configure(text=f"Install error: {e}", text_color="#f44336")
        threading.Thread(target=_do_install, daemon=True).start()
