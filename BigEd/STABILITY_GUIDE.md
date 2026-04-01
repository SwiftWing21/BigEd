---
name: 24/7 Stability & Security Guide
description: Comprehensive guide for maintaining S-tier uptime — all fixes from this session, risk map, dependency paths, health checklist
type: project
---

# BigEd CC — 24/7 Stability & Security Guide

## Session Fix History (2026-03-18/19, ~100 commits)

### Boot Sequence Fixes
- **pgrep self-match**: `pgrep -f train.py` matched its own command line → false training detection → blocked boot. Fixed with bracket trick `[t]rain\.py`, then fully replaced with psutil cross-platform detection.
- **Stale hw_state.json**: Old timestamps from previous sessions caused boot timeout. Fixed: delete before hw_supervisor launch.
- **hw_supervisor ghost eviction**: Startup code evicted the maintainer model (loaded by boot Stage 1) as a "ghost". Fixed: known_models set includes ALL tier models.
- **hw_supervisor never writing state**: Model validation HTTP calls (with 5s timeouts) delayed first `write_state()`. Fixed: write "starting" state IMMEDIATELY before any HTTP calls.
- **hw_supervisor WSL launch failures**: `nohup` + `pkill` in WSL was unreliable (pkill killed new process, nohup redirect conflicts). Fixed: launch natively on Windows via `subprocess.Popen(sys.executable)`.
- **Boot model load on missing models**: fleet.toml referenced qwen3:1.7b/4b/0.6b but only 8b was installed. Fixed: model validation at startup + `model-check` CLI + `model-install` CLI.
- **Boot button state race**: Refresh loop auto-toggled Start/Stop based on supervisor status, fighting with boot.py's explicit state management. Fixed: refresh only updates labels, never button.

### Process Management Fixes
- **All WSL/pkill eliminated**: 20+ hardcoded `wsl()`, `wsl_bg()`, `pkill -f`, `pgrep -f` calls replaced with native psutil `_kill_fleet_processes()` + `_kill_ollama()`. Windows defaults to NativeWindowsBridge (was WslBridge).
- **supervisor.py `nice`/`which` crash on Windows**: Unix-only commands wrapped in `sys.platform != "win32"` guard.
- **dag_queue never started**: `enqueue_promotion()` queued events nobody consumed. Fixed: auto-start processor thread on first enqueue.
- **Keepalive during training**: hw_supervisor keepalive ping reloaded models that eviction just freed. Fixed: skip when training or throttled.

### Close Flow Fixes
- **Timer TclError after destroy**: `_schedule_refresh`, `_schedule_hw`, `_schedule_ollama_watch` kept firing on destroyed widgets. Fixed: `_shutdown_gui()` sets all flags to False before destroy.
- **`self.after(2000, destroy)` race**: wsl_bg callback tried to update destroyed app. Fixed: synchronous kill with 8s timeout, then destroy.

### GUI Fixes
- **Stale agents before Start**: `_ever_seen_roles` from old DB records. Fixed: only populate when `_system_running=True`, clear on boot start.
- **`_db_path` crash**: DAL extraction used wrong attribute name. Fixed: use module-level `DB_PATH`.
- **`_log_output` before widget init**: Called during `_build_ui()` before `_output_text` created. Fixed: use `print()` for early init errors.
- **Header stats too small**: Font 9pt → 11pt, header height 50px → 60px.

### Model Management Fixes
- **Park+guard pattern**: hw_supervisor no longer auto-scales UP. Only scales DOWN under pressure. Operator controls baseline via `model-profile`.
- **Recovery logic**: If no model loaded (crash/eviction), loads smallest available from tier system.
- **Softened thermal thresholds**: Sustained 75→82°C, burst 78→85°C, VRAM high 75→85%. Prevents unnecessary model swaps.

---

## Risk Map: Dependencies & Vulnerabilities

### CRITICAL Dependencies (if these break, fleet stops)
| Dependency | Version | Risk | Mitigation |
|-----------|---------|------|------------|
| **Python** | 3.11+ | LOW — stable | Pin in pyproject.toml |
| **Ollama** | latest | MEDIUM — API changes | Pin to known-good version. `/api/tags`, `/api/ps`, `/api/generate` are stable. Monitor `/api/pull` for breaking changes. |
| **SQLite** | stdlib | LOW — bundled | WAL mode well-tested. Watch for PRAGMA changes in new Python versions. |
| **psutil** | 5.9+ | LOW — stable | Core process management. No alternatives needed. |
| **pynvml (nvidia-ml-py)** | 12+ | MEDIUM — GPU driver coupling | Must match NVIDIA driver version. Test after driver updates. Graceful fallback if missing. |

### HIGH Dependencies (if these break, features degrade)
| Dependency | Version | Risk | Mitigation |
|-----------|---------|------|------------|
| **anthropic** | 0.40+ | MEDIUM — API versioning | Pin to known-good. `resp.usage` fields could change. Monitor Anthropic changelog. |
| **google-genai** | 0.3+ | MEDIUM — rapid iteration | Gemini SDK changes frequently. `usage_metadata` field names may shift. |
| **flask** | 3.0+ | LOW — mature | Dashboard + web launcher. Stable API. |
| **customtkinter** | 5.2+ | MEDIUM — single maintainer | GUI framework. No commercial support. Monitor GitHub for abandonment. Fallback: tkinter raw. |
| **tomlkit** | 0.14+ | LOW — stable | Config writes. Could fallback to regex if needed (but worse). |
| **httpx** | 0.27+ | LOW — mature | HTTP client for skills. Could fallback to urllib. |

### MEDIUM Dependencies (optional features)
| Dependency | Risk | Notes |
|-----------|------|-------|
| **playwright** | Browser skills only. Heavy install (~200MB). |
| **paho-mqtt** | MQTT inspection only. Stable. |
| **discord.py** | Discord bot only. API changes with Discord's cadence. |
| **sqlcipher3-wheels** | DB encryption only. Community fork — monitor for staleness. |
| **boto3** | AWS key rotation only. Heavy dependency tree. |

### Dependency Upgrade Paths (S-Tier Quality)
1. **Before any upgrade**: Run `refactor_verify.py` full check
2. **After upgrade**: Run smoke (19/19) + GUI smoke (8/8) + soak
3. **Pin versions** in pyproject.toml — never use `>=` without upper bound
4. **Test GPU driver updates separately** from Python/pip updates
5. **Ollama updates**: Test `/api/tags`, `/api/ps`, `/api/generate` responses haven't changed
6. **Anthropic SDK updates**: Check `resp.usage` field names, `resp.content[0].text` structure
7. **CustomTkinter updates**: Test all extracted UI modules (consoles, settings, boot, omnibox)

---

## System Health Checklist (24/7 Uptime)

### Every Boot (automatic)
- [ ] Ollama reachable (`/api/tags` responds)
- [ ] Maintainer model loaded on CPU (qwen3:0.6b)
- [ ] hw_supervisor checkpoints pass (GPU readable, Ollama reachable, models known)
- [ ] hw_state.json written within 10s
- [ ] Supervisor starts, STATUS.md written within 45s
- [ ] Workers register (agents appear in DB)
- [ ] Conductor model loaded on CPU

### Every 5 Minutes (hw_supervisor)
- [ ] GPU temperature < 82°C sustained
- [ ] VRAM usage < 85%
- [ ] At least 1 worker model loaded
- [ ] Thermal data flowing to hw_state.json

### Every 10 Minutes (watchdog)
- [ ] No agent with 3+ consecutive failures (quarantine trigger)
- [ ] No tasks stuck in REVIEW > 30min
- [ ] DLP scan on recent task results (secrets + PII)
- [ ] Knowledge file integrity check (SHA-256 manifest)

### Every Hour (operator check)
- [ ] Dashboard responsive (`/api/fleet/health`)
- [ ] Log files rotating (< 10MB each)
- [ ] Audit log being written (HMAC-signed)
- [ ] Token budget not exceeded for any skill
- [ ] No CRITICAL OOM risk for loaded model configuration

### Daily
- [ ] Cost tracking delta — no >20% regression
- [ ] Backup run (`scripts/backup.sh`)
- [ ] Check for Ollama model updates (`model-check`)
- [ ] Review audit log for anomalies

---

## Codebase Risk Areas

### Files That Must Never Break
| File | Why | Last Hardened |
|------|-----|---------------|
| fleet/db.py | All data access — if this breaks, everything stops | 2026-03-19 |
| fleet/hw_supervisor.py | GPU/VRAM safety — if this breaks, OOM crashes | 2026-03-19 |
| fleet/supervisor.py | Worker lifecycle — if this breaks, no task execution | 2026-03-19 |
| fleet/worker.py | Skill dispatch — if this breaks, tasks pile up | 2026-03-19 |
| BigEd/launcher/ui/boot.py | Startup sequence — if this breaks, app won't launch | 2026-03-19 |

### Known Remaining Issues (from 12-agent audit)
| Severity | Count | Category |
|----------|-------|----------|
| LOW | 12 | Skill whitelist cache, session boundary double-fire, redundant sys.path inserts |
| MEDIUM | 8 | SSE client list race, connection leaks in dashboard, auto-start during walkthrough |
| Info | 3 | Dead config (gpu_max_sustained_c unused), IO_COUNTERS struct names |

### Security Posture
- **OWASP LLM Top 10**: B+ (strong on 7/10)
- **Input scanning**: 14 secret patterns + 8 injection patterns + 4 PII patterns + base64 decode
- **Output guardrails**: toxicity, PII redaction, refusal detection, topic rails
- **Auth**: Dashboard bearer token (empty = open for local dev)
- **Encryption**: SQLCipher available but not enabled by default
- **Audit**: HMAC-signed JSON event log with rotation

---

## S-Tier Upgrade Path

### Phase 1: Reliability (current → 99.9%)
- Fix remaining MEDIUM audit issues (SSE race, connection leaks)
- Add `_alive` flag to all timer chains (prevent TclError after destroy)
- Move `_stop_system` kill to background thread (prevent UI freeze)
- Add escalating backoff for worker crash-loop (15s → 30s → 60s → cap)

### Phase 2: Observability (99.9% → 99.95%)
- Centralized structured logging (JSON, not text)
- Health endpoint aggregation (single `/api/health` with all subsystem status)
- Uptime tracking with historical chart in dashboard
- Alert escalation (watchdog → audit log → operator notification)

### Phase 3: Resilience (99.95% → 99.99%)
- Automatic recovery from all known failure modes (no operator intervention)
- Supervisor self-restart on crash (systemd/launchd watchdog)
- DB connection pool with health monitoring
- Graceful degradation cascade (if Ollama dies → queue tasks → auto-restart → resume)

### Phase 4: Security (D+ → A)
- Enable SQLCipher encryption by default
- Enable review pipeline by default (already done in fleet.toml)
- Dashboard TLS by default (self-signed cert auto-generated)
- RBAC roles (operator → admin → viewer)
- API call attribution logging
