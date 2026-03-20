"""
Intelligence Module — System transparency, model controls, prompt queue, and evaluation display.

Panels:
  1. System Overview    — what BigEd CC can do, how skills/weights work
  2. Model Settings     — active model, keep_alive, tier preferences
  3. Prompt Queue       — unattended round-robin prompt list
  4. Evaluation Display — how Claude/Gemini evaluation routines score outputs
  5. Cost Intelligence  — live spend, optimization recommendations
"""
import json
import os
import threading
import time
from pathlib import Path

import customtkinter as ctk

FLEET_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fleet"
FLEET_TOML = FLEET_DIR / "fleet.toml"

LABEL = "Intelligence"

# Theme imports
try:
    from ui.theme import (BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
                          GREEN, ORANGE, RED, FONT, FONT_SM, FONT_BOLD, FONT_TITLE,
                          FONT_STAT, FONT_XS, CARD_RADIUS)
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


class Module:
    """Intelligence module — system transparency + model controls + prompt queue."""

    LABEL = "Intelligence"

    def __init__(self, app):
        self._app = app
        self._prompt_queue = []
        self._queue_running = False
        self._queue_loop_count = 0
        self._queue_max_loops = 1

    def build_tab(self, parent):
        """Build the Intelligence tab UI."""
        scroll = ctk.CTkScrollableFrame(parent, fg_color=BG, corner_radius=0)
        scroll.pack(fill="both", expand=True)
        scroll.grid_columnconfigure(0, weight=1)

        self._build_overview(scroll)
        self._build_model_settings(scroll)
        self._build_prompt_queue(scroll)
        self._build_evaluation(scroll)
        self._build_cost_panel(scroll)

    def _card(self, parent, title):
        """Create a styled card frame with title."""
        card = ctk.CTkFrame(parent, fg_color=BG2, corner_radius=CARD_RADIUS)
        card.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(card, text=title, font=FONT_TITLE, text_color=GOLD,
                     anchor="w").pack(fill="x", padx=12, pady=(10, 4))
        return card

    # ── Panel 1: System Overview ─────────────────────────────────────────────

    def _build_overview(self, parent):
        card = self._card(parent, "System Capabilities")

        capabilities = [
            ("74 Skills", "Code review, security audit, web search, RAG indexing, ML training, and more"),
            ("Dynamic Scaling", "4 core agents + on-demand scaling based on task queue depth"),
            ("Model Tiers", "qwen3:8b (default) → 4b (mid) → 1.7b (low) → 0.6b (critical/failsafe)"),
            ("HA Fallback", "Claude → Gemini → Local Ollama with circuit breaker (3 failures → 60s cooldown)"),
            ("Intelligence Scoring", "Tier 1 heuristic + Tier 2 LLM quality eval → blended IQ score per task"),
            ("Idle Evolution", "Agents self-improve when idle: code_quality, benchmark, skill_evolve"),
            ("Cost Tracking", "Per-call token/cost tracking, budget enforcement, provider comparison"),
        ]

        for title, desc in capabilities:
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=2)
            ctk.CTkLabel(row, text=f"  {title}", font=FONT_BOLD, text_color=TEXT,
                         anchor="w", width=160).pack(side="left")
            ctk.CTkLabel(row, text=desc, font=FONT_SM, text_color=DIM,
                         anchor="w", wraplength=500).pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(card, text="", font=FONT_XS).pack(pady=(0, 6))

    # ── Panel 2: Model Settings ──────────────────────────────────────────────

    def _build_model_settings(self, parent):
        card = self._card(parent, "Model Settings")

        config = self._load_config()
        models = config.get("models", {})
        tiers = config.get("models", {}).get("tiers", {})

        # Current model
        settings_grid = ctk.CTkFrame(card, fg_color="transparent")
        settings_grid.pack(fill="x", padx=12, pady=4)
        settings_grid.grid_columnconfigure(1, weight=1)

        fields = [
            ("Active GPU Model", models.get("local", "qwen3:8b")),
            ("Conductor (CPU)", models.get("conductor_model", "qwen3:4b")),
            ("Keep Alive", f"{models.get('keep_alive_mins', 30)} minutes"),
            ("Tier Default", tiers.get("default", "qwen3:8b")),
            ("Tier Low", tiers.get("low", "qwen3:1.7b")),
            ("Tier Critical", tiers.get("critical", "qwen3:0.6b")),
        ]

        for i, (label, value) in enumerate(fields):
            ctk.CTkLabel(settings_grid, text=label, font=FONT_SM, text_color=DIM,
                         anchor="w").grid(row=i, column=0, padx=(0, 12), pady=2, sticky="w")
            ctk.CTkLabel(settings_grid, text=value, font=FONT_STAT, text_color=TEXT,
                         anchor="w").grid(row=i, column=1, pady=2, sticky="w")

        # Weight adjustment
        ctk.CTkLabel(card, text="Skill Complexity Routing", font=FONT_BOLD,
                     text_color=GOLD, anchor="w").pack(fill="x", padx=12, pady=(8, 2))
        ctk.CTkLabel(card, text="Simple tasks → Haiku ($0.80/M)  |  Standard → Sonnet ($3/M)  |  Complex → Opus ($15/M)",
                     font=FONT_XS, text_color=DIM, anchor="w").pack(fill="x", padx=16, pady=(0, 8))

    # ── Panel 3: Prompt Queue ────────────────────────────────────────────────

    def _build_prompt_queue(self, parent):
        card = self._card(parent, "Prompt Queue (Unattended)")

        ctk.CTkLabel(card, text="Add prompts for round-robin unattended execution. "
                     "Agents will process each prompt in sequence.",
                     font=FONT_SM, text_color=DIM, wraplength=600,
                     anchor="w").pack(fill="x", padx=12, pady=(0, 6))

        # Input row
        input_row = ctk.CTkFrame(card, fg_color="transparent")
        input_row.pack(fill="x", padx=12, pady=2)
        input_row.grid_columnconfigure(0, weight=1)

        self._prompt_entry = ctk.CTkEntry(input_row, font=FONT, fg_color=BG,
                                          placeholder_text="Enter a prompt...")
        self._prompt_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        ctk.CTkButton(input_row, text="Add", width=60, height=28, font=FONT_SM,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self._add_prompt).grid(row=0, column=1, padx=2)

        # Queue list
        self._queue_frame = ctk.CTkFrame(card, fg_color=BG3, corner_radius=4)
        self._queue_frame.pack(fill="x", padx=12, pady=4)
        self._queue_label = ctk.CTkLabel(self._queue_frame, text="Queue empty",
                                         font=FONT_SM, text_color=DIM)
        self._queue_label.pack(padx=8, pady=8)

        # Controls
        ctrl_row = ctk.CTkFrame(card, fg_color="transparent")
        ctrl_row.pack(fill="x", padx=12, pady=(2, 8))

        ctk.CTkLabel(ctrl_row, text="Loops:", font=FONT_SM, text_color=DIM).pack(side="left")
        self._loop_var = ctk.StringVar(value="1")
        ctk.CTkEntry(ctrl_row, textvariable=self._loop_var, width=40, height=28,
                     font=FONT_SM, fg_color=BG).pack(side="left", padx=4)
        ctk.CTkLabel(ctrl_row, text="(0 = infinite)", font=FONT_XS, text_color=DIM).pack(side="left", padx=4)

        self._start_btn = ctk.CTkButton(ctrl_row, text="Start Queue", width=100, height=28,
                                        font=FONT_SM, fg_color="#1e3a1e", hover_color="#2a4a2a",
                                        command=self._start_queue)
        self._start_btn.pack(side="right")

        self._stop_btn = ctk.CTkButton(ctrl_row, text="Stop", width=60, height=28,
                                       font=FONT_SM, fg_color="#5a2020", hover_color="#6a2828",
                                       command=self._stop_queue, state="disabled")
        self._stop_btn.pack(side="right", padx=4)

        self._queue_status = ctk.CTkLabel(ctrl_row, text="", font=FONT_XS, text_color=DIM)
        self._queue_status.pack(side="right", padx=8)

    def _add_prompt(self):
        text = self._prompt_entry.get().strip()
        if not text:
            return
        self._prompt_queue.append(text)
        self._prompt_entry.delete(0, "end")
        self._refresh_queue_display()

    def _refresh_queue_display(self):
        for w in self._queue_frame.winfo_children():
            w.destroy()
        if not self._prompt_queue:
            ctk.CTkLabel(self._queue_frame, text="Queue empty",
                         font=FONT_SM, text_color=DIM).pack(padx=8, pady=8)
            return
        for i, prompt in enumerate(self._prompt_queue):
            row = ctk.CTkFrame(self._queue_frame, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=1)
            ctk.CTkLabel(row, text=f"{i+1}.", font=FONT_XS, text_color=DIM,
                         width=20).pack(side="left")
            ctk.CTkLabel(row, text=prompt[:80], font=FONT_SM, text_color=TEXT,
                         anchor="w").pack(side="left", fill="x", expand=True)
            ctk.CTkButton(row, text="x", width=20, height=20, font=FONT_XS,
                          fg_color=BG, hover_color="#5a2020",
                          command=lambda idx=i: self._remove_prompt(idx)).pack(side="right")

    def _remove_prompt(self, idx):
        if 0 <= idx < len(self._prompt_queue):
            self._prompt_queue.pop(idx)
            self._refresh_queue_display()

    def _start_queue(self):
        if not self._prompt_queue:
            return
        try:
            loops = int(self._loop_var.get())
        except ValueError:
            loops = 1
        self._queue_max_loops = loops
        self._queue_loop_count = 0
        self._queue_running = True
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        threading.Thread(target=self._run_queue, daemon=True).start()

    def _stop_queue(self):
        self._queue_running = False
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._queue_status.configure(text="Stopped")

    def _run_queue(self):
        """Background thread: dispatch prompts round-robin."""
        import sys
        sys.path.insert(0, str(FLEET_DIR))
        try:
            import db
        except ImportError:
            self._queue_running = False
            return

        idx = 0
        while self._queue_running:
            if not self._prompt_queue:
                break
            prompt = self._prompt_queue[idx % len(self._prompt_queue)]
            # Dispatch as a task
            try:
                db.post_task("summarize", json.dumps({"prompt": prompt}))
                status = f"Loop {self._queue_loop_count+1}, prompt {idx+1}/{len(self._prompt_queue)}"
                try:
                    self._queue_status.configure(text=status)
                except Exception:
                    pass
            except Exception:
                pass

            idx += 1
            if idx % len(self._prompt_queue) == 0:
                self._queue_loop_count += 1
                if self._queue_max_loops > 0 and self._queue_loop_count >= self._queue_max_loops:
                    break

            time.sleep(2)  # Pace between dispatches

        self._queue_running = False
        try:
            self._start_btn.configure(state="normal")
            self._stop_btn.configure(state="disabled")
            self._queue_status.configure(text=f"Complete ({self._queue_loop_count} loops)")
        except Exception:
            pass

    # ── Panel 4: Evaluation Display ──────────────────────────────────────────

    def _build_evaluation(self, parent):
        card = self._card(parent, "Quality Evaluation System")

        evals = [
            ("Tier 1: Heuristic", "Fast, rule-based scoring on every task output",
             "Checks: output length, structure, code validity, error patterns. "
             "Score: 0.0-1.0. Cost: zero (local computation)."),
            ("Tier 2: LLM Review", "Sampled ~10% of tasks get LLM-based quality evaluation",
             "A second model judges output quality, relevance, and correctness. "
             "Score blended: 60% Tier1 + 40% Tier2. Uses cheapest available model."),
            ("Adversarial Review", "High-stakes tasks get multi-provider review",
             "code_write, security_audit, pen_test outputs are reviewed by a different "
             "provider (Claude reviews Gemini output, vice versa). Max 2 rounds."),
            ("IQ Score", "Per-agent rolling intelligence score (24h window)",
             "Average blended score across all scored tasks. Displayed as IQ in agent cards. "
             "Agents with consistently low IQ may be quarantined by the watchdog."),
        ]

        for title, subtitle, desc in evals:
            row = ctk.CTkFrame(card, fg_color=BG3, corner_radius=4)
            row.pack(fill="x", padx=12, pady=2)
            header = ctk.CTkFrame(row, fg_color="transparent")
            header.pack(fill="x", padx=8, pady=(6, 0))
            ctk.CTkLabel(header, text=title, font=FONT_BOLD, text_color=TEXT,
                         anchor="w").pack(side="left")
            ctk.CTkLabel(header, text=subtitle, font=FONT_XS, text_color=DIM,
                         anchor="e").pack(side="right")
            ctk.CTkLabel(row, text=desc, font=FONT_SM, text_color=DIM,
                         wraplength=550, anchor="w", justify="left"
                         ).pack(fill="x", padx=8, pady=(2, 6))

        ctk.CTkLabel(card, text="", font=FONT_XS).pack(pady=(0, 4))

    # ── Panel 5: Cost Intelligence ───────────────────────────────────────────

    def _build_cost_panel(self, parent):
        card = self._card(parent, "Cost Intelligence")

        rules = [
            ("Prompt Caching", "90% input reduction", "cache_control: {type: 'ephemeral'} on stable system prompts"),
            ("Local-First", "100% API savings", "Route non-critical tasks to Ollama qwen3:8b"),
            ("Model Routing", "73% savings", "Simple tasks → Haiku ($0.80/M) instead of Sonnet ($3/M)"),
            ("Batch API", "50% savings", "Message Batches for non-real-time bulk operations"),
            ("Context Pruning", "30-60% savings", "Keep last 3-5 messages, reference don't repeat"),
        ]

        for title, savings, desc in rules:
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=1)
            ctk.CTkLabel(row, text=title, font=FONT_SM, text_color=TEXT,
                         anchor="w", width=140).pack(side="left")
            ctk.CTkLabel(row, text=savings, font=FONT_STAT, text_color=GREEN,
                         anchor="w", width=120).pack(side="left")
            ctk.CTkLabel(row, text=desc, font=FONT_XS, text_color=DIM,
                         anchor="w").pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(card, text="", font=FONT_XS).pack(pady=(0, 6))

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        try:
            import tomllib
            with open(FLEET_TOML, "rb") as f:
                return tomllib.load(f)
        except Exception:
            return {}

    def on_refresh(self):
        pass  # Static panels — no periodic refresh needed

    def on_close(self):
        self._queue_running = False
