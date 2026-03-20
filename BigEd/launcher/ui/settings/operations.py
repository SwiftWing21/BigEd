"""Operations settings panel — fleet recovery, security, marathon."""
import customtkinter as ctk

from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    FONT_SM, FONT_BOLD,
    GLASS_BG, GLASS_PANEL,
)


class OperationsPanelMixin:
    """Mixin providing the Operations settings panel."""

    def _build_operations_panel(self):
        panel = ctk.CTkScrollableFrame(self._content, fg_color=GLASS_PANEL)
        self._panels["operations"] = panel

        def _ops_card(parent, icon, label, cmd, desc, color=BG3, hover=BG2):
            """Operation action card with icon, label, description."""
            card = ctk.CTkFrame(parent, fg_color=GLASS_BG, corner_radius=6)
            card.pack(fill="x", padx=0, pady=3)
            card.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(card, text=icon, font=("RuneScape Plain 12", 16)
                         ).grid(row=0, column=0, rowspan=2, padx=(12, 8), pady=8)
            ctk.CTkLabel(card, text=label, font=FONT_BOLD,
                         text_color=TEXT, anchor="w"
                         ).grid(row=0, column=1, padx=(0, 8), pady=(8, 0), sticky="w")
            ctk.CTkLabel(card, text=desc, font=("RuneScape Plain 11", 9),
                         text_color=DIM, anchor="w"
                         ).grid(row=1, column=1, padx=(0, 8), pady=(0, 8), sticky="w")
            ctk.CTkButton(card, text="Run", font=FONT_SM,
                          width=60, height=26, fg_color=color, hover_color=hover,
                          command=cmd
                          ).grid(row=0, column=2, rowspan=2, padx=(0, 12), pady=8)

        # Fleet Recovery
        self._section_header(panel, "Fleet Recovery")
        recover_frame = ctk.CTkFrame(panel, fg_color="transparent")
        recover_frame.pack(fill="x", padx=16, pady=(0, 12))
        _ops_card(recover_frame, "↺", "Recover All",
                  self._parent._recover_all,
                  "Kill and restart Ollama + supervisor + all workers")

        # Security
        self._section_header(panel, "Security")
        sec_frame = ctk.CTkFrame(panel, fg_color="transparent")
        sec_frame.pack(fill="x", padx=16, pady=(0, 12))
        _ops_card(sec_frame, "🔍", "Security Audit",
                  self._parent._run_audit,
                  "Audit all fleet skills and configs")
        _ops_card(sec_frame, "🌐", "Pen Test",
                  self._parent._run_pentest,
                  "Network service scan of local environment")
        _ops_card(sec_frame, "📂", "Advisories",
                  self._parent._open_advisories,
                  "View and apply pending security advisories")

        # Marathon
        self._section_header(panel, "Marathon")
        marathon_frame = ctk.CTkFrame(panel, fg_color="transparent")
        marathon_frame.pack(fill="x", padx=16, pady=(0, 12))
        _ops_card(marathon_frame, "🏃", "Start Marathon",
                  self._parent._start_marathon,
                  "8-hour discussion + lead research + synthesis run")
        _ops_card(marathon_frame, "📋", "Marathon Log",
                  self._parent._show_marathon_log,
                  "Tail marathon.log — current phase and output")
        _ops_card(marathon_frame, "⏹", "Stop Marathon",
                  self._parent._stop_marathon,
                  "Kill the running marathon process",
                  "#5a2020", "#6a2828")
