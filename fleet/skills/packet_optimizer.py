"""
Packet Optimizer — Audit and optimize sent/received data sizes across all fleet API calls.

Tracks payload sizes for: Ollama, Claude, Gemini, dashboard SSE, inter-agent messages.
Identifies oversized prompts, redundant context, bloated responses.
Auto-evolves compression strategies via research loop.

Actions:
  audit       — snapshot current packet sizes across all providers
  optimize    — apply safe compression (trim context, reduce system prompts)
  benchmark   — compare before/after sizes on identical tasks
  evolve      — auto-research new compression methods (idle evolution)

Usage:
    lead_client.py task '{"type": "packet_optimizer"}'
    lead_client.py task '{"type": "packet_optimizer", "payload": {"action": "audit"}}'
"""
import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

FLEET_DIR = Path(__file__).parent.parent
SKILL_NAME = "packet_optimizer"
DESCRIPTION = "Audit and optimize sent/received packet sizes across fleet API calls."
COMPLEXITY = "simple"
REQUIRES_NETWORK = False

# Target sizes (bytes) — anything above triggers optimization recommendation
TARGETS = {
    "system_prompt": 2000,       # system prompt should be < 2KB
    "user_prompt": 4000,         # user message < 4KB typical
    "total_input": 8000,         # total input < 8KB ideal
    "response": 4000,            # response < 4KB typical
    "sse_event": 1000,           # SSE event < 1KB
    "db_payload": 2000,          # task payload_json < 2KB
}


def run(payload: dict, config: dict, log=None) -> dict:
    if log is None:
        log = logging.getLogger(__name__)
    action = payload.get("action", "audit")

    if action == "audit":
        return _audit(config, log)
    elif action == "optimize":
        return _optimize(config, log)
    elif action == "benchmark":
        return _benchmark(config, log)
    elif action == "evolve":
        return _evolve(config, log)
    else:
        return {"error": f"Unknown action: {action}"}


def _audit(config, log) -> dict:
    """Snapshot current packet sizes across all communication channels."""
    findings = []
    stats = {"total_calls": 0, "total_input_bytes": 0, "total_output_bytes": 0}

    db_path = FLEET_DIR / "fleet.db"
    if not db_path.exists():
        return {"error": "fleet.db not found"}

    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row

    # ── 1. API call sizes (from usage table) ──────────────────────────────
    try:
        rows = conn.execute("""
            SELECT provider, model, skill,
                   AVG(input_tokens) as avg_input_tok,
                   AVG(output_tokens) as avg_output_tok,
                   MAX(input_tokens) as max_input_tok,
                   MAX(output_tokens) as max_output_tok,
                   COUNT(*) as calls,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output
            FROM usage
            WHERE created_at >= datetime('now', '-7 days')
            GROUP BY provider, model, skill
            ORDER BY total_input DESC
            LIMIT 30
        """).fetchall()

        api_stats = []
        for r in rows:
            avg_input_bytes = (r["avg_input_tok"] or 0) * 4  # ~4 bytes per token
            avg_output_bytes = (r["avg_output_tok"] or 0) * 4
            max_input_bytes = (r["max_input_tok"] or 0) * 4

            entry = {
                "provider": r["provider"] or "local",
                "model": r["model"],
                "skill": r["skill"],
                "calls": r["calls"],
                "avg_input_bytes": round(avg_input_bytes),
                "avg_output_bytes": round(avg_output_bytes),
                "max_input_bytes": round(max_input_bytes),
                "total_input_mb": round((r["total_input"] or 0) * 4 / 1024 / 1024, 2),
                "total_output_mb": round((r["total_output"] or 0) * 4 / 1024 / 1024, 2),
            }
            api_stats.append(entry)

            stats["total_calls"] += r["calls"]
            stats["total_input_bytes"] += (r["total_input"] or 0) * 4
            stats["total_output_bytes"] += (r["total_output"] or 0) * 4

            # Flag oversized
            if avg_input_bytes > TARGETS["total_input"]:
                findings.append({
                    "severity": "warning",
                    "area": "api_input",
                    "skill": r["skill"],
                    "message": f"{r['skill']} avg input {avg_input_bytes/1024:.1f}KB "
                               f"(target <{TARGETS['total_input']/1024:.0f}KB)",
                    "savings_pct": round((1 - TARGETS['total_input'] / avg_input_bytes) * 100),
                })

            if max_input_bytes > TARGETS["total_input"] * 3:
                findings.append({
                    "severity": "high",
                    "area": "api_input_spike",
                    "skill": r["skill"],
                    "message": f"{r['skill']} max input {max_input_bytes/1024:.1f}KB — "
                               f"possible context bloat",
                })

    except Exception as e:
        findings.append({"severity": "error", "area": "usage_query", "message": str(e)})

    # ── 2. Task payload sizes ─────────────────────────────────────────────
    try:
        payload_stats = conn.execute("""
            SELECT type,
                   AVG(LENGTH(payload_json)) as avg_payload,
                   MAX(LENGTH(payload_json)) as max_payload,
                   AVG(LENGTH(result_json)) as avg_result,
                   MAX(LENGTH(result_json)) as max_result,
                   COUNT(*) as tasks
            FROM tasks
            WHERE created_at >= datetime('now', '-7 days')
            AND payload_json IS NOT NULL
            GROUP BY type
            ORDER BY avg_payload DESC
            LIMIT 20
        """).fetchall()

        for r in payload_stats:
            avg_p = r["avg_payload"] or 0
            max_p = r["max_payload"] or 0
            if avg_p > TARGETS["db_payload"]:
                findings.append({
                    "severity": "info",
                    "area": "task_payload",
                    "skill": r["type"],
                    "message": f"{r['type']} avg payload {avg_p/1024:.1f}KB "
                               f"(target <{TARGETS['db_payload']/1024:.0f}KB)",
                })
            if max_p > TARGETS["db_payload"] * 5:
                findings.append({
                    "severity": "warning",
                    "area": "task_payload_spike",
                    "skill": r["type"],
                    "message": f"{r['type']} max payload {max_p/1024:.1f}KB — consider compression",
                })

    except Exception:
        pass

    # ── 3. Message sizes (inter-agent) ────────────────────────────────────
    try:
        msg_stats = conn.execute("""
            SELECT channel,
                   AVG(LENGTH(body_json)) as avg_size,
                   MAX(LENGTH(body_json)) as max_size,
                   COUNT(*) as messages
            FROM messages
            WHERE created_at >= datetime('now', '-7 days')
            GROUP BY channel
        """).fetchall()

        for r in msg_stats:
            if (r["avg_size"] or 0) > 1000:
                findings.append({
                    "severity": "info",
                    "area": "messages",
                    "channel": r["channel"],
                    "message": f"Channel '{r['channel']}' avg message {(r['avg_size'] or 0)/1024:.1f}KB",
                })

    except Exception:
        pass

    conn.close()

    # ── 4. Summary ────────────────────────────────────────────────────────
    stats["total_input_mb"] = round(stats["total_input_bytes"] / 1024 / 1024, 2)
    stats["total_output_mb"] = round(stats["total_output_bytes"] / 1024 / 1024, 2)
    stats["total_bandwidth_mb"] = stats["total_input_mb"] + stats["total_output_mb"]

    # Optimization potential
    high_findings = [f for f in findings if f["severity"] in ("high", "warning")]
    potential_savings = sum(f.get("savings_pct", 10) for f in high_findings)

    log.info(f"Packet audit: {len(findings)} findings, "
             f"{stats['total_bandwidth_mb']:.1f}MB bandwidth (7d), "
             f"~{potential_savings}% potential savings")

    return {
        "stats": stats,
        "findings": findings,
        "api_breakdown": api_stats if 'api_stats' in dir() else [],
        "optimization_potential_pct": min(potential_savings, 80),
    }


def _optimize(config, log) -> dict:
    """Generate optimization recommendations based on audit."""
    audit = _audit(config, log)
    recommendations = []

    for f in audit.get("findings", []):
        skill = f.get("skill", f.get("channel", "unknown"))

        if f["area"] == "api_input":
            recommendations.append({
                "skill": skill,
                "action": "trim_system_prompt",
                "detail": "Reduce system prompt to essential instructions only. "
                          "Move examples and context to cached prefix.",
                "estimated_savings": f"{f.get('savings_pct', 10)}%",
            })

        elif f["area"] == "api_input_spike":
            recommendations.append({
                "skill": skill,
                "action": "cap_context_window",
                "detail": "Add max_tokens limit. Truncate conversation history to last 3 turns.",
                "estimated_savings": "30-50%",
            })

        elif f["area"] == "task_payload_spike":
            recommendations.append({
                "skill": skill,
                "action": "compress_payload",
                "detail": "Use JSON minification. Store large payloads as file references "
                          "instead of inline content.",
                "estimated_savings": "20-40%",
            })

    return {
        "recommendations": recommendations,
        "audit_summary": {
            "total_findings": len(audit.get("findings", [])),
            "total_bandwidth_mb": audit.get("stats", {}).get("total_bandwidth_mb", 0),
        },
    }


def _benchmark(config, log) -> dict:
    """Compare packet sizes before/after optimization on a sample task."""
    # Run a simple summarize task and measure sizes
    try:
        import urllib.request
        host = config.get("models", {}).get("ollama_host", "http://localhost:11434")

        test_prompt = "Summarize the concept of prompt caching in 2 sentences."
        system = "You are a concise technical writer."

        # Measure request size
        request_body = json.dumps({
            "model": config.get("models", {}).get("local", "qwen3:8b"),
            "prompt": test_prompt,
            "system": system,
            "stream": False,
        })
        request_bytes = len(request_body.encode())

        body = request_body.encode()
        req = urllib.request.Request(
            f"{host}/api/generate", data=body, method="POST",
            headers={"Content-Type": "application/json"})

        start = time.time()
        with urllib.request.urlopen(req, timeout=30) as r:
            response_data = r.read()
        elapsed = time.time() - start

        response_bytes = len(response_data)

        result = {
            "request_bytes": request_bytes,
            "response_bytes": response_bytes,
            "total_bytes": request_bytes + response_bytes,
            "elapsed_ms": round(elapsed * 1000),
            "efficiency": round(response_bytes / max(request_bytes, 1), 2),
        }

        log.info(f"Benchmark: {request_bytes}B sent, {response_bytes}B received, "
                 f"{elapsed*1000:.0f}ms")
        return result

    except Exception as e:
        return {"error": f"Benchmark failed: {e}"}


def _evolve(config, log) -> dict:
    """Auto-research compression strategies for fleet communications.

    This action is designed for idle evolution — it researches new
    optimization techniques and logs discoveries for operator review.
    """
    audit = _audit(config, log)

    # Identify top 3 skills by bandwidth usage
    api_data = audit.get("api_breakdown", [])
    if not api_data:
        return {"message": "No API usage data to analyze"}

    top_skills = sorted(api_data, key=lambda x: x.get("total_input_mb", 0), reverse=True)[:3]

    discoveries = []
    for skill_data in top_skills:
        skill = skill_data["skill"]
        avg_input = skill_data.get("avg_input_bytes", 0)
        calls = skill_data.get("calls", 0)

        # Calculate potential savings per strategy
        strategies = []

        # Strategy 1: Prompt caching
        if skill_data.get("provider") == "claude" and avg_input > 2000:
            cache_savings = avg_input * 0.9 * calls  # 90% on cached reads
            strategies.append({
                "strategy": "prompt_caching",
                "savings_bytes_7d": round(cache_savings),
                "savings_mb_7d": round(cache_savings / 1024 / 1024, 2),
                "complexity": "low",
                "description": f"Enable cache_control on {skill} system prompt",
            })

        # Strategy 2: Context truncation
        if avg_input > TARGETS["total_input"]:
            trunc_savings = (avg_input - TARGETS["total_input"]) * calls
            strategies.append({
                "strategy": "context_truncation",
                "savings_bytes_7d": round(trunc_savings),
                "savings_mb_7d": round(trunc_savings / 1024 / 1024, 2),
                "complexity": "medium",
                "description": f"Limit {skill} context to {TARGETS['total_input']/1024:.0f}KB",
            })

        # Strategy 3: Response compression
        avg_output = skill_data.get("avg_output_bytes", 0)
        if avg_output > TARGETS["response"]:
            compress_savings = (avg_output * 0.3) * calls  # ~30% via shorter responses
            strategies.append({
                "strategy": "response_compression",
                "savings_bytes_7d": round(compress_savings),
                "savings_mb_7d": round(compress_savings / 1024 / 1024, 2),
                "complexity": "low",
                "description": f"Add max_tokens constraint to {skill}",
            })

        if strategies:
            discoveries.append({
                "skill": skill,
                "current_avg_input_kb": round(avg_input / 1024, 1),
                "calls_7d": calls,
                "strategies": strategies,
            })

    # Save discoveries for review
    discovery_path = FLEET_DIR / "knowledge" / "reports"
    discovery_path.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = discovery_path / f"packet_optimization_{ts}.md"

    report = f"# Packet Optimization Discovery — {ts}\n\n"
    for d in discoveries:
        report += f"## {d['skill']} ({d['current_avg_input_kb']:.1f}KB avg, {d['calls_7d']} calls)\n\n"
        for s in d["strategies"]:
            report += f"- **{s['strategy']}**: {s['description']}\n"
            report += f"  Savings: {s['savings_mb_7d']:.2f}MB/week, complexity: {s['complexity']}\n\n"

    report_file.write_text(report, encoding="utf-8")
    log.info(f"Packet optimization discoveries saved: {report_file.name}")

    return {
        "discoveries": discoveries,
        "report_file": str(report_file),
        "total_potential_savings_mb": round(
            sum(s["savings_mb_7d"] for d in discoveries for s in d["strategies"]), 2
        ),
    }
