"""BigEd CC — GPU Power & Thermal monitor dialog.

Extracted from launcher.py (TECH_DEBT 4.2).
"""
import subprocess
import threading

import customtkinter as ctk

from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    GREEN, ORANGE, RED, FONT_SM,
)

# Late-bound: set by launcher.py after path resolution
HERE = None
_GPU_OK = False
_GPU_HANDLE = None
pynvml = None


def _init_gpu_refs(here, gpu_ok, gpu_handle, _pynvml):
    """Called once from launcher.py to inject GPU state without circular imports."""
    global HERE, _GPU_OK, _GPU_HANDLE, pynvml
    HERE = here
    _GPU_OK = gpu_ok
    _GPU_HANDLE = gpu_handle
    pynvml = _pynvml


class ThermalDialog(ctk.CTkToplevel):
    """
    GPU power-limit and thermal monitor.
    Lower the power limit to reduce heat, noise, and long-term wear.
    Applies via pynvml (no UAC) when the process has the right privileges;
    falls back to nvidia-smi via a UAC-elevated PowerShell call.
    Works on any NVIDIA GPU that exposes power management through NVML.
    """
    REFRESH_MS = 2000

    def __init__(self, parent):
        super().__init__(parent)
        self.title("BigEd CC — GPU Power & Thermal")
        self.geometry("580x480")
        self.configure(fg_color=BG)
        self.grab_set()

        if HERE:
            ico = HERE / "brick.ico"
            if ico.exists():
                try: self.iconbitmap(str(ico))
                except Exception: pass

        self._alive        = True
        self._default_w    = 0.0    # default TDP watts
        self._min_w        = 0.0
        self._max_w        = 0.0
        self._current_w    = 0.0
        self._slider_var   = ctk.DoubleVar(value=0)
        self._slider_ready = False

        self._build_ui()
        threading.Thread(target=self._load_limits, daemon=True).start()
        self._schedule_live()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self._alive = False
        self.destroy()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color=BG3, height=52, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="⚡  GPU POWER & THERMAL",
                     font=("Segoe UI", 13, "bold"), text_color=GOLD
                     ).grid(row=0, column=0, padx=14, pady=10, sticky="w")
        self._gpu_name_lbl = ctk.CTkLabel(
            hdr, text="Detecting GPU...", font=("Segoe UI", 9), text_color=DIM)
        self._gpu_name_lbl.grid(row=0, column=1, padx=8, sticky="w")

        # Live stats bar
        stats = ctk.CTkFrame(self, fg_color="#111111", height=28, corner_radius=0)
        stats.grid(row=1, column=0, sticky="ew")
        stats.grid_propagate(False)
        stats.grid_columnconfigure((0, 1, 2, 3), weight=1)
        kw = dict(font=("Consolas", 10), text_color=DIM, anchor="center")
        self._lv_temp  = ctk.CTkLabel(stats, text="Temp —", **kw)
        self._lv_power = ctk.CTkLabel(stats, text="Power —", **kw)
        self._lv_util  = ctk.CTkLabel(stats, text="GPU Util —", **kw)
        self._lv_vram  = ctk.CTkLabel(stats, text="VRAM —", **kw)
        self._lv_temp.grid( row=0, column=0, padx=6, pady=4)
        self._lv_power.grid(row=0, column=1, padx=6, pady=4)
        self._lv_util.grid( row=0, column=2, padx=6, pady=4)
        self._lv_vram.grid( row=0, column=3, padx=6, pady=4)

        # Main control area
        ctrl = ctk.CTkFrame(self, fg_color=BG2, corner_radius=0)
        ctrl.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)
        ctrl.grid_columnconfigure(0, weight=1)

        # Power limit section
        pl_frame = ctk.CTkFrame(ctrl, fg_color=BG3, corner_radius=6)
        pl_frame.grid(row=0, column=0, padx=16, pady=(16, 8), sticky="ew")
        pl_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(pl_frame, text="Power Limit",
                     font=("Segoe UI", 11, "bold"), text_color=GOLD,
                     anchor="w").grid(row=0, column=0, padx=12, pady=(10, 2), sticky="w")
        ctk.CTkLabel(pl_frame,
                     text="Lowering power reduces heat and extends GPU lifespan.\n"
                          "The GPU throttles gracefully — no stability issues.",
                     font=("Segoe UI", 9), text_color=DIM,
                     anchor="w", justify="left"
                     ).grid(row=1, column=0, padx=12, pady=(0, 8), sticky="w")

        slider_row = ctk.CTkFrame(pl_frame, fg_color="transparent")
        slider_row.grid(row=2, column=0, padx=12, pady=(0, 4), sticky="ew")
        slider_row.grid_columnconfigure(0, weight=1)

        self._slider = ctk.CTkSlider(
            slider_row, variable=self._slider_var,
            from_=0, to=100,
            height=18, corner_radius=4,
            fg_color=BG, progress_color=ACCENT, button_color=GOLD,
            button_hover_color="#e0c060",
            command=self._on_slider,
            state="disabled",
        )
        self._slider.grid(row=0, column=0, sticky="ew", pady=4)

        self._slider_lbl = ctk.CTkLabel(
            slider_row, text="— W  (—%)",
            font=("Consolas", 12, "bold"), text_color=TEXT, width=120, anchor="e")
        self._slider_lbl.grid(row=0, column=1, padx=(10, 0))

        # Range labels
        range_row = ctk.CTkFrame(pl_frame, fg_color="transparent")
        range_row.grid(row=3, column=0, padx=12, pady=(0, 6), sticky="ew")
        range_row.grid_columnconfigure(1, weight=1)
        self._min_lbl = ctk.CTkLabel(range_row, text="min", font=("Segoe UI", 8),
                                     text_color=DIM, anchor="w")
        self._min_lbl.grid(row=0, column=0, sticky="w")
        self._max_lbl = ctk.CTkLabel(range_row, text="max", font=("Segoe UI", 8),
                                     text_color=DIM, anchor="e")
        self._max_lbl.grid(row=0, column=2, sticky="e")
        self._default_lbl = ctk.CTkLabel(range_row, text="", font=("Segoe UI", 8),
                                         text_color=DIM, anchor="center")
        self._default_lbl.grid(row=0, column=1)

        # Presets
        preset_frame = ctk.CTkFrame(ctrl, fg_color="transparent")
        preset_frame.grid(row=1, column=0, padx=16, pady=4, sticky="w")
        ctk.CTkLabel(preset_frame, text="Presets:", font=FONT_SM,
                     text_color=DIM).grid(row=0, column=0, padx=(0, 8))
        for col, (label, pct) in enumerate([
            ("Eco  50%", 50), ("Balanced  75%", 75),
            ("Stock  100%", 100), ("Max TDP", 110),
        ]):
            ctk.CTkButton(
                preset_frame, text=label, font=("Segoe UI", 10),
                width=0, height=28, fg_color=BG3, hover_color=BG,
                command=lambda p=pct: self._set_pct(p),
            ).grid(row=0, column=col + 1, padx=4)

        # Status and apply
        bottom = ctk.CTkFrame(self, fg_color=BG3, height=52, corner_radius=0)
        bottom.grid(row=3, column=0, sticky="ew")
        bottom.grid_propagate(False)
        bottom.grid_columnconfigure(1, weight=1)

        self._apply_btn = ctk.CTkButton(
            bottom, text="⚡ Apply Limit", width=120, height=34,
            fg_color=ACCENT, hover_color=ACCENT_H,
            font=("Segoe UI", 11, "bold"),
            state="disabled",
            command=self._apply)
        self._apply_btn.grid(row=0, column=0, padx=(12, 8), pady=9)

        ctk.CTkButton(
            bottom, text="↺ Restore Default", width=130, height=34,
            fg_color=BG2, hover_color=BG,
            command=self._restore_default
        ).grid(row=0, column=1, padx=4, pady=9, sticky="w")

        self._status_lbl = ctk.CTkLabel(
            bottom, text="", font=FONT_SM, text_color=DIM)
        self._status_lbl.grid(row=0, column=2, padx=12, sticky="e")

    # ── Live stats ────────────────────────────────────────────────────────────
    def _schedule_live(self):
        if not self._alive:
            return
        threading.Thread(target=self._sample_live, daemon=True).start()
        self.after(self.REFRESH_MS, self._schedule_live)

    def _sample_live(self):
        if not _GPU_OK:
            return
        try:
            temp  = pynvml.nvmlDeviceGetTemperature(_GPU_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
            power = pynvml.nvmlDeviceGetPowerUsage(_GPU_HANDLE) / 1000
            util  = pynvml.nvmlDeviceGetUtilizationRates(_GPU_HANDLE)
            mem   = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
            self.after(0, lambda: self._apply_live(temp, power, util.gpu, mem))
        except Exception:
            pass

    def _apply_live(self, temp, power, util_pct, mem):
        def temp_color(t):
            return RED if t >= 85 else ORANGE if t >= 70 else GREEN
        def pwr_color(w):
            if self._max_w > 0:
                p = w / self._max_w
                return RED if p >= 0.95 else ORANGE if p >= 0.75 else GREEN
            return DIM
        self._lv_temp.configure( text=f"Temp  {temp}°C",    text_color=temp_color(temp))
        self._lv_power.configure(text=f"Power  {power:.0f}W", text_color=pwr_color(power))
        self._lv_util.configure( text=f"Util  {util_pct}%",  text_color=GREEN if util_pct > 0 else DIM)
        self._lv_vram.configure( text=f"VRAM  {mem.used/1e9:.1f}/{mem.total/1e9:.1f}GB", text_color=DIM)

    # ── Load power limits ─────────────────────────────────────────────────────
    def _load_limits(self):
        if not _GPU_OK:
            self.after(0, lambda: self._status_lbl.configure(
                text="pynvml not available — install nvidia-ml-py", text_color=RED))
            return
        try:
            name = pynvml.nvmlDeviceGetName(_GPU_HANDLE)
            lo, hi = pynvml.nvmlDeviceGetPowerManagementLimitConstraints(_GPU_HANDLE)
            default = pynvml.nvmlDeviceGetPowerManagementDefaultLimit(_GPU_HANDLE)
            current = pynvml.nvmlDeviceGetPowerManagementLimit(_GPU_HANDLE)
            self._min_w     = lo / 1000
            self._max_w     = hi / 1000
            self._default_w = default / 1000
            self._current_w = current / 1000
            self.after(0, lambda: self._init_slider(name))
        except Exception as e:
            self.after(0, lambda: self._status_lbl.configure(
                text=f"NVML error: {e}", text_color=RED))

    def _init_slider(self, gpu_name: str):
        self._gpu_name_lbl.configure(text=gpu_name)
        self._slider.configure(
            from_=self._min_w, to=self._max_w, state="normal")
        self._slider_var.set(self._current_w)
        self._slider_ready = True
        self._update_slider_label(self._current_w)
        self._min_lbl.configure(text=f"min  {self._min_w:.0f}W")
        self._max_lbl.configure(text=f"max  {self._max_w:.0f}W")
        self._default_lbl.configure(text=f"default  {self._default_w:.0f}W")
        self._apply_btn.configure(state="normal")

    # ── Slider / presets ──────────────────────────────────────────────────────
    def _on_slider(self, value):
        if self._slider_ready:
            self._update_slider_label(value)

    def _update_slider_label(self, watts: float):
        pct = (watts / self._default_w * 100) if self._default_w else 0
        color = RED if pct >= 100 else ORANGE if pct >= 75 else GREEN
        self._slider_lbl.configure(
            text=f"{watts:.0f} W  ({pct:.0f}%)", text_color=color)

    def _set_pct(self, pct: int):
        if not self._slider_ready:
            return
        watts = max(self._min_w, min(self._max_w, self._default_w * pct / 100))
        self._slider_var.set(watts)
        self._update_slider_label(watts)

    def _restore_default(self):
        if not self._slider_ready:
            return
        self._set_pct(100)
        self._apply()

    # ── Apply ─────────────────────────────────────────────────────────────────
    def _apply(self):
        watts = self._slider_var.get()
        milliwatts = int(watts * 1000)
        self._status_lbl.configure(text=f"Applying {watts:.0f}W...", text_color=ORANGE)
        threading.Thread(target=self._do_apply, args=(milliwatts,), daemon=True).start()

    def _do_apply(self, milliwatts: int):
        watts = milliwatts / 1000
        # Try pynvml directly first (works if FMA is running as admin)
        if _GPU_OK:
            try:
                pynvml.nvmlDeviceSetPowerManagementLimit(_GPU_HANDLE, milliwatts)
                self._current_w = watts
                self.after(0, lambda: self._status_lbl.configure(
                    text=f"✓ Power limit set to {watts:.0f}W", text_color=GREEN))
                return
            except Exception:
                pass  # fall through to nvidia-smi elevation

        # Fall back: nvidia-smi via UAC-elevated PowerShell
        ps_cmd = (
            f"Start-Process 'nvidia-smi' '-pl {watts:.0f}' "
            f"-Verb RunAs -Wait -WindowStyle Hidden"
        )
        try:
            r = subprocess.run(
                ["powershell", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=30,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if r.returncode == 0:
                self._current_w = watts
                self.after(0, lambda: self._status_lbl.configure(
                    text=f"✓ Power limit set to {watts:.0f}W (via nvidia-smi)", text_color=GREEN))
            else:
                err = (r.stderr or r.stdout or "unknown error")[:120]
                self.after(0, lambda: self._status_lbl.configure(
                    text=f"✗ {err}", text_color=RED))
        except Exception as e:
            err_msg = f"✗ {e}"
            self.after(0, lambda m=err_msg: self._status_lbl.configure(
                text=m, text_color=RED))
