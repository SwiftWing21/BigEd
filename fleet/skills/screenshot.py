"""
Screenshot Capture — captures screen, specific windows, or regions for UX testing and documentation.

Actions:
  full      — capture entire screen
  window    — capture a specific window by title (partial match)
  region    — capture a rectangular region (x, y, width, height)
  app       — capture BigEd CC window specifically

Usage:
    lead_client.py task '{"type": "screenshot", "payload": {"action": "full"}}'
    lead_client.py task '{"type": "screenshot", "payload": {"action": "window", "title": "BigEd CC"}}'
    lead_client.py task '{"type": "screenshot", "payload": {"action": "region", "x": 0, "y": 0, "w": 1920, "h": 1080}}'
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
SKILL_NAME = "screenshot"
DESCRIPTION = "Capture screenshots for UX testing, documentation, and GitHub reference images."
REQUIRES_NETWORK = False

SCREENSHOT_DIR = FLEET_DIR / "knowledge" / "screenshots"


def run(payload: dict, config: dict, log) -> dict:
    """Capture a screenshot based on action type."""
    action = payload.get("action", "full")
    label = payload.get("label", "")  # optional label for filename

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    filename = f"screenshot_{action}{suffix}_{ts}.png"
    filepath = SCREENSHOT_DIR / filename

    try:
        if action == "full":
            img = _capture_full()
        elif action == "window":
            title = payload.get("title", "BigEd")
            img = _capture_window(title)
        elif action == "region":
            x = payload.get("x", 0)
            y = payload.get("y", 0)
            w = payload.get("w", 800)
            h = payload.get("h", 600)
            img = _capture_region(x, y, w, h)
        elif action == "app":
            img = _capture_window("BigEd")
        elif action == "dashboard":
            img = _capture_window("Fleet Dashboard")
            if img is None:
                # Try browser window
                for title in ["localhost:5555", "Fleet Dashboard", "Chrome", "Firefox", "Edge"]:
                    img = _capture_window(title)
                    if img:
                        break
        elif action == "diff":
            # Screenshot diff tool (0.051.04b) — compare before/after images
            before = payload.get("before", "")
            after = payload.get("after", "")
            threshold = payload.get("threshold", 0.02)
            if not before or not after:
                return {"error": "diff action requires 'before' and 'after' image paths"}
            return screenshot_diff(before, after, threshold=threshold)
        else:
            return {"error": f"Unknown action: {action}. Use: full, window, region, app, dashboard, diff"}

        if img is None:
            return {"error": "Screenshot capture failed — window not found or display unavailable"}

        img.save(str(filepath), "PNG")
        size_kb = filepath.stat().st_size / 1024
        log.info(f"Screenshot saved: {filename} ({size_kb:.0f} KB)")

        return {
            "file": str(filepath),
            "filename": filename,
            "size_kb": round(size_kb, 1),
            "resolution": f"{img.width}x{img.height}",
            "action": action,
        }

    except Exception as e:
        return {"error": f"Screenshot failed: {e}"}


def _capture_full():
    """Capture the entire screen."""
    from PIL import ImageGrab
    return ImageGrab.grab()


def _capture_region(x, y, w, h):
    """Capture a rectangular region."""
    from PIL import ImageGrab
    return ImageGrab.grab(bbox=(x, y, x + w, y + h))


def _capture_window(title_pattern: str):
    """Capture a specific window by title (partial match). Windows only."""
    if sys.platform != "win32":
        # Linux/macOS: fall back to full screen crop
        from PIL import ImageGrab
        return ImageGrab.grab()

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        # Find window by partial title match
        target_hwnd = None

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def enum_callback(hwnd, _):
            nonlocal target_hwnd
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    if title_pattern.lower() in buf.value.lower():
                        target_hwnd = hwnd
                        return False  # Stop enumeration
            return True

        user32.EnumWindows(enum_callback, 0)

        if not target_hwnd:
            return None

        # Get window rect
        rect = wintypes.RECT()
        user32.GetWindowRect(target_hwnd, ctypes.byref(rect))
        x, y, x2, y2 = rect.left, rect.top, rect.right, rect.bottom

        # Clamp to screen bounds
        x = max(0, x)
        y = max(0, y)

        from PIL import ImageGrab
        return ImageGrab.grab(bbox=(x, y, x2, y2))

    except Exception:
        # Fallback: full screen
        from PIL import ImageGrab
        return ImageGrab.grab()


def capture_ux_test_suite(config: dict, log) -> list:
    """Capture a standard set of UX screenshots for testing/documentation.
    Returns list of result dicts."""
    results = []

    shots = [
        {"action": "app", "label": "launcher_main"},
        {"action": "dashboard", "label": "web_dashboard"},
        {"action": "full", "label": "full_desktop"},
    ]

    for shot in shots:
        result = run(shot, config, log)
        results.append(result)
        time.sleep(1)  # Brief pause between captures

    return results


def screenshot_diff(before_path: str, after_path: str, output_path: str = None,
                    threshold: float = 0.02) -> dict:
    """Compare two screenshots for visual regression (0.051.04b).

    Args:
        before_path: Path to the "before" screenshot (baseline).
        after_path: Path to the "after" screenshot (new version).
        output_path: Optional path for the diff image. Auto-generated if None.
        threshold: Pixel difference threshold (0.0-1.0). Default 2% = pass.

    Returns:
        dict with keys: match (bool), diff_pct (float), diff_pixels (int),
        total_pixels (int), diff_image (str path or None), verdict (str).
    """
    try:
        from PIL import Image, ImageChops
        import math

        img_before = Image.open(before_path).convert("RGB")
        img_after = Image.open(after_path).convert("RGB")

        # Resize to match if dimensions differ
        if img_before.size != img_after.size:
            img_after = img_after.resize(img_before.size, Image.Resampling.LANCZOS)

        # Compute pixel-wise difference
        diff = ImageChops.difference(img_before, img_after)

        # Count significantly different pixels (threshold per channel: 30/255)
        diff_data = list(diff.getdata())
        total = len(diff_data)
        changed = sum(1 for r, g, b in diff_data if max(r, g, b) > 30)
        diff_pct = changed / total if total > 0 else 0.0

        # Generate diff image (amplified for visibility)
        diff_amplified = diff.point(lambda x: min(255, x * 5))

        if output_path is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            output_path = str(SCREENSHOT_DIR / f"diff_{ts}.png")

        diff_amplified.save(output_path, "PNG")

        verdict = "PASS" if diff_pct <= threshold else "FAIL"

        return {
            "match": diff_pct <= threshold,
            "diff_pct": round(diff_pct * 100, 2),
            "diff_pixels": changed,
            "total_pixels": total,
            "diff_image": output_path,
            "verdict": verdict,
            "threshold_pct": round(threshold * 100, 2),
            "before": before_path,
            "after": after_path,
        }

    except ImportError:
        return {"error": "PIL/Pillow not installed — run: pip install Pillow"}
    except Exception as e:
        return {"error": f"Screenshot diff failed: {e}"}
