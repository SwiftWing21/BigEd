"""Review settings panel — evaluator-optimizer configuration."""
import customtkinter as ctk
from pathlib import Path

from ui.theme import (
    ACCENT, ACCENT_H, GOLD, DIM,
    GREEN, RED, FONT_SM,
    GLASS_BG, GLASS_PANEL,
)


def _launcher():
    """Return the launcher module (import once, cache)."""
    import launcher as _mod
    return _mod


class ReviewPanelMixin:
    """Mixin providing the Review settings panel."""

    def _build_review_panel(self):
        L = _launcher()
        panel = ctk.CTkScrollableFrame(self._content, fg_color=GLASS_PANEL)
        self._panels["review"] = panel

        self._section_header(panel, "Evaluator-Optimizer Review")

        # Review config summary
        cfg_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        cfg_frame.pack(fill="x", padx=16, pady=(0, 12))
        cfg_frame.grid_columnconfigure(1, weight=1)

        review_enabled = True
        review_provider = "api"
        max_rounds = 2
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "fleet"))
            from config import load_config
            cfg = load_config()
            rc = cfg.get("review", {})
            review_enabled = rc.get("enabled", True)
            review_provider = rc.get("provider", "api")
            max_rounds = rc.get("max_rounds", 2)
        except Exception:
            pass

        settings_data = [
            ("Status", "Enabled" if review_enabled else "Disabled",
             GREEN if review_enabled else RED),
            ("Provider", review_provider.upper(), GOLD),
            ("Max Rounds", str(max_rounds), DIM),
        ]

        for i, (label, value, color) in enumerate(settings_data):
            ctk.CTkLabel(cfg_frame, text=label, font=FONT_SM,
                         text_color=DIM, anchor="w", width=100
                         ).grid(row=i, column=0, padx=(12, 8), pady=6, sticky="w")
            ctk.CTkLabel(cfg_frame, text=value, font=("Consolas", 11, "bold"),
                         text_color=color, anchor="w"
                         ).grid(row=i, column=1, padx=(0, 12), pady=6, sticky="w")

        ctk.CTkFrame(cfg_frame, fg_color="transparent", height=4).grid(
            row=len(settings_data), column=0, columnspan=2)

        # Description
        self._section_header(panel, "How It Works")
        desc_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        desc_frame.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkLabel(desc_frame,
                     text="High-stakes skills (code_write, pen_test, legal_draft) go through\n"
                          "adversarial review before results are accepted. The evaluator checks\n"
                          "quality, safety, and correctness. Configurable in fleet.toml [review].",
                     font=("Segoe UI", 9), text_color=DIM, justify="left"
                     ).pack(padx=12, pady=10, anchor="w")

        ctk.CTkButton(desc_frame, text="Open Review Settings", font=FONT_SM,
                      width=160, height=28, fg_color=ACCENT, hover_color=ACCENT_H,
                      command=lambda: L.ReviewDialog(self._parent)
                      ).pack(padx=12, pady=(0, 10), anchor="w")
