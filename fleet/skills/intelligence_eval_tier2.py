"""LLM-based semantic evaluation of task output quality (Tier 2 scoring).

Complements the mechanical Tier 1 scoring in intelligence.py by sending
completed task prompt+result pairs through a local Ollama model for
multi-dimensional quality grading: coherence, correctness, depth, actionability.

Runs on a 10% sample of recent completed tasks (or explicit task IDs).
Updates intelligence_score in the DB and saves per-skill trend data.
"""
import json
import logging
import re
from datetime import date
from pathlib import Path

SKILL_NAME = "intelligence_eval_tier2"
DESCRIPTION = "LLM-based semantic evaluation of task output quality (Tier 2 scoring)."
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False  # Uses local Ollama model

log = logging.getLogger(__name__)

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
EVAL_DIR = KNOWLEDGE_DIR / "evaluations"


def run(payload, config):
    import db
    import hashlib

    EVAL_DIR.mkdir(parents=True, exist_ok=True)

    # Get tasks to evaluate
    task_ids = payload.get("task_ids", [])
    sample_pct = payload.get("sample_pct", 10)
    limit = payload.get("limit", 20)

    with db.get_conn() as conn:
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            rows = conn.execute(
                f"SELECT id, type, prompt, result, assigned_to, intelligence_score "
                f"FROM tasks WHERE id IN ({placeholders}) AND status='DONE'",
                task_ids,
            ).fetchall()
        else:
            # Fetch recent completed tasks, then apply deterministic sampling
            rows = conn.execute("""
                SELECT id, type, prompt, result, assigned_to, intelligence_score
                FROM tasks
                WHERE status = 'DONE'
                  AND result IS NOT NULL
                  AND created_at >= datetime('now', '-48 hours')
                ORDER BY id DESC
                LIMIT ?
            """, (limit * 10,)).fetchall()

            # Deterministic sampling by hash -- reproducible, no randomness
            sampled = []
            for r in rows:
                h = int(hashlib.md5(str(r["id"]).encode()).hexdigest()[:8], 16)
                if h % 100 < sample_pct:
                    sampled.append(r)
                    if len(sampled) >= limit:
                        break
            rows = sampled

    if not rows:
        return {"status": "skip", "message": "No tasks to evaluate"}

    # Grade each task using local LLM
    evaluations = []
    scores_updated = 0

    for task in rows:
        prompt_text = task["prompt"] or ""
        result_text = task["result"] or ""

        if len(prompt_text) < 10 or len(result_text) < 10:
            continue

        # Truncate for LLM context window
        prompt_text = prompt_text[:500]
        result_text = result_text[:1500]

        grading_prompt = (
            "Grade this task output on 4 dimensions (score 0.0 to 1.0 each):\n\n"
            f"TASK: {prompt_text}\n\n"
            f"OUTPUT: {result_text}\n\n"
            "Score each dimension:\n"
            "- coherence: Is the output well-structured and logically organized?\n"
            "- correctness: Does the output correctly address the task?\n"
            "- depth: Is the response thorough and detailed enough?\n"
            "- actionability: Can the output be directly used or acted upon?\n\n"
            'Respond ONLY with JSON: {"coherence": 0.X, "correctness": 0.X, '
            '"depth": 0.X, "actionability": 0.X}'
        )

        try:
            grading_scores = _call_local_grading(grading_prompt, config)
            if grading_scores is None:
                continue

            # Composite score (weighted average -- correctness weighted highest)
            composite = (
                grading_scores.get("coherence", 0.5) * 0.2
                + grading_scores.get("correctness", 0.5) * 0.4
                + grading_scores.get("depth", 0.5) * 0.2
                + grading_scores.get("actionability", 0.5) * 0.2
            )
            composite = round(min(1.0, max(0.0, composite)), 3)

            # Blend with existing Tier 1 score (60% existing, 40% new)
            existing = task["intelligence_score"] or 0.5
            blended = round(existing * 0.6 + composite * 0.4, 3)

            def _update(tid=task["id"], score=blended):
                def _do():
                    with db.get_conn() as conn:
                        conn.execute(
                            "UPDATE tasks SET intelligence_score=? WHERE id=?",
                            (score, tid),
                        )
                db._retry_write(_do)

            _update()
            scores_updated += 1

            evaluations.append({
                "task_id": task["id"],
                "skill": task["type"],
                "agent": task["assigned_to"],
                "scores": grading_scores,
                "composite": composite,
                "previous_score": existing,
                "new_score": blended,
            })
        except Exception:
            log.warning(
                "Tier 2 grading failed for task %s", task["id"], exc_info=True
            )
            continue

    # Per-skill quality summary
    skill_quality = {}
    for ev in evaluations:
        skill = ev["skill"] or "unknown"
        skill_quality.setdefault(skill, {"scores": [], "count": 0})
        skill_quality[skill]["scores"].append(ev["composite"])
        skill_quality[skill]["count"] += 1

    for skill, data in skill_quality.items():
        data["avg_composite"] = round(sum(data["scores"]) / len(data["scores"]), 3)
        del data["scores"]  # Don't save raw score lists to report

    # Save grading report
    report = {
        "date": date.today().isoformat(),
        "sampled": len(rows),
        "graded": len(evaluations),
        "scores_updated": scores_updated,
        "skill_quality": skill_quality,
        "evaluations": evaluations,
    }

    out_file = EVAL_DIR / f"tier2_grading_{date.today().isoformat()}.json"
    out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return {
        "status": "ok",
        "saved_to": str(out_file),
        "sampled": len(rows),
        "graded": len(evaluations),
        "scores_updated": scores_updated,
        "skill_quality": skill_quality,
    }


def _call_local_grading(prompt, config):
    """Call local Ollama model for quality grading. Returns parsed JSON scores or None."""
    model = config.get("models", {}).get("conductor_model", "qwen3:4b")
    try:
        data = json.dumps({
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 200},
        }).encode("utf-8")

        import urllib.request
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())

        response_text = result.get("response", "")
        # Extract JSON from response (may have markdown wrapping)
        json_match = re.search(r'\{[^}]+\}', response_text)
        if json_match:
            scores = json.loads(json_match.group())
            # Validate all keys present and in range
            for key in ("coherence", "correctness", "depth", "actionability"):
                if key not in scores:
                    return None
                scores[key] = min(1.0, max(0.0, float(scores[key])))
            return scores
    except Exception:
        log.warning("LLM grading call failed", exc_info=True)
    return None
