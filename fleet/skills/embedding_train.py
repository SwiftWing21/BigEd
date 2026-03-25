"""Embedding model training skill — fine-tunes a SentenceTransformer on fleet task data.

Uses the ExperimentFramework for structured lifecycle management:
PROPOSED -> APPROVED -> RUNNING -> EVALUATING -> DEPLOYED | ROLLED_BACK

Builds training pairs from task queries + RAG results, trains with
MultipleNegativesRankingLoss, and evaluates via rag_eval metrics.

Payload:
  model_name    str   base model (default: all-MiniLM-L6-v2)
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

SKILL_NAME = "embedding_train"
DESCRIPTION = "Fine-tune SentenceTransformer embeddings on fleet task data via ExperimentFramework"
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = True

FLEET_DIR = Path(__file__).parent.parent
MODELS_DIR = FLEET_DIR / "models" / "embeddings"

sys.path.insert(0, str(FLEET_DIR))
log = logging.getLogger(__name__)


def _build_training_pairs() -> list[tuple]:
    """Build (query, positive_passage) pairs from DONE tasks + RAG results.

    Each pair consists of a task query and a relevant RAG chunk that was
    retrieved for that query — suitable for contrastive learning.
    """
    import db
    from rag import RAGIndex

    idx = RAGIndex()
    pairs = []

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
            results = idx.search(query.strip(), limit=3)
        except Exception:
            continue

        for r in results:
            text = r.get("text", "")
            if text and len(text) > 20:
                pairs.append((query.strip(), text))

    return pairs


def run(payload, config):
    try:
        from experiment import ExperimentFramework

        model_name = payload.get("model_name", "all-MiniLM-L6-v2")
        epochs = max(1, min(20, int(payload.get("epochs", 3))))
        batch_size = max(4, min(128, int(payload.get("batch_size", 16))))

        fw = ExperimentFramework()

        # Propose experiment
        exp_id = fw.propose(
            agent="ds_rag",
            experiment_type="embedding_train",
            hypothesis=f"Fine-tuning {model_name} on fleet task pairs improves RAG retrieval",
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
                from sentence_transformers import SentenceTransformer, InputExample, losses
                from torch.utils.data import DataLoader
            except ImportError:
                log.warning("sentence-transformers not installed — skipping training")
                return None

            pairs = _build_training_pairs()
            if len(pairs) < 5:
                log.warning("Too few training pairs (%d) — need at least 5", len(pairs))
                return None

            log.info("Training embedding model %s with %d pairs, %d epochs",
                     model_name, len(pairs), epochs)

            model = SentenceTransformer(model_name)

            # Build InputExamples for contrastive learning
            examples = [InputExample(texts=[q, p]) for q, p in pairs]
            dataloader = DataLoader(examples, shuffle=True, batch_size=batch_size)
            loss = losses.MultipleNegativesRankingLoss(model)

            model.fit(
                train_objectives=[(dataloader, loss)],
                epochs=epochs,
                warmup_steps=min(100, len(dataloader) // 2),
                show_progress_bar=False,
            )

            # Save model
            timestamp = int(time.time())
            output_path = MODELS_DIR / f"fleet_embed_{timestamp}"
            output_path.mkdir(parents=True, exist_ok=True)
            model.save(str(output_path))
            log.info("Saved embedding model to %s", output_path)
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
        log.warning("embedding_train failed", exc_info=True)
        return {"status": "error", "message": "embedding training failed — see logs"}
