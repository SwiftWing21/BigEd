"""Display settings panel — UI scale, font, always-on-top."""
import customtkinter as ctk

from ui.theme import (
    BG2, BG3, ACCENT_H, GOLD, TEXT, DIM,
    FONT_SM, FONT_PRESETS, _active_preset,
    GLASS_BG, GLASS_PANEL,
)


def _launcher():
    """Return the launcher module (import once, cache)."""
    import launcher as _mod
    return _mod


class DisplayPanelMixin:
    """Mixin providing the Display settings panel."""

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
                     font=("RuneScape Plain 11", 9), text_color=DIM).pack(padx=12, pady=(10, 6), anchor="w")

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

        # Section: Font
        self._section_header(panel, "Font")
        font_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        font_frame.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(font_frame, text="Select display font. Applied on next launch.",
                     font=FONT_SM, text_color=DIM).pack(padx=12, pady=(10, 6), anchor="w")

        font_row = ctk.CTkFrame(font_frame, fg_color="transparent")
        font_row.pack(fill="x", padx=12, pady=(0, 4))

        self._font_var = ctk.StringVar(value=_active_preset)
        preset_names = list(FONT_PRESETS.keys())
        ctk.CTkOptionMenu(
            font_row, values=preset_names, variable=self._font_var,
            font=FONT_SM, width=200, height=28,
            fg_color=BG3, button_color=GOLD, button_hover_color=ACCENT_H,
            command=self._on_font_change,
        ).pack(side="left")

        self._font_preview = ctk.CTkLabel(
            font_row, text="  Preview: BigEd CC Fleet", font=FONT_SM, text_color=TEXT)
        self._font_preview.pack(side="left", padx=(16, 0))

        # Show sample of each style
        sample_frame = ctk.CTkFrame(font_frame, fg_color="transparent")
        sample_frame.pack(fill="x", padx=12, pady=(4, 10))
        self._font_sample_normal = ctk.CTkLabel(
            sample_frame, text="Normal: The quick brown fox", font=FONT_SM, text_color=DIM)
        self._font_sample_normal.pack(anchor="w")
        self._font_sample_mono = ctk.CTkLabel(
            sample_frame, text="Mono: 114.4 tok/s | IQ: 0.85", font=("Consolas", 10), text_color=DIM)
        self._font_sample_mono.pack(anchor="w")

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
                     font=("RuneScape Plain 11", 9), text_color=DIM).pack(padx=12, pady=(0, 10), anchor="w")

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

    def _on_font_change(self, choice):
        """Update font preference and preview."""
        from ui.theme import FONT_PRESETS
        preset = FONT_PRESETS.get(choice, FONT_PRESETS["System Default"])
        # Update preview labels
        self._font_preview.configure(font=(preset["family"], 11))
        self._font_sample_normal.configure(
            text=f"Normal: The quick brown fox",
            font=(preset["family"], 11))
        self._font_sample_mono.configure(
            text=f"Mono: 114.4 tok/s | IQ: 0.85",
            font=(preset["mono"], 10))
        # Save preference
        L = _launcher()
        data = L._load_settings()
        data["font_preset"] = choice
        L._save_settings(data)

    def _on_always_on_top_toggle(self):
        on_top = self._on_top_var.get()
        self._parent.attributes("-topmost", on_top)
        self._save_display_prefs()
