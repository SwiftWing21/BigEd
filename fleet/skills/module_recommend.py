"""Analyze fleet activity and suggest useful modules."""
SKILL_NAME = "module_recommend"
DESCRIPTION = "Analyze fleet task patterns and recommend useful modules from the hub"
REQUIRES_NETWORK = True

_MODULE_RELEVANCE = {
    "analytics_pro": ["data_analysis", "autoresearch_trial", "evaluate"],
    "webhooks": ["api_call", "web_search", "monitor"],
    "crm": ["lead_research", "outreach", "account_review"],
    "onboarding": ["onboarding", "setup", "walkthrough"],
    "customers": ["account_review", "client_onboarding"],
}


def run(task: dict, context: dict) -> dict:
    import db
    import json
    import sys
    from pathlib import Path

    payload = task.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}

    lookback_days = payload.get("lookback_days", 7)
    max_suggestions = payload.get("max_suggestions", 3)

    with db.get_conn() as conn:
        task_types = conn.execute(
            "SELECT type, COUNT(*) as cnt FROM tasks "
            "WHERE created_at >= datetime('now', ? || ' days') "
            "AND classification != 'synthetic_prefix' AND type IS NOT NULL "
            "GROUP BY type ORDER BY cnt DESC",
            (str(-lookback_days),)
        ).fetchall()

    type_counts = {r["type"]: r["cnt"] for r in task_types}

    hub_dir = Path(__file__).parent.parent.parent / "BigEd" / "launcher" / "modules"
    sys.path.insert(0, str(hub_dir))
    try:
        from hub import ModuleHub
    finally:
        if str(hub_dir) in sys.path:
            sys.path.remove(str(hub_dir))

    try:
        from config import load_config
        cfg = load_config()
    except Exception:
        cfg = {}

    hub = ModuleHub(cfg)
    installed_names = {m["name"] for m in hub.list_installed()}

    suggestions = []
    for mod_name, relevant_tasks in _MODULE_RELEVANCE.items():
        if mod_name in installed_names:
            continue
        score = 0.0
        matched_tasks = []
        for task_type in relevant_tasks:
            if task_type in type_counts:
                score += min(1.0, type_counts[task_type] / 50.0)
                matched_tasks.append(f"{task_type} ({type_counts[task_type]})")

        if score > 0.5:
            reason = f"Your fleet ran {', '.join(matched_tasks)} tasks in the last {lookback_days} days"
            suggestions.append({
                "module_name": mod_name,
                "reason": reason,
                "relevance_score": round(score, 2),
            })

    suggestions.sort(key=lambda x: x["relevance_score"], reverse=True)
    suggestions = suggestions[:max_suggestions]

    if suggestions:
        def _write():
            with db.get_conn() as conn:
                for s in suggestions:
                    conn.execute(
                        "INSERT OR REPLACE INTO module_suggestions "
                        "(module_name, reason, relevance_score, dismissed) "
                        "VALUES (?, ?, ?, 0)",
                        (s["module_name"], s["reason"], s["relevance_score"])
                    )
        db._retry_write(_write)

    return {"status": "ok", "suggestions": suggestions, "task_types_analyzed": len(type_counts)}
