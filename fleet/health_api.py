"""Self-Healing Fleet — Dashboard REST API (v0.200.00b).

Blueprint providing /api/health/* endpoints for agent health, skill
regression monitoring, circuit breakers, and manual recovery triggers.

Registered in dashboard.py alongside fleet_bp and a2a_bp.
"""
import json
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

FLEET_DIR = Path(__file__).parent
sys.path.insert(0, str(FLEET_DIR))

from security import (
    require_role as _require_role_raw,
    safe_error as _safe_error,
)

health_bp = Blueprint("health", __name__)


def _load_config():
    """Load fleet.toml config."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return {}
    try:
        with open(FLEET_DIR / "fleet.toml", "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _require_role(role):
    """Convenience wrapper for role-based access."""
    return _require_role_raw(role, _load_config)


# ── GET /api/health/agents — per-agent health status ────────────────────────

@health_bp.route("/api/health/agents")
def api_health_agents():
    """Per-agent health status: heartbeat freshness, error rate, issues."""
    try:
        from self_healing import get_agent_health_summary
        agents = get_agent_health_summary()
        healthy = sum(1 for a in agents if a.get("healthy"))
        return jsonify({
            "agents": agents,
            "total": len(agents),
            "healthy": healthy,
            "unhealthy": len(agents) - healthy,
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── GET /api/health/skills — skill success rates, regression flags ──────────

@health_bp.route("/api/health/skills")
def api_health_skills():
    """Skill health: success rates, regression flags, circuit breaker state."""
    try:
        from self_healing import get_skill_health_summary
        skills = get_skill_health_summary()
        regressed = [s for s in skills if s.get("regressed")]
        breaker_open = [s for s in skills if s.get("circuit_breaker_open")]
        return jsonify({
            "skills": skills,
            "total": len(skills),
            "regressed_count": len(regressed),
            "circuit_breakers_open": len(breaker_open),
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── POST /api/health/recover/<agent> — manual recovery trigger ──────────────

@health_bp.route("/api/health/recover/<agent>", methods=["POST"])
@_require_role("operator")
def api_health_recover(agent):
    """Manually trigger agent recovery: kill process, reset DB state, requeue tasks."""
    try:
        from self_healing import recover_agent
        result = recover_agent(agent)
        status_code = 200 if result.get("recovered") else 404
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── GET /api/health/circuit-breakers — currently tripped breakers ────────────

@health_bp.route("/api/health/circuit-breakers")
def api_health_circuit_breakers():
    """Circuit breaker state for all tracked skills."""
    try:
        from self_healing import get_circuit_breaker_status
        breakers = get_circuit_breaker_status()
        tripped = [b for b in breakers if b.get("tripped")]
        return jsonify({
            "breakers": breakers,
            "total_tracked": len(breakers),
            "tripped_count": len(tripped),
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── GET /api/health/rollback-candidates — skills eligible for rollback ───────

@health_bp.route("/api/health/rollback-candidates")
def api_health_rollback_candidates():
    """Skills with significant success rate drops that have backup drafts."""
    try:
        from self_healing import get_rollback_candidates
        candidates = get_rollback_candidates()
        return jsonify({
            "candidates": candidates,
            "count": len(candidates),
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── POST /api/health/rollback/<skill> — trigger skill rollback ──────────────

@health_bp.route("/api/health/rollback/<skill>", methods=["POST"])
@_require_role("admin")
def api_health_rollback(skill):
    """Roll back a regressed skill to its most recent code_drafts version."""
    try:
        from self_healing import rollback_skill
        result = rollback_skill(skill)
        status_code = 200 if result.get("rolled_back") else 400
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── GET /api/health/recovery-log — recent recovery actions ──────────────────

@health_bp.route("/api/health/recovery-log")
def api_health_recovery_log():
    """Recent self-healing recovery actions (agent recoveries, retries, breaker trips)."""
    try:
        from self_healing import get_recovery_log
        entries = get_recovery_log()
        limit = request.args.get("limit", 50, type=int)
        return jsonify({
            "entries": entries[-limit:],
            "total": len(entries),
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── GET /api/health/sweep — run health sweep on demand ──────────────────────

@health_bp.route("/api/health/sweep", methods=["POST"])
@_require_role("operator")
def api_health_sweep():
    """Trigger an immediate health sweep (normally runs every 60s in supervisor)."""
    try:
        from self_healing import run_health_sweep
        summary = run_health_sweep()
        return jsonify(summary)
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500
