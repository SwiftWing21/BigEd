"""Models settings panel — model selection, Stable Diffusion config."""
import customtkinter as ctk

from ui.theme import (
    BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    GREEN, ORANGE, FONT_SM,
    GLASS_BG, GLASS_PANEL, GLASS_BORDER,
)


def _launcher():
    import launcher as _mod
    return _mod


class ModelsPanelMixin:
    """Mixin providing the Models settings panel."""

    def _build_models_panel(self):
        L = _launcher()
        panel = ctk.CTkScrollableFrame(self._content, fg_color=GLASS_PANEL)
        self._panels["models"] = panel

        # LLM Model button
        self._section_header(panel, "LLM Model")
        llm_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        llm_frame.pack(fill="x", padx=16, pady=(0, 12))

        current_model = L.load_model_cfg().get("local", "qwen3:8b")
        ctk.CTkLabel(llm_frame, text=f"Current: {current_model}",
                     font=("Consolas", 10), text_color=TEXT
                     ).pack(padx=12, pady=(12, 4), anchor="w")
        ctk.CTkLabel(llm_frame,
                     text="Select the Ollama model used by fleet workers for local inference.",
                     font=("RuneScape Plain 11", 9), text_color=DIM
                     ).pack(padx=12, pady=(0, 6), anchor="w")
        ctk.CTkButton(llm_frame, text="Open Model Selector", font=FONT_SM,
                      width=160, height=30, fg_color=BG3, hover_color=BG2,
                      command=lambda: L.ModelSelectorDialog(self._parent)
                      ).pack(padx=12, pady=(0, 12), anchor="w")

        # Diffusion Models
        self._section_header(panel, "Image Generation (Stable Diffusion)")
        diff_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        diff_frame.pack(fill="x", padx=16, pady=(0, 12))

        diff_settings = self._settings.get("diffusion", {})

        # SD 1.5 toggle
        sd15_row = ctk.CTkFrame(diff_frame, fg_color="transparent")
        sd15_row.pack(fill="x", padx=12, pady=(12, 0))
        sd15_row.grid_columnconfigure(1, weight=1)

        self._sd15_var = ctk.BooleanVar(value=diff_settings.get("sd15_enabled", True))
        ctk.CTkSwitch(
            sd15_row, text="  SD 1.5  —  GPU (fp16)",
            variable=self._sd15_var, font=FONT_SM, text_color=TEXT,
            progress_color=GREEN, button_color=TEXT,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(sd15_row, text="~4 GB VRAM  |  ~30s/image  |  512x512",
                     font=("Consolas", 9), text_color=DIM
                     ).grid(row=0, column=1, padx=(12, 0), sticky="w")

        sd15_detail = ctk.CTkFrame(diff_frame, fg_color="transparent")
        sd15_detail.pack(fill="x", padx=24, pady=(2, 0))
        ctk.CTkLabel(sd15_detail,
                     text="Fast local generation on GPU. Good for iteration and drafts.",
                     font=("RuneScape Plain 11", 9), text_color=DIM
                     ).pack(anchor="w")

        # SDXL toggle
        sdxl_row = ctk.CTkFrame(diff_frame, fg_color="transparent")
        sdxl_row.pack(fill="x", padx=12, pady=(12, 0))
        sdxl_row.grid_columnconfigure(1, weight=1)

        self._sdxl_var = ctk.BooleanVar(value=diff_settings.get("sdxl_enabled", False))
        ctk.CTkSwitch(
            sdxl_row, text="  SDXL  —  CPU (fp32)",
            variable=self._sdxl_var, font=FONT_SM, text_color=TEXT,
            progress_color=ORANGE, button_color=TEXT,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(sdxl_row, text="~12 GB RAM  |  ~10-15 min/image  |  768x768",
                     font=("Consolas", 9), text_color=DIM
                     ).grid(row=0, column=1, padx=(12, 0), sticky="w")

        sdxl_detail = ctk.CTkFrame(diff_frame, fg_color="transparent")
        sdxl_detail.pack(fill="x", padx=24, pady=(2, 0))
        ctk.CTkLabel(sdxl_detail,
                     text="Higher quality output on CPU. Slow but no VRAM cost.",
                     font=("RuneScape Plain 11", 9), text_color=DIM
                     ).pack(anchor="w")

        # Default model selector
        default_row = ctk.CTkFrame(diff_frame, fg_color="transparent")
        default_row.pack(fill="x", padx=12, pady=(12, 0))
        ctk.CTkLabel(default_row, text="Default model:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._diff_default_var = ctk.StringVar(
            value=diff_settings.get("default_model", "sd15"))
        ctk.CTkOptionMenu(
            default_row, values=["sd15", "sdxl"],
            variable=self._diff_default_var, font=FONT_SM,
            fg_color=BG3, button_color=ACCENT, button_hover_color=ACCENT_H,
            height=28, width=100,
        ).pack(side="left", padx=(8, 0))

        # Steps / guidance
        params_row = ctk.CTkFrame(diff_frame, fg_color="transparent")
        params_row.pack(fill="x", padx=12, pady=(10, 0))

        ctk.CTkLabel(params_row, text="Steps:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._diff_steps_var = ctk.StringVar(
            value=str(diff_settings.get("default_steps", 30)))
        ctk.CTkEntry(params_row, textvariable=self._diff_steps_var,
                     font=FONT_SM, fg_color=GLASS_BG, border_color=GLASS_BORDER,
                     text_color=TEXT, width=50, height=28
                     ).pack(side="left", padx=(4, 16))

        ctk.CTkLabel(params_row, text="Guidance:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._diff_guidance_var = ctk.StringVar(
            value=str(diff_settings.get("default_guidance", 7.5)))
        ctk.CTkEntry(params_row, textvariable=self._diff_guidance_var,
                     font=FONT_SM, fg_color=GLASS_BG, border_color=GLASS_BORDER,
                     text_color=TEXT, width=50, height=28
                     ).pack(side="left", padx=(4, 0))

        # ── Upscale section ───────────────────────────────────────────
        self._section_header(panel, "Upscale Pipeline (SD 1.5)")
        up_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        up_frame.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(up_frame,
                     text="Apply after base 512x512 generation to increase resolution.",
                     font=("RuneScape Plain 11", 9), text_color=DIM
                     ).pack(padx=12, pady=(10, 6), anchor="w")

        # Upscale method
        method_row = ctk.CTkFrame(up_frame, fg_color="transparent")
        method_row.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkLabel(method_row, text="Method:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._upscale_var = ctk.StringVar(
            value=diff_settings.get("default_upscale", "none"))
        ctk.CTkSegmentedButton(
            method_row, values=["none", "refine", "x4"],
            variable=self._upscale_var, font=FONT_SM,
            selected_color=ACCENT, selected_hover_color=ACCENT_H,
        ).pack(side="left", padx=(8, 0))

        # Method descriptions
        desc_frame = ctk.CTkFrame(up_frame, fg_color=GLASS_BG, corner_radius=4)
        desc_frame.pack(fill="x", padx=12, pady=(4, 8))
        ctk.CTkLabel(desc_frame,
                     text="none     — output at base resolution (512x512)\n"
                          "refine   — img2img re-pass at higher res (~30s/pass, same model)\n"
                          "x4       — SD upscaler 512→2048 (~90s, ~3 GB extra download)",
                     font=("Consolas", 9), text_color=DIM, justify="left"
                     ).pack(padx=10, pady=8, anchor="w")

        # Refine params
        refine_row = ctk.CTkFrame(up_frame, fg_color="transparent")
        refine_row.pack(fill="x", padx=12, pady=(0, 4))

        ctk.CTkLabel(refine_row, text="Passes:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._upscale_passes_var = ctk.StringVar(
            value=str(diff_settings.get("default_upscale_passes", 1)))
        ctk.CTkEntry(refine_row, textvariable=self._upscale_passes_var,
                     font=FONT_SM, fg_color=GLASS_BG, border_color=GLASS_BORDER,
                     text_color=TEXT, width=40, height=28
                     ).pack(side="left", padx=(4, 14))

        ctk.CTkLabel(refine_row, text="Scale:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._upscale_factor_var = ctk.StringVar(
            value=str(diff_settings.get("default_upscale_factor", 1.5)))
        ctk.CTkEntry(refine_row, textvariable=self._upscale_factor_var,
                     font=FONT_SM, fg_color=GLASS_BG, border_color=GLASS_BORDER,
                     text_color=TEXT, width=50, height=28
                     ).pack(side="left", padx=(4, 14))

        ctk.CTkLabel(refine_row, text="Strength:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._upscale_strength_var = ctk.StringVar(
            value=str(diff_settings.get("default_upscale_strength", 0.35)))
        ctk.CTkEntry(refine_row, textvariable=self._upscale_strength_var,
                     font=FONT_SM, fg_color=GLASS_BG, border_color=GLASS_BORDER,
                     text_color=TEXT, width=50, height=28
                     ).pack(side="left", padx=(4, 0))

        # Pipeline preview
        preview_frame = ctk.CTkFrame(up_frame, fg_color=GLASS_BG, corner_radius=4)
        preview_frame.pack(fill="x", padx=12, pady=(4, 10))
        self._pipeline_preview = ctk.CTkLabel(
            preview_frame, text="", font=("Consolas", 9), text_color=GOLD, anchor="w")
        self._pipeline_preview.pack(padx=10, pady=6, anchor="w")
        self._update_pipeline_preview()

        # Bind updates to preview
        self._upscale_var.trace_add("write", lambda *_: self._update_pipeline_preview())
        self._upscale_passes_var.trace_add("write", lambda *_: self._update_pipeline_preview())
        self._upscale_factor_var.trace_add("write", lambda *_: self._update_pipeline_preview())

        # Save button
        save_row = ctk.CTkFrame(diff_frame, fg_color="transparent")
        save_row.pack(fill="x", padx=12, pady=(12, 12))
        ctk.CTkButton(save_row, text="Save Diffusion Settings", font=FONT_SM,
                      width=160, height=30, fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self._save_diffusion).pack(side="right")
        self._diff_status = ctk.CTkLabel(save_row, text="", font=("RuneScape Plain 11", 9),
                                         text_color=DIM)
        self._diff_status.pack(side="left", padx=8)

        # First-run notice
        notice = ctk.CTkFrame(panel, fg_color="#1a1a10", corner_radius=6)
        notice.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkLabel(notice,
                     text="Models download from HuggingFace on first use (~5 GB for SD1.5, ~7 GB for SDXL, ~3 GB x4 upscaler).\n"
                          "Requires: pip install diffusers transformers accelerate torch",
                     font=("RuneScape Plain 11", 9), text_color=ORANGE, justify="left"
                     ).pack(padx=12, pady=10, anchor="w")

    def _update_pipeline_preview(self):
        method = self._upscale_var.get()
        if method == "none":
            text = "512x512  (~30s)"
        elif method == "refine":
            try:
                passes = int(self._upscale_passes_var.get())
            except ValueError:
                passes = 1
            try:
                factor = float(self._upscale_factor_var.get())
            except ValueError:
                factor = 1.5
            w, h = 512, 512
            stages = ["512x512"]
            time_est = 30
            for _ in range(passes):
                w = (int(w * factor) // 8) * 8
                h = (int(h * factor) // 8) * 8
                stages.append(f"{w}x{h}")
                time_est += 30
            text = " → ".join(stages) + f"  (~{time_est}s)"
        elif method == "x4":
            text = "512x512 → 2048x2048  (~2 min)"
        else:
            text = ""
        if hasattr(self, "_pipeline_preview"):
            self._pipeline_preview.configure(text=f"Pipeline: {text}")

    def _save_diffusion(self):
        L = _launcher()
        try:
            steps = int(self._diff_steps_var.get())
        except ValueError:
            steps = 30
        try:
            guidance = float(self._diff_guidance_var.get())
        except ValueError:
            guidance = 7.5

        try:
            upscale_passes = int(self._upscale_passes_var.get())
        except ValueError:
            upscale_passes = 1
        try:
            upscale_factor = float(self._upscale_factor_var.get())
        except ValueError:
            upscale_factor = 1.5
        try:
            upscale_strength = float(self._upscale_strength_var.get())
        except ValueError:
            upscale_strength = 0.35

        data = L._load_settings()
        data["diffusion"] = {
            "sd15_enabled": self._sd15_var.get(),
            "sdxl_enabled": self._sdxl_var.get(),
            "default_model": self._diff_default_var.get(),
            "default_steps": steps,
            "default_guidance": guidance,
            "default_upscale": self._upscale_var.get(),
            "default_upscale_passes": upscale_passes,
            "default_upscale_factor": upscale_factor,
            "default_upscale_strength": upscale_strength,
        }
        L._save_settings(data)
        self._diff_status.configure(text="Saved.", text_color=GREEN)
