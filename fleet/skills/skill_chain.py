"""
Skill chain — runs a sequence of skills, piping each output as input to the next.

Enables compound workflows like:
  web_search → summarize → flashcard
  lead_research → web_crawl → marketing
  rag_query → code_discuss → skill_draft

Payload:
  steps    list   [{skill: str, payload: dict, merge_key: str}, ...]
                  merge_key: which key from the previous result to merge into next payload
                  (default: merges entire previous result as "prior_result")
  stop_on_error  bool  stop chain on first failure (default true)

Returns: {steps_completed, results: [{skill, status, result_preview}], final_result}
"""
import importlib
import json
import logging
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"

log = logging.getLogger("skill_chain")


def _run_skill(skill_name: str, payload: dict, config: dict) -> dict:
    """Import and run a skill by name."""
    module = importlib.import_module(f"skills.{skill_name}")
    return module.run(payload, config)


def run(payload, config):
    steps = payload.get("steps", [])
    stop_on_error = payload.get("stop_on_error", True)

    if not steps:
        return {"error": "No steps provided"}

    results = []
    prev_result = {}
    completed = 0

    for i, step in enumerate(steps):
        skill = step.get("skill", "")
        step_payload = step.get("payload", {})
        merge_key = step.get("merge_key", "")

        if not skill:
            results.append({"skill": f"step_{i}", "status": "SKIP", "error": "No skill name"})
            continue

        # Merge previous result into this step's payload
        if prev_result:
            if merge_key and merge_key in prev_result:
                step_payload["prior_result"] = prev_result[merge_key]
            else:
                step_payload["prior_result"] = prev_result

        try:
            result = _run_skill(skill, step_payload, config)
            prev_result = result if isinstance(result, dict) else {"result": result}
            preview = json.dumps(prev_result, default=str)[:300]
            results.append({"skill": skill, "status": "DONE", "result_preview": preview})
            completed += 1
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            results.append({"skill": skill, "status": "FAILED", "error": error_msg})
            if stop_on_error:
                break

    # Save chain log
    log_dir = KNOWLEDGE_DIR / "chains"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    skill_names = "_".join(s.get("skill", "?")[:10] for s in steps[:4])
    log_file = log_dir / f"chain_{skill_names}_{ts}.md"

    lines = [
        f"# Skill Chain — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Steps:** {len(steps)} | **Completed:** {completed}",
        "",
    ]
    for r in results:
        status_icon = "✓" if r["status"] == "DONE" else "✕" if r["status"] == "FAILED" else "–"
        lines.append(f"- {status_icon} **{r['skill']}** → {r['status']}")
        if r.get("error"):
            lines.append(f"  Error: {r['error']}")
    log_file.write_text("\n".join(lines))

    return {
        "steps_completed": completed,
        "total_steps": len(steps),
        "results": results,
        "final_result": prev_result,
        "saved_to": str(log_file),
    }
