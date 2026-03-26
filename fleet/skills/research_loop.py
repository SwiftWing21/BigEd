"""Tier 2: Autonomous research → synthesize → train cycle."""
import json
from datetime import datetime
from pathlib import Path
from collections import Counter
import logging

SKILL_NAME = "research_loop"
DESCRIPTION = "Autonomous research cycle — detect gaps, research, synthesize, generate training data"
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False
SUITE = "research"
log = logging.getLogger(SKILL_NAME)

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"


def run(payload: dict, config: dict) -> dict:
    action = payload.get("action", "detect_gaps")

    if action == "detect_gaps":
        return _detect_knowledge_gaps()
    elif action == "research_cycle":
        return _run_research_cycle(payload, config)
    elif action == "quality_scores":
        return _get_quality_scores()
    elif action == "ingest_batch":
        return _ingest_batch(payload, config)
    else:
        return {"status": "error", "error": f"Unknown action: {action}"}


def _ingest_batch(payload, config):
    """Pull a batch from a HuggingFace source and dispatch as tasks or ingest to RAG.

    Called by marathon/idle mode to systematically build the knowledge base.
    Tracks offset per source so each call picks up where the last left off.
    """
    import sys
    sys.path.insert(0, str(FLEET_DIR))

    source_id = payload.get("source_id")
    if not source_id:
        return {"status": "error", "error": "source_id required"}

    batch_size = payload.get("batch_size", 10)
    destination = payload.get("destination", "tasks")

    try:
        import ingest_manager

        # Find source config
        sources = ingest_manager.list_sources()
        source = next((s for s in sources if s["id"] == source_id), None)
        if not source:
            return {"status": "error", "error": f"Source '{source_id}' not found"}

        dataset_id = source.get("dataset", "")
        content_col = source.get("content_column", "text")
        skill = source.get("skill", "summarize")
        dest = source.get("destination", destination)

        # Read offset from source_meta.json (resume where we left off)
        cache_dir = ingest_manager._get_cache_dir() / source_id
        cache_dir.mkdir(parents=True, exist_ok=True)
        meta_path = cache_dir / "source_meta.json"

        offset = 0
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                offset = meta.get("last_offset", 0)
            except Exception:
                pass

        # Fetch batch from HuggingFace
        result = ingest_manager.fetch_hf_rows(
            dataset_id, offset=offset, length=batch_size,
            split="train", config="default",
        )

        if result.get("error"):
            return {"status": "error", "error": result["error"], "source_id": source_id}

        rows = result.get("rows", [])
        if not rows:
            return {"status": "ok", "source_id": source_id, "message": "No more rows (dataset exhausted)",
                    "offset": offset, "dispatched": 0}

        # Dispatch each row
        import db
        dispatched = 0

        for i, row in enumerate(rows):
            content = row.get(content_col, "")
            if not content:
                # Try first string column as fallback
                for v in row.values():
                    if isinstance(v, str) and len(v) > 20:
                        content = v
                        break
            if not content:
                continue

            title = content[:80].replace("\n", " ")
            token_est = len(content) // 4

            if dest == "rag":
                # Ingest to RAG: chunks_meta (source, heading, text) + chunks FTS5 (text, heading, source)
                try:
                    import rag
                    idx = rag.RAGIndex(str(FLEET_DIR / "rag.db"))
                    conn = idx._get_conn()
                    try:
                        conn.execute(
                            "INSERT INTO chunks_meta (source, heading, text) VALUES (?, ?, ?)",
                            (f"ingest:{source_id}", title, content[:500]),
                        )
                        chunk_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                        conn.execute(
                            "INSERT INTO chunks (rowid, text, heading, source) VALUES (?, ?, ?, ?)",
                            (chunk_id, content[:8000], title, f"ingest:{source_id}"),
                        )
                        conn.commit()
                        dispatched += 1
                    finally:
                        conn.close()
                except Exception as e:
                    log.warning("RAG ingest failed for %s row %d: %s", source_id, offset + i, e)
            else:
                # Dispatch as fleet task
                try:
                    task_payload = {
                        "source": f"hf:{source_id}",
                        "row_id": offset + i,
                        "content": content[:4000],
                        "title": title,
                    }
                    db.post_task(
                        type_=skill,
                        payload_json=json.dumps(task_payload),
                        priority=3,
                        classification="ingest",
                    )
                    dispatched += 1
                except Exception as e:
                    log.warning("Task dispatch failed for %s row %d: %s", source_id, offset + i, e)

        # Update offset for next call
        new_offset = offset + len(rows)
        meta = {"source_id": source_id, "last_offset": new_offset,
                "total_fetched": (meta.get("total_fetched", 0) if meta_path.exists() else 0) + len(rows)}
        try:
            existing = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            existing.update(meta)
            meta_path.write_text(json.dumps(existing, indent=2))
        except Exception:
            meta_path.write_text(json.dumps(meta, indent=2))

        # Cache the batch
        try:
            ingest_manager.cache_batch(source_id, rows)
        except Exception:
            pass  # Cache is nice-to-have, not critical

        return {
            "status": "ok",
            "source_id": source_id,
            "dataset": dataset_id,
            "destination": dest,
            "offset": offset,
            "new_offset": new_offset,
            "rows_fetched": len(rows),
            "dispatched": dispatched,
            "skill": skill,
        }

    except Exception as e:
        log.warning("ingest_batch failed for %s: %s", source_id, e, exc_info=True)
        return {"status": "error", "error": str(e), "source_id": source_id}


def _detect_knowledge_gaps():
    """Scan knowledge/ for thin coverage areas."""
    coverage = {}
    if not KNOWLEDGE_DIR.exists():
        return {"status": "ok", "gaps": [], "coverage": {}}

    for subdir in sorted(KNOWLEDGE_DIR.iterdir()):
        if not subdir.is_dir() or subdir.name.startswith("_"):
            continue
        files = list(subdir.rglob("*.md")) + list(subdir.rglob("*.json"))
        total_size = sum(f.stat().st_size for f in files if f.is_file())
        coverage[subdir.name] = {
            "files": len(files),
            "size_kb": round(total_size / 1024, 1),
        }

    # Identify gaps: directories with < 3 files or < 5KB
    gaps = []
    for name, stats in coverage.items():
        if stats["files"] < 3:
            gaps.append({"area": name, "reason": f"only {stats['files']} files", "priority": "high"})
        elif stats["size_kb"] < 5:
            gaps.append({"area": name, "reason": f"only {stats['size_kb']}KB content", "priority": "medium"})

    return {"status": "ok", "gaps": gaps, "coverage": coverage, "total_areas": len(coverage)}


def _run_research_cycle(payload, config):
    """Full research cycle: detect gap → dispatch research → synthesize → generate training data."""
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db

    topic = payload.get("topic")
    if not topic:
        # Auto-detect from gaps
        gaps_result = _detect_knowledge_gaps()
        gaps = gaps_result.get("gaps", [])
        if gaps:
            import random
            topic = random.choice(gaps[:3])["area"]  # pick from top 3 gaps
        else:
            return {"status": "no_gaps", "message": "Knowledge base is well-covered"}

    # Dispatch research chain
    tasks = []

    # Step 1: Research
    t1 = db.post_task("web_search", json.dumps({"query": f"{topic} best practices 2026"}), priority=4)
    tasks.append({"step": "research", "task_id": t1, "skill": "web_search"})

    # Step 2: Summarize (depends on research)
    t2 = db.post_task("summarize", json.dumps({"description": f"Summarize research on {topic}"}),
                       priority=3, depends_on=[t1])
    tasks.append({"step": "summarize", "task_id": t2, "skill": "summarize"})

    # Step 3: Generate training data (depends on summary)
    t3 = db.post_task("dataset_synthesize", json.dumps({
        "topic": topic, "format": "instruction", "count": 10,
    }), priority=2, depends_on=[t2])
    tasks.append({"step": "training_data", "task_id": t3, "skill": "dataset_synthesize"})

    return {
        "status": "dispatched",
        "topic": topic,
        "tasks": tasks,
        "pipeline": "research → summarize → training_data",
    }


def _get_quality_scores():
    """Aggregate quality scores from task results."""
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db

    try:
        with db.get_conn() as conn:
            # Count completed tasks per skill with success/fail rates
            rows = conn.execute("""
                SELECT type, status, COUNT(*) as n
                FROM tasks
                WHERE created_at >= datetime('now', '-7 days')
                GROUP BY type, status
            """).fetchall()

        skills = {}
        for r in rows:
            skill = r["type"]
            if skill not in skills:
                skills[skill] = {"done": 0, "failed": 0, "total": 0}
            skills[skill]["total"] += r["n"]
            if r["status"] == "DONE":
                skills[skill]["done"] += r["n"]
            elif r["status"] == "FAILED":
                skills[skill]["failed"] += r["n"]

        # Calculate success rate
        scored = []
        for name, stats in skills.items():
            rate = stats["done"] / stats["total"] if stats["total"] > 0 else 0
            scored.append({
                "skill": name,
                "success_rate": round(rate * 100, 1),
                "total": stats["total"],
                "done": stats["done"],
                "failed": stats["failed"],
            })
        scored.sort(key=lambda x: -x["success_rate"])

        return {"status": "ok", "quality_scores": scored}
    except Exception as e:
        return {"status": "error", "error": str(e)}
