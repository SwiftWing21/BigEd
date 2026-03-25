"""Autoresearch experiment analyzer — parses results.tsv, detects plateau, suggests next steps."""
import csv
import logging
from pathlib import Path

SKILL_NAME = "autoresearch_analyze"
DESCRIPTION = "Parse autoresearch results.tsv, rank by val_bpb, detect plateau, suggest hyperparams"
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False
SUITE = "ml"

log = logging.getLogger(__name__)

AUTORESEARCH_DIR = Path(__file__).parent.parent.parent / "autoresearch"
RESULTS_PATH = AUTORESEARCH_DIR / "results.tsv"


def run(payload, config):
    source = Path(payload.get("source", str(RESULTS_PATH)))
    if not source.exists():
        return {"error": "results.tsv not found", "status": "skip"}

    try:
        rows = _parse_results(source)
    except Exception:
        log.warning("Failed to parse results.tsv", exc_info=True)
        return {"error": "Failed to parse results.tsv", "status": "skip"}

    if not rows:
        return {"error": "results.tsv is empty", "status": "skip"}

    # Filter to rows that have valid val_bpb > 0 (crashes have 0.0)
    scored = [r for r in rows if r.get("val_bpb") is not None and r["val_bpb"] > 0]
    scored_sorted = sorted(scored, key=lambda r: r["val_bpb"])

    best = scored_sorted[0] if scored_sorted else None
    best_bpb = best["val_bpb"] if best else None

    # Plateau detection: last 5 scored results within 1% range
    plateau_detected = False
    recent_scored = scored[-5:] if len(scored) >= 5 else scored
    if len(recent_scored) >= 5:
        bpbs = [r["val_bpb"] for r in recent_scored]
        min_bpb = min(bpbs)
        max_bpb = max(bpbs)
        if min_bpb > 0:
            spread = (max_bpb - min_bpb) / min_bpb
            plateau_detected = spread < 0.01

    # Recent trend: last N results with val_bpb
    recent_trend = []
    for r in scored[-5:]:
        recent_trend.append({
            "commit": r.get("commit", "?"),
            "val_bpb": r["val_bpb"],
            "status": r.get("status", "?"),
            "description": r.get("description", ""),
        })

    # Generate suggestions based on findings
    suggestions = _generate_suggestions(rows, scored_sorted, plateau_detected, best)

    return {
        "status": "ok",
        "total_experiments": len(rows),
        "best": best,
        "best_bpb": best_bpb,
        "plateau_detected": plateau_detected,
        "suggestions": suggestions,
        "recent_trend": recent_trend,
    }


def _parse_results(path):
    """Read results.tsv and return list of dicts with numeric casting."""
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
                try:
                    parsed[k] = int(v)
                except ValueError:
                    try:
                        parsed[k] = float(v)
                    except ValueError:
                        parsed[k] = v
            rows.append(parsed)
    return rows


def _generate_suggestions(all_rows, scored_sorted, plateau, best):
    """Suggest next hyperparameters based on analysis."""
    suggestions = []

    if not scored_sorted:
        suggestions.append("No valid results yet — run baseline experiment first")
        return suggestions

    # Count crashes and discards
    crashes = [r for r in all_rows if r.get("status") == "crash"]
    discards = [r for r in all_rows if r.get("status") == "discard"]
    keeps = [r for r in all_rows if r.get("status") == "keep"]

    if crashes:
        suggestions.append(
            f"{len(crashes)} crash(es) detected — reduce model size or batch size to stay within VRAM"
        )

    if plateau:
        suggestions.append(
            "Plateau detected in last 5 runs — consider changing architecture "
            "(depth, attention heads) rather than tuning existing hyperparams"
        )
        suggestions.append(
            "Try alternative optimizer (e.g., SOAP, Schedule-Free) or learning rate schedule"
        )
    else:
        # Suggest based on best result
        if best:
            desc = best.get("description", "")
            if "DEPTH" in str(desc) and best.get("val_bpb", 999) < 1.15:
                suggestions.append(
                    "DEPTH scaling showed improvement — try DEPTH+1 with reduced ASPECT_RATIO to stay in VRAM"
                )
            if best.get("memory_gb", 0) and best["memory_gb"] < 8.0:
                suggestions.append(
                    f"Best run used {best['memory_gb']}GB — headroom available for larger batch or model"
                )

    if len(keeps) > 0 and len(discards) > len(keeps):
        suggestions.append(
            f"High discard rate ({len(discards)}/{len(all_rows)}) — narrow search range around best config"
        )

    if not suggestions:
        suggestions.append("Results look healthy — continue systematic hyperparameter sweep")

    return suggestions
