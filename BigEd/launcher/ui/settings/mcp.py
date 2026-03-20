"""MCP Servers settings panel — discover, configure, manage MCP servers."""
import customtkinter as ctk
from pathlib import Path

from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    GREEN, ORANGE, RED, MONO, FONT_SM, FONT_BOLD,
    GLASS_BG, GLASS_PANEL, GLASS_BORDER,
)


class McpPanelMixin:
    """Mixin providing the MCP Servers settings panel."""

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
            status_frame, text="↻ Refresh", font=FONT_SM,
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
            dot = ctk.CTkLabel(row, text="●", font=("Consolas", 11),
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

            ctk.CTkLabel(row, text="●", font=("Consolas", 11),
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
            sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "fleet"))
            from mcp_manager import load_mcp_json
            data = load_mcp_json()
            return name in data.get("mcpServers", {})
        except Exception:
            return False

    def _toggle_mcp_default(self, name: str, enable: bool):
        """Toggle a bundled default MCP server."""
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "fleet"))
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
            sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "fleet"))
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
                ctk.CTkLabel(row, text="●", font=("Consolas", 11),
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
                sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "fleet"))
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
                sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "fleet"))
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
            sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "fleet"))
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
            sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "fleet"))
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
