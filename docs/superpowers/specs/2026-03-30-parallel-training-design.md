# Parallel Factorio RL Training — Design Specification

**Date:** 2026-03-30
**Status:** Proposed
**Author:** Claude Opus 4.6
**Depends on:** bridge.py, trainer.py, setup_and_launch.py, process_manager.py, bridge_config.py

---

## 1. Problem Statement

The current training setup runs a single Factorio headless server (port 27015) with a single bridge (port 27016) feeding transitions into one PPO trainer. At ~30-50 ticks/sec with game speed 2x, the policy network (496K params) trains slowly because:

- **Environment-bound**: PPO updates take microseconds; RCON round-trips take milliseconds. The GPU sits idle >95% of the time.
- **Low sample diversity**: one environment means one trajectory at a time. PPO benefits enormously from parallel rollouts with diverse initial conditions.
- **Wasted hardware**: 32GB RAM and 12GB VRAM are barely touched by one instance (~500MB RAM, <100MB VRAM).

Parallel training with N headless instances can achieve near-linear speedup in wall-clock samples/sec, dramatically improving both sample diversity and training throughput.

## 2. Hardware Budget

| Resource | Total | Per Factorio Instance | Per Bridge | Shared Trainer | OS/Fleet Overhead |
|----------|-------|-----------------------|------------|----------------|-------------------|
| RAM | 32 GB | ~400 MB | ~100 MB | ~200 MB | ~4 GB |
| CPU | 12 threads (Ryzen) | ~0.5 thread (headless + 2x speed) | ~0.2 thread (async) | ~1 thread (PPO) | ~2 threads |
| VRAM | 12 GB | 0 | 0 | ~200 MB (policy 496K params) | ~2 GB (Ollama qwen3:8b) |

**Recommended N**: 8 instances at game_speed=2 uses ~4 GB RAM + ~6 CPU threads, leaving comfortable headroom. Maximum safe: 12 instances. At game_speed=1, can push to 16.

**Note**: The teacher LLM (qwen3:8b via Ollama) uses ~5 GB VRAM. Disable the hybrid teacher during parallel training or share one teacher across all instances with rate limiting.

## 3. Architecture Overview

```
                    ┌─────────────────────────────────┐
                    │       Central Trainer            │
                    │  (PPO policy on GPU, single)     │
                    │                                  │
                    │  SharedPolicyServer              │
                    │    - holds canonical weights      │
                    │    - accepts trajectory batches   │
                    │    - runs PPO update              │
                    │    - broadcasts new weights        │
                    └──────────┬───────────────────────┘
                               │ (localhost TCP or shared memory)
              ┌────────────────┼────────────────┐
              │                │                │
    ┌─────────▼──────┐ ┌──────▼────────┐ ┌─────▼─────────┐
    │  Worker 0      │ │  Worker 1     │ │  Worker N-1   │
    │                │ │               │ │               │
    │  Factorio:27015│ │ Factorio:27017│ │ Factorio:...  │
    │  Bridge :27016 │ │ Bridge :27026 │ │ Bridge :...   │
    │  ┌───────────┐ │ │ ┌───────────┐ │ │ ┌───────────┐ │
    │  │RL tick    │ │ │ │RL tick    │ │ │ │RL tick    │ │
    │  │loop       │ │ │ │loop       │ │ │ │loop       │ │
    │  │(local     │ │ │ │(local     │ │ │ │(local     │ │
    │  │ policy    │ │ │ │ policy    │ │ │ │ policy    │ │
    │  │ copy)     │ │ │ │ copy)     │ │ │ │ copy)     │ │
    │  └───────────┘ │ │ └───────────┘ │ │ └───────────┘ │
    └────────────────┘ └───────────────┘ └───────────────┘
```

**Design choice: Separate processes with periodic policy sync.** Rationale:
- Each Factorio server is a separate OS process with its own RCON port. The bridge already runs as its own Python process with asyncio event loop.
- A shared-memory approach would require rewriting the bridge into a multi-tenant asyncio system — high complexity, brittle.
- Ray is overkill for 8-12 workers on one machine and adds a heavyweight dependency.
- Instead: each worker holds a **local copy** of the policy (CPU-only inference is fine for 496K params — ~0.1ms per forward pass). The central trainer holds the **canonical policy on GPU**, collects trajectory batches, runs PPO, and pushes updated weights to all workers.

## 4. Detailed Component Design

### 4.1 Instance Manager (`fleet/factorio/instance_manager.py`)

New module. Spawns and manages N Factorio headless servers.

```python
@dataclass
class InstanceConfig:
    instance_id: int
    rcon_port: int          # 27015 + instance_id * 2
    bridge_port: int        # 27016 + instance_id * 2
    rcon_password: str      # unique per instance
    save_file: str          # biged-sandbox-{instance_id}.zip
    server_data_dir: str    # fleet/factorio/server_data_{instance_id}/
    log_dir: str            # fleet/factorio/logs/instance_{instance_id}/

class InstanceManager:
    """Manages N Factorio headless server + bridge pairs."""

    def __init__(self, n_instances: int, base_config: BridgeConfig):
        self.instances: list[InstanceConfig] = []
        self.server_procs: dict[int, subprocess.Popen] = {}
        self.bridge_procs: dict[int, subprocess.Popen] = {}

    def generate_configs(self) -> list[InstanceConfig]:
        """Create N configs with non-overlapping ports and isolated dirs."""

    def start_instance(self, instance_id: int) -> None:
        """Start one Factorio server + bridge worker pair."""

    def stop_instance(self, instance_id: int) -> None:
        """Graceful shutdown of one instance (bridge API /shutdown, then server kill)."""

    def start_all(self) -> None:
        """Start all N instances sequentially (stagger by 3s to avoid disk contention)."""

    def stop_all(self) -> None:
        """Stop all instances."""

    def restart_instance(self, instance_id: int) -> None:
        """Restart a crashed instance."""

    def health_check(self) -> dict[int, dict]:
        """Poll each bridge API /api/status. Return per-instance health."""

    def get_status(self) -> dict:
        """Aggregate status for dashboard."""
```

#### Port Allocation Scheme

```
Instance 0: Factorio 27015, Bridge 27016   (backwards compatible — existing single-instance)
Instance 1: Factorio 27017, Bridge 27018
Instance 2: Factorio 27019, Bridge 27020
Instance 3: Factorio 27021, Bridge 27022
...
Instance N: Factorio 27015 + N*2, Bridge 27016 + N*2
```

Stride of 2 keeps each pair adjacent. Max 50 instances before hitting ephemeral port ranges (27015 + 100 = 27115, well within safe range).

#### Save File Management

Each instance gets its own save: `biged-sandbox-{instance_id}.zip`. All generated from the same `map-gen-settings.json` (identical ore layout, resource richness, biters off). Create saves sequentially at startup since Factorio's `--create` is a blocking operation.

The save creation step runs once per instance (cached). On subsequent launches, existing saves are reused. A `--recreate-saves` flag forces fresh saves.

#### Isolated Server Data

Each server needs its own `server_data_{id}/` directory to avoid lock contention on `config.ini` and `.lock` files. Structure:

```
fleet/factorio/
  server_data_0/       # existing (instance 0 = current setup)
    config.ini
    server-settings.json
    mods/
  server_data_1/
    config.ini
    server-settings.json
    mods/
  ...
```

The Lua mod is copied to each `server_data_{id}/mods/` during setup.

### 4.2 Bridge Worker Mode (`fleet/factorio/bridge_worker.py`)

New module. A slimmed-down bridge that runs the RL tick loop and sends trajectories to the central trainer instead of running PPO locally.

```python
class BridgeWorker:
    """RL tick loop for parallel training. No local PPO — sends transitions upstream."""

    def __init__(self, instance_config: InstanceConfig, policy: FactorioPolicy,
                 trajectory_queue: multiprocessing.Queue):
        self.config = instance_config
        self.policy = policy          # local CPU copy, periodically refreshed
        self.traj_queue = trajectory_queue
        self._local_buffer: list[Transition] = []
        self._buffer_flush_size = 256  # send batch every 256 steps

    async def ml_tick(self) -> None:
        """Same as FactorioBridge.ml_tick() but:
        - Policy inference on CPU (local copy)
        - Transitions buffered locally, flushed to traj_queue in batches
        - No local PPO update
        - No hybrid teacher (disabled for parallel training)
        """

    def refresh_policy(self, state_dict: dict) -> None:
        """Called by trainer process to push new weights. Thread-safe via lock."""

    async def run(self) -> None:
        """Connect to RCON, enter tick loop."""
```

Key differences from `FactorioBridge.ml_tick()`:
1. **No PPO locally** — transitions go to a `multiprocessing.Queue`
2. **No hybrid teacher** — LLM is disabled (single teacher can optionally be shared)
3. **CPU inference** — `self.policy` is on CPU; 496K params means <0.1ms per forward pass
4. **Batch flushing** — transitions buffered locally and flushed every 256 steps to reduce IPC overhead

### 4.3 Central Trainer (`fleet/factorio/central_trainer.py`)

New module. Single process that owns the GPU policy and runs PPO.

```python
class CentralTrainer:
    """Collects trajectories from N workers, runs PPO, distributes weights."""

    def __init__(self, policy: FactorioPolicy, n_workers: int,
                 config: BridgeConfig):
        self.policy = policy.to("cuda")
        self.optimizer = Adam(self.policy.parameters(), lr=config.ml_learning_rate)
        self.n_workers = n_workers
        self.trajectory_queue = multiprocessing.Queue(maxsize=n_workers * 4)
        self.weight_queues: list[multiprocessing.Queue] = [
            multiprocessing.Queue(maxsize=1) for _ in range(n_workers)
        ]
        self._global_step = 0
        self._update_threshold = config.ml_update_every  # total steps across ALL workers
        self._pending_transitions: list[Transition] = []

    def run(self) -> None:
        """Main loop:
        1. Drain trajectory_queue into _pending_transitions
        2. When len >= _update_threshold, run PPO update
        3. Push state_dict to all weight_queues
        4. Log metrics, save checkpoints
        """

    def _ppo_update(self) -> dict:
        """Same as PPOTrainer.update() but operates on the aggregated buffer."""

    def _broadcast_weights(self) -> None:
        """Push policy.state_dict() to each worker's weight_queue.
        Workers pick up new weights between ticks (non-blocking get)."""

    def save_checkpoint(self, global_step: int) -> str:
        """Save canonical policy + optimizer state."""
```

#### Update Cadence

With N=8 workers each producing ~40 transitions/sec, the combined rate is ~320 transitions/sec. With `ml_update_every=512`, PPO runs roughly every 1.6 seconds. This is fast enough to keep the policy fresh without overwhelming the GPU.

The threshold should be configurable: `ml_parallel_update_every` in fleet.toml. Default: `512 * N` (scale with workers so each worker contributes a full rollout before the update).

#### Weight Distribution

Use `multiprocessing.Queue` with `maxsize=1`. Workers do a non-blocking `get()` between ticks:

```python
# In BridgeWorker, between ticks:
try:
    new_weights = self.weight_queue.get_nowait()
    self.policy.load_state_dict(new_weights)
except queue.Empty:
    pass  # keep using current weights
```

This means workers are always slightly behind the latest policy — this is fine and actually helps exploration diversity (a well-known property of IMPALA-style architectures).

### 4.4 Orchestrator (`fleet/factorio/parallel_launcher.py`)

Entry point that wires everything together.

```python
def main():
    """
    1. Parse args (--instances N, --resume)
    2. Create InstanceManager
    3. Generate/verify save files
    4. Start central trainer process
    5. Start N bridge worker processes
    6. Start N Factorio server subprocesses
    7. Monitor health, restart crashed instances
    8. Handle SIGINT/SIGTERM — graceful shutdown
    """
```

Process tree:
```
parallel_launcher.py (orchestrator)
  |-- central_trainer (Process)
  |-- bridge_worker_0 (Process) --> factorio_server_0 (Popen)
  |-- bridge_worker_1 (Process) --> factorio_server_1 (Popen)
  +-- bridge_worker_N (Process) --> factorio_server_N (Popen)
```

### 4.5 Configuration Changes (`fleet.toml`)

New section under `[factorio]`:

```toml
[factorio.parallel]
enabled = false
n_instances = 4                    # number of parallel environments
base_rcon_port = 27015             # instance i = base + i*2
base_bridge_port = 27016           # instance i = base + i*2
stagger_start_secs = 3             # delay between instance launches
update_every_multiplier = 1.0      # ml_update_every * N * multiplier
disable_teacher = true             # disable LLM teacher in parallel mode
worker_buffer_size = 256           # transitions buffered before flush
checkpoint_every_global = 10000    # global steps between checkpoints
max_instances = 16                 # safety cap
game_speed_override = 2            # can override per-instance game speed
```

### 4.6 BridgeConfig Extension

Add fields to the existing `BridgeConfig` dataclass:

```python
# Parallel training fields
parallel_enabled: bool = False
parallel_n_instances: int = 4
parallel_instance_id: int = 0          # set per-worker at launch
parallel_trainer_port: int = 27100     # unused if using multiprocessing.Queue
parallel_disable_teacher: bool = True
parallel_worker_buffer_size: int = 256
```

### 4.7 Dashboard Integration

#### 4.7.1 Aggregate Training Metrics

The central trainer exposes a small Flask API (or writes to a shared JSON file) with:
- Global step count, PPO updates, episodes completed (summed across workers)
- Per-worker: instance_id, steps, episodes, last_reward, tick rate (ticks/sec)
- Policy loss, value loss, entropy (from latest PPO update)
- Worker health: alive/dead/restarting

The existing dashboard proxy at `/api/factorio/*` is extended to aggregate from all bridge APIs. A new endpoint:

```
GET /api/factorio/parallel/status
{
  "enabled": true,
  "n_workers": 8,
  "global_step": 142000,
  "total_episodes": 284,
  "ppo_updates": 277,
  "workers": [
    {"id": 0, "status": "running", "steps": 18200, "ticks_per_sec": 38.2, "last_reward": 0.42},
    {"id": 1, "status": "running", "steps": 17800, "ticks_per_sec": 36.7, "last_reward": 0.31},
    ...
  ],
  "policy_loss": 0.023,
  "value_loss": 0.15,
  "entropy": 1.82
}
```

#### 4.7.2 Multi-Agent Spatial Map

The dashboard spatial map currently shows one agent's position. For parallel training, each worker reports its agent's position via its bridge API. The dashboard aggregates positions and renders N dots on the map, color-coded by instance_id.

This is a lightweight addition — the existing `/api/factorio/spatial` endpoint already returns player position. The dashboard just polls N endpoints instead of 1.

#### 4.7.3 Training Curves

The dashboard training chart (loss/reward over time) adds lines for:
- Global average reward (across all workers)
- Per-worker reward (thin lines, color-coded)
- Throughput chart: total transitions/sec

### 4.8 Process Lifecycle

#### Startup Sequence

1. **Validate resources**: check RAM, estimate N instances fit
2. **Create/verify saves**: for each instance, ensure save file exists
3. **Copy Lua mod**: to each `server_data_{id}/mods/`
4. **Start Factorio servers**: staggered by 3 seconds each
5. **Wait for RCON**: poll each server's RCON port until connected
6. **Start central trainer**: initialize policy (load checkpoint if resuming)
7. **Start bridge workers**: each connects to its Factorio instance
8. **Enter monitoring loop**: health check every 5s, restart crashed instances

#### Individual Instance Restart

If a Factorio server crashes (detected by `proc.poll() is not None`):
1. Kill the corresponding bridge worker (graceful shutdown via flag)
2. Restart the Factorio server on the same ports
3. Wait for RCON connection
4. Restart the bridge worker with the latest policy weights

No training data is lost — the central trainer simply receives fewer transitions during the restart window.

#### Graceful Shutdown

On SIGINT/SIGTERM or keyboard interrupt:
1. Signal all bridge workers to stop (set `_running = False`)
2. Wait up to 5s for workers to flush remaining transitions
3. Run one final PPO update on any remaining transitions
4. Save checkpoint
5. Terminate all Factorio server processes
6. Clean up PID files and lock files

### 4.9 Backward Compatibility

When `parallel.enabled = false` (default), the system behaves exactly as today:
- `setup_and_launch.py` starts one server + one bridge
- `bridge.py` runs its own PPO locally
- No new processes, no new ports

The parallel system is opt-in. The existing `FactorioBridge` class is untouched. `BridgeWorker` is a new class that reuses the same encoder, action space, reward computer, and curriculum manager.

## 5. Data Flow

```
Worker 0 tick loop:                    Central Trainer:
  state = rcon.get_state()             while True:
  grid, feat = encode(state)             batch = trajectory_queue.get(timeout=1)
  action = policy.act(grid, feat)        pending.extend(batch)
  result = rcon.exec_cmd(action)         if len(pending) >= threshold:
  reward = compute_reward()                stats = ppo_update(pending)
  transition = Transition(...)             pending.clear()
  local_buffer.append(transition)          broadcast_weights()
  if len(local_buffer) >= 256:             log_metrics(stats)
    trajectory_queue.put(local_buffer)     save_checkpoint_if_needed()
    local_buffer = []
  # check for new weights
  try: weights = weight_queue.get_nowait()
  except Empty: pass
```

Transitions are plain dataclasses with numpy arrays — they serialize efficiently through `multiprocessing.Queue` (standard Python serialization). For 256 transitions at ~4KB each, a flush is ~1MB — negligible IPC overhead.

## 6. Failure Modes and Mitigations

| Failure | Detection | Mitigation |
|---------|-----------|------------|
| Factorio server crash | `proc.poll() is not None` | Auto-restart with same config |
| Bridge worker crash | Process exit detected by orchestrator | Restart worker, inject latest weights |
| RCON timeout | 3 consecutive failures (existing circuit breaker) | Bridge pauses, retries with backoff |
| Queue full | `trajectory_queue.put()` blocks | Use `put(timeout=5)`, log warning, drop batch |
| Central trainer crash | Orchestrator detects exit | Restart trainer, load latest checkpoint, workers keep collecting |
| Out of memory | psutil RAM check in health loop | Stop lowest-ID instances until RAM < 90% |
| Port conflict | OSError on server start | Skip instance, log error, continue with N-1 |

## 7. Fleet.toml Configuration Reference

```toml
[factorio.parallel]
# Master switch — false means single-instance mode (existing behavior)
enabled = false

# Number of parallel environments
n_instances = 4

# Port allocation base (instance i uses base + i*2 for RCON and base+1 + i*2 for bridge)
base_rcon_port = 27015

# Seconds between instance launches (avoid disk I/O contention)
stagger_start_secs = 3

# PPO update threshold = ml_update_every * n_instances * multiplier
# 1.0 means each worker contributes one full rollout per update
update_every_multiplier = 1.0

# Disable LLM teacher in parallel mode (saves 5GB VRAM for Ollama)
disable_teacher = true

# Transitions buffered per worker before flushing to central trainer
worker_buffer_size = 256

# Global steps between checkpoints (across all workers)
checkpoint_every_global = 10000

# Safety cap — instance_manager refuses to start more than this
max_instances = 16
```

---

## 8. Roadmap

### Phase 1: Dual Instance Proof of Concept

**Goal:** Run 2 Factorio instances with 2 bridge workers feeding one central trainer. Prove the architecture works end-to-end.

**Grading Alignment:** ML Training Throughput -> impact: +15 pts / weight: 10%

**Dependencies:** Current bridge.py, trainer.py, setup_and_launch.py all working (they are).

**Est. Tokens:** ~40k (L)

**Status:** [ ] Not started

#### Files to Create
| File | Lines (est.) | Description |
|------|-------------|-------------|
| `fleet/factorio/instance_manager.py` | ~200 | Spawn/manage N Factorio servers + isolated dirs |
| `fleet/factorio/bridge_worker.py` | ~300 | Slimmed bridge with trajectory queue output |
| `fleet/factorio/central_trainer.py` | ~250 | GPU policy, trajectory collection, PPO, weight broadcast |
| `fleet/factorio/parallel_launcher.py` | ~150 | Entry point: wire instances + trainer + workers |

#### Files to Modify
| File | Changes | Description |
|------|---------|-------------|
| `fleet/factorio/bridge_config.py` | +20 lines | Add `parallel_*` fields to BridgeConfig |
| `fleet/factorio/setup_and_launch.py` | +40 lines | `--parallel N` flag, call instance_manager |
| `fleet/fleet.toml` | +15 lines | `[factorio.parallel]` section |

#### Acceptance Criteria
- [ ] 2 Factorio servers running on ports 27015 and 27017
- [ ] 2 bridge workers collecting transitions independently
- [ ] Central trainer receives transitions from both, runs PPO, pushes weights
- [ ] Training throughput >= 1.8x single instance (accounting for IPC overhead)
- [ ] Checkpoint save/load works with parallel metadata

---

### Phase 2: N-Instance Scaling

**Goal:** Scale to 4-8 instances. Add health monitoring, auto-restart, and resource-aware scaling.

**Grading Alignment:** Reliability -> impact: +10 pts / weight: 15%

**Dependencies:** Phase 1 complete.

**Est. Tokens:** ~25k (M-L)

**Status:** [ ] Not started

#### Files to Create
| File | Lines (est.) | Description |
|------|-------------|-------------|
| `fleet/factorio/parallel_monitor.py` | ~150 | Health check loop, auto-restart, RAM monitoring |

#### Files to Modify
| File | Changes | Description |
|------|---------|-------------|
| `fleet/factorio/instance_manager.py` | +80 lines | Auto-restart, staggered launch, port conflict handling |
| `fleet/factorio/parallel_launcher.py` | +60 lines | Monitor loop, graceful shutdown, SIGINT handler |
| `fleet/factorio/central_trainer.py` | +40 lines | Handle worker count changes, dynamic threshold |
| `fleet/fleet.toml` | +5 lines | max_instances, stagger_start_secs |

#### Acceptance Criteria
- [ ] 8 instances running simultaneously with <80% RAM usage
- [ ] Crashed instance auto-restarts within 15 seconds
- [ ] Central trainer handles workers joining/leaving without crash
- [ ] Throughput scales near-linearly: 8 workers >= 6x single (accounting for overhead)
- [ ] `--instances N` CLI flag validated against hardware resources

---

### Phase 3: Dashboard and FPM Integration

**Goal:** Visualize N parallel agents on the dashboard. Update the Process Manager to control parallel instances.

**Grading Alignment:** Observability -> impact: +8 pts / weight: 10%

**Dependencies:** Phase 2 complete, dashboard.py working.

**Est. Tokens:** ~20k (M)

**Status:** [ ] Not started

#### Files to Modify
| File | Changes | Description |
|------|---------|-------------|
| `fleet/factorio/central_trainer.py` | +30 lines | Expose metrics via Flask API or shared JSON |
| `fleet/dashboard.py` or `fleet/factorio_blueprint.py` | +80 lines | `/api/factorio/parallel/status` endpoint |
| `fleet/templates/dashboard.html` | +100 lines | Multi-agent spatial map, per-worker metrics, throughput chart |
| `fleet/factorio/process_manager.py` | +120 lines | Start/stop/restart parallel mode, per-instance controls |
| `fleet/factorio/bridge_api.py` | +20 lines | Instance ID in status response |

#### Acceptance Criteria
- [ ] Dashboard shows all N agent positions on spatial map (color-coded)
- [ ] Training metrics panel shows aggregate + per-worker stats
- [ ] Throughput chart (transitions/sec) renders correctly
- [ ] Process Manager can start/stop parallel mode and individual instances
- [ ] Existing single-instance dashboard UX unchanged when parallel disabled

---

### Phase 4: Auto-Scaling

**Goal:** Dynamically adjust instance count based on system resources. Start conservatively, scale up if RAM and CPU allow.

**Grading Alignment:** Infrastructure Efficiency -> impact: +5 pts / weight: 5%

**Dependencies:** Phase 2 complete, system_info.py.

**Est. Tokens:** ~15k (M)

**Status:** [ ] Not started

#### Files to Create
| File | Lines (est.) | Description |
|------|-------------|-------------|
| `fleet/factorio/auto_scaler.py` | ~120 | Monitor RAM/CPU, add/remove instances dynamically |

#### Files to Modify
| File | Changes | Description |
|------|---------|-------------|
| `fleet/factorio/instance_manager.py` | +40 lines | `add_instance()` / `remove_instance()` at runtime |
| `fleet/factorio/parallel_launcher.py` | +30 lines | Auto-scaler integration |
| `fleet/fleet.toml` | +5 lines | Auto-scale config (min/max instances, RAM ceiling) |

#### Auto-Scale Algorithm
```
every 30 seconds:
    ram_pct = psutil.virtual_memory().percent
    cpu_pct = psutil.cpu_percent(interval=1)

    if ram_pct > 85 and n_instances > min_instances:
        remove_instance(highest_id)
        log("Scaled down to %d instances (RAM: %d%%)", n_instances, ram_pct)

    elif ram_pct < 60 and cpu_pct < 70 and n_instances < max_instances:
        add_instance()
        log("Scaled up to %d instances (RAM: %d%%, CPU: %d%%)", n_instances, ram_pct, cpu_pct)
```

#### Acceptance Criteria
- [ ] System starts with `min_instances`, scales up to `max_instances` within 2 minutes
- [ ] RAM spike triggers graceful scale-down (no data loss)
- [ ] Scale events logged and visible on dashboard
- [ ] Manual override: `--instances N` disables auto-scaling

---

## 9. Migration Path

The existing single-instance system is **not modified**. Parallel training is an entirely additive feature:

1. All new code lives in new files (`instance_manager.py`, `bridge_worker.py`, `central_trainer.py`, `parallel_launcher.py`)
2. The existing `bridge.py` + `trainer.py` continue to work as-is for single-instance mode
3. `BridgeWorker` extracts and reuses the `ml_tick()` logic pattern, but is a separate class
4. `CentralTrainer` reuses `PPOTrainer`'s GAE and PPO math but manages multi-worker collection
5. Config is additive: `[factorio.parallel]` section, default `enabled = false`

To switch between modes:
```bash
# Single instance (existing)
python fleet/factorio/setup_and_launch.py

# Parallel (new)
python fleet/factorio/parallel_launcher.py --instances 8
```

## 10. Open Questions

1. **Curriculum sync**: Should all workers train on the same curriculum lesson, or allow independent progression? Same lesson maximizes sample efficiency for that lesson; independent progression explores the curriculum space. **Recommendation**: same lesson (central trainer decides phase advancement based on aggregate progress).

2. **Episode boundary alignment**: Workers finish episodes at different times. Should the PPO update wait for episode boundaries? **Recommendation**: No — collect transitions regardless of episode boundaries. PPO handles `done` flags in GAE computation already.

3. **Save state divergence**: Over many episodes, each instance's world state diverges (ore depletion patterns, entity placement). Is this a feature (diversity) or a bug (non-stationarity)? **Recommendation**: Feature. The soft reset already replenishes ore and clears entities, so divergence is bounded.

4. **Spectator mode**: Can a human spectate one of the parallel instances while training? **Recommendation**: Yes — the Factorio server supports multiplayer. Instance 0 can be the "spectator instance" with `game_speed=2`, while others run at higher speeds.

5. **Teacher sharing**: If the LLM teacher is enabled, should one teacher serve all instances? **Recommendation**: Yes, via a shared teacher process with a request queue. But default to disabled for maximum throughput.

---

## 11. Summary

| Phase | New Files | Modified Files | Est. Lines | Effort |
|-------|-----------|----------------|------------|--------|
| Phase 1 | 4 | 3 | ~900 new, ~75 modified | L (40k tokens) |
| Phase 2 | 1 | 4 | ~150 new, ~180 modified | M-L (25k tokens) |
| Phase 3 | 0 | 5 | ~350 modified | M (20k tokens) |
| Phase 4 | 1 | 3 | ~120 new, ~75 modified | M (15k tokens) |
| **Total** | **6** | **8 unique** | **~1,850** | **~100k tokens** |

Expected speedup with 8 instances: **6-7x** wall-clock samples/sec (accounting for IPC overhead, staggered updates, and restart windows).
