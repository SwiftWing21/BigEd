"""
Manual Mode Engine — operator-driven Claude Code compliance auditing.

Provides:
  - run_queue()            : execute a list of audit prompts with HITL gate
  - _check_cost_anomaly()  : rolling-average cost spike detection
  - _write_audit_results_md(): structured markdown audit output
  - launch_vscode()        : cross-platform VS Code launcher
"""
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FLEET_DIR = Path(__file__).parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
LOGS_DIR = FLEET_DIR / "logs"

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
    import json
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


def _get_recent_run_costs(limit: int = 5) -> list[float]:
    """Return the cost_usd values of the last `limit` completed runs."""
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

def _check_cost_anomaly(run_cost: float, history: list[float]) -> bool:
    """Detect cost spikes vs rolling average of last 5 runs.

    Writes a DB alert and logs a warning if run_cost > avg * 2.5.

    Returns True if an anomaly was detected.
    """
    if not history:
        return False
    avg = sum(history) / len(history)
    if avg <= 0:
        return False
    if run_cost > avg * 2.5:
        msg = f"Cost anomaly: ${run_cost:.4f} vs avg ${avg:.4f} (×{run_cost/avg:.1f})"
        logger.warning("[MANUAL MODE] %s", msg)
        _db_log_alert(
            severity="warning",
            source="manual_mode",
            message=msg,
            details={"run_cost": run_cost, "rolling_avg": avg, "history": history},
        )
        return True
    return False


# ── Audit Results Markdown ────────────────────────────────────────────────────

def _write_audit_results_md(run_summary: dict) -> Path | None:
    """Write a structured audit-results markdown file to knowledge/.

    Args:
        run_summary: dict with keys: items, total_tokens, total_cost_usd,
                     run_at (ISO str), anomaly_detected (bool).

    Returns:
        Path to the written file, or None on failure.
    """
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = KNOWLEDGE_DIR / f"audit_results_{ts}.md"

    items: list[dict] = run_summary.get("items", [])
    total_tokens: int = run_summary.get("total_tokens", 0)
    total_cost: float = run_summary.get("total_cost_usd", 0.0)
    run_at: str = run_summary.get("run_at", ts)
    anomaly: bool = run_summary.get("anomaly_detected", False)

    # Collect recommendations from items that returned action items
    recommendations: list[str] = []
    for item in items:
        for rec in item.get("action_items", []):
            recommendations.append(f"- [{item.get('prompt', 'unknown')}] {rec}")

    lines = [
        "# Audit Results",
        "",
        "## Run Summary",
        "",
        f"| Field | Value |",
        f"|-------|-------|",
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
        tokens = item.get("tokens_used", 0)
        cost = item.get("cost_usd", 0.0)
        error = item.get("error", "")
        output_preview = str(item.get("output", ""))[:300].replace("\n", " ")

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
        f"| Prompt | Tokens | Cost (USD) |",
        f"|--------|--------|-----------|",
    ]
    for item in items:
        prompt_short = item.get("prompt", "")[:50]
        lines.append(
            f"| {prompt_short} | {item.get('tokens_used', 0):,} | ${item.get('cost_usd', 0.0):.4f} |"
        )
    lines += [
        f"| **TOTAL** | **{total_tokens:,}** | **${total_cost:.4f}** |",
        "",
    ]

    if recommendations:
        lines += [
            "## Recommendations",
            "",
        ] + recommendations + [""]
    else:
        lines += ["## Recommendations", "", "_No action items returned by this run._", ""]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("[MANUAL MODE] Audit results written to %s", out_path)
    return out_path


# ── Queue Execution ───────────────────────────────────────────────────────────

def run_queue(
    queue_items: list[dict],
    config: dict | None = None,
) -> dict:
    """Execute a list of audit prompt items via Claude API.

    Each item in queue_items:
        {
            "prompt": str,
            "skill": str (optional, defaults to "code_review"),
            "max_tokens": int (optional, default 1024),
            "repeat": int (optional, default 1),
        }

    Config keys consumed (from fleet.toml [manual_mode]):
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
    results: list[dict] = []
    total_tokens = 0
    total_cost = 0.0

    for item in queue_items:
        prompt: str = item.get("prompt", "")
        skill: str = item.get("skill", "code_review")
        max_tokens: int = int(item.get("max_tokens", 1024))
        repeat: int = int(item.get("repeat", 1))

        for _ in range(repeat):
            result = _run_single_item(prompt, skill, max_tokens, cfg)
            results.append(result)
            total_tokens += result.get("tokens_used", 0)
            total_cost += result.get("cost_usd", 0.0)

    # ── Cost Anomaly Check ────────────────────────────────────────────────────
    history = _get_recent_run_costs()
    anomaly = _check_cost_anomaly(total_cost, history)

    # ── Persist run record ────────────────────────────────────────────────────
    run_summary = {
        "items": results,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "run_at": run_at,
        "anomaly_detected": anomaly,
    }
    _save_run_record(total_tokens, total_cost, len(results), run_summary)

    # ── Auto-write audit results markdown ─────────────────────────────────────
    md_path = _write_audit_results_md(run_summary)

    return {
        "status": "ok",
        "items": results,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "anomaly_detected": anomaly,
        "audit_md_path": str(md_path) if md_path else None,
    }


def _run_single_item(prompt: str, skill: str, max_tokens: int, cfg: dict) -> dict:
    """Execute one prompt item. Returns a result dict."""
    try:
        sys.path.insert(0, str(FLEET_DIR))
        from skills._models import call_complex  # type: ignore[import]

        output = call_complex(
            prompt,
            max_tokens=max_tokens,
            system="You are a compliance auditor reviewing BigEd CC fleet code.",
        )
        tokens_used = getattr(output, "usage", None)
        if tokens_used is not None:
            in_t = getattr(tokens_used, "input_tokens", 0)
            out_t = getattr(tokens_used, "output_tokens", 0)
            tok = in_t + out_t
        else:
            # Estimate if usage not available
            tok = max_tokens // 2
        cost = _estimate_cost(tok)
        output_text = getattr(output, "content", [{}])
        if isinstance(output_text, list) and output_text:
            output_text = output_text[0].get("text", str(output_text))
        action_items = _extract_action_items(str(output_text))
        return {
            "prompt": prompt,
            "skill": skill,
            "status": "ok",
            "output": str(output_text)[:2000],
            "tokens_used": tok,
            "cost_usd": cost,
            "action_items": action_items,
        }
    except Exception as exc:
        logger.error("[MANUAL MODE] Item failed: %s — %s", prompt[:60], exc)
        return {
            "prompt": prompt,
            "skill": skill,
            "status": "error",
            "error": str(exc),
            "output": "",
            "tokens_used": 0,
            "cost_usd": 0.0,
            "action_items": [],
        }


def _estimate_cost(tokens: int) -> float:
    """Rough cost estimate at Sonnet pricing ($3/$15 per M in/out tokens)."""
    return tokens * 0.000009  # ~$9/M blended


def _extract_action_items(text: str) -> list[str]:
    """Extract lines that look like action items / TODOs from output text."""
    keywords = ("TODO", "FIXME", "ACTION:", "- [ ]", "must ", "should ", "needs to")
    items = []
    for line in text.splitlines():
        stripped = line.strip()
        if any(kw.lower() in stripped.lower() for kw in keywords):
            if stripped and len(stripped) > 10:
                items.append(stripped[:200])
    return items[:10]


# ── VS Code Launcher ──────────────────────────────────────────────────────────

def launch_vscode(workspace_path: str) -> bool:
    """Launch VS Code in the given workspace. Returns True if launched."""
    code_cmd = shutil.which("code")
    if not code_cmd:
        candidates = [
            r"C:\Users\{}\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd".format(
                os.getenv("USERNAME", "")
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
