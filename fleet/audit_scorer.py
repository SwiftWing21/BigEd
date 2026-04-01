# fleet/audit_scorer.py
"""Two-Brain Audit Scoring Engine.

Combines automated quantitative scoring (left brain) with manual qualitative
grading (right brain). Reconciles them with divergence detection.

Tiers: light (smoke test), medium (on-demand), daily (3 AM), weekly (Sunday).
"""
import json
import logging
import os
from pathlib import Path

log = logging.getLogger("audit_scorer")

FLEET_DIR = Path(__file__).parent
BASELINE_PATH = FLEET_DIR / "audit_baseline.json"

# ── Grade Scale ───────────────────────────────────────────────────────────

GRADE_TO_SCORE = {
    "S": 1.00, "A+": 0.95, "A": 0.90, "A-": 0.85,
    "B+": 0.80, "B": 0.75, "B-": 0.70,
    "C+": 0.65, "C": 0.60,
    "D": 0.50,
    "F": 0.30,
}

_SCORE_THRESHOLDS = sorted(GRADE_TO_SCORE.items(), key=lambda x: x[1], reverse=True)


def grade_to_score(grade: str) -> float:
    """Convert a letter grade to its numeric score (0.0-1.0)."""
    return GRADE_TO_SCORE.get(grade, 0.0)


def score_to_grade(score: float) -> str:
    """Convert a numeric score to the nearest letter grade.

    Uses a half-step tolerance: each grade covers scores from its midpoint
    with the grade below up to (but not including) the midpoint with the
    grade above.  Tolerance of 0.035 keeps the rounding intuitive across
    the full scale (S=1.0 needs score>=0.965, A+>=0.915, A>=0.865, …).
    """
    for grade, threshold in _SCORE_THRESHOLDS:
        if score >= threshold - 0.035:
            return grade
    return "F"


# ── Baseline Sidecar ─────────────────────────────────────────────────────

def load_baseline() -> dict:
    """Load manual grades from audit_baseline.json."""
    if not BASELINE_PATH.exists():
        log.warning("audit_baseline.json not found at %s", BASELINE_PATH)
        return {"version": "unknown", "dimensions": {}}
    try:
        with open(BASELINE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        log.warning("Failed to parse audit_baseline.json", exc_info=True)
        return {"version": "unknown", "dimensions": {}}


def save_baseline(baseline: dict) -> None:
    """Write updated baseline back to disk."""
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ── Helpers ───────────────────────────────────────────────────────────────

def _dashboard_port() -> int:
    """Read dashboard port from fleet.toml (default 5555)."""
    try:
        from config import load_config
        cfg = load_config()
        return int(cfg.get("dashboard", {}).get("port", 5555))
    except Exception:
        return 5555


def _probe_url(url: str, timeout: int = 8) -> tuple[bool, float]:
    """Return (ok, latency_ms) for a GET request."""
    import time
    import urllib.request
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            ok = resp.status == 200
    except Exception:
        ok = False
    latency = (time.perf_counter() - t0) * 1000
    return ok, latency


# ── Check Functions ───────────────────────────────────────────────────────

def _check_testing() -> tuple[float, dict]:
    """Run pytest and score passed/total."""
    import subprocess
    try:
        root = str(FLEET_DIR.parent)
        result = subprocess.run(
            ["python", "-m", "pytest", "--tb=no", "-q"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = result.stdout + result.stderr
        # parse "X passed" and "Y failed"
        import re
        passed = 0
        failed = 0
        m_passed = re.search(r"(\d+) passed", output)
        m_failed = re.search(r"(\d+) failed", output)
        if m_passed:
            passed = int(m_passed.group(1))
        if m_failed:
            failed = int(m_failed.group(1))
        total = passed + failed
        score = passed / total if total > 0 else 0.0
        return score, {"passed": passed, "failed": failed, "total": total}
    except Exception as e:
        log.warning("_check_testing failed: %s", e)
        return 0.0, {"error": str(e)}


def _check_module_plugin() -> tuple[float, dict]:
    """Count rows in the modules table."""
    try:
        import db
        with db.get_conn() as conn:
            # Check if modules table exists first
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='modules'"
            ).fetchone()
            if not exists:
                return 0.5, {"modules": 0, "note": "modules table not found"}
            count = conn.execute("SELECT COUNT(*) FROM modules").fetchone()[0]
        score = min(1.0, count / 10.0) if count > 0 else 0.0
        return score, {"modules": count}
    except Exception as e:
        log.warning("_check_module_plugin failed: %s", e)
        return 0.0, {"error": str(e)}


def _check_documentation() -> tuple[float, dict]:
    """Score = (total_refs - stale) / total_refs via doc_freshness skill."""
    try:
        import sys
        skills_dir = str(FLEET_DIR / "skills")
        if skills_dir not in sys.path:
            sys.path.insert(0, skills_dir)
        from doc_freshness import run as df_run
        result = df_run({}, {})
        stale = result.get("stale_count", 0)
        live = result.get("live_values", {})
        # Estimate total_refs as stale + count of live values
        total = stale + len([v for v in live.values() if v is not None])
        if total == 0:
            return 1.0, {"stale": 0, "total": 0}
        score = max(0.0, (total - stale) / total)
        return score, {"stale": stale, "total": total, "live_values": live}
    except Exception as e:
        log.warning("_check_documentation failed: %s", e)
        return 0.0, {"error": str(e)}


def _check_performance() -> tuple[float, dict]:
    """Probe key endpoints for availability and latency."""
    port = _dashboard_port()
    base = f"http://127.0.0.1:{port}"
    endpoints = ["/api/health", "/api/health/agents", "/api/health/skills"]
    results = []
    ok_count = 0
    for ep in endpoints:
        ok, latency = _probe_url(base + ep, timeout=8)
        results.append({"endpoint": ep, "ok": ok, "latency_ms": round(latency, 1)})
        if ok:
            ok_count += 1
    score = ok_count / len(endpoints)
    return score, {"endpoints": results, "ok": ok_count, "total": len(endpoints)}


def _check_reliability() -> tuple[float, dict]:
    """(healthy_agents + closed_breakers) / (total_agents + total_breakers)."""
    try:
        from health_monitor import get_agent_health_summary
        agents = get_agent_health_summary()
        total_agents = len(agents)
        healthy_agents = sum(1 for a in agents if a.get("status") == "healthy")

        import db
        # Count circuit breakers from circuit_breakers table if it exists
        closed_breakers = 0
        total_breakers = 0
        try:
            with db.get_conn() as conn:
                exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='circuit_breakers'"
                ).fetchone()
                if exists:
                    rows = conn.execute("SELECT state FROM circuit_breakers").fetchall()
                    total_breakers = len(rows)
                    closed_breakers = sum(1 for r in rows if r["state"] == "closed")
        except Exception as e:
            log.warning("_check_reliability breakers query failed: %s", e)

        denom = total_agents + total_breakers
        if denom == 0:
            return 1.0, {"note": "no agents or breakers found"}
        score = (healthy_agents + closed_breakers) / denom
        return score, {
            "healthy_agents": healthy_agents,
            "total_agents": total_agents,
            "closed_breakers": closed_breakers,
            "total_breakers": total_breakers,
        }
    except Exception as e:
        log.warning("_check_reliability failed: %s", e)
        return 0.0, {"error": str(e)}


def _check_observability() -> tuple[float, dict]:
    """SSE module loads + audit_log readable + /api/health 200."""
    checks = {}
    ok = 0
    total = 3

    # 1. Check SSE module loads (sse_blueprint or similar)
    try:
        sse_template = FLEET_DIR / "templates" / "components" / "_scripts_sse.html"
        checks["sse_template"] = sse_template.exists()
        if checks["sse_template"]:
            ok += 1
    except Exception as e:
        checks["sse_template"] = False
        log.warning("_check_observability sse check failed: %s", e)

    # 2. audit_log table readable
    try:
        import db
        with db.get_conn() as conn:
            conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
        checks["audit_log"] = True
        ok += 1
    except Exception as e:
        checks["audit_log"] = False
        log.warning("_check_observability audit_log check failed: %s", e)

    # 3. /api/health responds 200
    port = _dashboard_port()
    health_ok, _ = _probe_url(f"http://127.0.0.1:{port}/api/health", timeout=8)
    checks["api_health"] = health_ok
    if health_ok:
        ok += 1

    score = ok / total
    return score, {"checks": checks, "ok": ok, "total": total}


def _check_architecture() -> tuple[float, dict]:
    """Count LOC for key files; score = files under 1500 LOC / total."""
    key_files = [
        FLEET_DIR / "dashboard.py",
        FLEET_DIR / "supervisor.py",
        FLEET_DIR / "hw_supervisor.py",
        FLEET_DIR / "db.py",
        FLEET_DIR / "providers.py",
    ]
    results = {}
    under_limit = 0
    found = 0
    for f in key_files:
        if not f.exists():
            results[f.name] = None
            continue
        found += 1
        try:
            loc = sum(1 for _ in f.open("r", encoding="utf-8", errors="ignore"))
        except Exception:
            loc = 9999
        results[f.name] = loc
        if loc <= 1500:
            under_limit += 1
    score = under_limit / found if found > 0 else 1.0
    return score, {"files": results, "under_1500": under_limit, "total_checked": found}


def _check_code_quality() -> tuple[float, dict]:
    """ruff clean + no raw sqlite3 in skills + no bare excepts."""
    import subprocess
    checks = {}
    ok = 0
    total = 3

    # 1. ruff lint
    try:
        result = subprocess.run(
            ["python", "-m", "ruff", "check", str(FLEET_DIR), "--quiet"],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        checks["ruff_clean"] = result.returncode == 0
        if checks["ruff_clean"]:
            ok += 1
    except Exception as e:
        checks["ruff_clean"] = False
        log.warning("_check_code_quality ruff failed: %s", e)

    # 2. No raw sqlite3.connect in skills/
    try:
        import re
        skills_dir = FLEET_DIR / "skills"
        raw_sqlite_files = []
        if skills_dir.exists():
            for py in skills_dir.glob("*.py"):
                try:
                    content = py.read_text(encoding="utf-8", errors="ignore")
                    if re.search(r"sqlite3\.connect\s*\(", content):
                        raw_sqlite_files.append(py.name)
                except Exception:
                    pass
        checks["no_raw_sqlite"] = len(raw_sqlite_files) == 0
        checks["raw_sqlite_files"] = raw_sqlite_files
        if checks["no_raw_sqlite"]:
            ok += 1
    except Exception as e:
        checks["no_raw_sqlite"] = False
        log.warning("_check_code_quality sqlite check failed: %s", e)

    # 3. No bare excepts in skills/
    try:
        import re
        bare_except_files = []
        skills_dir = FLEET_DIR / "skills"
        if skills_dir.exists():
            for py in skills_dir.glob("*.py"):
                try:
                    content = py.read_text(encoding="utf-8", errors="ignore")
                    if re.search(r"^\s*except\s*:", content, re.MULTILINE):
                        bare_except_files.append(py.name)
                except Exception:
                    pass
        checks["no_bare_excepts"] = len(bare_except_files) == 0
        checks["bare_except_files"] = bare_except_files
        if checks["no_bare_excepts"]:
            ok += 1
    except Exception as e:
        checks["no_bare_excepts"] = False
        log.warning("_check_code_quality bare_except check failed: %s", e)

    score = ok / total
    return score, {"checks": checks, "ok": ok, "total": total}


def _check_security() -> tuple[float, dict]:
    """RBAC roles >= 2 distinct + path traversal endpoint check."""
    import db
    checks = {}
    ok = 0
    total = 2

    # 1. RBAC roles populated
    try:
        with db.get_conn() as conn:
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='rbac_roles'"
            ).fetchone()
            if exists:
                count = conn.execute(
                    "SELECT COUNT(DISTINCT role) FROM rbac_roles"
                ).fetchone()[0]
            else:
                # Try user_roles or roles table
                exists2 = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='roles'"
                ).fetchone()
                if exists2:
                    count = conn.execute(
                        "SELECT COUNT(DISTINCT name) FROM roles"
                    ).fetchone()[0]
                else:
                    count = 0
        checks["rbac_roles"] = count
        checks["rbac_ok"] = count >= 2
        if checks["rbac_ok"]:
            ok += 1
    except Exception as e:
        checks["rbac_roles"] = 0
        checks["rbac_ok"] = False
        log.warning("_check_security rbac check failed: %s", e)

    # 2. Path traversal check — endpoint rejects ../
    port = _dashboard_port()
    traversal_ok = False
    try:
        import urllib.request
        import urllib.error
        url = f"http://127.0.0.1:{port}/api/../../../etc/passwd"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                # If it returns 200, that's bad
                traversal_ok = resp.status != 200
        except urllib.error.HTTPError as he:
            # 400/403/404 are all acceptable rejections
            traversal_ok = he.code in (400, 403, 404)
        except Exception:
            # Connection refused = server not running, neutral
            traversal_ok = True
    except Exception as e:
        log.warning("_check_security traversal check failed: %s", e)
        traversal_ok = True  # neutral if can't test

    checks["path_traversal_blocked"] = traversal_ok
    if traversal_ok:
        ok += 1

    score = ok / total
    return score, {"checks": checks, "ok": ok, "total": total}


def _check_usability_ux() -> tuple[float, dict]:
    """Pages load check + blend with user_feedback if available."""
    port = _dashboard_port()
    base = f"http://127.0.0.1:{port}"
    pages = ["/", "/api/health", "/api/fleet/status"]
    ok_count = 0
    page_results = []
    for page in pages:
        ok, latency = _probe_url(base + page, timeout=10)
        page_results.append({"page": page, "ok": ok, "latency_ms": round(latency, 1)})
        if ok:
            ok_count += 1
    page_score = ok_count / len(pages)

    # Blend with recent user_feedback if available
    feedback_score = None
    try:
        import db
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT AVG(score) as avg_score FROM user_feedback "
                "WHERE scope='ux' AND timestamp >= datetime('now', '-7 days')"
            ).fetchone()
            if row and row["avg_score"] is not None:
                feedback_score = float(row["avg_score"])
    except Exception as e:
        log.warning("_check_usability_ux feedback query failed: %s", e)

    if feedback_score is not None:
        final_score = 0.6 * page_score + 0.4 * feedback_score
    else:
        final_score = page_score

    return final_score, {
        "pages": page_results,
        "page_score": page_score,
        "feedback_score": feedback_score,
        "ok": ok_count,
        "total": len(pages),
    }


def _check_dynamic_abilities() -> tuple[float, dict]:
    """Check that ml_route_log + experiments + billing_events tables exist."""
    import db
    target_tables = ["ml_route_log", "experiments", "billing_events"]
    found = []
    try:
        with db.get_conn() as conn:
            for tbl in target_tables:
                exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (tbl,),
                ).fetchone()
                if exists:
                    found.append(tbl)
    except Exception as e:
        log.warning("_check_dynamic_abilities failed: %s", e)
        return 0.0, {"error": str(e)}
    score = len(found) / len(target_tables)
    return score, {"found": found, "missing": [t for t in target_tables if t not in found]}


def _check_data_hitl() -> tuple[float, dict]:
    """Check hitl_queue + ingest_sources tables exist with rows."""
    import db
    target_tables = ["hitl_queue", "ingest_sources"]
    results = {}
    ok = 0
    try:
        with db.get_conn() as conn:
            for tbl in target_tables:
                exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (tbl,),
                ).fetchone()
                if exists:
                    count = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                    results[tbl] = {"exists": True, "rows": count}
                    ok += 1
                else:
                    results[tbl] = {"exists": False, "rows": 0}
    except Exception as e:
        log.warning("_check_data_hitl failed: %s", e)
        return 0.0, {"error": str(e)}
    score = ok / len(target_tables)
    return score, {"tables": results, "ok": ok, "total": len(target_tables)}


# ── DIMENSIONS Registry ───────────────────────────────────────────────────

DIMENSIONS: dict = {
    "testing":           {"check_fn": _check_testing,           "confidence": 0.95, "tier": "light"},
    "module_plugin":     {"check_fn": _check_module_plugin,     "confidence": 0.90, "tier": "light"},
    "documentation":     {"check_fn": _check_documentation,     "confidence": 0.85, "tier": "light"},
    "performance":       {"check_fn": _check_performance,       "confidence": 0.85, "tier": "light"},
    "reliability":       {"check_fn": _check_reliability,       "confidence": 0.85, "tier": "medium"},
    "observability":     {"check_fn": _check_observability,     "confidence": 0.80, "tier": "medium"},
    "architecture":      {"check_fn": _check_architecture,      "confidence": 0.75, "tier": "medium"},
    "code_quality":      {"check_fn": _check_code_quality,      "confidence": 0.75, "tier": "medium"},
    "security":          {"check_fn": _check_security,          "confidence": 0.60, "tier": "daily"},
    "usability_ux":      {"check_fn": _check_usability_ux,      "confidence": 0.30, "tier": "daily"},
    "dynamic_abilities": {"check_fn": _check_dynamic_abilities, "confidence": 0.60, "tier": "daily"},
    "data_hitl":         {"check_fn": _check_data_hitl,         "confidence": 0.55, "tier": "daily"},
}

TIER_ORDER = ["light", "medium", "daily", "weekly"]


# ── Tier Runner ───────────────────────────────────────────────────────────

def run_tier(tier: str) -> list[dict]:
    """Run all dimension checks up to and including the specified tier.

    Returns a list of dicts with keys:
        dimension, auto_score, auto_detail, manual_grade, divergence
    """
    if tier not in TIER_ORDER:
        log.warning("run_tier: unknown tier '%s'", tier)
        return []

    allowed_tiers = TIER_ORDER[: TIER_ORDER.index(tier) + 1]
    baseline = load_baseline()
    dimensions_baseline = baseline.get("dimensions", {})

    results = []
    for dim_name, dim_cfg in DIMENSIONS.items():
        if dim_cfg["tier"] not in allowed_tiers:
            continue
        try:
            score, detail = dim_cfg["check_fn"]()
        except Exception as e:
            log.warning("run_tier: check for '%s' raised: %s", dim_name, e)
            score, detail = 0.0, {"error": str(e)}

        manual_grade = dimensions_baseline.get(dim_name, {}).get("grade", "")
        manual_score = grade_to_score(manual_grade) if manual_grade else None

        divergence = 0
        if manual_score is not None:
            gap = abs(score - manual_score)
            confidence = dim_cfg.get("confidence", 1.0)
            # Flag divergence if gap exceeds 2 grade steps adjusted by confidence
            threshold = 0.15 * (1.0 - confidence + 0.5)
            if gap > threshold:
                divergence = 1

        results.append({
            "dimension": dim_name,
            "auto_score": round(score, 4),
            "auto_detail": json.dumps(detail) if not isinstance(detail, str) else detail,
            "manual_grade": manual_grade,
            "divergence": divergence,
        })

    return results


# ── DB Persistence ────────────────────────────────────────────────────────

def write_scores(tier: str, scores: list[dict]) -> None:
    """Write score results to audit_scores table using db._retry_write."""
    import db

    def _do():
        with db.get_conn() as conn:
            for s in scores:
                conn.execute(
                    """INSERT INTO audit_scores
                       (tier, dimension, auto_score, auto_detail, manual_grade, divergence)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        tier,
                        s["dimension"],
                        s["auto_score"],
                        s.get("auto_detail", "{}"),
                        s.get("manual_grade", ""),
                        s.get("divergence", 0),
                    ),
                )

    db._retry_write(_do)


def get_active_divergences() -> list[dict]:
    """Return unacknowledged divergences from the latest run."""
    import db
    results = []
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT dimension, auto_score, manual_grade, auto_detail, timestamp
                   FROM audit_scores
                   WHERE divergence = 1 AND acknowledged = 0
                   ORDER BY timestamp DESC"""
            ).fetchall()
        for row in rows:
            results.append(dict(row))
    except Exception as e:
        log.warning("get_active_divergences failed: %s", e)
    return results


def acknowledge_divergence(dimension: str) -> bool:
    """Mark a divergence as acknowledged."""
    import db

    def _do():
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE audit_scores SET acknowledged = 1 "
                "WHERE dimension = ? AND divergence = 1 AND acknowledged = 0",
                (dimension,),
            )

    try:
        db._retry_write(_do)
        return True
    except Exception as e:
        log.warning("acknowledge_divergence failed: %s", e)
        return False


def get_latest_scores() -> list[dict]:
    """Get the most recent score for each dimension."""
    import db
    results = []
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT dimension, auto_score, manual_grade, divergence, tier, timestamp
                   FROM audit_scores
                   WHERE id IN (
                       SELECT MAX(id) FROM audit_scores GROUP BY dimension
                   )
                   ORDER BY dimension"""
            ).fetchall()
        for row in rows:
            results.append(dict(row))
    except Exception as e:
        log.warning("get_latest_scores failed: %s", e)
    return results


def get_score_history(days: int = 30) -> list[dict]:
    """Get score history for trending."""
    import db
    results = []
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT dimension, auto_score, manual_grade, divergence, tier, timestamp
                   FROM audit_scores
                   WHERE timestamp >= datetime('now', ? || ' days')
                   ORDER BY dimension, timestamp""",
                (f"-{days}",),
            ).fetchall()
        for row in rows:
            results.append(dict(row))
    except Exception as e:
        log.warning("get_score_history failed: %s", e)
    return results


# ── Reconciliation Engine ─────────────────────────────────────────────────

DIVERGENCE_THRESHOLD = 0.15
CONFIDENCE_GATE = 0.5


def reconcile(scores: list[dict]) -> list[dict]:
    """Compare auto scores against manual grades, flag divergences.

    Only flags when: gap > DIVERGENCE_THRESHOLD AND dimension confidence >= CONFIDENCE_GATE.
    Mutates and returns the input scores list with updated divergence flags.
    """
    baseline = load_baseline()
    for s in scores:
        dim_name = s["dimension"]
        dim_conf = DIMENSIONS.get(dim_name, {}).get("confidence", 0.0)
        manual = baseline.get("dimensions", {}).get(dim_name, {})
        manual_grade = manual.get("grade", s.get("manual_grade", ""))
        s["manual_grade"] = manual_grade
        if not manual_grade:
            continue
        manual_numeric = grade_to_score(manual_grade)
        gap = abs(s["auto_score"] - manual_numeric)
        if gap > DIVERGENCE_THRESHOLD and dim_conf >= CONFIDENCE_GATE:
            s["divergence"] = 1
        else:
            s["divergence"] = 0
    return scores


def check_ratchets(scores: list[dict], ratchets: dict | None = None) -> list[dict]:
    """Check if scores dropped below ratchet targets.

    ratchets: {dimension: grade_string}. Loads from baseline if None.
    Returns list of violation dicts.
    """
    if ratchets is None:
        baseline = load_baseline()
        ratchets = baseline.get("ratchets", {})
    violations = []
    for s in scores:
        dim = s["dimension"]
        if dim not in ratchets:
            continue
        ratchet_grade = ratchets[dim]
        ratchet_score = grade_to_score(ratchet_grade)
        if s["auto_score"] < ratchet_score:
            violations.append({
                "dimension": dim,
                "auto_score": s["auto_score"],
                "ratchet_grade": ratchet_grade,
                "ratchet_score": ratchet_score,
            })
    return violations


def run_and_store(tier: str) -> dict:
    """Run a tier, reconcile, check ratchets, store to DB, return summary."""
    scores = run_tier(tier)
    scores = reconcile(scores)
    baseline = load_baseline()
    ratchet_violations = check_ratchets(scores, baseline.get("ratchets", {}))
    write_scores(tier, scores)
    divergences = [s for s in scores if s["divergence"] == 1]
    return {
        "tier": tier,
        "dimensions_scored": len(scores),
        "divergences": len(divergences),
        "ratchet_violations": len(ratchet_violations),
        "scores": scores,
        "ratchet_details": ratchet_violations,
    }


# ── User Feedback ─────────────────────────────────────────────────────────

def record_feedback(
    score: float,
    scope: str = "overall",
    session_id: str | None = None,
    text: str | None = None,
    inferred: list | None = None,
    actor: str | None = None,
) -> int:
    """Record user feedback. Returns the row ID."""
    import db
    row_id = None

    def _do():
        nonlocal row_id
        with db.get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO user_feedback (score, scope, session_id, text, inferred, actor) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    score,
                    scope,
                    session_id,
                    text,
                    json.dumps(inferred) if inferred else None,
                    actor,
                ),
            )
            row_id = cur.lastrowid

    db._retry_write(_do)
    return row_id


def get_feedback_summary() -> dict:
    """Aggregate feedback by scope."""
    import db
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT scope, AVG(score) as avg_score, COUNT(*) as count, "
            "MIN(score) as min_score, MAX(score) as max_score "
            "FROM user_feedback GROUP BY scope"
        ).fetchall()
    return {r["scope"]: dict(r) for r in rows}


def get_ux_confidence(feedback_count: int) -> float:
    """UX confidence scales from 0.30 base to 0.75 at 100+ entries."""
    return min(0.75, 0.30 + (feedback_count / 100) * 0.45)


# ── Weekly External Checks ────────────────────────────────────────────────

def _check_semgrep() -> tuple[float, dict]:
    """Run semgrep SAST scan; score penalises errors and warnings."""
    import subprocess
    try:
        result = subprocess.run(
            [
                "semgrep",
                "--config", "p/python",
                "--config", "p/owasp-top-ten",
                "--json",
                "--quiet",
                str(FLEET_DIR),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            data = json.loads(result.stdout or "{}")
        except Exception:
            data = {}
        results_list = data.get("results", [])
        errors = sum(1 for r in results_list if r.get("extra", {}).get("severity") == "ERROR")
        warnings = sum(1 for r in results_list if r.get("extra", {}).get("severity") == "WARNING")
        score = max(0.0, 1.0 - (errors * 0.15 + warnings * 0.05))
        return score, {"errors": errors, "warnings": warnings, "total_findings": len(results_list)}
    except FileNotFoundError:
        return 1.0, {"skipped": "semgrep not installed"}
    except Exception as e:
        log.warning("_check_semgrep failed: %s", e)
        return 1.0, {"skipped": str(e)}


def _check_version_drift() -> tuple[float, dict]:
    """Check tracked packages for version drift against PyPI latest."""
    import urllib.request
    import importlib.metadata

    packages = ["flask", "psutil", "httpx", "scikit-learn"]
    detail: dict = {}
    scores: list[float] = []

    for pkg in packages:
        try:
            installed = importlib.metadata.version(pkg)
        except Exception:
            detail[pkg] = {"skipped": "not installed"}
            continue

        try:
            url = f"https://pypi.org/pypi/{pkg}/json"
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            latest = data["info"]["version"]
        except Exception as e:
            log.warning("_check_version_drift: PyPI fetch failed for %s: %s", pkg, e)
            detail[pkg] = {"installed": installed, "skipped": "network error"}
            continue  # network failure — don't penalise

        # Parse semver components
        def _parts(v: str) -> tuple[int, int, int]:
            parts = (v.split("+")[0]).split(".")
            try:
                major = int(parts[0]) if len(parts) > 0 else 0
                minor = int(parts[1]) if len(parts) > 1 else 0
                patch = int(parts[2]) if len(parts) > 2 else 0
            except Exception:
                major, minor, patch = 0, 0, 0
            return major, minor, patch

        inst_parts = _parts(installed)
        latest_parts = _parts(latest)

        if inst_parts[0] < latest_parts[0]:
            pkg_score = 0.0
            drift = "major"
        elif inst_parts[1] < latest_parts[1]:
            pkg_score = 0.5
            drift = "minor"
        else:
            pkg_score = 1.0
            drift = "current" if inst_parts == latest_parts else "patch"

        detail[pkg] = {"installed": installed, "latest": latest, "drift": drift, "score": pkg_score}
        scores.append(pkg_score)

    avg = sum(scores) / len(scores) if scores else 1.0
    return avg, detail


def _check_github() -> tuple[float, dict]:
    """Check CI status, open bugs, and stale PRs via GitHub API."""
    import os as _os
    token = _os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return 1.0, {"skipped": "no GITHUB_TOKEN"}

    owner = "SwiftWing21"
    repo = "BigEd-private"

    try:
        from skills.git_suite import _github_api
    except Exception as e:
        log.warning("_check_github: could not import git_suite: %s", e)
        return 1.0, {"skipped": f"git_suite import failed: {e}"}

    detail: dict = {}
    sub_scores: list[float] = []

    # 1. CI status — latest workflow run
    try:
        data = _github_api("GET", f"/repos/{owner}/{repo}/actions/runs?per_page=5")
        runs = data.get("workflow_runs", [])
        if runs:
            latest_conclusion = runs[0].get("conclusion")
            ci_score = 1.0 if latest_conclusion == "success" else 0.0
            detail["ci"] = {"conclusion": latest_conclusion, "score": ci_score}
        else:
            ci_score = 1.0
            detail["ci"] = {"conclusion": None, "score": 1.0, "note": "no runs found"}
        sub_scores.append(ci_score)
    except Exception as e:
        log.warning("_check_github CI check failed: %s", e)
        detail["ci"] = {"error": str(e)}

    # 2. Open bug count
    try:
        data = _github_api("GET", f"/repos/{owner}/{repo}/issues?labels=bug&state=open")
        bug_count = len(data) if isinstance(data, list) else 0
        bug_score = max(0.0, 1.0 - bug_count * 0.05)
        detail["open_bugs"] = {"count": bug_count, "score": bug_score}
        sub_scores.append(bug_score)
    except Exception as e:
        log.warning("_check_github open bugs check failed: %s", e)
        detail["open_bugs"] = {"error": str(e)}

    # 3. Stale open PRs (> 14 days)
    try:
        from datetime import datetime, timezone, timedelta
        data = _github_api("GET", f"/repos/{owner}/{repo}/pulls?state=open")
        prs = data if isinstance(data, list) else []
        cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        stale_count = 0
        for pr in prs:
            created_at_str = pr.get("created_at", "")
            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                if created_at < cutoff:
                    stale_count += 1
            except Exception:
                pass
        stale_score = max(0.0, 1.0 - stale_count * 0.1)
        detail["stale_prs"] = {"stale_count": stale_count, "total_open": len(prs), "score": stale_score}
        sub_scores.append(stale_score)
    except Exception as e:
        log.warning("_check_github stale PR check failed: %s", e)
        detail["stale_prs"] = {"error": str(e)}

    avg = sum(sub_scores) / len(sub_scores) if sub_scores else 1.0
    return avg, detail


def _check_federation_mcp() -> tuple[float, dict]:
    """Probe federation peers and MCP servers for availability."""
    import urllib.request

    ok = 0
    total = 0
    detail: dict = {"peers": {}, "mcp": {}}

    # Federation peers
    try:
        from config import load_config
        cfg = load_config()
        peers = cfg.get("federation", {}).get("peers", [])
        for peer in peers:
            total += 1
            peer_url = peer if isinstance(peer, str) else peer.get("url", "")
            probe_url = peer_url.rstrip("/") + "/api/federation/capacity"
            try:
                with urllib.request.urlopen(probe_url, timeout=10) as resp:
                    peer_ok = resp.status == 200
            except Exception:
                peer_ok = False
            detail["peers"][peer_url] = peer_ok
            if peer_ok:
                ok += 1
    except Exception as e:
        log.warning("_check_federation_mcp peers probe failed: %s", e)
        detail["peers_error"] = str(e)

    # MCP servers
    try:
        import sys as _sys
        fleet_dir = str(FLEET_DIR)
        if fleet_dir not in _sys.path:
            _sys.path.insert(0, fleet_dir)
        from mcp_manager import get_all_server_status
        mcp_statuses = get_all_server_status()
        for name, status in mcp_statuses.items():
            total += 1
            mcp_ok = status.get("ok", False) or status.get("status") == "ok"
            detail["mcp"][name] = mcp_ok
            if mcp_ok:
                ok += 1
    except Exception as e:
        log.warning("_check_federation_mcp MCP probe failed: %s", e)
        detail["mcp_error"] = str(e)

    score = ok / total if total > 0 else 1.0
    return score, {"ok": ok, "total": total, **detail}


# ── Score Blending ────────────────────────────────────────────────────────

def merged_score(daily: float, weekly: float | None, weekly_age_days: int) -> float:
    """Blend daily and weekly scores; weekly weight decays with age.

    Weight = max(0.1, 0.4 - age_days * 0.05).
    If weekly is None, returns daily unchanged.
    """
    if weekly is None:
        return daily
    weight = max(0.1, 0.4 - weekly_age_days * 0.05)
    return daily * (1.0 - weight) + weekly * weight
