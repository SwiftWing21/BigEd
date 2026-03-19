"""0.10.00: RAG compression — deduplicate and consolidate knowledge chunks."""
import json
from datetime import datetime
from pathlib import Path
from skills._models import call_complex

SKILL_NAME = "rag_compress"
DESCRIPTION = "Deduplicate and consolidate overlapping RAG knowledge chunks"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"

def run(payload: dict, config: dict) -> str:
    target_dir = payload.get("directory", "summaries")
    max_files = payload.get("max_files", 20)

    source = KNOWLEDGE_DIR / target_dir
    if not source.exists():
        return json.dumps({"status": "no_data", "directory": target_dir})

    files = sorted(source.glob("*.md"), key=lambda f: f.stat().st_mtime)[:max_files]
    if len(files) < 2:
        return json.dumps({"status": "nothing_to_compress", "files": len(files)})

    # Read all file contents
    contents = {}
    for f in files:
        try:
            contents[f.name] = f.read_text(encoding="utf-8")[:2000]  # cap per file
        except Exception:
            continue

    # Ask LLM to identify overlapping content
    system = "You are a knowledge librarian. Identify overlapping or duplicate content across these documents. Return JSON: {\"groups\": [{\"topic\": \"...\", \"files\": [\"file1.md\", \"file2.md\"], \"summary\": \"...\"}]}"
    user = "\n\n".join(f"### {name}\n{text[:500]}" for name, text in contents.items())

    try:
        result = call_complex(system, user, config, max_tokens=1024, skill_name="rag_compress")
        # Save compression report
        report_path = KNOWLEDGE_DIR / "reports" / f"compression_{datetime.now().strftime('%Y%m%d')}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(f"# Knowledge Compression Report\n\n{result}", encoding="utf-8")

        return json.dumps({
            "status": "ok",
            "files_analyzed": len(contents),
            "report": str(report_path),
        })
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})
