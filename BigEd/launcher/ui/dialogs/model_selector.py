"""BigEd CC — LLM Model Selector dialog.

Extracted from launcher.py (TECH_DEBT 4.2).
"""
import sys

import customtkinter as ctk
import tomlkit

from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    GREEN, ORANGE, RED, FONT_SM,
)

# Late-bound: set by launcher.py after path resolution
HERE = None
FLEET_DIR = None
FLEET_TOML = None
_GPU_OK = False
_GPU_HANDLE = None
pynvml = None
load_model_cfg = None  # function reference


def _init_model_selector_refs(here, fleet_dir, fleet_toml, gpu_ok, gpu_handle,
                               _pynvml, _load_model_cfg):
    """Called once from launcher.py to inject refs without circular imports."""
    global HERE, FLEET_DIR, FLEET_TOML, _GPU_OK, _GPU_HANDLE, pynvml, load_model_cfg
    HERE = here
    FLEET_DIR = fleet_dir
    FLEET_TOML = fleet_toml
    _GPU_OK = gpu_ok
    _GPU_HANDLE = gpu_handle
    pynvml = _pynvml
    load_model_cfg = _load_model_cfg


# (model_id, display_name, vram_gb, description)
OLLAMA_MODELS = [
    ("qwen3:0.6b",       "Qwen3 0.6B",       0.5,  "Ultra-fast, minimal tasks"),
    ("qwen3:1.7b",       "Qwen3 1.7B",       1.0,  "Fast small tasks"),
    ("qwen3:4b",         "Qwen3 4B",         2.5,  "Balanced performance"),
    ("qwen3:8b",         "Qwen3 8B",         5.0,  "Strong reasoning  — fleet default"),
    ("qwen3:14b",        "Qwen3 14B",        9.0,  "Near VRAM ceiling"),
    ("qwen3:30b",        "Qwen3 30B",       19.0,  "Requires 24GB+ VRAM"),
    ("llama3.1:8b",      "Llama 3.1 8B",     5.0,  "Meta flagship 8B"),
    ("llama3.1:70b",     "Llama 3.1 70B",   42.0,  "Requires 48GB+ VRAM"),
    ("mistral:7b",       "Mistral 7B",       4.5,  "Fast instruction model"),
    ("deepseek-r1:7b",   "DeepSeek-R1 7B",   4.5,  "Chain-of-thought reasoning"),
    ("deepseek-r1:14b",  "DeepSeek-R1 14B",  9.0,  "Near VRAM ceiling"),
    ("deepseek-r1:32b",  "DeepSeek-R1 32B", 19.0,  "Requires 24GB+ VRAM"),
    ("phi4:14b",         "Phi-4 14B",        9.0,  "Microsoft 14B"),
    ("gemma3:4b",        "Gemma 3 4B",       3.0,  "Google 4B"),
    ("gemma3:12b",       "Gemma 3 12B",      8.0,  "Google 12B"),
    ("gemma3:27b",       "Gemma 3 27B",     17.0,  "Requires 20GB+ VRAM"),
    ("codellama:7b",     "CodeLlama 7B",     4.5,  "Code-specialized"),
    ("codellama:13b",    "CodeLlama 13B",    8.0,  "Code-specialized 13B"),
]


class ModelSelectorDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("BigEd CC — LLM Model Selector")
        self.geometry("700x520")
        self.configure(fg_color=BG)
        self.grab_set()

        if HERE:
            ico = HERE / "brick.ico"
            if ico.exists():
                try: self.iconbitmap(str(ico))
                except Exception: pass

        self._hw         = self._detect_hardware()
        self._vram_total = self._hw["vram_gb"]
        # CPU-only: no GPU VRAM detected; allow small models (≤4B) to remain selectable
        self._vram_safe  = self._vram_total * 0.85 if self._vram_total > 0 else 4.0
        self._vendor     = self._hw["vendor"]
        self._is_apu     = self._hw["is_apu"]
        self._current    = self._read_current_model()
        self._selected   = self._current
        self._row_frames = {}
        mcfg = load_model_cfg()
        self._stack_var  = ctk.StringVar(value=mcfg.get("complex_provider", "claude"))
        self._build_ui()

    def _detect_hardware(self) -> dict:
        """Detect GPU vendor, VRAM, and type via fleet/gpu.py backends."""
        try:
            fleet_str = str(FLEET_DIR)
            if fleet_str not in sys.path:
                sys.path.insert(0, fleet_str)
            from gpu import detect_gpu, NullBackend, NvidiaBackend, AmdBackend, SysfsBackend  # noqa: PLC0415
            backend, has_gpu = detect_gpu()
            if not has_gpu:
                return {"vram_gb": 0.0, "name": "CPU only (no GPU)", "vendor": "cpu", "is_apu": False}
            mem     = backend.get_memory_info()
            vram_gb = (mem.total_bytes / 1e9) if mem else 0.0
            name    = backend.get_name()
            if isinstance(backend, NvidiaBackend):
                vendor, is_apu = "nvidia", False
            elif isinstance(backend, AmdBackend):
                vendor, is_apu = "amd", False
            elif isinstance(backend, SysfsBackend):
                vendor = "amd_sysfs"
                is_apu = vram_gb < 4.0
            else:
                vendor, is_apu = "unknown", False
            return {"vram_gb": vram_gb, "name": name, "vendor": vendor, "is_apu": is_apu}
        except Exception:
            pass
        # Final fallback — pynvml direct
        if _GPU_OK:
            try:
                mem  = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
                raw  = pynvml.nvmlDeviceGetName(_GPU_HANDLE)
                name = raw if isinstance(raw, str) else raw.decode()
                return {"vram_gb": mem.total / 1e9, "name": name, "vendor": "nvidia", "is_apu": False}
            except Exception:
                pass
        return {"vram_gb": 12.0, "name": "Unknown GPU", "vendor": "unknown", "is_apu": False}

    def _read_current_model(self) -> str:
        return load_model_cfg().get("local", "qwen3:8b")

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color=BG3, height=52, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="🧠  LLM MODEL SELECTOR",
                     font=("Segoe UI", 13, "bold"), text_color=GOLD
                     ).grid(row=0, column=0, padx=14, pady=10, sticky="w")
        gpu_label = self._hw.get("name", "Unknown GPU")
        vram_str = (f"{gpu_label}  |  "
                    f"{self._vram_total:.1f} GB total  |  "
                    f"safe limit: {self._vram_safe:.1f} GB  |  "
                    f"grayed = won't fit")
        ctk.CTkLabel(hdr, text=vram_str,
                     font=("Segoe UI", 9), text_color=DIM
                     ).grid(row=0, column=1, padx=8, sticky="w")

        # Vendor-specific advisory
        if self._is_apu:
            warn_text = ("⚠  Integrated / APU GPU (Steam Deck or similar) — "
                         "VRAM is shared system RAM. Keep models ≤ 4B for stable performance.")
        elif self._vendor == "amd":
            warn_text = ("⚠  AMD GPU — Ollama needs ROCm for GPU acceleration. "
                         "Without ROCm, models run CPU-only regardless of VRAM.")
        elif self._vendor == "cpu":
            warn_text = ("⚠  No GPU detected — CPU-only mode. "
                         "Stick to 4B models or smaller for usable speed.")
        else:
            warn_text = None

        if warn_text:
            hdr.configure(height=72)
            ctk.CTkLabel(hdr, text=warn_text,
                         font=("Segoe UI", 8), text_color=ORANGE,
                         anchor="w"
                         ).grid(row=1, column=0, columnspan=2, padx=14, pady=(0, 6), sticky="w")

        # Stack Mode bar
        stack_bar = ctk.CTkFrame(self, fg_color=BG2, height=48, corner_radius=0)
        stack_bar.grid(row=1, column=0, sticky="ew")
        stack_bar.grid_propagate(False)
        stack_bar.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(stack_bar, text="Stack Mode:", font=("Segoe UI", 10, "bold"),
                     text_color=DIM).grid(row=0, column=0, padx=(14, 8), pady=12)
        ctk.CTkSegmentedButton(
            stack_bar,
            values=["local", "claude", "gemini"],
            variable=self._stack_var,
            font=FONT_SM,
            selected_color=ACCENT, selected_hover_color=ACCENT_H,
        ).grid(row=0, column=1, padx=4, pady=8)
        descriptions = {
            "local":  "Full local — Ollama for all tasks",
            "claude": "Local + Claude Sonnet for complex tasks",
            "gemini": "Local + Gemini Flash for complex tasks (free tier)",
        }
        self._stack_desc = ctk.CTkLabel(
            stack_bar, text=descriptions.get(self._stack_var.get(), ""),
            font=("Segoe UI", 9), text_color=DIM)
        self._stack_desc.grid(row=0, column=2, padx=12, sticky="w")
        self._stack_var.trace_add("write", lambda *_: self._stack_desc.configure(
            text=descriptions.get(self._stack_var.get(), "")))

        # Model list
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG2, corner_radius=0)
        scroll.grid(row=2, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        # Column headers
        hrow = ctk.CTkFrame(scroll, fg_color=BG3, corner_radius=4)
        hrow.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        hrow.grid_columnconfigure(1, weight=1)
        hrow.grid_columnconfigure(4, weight=1)
        for col, (txt, w) in enumerate([
            ("",         18), ("Model ID", 0), ("VRAM",  68),
            ("Status",  110), ("Description", 0)
        ]):
            ctk.CTkLabel(hrow, text=txt, font=("Segoe UI", 9, "bold"),
                         text_color=DIM, width=w, anchor="w"
                         ).grid(row=0, column=col, padx=6, pady=4, sticky="w")

        for i, (model_id, _name, vram, desc) in enumerate(OLLAMA_MODELS):
            fits        = vram <= self._vram_safe
            borderline  = fits and vram > self._vram_safe * 0.75
            is_current  = model_id == self._current

            if fits:
                dot_color  = ORANGE if borderline else GREEN
                status_txt = f"fits ({vram:.1f} GB)"
                status_col = ORANGE if borderline else GREEN
                row_fg     = "#1e2e1e" if is_current else (BG if i % 2 else "#1e1e1e")
                text_col   = GREEN if is_current else TEXT
                desc_col   = DIM
            else:
                dot_color  = "#444444"
                status_txt = f"need {vram:.0f} GB"
                status_col = "#555555"
                row_fg     = "#141414" if i % 2 else "#121212"
                text_col   = "#555555"
                desc_col   = "#444444"

            row = ctk.CTkFrame(scroll, fg_color=row_fg, corner_radius=3)
            row.grid(row=i + 1, column=0, sticky="ew", padx=8, pady=1)
            row.grid_columnconfigure(1, weight=1)
            row.grid_columnconfigure(4, weight=1)

            ctk.CTkLabel(row, text="●", font=("Consolas", 12),
                         text_color=dot_color, width=18
                         ).grid(row=0, column=0, padx=(8, 2), pady=8)

            label_txt = model_id + ("  ◀ current" if is_current else "")
            name_lbl = ctk.CTkLabel(row, text=label_txt, font=("Consolas", 10),
                                    text_color=text_col, anchor="w")
            name_lbl.grid(row=0, column=1, padx=4, sticky="w")

            ctk.CTkLabel(row, text=f"{vram:.1f} GB", font=("Consolas", 10),
                         text_color=text_col, width=65, anchor="w"
                         ).grid(row=0, column=2, padx=4)
            ctk.CTkLabel(row, text=status_txt, font=("Segoe UI", 9),
                         text_color=status_col, width=105, anchor="w"
                         ).grid(row=0, column=3, padx=4)
            ctk.CTkLabel(row, text=desc, font=("Segoe UI", 9),
                         text_color=desc_col, anchor="w"
                         ).grid(row=0, column=4, padx=(4, 8), sticky="w")

            if fits:
                for widget in [row] + row.winfo_children():
                    widget.bind("<Button-1>",
                                lambda e, m=model_id, r=row, idx=i: self._select(m, r, idx))

            self._row_frames[model_id] = (row, i)

        # Bottom bar
        bar = ctk.CTkFrame(self, fg_color=BG3, height=50, corner_radius=0)
        bar.grid(row=3, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(2, weight=1)

        self._sel_lbl = ctk.CTkLabel(
            bar, text=f"Selected: {self._selected}",
            font=("Consolas", 10), text_color=GOLD)
        self._sel_lbl.grid(row=0, column=0, padx=14, pady=12, sticky="w")

        self._status_lbl = ctk.CTkLabel(bar, text="", font=FONT_SM, text_color=DIM)
        self._status_lbl.grid(row=0, column=2, padx=8)

        self._apply_btn = ctk.CTkButton(
            bar, text="✓ Apply", width=100, height=32,
            fg_color=ACCENT, hover_color=ACCENT_H,
            font=("Segoe UI", 11, "bold"),
            command=self._apply)
        self._apply_btn.grid(row=0, column=3, padx=(8, 6), pady=9)

        ctk.CTkButton(bar, text="Cancel", width=80, height=32,
                      fg_color=BG2, hover_color=BG,
                      command=self.destroy
                      ).grid(row=0, column=4, padx=(0, 12), pady=9)

    def _select(self, model_id: str, row_frame, idx: int):
        # Restore previous selection
        if self._selected and self._selected in self._row_frames:
            prev_frame, prev_idx = self._row_frames[self._selected]
            if self._selected == self._current:
                prev_frame.configure(fg_color="#1e2e1e")
            else:
                prev_frame.configure(fg_color=BG if prev_idx % 2 else "#1e1e1e")
        self._selected = model_id
        row_frame.configure(fg_color="#1e1e2e")
        self._sel_lbl.configure(text=f"Selected: {model_id}")

    def _apply(self):
        if not self._selected:
            return
        try:
            doc = tomlkit.parse(FLEET_TOML.read_text(encoding="utf-8"))
            provider = self._stack_var.get()
            complex_models = {
                "claude": "claude-sonnet-4-6",
                "gemini": "gemini-2.0-flash",
                "local":  self._selected,
            }
            complex_val = complex_models.get(provider, "claude-sonnet-4-6")
            models = doc.setdefault("models", {})
            models["local"] = self._selected
            models["complex"] = complex_val
            models["complex_provider"] = provider
            FLEET_TOML.write_text(tomlkit.dumps(doc), encoding="utf-8")
            self._status_lbl.configure(
                text="✓ Saved — restart fleet to apply", text_color=GREEN)
            self._current = self._selected
            self._apply_btn.configure(state="disabled")
        except Exception as e:
            self._status_lbl.configure(text=f"✗ {e}", text_color=RED)
