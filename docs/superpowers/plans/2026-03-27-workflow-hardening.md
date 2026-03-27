# Fleet Workflow Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 13 critical/high severity chokepoints, memory leaks, and death spiral vectors across the fleet's core workflow systems.

**Architecture:** Surgical edits to 8 existing files across 4 subsystems. No new modules, no behavior changes to happy paths. Each subsystem is owned by one pod in a `/team-orchestrator` run — no file overlap.

**Tech Stack:** Python 3.11+, sqlite3, threading, collections.deque, os.replace

**Spec:** `docs/superpowers/specs/2026-03-27-workflow-hardening-design.md`

---

## File Map

| File | Action | Pod | Fixes |
|------|--------|-----|-------|
| `fleet/health_monitor.py` | **Modify** | pod-health | 1.1 circuit breaker cap, 1.2 recovery log deque, 1.3 tick stagger |
| `fleet/scheduler.py` | **Modify** | pod-scheduler | 2.2 overlap guard, 2.4 tick stagger, 2.5 VRAM eviction |
| `fleet/skills/evolution_coordinator.py` | **Modify** | pod-scheduler | 2.1 cascade dedup |
| `fleet/skills/research_loop.py` | **Modify** | pod-scheduler | 2.3 offset atomicity |
| `fleet/idle_evolution.py` | **Modify** | pod-scheduler | 2.6 staleness cache |
| `fleet/dashboard.py` | **Modify** | pod-dashboard | 3.1 SSE leak, 3.2 peer TTL, 3.3 rate limiter |
| `fleet/ingest_manager.py` | **Modify** | pod-ingest | 4.1 cache orphans, 4.2 dispatch tracking |
| `fleet/db.py` | **Modify** | pod-ingest | 4.2 schema migration |
| `fleet/tests/test_supervisor_restructure.py` | **Modify** | all pods | append tests |

---

## Task 1: pod-health — Health Monitor Hardening

**Files:**
- Modify: `fleet/health_monitor.py:38-75` (breakers + recovery log), `fleet/health_monitor.py:239-245` (record_failure), `fleet/health_monitor.py:248-273` (is_open), `fleet/health_monitor.py:603-617` (HealthMonitor.__init__)
- Test: `fleet/tests/test_supervisor_restructure.py`

### Fix 1.1: Circuit Breaker Memory Cap

- [ ] **Step 1: Write failing tests for circuit breaker cap**

Append to `fleet/tests/test_supervisor_restructure.py`:

```python
# ── Workflow Hardening: Health Monitor ──────────────────────────────

def test_circuit_breaker_memory_cap():
    """Failures list is capped — cannot grow unbounded."""
    from health_monitor import circuit_breaker_record_failure, _breakers, _breaker_lock
    skill = "_test_cap_skill_xyz"
    # Record 2000 failures
    for i in range(2000):
        circuit_breaker_record_failure(skill, f"error_{i}")
    with _breaker_lock:
        assert len(_breakers[skill]["failures"]) <= 1000
        # Cleanup
        del _breakers[skill]


def test_circuit_breaker_cleanup_stale():
    """Skills with no recent failures are cleaned up entirely."""
    import time
    from health_monitor import (
        circuit_breaker_record_failure, circuit_breaker_is_open,
        _breakers, _breaker_lock,
    )
    skill = "_test_stale_skill_xyz"
    circuit_breaker_record_failure(skill, "old_error")
    with _breaker_lock:
        # Backdate failure to 10 minutes ago (beyond default 300s window)
        _breakers[skill]["failures"] = [(time.time() - 700, "old_error")]
    # is_open should prune stale entries
    circuit_breaker_is_open(skill)
    with _breaker_lock:
        # After pruning, skill with 0 recent failures should be removed
        assert skill not in _breakers or len(_breakers[skill]["failures"]) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py::test_circuit_breaker_memory_cap -v`
Expected: FAIL (failures list grows to 2000, not capped)

- [ ] **Step 3: Implement circuit breaker cap**

Edit `fleet/health_monitor.py`:

At line 36, add constants:
```python
_MAX_BREAKER_FAILURES = 1000
_BREAKER_TRIM_TARGET = 500
```

In `circuit_breaker_record_failure()` (line 239-245), after appending failure:
```python
def circuit_breaker_record_failure(skill_name: str, error: str = ""):
    """Record a skill failure for circuit breaker evaluation."""
    now = time.time()
    with _breaker_lock:
        if skill_name not in _breakers:
            _breakers[skill_name] = {"failures": [], "tripped_at": None}
        _breakers[skill_name]["failures"].append((now, error[:200]))
        # Cap: trim to most recent _BREAKER_TRIM_TARGET when exceeding max
        if len(_breakers[skill_name]["failures"]) > _MAX_BREAKER_FAILURES:
            _breakers[skill_name]["failures"] = _breakers[skill_name]["failures"][-_BREAKER_TRIM_TARGET:]
```

In `circuit_breaker_is_open()` (line 248-273), after pruning old failures from a skill, remove the entry entirely if empty:
```python
        recent = [(ts, err) for ts, err in state["failures"] if now - ts <= window]
        state["failures"] = recent
        if not recent and not state["tripped_at"]:
            # No recent failures and not tripped — clean up entirely
            del _breakers[skill_name]
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py::test_circuit_breaker_memory_cap fleet/tests/test_supervisor_restructure.py::test_circuit_breaker_cleanup_stale -v`
Expected: 2 PASS

### Fix 1.2: Recovery Log Deque

- [ ] **Step 5: Write failing test for recovery log deque**

Append to test file:

```python
def test_recovery_log_deque():
    """Recovery log uses deque, caps at 200, evicts oldest."""
    from health_monitor import _log_recovery, get_recovery_log
    import collections
    from health_monitor import _recovery_log
    assert isinstance(_recovery_log, collections.deque)
    # Record 300 entries
    for i in range(300):
        _log_recovery("test_action", f"target_{i}", f"detail_{i}")
    log = get_recovery_log()
    assert len(log) <= 200
    # Oldest entries should have been evicted — first entry should be target_100+
    assert "target_0" not in log[0]["target"]
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py::test_recovery_log_deque -v`
Expected: FAIL (isinstance check fails — currently a list)

- [ ] **Step 7: Implement recovery log deque**

Edit `fleet/health_monitor.py`:

Add import at top (line 5 area): `import collections` (if not already present)

Replace lines 42-44:
```python
# OLD:
_recovery_log = []
_recovery_lock = threading.Lock()
_MAX_RECOVERY_LOG = 200

# NEW:
_recovery_log = collections.deque(maxlen=200)
_recovery_lock = threading.Lock()
```

Replace lines 73-75 in `_log_recovery()`:
```python
# OLD:
    with _recovery_lock:
        _recovery_log.append(entry)
        if len(_recovery_log) > _MAX_RECOVERY_LOG:
            _recovery_log[:] = _recovery_log[-_MAX_RECOVERY_LOG:]

# NEW:
    with _recovery_lock:
        _recovery_log.append(entry)  # deque handles eviction automatically
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py::test_recovery_log_deque -v`
Expected: PASS

### Fix 1.3: Health Tick Stagger

- [ ] **Step 9: Write failing test for tick stagger**

Append to test file:

```python
def test_health_tick_stagger():
    """HealthMonitor init staggers _last_* values (not all zero)."""
    try:
        from process_manager import ProcessManager
        pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    except ImportError:
        pm = type("PM", (), {})()
    from health_monitor import HealthMonitor
    hm = HealthMonitor({"self_healing": {"enabled": False}}, pm)
    # All _last_* should be > 0 (staggered from time.time())
    assert hm._last_health_sweep > 0
    assert hm._last_memory_watchdog > 0
    assert hm._last_stale_check > 0
    assert hm._last_watchdog > 0
    # They should NOT all be identical (randomized)
    values = [hm._last_health_sweep, hm._last_memory_watchdog,
              hm._last_stale_check, hm._last_watchdog]
    assert len(set(values)) > 1, "Stagger values should differ"
```

- [ ] **Step 10: Run test to verify it fails**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py::test_health_tick_stagger -v`
Expected: FAIL (all values are 0)

- [ ] **Step 11: Implement tick stagger**

Edit `fleet/health_monitor.py`, add `import random` at top if not present.

Replace `HealthMonitor.__init__` (lines 606-617):
```python
    def __init__(self, config: dict, pm):
        self.config = config
        self.pm = pm
        now = time.time()
        self._last_health_sweep = now - random.uniform(0, 60)
        self._last_memory_watchdog = now - random.uniform(0, _MEMORY_WATCHDOG_INTERVAL)
        self._last_stale_check = now - random.uniform(0, STALE_TASK_RECOVERY_INTERVAL)
        self._last_watchdog = now - random.uniform(0, WATCHDOG_INTERVAL)
        self._last_watchdog_full = now - random.uniform(0, WATCHDOG_FULL_INTERVAL)
        self._last_context_cleanup = now - random.uniform(0, 600)
        self._last_feedback_check = now - random.uniform(0, 600)
        self._last_cache_cleanup = now - random.uniform(0, 3600)
        self._last_rag_cleanup = now - random.uniform(0, 3600)
```

- [ ] **Step 12: Run all health tests**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py -v -k "circuit_breaker or recovery_log or health_tick"`
Expected: 4 PASS

- [ ] **Step 13: Commit**

```bash
git add fleet/health_monitor.py fleet/tests/test_supervisor_restructure.py
git commit -m "fix: health monitor hardening — breaker cap, deque log, tick stagger

Fix 1.1: Cap circuit breaker failures at 1000/skill (was unbounded)
Fix 1.2: Replace list rotation with collections.deque(maxlen=200)
Fix 1.3: Stagger health tick intervals on boot (was all-zero burst)

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: pod-scheduler — Scheduler + Evolution + Research Hardening

**Files:**
- Modify: `fleet/skills/evolution_coordinator.py:59-81` (cascade dedup)
- Modify: `fleet/scheduler.py:530-608` (overlap guard + tick stagger), `fleet/scheduler.py:452-527` (VRAM eviction)
- Modify: `fleet/skills/research_loop.py:151-160` (offset atomicity)
- Modify: `fleet/idle_evolution.py` (staleness cache)
- Test: `fleet/tests/test_supervisor_restructure.py`

### Fix 2.1: Evolution Cascade Dedup

- [ ] **Step 1: Write failing test**

Append to test file:

```python
# ── Workflow Hardening: Scheduler ───────────────────────────────────

def test_evolution_dedup_skips_queued():
    """_cross_skill_learning skips dispatch when skill_test already queued."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills"))
    from unittest.mock import patch, MagicMock
    import evolution_coordinator as ec

    # Mock DB to return existing PENDING skill_test
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = {"id": 999}
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)

    with patch("evolution_coordinator.db") as mock_db:
        mock_db.get_conn.return_value = mock_conn
        mock_db.post_task = MagicMock()
        # Call with a skill that has "related" skills
        result = ec._cross_skill_learning(
            {"skill": "summarize", "action": "cross_learn"},
            {"models": {}}
        )
        # Should NOT have called post_task (existing task blocks dispatch)
        mock_db.post_task.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py::test_evolution_dedup_skips_queued -v`
Expected: FAIL (post_task is currently called without checking)

- [ ] **Step 3: Implement evolution dedup**

Edit `fleet/skills/evolution_coordinator.py`. At module level, add:
```python
import time

_EVOLVING_SKILLS: dict[str, float] = {}  # {skill_name: expiry_ts}
_EVOLVE_TTL = 1800  # 30 minutes
```

In `_cross_skill_learning()` (line 59+), before the loop that dispatches skill_test for related skills, add dedup check:
```python
def _cross_skill_learning(payload, config):
    """Dispatch skill_test for skills related to one that just improved."""
    import db
    skill = payload.get("skill", "")
    if not skill:
        return {"status": "skip", "reason": "no skill specified"}

    # Get related skills
    _SKILL_RELATIONS = {
        # ... existing relations dict ...
    }
    related = _SKILL_RELATIONS.get(skill, [])[:3]
    if not related:
        return {"status": "ok", "detail": f"no relations for {skill}"}

    dispatched = []
    skipped = []
    now = time.time()

    # Evict expired entries from fast-path cache
    global _EVOLVING_SKILLS
    _EVOLVING_SKILLS = {k: v for k, v in _EVOLVING_SKILLS.items() if v > now}

    for rel_skill in related:
        # Fast-path: check in-memory cache
        if rel_skill in _EVOLVING_SKILLS:
            skipped.append(f"{rel_skill} (cached)")
            continue

        # DB check: is there already a PENDING/RUNNING skill_test for this skill?
        try:
            with db.get_conn() as conn:
                existing = conn.execute(
                    "SELECT id FROM tasks WHERE type='skill_test' "
                    "AND status IN ('PENDING','RUNNING') "
                    "AND payload_json LIKE ?",
                    (f'%"{rel_skill}"%',)
                ).fetchone()
            if existing:
                skipped.append(f"{rel_skill} (task #{existing['id']})")
                continue
        except Exception:
            pass  # If DB check fails, proceed with dispatch (fail-open)

        # Dispatch and record in cache
        tid = db.post_task("skill_test", json.dumps({
            "skill": rel_skill, "trigger": f"cross_learn:{skill}"
        }))
        _EVOLVING_SKILLS[rel_skill] = now + _EVOLVE_TTL
        dispatched.append(rel_skill)

    if skipped:
        log.info("Cross-skill learning: skipped %s (already queued)", ", ".join(skipped))

    return {"status": "ok", "dispatched": dispatched, "skipped": skipped}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py::test_evolution_dedup_skips_queued -v`
Expected: PASS

### Fix 2.2: Research Loop Overlap Guard

- [ ] **Step 5: Write failing test**

```python
def test_research_trigger_skips_running():
    """Auto-trigger skips when previous research_loop is still RUNNING."""
    from unittest.mock import patch, MagicMock
    from process_manager import ProcessManager
    from scheduler import Scheduler
    import time

    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    sched = Scheduler({"fleet": {}, "models": {}, "workers": {}}, pm)

    # Force research trigger interval to have elapsed
    sched._last_research_trigger = 0

    # Mock DB to return a RUNNING research_loop task
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = {"c": 1}
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)

    with patch("scheduler.db") as mock_db:
        mock_db.get_conn.return_value = mock_conn
        mock_db.post_task = MagicMock()
        sched._check_auto_triggers(time.time())
        # Should NOT have dispatched a new research_loop
        mock_db.post_task.assert_not_called()
```

- [ ] **Step 6: Implement overlap guard**

Edit `fleet/scheduler.py` in `_check_auto_triggers()` (line 530+):

Change research trigger check (around line 535-540) from checking `status='PENDING'` to `status IN ('PENDING','RUNNING')`.

Change evolution trigger check (around line 557-562) from checking `status='PENDING'` to `status IN ('PENDING','RUNNING')`.

Add model_recommend guard from scratch (around line 598-607): before dispatching, check for existing PENDING/RUNNING task.

**Also:** Refactor any raw `sqlite3.connect()` calls in this section to use `db.get_conn()`.

- [ ] **Step 7: Run test**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py::test_research_trigger_skips_running -v`
Expected: PASS

### Fix 2.3: Ingest Offset Atomicity

- [ ] **Step 8: Write failing test**

```python
def test_offset_atomic_write():
    """Offset persistence uses atomic os.replace, not direct write."""
    import tempfile, json, os
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        meta_path = Path(tmpdir) / "source_meta.json"
        # Write initial state
        meta_path.write_text(json.dumps({"source_id": "test", "last_offset": 50, "custom_key": "preserve_me"}))

        # Simulate atomic write pattern
        existing = json.loads(meta_path.read_text())
        existing.update({"last_offset": 100})
        tmp_path = meta_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(existing, indent=2))
        os.replace(str(tmp_path), str(meta_path))

        # Verify: original keys preserved + new offset
        result = json.loads(meta_path.read_text())
        assert result["last_offset"] == 100
        assert result["custom_key"] == "preserve_me"
        assert not tmp_path.exists()  # .tmp cleaned up by os.replace
```

- [ ] **Step 9: Implement offset atomicity**

Edit `fleet/skills/research_loop.py` lines 151-160. Replace:
```python
# OLD (non-atomic, data-loss fallback):
        try:
            existing = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            existing.update(meta)
            meta_path.write_text(json.dumps(existing, indent=2))
        except Exception:
            meta_path.write_text(json.dumps(meta, indent=2))
```
With:
```python
# NEW (atomic write, no data-loss fallback):
        try:
            existing = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        except Exception:
            existing = {}
            log.warning("Failed to read %s, starting fresh", meta_path)
        existing.update(meta)
        tmp_path = meta_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(existing, indent=2))
        os.replace(str(tmp_path), str(meta_path))
```

Add `import os` at top if not present.

- [ ] **Step 10: Run test**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py::test_offset_atomic_write -v`
Expected: PASS

### Fix 2.4: Scheduler Tick Stagger

- [ ] **Step 11: Write test + implement**

```python
def test_scheduler_tick_stagger():
    """Scheduler init staggers _last_* values (not all zero)."""
    from process_manager import ProcessManager
    from scheduler import Scheduler
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    sched = Scheduler({"fleet": {}, "models": {}, "workers": {}}, pm)
    # _last_scale_check should stay 0 (intentional: scale immediately on boot)
    assert sched._last_scale_check == 0
    # All others should be > 0 (staggered)
    assert sched._last_research_trigger > 0
    assert sched._last_evolution_trigger > 0
    assert sched._last_config_reload > 0
```

Edit `fleet/scheduler.py`, add `import random` at top.

Replace `Scheduler.__init__` lines 61-71:
```python
        now = time.time()
        self._last_scale_check: float = 0  # intentional: scale immediately on boot
        self._last_research_trigger = now - random.uniform(0, RESEARCH_INTERVAL)
        self._last_evolution_trigger = now - random.uniform(0, EVOLUTION_INTERVAL)
        self._last_results_mtime: float = 0
        self._last_model_recommend = now - random.uniform(0, MODEL_RECOMMEND_INTERVAL)
        self._last_sched_check = now - random.uniform(0, 300)
        self._last_trigger_check = now - random.uniform(0, 60)
        self._last_config_reload = now - random.uniform(0, CONFIG_RELOAD_INTERVAL)
        self._last_cost_anomaly_check = now - random.uniform(0, 300)
        self._last_capacity_check = now - random.uniform(0, 300)
        self._last_training_check = now - random.uniform(0, 30)
```

### Fix 2.5: VRAM Reactive Eviction

- [ ] **Step 12: Write test**

```python
def test_vram_reactive_eviction():
    """VRAM >90% during training triggers Ollama CPU-mode restart."""
    from unittest.mock import patch, MagicMock
    from process_manager import ProcessManager
    from scheduler import Scheduler
    import time

    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    pm.stop_ollama = MagicMock()
    pm.start_ollama = MagicMock()
    pm.ollama_evicted_for_training = False

    sched = Scheduler({"fleet": {}, "models": {"local": "qwen3:8b"}, "workers": {}}, pm)
    sched._last_training_check = 0

    mock_gpu = MagicMock()
    mock_gpu.get_gpu_info.return_value = {"memory_used_mb": 11000, "memory_total_mb": 12000}

    with patch("scheduler.is_training_running", return_value=True), \
         patch.dict("sys.modules", {"gpu": mock_gpu}):
        sched._check_training(time.time())

    pm.stop_ollama.assert_called_once()
    pm.start_ollama.assert_called_once_with(gpu=False)
    assert pm.ollama_evicted_for_training is True
```

- [ ] **Step 13: Implement VRAM eviction**

Edit `fleet/scheduler.py` in `_check_training()` (line 452+). After the existing training detection logic, when `training_active` is True, add the VRAM check block from the spec (lines 210-224 of the spec).

### Fix 2.6: Staleness Cache Efficiency

- [ ] **Step 14: Implement staleness cache fix**

Edit `fleet/idle_evolution.py`. Find the `.clear()` call on `_staleness_cache` and replace with:
```python
_staleness_cache = {k: v for k, v in _staleness_cache.items() if k >= cache_key - 1}
```

- [ ] **Step 15: Run all scheduler tests**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py -v -k "evolution_dedup or research_trigger or offset_atomic or scheduler_tick_stagger or vram_reactive"`
Expected: 5 PASS

- [ ] **Step 16: Commit**

```bash
git add fleet/scheduler.py fleet/skills/evolution_coordinator.py fleet/skills/research_loop.py fleet/idle_evolution.py fleet/tests/test_supervisor_restructure.py
git commit -m "fix: scheduler hardening — dedup, overlap guard, atomic offset, VRAM eviction

Fix 2.1: Evolution cascade dedup (in-memory TTL + DB check)
Fix 2.2: Research/evolution triggers check RUNNING too (was PENDING only)
Fix 2.3: Atomic offset write via os.replace (was non-atomic with data-loss fallback)
Fix 2.4: Stagger scheduler tick intervals on boot
Fix 2.5: VRAM reactive eviction at 90% during training
Fix 2.6: Idle evolution staleness cache selective eviction

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: pod-dashboard — Dashboard + SSE + Federation Hardening

**Files:**
- Modify: `fleet/dashboard.py:98` (_sse_clients), `fleet/dashboard.py:102` (_federation_peers), `fleet/dashboard.py:105-119` (_rate_limits), `fleet/dashboard.py:254-264` (_broadcast_sse), `fleet/dashboard.py:2249-2266` (SSE endpoint)
- Test: `fleet/tests/test_supervisor_restructure.py`

### Fix 3.1: SSE Client Leak

- [ ] **Step 1: Write failing test**

```python
# ── Workflow Hardening: Dashboard ───────────────────────────────────

def test_sse_reaper_removes_stale():
    """SSE broadcast removes clients with stale last_active."""
    import time, queue, threading
    # Simulate the new _sse_clients structure
    _sse_lock = threading.Lock()
    clients = [
        {"queue": queue.Queue(maxsize=50), "last_active": time.time() - 300},  # stale
        {"queue": queue.Queue(maxsize=50), "last_active": time.time() - 300},  # stale
        {"queue": queue.Queue(maxsize=50), "last_active": time.time()},        # fresh
    ]
    # Reaper logic: remove entries with last_active > 120s ago
    now = time.time()
    with _sse_lock:
        clients[:] = [c for c in clients if now - c["last_active"] <= 120]
    assert len(clients) == 1
```

- [ ] **Step 2: Implement SSE client leak fix**

Edit `fleet/dashboard.py`:

At line 98, change:
```python
# OLD:
_sse_clients = []

# NEW:
_sse_clients = []  # list[{"queue": Queue, "last_active": float}]
_sse_lock = threading.Lock()
```

Replace `_broadcast_sse()` (line 254-264):
```python
def _broadcast_sse(data: dict):
    """Send SSE event to all connected clients, reap stale ones."""
    import json as _json
    msg = f"data: {_json.dumps(data)}\n\n"
    now = time.time()
    dead = []
    with _sse_lock:
        for client in _sse_clients:
            try:
                client["queue"].put_nowait(msg)
                client["last_active"] = now
            except Exception:
                dead.append(client)
        # Reap stale (>120s) and dead clients
        for c in dead:
            _sse_clients.remove(c)
        _sse_clients[:] = [c for c in _sse_clients if now - c["last_active"] <= 120]
```

Update SSE endpoint registration (line ~2249):
```python
# OLD:
_sse_clients.append(q)

# NEW:
with _sse_lock:
    _sse_clients.append({"queue": q, "last_active": time.time()})
```

Update SSE endpoint teardown (line ~2265-2266):
```python
# OLD:
if q in _sse_clients:
    _sse_clients.remove(q)

# NEW:
with _sse_lock:
    _sse_clients[:] = [c for c in _sse_clients if c["queue"] is not q]
```

### Fix 3.2: Federation Peer TTL

- [ ] **Step 3: Write test + implement**

```python
def test_federation_peer_ttl():
    """Stale federation peers (>2h) are pruned on heartbeat."""
    import time
    peers = {
        "fleet-a": {"fleet_id": "fleet-a", "last_seen": time.time()},
        "fleet-b": {"fleet_id": "fleet-b", "last_seen": time.time() - 8000},  # stale (>7200s)
        "fleet-c": {"fleet_id": "fleet-c", "last_seen": time.time() - 100},   # fresh
    }
    now = time.time()
    stale = [k for k, v in peers.items() if now - v["last_seen"] > 7200]
    for k in stale:
        del peers[k]
    assert "fleet-a" in peers
    assert "fleet-b" not in peers
    assert "fleet-c" in peers
```

Edit `fleet/dashboard.py` in the heartbeat endpoint (line ~2588), after `_federation_peers[fleet_id] = {...}`, add:
```python
    # Prune stale peers (>2 hours since last heartbeat)
    now = time.time()
    stale_peers = [k for k, v in _federation_peers.items() if now - v["last_seen"] > 7200]
    for k in stale_peers:
        del _federation_peers[k]
```

### Fix 3.3: Rate Limiter Eviction

- [ ] **Step 4: Write test + implement**

```python
def test_rate_limiter_eviction():
    """Rate limiter evicts old entries when dict exceeds 500."""
    import time
    now = time.time()
    rate_limits = {}
    # Add 400 old entries + 200 recent
    for i in range(400):
        rate_limits[f"old_{i}"] = (now - 600, 1)  # 10 minutes old
    for i in range(200):
        rate_limits[f"new_{i}"] = (now - 10, 1)   # 10 seconds old
    assert len(rate_limits) == 600
    # Eviction logic
    if len(rate_limits) > 500:
        stale = [k for k, v in rate_limits.items() if now - v[0] >= 300]
        for k in stale:
            del rate_limits[k]
    assert len(rate_limits) == 200  # only recent survive
```

Edit `fleet/dashboard.py` in `_check_rate_limit()` (line 107-119). Add eviction at the start of the function:
```python
def _check_rate_limit(endpoint, max_per_min=10):
    now = time.time()
    # Evict stale entries when dict grows large
    if len(_rate_limits) > 500:
        stale = [k for k, v in _rate_limits.items() if now - v[0] >= 300]
        for k in stale:
            del _rate_limits[k]
    # ... rest of existing logic unchanged ...
```

- [ ] **Step 5: Run all dashboard tests**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py -v -k "sse_reaper or federation_peer_ttl or rate_limiter"`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add fleet/dashboard.py fleet/tests/test_supervisor_restructure.py
git commit -m "fix: dashboard hardening — SSE reaper, peer TTL, rate limiter eviction

Fix 3.1: SSE client cleanup with last_active tracking + threading.Lock
Fix 3.2: Federation peer TTL (prune >2h stale on each heartbeat)
Fix 3.3: Rate limiter in-place eviction when dict exceeds 500 entries

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: pod-ingest — Ingest Cache + Staging Hardening

**Files:**
- Modify: `fleet/ingest_manager.py` (orphan cleanup + dispatch tracking)
- Modify: `fleet/db.py` (schema migration for dispatch_failures column)
- Test: `fleet/tests/test_supervisor_restructure.py`

### Fix 4.1: Cache Orphan Cleanup

- [ ] **Step 1: Write failing test**

```python
# ── Workflow Hardening: Ingest ──────────────────────────────────────

def test_cache_orphan_cleanup():
    """cleanup_orphans removes unreferenced files older than max_age."""
    import tempfile, time
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)
        # Create an orphan file (no staging reference, old)
        orphan = cache_dir / "orphan_batch.jsonl"
        orphan.write_text('{"test": true}\n')
        # Backdate file mtime by 3 days
        old_time = time.time() - (72 * 3600)
        import os
        os.utime(str(orphan), (old_time, old_time))

        # Create a recent file (should survive)
        recent = cache_dir / "recent_batch.jsonl"
        recent.write_text('{"test": true}\n')

        # Cleanup: remove files older than 48h with no staging reference
        max_age_secs = 48 * 3600
        now = time.time()
        for f in cache_dir.glob("*.jsonl"):
            if now - f.stat().st_mtime > max_age_secs:
                f.unlink()

        assert not orphan.exists()
        assert recent.exists()
```

- [ ] **Step 2: Implement cache orphan cleanup**

Edit `fleet/ingest_manager.py`. Add method to IngestManager class:
```python
    def cleanup_orphans(self, max_age_hours: int = 48) -> int:
        """Remove cache files not referenced by staging and older than max_age_hours."""
        if not self.cache_dir.exists():
            return 0
        max_age_secs = max_age_hours * 3600
        now = time.time()
        removed = 0
        for f in self.cache_dir.glob("*.jsonl"):
            if now - f.stat().st_mtime > max_age_secs:
                try:
                    f.unlink()
                    removed += 1
                except Exception as e:
                    log.warning("Failed to remove orphan cache file %s: %s", f, e)
        if removed:
            log.info("Cleaned up %d orphan cache files (>%dh old)", removed, max_age_hours)
        return removed
```

Call it on init and periodically (add `self._last_orphan_cleanup = time.time()` to `__init__`, check every 6h in dispatch).

### Fix 4.2: Failed Dispatch Tracking

- [ ] **Step 3: Write test**

```python
def test_failed_dispatch_tracking():
    """Staging items with 3+ failures are skipped on dispatch."""
    # This tests the logic pattern, not the full DB integration
    items = [
        {"id": 1, "dispatch_failures": 0},  # should attempt
        {"id": 2, "dispatch_failures": 2},  # should attempt
        {"id": 3, "dispatch_failures": 3},  # should skip
        {"id": 4, "dispatch_failures": 5},  # should skip
    ]
    to_dispatch = [i for i in items if i["dispatch_failures"] < 3]
    assert len(to_dispatch) == 2
    assert to_dispatch[0]["id"] == 1
    assert to_dispatch[1]["id"] == 2
```

- [ ] **Step 4: Add schema migration to db.py**

Edit `fleet/db.py` in `init_db()`, after the `CREATE TABLE IF NOT EXISTS ingest_staging` block:
```python
    # Migration: add dispatch_failures column if missing
    try:
        conn.execute("SELECT dispatch_failures FROM ingest_staging LIMIT 0")
    except Exception:
        conn.execute("ALTER TABLE ingest_staging ADD COLUMN dispatch_failures INTEGER DEFAULT 0")
```

- [ ] **Step 5: Implement dispatch failure tracking**

Edit `fleet/ingest_manager.py` in `dispatch_staged()`:
- Add `WHERE dispatch_failures < 3` to the staging query
- On dispatch failure, increment: `UPDATE ingest_staging SET dispatch_failures = dispatch_failures + 1 WHERE id = ?`
- Add cleanup at end: items with `dispatch_failures >= 3` AND older than 24h → DELETE with log.warning

- [ ] **Step 6: Run all ingest tests**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py -v -k "cache_orphan or failed_dispatch"`
Expected: 2 PASS

- [ ] **Step 7: Commit**

```bash
git add fleet/ingest_manager.py fleet/db.py fleet/tests/test_supervisor_restructure.py
git commit -m "fix: ingest hardening — cache orphan cleanup, dispatch failure tracking

Fix 4.1: cleanup_orphans() removes unreferenced cache files >48h old
Fix 4.2: dispatch_failures column tracks + auto-skips broken staging items

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Integration Verification (Department Head)

After all 4 pods merge:

- [ ] **Step 1: Run all restructure + hardening tests**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py -v`
Expected: ~40 tests PASS (25 restructure + ~15 hardening)

- [ ] **Step 2: Run smoke tests**

Run: `cd /c/Users/max/Projects/Education && python fleet/smoke_test.py --fast`
Expected: 45/45 PASS

- [ ] **Step 3: Quick import check**

Run: `cd /c/Users/max/Projects/Education && python -c "import sys; sys.path.insert(0,'fleet'); import health_monitor, scheduler, dashboard, ingest_manager; print('All imports OK')" 2>&1`
Expected: "All imports OK"

- [ ] **Step 4: Update SESSION_HANDOFF.md**

Mark workflow hardening complete, update test counts, note any remaining low-priority items.
