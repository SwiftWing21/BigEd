"""
fleet/debug_models.py — Ollama model debug, diagnostics & cleanup.

Canonical module for all idle-model detection and VRAM management.
Used by:
  - CLI:   python fleet/debug_models.py [--clean] [--target MODEL] [--json]
  - Skill: fleet/skills/model_manager.py  action="debug"
  - Boot:  BigEd/launcher/ui/boot.py      _evict_idle_blockers()

Core logic lives here — consumers import, never duplicate.

Usage:
    python fleet/debug_models.py                            # report only
    python fleet/debug_models.py --clean                    # evict idle models
    python fleet/debug_models.py --clean --target qwen3:8b  # protect target
    python fleet/debug_models.py --host http://10.0.0.5:11434
    python fleet/debug_models.py --json                     # machine-readable
    python fleet/debug_models.py --threshold 12             # custom idle hours
"""

import argparse
import json
import sqlite3
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

FLEET_DIR = Path(__file__).parent
DB_PATH = FLEET_DIR / "fleet.db"
DEFAULT_HOST = "http://localhost:11434"

# Models are considered "idle keepalive blockers" if their expires_at is
# more than this many hours in the future (implies keep_alive:"24h" park).
# Overridable via fleet.toml [models] idle_threshold_hours or --threshold.
DEFAULT_IDLE_THRESHOLD_HOURS = 20.0

# Task statuses that indicate a model is actively in use and must not be evicted.
_ACTIVE_TASK_STATUSES = ("RUNNING", "WAITING_HUMAN")


# ── Ollama timestamp parsing ─────────────────────────────────────────────────

def parse_ollama_timestamp(ts: str) -> datetime | None:
    """Parse Ollama's Go-style ISO timestamp to a timezone-aware datetime.

    Ollama emits nanosecond-precision timestamps like:
        2026-03-20T18:30:00.123456789Z
    Python's fromisoformat only handles up to microseconds (6 digits).

    Returns None on parse failure (caller decides policy).
    """
    if not ts:
        return None
    try:
        ts = ts.replace("Z", "+00:00")
        if "." in ts:
            dot = ts.index(".")
            # Find the timezone offset start ('+' or '-' after the dot)
            tz_start = len(ts)
            for i in range(dot + 1, len(ts)):
                if ts[i] in ("+", "-"):
                    tz_start = i
                    break
            frac = ts[dot + 1:tz_start][:6]  # truncate nanoseconds → microseconds
            ts = ts[:dot + 1] + frac + ts[tz_start:]
        return datetime.fromisoformat(ts)
    except Exception:
        return None


# ── Ollama API helpers ────────────────────────────────────────────────────────

def get_loaded_models(host: str = DEFAULT_HOST) -> list[dict]:
    """Query /api/ps — returns all models currently loaded in VRAM.

    Each dict contains at minimum: name, size, expires_at, model, digest.
    Raises RuntimeError if Ollama is unreachable.
    """
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=5) as r:
            data = json.loads(r.read())
        return data.get("models", [])
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach Ollama at {host}: {e}")


def get_loaded_names(host: str = DEFAULT_HOST) -> list[str]:
    """Convenience: just the model name strings from /api/ps."""
    try:
        return [m["name"] for m in get_loaded_models(host)]
    except RuntimeError:
        return []


def get_installed_models(host: str = DEFAULT_HOST) -> list[str]:
    """Query /api/tags — returns all installed model names."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as r:
            data = json.loads(r.read())
        return [m["name"] for m in data.get("models", [])]
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach Ollama at {host}: {e}")


def evict_model(name: str, host: str = DEFAULT_HOST) -> bool:
    """Evict a single model from VRAM by setting keep_alive=0.

    Returns True on success, False on failure (non-fatal).
    """
    try:
        body = json.dumps({"model": name, "keep_alive": 0, "prompt": ""}).encode()
        req = urllib.request.Request(
            f"{host}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
        return True
    except Exception as e:
        print(f"  [warn] evict {name}: {e}", file=sys.stderr)
        return False


# ── Fleet DB helpers ──────────────────────────────────────────────────────────

def get_active_task_count(db_path: Path | None = None) -> int:
    """Count tasks in active states (RUNNING + WAITING_HUMAN) in fleet.db."""
    db = db_path or DB_PATH
    if not db.exists():
        return 0
    try:
        placeholders = ",".join(f"'{s}'" for s in _ACTIVE_TASK_STATUSES)
        conn = sqlite3.connect(str(db), timeout=5, check_same_thread=False)
        cur = conn.execute(f"SELECT COUNT(*) FROM tasks WHERE status IN ({placeholders})")
        count = cur.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


def get_active_task_models(db_path: Path | None = None) -> set[str]:
    """Get model names referenced by active tasks (RUNNING + WAITING_HUMAN).

    Parses the 'model' key from each task's payload_json.
    Returns empty set on any failure (safe default — won't cause eviction).
    """
    db = db_path or DB_PATH
    if not db.exists():
        return set()
    try:
        placeholders = ",".join(f"'{s}'" for s in _ACTIVE_TASK_STATUSES)
        conn = sqlite3.connect(str(db), timeout=5, check_same_thread=False)
        cur = conn.execute(
            f"SELECT payload_json FROM tasks WHERE status IN ({placeholders})"
        )
        rows = cur.fetchall()
        conn.close()
        active = set()
        for (payload_json,) in rows:
            if not payload_json:
                continue
            try:
                p = json.loads(payload_json)
                if "model" in p:
                    active.add(p["model"])
            except (json.JSONDecodeError, TypeError):
                pass
        return active
    except Exception:
        return set()


# ── Idle detection ────────────────────────────────────────────────────────────

def is_idle(
    model_info: dict,
    active_task_models: set[str],
    idle_threshold_hours: float = DEFAULT_IDLE_THRESHOLD_HOURS,
) -> tuple[bool, str]:
    """Determine if a loaded model is idle and safe to evict.

    Returns (is_idle: bool, reason: str).

    A model is idle when:
      1. It is NOT referenced by any RUNNING or WAITING_HUMAN task, AND
      2. It has an expires_at timestamp (Ollama keepalive), AND
      3. That expiry is NOT in the past (already dying)

    The idle_threshold_hours param controls the "long keepalive" vs
    "expiring naturally" split for the status reason string. Models
    with > threshold hours remaining were parked with keep_alive:"24h".
    Both categories are evictable — the threshold only affects reporting.
    """
    name = model_info.get("name", "")

    # Signal 1: fleet DB — task actively using this model
    if name in active_task_models:
        return False, "referenced by active task"

    # Signal 2: Ollama keepalive expiry
    expires_at = model_info.get("expires_at", "")
    if not expires_at:
        return False, "no expires_at (permanent load)"

    expires = parse_ollama_timestamp(expires_at)
    if expires is None:
        return False, "could not parse expires_at"

    remaining_hours = (expires - datetime.now(timezone.utc)).total_seconds() / 3600

    if remaining_hours <= 0:
        # Already expired / expiring this instant — Ollama will clean up
        return False, "already expired (Ollama will unload)"

    if remaining_hours > idle_threshold_hours:
        return True, f"idle keepalive (expires in {remaining_hours:.1f}h, no active tasks)"
    return True, f"expiring in {remaining_hours:.1f}h, no active tasks"


# ── High-level convenience functions ──────────────────────────────────────────

def evict_idle_blockers(
    host: str = DEFAULT_HOST,
    target_model: str | None = None,
    db_path: Path | None = None,
    idle_threshold_hours: float = DEFAULT_IDLE_THRESHOLD_HOURS,
    protect: set[str] | None = None,
) -> list[str]:
    """One-call interface: evict all idle VRAM-blocker models.

    This is the function boot.py and model_manager.py should call.
    Returns list of successfully evicted model names.

    Args:
        host:                  Ollama API base URL
        target_model:          Model name to always protect from eviction
        db_path:               Override fleet.db path (default: FLEET_DIR/fleet.db)
        idle_threshold_hours:  Only evict if keepalive > this many hours
        protect:               Additional model names to never evict
    """
    try:
        loaded = get_loaded_models(host)
    except RuntimeError:
        return []

    if not loaded:
        return []

    active_task_models = get_active_task_models(db_path)

    # Build protection set: target + explicitly protected + active-task models
    safe = set(active_task_models)
    if target_model:
        safe.add(target_model)
    if protect:
        safe.update(protect)

    evicted = []
    for m in loaded:
        name = m.get("name", "")
        if name in safe:
            continue

        idle, _reason = is_idle(m, active_task_models, idle_threshold_hours)
        if not idle:
            continue

        if evict_model(name, host):
            evicted.append(name)

    return evicted


# ── Full diagnostics ──────────────────────────────────────────────────────────

def diagnose(
    host: str = DEFAULT_HOST,
    target: str | None = None,
    db_path: Path | None = None,
    idle_threshold_hours: float = DEFAULT_IDLE_THRESHOLD_HOURS,
) -> dict:
    """Full diagnostic report: loaded models, idle status, DB task correlation."""
    loaded = get_loaded_models(host)
    installed = get_installed_models(host)
    active_count = get_active_task_count(db_path)
    active_task_models = get_active_task_models(db_path)

    models_report = []
    for m in loaded:
        name = m.get("name", "")
        size_gb = round(m.get("size", 0) / 1e9, 2)
        expires_at = m.get("expires_at", "")
        idle, reason = is_idle(m, active_task_models, idle_threshold_hours)
        is_target = (name == target)
        models_report.append({
            "name": name,
            "size_gb": size_gb,
            "expires_at": expires_at,
            "is_idle": idle,
            "is_target": is_target,
            "status": "TARGET" if is_target else ("IDLE" if idle else "ACTIVE"),
            "reason": reason,
        })

    total_vram_gb = sum(m["size_gb"] for m in models_report)
    idle_vram_gb = sum(m["size_gb"] for m in models_report if m["is_idle"] and not m["is_target"])

    return {
        "host": host,
        "target": target,
        "idle_threshold_hours": idle_threshold_hours,
        "active_fleet_tasks": active_count,
        "active_task_models": sorted(active_task_models),
        "loaded_count": len(loaded),
        "installed_count": len(installed),
        "total_vram_gb": round(total_vram_gb, 2),
        "idle_vram_gb": round(idle_vram_gb, 2),
        "models": models_report,
        "blockers": [m["name"] for m in models_report if m["is_idle"] and not m["is_target"]],
    }


def clean_idle(
    host: str = DEFAULT_HOST,
    target: str | None = None,
    dry_run: bool = False,
    db_path: Path | None = None,
    idle_threshold_hours: float = DEFAULT_IDLE_THRESHOLD_HOURS,
) -> dict:
    """Diagnose then evict idle models. Returns full report + eviction results."""
    report = diagnose(host, target, db_path, idle_threshold_hours)
    blockers = report["blockers"]

    if not blockers:
        return {
            **report, "evicted": [], "failed": [],
            "message": "Nothing to evict — no idle blockers found",
        }

    evicted = []
    failed = []
    for name in blockers:
        if dry_run:
            evicted.append({"name": name, "status": "dry_run"})
        else:
            ok = evict_model(name, host)
            evicted.append({"name": name, "status": "evicted" if ok else "failed"})
            if not ok:
                failed.append(name)

    return {
        **report,
        "evicted": evicted,
        "failed": failed,
        "message": f"Evicted {len(evicted) - len(failed)}/{len(blockers)} idle models",
    }


# ── Config helper ─────────────────────────────────────────────────────────────

def load_host_from_config() -> str:
    """Read ollama_host from fleet.toml; fall back to DEFAULT_HOST."""
    try:
        import tomllib
        toml_path = FLEET_DIR / "fleet.toml"
        if toml_path.exists():
            with open(toml_path, "rb") as f:
                cfg = tomllib.load(f)
            return cfg.get("models", {}).get("ollama_host", DEFAULT_HOST)
    except Exception:
        pass
    return DEFAULT_HOST


def load_idle_threshold_from_config() -> float:
    """Read idle_threshold_hours from fleet.toml; fall back to default."""
    try:
        import tomllib
        toml_path = FLEET_DIR / "fleet.toml"
        if toml_path.exists():
            with open(toml_path, "rb") as f:
                cfg = tomllib.load(f)
            return float(
                cfg.get("models", {}).get("idle_threshold_hours", DEFAULT_IDLE_THRESHOLD_HOURS)
            )
    except Exception:
        pass
    return DEFAULT_IDLE_THRESHOLD_HOURS


# ── Pretty-print output ───────────────────────────────────────────────────────

def _col(text: str, color_code: str) -> str:
    """ANSI colour if stdout is a tty."""
    if sys.stdout.isatty():
        return f"\033[{color_code}m{text}\033[0m"
    return text


def print_report(report: dict, cleaned: bool = False):
    RED, YELLOW, GREEN, CYAN, BOLD, DIM = "31", "33", "32", "36", "1", "2"

    print()
    print(_col("═" * 62, BOLD))
    print(_col("  BigEd CC — Ollama Model Debugger", BOLD))
    print(_col("═" * 62, BOLD))
    print(f"  Host      : {report['host']}")
    if report.get("target"):
        print(f"  Target    : {_col(report['target'], CYAN)}")
    threshold = report.get("idle_threshold_hours", DEFAULT_IDLE_THRESHOLD_HOURS)
    print(f"  Threshold : >{threshold}h keepalive = idle blocker")
    print(f"  Fleet     : {report['active_fleet_tasks']} active task(s)")
    if report["active_task_models"]:
        print(f"  DB refs   : {', '.join(report['active_task_models'])}")
    idle_label = _col(f"{report['idle_vram_gb']:.2f} GB idle", YELLOW)
    print(f"  VRAM      : {report['total_vram_gb']:.2f} GB loaded  |  {idle_label}")
    print()

    models = report["models"]
    if not models:
        print(_col("  No models currently loaded in VRAM.", DIM))
    else:
        print(f"  {'MODEL':<38} {'SIZE':>6}  {'STATUS':<8}  REASON")
        print("  " + "─" * 74)
        for m in models:
            status = m["status"]
            colour = CYAN if status == "TARGET" else (YELLOW if status == "IDLE" else GREEN)
            tag = _col(f"[{status}]", colour)
            print(f"  {m['name']:<38} {m['size_gb']:>5.2f}G  {tag:<17}  {_col(m['reason'], DIM)}")

    blockers = report.get("blockers", [])
    if blockers:
        print()
        label = "EVICTED" if cleaned else "CAN EVICT"
        print(_col(f"  ⚠  {len(blockers)} idle blocker(s) [{label}]:", YELLOW))
        for b in blockers:
            print(f"     • {b}")
        if not cleaned:
            print(_col("  → Run with --clean to free them automatically.", DIM))

    evicted = report.get("evicted", [])
    if evicted:
        print()
        for e in evicted:
            icon = "✓" if e["status"] == "evicted" else ("~" if e["status"] == "dry_run" else "✗")
            colour = GREEN if e["status"] == "evicted" else (DIM if e["status"] == "dry_run" else RED)
            print(f"  {_col(icon, colour)} {e['name']}  [{e['status']}]")
        print()
        print(_col(f"  {report.get('message', '')}", GREEN))

    print()
    print(_col("═" * 62, BOLD))
    print()


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    cfg_host = load_host_from_config()
    cfg_threshold = load_idle_threshold_from_config()

    parser = argparse.ArgumentParser(
        description="Diagnose and clean idle Ollama models blocking fleet startup."
    )
    parser.add_argument("--host", default=cfg_host,
                        help=f"Ollama API host (default from fleet.toml: {cfg_host})")
    parser.add_argument("--target", default=None,
                        help="Model name to protect from eviction (e.g. qwen3:8b)")
    parser.add_argument("--clean", action="store_true",
                        help="Evict idle models from VRAM")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be evicted without doing it")
    parser.add_argument("--json", action="store_true", dest="json_out",
                        help="Output raw JSON report (machine-readable)")
    parser.add_argument("--threshold", type=float, default=cfg_threshold,
                        help=f"Idle threshold in hours (default: {cfg_threshold})")
    args = parser.parse_args()

    try:
        if args.clean or args.dry_run:
            report = clean_idle(args.host, args.target, dry_run=args.dry_run,
                                idle_threshold_hours=args.threshold)
            cleaned = args.clean and not args.dry_run
        else:
            report = diagnose(args.host, args.target,
                              idle_threshold_hours=args.threshold)
            cleaned = False
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json_out:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_report(report, cleaned=cleaned)

    # Exit code: 0=clean, 1=idle blockers remain
    remaining = len(report.get("blockers", []))
    if cleaned:
        remaining = len([e for e in report.get("evicted", []) if e["status"] == "failed"])
    sys.exit(1 if remaining > 0 and not cleaned else 0)


if __name__ == "__main__":
    main()
