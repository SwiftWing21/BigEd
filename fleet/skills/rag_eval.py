"""RAG evaluation skill — builds eval datasets and benchmarks retrieval quality.

Auto-generates an evaluation dataset from successful task queries, then
measures retrieval quality using MRR (Mean Reciprocal Rank) and recall@10.

Payload:
  rebuild_eval  bool  force rebuild of the eval dataset (default false)

Returns:
  {"status": "ok", "metrics": {"mrr": float, "recall_at_10": float,
   "eval_size": int, "method": "bm25"}}
"""
import json
import logging
import sys
from pathlib import Path

SKILL_NAME = "rag_eval"
DESCRIPTION = "RAG evaluation — builds eval datasets from task history and benchmarks retrieval quality"
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False
SUITE = "rag"
TAGS = ['evaluation']

FLEET_DIR = Path(__file__).parent.parent
MODELS_DIR = FLEET_DIR / "models"
EVAL_DATASET_PATH = MODELS_DIR / "rag_eval_dataset.json"

sys.path.insert(0, str(FLEET_DIR))
log = logging.getLogger(__name__)


def _build_eval_dataset() -> list[dict]:
    """Generate eval dataset from DONE tasks that have prompt payloads.

    For each query, runs BM25 search and stores the top results as
    'expected' sources — these form the ground-truth for evaluation.
    """
    import db
    from rag import RAGIndex

    idx = RAGIndex()
    dataset = []

    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT id, payload_json FROM tasks
               WHERE status = 'DONE' AND payload_json IS NOT NULL
               ORDER BY id DESC LIMIT 200"""
        ).fetchall()

    for row in rows:
        try:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        except Exception:
            continue

        # Extract query from common payload fields
        query = (
            payload.get("query")
            or payload.get("prompt")
            or payload.get("description")
            or payload.get("text", "")
        )
        if not query or not isinstance(query, str) or len(query.strip()) < 10:
            continue

        query = query.strip()

        # Get BM25 results as "expected" ground truth
        try:
            results = idx.search(query, limit=10)
        except Exception:
            continue

        if not results:
            continue

        expected_sources = [r["source"] for r in results]
        dataset.append({
            "task_id": row["id"],
            "query": query,
            "expected_sources": expected_sources,
            "top_source": expected_sources[0] if expected_sources else None,
        })

    return dataset


def _save_dataset(dataset: list[dict]) -> None:
    """Persist eval dataset to disk."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    EVAL_DATASET_PATH.write_text(
        json.dumps({"version": 1, "size": len(dataset), "items": dataset}, indent=2),
        encoding="utf-8",
    )


def _load_dataset() -> list[dict]:
    """Load eval dataset from disk. Returns empty list if missing."""
    if not EVAL_DATASET_PATH.exists():
        return []
    try:
        data = json.loads(EVAL_DATASET_PATH.read_text(encoding="utf-8"))
        return data.get("items", [])
    except Exception:
        log.warning("Failed to load eval dataset", exc_info=True)
        return []


def _compute_metrics(dataset: list[dict], strategy: str = "bm25") -> dict:
    """Compute MRR and recall@10 for the given retrieval strategy.

    Args:
        dataset: list of eval items with 'query' and 'expected_sources'
        strategy: 'bm25' (default), 'vector', or 'hybrid'

    Returns:
        dict with mrr, recall_at_10, eval_size, method
    """
    from rag import RAGIndex

    idx = RAGIndex()
    reciprocal_ranks = []
    recall_hits = 0

    for item in dataset:
        query = item["query"]
        expected = set(item.get("expected_sources", []))
        top_expected = item.get("top_source")

        if not expected or not query:
            continue

        # Retrieve using the specified strategy
        try:
            if strategy == "bm25":
                results = idx.search(query, limit=10)
            elif strategy == "vector":
                # Vector search via RAGIndex if available, else fallback to BM25
                if hasattr(idx, "vector_search"):
                    results = idx.vector_search(query, limit=10)
                else:
                    results = idx.search(query, limit=10)
            elif strategy == "hybrid":
                if hasattr(idx, "hybrid_search"):
                    results = idx.hybrid_search(query, limit=10)
                else:
                    results = idx.search(query, limit=10)
            else:
                results = idx.search(query, limit=10)
        except Exception:
            reciprocal_ranks.append(0.0)
            continue

        retrieved_sources = [r["source"] for r in results]

        # MRR: reciprocal rank of first relevant result
        rr = 0.0
        for rank, source in enumerate(retrieved_sources, start=1):
            if source == top_expected or source in expected:
                rr = 1.0 / rank
                break
        reciprocal_ranks.append(rr)

        # Recall@10: did we retrieve any expected source in top 10?
        if expected & set(retrieved_sources):
            recall_hits += 1

    n = len(reciprocal_ranks)
    if n == 0:
        return {"mrr": 0.0, "recall_at_10": 0.0, "eval_size": 0, "method": strategy}

    mrr = round(sum(reciprocal_ranks) / n, 4)
    recall_at_10 = round(recall_hits / n, 4)

    return {
        "mrr": mrr,
        "recall_at_10": recall_at_10,
        "eval_size": n,
        "method": strategy,
    }


def run(payload, config):
    rebuild = payload.get("rebuild_eval", False)

    try:
        # Load or build eval dataset
        dataset = _load_dataset()
        if not dataset or rebuild:
            log.info("Building RAG eval dataset (rebuild=%s, existing=%d)",
                     rebuild, len(dataset))
            dataset = _build_eval_dataset()
            if dataset:
                _save_dataset(dataset)
            else:
                return {
                    "status": "no_data",
                    "message": "No suitable DONE tasks found to build eval dataset",
                    "metrics": {"mrr": 0.0, "recall_at_10": 0.0, "eval_size": 0, "method": "bm25"},
                }

        metrics = _compute_metrics(dataset, strategy="bm25")
        return {"status": "ok", "metrics": metrics}

    except Exception:
        log.warning("rag_eval failed", exc_info=True)
        return {
            "status": "error",
            "metrics": {"mrr": 0.0, "recall_at_10": 0.0, "eval_size": 0, "method": "bm25"},
        }
