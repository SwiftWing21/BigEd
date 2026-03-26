# Operations Swimlane Diagrams

Covers: Dr. Ders Health Monitoring, Self-Healing & Circuit Breaker, Backup/Recovery, Dashboard API.

---

## 9. Dr. Ders Health Monitoring

```mermaid
sequenceDiagram
    participant DrDers
    participant GPU
    participant Ollama
    participant HWState
    participant Supervisor
    participant Workers

    DrDers->>DrDers: Load thermal config from fleet.toml
    DrDers->>GPU: Detect GPU vendor (NVIDIA / AMD / Intel / None)

    alt GPU unavailable
        Note over DrDers: CPU-only mode — skip GPU telemetry
    end

    loop Every 5s poll
        DrDers->>GPU: Read VRAM %, GPU temp, power draw
        DrDers->>DrDers: Read CPU temp via psutil

        Note over DrDers: Thermal safety gate

        alt Temp > 85°C burst threshold
            DrDers->>Ollama: Unload all models (keep_alive=0)
            DrDers->>HWState: Write status="cooldown"
        else Temp > 82°C sustained threshold
            DrDers->>Ollama: Downgrade to lower tier model
            DrDers->>HWState: Write status="transitioning"
        else Temp < 75°C cooldown reached
            Note over DrDers: Thermal pressure cleared — allow upgrade
        end

        Note over DrDers: VRAM pressure check

        alt VRAM > 92% — emergency
            DrDers->>Ollama: Unload current model (keep_alive=0)
            DrDers->>Ollama: Load qwen3:0.6b (emergency tier)
            DrDers->>HWState: Write status="transitioning", tier="emergency"
        else VRAM > 85% — high pressure
            DrDers->>Ollama: Unload current model (keep_alive=0)
            DrDers->>Ollama: Load qwen3:1.7b (reduced tier)
            DrDers->>HWState: Write status="transitioning", tier="reduced"
        else VRAM < 60% — restore
            DrDers->>Ollama: Load qwen3:8b (full tier)
            DrDers->>HWState: Write status="ok", tier="full"
        end

        DrDers->>Ollama: Unload models not in current tier (keep_alive=0)
        DrDers->>Ollama: Keepalive ping for current tier model (window=240s)

        alt Training job detected
            DrDers->>Ollama: Evict all GPU models (exclusive lock for training)
            DrDers->>HWState: Write status="training_lock"
        end

        DrDers->>HWState: Atomic write hw_state.json (tempfile + os.replace)
        Workers->>HWState: Poll status field
        alt status == "transitioning"
            Workers->>Workers: Pause new task claims
        end
    end

    loop Every 30min memory self-check
        DrDers->>DrDers: Compare RSS vs baseline
        alt RSS growth detected
            DrDers->>DrDers: gc.collect()
        end
    end
```

---

## 10. Self-Healing & Circuit Breaker

```mermaid
sequenceDiagram
    participant Supervisor
    participant SelfHealing
    participant CircuitBreaker
    participant AgentMonitor
    participant SkillTracker
    participant Worker

    loop Every 10min health sweep
        Supervisor->>AgentMonitor: Check all agents — last heartbeat time

        alt Heartbeat stale > 300s
            AgentMonitor->>Supervisor: Mark agent unhealthy
            Supervisor->>Worker: Terminate stale process
            Supervisor->>Worker: Spawn replacement worker
        end

        Supervisor->>SelfHealing: Check stuck tasks (running > 600s)
        SelfHealing->>CircuitBreaker: Query circuit state for failing skill

        Note over CircuitBreaker: State machine: CLOSED → OPEN → HALF_OPEN → CLOSED

        alt 3 failures within 5min window
            CircuitBreaker->>CircuitBreaker: Transition CLOSED → OPEN
            CircuitBreaker->>SkillTracker: Quarantine skill
            Note over SkillTracker: Skip skill in dispatch for quarantine_period
        else Circuit OPEN, cooldown elapsed
            CircuitBreaker->>CircuitBreaker: Transition OPEN → HALF_OPEN
            SkillTracker->>Worker: Dispatch single probe task
            alt Probe succeeds
                CircuitBreaker->>CircuitBreaker: Transition HALF_OPEN → CLOSED
                SkillTracker->>SkillTracker: Unquarantine skill
            else Probe fails
                CircuitBreaker->>CircuitBreaker: Remain OPEN, extend quarantine
                SkillTracker->>Supervisor: Notify operator — skill still failing
            end
        else Circuit CLOSED — normal operation
            Note over CircuitBreaker: Pass through, accumulate failure count
        end

        Supervisor->>SkillTracker: Check IQ regression for recently updated skills
        alt IQ regression detected
            SkillTracker->>SkillTracker: Restore previous skill version
            SkillTracker->>Supervisor: Log rollback event
        end
    end
```

---

## 11. Backup / Recovery

```mermaid
sequenceDiagram
    participant Supervisor
    participant BackupManager
    participant FleetDB
    participant RAGDB
    participant Config
    participant BackupStorage

    Supervisor->>BackupManager: Spawn BackupManager thread on boot
    BackupManager->>BackupStorage: Trigger initial backup (trigger="fleet_startup")

    loop Every 20min auto-save (interval_secs=1200)
        BackupManager->>BackupStorage: Create backup dir ~/BigEd-backups/YYYYMMDD_HHMMSS/

        BackupManager->>FleetDB: PRAGMA wal_checkpoint — flush WAL to disk
        BackupManager->>RAGDB: PRAGMA wal_checkpoint — flush WAL to disk

        BackupManager->>FleetDB: Copy fleet.db → backup dir
        BackupManager->>RAGDB: Copy rag.db → backup dir
        BackupManager->>Config: Copy fleet.toml → backup dir
        BackupManager->>BackupStorage: Copy knowledge/ (recursive) → backup dir

        BackupManager->>BackupStorage: PRAGMA integrity_check on backed-up fleet.db
        BackupManager->>BackupStorage: PRAGMA integrity_check on backed-up rag.db

        alt Integrity check passes
            BackupManager->>BackupStorage: Write manifest.json (id, timestamp, trigger, files+sha256, integrity=ok, total_size)
        else Integrity check fails
            BackupManager->>BackupStorage: Write manifest.json (integrity=FAILED)
            BackupManager->>Supervisor: Emit warning — backup integrity failure
        end

        BackupManager->>BackupStorage: Count existing backups
        alt Backup count > depth config (default 10)
            BackupManager->>BackupStorage: Delete oldest backup dir(s)
        end

        BackupManager->>BackupStorage: Check total backup disk usage
        alt Disk usage > warn_disk_usage_pct (80%)
            BackupManager->>Supervisor: Emit disk usage warning
        end
    end

    Note over Supervisor,BackupStorage: Manual Recovery Procedure

    Supervisor->>Supervisor: Operator initiates recovery — shutdown fleet
    BackupStorage->>FleetDB: Copy backup fleet.db → active path
    BackupStorage->>RAGDB: Copy backup rag.db → active path
    BackupStorage->>Config: Copy backup fleet.toml → active path
    Supervisor->>Supervisor: Restart fleet
    Supervisor->>FleetDB: PRAGMA integrity_check — verify restored DB
    alt Integrity ok
        Note over Supervisor: Recovery complete
    else Integrity fails
        Supervisor->>Supervisor: Abort — alert operator, try earlier backup
    end
```

---

## 12. Dashboard API

```mermaid
sequenceDiagram
    participant Client
    participant Flask
    participant SecurityMiddleware
    participant RateLimiter
    participant DB
    participant SSEClients

    Client->>Flask: HTTP request (port 5555)

    Flask->>SecurityMiddleware: before_request hooks

    SecurityMiddleware->>SecurityMiddleware: CORS origin check
    alt CORS mismatch
        SecurityMiddleware-->>Client: 403 Forbidden
    end

    SecurityMiddleware->>SecurityMiddleware: CSRF token validation (POST/PUT/DELETE)
    alt CSRF token invalid or missing
        SecurityMiddleware-->>Client: 403 Forbidden
    end

    SecurityMiddleware->>RateLimiter: Check rate limit (default 10 req/min)
    alt Rate limit exceeded
        RateLimiter-->>Client: 429 Too Many Requests
    end

    SecurityMiddleware->>SecurityMiddleware: Extract role from header/session
    alt Role lacks permission for route
        SecurityMiddleware-->>Client: 403 Forbidden
    end

    Flask->>Flask: Route dispatch — match URL to handler
    Flask->>DB: Execute handler query
    DB-->>Flask: Return result set
    Flask-->>Client: JSON response + headers

    Flask->>Flask: after_request — audit logging
    Note over Flask: GET ops logged at 10% sample rate; all write ops logged at 100%

    alt Write operation (POST/PUT/DELETE/PATCH)
        Flask->>SSEClients: _broadcast_sse({type, data}) to all connected clients
    end

    Note over Client,SSEClients: SSE streaming connection

    Client->>Flask: GET /api/events (SSE endpoint)
    Flask->>SSEClients: Register client in SSE client list
    loop While client connected
        SSEClients-->>Client: Stream server-sent events on write ops
    end
```
