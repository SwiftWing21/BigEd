"""ML-based task routing — trains on task history to pick optimal agents.

Replaces the simple IQ-average lookup in providers.get_optimal_agent_for_skill()
with a trained sklearn model. Falls back to IQ-based routing when no model exists
or insufficient training data.

Training pipeline:
    collect_training_data()  -> DataFrame of task history features
    train_routing_model()    -> fit + save model to fleet/data/routing_model.pkl
    predict_best_agent()     -> rank agents for a skill using the trained model
    retrain_if_stale()       -> retrain when model is old or enough new tasks arrived

Integration:
    - supervisor calls retrain_if_stale() on idle cycles
    - dashboard exposes /api/routing/model-status and /api/routing/retrain
    - providers.get_optimal_agent_for_skill() delegates here when model exists

Security note:
    Model serialization uses pickle, which is standard for sklearn. The .pkl files
    are only written and read locally by this module's own training pipeline — never
    loaded from untrusted or external sources.
"""
import json
import logging
import os
import pickle
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("ml_router")

# ── Paths ────────────────────────────────────────────────────────────────────
_DATA_DIR = Path(__file__).parent / "data"
_MODEL_PATH = _DATA_DIR / "routing_model.pkl"
_META_PATH = _DATA_DIR / "routing_meta.json"

# ── Defaults (overridden by fleet.toml [routing]) ───────────────────────────
_DEFAULT_RETRAIN_HOURS = 24
_DEFAULT_MIN_SAMPLES = 50
_DEFAULT_ML_ENABLED = True
_DEFAULT_FALLBACK_TO_IQ = True


# ── Config helper ────────────────────────────────────────────────────────────

def _get_routing_config() -> dict:
    """Load [routing] section from fleet.toml with defaults."""
    try:
        from config import load_config
        cfg = load_config()
        routing = cfg.get("routing", {})
    except Exception:
        routing = {}
    return {
        "ml_enabled": routing.get("ml_enabled", _DEFAULT_ML_ENABLED),
        "retrain_interval_hours": routing.get("retrain_interval_hours", _DEFAULT_RETRAIN_HOURS),
        "min_training_samples": routing.get("min_training_samples", _DEFAULT_MIN_SAMPLES),
        "fallback_to_iq": routing.get("fallback_to_iq", _DEFAULT_FALLBACK_TO_IQ),
    }


# ── Training data collection ────────────────────────────────────────────────

def collect_training_data():
    """Query task history and build a training DataFrame.

    Features per row:
        skill_type, assigned_to, priority, hour_of_day, day_of_week,
        agent_recent_success_rate, intelligence_score,
        success (binary target), duration_secs
    """
    import db as _db

    with _db.get_conn() as conn:
        rows = conn.execute("""
            SELECT t.id, t.type as skill_type, t.assigned_to, t.priority,
                   t.created_at, t.status, t.intelligence_score, t.result_json
            FROM tasks t
            WHERE t.status IN ('DONE', 'FAILED')
              AND t.assigned_to IS NOT NULL
              AND t.type IS NOT NULL
              AND t.created_at >= datetime('now', '-90 days')
            ORDER BY t.created_at
        """).fetchall()

        if not rows:
            return None

        # Pre-compute agent success rates (rolling 30-day window)
        agent_stats = {}
        for r in conn.execute("""
            SELECT assigned_to, status, COUNT(*) as cnt
            FROM tasks
            WHERE status IN ('DONE', 'FAILED')
              AND assigned_to IS NOT NULL
              AND created_at >= datetime('now', '-30 days')
            GROUP BY assigned_to, status
        """).fetchall():
            agent = r["assigned_to"]
            if agent not in agent_stats:
                agent_stats[agent] = {"done": 0, "total": 0}
            agent_stats[agent]["total"] += r["cnt"]
            if r["status"] == "DONE":
                agent_stats[agent]["done"] += r["cnt"]

    records = []
    for row in rows:
        row = dict(row)
        created = row["created_at"] or ""
        try:
            dt = datetime.fromisoformat(created)
            hour = dt.hour
            dow = dt.weekday()
        except (ValueError, TypeError):
            hour = 12
            dow = 0

        agent = row["assigned_to"]
        stats = agent_stats.get(agent, {"done": 0, "total": 1})
        success_rate = stats["done"] / max(stats["total"], 1)

        success = 1 if row["status"] == "DONE" else 0

        # Estimate duration from result_json if available
        duration = 0.0
        if row["result_json"]:
            try:
                rj = json.loads(row["result_json"])
                duration = float(rj.get("duration_secs", 0))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        records.append({
            "skill_type": row["skill_type"],
            "assigned_to": agent,
            "priority": row["priority"] or 5,
            "hour_of_day": hour,
            "day_of_week": dow,
            "agent_success_rate": round(success_rate, 3),
            "intelligence_score": row["intelligence_score"] or 0.0,
            "success": success,
            "duration_secs": duration,
        })

    try:
        import pandas as pd
        return pd.DataFrame(records)
    except ImportError:
        log.warning("pandas not installed — ML router training unavailable")
        return None


# ── Model training ───────────────────────────────────────────────────────────

def train_routing_model() -> dict:
    """Train a routing model on task history and save to disk.

    Returns a status dict with accuracy, feature importances, sample count,
    or an error message on failure.
    """
    try:
        import numpy as np
        import pandas as pd
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.model_selection import cross_val_score
        from sklearn.preprocessing import LabelEncoder
    except ImportError as e:
        msg = f"Missing ML dependency: {e}. Install scikit-learn, pandas, numpy."
        log.warning(msg)
        return {"error": msg}

    rcfg = _get_routing_config()

    df = collect_training_data()
    if df is None or len(df) < rcfg["min_training_samples"]:
        count = 0 if df is None else len(df)
        msg = (f"Insufficient training data: {count} samples "
               f"(need {rcfg['min_training_samples']})")
        log.info(msg)
        return {"error": msg, "sample_count": count}

    # Encode categoricals
    skill_encoder = LabelEncoder()
    agent_encoder = LabelEncoder()

    df["skill_encoded"] = skill_encoder.fit_transform(df["skill_type"])
    df["agent_encoded"] = agent_encoder.fit_transform(df["assigned_to"])

    feature_cols = [
        "skill_encoded", "agent_encoded", "priority",
        "hour_of_day", "day_of_week", "agent_success_rate",
        "intelligence_score",
    ]
    X = df[feature_cols].values
    y = df["success"].values

    # Train gradient boosting classifier
    model = GradientBoostingClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.1,
        min_samples_split=5,
        random_state=42,
    )
    model.fit(X, y)

    # Cross-validate for accuracy estimate
    try:
        cv_scores = cross_val_score(model, X, y, cv=min(5, max(2, len(df) // 20)),
                                    scoring="accuracy")
        accuracy = float(np.mean(cv_scores))
    except Exception:
        accuracy = 0.0

    # Feature importances
    importances = dict(zip(feature_cols, [round(float(v), 4) for v in model.feature_importances_]))

    # Save model + encoders + metadata
    # NOTE: pickle is used here for sklearn model serialization (standard practice).
    # These files are only written/read by this module — never from external sources.
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    bundle = {
        "model": model,
        "skill_encoder": skill_encoder,
        "agent_encoder": agent_encoder,
        "feature_cols": feature_cols,
    }
    with open(_MODEL_PATH, "wb") as f:
        pickle.dump(bundle, f)

    meta = {
        "trained_at": datetime.utcnow().isoformat(),
        "sample_count": len(df),
        "accuracy": round(accuracy, 4),
        "feature_importances": importances,
        "skills": list(skill_encoder.classes_),
        "agents": list(agent_encoder.classes_),
        "tasks_at_train": _count_completed_tasks(),
    }
    with open(_META_PATH, "w") as f:
        json.dump(meta, f, indent=2)

    log.info("ML routing model trained: %d samples, accuracy=%.3f", len(df), accuracy)
    return meta


def _count_completed_tasks() -> int:
    """Count completed tasks in the last 90 days."""
    try:
        import db as _db
        with _db.get_conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) FROM tasks
                WHERE status IN ('DONE', 'FAILED')
                  AND created_at >= datetime('now', '-90 days')
            """).fetchone()
            return row[0] if row else 0
    except Exception:
        return 0


# ── Prediction ───────────────────────────────────────────────────────────────

def _load_model():
    """Load the trained model bundle from disk. Returns None on failure.

    NOTE: Uses pickle.load on a locally-written file (routing_model.pkl).
    This file is only produced by train_routing_model() above — never from
    untrusted external sources.
    """
    if not _MODEL_PATH.exists():
        return None
    try:
        with open(_MODEL_PATH, "rb") as f:
            return pickle.load(f)  # noqa: S301 — local-only file, see docstring
    except Exception as e:
        log.warning("Failed to load routing model: %s", e)
        return None


def _load_meta() -> dict:
    """Load model metadata from disk. Returns empty dict on failure."""
    if not _META_PATH.exists():
        return {}
    try:
        with open(_META_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def predict_best_agent(skill_name: str, available_agents: list) -> str | None:
    """Use the trained model to pick the optimal agent for a skill.

    Scores each available agent on predicted success probability and returns
    the agent with the highest score. Returns None if no model or the skill
    is unknown to the model.

    Args:
        skill_name: the skill/task type to route
        available_agents: list of agent names currently idle/available
    """
    rcfg = _get_routing_config()
    if not rcfg["ml_enabled"]:
        return None

    bundle = _load_model()
    if bundle is None:
        return None

    try:
        import numpy as np
    except ImportError:
        return None

    model = bundle["model"]
    skill_enc = bundle["skill_encoder"]
    agent_enc = bundle["agent_encoder"]

    # Check if skill is known to the model
    if skill_name not in skill_enc.classes_:
        log.debug("Skill %s not in trained model — falling back", skill_name)
        return None

    skill_code = skill_enc.transform([skill_name])[0]

    # Get current hour/day for temporal features
    now = datetime.now()
    hour = now.hour
    dow = now.weekday()

    # Compute per-agent success rates from recent history
    agent_rates = _get_agent_success_rates(available_agents)

    best_agent = None
    best_prob = -1.0

    for agent in available_agents:
        # Skip agents unknown to the model — they have no training signal
        if agent not in agent_enc.classes_:
            continue

        agent_code = agent_enc.transform([agent])[0]
        rate = agent_rates.get(agent, 0.5)
        iq = _get_agent_avg_iq(agent, skill_name)

        features = np.array([[
            skill_code, agent_code, 5,  # default priority
            hour, dow, rate, iq,
        ]])

        try:
            proba = model.predict_proba(features)[0]
            # proba[1] = probability of success
            success_prob = float(proba[1]) if len(proba) > 1 else float(proba[0])
        except Exception:
            continue

        if success_prob > best_prob:
            best_prob = success_prob
            best_agent = agent

    if best_agent:
        log.debug("ML route: %s -> %s (p=%.3f)", skill_name, best_agent, best_prob)

    return best_agent


def _get_agent_success_rates(agents: list) -> dict:
    """Fetch recent success rates for a list of agents."""
    if not agents:
        return {}
    try:
        import db as _db
        with _db.get_conn() as conn:
            placeholders = ",".join("?" * len(agents))
            rows = conn.execute(f"""
                SELECT assigned_to,
                       SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as done,
                       COUNT(*) as total
                FROM tasks
                WHERE assigned_to IN ({placeholders})
                  AND status IN ('DONE', 'FAILED')
                  AND created_at >= datetime('now', '-30 days')
                GROUP BY assigned_to
            """, agents).fetchall()
            return {r["assigned_to"]: r["done"] / max(r["total"], 1) for r in rows}
    except Exception:
        return {}


def _get_agent_avg_iq(agent: str, skill: str) -> float:
    """Fetch average IQ score for an agent on a specific skill."""
    try:
        import db as _db
        with _db.get_conn() as conn:
            row = conn.execute("""
                SELECT AVG(intelligence_score) as avg_iq
                FROM tasks
                WHERE assigned_to = ? AND type = ?
                  AND intelligence_score IS NOT NULL
                  AND created_at >= datetime('now', '-30 days')
            """, (agent, skill)).fetchone()
            return float(row["avg_iq"]) if row and row["avg_iq"] is not None else 0.0
    except Exception:
        return 0.0


# ── Staleness check + auto-retrain ──────────────────────────────────────────

def retrain_if_stale() -> dict | None:
    """Retrain the model if it is older than the configured interval
    or if enough new tasks have arrived since last training.

    Returns training result dict if retrained, None if skipped.
    """
    rcfg = _get_routing_config()
    if not rcfg["ml_enabled"]:
        return None

    meta = _load_meta()

    # Check age
    stale = False
    if not meta or "trained_at" not in meta:
        stale = True
    else:
        try:
            trained = datetime.fromisoformat(meta["trained_at"])
            age_hours = (datetime.utcnow() - trained).total_seconds() / 3600
            if age_hours >= rcfg["retrain_interval_hours"]:
                stale = True
        except (ValueError, TypeError):
            stale = True

    # Check new task volume since last train
    if not stale and meta.get("tasks_at_train"):
        current_count = _count_completed_tasks()
        delta = current_count - meta["tasks_at_train"]
        if delta >= 100:
            stale = True
            log.info("ML router: %d new tasks since last train — retraining", delta)

    if not stale:
        return None

    log.info("ML router: model stale — retraining")
    return train_routing_model()


# ── Model status (for dashboard) ────────────────────────────────────────────

def get_model_status() -> dict:
    """Return current model status for the dashboard API."""
    meta = _load_meta()
    rcfg = _get_routing_config()

    if not meta:
        return {
            "status": "no_model",
            "ml_enabled": rcfg["ml_enabled"],
            "fallback_to_iq": rcfg["fallback_to_iq"],
            "min_training_samples": rcfg["min_training_samples"],
            "retrain_interval_hours": rcfg["retrain_interval_hours"],
            "message": "No trained model. Need at least "
                       f"{rcfg['min_training_samples']} completed tasks.",
        }

    # Calculate age
    age_hours = None
    try:
        trained = datetime.fromisoformat(meta["trained_at"])
        age_hours = round((datetime.utcnow() - trained).total_seconds() / 3600, 1)
    except (ValueError, TypeError, KeyError):
        pass

    return {
        "status": "active" if rcfg["ml_enabled"] else "disabled",
        "ml_enabled": rcfg["ml_enabled"],
        "fallback_to_iq": rcfg["fallback_to_iq"],
        "trained_at": meta.get("trained_at"),
        "age_hours": age_hours,
        "sample_count": meta.get("sample_count", 0),
        "accuracy": meta.get("accuracy", 0),
        "feature_importances": meta.get("feature_importances", {}),
        "known_skills": meta.get("skills", []),
        "known_agents": meta.get("agents", []),
        "retrain_interval_hours": rcfg["retrain_interval_hours"],
        "min_training_samples": rcfg["min_training_samples"],
    }
