# QA Test Plan

## Pre-Test Setup

1. Stop all fleet processes: `wsl pgrep -f supervisor.py` should return empty
2. Run smoke test: `wsl -d Ubuntu -- bash -c "cd fleet && uv run python smoke_test.py --fast"`
3. Verify Dashboard port available: `curl -s http://localhost:5555` should fail (not running)

---

## Suite 1: Boot Sequence (5 tests)

| Test | Action | Verify |
|------|--------|--------|
| T1.1 Cold Start | Click "Start" | Output shows stages, sup/hw_sup ONLINE, Ollama green |
| T1.2 Model Verify | Wait 8s after start | Ollama shows model name, both qwen3:8b and qwen3:4b loaded |
| T1.3 HW Supervisor | Wait 15s after start | hw_state.json fresh (<30s), HW Sup label green |
| T1.4 Stop System | Click "Stop" | All processes killed, all indicators red/OFFLINE |
| T1.5 Recover All | Click "Recover All" | Same as T1.1 via alternate code path |

## Suite 2: Dynamic Agent Display (5 tests)

| Test | Action | Verify |
|------|--------|--------|
| T2.1 Empty State | Launch with fleet stopped | Agents panel empty, no hardcoded offline rows |
| T2.2 Incremental Appear | Start fleet, watch | Agents appear one-by-one as they register |
| T2.3 Agent Offline Persists | Kill one worker | Status → SLEEPING (red), row stays, recover btn appears |
| T2.4 Agent Recovers | Click recover button | Dot goes green/yellow, no duplicate row |
| T2.5 Sort Stability | Watch through 5+ refreshes | No visual jumping, alphabetical order stable |

## Suite 3: Per-Agent Task Info (4 tests)

| Test | Action | Verify |
|------|--------|--------|
| T3.1 Idle = No Task | All agents idle | Task column blank/empty for all |
| T3.2 Task Shows Type | Dispatch `web_search` | Claiming agent shows "web_search" in task column |
| T3.3 Concurrent Tasks | Dispatch 5 different tasks | Multiple agents show correct task types |
| T3.4 Backward Compat | Old STATUS.md format (3 cols) | No crash, task column shows nothing |

## Suite 4: Agents Tab Timer (3 tests)

| Test | Action | Verify |
|------|--------|--------|
| T4.1 Auto-Refresh | Open Agents tab, wait 10s | Status updates without manual action |
| T4.2 New Agent Appears | Start new worker | Appears in tab within 8s |
| T4.3 No Resource Leak | Run 5+ minutes | No memory growth, no UI lag |

## Suite 5: Integration & Edge Cases (5 tests)

| Test | Action | Verify |
|------|--------|--------|
| T5.1 No Fleet Launch | Start launcher only | No crashes, all indicators OFFLINE, Start btn available |
| T5.2 STATUS.md Missing | Delete STATUS.md mid-run | Graceful fallback, no crash, agents persist in GUI |
| T5.3 hw_state.json Corrupt | Write bad JSON to hw_state | HW Sup → OFFLINE, no crash |
| T5.4 Rapid Start/Stop | Click Start→Stop→Start fast | No zombies, correct final state |
| T5.5 Existing Tests Pass | Run smoke + soak tests | 10/10 + 25/25, no regressions |

## Suite 6: RAG Dev Environment (2 tests)

| Test | Action | Verify |
|------|--------|--------|
| T6.1 Doc Indexed | Search RAG for "dashboard port" | Returns chunks from dev_environment.md |
| T6.2 Agent Query | Dispatch `rag_query` about ports | Result includes "5555" |

## Suite 7: Dashboard & Services (4 tests)

| Test | Action | Verify |
|------|--------|--------|
| T7.1 Dashboard Up | Fleet running, open http://localhost:5555 | Dashboard loads with agent/task data |
| T7.2 API Status | GET /api/status | Returns JSON with agents + task counts |
| T7.3 SSE Stream | GET /api/stream | Events arrive every 5s |
| T7.4 Thermal Endpoint | GET /api/thermal | Returns GPU/CPU temp data |

## Suite 8: Task Lifecycle (4 tests)

| Test | Action | Verify |
|------|--------|--------|
| T8.1 Dispatch + Complete | `lead_client.py dispatch summarize '{"query":"test"}'` | Task goes PENDING→RUNNING→DONE |
| T8.2 Task Chain | `post_task_chain` via Python | A→B→C executes in order |
| T8.3 Cascade Fail | Fail parent of chain | Downstream WAITING tasks → FAILED |
| T8.4 Console Dispatch | Use Local Console in launcher | Result appears in chat within 60s |

## Suite 9: Channel-Based Communication (5 tests)

| Test | Action | Verify |
|------|--------|--------|
| T9.1 Channel Isolation | Send message on channel="agent" | Supervisors don't receive it, workers do |
| T9.2 Sup Channel | hw_supervisor posts thermal note | supervisor reads it via get_notes("sup") |
| T9.3 Note Round-Trip | `lead_client.py notes sup --post '{"test":true}'` | `lead_client.py notes sup` shows it |
| T9.4 Broadcast Channel | `lead_client.py broadcast "test" --channel agent` | Only non-supervisor agents receive |
| T9.5 Backward Compat | `lead_client.py send researcher "hello"` (no --channel) | Message defaults to channel="fleet" |

## Suite 10: Review, Watchdog & HitL (6 tests)

| Test | Action | Verify |
|------|--------|--------|
| T10.1 Review Gate | Dispatch `code_write` task | Task goes RUNNING→REVIEW→DONE (or REVIEW→PENDING on reject) |
| T10.2 Quarantine | Trigger 3+ consecutive failures for an agent | Agent status → QUARANTINED, stops claiming tasks |
| T10.3 Clear Quarantine | `db.clear_quarantine("agent_name")` | Agent resumes IDLE, claims tasks again |
| T10.4 DLP Scrub | Insert `sk-ant-api03-xxx` in task result | Watchdog redacts it within 60s |
| T10.5 HitL Request | Agent calls `request_human_input()` | Task → WAITING_HUMAN, Fleet Comm tab shows question |
| T10.6 HitL Response | Reply via Fleet Comm tab | Task resumes PENDING, `_human_response` in payload |

## Suite 11: Dashboard & API (3 tests)

| Test | Action | Verify |
|------|--------|--------|
| T11.1 Comms Endpoint | GET /api/comms | Returns per-channel message/note counts for sup/agent/fleet/pool |
| T11.2 Data Stats | GET /api/data_stats | Includes fleet.notes row count |
| T11.3 Resolutions | GET /api/resolutions | Returns entries from data/resolutions.jsonl (or empty array) |

## Suite 12: Token Usage Tracking — CT-1/CT-2 (5 tests)

| Test | Action | Verify |
|------|--------|--------|
| T12.1 Usage Table | `db.init_db()` on fresh DB | `usage` table exists with correct columns |
| T12.2 Log Usage | `db.log_usage(skill="test", model="claude-sonnet-4-6", ...)` | Row appears in usage table with correct values |
| T12.3 Usage Summary | Log 3 rows, call `db.get_usage_summary("day", "skill")` | Returns aggregated row with correct totals |
| T12.4 Usage Delta | Log rows in two date ranges, call `db.get_usage_delta(...)` | Returns per-skill delta with correct direction |
| T12.5 CLI Usage | `lead_client.py usage --period day` | Prints formatted table with totals and cache savings |

## Suite 13: Stability Analysis — DT-4 (3 tests)

| Test | Action | Verify |
|------|--------|--------|
| T13.1 Empty Resolutions | Run stability_report with no resolutions.jsonl | Returns graceful "no data" result, no crash |
| T13.2 Pattern Detection | Create sample resolutions.jsonl, run skill | Report shows top components, severity breakdown, MTTR |
| T13.3 Report Output | Run stability_report with data | Markdown file created in knowledge/reports/ |

## Suite 14: Cost Dashboard API (3 tests)

| Test | Action | Verify |
|------|--------|--------|
| T14.1 Usage Endpoint | GET /api/usage?period=week&group=skill | Returns JSON array with usage aggregates |
| T14.2 Usage Delta | GET /api/usage/delta?from_start=...&to_end=... | Returns per-skill deltas with direction field |
| T14.3 Usage Group | GET /api/usage?group=model | Groups by model instead of skill |

## Suite 15: Token Budgets — CT-4 (4 tests)

| Test | Action | Verify |
|------|--------|--------|
| T15.1 Budget Config | Read fleet.toml [budgets] | Section exists with per-skill USD limits |
| T15.2 Budget Check | `check_budget("lead_research", config)` after logging usage | Returns exceeded=True when over limit |
| T15.3 Budget CLI | `lead_client.py budget` | Prints table with Skill, Budget, Spent, Remaining, Status |
| T15.4 Budget API | GET /api/usage/budgets | Returns JSON array with pct_used per skill |

## Suite 16: Delta Comparison — CT-3 (3 tests)

| Test | Action | Verify |
|------|--------|--------|
| T16.1 Delta CLI | `lead_client.py usage-delta 2026-01-01 2026-01-07 2026-01-08 2026-01-14` | Prints formatted delta table with direction arrows |
| T16.2 Regression API | GET /api/usage/regression | Returns regressions array (skills >20% increase) |
| T16.3 Delta Direction | Insert usage in two periods, call get_usage_delta() | Returns correct "up"/"down"/"flat" direction |

## Suite 17: Marathon ML & Context Persistence — v0.43 (4 tests)

| Test | Action | Verify |
|------|--------|--------|
| T17.1 Marathon Write | `marathon_log.run({session_id: "test", ...})` | Snapshot file created in knowledge/marathon/ |
| T17.2 Session Boundary | `log_session_boundary("fleet_start")` | Entry appended to fleet.md marathon log |
| T17.3 Checkpoint API | GET /api/fleet/checkpoints | Returns checkpoint list (or empty array) |
| T17.4 Marathon API | GET /api/fleet/marathon | Returns session list with snapshot counts |

## Suite 18: Idle Evolution — v0.42 (3 tests)

| Test | Action | Verify |
|------|--------|--------|
| T18.1 Idle Log | `db.log_idle_run("test", "summarize")` | Row appears in idle_runs table |
| T18.2 Idle Stats | `db.get_idle_stats("day")` after logging | Returns aggregated skill runs |
| T18.3 Least Evolved | `db.get_least_evolved_skill()` | Returns skill with oldest/no idle run |

---

## Total: 71 tests across 18 suites
