"""Checkpoint evaluator — lists, ranks, and recommends autoresearch checkpoints."""
import json
import logging
from pathlib import Path

SKILL_NAME = "checkpoint_eval"
DESCRIPTION = "List and rank autoresearch checkpoints by val_bpb, recommend best for deployment"
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False
SUITE = "ml"

log = logging.getLogger(__name__)

CHECKPOINTS_DIR = Path(__file__).parent.parent.parent / "autoresearch" / "checkpoints"


def run(payload, config):
    ckpt_dir = Path(payload.get("checkpoints_dir", str(CHECKPOINTS_DIR)))

    if not ckpt_dir.exists() or not ckpt_dir.is_dir():
        return {"status": "skip", "message": f"Checkpoints directory not found: {ckpt_dir}"}

    # Find .pt checkpoint files
    pt_files = list(ckpt_dir.glob("*.pt"))
    if not pt_files:
        return {"status": "skip", "message": "No .pt checkpoint files found"}

    checkpoints = []
    for pt in pt_files:
        entry = _build_checkpoint_entry(pt)
        checkpoints.append(entry)

    # Separate scored (have val_bpb) from unscored
    scored = [c for c in checkpoints if c.get("val_bpb") is not None]
    unscored = [c for c in checkpoints if c.get("val_bpb") is None]

    # Sort scored by val_bpb (lower is better)
    scored.sort(key=lambda c: c["val_bpb"])

    # Combine: scored first (best to worst), then unscored
    ranked = scored + unscored

    best = scored[0] if scored else None
    recommendation = ""
    if best:
        recommendation = (
            f"Deploy {best['name']} (val_bpb={best['val_bpb']:.6f})"
        )
    elif unscored:
        recommendation = (
            f"{len(unscored)} checkpoint(s) found but none have metadata — "
            "run evaluation to generate sidecar .json files"
        )

    return {
        "status": "ok",
        "checkpoints": ranked,
        "best": best,
        "recommendation": recommendation,
        "total": len(ranked),
        "scored": len(scored),
        "unscored": len(unscored),
    }


def _build_checkpoint_entry(pt_path):
    """Build a checkpoint info dict, reading .json sidecar if present."""
    entry = {
        "name": pt_path.name,
        "path": str(pt_path),
        "size_mb": round(pt_path.stat().st_size / (1024 * 1024), 2),
        "val_bpb": None,
        "config": None,
        "has_metadata": False,
    }

    # Check for JSON sidecar: same name with .json extension
    sidecar = pt_path.with_suffix(".json")
    if not sidecar.exists():
        # Also check <name>.pt.json pattern
        sidecar = pt_path.parent / (pt_path.name + ".json")

    if sidecar.exists():
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            entry["has_metadata"] = True

            # Extract val_bpb from metadata
            val_bpb = meta.get("val_bpb")
            if val_bpb is not None:
                try:
                    entry["val_bpb"] = float(val_bpb)
                except (TypeError, ValueError):
                    log.warning("Invalid val_bpb in %s: %r", sidecar, val_bpb)

            # Extract config
            ckpt_config = meta.get("config")
            if ckpt_config:
                entry["config"] = ckpt_config

            # Preserve any other useful metadata
            for key in ("commit", "description", "memory_gb", "train_time_s"):
                if key in meta:
                    entry[key] = meta[key]

        except Exception:
            log.warning("Failed to read sidecar %s", sidecar, exc_info=True)

    return entry
