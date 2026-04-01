"""Autoresearch autonomous loop — Claude API-driven experiment proposals with local GPU training.

The brain (experiment design) runs on Claude API via providers._call_claude().
The body (training) runs locally on GPU via train.py (5-min fixed budget).

Usage via fleet:
    python lead_client.py task '{"type": "autoresearch_loop", "max_cycles": 20}'

Or direct:
    python fleet/skills/autoresearch_loop.py [--max-cycles 20] [--budget 2.00] [--profile stable]
"""
import csv
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

SKILL_NAME = "autoresearch_loop"
DESCRIPTION = "Autonomous Claude API-driven autoresearch loop (local GPU training, API experiment design)"
VERSION = "1.0.0"
COMPLEXITY = "high"
REQUIRES_NETWORK = True
SUITE = "ml"

log = logging.getLogger(__name__)

FLEET_DIR = Path(__file__).parent.parent
AUTORESEARCH_DIR = FLEET_DIR.parent / "autoresearch"
TRAIN_SCRIPT = AUTORESEARCH_DIR / "train.py"
RESULTS_TSV = AUTORESEARCH_DIR / "results.tsv"
RUN_LOG = AUTORESEARCH_DIR / "run.log"
TRAIN_PY = AUTORESEARCH_DIR / "train.py"

# Default config
DEFAULT_MAX_CYCLES = 20
DEFAULT_BUDGET_USD = 2.00
DEFAULT_PROFILE = "stable"
TRAIN_TIMEOUT_SECS = 420  # 7 min (5 min training + overhead)


def run(payload, config):
    """Fleet skill entry point."""
    max_cycles = payload.get("max_cycles", DEFAULT_MAX_CYCLES)
    budget_usd = payload.get("budget", DEFAULT_BUDGET_USD)
    profile = payload.get("profile", DEFAULT_PROFILE)

    if not TRAIN_SCRIPT.exists():
        return {"status": "skip", "error": f"train.py not found at {TRAIN_SCRIPT}"}

    # Lazy imports — prevents circular deps
    sys.path.insert(0, str(FLEET_DIR))
    try:
        import providers
    except Exception:
        log.warning("Failed to import providers", exc_info=True)
        return {"status": "error", "error": "providers module not available"}

    # Self-enable api_gate for this session only.
    # fleet.toml keeps gate OFF so fleet workers can't use it —
    # only this skill explicitly enables Claude for its own run.
    try:
        import api_gate
        api_gate.enable(
            budget=budget_usd,
            providers=["claude"],
            drain_mode="graceful",
        )
        log.info("API gate enabled: budget=$%.2f, providers=['claude']", budget_usd)
    except Exception:
        log.warning("Failed to enable api_gate", exc_info=True)

    results = []
    total_api_cost = 0.0
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 3  # halt after 3 crashes/errors in a row

    for cycle in range(1, max_cycles + 1):
        if total_api_cost >= budget_usd:
            log.info("Budget cap reached ($%.2f / $%.2f) after %d cycles",
                     total_api_cost, budget_usd, cycle - 1)
            break

        log.info("=== Cycle %d/%d (spent $%.3f / $%.2f) ===",
                 cycle, max_cycles, total_api_cost, budget_usd)

        try:
            cycle_result = _run_cycle(cycle, profile, providers)
            results.append(cycle_result)

            # Cost estimate: ~3K input + ~3K output per Sonnet call
            total_api_cost += cycle_result.get("est_api_cost", 0.06)

            if cycle_result.get("status") in ("error", "crash"):
                consecutive_failures += 1
                log.warning("Cycle %d failed (%d/%d consecutive): %s",
                            cycle, consecutive_failures, MAX_CONSECUTIVE_FAILURES,
                            cycle_result.get("error", cycle_result.get("status")))
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    log.error("Halting: %d consecutive failures — fix the issue before retrying",
                              MAX_CONSECUTIVE_FAILURES)
                    break
                # Continue unless it's a fatal error
                if cycle_result.get("fatal"):
                    break
            else:
                consecutive_failures = 0  # reset on success
        except Exception:
            log.warning("Cycle %d failed unexpectedly", cycle, exc_info=True)
            results.append({"cycle": cycle, "status": "error", "error": "unexpected failure"})

    return {
        "status": "ok",
        "cycles_completed": len(results),
        "total_est_cost": round(total_api_cost, 3),
        "results": results,
        "best": _get_best_from_results(),
    }


def _run_cycle(cycle: int, profile: str, providers_mod):
    """Single experiment cycle: propose → apply → train → evaluate → record."""

    # 1. Read current state
    history = _read_results_tsv()
    train_py_code = TRAIN_PY.read_text(encoding="utf-8", errors="ignore")

    # 2. Ask Claude for next experiment proposal
    proposal = _get_proposal(history, train_py_code, providers_mod)
    if not proposal:
        return {"cycle": cycle, "status": "error", "error": "empty proposal from API"}

    # 3. Parse the proposal — extract code diff and description
    code_change, description = _parse_proposal(proposal)
    if not code_change:
        return {"cycle": cycle, "status": "error",
                "error": "could not parse code from proposal", "raw": proposal[:500]}

    # 4. Save current train.py as backup
    backup = train_py_code

    # 5. Apply the proposed code change
    try:
        TRAIN_PY.write_text(code_change, encoding="utf-8")
    except Exception:
        log.warning("Failed to write proposed train.py", exc_info=True)
        return {"cycle": cycle, "status": "error", "error": "failed to write train.py"}

    # 6. Git commit the change
    _git_commit(description)

    # 7. Run training (5-min budget, local GPU)
    train_result = _run_training(profile)

    # 8. Parse results from run.log
    val_bpb, memory_gb = _parse_run_log()

    # 9. Decide keep/discard
    current_best = _get_best_bpb(history)

    if train_result["crashed"]:
        status = "crash"
        # Revert
        TRAIN_PY.write_text(backup, encoding="utf-8")
        _git_reset()
    elif val_bpb is not None and current_best is not None and val_bpb < current_best:
        status = "keep"
        log.info("IMPROVED: %.6f -> %.6f (%s)", current_best, val_bpb, description)
    else:
        status = "discard"
        TRAIN_PY.write_text(backup, encoding="utf-8")
        _git_reset()

    # 10. Record to results.tsv
    commit_hash = _git_short_hash()
    _append_result(commit_hash, val_bpb or 0.0, memory_gb or 0.0, status, description)

    return {
        "cycle": cycle,
        "status": status,
        "val_bpb": val_bpb,
        "memory_gb": memory_gb,
        "description": description,
        "est_api_cost": 0.06,  # ~3K in + ~3K out on Sonnet (full train.py round-trip)
    }


# ── Claude API proposal ─────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an autonomous ML researcher optimizing a small GPT model.
Your goal: minimize val_bpb (validation bits-per-byte) on a fixed 5-minute training budget.

Hardware: EVGA RTX 3080 Ti FTW3 Ultra (12GB GDDR6X, 10240 CUDA cores, 1800MHz boost).
Safe VRAM cap: ~10GB for training (leave headroom).

Rules:
- You ONLY modify train.py. prepare.py is read-only.
- No new packages. Only what's in pyproject.toml.
- DEPTH=7 causes OOM at default settings. Be careful with model size.
- Simpler is better: a tiny improvement with ugly complexity = not worth it.
- Removing code for equal/better results = great outcome.
- Think about interaction effects between hyperparameters.
- Consider: architecture (depth, heads, attention), optimizer (Muon/AdamW params),
  regularization (weight decay, dropout), batch size, learning rate schedules.

Output format — you MUST respond with exactly:
DESCRIPTION: <one-line description of what you're trying>
```python
<complete train.py file content>
```

No other text. The python block must be the COMPLETE train.py file, not a diff."""


def _get_proposal(history: list[dict], train_py_code: str, providers_mod) -> str:
    """Call Claude API to get next experiment proposal."""
    history_text = _format_history(history)

    user_prompt = f"""Current results.tsv (most recent last):
{history_text}

Current train.py:
```python
{train_py_code}
```

Analyze the experiment history. Propose the SINGLE most promising next experiment.
Consider what worked, what didn't, and what hasn't been tried yet.
Return the complete modified train.py with your change."""

    try:
        models = {"complex": "claude-sonnet-4-6"}
        response = providers_mod._call_claude(
            system=SYSTEM_PROMPT,
            user=user_prompt,
            models=models,
            max_tokens=4096,
            cache_system=True,
            skill_name=SKILL_NAME,
            purpose="autoresearch",
        )
        return response
    except Exception:
        log.warning("Claude API call failed", exc_info=True)
        return None


def _format_history(history: list[dict]) -> str:
    """Format results.tsv rows into a readable table for the prompt."""
    if not history:
        return "(no experiments yet — this will be the first run after baseline)"

    lines = ["commit\tval_bpb\tmemory_gb\tstatus\tdescription"]
    for r in history:
        lines.append(
            f"{r.get('commit', '?')}\t{r.get('val_bpb', 0)}\t"
            f"{r.get('memory_gb', 0)}\t{r.get('status', '?')}\t"
            f"{r.get('description', '')}"
        )
    return "\n".join(lines)


def _parse_proposal(proposal: str) -> tuple:
    """Extract code and description from Claude's response."""
    description = "Claude API proposal"

    # Extract DESCRIPTION line
    for line in proposal.split("\n"):
        if line.strip().startswith("DESCRIPTION:"):
            description = line.split("DESCRIPTION:", 1)[1].strip()
            break

    # Extract python code block
    code = None
    if "```python" in proposal:
        parts = proposal.split("```python", 1)
        if len(parts) > 1:
            code_part = parts[1]
            if "```" in code_part:
                code = code_part.split("```", 1)[0].strip()
            else:
                code = code_part.strip()

    # Sanity check — must contain key imports
    if code and "from prepare import" not in code:
        log.warning("Proposed code missing 'from prepare import' — rejecting")
        return None, description

    return code, description


# ── Training subprocess ──────────────────────────────────────────────────────

def _run_training(profile: str) -> dict:
    """Run train.py locally on GPU. Returns dict with crashed flag."""
    env = os.environ.copy()

    # Use train_profile.py if available, else raw train.py
    train_profile = AUTORESEARCH_DIR / "train_profile.py"
    if train_profile.exists():
        cmd = [sys.executable, str(train_profile), "--profile", profile]
    else:
        cmd = [sys.executable, str(TRAIN_SCRIPT)]

    log.info("Starting training: %s", " ".join(cmd))

    try:
        with open(RUN_LOG, "w") as log_file:
            proc = subprocess.run(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                timeout=TRAIN_TIMEOUT_SECS,
                cwd=str(AUTORESEARCH_DIR),
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        crashed = proc.returncode != 0
    except subprocess.TimeoutExpired:
        log.warning("Training timed out after %ds", TRAIN_TIMEOUT_SECS)
        crashed = True
    except Exception:
        log.warning("Training subprocess error", exc_info=True)
        crashed = True

    return {"crashed": crashed}


def _parse_run_log() -> tuple:
    """Extract val_bpb and peak_vram_mb from run.log."""
    val_bpb = None
    memory_gb = None

    if not RUN_LOG.exists():
        return None, None

    try:
        content = RUN_LOG.read_text(encoding="utf-8", errors="ignore")
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("val_bpb:"):
                val_bpb = float(line.split(":")[1].strip())
            elif line.startswith("peak_vram_mb:"):
                mb = float(line.split(":")[1].strip())
                memory_gb = round(mb / 1024, 1)
    except Exception:
        log.warning("Failed to parse run.log", exc_info=True)

    return val_bpb, memory_gb


# ── Results tracking ─────────────────────────────────────────────────────────

def _read_results_tsv() -> list[dict]:
    """Read results.tsv and return list of dicts."""
    if not RESULTS_TSV.exists():
        return []

    rows = []
    try:
        with open(RESULTS_TSV, "r", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                parsed = {}
                for k, v in row.items():
                    if v is None:
                        parsed[k] = None
                        continue
                    v = v.strip()
                    try:
                        parsed[k] = float(v)
                    except ValueError:
                        parsed[k] = v
                rows.append(parsed)
    except Exception:
        log.warning("Failed to read results.tsv", exc_info=True)
    return rows


def _get_best_bpb(history: list[dict]) -> float | None:
    """Get current best val_bpb from history."""
    scored = [r["val_bpb"] for r in history
              if isinstance(r.get("val_bpb"), (int, float)) and r["val_bpb"] > 0]
    return min(scored) if scored else None


def _get_best_from_results() -> dict | None:
    """Get the overall best result."""
    history = _read_results_tsv()
    scored = [r for r in history
              if isinstance(r.get("val_bpb"), (int, float)) and r["val_bpb"] > 0]
    if not scored:
        return None
    return min(scored, key=lambda r: r["val_bpb"])


def _append_result(commit: str, val_bpb: float, memory_gb: float,
                   status: str, description: str):
    """Append a row to results.tsv."""
    try:
        with open(RESULTS_TSV, "a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerow([commit, f"{val_bpb:.6f}", f"{memory_gb:.1f}",
                             status, description])
    except Exception:
        log.warning("Failed to write to results.tsv", exc_info=True)


# ── Git helpers ──────────────────────────────────────────────────────────────

def _git_commit(description: str):
    """Commit current train.py changes."""
    try:
        subprocess.run(
            ["git", "add", "train.py"],
            cwd=str(AUTORESEARCH_DIR),
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        subprocess.run(
            ["git", "commit", "-m", f"exp: {description}"],
            cwd=str(AUTORESEARCH_DIR),
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        log.warning("Git commit failed", exc_info=True)


def _git_reset():
    """Reset last commit (discard experiment)."""
    try:
        subprocess.run(
            ["git", "reset", "--hard", "HEAD~1"],
            cwd=str(AUTORESEARCH_DIR),
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        log.warning("Git reset failed", exc_info=True)


def _git_short_hash() -> str:
    """Get current short commit hash."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(AUTORESEARCH_DIR),
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ── CLI entry point ──────────────────────────────────────────────────────────

def _cli():
    """Direct CLI invocation (outside fleet)."""
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Autoresearch autonomous loop (Claude API)")
    parser.add_argument("--max-cycles", type=int, default=DEFAULT_MAX_CYCLES,
                        help=f"Max experiment cycles (default: {DEFAULT_MAX_CYCLES})")
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET_USD,
                        help=f"API budget cap in USD (default: ${DEFAULT_BUDGET_USD:.2f})")
    parser.add_argument("--profile", default=DEFAULT_PROFILE,
                        help=f"Training profile (default: {DEFAULT_PROFILE})")
    args = parser.parse_args()

    # Ensure fleet dir is on path for providers import
    sys.path.insert(0, str(FLEET_DIR))

    result = run(
        payload={
            "max_cycles": args.max_cycles,
            "budget": args.budget,
            "profile": args.profile,
        },
        config={},
    )

    print(f"\n{'='*60}")
    print(f"  Autoresearch Loop Complete")
    print(f"  Cycles: {result.get('cycles_completed', 0)}")
    print(f"  Est. API cost: ${result.get('total_est_cost', 0):.3f}")
    best = result.get("best")
    if best:
        print(f"  Best val_bpb: {best.get('val_bpb', '?')}")
        print(f"  Best config: {best.get('description', '?')}")
    print(f"{'='*60}")


if __name__ == "__main__":
    _cli()
