"""Manual Mode Engine — operator-driven Claude Code compliance auditing.

Provides two complementary APIs:

Module-level (used by mod_manual_mode.py / UI layer):
  - run_queue()              : execute queue with HITL gate
  - launch_vscode()          : cross-platform VS Code launcher
  - _check_cost_anomaly()    : rolling-average cost spike detection
  - _write_audit_results_md(): structured markdown audit output

Class-based ManualModeEngine (queue/scheduler persistence + direct API calls):
  - get_queue() / set_queue()
  - get_scheduler() / set_scheduler()
  - run_queue(queue, on_progress) — includes HITL gate + governance
  - cancel_run()             — signal mid-run cancellation (threading.Event)
  - reset_cancel()           — clear cancel flag before a new run
  - get_run_history(limit)
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FLEET_DIR = Path(__file__).parent
FLEET_TOML = FLEET_DIR / "fleet.toml"
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
LOGS_DIR = FLEET_DIR / "logs"

# Model display-name → API ID mapping
CLAUDE_MODELS = {
    "claude-sonnet": "claude-sonnet-4-6",
    "claude-haiku":  "claude-haiku-4-5",
    "claude-opus":   "claude-opus-4-6",
}

# Pricing per million tokens (input / output)
_PRICING = {
    "claude-haiku-4-5":  {"input": 0.80,  "output": 4.00},
    "claude-sonnet-4-6": {"input": 3.00,  "output": 15.00},
    "claude-opus-4-6":   {"input": 15.00, "output": 75.00},
}


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db_log_alert(severity: str, source: str, message: str, details: Any = None) -> None:
    """Write an alert to the alerts table via db.py's log_alert helper."""
    try:
        sys.path.insert(0, str(FLEET_DIR))
        from db import log_alert
        log_alert(severity, source, message, details)
    except Exception as exc:
        logger.warning("[MANUAL MODE] Could not write alert to DB: %s", exc)


def _get_last_run_tokens() -> int:
    """Return total tokens consumed by the most recent manual-mode audit run (0 if none)."""
    try:
        sys.path.insert(0, str(FLEET_DIR))
        from db import get_conn
        with get_conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS manual_mode_runs ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "run_at TEXT DEFAULT (datetime('now')), "
                "total_tokens INTEGER DEFAULT 0, "
                "total_cost_usd REAL DEFAULT 0.0, "
                "item_count INTEGER DEFAULT 0, "
                "summary_json TEXT)"
            )
            row = conn.execute(
                "SELECT total_tokens FROM manual_mode_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return int(row[0]) if row else 0
    except Exception as exc:
        logger.warning("[MANUAL MODE] Could not read last run tokens: %s", exc)
        return 0


def _save_run_record(total_tokens: int, total_cost: float, item_count: int, summary: dict) -> None:
    """Persist a completed run record for future HITL comparisons."""
    try:
        sys.path.insert(0, str(FLEET_DIR))
        from db import get_conn
        with get_conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS manual_mode_runs ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "run_at TEXT DEFAULT (datetime('now')), "
                "total_tokens INTEGER DEFAULT 0, "
                "total_cost_usd REAL DEFAULT 0.0, "
                "item_count INTEGER DEFAULT 0, "
                "summary_json TEXT)"
            )
            conn.execute(
                "INSERT INTO manual_mode_runs (total_tokens, total_cost_usd, item_count, summary_json) "
                "VALUES (?, ?, ?, ?)",
                (total_tokens, total_cost, item_count, json.dumps(summary))
            )
    except Exception as exc:
        logger.warning("[MANUAL MODE] Could not save run record: %s", exc)


def _get_recent_run_costs(limit: int = 5) -> list:
    """Return the total_cost_usd values of the last `limit` completed runs."""
    try:
        sys.path.insert(0, str(FLEET_DIR))
        from db import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT total_cost_usd FROM manual_mode_runs "
                "ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [float(r[0]) for r in rows]
    except Exception:
        return []


# ── Cost Anomaly Detection ────────────────────────────────────────────────────

def _check_cost_anomaly(run_cost: float, history: list) -> bool:
    """Detect cost spikes vs rolling average of last 5 runs.

    Writes a DB alert and logs a warning if run_cost > avg * 2.5.
    Requires at least 3 historical runs to avoid false positives.

    Returns True if an anomaly was detected.
    """
    if len(history) < 3:
        return False
    avg = sum(history[-5:]) / len(history[-5:])
    if avg <= 0:
        return False
    if run_cost > avg * 2.5:
        msg = f"Cost anomaly: ${run_cost:.4f} vs avg ${avg:.4f} (×{run_cost / avg:.1f})"
        logger.warning("[MANUAL MODE] %s", msg)
        _db_log_alert(
            "warning", "manual_mode", msg,
            {"run_cost": run_cost, "rolling_avg": avg, "history": history},
        )
        return True
    return False


# ── Audit Results Markdown ────────────────────────────────────────────────────

def _write_audit_results_md(run_summary: dict):
    """Write a structured audit-results markdown file to knowledge/.

    Sections: Run Summary, Per-Prompt Results, Cost Breakdown, Recommendations.
    Creates knowledge/ dir if missing.

    Returns Path to the written file, or None on failure.
    """
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = KNOWLEDGE_DIR / f"audit_results_{ts}.md"

    items: list = run_summary.get("items", [])
    total_tokens: int = run_summary.get("total_tokens", 0)
    total_cost: float = run_summary.get("total_cost_usd", 0.0)
    run_at: str = run_summary.get("run_at", ts)
    anomaly: bool = run_summary.get("anomaly_detected", False)

    # Extract recommendations (lines containing action keywords, up to 10)
    keywords = ("TODO", "FIXME", "ACTION", "must ", "should ")
    recommendations: list = []
    for item in items:
        text = str(item.get("output", "") or item.get("response", ""))
        for line in text.splitlines():
            stripped = line.strip()
            if any(kw.lower() in stripped.lower() for kw in keywords) and len(stripped) > 10:
                recommendations.append(f"- {stripped[:200]}")
                if len(recommendations) >= 10:
                    break
        if len(recommendations) >= 10:
            break

    lines = [
        "# Audit Results",
        "",
        "## Run Summary",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| Run At | {run_at} |",
        f"| Prompts Executed | {len(items)} |",
        f"| Total Tokens | {total_tokens:,} |",
        f"| Total Cost (USD) | ${total_cost:.4f} |",
        f"| Cost Anomaly | {'⚠ YES' if anomaly else 'No'} |",
        "",
        "## Per-Prompt Results",
        "",
    ]

    for i, item in enumerate(items, 1):
        status = item.get("status", "unknown")
        prompt = item.get("prompt", "")
        tokens = (
            item.get("tokens_used", 0)
            or item.get("input_tokens", 0) + item.get("output_tokens", 0)
        )
        cost = item.get("cost_usd", 0.0) or item.get("cost", 0.0)
        output_preview = str(item.get("output", "") or item.get("response", ""))[:300].replace("\n", " ")
        error = item.get("error", "")

        lines += [
            f"### {i}. {prompt[:80]}",
            "",
            f"**Status:** {status}  ",
            f"**Tokens:** {tokens:,}  ",
            f"**Cost:** ${cost:.4f}  ",
        ]
        if error:
            lines.append(f"**Error:** {error}  ")
        if output_preview:
            lines += ["**Output (preview):**", "", f"> {output_preview}", ""]
        lines.append("")

    lines += [
        "## Cost Breakdown",
        "",
        "| Prompt | Tokens | Cost (USD) |",
        "|--------|--------|-----------|",
    ]
    for item in items:
        prompt_short = item.get("prompt", "")[:50]
        tok = (
            item.get("tokens_used", 0)
            or item.get("input_tokens", 0) + item.get("output_tokens", 0)
        )
        cost = item.get("cost_usd", 0.0) or item.get("cost", 0.0)
        lines.append(f"| {prompt_short} | {tok:,} | ${cost:.4f} |")
    lines += [
        f"| **TOTAL** | **{total_tokens:,}** | **${total_cost:.4f}** |",
        "",
    ]

    if recommendations:
        lines += ["## Recommendations", ""] + recommendations + [""]
    else:
        lines += ["## Recommendations", "", "_No action items returned by this run._", ""]

    try:
        out_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("[MANUAL MODE] Audit results written to %s", out_path)
        return out_path
    except Exception as exc:
        logger.warning("[MANUAL MODE] Could not write audit results MD: %s", exc)
        return None


# ── Module-level Queue Execution (UI-facing — used by mod_manual_mode.py) ─────

def run_queue(queue_items: list, config: dict = None) -> dict:
    """Execute a list of audit prompt items via Claude API.

    Each item in queue_items:
        {prompt, skill (opt), max_tokens (opt, default 1024), repeat (opt, default 1)}

    Config keys (from fleet.toml [manual_mode] or caller-supplied dict):
        approval_required_threshold: float (default 0.20)

    Returns one of:
        {"status": "approval_required", "estimated_tokens": int,
         "last_tokens": int, "increase_pct": float}
        {"status": "ok", "items": [...], "total_tokens": int,
         "total_cost_usd": float, "anomaly_detected": bool,
         "audit_md_path": str | None}
        {"status": "error", "error": str}
    """
    cfg = config or {}
    threshold: float = float(cfg.get("approval_required_threshold", 0.20))

    # ── HITL Approval Gate ────────────────────────────────────────────────────
    estimated_tokens = sum(
        item.get("max_tokens", 1024) * item.get("repeat", 1)
        for item in queue_items
    )
    last_tokens = _get_last_run_tokens()

    if last_tokens > 0:
        increase_pct = (estimated_tokens - last_tokens) / last_tokens
        if increase_pct > threshold:
            logger.info(
                "[MANUAL MODE] Approval required: est %d tokens (+%.0f%% vs last %d)",
                estimated_tokens, increase_pct * 100, last_tokens,
            )
            return {
                "status": "approval_required",
                "estimated_tokens": estimated_tokens,
                "last_tokens": last_tokens,
                "increase_pct": round(increase_pct, 4),
            }

    # ── Execute items ─────────────────────────────────────────────────────────
    run_at = datetime.now(timezone.utc).isoformat()
    results: list = []
    total_tokens = 0
    total_cost = 0.0

    for item in queue_items:
        prompt: str = item.get("prompt", "")
        skill: str = item.get("skill", "code_review")
        max_tokens: int = int(item.get("max_tokens", 1024))
        repeat: int = int(item.get("repeat", 1))

        for _ in range(repeat):
            result = _run_single_item(prompt, skill, max_tokens)
            results.append(result)
            total_tokens += result.get("tokens_used", 0)
            total_cost += result.get("cost_usd", 0.0)

    # ── Cost Anomaly Check ─────────────────────────────────────────────────────
    history = _get_recent_run_costs()
    anomaly = _check_cost_anomaly(total_cost, history)

    # ── Persist run record ─────────────────────────────────────────────────────
    run_summary = {
        "items": results,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "run_at": run_at,
        "anomaly_detected": anomaly,
    }
    _save_run_record(total_tokens, total_cost, len(results), run_summary)

    # ── Write audit markdown ───────────────────────────────────────────────────
    md_path = _write_audit_results_md(run_summary)

    return {
        "status": "ok",
        "items": results,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "anomaly_detected": anomaly,
        "audit_md_path": str(md_path) if md_path else None,
    }


def _run_single_item(prompt: str, skill: str, max_tokens: int) -> dict:
    """Execute one audit prompt via direct Claude API call."""
    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        import anthropic
        time.sleep(0.3)  # 300 ms min between requests
        client = anthropic.Anthropic(api_key=api_key)
        model_id = "claude-sonnet-4-6"
        resp = client.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            system=[{
                "type": "text",
                "text": "You are a compliance auditor reviewing BigEd CC fleet code.",
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": prompt}],
        )
        in_tok = resp.usage.input_tokens
        out_tok = resp.usage.output_tokens
        tok = in_tok + out_tok
        rates = _PRICING.get(model_id, _PRICING["claude-sonnet-4-6"])
        cost = round(
            in_tok * rates["input"] / 1_000_000
            + out_tok * rates["output"] / 1_000_000,
            6,
        )
        output_text = resp.content[0].text if resp.content else ""
        return {
            "prompt": prompt, "skill": skill, "status": "ok",
            "output": output_text[:2000], "tokens_used": tok, "cost_usd": cost,
        }
    except Exception as exc:
        logger.error("[MANUAL MODE] Item failed: %s — %s", prompt[:60], exc)
        return {
            "prompt": prompt, "skill": skill, "status": "error",
            "error": str(exc), "output": "", "tokens_used": 0, "cost_usd": 0.0,
        }


# ── VS Code Launcher ──────────────────────────────────────────────────────────

def launch_vscode(workspace_path: str) -> bool:
    """Launch VS Code in the given workspace. Returns True if launched."""
    code_cmd = shutil.which("code")
    if not code_cmd and sys.platform == "win32":
        candidates = [
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "Programs", "Microsoft VS Code", "bin", "code.cmd",
            ),
            r"C:\Program Files\Microsoft VS Code\bin\code.cmd",
        ]
        code_cmd = next((c for c in candidates if os.path.exists(c)), None)
    if not code_cmd:
        logger.warning("[MANUAL MODE] VS Code not found on PATH or default install paths")
        return False
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen([code_cmd, workspace_path], **kwargs)
    logger.info("[MANUAL MODE] Launched VS Code at %s", workspace_path)
    return True


# ── ManualModeEngine — class-based API (queue/scheduler/history) ──────────────

class ManualModeEngine:
    """Backend for Manual Mode — TOML-backed queue/scheduler + direct Claude API calls.

    Used by the rich sidebar UI (mod_manual_mode.py) for full queue management,
    scheduling, and run history. Includes HITL approval gate, cost anomaly
    detection, audit markdown output, and mid-run cancellation support.
    """

    def __init__(self):
        if str(FLEET_DIR) not in sys.path:
            sys.path.insert(0, str(FLEET_DIR))
        # Gap 3: cancellation flag — set to cancel a running run_queue()
        self._cancel_event = threading.Event()

    # ── Cancellation ──────────────────────────────────────────────────────────

    def cancel_run(self) -> None:
        """Signal run_queue() to stop after the current item completes."""
        self._cancel_event.set()

    def reset_cancel(self) -> None:
        """Clear the cancel flag before starting a new run."""
        self._cancel_event.clear()

    # ── TOML I/O ──────────────────────────────────────────────────────────────

    def _load_toml(self):
        import tomlkit
        try:
            return tomlkit.loads(FLEET_TOML.read_text(encoding="utf-8"))
        except Exception:
            return tomlkit.document()

    def _save_toml(self, doc) -> None:
        import tomlkit
        try:
            FLEET_TOML.write_text(tomlkit.dumps(doc), encoding="utf-8")
        except Exception:
            pass

    def _ensure_section(self, doc, section: str):
        import tomlkit
        if section not in doc:
            doc.add(tomlkit.nl())
            doc.add(tomlkit.comment(" Manual Mode operator queue and scheduler"))
            doc[section] = tomlkit.table()
        return doc[section]

    # ── Queue ─────────────────────────────────────────────────────────────────

    def get_queue(self) -> list:
        """Return the audit queue from fleet.toml [manual_mode] queue."""
        try:
            doc = self._load_toml()
            raw = doc.get("manual_mode", {}).get("queue", [])
            return [dict(item) for item in raw]
        except Exception:
            return []

    def set_queue(self, queue: list) -> None:
        """Persist the audit queue to fleet.toml [manual_mode] queue."""
        doc = self._load_toml()
        sec = self._ensure_section(doc, "manual_mode")
        sec["queue"] = queue
        self._save_toml(doc)

    # ── Scheduler ─────────────────────────────────────────────────────────────

    def get_scheduler(self) -> dict:
        """Return scheduler config from fleet.toml [manual_mode] scheduler."""
        try:
            doc = self._load_toml()
            raw = doc.get("manual_mode", {}).get("scheduler", {})
            return {
                "enabled":       bool(raw.get("enabled", False)),
                "mode":          str(raw.get("mode", "one-time")),
                "run_at":        str(raw.get("run_at", "")),
                "interval_days": int(raw.get("interval_days", 1)),
                "next_run":      str(raw.get("next_run", "")),
            }
        except Exception:
            return {
                "enabled": False, "mode": "one-time",
                "run_at": "", "interval_days": 1, "next_run": "",
            }

    def set_scheduler(self, scheduler: dict) -> None:
        """Persist scheduler config to fleet.toml [manual_mode] scheduler."""
        doc = self._load_toml()
        sec = self._ensure_section(doc, "manual_mode")
        sec["scheduler"] = scheduler
        self._save_toml(doc)

    # ── Run Queue ─────────────────────────────────────────────────────────────

    def run_queue(self, queue: list, on_progress=None) -> dict:
        """Execute all prompts in the queue via the Claude API.

        Includes HITL approval gate: if estimated token increase exceeds
        approval_required_threshold vs last run, returns immediately with
        {"status": "approval_required", "estimated_tokens", "last_tokens", "increase_pct"}.

        Supports mid-run cancellation via cancel_run() / reset_cancel().
        Call reset_cancel() before starting a new run, then cancel_run() to stop.

        Args:
            queue:       list of dicts — keys: prompt, model, max_tokens, repeat
            on_progress: optional callback(i, total, result_dict)

        Returns:
            dict with total_tokens, total_cost, results list
            (or approval_required dict if gate triggers)
        """
        import db as _db

        # ── HITL Approval Gate ────────────────────────────────────────────────
        estimated_tokens = sum(
            int(item.get("max_tokens", 4096)) * max(1, min(10, int(item.get("repeat", 1))))
            for item in queue
        )
        last_run_tokens = _get_last_run_tokens()

        if last_run_tokens > 0:
            threshold = 0.20
            try:
                doc = self._load_toml()
                threshold = float(
                    doc.get("manual_mode", {}).get("approval_required_threshold", 0.20)
                )
            except Exception:
                pass
            increase_pct = (estimated_tokens - last_run_tokens) / last_run_tokens
            if increase_pct > threshold:
                logger.info(
                    "[MANUAL MODE ENGINE] Approval required: est %d tokens (+%.0f%% vs last %d)",
                    estimated_tokens, increase_pct * 100, last_run_tokens,
                )
                return {
                    "status": "approval_required",
                    "estimated_tokens": estimated_tokens,
                    "last_tokens": last_run_tokens,
                    "increase_pct": round(increase_pct, 4),
                }

        # ── Execute items ─────────────────────────────────────────────────────
        results = []
        total_tokens = 0
        total_cost = 0.0

        expanded = []
        for item in queue:
            repeat = max(1, min(10, int(item.get("repeat", 1))))
            for _ in range(repeat):
                expanded.append({
                    "prompt":     item.get("prompt", ""),
                    "model":      item.get("model", "claude-sonnet"),
                    "max_tokens": int(item.get("max_tokens", 4096)),
                })

        total = len(expanded)
        for i, item in enumerate(expanded):
            # Gap 3: check cancellation flag before each item
            if self._cancel_event.is_set():
                logger.info("[MANUAL MODE ENGINE] Run cancelled by user after %d/%d items.", i, total)
                break

            model_key = item["model"]
            model_id  = CLAUDE_MODELS.get(model_key, "claude-sonnet-4-6")
            prompt    = item["prompt"]
            max_tok   = item["max_tokens"]

            result = {
                "prompt": prompt[:200], "model": model_key, "model_id": model_id,
                "max_tokens": max_tok, "status": "error",
                "response": "", "input_tokens": 0, "output_tokens": 0,
                "cost": 0.0, "error": "",
            }

            try:
                text, in_tok, out_tok, cost = self._call_claude(prompt, model_id, max_tok)
                result.update({
                    "status": "done", "response": text,
                    "input_tokens": in_tok, "output_tokens": out_tok, "cost": cost,
                })
                total_tokens += in_tok + out_tok
                total_cost   += cost
            except Exception as exc:
                result["error"] = str(exc)

            results.append(result)
            if on_progress:
                try:
                    on_progress(i + 1, total, result)
                except Exception:
                    pass

        # ── Persist to audit_runs table ───────────────────────────────────────
        try:
            _db.log_audit_run(
                prompts=[{"prompt": r["prompt"], "model": r["model"]} for r in results],
                results=results,
                total_tokens=total_tokens,
                total_cost=total_cost,
            )
        except Exception:
            pass

        # ── Governance: persist run record, cost anomaly, audit markdown ──────
        run_summary = {
            "items": [{
                "prompt": r["prompt"],
                "status": r["status"],
                "output": r.get("response", ""),
                "tokens_used": r.get("input_tokens", 0) + r.get("output_tokens", 0),
                "cost_usd": r.get("cost", 0.0),
            } for r in results],
            "total_tokens": total_tokens,
            "total_cost_usd": total_cost,
            "run_at": datetime.now(timezone.utc).isoformat(),
            "anomaly_detected": False,
        }
        _save_run_record(total_tokens, total_cost, len(results), run_summary)
        self._check_cost_anomaly(total_cost, self._get_recent_run_costs())
        self._write_audit_results_md(run_summary)

        return {
            "total_tokens": total_tokens,
            "total_cost":   total_cost,
            "results":      results,
        }

    def _call_claude(self, prompt: str, model_id: str, max_tokens: int) -> tuple:
        """Call Claude API with exponential-backoff retry on rate-limit (HTTP 429).

        Returns (text, input_tokens, output_tokens, cost_usd).
        Raises on non-429 errors or after MAX_RETRIES exhausted.
        """
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        client = anthropic.Anthropic(api_key=api_key)
        time.sleep(0.3)  # 300 ms minimum between requests (rate-limit headroom)

        MAX_RETRIES = 4
        for attempt in range(MAX_RETRIES):
            try:
                resp = client.messages.create(
                    model=model_id,
                    max_tokens=max_tokens,
                    system=[{
                        "type": "text",
                        "text": "You are a helpful AI audit assistant.",
                        "cache_control": {"type": "ephemeral"},
                    }],
                    messages=[{"role": "user", "content": prompt}],
                )
                break  # success — exit retry loop
            except Exception as e:
                if "429" in str(e) or "rate_limit" in str(e).lower():
                    wait = 2 ** attempt  # 1s, 2s, 4s, 8s
                    logger.warning(
                        "[MANUAL MODE ENGINE] Rate limit hit, retrying in %ds "
                        "(attempt %d/%d)", wait, attempt + 1, MAX_RETRIES,
                    )
                    time.sleep(wait)
                    if attempt == MAX_RETRIES - 1:
                        raise
                else:
                    raise

        in_tok  = resp.usage.input_tokens
        out_tok = resp.usage.output_tokens
        rates   = _PRICING.get(model_id, _PRICING["claude-sonnet-4-6"])
        cost    = round(
            in_tok  * rates["input"]  / 1_000_000
            + out_tok * rates["output"] / 1_000_000,
            6,
        )
        return resp.content[0].text, in_tok, out_tok, cost

    # ── History ───────────────────────────────────────────────────────────────

    def get_run_history(self, limit: int = 20) -> list:
        """Return recent audit runs from DB, newest first."""
        try:
            import db as _db
            return _db.get_audit_runs(limit=limit)
        except Exception:
            return []

    # ── Governance methods ────────────────────────────────────────────────────

    def _check_cost_anomaly(self, run_cost: float, history: list) -> bool:
        """Detect cost spikes vs rolling average. Delegates to module-level function."""
        return _check_cost_anomaly(run_cost, history)

    def _get_recent_run_costs(self, limit: int = 5) -> list:
        """Return cost_usd values from recent manual_mode_runs. Delegates to module-level."""
        return _get_recent_run_costs(limit)

    def _write_audit_results_md(self, run_summary: dict):
        """Write audit results markdown to knowledge/. Delegates to module-level."""
        return _write_audit_results_md(run_summary)
