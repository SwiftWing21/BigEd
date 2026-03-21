"""Event trigger system — file watch, scheduled tasks, webhook dispatch.

Watches for events and auto-dispatches fleet tasks via db.post_task().
Thread-safe: designed to be called from the supervisor main loop.

Usage from supervisor:
    from event_triggers import check_all_triggers
    dispatched = check_all_triggers(config)
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("event_triggers")

FLEET_DIR = Path(__file__).parent
DATA_DIR = FLEET_DIR / "data"
SCHEDULE_STATE_FILE = DATA_DIR / "schedule_state.json"

# Thread lock for all mutable state
_lock = threading.Lock()


# ── File Watch Trigger ────────────────────────────────────────────────────────

class FileWatchTrigger:
    """Watch a directory for new files and auto-dispatch ingest tasks.

    On first scan, only files newer than scan_interval are considered
    (prevents re-processing an entire directory of existing files).
    Subsequent scans detect any file not in the known set.
    """

    def __init__(self, watch_dir: str, config: dict):
        self.watch_dir = Path(os.path.expanduser(watch_dir))
        triggers_cfg = config.get("triggers", {})
        self._extensions = set(triggers_cfg.get("file_watch_extensions", [".pdf", ".md", ".txt", ".docx"]))
        self._scan_interval = 30  # seconds
        self._known_files: set[str] = set()
        self._first_scan = True
        self._last_scan = 0.0

    def scan(self) -> list[dict]:
        """Check for new files since last scan.

        Returns list of dicts: [{"path": str, "name": str, "size": int}, ...]
        """
        now = time.time()
        if now - self._last_scan < self._scan_interval:
            return []

        self._last_scan = now

        if not self.watch_dir.exists() or not self.watch_dir.is_dir():
            return []

        new_files = []
        try:
            for entry in self.watch_dir.iterdir():
                if not entry.is_file():
                    continue
                if entry.suffix.lower() not in self._extensions:
                    continue

                fpath = str(entry.resolve())
                if fpath in self._known_files:
                    continue

                # First scan: skip files older than scan_interval
                if self._first_scan:
                    try:
                        mtime = entry.stat().st_mtime
                        if now - mtime > self._scan_interval:
                            self._known_files.add(fpath)
                            continue
                    except OSError:
                        continue

                self._known_files.add(fpath)
                new_files.append({
                    "path": fpath,
                    "name": entry.name,
                    "size": entry.stat().st_size,
                })
        except PermissionError:
            log.warning("[FILE_WATCH] Permission denied reading %s", self.watch_dir)
        except OSError as exc:
            log.warning("[FILE_WATCH] OS error scanning %s: %s", self.watch_dir, exc)

        self._first_scan = False
        return new_files

    def dispatch(self, new_files: list[dict]) -> list[int]:
        """Create ingest tasks for new files. Returns list of task IDs."""
        # Lazy import — db may not be initialized at module load time
        import db

        task_ids = []
        for f in new_files:
            try:
                payload = json.dumps({
                    "source": "file_watch",
                    "file_path": f["path"],
                    "file_name": f["name"],
                    "file_size": f["size"],
                })
                task_id = db.post_task(
                    type_="ingest",
                    payload_json=payload,
                    priority=4,
                    classification="internal",
                )
                task_ids.append(task_id)
                log.info("[FILE_WATCH] Dispatched ingest task #%d for %s", task_id, f["name"])
            except Exception as exc:
                log.error("[FILE_WATCH] Failed to dispatch task for %s: %s", f["name"], exc)
        return task_ids


# ── Scheduled Task Dispatcher ─────────────────────────────────────────────────

class ScheduledTask:
    """Cron-like scheduled task dispatcher.

    Reads [schedules] from fleet.toml and tracks last_run times in
    fleet/data/schedule_state.json to survive restarts.
    """

    def __init__(self, config: dict):
        self._schedules = config.get("schedules", {})
        self._state = self._load_state()

    def _load_state(self) -> dict:
        """Load schedule state from JSON file."""
        if SCHEDULE_STATE_FILE.exists():
            try:
                return json.loads(SCHEDULE_STATE_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("[SCHED] Failed to load schedule state: %s", exc)
        return {}

    def _save_state(self) -> None:
        """Persist schedule state to JSON file."""
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            SCHEDULE_STATE_FILE.write_text(
                json.dumps(self._state, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            log.error("[SCHED] Failed to save schedule state: %s", exc)

    def check_due(self) -> list[dict]:
        """Return tasks that are due to run.

        Each returned dict has: name, skill, payload, interval_hours.
        """
        now = time.time()
        due = []

        for name, spec in self._schedules.items():
            # spec can be a dict (inline table) or we skip non-dict entries
            if not isinstance(spec, dict):
                continue

            skill = spec.get("skill")
            if not skill:
                continue

            interval_hours = spec.get("interval_hours", 24)
            interval_secs = interval_hours * 3600

            last_run = self._state.get(name, 0)
            if now - last_run >= interval_secs:
                due.append({
                    "name": name,
                    "skill": skill,
                    "payload": spec.get("payload", "{}"),
                    "interval_hours": interval_hours,
                })
        return due

    def dispatch(self, due_tasks: list[dict]) -> list[int]:
        """Dispatch due tasks and update last_run state. Returns task IDs."""
        import db

        task_ids = []
        now = time.time()

        for task in due_tasks:
            try:
                # Validate payload is valid JSON string
                payload_str = task["payload"]
                if isinstance(payload_str, dict):
                    payload_str = json.dumps(payload_str)
                # Verify it parses
                json.loads(payload_str)

                task_id = db.post_task(
                    type_=task["skill"],
                    payload_json=payload_str,
                    priority=5,
                    classification="internal",
                )
                task_ids.append(task_id)
                self._state[task["name"]] = now
                log.info(
                    "[SCHED] Dispatched scheduled task #%d: %s (skill=%s, interval=%dh)",
                    task_id, task["name"], task["skill"], task["interval_hours"],
                )
            except Exception as exc:
                log.error("[SCHED] Failed to dispatch %s: %s", task["name"], exc)

        if task_ids:
            self._save_state()
        return task_ids


# ── Webhook Trigger ───────────────────────────────────────────────────────────

# Rate limiter state for webhook endpoint
_webhook_calls: list[float] = []
_WEBHOOK_MAX_PER_MINUTE = 10


def _webhook_rate_ok() -> bool:
    """Check if webhook is under rate limit (max 10/minute). Thread-safe."""
    now = time.time()
    cutoff = now - 60.0

    with _lock:
        # Prune old entries
        _webhook_calls[:] = [t for t in _webhook_calls if t > cutoff]
        if len(_webhook_calls) >= _WEBHOOK_MAX_PER_MINUTE:
            return False
        _webhook_calls.append(now)
        return True


# Valid skill names: alphanumeric + underscore, 1-64 chars
_VALID_SKILL_RE = None


def _is_valid_skill(name: str) -> bool:
    """Validate skill name format."""
    global _VALID_SKILL_RE
    if _VALID_SKILL_RE is None:
        import re
        _VALID_SKILL_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
    return bool(_VALID_SKILL_RE.match(name))


def handle_webhook(data: dict) -> dict:
    """Process an incoming webhook payload and dispatch a task.

    Args:
        data: JSON payload with keys:
            - type (required): skill name to dispatch
            - payload (optional): dict payload for the skill
            - priority (optional): 1-10, default 5
            - assigned_to (optional): agent name

    Returns:
        dict with "task_id" on success, or "error" on failure.
    """
    # Rate limit
    if not _webhook_rate_ok():
        return {"error": "Rate limit exceeded (max 10/minute)", "status": 429}

    # Validate required field
    if not data or not isinstance(data, dict):
        return {"error": "Request body must be a JSON object", "status": 400}

    skill_type = data.get("type")
    if not skill_type:
        return {"error": "Missing required field: type (skill name)", "status": 400}

    if not isinstance(skill_type, str) or not _is_valid_skill(skill_type):
        return {"error": "Invalid skill name: must be lowercase alphanumeric/underscore, 1-64 chars", "status": 400}

    # Build payload
    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        return {"error": "payload must be a JSON object", "status": 400}

    # Tag the payload source
    payload["_trigger_source"] = "webhook"
    payload["_trigger_time"] = datetime.now(timezone.utc).isoformat()

    payload_json = json.dumps(payload)
    priority = data.get("priority", 5)
    assigned_to = data.get("assigned_to")

    # Clamp priority
    try:
        priority = max(1, min(10, int(priority)))
    except (ValueError, TypeError):
        priority = 5

    # Validate assigned_to if provided
    if assigned_to is not None:
        if not isinstance(assigned_to, str) or len(assigned_to) > 64:
            return {"error": "assigned_to must be a string (max 64 chars)", "status": 400}

    # Dispatch
    try:
        import db
        task_id = db.post_task(
            type_=skill_type,
            payload_json=payload_json,
            priority=priority,
            assigned_to=assigned_to,
            classification="internal",
        )
    except Exception as exc:
        log.error("[WEBHOOK] Failed to dispatch task: %s", exc)
        return {"error": f"Dispatch failed: {exc}", "status": 500}

    # Audit log
    try:
        from audit import log_audit
        log_audit(
            actor="webhook",
            action="trigger.webhook",
            resource=f"task:{task_id}",
            detail=f"Webhook dispatched {skill_type} task #{task_id}",
            metadata={"skill": skill_type, "priority": priority, "assigned_to": assigned_to},
        )
    except Exception:
        pass  # Audit failure should never block dispatch

    log.info("[WEBHOOK] Dispatched task #%d: skill=%s priority=%d", task_id, skill_type, priority)
    return {"task_id": task_id, "skill": skill_type, "status": 200}


# ── Unified trigger check (called by supervisor) ─────────────────────────────

# Module-level singletons (created lazily, thread-safe)
_file_watcher: FileWatchTrigger | None = None
_scheduler: ScheduledTask | None = None
_initialized = False


def _ensure_initialized(config: dict) -> None:
    """Lazily initialize trigger singletons."""
    global _file_watcher, _scheduler, _initialized

    if _initialized:
        return

    with _lock:
        if _initialized:
            return

        triggers_cfg = config.get("triggers", {})

        # File watch trigger
        if triggers_cfg.get("file_watch_enabled", False):
            watch_dir = triggers_cfg.get("file_watch_dir", "~/Downloads")
            _file_watcher = FileWatchTrigger(watch_dir, config)
            log.info("[TRIGGERS] File watch enabled: %s", _file_watcher.watch_dir)

        # Scheduled tasks
        schedules = config.get("schedules", {})
        if schedules:
            _scheduler = ScheduledTask(config)
            log.info("[TRIGGERS] Scheduled tasks loaded: %s", list(schedules.keys()))

        _initialized = True


def check_all_triggers(config: dict) -> int:
    """Check file watch + schedules. Returns number of tasks dispatched.

    Call this periodically from the supervisor main loop.
    Thread-safe — uses internal locking for mutable state.
    """
    _ensure_initialized(config)
    dispatched = 0

    # File watch
    if _file_watcher is not None:
        try:
            new_files = _file_watcher.scan()
            if new_files:
                ids = _file_watcher.dispatch(new_files)
                dispatched += len(ids)
        except Exception as exc:
            log.error("[TRIGGERS] File watch error: %s", exc)

    # Scheduled tasks
    if _scheduler is not None:
        try:
            due = _scheduler.check_due()
            if due:
                ids = _scheduler.dispatch(due)
                dispatched += len(ids)
        except Exception as exc:
            log.error("[TRIGGERS] Schedule check error: %s", exc)

    return dispatched


def reset() -> None:
    """Reset trigger state (for testing)."""
    global _file_watcher, _scheduler, _initialized
    with _lock:
        _file_watcher = None
        _scheduler = None
        _initialized = False
        _webhook_calls.clear()
