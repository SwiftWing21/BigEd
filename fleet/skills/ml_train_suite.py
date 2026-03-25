"""ML training suite — consolidates embedding, reranker, router, and scaler training.

All four trainers share identical ExperimentFramework lifecycle boilerplate
(propose -> pending-approval check -> run -> eval -> return).  Only the
train_fn / eval_fn bodies differ.  This module extracts that shared logic
into _experiment_lifecycle() and exposes each trainer as a sub-action.

Actions (payload["action"]):
  embedding  — fine-tune SentenceTransformer on fleet task pairs (default)
  reranker   — fine-tune CrossEncoder reranker on fleet scored pairs
  router     — retrain the ML task-routing model (sklearn)
  scaler     — train the predictive agent-scaler regression model

Payload keys by action:
  embedding / reranker:
    model_name  str  base model identifier
    epochs      int  training epochs (default: 3, clamped 1-20)
    batch_size  int  batch size (default: 16, clamped 4-128)
  router / scaler:
    (no required keys — config comes from fleet.toml)

Returns:
  {"status": "ok",          "experiment_id": ..., "experiment": ...}  on success
  {"status": "pending_approval", "experiment_id": ..., "message": ...}  when awaiting HITL
  {"status": "error",       "message": ...}  on failure
"""
import json
import logging
import sys
import time
from pathlib import Path

SKILL_NAME = "ml_train_suite"
DESCRIPTION = "Train ML models: embeddings, reranker, router, scaler"
VERSION = "1.0.0"
REQUIRES_NETWORK = False  # each sub-action declares its own network need via REQUIRES_NETWORK
COMPLEXITY = "complex"
SUITE = "ml"

FLEET_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(FLEET_DIR))
log = logging.getLogger(SKILL_NAME)


# ---------------------------------------------------------------------------
# Shared lifecycle helper
# ---------------------------------------------------------------------------

def _experiment_lifecycle(agent, exp_type, hypothesis, config, train_fn, eval_fn,
                           exp_config=None):
    """Propose an experiment, check for HITL approval, then run and evaluate.

    Args:
        agent:      Agent name to attribute the experiment to.
        exp_type:   Experiment type string (e.g. "embedding_train").
        hypothesis: Human-readable hypothesis string.
        config:     Experiment config dict passed to fw.propose().
        train_fn:   Callable(exp_config) -> artifact_path | None.
        eval_fn:    Callable(exp_config) -> metrics dict.
        exp_config: Optional extra config dict (merged into propose config).

    Returns:
        Result dict: {"status": ..., "experiment_id": ..., ...}
    """
    from experiment import ExperimentFramework

    propose_cfg = dict(exp_config or {})
    propose_cfg.update(config)

    fw = ExperimentFramework()

    exp_id = fw.propose(
        agent=agent,
        experiment_type=exp_type,
        hypothesis=hypothesis,
        config=propose_cfg if propose_cfg else None,
    )

    exp = fw.get(exp_id)
    if exp and exp["status"] not in ("APPROVED",):
        return {
            "status": "pending_approval",
            "experiment_id": exp_id,
            "message": f"Experiment {exp_id} is {exp['status']} — needs HITL approval",
        }

    result = fw.run(exp_id, train_fn, eval_fn)
    return {
        "status": "ok",
        "experiment_id": exp_id,
        "experiment": result,
    }


# ---------------------------------------------------------------------------
# Data builders
# ---------------------------------------------------------------------------

def _build_training_pairs() -> list:
    """Return (query, positive_passage) pairs from DONE tasks + RAG results."""
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


def _build_scored_pairs() -> list:
    """Return (query, passage, score) dicts from DONE tasks + RAG results.

    Scoring: top result 1.0, then 0.7 / 0.4 / 0.2 / 0.1 decay.
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


# ---------------------------------------------------------------------------
# Sub-action handlers
# ---------------------------------------------------------------------------

def _train_embedding(payload, config):
    """Fine-tune a SentenceTransformer on fleet task pairs via ExperimentFramework."""
    try:
        model_name = payload.get("model_name", "all-MiniLM-L6-v2")
        epochs = max(1, min(20, int(payload.get("epochs", 3))))
        batch_size = max(4, min(128, int(payload.get("batch_size", 16))))

        models_dir = FLEET_DIR / "models" / "embeddings"

        def train_fn(exp_config):
            try:
                from sentence_transformers import SentenceTransformer, InputExample, losses
                from torch.utils.data import DataLoader
            except ImportError:
                log.warning("sentence-transformers not installed — skipping embedding training")
                return None

            pairs = _build_training_pairs()
            if len(pairs) < 5:
                log.warning("Too few training pairs (%d) — need at least 5", len(pairs))
                return None

            log.info("Training embedding model %s with %d pairs, %d epochs",
                     model_name, len(pairs), epochs)

            model = SentenceTransformer(model_name)
            examples = [InputExample(texts=[q, p]) for q, p in pairs]
            dataloader = DataLoader(examples, shuffle=True, batch_size=batch_size)
            loss = losses.MultipleNegativesRankingLoss(model)

            model.fit(
                train_objectives=[(dataloader, loss)],
                epochs=epochs,
                warmup_steps=min(100, len(dataloader) // 2),
                show_progress_bar=False,
            )

            timestamp = int(time.time())
            output_path = models_dir / f"fleet_embed_{timestamp}"
            output_path.mkdir(parents=True, exist_ok=True)
            model.save(str(output_path))
            log.info("Saved embedding model to %s", output_path)
            return str(output_path)

        def eval_fn(exp_config):
            from skills.rag_eval import run as rag_eval_run
            result = rag_eval_run({"rebuild_eval": False}, config)
            return result.get("metrics", {})

        return _experiment_lifecycle(
            agent="ds_rag",
            exp_type="embedding_train",
            hypothesis=f"Fine-tuning {model_name} on fleet task pairs improves RAG retrieval",
            config={"model_name": model_name, "epochs": epochs, "batch_size": batch_size},
            train_fn=train_fn,
            eval_fn=eval_fn,
        )

    except Exception:
        log.warning("embedding training failed", exc_info=True)
        return {"status": "error", "message": "embedding training failed — see logs"}


def _train_reranker(payload, config):
    """Fine-tune a CrossEncoder reranker on fleet scored pairs via ExperimentFramework."""
    try:
        model_name = payload.get("model_name", "cross-encoder/ms-marco-MiniLM-L-6-v2")
        epochs = max(1, min(20, int(payload.get("epochs", 3))))
        batch_size = max(4, min(128, int(payload.get("batch_size", 16))))

        models_dir = FLEET_DIR / "models" / "reranker"

        def train_fn(exp_config):
            try:
                from sentence_transformers import CrossEncoder, InputExample
            except ImportError:
                log.warning("sentence-transformers not installed — skipping reranker training")
                return None

            scored = _build_scored_pairs()
            if len(scored) < 5:
                log.warning("Too few scored pairs (%d) — need at least 5", len(scored))
                return None

            log.info("Training reranker %s with %d scored pairs, %d epochs",
                     model_name, len(scored), epochs)

            model = CrossEncoder(model_name, num_labels=1)
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

            timestamp = int(time.time())
            output_path = models_dir / f"fleet_reranker_{timestamp}"
            output_path.mkdir(parents=True, exist_ok=True)
            model.save(str(output_path))
            log.info("Saved reranker model to %s", output_path)
            return str(output_path)

        def eval_fn(exp_config):
            from skills.rag_eval import run as rag_eval_run
            result = rag_eval_run({"rebuild_eval": False}, config)
            return result.get("metrics", {})

        return _experiment_lifecycle(
            agent="ds_rag",
            exp_type="reranker_train",
            hypothesis=f"Fine-tuning {model_name} on fleet scored pairs improves reranking",
            config={"model_name": model_name, "epochs": epochs, "batch_size": batch_size},
            train_fn=train_fn,
            eval_fn=eval_fn,
        )

    except Exception:
        log.warning("reranker training failed", exc_info=True)
        return {"status": "error", "message": "reranker training failed — see logs"}


def _train_router(payload, config):
    """Retrain the ML task-routing model via ExperimentFramework."""
    try:
        import ml_router

        def train_fn(exp_config):
            result = ml_router.train_routing_model()
            if result.get("error"):
                log.warning("Router training returned error: %s", result["error"])
                return None
            return str(FLEET_DIR / "data" / "routing_model.pkl")

        def eval_fn(exp_config):
            status = ml_router.get_model_status()
            return {
                "accuracy": status.get("accuracy", 0),
                "sample_count": status.get("sample_count", 0),
                "known_skills": len(status.get("known_skills", [])),
                "known_agents": len(status.get("known_agents", [])),
            }

        return _experiment_lifecycle(
            agent="ds_fleet",
            exp_type="router_retrain",
            hypothesis="Retraining on recent task history improves routing accuracy",
            config={},
            train_fn=train_fn,
            eval_fn=eval_fn,
        )

    except Exception:
        log.warning("router retrain failed", exc_info=True)
        return {"status": "error", "message": "router retrain failed — see logs"}


def _train_scaler(payload, config):
    """Train the predictive agent-scaler regression model via ExperimentFramework."""
    try:
        import predictive_scaler

        def train_fn(exp_config):
            result = predictive_scaler.train_scaler_model()
            if not result.get("ok"):
                log.warning("Scaler training returned error: %s", result.get("error"))
                return None
            return str(predictive_scaler.MODEL_PATH)

        def eval_fn(exp_config):
            try:
                summary = predictive_scaler.get_prediction_summary()
                return {
                    "model_available": summary.get("model_available", False),
                    "method": summary.get("method", "unknown"),
                    "r2_score": summary.get("r2_score", 0) or 0,
                    "history_count": summary.get("history_count", 0),
                }
            except Exception:
                log.warning("scaler eval failed", exc_info=True)
                return {
                    "model_available": False,
                    "method": "unknown",
                    "r2_score": 0,
                    "history_count": 0,
                }

        return _experiment_lifecycle(
            agent="ds_fleet",
            exp_type="scaler_train",
            hypothesis="Training on scaling history improves agent count predictions",
            config={},
            train_fn=train_fn,
            eval_fn=eval_fn,
        )

    except Exception:
        log.warning("scaler training failed", exc_info=True)
        return {"status": "error", "message": "scaler training failed — see logs"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(payload: dict, config: dict) -> dict:
    from skills._dispatch import dispatch_action
    return dispatch_action(payload, config, {
        "embedding": _train_embedding,
        "reranker": _train_reranker,
        "router": _train_router,
        "scaler": _train_scaler,
    }, default="embedding")
