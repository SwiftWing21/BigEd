"""ML-based predictive scaler — replaces heuristic queue-growth prediction.

Collects historical scaling data (queue depth, agent count, task rate, actions
taken), trains a lightweight model to predict optimal agent counts, and exposes
a predict_optimal_agents() API for the supervisor scaling loop.

Falls back to the heuristic _predict_queue_growth() when insufficient training
data or when the model file is absent.

pickle is used intentionally for sklearn model serialization — the model file
is local-only, written and read exclusively by this module.

v0.200.00b
"""

import json
import logging
import os
import pickle  # nosec — local-only model file, not user-supplied
import time
from pathlib import Path

_log = logging.getLogger("predictive_scaler")

FLEET_DIR = Path(__file__).parent
MODEL_PATH = FLEET_DIR / "data" / "scaler_model.pkl"
HISTORY_TABLE = "scaling_history"

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scaling_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL DEFAULT (datetime('now')),
    queue_depth     INTEGER NOT NULL,
    agent_count     INTEGER NOT NULL,
    task_rate_5m    REAL NOT NULL DEFAULT 0.0,
    task_rate_15m   REAL NOT NULL DEFAULT 0.0,
    action          TEXT NOT NULL DEFAULT 'none',
    target_agents   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sh_ts ON scaling_history(ts);
"""


def _ensure_table():
    """Create the scaling_history table if it doesn't exist."""
    import db
    def _do():
        with db.get_conn() as conn:
            conn.executescript(_SCHEMA_SQL)
    db._retry_write(_do)


# ── Data Collection ───────────────────────────────────────────────────────────

def record_scaling_event(queue_depth: int, agent_count: int,
                         task_rate_5m: float, task_rate_15m: float,
                         action: str, target_agents: int):
    """Record a scaling observation to the history table.

    Called by the supervisor after each scaling decision (including 'none').

    Args:
        queue_depth: current pending task count
        agent_count: current running worker count
        task_rate_5m: tasks created in the last 5 minutes
        task_rate_15m: tasks created in the last 15 minutes
        action: 'scale_up', 'scale_down', or 'none'
        target_agents: agent count after the scaling action
    """
    _ensure_table()
    import db
    def _do():
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO scaling_history "
                "(queue_depth, agent_count, task_rate_5m, task_rate_15m, action, target_agents) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (queue_depth, agent_count, task_rate_5m, task_rate_15m, action, target_agents),
            )
    db._retry_write(_do)


def collect_scaling_data(limit: int = 5000) -> list[dict]:
    """Retrieve historical scaling data for model training.

    Returns list of dicts with keys:
        ts, queue_depth, agent_count, task_rate_5m, task_rate_15m,
        action, target_agents
    """
    _ensure_table()
    import db
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT ts, queue_depth, agent_count, task_rate_5m, task_rate_15m, "
            "action, target_agents FROM scaling_history ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def _get_task_rate(minutes: int) -> float:
    """Count tasks created in the last N minutes."""
    import db
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM tasks WHERE created_at >= datetime('now', '-{minutes} minutes')"
            ).fetchone()
            return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


# ── Model Training ────────────────────────────────────────────────────────────

def _get_min_history() -> int:
    """Read minimum history threshold from config."""
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("scaling", {}).get("min_scaling_history", 100)
    except Exception:
        return 100


def train_scaler_model() -> dict:
    """Train a lightweight regression model to predict optimal agent count.

    Uses scikit-learn LinearRegression (available via pip). Falls back to a
    simple weighted-average model stored as coefficients if sklearn is absent.

    Returns:
        {"ok": bool, "samples": int, "method": str, "error": str|None}
    """
    data = collect_scaling_data()
    min_history = _get_min_history()

    if len(data) < min_history:
        return {
            "ok": False,
            "samples": len(data),
            "method": "none",
            "error": f"Need {min_history} samples, have {len(data)}",
        }

    # Features: queue_depth, agent_count, task_rate_5m, task_rate_15m
    # Target: target_agents (what the supervisor decided)
    X = []
    y = []
    for row in data:
        X.append([
            row["queue_depth"],
            row["agent_count"],
            row["task_rate_5m"],
            row["task_rate_15m"],
        ])
        y.append(row["target_agents"])

    model_data = {"trained_at": time.time(), "samples": len(data)}

    try:
        from sklearn.linear_model import LinearRegression
        import numpy as np

        X_arr = np.array(X, dtype=float)
        y_arr = np.array(y, dtype=float)

        model = LinearRegression()
        model.fit(X_arr, y_arr)

        model_data["method"] = "sklearn_linear"
        model_data["model"] = model
        model_data["coef"] = model.coef_.tolist()
        model_data["intercept"] = float(model.intercept_)

        # R^2 score for diagnostics
        score = model.score(X_arr, y_arr)
        model_data["r2_score"] = round(score, 4)
        _log.info("Trained sklearn model: R2=%.4f, samples=%d", score, len(data))

    except ImportError:
        # Fallback: simple weighted average coefficients
        _log.info("sklearn not available — using weighted-average fallback")
        n = len(X)
        if n == 0:
            return {"ok": False, "samples": 0, "method": "none", "error": "No data"}

        # Compute mean feature values and mean target
        mean_x = [sum(row[j] for row in X) / n for j in range(4)]
        mean_y = sum(y) / n

        # Simple: predict mean_y + weighted deviation from mean features
        # Weight: queue_depth most important, then rates, agent_count least
        weights = [0.5, 0.1, 0.3, 0.1]
        # Compute variance-normalized coefficients
        coefs = []
        for j in range(4):
            var = sum((row[j] - mean_x[j]) ** 2 for row in X) / max(n, 1)
            if var > 0:
                cov = sum((X[i][j] - mean_x[j]) * (y[i] - mean_y) for i in range(n)) / n
                coefs.append(weights[j] * (cov / var))
            else:
                coefs.append(0.0)

        intercept = mean_y - sum(coefs[j] * mean_x[j] for j in range(4))

        model_data["method"] = "weighted_average"
        model_data["model"] = None
        model_data["coef"] = coefs
        model_data["intercept"] = intercept
        model_data["r2_score"] = None

    # Save model — pickle is used intentionally for sklearn model objects.
    # The file is local-only, never user-supplied.
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(model_data, f, protocol=pickle.HIGHEST_PROTOCOL)
        _log.info("Saved scaler model to %s", MODEL_PATH)
    except Exception:
        _log.warning("Failed to save scaler model", exc_info=True)
        return {"ok": False, "samples": len(data), "method": model_data["method"],
                "error": "Failed to save model file"}

    return {
        "ok": True,
        "samples": len(data),
        "method": model_data["method"],
        "r2_score": model_data.get("r2_score"),
        "error": None,
    }


def _load_model() -> dict | None:
    """Load the trained model from disk. Returns None if absent or stale.

    pickle is used intentionally — the model file is written exclusively by
    train_scaler_model() in this module, never from external/untrusted sources.
    """
    if not MODEL_PATH.exists():
        return None
    try:
        with open(MODEL_PATH, "rb") as f:
            data = pickle.load(f)  # nosec — local-only model file

        # Check staleness
        try:
            from config import load_config
            cfg = load_config()
            retrain_hours = cfg.get("scaling", {}).get("predictor_retrain_hours", 12)
        except Exception:
            retrain_hours = 12

        age_hours = (time.time() - data.get("trained_at", 0)) / 3600
        if age_hours > retrain_hours * 3:
            _log.warning("Scaler model is %.1f hours old (limit %d) — consider retraining",
                         age_hours, retrain_hours)
        return data
    except Exception:
        _log.warning("Failed to load scaler model", exc_info=True)
        return None


# ── Prediction ────────────────────────────────────────────────────────────────

def predict_optimal_agents(current_queue: int, current_agents: int,
                           task_rate_5m: float = None,
                           task_rate_15m: float = None) -> int:
    """Predict the optimal number of agents given current metrics.

    Falls back to a simple heuristic if no trained model is available.

    Args:
        current_queue: pending task count
        current_agents: running worker count
        task_rate_5m: tasks in last 5 min (auto-fetched if None)
        task_rate_15m: tasks in last 15 min (auto-fetched if None)

    Returns:
        Recommended agent count (clamped to [1, 16])
    """
    if task_rate_5m is None:
        task_rate_5m = _get_task_rate(5)
    if task_rate_15m is None:
        task_rate_15m = _get_task_rate(15)

    model_data = _load_model()

    if model_data is None:
        # Heuristic fallback: base + 1 per 2 pending tasks
        predicted = max(4, current_agents + (current_queue // 2))
        return max(1, min(16, predicted))

    features = [current_queue, current_agents, task_rate_5m, task_rate_15m]

    if model_data.get("method") == "sklearn_linear" and model_data.get("model"):
        try:
            import numpy as np
            X = np.array([features], dtype=float)
            predicted = model_data["model"].predict(X)[0]
        except Exception:
            _log.warning("sklearn predict failed — using coefficients", exc_info=True)
            predicted = _predict_from_coefs(features, model_data)
    else:
        predicted = _predict_from_coefs(features, model_data)

    result = max(1, min(16, round(predicted)))
    return result


def _predict_from_coefs(features: list[float], model_data: dict) -> float:
    """Manual linear prediction from stored coefficients."""
    coef = model_data.get("coef", [0, 0, 0, 0])
    intercept = model_data.get("intercept", 4.0)
    return intercept + sum(c * f for c, f in zip(coef, features))


def should_scale(direction: str, current_queue: int, current_agents: int) -> bool:
    """Recommend whether to scale up or down based on prediction.

    Args:
        direction: 'up' or 'down'
        current_queue: pending task count
        current_agents: running worker count

    Returns:
        True if scaling in the given direction is recommended
    """
    try:
        from config import load_config
        cfg = load_config()
        if not cfg.get("scaling", {}).get("ml_predictor_enabled", True):
            return False  # ML predictor disabled — let heuristic handle it
    except Exception:
        pass

    optimal = predict_optimal_agents(current_queue, current_agents)

    if direction == "up":
        return optimal > current_agents
    elif direction == "down":
        return optimal < current_agents
    return False


def get_prediction_summary(current_queue: int = None, current_agents: int = None) -> dict:
    """Return current prediction state for the dashboard.

    Returns:
        {
            "model_available": bool,
            "method": str,
            "r2_score": float|None,
            "model_age_hours": float,
            "current_queue": int,
            "current_agents": int,
            "predicted_optimal": int,
            "recommendation": str,
            "history_count": int,
        }
    """
    import db

    if current_queue is None:
        try:
            with db.get_conn() as conn:
                row = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='PENDING'").fetchone()
                current_queue = row[0] if row else 0
        except Exception:
            current_queue = 0

    if current_agents is None:
        try:
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM agents WHERE status IN ('IDLE', 'BUSY', 'WORKING')"
                ).fetchone()
                current_agents = row[0] if row else 4
        except Exception:
            current_agents = 4

    model_data = _load_model()
    history_count = 0
    try:
        _ensure_table()
        with db.get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM scaling_history").fetchone()
            history_count = row[0] if row else 0
    except Exception:
        pass

    optimal = predict_optimal_agents(current_queue, current_agents)

    if optimal > current_agents:
        recommendation = "scale_up"
    elif optimal < current_agents:
        recommendation = "scale_down"
    else:
        recommendation = "hold"

    return {
        "model_available": model_data is not None,
        "method": model_data.get("method", "heuristic") if model_data else "heuristic",
        "r2_score": model_data.get("r2_score") if model_data else None,
        "model_age_hours": round((time.time() - model_data.get("trained_at", 0)) / 3600, 1) if model_data else None,
        "current_queue": current_queue,
        "current_agents": current_agents,
        "predicted_optimal": optimal,
        "recommendation": recommendation,
        "history_count": history_count,
    }
