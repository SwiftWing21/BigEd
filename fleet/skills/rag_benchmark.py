"""RAG benchmark skill — compares retrieval strategies (BM25, vector, hybrid).

Runs all available retrieval strategies against the rag_eval dataset and
computes MRR + recall@10 for each, producing a side-by-side comparison.

Payload:
  rebuild_eval  bool  force rebuild of eval dataset first (default false)

Returns:
  {"status": "ok", "benchmark": {"bm25": {...}, "vector": {...}, "hybrid": {...}}}
"""
import logging
import sys
from pathlib import Path

SKILL_NAME = "rag_benchmark"
DESCRIPTION = "Benchmark all RAG retrieval strategies (BM25, vector, hybrid) with MRR and recall@10"
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False
SUITE = "rag"
TAGS = ['retrieval', 'benchmark']

FLEET_DIR = Path(__file__).parent.parent

sys.path.insert(0, str(FLEET_DIR))
log = logging.getLogger(__name__)

STRATEGIES = ["bm25", "vector", "hybrid"]


def run(payload, config):
    try:
        from skills.rag_eval import (
            _build_eval_dataset,
            _compute_metrics,
            _load_dataset,
            _save_dataset,
        )

        rebuild = payload.get("rebuild_eval", False)

        # Load or build eval dataset
        dataset = _load_dataset()
        if not dataset or rebuild:
            log.info("Building RAG eval dataset for benchmark (rebuild=%s)", rebuild)
            dataset = _build_eval_dataset()
            if dataset:
                _save_dataset(dataset)
            else:
                return {
                    "status": "no_data",
                    "message": "No suitable DONE tasks found to build eval dataset",
                    "benchmark": {
                        s: {"mrr": 0.0, "recall_at_10": 0.0, "eval_size": 0, "method": s}
                        for s in STRATEGIES
                    },
                }

        # Run each strategy
        benchmark = {}
        for strategy in STRATEGIES:
            try:
                metrics = _compute_metrics(dataset, strategy=strategy)
                benchmark[strategy] = metrics
            except Exception:
                log.warning("Benchmark strategy '%s' failed", strategy, exc_info=True)
                benchmark[strategy] = {
                    "mrr": 0.0,
                    "recall_at_10": 0.0,
                    "eval_size": 0,
                    "method": strategy,
                    "error": True,
                }

        # Determine best strategy
        best_strategy = max(benchmark, key=lambda s: benchmark[s].get("mrr", 0))
        best_mrr = benchmark[best_strategy].get("mrr", 0)

        return {
            "status": "ok",
            "benchmark": benchmark,
            "best_strategy": best_strategy,
            "best_mrr": best_mrr,
            "eval_size": len(dataset),
        }

    except Exception:
        log.warning("rag_benchmark failed", exc_info=True)
        return {
            "status": "error",
            "benchmark": {
                s: {"mrr": 0.0, "recall_at_10": 0.0, "eval_size": 0, "method": s}
                for s in STRATEGIES
            },
        }
