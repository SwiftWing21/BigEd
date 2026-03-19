"""HITL model recommendation — analyze fleet performance and recommend upgrades via operator approval."""
import json, os, tempfile, urllib.request
from pathlib import Path

SKILL_NAME = "model_recommend"
DESCRIPTION = "Analyze model performance and recommend upgrades via HITL approval"
REQUIRES_NETWORK = False
FLEET_DIR = Path(__file__).parent.parent

# Known model quality tiers (higher = more intelligent)
MODEL_QUALITY = {
    "qwen3:8b":   {"intelligence": 0.80, "speed_tier": "medium"},
    "qwen3:4b":   {"intelligence": 0.60, "speed_tier": "fast"},
    "qwen3:1.7b": {"intelligence": 0.40, "speed_tier": "fast"},
    "qwen3:0.6b": {"intelligence": 0.20, "speed_tier": "fastest"},
    "llama3.1:8b": {"intelligence": 0.75, "speed_tier": "medium"},
    "gemma2:9b":   {"intelligence": 0.70, "speed_tier": "medium"},
    "mistral:7b":  {"intelligence": 0.70, "speed_tier": "medium"},
    "phi3:mini":   {"intelligence": 0.50, "speed_tier": "fast"},
}
TIER_LABELS = {"local": "default worker", "default": "default tier", "mid": "mid tier",
               "low": "low tier", "critical": "critical tier", "conductor_model": "conductor"}
SPEED_RANK = {"fastest": 3, "fast": 2, "medium": 1}  # higher = faster


def run(payload: dict, config: dict) -> str:
    action = payload.get("action", "analyze")
    if action == "analyze":
        return _analyze_and_recommend(config)
    elif action == "apply":
        return _apply_recommendation(payload, config)
    return json.dumps({"error": f"Unknown action: {action}"})


def _get_installed(config):
    host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return []


def _get_current(config):
    models = config.get("models", {})
    current = {}
    for k in ("local", "conductor_model"):
        if models.get(k):
            current[k] = models[k]
    for k, v in models.get("tiers", {}).items():
        current[k] = v
    return current


def _find_upgrade(current_model, tier_key, installed):
    cur = MODEL_QUALITY.get(current_model)
    if not cur:
        return None
    prefer_speed = tier_key in ("conductor_model", "critical", "low")
    best, best_score = None, cur["intelligence"]
    for name, q in MODEL_QUALITY.items():
        if name == current_model or name not in installed:
            continue
        if prefer_speed:
            cs, ns = SPEED_RANK.get(cur["speed_tier"], 0), SPEED_RANK.get(q["speed_tier"], 0)
            if ns > cs and q["intelligence"] >= cur["intelligence"] * 0.8:
                if not best or ns > SPEED_RANK.get(MODEL_QUALITY[best]["speed_tier"], 0):
                    best = name
        elif q["intelligence"] > best_score + 0.05:
            best, best_score = name, q["intelligence"]
    if not best:
        return None
    nq, delta = MODEL_QUALITY[best], MODEL_QUALITY[best]["intelligence"] - cur["intelligence"]
    return {"model": best, "reason": f"intelligence {cur['intelligence']:.0%} -> "
            f"{nq['intelligence']:.0%} (+{delta:.0%}), speed: {nq['speed_tier']}"}


def _get_usage_stats():
    try:
        import sys; sys.path.insert(0, str(FLEET_DIR)); import db
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT model, COUNT(*) as n FROM usage "
                "WHERE created_at > datetime('now','-24 hours') GROUP BY model").fetchall()
        return {r["model"]: r["n"] for r in rows}
    except Exception:
        return {}


def _analyze_and_recommend(config):
    import sys; sys.path.insert(0, str(FLEET_DIR)); import db
    installed = _get_installed(config)
    if not installed:
        return json.dumps({"status": "skip", "reason": "Cannot reach Ollama"})

    current = _get_current(config)
    _get_usage_stats()  # collected for future scoring; not blocking recommendations yet
    recs = []
    for tier_key, cur_model in current.items():
        upgrade = _find_upgrade(cur_model, tier_key, installed)
        if upgrade:
            recs.append({"tier": tier_key, "label": TIER_LABELS.get(tier_key, tier_key),
                         "current": cur_model, "recommended": upgrade["model"],
                         "reason": upgrade["reason"]})

    if not recs:
        return json.dumps({"status": "ok", "message": "All models optimal", "checked": len(current)})
    hitl_ids = []
    for rec in recs:
        tid = db.post_task("model_recommend", json.dumps({
            "action": "apply", "tier": rec["tier"],
            "model": rec["recommended"], "previous": rec["current"]}), priority=3)
        db.request_human_input(tid, "model_recommend",
            f"Model recommendation: Replace {rec['current']} with {rec['recommended']} "
            f"for {rec['label']}.\nExpected improvement: {rec['reason']}\n"
            f"Reply 'approve' to apply, 'reject' to dismiss.")
        hitl_ids.append(tid)
    return json.dumps({"status": "recommendations_pending", "count": len(recs),
                        "recommendations": recs, "hitl_task_ids": hitl_ids})


def _apply_recommendation(payload, config):
    tier, model = payload.get("tier"), payload.get("model")
    previous = payload.get("previous")
    if not tier or not model:
        return json.dumps({"error": "Missing 'tier' or 'model' in payload"})

    resp = payload.get("_human_response", "").strip().lower()
    if resp == "reject":
        return json.dumps({"status": "rejected", "tier": tier, "model": model})
    if resp != "approve":
        return json.dumps({"status": "waiting", "message": "Awaiting operator approval"})

    try:
        import tomlkit
        fleet_toml = FLEET_DIR / "fleet.toml"
        doc = tomlkit.parse(fleet_toml.read_text(encoding="utf-8"))
        doc.setdefault("models", {})
        if tier in ("local", "conductor_model", "vision_model", "complex"):
            doc["models"][tier] = model
        else:
            doc["models"].setdefault("tiers", {})
            doc["models"]["tiers"][tier] = model
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(FLEET_DIR), suffix=".toml")
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(tomlkit.dumps(doc))
        os.replace(tmp_path, str(fleet_toml))
        return json.dumps({"status": "applied", "tier": tier, "model": model, "previous": previous})
    except Exception as e:
        return json.dumps({"error": str(e), "tier": tier, "model": model})
