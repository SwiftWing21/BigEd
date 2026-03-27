# Fleet Workflow Hardening — Design Spec

## Goal

Audit and fix chokepoints, unintended loops, death spirals, and memory leaks across the fleet's core task workflow systems. Surgical fixes to critical + high severity issues found during deep-dive analysis of scheduler, health monitor, dashboard, and ingest subsystems.

**Scope:** 13 fixes across 4 subsystems. Pure hardening — no feature additions, no behavior changes to happy paths.

**Execution:** 4-pod `/team-orchestrator` run, each pod owns one subsystem. No file overlap.

## Findings Summary

Two independent deep-dive analyses identified 15+ issues. This spec addresses the 13 rated Critical or High:

| Severity | Count | Examples |
|----------|-------|---------|
| Critical | 4 | Circuit breaker memory leak, SSE client leak, evolution cascade, ingest offset corruption |
| High | 9 | Research loop overlap, federation peer accumulation, VRAM reactive eviction, config staleness |

## Architecture

No new modules. All fixes are surgical edits to existing files:

```
fleet/
├── health_monitor.py      ← pod-health (3 fixes)
├── scheduler.py            ← pod-scheduler (2 fixes)
├── skills/
│   ├── evolution_coordinator.py  ← pod-scheduler (1 fix)
│   └── research_loop.py          ← pod-scheduler (1 fix)
├── marathon.py             ← pod-scheduler (1 fix)
├── idle_evolution.py       ← pod-scheduler (bonus efficiency fix)
├── dashboard.py            ← pod-dashboard (3 fixes)
├── ingest_manager.py       ← pod-ingest (2 fixes)
└── tests/
    └── test_supervisor_restructure.py  ← all pods append tests
```

---

## Pod 1: pod-health — Health Monitor Hardening

### Fix 1.1: Circuit Breaker Memory Cap

**Problem:** `_breakers` dict stores failure lists per skill with no size limit. Skills that fail intermittently (below trip threshold) accumulate failures forever. After 2+ weeks: 200 skills × 10,000 failures × ~250 bytes = 500MB+.

**File:** `fleet/health_monitor.py` — `_breakers` dict, `circuit_breaker_record_failure()`, `circuit_breaker_is_open()`

**Fix:**
- Cap each skill's failures list to 1000 entries
- On `record_failure()`, if `len(failures) > 1000`, trim to most recent 500 (halving avoids per-call overhead)
- Add `_MAX_BREAKER_FAILURES = 1000` and `_BREAKER_TRIM_TARGET = 500` constants
- Also add periodic full cleanup: skills with no failures in the last `window` seconds get their entries removed entirely

**Memory ceiling:** 200 skills × 1000 failures × 250 bytes = 50MB max (down from unbounded).

### Fix 1.2: Recovery Log Efficiency

**Problem:** `_recovery_log` uses list with manual slice rotation: `_recovery_log[:] = _recovery_log[-200:]`. This copies the entire tail on every 201st entry.

**File:** `fleet/health_monitor.py` — `_recovery_log`, `_log_recovery()`, `get_recovery_log()`

**Fix:**
- Replace `list` with `collections.deque(maxlen=200)`
- `_log_recovery()` becomes just `.append()` — deque handles eviction automatically
- `get_recovery_log()` returns `list(_recovery_log)` (snapshot)
- Remove `_MAX_RECOVERY_LOG` constant (maxlen handles it)

### Fix 1.3: Health Tick Stagger on Boot

**Problem:** All 8 `_last_*` interval trackers initialize to `0`, so every health check fires simultaneously on first tick after boot/restart. Causes a DB contention spike + memory spike.

**File:** `fleet/health_monitor.py` — `HealthMonitor.__init__()`

**Fix:**
- On init, set each `_last_*` to `time.time() - random.uniform(0, interval)` where `interval` is that check's period
- Example: `self._last_health_sweep = time.time() - random.uniform(0, 60)` (60s is sweep interval)
- This spreads checks across the first interval window instead of all firing at T=0
- Import `random` (already available in stdlib)

**Tests:**
- `test_circuit_breaker_memory_cap`: Record 2000 failures for one skill, assert failures list <= 1000
- `test_circuit_breaker_cleanup_stale`: Record failures, wait past window, assert stale skill entry removed
- `test_recovery_log_deque`: Append 300 entries, assert len == 200, assert oldest entries evicted
- `test_health_tick_stagger`: Init HealthMonitor, assert all `_last_*` values are > 0 (not default 0)

---

## Pod 2: pod-scheduler — Scheduler + Evolution + Research Hardening

### Fix 2.1: Evolution Cascade Deduplication

**Problem:** `evolution_coordinator.py` `_cross_skill_learning()` dispatches `skill_test` tasks for related skills without checking if those tests are already queued. During marathon runs, this creates cascading chains of 30+ duplicate lifecycle tasks.

**File:** `fleet/skills/evolution_coordinator.py` — `_cross_skill_learning()` (or equivalent function that dispatches related skill tests)

**Fix:**
- Before dispatching `skill_test` for a related skill, query DB:
  ```python
  existing = conn.execute(
      "SELECT id FROM tasks WHERE type='skill_test' AND status IN ('PENDING','RUNNING') "
      "AND payload_json LIKE ?", (f'%"{skill_name}"%',)
  ).fetchone()
  ```
- If existing, skip dispatch and log: `"Skipping skill_test for {skill_name} — already queued (task #{id})"`
- Also add a module-level `_EVOLVING_SKILLS: set` with a 30-minute TTL per entry, as a fast-path check before hitting DB

### Fix 2.2: Research Loop Overlap Guard

**Problem:** Scheduler's research and evolution auto-triggers only check for `status='PENDING'` before dispatching. If a previous run is still `RUNNING`, a new one gets dispatched, creating overlapping chains.

**File:** `fleet/scheduler.py` — `_check_auto_triggers()` (research trigger + evolution trigger sections)

**Fix:**
- Change both pending checks from:
  ```python
  "SELECT COUNT(*) FROM tasks WHERE type=? AND status='PENDING'"
  ```
  to:
  ```python
  "SELECT COUNT(*) FROM tasks WHERE type=? AND status IN ('PENDING','RUNNING')"
  ```
- Applies to: research_loop trigger, evolution_coordinator trigger, model_recommend trigger

### Fix 2.3: Ingest Offset Atomicity

**Problem:** `research_loop.py` writes offset tracking to `source_meta.json` non-atomically. If the process crashes mid-write, the file corrupts and offset resets to 0, causing re-ingestion of already-processed rows.

**File:** `fleet/skills/research_loop.py` — offset persistence section

**Fix:**
- Write to `source_meta.json.tmp` first, then `os.replace("source_meta.json.tmp", "source_meta.json")`
- `os.replace()` is atomic on all platforms (POSIX rename, Windows MoveFileEx)
- Wrap in try/except to handle edge case where .tmp write itself fails (don't corrupt existing file)

### Fix 2.4: Scheduler Tick Stagger

**Problem:** Same as Fix 1.3 but for `Scheduler.__init__()`. All `_last_*` trackers start at 0, so research trigger + evolution trigger + model recommend + cost anomaly all fire on first tick.

**File:** `fleet/scheduler.py` — `Scheduler.__init__()`

**Fix:**
- Same pattern: `self._last_X = time.time() - random.uniform(0, INTERVAL_X)`
- Exception: `_last_scale_check` stays at 0 (scaling should run immediately on boot — that's intentional)

### Fix 2.5: VRAM Reactive Eviction

**Problem:** Training VRAM budgets are profile-based estimates. If actual training memory exceeds the estimate, Ollama stays on GPU and eventually OOMs, causing silent worker stalls.

**Files:** `fleet/scheduler.py` — `_check_training()`, `fleet/marathon.py` — VRAM profiles

**Fix:**
- In `_check_training()`, when `training_active` is True:
  ```python
  try:
      import gpu
      gpu_info = gpu.get_gpu_info()
      used_mb = gpu_info.get("memory_used_mb", 0)
      total_mb = gpu_info.get("memory_total_mb", 0)
      if total_mb > 0 and (used_mb / total_mb) > 0.90:
          if not self.pm.ollama_evicted_for_training:
              log.warning(f"VRAM usage {used_mb}/{total_mb}MB (>90%) — evicting Ollama to CPU")
              self.pm.stop_ollama()
              self.pm.start_ollama(gpu=False)
              self.pm.ollama_evicted_for_training = True
  except Exception:
      log.debug("GPU check unavailable for VRAM reactive eviction")
  ```
- Threshold: 90% VRAM used (conservative — leaves 10% headroom)
- Only triggers once per training session (`ollama_evicted_for_training` flag prevents re-triggering)

### Bonus Fix 2.6: Idle Evolution Staleness Cache Efficiency

**Problem:** `idle_evolution.py` clears its entire `_staleness_cache` every 60s, causing a thundering herd of 50+ identical DB queries. Not severity-critical, but since we're already in the scheduler domain, this is a cheap win.

**File:** `fleet/idle_evolution.py` — `_staleness_cache`, staleness query

**Fix:**
- Replace full `.clear()` with selective eviction: keep entries from current + previous bucket
- `_staleness_cache = {k: v for k, v in _staleness_cache.items() if k >= cache_key - 1}`
- Extends effective cache TTL from 60s to 120s, cutting DB queries by ~50%

**Tests:**
- `test_evolution_dedup_skips_queued`: Mock DB with existing PENDING skill_test, assert no new dispatch
- `test_research_trigger_skips_running`: Set research_loop task to RUNNING, assert trigger skips
- `test_offset_atomic_write`: Write offset, simulate crash (delete .tmp mid-write), assert original file intact
- `test_scheduler_tick_stagger`: Init Scheduler, assert `_last_research_trigger` > 0
- `test_vram_reactive_eviction`: Mock gpu.get_gpu_info returning 95% usage, assert stop_ollama + start_ollama(gpu=False) called

---

## Pod 3: pod-dashboard — Dashboard + SSE + Federation Hardening

### Fix 3.1: SSE Client Leak Cleanup

**Problem:** `_sse_clients` list accumulates Queue objects for disconnected clients. Abrupt disconnects (network failure, browser close) leave orphan queues. Over 8+ hours with dashboard access: 1-2MB per 100 orphaned connections.

**File:** `fleet/dashboard.py` — `_sse_clients`, `_broadcast_sse()`, SSE endpoint

**Fix:**
- Add `last_active` timestamp per client: change `_sse_clients` from `list[Queue]` to `list[dict]` with `{"queue": q, "last_active": time.time()}`
- In `_broadcast_sse()`, update `last_active` on successful put
- Add reaper: every 60s in `_broadcast_sse()` (or a dedicated background thread), remove entries where `time.time() - last_active > 120`
- Simpler alternative: just use the existing `_broadcast_sse()` error path more aggressively — on `queue.put()` failure OR if queue is full (`queue.full()`), remove immediately

### Fix 3.2: Federation Peer TTL

**Problem:** `_federation_peers` dict accumulates entries for peers that crash/disconnect/get replaced. Entries never expire — only grow.

**File:** `fleet/dashboard.py` — `_federation_peers`, heartbeat endpoint

**Fix:**
- After updating the peer entry in the heartbeat endpoint, add cleanup:
  ```python
  now = time.time()
  stale = [k for k, v in _federation_peers.items() if now - v["last_seen"] > 7200]
  for k in stale:
      del _federation_peers[k]
  ```
- 7200s = 2 hours. Peers heartbeat every 60s, so anything >2h is definitely dead.
- Run cleanup on every heartbeat (piggyback, no extra thread needed)

### Fix 3.3: Rate Limiter Eviction

**Problem:** `_rate_limits` dict creates entries for every unique endpoint accessed, never removes them. With 190+ endpoints × diverse query params over months: 10+ MB.

**File:** `fleet/dashboard.py` — `_rate_limits`, rate limit check function

**Fix:**
- On each rate limit check, if `len(_rate_limits) > 500`, evict entries older than 300s:
  ```python
  if len(_rate_limits) > 500:
      now = time.time()
      _rate_limits = {k: v for k, v in _rate_limits.items() if now - v[0] < 300}
  ```
- Threshold of 500 entries prevents running cleanup on every request (only when dict gets large)
- 300s eviction matches the existing 60s rate window with generous margin

**Tests:**
- `test_sse_reaper_removes_stale`: Create mock client entries with old timestamps, trigger cleanup, assert removed
- `test_federation_peer_ttl`: Add peer with `last_seen` 3 hours ago, trigger heartbeat, assert pruned
- `test_rate_limiter_eviction`: Add 600 entries (400 old, 200 recent), trigger eviction, assert ~200 remain

---

## Pod 4: pod-ingest — Ingest Cache + Staging Hardening

### Fix 4.1: Cache Orphan Cleanup

**Problem:** If `dispatch_staged()` fails, cache batch files stay on disk indefinitely. No eviction for failed dispatches. Over a marathon with 100+ sources: can hit the 2GB cache cap with orphans.

**File:** `fleet/ingest_manager.py` — new `cleanup_orphans()` method

**Fix:**
- Add `cleanup_orphans(max_age_hours=48)` method:
  1. Scan cache directory for all `.jsonl` batch files
  2. For each file, check if it's referenced by any active staging entry (DB query)
  3. If unreferenced AND older than `max_age_hours`, delete it
  4. Log count of cleaned files
- Call `cleanup_orphans()` on IngestManager init (boot cleanup) and every 6 hours via a check in the main dispatch loop
- Also add to `evict_processed()` as a secondary cleanup pass

### Fix 4.2: Failed Dispatch Tracking + Auto-Remove

**Problem:** When `dispatch_staged()` fails for an item, it stays in staging permanently. No retry tracking, no cleanup. Staging area clogs over time.

**File:** `fleet/ingest_manager.py` — `dispatch_staged()`, staging table

**Fix:**
- Add `dispatch_failures` integer column to `ingest_staging` table (default 0)
- On dispatch failure, increment: `UPDATE ingest_staging SET dispatch_failures = dispatch_failures + 1 WHERE id = ?`
- On each dispatch attempt, skip items where `dispatch_failures >= 3`
- Add cleanup: items with `dispatch_failures >= 3` AND older than 24h → auto-delete from staging with warning log
- Run cleanup check in `dispatch_staged()` itself (piggyback, no extra timer)

**Tests:**
- `test_cache_orphan_cleanup`: Create orphan file in cache dir, run cleanup, assert deleted
- `test_cache_cleanup_preserves_active`: Create file referenced in staging, run cleanup, assert preserved
- `test_failed_dispatch_tracking`: Simulate 3 dispatch failures, assert item skipped on 4th attempt
- `test_failed_dispatch_auto_remove`: Simulate 3 failures + 24h age, assert item auto-removed from staging

---

## What This Does NOT Change

- **supervisor.py** — untouched (just restructured)
- **process_manager.py, boot_sequence.py, federation_manager.py** — untouched
- **fleet.toml** — no new config keys (all thresholds are sensible hardcoded defaults)
- **fleet.db schema** — one minor addition: `dispatch_failures` column on `ingest_staging` (backward-compatible, default 0)
- **Happy path behavior** — all fixes are guards, cleanup, and caps for edge cases
- **Worker execution** — no changes to `worker.py` or skill contracts

## Error Handling

All fixes follow project conventions:
- `except Exception:` (never bare `except:`)
- `log.warning()` minimum on caught errors
- Cleanup operations are idempotent (safe to run multiple times)
- DB writes use `db._retry_write()` for WAL safety

## Merge Order

No inter-pod dependencies. All 4 pods touch different files. Merge in any order, run `smoke_test.py --fast` after final merge.

## Success Criteria

1. All new tests pass (target: ~13 new tests)
2. 45/45 existing smoke tests still green
3. No new warnings in `python -c "import health_monitor, scheduler, dashboard, ingest_manager"`
4. Circuit breaker memory stays <50MB after 10,000 simulated failures (test verifies cap)
5. SSE clients cleaned up within 120s of disconnect (test verifies reaper)
