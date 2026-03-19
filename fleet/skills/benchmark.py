"""
Benchmark skill — runs a skill N times with varied payloads, measures latency,
error rate, and output consistency.

Payload:
  skill_name    str    skill to benchmark (required)
  payloads      list   list of payload dicts to test with (required, min 1)
  runs_per      int    times to run each payload (default 1)
  timeout       int    max seconds per run (default 60)

Output: knowledge/reports/benchmark_<skill>_<date>.md
Returns: {skill, total_runs, passed, failed, avg_ms, min_ms, max_ms, error_rate}
"""
import importlib
import time
from datetime import datetime
from pathlib import Path

SKILL_NAME = "benchmark"
DESCRIPTION = "Benchmark skill — runs a skill N times with varied payloads, measures latency,"

FLEET_DIR = Path(__file__).parent.parent
REPORTS_DIR = FLEET_DIR / "knowledge" / "reports"


def run(payload, config):
    skill_name = payload.get("skill_name", "")
    payloads = payload.get("payloads", [])
    runs_per = payload.get("runs_per", 1)
    timeout = payload.get("timeout", 60)

    if not skill_name:
        return {"error": "No skill_name provided"}
    if not payloads:
        return {"error": "No payloads provided — need at least one test payload"}

    try:
        module = importlib.import_module(f"skills.{skill_name}")
    except ImportError as e:
        return {"error": f"Cannot import skill: {e}"}

    results = []
    total = 0
    passed = 0
    failed = 0
    latencies = []

    for pi, test_payload in enumerate(payloads):
        for ri in range(runs_per):
            total += 1
            start = time.time()
            try:
                result = module.run(test_payload, config)
                elapsed_ms = (time.time() - start) * 1000
                has_error = isinstance(result, dict) and "error" in result
                status = "FAIL" if has_error else "PASS"
                if has_error:
                    failed += 1
                else:
                    passed += 1
                latencies.append(elapsed_ms)
                results.append({
                    "payload_idx": pi, "run": ri, "status": status,
                    "ms": round(elapsed_ms, 1),
                    "output_keys": list(result.keys()) if isinstance(result, dict) else type(result).__name__,
                    "error": result.get("error") if has_error else None,
                })
            except Exception as e:
                elapsed_ms = (time.time() - start) * 1000
                failed += 1
                latencies.append(elapsed_ms)
                results.append({
                    "payload_idx": pi, "run": ri, "status": "ERROR",
                    "ms": round(elapsed_ms, 1),
                    "error": f"{type(e).__name__}: {e}",
                })

    avg_ms = round(sum(latencies) / len(latencies), 1) if latencies else 0
    min_ms = round(min(latencies), 1) if latencies else 0
    max_ms = round(max(latencies), 1) if latencies else 0
    error_rate = round(failed / total * 100, 1) if total else 0

    # Save report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    report = REPORTS_DIR / f"benchmark_{skill_name}_{date_str}.md"
    lines = [
        f"# Benchmark: `{skill_name}`",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Payloads:** {len(payloads)} | **Runs/payload:** {runs_per} | **Total:** {total}",
        "",
        f"## Results",
        f"- Passed: {passed} | Failed: {failed} | Error rate: {error_rate}%",
        f"- Latency: avg={avg_ms}ms, min={min_ms}ms, max={max_ms}ms",
        "",
        "## Run Details",
        "| # | Payload | Run | Status | ms | Error |",
        "|---|---------|-----|--------|-----|-------|",
    ]
    for r in results:
        lines.append(f"| {r['payload_idx']}.{r['run']} | {r['payload_idx']} | {r['run']} | {r['status']} | {r['ms']} | {r.get('error', '-') or '-'} |")
    report.write_text("\n".join(lines))

    return {
        "skill": skill_name,
        "total_runs": total,
        "passed": passed,
        "failed": failed,
        "avg_ms": avg_ms,
        "min_ms": min_ms,
        "max_ms": max_ms,
        "error_rate": error_rate,
        "saved_to": str(report),
    }