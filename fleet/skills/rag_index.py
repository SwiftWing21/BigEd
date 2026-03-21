"""
RAG index skill — rebuilds, updates, or cleans up the RAG search index.

Scans all .md files across the project and indexes them into SQLite FTS5
for retrieval-augmented generation by fleet agents.

Payload:
  mode    str   "update" (incremental, default) | "rebuild" (full re-index)
                | "cleanup" (remove stale entries) | "stats" (index stats)

Returns: {files_indexed, total_chunks, ...}
"""
import sys
from pathlib import Path

SKILL_NAME = "rag_index"
DESCRIPTION = "RAG index skill — rebuilds or incrementally updates the RAG search index."

FLEET_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(FLEET_DIR))


def run(payload, config):
    try:
        from filesystem_guard import FileSystemGuard
        from config import load_config
        guard = FileSystemGuard(load_config())
        if not guard.check_access("fleet/knowledge", "write", skill="rag_index"):
            return {"error": "Access denied to knowledge/ by FileSystemGuard"}
    except ImportError:
        pass  # Guard not available — allow (non-enterprise)

    from rag import RAGIndex

    mode = payload.get("mode", "update")
    idx = RAGIndex()

    if mode == "rebuild":
        result = idx.rebuild()
        return {"action": "rebuild", **result}
    elif mode == "cleanup":
        result = idx.cleanup_stale()
        return {"action": "cleanup", **result}
    elif mode == "stats":
        result = idx.get_index_stats()
        return {"action": "stats", **result}
    else:
        result = idx.update()
        return {"action": "update", **result}