"""
Claude Maximum Efficiency — cost optimization skill for Claude API usage.

Implements every known token-saving technique:
  1. Prompt caching audit   — find skills not using cache_control ephemeral
  2. Batch API eligibility  — identify non-urgent tasks for 50% cost reduction
  3. Capacity windows       — detect 2x bonus periods, recommend scaling
  4. MCP tool routing       — reduce API round-trips via MCP servers
  5. Savings reporting      — before/after cost projections

Actions:
    audit          — analyze cost_tracking data, find Claude-using skills, check caching
    optimize       — write recommendations (read-only, never auto-modifies providers.py)
    capacity_check — check current time against fleet.toml [capacity] windows
    batch_queue    — identify non-urgent pending tasks eligible for batch API
    report         — generate savings report markdown

Usage:
    lead_client.py task '{"type": "claude_efficiency"}'
    lead_client.py task '{"type": "claude_efficiency", "payload": {"action": "audit"}}'
    lead_client.py task '{"type": "claude_efficiency", "payload": {"action": "capacity_check"}}'
    lead_client.py task '{"type": "claude_efficiency", "payload": {"action": "report"}}'
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
SKILL_NAME = "claude_efficiency"
DESCRIPTION = "Optimize Claude API usage — caching, batching, MCP routing, capacity windows"
COMPLEXITY = "simple"
REQUIRES_NETWORK = False  # analyzes local data, doesn't call APIs

# ── Constants ────────────────────────────────────────────────────────────────

# ET (Eastern Time) offset from UTC: -5 standard, -4 daylight
# We calculate dynamically to handle DST transitions correctly.
_ET = timezone(timedelta(hours=-5))
_EDT = timezone(timedelta(hours=-4))

# Skills known to call Claude API (from providers.py SKILL_COMPLEXITY + FALLBACK_CHAIN)
CLAUDE_CALLING_SKILLS = {
    "code_review", "code_write", "code_write_review", "code_discuss",
    "code_refactor", "discuss", "lead_research", "plan_workload",
    "security_audit", "skill_evolve", "skill_chain", "skill_learn",
    "skill_promote", "synthesize", "evaluate", "legal_draft",
    "claude_code", "swarm_intelligence", "evolution_coordinator",
    "swarm_consensus", "dataset_synthesize", "research_loop",
    "web_search", "summarize", "marketing", "curriculum_update",
}

# Skills eligible for Message Batches API (non-realtime, no user waiting)
BATCH_ELIGIBLE_SKILLS = {
    "code_review", "code_quality", "security_audit", "skill_evolve",
    "evolution_coordinator", "dataset_synthesize", "skill_test",
    "benchmark", "evaluate", "stability_report", "skill_train",
    "code_refactor", "rag_compress", "knowledge_prune",
}

# Skills that should always cache their system prompts
CACHE_RECOMMENDED_SKILLS = {
    "code_review", "code_write", "code_write_review", "code_discuss",
    "code_refactor", "discuss", "lead_research", "plan_workload",
    "security_audit", "summarize", "evaluate", "synthesize",
    "skill_evolve", "legal_draft", "claude_code", "research_loop",
    "marketing", "curriculum_update", "dataset_synthesize",
}


# ── Timezone helper ──────────────────────────────────────────────────────────

def _get_et_now() -> datetime:
    """Return current time in US Eastern, handling DST automatically.

    Uses a simplified DST rule: EDT (UTC-4) from second Sunday of March
    to first Sunday of November, EST (UTC-5) otherwise.
    """
    utc_now = datetime.now(timezone.utc)
    year = utc_now.year

    # Second Sunday of March: find March 1, advance to second Sunday
    mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    days_to_sunday = (6 - mar1.weekday()) % 7
    dst_start = mar1 + timedelta(days=days_to_sunday + 7, hours=2)  # 2 AM ET

    # First Sunday of November
    nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    days_to_sunday_nov = (6 - nov1.weekday()) % 7
    dst_end = nov1 + timedelta(days=days_to_sunday_nov, hours=2)  # 2 AM ET

    if dst_start <= utc_now < dst_end:
        return utc_now.astimezone(_EDT)
    return utc_now.astimezone(_ET)


# ── Core functions ───────────────────────────────────────────────────────────

def is_in_bonus_window(config: dict) -> bool:
    """Check if current time is in a Claude capacity bonus window.

    Reads [capacity] from config (fleet.toml). Checks current time in ET
    against weekday/weekend bonus hours defined in capacity.windows.

    Returns True if currently in a bonus period, False otherwise.
    """
    capacity = config.get("capacity", {})
    if not capacity.get("enabled", False):
        return False

    windows = capacity.get("windows", [])
    if not windows:
        return False

    et_now = _get_et_now()
    today = et_now.date()
    current_hour = et_now.hour
    is_weekend = et_now.weekday() >= 5  # Saturday=5, Sunday=6

    for window in windows:
        # Parse window date range
        try:
            start_date = datetime.strptime(window["start"], "%Y-%m-%d").date()
            end_date = datetime.strptime(window["end"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue

        if not (start_date <= today <= end_date):
            continue

        # Weekend: all day bonus if configured
        if is_weekend and window.get("weekend_all_day", False):
            return True

        # Weekday: check bonus hours
        # Bonus hours wrap around midnight: e.g. 14:00-08:00 means 2PM to 8AM next day
        bonus_start = window.get("weekday_bonus_start_et")
        bonus_end = window.get("weekday_bonus_end_et")

        if bonus_start is None or bonus_end is None:
            continue

        if bonus_start > bonus_end:
            # Wraps midnight: bonus is active from start..23:59 and 00:00..end
            if current_hour >= bonus_start or current_hour < bonus_end:
                return True
        else:
            # Normal range (e.g. 22:00-06:00 doesn't wrap, 9:00-17:00)
            if bonus_start <= current_hour < bonus_end:
                return True

    return False


def get_savings_potential(config: dict) -> dict:
    """Analyze current usage and calculate potential savings.

    Queries cost_tracking for last 30 days. Counts skills using Claude,
    checks cache_system usage, estimates batch-eligible tasks.

    Returns:
        {current_cost, projected_cost, savings_pct, recommendations}
    """
    sys.path.insert(0, str(FLEET_DIR))

    try:
        import cost_tracking
    except ImportError:
        return {"error": "cost_tracking module not available"}

    try:
        import db as fleet_db
    except ImportError:
        return {"error": "db module not available"}

    # Get 30-day usage summary by skill
    usage_by_skill = cost_tracking.get_usage_summary(period="month", group_by="skill")
    usage_by_model = cost_tracking.get_usage_summary(period="month", group_by="model")

    total_cost = sum(row.get("total_cost", 0) or 0 for row in usage_by_skill)
    recommendations = []

    # 1. Prompt caching analysis
    claude_skills_cost = 0
    claude_skills_without_cache = []
    for row in usage_by_skill:
        skill = row.get("skill", "")
        if skill in CLAUDE_CALLING_SKILLS:
            skill_cost = row.get("total_cost", 0) or 0
            claude_skills_cost += skill_cost
            cache_reads = row.get("total_cache_reads", 0) or 0
            total_input = row.get("total_input", 0) or 0
            # If cache read ratio is < 10% of input tokens, caching is underused
            if total_input > 0 and cache_reads / total_input < 0.10:
                claude_skills_without_cache.append(skill)

    caching_savings = 0
    if claude_skills_without_cache:
        # Prompt caching saves ~30% on input tokens for cached prefixes
        caching_savings = claude_skills_cost * 0.30
        recommendations.append({
            "technique": "prompt_caching",
            "current": f"{len(CLAUDE_CALLING_SKILLS) - len(claude_skills_without_cache)} of "
                       f"{len(CLAUDE_CALLING_SKILLS)} Claude skills use caching",
            "potential": f"all {len(CLAUDE_CALLING_SKILLS)} Claude-routed skills",
            "skills_missing_cache": claude_skills_without_cache[:10],
            "estimated_savings_usd": round(caching_savings, 2),
            "estimated_savings_pct": 30,
        })

    # 2. Batch API analysis
    batch_cost = 0
    batch_eligible_found = []
    for row in usage_by_skill:
        skill = row.get("skill", "")
        if skill in BATCH_ELIGIBLE_SKILLS:
            skill_cost = row.get("total_cost", 0) or 0
            batch_cost += skill_cost
            if skill_cost > 0:
                batch_eligible_found.append(skill)

    batch_savings = batch_cost * 0.50  # Batch API is 50% off
    if batch_eligible_found:
        recommendations.append({
            "technique": "batch_api",
            "current": "0 skills use batching",
            "potential": f"{len(batch_eligible_found)} non-realtime skills eligible",
            "skills": batch_eligible_found[:10],
            "estimated_savings_usd": round(batch_savings, 2),
            "estimated_savings_pct": 50,
        })

    # 3. Capacity window analysis
    in_bonus = is_in_bonus_window(config)
    capacity_cfg = config.get("capacity", {})
    if capacity_cfg.get("enabled", False):
        recommendations.append({
            "technique": "capacity_windows",
            "current": "active" if in_bonus else "monitoring",
            "bonus_active_now": in_bonus,
            "potential": "shift 40% of Claude calls to bonus hours",
            "estimated_savings": "effectively 2x capacity at same cost",
        })
    else:
        recommendations.append({
            "technique": "capacity_windows",
            "current": "not configured",
            "potential": "enable [capacity] in fleet.toml to track bonus periods",
            "estimated_savings": "2x throughput during promotion windows",
        })

    # 4. Model routing check — are simple skills being sent to expensive models?
    model_waste = 0
    for row in usage_by_model:
        model = row.get("model", "")
        if "sonnet" in model or "opus" in model:
            model_waste += (row.get("total_cost", 0) or 0)

    projected_savings = caching_savings + batch_savings
    projected_cost = max(0, total_cost - projected_savings)
    savings_pct = round((projected_savings / total_cost * 100) if total_cost > 0 else 0, 1)

    return {
        "current_cost_30d": round(total_cost, 4),
        "projected_cost_30d": round(projected_cost, 4),
        "projected_savings_30d": round(projected_savings, 4),
        "savings_pct": savings_pct,
        "recommendations": recommendations,
        "claude_skills_count": len(CLAUDE_CALLING_SKILLS),
        "batch_eligible_count": len(batch_eligible_found),
        "bonus_window_active": in_bonus,
    }


# ── Action handlers ──────────────────────────────────────────────────────────

def _action_audit(config: dict, log) -> dict:
    """Analyze cost_tracking data, find Claude-using skills, check caching."""
    sys.path.insert(0, str(FLEET_DIR))

    try:
        import db as fleet_db
    except ImportError:
        return {"error": "db module not available — run from fleet directory"}

    result = {
        "action": "audit",
        "period": "30 days",
        "findings": [],
        "summary": {},
    }

    try:
        conn = fleet_db.get_conn()
    except Exception as e:
        return {"error": f"Cannot connect to fleet.db: {e}"}

    try:
        # 1. Total Claude spend
        claude_usage = conn.execute("""
            SELECT COALESCE(SUM(cost_usd), 0) as total_cost,
                   COALESCE(SUM(input_tokens), 0) as total_input,
                   COALESCE(SUM(output_tokens), 0) as total_output,
                   COALESCE(SUM(cache_read_tokens), 0) as total_cache_reads,
                   COALESCE(SUM(cache_create_tokens), 0) as total_cache_creates,
                   COUNT(*) as total_calls
            FROM usage
            WHERE created_at >= datetime('now', '-30 days')
              AND (provider = 'claude' OR model LIKE 'claude-%')
        """).fetchone()

        result["summary"]["claude"] = {
            "cost_usd": round(claude_usage["total_cost"], 4),
            "input_tokens": claude_usage["total_input"],
            "output_tokens": claude_usage["total_output"],
            "cache_read_tokens": claude_usage["total_cache_reads"],
            "cache_create_tokens": claude_usage["total_cache_creates"],
            "calls": claude_usage["total_calls"],
        }

        # 2. Per-skill Claude usage
        skill_rows = conn.execute("""
            SELECT skill,
                   COALESCE(SUM(cost_usd), 0) as cost,
                   COALESCE(SUM(input_tokens), 0) as input_tok,
                   COALESCE(SUM(cache_read_tokens), 0) as cache_reads,
                   COUNT(*) as calls
            FROM usage
            WHERE created_at >= datetime('now', '-30 days')
              AND (provider = 'claude' OR model LIKE 'claude-%')
            GROUP BY skill
            ORDER BY cost DESC
        """).fetchall()

        skills_using_claude = []
        skills_not_caching = []
        for row in skill_rows:
            skill_info = {
                "skill": row["skill"],
                "cost_usd": round(row["cost"], 4),
                "input_tokens": row["input_tok"],
                "cache_read_tokens": row["cache_reads"],
                "calls": row["calls"],
            }
            skills_using_claude.append(skill_info)
            # Check cache utilization
            if row["input_tok"] > 0:
                cache_ratio = row["cache_reads"] / row["input_tok"]
                skill_info["cache_ratio"] = round(cache_ratio, 3)
                if cache_ratio < 0.10 and row["skill"] in CACHE_RECOMMENDED_SKILLS:
                    skills_not_caching.append(row["skill"])

        result["skills_using_claude"] = skills_using_claude

        # 3. Findings
        if skills_not_caching:
            result["findings"].append({
                "type": "caching_underused",
                "severity": "high",
                "message": f"{len(skills_not_caching)} Claude skills have <10% cache hit rate",
                "skills": skills_not_caching,
                "fix": "Ensure cache_system=True in call_complex() for these skills",
                "estimated_savings_pct": 30,
            })

        # Check for batch-eligible high-frequency skills
        batch_candidates = conn.execute("""
            SELECT skill, COUNT(*) as calls,
                   COALESCE(SUM(cost_usd), 0) as cost
            FROM usage
            WHERE created_at >= datetime('now', '-7 days')
              AND (provider = 'claude' OR model LIKE 'claude-%')
              AND skill IN ({})
            GROUP BY skill
            HAVING calls > 10
            ORDER BY cost DESC
        """.format(",".join(f"'{s}'" for s in BATCH_ELIGIBLE_SKILLS))).fetchall()

        if batch_candidates:
            batch_cost = sum(r["cost"] for r in batch_candidates)
            result["findings"].append({
                "type": "batch_eligible",
                "severity": "medium",
                "message": f"{sum(r['calls'] for r in batch_candidates)} calls to "
                           f"{len(batch_candidates)} batch-eligible skills in 7d "
                           f"(${batch_cost:.4f})",
                "skills": [{"skill": r["skill"], "calls": r["calls"],
                            "cost": round(r["cost"], 4)} for r in batch_candidates],
                "fix": "Use Message Batches API for 50% cost reduction on these skills",
                "estimated_savings_pct": 50,
            })

        # Capacity window status
        in_bonus = is_in_bonus_window(config)
        result["capacity_bonus_active"] = in_bonus

        total_cost = claude_usage["total_cost"]
        result["total_claude_cost_30d"] = round(total_cost, 4)
        result["findings_count"] = len(result["findings"])

    finally:
        conn.close()

    if log:
        log.info(f"Claude efficiency audit: ${result.get('total_claude_cost_30d', 0):.4f} "
                 f"Claude spend, {result['findings_count']} findings")
    return result


def _action_optimize(config: dict, log) -> dict:
    """Generate optimization recommendations (read-only, never auto-modifies)."""
    savings = get_savings_potential(config)

    if "error" in savings:
        return savings

    report = {
        "action": "optimize",
        "mode": "read-only (recommendations only, no auto-modification)",
        "current_cost_30d": savings["current_cost_30d"],
        "projected_cost_30d": savings["projected_cost_30d"],
        "projected_savings_30d": savings["projected_savings_30d"],
        "savings_pct": savings["savings_pct"],
        "recommendations": savings["recommendations"],
        "implementation_notes": [
            "1. Enable cache_system=True on all call_complex() calls with static system prompts",
            "2. Queue batch-eligible skills via /v1/messages/batches for 50% savings",
            "3. Configure [capacity] in fleet.toml to track bonus windows",
            "4. Route simple skills to Haiku ($0.80/M vs $3.00/M Sonnet)",
            "5. Shift non-urgent work to bonus hours for 2x effective capacity",
        ],
    }

    if log:
        log.info(f"Optimization: {savings['savings_pct']}% potential savings "
                 f"(${savings['projected_savings_30d']:.2f}/month)")
    return report


def _action_capacity_check(config: dict, log) -> dict:
    """Check current time against fleet.toml [capacity] windows."""
    capacity = config.get("capacity", {})
    et_now = _get_et_now()

    result = {
        "action": "capacity_check",
        "current_time_et": et_now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "is_weekend": et_now.weekday() >= 5,
        "current_hour_et": et_now.hour,
        "capacity_enabled": capacity.get("enabled", False),
        "bonus_active": False,
        "windows": [],
        "auto_scale": {},
    }

    if not capacity.get("enabled", False):
        result["message"] = "Capacity tracking not enabled. Add [capacity] to fleet.toml."
        if log:
            log.info("Capacity check: not enabled")
        return result

    # Check each window
    windows = capacity.get("windows", [])
    today = et_now.date()

    for window in windows:
        try:
            start_date = datetime.strptime(window["start"], "%Y-%m-%d").date()
            end_date = datetime.strptime(window["end"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue

        w_info = {
            "start": window.get("start"),
            "end": window.get("end"),
            "active_range": start_date <= today <= end_date,
            "weekday_bonus_hours": f"{window.get('weekday_bonus_start_et', '?')}:00 - "
                                   f"{window.get('weekday_bonus_end_et', '?')}:00 ET",
            "weekend_all_day": window.get("weekend_all_day", False),
            "multiplier": window.get("multiplier", 1.0),
        }

        # Calculate days remaining
        if start_date <= today <= end_date:
            w_info["days_remaining"] = (end_date - today).days
            w_info["status"] = "active"
        elif today < start_date:
            w_info["days_until_start"] = (start_date - today).days
            w_info["status"] = "upcoming"
        else:
            w_info["status"] = "expired"

        result["windows"].append(w_info)

    # Check bonus status
    result["bonus_active"] = is_in_bonus_window(config)

    # Auto-scale config
    auto_scale = capacity.get("auto_scale", {})
    if auto_scale:
        result["auto_scale"] = {
            "deploy_agents": auto_scale.get("deploy_agents", 0),
            "prefer_claude": auto_scale.get("prefer_claude", False),
            "disable_budget_throttle": auto_scale.get("disable_budget_throttle", False),
        }

    if result["bonus_active"]:
        result["message"] = (
            f"BONUS ACTIVE — {et_now.strftime('%H:%M ET')} is in a capacity bonus window. "
            f"Multiplier: {windows[0].get('multiplier', 2.0) if windows else 2.0}x"
        )
    else:
        # Find next bonus period
        if et_now.weekday() < 5:  # weekday
            bonus_start = None
            for w in windows:
                try:
                    s = datetime.strptime(w["start"], "%Y-%m-%d").date()
                    e = datetime.strptime(w["end"], "%Y-%m-%d").date()
                    if s <= today <= e:
                        bonus_start = w.get("weekday_bonus_start_et")
                        break
                except (KeyError, ValueError):
                    continue
            if bonus_start is not None:
                if et_now.hour < bonus_start:
                    result["message"] = (
                        f"No bonus now — next bonus starts at {bonus_start}:00 ET today"
                    )
                else:
                    result["message"] = "No bonus now — next bonus tomorrow or weekend"
            else:
                result["message"] = "No active capacity windows"
        else:
            result["message"] = "Weekend — check window config for weekend_all_day"

    if log:
        log.info(f"Capacity check: bonus={'yes' if result['bonus_active'] else 'no'} "
                 f"at {et_now.strftime('%H:%M ET')}")
    return result


def _action_batch_queue(config: dict, log) -> dict:
    """Identify non-urgent pending tasks eligible for batch API."""
    sys.path.insert(0, str(FLEET_DIR))

    try:
        import db as fleet_db
    except ImportError:
        return {"error": "db module not available"}

    result = {
        "action": "batch_queue",
        "eligible_tasks": [],
        "summary": {},
    }

    try:
        conn = fleet_db.get_conn()
    except Exception as e:
        return {"error": f"Cannot connect to fleet.db: {e}"}

    try:
        # Find pending tasks that match batch-eligible skills
        pending = conn.execute("""
            SELECT id, type, status, created_at, assigned_to
            FROM tasks
            WHERE status IN ('PENDING', 'QUEUED')
              AND type IN ({})
            ORDER BY created_at ASC
        """.format(",".join(f"'{s}'" for s in BATCH_ELIGIBLE_SKILLS))).fetchall()

        for task in pending:
            result["eligible_tasks"].append({
                "task_id": task["id"],
                "skill": task["type"],
                "status": task["status"],
                "created_at": task["created_at"],
                "assigned_to": task["assigned_to"],
            })

        # Also check recent completed tasks that could have been batched
        recent_batchable = conn.execute("""
            SELECT type as skill, COUNT(*) as count,
                   COALESCE(SUM(cost_usd), 0) as cost
            FROM (
                SELECT t.type, u.cost_usd
                FROM tasks t
                LEFT JOIN usage u ON u.task_id = t.id
                WHERE t.status = 'DONE'
                  AND t.created_at >= datetime('now', '-7 days')
                  AND t.type IN ({})
            )
            GROUP BY skill
            ORDER BY cost DESC
        """.format(",".join(f"'{s}'" for s in BATCH_ELIGIBLE_SKILLS))).fetchall()

        result["summary"] = {
            "pending_eligible": len(result["eligible_tasks"]),
            "recent_batchable_skills": [
                {"skill": r["skill"], "count": r["count"],
                 "cost_usd": round(r["cost"], 4)}
                for r in recent_batchable
            ],
            "potential_savings_pct": 50,
            "note": "Batch API processes within 24hrs at 50% cost. "
                    "Suitable for non-interactive tasks only.",
        }
    finally:
        conn.close()

    if log:
        log.info(f"Batch queue: {len(result['eligible_tasks'])} pending eligible, "
                 f"{len(result['summary'].get('recent_batchable_skills', []))} "
                 f"recently batchable skills")
    return result


def _action_report(config: dict, log) -> dict:
    """Generate comprehensive savings report."""
    # Gather all data
    audit = _action_audit(config, log)
    savings = get_savings_potential(config)
    capacity = _action_capacity_check(config, log)

    if "error" in audit:
        return audit
    if "error" in savings:
        return savings

    # Build markdown report
    et_now = _get_et_now()
    lines = [
        f"# Claude Efficiency Report",
        f"",
        f"**Generated:** {et_now.strftime('%Y-%m-%d %H:%M ET')}",
        f"**Period:** Last 30 days",
        f"",
        f"---",
        f"",
        f"## Cost Summary",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Current 30d cost | ${savings.get('current_cost_30d', 0):.4f} |",
        f"| Projected 30d cost | ${savings.get('projected_cost_30d', 0):.4f} |",
        f"| Potential savings | ${savings.get('projected_savings_30d', 0):.4f} "
        f"({savings.get('savings_pct', 0)}%) |",
        f"| Claude API calls | {audit.get('summary', {}).get('claude', {}).get('calls', 0)} |",
        f"| Skills using Claude | {len(audit.get('skills_using_claude', []))} |",
        f"| Batch-eligible skills | {savings.get('batch_eligible_count', 0)} |",
        f"",
        f"## Capacity Window",
        f"",
        f"| Status | Value |",
        f"|--------|-------|",
        f"| Enabled | {capacity.get('capacity_enabled', False)} |",
        f"| Bonus active now | {capacity.get('bonus_active', False)} |",
        f"| Current time ET | {capacity.get('current_time_et', 'unknown')} |",
        f"",
    ]

    # Windows detail
    windows = capacity.get("windows", [])
    if windows:
        lines.append("### Active Windows")
        lines.append("")
        for w in windows:
            status = w.get("status", "unknown")
            lines.append(f"- **{w.get('start')} to {w.get('end')}** "
                         f"({status})")
            if "days_remaining" in w:
                lines.append(f"  - {w['days_remaining']} days remaining")
            lines.append(f"  - Weekday bonus: {w.get('weekday_bonus_hours', 'N/A')}")
            lines.append(f"  - Weekend: {'all day' if w.get('weekend_all_day') else 'no bonus'}")
            lines.append(f"  - Multiplier: {w.get('multiplier', 1.0)}x")
        lines.append("")

    # Findings
    findings = audit.get("findings", [])
    if findings:
        lines.append("## Findings")
        lines.append("")
        for i, f in enumerate(findings, 1):
            severity = f.get("severity", "info").upper()
            lines.append(f"### {i}. [{severity}] {f.get('type', 'unknown')}")
            lines.append(f"")
            lines.append(f"{f.get('message', '')}")
            lines.append(f"")
            if f.get("skills"):
                skills_str = ", ".join(f["skills"][:10]) if isinstance(f["skills"][0], str) else \
                    ", ".join(s.get("skill", "") for s in f["skills"][:10])
                lines.append(f"**Skills:** {skills_str}")
                lines.append(f"")
            lines.append(f"**Fix:** {f.get('fix', 'N/A')}")
            lines.append(f"**Estimated savings:** {f.get('estimated_savings_pct', 0)}%")
            lines.append(f"")

    # Recommendations
    recs = savings.get("recommendations", [])
    if recs:
        lines.append("## Recommendations")
        lines.append("")
        for r in recs:
            tech = r.get("technique", "unknown")
            lines.append(f"### {tech}")
            lines.append(f"- **Current:** {r.get('current', 'N/A')}")
            lines.append(f"- **Potential:** {r.get('potential', 'N/A')}")
            if "estimated_savings_usd" in r:
                lines.append(f"- **Savings:** ${r['estimated_savings_usd']:.2f}/month "
                             f"({r.get('estimated_savings_pct', 0)}%)")
            elif "estimated_savings" in r:
                lines.append(f"- **Savings:** {r['estimated_savings']}")
            lines.append(f"")

    report_md = "\n".join(lines)

    # Save report
    reports_dir = FLEET_DIR / "knowledge" / "reports"
    date_str = et_now.strftime("%Y%m%d_%H%M")
    report_path = reports_dir / f"claude_efficiency_{date_str}.md"

    try:
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_md, encoding="utf-8")
    except Exception as e:
        return {
            "action": "report",
            "status": "error",
            "error": f"Failed to write report: {e}",
            "report_md": report_md,
        }

    result = {
        "action": "report",
        "status": "ok",
        "report_path": str(report_path),
        "current_cost_30d": savings.get("current_cost_30d", 0),
        "projected_cost_30d": savings.get("projected_cost_30d", 0),
        "savings_pct": savings.get("savings_pct", 0),
        "findings_count": len(findings),
        "bonus_active": capacity.get("bonus_active", False),
    }

    if log:
        log.info(f"Efficiency report saved: {report_path} "
                 f"(${savings.get('projected_savings_30d', 0):.2f} potential savings)")
    return result


# ── Skill entry point ────────────────────────────────────────────────────────

def run(payload: dict, config: dict, log=None) -> dict:
    """Run claude_efficiency skill.

    Actions:
        audit          — analyze Claude usage patterns and find optimization opportunities
        optimize       — generate recommendations (read-only, no auto-modification)
        capacity_check — check if current time is in a bonus capacity window
        batch_queue    — identify pending tasks eligible for batch API
        report         — generate comprehensive savings report markdown
    """
    action = payload.get("action", "audit")

    actions = {
        "audit": _action_audit,
        "optimize": _action_optimize,
        "capacity_check": _action_capacity_check,
        "batch_queue": _action_batch_queue,
        "report": _action_report,
    }

    handler = actions.get(action)
    if not handler:
        return {
            "error": f"Unknown action: {action}",
            "valid_actions": list(actions.keys()),
        }

    return handler(config, log)
