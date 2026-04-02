"""Two-Brain Audit REST API."""
import json
import logging
from flask import Blueprint, jsonify, request

log = logging.getLogger("audit_api")
audit_bp = Blueprint("audit", __name__)


def _safe_error(exc):
    return f"{type(exc).__name__}: {exc}"


# ── GET /api/audit/scores — latest scores for all dimensions ────────────────

@audit_bp.route("/api/audit/scores")
def api_audit_scores():
    """Return the most recent audit scores for all 12 dimensions."""
    try:
        from audit_scorer import get_latest_scores
        scores = get_latest_scores()
        return jsonify({"scores": scores})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── GET /api/audit/scores/history — score history over N days ───────────────

@audit_bp.route("/api/audit/scores/history")
def api_audit_scores_history():
    """Return audit score history for the past N days (default 30)."""
    try:
        days = request.args.get("days", 30, type=int)
        from audit_scorer import get_score_history
        history = get_score_history(days)
        return jsonify({"history": history, "days": days})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── GET /api/audit/divergences — active brain divergences ───────────────────

@audit_bp.route("/api/audit/divergences")
def api_audit_divergences():
    """Return currently active divergences between the two audit brains."""
    try:
        from audit_scorer import get_active_divergences
        divergences = get_active_divergences()
        return jsonify({"divergences": divergences, "count": len(divergences)})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── POST /api/audit/acknowledge/<dimension> — acknowledge a divergence ───────

@audit_bp.route("/api/audit/acknowledge/<dimension>", methods=["POST"])
def api_audit_acknowledge(dimension):
    """Acknowledge a divergence for the given dimension."""
    try:
        from audit_scorer import acknowledge_divergence
        ok = acknowledge_divergence(dimension)
        return jsonify({"acknowledged": ok, "dimension": dimension})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── POST /api/audit/trigger/<tier> — trigger an audit run ───────────────────

_VALID_TIERS = {"medium", "daily", "weekly"}


@audit_bp.route("/api/audit/trigger/<tier>", methods=["POST"])
def api_audit_trigger(tier):
    """Trigger an on-demand audit run for the given tier (medium/daily/weekly)."""
    try:
        if tier not in _VALID_TIERS:
            return jsonify({
                "error": f"Invalid tier '{tier}'. Must be one of: {sorted(_VALID_TIERS)}"
            }), 400
        from audit_scorer import run_and_store
        result = run_and_store(tier)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── GET /api/audit/baseline — load the audit baseline ───────────────────────

@audit_bp.route("/api/audit/baseline")
def api_audit_baseline():
    """Return the current audit baseline (expected scores per dimension)."""
    try:
        from audit_scorer import load_baseline
        baseline = load_baseline()
        return jsonify({"baseline": baseline})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── POST /api/audit/feedback — record human feedback ────────────────────────

@audit_bp.route("/api/audit/feedback", methods=["POST"])
def api_audit_feedback():
    """Record a human feedback entry with a score in [0.0, 1.0]."""
    try:
        body = request.get_json(force=True) or {}
        score = body.get("score")
        if score is None:
            return jsonify({"error": "Missing required field: score"}), 400
        try:
            score = float(score)
        except (TypeError, ValueError):
            return jsonify({"error": "Field 'score' must be a number"}), 400
        if not (0.0 <= score <= 1.0):
            return jsonify({"error": "Field 'score' must be between 0.0 and 1.0"}), 400

        scope = body.get("scope", "general")
        from audit_scorer import record_feedback
        _FEEDBACK_KEYS = {"session_id", "text", "inferred", "actor"}
        extras = {k: v for k, v in body.items()
                  if k in _FEEDBACK_KEYS}
        row_id = record_feedback(score=score, scope=scope, **extras)
        return jsonify({"ok": True, "row_id": row_id})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── GET /api/audit/feedback/summary — feedback aggregate summary ─────────────

@audit_bp.route("/api/audit/feedback/summary")
def api_audit_feedback_summary():
    """Return aggregate summary of human feedback entries."""
    try:
        from audit_scorer import get_feedback_summary
        summary = get_feedback_summary()
        return jsonify({"summary": summary})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── POST /api/audit/oauth-review/<dimension> — dispatch OAuth audit review ──

@audit_bp.route("/api/audit/oauth-review/<dimension>", methods=["POST"])
def api_audit_oauth_review(dimension):
    """Dispatch a fleet task to perform an OAuth audit review for the given dimension."""
    try:
        from audit_scorer import DIMENSIONS
        if dimension not in DIMENSIONS:
            return jsonify({
                "error": f"Unknown dimension '{dimension}'. Valid: {sorted(DIMENSIONS)}"
            }), 400
        import db
        task_id = db.post_task(
            "oauth_audit_review",
            json.dumps({"dimension": dimension}),
            priority=3,
        )
        return jsonify({"queued": True, "dimension": dimension, "task_id": task_id})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── GET /api/audit/snapshot — trigger a sanitized snapshot export ─────────

@audit_bp.route("/api/audit/snapshot")
def api_audit_snapshot():
    """Export a sanitized audit snapshot and return the manifest entry."""
    try:
        sanitize = request.args.get("sanitize", "true").lower() != "false"
        from audit_snapshot import export_snapshot, list_snapshots
        export_snapshot(sanitize=sanitize)
        entries = list_snapshots()
        entry = entries[0] if entries else {}
        return jsonify({"snapshot": entry})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── GET /api/audit/snapshots — list all snapshot manifest entries ─────────

@audit_bp.route("/api/audit/snapshots")
def api_audit_snapshots():
    """Return all snapshot manifest entries (newest first)."""
    try:
        from audit_snapshot import list_snapshots
        entries = list_snapshots()
        return jsonify({"snapshots": entries, "count": len(entries)})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500
