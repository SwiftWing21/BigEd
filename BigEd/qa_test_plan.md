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
| T5.5 Existing Tests Pass | Run smoke + soak tests | 10/10 + 13/13, no regressions |

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

---

## Total: 32 tests across 8 suites
