"""Manual Mode Module — operator audit queue, scheduler, and results viewer.

Panels (sidebar navigation):
  A. Prompt Queue   — queue audit prompts with per-item model/token/repeat config
  B. Scheduler      — one-time or recurring schedule with enable toggle
  C. Results Viewer — past run history; click to expand, Export to .md
"""
import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import customtkinter as ctk

# Dynamic FLEET_DIR — works regardless of install location
try:
    import launcher as _launcher
    FLEET_DIR = Path(_launcher.FLEET_DIR)
except ImportError:
    FLEET_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fleet"

FLEET_TOML    = FLEET_DIR / "fleet.toml"
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"

LABEL = "Manual Mode"

# Theme imports
try:
    from ui.theme import (
        BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
        GREEN, ORANGE, RED, FONT, FONT_SM, FONT_BOLD, FONT_TITLE,
        FONT_STAT, FONT_XS, CARD_RADIUS, BTN_HEIGHT,
    )
except ImportError:
    BG, BG2, BG3                    = "#1a1a1a", "#242424", "#2d2d2d"
    ACCENT, ACCENT_H, GOLD          = "#b22222", "#8b0000", "#c8a84b"
    TEXT, DIM                       = "#e2e2e2", "#888888"
    GREEN, ORANGE, RED              = "#4caf50", "#ff9800", "#f44336"
    FONT      = ("Segoe UI", 11)
    FONT_SM   = ("Segoe UI", 10)
    FONT_BOLD = ("Segoe UI", 11, "bold")
    FONT_TITLE= ("Segoe UI", 14, "bold")
    FONT_STAT = ("Consolas", 10)
    FONT_XS   = ("Consolas", 8)
    CARD_RADIUS = 8
    BTN_HEIGHT  = 28

_MODELS = ["claude-sonnet", "claude-haiku", "claude-opus"]


class Module:
    """Manual Mode module — operator-controlled audit queue + scheduler + results."""

    LABEL           = "Manual Mode"
    VERSION         = "0.052"
    DEFAULT_ENABLED = True
    DEPENDS_ON      = []

    def __init__(self, app):
        self._app        = app
        self._engine     = None   # lazy-init ManualModeEngine
        self._queue      = []     # in-memory queue; synced to TOML on change
        self._run_active = False

        # Widget refs populated by build_tab
        self._content             = None
        self._panel_frames        = {}
        self._sidebar_btns        = {}
        self._queue_list_frame    = None
        self._run_btn             = None
        self._queue_status_lbl    = None
        self._results_scroll      = None

        # Scheduler widget refs
        self._sched_enabled_var   = None
        self._sched_mode_var      = None
        self._sched_run_at_var    = None
        self._sched_interval_var  = None
        self._next_run_lbl        = None
        self._sched_dt_frame      = None
        self._sched_iv_frame      = None

    # ── Engine ────────────────────────────────────────────────────────────────

    def _get_engine(self):
        if self._engine is None:
            if str(FLEET_DIR) not in sys.path:
                sys.path.insert(0, str(FLEET_DIR))
            try:
                from manual_mode import ManualModeEngine
                self._engine = ManualModeEngine()
                self._queue  = self._engine.get_queue()
            except Exception:
                self._engine = None
        return self._engine

    # ── Module interface ──────────────────────────────────────────────────────

    def build_tab(self, parent):
        """Build the Manual Mode tab — sidebar + three content panels."""
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        # ── Sidebar ──
        sidebar = ctk.CTkFrame(parent, fg_color=BG2, corner_radius=0, width=148)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        ctk.CTkLabel(
            sidebar, text="MANUAL MODE", font=FONT_BOLD, text_color=GOLD, anchor="w",
        ).pack(fill="x", padx=10, pady=(14, 10))

        for key, label in [
            ("queue",     "Prompt Queue"),
            ("scheduler", "Scheduler"),
            ("results",   "Results"),
        ]:
            btn = ctk.CTkButton(
                sidebar, text=label, anchor="w", font=FONT_SM,
                fg_color="transparent", text_color=DIM,
                hover_color=BG3, height=30, corner_radius=4,
                command=lambda k=key: self._switch_panel(k),
            )
            btn.pack(fill="x", padx=6, pady=2)
            self._sidebar_btns[key] = btn

        # ── Content area ──
        self._content = ctk.CTkFrame(parent, fg_color=BG, corner_radius=0)
        self._content.grid(row=0, column=1, sticky="nsew")
        self._content.grid_columnconfigure(0, weight=1)
        self._content.grid_rowconfigure(0, weight=1)

        self._build_panel_queue(self._content)
        self._build_panel_scheduler(self._content)
        self._build_panel_results(self._content)

        self._switch_panel("queue")

    def _switch_panel(self, key: str):
        for k, frame in self._panel_frames.items():
            if k == key:
                frame.grid(row=0, column=0, sticky="nsew")
            else:
                frame.grid_remove()
        for k, btn in self._sidebar_btns.items():
            btn.configure(
                fg_color=BG3   if k == key else "transparent",
                text_color=TEXT if k == key else DIM,
            )
        if key == "results":
            self._refresh_results()

    def _card(self, parent, title: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=BG2, corner_radius=CARD_RADIUS)
        card.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(card, text=title, font=FONT_TITLE, text_color=GOLD,
                     anchor="w").pack(fill="x", padx=12, pady=(10, 4))
        return card

    # ── Panel A: Prompt Queue ─────────────────────────────────────────────────

    def _build_panel_queue(self, parent):
        frame = ctk.CTkScrollableFrame(parent, fg_color=BG, corner_radius=0)
        self._panel_frames["queue"] = frame

        card = self._card(frame, "Audit Prompt Queue")
        ctk.CTkLabel(
            card,
            text="Queue prompts for unattended audit runs. "
                 "Each item is dispatched directly to the Claude API.",
            font=FONT_SM, text_color=DIM, wraplength=580, anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 8))

        # Queue list container
        self._queue_list_frame = ctk.CTkFrame(card, fg_color=BG3, corner_radius=4)
        self._queue_list_frame.pack(fill="x", padx=12, pady=(0, 6))

        # Bottom controls
        ctrl = ctk.CTkFrame(card, fg_color="transparent")
        ctrl.pack(fill="x", padx=12, pady=(4, 10))

        ctk.CTkButton(
            ctrl, text="+ Add Prompt", width=110, height=BTN_HEIGHT,
            font=FONT_SM, fg_color=ACCENT, hover_color=ACCENT_H,
            command=self._open_add_prompt_dialog,
        ).pack(side="left")

        self._run_btn = ctk.CTkButton(
            ctrl, text="Run Queue", width=100, height=BTN_HEIGHT,
            font=FONT_SM, fg_color="#1e3a1e", hover_color="#2a4a2a",
            command=self._run_queue,
        )
        self._run_btn.pack(side="right")

        self._queue_status_lbl = ctk.CTkLabel(
            ctrl, text="", font=FONT_XS, text_color=DIM,
        )
        self._queue_status_lbl.pack(side="right", padx=8)

        self._refresh_queue_display()

    def _refresh_queue_display(self):
        for w in self._queue_list_frame.winfo_children():
            w.destroy()
        if not self._queue:
            ctk.CTkLabel(
                self._queue_list_frame,
                text="Queue empty — click '+ Add Prompt' to begin",
                font=FONT_SM, text_color=DIM,
            ).pack(padx=8, pady=12)
            return
        for i, item in enumerate(self._queue):
            self._build_queue_row(self._queue_list_frame, i, item)

    def _build_queue_row(self, parent, idx: int, item: dict):
        row = ctk.CTkFrame(
            parent, fg_color=BG2 if idx % 2 == 0 else BG3, corner_radius=4,
        )
        row.pack(fill="x", padx=4, pady=2)

        # Index label
        ctk.CTkLabel(
            row, text=f"{idx + 1}.", font=FONT_STAT, text_color=DIM, width=24,
        ).pack(side="left", padx=(8, 2), pady=6)

        # Prompt preview
        preview = (item.get("prompt", "") or "")[:80]
        if len(item.get("prompt", "") or "") > 80:
            preview += "…"
        ctk.CTkLabel(
            row, text=preview, font=FONT_SM, text_color=TEXT, anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=4, pady=6)

        # Controls on the right
        ctrl = ctk.CTkFrame(row, fg_color="transparent")
        ctrl.pack(side="right", padx=4, pady=4)

        # Model selector
        model_var = ctk.StringVar(value=item.get("model", "claude-sonnet"))
        ctk.CTkOptionMenu(
            ctrl, variable=model_var, values=_MODELS,
            width=130, height=24, font=FONT_XS, fg_color=BG,
            command=lambda v, i=idx: self._update_item(i, "model", v),
        ).pack(side="left", padx=2)

        # Max tokens entry
        tok_var = ctk.StringVar(value=str(item.get("max_tokens", 4096)))
        tok_entry = ctk.CTkEntry(
            ctrl, textvariable=tok_var, width=64, height=24,
            font=FONT_STAT, fg_color=BG, placeholder_text="tokens",
        )
        tok_entry.pack(side="left", padx=2)
        tok_entry.bind(
            "<FocusOut>",
            lambda e, i=idx, v=tok_var: self._update_item(
                i, "max_tokens", self._clamp_tokens(v.get())
            ),
        )

        # Repeat count entry
        ctk.CTkLabel(ctrl, text="×", font=FONT_SM, text_color=DIM).pack(side="left")
        rep_var = ctk.StringVar(value=str(item.get("repeat", 1)))
        rep_entry = ctk.CTkEntry(ctrl, textvariable=rep_var, width=36, height=24,
                                  font=FONT_STAT, fg_color=BG)
        rep_entry.pack(side="left", padx=2)
        rep_entry.bind(
            "<FocusOut>",
            lambda e, i=idx, v=rep_var: self._update_item(
                i, "repeat", max(1, min(10, int(v.get() or 1)))
            ),
        )

        # Up / Down / Delete
        for symbol, delta_or_cmd in [
            ("↑", lambda i=idx: self._move_item(i, -1)),
            ("↓", lambda i=idx: self._move_item(i, +1)),
        ]:
            ctk.CTkButton(
                ctrl, text=symbol, width=24, height=24, font=FONT_XS,
                fg_color=BG, hover_color=BG3, command=delta_or_cmd,
            ).pack(side="left", padx=1)

        ctk.CTkButton(
            ctrl, text="✕", width=24, height=24, font=FONT_XS,
            fg_color=BG, hover_color="#5a2020",
            command=lambda i=idx: self._delete_item(i),
        ).pack(side="left", padx=(1, 4))

    @staticmethod
    def _clamp_tokens(val: str) -> int:
        try:
            return max(256, min(32768, int(val)))
        except (ValueError, TypeError):
            return 4096

    def _update_item(self, idx: int, key: str, value):
        if 0 <= idx < len(self._queue):
            self._queue[idx][key] = value
            self._persist_queue()

    def _move_item(self, idx: int, direction: int):
        new_idx = idx + direction
        if 0 <= new_idx < len(self._queue):
            self._queue[idx], self._queue[new_idx] = self._queue[new_idx], self._queue[idx]
            self._persist_queue()
            self._refresh_queue_display()

    def _delete_item(self, idx: int):
        if 0 <= idx < len(self._queue):
            self._queue.pop(idx)
            self._persist_queue()
            self._refresh_queue_display()

    def _persist_queue(self):
        eng = self._get_engine()
        if eng:
            try:
                eng.set_queue(self._queue)
            except Exception:
                pass

    def _open_add_prompt_dialog(self):
        """Open a CTkToplevel with a full-text prompt entry."""
        dialog = ctk.CTkToplevel(self._content)
        dialog.title("Add Audit Prompt")
        dialog.geometry("660x440")
        dialog.resizable(True, True)
        dialog.configure(fg_color=BG)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="Prompt Text", font=FONT_BOLD,
                     text_color=GOLD, anchor="w").pack(fill="x", padx=16, pady=(16, 4))

        txt = ctk.CTkTextbox(dialog, font=FONT, fg_color=BG2, height=210)
        txt.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        opt_row = ctk.CTkFrame(dialog, fg_color="transparent")
        opt_row.pack(fill="x", padx=16, pady=4)

        ctk.CTkLabel(opt_row, text="Model:", font=FONT_SM, text_color=DIM).pack(side="left")
        model_var = ctk.StringVar(value="claude-sonnet")
        ctk.CTkOptionMenu(opt_row, variable=model_var, values=_MODELS,
                          width=140, height=26, font=FONT_SM,
                          fg_color=BG2).pack(side="left", padx=4)

        ctk.CTkLabel(opt_row, text="Max tokens:", font=FONT_SM,
                     text_color=DIM).pack(side="left", padx=(12, 2))
        tok_var = ctk.StringVar(value="4096")
        ctk.CTkEntry(opt_row, textvariable=tok_var, width=72, height=26,
                     font=FONT_STAT, fg_color=BG2).pack(side="left", padx=4)

        ctk.CTkLabel(opt_row, text="Repeat (1-10):", font=FONT_SM,
                     text_color=DIM).pack(side="left", padx=(12, 2))
        rep_var = ctk.StringVar(value="1")
        ctk.CTkEntry(opt_row, textvariable=rep_var, width=40, height=26,
                     font=FONT_STAT, fg_color=BG2).pack(side="left", padx=4)

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(8, 16))

        def _add():
            prompt = txt.get("1.0", "end").strip()
            if not prompt:
                return
            self._queue.append({
                "prompt":     prompt,
                "model":      model_var.get(),
                "max_tokens": self._clamp_tokens(tok_var.get()),
                "repeat":     max(1, min(10, int(rep_var.get() or 1))),
            })
            self._persist_queue()
            self._refresh_queue_display()
            dialog.destroy()

        ctk.CTkButton(
            btn_row, text="Add to Queue", width=120, height=BTN_HEIGHT,
            font=FONT_SM, fg_color=ACCENT, hover_color=ACCENT_H, command=_add,
        ).pack(side="right")
        ctk.CTkButton(
            btn_row, text="Cancel", width=80, height=BTN_HEIGHT,
            font=FONT_SM, fg_color=BG3, command=dialog.destroy,
        ).pack(side="right", padx=6)

    def _run_queue(self):
        if not self._queue or self._run_active:
            return
        eng = self._get_engine()
        if not eng:
            self._queue_status_lbl.configure(text="Engine unavailable", text_color=RED)
            return

        self._run_active = True
        self._run_btn.configure(state="disabled")
        self._queue_status_lbl.configure(text="Running…", text_color=ORANGE)

        def _progress(i, total, result):
            txt = f"{i}/{total} — {result.get('status', '?')}"
            try:
                self._queue_status_lbl.configure(text=txt, text_color=ORANGE)
            except Exception:
                pass

        def _run():
            try:
                summary = eng.run_queue(self._queue, on_progress=_progress)
                tok  = summary.get("total_tokens", 0)
                cost = summary.get("total_cost", 0.0)
                try:
                    self._queue_status_lbl.configure(
                        text=f"Done — {tok:,} tokens, ${cost:.4f}", text_color=GREEN,
                    )
                except Exception:
                    pass
            except Exception as exc:
                try:
                    self._queue_status_lbl.configure(
                        text=f"Error: {exc}", text_color=RED,
                    )
                except Exception:
                    pass
            finally:
                self._run_active = False
                try:
                    self._run_btn.configure(state="normal")
                except Exception:
                    pass

        threading.Thread(target=_run, daemon=True).start()

    # ── Panel B: Scheduler ────────────────────────────────────────────────────

    def _build_panel_scheduler(self, parent):
        frame = ctk.CTkScrollableFrame(parent, fg_color=BG, corner_radius=0)
        self._panel_frames["scheduler"] = frame

        card = self._card(frame, "Queue Scheduler")
        ctk.CTkLabel(
            card,
            text="Automatically run the prompt queue on a schedule. "
                 "The fleet must be running for scheduled execution.",
            font=FONT_SM, text_color=DIM, wraplength=580, anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 10))

        eng   = self._get_engine()
        sched = eng.get_scheduler() if eng else {}

        # Enabled toggle
        self._sched_enabled_var = ctk.BooleanVar(value=sched.get("enabled", False))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=3)
        ctk.CTkLabel(row, text="Enabled", font=FONT_SM, text_color=TEXT,
                     width=130, anchor="w").pack(side="left")
        ctk.CTkSwitch(row, variable=self._sched_enabled_var,
                      text="", width=50,
                      command=self._save_scheduler).pack(side="left")

        # Mode selector
        self._sched_mode_var = ctk.StringVar(value=sched.get("mode", "one-time"))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=3)
        ctk.CTkLabel(row, text="Mode", font=FONT_SM, text_color=TEXT,
                     width=130, anchor="w").pack(side="left")
        ctk.CTkSegmentedButton(
            row, values=["one-time", "recurring"],
            variable=self._sched_mode_var,
            command=self._on_sched_mode_change,
        ).pack(side="left")

        # Mode-specific settings (inside a fixed-position container)
        mode_container = ctk.CTkFrame(card, fg_color="transparent")
        mode_container.pack(fill="x", padx=12, pady=3)

        # One-time: date/time picker
        self._sched_dt_frame = ctk.CTkFrame(mode_container, fg_color="transparent")
        ctk.CTkLabel(self._sched_dt_frame, text="Run At", font=FONT_SM,
                     text_color=TEXT, width=130, anchor="w").pack(side="left")
        self._sched_run_at_var = ctk.StringVar(value=sched.get("run_at", ""))
        ctk.CTkEntry(
            self._sched_dt_frame, textvariable=self._sched_run_at_var,
            width=200, height=28, font=FONT_STAT, fg_color=BG2,
            placeholder_text="YYYY-MM-DD HH:MM",
        ).pack(side="left", padx=4)

        # Recurring: interval in days
        self._sched_iv_frame = ctk.CTkFrame(mode_container, fg_color="transparent")
        ctk.CTkLabel(self._sched_iv_frame, text="Interval (days)", font=FONT_SM,
                     text_color=TEXT, width=130, anchor="w").pack(side="left")
        self._sched_interval_var = ctk.StringVar(
            value=str(sched.get("interval_days", 1))
        )
        ctk.CTkEntry(
            self._sched_iv_frame, textvariable=self._sched_interval_var,
            width=60, height=28, font=FONT_STAT, fg_color=BG2,
        ).pack(side="left", padx=4)
        ctk.CTkLabel(self._sched_iv_frame, text="(1-30)", font=FONT_XS,
                     text_color=DIM).pack(side="left")

        # Next run (read-only)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=3)
        ctk.CTkLabel(row, text="Next Run", font=FONT_SM, text_color=TEXT,
                     width=130, anchor="w").pack(side="left")
        self._next_run_lbl = ctk.CTkLabel(
            row, text=sched.get("next_run", "—") or "—",
            font=FONT_STAT, text_color=DIM,
        )
        self._next_run_lbl.pack(side="left")

        # Save button
        ctk.CTkButton(
            card, text="Save Schedule", width=120, height=BTN_HEIGHT,
            font=FONT_SM, fg_color=ACCENT, hover_color=ACCENT_H,
            command=self._save_scheduler,
        ).pack(anchor="e", padx=12, pady=(6, 12))

        # Show correct mode frame on load
        self._on_sched_mode_change(self._sched_mode_var.get())

    def _on_sched_mode_change(self, mode: str):
        if mode == "one-time":
            self._sched_dt_frame.pack(fill="x")
            self._sched_iv_frame.pack_forget()
        else:
            self._sched_dt_frame.pack_forget()
            self._sched_iv_frame.pack(fill="x")

    def _save_scheduler(self):
        try:
            interval = max(1, min(30, int(self._sched_interval_var.get())))
        except (ValueError, AttributeError):
            interval = 1
        next_run = self._compute_next_run()
        sched = {
            "enabled":       self._sched_enabled_var.get(),
            "mode":          self._sched_mode_var.get(),
            "run_at":        self._sched_run_at_var.get().strip(),
            "interval_days": interval,
            "next_run":      next_run,
        }
        eng = self._get_engine()
        if eng:
            try:
                eng.set_scheduler(sched)
                self._next_run_lbl.configure(text=next_run or "—")
            except Exception:
                pass

    def _compute_next_run(self) -> str:
        mode = self._sched_mode_var.get()
        if mode == "one-time":
            return self._sched_run_at_var.get().strip()
        try:
            days    = max(1, min(30, int(self._sched_interval_var.get())))
            next_dt = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0,
            ) + timedelta(days=days)
            return next_dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    # ── Panel C: Results Viewer ───────────────────────────────────────────────

    def _build_panel_results(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=BG, corner_radius=0)
        self._panel_frames["results"] = frame
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        # Fixed header
        header = ctk.CTkFrame(frame, fg_color=BG2, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            header, text="Audit Run History", font=FONT_TITLE,
            text_color=GOLD, anchor="w",
        ).pack(side="left", padx=14, pady=10)
        ctk.CTkButton(
            header, text="Refresh", width=80, height=BTN_HEIGHT,
            font=FONT_SM, fg_color=BG3, command=self._refresh_results,
        ).pack(side="right", padx=10, pady=8)

        # Scrollable results list
        self._results_scroll = ctk.CTkScrollableFrame(
            frame, fg_color=BG, corner_radius=0,
        )
        self._results_scroll.grid(row=1, column=0, sticky="nsew")
        self._results_scroll.grid_columnconfigure(0, weight=1)

    def _refresh_results(self):
        for w in self._results_scroll.winfo_children():
            w.destroy()

        eng = self._get_engine()
        if not eng:
            ctk.CTkLabel(self._results_scroll, text="Engine unavailable.",
                         font=FONT_SM, text_color=DIM).pack(pady=24)
            return

        try:
            runs = eng.get_run_history(limit=20)
        except Exception:
            runs = []

        if not runs:
            ctk.CTkLabel(self._results_scroll, text="No audit runs yet.",
                         font=FONT_SM, text_color=DIM).pack(pady=24)
            return

        for run in runs:
            self._build_result_row(self._results_scroll, run)

    def _build_result_row(self, parent, run: dict):
        ts      = run.get("created_at", "")
        count   = run.get("prompt_count", 0)
        tokens  = run.get("total_tokens", 0)
        cost    = run.get("total_cost", 0.0)
        status  = run.get("status", "done")

        card = ctk.CTkFrame(parent, fg_color=BG2, corner_radius=CARD_RADIUS)
        card.pack(fill="x", padx=8, pady=3)

        # Summary header
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=10, pady=6)

        ctk.CTkLabel(hdr, text=ts, font=FONT_STAT, text_color=DIM,
                     width=150, anchor="w").pack(side="left")
        ctk.CTkLabel(hdr, text=f"{count} prompts", font=FONT_SM, text_color=TEXT,
                     width=80, anchor="w").pack(side="left")
        ctk.CTkLabel(hdr, text=f"{tokens:,} tok", font=FONT_STAT, text_color=TEXT,
                     width=90, anchor="w").pack(side="left")
        ctk.CTkLabel(hdr, text=f"${cost:.4f}", font=FONT_STAT, text_color=GOLD,
                     width=70, anchor="w").pack(side="left")
        ctk.CTkLabel(
            hdr, text=status.upper(), font=FONT_XS,
            text_color=GREEN if status == "done" else RED,
            width=54, anchor="w",
        ).pack(side="left")

        # Export button
        ctk.CTkButton(
            hdr, text="Export", width=58, height=22, font=FONT_XS,
            fg_color=BG, hover_color=BG3,
            command=lambda r=run: self._export_run(r),
        ).pack(side="right", padx=2)

        # Expand/collapse button + detail frame
        detail_frame = ctk.CTkFrame(card, fg_color=BG3, corner_radius=4)
        _expanded    = [False]

        expand_btn = ctk.CTkButton(
            hdr, text="▸ Expand", width=76, height=22, font=FONT_XS,
            fg_color=BG, hover_color=BG3,
        )
        expand_btn.pack(side="right", padx=2)

        def _toggle(df=detail_frame, btn=expand_btn, r=run):
            if _expanded[0]:
                df.pack_forget()
                btn.configure(text="▸ Expand")
                _expanded[0] = False
            else:
                self._populate_detail(df, r)
                df.pack(fill="x", padx=8, pady=(0, 8))
                btn.configure(text="▾ Collapse")
                _expanded[0] = True

        expand_btn.configure(command=_toggle)

    def _populate_detail(self, frame: ctk.CTkFrame, run: dict):
        for w in frame.winfo_children():
            w.destroy()

        try:
            raw = run.get("results_json", "[]")
            results = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            results = []

        if not results:
            ctk.CTkLabel(frame, text="No per-prompt detail available.",
                         font=FONT_SM, text_color=DIM).pack(padx=8, pady=8)
            return

        for i, r in enumerate(results):
            p_frame = ctk.CTkFrame(frame, fg_color=BG2, corner_radius=4)
            p_frame.pack(fill="x", padx=6, pady=3)

            preview = (r.get("prompt", "") or "")[:120]
            status  = r.get("status", "?")
            ctk.CTkLabel(
                p_frame, text=f"[{i + 1}] {preview}",
                font=FONT_SM, text_color=TEXT, anchor="w", wraplength=520,
            ).pack(fill="x", padx=8, pady=(6, 2))

            meta = (
                f"Model: {r.get('model', '?')}  |  "
                f"Tokens: {r.get('input_tokens', 0)}+{r.get('output_tokens', 0)}  |  "
                f"Cost: ${r.get('cost', 0):.4f}  |  "
                f"Status: {status}"
            )
            ctk.CTkLabel(p_frame, text=meta, font=FONT_XS, text_color=DIM,
                         anchor="w").pack(fill="x", padx=8, pady=(0, 2))

            response = r.get("response") or r.get("error", "")
            if response:
                resp_box = ctk.CTkTextbox(p_frame, height=80, font=FONT_STAT,
                                          fg_color=BG)
                resp_box.pack(fill="x", padx=8, pady=(2, 8))
                resp_box.insert("1.0", response[:1200])
                resp_box.configure(state="disabled")

    def _export_run(self, run: dict):
        try:
            ts      = run.get("created_at", "") or datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_ts = ts.replace(":", "").replace(" ", "_").replace("-", "")
            out_dir = KNOWLEDGE_DIR / "audit_results"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"audit_results_{safe_ts}.md"

            try:
                raw     = run.get("results_json", "[]")
                results = json.loads(raw) if isinstance(raw, str) else (raw or [])
            except Exception:
                results = []

            lines = [
                f"# Audit Run — {run.get('created_at', 'unknown')}",
                "",
                f"- **Prompts:** {run.get('prompt_count', 0)}",
                f"- **Total tokens:** {run.get('total_tokens', 0):,}",
                f"- **Total cost:** ${run.get('total_cost', 0.0):.4f}",
                f"- **Status:** {run.get('status', 'done')}",
                "",
                "---",
                "",
            ]
            for i, r in enumerate(results):
                prompt   = (r.get("prompt", "") or "").replace("\n", "\n> ")
                response = r.get("response") or r.get("error", "")
                lines += [
                    f"## Prompt {i + 1}",
                    (
                        f"**Model:** {r.get('model', '?')}  |  "
                        f"**Tokens:** {r.get('input_tokens', 0)}+{r.get('output_tokens', 0)}  |  "
                        f"**Cost:** ${r.get('cost', 0):.4f}  |  "
                        f"**Status:** {r.get('status', '?')}"
                    ),
                    "",
                    "**Prompt:**",
                    f"> {prompt}",
                    "",
                    "**Response:**",
                    "```",
                    response,
                    "```",
                    "",
                ]
            out_path.write_text("\n".join(lines), encoding="utf-8")

            # Confirmation dialog
            try:
                dlg = ctk.CTkToplevel(self._content)
                dlg.title("Exported")
                dlg.geometry("420x120")
                dlg.configure(fg_color=BG)
                ctk.CTkLabel(dlg, text=f"Saved to:\n{out_path}",
                             font=FONT_SM, text_color=TEXT,
                             wraplength=380).pack(pady=20)
                ctk.CTkButton(dlg, text="OK", width=80,
                              fg_color=ACCENT, command=dlg.destroy).pack()
            except Exception:
                pass

        except Exception:
            pass

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_refresh(self):
        pass  # No periodic polling needed

    def on_close(self):
        self._run_active = False
