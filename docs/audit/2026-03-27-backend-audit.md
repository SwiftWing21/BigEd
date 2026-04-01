# Backend Audit — 2026-03-27

Auditor: pod-backend (automated read-only audit)
Scope: fleet/ Python backend — supervisor modules, db, dashboard, providers, hw_supervisor, config, api_gate, skills, ingest, experiment

---

## Critical (must fix before v1.0)

- **[dashboard.py:207] Dashboard uses raw `sqlite3.connect` instead of `db.get_conn()`** — The dashboard's `get_conn()` helper bypasses db.py's connection management (SQLCipher support, WAL retry, standardized busy_timeout=30s). Dashboard uses timeout=10 vs db.py's timeout=30, which could cause spurious `OperationalError` under load. All DB access should go through db.py per project rules.

- **[scheduler.py:98-106,155-166,192-204] Scheduler uses raw `sqlite3.connect` in 3 places** — `count_pending_tasks()`, `_pending_tasks_by_type()`, and `_predict_queue_growth()` all open raw connections instead of using `db.get_conn()`. This bypasses WAL retry, SQLCipher, and connection pooling. These run every 30s in the main loop.

- **[federation_manager.py:103-112] FederationManager uses raw `sqlite3.connect`** — `_count_pending()` opens a direct connection to fleet.db, bypassing db.py. Runs every 60s in the heartbeat loop.

- **[db.py:524-565] `claim_task` race condition on fallback path** — After the atomic `UPDATE...WHERE(SELECT)` for general tasks, the subsequent SELECT to find the claimed task (`ORDER BY id DESC LIMIT 1`) could return a task claimed by a *different* concurrent caller if two agents claim simultaneously. The affinity path has the same issue. Should use `RETURNING id` (SQLite 3.35+) or track the rowcount/changes.

- **[dashboard.py:300] Variable scoping bug in `_alert_monitor`** — Line 300 uses `cfg = cfg if 'cfg' in dir() else _load_config()` which is fragile — `dir()` checks local scope but `cfg` is defined inside a try block that may not have executed if `HW_STATE_JSON` doesn't exist. This could cause `NameError` or use stale config.

- **[db.py:139-163] `get_conn()` returns non-pooled connections with `check_same_thread=False`** — Every call creates a new connection. With workers + dashboard + supervisor all calling this frequently, there's no connection pooling or lifecycle management. Combined with `check_same_thread=False`, connections created in one thread and used in another without synchronization could corrupt data under heavy concurrency.

## High (should fix before v1.0)

- **[supervisor.py:191-192] Heartbeat file write silently swallowed** — The `.supervisor_heartbeat` write has `except Exception: pass` which means if the heartbeat stops being written (disk full, permission error), Dr. Ders will think the supervisor is dead and may take incorrect action. Should log at minimum.

- **[dashboard.py:896-920] `api_code_stats` runs subprocess without `CREATE_NO_WINDOW`** — The `subprocess.run(["git", ...])` calls at lines 897 and 914 don't set `creationflags`, violating the project rule for Windows-safe subprocess spawning.

- **[hw_supervisor.py:529] Hardcoded idle_offset for ambient estimation** — `idle_offset = 12` comment says "reasonable estimate for RTX 3080 Ti in a case" but this is hardware-specific. Should be configurable in fleet.toml or auto-calibrated, especially since the project aims to support diverse GPU profiles.

- **[providers.py:44] Usage logger silently drops failed writes** — In `_flush_loop`, the `except Exception: pass` on line 44 means usage data (cost tracking) can be silently lost. For a billing-relevant code path, this should at least log a warning.

- **[dashboard.py:95-99] SSE client list and alert list are in-memory only** — `_sse_clients` and `_alerts` are lost on dashboard restart. Alerts should be persisted to DB (partially done via `db.get_alerts` at line 1394, but the primary alert path is still in-memory).

- **[dashboard.py:1303] f-string SQL table name in `api_data_stats`** — `conn.execute(f"SELECT COUNT(*) FROM {table}")` uses string interpolation for table names. While protected by the `ALLOWED_FLEET_TABLES` frozenset whitelist above, this pattern is fragile — if the whitelist check is ever removed or bypassed, it becomes SQL injection.

- **[process_manager.py:293-294] Incorrect `IO_COUNTERS` struct definition** — The `IO_COUNTERS` struct has `_fields_ = [("ReadOperationCount", ctypes.c_uint64)] * 6` which creates 6 fields all named "ReadOperationCount". This is technically incorrect (Windows JOBOBJECT expects 6 differently-named fields). May cause incorrect memory layout for the `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` struct, making the memory limit ineffective.

- **[skills/db_encrypt.py:45] Raw `sqlite3.connect` bypasses db.py** — The encryption status check opens a direct connection. This is somewhat justified (checking if DB is encrypted before db.py can open it), but should be documented as an intentional exception.

- **[skills/account_review.py:52] Raw `sqlite3.connect` to launcher DB** — Uses `sqlite3.connect(str(LAUNCHER_DB), timeout=10)` directly. Should use a shared data access pattern.

- **[dashboard.py:553,999,1316] Multiple raw `sqlite3.connect` calls to rag.db and tools.db** — These databases are separate from fleet.db, so db.py doesn't apply. However, they lack WAL mode and consistent error handling. Consider a thin wrapper for non-fleet DBs.

## Medium (fix soon after v1.0)

- **[config.py:20-21] `load_config()` has no error handling** — If `fleet.toml` is missing or malformed, this raises an unhandled exception. Other callers (hw_supervisor, dashboard) have their own fallback, but direct callers will crash. Should return empty dict or have a documented exception contract.

- **[config.py:30-36] Module-level config cache (`_cfg`) is never invalidated automatically** — `reload_config()` exists but must be called manually. If fleet.toml is edited while the dashboard or worker is running, they'll use stale config until restart. The scheduler does periodic reload (every 5min), but dashboard and workers don't.

- **[db.py:166-174] `_retry_write` catches only `sqlite3.OperationalError`** — If the database is accessed via `sqlcipher3`, the exception class is `sqlcipher3.OperationalError`, not `sqlite3.OperationalError`. Writes through SQLCipher may not get retry protection.

- **[experiment.py:313-319] GPU lock timestamp parsing assumes wrong format** — `_acquire_gpu_lock` writes `now_iso` with ISO format (`%Y-%m-%dT%H:%M:%S` with T separator), but reads with `time.strptime(row["acquired_at"], "%Y-%m-%d %H:%M:%S")` (space separator). This means locks may never be recognized as expired, causing permanent GPU lock.

- **[health_monitor.py:296-302] `get_circuit_breaker_status` potential TypeError** — If `tripped_at` is None, the `max(0, int(window - (now - state["tripped_at"])))` expression in `cooldown_remaining` will raise `TypeError`. The ternary condition protects this, but the logic is fragile.

- **[dashboard.py:371-376] `_alert_monitor._failures` using function attribute for state** — Line 371 `_alert_failure_count = getattr(_alert_monitor, '_failures', 0) + 1` sets attribute on the function object. This works but is unconventional and not thread-safe — the monitor thread could have stale reads.

- **[ingest_blueprint.py] No input validation on POST body fields** — `api_ingest_add_source` passes user-provided `batch_size` directly to the DB without type validation. A string value would cause a downstream error. Similarly, `dataset` and other fields aren't length-limited.

- **[providers.py:82] `_circuit_state.setdefault` adds "cooldowns" key inconsistently** — The initial state created by `setdefault` doesn't include "cooldowns", which is then accessed via `.get("cooldowns", 0)` on line 91. Works due to `.get()` default, but the data model is inconsistent.

- **[dashboard.py:72-73] `_is_recent` uses `datetime.utcnow()` (deprecated)** — `datetime.utcnow()` is deprecated in Python 3.12+ in favor of `datetime.now(timezone.utc)`. Used throughout dashboard and health_monitor.

- **[hw_supervisor.py:453-454] macOS subprocess call missing `CREATE_NO_WINDOW`** — `subprocess.run(["sysctl", ...])` in `detect_gpu_config` doesn't set creationflags. While this only runs on macOS (where the flag is 0 anyway), the pattern is inconsistent with project standards.

## Low (nice to have)

- **[dashboard.py:4658 lines] Dashboard is too large** — At 4658 lines, dashboard.py is well above the 200-line decomposition threshold. While blueprints have been extracted, the main file still handles 30+ endpoints, SSE, alerts, config loading, and background threads. Further decomposition would improve maintainability.

- **[scheduler.py] `_json_log` function duplicated** — The `_json_log` helper is defined in supervisor.py, process_manager.py, boot_sequence.py, and scheduler.py with identical implementations. Should be in a shared module.

- **[providers.py:264-282] Hardcoded pricing table** — Model pricing is hardcoded in `PRICING` dict. Should be in fleet.toml or fetched from an API for easier updates when pricing changes.

- **[providers.py:300-330] Hardcoded skill-to-complexity mapping** — `SKILL_COMPLEXITY` dict hardcodes 60+ skill classifications. Skills already support `COMPLEXITY` attribute (line 337), but the fallback dict is large and will drift as skills are added/removed.

- **[federation_manager.py] No authentication on federation heartbeat** — Peers accept heartbeat POSTs without authentication beyond optional mTLS. A malicious peer could inject false capacity data.

- **[db.py:222-418] `init_db()` is 196 lines of schema + migrations** — This function handles initial schema, 15+ ALTER TABLE migrations, and 10+ CREATE TABLE statements. Should be split into versioned migration files.

- **[experiment.py:244-272] `_in_auto_window` is defined but never called** — The auto-window time check exists but `_should_auto_approve` doesn't call it, meaning auto-approve is always-on when configured regardless of time window.

- **[hw_supervisor.py:670-673] Fallback pgrep for training detection on non-Windows** — Uses shell command execution with a hardcoded pattern for process detection. Currently safe with hardcoded pattern, but the approach is fragile and should use psutil consistently across all platforms.

## Incomplete Features

- **Experiment Framework (`experiment.py`)**: Core lifecycle works (propose/approve/run/eval/deploy). Missing: `_in_auto_window()` is dead code (time-windowed auto-approval never fires), no rollback implementation for deployed experiments (status set but artifact not restored), no experiment comparison view.

- **Federation (`federation_manager.py`)**: Heartbeat and peer discovery work. Missing: actual task overflow routing (scheduler.py:386-398 queries peers but never dispatches tasks), no peer authentication beyond optional mTLS, no capacity-based routing algorithm.

- **Benchmark Providers (`providers.py:425-452`)**: `benchmark_providers()` is a stub that returns static data. The full benchmarking pipeline (send same prompt to all providers, measure quality) is not implemented.

- **ML Task Routing (`providers.py:457-499`)**: Delegates to `ml_router.py` when available, with IQ-heuristic fallback. The ML model training pipeline exists but depends on external sklearn model on disk.

- **Predictive Scaler (`scheduler.py:293-310`)**: References `predictive_scaler.py` for ML-based scaling. Falls back to simple heuristic (`_predict_queue_growth`) which only looks at 5-10 minute task rate trends.

- **Multi-tenant DB isolation (`db.py:24-38`)**: `get_tenant_db_path()` creates per-tenant directories, but no callers pass `tenant_id` to `get_conn()`. Multi-tenancy is architecturally prepared but not wired up.

## Positive Findings (things done well)

- **Atomic task claiming** — `claim_task()` uses `UPDATE...WHERE(SELECT)` pattern to eliminate SELECT-then-UPDATE race conditions. The core pattern is correct even though the result-fetch has a minor race.

- **WAL retry with jittered backoff** — `_retry_write()` implements exponential backoff with random jitter, preventing thundering herd on write contention. The 30s busy_timeout at the SQLite level provides additional protection.

- **Atomic file writes throughout** — hw_supervisor's `write_state()` uses `tempfile.mkstemp` + `os.replace` for crash-safe JSON writes. fleet.toml updates use the same pattern.

- **Circuit breakers at multiple levels** — Both providers.py (API-level) and health_monitor.py (skill-level) have circuit breakers with configurable thresholds, cooldown windows, and exponential backoff.

- **Structured process lifecycle** — The supervisor restructure into 5 focused modules (process_manager, scheduler, health_monitor, federation_manager, boot_sequence) is clean. Each has a clear responsibility and tick-based interface.

- **Skill contract compliance** — All 10+ skills spot-checked have SKILL_NAME, DESCRIPTION, VERSION, COMPLEXITY, REQUIRES_NETWORK, and `run(payload, config)` signature. Lazy `import db` inside functions prevents circular imports.

- **Comprehensive DB schema with migrations** — `init_db()` handles safe ALTER TABLE migrations with column existence checks. Foreign keys, indexes, and WAL mode are properly configured.

- **Security headers and RBAC** — Dashboard has CSP headers, CORS configuration, role-based access control via `security.py`, CSRF tokens, and API call attribution logging with audit trail.

- **Thermal-aware GPU governance** — Dr. Ders implements a sophisticated park-and-guard pattern with tiered model fallback, sticky GPU assignments, cooldown dampening, and ambient temperature estimation.

- **Cost tracking and budget enforcement** — api_gate.py provides session-level budget caps, provider whitelisting, TTL expiry, drain modes, and ring buffer call recording. Anomaly detection alerts on unusual spend.

- **Error handling is consistently `except Exception:`** — No bare `except:` found in the entire fleet codebase (verified via grep). The code_suite skill even checks for this anti-pattern.

- **All `urlopen` calls have explicit timeouts** — Verified via grep — every `urllib.request.urlopen` call has a `timeout=` parameter (2-120s depending on operation).

- **Windows-safe subprocess spawning** — All `subprocess.Popen` calls in supervisor modules include `creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)` (except 2 `subprocess.run` calls in dashboard noted above).
