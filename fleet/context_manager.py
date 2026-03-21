"""Conversation context window manager for multi-turn local model interactions.

Manages per-agent sliding windows with token budgets, overflow summarization,
and crash-recovery persistence to fleet.db.

Usage:
    from context_manager import get_context
    ctx = get_context("researcher")
    ctx.add_turn("user", prompt)
    full_prompt = ctx.get_prompt_with_context(prompt)
    # ... call provider with full_prompt ...
    ctx.add_turn("assistant", response)
"""

import sqlite3
import threading
import time
import random
from pathlib import Path

# ── Lazy imports (avoid circular deps with db.py) ─────────────────────────────

_db_mod = None


def _db():
    """Lazy-load db module to avoid circular imports at module load time."""
    global _db_mod
    if _db_mod is None:
        import db as _m
        _db_mod = _m
    return _db_mod


# ── Config defaults ───────────────────────────────────────────────────────────

_DEFAULT_MAX_TURNS = 5
_DEFAULT_MAX_TOKENS = 4096
_DEFAULT_CLOUD_MAX_TOKENS = 8192
_DEFAULT_SUMMARIZE_ON_OVERFLOW = True
_DEFAULT_PERSIST_TO_DB = True
_DEFAULT_STALE_HOURS = 24

_CHARS_PER_TOKEN = 4  # rough estimate: 4 chars ~= 1 token


def _load_context_config() -> dict:
    """Load [context] section from fleet.toml, with defaults."""
    try:
        from config import load_config
        cfg = load_config()
        ctx_cfg = cfg.get("context", {})
    except Exception:
        ctx_cfg = {}
    return {
        "max_turns": ctx_cfg.get("max_turns", _DEFAULT_MAX_TURNS),
        "max_tokens": ctx_cfg.get("max_tokens", _DEFAULT_MAX_TOKENS),
        "summarize_on_overflow": ctx_cfg.get("summarize_on_overflow", _DEFAULT_SUMMARIZE_ON_OVERFLOW),
        "persist_to_db": ctx_cfg.get("persist_to_db", _DEFAULT_PERSIST_TO_DB),
        "stale_hours": ctx_cfg.get("stale_hours", _DEFAULT_STALE_HOURS),
    }


# ── Token estimation ──────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Rough token count: len(text) // 4 (4 chars ~= 1 token)."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


# ── DB schema & helpers ───────────────────────────────────────────────────────

_CONTEXT_DDL = """
CREATE TABLE IF NOT EXISTS agent_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    token_estimate INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(agent_name, created_at)
);
CREATE INDEX IF NOT EXISTS idx_agent_context_agent
    ON agent_context (agent_name, created_at);
"""

_schema_ensured = False
_schema_lock = threading.Lock()


def _ensure_schema():
    """Create the agent_context table if it doesn't exist. Idempotent."""
    global _schema_ensured
    if _schema_ensured:
        return
    with _schema_lock:
        if _schema_ensured:
            return
        try:
            with _db().get_conn() as conn:
                conn.executescript(_CONTEXT_DDL)
            _schema_ensured = True
        except Exception:
            pass  # schema creation must never crash the caller


def _retry_write(fn, retries=8):
    """Retry a write on OperationalError (locked) with jittered backoff.

    Mirrors the pattern from db.py for consistency.
    """
    for attempt in range(retries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or attempt == retries - 1:
                raise
            time.sleep(0.2 * (2 ** attempt) + random.uniform(0, 0.1))


# ── ContextWindow class ──────────────────────────────────────────────────────

class ContextWindow:
    """Per-agent conversation context window with token budgeting.

    Thread-safe: all mutable state guarded by self._lock.

    Args:
        agent_name: unique agent identifier (e.g. "researcher", "coder_1")
        max_turns: sliding window size (default from fleet.toml or 5)
        max_tokens: token budget for total context (default from fleet.toml or 4096)
    """

    def __init__(self, agent_name: str, max_turns: int = 0, max_tokens: int = 0):
        cfg = _load_context_config()
        self.agent_name = agent_name
        self.max_turns = max_turns if max_turns > 0 else cfg["max_turns"]
        self.max_tokens = max_tokens if max_tokens > 0 else cfg["max_tokens"]
        self._summarize_on_overflow = cfg["summarize_on_overflow"]
        self._persist = cfg["persist_to_db"]

        # In-memory turn buffer: list of {"role": str, "content": str, "tokens": int}
        self._turns: list[dict] = []
        # Compacted summary of evicted turns (if any)
        self._summary: str = ""
        self._summary_tokens: int = 0

        self._lock = threading.Lock()
        self._loaded = False

    # ── Lazy load from DB ─────────────────────────────────────────────────

    def _ensure_loaded(self):
        """Load persisted context from DB on first access."""
        if self._loaded:
            return
        if not self._persist:
            self._loaded = True
            return
        try:
            _ensure_schema()
            with _db().get_conn() as conn:
                rows = conn.execute(
                    "SELECT role, content, token_estimate FROM agent_context "
                    "WHERE agent_name = ? ORDER BY created_at ASC",
                    (self.agent_name,)
                ).fetchall()
            for row in rows:
                role = row["role"] if isinstance(row, sqlite3.Row) else row[0]
                content = row["content"] if isinstance(row, sqlite3.Row) else row[1]
                tokens = row["token_estimate"] if isinstance(row, sqlite3.Row) else row[2]
                if role == "summary":
                    self._summary = content
                    self._summary_tokens = tokens or estimate_tokens(content)
                else:
                    self._turns.append({
                        "role": role,
                        "content": content,
                        "tokens": tokens or estimate_tokens(content),
                    })
        except Exception:
            pass  # DB load failure is non-fatal; start with empty context
        self._loaded = True

    # ── Public API ────────────────────────────────────────────────────────

    def add_turn(self, role: str, content: str) -> None:
        """Add a user/assistant turn to the context window.

        If the window exceeds max_turns or max_tokens, older turns are
        evicted (and optionally summarized).
        """
        if role not in ("user", "assistant", "system"):
            raise ValueError(f"Invalid role '{role}' — must be 'user', 'assistant', or 'system'")

        tokens = estimate_tokens(content)

        with self._lock:
            self._ensure_loaded()
            self._turns.append({"role": role, "content": content, "tokens": tokens})
            self._enforce_limits()
            if self._persist:
                self._persist_turn(role, content, tokens)

    def get_context(self) -> list[dict]:
        """Return the current context window as a messages list.

        Returns a list of dicts with 'role' and 'content' keys, suitable
        for passing to an LLM API. If a summary exists, it is prepended
        as a system message.
        """
        with self._lock:
            self._ensure_loaded()
            messages = []
            if self._summary:
                messages.append({
                    "role": "system",
                    "content": f"[Previous conversation summary]\n{self._summary}",
                })
            for turn in self._turns:
                messages.append({"role": turn["role"], "content": turn["content"]})
            return messages

    def get_prompt_with_context(self, new_prompt: str) -> str:
        """Build a prompt string that includes relevant conversation context.

        Returns a single string with the summary (if any), recent turns,
        and the new prompt concatenated. Suitable for Ollama's single-prompt
        API (`/api/generate`).
        """
        with self._lock:
            self._ensure_loaded()
            parts = []

            if self._summary:
                parts.append(f"[Previous conversation summary]\n{self._summary}\n")

            for turn in self._turns:
                prefix = "User" if turn["role"] == "user" else "Assistant"
                parts.append(f"{prefix}: {turn['content']}")

            parts.append(f"User: {new_prompt}")
            return "\n\n".join(parts)

    def clear(self) -> None:
        """Clear all context for this agent (memory and DB)."""
        with self._lock:
            self._turns.clear()
            self._summary = ""
            self._summary_tokens = 0
            if self._persist:
                self._clear_persisted()

    def summarize_and_compact(self) -> str:
        """Summarize old turns and compact the window.

        Evicts all but the most recent 2 turns. The evicted turns are
        summarized using the local model (via _call_local). If the local
        model is unavailable, a simple extractive summary is generated
        from the first 200 chars of each evicted turn.

        Returns the summary text.
        """
        with self._lock:
            self._ensure_loaded()
            if len(self._turns) <= 2:
                return self._summary  # nothing to compact

            # Split: evict all but last 2 turns
            keep = 2
            to_summarize = self._turns[:-keep]
            self._turns = self._turns[-keep:]

            summary_text = self._generate_summary(to_summarize)

            # Merge with existing summary
            if self._summary:
                merged = f"{self._summary}\n\n{summary_text}"
                # Re-summarize if merged summary is too long
                merged_tokens = estimate_tokens(merged)
                if merged_tokens > self.max_tokens // 3:
                    merged = self._truncate_summary(merged, self.max_tokens // 4)
                self._summary = merged
            else:
                self._summary = summary_text
            self._summary_tokens = estimate_tokens(self._summary)

            # Persist compacted state
            if self._persist:
                self._persist_compacted_state()

            return self._summary

    @property
    def total_tokens(self) -> int:
        """Total token count across summary + all turns."""
        with self._lock:
            self._ensure_loaded()
            turn_tokens = sum(t["tokens"] for t in self._turns)
            return self._summary_tokens + turn_tokens

    @property
    def turn_count(self) -> int:
        """Number of turns in the current window (excludes summary)."""
        with self._lock:
            self._ensure_loaded()
            return len(self._turns)

    # ── Internal helpers ──────────────────────────────────────────────────

    def _enforce_limits(self):
        """Evict oldest turns when window exceeds turn count or token budget.

        Called with self._lock held. If summarize_on_overflow is enabled,
        evicted turns are summarized before removal.
        """
        evicted = []

        # Enforce turn limit
        while len(self._turns) > self.max_turns:
            evicted.append(self._turns.pop(0))

        # Enforce token budget
        while self._total_tokens_unlocked() > self.max_tokens and len(self._turns) > 1:
            evicted.append(self._turns.pop(0))

        # Summarize evicted turns if configured
        if evicted and self._summarize_on_overflow:
            summary_text = self._generate_summary(evicted)
            if self._summary:
                self._summary = f"{self._summary}\n\n{summary_text}"
                # Cap summary size at 1/3 of token budget
                max_summary_tokens = self.max_tokens // 3
                if estimate_tokens(self._summary) > max_summary_tokens:
                    self._summary = self._truncate_summary(self._summary, max_summary_tokens)
            else:
                self._summary = summary_text
            self._summary_tokens = estimate_tokens(self._summary)

            if self._persist:
                self._persist_summary()

    def _total_tokens_unlocked(self) -> int:
        """Token count without acquiring the lock (caller holds lock)."""
        turn_tokens = sum(t["tokens"] for t in self._turns)
        return self._summary_tokens + turn_tokens

    def _generate_summary(self, turns: list[dict]) -> str:
        """Summarize a list of turns using the local model.

        Falls back to extractive summary if the local model call fails.
        """
        # Build the conversation text to summarize
        lines = []
        for t in turns:
            prefix = "User" if t["role"] == "user" else "Assistant"
            lines.append(f"{prefix}: {t['content']}")
        conversation = "\n".join(lines)

        # Try LLM-based summarization
        try:
            from config import load_config
            config = load_config()
            from providers import _call_local
            models = {
                "ollama_host": config.get("models", {}).get("ollama_host", "http://localhost:11434"),
                "local": config.get("models", {}).get("local", "qwen3:8b"),
            }
            summary = _call_local(
                system="You are a conversation summarizer. Produce a brief, factual summary "
                       "of the key points, decisions, and outcomes from this conversation. "
                       "Keep it under 150 words. Do not add commentary.",
                user=conversation,
                models=models,
                max_tokens=256,
                skill_name="context_summarize",
                config=config,
            )
            if summary and len(summary.strip()) > 10:
                return summary.strip()
        except Exception:
            pass  # Local model unavailable — fall back to extractive

        # Extractive fallback: first 200 chars of each turn
        parts = []
        for t in turns:
            prefix = "User" if t["role"] == "user" else "Assistant"
            snippet = t["content"][:200].replace("\n", " ")
            if len(t["content"]) > 200:
                snippet += "..."
            parts.append(f"- {prefix}: {snippet}")
        return "Summary of earlier conversation:\n" + "\n".join(parts)

    def _truncate_summary(self, text: str, max_tokens: int) -> str:
        """Truncate summary text to fit within a token budget."""
        max_chars = max_tokens * _CHARS_PER_TOKEN
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rsplit(" ", 1)[0] + "..."

    # ── Persistence ───────────────────────────────────────────────────────

    def _persist_turn(self, role: str, content: str, tokens: int):
        """Write a single turn to fleet.db."""
        def _do():
            _ensure_schema()
            with _db().get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO agent_context "
                    "(agent_name, role, content, token_estimate) VALUES (?, ?, ?, ?)",
                    (self.agent_name, role, content, tokens)
                )
        try:
            _retry_write(_do)
        except Exception:
            pass  # DB write failure is non-fatal

    def _persist_summary(self):
        """Write/update the summary row in fleet.db."""
        def _do():
            _ensure_schema()
            with _db().get_conn() as conn:
                # Remove any prior summary for this agent
                conn.execute(
                    "DELETE FROM agent_context WHERE agent_name = ? AND role = 'summary'",
                    (self.agent_name,)
                )
                if self._summary:
                    conn.execute(
                        "INSERT INTO agent_context "
                        "(agent_name, role, content, token_estimate) VALUES (?, 'summary', ?, ?)",
                        (self.agent_name, self._summary, self._summary_tokens)
                    )
        try:
            _retry_write(_do)
        except Exception:
            pass

    def _persist_compacted_state(self):
        """Replace all persisted rows with the current compacted state."""
        def _do():
            _ensure_schema()
            with _db().get_conn() as conn:
                # Clear all rows for this agent
                conn.execute(
                    "DELETE FROM agent_context WHERE agent_name = ?",
                    (self.agent_name,)
                )
                # Write summary
                if self._summary:
                    conn.execute(
                        "INSERT INTO agent_context "
                        "(agent_name, role, content, token_estimate) VALUES (?, 'summary', ?, ?)",
                        (self.agent_name, self._summary, self._summary_tokens)
                    )
                # Write remaining turns
                for turn in self._turns:
                    conn.execute(
                        "INSERT INTO agent_context "
                        "(agent_name, role, content, token_estimate) VALUES (?, ?, ?, ?)",
                        (self.agent_name, turn["role"], turn["content"], turn["tokens"])
                    )
        try:
            _retry_write(_do)
        except Exception:
            pass

    def _clear_persisted(self):
        """Remove all persisted rows for this agent."""
        def _do():
            _ensure_schema()
            with _db().get_conn() as conn:
                conn.execute(
                    "DELETE FROM agent_context WHERE agent_name = ?",
                    (self.agent_name,)
                )
        try:
            _retry_write(_do)
        except Exception:
            pass


# ── Module-level registry ─────────────────────────────────────────────────────

_registry: dict[str, ContextWindow] = {}
_registry_lock = threading.Lock()


def get_context(agent_name: str, max_turns: int = 0, max_tokens: int = 0) -> ContextWindow:
    """Get or create a context window for an agent.

    Thread-safe. Returns the same ContextWindow instance for a given
    agent_name across multiple calls.

    Args:
        agent_name: unique agent identifier
        max_turns: override max turns (0 = use config default)
        max_tokens: override max tokens (0 = use config default)
    """
    with _registry_lock:
        if agent_name not in _registry:
            _registry[agent_name] = ContextWindow(agent_name, max_turns, max_tokens)
        return _registry[agent_name]


def clear_all_contexts() -> int:
    """Clear all agent contexts (memory and DB). Returns count cleared."""
    with _registry_lock:
        count = len(_registry)
        for ctx in _registry.values():
            ctx.clear()
        _registry.clear()

    # Also clear any DB-only contexts not in the registry
    try:
        _ensure_schema()

        def _do():
            with _db().get_conn() as conn:
                cursor = conn.execute("SELECT COUNT(DISTINCT agent_name) FROM agent_context")
                row = cursor.fetchone()
                db_count = row[0] if row else 0
                conn.execute("DELETE FROM agent_context")
                return max(count, db_count)

        result = _retry_write(_do)
        return result if result is not None else count
    except Exception:
        return count


def clear_stale_contexts(max_age_hours: int = 0) -> int:
    """Clear contexts older than max_age_hours. Returns count cleared.

    Args:
        max_age_hours: age threshold (0 = use config default from fleet.toml)
    """
    if max_age_hours <= 0:
        cfg = _load_context_config()
        max_age_hours = cfg["stale_hours"]

    stale_names: list[str] = []
    try:
        _ensure_schema()

        def _do():
            with _db().get_conn() as conn:
                # Find stale agents
                rows = conn.execute(
                    "SELECT DISTINCT agent_name FROM agent_context "
                    "WHERE created_at < datetime('now', ?)",
                    (f"-{max_age_hours} hours",)
                ).fetchall()
                agents = [r[0] if not isinstance(r, sqlite3.Row) else r["agent_name"] for r in rows]
                if not agents:
                    return agents
                placeholders = ",".join("?" * len(agents))
                conn.execute(
                    f"DELETE FROM agent_context WHERE agent_name IN ({placeholders})",
                    agents
                )
                return agents

        result = _retry_write(_do)
        stale_names = result if result else []

        # Also clear from in-memory registry
        if stale_names:
            with _registry_lock:
                for name in stale_names:
                    _registry.pop(name, None)
    except Exception:
        pass

    return len(stale_names)


def list_contexts() -> list[dict]:
    """List all active agent contexts with metadata.

    Returns a list of dicts: [{"agent_name": str, "turns": int, "tokens": int,
                                "last_activity": str}]
    """
    results = []
    try:
        _ensure_schema()
        with _db().get_conn() as conn:
            rows = conn.execute(
                "SELECT agent_name, COUNT(*) as turns, "
                "SUM(token_estimate) as total_tokens, "
                "MAX(created_at) as last_activity "
                "FROM agent_context "
                "WHERE role != 'summary' "
                "GROUP BY agent_name "
                "ORDER BY last_activity DESC"
            ).fetchall()
            for row in rows:
                results.append({
                    "agent_name": row["agent_name"] if isinstance(row, sqlite3.Row) else row[0],
                    "turns": row["turns"] if isinstance(row, sqlite3.Row) else row[1],
                    "tokens": row["total_tokens"] if isinstance(row, sqlite3.Row) else row[2],
                    "last_activity": row["last_activity"] if isinstance(row, sqlite3.Row) else row[3],
                })
    except Exception:
        # Fall back to in-memory registry
        with _registry_lock:
            for name, ctx in _registry.items():
                results.append({
                    "agent_name": name,
                    "turns": ctx.turn_count,
                    "tokens": ctx.total_tokens,
                    "last_activity": "",
                })
    return results
