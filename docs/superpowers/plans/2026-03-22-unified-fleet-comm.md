# Unified Fleet Comm Console — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate Fleet Comm into a single-stream, multi-provider console with clear model indicators, usage tracking, quarantine for new models, HITL-via-VS-Code workflow, and relocate power-user consoles to Settings.

**Architecture:** Provider pill buttons (Local/Claude/Gemini/OAuth) select the active API. Messages are tagged and color-coded by provider in one chat stream. A compact usage status bar shows cost/performance. Agent HITL requests can be answered in-chat or via VS Code with a file-based response flow.

**Tech Stack:** Python 3, customtkinter, Ollama API, Anthropic SDK, Google GenAI SDK, fleet.db (SQLite), threading, pathlib

**Spec:** `docs/superpowers/specs/2026-03-22-unified-fleet-comm-design.md`

---

### Task 1: Provider Color Constants

**Files:**
- Modify: `BigEd/launcher/ui/theme.py`

- [ ] **Step 1: Add provider color constants to theme.py**

After the existing color constants (GOLD, GREEN, etc.), add:

```python
# Provider colors (for Fleet Comm unified console)
PROVIDER_LOCAL = "#d4a84b"   # gold — Ollama
PROVIDER_CLAUDE = "#6b8afd"  # blue — Anthropic
PROVIDER_GEMINI = "#4caf50"  # green — Google
PROVIDER_OAUTH = "#9c7cfc"   # purple — VS Code OAuth
PROVIDER_BG_LOCAL = "#2a2010"
PROVIDER_BG_CLAUDE = "#0d0d2a"
PROVIDER_BG_GEMINI = "#0d1a0d"
```

- [ ] **Step 2: Commit**

```bash
git add BigEd/launcher/ui/theme.py
git commit -m "feat(theme): add provider color constants for unified Fleet Comm"
```

---

### Task 2: Session Usage Tracker

**Files:**
- Create: `BigEd/launcher/ui/usage_tracker.py`

- [ ] **Step 1: Write tests for UsageTracker**

```python
# tests/test_usage_tracker.py
import pytest
from BigEd.launcher.ui.usage_tracker import UsageTracker

def test_initial_state():
    t = UsageTracker()
    s = t.session_stats("local")
    assert s["tokens"] == 0
    assert s["cost"] == 0.0
    assert s["calls"] == 0

def test_record_local():
    t = UsageTracker()
    t.record("local", "qwen3:8b", tokens_in=100, tokens_out=50, cost=0.0, tok_per_sec=45.0)
    s = t.session_stats("local")
    assert s["tokens"] == 150
    assert s["calls"] == 1
    assert s["tok_per_sec"] == 45.0

def test_record_claude():
    t = UsageTracker()
    t.record("claude", "claude-sonnet-4-6", tokens_in=500, tokens_out=200, cost=0.003)
    s = t.session_stats("claude")
    assert s["tokens"] == 700
    assert s["cost"] == 0.003
    assert s["calls"] == 1

def test_format_status_line():
    t = UsageTracker()
    t.record("claude", "claude-sonnet-4-6", tokens_in=8000, tokens_out=100, cost=0.12)
    line = t.format_line("claude")
    assert "$0.12" in line
    assert "8.1k" in line

def test_reset():
    t = UsageTracker()
    t.record("local", "qwen3:8b", tokens_in=100, tokens_out=50, cost=0.0)
    t.reset("local")
    assert t.session_stats("local")["tokens"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_usage_tracker.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement UsageTracker**

```python
# BigEd/launcher/ui/usage_tracker.py
"""Session and all-time usage tracking for Fleet Comm providers."""
from __future__ import annotations
import threading

PROVIDERS = ("local", "claude", "gemini")

def _fmt_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)

class UsageTracker:
    """In-memory session counters + helpers for fleet.db all-time queries."""

    def __init__(self):
        self._lock = threading.Lock()
        self._session: dict[str, dict] = {}
        for p in PROVIDERS:
            self._session[p] = self._empty()

    @staticmethod
    def _empty() -> dict:
        return {"tokens": 0, "cost": 0.0, "calls": 0,
                "tok_per_sec": 0.0, "model": ""}

    def record(self, provider: str, model: str, *,
               tokens_in: int = 0, tokens_out: int = 0,
               cost: float = 0.0, tok_per_sec: float = 0.0) -> None:
        with self._lock:
            s = self._session.setdefault(provider, self._empty())
            s["tokens"] += tokens_in + tokens_out
            s["cost"] += cost
            s["calls"] += 1
            s["model"] = model
            if tok_per_sec > 0:
                s["tok_per_sec"] = tok_per_sec

    def session_stats(self, provider: str) -> dict:
        with self._lock:
            return dict(self._session.get(provider, self._empty()))

    def reset(self, provider: str) -> None:
        with self._lock:
            self._session[provider] = self._empty()

    def format_line(self, provider: str) -> str:
        s = self.session_stats(provider)
        if s["calls"] == 0:
            return ""
        tok = _fmt_tokens(s["tokens"])
        if provider == "local":
            tps = f"{s['tok_per_sec']:.0f} tok/s" if s["tok_per_sec"] else ""
            model = s["model"] or ""
            parts = [f"{tok} tok", tps, model]
            return " | ".join(p for p in parts if p)
        cost = f"${s['cost']:.2f}"
        calls = f"{s['calls']} call{'s' if s['calls'] != 1 else ''}"
        return f"{tok} tok | {cost} | {calls}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_usage_tracker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add BigEd/launcher/ui/usage_tracker.py tests/test_usage_tracker.py
git commit -m "feat: add UsageTracker for session/all-time provider usage"
```

---

### Task 3: Trusted Models Table + Quarantine Helpers

**Files:**
- Modify: `fleet/db.py`

- [ ] **Step 1: Add `trusted_models` table to schema**

In `fleet/db.py`, add the following to the `SCHEMA` string constant (after the `usage` table definition around line 110, before the `idle_runs` table):

```sql
CREATE TABLE IF NOT EXISTS trusted_models (
    model       TEXT PRIMARY KEY,
    trusted_at  TEXT DEFAULT (datetime('now')),
    accept_count INTEGER DEFAULT 0,
    notes       TEXT DEFAULT ''
);
```

- [ ] **Step 2: Add helper functions**

```python
def is_model_trusted(model: str) -> bool:
    """Check if a model is in the trusted_models table."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM trusted_models WHERE model = ?", (model,)
        ).fetchone()
        return row is not None

def record_model_accept(model: str) -> int:
    """Increment accept count. Returns new count. Trusts at threshold."""
    TRUST_THRESHOLD = 5
    def _do():
        with get_conn() as conn:
            row = conn.execute(
                "SELECT accept_count FROM trusted_models WHERE model = ?",
                (model,),
            ).fetchone()
            if row:
                new_count = row[0] + 1
                conn.execute(
                    "UPDATE trusted_models SET accept_count = ? WHERE model = ?",
                    (new_count, model),
                )
                return new_count
            else:
                conn.execute(
                    "INSERT INTO trusted_models (model, accept_count) VALUES (?, 1)",
                    (model,),
                )
                return 1
    return _retry_write(_do)

def get_registered_models() -> list[str]:
    """Return list of all trusted model names."""
    with get_conn() as conn:
        rows = conn.execute("SELECT model FROM trusted_models").fetchall()
        return [r[0] for r in rows]
```

- [ ] **Step 3: Commit**

```bash
git add fleet/db.py
git commit -m "feat(db): add trusted_models table and quarantine helpers"
```

---

### Task 4: HITL Response File Poller

**Files:**
- Create: `fleet/hitl_responder.py`

- [ ] **Step 1: Write the HITL response file creator and poller**

```python
# fleet/hitl_responder.py
"""File-based HITL response flow for VS Code integration."""
from __future__ import annotations
import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("hitl_responder")

FLEET_DIR = Path(__file__).parent
RESPONSE_DIR = FLEET_DIR / "hitl-responses"

def create_response_file(task_id: int, agent_name: str,
                         question: str, context: str = "") -> Path:
    """Create a pre-filled HITL response file for VS Code editing."""
    RESPONSE_DIR.mkdir(exist_ok=True)
    path = RESPONSE_DIR / f"hitl-response-{task_id}.md"
    content = (
        f"# HITL Response — Task #{task_id}\n\n"
        f"**Agent:** {agent_name}\n"
        f"**Question:**\n\n{question}\n\n"
        f"---\n\n"
        f"## Your Response\n\n"
        f"<!-- Write your response below this line. Save the file when done. -->\n\n"
    )
    if context:
        content += f"\n---\n\n## Context\n\n{context}\n"
    path.write_text(content, encoding="utf-8")
    return path


def parse_response_file(path: Path) -> str | None:
    """Extract the operator's response from a saved HITL response file."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    marker = "## Your Response"
    idx = text.find(marker)
    if idx < 0:
        return None
    after = text[idx + len(marker):]
    # Strip the HTML comment placeholder
    after = after.replace(
        "<!-- Write your response below this line. Save the file when done. -->", ""
    )
    # Strip context section if present
    ctx_idx = after.find("## Context")
    if ctx_idx >= 0:
        after = after[:ctx_idx]
    response = after.strip()
    return response if response else None


class HITLFilePoller:
    """Polls hitl-responses/ for saved files and dispatches responses."""

    def __init__(self, send_callback, poll_interval: float = 2.0):
        """
        send_callback(task_id: int, response: str) -> bool
        """
        self._send = send_callback
        self._interval = poll_interval
        self._active: dict[int, float] = {}  # task_id -> file mtime at creation
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def watch(self, task_id: int, path: Path) -> None:
        """Register a response file for polling."""
        try:
            self._active[task_id] = path.stat().st_mtime
        except Exception:
            self._active[task_id] = 0
        if self._thread is None or not self._thread.is_alive():
            self._start()

    def _start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _poll_loop(self) -> None:
        while not self._stop.is_set() and self._active:
            completed = []
            for task_id, orig_mtime in list(self._active.items()):
                path = RESPONSE_DIR / f"hitl-response-{task_id}.md"
                if not path.exists():
                    completed.append(task_id)
                    continue
                try:
                    current_mtime = path.stat().st_mtime
                except Exception:
                    continue
                if current_mtime > orig_mtime:
                    response = parse_response_file(path)
                    if response:
                        try:
                            ok = self._send(task_id, response)
                            if ok:
                                log.info("HITL response sent for task #%d", task_id)
                                path.unlink(missing_ok=True)
                                completed.append(task_id)
                        except Exception:
                            log.warning("Failed to send HITL response #%d",
                                        task_id, exc_info=True)
            for tid in completed:
                self._active.pop(tid, None)
            self._stop.wait(self._interval)
```

- [ ] **Step 2: Commit**

```bash
git add fleet/hitl_responder.py
git commit -m "feat: add HITL response file creator and poller for VS Code flow"
```

---

### Task 5: Claude Code Companion Skill — `/hitl-respond`

**Files:**
- Create: `fleet/skills/hitl_respond.py`

- [ ] **Step 1: Write the skill**

```python
# fleet/skills/hitl_respond.py
"""Claude Code companion skill for HITL response editing.

Usage from Claude Code:  /hitl-respond
Reads pending hitl-response-{id}.md files, shows context, helps draft response.
"""
SKILL_NAME = "hitl_respond"
DESCRIPTION = "Help operator draft HITL responses for fleet agents via VS Code"
REQUIRES_NETWORK = False


def run(task: dict, context: dict) -> dict:
    import db
    from pathlib import Path

    response_dir = Path(__file__).parent.parent / "hitl-responses"
    if not response_dir.exists():
        return {"status": "ok", "result": "No pending HITL responses."}

    pending = sorted(response_dir.glob("hitl-response-*.md"))
    if not pending:
        return {"status": "ok", "result": "No pending HITL responses."}

    results = []
    for p in pending:
        text = p.read_text(encoding="utf-8")
        # Extract task ID from filename
        tid = p.stem.replace("hitl-response-", "")
        results.append({
            "task_id": tid,
            "file": str(p),
            "content": text[:2000],
        })

    return {
        "status": "ok",
        "result": f"Found {len(results)} pending HITL response(s).",
        "pending": results,
        "instructions": (
            "Open the response file, write your response under '## Your Response', "
            "then save. BigEd will automatically detect the save and deliver it."
        ),
    }
```

- [ ] **Step 2: Commit**

```bash
git add fleet/skills/hitl_respond.py
git commit -m "feat: add /hitl-respond Claude Code companion skill"
```

---

### Task 6: Developer Consoles Settings Panel

**Files:**
- Create: `BigEd/launcher/ui/settings/consoles.py`
- Modify: `BigEd/launcher/ui/settings/__init__.py`

- [ ] **Step 1: Create the consoles settings panel as a mixin**

```python
# BigEd/launcher/ui/settings/consoles.py
"""Developer Consoles settings panel — power-user standalone console launchers."""
from __future__ import annotations
import customtkinter as ctk
from ui.theme import (GLASS_PANEL, BG2, BG3, TEXT, DIM, GOLD, FONT_SM,
                      FONT_BOLD, FONT_TITLE, ACCENT_H,
                      PROVIDER_CLAUDE, PROVIDER_GEMINI, PROVIDER_LOCAL)


class ConsolesPanelMixin:
    """Settings mixin — Developer Consoles section."""

    def _build_consoles_panel(self):
        frame = ctk.CTkFrame(self._content, fg_color=GLASS_PANEL)
        self._panels["consoles"] = frame

        ctk.CTkLabel(frame, text="Developer Consoles", font=FONT_TITLE,
                     text_color=GOLD).pack(anchor="w", padx=16, pady=(16, 4))
        ctk.CTkLabel(frame, text="Power-user standalone chat windows with full context injection",
                     font=FONT_SM, text_color=DIM).pack(anchor="w", padx=16, pady=(0, 16))

        consoles = [
            ("Local Console (Ollama)", PROVIDER_LOCAL, "_open_local_console"),
            ("Claude Console", PROVIDER_CLAUDE, "_open_claude_console"),
            ("Gemini Console", PROVIDER_GEMINI, "_open_gemini_console"),
        ]

        L = self._parent  # launcher instance

        for label, color, method_name in consoles:
            row = ctk.CTkFrame(frame, fg_color=BG2, corner_radius=6)
            row.pack(fill="x", padx=16, pady=4)

            ctk.CTkLabel(row, text=label, font=FONT_BOLD, text_color=TEXT
                         ).pack(side="left", padx=12, pady=10)

            def _open(m=method_name):
                if hasattr(L, m):
                    getattr(L, m)()

            ctk.CTkButton(
                row, text="Open", width=80, height=28, font=FONT_SM,
                fg_color=color, hover_color=ACCENT_H,
                command=_open,
            ).pack(side="right", padx=12, pady=10)

        # Usage bar visibility toggle
        sep = ctk.CTkFrame(frame, fg_color=BG3, height=1)
        sep.pack(fill="x", padx=16, pady=16)

        ctk.CTkLabel(frame, text="Fleet Comm Options", font=FONT_BOLD,
                     text_color=TEXT).pack(anchor="w", padx=16, pady=(0, 8))

        usage_var = ctk.BooleanVar(value=self._settings.get("show_usage_bar", True))

        def _toggle_usage(val=None):
            self._settings["show_usage_bar"] = usage_var.get()
            L._save_settings()

        ctk.CTkCheckBox(
            frame, text="Show usage status bar in Fleet Comm",
            font=FONT_SM, variable=usage_var, command=_toggle_usage,
        ).pack(anchor="w", padx=16, pady=4)
```

- [ ] **Step 2: Add "Consoles" to settings navigation**

In `BigEd/launcher/ui/settings/__init__.py`:

1. Add `("Consoles", "consoles")` to the `_SETTINGS_NAV` list (after the `("MCP Servers", "mcp")` entry at line 38).

2. Add `ConsolesPanel Mixin` import after the existing mixin imports (line 50):
```python
from ui.settings.consoles import ConsolesPanelMixin
```

3. Add `ConsolesPanelMixin` to the `SettingsDialog` class inheritance (line 55-64).

4. Add `self._build_consoles_panel()` after `self._build_mcp_panel()` (line 158).

The mixin pattern matches the existing settings architecture — each panel is a mixin class with a `_build_*_panel()` method that creates a frame and registers it in `self._panels[key]`.

- [ ] **Step 3: Commit**

```bash
git add BigEd/launcher/ui/settings/consoles.py BigEd/launcher/ui/settings/__init__.py
git commit -m "feat(settings): add Developer Consoles panel"
```

---

### Task 7: Rewrite Fleet Comm Chat UI — Provider Pills + Usage Bar

**Files:**
- Modify: `BigEd/launcher/launcher.py` (lines 3259-3367 — `_build_tab_comm` chat section)

This is the core task. Replace the manual chat section with the unified console.

- [ ] **Step 1: Add instance variables for unified console**

At the top of `_build_tab_comm()` (after the agent requests section), add initialization:

```python
# ── Unified Console state ──────────────────────────────────
if not hasattr(self, "_usage_tracker"):
    from ui.usage_tracker import UsageTracker
    self._usage_tracker = UsageTracker()
self._active_provider = "local"  # local | claude | gemini
self._provider_dots = {}  # provider -> dot label widget
self._provider_pills = {}  # provider -> pill button widget
self._streaming = False
self._chat_history = []  # [{role, content, provider, model}]
```

- [ ] **Step 2: Build provider pill selector row**

Replace the old `chat_hdr` (lines 3264-3278) with:

```python
# ── Provider Pills + Model Swapper ─────────────────────────
pill_row = ctk.CTkFrame(self._chat_container, fg_color=BG2, height=40, corner_radius=0)
pill_row.pack(fill="x")
pill_row.pack_propagate(False)

from ui.theme import (PROVIDER_LOCAL, PROVIDER_CLAUDE, PROVIDER_GEMINI,
                       PROVIDER_OAUTH, PROVIDER_BG_LOCAL, PROVIDER_BG_CLAUDE,
                       PROVIDER_BG_GEMINI)

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
```

- [ ] **Step 3: Build compact usage status bar**

```python
# ── Usage Status Bar ───────────────────────────────────────
self._usage_bar_frame = ctk.CTkFrame(self._chat_container, fg_color=BG2,
                                      height=48, corner_radius=0)
if self._settings.get("show_usage_bar", True):
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
```

- [ ] **Step 4: Build unified chat display and input**

Keep the existing chat display structure but update message formatting:

```python
# ── Chat Display ───────────────────────────────────────────
self._local_chat_frame = ctk.CTkFrame(self._chat_container, fg_color="transparent")
self._local_chat_frame.pack(fill="both", expand=True)

self._manual_chat_display = ctk.CTkTextbox(
    self._local_chat_frame, font=FONT_STAT, fg_color=BG2,
    text_color=TEXT, corner_radius=4)
self._manual_chat_display.pack(fill="both", expand=True, padx=8, pady=4)
self._manual_chat_display.configure(state="disabled")

# ── Input Row (unchanged structure, updated placeholder) ──
input_row = ctk.CTkFrame(self._local_chat_frame, fg_color="transparent")
input_row.pack(fill="x", padx=8, pady=(0, 8))
input_row.grid_columnconfigure(0, weight=1)

self._manual_chat_entry = ctk.CTkEntry(
    input_row, font=FONT, fg_color=BG2,
    placeholder_text="Message (Local) — switch provider with pills above...")
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
```

- [ ] **Step 5: Remove the old VS Code guide frame**

Delete the entire `_vscode_guide_frame` section (lines 3313-3367). OAuth is now a pill button.

- [ ] **Step 6: Commit**

```bash
git add BigEd/launcher/launcher.py
git commit -m "feat(fleet-comm): provider pills, usage bar, unified chat display"
```

---

### Task 8: Provider Selection + Model Swapper Logic

**Files:**
- Modify: `BigEd/launcher/launcher.py`

- [ ] **Step 1: Add `_select_provider()` method**

```python
def _select_provider(self, provider: str) -> None:
    """Switch active provider in Fleet Comm unified console."""
    from ui.theme import (PROVIDER_LOCAL, PROVIDER_CLAUDE, PROVIDER_GEMINI,
                           PROVIDER_OAUTH, PROVIDER_BG_LOCAL, PROVIDER_BG_CLAUDE,
                           PROVIDER_BG_GEMINI)

    if provider == "oauth":
        # Launch VS Code OAuth flow
        self._launch_oauth_session("OAuth", "")
        return

    self._active_provider = provider
    colors = {
        "local": (PROVIDER_LOCAL, PROVIDER_BG_LOCAL),
        "claude": (PROVIDER_CLAUDE, PROVIDER_BG_CLAUDE),
        "gemini": (PROVIDER_GEMINI, PROVIDER_BG_GEMINI),
    }

    # Update pill visual state
    for name, pill in self._provider_pills.items():
        if name == "oauth":
            continue
        c, bg = colors.get(name, (DIM, BG3))
        if name == provider:
            pill.configure(border_width=2, border_color=c, fg_color=bg)
        else:
            pill.configure(border_width=0, fg_color=BG3)

    # Update model swapper options
    self._update_model_swapper(provider)

    # Update placeholder text
    labels = {"local": "Local", "claude": "Claude", "gemini": "Gemini"}
    self._manual_chat_entry.configure(
        placeholder_text=f"Message ({labels.get(provider, provider)}) — switch provider with pills above..."
    )
```

- [ ] **Step 2: Add `_update_model_swapper()` method**

```python
def _update_model_swapper(self, provider: str) -> None:
    """Update model dropdown based on selected provider."""
    mcfg = load_model_cfg()

    if provider == "local":
        # Fetch installed Ollama models
        def _fetch():
            try:
                host = mcfg.get("ollama_host", "http://localhost:11434")
                req = urllib.request.Request(f"{host}/api/tags", method="GET")
                with urllib.request.urlopen(req, timeout=3) as r:
                    data = json.loads(r.read())
                models = [m["name"] for m in data.get("models", [])]
                if not models:
                    models = [mcfg.get("local", "qwen3:8b")]
                # Mark unregistered models
                import sys
                if str(FLEET_DIR) not in sys.path:
                    sys.path.insert(0, str(FLEET_DIR))
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
```

- [ ] **Step 3: Commit**

```bash
git add BigEd/launcher/launcher.py
git commit -m "feat(fleet-comm): provider selection + model swapper logic"
```

---

### Task 9: Unified Send + Provider-Tagged Messages

**Files:**
- Modify: `BigEd/launcher/launcher.py`

- [ ] **Step 1: Rewrite `_send_manual_chat()` for multi-provider routing**

Replace the existing method:

```python
def _send_manual_chat(self):
    """Send a message from the unified Fleet Comm console."""
    text = self._manual_chat_entry.get().strip()
    if not text:
        return
    self._manual_chat_entry.delete(0, "end")

    # Append tagged user message
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
```

- [ ] **Step 2: Add `_append_tagged_message()` method**

```python
def _append_tagged_message(self, role: str, text: str,
                           provider: str, model: str) -> None:
    """Append a provider-tagged message to the unified chat display."""
    from ui.theme import PROVIDER_LOCAL, PROVIDER_CLAUDE, PROVIDER_GEMINI

    self._manual_chat_display.configure(state="normal")
    if role == "user":
        self._manual_chat_display.insert("end", f"\nYou: {text}\n")
    elif role == "system":
        self._manual_chat_display.insert("end", f"\n--- {text} ---\n")
    else:
        # Assistant message with provider tag
        provider_icons = {"local": "\u26a1", "claude": "\U0001f916", "gemini": "\u2726"}
        icon = provider_icons.get(provider, "")
        tag = f"[{model}]" if model else f"[{provider}]"
        self._manual_chat_display.insert("end", f"\n{icon} {tag} {text}\n")
    self._manual_chat_display.configure(state="disabled")
    self._manual_chat_display.see("end")
```

- [ ] **Step 3: Add `_set_streaming()` for active indicator**

```python
def _set_streaming(self, active: bool) -> None:
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
        # Update placeholder to show thinking
        self._manual_chat_entry.configure(placeholder_text="Waiting for response...")
    else:
        pill.configure(border_width=2, border_color=color)
        labels = {"local": "Local", "claude": "Claude", "gemini": "Gemini"}
        self._manual_chat_entry.configure(
            placeholder_text=f"Message ({labels.get(self._active_provider, '')})..."
        )
```

- [ ] **Step 4: Commit**

```bash
git add BigEd/launcher/launcher.py
git commit -m "feat(fleet-comm): unified send routing with provider-tagged messages"
```

---

### Task 10: Provider API Call Methods (Local + Claude + Gemini)

**Files:**
- Modify: `BigEd/launcher/launcher.py`

- [ ] **Step 1: Add `_unified_local_chat()` with quarantine**

```python
def _unified_local_chat(self, prompt: str, model: str) -> None:
    """Send to Ollama with usage tracking and quarantine for new models."""
    try:
        mcfg = load_model_cfg()
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

        # Track usage
        self._usage_tracker.record("local", model, tokens_in=tok_in,
                                    tokens_out=tok_out, tok_per_sec=tps)
        self._safe_after(0, self._update_usage_bar)

        # Check quarantine
        import sys
        if str(FLEET_DIR) not in sys.path:
            sys.path.insert(0, str(FLEET_DIR))
        import db
        is_new = not db.is_model_trusted(model)

        def _show():
            self._set_streaming(False)
            self._append_tagged_message("assistant", response, "local", model)
            if is_new:
                self._show_quarantine_controls(model, response)

        self._safe_after(0, _show)
    except Exception as e:
        self._safe_after(0, lambda: (
            self._set_streaming(False),
            self._append_tagged_message("assistant", f"Error: {e}", "local", model),
        ))
```

- [ ] **Step 2: Add `_unified_claude_chat()`**

```python
def _unified_claude_chat(self, prompt: str, model: str) -> None:
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

        # Estimate cost (approximate)
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
```

- [ ] **Step 3: Add `_unified_gemini_chat()`**

```python
def _unified_gemini_chat(self, prompt: str, model: str) -> None:
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

        # Gemini pricing varies; rough estimate for flash
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
```

- [ ] **Step 4: Commit**

```bash
git add BigEd/launcher/launcher.py
git commit -m "feat(fleet-comm): unified API calls for Local + Claude + Gemini"
```

---

### Task 11: Connection Status Polling + Usage Bar Updates

**Files:**
- Modify: `BigEd/launcher/launcher.py`

- [ ] **Step 1: Add connection status checker**

```python
def _check_provider_connections(self) -> None:
    """Poll provider availability and update connection dots."""
    def _check():
        results = {}
        # Local (Ollama)
        try:
            mcfg = load_model_cfg()
            host = mcfg.get("ollama_host", "http://localhost:11434")
            req = urllib.request.Request(f"{host}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=3) as r:
                json.loads(r.read())
            results["local"] = "green"
        except Exception:
            results["local"] = "red"

        # Claude
        results["claude"] = "green" if os.environ.get("ANTHROPIC_API_KEY") else "gray"

        # Gemini
        results["gemini"] = "green" if os.environ.get("GEMINI_API_KEY") else "gray"

        colors = {"green": GREEN, "red": RED, "gray": DIM}
        self._safe_after(0, lambda: [
            self._provider_dots[p].configure(text_color=colors.get(s, DIM))
            for p, s in results.items() if p in self._provider_dots
        ])

    threading.Thread(target=_check, daemon=True).start()
    # Re-poll every 30 seconds
    self._safe_after(30_000, self._check_provider_connections)
```

- [ ] **Step 2: Add usage bar update method**

```python
def _update_usage_bar(self) -> None:
    """Refresh the usage status bar labels."""
    for prov, lbl in self._usage_labels.items():
        line = self._usage_tracker.format_line(prov)
        prefix = {"local": "\u26a1 Local: ", "claude": "\U0001f916 Claude: ",
                  "gemini": "\u2726 Gemini: "}
        if line:
            lbl.configure(text=f"{prefix.get(prov, '')}{line}")
        else:
            lbl.configure(text="")

def _show_usage_popover(self, provider: str) -> None:
    """Show all-time usage from fleet.db (click on usage bar line)."""
    try:
        import sys
        if str(FLEET_DIR) not in sys.path:
            sys.path.insert(0, str(FLEET_DIR))
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
```

- [ ] **Step 3: Wire connection check into boot sequence**

In `_build_tab_comm()`, at the end, add:

```python
# Start connection polling + model swapper population
self._safe_after(1000, self._check_provider_connections)
self._safe_after(1500, lambda: self._update_model_swapper("local"))
```

- [ ] **Step 4: Commit**

```bash
git add BigEd/launcher/launcher.py
git commit -m "feat(fleet-comm): connection status polling + usage bar updates"
```

---

### Task 12: Quarantine Controls for New Models

**Files:**
- Modify: `BigEd/launcher/launcher.py`

- [ ] **Step 1: Add quarantine UI method**

```python
def _show_quarantine_controls(self, model: str, response: str) -> None:
    """Show Accept/Reject/Flag controls for unregistered model responses."""
    self._manual_chat_display.configure(state="normal")
    self._manual_chat_display.insert(
        "end",
        f"\n  \u26a0 Unregistered model: {model} — review response above\n"
    )
    self._manual_chat_display.configure(state="disabled")

    # Insert inline buttons (using a small frame overlay approach)
    qframe = ctk.CTkFrame(self._local_chat_frame, fg_color=BG2, corner_radius=4, height=32)
    qframe.pack(fill="x", padx=8, pady=(0, 4))

    ctk.CTkLabel(qframe, text=f"\u26a0 {model}", font=FONT_XS,
                 text_color=ORANGE).pack(side="left", padx=8)

    def _accept():
        import sys
        if str(FLEET_DIR) not in sys.path:
            sys.path.insert(0, str(FLEET_DIR))
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
```

- [ ] **Step 2: Commit**

```bash
git add BigEd/launcher/launcher.py
git commit -m "feat(fleet-comm): quarantine Accept/Reject/Flag for unregistered models"
```

---

### Task 13: Agent Request Cards — VS Code Reply Button + HITL Poller

**Files:**
- Modify: `BigEd/launcher/launcher.py`

- [ ] **Step 1: Initialize HITL file poller in `__init__` or `_build_tab_comm`**

```python
# In _build_tab_comm, after usage bar setup:
import sys
if str(FLEET_DIR) not in sys.path:
    sys.path.insert(0, str(FLEET_DIR))
from hitl_responder import HITLFilePoller
if not hasattr(self, "_hitl_poller"):
    def _hitl_send(task_id, response):
        from data_access import FleetDB  # BigEd/launcher/data_access.py — already on path
        return FleetDB.send_human_response(FLEET_DIR / "fleet.db", task_id, response)
    self._hitl_poller = HITLFilePoller(_hitl_send)
```

- [ ] **Step 2: Update agent request card rendering**

In `_refresh_comm()` where cards are built (around line 4023-4030), replace the single "Load to Chat" button with two buttons:

```python
# Replace the existing "Load to Chat" button with:
btn_row = ctk.CTkFrame(card, fg_color="transparent")
btn_row.pack(fill="x", padx=8, pady=(0, 6))

ctk.CTkButton(
    btn_row, text="\u2193 Reply in Local Chat", width=140, height=22,
    fg_color=BG3, hover_color=BG2, font=FONT_XS, text_color=DIM,
    command=lambda t=tid, q=question, ag=agent_name, tt=task_type:
        self._load_hitl_to_chat(t, q, ag, tt),
).pack(side="left", padx=(0, 4))

ctk.CTkButton(
    btn_row, text="\U0001f4bb Reply in VS Code", width=140, height=22,
    fg_color=BG3, hover_color=BG2, font=FONT_XS, text_color=PROVIDER_OAUTH,
    command=lambda t=tid, q=question, ag=agent_name:
        self._reply_via_vscode(t, q, ag),
).pack(side="left")
```

- [ ] **Step 3: Add `_reply_via_vscode()` method**

```python
def _reply_via_vscode(self, task_id: int, question: str, agent_name: str) -> None:
    """Create HITL response file and open in VS Code."""
    import sys as _sys
    if str(FLEET_DIR) not in _sys.path:
        _sys.path.insert(0, str(FLEET_DIR))
    from hitl_responder import create_response_file

    path = create_response_file(task_id, agent_name, question)
    self._hitl_poller.watch(task_id, path)

    # Open in VS Code
    code_exe = self._find_vscode()
    if code_exe:
        import subprocess
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

def _find_vscode(self) -> str | None:
    """Locate VS Code executable."""
    import shutil
    code = shutil.which("code")
    if code:
        return code
    # Windows fallback
    for candidate in [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Microsoft VS Code\Code.exe"),
    ]:
        if os.path.isfile(candidate):
            return candidate
    return None
```

- [ ] **Step 4: Commit**

```bash
git add BigEd/launcher/launcher.py
git commit -m "feat(fleet-comm): Reply in Local Chat + Reply in VS Code on agent cards"
```

---

### Task 14: Remove Console Buttons from Sidebar

**Files:**
- Modify: `BigEd/launcher/launcher.py`

- [ ] **Step 1: Remove sidebar console buttons (lines ~1836-1850)**

Find the CONSOLES section in the sidebar and remove the three console buttons and their status dots. Keep the console opener methods (`_open_claude_console`, `_open_gemini_console`, `_open_local_console`) since Settings will still call them.

- [ ] **Step 2: Clean up old `_on_manual_model_change` method**

Remove the `_on_manual_model_change()` method (lines 3415-3467) since the VS Code guide frame and mode switching are replaced by provider pills.

- [ ] **Step 3: Remove old manual model dropdown variable**

The `_manual_model_var` and its `CTkOptionMenu` are replaced by provider pills and `_model_swap_var`. Remove references to `_manual_model_var` throughout.

- [ ] **Step 4: Commit**

```bash
git add BigEd/launcher/launcher.py
git commit -m "refactor: remove sidebar console buttons + old manual model dropdown"
```

---

### Task 15: Integration Test + Smoke Verification

**Files:**
- No new files — run existing test infrastructure

- [ ] **Step 1: Run smoke tests**

```bash
cd C:/Users/max/Projects/Education
python fleet/smoke_test.py --fast
```

Expected: 22/22 pass (no regressions)

- [ ] **Step 2: Run unit tests**

```bash
python -m pytest tests/test_usage_tracker.py -v
```

Expected: All pass

- [ ] **Step 3: Manual verification — launch the GUI**

```bash
python BigEd/launcher/launcher.py
```

Verify:
- Fleet Comm tab shows provider pills (Local/Claude/Gemini/OAuth)
- Connection dots show correct status
- Model swapper updates per provider
- Usage bar shows after sending a local message
- Agent request cards have "Reply in Local Chat" and "Reply in VS Code" buttons
- OAuth pill launches VS Code
- Sidebar no longer has console buttons
- Settings > Consoles panel has console launchers

- [ ] **Step 4: Commit any fixes from manual testing**

```bash
git add -u
git commit -m "fix: address integration issues from manual testing"
```

---

## Execution Notes

- **Parallelizable batch 1:** Tasks 1, 2, 3, 4, 5, 6 are independent (no shared file dependencies except db.py in Task 3). Can run concurrently.
- **Sequential batch 2:** Tasks 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14 → 15 (each builds on launcher.py changes from the previous task)
- **Risk areas:** Task 7 (core UI rewrite) and Task 9 (routing logic) are the highest-risk — test thoroughly
- **Largest task:** Task 7 modifies the most lines in launcher.py
- **Import note:** `FLEET_DIR` and `load_model_cfg()` are module-level references in `launcher.py` — available in all methods without extra imports
