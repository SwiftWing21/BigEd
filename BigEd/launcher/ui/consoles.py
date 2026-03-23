"""
BigEd CC — API Console classes (Claude, Gemini, Local/Ollama).
Extracted from launcher.py to reduce god-object complexity (TECH_DEBT 4.1).

Each console is a CTkToplevel chat window with:
- Fleet context injection (status, advisories, reports)
- DISPATCH: command parsing for fleet task execution
- Thinking animation during API calls
- API key management
"""
import base64
import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path

import customtkinter as ctk

# ─── Theme (single source of truth) ──────────────────────────────────────────
from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    GREEN, ORANGE, RED, MONO, FONT, FONT_SM, FONT_H,
)

# ─── Chat history persistence ─────────────────────────────────────────────────
HISTORY_DIR = Path(__file__).parent.parent / "data" / "console_history"

# ─── Lazy imports from launcher ──────────────────────────────────────────────
# These are resolved at runtime to avoid circular imports. The consoles call
# into the launcher module for fleet-level helpers that are shared with other
# parts of the UI (status parsing, WSL bridge, model config, etc.).

def _launcher():
    """Return the launcher module (import once, cache)."""
    import launcher as _mod
    return _mod


# ─── BigEd persona — first-run greeting & slash-command reference ─────────────

_BIGED_GREETING = """\
Hi, I'm BigEd — Big Edge Compute Command running on your hardware.

Here's what I bring to the table:
  🤖  73 autonomous AI skills — research, code review, security audits, and more
  ⚡  Dual-supervisor fleet — Dr. Ders monitors thermals, supervisor manages workers
  🔒  Privacy-first — all work stays on your machine; cloud AI is optional
  🧠  VRAM-aware routing — models scale automatically under GPU pressure

Ready to go:
  → Type  ?  anytime for a command reference
  → Press  Ctrl+K  to open the command palette (skills, agents, actions)
  → Switch to  Fleet Comm  to review tasks waiting on your input
  → Go to  Settings → Models  to select or install a different local model

Ask me anything about your fleet, or just describe a task and I'll handle it.\
"""

_BIGED_COMMANDS = """\
BigEd Command Reference
────────────────────────────────────────────────
  ?  /list               Show this command reference
  /help                  Overview of all help topics
  /help modules          Add or toggle UI modules (Accounts, CRM, Ingestion…)
  /help agents           How to prompt local agents for code or research tasks
  /help review           How to review agent output and approve / reject tasks
  /help skills           How to build, train, and evolve a custom skill
  /help dispatch         How to manually dispatch tasks to the fleet
  /figma                 Export current UI to Figma-compatible SVG

Context shortcuts  (inject live data into this chat):
  Fleet Status           Attach live agent + task counts
  Pending Advisories     Attach security findings awaiting review
  Recent Reports         Attach the latest generated reports

Keyboard shortcuts:
  Ctrl+K                 Command palette — skills, agents, system actions
  Ctrl+1 / 2 / 3        Jump to Command Center / Fleet / Fleet Comm tabs
  F5                     Refresh fleet status\
"""

_BIGED_HELP: dict[str, str] = {
    "": """\
BigEd Help — Topics
────────────────────────────────────────────────
  /help modules     Adding and toggling UI modules
  /help agents      Prompting local agents for code, research, or review work
  /help review      Reviewing agent output and handling WAITING_HUMAN tasks
  /help skills      Building, training, and evolving custom fleet skills
  /help dispatch    Dispatching tasks manually from this console\
""",

    "modules": """\
Module Integration — Adding New Tabs to BigEd
────────────────────────────────────────────────
Modules live in  BigEd/launcher/modules/  as  mod_<name>.py  files.

Each module is a class with:
  LABEL = "Tab Name"          # Shown in the tab bar
  def build_tab(self, parent) # Build your UI into this frame
  def on_refresh(self)        # Called on each refresh cycle (optional)

To enable or disable modules:
  Settings → Display → Visible Tabs

To scaffold a new module, just ask me:
  "Build me a module for tracking customer onboarding progress"
  I'll dispatch the code_write skill and drop the file into modules/.

After adding a module file, restart BigEd — your tab appears automatically.\
""",

    "agents": """\
Prompting Local Agents — Getting Work Done
────────────────────────────────────────────────
Describe the task here and I'll format and dispatch it for you.

Code work:
  "Review fleet/skills/my_skill.py for bugs and edge cases"
  "Refactor the auth module to use async/await"
  → Routes to: code_review / code_refactor  (Sonnet)

Research:
  "Research best practices for VRAM management in Ollama"
  "Compare the top 5 Python HTTP client libraries"
  → Routes to: researcher  (Brave Search + Sonnet)

Skill building:
  "Build a skill that monitors disk usage and alerts on thresholds"
  "Evolve the summarize skill to support multi-document chunking"
  → Routes to: skill_evolve / code_write  (Opus)

Security:
  "Run a security audit on the fleet API endpoints"
  → Routes to: security_audit  (Sonnet)

Or dispatch directly: Ctrl+K → type the skill name.\
""",

    "review": """\
Reviewing Agent Output
────────────────────────────────────────────────
Fleet Comm tab shows all WAITING_HUMAN tasks — agents that need your input.

When an agent is waiting:
  1. Click the orange task card to expand it
  2. Read the agent's question and any attached context
  3. Type your response and press Enter or click Send
  4. The agent resumes automatically

Security advisories (dark red cards):
  • Approve  — dispatches security_apply to patch the finding
  • Dismiss  — moves advisory to the dismissed folder

To review completed work in this chat:
  • Click  Recent Reports  above to inject the latest output
  • Or ask me: "Summarize the last 3 completed tasks"
  • Settings → Review tab → configure the evaluator model (Claude / Gemini / Local)\
""",

    "skills": """\
Building and Evolving Skills
────────────────────────────────────────────────
Skills live in  fleet/skills/  as Python files:

  class MySkill:
      SKILL_NAME = "my_skill"
      COMPLEXITY = "medium"         # simple / medium / complex
      def run(self, payload):       # Your logic here
          return {"result": ...}

To build a skill from scratch, ask me:
  "Build a skill that converts Markdown files to formatted PDFs"
  I'll scaffold the file via DISPATCH: code_write and place it in fleet/skills/.

To evolve an existing skill, ask me:
  "Evolve the summarize skill to handle documents over 100 pages"
  The skill_evolve agent runs the skill, scores output quality, generates
  improvements, and writes the updated version back — with an __evolve_log__.

To train on your own data:
  Settings → Ingestion → point at your folder
  The ingest skill indexes it for RAG queries.
  Then ask agents: "Using the ingested knowledge, find references to X"\
""",

    "dispatch": """\
Dispatching Tasks Manually
────────────────────────────────────────────────
Three ways to dispatch:

1. Natural language (easiest) — just ask me here and I'll dispatch:
   "Summarize the last report" → DISPATCH: {"skill": "summarize", ...}

2. Ctrl+K command palette:
   Type a skill name (e.g. "code_review") to dispatch directly
   Type @agent_name to send a message to a specific agent

3. Sidebar task bar:
   Type any prompt in the task entry and press Enter
   The fleet routes it to the best-matching agent automatically

Skill complexity and routing:
  Simple  → qwen3:4b  (~89 tok/s)   e.g. flashcard, rag_query, summarize
  Medium  → qwen3:8b  (~45 tok/s)   e.g. code_review, discuss, security_audit
  Complex → Claude / Gemini / Opus   e.g. plan_workload, code_write, legal_draft\
""",
}


# ─── Console Base Class ─────────────────────────────────────────────────────
class _ConsoleBase(ctk.CTkToplevel):
    """Shared base for Claude and Gemini chat consoles."""
    SYSTEM_PROMPT = """\
You are BigEd — the AI interface for Big Edge Compute Command, a local autonomous agent \
fleet running on the operator's own hardware. Introduce yourself as "BigEd" for short.

Your role is to help the operator manage, review, and direct the fleet via natural language.

Your capabilities:
- Read and interpret fleet status, agent health, and the task queue
- Review security advisories and findings
- Dispatch fleet tasks by outputting a JSON block the UI will execute
- Guide the operator on module integration, skill building, and agent prompting
- Give strategic recommendations on code, research, business ops, and security posture
- Answer questions about the fleet's agents, skills, and findings

To dispatch a fleet task, output a line in this exact format (the UI parses it):
DISPATCH: {"skill": "skill_name", "payload": {...}}

Keep responses concise and action-oriented. Lead with the most important insight or next step.
"""
    # Subclasses override these
    TITLE = ""
    HEADER_LABEL = ""
    HEADER_COLOR = BG3
    HEADER_TEXT_COLOR = GOLD
    CHAT_BG = BG2
    CTX_BTN_FG = BG3
    CTX_BTN_HOVER = BG
    SEND_BTN_FG = ACCENT
    SEND_BTN_HOVER = ACCENT_H
    ROLE_COLORS = {"user": GOLD, "system": DIM}
    ROLE_PREFIXES = {"user": "You", "system": "System"}
    ASSISTANT_ROLE = "assistant"

    def __init__(self, parent):
        super().__init__(parent)
        self.title(self.TITLE)
        self.geometry("820x620")
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.grab_set()

        L = _launcher()
        ico = L.HERE / "brick.ico"
        if ico.exists():
            try: self.iconbitmap(str(ico))
            except Exception: pass

        self._history = []
        self._api_key = self._get_api_key()
        self._mcfg = L.load_model_cfg()
        self._build_ui()
        # Restore persisted chat history (subclasses set _console_name before super().__init__)
        if getattr(self, '_console_name', None):
            self._history = self._load_history()
            for msg in self._history:
                role = msg.get("role", "user")
                # Map "assistant" to console-specific role for display
                if role == "assistant":
                    role = self.ASSISTANT_ROLE
                self._append(role, msg.get("content", ""))
        self._on_init()
        # Show BigEd intro on first session (no prior history for this console)
        self.after(80, self._maybe_greet)

    def _get_api_key(self):
        raise NotImplementedError

    def _on_init(self):
        raise NotImplementedError

    # ── History persistence ────────────────────────────────────────────────────
    def _history_path(self):
        """Path for this console's history file."""
        name = getattr(self, '_console_name', 'unknown')
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        return HISTORY_DIR / f"{name}_history.jsonl"

    def _save_history(self):
        """Save chat history to disk."""
        try:
            path = self._history_path()
            with open(path, "w", encoding="utf-8") as f:
                for msg in self._history[-100:]:  # keep last 100 messages
                    f.write(json.dumps(msg) + "\n")
        except Exception:
            pass

    def _load_history(self):
        """Load chat history from disk."""
        try:
            path = self._history_path()
            if path.exists():
                messages = []
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        messages.append(json.loads(line))
                return messages[-100:]
        except Exception:
            pass
        return []

    def _clear_history(self):
        """Clear chat history from disk and memory."""
        self._history = []
        try:
            path = self._history_path()
            if path.exists():
                path.unlink()
        except Exception:
            pass
        # Clear the chat display
        self._chat.configure(state="normal")
        self._chat.delete("1.0", "end")
        self._chat.configure(state="disabled")
        self._append("system", "Chat history cleared.")

    def _get_model_display(self) -> str:
        raise NotImplementedError

    def _build_model_widget(self, hdr):
        """Place the model indicator in the header. Override for interactive selectors."""
        self._model_lbl = ctk.CTkLabel(
            hdr, text=self._get_model_display(), font=("RuneScape Plain 11", 9), text_color=DIM)
        self._model_lbl.grid(row=0, column=1, padx=8, sticky="e")

    def _get_key_env_name(self) -> str:
        """Return the env var name for this console's API key."""
        raise NotImplementedError

    def _set_key_dialog(self):
        """Inline key entry — sets key for this session and optionally saves to ~/.secrets."""
        L = _launcher()
        win = ctk.CTkToplevel(self)
        win.title("Set API Key")
        win.geometry("480x190")
        win.resizable(False, False)
        win.configure(fg_color=BG)
        win.grab_set()
        win.lift()

        env_name = self._get_key_env_name()
        ctk.CTkLabel(win, text=f"Paste your {env_name}:",
                     font=FONT_SM, text_color=DIM
                     ).pack(padx=14, pady=(14, 4), anchor="w")
        entry = ctk.CTkEntry(win, font=MONO, fg_color=BG2, border_color="#444",
                             text_color=TEXT, show="*")
        entry.pack(padx=14, fill="x")

        save_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(win, text=f"Save to ~/.secrets (export {env_name}=...)",
                        variable=save_var, font=FONT_SM, text_color=DIM,
                        fg_color=ACCENT, hover_color=ACCENT_H
                        ).pack(padx=14, pady=(8, 4), anchor="w")

        def _apply():
            key = entry.get().strip()
            if not key:
                return
            self._api_key = key
            if save_var.get():
                b64_val = base64.b64encode(key.encode()).decode()
                L.wsl_bg(
                    f"~/.local/bin/uv run python lead_client.py secret set {env_name} {b64_val} --b64",
                    lambda o, e: None,
                )
            if hasattr(self, '_init_model'):
                self._init_model()
            self._append("system", f"✓ {env_name} set — ready.")
            win.destroy()

        ctk.CTkButton(win, text="Apply", font=FONT_SM, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_H, command=_apply
                      ).pack(padx=14, pady=(6, 14), fill="x")

    def _get_context_buttons(self) -> list:
        """Return list of (label, callback) for context inject buttons."""
        return [
            ("Fleet Status",      self._inject_status),
            ("Pending Advisories", self._inject_advisories),
            ("Recent Reports",    self._inject_reports),
        ]

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color=self.HEADER_COLOR, height=46, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text=self.HEADER_LABEL,
                     font=("RuneScape Bold 12", 13, "bold"), text_color=self.HEADER_TEXT_COLOR
                     ).grid(row=0, column=0, padx=14, pady=10, sticky="w")
        self._build_model_widget(hdr)
        ctk.CTkButton(
            hdr, text="🗑 Clear History", font=("RuneScape Plain 11", 9), width=100, height=26,
            fg_color=BG3, hover_color=BG, command=self._clear_history
        ).grid(row=0, column=2, padx=(0, 6))
        if self._get_key_env_name():
            ctk.CTkButton(
                hdr, text="🔑 Set Key", font=("RuneScape Plain 11", 9), width=80, height=26,
                fg_color=BG3, hover_color=BG, command=self._set_key_dialog
            ).grid(row=0, column=3, padx=(0, 10))

        # Chat history
        self._chat = ctk.CTkTextbox(
            self, font=("RuneScape Plain 12", 11), fg_color=self.CHAT_BG,
            text_color=TEXT, wrap="word", corner_radius=0)
        self._chat.grid(row=1, column=0, sticky="nsew")
        self._chat.configure(state="disabled")

        # Context inject buttons
        ctx_bar = ctk.CTkFrame(self, fg_color=BG3, height=34, corner_radius=0)
        ctx_bar.grid(row=2, column=0, sticky="ew")
        ctx_bar.grid_propagate(False)
        ctk.CTkLabel(ctx_bar, text="Inject context:", font=("RuneScape Plain 11", 9),
                     text_color=DIM).grid(row=0, column=0, padx=(10, 6), pady=6)
        for i, (lbl, fn) in enumerate(self._get_context_buttons()):
            ctk.CTkButton(ctx_bar, text=lbl, font=("RuneScape Plain 11", 9), height=22, width=0,
                          fg_color=self.CTX_BTN_FG, hover_color=self.CTX_BTN_HOVER,
                          command=fn).grid(row=0, column=i + 1, padx=3, pady=6)

        # Input bar
        bar = ctk.CTkFrame(self, fg_color=BG3, height=52, corner_radius=0)
        bar.grid(row=3, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)

        self._input = ctk.CTkEntry(
            bar, font=("RuneScape Plain 12", 11), fg_color=BG,
            border_color="#334", text_color=TEXT,
            placeholder_text="Type a message...")
        self._input.grid(row=0, column=0, padx=(10, 6), pady=10, sticky="ew")
        self._input.bind("<Return>", lambda e: self._send())

        self._send_btn = ctk.CTkButton(
            bar, text="Send", font=("RuneScape Bold 12", 11, "bold"),
            width=80, height=32,
            fg_color=self.SEND_BTN_FG, hover_color=self.SEND_BTN_HOVER,
            command=self._send)
        self._send_btn.grid(row=0, column=1, padx=(0, 10), pady=10)

        self._thinking_lbl = ctk.CTkLabel(
            bar, text="", font=("RuneScape Plain 11", 9), text_color=DIM)
        self._thinking_lbl.grid(row=0, column=2, padx=6)

    # ── Context injectors ─────────────────────────────────────────────────────
    def _inject_status(self):
        L = _launcher()
        txt = L.STATUS_MD.read_text() if L.STATUS_MD.exists() else "STATUS.md not found"
        self._input.insert("end", f"\n\n[Fleet Status]\n{txt[:800]}")

    def _inject_advisories(self):
        L = _launcher()
        if not L.PENDING_DIR.exists():
            self._input.insert("end", "\n\n[No pending advisories]")
            return
        files = list(L.PENDING_DIR.glob("advisory_*.md"))
        content = "\n\n".join(
            f.read_text(encoding="utf-8", errors="ignore")[:400]
            for f in files[:2])
        self._input.insert("end", f"\n\n[Pending Advisories]\n{content}")

    def _inject_reports(self):
        L = _launcher()
        if not L.REPORTS_DIR.exists():
            self._input.insert("end", "\n\n[No reports]")
            return
        files = sorted(L.REPORTS_DIR.glob("*.md"), reverse=True)[:2]
        content = "\n\n".join(
            f"--- {f.name} ---\n" + f.read_text(encoding="utf-8", errors="ignore")[:400]
            for f in files)
        self._input.insert("end", f"\n\n[Recent Reports]\n{content}")

    # ── Chat ──────────────────────────────────────────────────────────────────
    # ── Thinking animation ─────────────────────────────────────────────────────
    _DOTS = ("·", "··", "···", "··")

    def _start_thinking_animation(self, label: str, color: str):
        self._thinking_anim_label = label
        self._thinking_anim_color = color
        self._thinking_anim_step  = 0
        self._thinking_anim_id    = None
        self._thinking_anim_tick()

    def _thinking_anim_tick(self):
        dots = self._DOTS[self._thinking_anim_step % len(self._DOTS)]
        self._thinking_lbl.configure(
            text=f"{self._thinking_anim_label}{dots}",
            text_color=self._thinking_anim_color,
        )
        self._thinking_anim_step += 1
        self._thinking_anim_id = self.after(420, self._thinking_anim_tick)

    def _stop_thinking_animation(self):
        if self._thinking_anim_id is not None:
            self.after_cancel(self._thinking_anim_id)
            self._thinking_anim_id = None
        self._thinking_lbl.configure(text="")

    def _thinking_label_for(self, text: str) -> tuple[str, str]:
        """Return (label, color) based on message content. Override in subclasses."""
        return "● drafting", ORANGE

    # ── BigEd local command handling ──────────────────────────────────────────
    @staticmethod
    def _is_local_command(text: str) -> bool:
        """True if the message is a BigEd built-in command (no API call needed)."""
        t = text.strip().lower()
        return t in ("?", "/list", "/figma") or t.startswith("/help")

    def _handle_command(self, text: str) -> None:
        """Render a BigEd response locally without calling the API."""
        t = text.strip().lower()
        if t in ("?", "/list"):
            self._append("biged", _BIGED_COMMANDS)
        elif t == "/figma":
            self._export_to_figma_svg()
        elif t == "/help":
            self._append("biged", _BIGED_HELP[""])
        elif t.startswith("/help "):
            topic = t[len("/help "):].strip()
            if topic in _BIGED_HELP:
                self._append("biged", _BIGED_HELP[topic])
            else:
                known = ", ".join(f"/help {k}" for k in _BIGED_HELP if k)
                self._append("biged",
                    f"Unknown help topic: '{topic}'\nAvailable topics: {known}")

    def _maybe_greet(self) -> None:
        """Show the BigEd intro if this console has no prior history."""
        if not self._history:
            self._append("biged", _BIGED_GREETING)

    def _export_to_figma_svg(self) -> None:
        """Dumps the parent launcher window's widget tree to an SVG file for Figma."""
        try:
            L = _launcher()
            reports_dir = L.HERE.parent / "knowledge" / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            filename = reports_dir / f"ui_export_{int(time.time())}.svg"

            # Export the main application window (parent) or fallback to console
            target_window = self.master if self.master else self
            target_window.update_idletasks()

            width = target_window.winfo_width()
            height = target_window.winfo_height()
            svg_elements = []

            def _get_hex_color(color_attr):
                if isinstance(color_attr, (list, tuple)):
                    mode_idx = 1 if ctk.get_appearance_mode().lower() == "dark" else 0
                    color_attr = color_attr[mode_idx]
                if color_attr and isinstance(color_attr, str):
                    return color_attr
                return "none"

            def _walk_tree(w, abs_x, abs_y):
                if not w.winfo_ismapped():
                    return
                
                w_x = w.winfo_x()
                w_y = w.winfo_y()
                curr_x = abs_x + w_x
                curr_y = abs_y + w_y
                w_w = w.winfo_width()
                w_h = w.winfo_height()

                bg_color = "none"
                try:
                    if hasattr(w, "cget"):
                        for attr in ["fg_color", "bg_color"]:
                            try:
                                val = w.cget(attr)
                                if val and str(val).lower() != "transparent":
                                    bg_color = _get_hex_color(val)
                                    break
                            except Exception: pass
                except Exception: pass

                if bg_color != "none" and w_w > 0 and w_h > 0:
                    svg_elements.append(f'  <rect x="{curr_x}" y="{curr_y}" width="{w_w}" height="{w_h}" fill="{bg_color}" rx="4" />')

                try:
                    if hasattr(w, "cget"):
                        text = w.cget("text")
                        if isinstance(text, str) and text.strip():
                            tc = _get_hex_color(w.cget("text_color")) if hasattr(w, "cget") else "#FFFFFF"
                            if tc == "none": tc = "#FFFFFF"
                            text_clean = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                            svg_elements.append(f'  <text x="{curr_x + 8}" y="{curr_y + (w_h/2) + 4}" fill="{tc}" font-family="sans-serif" font-size="12">{text_clean}</text>')
                except Exception: pass

                for child in w.winfo_children():
                    _walk_tree(child, curr_x, curr_y)

            _walk_tree(target_window, 0, 0)

            svg_content = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">\n'
            svg_content += "\n".join(svg_elements)
            svg_content += '\n</svg>'

            filename.write_text(svg_content, encoding="utf-8")
            self._append("system", f"UI exported successfully to SVG!\n\nLocation: {filename}\n\nDrag and drop this file directly into Figma to start improving the design.")
        except Exception as e:
            self._append("system", f"Failed to export UI: {e}")

    # ── Chat ──────────────────────────────────────────────────────────────────
    def _send(self):
        text = self._input.get().strip()
        if not text:
            return
        # Local commands — handled instantly, no API key required
        if self._is_local_command(text):
            self._input.delete(0, "end")
            self._append("user", text)
            self._handle_command(text)
            return
        # Normal API flow
        if not self._api_key:
            return
        if not self._can_send():
            return
        self._input.delete(0, "end")
        self._append("user", text)
        self._send_btn.configure(state="disabled")
        label, color = self._thinking_label_for(text)
        self._start_thinking_animation(label, color)
        self._do_send(text)

    def _can_send(self) -> bool:
        return True

    def _fleet_context_prompt(self) -> str:
        """Build system prompt with live fleet context injected."""
        L = _launcher()
        try:
            status = L.parse_status()
            agents = status.get("agents", [])
            busy = sum(1 for a in agents if a["status"] == "BUSY")
            idle = sum(1 for a in agents if a["status"] == "IDLE")
            t = status.get("tasks", {})
            hw_status = status.get("hw_supervisor_status", "OFFLINE")
            context = (
                f"\n\n[Fleet Context — live]\n"
                f"Agents: {len(agents)} total, {idle} idle, {busy} busy\n"
                f"Tasks: {t.get('Pending', 0)} pending, {t.get('Running', 0)} running, "
                f"{t.get('Done', 0)} done, {t.get('Failed', 0)} failed\n"
                f"HW Supervisor: {hw_status}\n"
                f"Supervisor: {status.get('supervisor_status', 'OFFLINE')}")
            return self.SYSTEM_PROMPT + context
        except Exception:
            return self.SYSTEM_PROMPT

    def _do_send(self, text: str):
        raise NotImplementedError

    def _on_reply(self, reply: str):
        self._stop_thinking_animation()
        self._append(self.ASSISTANT_ROLE, reply)
        self._save_history()
        self._send_btn.configure(state="normal")

        # Parse and execute any DISPATCH: lines
        for m in re.finditer(r'DISPATCH:\s*(\{.*?\})', reply, re.DOTALL):
            try:
                data = json.loads(m.group(1))
                skill   = data.get("skill", "")
                payload = data.get("payload", {})
                if skill:
                    self._execute_dispatch(skill, payload)
            except Exception:
                pass

    def _execute_dispatch(self, skill: str, payload: dict):
        L = _launcher()
        safe_skill = L._shell_safe(skill)
        b64 = base64.b64encode(json.dumps(payload).encode()).decode()
        cmd = f"~/.local/bin/uv run python lead_client.py dispatch {safe_skill} {b64} --b64 --priority 10"

        def _on_dispatch(o, e):
            tid_str = o.split()[-1] if o else "?"
            self.after(0, lambda: self._append(
                "system", f"✓ Dispatched {safe_skill} (task {tid_str}, priority 10)"))
            # Poll for task result in background
            try:
                tid = int(tid_str)
                threading.Thread(target=self._poll_task_result,
                                 args=(tid, safe_skill), daemon=True).start()
            except (ValueError, TypeError):
                pass

        L.wsl_bg(cmd, _on_dispatch)

    def _poll_task_result(self, task_id: int, skill: str, timeout: int = 60):
        """Poll DB for task completion and show result in chat."""
        L = _launcher()
        deadline = time.time() + timeout
        cmd = f"~/.local/bin/uv run python lead_client.py result {task_id}"
        while time.time() < deadline:
            time.sleep(2)
            try:
                out, err = L.wsl(cmd, capture=True, timeout=5)
                if out and "Status:" in out:
                    if "Status: DONE" in out:
                        brief = out[:300]
                        if self.winfo_exists():
                            self.after(0, lambda b=brief: self._append(
                                "system", f"Task {task_id} ({skill}) completed:\n{b}"))
                        return
                    elif "Status: FAILED" in out:
                        brief = out[:200]
                        if self.winfo_exists():
                            self.after(0, lambda b=brief: self._append(
                                "system", f"Task {task_id} ({skill}) failed: {b}"))
                        return
            except Exception:
                pass
        # Timeout — task still running, notify user
        if self.winfo_exists():
            self.after(0, lambda: self._append(
                "system", f"Task {task_id} ({skill}) — still running after {timeout}s. "
                "Check fleet status for updates."))

    def _append(self, role: str, text: str):
        # "biged" is a synthetic role for local persona responses — always "BigEd"
        prefix = "BigEd" if role == "biged" else self.ROLE_PREFIXES.get(role, role.title())
        self._chat.configure(state="normal")
        self._chat.insert("end", f"\n{prefix}:\n")
        self._chat.insert("end", f"{text}\n\n")
        self._chat.see("end")
        self._chat.configure(state="disabled")


# ─── Claude Console ───────────────────────────────────────────────────────────
class ClaudeConsole(_ConsoleBase):
    SYSTEM_PROMPT = _ConsoleBase.SYSTEM_PROMPT.replace(
        "Your capabilities:", (
            "You are the executive AI advisor (C-suite) for a local autonomous agent fleet "
            "called BigEd CC.\n\nYour capabilities:"))
    TITLE = "BigEd CC — Claude Console"
    HEADER_LABEL = "🤖  CLAUDE CONSOLE  —  C-Suite Mode"
    HEADER_COLOR = "#0d0d1a"
    HEADER_TEXT_COLOR = "#7b9fff"
    CHAT_BG = "#0f0f1f"
    CTX_BTN_FG = "#1a1a2e"
    CTX_BTN_HOVER = "#252540"
    SEND_BTN_FG = "#334488"
    SEND_BTN_HOVER = "#445599"
    ASSISTANT_ROLE = "claude"
    ROLE_PREFIXES = {"user": "You", "claude": "Claude", "system": "System"}

    def __init__(self, parent):
        self._console_name = "claude"
        super().__init__(parent)

    def _get_api_key(self):
        return os.environ.get("ANTHROPIC_API_KEY") or None

    # Available API models — label: model_id
    _CLAUDE_MODELS = {
        "Haiku 4.5  · fast / cheap":   "claude-haiku-4-5-20251001",
        "Sonnet 4.6 · balanced":        "claude-sonnet-4-6",
        "Opus 4.6   · most capable":    "claude-opus-4-6",
    }

    def _get_key_env_name(self):
        return "ANTHROPIC_API_KEY"

    def _get_model_display(self):
        return self._mcfg["claude_model"]

    def _build_model_widget(self, hdr):
        default_id  = self._mcfg.get("claude_model", "claude-sonnet-4-6")
        # Find the label whose value matches the configured model (fall back to Sonnet)
        default_lbl = next(
            (lbl for lbl, mid in self._CLAUDE_MODELS.items() if mid == default_id),
            "Sonnet 4.6 · balanced",
        )
        self._model_var = ctk.StringVar(value=default_lbl)
        ctk.CTkOptionMenu(
            hdr,
            values=list(self._CLAUDE_MODELS),
            variable=self._model_var,
            font=("RuneScape Plain 11", 9),
            fg_color=BG3,
            button_color=BG2,
            button_hover_color=BG,
            dropdown_fg_color=BG2,
            dropdown_hover_color=BG3,
            text_color=TEXT,
            width=200,
            height=26,
            dynamic_resizing=False,
        ).grid(row=0, column=1, padx=8, sticky="e")

    def _on_init(self):
        if not self._api_key:
            self._append("system",
                         "⚠  ANTHROPIC_API_KEY not found in ~/.secrets.\n"
                         "Click 🔑 Set Key in the header to enter your key now.\n\n"
                         "Note: this console requires an Anthropic API key (console.anthropic.com).\n"
                         "Claude.ai subscriptions (claude.ai) are separate — they cannot be used here.")
        else:
            self._append("system", "Claude Console ready — C-suite mode active.\n"
                         "Type a message or ask Claude to manage the fleet.")

    def _get_context_buttons(self):
        return super()._get_context_buttons() + [
            ("Key Status", self._inject_key_status),
        ]

    def _inject_key_status(self):
        try:
            secrets_file = Path.home() / ".secrets"
            if not secrets_file.exists():
                self._input.insert("end", "\n\n[No ~/.secrets file found]")
                return
            keys = []
            for line in secrets_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip() and not line.strip().startswith("#") and "=" in line:
                    keys.append(line.replace("export ", "").split("=")[0].strip())
            self._input.insert("end", f"\n\n[Configured Keys]\n" + ", ".join(keys))
        except Exception:
            self._input.insert("end", "\n\n[Could not read key status]")

    def _do_send(self, text: str):
        self._history.append({"role": "user", "content": text})
        self._save_history()
        threading.Thread(target=self._call_api, daemon=True).start()

    def _call_api(self):
        model_id = self._CLAUDE_MODELS.get(
            self._model_var.get(), self._mcfg.get("claude_model", "claude-sonnet-4-6"))
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._api_key)
            msg = client.messages.create(
                model=model_id,
                max_tokens=1024,
                system=self._fleet_context_prompt(),
                messages=self._history[-20:],
            )
            reply = msg.content[0].text
            self._history.append({"role": "assistant", "content": reply})
            self.after(0, lambda: self._on_reply(reply))
        except Exception as e:
            self.after(0, lambda: self._on_reply(f"[API Error] {e}"))


# ─── Gemini Console ───────────────────────────────────────────────────────────
class GeminiConsole(_ConsoleBase):
    TITLE = "BigEd CC — Gemini Console"
    HEADER_LABEL = "✦  GEMINI CONSOLE"
    HEADER_COLOR = "#0d1a0d"
    HEADER_TEXT_COLOR = "#4db86b"
    CHAT_BG = "#0d1a0d"
    CTX_BTN_FG = "#1a2a1a"
    CTX_BTN_HOVER = "#253525"
    SEND_BTN_FG = "#2a5a2a"
    SEND_BTN_HOVER = "#3a6a3a"
    ASSISTANT_ROLE = "gemini"
    ROLE_PREFIXES = {"user": "You", "gemini": "Gemini", "system": "System"}

    def __init__(self, parent):
        self._console_name = "gemini"
        self._chat_session = None
        super().__init__(parent)

    def _get_api_key(self):
        return os.environ.get("GEMINI_API_KEY") or None

    def _get_key_env_name(self):
        return "GEMINI_API_KEY"

    def _get_model_display(self):
        return self._mcfg["gemini_model"]

    def _on_init(self):
        if not self._api_key:
            self._append("system", "⚠  GEMINI_API_KEY not found in ~/.secrets.\n"
                         "Click 🔑 Set Key in the header to enter your key now.")
        else:
            self._init_model()
            self._append("system", "Gemini Console ready.\nType a message to begin.")

    def _get_context_buttons(self):
        return super()._get_context_buttons() + [
            ("Key Status", self._inject_key_status),
        ]

    def _inject_key_status(self):
        try:
            secrets_file = Path.home() / ".secrets"
            if not secrets_file.exists():
                self._input.insert("end", "\n\n[No ~/.secrets file found]")
                return
            keys = []
            for line in secrets_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip() and not line.strip().startswith("#") and "=" in line:
                    keys.append(line.replace("export ", "").split("=")[0].strip())
            self._input.insert("end", "\n\n[Configured Keys]\n" + ", ".join(keys))
        except Exception:
            self._input.insert("end", "\n\n[Could not read key status]")

    def _init_model(self):
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=self._api_key)
            self._chat_session = client.chats.create(
                model=self._mcfg["gemini_model"],
                config=types.GenerateContentConfig(
                    system_instruction=self.SYSTEM_PROMPT,
                    temperature=0.2,
                ),
            )
        except Exception as e:
            self._append("system", f"[Init error] {e}")

    def _thinking_label_for(self, text: str) -> tuple[str, str]:
        if text.strip().lower().startswith("/think"):
            return "◈ extended thinking", "#4db86b"   # green — matches Gemini header colour
        return "● drafting", ORANGE

    def _can_send(self) -> bool:
        return self._chat_session is not None

    def _do_send(self, text: str):
        self._history.append({"role": "user", "content": text})
        self._save_history()
        threading.Thread(target=self._call_api, args=(text,), daemon=True).start()

    def _call_api(self, text: str):
        try:
            # Inject live fleet context as a preamble to the user message
            context = self._fleet_context_prompt()[len(self.SYSTEM_PROMPT):]
            enriched = f"{context}\n\nUser: {text}" if context else text
            response = self._chat_session.send_message(enriched)
            reply = response.text
            self._history.append({"role": "assistant", "content": reply})
            self.after(0, lambda: self._on_reply(reply))
        except Exception as e:
            self.after(0, lambda: self._on_reply(f"[API Error] {e}"))


# ─── Local (Ollama) Console ───────────────────────────────────────────────────
class LocalConsole(_ConsoleBase):
    TITLE = "BigEd CC — Local Console"
    HEADER_LABEL = "⚡  LOCAL CONSOLE  —  Ollama"
    HEADER_COLOR = "#1a1510"
    HEADER_TEXT_COLOR = "#d4a84b"
    CHAT_BG = "#1a1510"
    CTX_BTN_FG = "#2a2010"
    CTX_BTN_HOVER = "#3a3020"
    SEND_BTN_FG = "#6b4c1a"
    SEND_BTN_HOVER = "#8b6c2a"
    ASSISTANT_ROLE = "ollama"
    ROLE_PREFIXES = {"user": "You", "ollama": "Ollama", "system": "System"}

    def __init__(self, parent):
        self._console_name = "local"
        super().__init__(parent)

    def _get_api_key(self):
        return "local"  # no key needed

    def _get_key_env_name(self):
        return ""

    def _get_model_display(self):
        return self._mcfg.get("local", "qwen3:8b")

    def _build_model_widget(self, hdr):
        self._model_lbl = ctk.CTkLabel(
            hdr, text=self._get_model_display(), font=("RuneScape Plain 11", 9), text_color=DIM)
        self._model_lbl.grid(row=0, column=1, padx=8, sticky="e")

    def _set_key_dialog(self):
        pass  # no key needed for local

    def _on_init(self):
        host = self._mcfg.get("ollama_host", "http://localhost:11434")
        model = self._get_model_display()
        try:
            req = urllib.request.Request(f"{host}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=3) as r:
                data = json.loads(r.read())
                loaded = [m["name"] for m in data.get("models", [])]
            if loaded:
                self._append("system",
                             f"Local Console ready — {model} via Ollama.\n"
                             f"Loaded models: {', '.join(loaded)}\n"
                             "Type a message or ask Ollama to manage the fleet.")
            else:
                self._append("system",
                             f"Ollama is running but no models loaded.\n"
                             f"Model '{model}' will be loaded on first message.")
        except Exception:
            self._append("system",
                         f"⚠ Ollama not reachable at {host}.\n"
                         "Start Ollama from the main panel, then reopen this console.")

    def _do_send(self, text: str):
        self._history.append({"role": "user", "content": text})
        self._save_history()
        threading.Thread(target=self._call_ollama, daemon=True).start()

    def _call_ollama(self):
        host = self._mcfg.get("ollama_host", "http://localhost:11434")
        model = self._mcfg.get("conductor_model", self._mcfg.get("local", "qwen3:8b"))
        messages = [{"role": "system", "content": self._fleet_context_prompt()}] + self._history[-20:]
        body = json.dumps({
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": "24h",
        }).encode()
        req = urllib.request.Request(
            f"{host}/api/chat", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            reply = data.get("message", {}).get("content", "(empty response)")
            self._history.append({"role": "assistant", "content": reply})
            self.after(0, lambda: self._on_reply(reply))
        except Exception as e:
            self.after(0, lambda: self._on_reply(f"[Ollama Error] {e}"))
