"""
v0.45: Omni-Box — Ctrl+K command palette for quick fleet actions.
Spotlight-style overlay with predictive autocomplete for skills,
agent pinging, recent tasks, and system commands.
"""
import json
import tkinter as tk
import customtkinter as ctk
from pathlib import Path

# Theme constants (copied from launcher for module independence)
BG = "#1a1a2e"
BG2 = "#16213e"
BG3 = "#0f3460"
TEXT = "#e0e0e0"
DIM = "#888888"
ACCENT = "#4fc3f7"
GREEN = "#66bb6a"
GOLD = "#ffd54f"
FONT = ("RuneScape Plain 12", 11)
FONT_SM = ("RuneScape Plain 11", 10)
FONT_XS = ("RuneScape Plain 11", 9)


class OmniBox(ctk.CTkToplevel):
    """Ctrl+K command palette — quick access to fleet skills, agents, and commands."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("")
        self.geometry("600x400")
        self.configure(fg_color=BG)
        self.overrideredirect(True)  # borderless
        self.attributes("-topmost", True)

        # Center on parent
        self.update_idletasks()
        px = app.winfo_rootx() + (app.winfo_width() - 600) // 2
        py = app.winfo_rooty() + 100
        self.geometry(f"600x400+{px}+{py}")

        self._commands = self._build_command_list()
        self._filtered = list(self._commands)
        self._selected_idx = 0

        self._build_ui()
        self._entry.focus_set()

        # Close on Escape or click outside
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<FocusOut>", lambda e: self.after(100, self._check_focus))

    def _check_focus(self):
        try:
            if not self.focus_get():
                self.destroy()
        except Exception:
            pass

    def _build_ui(self):
        # Search input
        self._entry = ctk.CTkEntry(
            self, placeholder_text="Type a command, skill, or @agent...",
            font=("RuneScape Plain 12", 14), height=44,
            fg_color=BG2, text_color=TEXT, border_color=ACCENT,
        )
        self._entry.pack(fill="x", padx=12, pady=(12, 6))
        self._entry.bind("<KeyRelease>", self._on_type)
        self._entry.bind("<Return>", self._on_execute)
        self._entry.bind("<Up>", self._on_up)
        self._entry.bind("<Down>", self._on_down)

        # Results list
        self._results_frame = ctk.CTkScrollableFrame(
            self, fg_color=BG, corner_radius=0
        )
        self._results_frame.pack(fill="both", expand=True, padx=12, pady=(0, 4))

        # P3 polish — badge legend for accessibility
        ctk.CTkLabel(self, text="SYS = System   SKL = Skill   AGT = Agent",
                     font=FONT_XS, text_color=DIM).pack(side="bottom", pady=(0, 8))

        self._render_results()

    def _build_command_list(self):
        """Build the searchable command list from skills, agents, and system commands."""
        commands = []

        # System commands
        commands.extend([
            {"type": "system", "name": "Start Fleet", "desc": "Boot all fleet services", "action": "start"},
            {"type": "system", "name": "Stop Fleet", "desc": "Graceful fleet shutdown", "action": "stop"},
            {"type": "system", "name": "Fleet Status", "desc": "Show agent and task counts", "action": "status"},
            {"type": "system", "name": "Fleet Health", "desc": "Check fleet health via API", "action": "health"},
            {"type": "system", "name": "Usage Report", "desc": "Token cost breakdown", "action": "usage"},
            {"type": "system", "name": "Budget Status", "desc": "Per-skill budget check", "action": "budget"},
            {"type": "system", "name": "Settings", "desc": "Open settings panel", "action": "settings"},
            {"type": "system", "name": "Report Issue", "desc": "Generate debug report", "action": "report"},
        ])

        # Skills (from fleet/skills/ directory)
        try:
            import sys
            fleet_dir = Path(__file__).parent.parent.parent / "fleet" / "skills"
            if fleet_dir.exists():
                for f in sorted(fleet_dir.glob("*.py")):
                    if f.name.startswith("_"):
                        continue
                    commands.append({
                        "type": "skill",
                        "name": f"/{f.stem}",
                        "desc": f"Dispatch {f.stem} skill",
                        "action": f"dispatch:{f.stem}",
                    })
        except Exception:
            pass

        # Agents
        for role in ["researcher", "coder_1", "archivist", "analyst", "sales",
                      "onboarding", "implementation", "security", "planner"]:
            commands.append({
                "type": "agent",
                "name": f"@{role}",
                "desc": f"Send message to {role}",
                "action": f"ping:{role}",
            })

        return commands

    def _on_type(self, event=None):
        query = self._entry.get().lower().strip()
        if not query:
            self._filtered = list(self._commands)
        else:
            self._filtered = [
                c for c in self._commands
                if query in c["name"].lower() or query in c["desc"].lower()
            ]
        self._selected_idx = 0
        self._render_results()

    def _on_up(self, event=None):
        if self._selected_idx > 0:
            self._selected_idx -= 1
            self._render_results()
        return "break"

    def _on_down(self, event=None):
        if self._selected_idx < len(self._filtered) - 1:
            self._selected_idx += 1
            self._render_results()
        return "break"

    def _on_execute(self, event=None):
        if not self._filtered:
            return
        cmd = self._filtered[self._selected_idx]
        self.destroy()
        self._execute_command(cmd)

    def _render_results(self):
        for widget in self._results_frame.winfo_children():
            widget.destroy()

        for i, cmd in enumerate(self._filtered[:15]):
            bg = BG3 if i == self._selected_idx else BG
            frame = ctk.CTkFrame(self._results_frame, fg_color=bg, height=36, corner_radius=4)
            frame.pack(fill="x", pady=1)
            frame.pack_propagate(False)

            # Type badge
            badge_colors = {"system": ACCENT, "skill": GREEN, "agent": GOLD}
            badge_color = badge_colors.get(cmd["type"], DIM)
            ctk.CTkLabel(frame, text=cmd["type"][:3].upper(), width=36,
                        font=("Consolas", 9, "bold"), text_color=badge_color
                        ).pack(side="left", padx=(8, 4))

            # Name
            ctk.CTkLabel(frame, text=cmd["name"], font=("RuneScape Bold 12", 11, "bold"),
                        text_color=TEXT, anchor="w"
                        ).pack(side="left", padx=(0, 8))

            # Description
            ctk.CTkLabel(frame, text=cmd["desc"], font=FONT_SM,
                        text_color=DIM, anchor="w"
                        ).pack(side="left", fill="x", expand=True)

            # Click handler
            idx = i
            frame.bind("<Button-1>", lambda e, idx=idx: self._click_item(idx))

    def _click_item(self, idx):
        self._selected_idx = idx
        self._on_execute()

    def _execute_command(self, cmd):
        """Execute the selected command."""
        action = cmd.get("action", "")

        try:
            if action == "start":
                if hasattr(self.app, '_start_system'):
                    self.app._start_system()
            elif action == "stop":
                if hasattr(self.app, '_stop_system'):
                    self.app._stop_system()
            elif action == "status":
                if hasattr(self.app, '_log_output'):
                    self.app._log_output("Refreshing fleet status...")
            elif action == "health":
                if hasattr(self.app, '_fleet_api'):
                    health = self.app._fleet_api("/api/fleet/health")
                    if health and hasattr(self.app, '_log_output'):
                        self.app._log_output(f"Fleet health: {json.dumps(health, indent=2)}")
            elif action == "settings":
                if hasattr(self.app, '_open_settings'):
                    self.app._open_settings()
            elif action == "report":
                if hasattr(self.app, '_generate_debug_report'):
                    self.app._generate_debug_report()
            elif action.startswith("dispatch:"):
                skill = action.split(":", 1)[1]
                if hasattr(self.app, '_dispatch_task'):
                    self.app._dispatch_task(skill)
            elif action.startswith("ping:"):
                agent = action.split(":", 1)[1]
                if hasattr(self.app, '_log_output'):
                    self.app._log_output(f"Pinging @{agent}...")
        except Exception:
            pass
