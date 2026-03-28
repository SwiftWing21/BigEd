# BigEd/launcher/modules/mod_factorio.py
"""Factorio Sandbox Module — launcher tab with status, cadence, curriculum."""
import json
import logging
import subprocess as sp
import urllib.request

import customtkinter as ctk

log = logging.getLogger("biged.module.factorio")

BG = BG2 = BG3 = ACCENT = ACCENT_H = GOLD = TEXT = DIM = GREEN = ORANGE = RED = ""
FONT_SM = FONT_STAT = FONT_BOLD = FONT_XS = ("Segoe UI", 10)
FLEET_DIR = None


class Module:
    NAME = "factorio"
    LABEL = "Factorio"
    VERSION = "0.1.0"
    DEFAULT_ENABLED = False
    DEPENDS_ON = []

    def __init__(self, app):
        self.app = app
        self._init_theme()
        self._status_lbl = None
        self._tick_lbl = None
        self._cadence_var = None
        self._phase_lbl = None
        self._spectator_btn = None

    def _init_theme(self):
        global BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM, GREEN, ORANGE, RED
        global FONT_SM, FONT_STAT, FONT_BOLD, FONT_XS, FLEET_DIR
        try:
            from ui.theme import (BG as _BG, BG2 as _BG2, BG3 as _BG3,
                                  ACCENT as _ACC, ACCENT_H as _AH, GOLD as _GOLD,
                                  TEXT as _TEXT, DIM as _DIM, GREEN as _GR,
                                  ORANGE as _OR, RED as _RED,
                                  FONT_SM as _FSM, FONT_STAT as _FST,
                                  FONT_BOLD as _FB, FONT_XS as _FXS)
            BG = _BG; BG2 = _BG2; BG3 = _BG3
            ACCENT = _ACC; ACCENT_H = _AH; GOLD = _GOLD
            TEXT = _TEXT; DIM = _DIM; GREEN = _GR; ORANGE = _OR; RED = _RED
            FONT_SM = _FSM; FONT_STAT = _FST; FONT_BOLD = _FB; FONT_XS = _FXS
        except Exception:
            pass

    def build_tab(self, parent):
        """Build the Factorio tab UI."""
        frame = ctk.CTkFrame(parent, fg_color=BG)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        # Header
        ctk.CTkLabel(frame, text="Factorio Sandbox", font=FONT_BOLD,
                     text_color=GOLD).pack(anchor="w", padx=10, pady=(10, 5))

        # Status panel
        status_frame = ctk.CTkFrame(frame, fg_color=BG2, corner_radius=8)
        status_frame.pack(fill="x", padx=10, pady=5)

        self._status_lbl = ctk.CTkLabel(status_frame, text="Bridge: Not Running",
                                        font=FONT_SM, text_color=DIM)
        self._status_lbl.pack(anchor="w", padx=10, pady=5)

        self._tick_lbl = ctk.CTkLabel(status_frame, text="Tick: \u2014",
                                      font=FONT_XS, text_color=DIM)
        self._tick_lbl.pack(anchor="w", padx=10, pady=(0, 5))

        # Cadence control
        cadence_frame = ctk.CTkFrame(frame, fg_color=BG2, corner_radius=8)
        cadence_frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(cadence_frame, text="Cadence", font=FONT_BOLD,
                     text_color=TEXT).pack(anchor="w", padx=10, pady=(10, 0))

        self._cadence_var = ctk.StringVar(value="adaptive")
        cadence_menu = ctk.CTkOptionMenu(
            cadence_frame, values=["fast", "medium", "slow", "adaptive"],
            variable=self._cadence_var, font=FONT_SM,
            fg_color=BG3, button_color=ACCENT,
        )
        cadence_menu.pack(anchor="w", padx=10, pady=10)

        # Spectator button
        spectator_frame = ctk.CTkFrame(frame, fg_color=BG2, corner_radius=8)
        spectator_frame.pack(fill="x", padx=10, pady=5)

        self._spectator_btn = ctk.CTkButton(
            spectator_frame, text="Launch Spectator", font=FONT_SM,
            fg_color=ACCENT, hover_color=ACCENT_H,
            command=self._launch_spectator,
        )
        self._spectator_btn.pack(anchor="w", padx=10, pady=10)

        # Curriculum progress
        curriculum_frame = ctk.CTkFrame(frame, fg_color=BG2, corner_radius=8)
        curriculum_frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(curriculum_frame, text="Training Progress", font=FONT_BOLD,
                     text_color=TEXT).pack(anchor="w", padx=10, pady=(10, 0))

        self._phase_lbl = ctk.CTkLabel(curriculum_frame,
                                       text="Phase 1: Curriculum \u2014 Not started",
                                       font=FONT_SM, text_color=DIM)
        self._phase_lbl.pack(anchor="w", padx=10, pady=10)

        # Check if first run
        self._check_first_run(frame)

    def _launch_spectator(self):
        """Launch Factorio client as spectator connecting to the headless server."""
        try:
            from factorio.lua_installer import detect_factorio_path
            from factorio.bridge_config import load_factorio_config
            cfg = load_factorio_config()
            fpath = detect_factorio_path()
            if not fpath:
                log.warning("Factorio install not found")
                return
            exe = fpath / "bin" / "x64" / "factorio.exe"
            if not exe.exists():
                exe = fpath / "factorio"
            sp.Popen(
                [str(exe), "--mp-connect", f"localhost:{cfg.rcon_port}"],
                creationflags=getattr(sp, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            log.warning("Failed to launch spectator", exc_info=True)

    def _check_first_run(self, parent):
        """Show setup prompt if RCON password is not configured."""
        try:
            import config as fleet_config
            cfg = fleet_config.load_config()
            if not cfg.get("factorio", {}).get("rcon_password"):
                lbl = ctk.CTkLabel(parent,
                    text="Setup needed \u2014 configure RCON password in Settings > Factorio",
                    font=FONT_SM, text_color=ORANGE)
                lbl.pack(anchor="w", padx=10, pady=5)
        except Exception:
            pass

    def on_refresh(self):
        """Poll bridge API for status updates."""
        try:
            import config as fleet_config
            cfg = fleet_config.load_config()
            port = cfg.get("factorio", {}).get("bridge_port", 27016)
            resp = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/status", timeout=2
            )
            data = json.loads(resp.read())
            if self._status_lbl:
                running = data.get("running", False)
                color = GREEN if running else RED
                self._status_lbl.configure(
                    text=f"Bridge: {'Running' if running else 'Stopped'}",
                    text_color=color,
                )
            if self._tick_lbl:
                self._tick_lbl.configure(text=f"Tick: {data.get('tick', '\u2014')}")
        except Exception:
            if self._status_lbl:
                self._status_lbl.configure(text="Bridge: Not Running",
                                           text_color=DIM)

    def on_close(self):
        """Clean up."""
        pass
