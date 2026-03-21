"""MCP Servers settings panel — discover, configure, manage MCP servers."""
import json
import logging
import re
import subprocess
import sys
import threading
from pathlib import Path

import customtkinter as ctk

from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    GREEN, ORANGE, RED, MONO, FONT, FONT_SM, FONT_BOLD, FONT_TITLE,
    FONT_XS, FONT_STAT,
    GLASS_BG, GLASS_PANEL, GLASS_BORDER,
)

_log = logging.getLogger("settings.mcp")

# ── Fleet path helper ────────────────────────────────────────────────────────
_FLEET_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "fleet"


def _fleet_import(name: str):
    """Lazy-import a module from fleet/ without polluting sys.path permanently."""
    if str(_FLEET_DIR) not in sys.path:
        sys.path.insert(0, str(_FLEET_DIR))
    return __import__(name)


def _launcher():
    """Return the launcher module (import once, cache)."""
    import launcher as _mod
    return _mod


# ── Key format validators ────────────────────────────────────────────────────
_KEY_PATTERNS = {
    "GITHUB_TOKEN":   re.compile(r"^(ghp_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})$"),
    "BRAVE_API_KEY":  re.compile(r"^BSA[A-Za-z0-9_\-]{20,}$"),
    "SLACK_BOT_TOKEN": re.compile(r"^xoxb-[0-9A-Za-z\-]{30,}$"),
    "POSTGRES_URL":   re.compile(r"^postgres(ql)?://"),
}


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

        # Action buttons row
        btn_row = ctk.CTkFrame(status_frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkButton(
            btn_row, text="↻ Refresh", font=FONT_SM,
            width=80, height=26, fg_color=BG3, hover_color=BG2,
            command=self._refresh_mcp_status
        ).pack(side="left")
        ctk.CTkButton(
            btn_row, text="+ Add Integration", font=FONT_SM,
            width=130, height=26, fg_color=ACCENT, hover_color=ACCENT_H,
            command=self._open_mcp_wizard
        ).pack(side="left", padx=(8, 0))

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

            ctk.CTkLabel(row, text=name, font=("RuneScape Bold 12", 11, "bold"),
                        text_color=TEXT).grid(row=0, column=1, sticky="w")

            ctk.CTkLabel(row, text=desc, font=("RuneScape Plain 11", 9),
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
            ctk.CTkLabel(row, text=label, font=("RuneScape Bold 12", 11, "bold"),
                        text_color=TEXT).grid(row=0, column=1, sticky="w")
            ctk.CTkLabel(row, text=desc, font=("RuneScape Plain 11", 9),
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
            mcp = _fleet_import("mcp_manager")
            data = mcp.load_mcp_json()
            return name in data.get("mcpServers", {})
        except Exception:
            return False

    def _toggle_mcp_default(self, name: str, enable: bool):
        """Toggle a bundled default MCP server."""
        try:
            mcp = _fleet_import("mcp_manager")
            if enable:
                mcp.enable_default(name)
            else:
                mcp.disable_server(name)
            # Update dot color
            if name in self._mcp_toggles:
                color = GREEN if enable else "#555"
                self._mcp_toggles[name]["dot"].configure(text_color=color)
        except Exception as e:
            _log.warning("MCP toggle failed: %s", e)

    def _refresh_mcp_status(self):
        """Refresh the status display of all MCP servers."""
        # Clear existing status widgets
        for w in self._mcp_status_area.winfo_children():
            w.destroy()

        try:
            mcp = _fleet_import("mcp_manager")
            servers = mcp.get_all_server_status()

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
                ctk.CTkLabel(row, text=s["name"], font=("RuneScape Bold 12", 10, "bold"),
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
            dialog = ctk.CTkInputDialog(
                text=f"Enter {key_name} for {name}:",
                title=f"Configure {name}")
            key_value = dialog.get_input()
            if not key_value:
                return
            try:
                mcp = _fleet_import("mcp_manager")
                server_def = mcp.MCP_INTEGRATIONS.get(name, {})
                config = {"type": server_def.get("type", "stdio")}
                if config["type"] == "stdio":
                    config["command"] = server_def.get("command", "npx")
                    config["args"] = server_def.get("args", [])
                    config["env"] = {key_name: key_value}
                elif config["type"] == "http":
                    config["url"] = server_def.get("url", "")
                mcp.add_server(name, config)
                self._refresh_mcp_status()
            except Exception as e:
                _log.warning("MCP add failed: %s", e)
        else:
            try:
                mcp = _fleet_import("mcp_manager")
                server_def = mcp.MCP_INTEGRATIONS.get(name, {})
                config = {"type": server_def.get("type", "stdio")}
                if config["type"] == "stdio":
                    config["command"] = server_def.get("command", "npx")
                    config["args"] = server_def.get("args", [])
                mcp.add_server(name, config)
                self._refresh_mcp_status()
            except Exception as e:
                _log.warning("MCP add failed: %s", e)

    def _remove_mcp_server(self, name: str):
        """Remove an MCP server."""
        try:
            mcp = _fleet_import("mcp_manager")
            mcp.disable_server(name)
            self._refresh_mcp_status()
        except Exception as e:
            _log.warning("MCP remove failed: %s", e)

    def _add_custom_server(self):
        """Add a custom MCP server from the name/URL fields."""
        name = self._custom_name.get().strip()
        url = self._custom_url.get().strip()
        if not name or not url:
            return

        try:
            mcp = _fleet_import("mcp_manager")

            # Auto-detect transport
            if url.startswith("http"):
                config = {"type": "http", "url": url}
            elif url.startswith("npx "):
                parts = url.split()
                config = {"type": "stdio", "command": parts[0], "args": parts[1:]}
            else:
                config = {"type": "stdio", "command": url, "args": []}

            mcp.add_server(name, config)
            self._custom_name.delete(0, "end")
            self._custom_url.delete(0, "end")
            self._refresh_mcp_status()
        except Exception as e:
            _log.warning("Custom MCP add failed: %s", e)

    def _open_mcp_wizard(self):
        """Launch the MCP Integration Wizard modal."""
        wizard = MCPWizardDialog(self)
        self.wait_window(wizard)
        # Refresh panel state after wizard closes
        self._refresh_mcp_status()


# ═══════════════════════════════════════════════════════════════════════════════
#  MCP Integration Wizard Dialog
# ═══════════════════════════════════════════════════════════════════════════════

class MCPWizardDialog(ctk.CTkToplevel):
    """Full-screen modal wizard for adding / configuring MCP server integrations.

    Three sections:
      1. Bundled Defaults — no keys needed, one-click enable/disable
      2. One-Click Add    — need an API key, inline key input + validation
      3. Custom           — URL or npx command, auto-detect transport, test button
    """

    # ── Server catalogue ──────────────────────────────────────────────────
    BUNDLED = [
        {
            "name": "filesystem",
            "label": "Filesystem",
            "desc": "File system operations — ingest, rag_index, code_index",
            "transport": "stdio",
        },
        {
            "name": "sequential-thinking",
            "label": "Sequential Thinking",
            "desc": "Multi-step reasoning chains for plan_workload, lead_research",
            "transport": "stdio",
        },
        {
            "name": "memory",
            "label": "Memory",
            "desc": "Persistent cross-session knowledge store",
            "transport": "stdio",
        },
        {
            "name": "fetch",
            "label": "Fetch",
            "desc": "HTTP fetch for web crawling and API probing",
            "transport": "stdio",
        },
    ]

    KEYED = [
        {
            "name": "github",
            "label": "GitHub",
            "desc": "Issues, PRs, code search",
            "transport": "stdio",
            "key_env": "GITHUB_TOKEN",
            "key_hint": "ghp_... or github_pat_...",
        },
        {
            "name": "slack",
            "label": "Slack",
            "desc": "Team notifications and fleet chat bridge",
            "transport": "stdio",
            "key_env": "SLACK_BOT_TOKEN",
            "key_hint": "xoxb-...",
        },
        {
            "name": "brave-search",
            "label": "Brave Search",
            "desc": "Web search API for research skills",
            "transport": "stdio",
            "key_env": "BRAVE_API_KEY",
            "key_hint": "BSA...",
        },
        {
            "name": "postgres",
            "label": "PostgreSQL",
            "desc": "Database queries for analyze_results",
            "transport": "stdio",
            "key_env": "POSTGRES_URL",
            "key_hint": "postgresql://user:pass@host/db",
        },
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.title("BigEd CC — MCP Integration Wizard")
        self.geometry("720x620")
        self.minsize(640, 500)
        self.configure(fg_color=GLASS_BG)
        self.grab_set()

        L = _launcher()
        ico = L.HERE / "brick.ico"
        if ico.exists():
            try:
                self.iconbitmap(str(ico))
            except Exception:
                pass

        # Track pending changes: name -> config dict (None = remove)
        self._pending: dict[str, dict | None] = {}
        # Track key entries for keyed servers
        self._key_entries: dict[str, ctk.CTkEntry] = {}
        # Track toggle vars
        self._toggle_vars: dict[str, ctk.BooleanVar] = {}
        # Track status labels for inline feedback
        self._status_labels: dict[str, ctk.CTkLabel] = {}
        # Track key frames for show/hide
        self._key_frames: dict[str, ctk.CTkFrame] = {}

        self._build_ui()

    # ── UI Construction ───────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── Header ────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color=BG3, height=52, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)

        stripe = ctk.CTkFrame(hdr, fg_color=GOLD, width=3, corner_radius=0)
        stripe.grid(row=0, column=0, sticky="ns")

        title_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        title_frame.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="w")
        ctk.CTkLabel(title_frame, text="MCP INTEGRATION WIZARD",
                     font=FONT_TITLE, text_color=GOLD).pack(anchor="w")
        ctk.CTkLabel(title_frame, text="add, configure, and test MCP server connections",
                     font=FONT_XS, text_color=DIM).pack(anchor="w")

        # ── Scrollable body ───────────────────────────────────────────────
        body = ctk.CTkScrollableFrame(self, fg_color=GLASS_BG)
        body.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        body.grid_columnconfigure(0, weight=1)

        self._build_section_bundled(body)
        self._build_section_keyed(body)
        self._build_section_custom(body)

        # ── Footer ────────────────────────────────────────────────────────
        footer = ctk.CTkFrame(self, fg_color=BG3, height=50, corner_radius=0)
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_propagate(False)
        footer.grid_columnconfigure(0, weight=1)

        self._footer_status = ctk.CTkLabel(
            footer, text="", font=FONT_SM, text_color=DIM)
        self._footer_status.grid(row=0, column=0, padx=14, sticky="w")

        btn_frame = ctk.CTkFrame(footer, fg_color="transparent")
        btn_frame.grid(row=0, column=1, padx=10, pady=8, sticky="e")

        ctk.CTkButton(
            btn_frame, text="Cancel", font=FONT_SM,
            width=80, height=30, fg_color=BG2, hover_color=BG,
            command=self.destroy
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            btn_frame, text="Save Changes", font=FONT_SM,
            width=120, height=30, fg_color=ACCENT, hover_color=ACCENT_H,
            command=self._save_all
        ).pack(side="left")

    # ── Section: Bundled Defaults ─────────────────────────────────────────

    def _build_section_bundled(self, parent):
        self._section_label(parent, "BUNDLED DEFAULTS", "No API keys needed — enable with one click")

        frame = ctk.CTkFrame(parent, fg_color=GLASS_PANEL, corner_radius=6)
        frame.pack(fill="x", padx=16, pady=(0, 14))

        for i, srv in enumerate(self.BUNDLED):
            self._build_server_row(frame, srv, i, section="bundled")

    # ── Section: One-Click Add (keyed) ────────────────────────────────────

    def _build_section_keyed(self, parent):
        self._section_label(parent, "ONE-CLICK ADD", "Requires an API key — enter below to activate")

        frame = ctk.CTkFrame(parent, fg_color=GLASS_PANEL, corner_radius=6)
        frame.pack(fill="x", padx=16, pady=(0, 14))

        for i, srv in enumerate(self.KEYED):
            self._build_server_row(frame, srv, i, section="keyed")

    # ── Section: Custom ───────────────────────────────────────────────────

    def _build_section_custom(self, parent):
        self._section_label(parent, "CUSTOM SERVER", "Power users — add by URL or npx command")

        frame = ctk.CTkFrame(parent, fg_color=GLASS_PANEL, corner_radius=6)
        frame.pack(fill="x", padx=16, pady=(0, 14))
        frame.grid_columnconfigure(1, weight=1)

        # Name
        ctk.CTkLabel(frame, text="Name", font=FONT_SM,
                     text_color=DIM).grid(row=0, column=0, padx=14, pady=(12, 4), sticky="w")
        self._custom_name_entry = ctk.CTkEntry(
            frame, font=MONO, fg_color=BG, border_color=GLASS_BORDER,
            height=30, placeholder_text="my-server")
        self._custom_name_entry.grid(row=0, column=1, padx=(0, 14), pady=(12, 4), sticky="ew")

        # Command / URL
        ctk.CTkLabel(frame, text="URL / Cmd", font=FONT_SM,
                     text_color=DIM).grid(row=1, column=0, padx=14, pady=4, sticky="w")
        self._custom_url_entry = ctk.CTkEntry(
            frame, font=MONO, fg_color=BG, border_color=GLASS_BORDER,
            height=30, placeholder_text="http://localhost:8080  or  npx -y @org/server")
        self._custom_url_entry.grid(row=1, column=1, padx=(0, 14), pady=4, sticky="ew")

        # Transport auto-detect label + test button
        bottom = ctk.CTkFrame(frame, fg_color="transparent")
        bottom.grid(row=2, column=0, columnspan=2, padx=14, pady=(4, 12), sticky="ew")
        bottom.grid_columnconfigure(1, weight=1)

        self._custom_transport_lbl = ctk.CTkLabel(
            bottom, text="Transport: auto-detect", font=FONT_XS, text_color=DIM)
        self._custom_transport_lbl.grid(row=0, column=0, sticky="w")

        self._custom_status_lbl = ctk.CTkLabel(
            bottom, text="", font=FONT_XS, text_color=DIM)
        self._custom_status_lbl.grid(row=0, column=1, padx=(12, 0), sticky="w")

        ctk.CTkButton(
            bottom, text="Test Connection", font=FONT_SM,
            width=120, height=26, fg_color=BG3, hover_color=BG2,
            command=self._test_custom_connection
        ).grid(row=0, column=2, padx=(8, 4))

        ctk.CTkButton(
            bottom, text="Add", font=FONT_SM,
            width=60, height=26, fg_color=ACCENT, hover_color=ACCENT_H,
            command=self._stage_custom_server
        ).grid(row=0, column=3)

        # Bind URL field to auto-detect transport on typing
        self._custom_url_entry.bind("<KeyRelease>", self._on_custom_url_change)

    # ── Shared row builder ────────────────────────────────────────────────

    def _build_server_row(self, parent, srv: dict, index: int, section: str):
        """Build a single server row with toggle, info, status, and optional key input."""
        name = srv["name"]
        is_installed = self._check_installed(name)

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(10 if index == 0 else 4, 4))
        row.grid_columnconfigure(2, weight=1)

        # Toggle switch
        var = ctk.BooleanVar(value=is_installed)
        self._toggle_vars[name] = var
        sw = ctk.CTkSwitch(
            row, text="", variable=var, width=40,
            progress_color=GREEN, button_color=ACCENT,
            command=lambda n=name, v=var, s=section: self._on_toggle(n, v.get(), s)
        )
        sw.grid(row=0, column=0, padx=(0, 8))

        # Name + transport badge
        info = ctk.CTkFrame(row, fg_color="transparent")
        info.grid(row=0, column=1, sticky="w", padx=(0, 8))

        name_row = ctk.CTkFrame(info, fg_color="transparent")
        name_row.pack(anchor="w")
        ctk.CTkLabel(name_row, text=srv["label"],
                     font=("RuneScape Bold 12", 11, "bold"),
                     text_color=TEXT).pack(side="left")
        transport_color = ORANGE if srv["transport"] == "http" else DIM
        ctk.CTkLabel(name_row, text=f"  [{srv['transport'].upper()}]",
                     font=("Consolas", 8), text_color=transport_color
                     ).pack(side="left")

        ctk.CTkLabel(info, text=srv["desc"], font=("RuneScape Plain 11", 9),
                     text_color=DIM).pack(anchor="w")

        # Status indicator
        status_text = "Installed" if is_installed else "Not installed"
        status_color = GREEN if is_installed else "#555"
        status_lbl = ctk.CTkLabel(
            row, text=status_text, font=("Consolas", 9), text_color=status_color)
        status_lbl.grid(row=0, column=3, padx=(8, 4), sticky="e")
        self._status_labels[name] = status_lbl

        # Key input frame (keyed servers only — hidden until toggled on)
        if section == "keyed":
            key_frame = ctk.CTkFrame(parent, fg_color=BG, corner_radius=4)
            self._key_frames[name] = key_frame

            key_frame.grid_columnconfigure(1, weight=1)

            key_env = srv["key_env"]
            key_hint = srv.get("key_hint", "")

            ctk.CTkLabel(key_frame, text=f"  {key_env}",
                         font=("Consolas", 9, "bold"), text_color=GOLD
                         ).grid(row=0, column=0, padx=(10, 6), pady=6, sticky="w")

            entry = ctk.CTkEntry(
                key_frame, font=("Consolas", 10), fg_color=GLASS_BG,
                border_color=GLASS_BORDER, height=28, show="*",
                placeholder_text=key_hint)
            entry.grid(row=0, column=1, padx=(0, 6), pady=6, sticky="ew")
            self._key_entries[name] = entry

            # Validate + reveal toggle
            val_lbl = ctk.CTkLabel(key_frame, text="", font=FONT_XS, text_color=DIM)
            val_lbl.grid(row=0, column=2, padx=(0, 4))

            reveal_var = ctk.BooleanVar(value=False)
            ctk.CTkCheckBox(
                key_frame, text="Show", font=FONT_XS, text_color=DIM,
                variable=reveal_var, width=50, height=20,
                checkbox_width=16, checkbox_height=16,
                command=lambda e=entry, rv=reveal_var: e.configure(
                    show="" if rv.get() else "*")
            ).grid(row=0, column=3, padx=(0, 10), pady=6)

            # Bind validation on key release
            entry.bind("<KeyRelease>",
                       lambda ev, n=name, ke=key_env, vl=val_lbl: self._validate_key(n, ke, vl))

            # Pre-fill if key exists in env
            existing = self._get_existing_key(name)
            if existing:
                entry.insert(0, existing)

            # Show frame only if toggle is on
            if is_installed:
                key_frame.pack(fill="x", padx=24, pady=(0, 6))
            # else: stays hidden, _on_toggle will pack/forget

    # ── Event handlers ────────────────────────────────────────────────────

    def _on_toggle(self, name: str, enabled: bool, section: str):
        """Handle toggle switch change — stage the change, show/hide key input."""
        if section == "keyed" and name in self._key_frames:
            if enabled:
                self._key_frames[name].pack(fill="x", padx=24, pady=(0, 6))
            else:
                self._key_frames[name].pack_forget()

        # Update status label
        if name in self._status_labels:
            if enabled:
                self._status_labels[name].configure(text="Pending", text_color=ORANGE)
            else:
                is_installed = self._check_installed(name)
                if is_installed:
                    self._status_labels[name].configure(text="Will remove", text_color=RED)
                else:
                    self._status_labels[name].configure(text="Not installed", text_color="#555")

        self._update_footer_count()

    def _validate_key(self, name: str, key_env: str, val_label: ctk.CTkLabel):
        """Validate key format on each keystroke."""
        entry = self._key_entries.get(name)
        if not entry:
            return
        value = entry.get().strip()
        if not value:
            val_label.configure(text="", text_color=DIM)
            return

        pattern = _KEY_PATTERNS.get(key_env)
        if pattern and pattern.match(value):
            val_label.configure(text="valid", text_color=GREEN)
        elif pattern:
            val_label.configure(text="bad format", text_color=RED)
        else:
            # No pattern defined — accept anything non-empty
            val_label.configure(text="ok", text_color=GREEN)

    def _on_custom_url_change(self, _event=None):
        """Auto-detect transport from the URL/command field."""
        url = self._custom_url_entry.get().strip()
        if url.startswith("http://") or url.startswith("https://"):
            self._custom_transport_lbl.configure(
                text="Transport: HTTP (streamable)", text_color=ORANGE)
        elif url.startswith("npx ") or url.startswith("npx.cmd "):
            self._custom_transport_lbl.configure(
                text="Transport: stdio (npx)", text_color=DIM)
        elif url:
            self._custom_transport_lbl.configure(
                text="Transport: stdio (command)", text_color=DIM)
        else:
            self._custom_transport_lbl.configure(
                text="Transport: auto-detect", text_color=DIM)

    def _test_custom_connection(self):
        """Test the custom server connection in a background thread."""
        url = self._custom_url_entry.get().strip()
        if not url:
            self._custom_status_lbl.configure(text="Enter a URL or command first", text_color=RED)
            return

        self._custom_status_lbl.configure(text="Testing...", text_color=ORANGE)

        def _test():
            try:
                if url.startswith("http://") or url.startswith("https://"):
                    # HTTP probe
                    import urllib.request
                    req = urllib.request.Request(url, method="HEAD")
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        code = resp.status
                    if code < 400:
                        self.after(0, lambda: self._custom_status_lbl.configure(
                            text=f"OK (HTTP {code})", text_color=GREEN))
                    else:
                        self.after(0, lambda: self._custom_status_lbl.configure(
                            text=f"Error (HTTP {code})", text_color=RED))
                else:
                    # stdio — try to spawn and check exit quickly
                    parts = url.split()
                    cmd = parts[0]
                    args = parts[1:] if len(parts) > 1 else []
                    # Just check the command exists via --version or --help
                    test_args = [cmd] + ["--version"]
                    result = subprocess.run(
                        test_args,
                        capture_output=True, text=True, timeout=10,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    if result.returncode == 0:
                        self.after(0, lambda: self._custom_status_lbl.configure(
                            text=f"Command found: {cmd}", text_color=GREEN))
                    else:
                        self.after(0, lambda: self._custom_status_lbl.configure(
                            text=f"Command returned exit {result.returncode}", text_color=ORANGE))
            except subprocess.TimeoutExpired:
                self.after(0, lambda: self._custom_status_lbl.configure(
                    text="Timeout (command may still work)", text_color=ORANGE))
            except FileNotFoundError:
                self.after(0, lambda: self._custom_status_lbl.configure(
                    text="Command not found", text_color=RED))
            except Exception as e:
                msg = str(e)[:50]
                self.after(0, lambda: self._custom_status_lbl.configure(
                    text=f"Error: {msg}", text_color=RED))

        threading.Thread(target=_test, daemon=True).start()

    def _stage_custom_server(self):
        """Stage the custom server for saving."""
        name = self._custom_name_entry.get().strip()
        url = self._custom_url_entry.get().strip()

        if not name:
            self._custom_status_lbl.configure(text="Enter a server name", text_color=RED)
            return
        if not url:
            self._custom_status_lbl.configure(text="Enter a URL or command", text_color=RED)
            return

        # Sanitize name
        name = re.sub(r"[^a-zA-Z0-9_\-]", "-", name).strip("-")
        if not name:
            self._custom_status_lbl.configure(text="Invalid name", text_color=RED)
            return

        # Build config
        if url.startswith("http://") or url.startswith("https://"):
            config = {"type": "http", "url": url}
        else:
            parts = url.split()
            config = {"type": "stdio", "command": parts[0], "args": parts[1:]}

        self._pending[name] = config
        self._custom_status_lbl.configure(
            text=f"Staged: {name} (will save on Save Changes)", text_color=GREEN)
        self._custom_name_entry.delete(0, "end")
        self._custom_url_entry.delete(0, "end")
        self._on_custom_url_change()
        self._update_footer_count()

    # ── Save ──────────────────────────────────────────────────────────────

    def _save_all(self):
        """Write all pending changes to .mcp.json and close the wizard."""
        try:
            mcp = _fleet_import("mcp_manager")
            data = mcp.load_mcp_json()
            servers = data.setdefault("mcpServers", {})
            changes = 0

            # Process bundled defaults
            for srv in self.BUNDLED:
                name = srv["name"]
                var = self._toggle_vars.get(name)
                if var is None:
                    continue
                want_enabled = var.get()
                is_installed = name in servers

                if want_enabled and not is_installed:
                    # Enable from MCP_DEFAULTS
                    default = mcp.MCP_DEFAULTS.get(name)
                    if default:
                        cfg = {"type": default["type"]}
                        if default["type"] == "stdio":
                            cfg["command"] = default["command"]
                            cfg["args"] = default["args"]
                        elif default["type"] == "http":
                            cfg["url"] = default["url"]
                        servers[name] = cfg
                        changes += 1
                elif not want_enabled and is_installed:
                    del servers[name]
                    changes += 1

            # Process keyed servers
            for srv in self.KEYED:
                name = srv["name"]
                var = self._toggle_vars.get(name)
                if var is None:
                    continue
                want_enabled = var.get()
                is_installed = name in servers

                if want_enabled:
                    # Build config from MCP_INTEGRATIONS + user key
                    integration = mcp.MCP_INTEGRATIONS.get(name, {})
                    cfg = {"type": integration.get("type", "stdio")}
                    if cfg["type"] == "stdio":
                        cfg["command"] = integration.get("command", "npx")
                        cfg["args"] = list(integration.get("args", []))

                    # Attach key if provided
                    entry = self._key_entries.get(name)
                    if entry:
                        key_val = entry.get().strip()
                        if key_val:
                            key_env = srv["key_env"]
                            cfg.setdefault("env", {})[key_env] = key_val

                    if not is_installed or cfg != servers.get(name):
                        servers[name] = cfg
                        changes += 1
                elif not want_enabled and is_installed:
                    del servers[name]
                    changes += 1

            # Process staged custom servers
            for name, config in self._pending.items():
                if config is not None:
                    servers[name] = config
                    changes += 1
                elif name in servers:
                    del servers[name]
                    changes += 1

            mcp.save_mcp_json(data)
            _log.info("MCP wizard saved %d changes", changes)
            self.destroy()

        except Exception as e:
            _log.warning("MCP wizard save failed: %s", e)
            self._footer_status.configure(
                text=f"Save failed: {e}", text_color=RED)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _check_installed(self, name: str) -> bool:
        """Check whether a server is currently in .mcp.json."""
        try:
            mcp = _fleet_import("mcp_manager")
            data = mcp.load_mcp_json()
            return name in data.get("mcpServers", {})
        except Exception:
            return False

    def _get_existing_key(self, name: str) -> str:
        """Return the stored API key for a keyed server if already configured."""
        try:
            mcp = _fleet_import("mcp_manager")
            data = mcp.load_mcp_json()
            cfg = data.get("mcpServers", {}).get(name, {})
            env = cfg.get("env", {})
            # Return first non-empty value
            for v in env.values():
                if v:
                    return v
        except Exception:
            pass
        return ""

    def _update_footer_count(self):
        """Show how many changes are pending in the footer."""
        count = 0
        for srv in self.BUNDLED + self.KEYED:
            name = srv["name"]
            var = self._toggle_vars.get(name)
            if var is None:
                continue
            want = var.get()
            installed = self._check_installed(name)
            if want != installed:
                count += 1
        count += len(self._pending)
        if count:
            self._footer_status.configure(
                text=f"{count} change{'s' if count != 1 else ''} pending",
                text_color=ORANGE)
        else:
            self._footer_status.configure(text="No changes", text_color=DIM)

    def _section_label(self, parent, title: str, subtitle: str):
        """Render a section header with gold stripe, title, and subtitle."""
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=16, pady=(14, 6))
        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkFrame(top, fg_color=GOLD, width=3, height=14,
                     corner_radius=1).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(top, text=title, font=FONT_BOLD,
                     text_color=GOLD).pack(side="left")
        ctk.CTkLabel(frame, text=subtitle, font=FONT_XS,
                     text_color=DIM).pack(anchor="w", padx=11)
