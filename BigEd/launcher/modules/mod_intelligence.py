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
import sys
import threading
import time
from pathlib import Path

import customtkinter as ctk

# Dynamic FLEET_DIR — works regardless of install location
try:
    import launcher as _launcher
    FLEET_DIR = Path(_launcher.FLEET_DIR)
except ImportError:
    FLEET_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fleet"
FLEET_TOML = FLEET_DIR / "fleet.toml"

# Launcher dir — anchored to this file, not cwd
_LAUNCHER_DIR = Path(__file__).resolve().parent.parent  # BigEd/launcher/
if str(_LAUNCHER_DIR) not in sys.path:
    sys.path.insert(0, str(_LAUNCHER_DIR))
if str(FLEET_DIR) not in sys.path:
    sys.path.insert(0, str(FLEET_DIR))
try:
    from data_access import FleetDB
except ImportError:
    FleetDB = None

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
    VERSION = "0.051"
    DEFAULT_ENABLED = True
    DEPENDS_ON = []

    def __init__(self, app):
        self._app = app
        self._prompt_queue = []  # list of {"prompt": str, "skill": str}
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

        # Edit controls
        edit_row = ctk.CTkFrame(card, fg_color="transparent")
        edit_row.pack(fill="x", padx=12, pady=(4, 8))

        ctk.CTkButton(edit_row, text="Edit Model Settings", font=FONT_SM,
                      width=140, height=28, fg_color=BG3, hover_color=BG2,
                      command=self._edit_model_settings).pack(side="left")

        self._model_edit_status = ctk.CTkLabel(edit_row, text="", font=FONT_XS, text_color=DIM)
        self._model_edit_status.pack(side="left", padx=8)

        # Weight adjustment
        ctk.CTkLabel(card, text="Skill Complexity Routing", font=FONT_BOLD,
                     text_color=GOLD, anchor="w").pack(fill="x", padx=12, pady=(8, 2))
        ctk.CTkLabel(card, text="Simple tasks → Haiku ($0.80/M)  |  Standard → Sonnet ($3/M)  |  Complex → Opus ($15/M)",
                     font=FONT_XS, text_color=DIM, anchor="w").pack(fill="x", padx=16, pady=(0, 8))

    def _edit_model_settings(self):
        """Open inline editor for model settings."""
        config = self._load_config()
        models = config.get("models", {})

        dlg = ctk.CTkToplevel(self._app)
        dlg.title("Model Settings")
        dlg.geometry("400x300")
        dlg.resizable(False, False)
        dlg.configure(fg_color=BG2)

        fields = {}
        entries = [
            ("local", models.get("local", "qwen3:8b"), "GPU model"),
            ("conductor_model", models.get("conductor_model", "qwen3:4b"), "CPU conductor"),
            ("keep_alive_mins", str(models.get("keep_alive_mins", 30)), "Keep alive (min)"),
        ]

        for i, (key, value, label) in enumerate(entries):
            ctk.CTkLabel(dlg, text=label, font=FONT_SM, text_color=DIM
                         ).grid(row=i, column=0, padx=12, pady=6, sticky="w")
            entry = ctk.CTkEntry(dlg, font=FONT, fg_color=BG, width=200)
            entry.insert(0, value)
            entry.grid(row=i, column=1, padx=8, pady=6)
            fields[key] = entry

        def save():
            try:
                import re
                toml_path = FLEET_TOML
                text = toml_path.read_text(encoding="utf-8")
                for key, entry in fields.items():
                    val = entry.get().strip()
                    # Update the value in fleet.toml
                    pattern = rf'^(\s*{re.escape(key)}\s*=\s*).*$'
                    if key == "keep_alive_mins":
                        text = re.sub(pattern, rf'\g<1>{val}', text, flags=re.MULTILINE)
                    else:
                        text = re.sub(pattern, rf'\g<1>"{val}"', text, flags=re.MULTILINE)
                toml_path.write_text(text, encoding="utf-8")
                self._model_edit_status.configure(text="Saved. Changes apply on next model load.", text_color="#4caf50")
                dlg.destroy()
            except Exception as e:
                self._model_edit_status.configure(text=f"Save failed: {e}", text_color="#f44336")

        ctk.CTkButton(dlg, text="Save", font=FONT_SM, width=80, height=28,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      command=save).grid(row=len(entries), column=1, pady=12, sticky="e", padx=8)

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

        # Skill type selector
        ctk.CTkLabel(input_row, text="Skill:", font=FONT_SM, text_color=DIM).grid(row=0, column=2, padx=(8, 2))
        self._queue_skill_var = ctk.StringVar(value="summarize")
        skill_menu = ctk.CTkOptionMenu(
            input_row, variable=self._queue_skill_var,
            values=["summarize", "code_review", "web_search", "code_quality",
                    "security_audit", "analyze_results", "benchmark", "research_loop"],
            font=FONT_SM, width=120, height=28,
            fg_color=BG3,
        )
        skill_menu.grid(row=0, column=3, padx=2)

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
        self._prompt_queue.append({"prompt": text, "skill": self._queue_skill_var.get()})
        self._prompt_entry.delete(0, "end")
        self._refresh_queue_display()

    def _refresh_queue_display(self):
        for w in self._queue_frame.winfo_children():
            w.destroy()
        if not self._prompt_queue:
            ctk.CTkLabel(self._queue_frame, text="Queue empty",
                         font=FONT_SM, text_color=DIM).pack(padx=8, pady=8)
            return
        for i, item in enumerate(self._prompt_queue):
            row = ctk.CTkFrame(self._queue_frame, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=1)
            ctk.CTkLabel(row, text=f"{i+1}.", font=FONT_XS, text_color=DIM,
                         width=20).pack(side="left")
            ctk.CTkLabel(row, text=f"[{item['skill']}]", font=FONT_XS, text_color=GOLD,
                         width=80).pack(side="left")
            ctk.CTkLabel(row, text=item['prompt'][:60], font=FONT_SM, text_color=TEXT,
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
        try:
            import db
        except ImportError:
            self._queue_running = False
            return

        idx = 0
        while self._queue_running:
            if not self._prompt_queue:
                break
            item = self._prompt_queue[idx % len(self._prompt_queue)]
            # Dispatch as a task
            try:
                db.post_task(item['skill'], json.dumps({"prompt": item['prompt']}))
                status = f"Loop {self._queue_loop_count+1}, [{item['skill']}] {idx+1}/{len(self._prompt_queue)}"
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

        # Live scoring display (0.051.03b)
        ctk.CTkLabel(card, text="Live Scoring Feed", font=FONT_BOLD,
                     text_color=GOLD, anchor="w").pack(fill="x", padx=12, pady=(8, 2))

        self._eval_feed_frame = ctk.CTkFrame(card, fg_color=BG3, corner_radius=4, height=120)
        self._eval_feed_frame.pack(fill="x", padx=12, pady=(2, 4))
        self._eval_feed_frame.pack_propagate(False)

        self._eval_feed_labels = []
        for i in range(5):
            lbl = ctk.CTkLabel(self._eval_feed_frame, text="", font=FONT_XS,
                               text_color=DIM, anchor="w")
            lbl.pack(fill="x", padx=8, pady=1)
            self._eval_feed_labels.append(lbl)

        refresh_row = ctk.CTkFrame(card, fg_color="transparent")
        refresh_row.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkButton(refresh_row, text="Refresh Scores", width=110, height=24,
                      font=FONT_XS, fg_color=BG3, hover_color=BG2,
                      command=self._refresh_eval_feed).pack(side="left")
        self._eval_feed_status = ctk.CTkLabel(refresh_row, text="Click to load recent scores",
                                               font=FONT_XS, text_color=DIM)
        self._eval_feed_status.pack(side="left", padx=8)

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

        # Skill routing weight display + adjustment UI (0.051.03b)
        ctk.CTkLabel(card, text="Skill Complexity Routing Weights", font=FONT_BOLD,
                     text_color=GOLD, anchor="w").pack(fill="x", padx=12, pady=(8, 2))
        ctk.CTkLabel(card, text="Drag skills between tiers to adjust routing cost. "
                     "Changes write back to providers.py SKILL_COMPLEXITY.",
                     font=FONT_XS, text_color=DIM, wraplength=600,
                     anchor="w").pack(fill="x", padx=16, pady=(0, 4))

        self._weight_tiers = {}
        tier_defs = [
            ("simple", "Haiku/Local", "$0.80/M or free", GREEN),
            ("medium", "Sonnet/8b", "$3.00/M or free", ORANGE),
            ("complex", "Opus", "$15.00/M", RED),
        ]

        # Load current SKILL_COMPLEXITY from providers.py
        current_tiers = self._load_skill_complexity()

        for tier_key, tier_label, cost, color in tier_defs:
            trow = ctk.CTkFrame(card, fg_color=BG3, corner_radius=4)
            trow.pack(fill="x", padx=12, pady=2)
            header = ctk.CTkFrame(trow, fg_color="transparent")
            header.pack(fill="x", padx=8, pady=(4, 0))
            ctk.CTkLabel(header, text=f"{tier_label}", font=FONT_BOLD,
                         text_color=color, anchor="w").pack(side="left")
            ctk.CTkLabel(header, text=cost, font=FONT_XS, text_color=DIM,
                         anchor="e").pack(side="right")

            skills_str = ", ".join(current_tiers.get(tier_key, []))
            skill_label = ctk.CTkLabel(trow, text=skills_str or "(none)",
                                       font=FONT_XS, text_color=DIM,
                                       wraplength=550, anchor="w", justify="left")
            skill_label.pack(fill="x", padx=8, pady=(2, 4))
            self._weight_tiers[tier_key] = skill_label

        # Move skill controls
        move_row = ctk.CTkFrame(card, fg_color="transparent")
        move_row.pack(fill="x", padx=12, pady=(4, 2))

        ctk.CTkLabel(move_row, text="Move skill:", font=FONT_SM, text_color=DIM).pack(side="left")
        self._move_skill_var = ctk.StringVar(value="")
        ctk.CTkEntry(move_row, textvariable=self._move_skill_var, width=140, height=28,
                     font=FONT_SM, fg_color=BG, placeholder_text="skill_name").pack(side="left", padx=4)

        ctk.CTkLabel(move_row, text="to:", font=FONT_SM, text_color=DIM).pack(side="left", padx=(4, 2))
        self._move_tier_var = ctk.StringVar(value="simple")
        ctk.CTkOptionMenu(move_row, variable=self._move_tier_var,
                          values=["simple", "medium", "complex"],
                          font=FONT_SM, width=90, height=28, fg_color=BG3).pack(side="left", padx=2)

        ctk.CTkButton(move_row, text="Move", width=60, height=28, font=FONT_SM,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self._move_skill_tier).pack(side="left", padx=4)

        self._weight_status = ctk.CTkLabel(move_row, text="", font=FONT_XS, text_color=DIM)
        self._weight_status.pack(side="left", padx=8)

        ctk.CTkLabel(card, text="", font=FONT_XS).pack(pady=(0, 6))

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        try:
            import tomllib
            with open(FLEET_TOML, "rb") as f:
                return tomllib.load(f)
        except Exception:
            return {}

    def _load_skill_complexity(self) -> dict:
        """Load SKILL_COMPLEXITY from providers.py."""
        try:
            sys.path.insert(0, str(FLEET_DIR))
            from providers import SKILL_COMPLEXITY
            return dict(SKILL_COMPLEXITY)
        except (ImportError, AttributeError):
            return {
                "simple": ["flashcard", "rag_query", "summarize", "benchmark"],
                "medium": ["code_review", "web_search", "discuss"],
                "complex": ["plan_workload", "code_write", "skill_evolve"],
            }

    def _move_skill_tier(self):
        """Move a skill from its current tier to the selected tier."""
        skill = self._move_skill_var.get().strip()
        target_tier = self._move_tier_var.get()
        if not skill:
            self._weight_status.configure(text="Enter a skill name", text_color=RED)
            return

        try:
            import re
            providers_path = FLEET_DIR / "providers.py"
            text = providers_path.read_text(encoding="utf-8")

            # Find and parse SKILL_COMPLEXITY dict
            current_tiers = self._load_skill_complexity()

            # Find which tier the skill is currently in
            source_tier = None
            for tier, skills in current_tiers.items():
                if skill in skills:
                    source_tier = tier
                    break

            if source_tier is None:
                self._weight_status.configure(text=f"Skill '{skill}' not found", text_color=RED)
                return

            if source_tier == target_tier:
                self._weight_status.configure(text=f"Already in {target_tier}", text_color=DIM)
                return

            # Remove from source, add to target
            current_tiers[source_tier].remove(skill)
            current_tiers[target_tier].append(skill)

            # Rebuild the SKILL_COMPLEXITY block in providers.py
            new_block = "SKILL_COMPLEXITY = {\n"
            for tier in ["simple", "medium", "complex"]:
                skills_list = current_tiers.get(tier, [])
                # Format as multi-line list with 8 skills per line
                lines = []
                for i in range(0, len(skills_list), 8):
                    chunk = skills_list[i:i+8]
                    lines.append(", ".join(f'"{s}"' for s in chunk))
                skills_str = ",\n        ".join(lines)
                new_block += f'    "{tier}": [\n        {skills_str},\n    ],\n'
            new_block += "}"

            # Replace in file using regex
            pattern = r'SKILL_COMPLEXITY\s*=\s*\{[^}]+\}'
            text = re.sub(pattern, new_block, text, flags=re.DOTALL)
            providers_path.write_text(text, encoding="utf-8")

            # Update UI labels
            for tier_key, label in self._weight_tiers.items():
                label.configure(text=", ".join(current_tiers.get(tier_key, [])) or "(none)")

            self._weight_status.configure(
                text=f"Moved '{skill}': {source_tier} -> {target_tier}", text_color=GREEN)

        except Exception as e:
            self._weight_status.configure(text=f"Error: {e}", text_color=RED)

    def _refresh_eval_feed(self):
        """Load and display the 5 most recent scored tasks."""
        try:
            if FleetDB is None:
                self._eval_feed_status.configure(text="FleetDB not available")
                return

            fdb = FleetDB()
            with fdb._conn() as conn:
                rows = conn.execute("""
                    SELECT id, type, assigned_to, intelligence_score, updated_at
                    FROM tasks WHERE intelligence_score IS NOT NULL
                    AND intelligence_score > 0
                    ORDER BY updated_at DESC LIMIT 5
                """).fetchall()

            if not rows:
                self._eval_feed_status.configure(text="No scored tasks yet")
                for lbl in self._eval_feed_labels:
                    lbl.configure(text="")
                return

            for i, row in enumerate(rows):
                if i < len(self._eval_feed_labels):
                    score = row["intelligence_score"]
                    # Color-code score
                    if score >= 0.7:
                        color = GREEN
                    elif score >= 0.4:
                        color = ORANGE
                    else:
                        color = RED
                    task_id = row["id"]
                    skill = row["type"] or "?"
                    agent = row["assigned_to"] or "?"
                    ts = row["updated_at"] or ""
                    # Shorten timestamp
                    ts_short = ts[11:19] if len(ts) > 19 else ts
                    self._eval_feed_labels[i].configure(
                        text=f"  #{task_id}  [{skill}]  agent={agent}  score={score:.3f}  {ts_short}",
                        text_color=color,
                    )

            # Clear remaining labels
            for i in range(len(rows), len(self._eval_feed_labels)):
                self._eval_feed_labels[i].configure(text="")

            self._eval_feed_status.configure(text=f"Showing {len(rows)} recent scores")
        except Exception as e:
            self._eval_feed_status.configure(text=f"Error: {e}")

    def on_refresh(self):
        """Periodic refresh -- update live eval feed if visible."""
        try:
            if hasattr(self, '_eval_feed_labels'):
                self._refresh_eval_feed()
        except Exception:
            pass

    def on_close(self):
        self._queue_running = False
