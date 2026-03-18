"""Analyze autoresearch results.tsv and write a report."""
import csv
from datetime import date
from pathlib import Path

_RESULTS_CANDIDATES = [
    Path("/mnt/c/Users/max/Projects/Education/autoresearch/results.tsv"),  # Education dir (preferred)
    Path("/home/max/autoresearch/results.tsv"),                             # legacy WSL path
]
RESULTS_PATH = next((p for p in _RESULTS_CANDIDATES if p.parent.exists()), _RESULTS_CANDIDATES[0])


def run(payload, config):
    path = Path(payload.get("source", str(RESULTS_PATH)))
    if not path.exists():
        return {"error": f"Not found: {path}"}

    rows = []
    with open(path) as f:
        for row in csv.DictReader(f, delimiter="\t"):
            rows.append(row)

    if not rows:
        return {"report": "No results yet."}

    keeps = [r for r in rows if r.get("status") == "keep"]
    discards = [r for r in rows if r.get("status") == "discard"]
    crashes = [r for r in rows if r.get("status") == "crash"]

    best = None
    for r in keeps:
        try:
            bpb = float(r["val_bpb"])
            if best is None or bpb < float(best["val_bpb"]):
                best = r
        except (ValueError, KeyError):
            pass

    lines = [
        f"# Autoresearch Report — {date.today()}",
        f"Total experiments: {len(rows)}  (keep={len(keeps)}, discard={len(discards)}, crash={len(crashes)})",
    ]
    if best:
        lines.append(
            f"**Best**: val_bpb={best['val_bpb']} @ {best['commit']} — {best.get('description', '')}"
        )

    lines += ["", "## All Results", "| Commit | val_bpb | VRAM | Status | Description |",
              "|--------|---------|------|--------|-------------|"]
    for r in rows:
        lines.append(
            f"| {r.get('commit','?')} | {r.get('val_bpb','?')} | {r.get('memory_gb','?')} | {r.get('status','?')} | {r.get('description','')} |"
        )

    report = "\n".join(lines)

    out_dir = Path(__file__).parent.parent / "knowledge" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{date.today()}_autoresearch.md"
    out_file.write_text(report)

    return {
        "report": report,
        "best_bpb": best["val_bpb"] if best else None,
        "total": len(rows),
        "saved_to": str(out_file),
    }
