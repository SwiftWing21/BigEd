"""Retrain the ML routing model via ExperimentFramework.

Proposes a 'router_retrain' experiment for agent 'ds_fleet', trains the
sklearn routing model through ml_router.train_routing_model(), and evaluates
via ml_router.get_model_status().

Payload:
  (none required — uses defaults from fleet.toml [routing])

Returns:
  Experiment result dict from ExperimentFramework, or pending_approval status.
"""
import logging
from pathlib import Path

SKILL_NAME = "router_retrain"
DESCRIPTION = "Retrain the ML task-routing model via ExperimentFramework"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
log = logging.getLogger(__name__)


def run(payload, config):
    try:
        from experiment import ExperimentFramework
        import ml_router

        fw = ExperimentFramework()

        # Propose experiment
        exp_id = fw.propose(
            agent="ds_fleet",
            experiment_type="router_retrain",
            hypothesis="Retraining on recent task history improves routing accuracy",
        )

        exp = fw.get(exp_id)
        if exp and exp["status"] not in ("APPROVED",):
            return {
                "status": "pending_approval",
                "experiment_id": exp_id,
                "message": f"Experiment {exp_id} is {exp['status']} — needs HITL approval",
            }

        # Training function: retrain the routing model, return model path
        def train_fn(exp_config):
            result = ml_router.train_routing_model()
            if result.get("error"):
                log.warning("Router training returned error: %s", result["error"])
                return None
            # Return the model path as the artifact
            model_path = str(FLEET_DIR / "data" / "routing_model.pkl")
            return model_path

        # Eval function: get model status (accuracy + sample count)
        def eval_fn(exp_config):
            status = ml_router.get_model_status()
            return {
                "accuracy": status.get("accuracy", 0),
                "sample_count": status.get("sample_count", 0),
                "known_skills": len(status.get("known_skills", [])),
                "known_agents": len(status.get("known_agents", [])),
            }

        # Run full experiment lifecycle
        result = fw.run(exp_id, train_fn, eval_fn)
        return {
            "status": "ok",
            "experiment_id": exp_id,
            "experiment": result,
        }

    except Exception:
        log.warning("router_retrain failed", exc_info=True)
        return {"status": "error", "message": "router retrain failed — see logs"}
