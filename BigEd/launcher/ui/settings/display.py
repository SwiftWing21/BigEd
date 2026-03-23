"""Display settings panel — UI scale, font, theme, always-on-top."""
import customtkinter as ctk

from ui.theme import (
    BG2, BG3, ACCENT_H, GOLD, TEXT, DIM,
    FONT_SM, FONT_PRESETS, _active_preset,
    THEME_PRESETS, _active_theme_name,
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

        # Section: Theme
        self._section_header(panel, "Theme")
        theme_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        theme_frame.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(theme_frame, text="Select color theme. Applied on next launch.",
                     font=FONT_SM, text_color=DIM).pack(padx=12, pady=(10, 6), anchor="w")

        theme_row = ctk.CTkFrame(theme_frame, fg_color="transparent")
        theme_row.pack(fill="x", padx=12, pady=(0, 4))

        self._theme_var = ctk.StringVar(value=_active_theme_name)
        theme_names = list(THEME_PRESETS.keys())
        ctk.CTkOptionMenu(
            theme_row, values=theme_names, variable=self._theme_var,
            font=FONT_SM, width=200, height=28,
            fg_color=BG3, button_color=GOLD, button_hover_color=ACCENT_H,
            command=self._on_theme_change,
        ).pack(side="left")

        _THEME_DESCRIPTIONS = {
            "Classic": "Original dark palette with sharp corners",
            "Modern": "Deeper tones, blue tint, softer rounded corners",
        }
        self._theme_desc = ctk.CTkLabel(
            theme_frame, text=_THEME_DESCRIPTIONS.get(_active_theme_name, ""),
            font=FONT_SM, text_color=DIM)
        self._theme_desc.pack(padx=12, pady=(4, 10), anchor="w")

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

        # Section: System Tray
        self._section_header(panel, "System Tray")
        tray_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        tray_frame.pack(fill="x", padx=16, pady=(0, 12))

        # Check if pystray is available
        from ui.tray import _tray_available
        tray_ok = _tray_available()

        tray_status = "pystray available" if tray_ok else "pystray not installed -- tray disabled"
        ctk.CTkLabel(tray_frame, text=tray_status,
                     font=FONT_SM, text_color=GOLD if tray_ok else DIM).pack(
                         padx=12, pady=(10, 4), anchor="w")

        # Close behavior: tray vs quit
        close_row = ctk.CTkFrame(tray_frame, fg_color="transparent")
        close_row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(close_row, text="On window close:",
                     font=FONT_SM, text_color=TEXT).pack(side="left")
        self._close_behavior_var = ctk.StringVar(
            value=prefs.get("close_behavior", "tray"))
        ctk.CTkOptionMenu(
            close_row, values=["tray", "quit"],
            variable=self._close_behavior_var,
            font=FONT_SM, width=140, height=28,
            fg_color=BG3, button_color=GOLD, button_hover_color=ACCENT_H,
            command=self._on_close_behavior_change,
            state="normal" if tray_ok else "disabled",
        ).pack(side="left", padx=(8, 0))

        ctk.CTkLabel(tray_frame,
                     text="'tray' = minimize to system tray, 'quit' = show close dialog",
                     font=FONT_SM, text_color=DIM).pack(padx=12, pady=(0, 4), anchor="w")

        # Start minimized
        self._start_minimized_var = ctk.BooleanVar(
            value=prefs.get("start_minimized", False))
        ctk.CTkSwitch(tray_frame, text="Start minimized to tray",
                      font=FONT_SM, text_color=TEXT,
                      variable=self._start_minimized_var,
                      command=self._save_display_prefs,
                      fg_color=BG3, progress_color=GOLD,
                      state="normal" if tray_ok else "disabled",
                      ).pack(padx=12, pady=4, anchor="w")

        # Tray notifications
        self._tray_notif_var = ctk.BooleanVar(
            value=prefs.get("tray_notifications", True))
        ctk.CTkSwitch(tray_frame, text="Show HITL notifications in tray",
                      font=FONT_SM, text_color=TEXT,
                      variable=self._tray_notif_var,
                      command=self._save_display_prefs,
                      fg_color=BG3, progress_color=GOLD,
                      state="normal" if tray_ok else "disabled",
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
        # System tray prefs
        if hasattr(self, "_close_behavior_var"):
            data["close_behavior"] = self._close_behavior_var.get()
        if hasattr(self, "_start_minimized_var"):
            data["start_minimized"] = self._start_minimized_var.get()
        if hasattr(self, "_tray_notif_var"):
            data["tray_notifications"] = self._tray_notif_var.get()
        L._save_settings(data)

    def _on_close_behavior_change(self, choice):
        """Handle close behavior option menu change."""
        self._save_display_prefs()

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

    def _on_theme_change(self, choice):
        """Save theme preference and update description label."""
        _THEME_DESCRIPTIONS = {
            "Classic": "Original dark palette with sharp corners",
            "Modern": "Deeper tones, blue tint, softer rounded corners",
        }
        self._theme_desc.configure(text=_THEME_DESCRIPTIONS.get(choice, ""))
        L = _launcher()
        data = L._load_settings()
        data["theme_preset"] = choice
        L._save_settings(data)

    def _on_always_on_top_toggle(self):
        on_top = self._on_top_var.get()
        self._parent.attributes("-topmost", on_top)
        self._save_display_prefs()
