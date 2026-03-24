"""
Data pipeline orchestrator — executes a sequence of transformation steps
(each referencing a skill + payload), tracks pass/fail per stage, and logs
the pipeline run.

Payload:
  pipeline_name  str    Human-readable pipeline name (default: "unnamed")
  steps          list   [{skill: str, payload: dict}, ...]
  stop_on_error  bool   Halt pipeline on first failure (default: true)

Output: knowledge/pipelines/pipeline_YYYY-MM-DD.md
Each step is posted as a sub-task in the DB for traceability.
"""
import importlib
import json
import logging
from datetime import datetime, date
from pathlib import Path

SKILL_NAME = "data_pipeline"
DESCRIPTION = "Orchestrate data pipeline stages — run skills sequentially, track pass/fail, log results."
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
PIPELINE_DIR = KNOWLEDGE_DIR / "pipelines"

log = logging.getLogger(__name__)


def _run_step(skill_name, step_payload, config):
    """Import and run a single skill step."""
    module = importlib.import_module(f"skills.{skill_name}")
    return module.run(step_payload, config)


def _record_sub_task(skill_name, step_payload, status, result_preview, parent_id):
    """Record a pipeline step as a sub-task in the database."""
    import db
    try:
        payload_json = json.dumps({
            "pipeline_step": skill_name,
            "step_payload": step_payload,
            "result_preview": result_preview[:500] if result_preview else "",
        }, default=str)
        task_id = db.post_task(
            type_=skill_name,
            payload_json=payload_json,
            priority=5,
            status=status,
            parent_id=parent_id,
        )
        return task_id
    except Exception:
        log.warning("Failed to record sub-task for step %s", skill_name, exc_info=True)
        return None


def _build_report(pipeline_name, steps_log, start_time, end_time):
    """Build the pipeline run report in Markdown."""
    elapsed = (end_time - start_time).total_seconds()
    passed = sum(1 for s in steps_log if s["status"] == "DONE")
    failed = sum(1 for s in steps_log if s["status"] == "FAILED")
    skipped = sum(1 for s in steps_log if s["status"] == "SKIPPED")

    lines = [
        f"# Pipeline Run — {pipeline_name}",
        f"**Date:** {date.today().isoformat()} | "
        f"**Duration:** {elapsed:.1f}s",
        f"**Steps:** {len(steps_log)} | "
        f"Passed: {passed} | Failed: {failed} | Skipped: {skipped}",
        "",
        "## Stage Results",
        "",
        "| # | Skill | Status | Duration | Notes |",
        "|---|-------|--------|----------|-------|",
    ]

    for i, s in enumerate(steps_log, 1):
        notes = s.get("error", s.get("preview", ""))[:80]
        lines.append(
            f"| {i} | `{s['skill']}` | {s['status']} | "
            f"{s.get('duration', 0):.1f}s | {notes} |"
        )

    lines.append("")

    if failed:
        lines.append("## Failures")
        lines.append("")
        for s in steps_log:
            if s["status"] == "FAILED":
                lines.append(f"### {s['skill']}")
                lines.append(f"```\n{s.get('error', 'Unknown error')}\n```")
                lines.append("")

    return "\n".join(lines)


def run(payload, config):
    """Orchestrate a data pipeline by running skills sequentially."""
    PIPELINE_DIR.mkdir(parents=True, exist_ok=True)

    pipeline_name = payload.get("pipeline_name", "unnamed")
    steps = payload.get("steps", [])
    stop_on_error = payload.get("stop_on_error", True)

    if not steps:
        return {"status": "error", "message": "No pipeline steps provided"}

    # Create a parent task for the pipeline run
    import db
    parent_id = None
    try:
        parent_id = db.post_task(
            type_="data_pipeline",
            payload_json=json.dumps({
                "pipeline_name": pipeline_name,
                "step_count": len(steps),
            }, default=str),
            priority=5,
            status="RUNNING",
        )
    except Exception:
        log.warning("Failed to create parent pipeline task", exc_info=True)

    start_time = datetime.now()
    steps_log = []
    halted = False

    for i, step in enumerate(steps):
        skill_name = step.get("skill", "")
        step_payload = step.get("payload", {})

        if not skill_name:
            steps_log.append({
                "skill": f"step_{i}",
                "status": "SKIPPED",
                "duration": 0,
                "error": "No skill name provided",
            })
            continue

        if halted:
            steps_log.append({
                "skill": skill_name,
                "status": "SKIPPED",
                "duration": 0,
                "error": "Pipeline halted by earlier failure",
            })
            _record_sub_task(skill_name, step_payload, "SKIPPED", "", parent_id)
            continue

        step_start = datetime.now()
        try:
            result = _run_step(skill_name, step_payload, config)
            duration = (datetime.now() - step_start).total_seconds()
            preview = json.dumps(result, default=str)[:300] if result else ""
            steps_log.append({
                "skill": skill_name,
                "status": "DONE",
                "duration": duration,
                "preview": preview,
            })
            _record_sub_task(skill_name, step_payload, "DONE", preview, parent_id)
        except Exception as exc:
            duration = (datetime.now() - step_start).total_seconds()
            error_msg = f"{type(exc).__name__}: {exc}"
            log.warning("Pipeline step %s failed: %s", skill_name, error_msg)
            steps_log.append({
                "skill": skill_name,
                "status": "FAILED",
                "duration": duration,
                "error": error_msg,
            })
            _record_sub_task(skill_name, step_payload, "FAILED", error_msg, parent_id)
            if stop_on_error:
                halted = True

    end_time = datetime.now()

    # Build and save report
    report = _build_report(pipeline_name, steps_log, start_time, end_time)
    today = date.today().isoformat()
    report_path = PIPELINE_DIR / f"pipeline_{today}.md"
    try:
        report_path.write_text(report, encoding="utf-8")
    except Exception:
        log.warning("Failed to write pipeline report", exc_info=True)

    # Mark parent task complete
    if parent_id:
        try:
            passed = sum(1 for s in steps_log if s["status"] == "DONE")
            failed = sum(1 for s in steps_log if s["status"] == "FAILED")
            final_status = "DONE" if failed == 0 else "FAILED"
            if final_status == "DONE":
                db.complete_task(parent_id, json.dumps({
                    "passed": passed, "failed": failed,
                }, default=str))
            else:
                db.fail_task(parent_id, f"{failed} stage(s) failed")
        except Exception:
            log.warning("Failed to update parent task status", exc_info=True)

    passed = sum(1 for s in steps_log if s["status"] == "DONE")
    failed = sum(1 for s in steps_log if s["status"] == "FAILED")

    return {
        "status": "ok" if failed == 0 else "partial",
        "pipeline_name": pipeline_name,
        "saved_to": str(report_path),
        "steps_total": len(steps_log),
        "passed": passed,
        "failed": failed,
        "skipped": sum(1 for s in steps_log if s["status"] == "SKIPPED"),
        "elapsed_secs": (end_time - start_time).total_seconds(),
    }
