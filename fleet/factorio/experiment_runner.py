"""Experiment runner — autoresearch-style loop for Factorio AgentBrain optimization."""
import asyncio
import copy
import csv
import glob as glob_module
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from factorio.bridge_config import BridgeConfig, load_factorio_config
from factorio.experiment_scorer import compute_score

log = logging.getLogger("biged.factorio.experiment")

_DEFAULT_RESULTS_FILE = "fleet/factorio/experiment_results.tsv"
_DEFAULT_REPLAY_FILE = "fleet/factorio/replay_log.jsonl"

_TSV_FIELDS = [
    "experiment_id", "timestamp", "phase", "load_save", "prompt",
    "metric", "baseline", "delta", "status", "description",
]


def load_candidate(path: str) -> dict:
    """Load a candidate config TOML file."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    with open(path, "rb") as f:
        data = tomllib.load(f)

    return {
        "prompt": data.get("prompt", "baseline"),
        "load_save": data.get("load_save"),
        "phase_override": data.get("phase_override"),
        "start_lesson": data.get("start_lesson", 0),
        "params": data.get("params", {}),
    }


def build_experiment_config(base_config: BridgeConfig, candidate: dict) -> BridgeConfig:
    """Merge candidate overrides into a copy of the base config."""
    cfg = copy.deepcopy(base_config)
    cfg.prompt_template = candidate.get("prompt", "baseline")

    params = candidate.get("params", {})
    if "plan_size" in params:
        cfg.plan_max_actions = params["plan_size"]
    if "ollama_timeout" in params:
        cfg.ollama_timeout = params["ollama_timeout"]
    if "cooldown_after_failure" in params:
        cfg.ollama_cooldown_secs = params["cooldown_after_failure"]
    if "failure_threshold" in params:
        cfg.plan_invalidation_failures = params["failure_threshold"]
    if "idle_assembler_replan" in params:
        cfg.idle_assembler_replan = params["idle_assembler_replan"]
    if "temperature" in params:
        cfg.temperature = params["temperature"]
    if "top_p" in params:
        cfg.top_p = params["top_p"]

    return cfg


def append_result(tsv_path: str, experiment_id: str, phase: int,
                  load_save: str | None, prompt: str, metric: float,
                  baseline: float | None, delta: float | None,
                  status: str, description: str) -> None:
    """Append one row to experiment_results.tsv. Creates file with header if needed."""
    path = Path(tsv_path)
    write_header = not path.exists()

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        if write_header:
            writer.writerow(_TSV_FIELDS)
        writer.writerow([
            experiment_id,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            phase,
            load_save or "-",
            prompt,
            f"{metric:.4f}",
            f"{baseline:.4f}" if baseline is not None else "-",
            f"{delta:+.4f}" if delta is not None else "-",
            status,
            description,
        ])


def append_replay(jsonl_path: str, experiment_id: str, phase: int,
                  lesson: str, state: dict, plan: list,
                  actions_taken: int, actions_succeeded: int,
                  lesson_passed: bool) -> None:
    """Append one replay entry to replay_log.jsonl."""
    entry = {
        "ts": int(time.time()),
        "experiment_id": experiment_id,
        "phase": phase,
        "lesson": lesson,
        "state": state,
        "plan": plan,
        "actions_taken": actions_taken,
        "actions_succeeded": actions_succeeded,
        "lesson_passed": lesson_passed,
    }
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")


def load_baseline(tsv_path: str, phase: int) -> float | None:
    """Load the best 'keep' score for a phase from results.tsv."""
    path = Path(tsv_path)
    if not path.exists():
        return None

    best = None
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if row.get("status") != "keep":
                continue
            try:
                row_phase = int(row.get("phase", 0))
            except ValueError:
                continue
            if row_phase != phase:
                continue
            try:
                metric = float(row["metric"])
            except (ValueError, KeyError):
                continue
            if best is None or metric > best:
                best = metric

    return best


def generate_experiment_id(results_path: str) -> str:
    """Generate next experiment ID (exp_0001, exp_0002, ...)."""
    path = Path(results_path)
    if not path.exists():
        return "exp_0001"

    count = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("exp_"):
                count += 1

    return f"exp_{count + 1:04d}"


# --- Task 6: Async orchestration ---

async def run_single_experiment(
    candidate_path: str,
    base_config: BridgeConfig,
    budget_seconds: int = 600,
    results_file: str = _DEFAULT_RESULTS_FILE,
    replay_file: str = _DEFAULT_REPLAY_FILE,
    prompts_dir: str = "fleet/factorio/prompts",
) -> dict:
    """Run a single experiment: load candidate, create bridge, run budget, score.

    Returns dict with experiment_id, score, status, and baseline.
    """
    from factorio.bridge import FactorioBridge

    candidate = load_candidate(candidate_path)
    exp_config = build_experiment_config(base_config, candidate)
    phase = candidate.get("phase_override") or exp_config.current_phase
    exp_id = generate_experiment_id(results_file)

    log.info("=== Experiment %s: prompt=%s, phase=%d ===",
             exp_id, candidate["prompt"], phase)

    # Create bridge with experiment config
    bridge = FactorioBridge(exp_config)
    brain = bridge.brain

    # Load save if specified
    load_save = candidate.get("load_save")
    if load_save:
        log.info("Loading save: %s", load_save)
        try:
            await bridge.rcon.connect()
            await bridge.rcon.command(f"/load {load_save}")
            await asyncio.sleep(3)  # wait for save to load
        except Exception as e:
            log.warning("Failed to load save '%s': %s", load_save, e)
            append_result(results_file, exp_id, phase, load_save,
                          candidate["prompt"], 0.0, None, None, "error",
                          f"save load failed: {e}")
            return {"experiment_id": exp_id, "score": 0.0, "status": "error"}

    # Reset counters
    brain.reset_counters()

    # Run bridge with budget timeout
    try:
        bridge._running = True
        if not load_save:
            if not await bridge.connect_with_retry():
                raise ConnectionError("RCON connect failed")

        bridge_task = asyncio.create_task(
            _run_bridge_ticks(bridge, budget_seconds)
        )
        await asyncio.wait_for(bridge_task, timeout=budget_seconds + 30)
    except asyncio.TimeoutError:
        log.info("Budget expired for %s", exp_id)
    except Exception as e:
        log.warning("Experiment %s error: %s", exp_id, e)
        append_result(results_file, exp_id, phase, load_save,
                      candidate["prompt"], 0.0, None, None, "error", str(e))
        return {"experiment_id": exp_id, "score": 0.0, "status": "error"}
    finally:
        bridge.stop()

    # Score
    progress = brain.curriculum.get_progress()
    lessons_passed = progress.get("completed", 0)
    score = compute_score(
        phase=phase,
        lessons_passed=lessons_passed,
        total_actions=brain.total_actions,
        total_failures=brain.total_failures,
        throughput=0.0,  # TODO: extract from metrics when available
    )

    # Compare to baseline
    baseline = load_baseline(results_file, phase)
    if baseline is None:
        status = "keep"
        delta = None
    elif score > baseline:
        status = "keep"
        delta = score - baseline
    else:
        status = "discard"
        delta = score - baseline

    # Log results
    append_result(results_file, exp_id, phase, load_save,
                  candidate["prompt"], score, baseline, delta, status,
                  f"lessons={lessons_passed} actions={brain.total_actions} "
                  f"failures={brain.total_failures}")

    log.info("Experiment %s: score=%.4f baseline=%s status=%s",
             exp_id, score, baseline, status)

    return {
        "experiment_id": exp_id,
        "score": score,
        "baseline": baseline,
        "status": status,
        "lessons_passed": lessons_passed,
    }


async def _run_bridge_ticks(bridge, budget_seconds: int) -> None:
    """Run bridge tick loop for a fixed budget duration."""
    start = time.monotonic()
    while bridge._running and (time.monotonic() - start) < budget_seconds:
        try:
            await bridge.tick()
        except Exception as e:
            log.warning("Tick error: %s", e)
        interval = bridge.cadence.get_interval_secs()
        await asyncio.sleep(interval)


async def run_loop(
    candidates_dir: str = "fleet/factorio/candidates",
    base_config: BridgeConfig | None = None,
    budget_seconds: int = 600,
    max_experiments: int = 0,
    max_total_hours: float = 0,
    results_file: str = _DEFAULT_RESULTS_FILE,
    replay_file: str = _DEFAULT_REPLAY_FILE,
) -> None:
    """Main experiment loop — run candidates until stopped."""
    if base_config is None:
        base_config = load_factorio_config()

    log.info("Experiment loop starting — budget=%ds, candidates from %s",
             budget_seconds, candidates_dir)

    experiment_count = 0
    start_time = time.monotonic()

    # Find candidate TOML files
    candidate_files = sorted(glob_module.glob(f"{candidates_dir}/*.toml"))
    if not candidate_files:
        log.warning("No candidate files found in %s", candidates_dir)
        return

    candidate_idx = 0

    while True:
        # Check stop conditions
        if max_experiments > 0 and experiment_count >= max_experiments:
            log.info("Max experiments (%d) reached", max_experiments)
            break
        if max_total_hours > 0:
            elapsed_hours = (time.monotonic() - start_time) / 3600
            if elapsed_hours >= max_total_hours:
                log.info("Max total hours (%.1f) reached", max_total_hours)
                break

        # Cycle through candidates
        candidate_path = candidate_files[candidate_idx % len(candidate_files)]
        candidate_idx += 1

        try:
            result = await run_single_experiment(
                candidate_path=candidate_path,
                base_config=base_config,
                budget_seconds=budget_seconds,
                results_file=results_file,
                replay_file=replay_file,
            )
            experiment_count += 1
            log.info("Experiment %d complete: %s", experiment_count, result)
        except KeyboardInterrupt:
            log.info("Experiment loop interrupted")
            break
        except Exception as e:
            log.warning("Experiment failed: %s", e, exc_info=True)
            experiment_count += 1


def main():
    """CLI entry point for running experiments."""
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Factorio AgentBrain experiment runner")
    parser.add_argument("--candidates-dir", default="fleet/factorio/candidates",
                        help="Directory containing candidate TOML files")
    parser.add_argument("--budget", type=int, default=600,
                        help="Budget per experiment in seconds (default: 600)")
    parser.add_argument("--max-experiments", type=int, default=0,
                        help="Max experiments to run (0=unlimited)")
    parser.add_argument("--max-hours", type=float, default=0,
                        help="Max total hours to run (0=unlimited)")
    parser.add_argument("--single", type=str, default=None,
                        help="Run a single candidate TOML file and exit")
    args = parser.parse_args()

    if args.single:
        base_config = load_factorio_config()
        result = asyncio.run(run_single_experiment(
            candidate_path=args.single,
            base_config=base_config,
            budget_seconds=args.budget,
        ))
        print(json.dumps(result, indent=2))
    else:
        asyncio.run(run_loop(
            candidates_dir=args.candidates_dir,
            budget_seconds=args.budget,
            max_experiments=args.max_experiments,
            max_total_hours=args.max_hours,
        ))


if __name__ == "__main__":
    main()
