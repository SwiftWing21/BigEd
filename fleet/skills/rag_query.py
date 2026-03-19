"""
RAG query skill — searches indexed .md files and returns relevant context,
optionally generating an answer using the local model.

Payload:
  query       str    search query (required)
  limit       int    max chunks to return (default 8)
  answer      bool   if true, pass retrieved context to LLM for a synthesized answer (default false)
  source      str    filter by source file pattern (optional, e.g. "fleet_commands")

Returns:
  {"chunks": [...], "answer": str (if answer=true), "query": str}
"""
import sys
from pathlib import Path

from skills._models import call_complex

SKILL_NAME = "rag_query"
DESCRIPTION = "RAG query skill — searches indexed .md files and returns relevant context,"

FLEET_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(FLEET_DIR))


def run(payload, config):
    from rag import RAGIndex

    query = payload.get("query", "")
    if not query:
        return {"error": "No query provided"}

    limit = payload.get("limit", 8)
    want_answer = payload.get("answer", False)
    source_filter = payload.get("source", "")

    idx = RAGIndex()

    # Search
    if source_filter:
        chunks = idx.search_by_source(source_filter, limit=limit)
    else:
        chunks = idx.search(query, limit=limit)

    result = {"query": query, "chunks": chunks, "num_results": len(chunks)}

    # Optionally generate an answer from retrieved context
    if want_answer and chunks:
        context = "\n\n---\n\n".join(
            f"**Source:** {c['source']} > {c['heading']}\n{c['text']}"
            for c in chunks[:6]
        )
        system_prompt = ("Answer the following question using ONLY the context provided below. "
                         "If the context doesn't contain enough information, say so. "
                         "Be concise and specific. Reference which source files your answer comes from.")
        user_prompt = f"QUESTION: {query}\n\nCONTEXT:\n{context}"
        result["answer"] = call_complex(system_prompt, user_prompt, config, skill_name="rag_query")

    return result