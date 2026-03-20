"""Hardware settings panel — system metric cards, GPU power."""
import subprocess
import threading

import customtkinter as ctk
import psutil

from ui.theme import (
    BG2, BG3, TEXT, DIM,
    GREEN, ORANGE, RED, FONT_SM,
    GLASS_BG, GLASS_PANEL,
)


def _launcher():
    import launcher as _mod
    return _mod


class HardwarePanelMixin:
    """Mixin providing the Hardware settings panel."""

    def _build_hardware_panel(self):
        L = _launcher()
        panel = ctk.CTkScrollableFrame(self._content, fg_color=GLASS_PANEL)
        self._panels["hardware"] = panel

        # ── GPU Power & Thermal ────────────────────────────────────────
        self._section_header(panel, "GPU Power & Thermal")
        gpu_ctrl = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        gpu_ctrl.pack(fill="x", padx=16, pady=(0, 12))
        gpu_ctrl.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(gpu_ctrl, text="Control GPU power limits and monitor thermals.",
                     font=("Segoe UI", 9), text_color=DIM
                     ).grid(row=0, column=0, columnspan=2, padx=12, pady=(10, 4), sticky="w")
        ctk.CTkButton(gpu_ctrl, text="Open GPU Power Manager", font=FONT_SM,
                      width=180, height=28, fg_color=BG3, hover_color=BG2,
                      command=lambda: L.ThermalDialog(self._parent)
                      ).grid(row=1, column=0, padx=12, pady=(0, 10), sticky="w")

        # ── System Overview (metric cards) ─────────────────────────────
        self._section_header(panel, "System Overview")
        self._hw_cards_frame = ctk.CTkFrame(panel, fg_color="transparent")
        self._hw_cards_frame.pack(fill="x", padx=16, pady=(0, 12))
        self._hw_cards_frame.grid_columnconfigure((0, 1, 2), weight=1)

        # Placeholder cards — populated by _load_hw_info
        self._hw_card_cpu = self._hw_metric_card(self._hw_cards_frame, 0, 0,
            "CPU", "—", "—", DIM)
        self._hw_card_ram = self._hw_metric_card(self._hw_cards_frame, 0, 1,
            "RAM", "—", "—", DIM)
        self._hw_card_gpu = self._hw_metric_card(self._hw_cards_frame, 0, 2,
            "GPU", "—", "—", DIM)

        # ── Detailed Metrics ───────────────────────────────────────────
        self._section_header(panel, "Details")
        details_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        details_frame.pack(fill="x", padx=16, pady=(0, 12))
        details_frame.grid_columnconfigure(1, weight=1)

        self._hw_detail_rows = {}
        detail_items = [
            ("cpu_name", "CPU Model"),
            ("cpu_cores", "Cores"),
            ("cpu_freq", "Frequency"),
            ("ram_total", "RAM Total"),
            ("ram_used", "RAM Used"),
            ("ram_avail", "RAM Available"),
            ("gpu_name", "GPU Model"),
            ("vram_total", "VRAM Total"),
            ("vram_used", "VRAM Used"),
            ("gpu_temp", "GPU Temp"),
            ("gpu_power", "GPU Power"),
            ("gpu_fan", "GPU Fan"),
        ]
        for i, (key, label) in enumerate(detail_items):
            ctk.CTkLabel(details_frame, text=label, font=FONT_SM,
                         text_color=DIM, anchor="w", width=100
                         ).grid(row=i, column=0, padx=(12, 8), pady=3, sticky="w")
            val_lbl = ctk.CTkLabel(details_frame, text="—", font=("Consolas", 10),
                                   text_color=TEXT, anchor="w")
            val_lbl.grid(row=i, column=1, padx=(0, 12), pady=3, sticky="w")
            self._hw_detail_rows[key] = val_lbl

        # Bottom padding
        ctk.CTkFrame(details_frame, fg_color="transparent", height=8).grid(
            row=len(detail_items), column=0, columnspan=2)

        # Refresh button
        bar = ctk.CTkFrame(panel, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkButton(bar, text="↻ Refresh", font=FONT_SM, width=90, height=28,
                      fg_color=BG3, hover_color=BG2,
                      command=lambda: threading.Thread(
                          target=self._load_hw_info, daemon=True).start()
                      ).pack(side="left")

        threading.Thread(target=self._load_hw_info, daemon=True).start()

    def _hw_metric_card(self, parent, row, col, title, value, subtitle, color):
        """Create a metric card widget. Returns dict of labels for updating."""
        card = ctk.CTkFrame(parent, fg_color=GLASS_BG, corner_radius=8, height=90)
        card.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")
        card.grid_propagate(False)

        ctk.CTkLabel(card, text=title.upper(), font=("Consolas", 8),
                     text_color=DIM).place(x=12, y=8)
        val_lbl = ctk.CTkLabel(card, text=value, font=("Segoe UI", 22, "bold"),
                               text_color=color)
        val_lbl.place(x=12, y=26)
        sub_lbl = ctk.CTkLabel(card, text=subtitle, font=("Consolas", 9),
                               text_color=DIM)
        sub_lbl.place(x=12, y=58)

        # Usage bar at bottom
        bar_bg = ctk.CTkFrame(card, fg_color=BG3, height=4, corner_radius=2)
        bar_bg.place(x=12, rely=1.0, y=-12, relwidth=1.0, width=-24)
        bar_fill = ctk.CTkFrame(bar_bg, fg_color=color, height=4, corner_radius=2)
        bar_fill.place(x=0, y=0, relwidth=0.0)

        return {"card": card, "value": val_lbl, "subtitle": sub_lbl, "bar": bar_fill, "color": color}

    def _load_hw_info(self):
        """Load hardware info into metric cards and detail rows."""
        L = _launcher()

        # ── CPU ────────────────────────────────────────────────────────
        try:
            cpu_pct = psutil.cpu_percent(interval=1)
            cores_phys = psutil.cpu_count(logical=False) or 0
            cores_log = psutil.cpu_count(logical=True) or 0
            cpu_freq = psutil.cpu_freq()
            freq_text = f"{cpu_freq.current:.0f} MHz" if cpu_freq else "—"
            try:
                cpu_name = subprocess.check_output(
                    ["wmic", "cpu", "get", "Name"],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    text=True, timeout=5).strip().split("\n")[-1].strip()
            except Exception:
                cpu_name = "Unknown"

            color = GREEN if cpu_pct < 60 else ORANGE if cpu_pct < 85 else RED
            self._hw_card_cpu["value"].configure(text=f"{cpu_pct:.0f}%", text_color=color)
            self._hw_card_cpu["subtitle"].configure(text=f"{cores_phys}C/{cores_log}T  {freq_text}")
            self._hw_card_cpu["bar"].configure(fg_color=color)
            self._hw_card_cpu["bar"].place(x=0, y=0, relwidth=cpu_pct / 100)

            self._hw_detail_rows["cpu_name"].configure(text=cpu_name)
            self._hw_detail_rows["cpu_cores"].configure(text=f"{cores_phys} physical, {cores_log} logical")
            self._hw_detail_rows["cpu_freq"].configure(
                text=f"{cpu_freq.current:.0f} MHz (max {cpu_freq.max:.0f} MHz)" if cpu_freq else "—")
        except Exception:
            pass

        # ── RAM ────────────────────────────────────────────────────────
        try:
            vm = psutil.virtual_memory()
            ram_pct = vm.percent
            color = GREEN if ram_pct < 60 else ORANGE if ram_pct < 85 else RED
            self._hw_card_ram["value"].configure(text=f"{ram_pct:.0f}%", text_color=color)
            self._hw_card_ram["subtitle"].configure(
                text=f"{vm.used/1e9:.1f} / {vm.total/1e9:.1f} GB")
            self._hw_card_ram["bar"].configure(fg_color=color)
            self._hw_card_ram["bar"].place(x=0, y=0, relwidth=ram_pct / 100)

            self._hw_detail_rows["ram_total"].configure(text=f"{vm.total/1e9:.1f} GB")
            self._hw_detail_rows["ram_used"].configure(text=f"{vm.used/1e9:.1f} GB ({ram_pct:.1f}%)")
            self._hw_detail_rows["ram_avail"].configure(text=f"{vm.available/1e9:.1f} GB")
        except Exception:
            pass

        # ── GPU ────────────────────────────────────────────────────────
        try:
            if L._GPU_OK:
                import pynvml
                name = pynvml.nvmlDeviceGetName(L._GPU_HANDLE)
                mem = pynvml.nvmlDeviceGetMemoryInfo(L._GPU_HANDLE)
                util = pynvml.nvmlDeviceGetUtilizationRates(L._GPU_HANDLE)
                temp = pynvml.nvmlDeviceGetTemperature(
                    L._GPU_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
                power = pynvml.nvmlDeviceGetPowerUsage(L._GPU_HANDLE) / 1000
                fan = 0
                try:
                    fan = pynvml.nvmlDeviceGetFanSpeed(L._GPU_HANDLE)
                except Exception:
                    pass

                vram_pct = mem.used * 100 / mem.total if mem.total else 0
                color = GREEN if temp < 70 else ORANGE if temp < 82 else RED
                self._hw_card_gpu["value"].configure(text=f"{temp}°C", text_color=color)
                self._hw_card_gpu["subtitle"].configure(
                    text=f"VRAM {mem.used/1e9:.1f}/{mem.total/1e9:.0f} GB  {util.gpu}%")
                self._hw_card_gpu["bar"].configure(fg_color=color)
                self._hw_card_gpu["bar"].place(x=0, y=0, relwidth=vram_pct / 100)

                self._hw_detail_rows["gpu_name"].configure(text=str(name))
                self._hw_detail_rows["vram_total"].configure(text=f"{mem.total/1e9:.1f} GB")
                self._hw_detail_rows["vram_used"].configure(
                    text=f"{mem.used/1e9:.2f} GB ({vram_pct:.0f}%)")
                self._hw_detail_rows["gpu_temp"].configure(
                    text=f"{temp}°C", text_color=color)
                self._hw_detail_rows["gpu_power"].configure(text=f"{power:.0f} W")
                self._hw_detail_rows["gpu_fan"].configure(
                    text=f"{fan}%" if fan else "—")
            else:
                self._hw_card_gpu["value"].configure(text="N/A", text_color=DIM)
                self._hw_card_gpu["subtitle"].configure(text="CPU-only mode")
                self._hw_detail_rows["gpu_name"].configure(text="No GPU detected")
        except Exception as e:
            self._hw_card_gpu["value"].configure(text="ERR", text_color=RED)
            self._hw_card_gpu["subtitle"].configure(text=str(e)[:30])
