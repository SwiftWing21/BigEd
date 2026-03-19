"""0.08.00: ML Bridge — connects autoresearch pipeline results to fleet knowledge layer."""
import csv
import json
from datetime import datetime
from pathlib import Path

SKILL_NAME = "ml_bridge"
DESCRIPTION = "Import autoresearch ML training results into fleet knowledge and dashboard"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
AUTORESEARCH_DIR = FLEET_DIR.parent / "autoresearch"
KNOWLEDGE_DIR = FLEET_DIR / "knowledge" / "ml_results"


def run(payload: dict, config: dict) -> str:
    action = payload.get("action", "import_results")

    if action == "import_results":
        return _import_results()
    elif action == "summary":
        return _get_summary()
    elif action == "best_run":
        return _get_best_run()
    else:
        return json.dumps({"error": f"Unknown action: {action}"})


def _parse_tsv(path: Path) -> list[dict]:
    """Read a TSV file and return a list of row dicts. Numeric values are cast."""
    rows = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            parsed = {}
            for k, v in row.items():
                if v is None:
                    parsed[k] = None
                    continue
                v = v.strip()
                # Try int, then float, else keep string
                try:
                    parsed[k] = int(v)
                except ValueError:
                    try:
                        parsed[k] = float(v)
                    except ValueError:
                        parsed[k] = v
            rows.append(parsed)
    return rows


def _load_results() -> list[dict]:
    """Load results.tsv from the autoresearch directory. Returns [] on missing file."""
    tsv_path = AUTORESEARCH_DIR / "results.tsv"
    if not tsv_path.exists():
        return []
    try:
        return _parse_tsv(tsv_path)
    except Exception:
        return []


def _import_results() -> str:
    """Read autoresearch/results.tsv, save structured JSON, log to fleet.db usage table."""
    results = _load_results()
    if not results:
        return json.dumps({
            "status": "no_data",
            "message": "No results.tsv found or file is empty",
        })

    # Ensure output directory exists
    try:
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return json.dumps({"status": "error", "error": f"Cannot create output dir: {e}"})

    # Build structured output
    timestamp = datetime.now().strftime("%Y%m%d")
    out_path = KNOWLEDGE_DIR / f"results_{timestamp}.json"

    export = {
        "imported_at": datetime.now().isoformat(),
        "source": str(AUTORESEARCH_DIR / "results.tsv"),
        "total_runs": len(results),
        "runs": results,
    }

    try:
        out_path.write_text(json.dumps(export, indent=2), encoding="utf-8")
    except Exception as e:
        return json.dumps({"status": "error", "error": f"Write failed: {e}"})

    # Log summary to fleet.db usage table for cost tracking integration
    try:
        import sys
        sys.path.insert(0, str(FLEET_DIR))
        import db
        db.log_usage(
            skill=SKILL_NAME,
            model="autoresearch",
            input_tokens=len(results),   # repurpose: count of runs imported
            output_tokens=0,
            cost_usd=0.0,
            agent="ml_bridge",
        )
    except Exception:
        pass  # non-critical — don't fail import over logging

    return json.dumps({
        "status": "imported",
        "total_runs": len(results),
        "output_file": str(out_path),
    })


def _get_summary() -> str:
    """Aggregate results: total runs, best val_bpb, avg improvement, param ranges."""
    results = _load_results()
    if not results:
        return json.dumps({
            "status": "no_data",
            "message": "No results.tsv found or file is empty",
        })

    val_bpbs = [r["val_bpb"] for r in results if isinstance(r.get("val_bpb"), (int, float))]
    params = [r["params"] for r in results if isinstance(r.get("params"), (int, float))]

    summary: dict = {
        "total_runs": len(results),
        "runs_with_val_bpb": len(val_bpbs),
    }

    if val_bpbs:
        summary["best_val_bpb"] = min(val_bpbs)
        summary["worst_val_bpb"] = max(val_bpbs)
        summary["mean_val_bpb"] = round(sum(val_bpbs) / len(val_bpbs), 6)
        # Average per-run improvement (successive difference)
        if len(val_bpbs) >= 2:
            improvements = [val_bpbs[i] - val_bpbs[i + 1] for i in range(len(val_bpbs) - 1)]
            summary["avg_improvement_per_run"] = round(sum(improvements) / len(improvements), 6)
        else:
            summary["avg_improvement_per_run"] = 0.0

    if params:
        summary["param_range"] = {"min": min(params), "max": max(params)}

    return json.dumps({"status": "ok", "summary": summary})


def _get_best_run() -> str:
    """Find the run with lowest val_bpb and return its full config."""
    results = _load_results()
    if not results:
        return json.dumps({
            "status": "no_data",
            "message": "No results.tsv found or file is empty",
        })

    scored = [r for r in results if isinstance(r.get("val_bpb"), (int, float))]
    if not scored:
        return json.dumps({
            "status": "no_data",
            "message": "No runs have a valid val_bpb value",
        })

    best = min(scored, key=lambda r: r["val_bpb"])
    return json.dumps({"status": "ok", "best_run": best})
