"""
Screenshot Diff — Compare two screenshots for visual regression testing.

Compares before/after screenshots pixel-by-pixel and returns a pass/warn/fail
verdict based on the percentage of changed pixels. Falls back to MD5 hash
comparison when PIL/numpy are unavailable.

Actions:
  diff (default) — compare two screenshots, return verdict + % changed
  batch          — compare multiple pairs from a 'pairs' list in payload

Thresholds (overridable per call):
  pass:  < 1% pixels changed
  warn:  1–5% pixels changed
  fail:  > 5% pixels changed (or images differ in size)

Usage:
    lead_client.py task '{"type": "screenshot_diff", "payload": {"before_path": "...", "after_path": "..."}}'
    lead_client.py task '{"type": "screenshot_diff", "payload": {"action": "batch", "pairs": [...]}}'
"""
import hashlib
import os
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
SKILL_NAME = "screenshot_diff"
DESCRIPTION = "Compare two screenshots for visual regression. Returns pass/warn/fail verdict with % pixels changed."
COMPLEXITY = "simple"
REQUIRES_NETWORK = False

THRESHOLD_WARN = 1.0   # % pixels changed → warn
THRESHOLD_FAIL = 5.0   # % pixels changed → fail


def run(payload: dict, config: dict, log) -> dict:
    action = payload.get("action", "diff")

    if action == "diff":
        return _diff(payload, log)
    elif action == "batch":
        return _batch(payload, log)
    else:
        return {"error": f"Unknown action: {action}"}


def _diff(payload: dict, log) -> dict:
    before_path = payload.get("before_path", "")
    after_path = payload.get("after_path", "")
    skip_if_missing = payload.get("skip_if_missing", False)
    threshold_warn = payload.get("threshold_warn", THRESHOLD_WARN)
    threshold_fail = payload.get("threshold_fail", THRESHOLD_FAIL)

    # Resolve relative paths against FLEET_DIR
    before = Path(before_path) if os.path.isabs(before_path) else FLEET_DIR / before_path
    after = Path(after_path) if os.path.isabs(after_path) else FLEET_DIR / after_path

    if not before.exists() or not after.exists():
        if skip_if_missing:
            missing = [str(p) for p in (before, after) if not p.exists()]
            log.info(f"Screenshot diff skipped — missing: {missing}")
            return {"verdict": "skip", "reason": "missing_files", "missing": missing}
        missing_label = "before" if not before.exists() else "after"
        return {"error": f"File not found ({missing_label}): {before if not before.exists() else after}"}

    try:
        from PIL import Image, ImageChops
        import numpy as np

        img_before = Image.open(before).convert("RGB")
        img_after = Image.open(after).convert("RGB")

        if img_before.size != img_after.size:
            return {
                "verdict": "fail",
                "reason": "size_mismatch",
                "before_size": list(img_before.size),
                "after_size": list(img_after.size),
                "pct_changed": 100.0,
                "before": str(before),
                "after": str(after),
            }

        diff = ImageChops.difference(img_before, img_after)
        diff_array = np.array(diff)
        changed_pixels = int(np.any(diff_array > 0, axis=2).sum())
        total_pixels = img_before.size[0] * img_before.size[1]
        pct_changed = round(changed_pixels / total_pixels * 100, 3)

        if pct_changed >= threshold_fail:
            verdict = "fail"
        elif pct_changed >= threshold_warn:
            verdict = "warn"
        else:
            verdict = "pass"

        log.info(f"Screenshot diff {before.name} vs {after.name}: "
                 f"{verdict} ({pct_changed}% changed)")
        return {
            "verdict": verdict,
            "pct_changed": pct_changed,
            "changed_pixels": changed_pixels,
            "total_pixels": total_pixels,
            "method": "pixel",
            "before": str(before),
            "after": str(after),
        }

    except ImportError:
        # PIL/numpy not available — fall back to hash comparison
        h1 = hashlib.md5(before.read_bytes()).hexdigest()
        h2 = hashlib.md5(after.read_bytes()).hexdigest()
        identical = h1 == h2
        verdict = "pass" if identical else "fail"
        pct_changed = 0.0 if identical else 100.0
        log.info(f"Screenshot diff (hash fallback, no PIL): {verdict}")
        return {
            "verdict": verdict,
            "pct_changed": pct_changed,
            "method": "hash",
            "before": str(before),
            "after": str(after),
        }


def _batch(payload: dict, log) -> dict:
    """Compare multiple screenshot pairs and return a summary verdict."""
    pairs = payload.get("pairs", [])
    if not pairs:
        return {"error": "pairs list required for batch action"}

    results = []
    counts = {"pass": 0, "warn": 0, "fail": 0, "skip": 0}

    for pair in pairs:
        merged = {**pair, "skip_if_missing": pair.get("skip_if_missing", True)}
        result = _diff(merged, log)
        results.append({
            "before": pair.get("before_path", ""),
            "after": pair.get("after_path", ""),
            **result,
        })
        counts[result.get("verdict", "skip")] += 1

    if counts["fail"] > 0:
        overall = "fail"
    elif counts["warn"] > 0:
        overall = "warn"
    else:
        overall = "pass"

    return {
        "verdict": overall,
        "summary": counts,
        "results": results,
    }
