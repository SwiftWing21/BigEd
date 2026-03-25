"""
Config drift detection — compares fleet.toml against a saved baseline
snapshot and reports added, removed, and changed keys.

First run saves the baseline. Subsequent runs diff against it.
Output: knowledge/config/drift_YYYY-MM-DD.md
"""
import json
import logging
from datetime import date
from pathlib import Path

SKILL_NAME = "config_drift_detect"
DESCRIPTION = "Detect configuration drift by comparing fleet.toml against a saved baseline snapshot."
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
CONFIG_DIR = KNOWLEDGE_DIR / "config"
BASELINE_FILE = CONFIG_DIR / "fleet_toml_baseline.json"

log = logging.getLogger(__name__)


def _flatten(data, prefix=""):
    """Flatten a nested dict into dot-separated key-value pairs."""
    flat = {}
    for k, v in data.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(_flatten(v, key))
        else:
            flat[key] = v
    return flat


def _parse_toml(path):
    """Parse a TOML file into a dict. Tries tomllib (3.11+), falls back to tomli."""
    text = path.read_bytes()
    try:
        import tomllib
        return tomllib.loads(text.decode("utf-8"))
    except ImportError:
        pass
    try:
        import tomli
        return tomli.loads(text.decode("utf-8"))
    except ImportError:
        pass
    # Last resort: simple line parser for key=value (no nested tables)
    result = {}
    for line in text.decode("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _diff(baseline, current):
    """Compute added, removed, and changed keys between two flat dicts."""
    base_keys = set(baseline.keys())
    curr_keys = set(current.keys())

    added = sorted(curr_keys - base_keys)
    removed = sorted(base_keys - curr_keys)
    changed = []
    for k in sorted(base_keys & curr_keys):
        if str(baseline[k]) != str(current[k]):
            changed.append((k, baseline[k], current[k]))

    return added, removed, changed


def _build_report(added, removed, changed, is_first_run):
    """Render the drift report as Markdown."""
    today = date.today().isoformat()
    lines = [
        f"# Config Drift Report — {today}",
        "",
    ]

    if is_first_run:
        lines.append("**First run — baseline saved.** No drift to report.")
        lines.append("")
        return "\n".join(lines)

    total = len(added) + len(removed) + len(changed)
    lines.append(f"**Total drifts:** {total} | "
                 f"Added: {len(added)} | Removed: {len(removed)} | "
                 f"Changed: {len(changed)}")
    lines.append("")

    if not total:
        lines.append("No configuration drift detected. Fleet.toml matches baseline.")
        lines.append("")
        return "\n".join(lines)

    if added:
        lines.append("## Added Keys")
        lines.append("")
        for k in added:
            lines.append(f"- `{k}`")
        lines.append("")

    if removed:
        lines.append("## Removed Keys")
        lines.append("")
        for k in removed:
            lines.append(f"- `{k}`")
        lines.append("")

    if changed:
        lines.append("## Changed Keys")
        lines.append("")
        lines.append("| Key | Baseline | Current |")
        lines.append("|-----|----------|---------|")
        for k, old, new in changed:
            lines.append(f"| `{k}` | `{old}` | `{new}` |")
        lines.append("")

    return "\n".join(lines)


def run(payload, config):
    """Detect configuration drift in fleet.toml."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    toml_path = FLEET_DIR / "fleet.toml"
    if not toml_path.exists():
        return {"status": "error", "message": "fleet.toml not found"}

    try:
        parsed = _parse_toml(toml_path)
    except Exception:
        log.warning("Failed to parse fleet.toml", exc_info=True)
        return {"status": "error", "message": "Failed to parse fleet.toml"}

    current = _flatten(parsed)
    is_first_run = not BASELINE_FILE.exists()

    if is_first_run:
        # Save baseline
        try:
            BASELINE_FILE.write_text(
                json.dumps(current, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            log.warning("Failed to save baseline", exc_info=True)
            return {"status": "error", "message": "Failed to save baseline"}

        report = _build_report([], [], [], is_first_run=True)
        added, removed, changed = [], [], []
    else:
        # Load baseline and diff
        try:
            baseline = json.loads(
                BASELINE_FILE.read_text(encoding="utf-8")
            )
        except Exception:
            log.warning("Failed to read baseline", exc_info=True)
            return {"status": "error", "message": "Failed to read baseline"}

        added, removed, changed = _diff(baseline, current)
        report = _build_report(added, removed, changed, is_first_run=False)

    # Save report
    today = date.today().isoformat()
    report_path = CONFIG_DIR / f"drift_{today}.md"
    try:
        report_path.write_text(report, encoding="utf-8")
    except Exception:
        log.warning("Failed to write drift report", exc_info=True)
        return {"status": "error", "message": "Failed to write drift report"}

    # Update baseline to current state after diffing
    if not is_first_run:
        try:
            BASELINE_FILE.write_text(
                json.dumps(current, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            log.warning("Failed to update baseline", exc_info=True)

    return {
        "status": "ok",
        "saved_to": str(report_path),
        "first_run": is_first_run,
        "added": len(added),
        "removed": len(removed),
        "changed": len(changed),
        "total_keys": len(current),
    }
