"""Train the predictive scaler model via ExperimentFramework.

Proposes a 'scaler_train' experiment for agent 'ds_fleet', trains the
regression model through predictive_scaler.train_scaler_model(), and evaluates
via predictive_scaler.get_prediction_summary().

Payload:
  (none required — uses defaults from fleet.toml [scaling])

Returns:
  Experiment result dict from ExperimentFramework, or pending_approval status.
"""
import logging
from pathlib import Path

SKILL_NAME = "scaler_train"
DESCRIPTION = "Train the predictive agent-scaler model via ExperimentFramework"
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
log = logging.getLogger(__name__)


def run(payload, config):
    try:
        from experiment import ExperimentFramework
        import predictive_scaler

        fw = ExperimentFramework()

        # Propose experiment
        exp_id = fw.propose(
            agent="ds_fleet",
            experiment_type="scaler_train",
            hypothesis="Training on scaling history improves agent count predictions",
        )

        exp = fw.get(exp_id)
        if exp and exp["status"] not in ("APPROVED",):
            return {
                "status": "pending_approval",
                "experiment_id": exp_id,
                "message": f"Experiment {exp_id} is {exp['status']} — needs HITL approval",
            }

        # Training function: train the scaler model, return model path
        def train_fn(exp_config):
            result = predictive_scaler.train_scaler_model()
            if not result.get("ok"):
                log.warning("Scaler training returned error: %s", result.get("error"))
                return None
            model_path = str(predictive_scaler.MODEL_PATH)
            return model_path

        # Eval function: get prediction summary metrics
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

        # Run full experiment lifecycle
        result = fw.run(exp_id, train_fn, eval_fn)
        return {
            "status": "ok",
            "experiment_id": exp_id,
            "experiment": result,
        }

    except Exception:
        log.warning("scaler_train failed", exc_info=True)
        return {"status": "error", "message": "scaler training failed — see logs"}
