"""
BigEd CC — Fleet Comm tab: chat providers, HITL, voice, OAuth, advisories.
Extracted from launcher.py to reduce god-object complexity (TECH_DEBT 4.1).

Provides a CommTabMixin that is mixed into BigEdCC:
- Tab builder: _build_tab_comm, _toggle_comm_requests, _expand_comm_requests,
  _toggle_comm_pin, _update_comm_request_view, _refresh_comm
- Chat providers: _select_provider, _update_model_swapper, _on_chat_enter,
  _send_manual_chat, _append_tagged_message, _append_chat_response,
  _set_streaming, _unified_local_chat, _unified_claude_chat,
  _unified_gemini_chat, _check_provider_connections
- Usage: _update_usage_bar, _show_usage_popover, _show_quarantine_controls
- VS Code: _reply_via_vscode, _find_vscode
- Voice: _voice_input, _voice_into_task, _voice_into_active_entry
- OAuth: _launch_oauth_session
- HITL: _send_human_response, _load_hitl_to_chat, _draft_comm_response,
  _draft_via_claude, _draft_via_gemini, _draft_via_local
- Advisories: _approve_advisory, _dismiss_advisory
"""
import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import customtkinter as ctk

# ─── Theme (single source of truth) ──────────────────────────────────────────
from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    GREEN, ORANGE, RED, FONT, FONT_SM, FONT_STAT, FONT_BOLD, FONT_XS,
    CARD_RADIUS,
)

# ─── Lazy imports from launcher ──────────────────────────────────────────────

def _launcher():
    """Return the launcher module (import once, cache)."""
    import launcher as _mod
    return _mod


# ─── Comm Tab Mixin ─────────────────────────────────────────────────────────

class CommTabMixin:
    """Mixin providing Fleet Comm tab UI and all chat/HITL/voice/advisory logic."""

    # ── Fleet Comm tab ─────────────────────────────────────────────────────
    def _build_tab_comm(self, parent):
        """Fleet Comm: Agent Requests (dominant) + Manual Chat / VS Code guide."""
        parent.grid_rowconfigure(1, weight=1)   # request list (dominant — takes most space)
        parent.grid_rowconfigure(2, weight=0)   # manual chat (compact at bottom)
        parent.grid_columnconfigure(0, weight=1)

        # Persist provider selection across refreshes (default: Local — always available)
        if not hasattr(self, "_comm_provider_var"):
            self._comm_provider_var = ctk.StringVar(value="\u26a1 Local")

        # ── Agent Requests header (collapsible) ──────────────────────────
        req_header = ctk.CTkFrame(parent, fg_color=BG2, height=36, corner_radius=0)
        req_header.grid(row=0, column=0, sticky="ew")
        req_header.grid_propagate(False)

        self._comm_requests_collapsed = True
        self._comm_pin = False

        self._comm_req_label = ctk.CTkLabel(
            req_header, text="\u25b8 0 agent requests", font=FONT_BOLD,
            text_color=DIM, anchor="w", cursor="hand2")
        self._comm_req_label.pack(side="left", padx=12)
        self._comm_req_label.bind("<Button-1>", lambda e: self._toggle_comm_requests())
        self._comm_req_label.bind("<Enter>", lambda e: self._expand_comm_requests())

        # Pin button
        self._comm_pin_btn = ctk.CTkButton(
            req_header, text="\U0001f4cc", width=28, height=28, font=FONT_SM,
            fg_color="transparent", hover_color=BG3,
            command=self._toggle_comm_pin)
        self._comm_pin_btn.pack(side="right", padx=4)

        # Refresh button
        ctk.CTkButton(req_header, text="Refresh", width=60, height=24, font=FONT_SM,
                      fg_color=BG3, hover_color=BG,
                      command=self._refresh_comm).pack(side="right", padx=4, pady=4)

        # AI draft provider toggle
        ctk.CTkSegmentedButton(
            req_header,
            values=["\U0001f916 Claude", "\u2726 Gemini", "\u26a1 Local"],
            variable=self._comm_provider_var,
            font=FONT_SM,
            selected_color=ACCENT, selected_hover_color=ACCENT_H,
            unselected_color=BG3, unselected_hover_color=BG2,
            width=220, height=24,
        ).pack(side="right", padx=(0, 4), pady=4)

        # ── Request list (scrollable, starts collapsed) ──────────────────
        self._comm_request_frame = ctk.CTkScrollableFrame(
            parent, fg_color=BG, corner_radius=0, height=0)
        self._comm_request_frame.grid_columnconfigure(0, weight=1)
        # Start collapsed — do not grid yet
        self._comm_request_cards = []
        self._comm_cards = []  # track rendered card widgets (compat)

        # ── Unified Console section ────────────────────────────────────
        self._chat_container = ctk.CTkFrame(parent, fg_color=BG, corner_radius=0)
        self._chat_container.grid(row=2, column=0, sticky="nsew")

        # Unified console state
        if not hasattr(self, "_usage_tracker"):
            from ui.usage_tracker import UsageTracker
            self._usage_tracker = UsageTracker()
        self._active_provider = "local"
        self._provider_dots = {}
        self._provider_pills = {}
        self._streaming = False

        from ui.theme import (PROVIDER_LOCAL, PROVIDER_CLAUDE, PROVIDER_GEMINI,
                              PROVIDER_OAUTH, PROVIDER_BG_LOCAL, PROVIDER_BG_CLAUDE,
                              PROVIDER_BG_GEMINI)

        # ── Provider Pills + Model Swapper ─────────────────────────
        pill_row = ctk.CTkFrame(self._chat_container, fg_color=BG2, height=40, corner_radius=0)
        pill_row.pack(fill="x")
        pill_row.pack_propagate(False)

        providers = [
            ("Local", PROVIDER_LOCAL, PROVIDER_BG_LOCAL),
            ("Claude", PROVIDER_CLAUDE, PROVIDER_BG_CLAUDE),
            ("Gemini", PROVIDER_GEMINI, PROVIDER_BG_GEMINI),
            ("OAuth", PROVIDER_OAUTH, None),
        ]

        for name, color, bg in providers:
            pill = ctk.CTkButton(
                pill_row, text=f"  {name}", width=90, height=28,
                font=FONT_SM, corner_radius=14,
                fg_color=bg or BG3, hover_color=color,
                border_width=2 if name.lower() == self._active_provider else 0,
                border_color=color,
                command=lambda n=name.lower(): self._select_provider(n),
            )
            pill.pack(side="left", padx=4, pady=6)
            self._provider_pills[name.lower()] = pill

            # Connection dot
            dot = ctk.CTkLabel(pill_row, text="\u2b24", font=("Segoe UI", 6),
                               text_color=DIM)
            dot.pack(side="left", padx=(0, 4))
            self._provider_dots[name.lower()] = dot

        # Model swapper (right-aligned)
        self._model_swap_var = ctk.StringVar(value="qwen3:8b")
        self._model_swap = ctk.CTkOptionMenu(
            pill_row, variable=self._model_swap_var,
            values=["qwen3:8b"], font=FONT_SM, width=160, height=26,
            fg_color=BG3,
        )
        self._model_swap.pack(side="right", padx=8, pady=6)

        # ── Usage Status Bar ───────────────────────────────────────
        L = _launcher()
        self._usage_bar_frame = ctk.CTkFrame(self._chat_container, fg_color=BG2,
                                              height=48, corner_radius=0)
        if L._load_settings().get("show_usage_bar", True):
            self._usage_bar_frame.pack(fill="x")
        self._usage_bar_frame.pack_propagate(False)

        self._usage_labels = {}
        for prov, color in [("local", PROVIDER_LOCAL), ("claude", PROVIDER_CLAUDE),
                             ("gemini", PROVIDER_GEMINI)]:
            lbl = ctk.CTkLabel(self._usage_bar_frame, text="",
                                font=FONT_XS, text_color=color, anchor="w")
            lbl.pack(side="left", padx=12, pady=2)
            lbl.bind("<Button-1>", lambda e, p=prov: self._show_usage_popover(p))
            self._usage_labels[prov] = lbl

        # ── Chat Display ───────────────────────────────────────────
        self._local_chat_frame = ctk.CTkFrame(self._chat_container, fg_color="transparent")
        self._local_chat_frame.pack(fill="both", expand=True)

        self._manual_chat_display = ctk.CTkTextbox(
            self._local_chat_frame, font=FONT_STAT, fg_color=BG2,
            text_color=TEXT, corner_radius=4)
        self._manual_chat_display.pack(fill="both", expand=True, padx=8, pady=4)
        self._manual_chat_display.configure(state="disabled")

        # ── Input Row ──────────────────────────────────────────────
        input_row = ctk.CTkFrame(self._local_chat_frame, fg_color="transparent")
        input_row.pack(fill="x", padx=8, pady=(0, 8))
        input_row.grid_columnconfigure(0, weight=1)

        self._manual_chat_entry = ctk.CTkEntry(
            input_row, font=FONT, fg_color=BG2,
            placeholder_text="Message (Local) \u2014 switch provider with pills above...")
        self._manual_chat_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._manual_chat_entry.bind("<Return>", self._on_chat_enter)

        ctk.CTkButton(
            input_row, text="Send", width=70, height=30, font=FONT_SM,
            fg_color=ACCENT, hover_color=ACCENT_H,
            command=self._send_manual_chat,
        ).grid(row=0, column=1)

        self._mic_btn = ctk.CTkButton(
            input_row, text="\U0001f3a4", width=36, height=30, font=("Segoe UI", 14),
            fg_color=BG3, hover_color=BG2,
            command=self._voice_input)
        self._mic_btn.grid(row=0, column=2, padx=(4, 0))

        # ── Initialize HITL poller ─────────────────────────────────
        import sys as _sys
        if str(L.FLEET_DIR) not in _sys.path:
            _sys.path.insert(0, str(L.FLEET_DIR))
        from hitl_responder import HITLFilePoller
        if not hasattr(self, "_hitl_poller"):
            def _hitl_send(task_id, response):
                from data_access import FleetDB
                return FleetDB.send_human_response(L.FLEET_DIR / "fleet.db", task_id, response)
            self._hitl_poller = HITLFilePoller(_hitl_send)

        # Start connection polling + model swapper population
        self._safe_after(1000, self._check_provider_connections)
        self._safe_after(1500, lambda: self._update_model_swapper("local"))

    def _toggle_comm_requests(self):
        """Toggle collapsible Agent Requests panel."""
        self._comm_requests_collapsed = not self._comm_requests_collapsed
        self._update_comm_request_view()

    def _expand_comm_requests(self):
        """Expand Agent Requests on hover (unless pinned open)."""
        if self._comm_requests_collapsed and not self._comm_pin:
            self._comm_requests_collapsed = False
            self._update_comm_request_view()

    def _toggle_comm_pin(self):
        """Pin/unpin the Agent Requests panel open."""
        self._comm_pin = not self._comm_pin
        color = GOLD if self._comm_pin else DIM
        self._comm_pin_btn.configure(text_color=color)

    def _update_comm_request_view(self):
        """Show/hide the collapsible request list and update arrow."""
        n = len(self._comm_request_cards)
        arrow = "\u25b8" if self._comm_requests_collapsed else "\u25be"
        color = ORANGE if n else GREEN
        self._comm_req_label.configure(
            text=f"{arrow} {n} agent request{'s' if n != 1 else ''}",
            text_color=color)
        parent = self._comm_request_frame.master
        if self._comm_requests_collapsed:
            self._comm_request_frame.grid_remove()
            # Give all space to the chat console when requests are collapsed
            parent.grid_rowconfigure(1, weight=0)
            parent.grid_rowconfigure(2, weight=1)
        else:
            h = min(300, max(60, n * 60))
            self._comm_request_frame.configure(height=h)
            self._comm_request_frame.grid(row=1, column=0, sticky="ew")
            parent.grid_rowconfigure(1, weight=1)
            parent.grid_rowconfigure(2, weight=0)

    # ── Unified Console helpers ──────────────────────────────────────────

    def _select_provider(self, provider):
        """Switch active provider in Fleet Comm unified console."""
        from ui.theme import (PROVIDER_LOCAL, PROVIDER_CLAUDE, PROVIDER_GEMINI,
                              PROVIDER_BG_LOCAL, PROVIDER_BG_CLAUDE, PROVIDER_BG_GEMINI)

        if provider == "oauth":
            self._launch_oauth_session("OAuth", "")
            return

        self._active_provider = provider
        colors = {
            "local": (PROVIDER_LOCAL, PROVIDER_BG_LOCAL),
            "claude": (PROVIDER_CLAUDE, PROVIDER_BG_CLAUDE),
            "gemini": (PROVIDER_GEMINI, PROVIDER_BG_GEMINI),
        }
        for name, pill in self._provider_pills.items():
            if name == "oauth":
                continue
            c, bg = colors.get(name, (DIM, BG3))
            if name == provider:
                pill.configure(border_width=2, border_color=c, fg_color=bg)
            else:
                pill.configure(border_width=0, fg_color=BG3)

        self._update_model_swapper(provider)
        labels = {"local": "Local", "claude": "Claude", "gemini": "Gemini"}
        self._manual_chat_entry.configure(
            placeholder_text=f"Message ({labels.get(provider, provider)}) \u2014 switch provider with pills above..."
        )

    def _update_model_swapper(self, provider):
        """Update model dropdown based on selected provider."""
        L = _launcher()
        mcfg = L.load_model_cfg()
        if provider == "local":
            def _fetch():
                try:
                    host = mcfg.get("ollama_host", "http://localhost:11434")
                    req = urllib.request.Request(f"{host}/api/tags", method="GET")
                    with urllib.request.urlopen(req, timeout=3) as r:
                        data = json.loads(r.read())
                    models = [m["name"] for m in data.get("models", [])]
                    if not models:
                        models = [mcfg.get("local", "qwen3:8b")]
                    import sys as _sys
                    if str(L.FLEET_DIR) not in _sys.path:
                        _sys.path.insert(0, str(L.FLEET_DIR))
                    import db
                    trusted = db.get_registered_models()
                    display = []
                    for m in models:
                        if m not in trusted:
                            display.append(f"NEW: {m}")
                        else:
                            display.append(m)
                    self._safe_after(0, lambda d=display: (
                        self._model_swap.configure(values=d),
                        self._model_swap_var.set(d[0] if d else "qwen3:8b"),
                    ))
                except Exception:
                    default = mcfg.get("local", "qwen3:8b")
                    self._safe_after(0, lambda: (
                        self._model_swap.configure(values=[default]),
                        self._model_swap_var.set(default),
                    ))
            threading.Thread(target=_fetch, daemon=True).start()
        elif provider == "claude":
            models = ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-6"]
            self._model_swap.configure(values=models)
            self._model_swap_var.set(mcfg.get("claude_model", "claude-sonnet-4-6"))
        elif provider == "gemini":
            models = [mcfg.get("gemini_model", "gemini-2.0-flash")]
            self._model_swap.configure(values=models)
            self._model_swap_var.set(models[0])

    def _on_chat_enter(self, event):
        """Enter sends chat; Shift+Enter does nothing (single-line entry)."""
        if event.state & 0x1:
            return
        self._send_manual_chat()
        return "break"

    def _send_manual_chat(self):
        """Send a message from the unified Fleet Comm console."""
        text = self._manual_chat_entry.get().strip()
        if not text:
            return
        self._manual_chat_entry.delete(0, "end")
        self._append_tagged_message("user", text, self._active_provider, "")

        # Handle HITL context
        hitl_id = getattr(self, '_active_hitl_task_id', None)
        hitl_agent = getattr(self, '_active_hitl_agent', None)
        if hitl_id is not None:
            self._send_human_response(hitl_id, text)
            self._active_hitl_task_id = None
            self._active_hitl_agent = None
            agent_label = hitl_agent or "agent"
            self._append_tagged_message(
                "system", f"Response sent to {agent_label} (task #{hitl_id})",
                self._active_provider, "")
            self._safe_after(500, self._refresh_comm)
            return

        # Route to active provider
        provider = self._active_provider
        model = self._model_swap_var.get().replace("NEW: ", "")
        self._set_streaming(True)

        if provider == "local":
            threading.Thread(target=self._unified_local_chat,
                             args=(text, model), daemon=True).start()
        elif provider == "claude":
            threading.Thread(target=self._unified_claude_chat,
                             args=(text, model), daemon=True).start()
        elif provider == "gemini":
            threading.Thread(target=self._unified_gemini_chat,
                             args=(text, model), daemon=True).start()

    def _append_tagged_message(self, role, text, provider, model):
        """Append a provider-tagged message to the unified chat display."""
        self._manual_chat_display.configure(state="normal")
        if role == "user":
            self._manual_chat_display.insert("end", f"\nYou: {text}\n")
        elif role == "system":
            self._manual_chat_display.insert("end", f"\n\u2500\u2500\u2500 {text} \u2500\u2500\u2500\n")
        else:
            provider_icons = {"local": "\u26a1", "claude": "\U0001f916", "gemini": "\u2726"}
            icon = provider_icons.get(provider, "")
            tag = f"[{model}]" if model else f"[{provider}]"
            self._manual_chat_display.insert("end", f"\n{icon} {tag} {text}\n")
        self._manual_chat_display.configure(state="disabled")
        self._manual_chat_display.see("end")

    def _append_chat_response(self, text):
        """Compat shim — routes to _append_tagged_message."""
        self._append_tagged_message("system", text, getattr(self, '_active_provider', 'local'), "")

    def _set_streaming(self, active):
        """Toggle streaming indicator on active provider pill."""
        from ui.theme import PROVIDER_LOCAL, PROVIDER_CLAUDE, PROVIDER_GEMINI
        self._streaming = active
        pill = self._provider_pills.get(self._active_provider)
        if not pill:
            return
        colors = {"local": PROVIDER_LOCAL, "claude": PROVIDER_CLAUDE,
                  "gemini": PROVIDER_GEMINI}
        color = colors.get(self._active_provider, DIM)
        if active:
            pill.configure(border_width=3, border_color=color)
            self._manual_chat_entry.configure(placeholder_text="Waiting for response...")
        else:
            pill.configure(border_width=2, border_color=color)
            labels = {"local": "Local", "claude": "Claude", "gemini": "Gemini"}
            self._manual_chat_entry.configure(
                placeholder_text=f"Message ({labels.get(self._active_provider, '')})..."
            )

    # ── Provider API calls ─────────────────────────────────────────────

    def _unified_local_chat(self, prompt, model):
        """Send to Ollama with usage tracking and quarantine for new models."""
        L = _launcher()
        try:
            mcfg = L.load_model_cfg()
            host = mcfg.get("ollama_host", "http://localhost:11434")
            body = json.dumps({
                "model": model, "prompt": prompt,
                "stream": False, "options": {"num_gpu": 99},
            }).encode()
            req = urllib.request.Request(
                f"{host}/api/generate", data=body, method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.loads(r.read())

            response = resp.get("response", "No response")
            tok_out = resp.get("eval_count", 0)
            tok_in = resp.get("prompt_eval_count", 0)
            eval_ns = resp.get("eval_duration", 0)
            tps = (tok_out / (eval_ns / 1e9)) if eval_ns > 0 else 0.0

            self._usage_tracker.record("local", model, tokens_in=tok_in,
                                        tokens_out=tok_out, tok_per_sec=tps)

            import sys as _sys
            if str(L.FLEET_DIR) not in _sys.path:
                _sys.path.insert(0, str(L.FLEET_DIR))
            import db
            is_new = not db.is_model_trusted(model)

            def _show():
                self._set_streaming(False)
                self._append_tagged_message("assistant", response, "local", model)
                self._update_usage_bar()
                if is_new:
                    self._show_quarantine_controls(model, response)

            self._safe_after(0, _show)
        except Exception as e:
            self._safe_after(0, lambda: (
                self._set_streaming(False),
                self._append_tagged_message("assistant", f"Error: {e}", "local", model),
            ))

    def _unified_claude_chat(self, prompt, model):
        """Send to Claude API with usage tracking."""
        try:
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                self._safe_after(0, lambda: (
                    self._set_streaming(False),
                    self._append_tagged_message("system",
                        "ANTHROPIC_API_KEY not set. Configure in Settings > API Keys.",
                        "claude", model),
                ))
                return

            import anthropic
            client = anthropic.Anthropic(api_key=key)
            resp = client.messages.create(
                model=model, max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            response = resp.content[0].text
            tok_in = resp.usage.input_tokens
            tok_out = resp.usage.output_tokens

            cost = 0.0
            if "haiku" in model:
                cost = (tok_in * 0.80 + tok_out * 4.0) / 1_000_000
            elif "sonnet" in model:
                cost = (tok_in * 3.0 + tok_out * 15.0) / 1_000_000
            elif "opus" in model:
                cost = (tok_in * 15.0 + tok_out * 75.0) / 1_000_000

            self._usage_tracker.record("claude", model, tokens_in=tok_in,
                                        tokens_out=tok_out, cost=cost)
            self._safe_after(0, lambda: (
                self._set_streaming(False),
                self._append_tagged_message("assistant", response, "claude", model),
                self._update_usage_bar(),
            ))
        except Exception as e:
            self._safe_after(0, lambda: (
                self._set_streaming(False),
                self._append_tagged_message("assistant", f"Error: {e}", "claude", model),
            ))

    def _unified_gemini_chat(self, prompt, model):
        """Send to Gemini API with usage tracking."""
        try:
            key = os.environ.get("GEMINI_API_KEY", "")
            if not key:
                self._safe_after(0, lambda: (
                    self._set_streaming(False),
                    self._append_tagged_message("system",
                        "GEMINI_API_KEY not set. Configure in Settings > API Keys.",
                        "gemini", model),
                ))
                return

            from google import genai
            client = genai.Client(api_key=key)
            resp = client.models.generate_content(model=model, contents=prompt)
            response = resp.text

            tok_in = getattr(resp.usage_metadata, "prompt_token_count", 0) or 0
            tok_out = getattr(resp.usage_metadata, "candidates_token_count", 0) or 0
            cost = (tok_in * 0.075 + tok_out * 0.30) / 1_000_000

            self._usage_tracker.record("gemini", model, tokens_in=tok_in,
                                        tokens_out=tok_out, cost=cost)
            self._safe_after(0, lambda: (
                self._set_streaming(False),
                self._append_tagged_message("assistant", response, "gemini", model),
                self._update_usage_bar(),
            ))
        except Exception as e:
            self._safe_after(0, lambda: (
                self._set_streaming(False),
                self._append_tagged_message("assistant", f"Error: {e}", "gemini", model),
            ))

    # ── Connection status + Usage bar ──────────────────────────────────

    def _check_provider_connections(self):
        """Poll provider availability and update connection dots."""
        L = _launcher()

        def _check():
            results = {}
            try:
                mcfg = L.load_model_cfg()
                host = mcfg.get("ollama_host", "http://localhost:11434")
                req = urllib.request.Request(f"{host}/api/tags", method="GET")
                with urllib.request.urlopen(req, timeout=3) as r:
                    json.loads(r.read())
                results["local"] = "green"
            except Exception:
                results["local"] = "red"
            results["claude"] = "green" if os.environ.get("ANTHROPIC_API_KEY") else "gray"
            results["gemini"] = "green" if os.environ.get("GEMINI_API_KEY") else "gray"

            dot_colors = {"green": GREEN, "red": RED, "gray": DIM}
            self._safe_after(0, lambda: [
                self._provider_dots[p].configure(text_color=dot_colors.get(s, DIM))
                for p, s in results.items() if p in self._provider_dots
            ])

        threading.Thread(target=_check, daemon=True).start()
        self._safe_after(30_000, self._check_provider_connections)

    def _update_usage_bar(self):
        """Refresh the usage status bar labels."""
        for prov, lbl in self._usage_labels.items():
            line = self._usage_tracker.format_line(prov)
            prefix = {"local": "\u26a1 Local: ", "claude": "\U0001f916 Claude: ",
                      "gemini": "\u2726 Gemini: "}
            if line:
                lbl.configure(text=f"{prefix.get(prov, '')}{line}")
            else:
                lbl.configure(text="")

    def _show_usage_popover(self, provider):
        """Show all-time usage from fleet.db (click on usage bar line)."""
        L = _launcher()
        try:
            import sys as _sys
            if str(L.FLEET_DIR) not in _sys.path:
                _sys.path.insert(0, str(L.FLEET_DIR))
            from cost_tracking import get_usage_summary
            summary = get_usage_summary("all", group_by="model")
            lines = [f"All-time usage ({provider}):"]
            for row in summary:
                if row.get("provider") == provider or provider == "local":
                    lines.append(f"  {row.get('model', '?')}: "
                                 f"{row.get('total_tokens', 0)} tok, "
                                 f"${row.get('total_cost', 0):.3f}")
            msg = "\n".join(lines) if len(lines) > 1 else "No all-time data yet."
            self._append_tagged_message("system", msg, provider, "")
        except Exception as e:
            self._append_tagged_message("system", f"Usage query error: {e}", provider, "")

    # ── Quarantine controls ────────────────────────────────────────────

    def _show_quarantine_controls(self, model, response):
        """Show Accept/Reject/Flag controls for unregistered model responses."""
        L = _launcher()
        self._manual_chat_display.configure(state="normal")
        self._manual_chat_display.insert(
            "end",
            f"\n  \u26a0 Unregistered model: {model} \u2014 review response above\n"
        )
        self._manual_chat_display.configure(state="disabled")

        qframe = ctk.CTkFrame(self._local_chat_frame, fg_color=BG2, corner_radius=4, height=32)
        qframe.pack(fill="x", padx=8, pady=(0, 4))

        ctk.CTkLabel(qframe, text=f"\u26a0 {model}", font=FONT_XS,
                     text_color=ORANGE).pack(side="left", padx=8)

        def _accept():
            import sys as _sys
            if str(L.FLEET_DIR) not in _sys.path:
                _sys.path.insert(0, str(L.FLEET_DIR))
            import db
            count = db.record_model_accept(model)
            qframe.destroy()
            trust_threshold = 5
            if count >= trust_threshold:
                self._append_tagged_message("system",
                    f"{model} marked as trusted ({count}/{trust_threshold} accepts).",
                    "local", model)
            else:
                self._append_tagged_message("system",
                    f"{model}: {count}/{trust_threshold} accepts toward trust.",
                    "local", model)

        def _reject():
            qframe.destroy()
            self._append_tagged_message("system",
                f"Response from {model} rejected.", "local", model)

        def _flag():
            qframe.destroy()
            self._append_tagged_message("system",
                f"{model} flagged for review.", "local", model)

        ctk.CTkButton(qframe, text="Accept", width=60, height=24, font=FONT_XS,
                      fg_color=GREEN, hover_color="#388e3c",
                      command=_accept).pack(side="right", padx=2, pady=4)
        ctk.CTkButton(qframe, text="Reject", width=60, height=24, font=FONT_XS,
                      fg_color=RED, hover_color="#c62828",
                      command=_reject).pack(side="right", padx=2, pady=4)
        ctk.CTkButton(qframe, text="Flag", width=60, height=24, font=FONT_XS,
                      fg_color=ORANGE, hover_color="#e65100",
                      command=_flag).pack(side="right", padx=2, pady=4)

    # ── VS Code HITL reply ─────────────────────────────────────────────

    def _reply_via_vscode(self, task_id, question, agent_name):
        """Create HITL response file and open in VS Code."""
        L = _launcher()
        import sys as _sys
        if str(L.FLEET_DIR) not in _sys.path:
            _sys.path.insert(0, str(L.FLEET_DIR))
        from hitl_responder import create_response_file

        path = create_response_file(task_id, agent_name, question)
        self._hitl_poller.watch(task_id, path)

        code_exe = self._find_vscode()
        if code_exe:
            subprocess.Popen(
                [code_exe, str(path)],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._append_tagged_message("system",
                f"Opened hitl-response-{task_id}.md in VS Code. "
                f"Edit and save to send response to {agent_name}.",
                "local", "")
        else:
            self._append_tagged_message("system",
                f"VS Code not found. Response file created at:\n{path}\n"
                f"Edit and save it manually.",
                "local", "")

        self._safe_after(500, self._refresh_comm)

    def _find_vscode(self):
        """Locate VS Code executable."""
        import shutil
        code = shutil.which("code")
        if code:
            return code
        for candidate in [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
            os.path.expandvars(r"%PROGRAMFILES%\Microsoft VS Code\Code.exe"),
        ]:
            if os.path.isfile(candidate):
                return candidate
        return None

    def _voice_input(self):
        """Capture voice and transcribe to chat input."""
        L = _launcher()
        self._mic_btn.configure(text="\u23fa", fg_color="#5a2020")
        self._append_tagged_message("system", "[Listening... 5 seconds]", self._active_provider, "")

        def _record():
            try:
                import sys as _sys
                if str(L.FLEET_DIR) not in _sys.path:
                    _sys.path.insert(0, str(L.FLEET_DIR))
                from skills.speech_to_text import run as stt_run
                config = {}
                try:
                    from config import load_config
                    config = load_config()
                except Exception:
                    pass

                import logging
                log = logging.getLogger("stt")
                result = stt_run({"action": "listen", "duration_secs": 5}, config, log)

                if "text" in result and result["text"]:
                    self._safe_after(0, lambda t=result["text"]: self._manual_chat_entry.insert(0, t))
                    self._safe_after(0, lambda: self._append_chat_response(
                        f"[Transcribed: {result.get('backend', '?')}]"))
                else:
                    self._safe_after(0, lambda: self._append_chat_response(
                        f"[STT: {result.get('error', 'no text')}]"))
            except Exception as e:
                self._safe_after(0, lambda e=str(e): self._append_chat_response(f"[Voice error: {e}]"))
            finally:
                self._safe_after(0, lambda: self._mic_btn.configure(text="\U0001f3a4", fg_color=BG3))

        threading.Thread(target=_record, daemon=True).start()

    def _voice_into_task(self):
        """Capture voice and transcribe into the Task entry bar."""
        L = _launcher()
        self._task_mic_btn.configure(text="\u23fa", fg_color="#5a2020")
        self._task_status.configure(text="Listening...", text_color=ORANGE)

        def _record():
            try:
                import sys as _sys
                if str(L.FLEET_DIR) not in _sys.path:
                    _sys.path.insert(0, str(L.FLEET_DIR))
                from skills.speech_to_text import run as stt_run
                config = {}
                try:
                    from config import load_config
                    config = load_config()
                except Exception:
                    pass
                import logging
                result = stt_run({"action": "listen", "duration_secs": 5}, config, logging.getLogger("stt"))
                if "text" in result and result["text"]:
                    self._safe_after(0, lambda t=result["text"]: self._task_entry.insert("end", t))
                    self._safe_after(0, lambda: self._task_status.configure(text="Transcribed", text_color=GREEN))
                    self._safe_after(3000, lambda: self._task_status.configure(text=""))
                else:
                    self._safe_after(0, lambda: self._task_status.configure(
                        text=result.get("error", "no text"), text_color=RED))
            except Exception as e:
                self._safe_after(0, lambda: self._task_status.configure(text=f"STT error", text_color=RED))
            finally:
                self._safe_after(0, lambda: self._task_mic_btn.configure(text="\U0001f3a4", fg_color=BG2))
        threading.Thread(target=_record, daemon=True).start()

    def _voice_into_active_entry(self):
        """Ctrl+Shift+M — voice input into whichever entry is focused (task bar or chat)."""
        focused = self.focus_get()
        # If focused widget is the manual chat entry, use chat voice
        if focused == getattr(self, '_manual_chat_entry', None):
            self._voice_input()
        else:
            # Default to task bar
            self._voice_into_task()

    def _launch_oauth_session(self, model, context):
        """Write context files and launch VS Code session (Claude Code or Gemini CLI)."""
        L = _launcher()
        # Context preview — show files to be written before proceeding
        briefing = L.FLEET_DIR / "task-briefing.md"
        files_preview = [str(briefing)]

        # Check DITL mode for compliance rules file
        ditl_enabled = False
        try:
            import tomllib
            with open(L.FLEET_TOML, "rb") as f:
                toml_data = tomllib.load(f)
            ditl_enabled = toml_data.get("ditl", {}).get("enabled", False)
        except Exception:
            pass
        compliance_rule = L.FLEET_DIR.parent / ".claude" / "rules" / "compliance.md"
        if ditl_enabled:
            files_preview.append(str(compliance_rule))

        # If an active HITL task is loaded, note the response will also be sent back
        hitl_id = getattr(self, '_active_hitl_task_id', None)
        if hitl_id:
            files_preview.append(f"  \u2192 HITL task #{hitl_id} response will be sent on submit")
        file_list = "\n".join(f"  \u2022 {f}" for f in files_preview)
        confirm = L._ctx_preview_confirm(self, model, file_list)
        if not confirm:
            return

        # ── Write rich task-briefing.md ──────────────────────────────────
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Gather recent agent activity for context
        recent = ""
        try:
            import sqlite3
            db_path = L.FLEET_DIR / "fleet.db"
            if db_path.exists():
                conn = sqlite3.connect(str(db_path), timeout=5)
                try:
                    rows = conn.execute(
                        "SELECT type, status, result_json FROM tasks "
                        "WHERE status='DONE' "
                        "AND created_at >= datetime('now', '-1 hour') "
                        "ORDER BY created_at DESC LIMIT 5"
                    ).fetchall()
                    if rows:
                        recent = "\n## Recent Agent Activity\n"
                        for r in rows:
                            recent += f"- {r[0]}: {r[1]}\n"
                finally:
                    conn.close()
        except Exception:
            pass

        fleet_status = "running" if self._system_running else "stopped"
        selected_model = getattr(self, '_model_swap_var', None)
        selected_model = selected_model.get() if selected_model else "OAuth"

        ditl_line = f"- DITL: {'enabled' if ditl_enabled else 'disabled'}"

        # DITL compliance: filter PHI from context sent to external providers
        if ditl_enabled:
            try:
                if str(L.FLEET_DIR) not in sys.path:
                    sys.path.insert(0, str(L.FLEET_DIR))
                from phi_deidentify import deidentify_text
                context = deidentify_text(context)
            except Exception:
                pass  # PHI filter not available — proceed with raw context

        # Check for MCP availability
        mcp_available = (L.FLEET_DIR.parent / ".mcp.json").exists()
        mcp_line = "- MCP: available (.mcp.json configured)" if mcp_available else "- MCP: not configured"

        content = (
            f"# Manual Chat Session Briefing\n"
            f"Generated: {ts} by BigEd CC\n\n"
            f"## User Request\n{context}\n\n"
            f"## System Context\n"
            f"- Fleet: {fleet_status}\n"
            f"- Model: {selected_model}\n"
            f"{ditl_line}\n"
            f"{mcp_line}\n"
            f"{recent}\n"
            f"## Efficiency Notes\n"
            f"- Use `cache_control: {{type: \"ephemeral\"}}` on stable prompt prefixes\n"
            f"- Use MCP tools for fleet DB access, file operations, and knowledge queries\n"
            f"- Batch related operations to reduce round-trips\n"
            f"- Read CLAUDE.md for project conventions before making changes\n\n"
            f"## Suggested Approach\n"
            f"Review the request above and use the fleet knowledge base for context.\n"
        )
        briefing.write_text(content, encoding="utf-8")

        # Write audit-results.md with recent fleet findings
        try:
            audit_results_path = L.FLEET_DIR / "audit-results.md"
            import sqlite3 as _sq
            _conn = _sq.connect(str(L.FLEET_DIR / "fleet.db"), timeout=5)
            _conn.row_factory = _sq.Row
            try:
                recent = _conn.execute(
                    "SELECT type, status, result_json, created_at, assigned_to FROM tasks "
                    "WHERE status IN ('DONE','FAILED') AND created_at >= datetime('now', '-24 hours') "
                    "ORDER BY created_at DESC LIMIT 20"
                ).fetchall()
                audit_md = f"# Audit Results\nGenerated: {ts} by BigEd CC API audit system\n\n"
                for r in recent:
                    preview = (r["result_json"] or "")[:300]
                    audit_md += f"### {r['type']} ({r['status']})\n"
                    audit_md += f"Agent: {r['assigned_to'] or '?'} | {r['created_at']}\n"
                    audit_md += f"```\n{preview}\n```\n\n"
                audit_results_path.write_text(audit_md, encoding="utf-8")
            finally:
                _conn.close()
        except Exception:
            pass

        # ── Write .claude/rules/compliance.md if DITL mode enabled ───────
        if ditl_enabled:
            compliance_rule.parent.mkdir(parents=True, exist_ok=True)
            compliance_rule.write_text(
                "---\n"
                "paths:\n"
                '  - "training-files/**/*.md"\n'
                '  - "training-files/**/*.pdf"\n'
                '  - "training-files/**/*.docx"\n'
                "---\n"
                "# Training file compliance rules\n\n"
                "- Every training file MUST contain: title, version, effective date,\n"
                "  review date, author, and learning objectives\n"
                '- Flag files where review date is in the past as "OVERDUE REVIEW"\n'
                '- Flag files missing any required metadata as "INCOMPLETE METADATA"\n'
                "- Check that all external links are formatted correctly\n"
                "- Verify assessment criteria match stated learning objectives\n"
                "- Output findings in this format:\n\n"
                "  ## [filename]\n"
                "  - **Status:** Compliant / Non-compliant / Needs review\n"
                "  - **Issues:** [list specific problems]\n"
                "  - **Suggested actions:** [concrete next steps]\n",
                encoding="utf-8",
            )

        # ── Both Claude and Gemini share VS Code as the launch environment ──
        import shutil
        code_exe = shutil.which("code")
        if not code_exe and sys.platform == "win32":
            for p in [
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "Code.exe",
                Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft VS Code" / "Code.exe",
                Path(os.environ.get("USERPROFILE", "")) / "AppData" / "Local" / "Programs" / "Microsoft VS Code" / "Code.exe",
            ]:
                if p.exists():
                    code_exe = str(p)
                    break
        if not code_exe and sys.platform == "darwin":
            for p in [
                Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
                Path.home() / "Applications" / "Visual Studio Code.app" / "Contents" / "Resources" / "app" / "bin" / "code",
            ]:
                if p.exists():
                    code_exe = str(p)
                    break
        if not code_exe and sys.platform == "linux":
            for p in [
                Path("/usr/bin/code"),
                Path("/usr/share/code/bin/code"),
                Path("/snap/bin/code"),
                Path.home() / ".local" / "bin" / "code",
            ]:
                if p.exists():
                    code_exe = str(p)
                    break

        if not code_exe:
            import webbrowser
            webbrowser.open("https://vscode.dev")
            self._append_chat_response(
                "VS Code not found locally. Opened vscode.dev in browser.\n"
                "Context written to task-briefing.md.")
            return

        try:
            # Open VS Code to project dir + auto-open the briefing file
            # Only auto-open README if walkthrough hasn't been completed (fail-open)
            cmd = [code_exe, str(L.FLEET_DIR.parent)]
            try:
                prefs = L._load_settings()
                walkthrough_done = prefs.get("walkthrough_completed", False)
            except Exception:
                walkthrough_done = False  # fail-open: show README if settings unreadable
            if not walkthrough_done:
                cmd.extend(["--goto", str(L.FLEET_DIR / "VSCODE_README.md")])
            # Always include task briefing
            cmd.extend(["--goto", str(briefing)])
            subprocess.Popen(
                cmd,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self._append_chat_response(f"Could not open VS Code: {e}")
            return

        if "Claude" in model:
            # Try to auto-start Claude Code CLI
            claude_exe = shutil.which("claude")
            if claude_exe:
                escaped = context.replace('"', '\\"')
                subprocess.Popen(
                    [claude_exe, "--print",
                     f"Read fleet/task-briefing.md and execute the task described in it. "
                     f"User request: {escaped}"],
                    cwd=str(L.FLEET_DIR.parent),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self._append_chat_response(
                    "VS Code opened with task-briefing.md.\n"
                    "Claude Code started with your task pre-loaded.\n"
                    "Fleet agents are listening \u2014 HITL requests will appear in Fleet Comm.")
            else:
                self._append_chat_response(
                    "VS Code opened with task-briefing.md.\n"
                    "Start Claude Code: Ctrl+Shift+P \u2192 'Claude: Open'\n"
                    "Fleet agents are listening \u2014 HITL requests will appear in Fleet Comm.")
        elif "Gemini" in model:
            # Gemini uses the same VS Code environment
            gemini_exe = shutil.which("gemini")
            if gemini_exe:
                subprocess.Popen(
                    [gemini_exe],
                    cwd=str(L.FLEET_DIR.parent),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self._append_chat_response(
                    "VS Code opened with task-briefing.md.\n"
                    "Gemini CLI started in the project directory.\n"
                    "Toggle agent mode: /agent-mode (approval-gated \u2194 autonomous)\n"
                    "Fleet agents are listening \u2014 HITL requests will appear in Fleet Comm.")
            else:
                self._append_chat_response(
                    "VS Code opened with task-briefing.md.\n"
                    "Start Gemini: open VS Code terminal \u2192 run 'gemini'\n"
                    "Toggle agent mode: /agent-mode (approval-gated \u2194 autonomous)\n"
                    "Fleet agents are listening \u2014 HITL requests will appear in Fleet Comm.")

    # ── Fleet Comm — data refresh ────────────────────────────────────────

    def _refresh_comm(self):
        """Load WAITING_HUMAN tasks and security advisories into Fleet Comm."""
        L = _launcher()
        # Guard: skip if Fleet Comm tab hasn't been built yet (lazy-load)
        if "Fleet Comm" not in getattr(self, '_built_tabs', set()):
            return
        def _fetch():
            from data_access import FleetDB
            waiting = FleetDB.waiting_human_tasks(L.FLEET_DIR / "fleet.db")
            for item in waiting:
                if not item.get("question"):
                    item["question"] = "(no question)"
            advisories = []
            try:
                if L.PENDING_DIR.exists():
                    for f in sorted(L.PENDING_DIR.glob("advisory_*.md"))[:10]:
                        try:
                            text = f.read_text(encoding="utf-8", errors="replace")
                            lines = text.splitlines()
                            title = lines[0].strip("# ").strip() if lines else f.name
                            summary = ""
                            counts = ""
                            in_analysis = False
                            for ln in lines:
                                if ln.startswith("**Findings:**"):
                                    counts = ln.replace("**Findings:**", "").strip()
                                elif ln.startswith("## Analysis"):
                                    in_analysis = True
                                elif in_analysis and ln.strip() and not ln.startswith("##"):
                                    summary = ln.strip("- ").strip()[:120]
                                    in_analysis = False
                                elif ln.startswith("## ") and in_analysis:
                                    in_analysis = False
                            json_path = f.with_suffix(".json")
                            if not counts and json_path.exists():
                                try:
                                    jdata = json.loads(json_path.read_text())
                                    c = jdata.get("counts", {})
                                    parts = []
                                    for sev in ("HIGH", "MEDIUM", "LOW"):
                                        if c.get(sev, 0):
                                            parts.append(f"{c[sev]} {sev}")
                                    counts = ", ".join(parts)
                                    if not summary:
                                        summary = (jdata.get("analysis", "") or "")[:120]
                                except Exception:
                                    pass
                            advisories.append({
                                "file": f.name, "title": title[:80],
                                "path": str(f), "counts": counts,
                                "summary": summary,
                            })
                        except Exception:
                            pass
            except Exception:
                pass
            return waiting, advisories

        def _render(data):
            waiting, advisories = data
            # Clear existing request cards
            cards = list(self._comm_request_cards)
            self._comm_request_cards.clear()
            self._comm_cards.clear()
            for w in cards:
                try:
                    w.destroy()
                except Exception:
                    pass

            total = len(waiting) + len(advisories)
            scroll = self._comm_request_frame

            # Update Fleet Comm tab badge with pending HITL count
            n = len(waiting)
            tab_text = "\U0001f4ac  Fleet Comm" + (f" ({n})" if n > 0 else "")
            if hasattr(self, '_tabs') and "Fleet Comm" in self._tabs._tab_buttons:
                self._tabs._tab_buttons["Fleet Comm"].configure(text=tab_text)

            # Notify via toast if new HITL requests appeared
            n_waiting = n
            if n_waiting > getattr(self, '_prev_hitl_count', 0):
                new_count = n_waiting - getattr(self, '_prev_hitl_count', 0)
                self._show_toast(f"{new_count} new agent request(s)", ORANGE, duration=5000)
            self._prev_hitl_count = n_waiting

            if not total:
                self._update_comm_request_view()
                return

            # Render WAITING_HUMAN cards
            for item in waiting:
                wrapper = ctk.CTkFrame(scroll, fg_color=ORANGE, corner_radius=6)
                wrapper.pack(fill="x", padx=4, pady=3)
                self._comm_request_cards.append(wrapper)
                self._comm_cards.append(wrapper)
                card = ctk.CTkFrame(wrapper, fg_color=BG2, corner_radius=6)
                card.pack(fill="both", expand=True, padx=(2, 0))

                top = ctk.CTkFrame(card, fg_color="transparent")
                top.pack(fill="x", padx=8, pady=(8, 0))
                hdr_left = ctk.CTkFrame(top, fg_color="transparent")
                hdr_left.pack(side="left")
                ctk.CTkLabel(hdr_left, text=item.get("type", "task"),
                             font=("RuneScape Bold 12", 10, "bold"), text_color=TEXT).pack(anchor="w")
                ctk.CTkLabel(hdr_left, text=item.get("assigned_to", "?"),
                             font=("RuneScape Plain 11", 8), text_color=DIM).pack(anchor="w")
                ago = self._fmt_ago(item.get("created_at"))
                if ago:
                    ctk.CTkLabel(top, text=ago,
                                 font=("RuneScape Plain 11", 8), text_color=DIM).pack(side="right")

                ctk.CTkLabel(card, text=item.get("question", ""),
                             font=FONT, text_color=TEXT, wraplength=600,
                             anchor="w", justify="left").pack(fill="x", padx=8, pady=(4, 0))

                reply_frame = ctk.CTkFrame(card, fg_color="transparent")
                reply_frame.pack(fill="x", padx=8, pady=(4, 8))
                reply_frame.grid_columnconfigure(0, weight=1)

                reply_var = ctk.StringVar()
                entry = ctk.CTkEntry(reply_frame, textvariable=reply_var,
                                     font=FONT_SM, fg_color=BG3, border_color=ACCENT,
                                     placeholder_text="Type your response or click \u2728 to AI-draft\u2026")
                entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

                tid = item["id"]
                question = item.get("question", "")
                agent_name = item.get("assigned_to", "agent")
                entry.bind("<Return>", lambda e, t=tid, v=reply_var: self._send_human_response(t, v.get()))

                draft_btn = ctk.CTkButton(
                    reply_frame, text="\u2728", width=32, height=28,
                    fg_color=BG3, hover_color=BG2,
                    font=("RuneScape Plain 12", 13), text_color=GOLD,
                )
                draft_btn.grid(row=0, column=1, padx=(0, 4))
                draft_btn.configure(
                    command=lambda q=question, ag=agent_name, en=entry, db=draft_btn:
                        self._draft_comm_response(q, ag, en, db))

                ctk.CTkButton(
                    reply_frame, text="Send", width=60, height=28,
                    fg_color=ACCENT, hover_color=ACCENT_H,
                    command=lambda t=tid, v=reply_var: self._send_human_response(t, v.get()),
                ).grid(row=0, column=2)

                # Reply buttons: Local Chat + VS Code
                task_type = item.get("type", "task")
                btn_row = ctk.CTkFrame(card, fg_color="transparent")
                btn_row.pack(fill="x", padx=8, pady=(0, 6))

                ctk.CTkButton(
                    btn_row, text="\u2193 Reply in Local Chat", width=140, height=22,
                    fg_color=BG3, hover_color=BG2, font=FONT_XS, text_color=DIM,
                    command=lambda t=tid, q=question, ag=agent_name, tt=task_type:
                        self._load_hitl_to_chat(t, q, ag, tt),
                ).pack(side="left", padx=(0, 4))

                from ui.theme import PROVIDER_OAUTH
                ctk.CTkButton(
                    btn_row, text="\U0001f4bb Reply in VS Code", width=140, height=22,
                    fg_color=BG3, hover_color=BG2, font=FONT_XS, text_color=PROVIDER_OAUTH,
                    command=lambda t=tid, q=question, ag=agent_name:
                        self._reply_via_vscode(t, q, ag),
                ).pack(side="left")

            # Render security advisories
            for adv in advisories:
                card = ctk.CTkFrame(scroll, fg_color="#2a1a1a", corner_radius=6)
                card.pack(fill="x", padx=4, pady=3)
                self._comm_request_cards.append(card)
                self._comm_cards.append(card)

                top = ctk.CTkFrame(card, fg_color="transparent")
                top.pack(fill="x", padx=8, pady=(8, 0))
                ctk.CTkLabel(top, text=f"\U0001f512 {adv['title']}",
                             font=("RuneScape Bold 12", 10, "bold"), text_color=ORANGE).pack(side="left")
                ctk.CTkButton(
                    top, text="Approve", width=70, height=24,
                    fg_color=GREEN, hover_color="#388e3c",
                    font=FONT_SM, text_color="#ffffff",
                    command=lambda p=adv["path"]: self._approve_advisory(p),
                ).pack(side="right", padx=(4, 0))
                ctk.CTkButton(
                    top, text="Dismiss", width=70, height=24,
                    fg_color=BG3, hover_color=BG,
                    font=FONT_SM, text_color=DIM,
                    command=lambda p=adv["path"]: self._dismiss_advisory(p),
                ).pack(side="right")

                _counts = adv.get("counts", "")
                if _counts:
                    _sev_colors = {"HIGH": RED, "MEDIUM": ORANGE, "LOW": DIM}
                    counts_frame = ctk.CTkFrame(card, fg_color="transparent")
                    counts_frame.pack(fill="x", padx=12, pady=(4, 0), anchor="w")
                    for part in _counts.split(", "):
                        _color = DIM
                        for sev, col in _sev_colors.items():
                            if sev in part:
                                _color = col
                                break
                        ctk.CTkLabel(counts_frame, text=part.strip(),
                                     font=("Consolas", 9, "bold"), text_color=_color,
                                     ).pack(side="left", padx=(0, 8))

                _summary = adv.get("summary", "")
                if _summary:
                    ctk.CTkLabel(card, text=_summary, font=FONT_SM,
                                 text_color=DIM, wraplength=600,
                                 anchor="w", justify="left",
                                 ).pack(fill="x", padx=12, pady=(2, 8))

            # Update collapsible view after cards rendered
            self._update_comm_request_view()

        # Run async
        def _bg():
            data = _fetch()
            self._safe_after(0, lambda: _render(data))
        threading.Thread(target=_bg, daemon=True).start()

    def _send_human_response(self, task_id, response):
        """Send operator response to a WAITING_HUMAN task."""
        L = _launcher()
        if not response.strip():
            return
        def _bg():
            try:
                from data_access import FleetDB
                ok = FleetDB.send_human_response(L.FLEET_DIR / "fleet.db", task_id, response)
                if ok:
                    self._safe_after(0, lambda: (
                        self._log_output(f"Response sent to task #{task_id}"),
                        self._refresh_comm()
                    ))
                else:
                    self._safe_after(0, lambda: self._log_output(f"Task #{task_id} not found"))
            except Exception as e:
                self._safe_after(0, lambda: self._log_output(f"Send error: {e}"))
        threading.Thread(target=_bg, daemon=True).start()

    def _load_hitl_to_chat(self, task_id: int, question: str,
                           agent_name: str, task_type: str) -> None:
        """Pre-fill Manual Chat with HITL request context and arm the HITL loop."""
        # Switch to Fleet Comm tab if not already there
        try:
            self._tabs.set("Fleet Comm")
        except Exception:
            pass
        # Build a concise context string for the entry field (truncated)
        entry_text = question[:200]
        try:
            self._manual_chat_entry.delete(0, "end")
            self._manual_chat_entry.insert(0, entry_text)
            self._manual_chat_entry.focus_set()
        except Exception:
            return
        # Store active HITL task ID so _send_manual_chat can close the loop
        self._active_hitl_task_id = task_id
        self._active_hitl_agent = agent_name
        # Visual cue in chat display — full context
        self._manual_chat_display.configure(state="normal")
        self._manual_chat_display.insert(
            "end",
            f"\n\u2500\u2500\u2500 Loaded: {agent_name} Task #{task_id} ({task_type}) \u2500\u2500\u2500\n"
            f"{question}\n"
            f"\u2500\u2500\u2500 Type your response below and press Send \u2500\u2500\u2500\n"
        )
        self._manual_chat_display.configure(state="disabled")
        self._manual_chat_display.see("end")

    # ── Fleet Comm — AI-assisted response drafting ───────────────────────────

    def _draft_comm_response(self, question: str, agent: str,
                             entry: ctk.CTkEntry, btn: ctk.CTkButton) -> None:
        """Route \u2728 Draft to whichever AI provider the operator has selected."""
        provider = getattr(self, "_comm_provider_var", None)
        provider = provider.get() if provider else "\u26a1 Local"

        prompt = (
            f"You are BigEd \u2014 an AI fleet management system.\n"
            f"An autonomous agent named '{agent}' is waiting for operator input.\n\n"
            f"Agent's question:\n{question}\n\n"
            f"Draft a concise, actionable response the operator can send back. "
            f"1-3 sentences maximum. Output only the draft text \u2014 no preamble or sign-off."
        )

        btn.configure(text="\u2026", state="disabled")

        def _on_result(text: str) -> None:
            self._safe_after(0, lambda: (
                entry.delete(0, "end"),
                entry.insert(0, text.strip()),
                btn.configure(text="\u2728", state="normal"),
            ))

        def _on_error(err: str) -> None:
            self._safe_after(0, lambda: (
                self._log_output(f"AI draft error ({provider}): {err}"),
                btn.configure(text="\u2728", state="normal"),
            ))

        if "Claude" in provider:
            threading.Thread(target=self._draft_via_claude,
                             args=(prompt, _on_result, _on_error), daemon=True).start()
        elif "Gemini" in provider:
            threading.Thread(target=self._draft_via_gemini,
                             args=(prompt, _on_result, _on_error), daemon=True).start()
        else:
            threading.Thread(target=self._draft_via_local,
                             args=(prompt, _on_result, _on_error), daemon=True).start()

    def _draft_via_claude(self, prompt: str, on_result, on_error) -> None:
        L = _launcher()
        try:
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                try:
                    out, _ = self.wsl("echo $ANTHROPIC_API_KEY", capture=True)
                    key = out.strip()
                except Exception:
                    pass
            if not key or key.startswith("$"):
                on_error("ANTHROPIC_API_KEY not set \u2014 open Claude Console to configure it.")
                return
            import anthropic
            mcfg = L.load_model_cfg()
            model = mcfg.get("claude_model", "claude-haiku-4-5")
            client = anthropic.Anthropic(api_key=key)
            msg = client.messages.create(
                model=model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            on_result(msg.content[0].text)
        except Exception as e:
            on_error(str(e))

    def _draft_via_gemini(self, prompt: str, on_result, on_error) -> None:
        L = _launcher()
        try:
            key = os.environ.get("GEMINI_API_KEY", "")
            if not key:
                try:
                    out, _ = self.wsl("echo $GEMINI_API_KEY", capture=True)
                    key = out.strip()
                except Exception:
                    pass
            if not key or key.startswith("$"):
                on_error("GEMINI_API_KEY not set \u2014 open Gemini Console to configure it.")
                return
            from google import genai
            mcfg = L.load_model_cfg()
            client = genai.Client(api_key=key)
            resp = client.models.generate_content(
                model=mcfg.get("gemini_model", "gemini-2.0-flash"),
                contents=prompt,
            )
            on_result(resp.text)
        except Exception as e:
            on_error(str(e))

    def _draft_via_local(self, prompt: str, on_result, on_error) -> None:
        L = _launcher()
        try:
            mcfg = L.load_model_cfg()
            host  = mcfg.get("ollama_host", "http://localhost:11434")
            model = mcfg.get("local", "qwen3:8b")
            body = json.dumps({
                "model": model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": "5m",
            }).encode()
            req = urllib.request.Request(
                f"{host}/api/generate", data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
            on_result(data.get("response", "(empty response)"))
        except Exception as e:
            on_error(str(e))

    def _approve_advisory(self, path):
        """Approve a security advisory — dispatch security_apply."""
        try:
            adv_path = Path(path)
            if adv_path.exists():
                self._log_output(f"Dispatching security_apply for {adv_path.name}")
                payload = json.dumps({"advisory_file": adv_path.name})
                b64 = base64.b64encode(payload.encode()).decode()
                self._dispatch_raw("security_apply", payload, "security",
                                   None)
        except Exception as e:
            self._log_output(f"Approve error: {e}")

    def _dismiss_advisory(self, path):
        """Move advisory to dismissed/ subfolder."""
        try:
            adv_path = Path(path)
            if adv_path.exists():
                dismissed_dir = adv_path.parent / "dismissed"
                dismissed_dir.mkdir(exist_ok=True)
                adv_path.rename(dismissed_dir / adv_path.name)
                self._log_output(f"Dismissed {adv_path.name}")
                self._refresh_comm()
        except Exception as e:
            self._log_output(f"Dismiss error: {e}")
