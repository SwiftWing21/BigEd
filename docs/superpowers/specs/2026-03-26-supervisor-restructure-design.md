# Supervisor Restructure Design Spec

## Goal

Decompose `supervisor.py` (1890 lines) into 5 focused modules with clear boundaries, making each independently testable and understandable. Supervisor.py becomes a ~150-line thin orchestrator. Pure refactor — no behavior changes.

## Architecture

The monolithic `supervisor.py` splits into:

```
supervisor.py  (~150 lines, thin orchestrator)
  ├── boot_sequence.py    — ordered startup: PID, logs, DB, Ollama, dashboard, workers
  ├── process_manager.py  — spawn/kill/respawn all fleet processes
  ├── scheduler.py        — dynamic scaling, auto-triggers, task scheduling
  ├── health_monitor.py   — health sweeps, memory watchdog, circuit breakers, diagnostics
  └── federation_manager.py — peer heartbeat, overflow routing, mTLS, discovery
```

Self_healing.py and diagnostics.py become thin re-export shims pointing to health_monitor.py.

## Module Specifications

### 1. `fleet/process_manager.py` (~400 lines)

**Responsibility:** Own all subprocess lifecycle — start, stop, respawn, resource limits.

**Extracts from supervisor.py:**
- `_find_ollama()`, `_find_running_ollama()`, `_discover_loaded_models()`, `start_ollama()`, `stop_ollama()` (lines 367-500)
- `start_discord_bot()`, `stop_discord_bot()` (lines 503-528)
- `start_dashboard()`, `stop_dashboard()`, `_dashboard_port_alive()` (lines 558-603)
- `start_hw_supervisor()` (lines 606-614)
- `start_worker()`, `_stop_worker()`, `_apply_resource_limits()` (lines 617-734)
- `_ping_ollama_keepalive()`, `_warmup_conductor()` (lines 755-792)
- `get_best_available_model()` (lines 385-414)
- `shutdown()` teardown logic (lines 847-875)
- CPU affinity logic (lines 635-658)

**Does NOT include:** OpenClaw start/stop (removed from boot — already disabled in fleet.toml).

**Class interface:**

```python
class ProcessManager:
    """Owns all fleet subprocess lifecycle."""

    def __init__(self, config: dict):
        self.config = config
        self.worker_procs: dict[str, subprocess.Popen | None] = {}
        self.ollama_proc: subprocess.Popen | None = None
        self.discord_proc: subprocess.Popen | None = None
        self.dashboard_proc: subprocess.Popen | None = None
        self.hw_supervisor_proc: subprocess.Popen | None = None
        self.training_active: bool = False
        self.ollama_evicted_for_training: bool = False
        self.last_busy: dict[str, float] = {}  # agent -> timestamp

    # Ollama
    def start_ollama(self, gpu: bool = True) -> None: ...
    def stop_ollama(self) -> None: ...
    def find_running_ollama(self) -> bool: ...
    def discover_loaded_models(self) -> list[str]: ...
    def get_best_available_model(self) -> str: ...
    def ping_ollama_keepalive(self, keep_alive: str = None, model: str = None) -> None: ...
    def warmup_conductor(self) -> None: ...

    # Workers
    def start_worker(self, role: str) -> None: ...    # affinity + Job Objects
    def stop_worker(self, role: str) -> None: ...
    def get_running_workers(self) -> set[str]: ...

    # Services
    def start_hw_supervisor(self) -> None: ...
    def start_dashboard(self) -> None: ...
    def start_discord_bot(self) -> None: ...

    # Lifecycle
    def check_alive(self) -> None: ...       # respawn dead workers, Dr.Ders, dashboard, Discord
    def shutdown_all(self) -> None: ...      # clean teardown (signal handler)

    # Config
    def update_config(self, config: dict) -> None: ...
```

**State owned:** All `*_proc` variables, `worker_procs`, `last_busy`, `training_active`, `ollama_evicted_for_training`.

**`check_alive()` logic** (extracted from main loop lines 1353-1399):
- Dead workers: mark None, schedule respawn after 15s cooldown
- Disabled workers: remove from worker_procs, don't respawn
- Dashboard: respawn only if port not alive (boot.py may own it)
- Dr. Ders: unconditional respawn
- Discord: unconditional respawn (when not offline)

### 2. `fleet/scheduler.py` (~450 lines)

**Responsibility:** Decide what work to do and when. Dynamic scaling, periodic triggers, task routing.

**Extracts from supervisor.py:**
- Role building: `BASE_ROLES`, `_build_roles()` (lines 58, 135-147)
- Scaling constants: `CORE_AGENTS`, `SCALE_ORDER`, thresholds (lines 62-70)
- Scaling logic: `_count_pending_tasks()`, `_get_running_workers()` (via PM), `_pending_tasks_by_type()`, `_skill_to_role()`, `_load_affinity_map()`, `_next_instance_name()`, `_predict_queue_growth()`, `_get_ram_usage_pct()`, `_should_scale_up()`, `_should_scale_down()` (lines 165-350)
- Main loop scaling block (lines 1178-1351): ML predictor, scale up/down, federation overflow
- Auto-triggers: research (daily), evolution (weekly), ml_bridge (results.tsv watch), model_recommend (6h) (lines 1575-1655)
- Manual mode schedule: `_check_manual_mode_schedule()` (lines 83-132)
- Event triggers dispatch (lines 1708-1717)
- Config reload (lines 1719-1726)
- Cost anomaly throttle (lines 1728-1763)
- Claude capacity bonus (lines 1783-1798)
- Training detection (lines 1412-1479) — calls PM.start/stop_ollama on mode switch

**Class interface:**

```python
class Scheduler:
    """Dynamic scaling, periodic triggers, and task scheduling."""

    def __init__(self, config: dict, pm: ProcessManager):
        self.config = config
        self.pm = pm
        # Internal interval trackers
        self._last_scale_check: float = 0
        self._last_research_trigger: float = 0
        self._last_evolution_trigger: float = 0
        self._last_results_mtime: float = 0
        self._last_model_recommend: float = 0
        self._last_sched_check: float = 0
        self._last_trigger_check: float = 0
        self._last_config_reload: float = 0
        self._last_cost_anomaly_check: float = 0
        self._last_capacity_check: float = 0
        self._last_training_check: float = 0

    def tick(self, now: float) -> None:
        """Called every 5s from main loop. Runs all scheduled checks."""
        self._check_scaling(now)
        self._check_training(now)
        self._check_auto_triggers(now)
        self._check_manual_mode(now)
        self._check_event_triggers(now)
        self._check_cost_anomaly(now)
        self._check_capacity_bonus(now)
        self._reload_config_if_stale(now)

    # Public for external callers
    def build_roles(self) -> list[str]: ...
    def count_pending_tasks(self) -> int: ...
    def get_core_agents(self) -> set[str]: ...
    def get_disabled_agents(self) -> set[str]: ...
```

### 3. `fleet/health_monitor.py` (~700 lines)

**Responsibility:** All health checking, recovery, and protection mechanisms.

**Absorbs entirely:**
- `self_healing.py` (585 lines): agent health checks, task retry, circuit breakers, regression detection, rollback, health sweep, dashboard data endpoints
- `diagnostics.py` (88 lines): quarantine, failure streaks, stuck reviews

**Extracts from supervisor.py:**
- Memory watchdog: `_memory_watchdog()` + constants (lines 878-960)
- Stale task recovery dispatch (lines 1508-1525)
- Semantic watchdog dispatch (lines 1538-1573)
- Context cleanup dispatch (lines 1527-1536)
- Reinforcement/ML router dispatch (lines 1686-1706)
- RAG stale cleanup (lines 1673-1684)
- Cache invalidation (lines 1662-1671)

**Class interface:**

```python
class HealthMonitor:
    """Unified health monitoring, recovery, and protection."""

    def __init__(self, config: dict, pm: ProcessManager):
        self.config = config
        self.pm = pm
        # Internal interval trackers
        self._last_health_sweep: float = 0
        self._last_memory_watchdog: float = 0
        self._last_stale_check: float = 0
        self._last_watchdog: float = 0
        self._last_watchdog_full: float = 0
        self._last_context_cleanup: float = 0
        self._last_feedback_check: float = 0
        self._last_cache_cleanup: float = 0
        self._last_rag_cleanup: float = 0

    def tick(self, now: float) -> None:
        """Called every 5s from main loop. Runs all health checks."""
        self._run_health_sweep(now)
        self._run_memory_watchdog(now)
        self._recover_stale_tasks(now)
        self._run_watchdog(now)
        self._cleanup_contexts(now)
        self._check_feedback(now)
        self._cleanup_caches(now)
        self._cleanup_rag(now)

    # Re-exported from absorbed self_healing.py (dashboard compatibility)
    def check_agent_health(agent_name: str) -> dict: ...
    def recover_agent(agent_name: str) -> dict: ...
    def retry_failed_task(task_id: int, max_retries: int = 3) -> dict: ...
    def circuit_breaker_record_failure(skill_name: str, error: str = "") -> None: ...
    def circuit_breaker_is_open(skill_name: str) -> bool: ...
    def get_circuit_breaker_status() -> list: ...
    def run_health_sweep() -> dict: ...
    def detect_skill_regression(skill_name: str, window_hours: int = 6) -> bool: ...
    def get_rollback_candidates() -> list: ...
    def rollback_skill(skill_name: str) -> dict: ...
    def get_agent_health_summary() -> list: ...
    def get_skill_health_summary() -> list: ...
    def get_recovery_log() -> list: ...

    # Re-exported from absorbed diagnostics.py
    def quarantine_agent(name: str, reason: str) -> None: ...
    def clear_quarantine(name: str) -> None: ...
    def get_failure_streaks(threshold: int = 3) -> list: ...
    def get_stuck_reviews(timeout_minutes: int = 30) -> list: ...
```

**Module-level re-exports:** The health_monitor module also exposes all public functions at module level (not just on the class) so that `from health_monitor import check_agent_health` works directly. The `tick()` method is class-only for the orchestrator.

### 4. `fleet/boot_sequence.py` (~200 lines)

**Responsibility:** The ordered startup sequence that runs once at supervisor launch.

**Extracts from supervisor.py:**
- `_load_secrets()` (lines 24-35)
- `main()` pre-loop setup (lines 963-1141): PID acquire, log rotation, DB init, agent registration, DAG queue start, config load, secrets, Ollama start, model resolution, dashboard (threaded), Dr. Ders, core workers, Discord, federation deferred, backup manager, STATUS.md write
- `_register_views()` (lines 1874-1886)

**Function interface:**

```python
def boot(config: dict = None) -> tuple[ProcessManager, Scheduler, HealthMonitor, FederationManager]:
    """Execute the full supervisor boot sequence.

    Order:
    1. PID acquire (exit if duplicate)
    2. Log rotation
    3. DB init + register supervisor agent
    4. DAG queue start
    5. Load config + secrets (skip in air-gap)
    6. Start Ollama (adopt or launch)
    7. Resolve best model + export override
    8. Initial keepalive ping
    9. Start dashboard (background thread)
    10. Start Dr. Ders
    11. Start core workers (no stagger)
    12. Start Discord (if online)
    13. Deferred federation (background thread)
    14. Start backup manager
    15. Write STATUS.md

    Returns initialized module instances for the main loop.
    """
    ...
```

**OpenClaw excluded:** No start_openclaw call in the boot sequence. The function still exists in process_manager for manual invocation via API if ever re-enabled, but it's not called during boot.

### 5. `fleet/federation_manager.py` (~200 lines)

**Responsibility:** All cross-fleet communication.

**Extracts from supervisor.py:**
- Federation rejoin on startup (lines 1071-1116) → moved to `announce_rejoin()`
- Federation heartbeat broadcast (lines 1800-1851) → part of `tick()`
- Discovery start (lines 1076-1083) → part of boot
- TLS auto-setup (lines 1064-1069) → part of boot

**Note:** Federation overflow *routing* (task forwarding in the scaling block, lines 1258-1349) stays in scheduler.py since it's triggered by queue depth decisions. Federation manager handles the *communication* layer (heartbeat, peer announce, TLS).

**Class interface:**

```python
class FederationManager:
    """Cross-fleet peer communication."""

    def __init__(self, config: dict, pm: ProcessManager):
        self.config = config
        self.pm = pm
        self._last_heartbeat: float = 0

    def tick(self, now: float) -> None:
        """Broadcast status to peers (every 60s)."""
        ...

    def announce_rejoin(self) -> None:
        """Announce rejoin to peers on startup (crash recovery)."""
        ...

    def start_discovery(self) -> None:
        """Start mesh auto-discovery (UDP broadcast + mDNS)."""
        ...

    def setup_tls(self) -> None:
        """Deferred mTLS auto-setup."""
        ...
```

## Backward Compatibility

### self_healing.py shim (~20 lines)

```python
"""Self-healing compatibility shim — imports from health_monitor.py."""
from health_monitor import (
    check_agent_health,
    recover_agent,
    retry_failed_task,
    circuit_breaker_record_failure,
    circuit_breaker_is_open,
    get_circuit_breaker_status,
    run_health_sweep,
    detect_skill_regression,
    get_rollback_candidates,
    rollback_skill,
    get_agent_health_summary,
    get_skill_health_summary,
    get_recovery_log,
)
```

### diagnostics.py shim (~10 lines)

```python
"""Diagnostics compatibility shim — imports from health_monitor.py."""
from health_monitor import (
    quarantine_agent,
    clear_quarantine,
    get_failure_streaks,
    get_stuck_reviews,
)
```

### supervisor.py (~150 lines)

Keeps: `main()`, `write_status_md()`, `_json_log()`, signal handlers, logging setup.
Delegates everything else to the 5 modules.

## Consumers That Import From These Files

Files that import from supervisor.py, self_healing.py, or diagnostics.py and need to keep working:

| Consumer | Current Import | After Restructure |
|----------|---------------|-------------------|
| `dashboard.py` | `from self_healing import ...` | Works via shim |
| `dashboard.py` | `from diagnostics import ...` | Works via shim |
| `worker.py` | (none from supervisor) | No change |
| `boot.py` (launcher) | References `supervisor.py` as script | No change — still runs `python supervisor.py` |
| `skills/_watchdog.py` | `from diagnostics import ...` | Works via shim |
| `lead_client.py` | Calls `/api/fleet/*` | No change (REST) |

## What This Does NOT Change

- **boot.py** (BigEd/launcher/ui/boot.py) — untouched. Still spawns `supervisor.py`.
- **dashboard.py** — untouched. Still 4540 lines. (Separate project if needed.)
- **hw_supervisor.py** — untouched. Dr. Ders keeps its own process.
- **worker.py** — untouched.
- **fleet.toml** — no config changes.
- **Any behavior** — same boot order, same intervals, same logic. Pure structural refactor.

## Error Handling

Each module's `tick()` wraps its work in try/except so one subsystem failure doesn't crash the supervisor loop:

```python
def tick(self, now: float) -> None:
    try:
        self._check_scaling(now)
    except Exception:
        log.warning("Scaling check failed", exc_info=True)
    try:
        self._check_training(now)
    except Exception:
        log.warning("Training check failed", exc_info=True)
    # ...
```

This matches the existing pattern where the main loop wraps everything in `try/except`.

## File Size Targets

| File | Before | After |
|------|--------|-------|
| supervisor.py | 1890 | ~150 |
| self_healing.py | 585 | ~20 (shim) |
| diagnostics.py | 88 | ~10 (shim) |
| process_manager.py | — | ~400 |
| scheduler.py | — | ~450 |
| health_monitor.py | — | ~700 |
| boot_sequence.py | — | ~200 |
| federation_manager.py | — | ~200 |
| **Total** | **2563** | **~2130** |

Net reduction of ~430 lines from removing duplication and dead code during extraction.
