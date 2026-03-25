"""Autoresearch trial — proposes and runs an ML experiment via the ExperimentFramework."""
import logging
import os
import subprocess
import sys
from pathlib import Path

SKILL_NAME = "autoresearch_trial"
DESCRIPTION = "Propose and run an autoresearch training trial via the ExperimentFramework"
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False
SUITE = "ml"

log = logging.getLogger(__name__)

AUTORESEARCH_DIR = Path(__file__).parent.parent.parent / "autoresearch"
TRAIN_SCRIPT = AUTORESEARCH_DIR / "train.py"


def run(payload, config):
    trial_config = payload.get("config", {})
    hypothesis = payload.get("hypothesis", "autoresearch trial run")
    agent = payload.get("agent", "ds_research")

    if not TRAIN_SCRIPT.exists():
        return {"status": "skip", "error": f"train.py not found at {TRAIN_SCRIPT}"}

    # Import ExperimentFramework lazily
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from experiment import ExperimentFramework
    except Exception:
        log.warning("Failed to import ExperimentFramework", exc_info=True)
        return {"status": "error", "error": "ExperimentFramework not available"}

    fw = ExperimentFramework()

    # Propose the experiment
    try:
        exp_id = fw.propose(
            agent=agent,
            experiment_type="autoresearch_trial",
            hypothesis=hypothesis,
            config=trial_config,
        )
    except Exception:
        log.warning("Failed to propose experiment", exc_info=True)
        return {"status": "error", "error": "Failed to propose experiment"}

    # Define train_fn
    def train_fn(cfg):
        return _run_training(cfg)

    # Define eval_fn — reads current best_bpb from results.tsv
    def eval_fn(cfg):
        return _evaluate()

    # Run the full lifecycle
    try:
        result = fw.run(exp_id, train_fn, eval_fn)
    except Exception:
        log.warning("Experiment %d failed during run", exp_id, exc_info=True)
        return {
            "status": "error",
            "experiment_id": exp_id,
            "error": "Experiment run failed",
        }

    return {
        "status": "ok",
        "experiment_id": exp_id,
        "experiment_status": result.get("status", "unknown") if result else "unknown",
        "result": result,
    }


def _run_training(cfg):
    """Spawn autoresearch/train.py as a subprocess with config as env vars."""
    env = os.environ.copy()

    # Pass config as AUTORESEARCH_<KEY> env vars
    for key, value in (cfg or {}).items():
        env_key = f"AUTORESEARCH_{key.upper()}"
        env[env_key] = str(value)

    try:
        proc = subprocess.run(
            [sys.executable, str(TRAIN_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=360,
            env=env,
            cwd=str(AUTORESEARCH_DIR),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        log.warning("Training timed out after 360s")
        raise
    except Exception:
        log.warning("Training subprocess failed", exc_info=True)
        raise

    if proc.returncode != 0:
        log.warning("Training exited with code %d: %s", proc.returncode, proc.stderr[:500])
        raise RuntimeError(f"Training failed (exit {proc.returncode}): {proc.stderr[:500]}")

    # Look for checkpoint directory in output
    checkpoint_dir = AUTORESEARCH_DIR / "checkpoints"
    if checkpoint_dir.exists():
        # Return the most recently modified checkpoint
        pts = sorted(checkpoint_dir.glob("*.pt"), key=lambda p: p.stat().st_mtime, reverse=True)
        if pts:
            return str(pts[0])

    return str(checkpoint_dir)


def _evaluate():
    """Call autoresearch_analyze to get current best_bpb as a metric."""
    try:
        from skills.autoresearch_analyze import run as analyze_run
        result = analyze_run({}, {})
        best_bpb = result.get("best_bpb")
        if best_bpb is not None:
            return {"best_bpb": best_bpb}
        return {}
    except Exception:
        log.warning("Evaluation via autoresearch_analyze failed", exc_info=True)
        return {}
