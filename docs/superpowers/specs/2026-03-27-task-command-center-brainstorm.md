# Task Command Center — Brainstorm

**Date:** 2026-03-27
**Status:** Brainstorm (not spec)

---

## 1. Current State Analysis

### How tasks enter the system

There are **5 entry points** for tasks today:

| Entry Point | File | How it works |
|---|---|---|
| **Manual dispatch** | `dashboard.py` `/api/tasks/dispatch` | User picks a skill, payload, priority, optional agent via the Tasks tab |
| **CLI / Dispatch Bridge** | `dispatch_bridge.py` + `intent.py` | NL text -> conductor model (qwen3:0.6b) parses intent -> `db.post_task()` |
| **Planner idle curriculum** | `skills/plan_workload.py` | When planner is idle, it calls `call_complex` to generate a batch of tasks based on fleet state, knowledge gaps, and business focus. Tasks are posted directly to DB with no preview step. |
| **Event triggers** | `event_triggers.py` | File watch (new files -> ingest tasks), cron-like schedules, webhook payloads. All dispatch via `db.post_task()`. |
| **Swarm / DAG chains** | `skills/swarm_intelligence.py`, `db.post_task_chain()` | Skills can spawn sub-tasks with parent_id and depends_on for DAG execution. |

### How routing works (claim-based, not push-based)

The system uses a **pull model** — workers claim tasks, the supervisor does not push:

1. **Worker polls** (`worker.py` L664-673): Each worker calls `db.claim_task(role, affinity_skills=...)` every 0.1-2.5s depending on idle state.
2. **Affinity matching** (`fleet.toml [affinity]`): Each role has a list of preferred skill types. `claim_task()` tries affinity-matched tasks first (SQL: `type IN (affinity_list)` with `priority DESC, created_at ASC`), then falls back to any unclaimed PENDING task.
3. **Atomic claim** (`db.py` L524-565): Uses `UPDATE...WHERE id=(SELECT...)` to prevent race conditions. The first worker to claim wins.
4. **ML router** (`ml_router.py` + `providers.py` L457-506): `predict_best_agent()` exists but is only called when code explicitly asks for routing advice (e.g., `get_optimal_agent_for_skill()`). It does NOT influence the claim-based pull loop — it's used for optional pre-assignment via `assigned_to`.
5. **Dynamic scaling** (`scheduler.py` L151-200): When pending queue depth > 2, the scheduler spins up additional agents (coder_2, coder_3, analyst, security) to handle load.

### What the user can see today

The current Tasks section (`dashboard.html` L1606-1653) has:
- **New Task form**: Skill dropdown, JSON payload, priority slider, optional agent assignment
- **Recent Tasks table**: ID, skill, status, agent, time — from `/api/timeline`
- **Queue section** (separate nav item): Pending/running tasks with pause button, priority change, cancel, requeue

### What is missing (the gap)

1. **No routing explainability** — user can't see *why* coder_1 got a task instead of coder_2
2. **No planner visibility** — planner generates batches silently; no preview, no audit trail of what it decided
3. **No task detail view** — clicking a task doesn't show payload, result, error, duration, or trace
4. **No source/trigger attribution** — can't tell if a task came from planner idle, webhook, manual dispatch, or DAG chain
5. **No reassignment** — once claimed, a task can't be moved to a different agent
6. **No live feed** — recent tasks require manual refresh (no SSE streaming for task events)
7. **No filtering** — can't filter by agent, skill, status, or time range

---

## 2. Proposed Features (Ranked by Impact)

### Tier 1 — High Impact, Moderate Effort

#### F1: Live Task Feed with SSE (Impact: 9/10, Effort: S)
**What:** Real-time task event stream on the dashboard. Every task create/claim/complete/fail pushes an SSE event.
**Why:** Eliminates manual refresh. User sees fleet activity as it happens.
**Backend:** SSE events already exist (`_broadcast_sse`) for task_dispatched, task_cancelled, task_requeued, task_priority. Add events for `task_claimed` (in worker or via DB trigger polling) and `task_completed`/`task_failed`.
**UI:** Scrolling event log panel with colored status badges, auto-scroll with pause-on-hover.

#### F2: Task Detail Drawer (Impact: 9/10, Effort: S)
**What:** Click any task row to open a slide-out panel showing full detail: payload, result, error, duration, agent, IQ score, trace_id, parent_id, creation source.
**Why:** Currently requires digging into fleet.db manually to see task results or errors.
**Backend:** New endpoint `GET /api/tasks/<id>/detail` returning all columns + computed duration (from created_at to completion).
**UI:** Right-side drawer or modal with JSON syntax highlighting for payload/result. Copy buttons. Link to parent task if DAG.

#### F3: Routing Explainability Panel (Impact: 8/10, Effort: M)
**What:** For each task, show *why* it was routed to its agent: affinity match? ML prediction? Pre-assigned? Fallback claim?
**Why:** This is the #1 black box. User sees "coder_1 did web_search" and wonders why a researcher didn't get it.
**Backend changes needed:**
- Add `routing_reason` TEXT column to tasks table (migration)
- In `db.claim_task()`: set routing_reason to "affinity_match" or "fallback_claim"
- In `db.post_task()` when `assigned_to` is set: "manual_assignment" or "ml_prediction"
- In `ml_router.predict_best_agent()`: return confidence score alongside agent name
- Store as JSON: `{"method": "affinity", "affinity_skills": ["code_write"], "confidence": null}` or `{"method": "ml", "confidence": 0.87, "alternatives": [{"agent": "coder_2", "score": 0.72}]}`
**UI:** Badge on task row ("Affinity", "ML", "Manual", "Fallback"). Detail drawer shows full routing explanation.

#### F4: Source Attribution (Impact: 7/10, Effort: XS)
**What:** Tag every task with its origin: "manual", "planner", "event_trigger", "webhook", "dag_child", "idle_curriculum", "cli".
**Why:** User needs to know what percentage of work is planner-generated vs. manually dispatched vs. event-driven.
**Backend:** Add `source` TEXT column to tasks table. Set it at each `db.post_task()` call site:
- `dashboard.py api_task_dispatch` -> "manual"
- `dispatch_bridge.py cmd_submit` -> "cli"
- `plan_workload.py` -> "planner"
- `event_triggers.py` -> "file_watch" / "schedule" / "webhook"
- `swarm_intelligence.py` / `post_task_chain` -> "dag_child"
- Worker idle curriculum -> "idle"
**UI:** Color-coded source badge on each task row. Filter dropdown.

### Tier 2 — Medium Impact, Medium Effort

#### F5: Planner Visibility & Preview (Impact: 8/10, Effort: M)
**What:** Before the planner auto-queues tasks, show the planned batch in a preview panel. Operator can approve, edit, or reject individual tasks before they enter the queue.
**Why:** Planner currently fires 5-500 tasks blindly. User has no way to see what it decided or stop bad batches.
**Backend:**
- Add `dry_run=True` mode to planner idle curriculum (already supported in plan_workload.py!)
- New table `planner_batches` (id, created_at, batch_json, status: "preview"/"approved"/"rejected")
- New endpoints: `GET /api/planner/batches`, `POST /api/planner/batches/<id>/approve`, `POST /api/planner/batches/<id>/reject`
- Planner writes to `planner_batches` instead of directly to tasks. Operator approves to dispatch.
- Config toggle: `[planner] auto_approve = true` for hands-off mode (current behavior)
**UI:** "Planner" sub-tab showing pending batches as expandable cards. Each task in the batch has approve/reject/edit controls. Batch-level approve-all / reject-all buttons.

#### F6: Manual Reassignment (Impact: 6/10, Effort: S)
**What:** Reassign a PENDING or RUNNING task to a different agent.
**Why:** Sometimes the wrong agent claims a task (e.g., coder_1 claims a research task during fallback).
**Backend:** New endpoint `PUT /api/tasks/<id>/reassign` with `{"agent": "researcher"}`. For RUNNING tasks: mark current agent's claim as void, requeue with `assigned_to` set.
**UI:** Dropdown in task detail drawer showing available agents. Reassign button.

#### F7: Filtering & Search (Impact: 7/10, Effort: S)
**What:** Filter task list by agent, skill type, status, time range, source. Full-text search on payload/result.
**Why:** With thousands of tasks, finding specific ones is impossible without SQL.
**Backend:** Extend `/api/tasks/recent` with query params: `?agent=coder_1&skill=code_write&status=DONE&since=2026-03-27&q=search_term`. Add FTS5 index on payload_json + result_json if not exists.
**UI:** Filter bar above task table with dropdowns (agent, skill, status, source) and a search input. Date range picker.

### Tier 3 — Nice to Have

#### F8: Task Timeline / Gantt View (Impact: 5/10, Effort: M)
**What:** Horizontal timeline showing when each task was created, claimed, completed/failed. Swim lanes per agent.
**Why:** Visual representation of fleet utilization over time. Spot bottlenecks.
**Backend:** Need `claimed_at` and `completed_at` timestamps (currently only `created_at` exists). Add columns.
**UI:** SVG or canvas-based Gantt chart. Zoom in/out on time range.

#### F9: Task Cost Attribution (Impact: 5/10, Effort: S)
**What:** Show token usage and estimated cost per task, linking to the `usage` table via task_id.
**Why:** User can see which tasks are expensive and optimize.
**Backend:** Already have `usage.task_id` FK. New endpoint `GET /api/tasks/<id>/cost` aggregating usage rows.
**UI:** Cost badge on task row. Detail drawer shows token breakdown (input/output/cache).

#### F10: Retry with Modification (Impact: 4/10, Effort: S)
**What:** Requeue a failed task with an edited payload (not just blind retry).
**Why:** If a task failed because the payload was wrong, requeuing the same payload wastes tokens.
**Backend:** Extend `POST /api/tasks/<id>/requeue` to accept optional `{"payload": {...}, "priority": N}`.
**UI:** "Retry with edit" button in task detail drawer that pre-fills payload editor.

---

## 3. UI Layout Description

### Option A: Dedicated "Command Center" Page (Recommended)

Replace the current Tasks + Queue sections with a single full-width "Task Command Center" page:

```
+------------------------------------------------------------------+
| [Filter Bar]  Agent: [All v]  Skill: [All v]  Status: [All v]    |
|               Source: [All v]  Time: [Last 24h v]  [Search...]    |
+------------------------------------------------------------------+
|                          |                                        |
|   LIVE TASK FEED         |   TASK DETAIL DRAWER                   |
|   (scrolling list)       |   (opens on row click)                 |
|                          |                                        |
|   #4521 code_write       |   Task #4521                           |
|   coder_1 [Affinity]     |   Skill: code_write                   |
|   RUNNING 2s ago         |   Agent: coder_1                      |
|                          |   Status: RUNNING                     |
|   #4520 web_search       |   Priority: 7                         |
|   researcher [ML 0.92]   |   Source: planner (batch #12)         |
|   DONE 15s ago           |   Routing: affinity match             |
|                          |                                        |
|   #4519 summarize        |   Payload:                            |
|   researcher [Fallback]  |   {"instructions": "..."}             |
|   FAILED 30s ago         |                                        |
|                          |   Result: (pending)                   |
|   ...                    |                                        |
|                          |   [Reassign v] [Cancel] [Retry]       |
+------------------------------------------------------------------+
|  PLANNER BATCHES                                                  |
|  Batch #12 (20 tasks, 45s ago) [Preview] [Approve All] [Reject]  |
|  Batch #11 (15 tasks, 2h ago) [Approved - 15/15 completed]       |
+------------------------------------------------------------------+
```

### Option B: Overlay on Existing Sections

Keep current layout but add:
- SSE-driven auto-refresh to existing Recent Tasks table
- Click-to-expand rows for detail view
- Filter bar above the table
- New "Planner" card below the dispatch form

Option A is cleaner but more work. Option B is incremental.

---

## 4. Backend Changes Summary

### Database migrations

| Change | Column/Table | Effort |
|---|---|---|
| Add `source` to tasks | `ALTER TABLE tasks ADD COLUMN source TEXT DEFAULT 'unknown'` | XS |
| Add `routing_reason` to tasks | `ALTER TABLE tasks ADD COLUMN routing_reason TEXT` | XS |
| Add `claimed_at` to tasks | `ALTER TABLE tasks ADD COLUMN claimed_at TEXT` | XS |
| Add `completed_at` to tasks | `ALTER TABLE tasks ADD COLUMN completed_at TEXT` | XS |
| New `planner_batches` table | `CREATE TABLE planner_batches (id, created_at, batch_json, status, approved_by, approved_at)` | S |

### New API endpoints

| Endpoint | Purpose | Effort |
|---|---|---|
| `GET /api/tasks/<id>/detail` | Full task detail with routing, cost, parent chain | S |
| `GET /api/tasks/<id>/cost` | Token usage breakdown for a task | XS |
| `PUT /api/tasks/<id>/reassign` | Move task to a different agent | S |
| `GET /api/planner/batches` | List planner batch previews | S |
| `POST /api/planner/batches/<id>/approve` | Approve a planner batch for dispatch | S |
| `POST /api/planner/batches/<id>/reject` | Reject a planner batch | XS |
| `GET /api/tasks/stats` | Aggregate stats (by agent, skill, source, time) | S |

### Modified code paths

| File | Change | Effort |
|---|---|---|
| `db.py claim_task()` | Set `claimed_at`, `routing_reason` on claim | XS |
| `db.py complete_task()` | Set `completed_at` timestamp | XS |
| `db.py post_task()` | Accept `source` parameter | XS |
| `worker.py` | Pass routing_reason through claim, emit SSE on claim | S |
| `plan_workload.py` | Write to `planner_batches` instead of direct `post_task()` | M |
| `event_triggers.py` | Pass `source="file_watch"/"schedule"/"webhook"` to `post_task()` | XS |
| `dashboard.py` | Add 7 new endpoints + SSE events for claim/complete | M |
| `dashboard.html` | New Command Center UI (or overlay) | L |

---

## 5. Effort Estimates

| Feature | Backend | Frontend | Total | Priority |
|---|---|---|---|---|
| F1: Live Task Feed (SSE) | S (3-5k tokens) | M (8-15k) | M | P0 |
| F2: Task Detail Drawer | S (3-5k) | M (8-15k) | M | P0 |
| F3: Routing Explainability | M (8-15k) | S (3-5k) | M | P0 |
| F4: Source Attribution | XS (1-2k) | XS (1-2k) | XS | P0 |
| F5: Planner Preview | M (8-15k) | M (8-15k) | L | P1 |
| F6: Manual Reassignment | S (3-5k) | S (3-5k) | S | P1 |
| F7: Filtering & Search | S (3-5k) | M (8-15k) | M | P1 |
| F8: Task Timeline/Gantt | S (3-5k) | L (20-40k) | L | P2 |
| F9: Task Cost Attribution | XS (1-2k) | S (3-5k) | S | P2 |
| F10: Retry with Modification | XS (1-2k) | S (3-5k) | S | P2 |

**Recommended build order:** F4 -> F1 -> F2 -> F3 -> F7 -> F5 -> F6 -> F9 -> F10 -> F8

F4 (source attribution) is the cheapest win — one column addition, one-line changes at each post_task call site. F1+F2+F3 form the core Command Center experience. F5 (planner preview) is the biggest architectural change but arguably the most valuable for operational control.

---

## 6. Open Questions

1. **Planner approval latency**: If planner batches require approval, idle agents will have nothing to do while waiting. Should there be a timeout that auto-approves after N minutes?
2. **Routing reason storage**: JSON blob vs. enum column? JSON is more flexible but harder to filter. Could do both: `routing_method` enum + `routing_detail` JSON.
3. **Historical depth**: How far back should the Command Center show tasks? Currently `/api/tasks/recent` returns 50. For filtering to be useful, need pagination + date ranges.
4. **SSE vs. polling for task claims**: Workers claim tasks via direct DB writes, not through the dashboard. SSE events for claims would require either (a) polling the DB for new RUNNING tasks, or (b) having workers POST to a dashboard endpoint after claiming. Option (a) is simpler but adds latency; option (b) is more immediate but couples worker to dashboard.
