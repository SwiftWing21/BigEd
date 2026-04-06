"""Knowledge endpoints — RAG, discussions, reviews, recommendations, experiments, feedback.

Extracted from dashboard.py (Phase 5 of dashboard decomposition).
"""
import json
import logging
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

from dashboard_utils import (
    FLEET_DIR, KNOWLEDGE_DIR,
    _load_config, get_conn, query,
    _get_request_role, _require_role,
    _check_rate_limit, _safe_error,
    _broadcast_sse,
)

log = logging.getLogger("dashboard.knowledge")

knowledge_bp = Blueprint("knowledge", __name__)


# ── Discussions ─────────────────────────────────────────────────────────────

@knowledge_bp.route("/api/discussions")
def api_discussions():
    rows = query("""
        SELECT from_agent, body_json, created_at
        FROM messages
        WHERE body_json IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 200
    """)
    topics = defaultdict(lambda: {"agents": set(), "rounds": set(), "count": 0, "last": ""})
    for r in rows:
        try:
            body = json.loads(r["body_json"])
            topic = body.get("topic", "unknown")
            topics[topic]["agents"].add(r["from_agent"])
            topics[topic]["rounds"].add(body.get("round", 1))
            topics[topic]["count"] += 1
            if not topics[topic]["last"] or r["created_at"] > topics[topic]["last"]:
                topics[topic]["last"] = r["created_at"]
        except Exception:
            pass
    result = []
    for topic, data in sorted(topics.items(), key=lambda x: x[1]["last"], reverse=True):
        result.append({
            "topic": topic,
            "agents": sorted(data["agents"]),
            "rounds": max(data["rounds"]) if data["rounds"] else 0,
            "contributions": data["count"],
            "last_activity": data["last"],
        })
    return jsonify(result)


# ── Knowledge ───────────────────────────────────────────────────────────────

@knowledge_bp.route("/api/knowledge")
def api_knowledge():
    if not _check_rate_limit("knowledge", 5):
        return jsonify({"error": "Rate limited"}), 429
    categories = {}
    if not KNOWLEDGE_DIR.exists():
        return jsonify(categories)
    for subdir in sorted(KNOWLEDGE_DIR.iterdir()):
        if subdir.is_dir():
            files = list(subdir.rglob("*"))
            file_list = [
                {"name": str(f.relative_to(KNOWLEDGE_DIR)), "size": f.stat().st_size,
                 "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()}
                for f in files if f.is_file()
            ]
            categories[subdir.name] = {
                "count": len(file_list),
                "files": sorted(file_list, key=lambda x: x["modified"], reverse=True)[:20],
            }
        elif subdir.is_file():
            categories[subdir.name] = {
                "count": 1,
                "files": [{"name": subdir.name, "size": subdir.stat().st_size,
                           "modified": datetime.fromtimestamp(subdir.stat().st_mtime).isoformat()}],
            }
    return jsonify(categories)


# ── Reviews ─────────────────────────────────────────────────────────────────

@knowledge_bp.route("/api/reviews")
def api_reviews():
    reviews = []
    for review_dir in [KNOWLEDGE_DIR / "code_reviews", KNOWLEDGE_DIR / "fma_reviews"]:
        if not review_dir.exists():
            continue
        for f in sorted(review_dir.glob("*_review_*.md"), reverse=True)[:30]:
            try:
                content = f.read_text(errors="ignore")
                lines = content.splitlines()[:6]
                reviews.append({
                    "file": f.name,
                    "category": review_dir.name,
                    "size": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                    "header": "\n".join(lines),
                })
            except Exception:
                pass
    return jsonify(reviews)


# ── RAG ─────────────────────────────────────────────────────────────────────

@knowledge_bp.route("/api/rag")
def api_rag():
    if not _check_rate_limit("rag", 5):
        return jsonify({"error": "Rate limited"}), 429
    rag_db = FLEET_DIR / "rag.db"
    if not rag_db.exists():
        return jsonify({"files": 0, "chunks": 0, "sources": []})
    try:
        # rag.db has no DAL get_conn() — intentional raw sqlite3 for read-only access
        with sqlite3.connect(str(rag_db), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            chunks = conn.execute("SELECT COUNT(*) FROM chunks_meta").fetchone()[0]
            sources = [
                dict(r) for r in conn.execute(
                    "SELECT path, chunks, indexed FROM files ORDER BY indexed DESC LIMIT 30"
                ).fetchall()
            ]
        return jsonify({"files": files, "chunks": chunks, "sources": sources})
    except Exception as e:
        return jsonify({"error": _safe_error(e), "files": 0, "chunks": 0, "sources": []})


# ── Skill Recommendations ──────────────────────────────────────────────────

@knowledge_bp.route("/api/recommendations/<skill>")
def api_skill_recommendations(skill):
    """Skill recommendations after completing a task — co-occurrence based."""
    try:
        if not _check_rate_limit("recommendations", max_per_min=30):
            return jsonify({"error": "rate limited"}), 429

        from skill_recommender import get_recommendations, get_skill_chain

        n = min(20, max(1, int(request.args.get("n", 5))))
        depth = min(10, max(1, int(request.args.get("depth", 3))))

        recs = get_recommendations(skill, n=n)
        chain = get_skill_chain(skill, depth=depth)

        return jsonify({
            "skill": skill,
            "recommendations": recs,
            "chain": chain,
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e), "recommendations": []}), 500


@knowledge_bp.route("/api/recommendations/popular")
def api_popular_skills():
    """Most-used skills by task count over the last 30 days."""
    try:
        if not _check_rate_limit("popular_skills", max_per_min=30):
            return jsonify({"error": "rate limited"}), 429

        from skill_recommender import get_popular_skills

        n = min(50, max(1, int(request.args.get("n", 10))))
        skills = get_popular_skills(n=n)
        return jsonify({"skills": skills, "total": len(skills)})
    except Exception as e:
        return jsonify({"error": _safe_error(e), "skills": []}), 500


# ── A/B Testing Experiments ─────────────────────────────────────────────────

@knowledge_bp.route("/api/experiments")
def api_experiments_list():
    """List active A/B experiments."""
    try:
        if not _check_rate_limit("experiments_list", max_per_min=30):
            return jsonify({"error": "rate limited"}), 429

        from ab_testing import get_active_experiments

        experiments = get_active_experiments()
        return jsonify({"experiments": experiments, "total": len(experiments)})
    except Exception as e:
        return jsonify({"error": _safe_error(e), "experiments": []}), 500


@knowledge_bp.route("/api/experiments", methods=["POST"])
def api_experiments_create():
    """Create a new A/B experiment.

    Body JSON:
        skill (str):        skill name to experiment on
        variant_path (str): Python module path for the variant skill
    """
    try:
        if not _check_rate_limit("experiments_create", max_per_min=10):
            return jsonify({"error": "rate limited"}), 429

        data = request.get_json(silent=True) or {}
        skill = (data.get("skill") or "").strip()
        variant_path = (data.get("variant_path") or "").strip()

        if not skill:
            return jsonify({"error": "skill required"}), 400
        if not variant_path:
            return jsonify({"error": "variant_path required"}), 400

        from ab_testing import create_experiment

        exp_id = create_experiment(skill, variant_path)
        if not exp_id:
            return jsonify({"error": "failed to create experiment"}), 500

        # Audit log
        try:
            from audit import log_audit
            log_audit(
                actor=_get_request_role() or "operator",
                action="experiment.create",
                resource=f"experiment:{exp_id}",
                detail=f"A/B test: {skill} vs {variant_path}",
                role=_get_request_role(),
                ip_address=request.remote_addr,
            )
        except Exception:
            pass

        return jsonify({"experiment_id": exp_id, "skill": skill, "variant_path": variant_path})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@knowledge_bp.route("/api/experiments/<exp_id>/results")
def api_experiment_results(exp_id):
    """Evaluate an experiment: compare control vs variant with p-value."""
    try:
        if not _check_rate_limit("experiment_results", max_per_min=30):
            return jsonify({"error": "rate limited"}), 429

        from ab_testing import evaluate_experiment

        result = evaluate_experiment(exp_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@knowledge_bp.route("/api/experiments/<exp_id>/promote", methods=["POST"])
def api_experiment_promote(exp_id):
    """Promote the winner of an experiment (marks as completed).

    Does NOT auto-deploy the variant file — operator must review and
    copy from code_drafts/ to skills/ per project conventions.
    """
    try:
        if not _check_rate_limit("experiment_promote", max_per_min=5):
            return jsonify({"error": "rate limited"}), 429

        from ab_testing import promote_winner

        result = promote_winner(exp_id)

        # Audit log
        try:
            from audit import log_audit
            log_audit(
                actor=_get_request_role() or "operator",
                action="experiment.promote",
                resource=f"experiment:{exp_id}",
                detail=f"Winner: {result.get('winner', 'unknown')}",
                role=_get_request_role(),
                ip_address=request.remote_addr,
            )
        except Exception:
            pass

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Human Feedback ──────────────────────────────────────────────────────────

@knowledge_bp.route("/api/feedback", methods=["POST"])
def api_submit_feedback():
    """Submit human feedback on an agent output.

    Body JSON:
        output_path (str):    path or 'task:<id>' identifying the output
        verdict (str):        'approved' or 'rejected'
        feedback_text (str):  optional free-text explanation
        agent_name (str):     optional agent that produced the output
        skill_type (str):     optional skill that produced the output
    """
    try:
        if not _check_rate_limit("feedback_submit", max_per_min=30):
            return jsonify({"error": "rate limited"}), 429

        data = request.get_json(silent=True) or {}
        output_path = (data.get("output_path") or "").strip()
        verdict = (data.get("verdict") or "").strip().lower()
        feedback_text = (data.get("feedback_text") or "").strip()
        agent_name = (data.get("agent_name") or "").strip()
        skill_type = (data.get("skill_type") or "").strip()

        if not output_path:
            return jsonify({"error": "output_path required"}), 400
        if verdict not in ("approved", "rejected"):
            return jsonify({"error": "verdict must be 'approved' or 'rejected'"}), 400

        # Store feedback
        import db
        db.submit_feedback(output_path, verdict, feedback_text, agent_name, skill_type)

        # Process reinforcement (IQ adjustments + re-review dispatch)
        result = {"output_path": output_path, "verdict": verdict}
        try:
            from reinforcement import process_approved, process_rejected, process_ditl_rejection

            if verdict == "approved":
                new_score = process_approved(output_path, agent_name, skill_type)
                if new_score is not None:
                    result["new_iq"] = new_score

            elif verdict == "rejected":
                # Dispatch re-review task
                re_task = process_rejected(output_path, agent_name, skill_type, feedback_text)
                if re_task is not None:
                    result["re_review_task_id"] = re_task

                # DITL: if enabled and rejected, also log PHI audit + clinical review
                try:
                    cfg = _load_config()
                    if cfg.get("ditl", {}).get("enabled", False):
                        ditl_result = process_ditl_rejection(output_path, agent_name, feedback_text)
                        if ditl_result:
                            result["ditl_audit_id"] = ditl_result.get("audit_id")
                            result["ditl_task_id"] = ditl_result.get("task_id")
                except Exception:
                    pass  # DITL is optional — never block feedback on it

        except Exception:
            pass  # reinforcement is enhancement — never block feedback storage

        # Broadcast SSE event so dashboard updates live
        _broadcast_sse({
            "type": "feedback",
            "data": {
                "output_path": output_path,
                "verdict": verdict,
                "agent_name": agent_name,
                "skill_type": skill_type,
            },
        })

        return jsonify(result)

    except ValueError as e:
        return jsonify({"error": _safe_error(e)}), 400
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@knowledge_bp.route("/api/feedback", methods=["GET"])
def api_get_feedback():
    """Query feedback with filters.

    Query params:
        output_path (str):  exact match on output path
        agent (str):        filter by agent_name
        skill (str):        filter by skill_type
        verdict (str):      filter by verdict (approved/rejected/neutral)
        days (int):         lookback window in days (default 30)
        limit (int):        max rows (default 100, max 500)
    """
    try:
        if not _check_rate_limit("feedback_get", max_per_min=30):
            return jsonify({"error": "rate limited"}), 429

        output_path = request.args.get("output_path", "").strip()
        agent = request.args.get("agent", "").strip()
        skill = request.args.get("skill", "").strip()
        verdict = request.args.get("verdict", "").strip()
        days = min(365, max(1, int(request.args.get("days", 30))))
        limit = min(500, max(1, int(request.args.get("limit", 100))))

        # If output_path is given, return single feedback
        if output_path:
            import db
            fb = db.get_feedback(output_path)
            return jsonify({"feedback": fb})

        # Otherwise, query with filters
        import db
        clauses = ["created_at >= datetime('now', ?)"]
        params = [f"-{days} days"]

        if agent:
            clauses.append("agent_name = ?")
            params.append(agent)
        if skill:
            clauses.append("skill_type = ?")
            params.append(skill)
        if verdict:
            clauses.append("verdict = ?")
            params.append(verdict)

        where = " AND ".join(clauses)
        params.append(limit)

        with db.get_conn() as conn:
            rows = conn.execute(
                f"""SELECT id, output_path, verdict, feedback_text, operator,
                           agent_name, skill_type, created_at
                    FROM output_feedback
                    WHERE {where}
                    ORDER BY created_at DESC
                    LIMIT ?""",
                params,
            ).fetchall()

        return jsonify({"feedback": [dict(r) for r in rows], "count": len(rows)})

    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@knowledge_bp.route("/api/feedback/stats")
def api_feedback_stats():
    """Feedback stats: approval rate by agent, by skill, trend.

    Query params:
        days (int): lookback window in days (default 7)
    """
    try:
        if not _check_rate_limit("feedback_stats", max_per_min=20):
            return jsonify({"error": "rate limited"}), 429

        days = min(365, max(1, int(request.args.get("days", 7))))

        import db
        raw = db.get_feedback_stats(days=days)

        # Pivot into by-agent and by-skill summaries
        by_agent = {}
        by_skill = {}
        totals = {"approved": 0, "rejected": 0, "neutral": 0}

        for row in raw:
            agent = row.get("agent_name") or "unknown"
            skill = row.get("skill_type") or "unknown"
            v = row.get("verdict", "neutral")
            cnt = row.get("cnt", 0)

            totals[v] = totals.get(v, 0) + cnt

            if agent not in by_agent:
                by_agent[agent] = {"approved": 0, "rejected": 0, "neutral": 0}
            by_agent[agent][v] = by_agent[agent].get(v, 0) + cnt

            if skill not in by_skill:
                by_skill[skill] = {"approved": 0, "rejected": 0, "neutral": 0}
            by_skill[skill][v] = by_skill[skill].get(v, 0) + cnt

        # Compute approval rates
        total_reviewed = totals["approved"] + totals["rejected"]
        approval_rate = round(totals["approved"] / total_reviewed, 3) if total_reviewed else None

        for d in list(by_agent.values()) + list(by_skill.values()):
            reviewed = d["approved"] + d["rejected"]
            d["approval_rate"] = round(d["approved"] / reviewed, 3) if reviewed else None

        # Daily trend (last N days)
        trend = []
        try:
            with db.get_conn() as conn:
                rows = conn.execute(
                    """SELECT DATE(created_at) as day, verdict, COUNT(*) as cnt
                       FROM output_feedback
                       WHERE created_at >= datetime('now', ?)
                       GROUP BY day, verdict
                       ORDER BY day""",
                    (f"-{days} days",),
                ).fetchall()
            trend_map = {}
            for r in rows:
                day = r["day"]
                if day not in trend_map:
                    trend_map[day] = {"day": day, "approved": 0, "rejected": 0, "neutral": 0}
                trend_map[day][r["verdict"]] = r["cnt"]
            trend = list(trend_map.values())
        except Exception:
            pass

        return jsonify({
            "days": days,
            "totals": totals,
            "approval_rate": approval_rate,
            "by_agent": by_agent,
            "by_skill": by_skill,
            "trend": trend,
        })

    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── GET /api/knowledge/wiki/graph — wiki pages as graph nodes (WS-6) ────────

@knowledge_bp.route("/api/knowledge/wiki/graph")
def api_wiki_graph():
    """Generate graph nodes + edges from the knowledge wiki.

    Reads knowledge/wiki/*.md for pages and cross-links.
    Returns {nodes, edges} for the views graph system.
    Wiki pages connect to each other (links_to) and to
    knowledge folders they summarize (summarizes).
    """
    import re

    wiki_dir = FLEET_DIR / "knowledge" / "wiki"
    nodes = []
    edges = []

    if not wiki_dir.exists():
        return jsonify({"nodes": [], "edges": []})

    for md_file in sorted(wiki_dir.glob("*.md")):
        page_name = md_file.stem
        node_id = f"wiki:{page_name}"

        try:
            content = md_file.read_text(encoding="utf-8")
            first_line = content.split("\n")[0]
            label = first_line.lstrip("# ").strip() if first_line.startswith("#") else page_name
        except Exception:
            label = page_name
            content = ""

        nodes.append({
            "id": node_id,
            "label": label,
            "type": "wiki_page",
            "name": page_name,
            "status": "active",
        })

        # Parse cross-links
        for match in re.finditer(r'\[([^\]]+)\]\(([^)]+)\)', content):
            link_text, link_target = match.groups()
            if link_target.endswith(".md") and "/" not in link_target:
                target_id = f"wiki:{link_target.replace('.md', '')}"
                edges.append({
                    "source": node_id, "target": target_id,
                    "type": "links_to", "label": link_text,
                })
            elif link_target.startswith("../") and link_target.endswith("/"):
                folder_name = link_target.strip("../").rstrip("/")
                edges.append({
                    "source": node_id, "target": f"folder:{folder_name}",
                    "type": "summarizes",
                })

    return jsonify({"nodes": nodes, "edges": edges, "page_count": len(nodes)})
