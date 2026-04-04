"""Benchmark skill — run local models through standardized test suites."""
import json
import logging
import os
import time
import urllib.request

log = logging.getLogger(__name__)

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "benchmarks", "prompts")


def _load_prompts(category: str) -> list[dict]:
    """Load prompt set from benchmarks/prompts/<category>.json."""
    path = os.path.join(_PROMPTS_DIR, f"{category}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Prompt set not found: {path}")
    with open(path) as f:
        return json.load(f)


def _run_prompt(model: str, system: str, prompt: str, host: str,
                max_tokens: int = 1024) -> dict:
    """Send a single prompt to Ollama and return the raw response dict."""
    body = json.dumps({
        "model": model,
        "system": system,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }).encode()
    req = urllib.request.Request(
        f"{host}/api/generate", data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def run_benchmark(
    model: str,
    prompt_category: str = "coding",
    host: str = "http://localhost:11434",
    kv_cache_type: str = "f16",
    judge_model: str = "",
) -> list[dict]:
    """Run benchmark suite for a single model + category. Returns list of metric dicts."""
    from skills.model_suite import ensure_model_available

    ensure_result = ensure_model_available(model, host)
    if ensure_result.get("status") == "error":
        return [{"model": model, "metric": "error", "value": 0,
                 "unit": "", "kv_cache_type": kv_cache_type,
                 "error": ensure_result.get("error", "model unavailable")}]

    prompts = _load_prompts(prompt_category)
    results = []
    variant = model.split(":")[-1] if ":" in model else model

    for p in prompts:
        try:
            t0 = time.perf_counter()
            resp = _run_prompt(model, p["system"], p["prompt"], host)
            wall_time = time.perf_counter() - t0

            eval_count = resp.get("eval_count", 0)
            eval_duration_ns = resp.get("eval_duration", 1)
            prompt_eval_count = resp.get("prompt_eval_count", 0)
            tokens_per_sec = (eval_count / (eval_duration_ns / 1e9)) if eval_duration_ns else 0
            time_to_first = resp.get("prompt_eval_duration", 0) / 1e9

            results.append({
                "model": model, "variant": variant,
                "metric": "tokens_per_sec", "value": round(tokens_per_sec, 2),
                "unit": "tok/s", "judge_model": judge_model,
                "kv_cache_type": kv_cache_type,
                "prompt_id": p["id"],
            })
            results.append({
                "model": model, "variant": variant,
                "metric": "time_to_first_token", "value": round(time_to_first, 3),
                "unit": "s", "judge_model": judge_model,
                "kv_cache_type": kv_cache_type,
                "prompt_id": p["id"],
            })
            results.append({
                "model": model, "variant": variant,
                "metric": "wall_time", "value": round(wall_time, 3),
                "unit": "s", "judge_model": judge_model,
                "kv_cache_type": kv_cache_type,
                "prompt_id": p["id"],
            })
            results.append({
                "model": model, "variant": variant,
                "metric": "eval_tokens", "value": eval_count,
                "unit": "tokens", "judge_model": judge_model,
                "kv_cache_type": kv_cache_type,
                "prompt_id": p["id"],
            })
        except Exception as e:
            log.warning("Benchmark failed for %s on %s: %s", model, p["id"], e)
            results.append({
                "model": model, "variant": variant,
                "metric": "error", "value": 0,
                "unit": "", "judge_model": judge_model,
                "kv_cache_type": kv_cache_type,
                "prompt_id": p["id"],
                "error": str(e),
            })

    return results


def save_results(results: list[dict], db_module=None) -> int:
    """Persist benchmark results to fleet.db. Returns count saved."""
    if db_module is None:
        import db as db_module
    saved = 0
    with db_module.get_conn() as conn:
        for r in results:
            if r.get("metric") == "error":
                continue
            conn.execute(
                """INSERT INTO benchmarks
                   (model, variant, metric, value, unit, judge_model, kv_cache_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (r["model"], r["variant"], r["metric"], r["value"],
                 r["unit"], r.get("judge_model", ""), r.get("kv_cache_type", "f16")),
            )
            saved += 1
    return saved


def compare_models(models: list[str], db_module=None) -> list[dict]:
    """Fetch and compare benchmark results for given models."""
    if db_module is None:
        import db as db_module
    placeholders = ",".join("?" * len(models))
    with db_module.get_conn() as conn:
        rows = conn.execute(
            f"""SELECT model, metric, AVG(value) as avg_value, unit, kv_cache_type
                FROM benchmarks
                WHERE model IN ({placeholders})
                GROUP BY model, metric, kv_cache_type
                ORDER BY model, metric""",
            models,
        ).fetchall()
    return [dict(r) for r in rows]
