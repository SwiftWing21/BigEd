"""
Screenshot Diff — compares two PNG screenshots for visual regression testing.

Computes a pixel-difference score and saves a diff image (if PIL available).

Usage:
    lead_client.py task '{"type": "screenshot_diff", "payload": {"before_path": "/path/before.png", "after_path": "/path/after.png"}}'

Returns:
    score               — normalised changed-pixel fraction (0.0–1.0)
    changed_pixels_pct  — percentage of pixels that changed (0.0–100.0)
    diff_path           — path to saved diff image, or null if PIL unavailable
    verdict             — "pass" (<2%), "warn" (2–10%), "fail" (>10%)
"""
import os
import sys
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
SKILL_NAME = "screenshot_diff"
DESCRIPTION = "Compare two screenshots for visual regression — returns pixel-diff score and saves diff image."
REQUIRES_NETWORK = False

SCREENSHOT_DIR = FLEET_DIR / "knowledge" / "screenshots"


def run(payload: dict, config: dict, log) -> dict:
    """Compare before/after screenshots and return a visual-regression verdict."""
    before_path = payload.get("before_path")
    after_path = payload.get("after_path")

    if not before_path or not after_path:
        return {"error": "Both 'before_path' and 'after_path' are required."}

    before_p = Path(before_path)
    after_p = Path(after_path)

    if not before_p.exists():
        return {"error": f"before_path not found: {before_path}"}
    if not after_p.exists():
        return {"error": f"after_path not found: {after_path}"}

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    diff_filename = f"diff_{ts}.png"
    diff_path = SCREENSHOT_DIR / diff_filename

    try:
        from PIL import Image, ImageChops, ImageFilter
        result = _diff_with_pil(before_p, after_p, diff_path, log)
    except ImportError:
        log.warning("PIL not available — falling back to byte comparison (no diff image)")
        result = _diff_bytes(before_p, after_p, log)
        result["diff_path"] = None

    # Classify verdict
    pct = result["changed_pixels_pct"]
    if pct > 10.0:
        verdict = "fail"
    elif pct > 2.0:
        verdict = "warn"
    else:
        verdict = "pass"

    result["verdict"] = verdict
    log.info(
        f"screenshot_diff: {pct:.2f}% changed — {verdict} "
        f"(before={before_p.name}, after={after_p.name})"
    )
    return result


def _diff_with_pil(before_p: Path, after_p: Path, diff_path: Path, log) -> dict:
    """Pixel-level diff using Pillow. Saves a highlight diff image."""
    from PIL import Image, ImageChops

    img_before = Image.open(before_p).convert("RGB")
    img_after = Image.open(after_p).convert("RGB")

    # Resize to the smaller of the two if dimensions differ
    if img_before.size != img_after.size:
        log.warning(
            f"Image sizes differ: {img_before.size} vs {img_after.size} — "
            "cropping to smaller common region for comparison"
        )
        w = min(img_before.width, img_after.width)
        h = min(img_before.height, img_after.height)
        img_before = img_before.crop((0, 0, w, h))
        img_after = img_after.crop((0, 0, w, h))

    diff = ImageChops.difference(img_before, img_after)

    # Count changed pixels (any channel > threshold 10 to ignore JPEG artefacts)
    total_pixels = img_before.width * img_before.height
    changed = sum(
        1 for px in diff.getdata()
        if any(c > 10 for c in px)
    )

    changed_pct = (changed / total_pixels * 100) if total_pixels > 0 else 0.0

    # Save enhanced diff image — amplify differences for visibility
    from PIL import ImageEnhance
    diff_enhanced = ImageEnhance.Brightness(diff).enhance(5.0)
    diff_enhanced.save(str(diff_path), "PNG")
    log.info(f"Diff image saved: {diff_path.name}")

    return {
        "score": round(changed / total_pixels, 6) if total_pixels > 0 else 0.0,
        "changed_pixels_pct": round(changed_pct, 4),
        "diff_path": str(diff_path),
        "total_pixels": total_pixels,
        "changed_pixels": changed,
    }


def _diff_bytes(before_p: Path, after_p: Path, log) -> dict:
    """Fallback: byte-level comparison when PIL is unavailable.

    Reports 0% or 100% changed (no per-pixel granularity without PIL).
    """
    before_bytes = before_p.read_bytes()
    after_bytes = after_p.read_bytes()

    if before_bytes == after_bytes:
        return {
            "score": 0.0,
            "changed_pixels_pct": 0.0,
            "total_pixels": None,
            "changed_pixels": 0,
        }
    else:
        # Files differ but we can't measure pixel delta without PIL
        log.warning("Files differ but PIL is unavailable — pixel diff not measurable; reporting 100%")
        return {
            "score": 1.0,
            "changed_pixels_pct": 100.0,
            "total_pixels": None,
            "changed_pixels": None,
        }
