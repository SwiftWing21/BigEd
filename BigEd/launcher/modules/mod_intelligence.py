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

# Dynamic FLEET_DIR — works regardless of install location
try:
    import launcher as _launcher
    FLEET_DIR = Path(_launcher.FLEET_DIR)
except ImportError:
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

        # Editable fields: (label, toml_key_path, current_value)
        field_defs = [
            ("Active GPU Model", ("models", "local"),             models.get("local", "qwen3:8b")),
            ("Conductor (CPU)",  ("models", "conductor_model"),   models.get("conductor_model", "qwen3:4b")),
            ("Keep Alive (mins)",("models", "keep_alive_mins"),   str(models.get("keep_alive_mins", 30))),
            ("Tier Default",     ("models", "tiers", "default"),  tiers.get("default", "qwen3:8b")),
            ("Tier Low",         ("models", "tiers", "low"),      tiers.get("low", "qwen3:1.7b")),
            ("Tier Critical",    ("models", "tiers", "critical"), tiers.get("critical", "qwen3:0.6b")),
        ]

        settings_grid = ctk.CTkFrame(card, fg_color="transparent")
        settings_grid.pack(fill="x", padx=12, pady=4)
        settings_grid.grid_columnconfigure(1, weight=1)

        self._model_entry_vars = {}
        for i, (label, key_path, value) in enumerate(field_defs):
            ctk.CTkLabel(settings_grid, text=label, font=FONT_SM, text_color=DIM,
                         anchor="w").grid(row=i, column=0, padx=(0, 12), pady=2, sticky="w")
            var = ctk.StringVar(value=value)
            self._model_entry_vars[key_path] = var
            ctk.CTkEntry(settings_grid, textvariable=var, font=FONT_STAT,
                         fg_color=BG3, border_color=BG3, text_color=TEXT,
                         height=26, width=160
                         ).grid(row=i, column=1, pady=2, sticky="w")

        # Save button
        save_row = ctk.CTkFrame(card, fg_color="transparent")
        save_row.pack(fill="x", padx=12, pady=(4, 8))
        self._model_save_status = ctk.CTkLabel(save_row, text="", font=FONT_XS, text_color=DIM)
        self._model_save_status.pack(side="left", padx=4)
        ctk.CTkButton(save_row, text="Save Model Settings", width=150, height=28,
                      font=FONT_SM, fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self._save_model_settings).pack(side="right")

        # ── Skill Complexity Routing ──
        ctk.CTkLabel(card, text="Skill Complexity Routing", font=FONT_BOLD,
                     text_color=GOLD, anchor="w").pack(fill="x", padx=12, pady=(8, 2))
        ctk.CTkLabel(card,
                     text="Simple → Haiku ($0.80/M)  |  Medium → Sonnet ($3/M)  |  Complex → Opus ($15/M)",
                     font=FONT_XS, text_color=DIM, anchor="w").pack(fill="x", padx=16, pady=(0, 4))

        # Build editable tier dropdowns from SKILL_COMPLEXITY
        try:
            import sys
            sys.path.insert(0, str(FLEET_DIR))
            from providers import SKILL_COMPLEXITY
        except Exception:
            SKILL_COMPLEXITY = {}

        complexity_scroll = ctk.CTkScrollableFrame(card, fg_color=BG3, corner_radius=4, height=160)
        complexity_scroll.pack(fill="x", padx=12, pady=(0, 4))
        complexity_scroll.grid_columnconfigure(1, weight=1)

        self._skill_tier_vars = {}
        for row_i, (skill, tier) in enumerate(sorted(SKILL_COMPLEXITY.items())):
            ctk.CTkLabel(complexity_scroll, text=skill, font=FONT_XS, text_color=DIM,
                         anchor="w").grid(row=row_i, column=0, padx=(6, 8), pady=1, sticky="w")
            var = ctk.StringVar(value=tier)
            self._skill_tier_vars[skill] = var
            ctk.CTkOptionMenu(
                complexity_scroll, variable=var,
                values=["simple", "medium", "complex"],
                font=FONT_XS, height=22, width=90,
                fg_color=BG2, button_color=BG3, button_hover_color=BG,
            ).grid(row=row_i, column=1, pady=1, sticky="w")

        skill_save_row = ctk.CTkFrame(card, fg_color="transparent")
        skill_save_row.pack(fill="x", padx=12, pady=(2, 10))
        self._skill_save_status = ctk.CTkLabel(skill_save_row, text="", font=FONT_XS, text_color=DIM)
        self._skill_save_status.pack(side="left", padx=4)
        ctk.CTkButton(skill_save_row, text="Save Routing", width=120, height=28,
                      font=FONT_SM, fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self._save_skill_complexity).pack(side="right")

    def _save_model_settings(self):
        """Write changed model values back to fleet.toml using tomlkit."""
        try:
            import tomlkit
            with open(FLEET_TOML, "r", encoding="utf-8") as f:
                doc = tomlkit.load(f)

            for key_path, var in self._model_entry_vars.items():
                val = var.get().strip()
                if not val:
                    continue
                # Navigate nested key path and set value
                node = doc
                for k in key_path[:-1]:
                    node = node[k]
                last_key = key_path[-1]
                # Preserve int type for numeric fields
                try:
                    node[last_key] = int(val)
                except (ValueError, TypeError):
                    node[last_key] = val

            with open(FLEET_TOML, "w", encoding="utf-8") as f:
                f.write(tomlkit.dumps(doc))
            self._model_save_status.configure(text="Saved.", text_color=GREEN)
        except Exception as e:
            self._model_save_status.configure(text=f"Error: {e}", text_color=RED)

    def _save_skill_complexity(self):
        """Write skill tier assignments to fleet.toml [skill_complexity]."""
        try:
            import tomlkit
            with open(FLEET_TOML, "r", encoding="utf-8") as f:
                doc = tomlkit.load(f)

            # Write/overwrite [skill_complexity] table
            tbl = tomlkit.table()
            for skill, var in self._skill_tier_vars.items():
                tbl[skill] = var.get()
            doc["skill_complexity"] = tbl

            with open(FLEET_TOML, "w", encoding="utf-8") as f:
                f.write(tomlkit.dumps(doc))
            self._skill_save_status.configure(text="Saved. Restart fleet to apply.", text_color=GREEN)
        except Exception as e:
            self._skill_save_status.configure(text=f"Error: {e}", text_color=RED)

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

        ctk.CTkLabel(card, text="", font=FONT_XS).pack(pady=(0, 4))

        # ── Live score feed ──
        live_hdr = ctk.CTkFrame(card, fg_color="transparent")
        live_hdr.pack(fill="x", padx=12, pady=(4, 2))
        ctk.CTkLabel(live_hdr, text="Recent Scores", font=FONT_BOLD,
                     text_color=GOLD, anchor="w").pack(side="left")
        self._eval_refresh_btn = ctk.CTkButton(
            live_hdr, text="↺", width=24, height=22, font=FONT_SM,
            fg_color=BG3, hover_color=BG2,
            command=self._refresh_eval_scores)
        self._eval_refresh_btn.pack(side="right")

        self._eval_scores_frame = ctk.CTkScrollableFrame(
            card, fg_color=BG3, corner_radius=4, height=140)
        self._eval_scores_frame.pack(fill="x", padx=12, pady=(0, 8))
        self._eval_scores_frame.grid_columnconfigure(0, weight=0)
        self._eval_scores_frame.grid_columnconfigure(1, weight=1)
        self._eval_scores_frame.grid_columnconfigure(2, weight=0)
        self._eval_scores_frame.grid_columnconfigure(3, weight=0)

        self._eval_empty_lbl = ctk.CTkLabel(
            self._eval_scores_frame, text="No scored tasks yet",
            font=FONT_SM, text_color=DIM)
        self._eval_empty_lbl.pack(pady=8)

        # Auto-refresh every 10s via on_refresh
        self._eval_refresh_after = None
        self._schedule_eval_refresh()

    def _schedule_eval_refresh(self):
        """Schedule periodic eval score refresh via the app's after() mechanism."""
        try:
            self._eval_refresh_after = self._app.after(10000, self._schedule_eval_refresh)
        except Exception:
            pass
        self._refresh_eval_scores()

    def _refresh_eval_scores(self):
        """Fetch recent eval scores from DB in a background thread and update UI."""
        def _fetch():
            try:
                import sys
                sys.path.insert(0, str(FLEET_DIR.parent / "BigEd" / "launcher"))
                from data_access import FleetDB
                return FleetDB.recent_eval_scores(FLEET_DIR / "fleet.db", limit=20)
            except Exception:
                return []

        def _render(rows):
            for w in self._eval_scores_frame.winfo_children():
                w.destroy()
            if not rows:
                ctk.CTkLabel(self._eval_scores_frame, text="No scored tasks yet",
                             font=FONT_SM, text_color=DIM).pack(pady=8)
                return
            # Column headers
            headers = ["Task", "Skill", "Agent", "Score"]
            for col, h in enumerate(headers):
                ctk.CTkLabel(self._eval_scores_frame, text=h,
                             font=FONT_XS, text_color=DIM, anchor="w"
                             ).grid(row=0, column=col, padx=(6, 4), pady=(2, 0), sticky="w")
            for row_i, r in enumerate(rows, start=1):
                score = r.get("intelligence_score") or 0.0
                # Colour by score tier
                if score >= 0.8:
                    score_color = GREEN
                elif score >= 0.5:
                    score_color = GOLD
                else:
                    score_color = ORANGE
                ctk.CTkLabel(self._eval_scores_frame, text=f"#{r['id']}",
                             font=FONT_XS, text_color=DIM, anchor="w",
                             ).grid(row=row_i, column=0, padx=(6, 4), pady=1, sticky="w")
                ctk.CTkLabel(self._eval_scores_frame,
                             text=(r.get("type") or "")[:18],
                             font=FONT_XS, text_color=TEXT, anchor="w",
                             ).grid(row=row_i, column=1, padx=(0, 4), pady=1, sticky="w")
                ctk.CTkLabel(self._eval_scores_frame,
                             text=(r.get("assigned_to") or "")[:14],
                             font=FONT_XS, text_color=DIM, anchor="w",
                             ).grid(row=row_i, column=2, padx=(0, 8), pady=1, sticky="w")
                ctk.CTkLabel(self._eval_scores_frame,
                             text=f"{score:.3f}",
                             font=("Consolas", 9, "bold"), text_color=score_color, anchor="e",
                             ).grid(row=row_i, column=3, padx=(0, 6), pady=1, sticky="e")

        import threading
        threading.Thread(target=lambda: self._app.after(0, lambda: _render(_fetch())),
                         daemon=True).start()

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
        """Refresh live eval scores when Intelligence tab is active."""
        if hasattr(self, '_eval_scores_frame'):
            self._refresh_eval_scores()

    def on_close(self):
        self._queue_running = False
        if getattr(self, '_eval_refresh_after', None):
            try:
                self._app.after_cancel(self._eval_refresh_after)
            except Exception:
                pass
