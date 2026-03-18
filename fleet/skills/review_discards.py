"""
Watch results.tsv and trigger tiered reviews:
  - Every 10 new discards  → Ollama (qwen3:8b) pattern review
  - Every 100 total discards (cumulative) → Sonnet 4.6 deep review
  - Any new val_bpb improvement (keep) → Sonnet 4.6 improvement review
All results saved to knowledge/reports/ and returned to Ollama context.
"""
import csv
import json
import os
from datetime import date
from pathlib import Path

_RESULTS_CANDIDATES = [
    Path(r"C:\Users\max\Projects\Education\autoresearch\results.tsv"),      # Windows native path
    Path("/mnt/c/Users/max/Projects/Education/autoresearch/results.tsv"),  # Education dir (preferred)
    Path("/home/max/autoresearch/results.tsv"),                             # legacy WSL path
]
RESULTS_PATH = next((p for p in _RESULTS_CANDIDATES if p.parent.exists()), _RESULTS_CANDIDATES[0])
TRACKER_PATH = Path(__file__).parent.parent / "knowledge" / "discard_tracker.json"

OLLAMA_THRESHOLD = 10    # new discards since last ollama review
SONNET_MILESTONE = 100   # cumulative discards (100, 200, 300 ...)


def _load_tracker():
    if TRACKER_PATH.exists():
        return json.loads(TRACKER_PATH.read_text())
    return {
        "last_ollama_review_at": 0,       # total discards at last ollama review
        "last_sonnet_milestone": 0,        # last 100-multiple reviewed by sonnet
        "last_known_best_bpb": None,       # to detect improvements
        "reviews": [],
    }


def _save_tracker(tracker):
    TRACKER_PATH.write_text(json.dumps(tracker, indent=2))


def _build_context(rows, recent_discards, best):
    """Build a shared prompt context string from experiment data."""
    keeps = [r for r in rows if r.get("status") == "keep"]
    lines = [
        f"Training target: minimize val_bpb (bits per byte). Lower is better.",
        f"Total experiments: {len(rows)} — keeps: {len(keeps)}, discards/crashes: {len([r for r in rows if r['status'] in ('discard','crash')])}",
    ]
    if best:
        lines.append(f"Current best: val_bpb={best['val_bpb']} @ {best['commit']} — {best.get('description','')}")
    lines.append("\nAll keep results:")
    for r in keeps:
        lines.append(f"  + {r.get('commit','?')} bpb={r.get('val_bpb','?')} | {r.get('description','')}")
    lines.append("\nRecent failures being reviewed:")
    for r in recent_discards:
        lines.append(f"  - {r.get('commit','?')} bpb={r.get('val_bpb','?')} | {r.get('description','')}")
    return "\n".join(lines)


def _ollama_review(context, config):
    from skills.summarize import _ollama
    prompt = (
        "You are analyzing failed ML training experiments for a GPT model.\n\n"
        + context
        + "\n\nWhat patterns do you see in the failures? "
        "What should be tried next? Be concise — 3-5 bullet points."
    )
    return _ollama(prompt, config)


def _sonnet_review(context, trigger, config=None):
    """Call the configured complex model for deeper analysis. Returns text."""
    from skills._models import call_complex

    system = (
        "You are an expert ML researcher analyzing autonomous LLM pretraining experiments. "
        "The model is a GPT variant (~26M params) trained on a fixed 5-minute budget on an RTX 3080 Ti. "
        "Metric: val_bpb (bits per byte) — lower is better. "
        "Optimizer: Muon for matrices, AdamW for embeddings. "
        "Current architecture: DEPTH=6, model_dim=384, HEAD_DIM=128, WINDOW_PATTERN=SSSL."
    )

    user = (
        f"Trigger: {trigger}\n\n"
        + context
        + "\n\nProvide:\n"
        "1. What patterns explain the failures?\n"
        "2. What does the successful trajectory suggest?\n"
        "3. Top 3 concrete next experiments to try (specific hyperparameter values).\n"
        "4. Any architectural changes worth attempting given VRAM headroom (~3GB free).\n"
        "Be specific and actionable."
    )

    return call_complex(system, user, config or {}, max_tokens=1024, cache_system=True)


def _save_report(label, trigger, context, analysis, review_num):
    out_dir = Path(__file__).parent.parent / "knowledge" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{date.today()}_{label}_review_{review_num}.md"
    out_file.write_text(
        f"# {label.title()} Review #{review_num} — {date.today()}\n\n"
        f"**Trigger**: {trigger}\n\n"
        f"## Experiment Context\n```\n{context}\n```\n\n"
        f"## Analysis\n{analysis}\n"
    )
    return out_file


def run(payload, config):
    if not RESULTS_PATH.exists():
        return {"skipped": "results.tsv not found"}

    rows = []
    with open(RESULTS_PATH) as f:
        for row in csv.DictReader(f, delimiter="\t"):
            rows.append(row)

    all_discards = [r for r in rows if r.get("status") in ("discard", "crash")]
    keeps = [r for r in rows if r.get("status") == "keep"]
    total_discards = len(all_discards)

    best = None
    for r in keeps:
        try:
            bpb = float(r["val_bpb"])
            if best is None or bpb < float(best["val_bpb"]):
                best = r
        except (ValueError, KeyError):
            pass

    tracker = _load_tracker()
    results = []

    # ── Trigger 1: Sonnet on val_bpb improvement ─────────────────────────────
    current_best_bpb = float(best["val_bpb"]) if best else None
    last_best = tracker.get("last_known_best_bpb")
    improvement = (
        current_best_bpb is not None and
        (last_best is None or current_best_bpb < float(last_best))
    )
    if improvement:
        trigger = f"New best val_bpb={current_best_bpb} (was {last_best})"
        recent = all_discards[-min(10, len(all_discards)):]
        context = _build_context(rows, recent, best)
        review_num = len(tracker["reviews"]) + 1
        analysis = _sonnet_review(context, trigger, config)
        out_file = _save_report("sonnet_improvement", trigger, context, analysis, review_num)
        tracker["last_known_best_bpb"] = str(current_best_bpb)
        tracker["reviews"].append({
            "date": str(date.today()), "type": "sonnet_improvement",
            "trigger": trigger, "review_num": review_num, "report": str(out_file),
        })
        results.append({"type": "sonnet_improvement", "trigger": trigger,
                        "analysis": analysis, "saved_to": str(out_file)})

    # ── Trigger 2: Sonnet at every 100-discard milestone ─────────────────────
    last_milestone = tracker.get("last_sonnet_milestone", 0)
    current_milestone = (total_discards // SONNET_MILESTONE) * SONNET_MILESTONE
    if current_milestone > last_milestone and current_milestone > 0:
        trigger = f"{current_milestone} total discards milestone"
        recent = all_discards[-10:]
        context = _build_context(rows, recent, best)
        review_num = len(tracker["reviews"]) + 1
        analysis = _sonnet_review(context, trigger, config)
        out_file = _save_report("sonnet_milestone", trigger, context, analysis, review_num)
        tracker["last_sonnet_milestone"] = current_milestone
        tracker["reviews"].append({
            "date": str(date.today()), "type": "sonnet_milestone",
            "trigger": trigger, "review_num": review_num, "report": str(out_file),
        })
        results.append({"type": "sonnet_milestone", "trigger": trigger,
                        "analysis": analysis, "saved_to": str(out_file)})

    # ── Trigger 3: Ollama every 10 new discards ───────────────────────────────
    new_discards = total_discards - tracker.get("last_ollama_review_at", 0)
    if new_discards >= OLLAMA_THRESHOLD:
        recent = all_discards[tracker.get("last_ollama_review_at", 0):][:OLLAMA_THRESHOLD]
        context = _build_context(rows, recent, best)
        review_num = len(tracker["reviews"]) + 1
        analysis = _ollama_review(context, config)
        out_file = _save_report("ollama_discard", f"{OLLAMA_THRESHOLD} new discards", context, analysis, review_num)
        tracker["last_ollama_review_at"] = total_discards
        tracker["reviews"].append({
            "date": str(date.today()), "type": "ollama_discard",
            "trigger": f"{OLLAMA_THRESHOLD} new discards", "review_num": review_num,
            "report": str(out_file),
        })
        results.append({"type": "ollama_discard", "new_discards": new_discards,
                        "analysis": analysis, "saved_to": str(out_file)})

    _save_tracker(tracker)

    if not results:
        return {
            "skipped": True,
            "new_discards": new_discards,
            "waiting_for_ollama": OLLAMA_THRESHOLD - new_discards,
            "next_sonnet_milestone": current_milestone + SONNET_MILESTONE,
        }

    return {"triggered": [r["type"] for r in results], "reviews": results}
