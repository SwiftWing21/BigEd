"""
Manual Mode Module — Operator-driven Claude Code compliance audit UI.

Panels:
  1. Queue Builder   — add/edit/reorder prompt items
  2. Run Controls    — execute queue with HITL gate + VS Code launch
  3. Results Viewer  — per-item output, cost breakdown, open audit MD
"""
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox

logger = logging.getLogger(__name__)

# Dynamic FLEET_DIR
try:
    import launcher as _launcher
    FLEET_DIR = Path(_launcher.FLEET_DIR)
except ImportError:
    FLEET_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fleet"
FLEET_TOML = FLEET_DIR / "fleet.toml"
LOGS_DIR = FLEET_DIR / "logs"
CONFIG_AUDIT_LOG = LOGS_DIR / "config_audit.log"

LABEL = "Manual Mode"

# Theme imports
try:
    from ui.theme import (BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
                          GREEN, ORANGE, RED, FONT, FONT_SM, FONT_BOLD,
                          FONT_TITLE, FONT_STAT, FONT_XS, CARD_RADIUS)
except ImportError:
    BG, BG2, BG3 = "#1a1a1a", "#242424", "#2d2d2d"
    ACCENT, ACCENT_H, GOLD = "#b22222", "#8b0000", "#c8a84b"
    TEXT, DIM, GREEN, ORANGE, RED = "#e2e2e2", "#888888", "#4caf50", "#ff9800", "#f44336"
    FONT = ("Segoe UI", 11)
    FONT_SM = ("Segoe UI", 10)
    FONT_BOLD = ("Segoe UI", 11, "bold")
    FONT_TITLE = ("Segoe UI", 14, "bold")
    FONT_STAT = ("Consolas", 10)
    FONT_XS = ("Consolas", 8)
    CARD_RADIUS = 8


# ── Config Audit Logging ──────────────────────────────────────────────────────

def _log_config_change(section: str, key: str, old_value, new_value) -> None:
    """Append a config-change audit line to fleet/logs/config_audit.log."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} [MANUAL_MODE] {section}.{key}: {old_value!r} → {new_value!r}\n"
    try:
        with open(CONFIG_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as exc:
        logger.warning("[MANUAL MODE] Could not write config_audit.log: %s", exc)


def _save_toml_key(section: str, key: str, value) -> None:
    """Write a single key into fleet.toml [section] using tomlkit (preserves comments)."""
    try:
        import tomlkit
        text = FLEET_TOML.read_text(encoding="utf-8")
        doc = tomlkit.parse(text)
        sec = doc.setdefault(section, tomlkit.table())
        old_value = sec.get(key)
        sec[key] = value
        FLEET_TOML.write_text(tomlkit.dumps(doc), encoding="utf-8")
        _log_config_change(section, key, old_value, value)
    except Exception as exc:
        logger.error("[MANUAL MODE] toml write failed for %s.%s: %s", section, key, exc)


def _load_manual_mode_config() -> dict:
    """Read [manual_mode] section from fleet.toml."""
    try:
        import tomlkit
        doc = tomlkit.parse(FLEET_TOML.read_text(encoding="utf-8"))
        return dict(doc.get("manual_mode", {}))
    except Exception:
        return {}


# ── Approval Dialog ───────────────────────────────────────────────────────────

class _ApprovalDialog(ctk.CTkToplevel):
    """Confirmation dialog shown when estimated token usage increases >threshold."""

    def __init__(self, parent, estimated_tokens: int, last_tokens: int, increase_pct: float):
        super().__init__(parent)
        self.title("Token Budget Approval Required")
        self.resizable(False, False)
        self.grab_set()
        self.result = False  # True = proceed, False = cancel

        pct_str = f"{increase_pct * 100:.1f}%"
        msg = (
            f"This run will use approximately {estimated_tokens:,} tokens\n"
            f"(+{pct_str} vs last run: {last_tokens:,} tokens).\n\n"
            f"Proceed with this audit run?"
        )

        ctk.CTkLabel(self, text="Token Budget Warning", font=FONT_TITLE,
                     text_color=ORANGE).pack(padx=20, pady=(20, 8))
        ctk.CTkLabel(self, text=msg, font=FONT, justify="center",
                     wraplength=360).pack(padx=24, pady=(0, 16))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(padx=24, pady=(0, 20))

        ctk.CTkButton(
            btn_row, text="Proceed", fg_color=ACCENT, hover_color=ACCENT_H,
            font=FONT_BOLD, command=self._proceed
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row, text="Cancel", fg_color=BG3, font=FONT,
            command=self._cancel
        ).pack(side="left")

    def _proceed(self):
        self.result = True
        self.destroy()

    def _cancel(self):
        self.result = False
        self.destroy()


# ── Main Module ───────────────────────────────────────────────────────────────

class Module:
    """Manual Mode module — operator-driven Claude compliance audit."""

    LABEL = "Manual Mode"
    VERSION = "0.052"
    DEFAULT_ENABLED = True
    DEPENDS_ON = []

    def __init__(self, app):
        self._app = app
        self._queue_items: list[dict] = []  # [{prompt, skill, max_tokens, repeat}]
        self._last_results: dict = {}
        self._running = False
        self._cfg: dict = _load_manual_mode_config()
        # Engine instance — created on first run, reused across runs
        self._engine = None

    def _get_engine(self):
        """Return (and lazily create) the ManualModeEngine instance."""
        if self._engine is None:
            import sys
            sys.path.insert(0, str(FLEET_DIR))
            from manual_mode import ManualModeEngine
            self._engine = ManualModeEngine()
        return self._engine

    # ── Tab construction ──────────────────────────────────────────────────────

    def build_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(parent, fg_color=BG2, corner_radius=CARD_RADIUS)
        hdr.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text="Manual Mode — Compliance Audit",
                     font=FONT_TITLE, text_color=GOLD).grid(
            row=0, column=0, sticky="w", padx=16, pady=10)

        # Notebook-style inner tabs using CTkTabview
        tabview = ctk.CTkTabview(parent, fg_color=BG2, segmented_button_fg_color=BG3,
                                 segmented_button_selected_color=ACCENT,
                                 segmented_button_unselected_color=BG3,
                                 text_color=TEXT, corner_radius=CARD_RADIUS)
        tabview.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        tabview.add("Queue Builder")
        tabview.add("Run Controls")
        tabview.add("Results")

        self._build_queue_tab(tabview.tab("Queue Builder"))
        self._build_run_tab(tabview.tab("Run Controls"))
        self._build_results_tab(tabview.tab("Results"))

    # ── Queue Builder tab ─────────────────────────────────────────────────────

    def _build_queue_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        # Input row
        input_row = ctk.CTkFrame(parent, fg_color="transparent")
        input_row.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        input_row.grid_columnconfigure(0, weight=1)

        self._prompt_entry = ctk.CTkEntry(
            input_row, placeholder_text="Enter audit prompt…",
            font=FONT, fg_color=BG3)
        self._prompt_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        ctk.CTkLabel(input_row, text="Skill:", font=FONT_SM).grid(
            row=0, column=1, padx=(0, 4))
        self._skill_var = ctk.StringVar(value="code_review")
        ctk.CTkOptionMenu(
            input_row,
            values=["code_review", "security_audit", "code_quality", "summarize"],
            variable=self._skill_var,
            font=FONT_SM, fg_color=BG3, button_color=BG3
        ).grid(row=0, column=2, padx=(0, 6))

        ctk.CTkLabel(input_row, text="Max tokens:", font=FONT_SM).grid(
            row=0, column=3, padx=(0, 4))
        self._max_tokens_entry = ctk.CTkEntry(
            input_row, width=70, placeholder_text="1024", font=FONT_SM, fg_color=BG3)
        self._max_tokens_entry.grid(row=0, column=4, padx=(0, 6))
        self._max_tokens_entry.insert(0, "1024")

        ctk.CTkButton(
            input_row, text="Add", width=60, font=FONT_BOLD,
            fg_color=ACCENT, hover_color=ACCENT_H, command=self._add_item
        ).grid(row=0, column=5)

        # Queue listbox
        list_frame = ctk.CTkScrollableFrame(parent, fg_color=BG3, label_text="Queue",
                                             label_font=FONT_BOLD, label_text_color=TEXT,
                                             corner_radius=CARD_RADIUS)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        list_frame.grid_columnconfigure(0, weight=1)
        self._list_frame = list_frame
        self._item_rows: list[ctk.CTkFrame] = []

        # Remove selected
        ctk.CTkButton(
            parent, text="Remove Selected", font=FONT_SM,
            fg_color=BG3, hover_color=RED, command=self._remove_selected
        ).grid(row=2, column=0, sticky="e", padx=8, pady=(0, 8))

    def _add_item(self) -> None:
        prompt = self._prompt_entry.get().strip()
        if not prompt:
            return
        try:
            max_tokens = int(self._max_tokens_entry.get().strip() or "1024")
        except ValueError:
            max_tokens = 1024
        item = {
            "prompt": prompt,
            "skill": self._skill_var.get(),
            "max_tokens": max_tokens,
            "repeat": 1,
        }
        self._queue_items.append(item)
        self._prompt_entry.delete(0, "end")
        self._refresh_item_list()

    def _refresh_item_list(self) -> None:
        for row in self._item_rows:
            row.destroy()
        self._item_rows.clear()
        for i, item in enumerate(self._queue_items):
            row = ctk.CTkFrame(self._list_frame, fg_color=BG2, corner_radius=4)
            row.grid(row=i, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                row,
                text=f"[{item['skill']}] {item['prompt'][:70]}  ({item['max_tokens']} tok)",
                font=FONT_SM, text_color=TEXT, anchor="w"
            ).grid(row=0, column=0, sticky="w", padx=8, pady=4)
            self._item_rows.append(row)

    def _remove_selected(self) -> None:
        # Remove last item for simplicity — full listbox selection requires tk.Listbox
        if self._queue_items:
            self._queue_items.pop()
            self._refresh_item_list()

    # ── Run Controls tab ──────────────────────────────────────────────────────

    def _build_run_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)

        # Threshold config
        cfg_card = ctk.CTkFrame(parent, fg_color=BG3, corner_radius=CARD_RADIUS)
        cfg_card.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        cfg_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(cfg_card, text="Approval threshold (%):", font=FONT_BOLD).grid(
            row=0, column=0, sticky="w", padx=12, pady=8)

        self._threshold_var = ctk.StringVar(
            value=str(int(float(self._cfg.get("approval_required_threshold", 0.20)) * 100))
        )
        threshold_entry = ctk.CTkEntry(
            cfg_card, textvariable=self._threshold_var,
            width=60, font=FONT, fg_color=BG2)
        threshold_entry.grid(row=0, column=1, sticky="w", padx=(0, 12), pady=8)

        ctk.CTkButton(
            cfg_card, text="Save", width=60, font=FONT_SM,
            fg_color=ACCENT, hover_color=ACCENT_H, command=self._save_threshold
        ).grid(row=0, column=2, padx=(0, 12), pady=8)

        # Run / Stop / VS Code row
        action_row = ctk.CTkFrame(parent, fg_color="transparent")
        action_row.grid(row=1, column=0, sticky="ew", padx=8, pady=4)

        self._run_btn = ctk.CTkButton(
            action_row, text="▶  Run Audit Queue", font=FONT_BOLD,
            fg_color=GREEN, hover_color="#388e3c", command=self._run_queue_click,
            width=200
        )
        self._run_btn.pack(side="left", padx=(0, 8))

        # Gap 3: Stop button — visible only while a run is active
        self._stop_btn = ctk.CTkButton(
            action_row, text="⏹ Stop", font=FONT_BOLD,
            fg_color=RED, hover_color="#b71c1c", command=self._cancel_run,
            width=90
        )
        # Start hidden; shown when _running = True
        # (pack/forget used for show/hide since CTkButton has no visible= kwarg)

        ctk.CTkButton(
            action_row, text="Open in VS Code", font=FONT_SM,
            fg_color=BG3, hover_color=BG2, command=self._open_vscode
        ).pack(side="left")

        # Status label
        self._status_label = ctk.CTkLabel(
            parent, text="Ready.", font=FONT_SM, text_color=DIM)
        self._status_label.grid(row=2, column=0, sticky="w", padx=12, pady=(4, 0))

        # Progress bar (indeterminate)
        self._progress = ctk.CTkProgressBar(parent, mode="indeterminate",
                                             progress_color=ACCENT, fg_color=BG3)
        self._progress.grid(row=3, column=0, sticky="ew", padx=8, pady=4)
        self._progress.set(0)

    def _set_run_active(self, active: bool) -> None:
        """Toggle run/stop button visibility and disabled state together."""
        self._running = active
        if active:
            self._run_btn.configure(state="disabled")
            self._stop_btn.pack(side="left", padx=(0, 8), before=self._run_btn)
        else:
            self._run_btn.configure(state="normal")
            try:
                self._stop_btn.pack_forget()
            except Exception:
                pass

    def _cancel_run(self) -> None:
        """Signal the engine to stop after the current item."""
        engine = self._engine
        if engine is not None:
            engine.cancel_run()
        self._set_status("Cancelling — finishing current item…", color=ORANGE)
        self._stop_btn.configure(state="disabled")

    def _save_threshold(self) -> None:
        try:
            pct = float(self._threshold_var.get())
            val = pct / 100.0
            _save_toml_key("manual_mode", "approval_required_threshold", val)
            self._cfg["approval_required_threshold"] = val
            self._set_status(f"Threshold saved: {pct:.0f}%", color=GREEN)
        except ValueError:
            self._set_status("Invalid threshold value.", color=RED)

    def _run_queue_click(self) -> None:
        if self._running:
            return
        if not self._queue_items:
            self._set_status("Queue is empty — add prompts first.", color=ORANGE)
            return
        engine = self._get_engine()
        engine.reset_cancel()
        self._set_run_active(True)
        self._stop_btn.configure(state="normal")
        self._progress.start()
        self._set_status("Running audit queue…")
        threading.Thread(target=self._run_queue_thread, daemon=True).start()

    def _run_queue_thread(self) -> None:
        try:
            engine = self._get_engine()
            result = engine.run_queue(list(self._queue_items))

            if result.get("status") == "approval_required":
                # Must show dialog on main thread
                self._app.after(0, lambda r=result: self._show_approval_dialog(r))
                return

            self._last_results = result
            self._app.after(0, self._on_run_complete)
        except Exception as exc:
            logger.exception("[MANUAL MODE] run_queue_thread error")
            self._app.after(0, lambda e=exc: self._on_run_error(e))

    def _show_approval_dialog(self, result: dict) -> None:
        """Called on main thread to show the HITL approval dialog."""
        self._set_run_active(False)
        self._progress.stop()
        self._progress.set(0)

        dlg = _ApprovalDialog(
            self._app,
            estimated_tokens=result["estimated_tokens"],
            last_tokens=result["last_tokens"],
            increase_pct=result["increase_pct"],
        )
        self._app.wait_window(dlg)

        if dlg.result:
            # User approved — bypass threshold for this run via a patched engine
            # by setting approval_required_threshold=1.0 temporarily in config
            try:
                import tomlkit
                doc = tomlkit.parse(FLEET_TOML.read_text(encoding="utf-8"))
                doc.setdefault("manual_mode", tomlkit.table())["approval_required_threshold"] = 1.0
                FLEET_TOML.write_text(tomlkit.dumps(doc), encoding="utf-8")
            except Exception:
                pass
            engine = self._get_engine()
            engine.reset_cancel()
            self._set_run_active(True)
            self._stop_btn.configure(state="normal")
            self._progress.start()
            self._set_status("Running (approved)…")
            threading.Thread(
                target=self._run_approved_thread,
                daemon=True
            ).start()
        else:
            self._set_status("Run cancelled by operator.", color=ORANGE)

    def _run_approved_thread(self) -> None:
        try:
            engine = self._get_engine()
            result = engine.run_queue(list(self._queue_items))
            self._last_results = result
            self._app.after(0, self._on_run_complete)
        except Exception as exc:
            self._app.after(0, lambda e=exc: self._on_run_error(e))

    def _on_run_complete(self) -> None:
        self._set_run_active(False)
        self._progress.stop()
        self._progress.set(1)
        r = self._last_results
        tok = r.get("total_tokens", 0)
        cost = r.get("total_cost", r.get("total_cost_usd", 0.0))
        anomaly = r.get("anomaly_detected", False)
        md = r.get("audit_md_path", "")
        cancelled = self._engine is not None and self._engine._cancel_event.is_set()
        status_msg = (
            ("Cancelled. " if cancelled else "Done. ")
            + f"{tok:,} tokens · ${cost:.4f}"
            + ("  ⚠ Cost anomaly!" if anomaly else "")
            + (f"  → {Path(md).name}" if md else "")
        )
        self._set_status(status_msg, color=ORANGE if (anomaly or cancelled) else GREEN)
        self._refresh_results()

    def _on_run_error(self, exc: Exception) -> None:
        self._set_run_active(False)
        self._progress.stop()
        self._progress.set(0)
        self._set_status(f"Error: {exc}", color=RED)

    def _open_vscode(self) -> None:
        import sys
        sys.path.insert(0, str(FLEET_DIR))
        from manual_mode import launch_vscode
        launched = launch_vscode(str(FLEET_DIR.parent))
        if launched:
            self._set_status("VS Code launched.", color=GREEN)
        else:
            self._set_status("VS Code not found — install it and ensure 'code' is on PATH.", color=ORANGE)

    def _set_status(self, msg: str, color: str = "") -> None:
        color = color or DIM
        try:
            self._status_label.configure(text=msg, text_color=color)
        except Exception:
            pass

    # ── Results tab ───────────────────────────────────────────────────────────

    def _build_results_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        self._results_scroll = ctk.CTkScrollableFrame(
            parent, fg_color=BG3, label_text="Audit Results",
            label_font=FONT_BOLD, label_text_color=TEXT, corner_radius=CARD_RADIUS)
        self._results_scroll.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._results_scroll.grid_columnconfigure(0, weight=1)
        self._result_item_frames: list[ctk.CTkFrame] = []

        # Open MD button
        self._open_md_btn = ctk.CTkButton(
            parent, text="Open Audit MD", font=FONT_SM,
            fg_color=BG3, hover_color=BG2, command=self._open_audit_md
        )
        self._open_md_btn.grid(row=1, column=0, sticky="e", padx=8, pady=(0, 8))

    def _refresh_results(self) -> None:
        for f in self._result_item_frames:
            f.destroy()
        self._result_item_frames.clear()

        items = self._last_results.get("results", self._last_results.get("items", []))
        for i, item in enumerate(items):
            card = ctk.CTkFrame(self._results_scroll, fg_color=BG2, corner_radius=4)
            card.grid(row=i, column=0, sticky="ew", pady=3)
            card.grid_columnconfigure(0, weight=1)

            status = item.get("status", "?")
            status_color = GREEN if status in ("ok", "done") else RED
            prompt_short = item.get("prompt", "")[:60]

            ctk.CTkLabel(
                card,
                text=f"[{status.upper()}] {prompt_short}",
                font=FONT_BOLD, text_color=status_color, anchor="w"
            ).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 2))

            tok = item.get("tokens_used", 0) or (
                item.get("input_tokens", 0) + item.get("output_tokens", 0)
            )
            cost = item.get("cost_usd", item.get("cost", 0.0))
            ctk.CTkLabel(
                card,
                text=f"Tokens: {tok:,}  Cost: ${cost:.4f}",
                font=FONT_SM, text_color=DIM, anchor="w"
            ).grid(row=1, column=0, sticky="w", padx=10, pady=(0, 4))

            output_preview = str(item.get("output", item.get("response", "")))[:200]
            if output_preview:
                ctk.CTkLabel(
                    card,
                    text=output_preview,
                    font=FONT_XS, text_color=DIM, anchor="w", wraplength=600
                ).grid(row=2, column=0, sticky="w", padx=10, pady=(0, 6))

            self._result_item_frames.append(card)

    def _open_audit_md(self) -> None:
        md_path = self._last_results.get("audit_md_path")
        if not md_path or not Path(md_path).exists():
            self._set_status("No audit MD from last run.", color=ORANGE)
            return
        import subprocess, sys
        try:
            if sys.platform == "win32":
                os.startfile(md_path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", md_path])
        except Exception as exc:
            self._set_status(f"Could not open MD: {exc}", color=RED)
