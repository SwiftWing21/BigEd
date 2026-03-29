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
        self._mode_var = None
        self._mode_lbl = None
        self._train_btn = None
        self._episode_lbl = None
        self._reward_lbl = None
        self._loss_lbl = None
        self._training_active = False

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
        self._phase_lbl.pack(anchor="w", padx=10, pady=(5, 0))

        self._episode_lbl = ctk.CTkLabel(curriculum_frame,
                                         text="Episode: \u2014  |  Step: \u2014",
                                         font=FONT_XS, text_color=DIM)
        self._episode_lbl.pack(anchor="w", padx=10, pady=(0, 5))

        self._reward_lbl = ctk.CTkLabel(curriculum_frame,
                                        text="Reward: \u2014  |  Loss: \u2014",
                                        font=FONT_XS, text_color=DIM)
        self._reward_lbl.pack(anchor="w", padx=10, pady=(0, 10))

        # ML Training Controls
        ml_frame = ctk.CTkFrame(frame, fg_color=BG2, corner_radius=8)
        ml_frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(ml_frame, text="Agent Mode", font=FONT_BOLD,
                     text_color=TEXT).pack(anchor="w", padx=10, pady=(10, 0))

        mode_row = ctk.CTkFrame(ml_frame, fg_color="transparent")
        mode_row.pack(fill="x", padx=10, pady=5)

        self._mode_var = ctk.StringVar(value=self._get_current_mode())
        mode_menu = ctk.CTkOptionMenu(
            mode_row, values=["ml", "llm"],
            variable=self._mode_var, font=FONT_SM,
            fg_color=BG3, button_color=ACCENT,
            command=self._on_mode_change,
            width=80,
        )
        mode_menu.pack(side="left")

        self._mode_lbl = ctk.CTkLabel(mode_row,
            text="RL Policy (PPO)" if self._mode_var.get() == "ml" else "LLM Agent Brain",
            font=FONT_XS, text_color=DIM)
        self._mode_lbl.pack(side="left", padx=(10, 0))

        # Start/Stop Training button
        btn_row = ctk.CTkFrame(ml_frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(0, 10))

        self._train_btn = ctk.CTkButton(
            btn_row, text="Start Training", font=FONT_SM,
            fg_color=GREEN or "#22c55e", hover_color=ACCENT_H,
            command=self._toggle_training, width=130,
        )
        self._train_btn.pack(side="left")

        self._loss_lbl = ctk.CTkLabel(btn_row, text="",
                                      font=FONT_XS, text_color=DIM)
        self._loss_lbl.pack(side="left", padx=(10, 0))

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

    def _get_current_mode(self):
        """Read current mode from fleet.toml."""
        try:
            import config as fleet_config
            cfg = fleet_config.load_config()
            return cfg.get("factorio", {}).get("mode", "ml")
        except Exception:
            return "ml"

    def _on_mode_change(self, value):
        """Update mode label and persist to fleet.toml."""
        if self._mode_lbl:
            self._mode_lbl.configure(
                text="RL Policy (PPO)" if value == "ml" else "LLM Agent Brain"
            )
        try:
            import config as fleet_config
            cfg = fleet_config.load_config()
            if "factorio" not in cfg:
                cfg["factorio"] = {}
            cfg["factorio"]["mode"] = value
            fleet_config.save_config(cfg)
            log.info("Factorio mode set to %s", value)
        except Exception:
            log.warning("Failed to save mode to fleet.toml", exc_info=True)

    def _toggle_training(self):
        """Start or stop ML training via bridge API."""
        try:
            import config as fleet_config
            cfg = fleet_config.load_config()
            port = cfg.get("factorio", {}).get("bridge_port", 27016)

            if self._training_active:
                # Pause training
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/pause",
                    method="POST",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=5)
                self._training_active = False
                if self._train_btn:
                    self._train_btn.configure(
                        text="Start Training",
                        fg_color=GREEN or "#22c55e",
                    )
                log.info("Training paused")
            else:
                # Ensure mode is ML
                mode = self._mode_var.get() if self._mode_var else "ml"
                if mode != "ml":
                    self._mode_var.set("ml")
                    self._on_mode_change("ml")

                # Resume training
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/resume",
                    method="POST",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=5)
                self._training_active = True
                if self._train_btn:
                    self._train_btn.configure(
                        text="Stop Training",
                        fg_color=RED or "#ef4444",
                    )
                log.info("Training started")
        except Exception:
            log.warning("Failed to toggle training", exc_info=True)
            if self._loss_lbl:
                self._loss_lbl.configure(text="Bridge not running", text_color=ORANGE)

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
                mode = data.get("mode", cfg.get("factorio", {}).get("mode", "ml"))
                mode_tag = " [ML]" if mode == "ml" else " [LLM]"
                color = GREEN if running else RED
                self._status_lbl.configure(
                    text=f"Bridge: {'Running' if running else 'Stopped'}{mode_tag}",
                    text_color=color,
                )
            if self._tick_lbl:
                self._tick_lbl.configure(text=f"Tick: {data.get('tick', '\u2014')}")

            # Update training button state from bridge
            paused = data.get("paused", True)
            if not paused and not self._training_active:
                self._training_active = True
                if self._train_btn:
                    self._train_btn.configure(text="Stop Training",
                                              fg_color=RED or "#ef4444")
            elif paused and self._training_active:
                self._training_active = False
                if self._train_btn:
                    self._train_btn.configure(text="Start Training",
                                              fg_color=GREEN or "#22c55e")
        except Exception:
            if self._status_lbl:
                self._status_lbl.configure(text="Bridge: Not Running",
                                           text_color=DIM)

        # Fetch ML training metrics
        self._refresh_training_metrics()

    def _refresh_training_metrics(self):
        """Fetch training metrics from bridge and update labels."""
        try:
            import config as fleet_config
            cfg = fleet_config.load_config()
            port = cfg.get("factorio", {}).get("bridge_port", 27016)
            resp = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/training/status", timeout=2
            )
            data = json.loads(resp.read())

            episode = data.get("episode", "\u2014")
            step = data.get("step", "\u2014")
            phase = data.get("phase", 1)
            phase_name = data.get("phase_name", "Bootstrap")
            lessons_done = data.get("lessons_completed", 0)
            lessons_total = data.get("lessons_total", "?")
            reward = data.get("last_reward", None)
            policy_loss = data.get("policy_loss", None)
            value_loss = data.get("value_loss", None)

            if self._phase_lbl:
                self._phase_lbl.configure(
                    text=f"Phase {phase}: {phase_name}  ({lessons_done}/{lessons_total} lessons)",
                    text_color=TEXT,
                )
            if self._episode_lbl:
                self._episode_lbl.configure(
                    text=f"Episode: {episode}  |  Step: {step}",
                    text_color=TEXT,
                )
            if self._reward_lbl:
                r_str = f"{reward:.3f}" if reward is not None else "\u2014"
                self._reward_lbl.configure(
                    text=f"Reward: {r_str}",
                    text_color=GREEN if reward and reward > 0 else DIM,
                )
            if self._loss_lbl:
                pl_str = f"{policy_loss:.4f}" if policy_loss is not None else "\u2014"
                vl_str = f"{value_loss:.4f}" if value_loss is not None else "\u2014"
                self._loss_lbl.configure(
                    text=f"P-Loss: {pl_str}  V-Loss: {vl_str}",
                    text_color=DIM,
                )
        except Exception:
            pass  # Training endpoint may not exist yet

    def on_close(self):
        """Clean up."""
        pass
