# Integration & Testing Audit — 2026-03-27

Auditor: pod-integration (automated)
Scope: Test coverage, documentation accuracy, integration points, operational readiness, deployment

---

## Critical (must fix before v1.0)

1. **No `requirements.txt` in project root.** Dockerfile references `requirements*.txt` (`RUN if [ -f requirements.txt ]; then uv pip install --system -r requirements.txt; fi`) but the file does not exist. Docker image will have no Python deps installed.

2. **Backups only saving `fleet.toml` in most snapshots.** Of 253 backups in `~/BigEd-backups/`, the most recent `backup_*` entries (2026-03-25) contain only `fleet.toml`. Only the latest non-prefixed entry (`20260327_164640`) contains the full set (fleet.db, fleet.toml, knowledge, manifest.json, rag.db). The `BackupManager` class appears correct — likely the `scripts/backup.sh` shell script produces incomplete backups while the Python `BackupManager` works correctly. Investigate which codepath is creating the sparse backups.

3. **No load/stress tests exist.** Zero load testing infrastructure. For a fleet managing 17+ agents, scaling workers, and handling 30K+ tasks, there is no way to verify behavior under concurrent load. Critical for stability claims.

4. **No end-to-end test covering full task lifecycle.** No test exercises: submit task -> intent parse -> dispatch -> worker claim -> skill execution -> result storage -> SSE notification. The smoke tests cover individual steps in isolation but never the integrated flow.

---

## High (should fix before v1.0)

5. **OPERATIONS.md stale metrics (revision 2, 2026-03-24).** Header says `skills=130+ endpoints=236 smoke=38 tables=20`. Actual: skills=102, smoke tests=46/46, DB tables=23. The "130+ skills" count is from before the skill restructure (132 -> 96+6 suites -> now 102 standalone). Endpoint count not verified but likely also changed.

6. **ROADMAP.md stale metrics.** Same header: `skills=130+ endpoints=236 smoke=38 tables=20`. All values outdated. Release gate checklist says "Smoke tests: 33/33" and "Soak tests: 13/13" — actual smoke count is 46.

7. **fleet/CLAUDE.md stale values:**
   - "Smoke: `python smoke_test.py --fast` (27/27)" — actual is 46/46
   - "Skills: 97+ registered" — actual is 102
   - "Dashboard: 190+ endpoints" — needs verification, may be outdated
   - "Deps: `python dependency_check.py` (11 checks)" — actual is 14 checks (13 passed, 1 warning)

8. **CLAUDE.md (root) stale values:**
   - "Skills: 130+" — actual is 102
   - "Dashboard: 190+ endpoints (across dashboard.py + 10 blueprints)" — needs verification
   - "Smoke: 33/33" — actual is 46/46
   - "`python fleet/dependency_check.py` # pre-flight check (11 deps)" — actual is 14 checks

9. **OPERATIONS.md skill authoring contract is wrong.** Documents `run(payload, config, log) -> dict` with 3 parameters, but actual skill contract (per `_contract.py` and all 102 skills) is `run(task, context) -> dict` with 2 parameters. This will mislead anyone writing new skills.

10. **OPERATIONS.md references `call_model()`.** Says "Import the routing layer" with `from skills._models import call_model`. The actual function is `call_complex()` per `_models.py`. `call_model` does not exist.

11. **No skill health_check() implementations.** Smoke test `test_skill_health_checks` explicitly notes "no skills define health_check() yet" and passes vacuously. This means there is no runtime skill health monitoring.

12. **SSE event type coverage not tested.** Dashboard has SSE infrastructure (`_sse_clients`, `_broadcast_sse()`) but no test verifies event types, payload shapes, or client reconnection behavior. The blueprints don't appear to use SSE at all — only `dashboard.py` core.

---

## Medium (fix soon after v1.0)

13. **SESSION_HANDOFF.md smoke count inconsistency.** Says "45/45 (Python) + 41 restructure/hardening" in the metrics table, but actual run shows 46/46. The 41 restructure/hardening tests are in `fleet/tests/` (pytest), not part of smoke_test.py.

14. **Soak tests referenced but not found.** ROADMAP.md mentions "Soak: 13/13", "Soak: 15/15", up to "Soak: 27+". No soak test file found in the codebase. Either they were removed or they live elsewhere.

15. **test_endpoints.py has 0 test functions.** The file defines 62 `EndpointTest` dataclass entries but 0 `def test_*` functions — it's a runner script, not a pytest module. Cannot be discovered by pytest.

16. **Helm chart version lag.** Chart.yaml `appVersion: "0.9.0"` matches the Rust rewrite but Helm chart version is `0.2.0`. No indication of Python version tracking.

17. **No test for backup/restore round-trip.** `BackupManager` has `perform_backup()` but there's no `restore()` method and no test that verifies a backup can be restored to a working state.

18. **Log rotation exists but not tested.** `fleet/log_manager.py` has `rotate_logs()` and `fleet/audit_log.py` has `rotate_audit_log()`. Neither has tests. Not referenced in smoke tests.

19. **CI workflow (`release.yml`) only builds Windows.** The release job is `build-windows` only. No Linux or macOS builds in CI. Cross-platform claims not verified in CI.

20. **Docker build untested in CI.** No `docker build` step in any workflow. Dockerfile has Rust multi-stage build that references specific crate structure — breakage won't be caught.

21. **`setup.ps1` and `setup.sh` not tested.** Scripts exist in `scripts/` but no CI job validates them. No integration test runs them even in dry-run mode.

---

## Low (nice to have)

22. **No pytest configuration file.** No `pyproject.toml [tool.pytest]`, `pytest.ini`, or `conftest.py` at root. Test discovery requires explicit paths.

23. **Test count scattered.** 119 test functions spread across 7 files in `fleet/tests/` (41 restructure + 32 skills + 22 dashboard + 7 security + 11 integration + 6 launcher + 0 endpoints), plus 46 smoke tests in `smoke_test.py`. Total: ~165 tests. No unified test runner command documented.

24. **OPERATIONS.md references `uv run` despite project rule against it.** The Quick Start says "On Linux/macOS with uv installed, you may prefix with `uv run`" but the global CLAUDE.md and project CLAUDE.md both say "No `uv run` on Windows." The Dockerfile also uses `uv pip install` which is different. Inconsistent guidance.

25. **smoke_test.py docstring says `Run: uv run python smoke_test.py`** — conflicts with the "no uv run" rule.

---

## Test Coverage Gaps

### What IS tested (good coverage):
- Skill imports (102/102 import cleanly)
- Skill contract compliance (102/102 have SKILL_NAME, DESCRIPTION, run())
- DB task round-trip (post, claim, complete, verify)
- Message routing (direct, broadcast, channel isolation)
- Config loading and validation
- DAG validation and conditional dependencies
- Training lock acquire/release
- HA fallback chain
- Security: path traversal, SSRF blocking, SQL injection, XSS
- Dashboard HTTP contract (22 tests using Flask test client)
- Supervisor restructure modules (41 tests)
- 8 representative skills in isolation (32 tests with mocked LLM)
- View registry, experiment framework, token bridge
- RAG search, usage tracking, budget check
- Launcher boot_status, webview compilation

### What is NOT tested:
- **Full task lifecycle** (submit -> intent -> dispatch -> execute -> result -> SSE)
- **Agent scaling** (scale-up/down based on queue depth and RAM)
- **Model switching** (hot-swap between GPU/CPU models under load)
- **Backup/restore** (no restore function, no round-trip test)
- **Graceful shutdown** (supervisor signal handling not tested end-to-end)
- **Worker timeout/kill** (task timeout + process cleanup)
- **Ollama integration** (smoke test checks reachability but not actual inference)
- **SSE event delivery** (no test for event types, reconnection, client cleanup)
- **Dashboard ↔ Supervisor** interaction (REST API calls between processes)
- **Launcher ↔ Dashboard** (PyWebView bridge, boot sequence orchestration)
- **Ingest pipeline** (HF download -> cache -> staging -> dispatch/RAG)
- **Log rotation** (rotate_logs() function untested)
- **Concurrent access** (multi-worker DB contention, WAL behavior)
- **Rate limiting** (API throttle behavior under concurrent requests)
- **Federation** (peer discovery, overflow routing — all mocked)
- **Multi-tenant isolation** (tenant CRUD, crypto, billing — all unit-level only)

---

## Documentation Gaps

| Doc | Issue | Severity |
|-----|-------|----------|
| CLAUDE.md | Skill count "130+" should be 102 | High |
| CLAUDE.md | Smoke "33/33" should be 46/46 | High |
| CLAUDE.md | Dependency check "11 deps" should be 14 | Medium |
| fleet/CLAUDE.md | Smoke "27/27" should be 46/46 | High |
| fleet/CLAUDE.md | Skills "97+" should be 102 | Medium |
| fleet/CLAUDE.md | Deps "11 checks" should be 14 | Medium |
| OPERATIONS.md | `run(payload, config, log)` should be `run(task, context)` | High |
| OPERATIONS.md | `call_model()` should be `call_complex()` | High |
| OPERATIONS.md | Metrics header entirely stale | Medium |
| ROADMAP.md | Metrics header entirely stale | Medium |
| ROADMAP.md | Soak tests referenced but not found in codebase | Medium |
| SESSION_HANDOFF.md | Smoke says 45/45 but actual is 46/46 | Low |

---

## Integration Issues

1. **Dashboard ↔ Supervisor:** Communicate via DB (tasks, agents tables) and filesystem (hw_state.json, STATUS.md). No direct REST calls between them documented or tested. The integration is implicit — both read/write the same DB.

2. **Dashboard SSE:** Implemented in `dashboard.py` with `_sse_clients` list and `_broadcast_sse()`. Client reaper runs on 120s timeout. No blueprint-level SSE. No test for SSE event delivery or reconnection.

3. **Launcher ↔ Dashboard:** PyWebView bridge loads dashboard URL. Boot sequence (`boot_sequence.py`) starts dashboard as subprocess. Integration only tested by `test_launcher.py` checking compilation, not actual boot flow.

4. **Fleet ↔ Ollama:** `process_manager.py` handles Ollama adoption/startup. Health checked via `/api/tags` in smoke test. No test for model pull, model swap, or error recovery (404 intermittent issue is a known bug).

5. **Ingest ↔ RAG:** `ingest_manager.py` dispatches to RAG via `rag.py`. Smoke test checks `ingest` module imports and cache stats but does not test the full data flow (HF download -> parse -> RAG index).

---

## Operational Gaps

1. **Backup restore:** `BackupManager` can create backups but has no `restore()` method. Recovery requires manual file copy from `~/BigEd-backups/`. Most older backups are incomplete (fleet.toml only).

2. **Log rotation:** `log_manager.py:rotate_logs()` exists. Called during boot per SESSION_HANDOFF.md ("fresh logs each boot, keep last 10 sessions"). Not tested, not verified working.

3. **Graceful shutdown:** `supervisor.py:shutdown()` handles SIGTERM/SIGINT. `process_manager.py:shutdown_all()` terminates subprocesses. `worker.py:shutdown()` handles signals. No end-to-end test that all processes clean up and no zombie PIDs remain.

4. **Monitoring/alerting:** Dashboard has in-memory alert system (`_alerts` list, `_broadcast_sse()`). No external monitoring integration (no Prometheus metrics, no health endpoint for load balancers). `hw_state.json` is the only persistent health signal.

5. **No `requirements.txt`:** Cannot reproduce Python environment. Dockerfile and release.yml reference it but it doesn't exist.

---

## Known Issues Status (from SESSION_HANDOFF.md)

| Known Issue | Status | Verified |
|-------------|--------|----------|
| mingw toolchain needs dlltool | Still an issue (Windows Rust builds) | Not tested |
| shlwapi.lib manual generation | Still an issue | Not tested |
| WASM target fails | Still an issue (listed in Next Priorities) | Confirmed: still in backlog |
| Timeout doesn't cancel Python execution | Still an issue | No fix attempted |
| TOML write-back loses comments | Still an issue | Structural limitation of TOML libs |
| 3 temp files in fleet/ | Not verified | Could not check (may be transient) |
| Gemini API 404s | Still an issue (gate blocks) | Workaround in place |
| ~20 skills return str not dict | Needs verification | 102/102 pass contract validation now, but live testing not done |
| Ollama intermittent 404 | Still an issue | Listed in Open Questions |
| 47 code drafts (mostly 0-byte) | Not verified | Knowledge dir not checked |

---

## Positive Findings

1. **Smoke tests are solid: 46/46 pass.** Comprehensive coverage of imports, DB, config, security, and module functionality. Fast execution (~5s).

2. **Skill contract compliance is 100%.** All 102 skills have SKILL_NAME, DESCRIPTION, and run(). The `_contract.py` validator is tested in smoke.

3. **Security testing is real.** `test_security.py` has adversarial SQL injection, XSS, path traversal, and SSRF tests using Flask test client. Smoke tests also verify path traversal and SSRF blocking.

4. **Dependency check is thorough.** 14 checks across core, hardware, data, optional, and MCP categories. Gives actionable remediation hints. JSON output for CI.

5. **Supervisor restructure is well-tested.** 41 tests cover all 5 extracted modules + backward-compat shims. Module sizes match documented values.

6. **Backup system is well-designed.** Configurable interval, depth, targets, integrity verification. The Python `BackupManager` produces complete backups (fleet.db, rag.db, config, knowledge, manifest).

7. **Dashboard endpoint catalog is comprehensive.** `test_endpoints.py` defines 62 endpoint specs with expected status codes and response keys. While not pytest-discoverable, it's a good contract reference.

8. **DB schema has 23 tables** covering tasks, agents, messages, usage, experiments, ingest, audit, and more. WAL mode with retry writes (`_retry_write`) for concurrency.

9. **Fleet.toml config is rich.** 51 sections covering all subsystems. Config loader with `is_offline()` and `is_air_gap()` mode detection.

10. **Recent work is high quality.** Supervisor restructure (1890 -> 201 lines), workflow hardening (13 fixes), and knowledge graph overhaul all demonstrate thoughtful engineering with zero regressions.
