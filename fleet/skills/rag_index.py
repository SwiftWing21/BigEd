"""
RAG index skill — rebuilds or incrementally updates the RAG search index.

Scans all .md files across the project and indexes them into SQLite FTS5
for retrieval-augmented generation by fleet agents.

Payload:
  mode    str   "update" (incremental, default) | "rebuild" (full re-index)

Returns: {files_indexed, total_chunks, ...}
"""
import sys
from pathlib import Path

SKILL_NAME = "rag_index"
DESCRIPTION = "RAG index skill — rebuilds or incrementally updates the RAG search index."

FLEET_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(FLEET_DIR))


def run(payload, config):
    from rag import RAGIndex

    mode = payload.get("mode", "update")
    idx = RAGIndex()

    if mode == "rebuild":
        result = idx.rebuild()
        return {"action": "rebuild", **result}
    else:
        result = idx.update()
        return {"action": "update", **result}