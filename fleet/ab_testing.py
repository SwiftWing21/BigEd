"""A/B Testing Framework — experiment on skill variants with statistical rigor.

Allows registering a variant implementation for any skill, splitting traffic
50/50 (sticky per-agent), recording outcomes, and evaluating which variant
wins using success rate and a chi-squared test for statistical significance.

v0.200.00b: Initial implementation.
"""
import json
import logging
import math
import time
import uuid

log = logging.getLogger("ab_testing")

# ── Schema bootstrap ──────────────────────────────────────────────────────────

_schema_initialized = False


def _ensure_schema():
    """Create experiments + experiment_results tables if missing."""
    global _schema_initialized
    if _schema_initialized:
        return
    try:
        import db

        def _do():
            with db.get_conn() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS experiments (
                        id TEXT PRIMARY KEY,
                        skill_name TEXT NOT NULL,
                        variant_path TEXT NOT NULL,
                        status TEXT DEFAULT 'active',
                        created_at REAL NOT NULL,
                        results_json TEXT
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_experiments_skill
                        ON experiments(skill_name, status)
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS experiment_results (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        experiment_id TEXT NOT NULL,
                        variant TEXT NOT NULL,
                        agent TEXT,
                        success INTEGER NOT NULL DEFAULT 0,
                        score REAL,
                        created_at REAL NOT NULL,
                        FOREIGN KEY (experiment_id) REFERENCES experiments(id)
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_expr_results_exp
                        ON experiment_results(experiment_id, variant)
                """)

        db._retry_write(_do)
        _schema_initialized = True
    except Exception:
        log.warning("Failed to initialize A/B testing schema", exc_info=True)


# ── Sticky assignments (in-memory, per-process) ──────────────────────────────
# Maps (experiment_id, agent) -> "control" | "variant"
_assignments = {}


def create_experiment(skill_name, variant_path):
    """Register a new A/B experiment: original skill vs variant at variant_path.

    Args:
        skill_name: the skill being tested (e.g. "code_review")
        variant_path: Python module path for the variant (e.g. "skills.code_review_v2")

    Returns:
        experiment ID string, or None on failure.
    """
    _ensure_schema()
    try:
        import db

        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        now = time.time()

        def _do():
            with db.get_conn() as conn:
                conn.execute(
                    """INSERT INTO experiments (id, skill_name, variant_path, status, created_at)
                       VALUES (?, ?, ?, 'active', ?)""",
                    (exp_id, skill_name, variant_path, now),
                )

        db._retry_write(_do)
        log.info("Created experiment %s: %s vs %s", exp_id, skill_name, variant_path)
        return exp_id

    except Exception:
        log.warning("create_experiment failed", exc_info=True)
        return None


def get_active_experiment(skill_name):
    """Return the active experiment for a skill, or None if none exists."""
    _ensure_schema()
    try:
        import db

        with db.get_conn() as conn:
            row = conn.execute(
                """SELECT id, skill_name, variant_path, status, created_at
                   FROM experiments
                   WHERE skill_name = ? AND status = 'active'
                   ORDER BY created_at DESC LIMIT 1""",
                (skill_name,),
            ).fetchone()
            return dict(row) if row else None

    except Exception:
        log.warning("get_active_experiment failed for %s", skill_name, exc_info=True)
        return None


def get_assignment(experiment_id, agent_name):
    """Return 'control' or 'variant' for an agent in an experiment.

    Assignment is sticky: same agent always gets the same variant within
    an experiment. Uses a simple hash for deterministic 50/50 split.
    """
    key = (experiment_id, agent_name)
    if key in _assignments:
        return _assignments[key]

    # Deterministic hash-based split
    h = hash(f"{experiment_id}:{agent_name}") % 100
    assignment = "variant" if h < 50 else "control"
    _assignments[key] = assignment
    return assignment


def record_result(experiment_id, variant, success, score=None, agent=None):
    """Record the outcome of a single trial in an experiment.

    Args:
        experiment_id: the experiment this result belongs to
        variant: "control" or "variant"
        success: 1 for success, 0 for failure
        score: optional float quality score (e.g. intelligence_score)
        agent: optional agent name
    """
    _ensure_schema()
    try:
        import db

        now = time.time()

        def _do():
            with db.get_conn() as conn:
                conn.execute(
                    """INSERT INTO experiment_results
                       (experiment_id, variant, agent, success, score, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (experiment_id, variant, agent, int(success), score, now),
                )

        db._retry_write(_do)
    except Exception:
        log.warning("record_result failed for %s", experiment_id, exc_info=True)


def evaluate_experiment(experiment_id):
    """Evaluate an experiment: compare control vs variant.

    Returns a dict with:
        - control: {n, successes, success_rate, avg_score}
        - variant: {n, successes, success_rate, avg_score}
        - p_value: chi-squared p-value (None if insufficient data)
        - winner: "control", "variant", or "inconclusive"
        - sufficient_data: bool
    """
    _ensure_schema()
    try:
        import db

        from config import load_config
        cfg = load_config()
        exp_cfg = cfg.get("experiments", {})
        min_samples = exp_cfg.get("min_samples_per_variant", 20)

        with db.get_conn() as conn:
            stats = {}
            for variant in ("control", "variant"):
                row = conn.execute("""
                    SELECT COUNT(*) as n,
                           SUM(success) as successes,
                           AVG(score) as avg_score
                    FROM experiment_results
                    WHERE experiment_id = ? AND variant = ?
                """, (experiment_id, variant)).fetchone()

                n = row["n"] or 0
                successes = row["successes"] or 0
                stats[variant] = {
                    "n": n,
                    "successes": successes,
                    "success_rate": round(successes / n, 4) if n > 0 else 0.0,
                    "avg_score": round(row["avg_score"], 4) if row["avg_score"] is not None else None,
                }

        sufficient = (stats["control"]["n"] >= min_samples
                      and stats["variant"]["n"] >= min_samples)

        p_value = None
        winner = "inconclusive"

        if sufficient:
            p_value = _chi_squared_p(stats["control"], stats["variant"])
            if p_value is not None and p_value < 0.05:
                if stats["variant"]["success_rate"] > stats["control"]["success_rate"]:
                    winner = "variant"
                else:
                    winner = "control"

        return {
            "experiment_id": experiment_id,
            "control": stats["control"],
            "variant": stats["variant"],
            "p_value": round(p_value, 6) if p_value is not None else None,
            "winner": winner,
            "sufficient_data": sufficient,
            "min_samples": min_samples,
        }

    except Exception:
        log.warning("evaluate_experiment failed for %s", experiment_id, exc_info=True)
        return {"experiment_id": experiment_id, "error": "evaluation failed"}


def _chi_squared_p(control, variant):
    """Compute a chi-squared p-value for 2x2 contingency (success/fail x variant).

    Uses the chi-squared approximation with Yates' correction.
    Returns None if any expected cell < 5 (approximation unreliable).
    """
    try:
        a = control["successes"]       # control success
        b = control["n"] - a           # control fail
        c = variant["successes"]       # variant success
        d = variant["n"] - c           # variant fail
        n = a + b + c + d

        if n == 0:
            return None

        # Expected values
        row1 = a + b
        row2 = c + d
        col1 = a + c
        col2 = b + d

        expected = [
            row1 * col1 / n,
            row1 * col2 / n,
            row2 * col1 / n,
            row2 * col2 / n,
        ]

        # Chi-squared approximation requires expected >= 5
        if any(e < 5 for e in expected):
            return None

        # Yates' corrected chi-squared
        chi2 = (n * (abs(a * d - b * c) - n / 2) ** 2) / (row1 * row2 * col1 * col2)

        # Survival function for chi-squared with df=1
        # Using the complementary error function approximation
        return _chi2_sf(chi2, df=1)

    except (ZeroDivisionError, ValueError):
        return None


def _chi2_sf(x, df=1):
    """Survival function (1 - CDF) for chi-squared distribution.

    Uses the regularized incomplete gamma function approximation for df=1.
    For df=1, P(X > x) = erfc(sqrt(x/2)).
    """
    try:
        return math.erfc(math.sqrt(x / 2))
    except (ValueError, OverflowError):
        return 0.0


def promote_winner(experiment_id):
    """Mark experiment as completed and record the winner.

    Does NOT auto-deploy the variant file to skills/ — that requires
    operator review per project conventions (drafts never auto-deploy).
    Returns the evaluation result dict.
    """
    _ensure_schema()
    try:
        import db

        result = evaluate_experiment(experiment_id)

        def _do():
            with db.get_conn() as conn:
                conn.execute(
                    """UPDATE experiments
                       SET status = 'completed',
                           results_json = ?
                       WHERE id = ?""",
                    (json.dumps(result), experiment_id),
                )

        db._retry_write(_do)
        log.info("Experiment %s completed: winner=%s", experiment_id, result.get("winner"))
        return result

    except Exception:
        log.warning("promote_winner failed for %s", experiment_id, exc_info=True)
        return {"experiment_id": experiment_id, "error": "promotion failed"}


def get_active_experiments():
    """Return all currently active experiments."""
    _ensure_schema()
    try:
        import db

        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT id, skill_name, variant_path, status, created_at
                   FROM experiments
                   WHERE status = 'active'
                   ORDER BY created_at DESC"""
            ).fetchall()
            return [dict(r) for r in rows]

    except Exception:
        log.warning("get_active_experiments failed", exc_info=True)
        return []


def check_auto_promote():
    """Check all active experiments for auto-promotion eligibility.

    Auto-promotes if variant p-value < 0.05 AND variant success rate
    exceeds auto_promote_threshold from config. Called periodically
    by the supervisor or dashboard.
    """
    try:
        from config import load_config
        cfg = load_config()
        exp_cfg = cfg.get("experiments", {})

        if not exp_cfg.get("ab_testing_enabled", True):
            return []

        threshold = exp_cfg.get("auto_promote_threshold", 0.95)
        promoted = []

        for exp in get_active_experiments():
            result = evaluate_experiment(exp["id"])
            if not result.get("sufficient_data"):
                continue
            p = result.get("p_value")
            if p is not None and p < 0.05:
                if result.get("winner") == "variant":
                    vr = result["variant"]["success_rate"]
                    if vr >= threshold:
                        promote_winner(exp["id"])
                        promoted.append(exp["id"])
                        log.info("Auto-promoted experiment %s (p=%.4f, rate=%.3f)",
                                 exp["id"], p, vr)

        return promoted

    except Exception:
        log.warning("check_auto_promote failed", exc_info=True)
        return []
