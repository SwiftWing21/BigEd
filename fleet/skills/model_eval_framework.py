"""Comprehensive model evaluation framework — runs evaluation prompts against
a local Ollama model, measures response quality metrics (latency, token count,
coherence heuristics), and compares results across models when historical data
exists in the DB.

Saves evaluation report to knowledge/evaluations/model_eval_<model>_YYYY-MM-DD.json.
"""
import json
import logging
import re
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

SKILL_NAME = "model_eval_framework"
DESCRIPTION = (
    "Run evaluation prompts against a local Ollama model and measure "
    "response quality metrics (latency, tokens, coherence)."
)
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False  # Local Ollama only
SUITE = "ml"

log = logging.getLogger(__name__)

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
EVAL_DIR = KNOWLEDGE_DIR / "evaluations"

# Default evaluation prompts covering different capability dimensions
DEFAULT_EVAL_PROMPTS = [
    {
        "id": "reasoning",
        "category": "reasoning",
        "prompt": (
            "A farmer has 17 sheep. All but 9 run away. "
            "How many sheep does the farmer have left? Explain your reasoning."
        ),
    },
    {
        "id": "code_gen",
        "category": "code_generation",
        "prompt": (
            "Write a Python function that checks whether a string is a valid "
            "IPv4 address. Include edge cases."
        ),
    },
    {
        "id": "summarization",
        "category": "summarization",
        "prompt": (
            "Summarize in 2-3 sentences: Machine learning is a subset of "
            "artificial intelligence that enables systems to learn and improve "
            "from experience without being explicitly programmed. It focuses on "
            "the development of algorithms that can access data and use it to "
            "learn for themselves. The process begins with observations or data, "
            "such as examples, direct experience, or instruction, to look for "
            "patterns in data and make better decisions in the future."
        ),
    },
    {
        "id": "instruction_follow",
        "category": "instruction_following",
        "prompt": (
            "List exactly 5 European capital cities. Format each as a numbered "
            "list item. Do not include any other text."
        ),
    },
    {
        "id": "creative",
        "category": "creative",
        "prompt": "Write a haiku about debugging code.",
    },
]


def _call_ollama(model, prompt, timeout=30):
    """Send a generate request to local Ollama and return (response_text, metadata)."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 512},
    }).encode("utf-8")

    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    start = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read())
    elapsed = time.monotonic() - start

    response_text = result.get("response", "")
    meta = {
        "total_duration_ns": result.get("total_duration", 0),
        "eval_count": result.get("eval_count", 0),
        "eval_duration_ns": result.get("eval_duration", 0),
        "wall_time_s": round(elapsed, 3),
    }

    # Compute tokens/sec from Ollama's own counters
    if meta["eval_duration_ns"] > 0 and meta["eval_count"] > 0:
        meta["tokens_per_sec"] = round(
            meta["eval_count"] / (meta["eval_duration_ns"] / 1e9), 2
        )
    else:
        meta["tokens_per_sec"] = 0.0

    return response_text, meta


def _coherence_heuristics(text):
    """Compute simple coherence metrics from response text."""
    if not text or not text.strip():
        return {
            "sentence_count": 0,
            "avg_sentence_length": 0.0,
            "word_count": 0,
            "char_count": 0,
            "unique_word_ratio": 0.0,
            "has_code_block": False,
        }

    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    words = text.split()
    unique_words = set(w.lower().strip(".,!?;:()[]{}\"'") for w in words)
    unique_words.discard("")

    return {
        "sentence_count": len(sentences),
        "avg_sentence_length": round(
            sum(len(s.split()) for s in sentences) / max(len(sentences), 1), 1
        ),
        "word_count": len(words),
        "char_count": len(text),
        "unique_word_ratio": round(
            len(unique_words) / max(len(words), 1), 3
        ),
        "has_code_block": "```" in text or "def " in text or "function " in text,
    }


def _load_historical(model):
    """Load previous evaluation results for the same model from the DB."""
    try:
        import db
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT result_json FROM tasks "
                "WHERE type = 'model_eval_framework' AND status = 'DONE' "
                "AND result_json LIKE ? "
                "ORDER BY created_at DESC LIMIT 5",
                (f'%"model": "{model}"%',),
            ).fetchall()
        results = []
        for row in rows:
            try:
                data = json.loads(row["result_json"])
                results.append(data)
            except (json.JSONDecodeError, TypeError):
                continue
        return results
    except Exception:
        log.warning("Failed to load historical eval data", exc_info=True)
        return []


def _compare_with_history(current_summary, historical):
    """Compare current evaluation summary with historical runs."""
    if not historical:
        return None

    prev_summaries = []
    for h in historical:
        if isinstance(h, dict) and "summary" in h:
            prev_summaries.append(h["summary"])

    if not prev_summaries:
        return None

    # Average historical metrics
    prev_avg_latency = []
    prev_avg_tps = []
    for ps in prev_summaries:
        if "avg_wall_time_s" in ps:
            prev_avg_latency.append(ps["avg_wall_time_s"])
        if "avg_tokens_per_sec" in ps:
            prev_avg_tps.append(ps["avg_tokens_per_sec"])

    comparison = {}
    if prev_avg_latency:
        hist_latency = sum(prev_avg_latency) / len(prev_avg_latency)
        curr_latency = current_summary.get("avg_wall_time_s", 0)
        if hist_latency > 0:
            comparison["latency_change_pct"] = round(
                ((curr_latency - hist_latency) / hist_latency) * 100, 1
            )

    if prev_avg_tps:
        hist_tps = sum(prev_avg_tps) / len(prev_avg_tps)
        curr_tps = current_summary.get("avg_tokens_per_sec", 0)
        if hist_tps > 0:
            comparison["tps_change_pct"] = round(
                ((curr_tps - hist_tps) / hist_tps) * 100, 1
            )

    comparison["historical_runs_compared"] = len(prev_summaries)
    return comparison


def run(payload, config):
    """Run evaluation prompts against a local Ollama model and report metrics."""
    import db

    EVAL_DIR.mkdir(parents=True, exist_ok=True)

    model = payload.get("model") or config.get(
        "models", {}
    ).get("default_model", "qwen3:8b")
    eval_prompts = payload.get("prompts", DEFAULT_EVAL_PROMPTS)
    timeout = payload.get("timeout", 30)

    # Validate Ollama is reachable
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5)
    except Exception:
        log.warning("Ollama not reachable at localhost:11434", exc_info=True)
        return {"status": "error", "message": "Ollama not reachable at localhost:11434"}

    results = []
    errors = 0

    for ep in eval_prompts:
        prompt_id = ep.get("id", "unknown")
        category = ep.get("category", "general")
        prompt_text = ep.get("prompt", "")

        if not prompt_text:
            continue

        try:
            response_text, meta = _call_ollama(model, prompt_text, timeout=timeout)
            coherence = _coherence_heuristics(response_text)

            results.append({
                "prompt_id": prompt_id,
                "category": category,
                "prompt": prompt_text[:200],
                "response_preview": response_text[:300],
                "wall_time_s": meta["wall_time_s"],
                "eval_count": meta["eval_count"],
                "tokens_per_sec": meta["tokens_per_sec"],
                "coherence": coherence,
            })
        except urllib.error.URLError:
            log.warning("Ollama request failed for prompt %s", prompt_id, exc_info=True)
            errors += 1
            results.append({
                "prompt_id": prompt_id,
                "category": category,
                "error": "Request failed or timed out",
            })
        except Exception:
            log.warning("Eval failed for prompt %s", prompt_id, exc_info=True)
            errors += 1
            results.append({
                "prompt_id": prompt_id,
                "category": category,
                "error": "Unexpected error during evaluation",
            })

    successful = [r for r in results if "error" not in r]

    if not successful:
        return {
            "status": "error",
            "message": f"All {len(results)} evaluations failed",
            "errors": errors,
        }

    # Compute summary statistics
    wall_times = [r["wall_time_s"] for r in successful]
    tps_values = [r["tokens_per_sec"] for r in successful if r["tokens_per_sec"] > 0]
    word_counts = [r["coherence"]["word_count"] for r in successful]

    summary = {
        "model": model,
        "prompts_run": len(results),
        "prompts_succeeded": len(successful),
        "prompts_failed": errors,
        "avg_wall_time_s": round(sum(wall_times) / len(wall_times), 3),
        "min_wall_time_s": round(min(wall_times), 3),
        "max_wall_time_s": round(max(wall_times), 3),
        "avg_tokens_per_sec": round(
            sum(tps_values) / len(tps_values), 2
        ) if tps_values else 0.0,
        "avg_word_count": round(sum(word_counts) / len(word_counts), 1),
    }

    # Historical comparison
    historical = _load_historical(model)
    comparison = _compare_with_history(summary, historical)
    if comparison:
        summary["vs_history"] = comparison

    # Build full report
    report = {
        "date": date.today().isoformat(),
        "model": model,
        "summary": summary,
        "evaluations": results,
    }

    # Save report to knowledge/evaluations/
    safe_model = re.sub(r'[^a-zA-Z0-9_.-]', '_', model)
    out_file = EVAL_DIR / f"model_eval_{safe_model}_{date.today().isoformat()}.json"
    out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Store summary in DB for future historical comparisons
    try:
        def _save():
            def _do():
                with db.get_conn() as conn:
                    conn.execute(
                        "INSERT INTO tasks (type, status, payload_json, result_json) "
                        "VALUES (?, 'DONE', ?, ?)",
                        (
                            SKILL_NAME,
                            json.dumps({"model": model}),
                            json.dumps({"model": model, "summary": summary}),
                        ),
                    )
            db._retry_write(_do)
        _save()
    except Exception:
        log.warning("Failed to save eval summary to DB", exc_info=True)

    return {
        "status": "ok",
        "saved_to": str(out_file),
        "model": model,
        "summary": summary,
    }
