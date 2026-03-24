"""Reranker model training skill — fine-tunes a CrossEncoder for RAG reranking.

Uses the ExperimentFramework for structured lifecycle management:
PROPOSED -> APPROVED -> RUNNING -> EVALUATING -> DEPLOYED | ROLLED_BACK

Builds scored pairs from task queries + RAG results (top result = 1.0,
others decay), trains a CrossEncoder, and evaluates via rag_eval metrics.

Payload:
  model_name    str   base model (default: cross-encoder/ms-marco-MiniLM-L-6-v2)
  epochs        int   training epochs (default: 3)
  batch_size    int   batch size (default: 16)

Returns:
  Experiment result dict from ExperimentFramework
"""
import json
import logging
import sys
import time
from pathlib import Path

SKILL_NAME = "reranker_train"
DESCRIPTION = "Fine-tune a CrossEncoder reranker on fleet task data via ExperimentFramework"
REQUIRES_NETWORK = True

FLEET_DIR = Path(__file__).parent.parent
MODELS_DIR = FLEET_DIR / "models" / "reranker"

sys.path.insert(0, str(FLEET_DIR))
log = logging.getLogger(__name__)


def _build_scored_pairs() -> list[dict]:
    """Build (query, passage, score) triples from DONE tasks + RAG results.

    Scoring: top result gets 1.0, second gets 0.7, third 0.4, rest 0.1.
    This creates a relevance gradient for the CrossEncoder to learn.
    """
    import db
    from rag import RAGIndex

    SCORE_DECAY = [1.0, 0.7, 0.4, 0.2, 0.1]
    idx = RAGIndex()
    scored_pairs = []

    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT payload_json FROM tasks
               WHERE status = 'DONE' AND payload_json IS NOT NULL
               ORDER BY id DESC LIMIT 300"""
        ).fetchall()

    for row in rows:
        try:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        except Exception:
            continue

        query = (
            payload.get("query")
            or payload.get("prompt")
            or payload.get("description")
            or payload.get("text", "")
        )
        if not query or not isinstance(query, str) or len(query.strip()) < 10:
            continue

        try:
            results = idx.search(query.strip(), limit=5)
        except Exception:
            continue

        for rank, r in enumerate(results):
            text = r.get("text", "")
            if text and len(text) > 20:
                score = SCORE_DECAY[rank] if rank < len(SCORE_DECAY) else 0.1
                scored_pairs.append({
                    "query": query.strip(),
                    "passage": text,
                    "score": score,
                })

    return scored_pairs


def run(payload, config):
    try:
        from experiment import ExperimentFramework

        model_name = payload.get("model_name", "cross-encoder/ms-marco-MiniLM-L-6-v2")
        epochs = max(1, min(20, int(payload.get("epochs", 3))))
        batch_size = max(4, min(128, int(payload.get("batch_size", 16))))

        fw = ExperimentFramework()

        # Propose experiment
        exp_id = fw.propose(
            agent="ds_rag",
            experiment_type="reranker_train",
            hypothesis=f"Fine-tuning {model_name} on fleet scored pairs improves reranking",
            config={
                "model_name": model_name,
                "epochs": epochs,
                "batch_size": batch_size,
            },
        )

        exp = fw.get(exp_id)
        if exp and exp["status"] not in ("APPROVED",):
            return {
                "status": "pending_approval",
                "experiment_id": exp_id,
                "message": f"Experiment {exp_id} is {exp['status']} — needs HITL approval",
            }

        # Define training function
        def train_fn(exp_config):
            try:
                from sentence_transformers import CrossEncoder, InputExample
            except ImportError:
                log.warning("sentence-transformers not installed — skipping training")
                return None

            scored = _build_scored_pairs()
            if len(scored) < 5:
                log.warning("Too few scored pairs (%d) — need at least 5", len(scored))
                return None

            log.info("Training reranker %s with %d scored pairs, %d epochs",
                     model_name, len(scored), epochs)

            model = CrossEncoder(model_name, num_labels=1)

            # Build training examples
            examples = [
                InputExample(texts=[sp["query"], sp["passage"]], label=sp["score"])
                for sp in scored
            ]

            model.fit(
                train_dataloader=examples,
                epochs=epochs,
                batch_size=batch_size,
                warmup_steps=min(100, len(examples) // (batch_size * 2)),
                show_progress_bar=False,
            )

            # Save model
            timestamp = int(time.time())
            output_path = MODELS_DIR / f"fleet_reranker_{timestamp}"
            output_path.mkdir(parents=True, exist_ok=True)
            model.save(str(output_path))
            log.info("Saved reranker model to %s", output_path)
            return str(output_path)

        # Define eval function using rag_eval
        def eval_fn(exp_config):
            from skills.rag_eval import run as rag_eval_run
            result = rag_eval_run({"rebuild_eval": False}, config)
            return result.get("metrics", {})

        # Run full experiment lifecycle
        result = fw.run(exp_id, train_fn, eval_fn)
        return {
            "status": "ok",
            "experiment_id": exp_id,
            "experiment": result,
        }

    except Exception:
        log.warning("reranker_train failed", exc_info=True)
        return {"status": "error", "message": "reranker training failed — see logs"}
