"""Developer Consoles settings panel — power-user standalone console launchers."""
from __future__ import annotations
import customtkinter as ctk
from ui.theme import (GLASS_PANEL, BG2, BG3, TEXT, DIM, GOLD, FONT_SM,
                      FONT_BOLD, FONT_TITLE, ACCENT_H,
                      PROVIDER_CLAUDE, PROVIDER_GEMINI, PROVIDER_LOCAL)


class ConsolesPanelMixin:
    """Settings mixin — Developer Consoles section."""

    def _build_consoles_panel(self):
        frame = ctk.CTkScrollableFrame(self._content, fg_color=GLASS_PANEL)
        self._panels["consoles"] = frame

        ctk.CTkLabel(frame, text="Developer Consoles", font=FONT_TITLE,
                     text_color=GOLD).pack(anchor="w", padx=16, pady=(16, 4))
        ctk.CTkLabel(frame, text="Power-user standalone chat windows with full context injection",
                     font=FONT_SM, text_color=DIM).pack(anchor="w", padx=16, pady=(0, 16))

        consoles = [
            ("Local Console (Ollama)", PROVIDER_LOCAL, "_open_local_console"),
            ("Claude Console", PROVIDER_CLAUDE, "_open_claude_console"),
            ("Gemini Console", PROVIDER_GEMINI, "_open_gemini_console"),
        ]

        L = self._parent  # launcher instance

        for label, color, method_name in consoles:
            row = ctk.CTkFrame(frame, fg_color=BG2, corner_radius=6)
            row.pack(fill="x", padx=16, pady=4)

            ctk.CTkLabel(row, text=label, font=FONT_BOLD, text_color=TEXT
                         ).pack(side="left", padx=12, pady=10)

            def _open(m=method_name):
                if hasattr(L, m):
                    getattr(L, m)()

            ctk.CTkButton(
                row, text="Open", width=80, height=28, font=FONT_SM,
                fg_color=color, hover_color=ACCENT_H,
                command=_open,
            ).pack(side="right", padx=12, pady=10)

        # Usage bar visibility toggle
        sep = ctk.CTkFrame(frame, fg_color=BG3, height=1)
        sep.pack(fill="x", padx=16, pady=16)

        ctk.CTkLabel(frame, text="Fleet Comm Options", font=FONT_BOLD,
                     text_color=TEXT).pack(anchor="w", padx=16, pady=(0, 8))

        usage_var = ctk.BooleanVar(value=self._settings.get("show_usage_bar", True))

        def _toggle_usage(val=None):
            self._settings["show_usage_bar"] = usage_var.get()
            import launcher
            launcher._save_settings(self._settings)

        ctk.CTkCheckBox(
            frame, text="Show usage status bar in Fleet Comm",
            font=FONT_SM, variable=usage_var, command=_toggle_usage,
        ).pack(anchor="w", padx=16, pady=4)
