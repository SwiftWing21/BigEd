# Team Orchestrator — Design Spec

**Date:** 2026-03-26
**Status:** Draft (rev 3 — post second spec review)
**Type:** Claude Code Plugin/Skill

## Overview

A Claude Code skill that orchestrates a 3-layer hierarchy of Claude Code agents for complex, multi-domain tasks. Built on Claude Code's native primitives: **TeamCreate** for team/task management, **SendMessage** for agent communication, **Agent** tool with `isolation: "worktree"` for pod-level isolation, and `model` parameter for tier routing.

An append-only `.team-log` serves as an **observability/audit layer** — not the coordination mechanism.

## Goals

- Parallelize complex work across up to 25 agents in a structured hierarchy
- Adaptive sizing: 1 pod for simple tasks, 5 pods for complex multi-domain work
- Smart model routing: Opus for planning/architecture, Sonnet for execution, Haiku for mechanical tasks
- Safe concurrency via git worktrees with clean merge flow
- Observable state via audit log + native task list

## Prerequisites

- **Claude Code Agent tool parameters used:** `name`, `model`, `isolation: "worktree"`, `team_name`, `run_in_background`, `subagent_type`, `mode`. All confirmed in the current Agent tool schema.
- **Team primitives:** `TeamCreate`, `TeamDelete`, `SendMessage`. Task tools (`TaskCreate`, `TaskUpdate`, `TaskList`) are available when a team is active.
- **Worktree:** `EnterWorktree` / `ExitWorktree` exist as standalone tools, but this spec uses the Agent tool's `isolation: "worktree"` parameter which handles worktree creation automatically for spawned agents.

## Non-Goals

- Integration with BigEd fleet (supervisor.py/worker.py) — this is pure Claude Code
- Persistent teams across sessions — each `/team` invocation is self-contained
- Custom agent runtimes — uses Claude Code's native Agent tool only

## Architecture

### Hierarchy

```
Department Head (L0, Opus, 1 instance — main session)
├── Pod Lead 1 (L1, Opus/Sonnet, worktree-isolated Agent)
│   ├── Sub-Agent 1a (L2, Sonnet/Haiku, same worktree, sequential)
│   ├── Sub-Agent 1b (L2, Sonnet/Haiku, same worktree, sequential)
│   └── ... up to 5
├── Pod Lead 2 (L1, Opus/Sonnet, worktree-isolated Agent)
│   └── ...
└── ... up to 5 Pod Leads
```

### Native Primitives Used

| Primitive | Role in Orchestrator |
|-----------|---------------------|
| `TeamCreate` | Creates team + shared task list at session start |
| `TaskCreate/TaskUpdate/TaskList` | Shared task board — department head creates, pod leads claim and update |
| `SendMessage` | All agent-to-agent communication (pod↔pod, pod→head, head→pod) |
| `Agent` with `isolation: "worktree"` | Pod leads get isolated worktrees automatically |
| `Agent` with `model` parameter | Department head sets pod lead model; pod leads set sub-agent model |
| `Agent` with `team_name` | Pod leads join the team, enabling messaging and task list access |

### Model Routing

| Layer | Role | Default Model | Downshift To | Model ID | Decides Model |
|-------|------|---------------|--------------|----------|---------------|
| L0 | Department Head | Opus | — | `claude-opus-4-6` | User/skill config |
| L1 | Pod Lead | Opus | Sonnet | `claude-opus-4-6` / `claude-sonnet-4-6` | Department Head |
| L2 | Sub-Agent | Sonnet | Haiku | `claude-sonnet-4-6` / `claude-haiku-4-5` | Pod Lead |

**Routing rules:**
- Department Head is always Opus (planning, decomposition, merge resolution)
- Pod Leads default Opus, downshift to Sonnet when work is execution-only (no architecture decisions)
- Sub-Agents default Sonnet, downshift to Haiku for mechanical tasks (formatting, grep, simple edits, test runs)
- Haiku only appears under a Pod Lead — never unsupervised
- Pod Lead decides sub-agent model based on task difficulty

### Agent Cap

- **Hard cap: 25 total across all layers** (1 head + 5 leads + 19 subs maximum)
- Enforced by department head reading team config (`~/.claude/teams/{team-name}.json` or `~/.claude/teams/{team-name}/config.json` — verify actual path at implementation time) `members` array before authorizing new spawns
- Pod leads must request spawn approval from department head via `SendMessage` before creating sub-agents — department head checks member count and approves/denies
- At cap: pod lead queues the task internally and waits for a sub-agent to finish, or self-executes
- Department head can preempt by sending reallocation messages to pod leads

**Cap math:**

| Pods | Max Subs/Pod | Total (1 + pods + subs) |
|------|-------------|------------------------|
| 1 | 5 | 7 |
| 2 | 5 | 13 |
| 3 | 5 | 19 |
| 4 | 4 | 21 |
| 5 | 3* | 21 |

*With 5 pods: 3 subs each = 21 agents. To reach 25, use mixed allocation (e.g., four pods with 4 subs + one pod with 3 = 1+5+19 = 25). Department head manages the per-pod allocation.

## Plugin Structure

```
.claude/skills/team-orchestrator/
├── SKILL.md              # Main skill — department head prompt + orchestration rules
├── pod-lead-prompt.md    # Template prompt injected into pod lead agents
├── sub-agent-prompt.md   # Template prompt injected into sub-agents
├── auto-detect.md        # Analyzes task complexity, suggests team mode
└── team-state/           # Runtime state (gitignored)
    └── .team-log          # Append-only audit log (observability only)
```

## Coordination: Native Primitives + Audit Log

### Primary coordination: TeamCreate + SendMessage + TaskList

All real coordination uses Claude Code's native system:

1. **Task assignment:** Department head creates tasks via `TaskCreate`, pod leads claim via `TaskUpdate`
2. **Pod-to-pod messaging:** `SendMessage(to: "pod-frontend", message: "API schema changed: /users now returns {user, token}")` — direct delivery, no polling
3. **Escalation:** `SendMessage(to: "department-head", message: "Blocked: need backend API to be merged first")`
4. **Status updates:** Pod leads mark tasks complete via `TaskUpdate`, department head sees via `TaskList`
5. **Shutdown:** Department head sends `{type: "shutdown_request"}` to all pod leads when done

### Secondary: Audit Log (observability only)

The `.team-log` is an append-only JSONL file written by the **department head only** (single-writer eliminates concurrent write issues). It records high-level events for debugging and post-run analysis:

```jsonl
{"ts":"...","event":"team_start","task":"build auth system","pods_planned":3,"team":"team-auth-abc"}
{"ts":"...","event":"pod_dispatched","pod":"backend","model":"opus","worktree":"auto"}
{"ts":"...","event":"pod_completed","pod":"backend","status":"ok","tasks_done":3}
{"ts":"...","event":"merge","pod":"backend","result":"clean"}
{"ts":"...","event":"merge","pod":"frontend","result":"conflict_resolved"}
{"ts":"...","event":"team_done","status":"ok","summary":"3 pods, 7 sub-agents, 1 conflict resolved"}
```

**Single-writer rule:** Only the department head appends to `.team-log`. Pod leads communicate status via `SendMessage` to the department head, who records it. This eliminates concurrent write corruption on Windows.

**Log archival:** On each new `/team` invocation, the previous log is moved to `.team-log.<timestamp>`.

### Communication Rules

| Scenario | Mechanism |
|----------|-----------|
| Interface/schema change notification | `SendMessage` pod → pod (direct) |
| Sharing context another pod needs | `SendMessage` pod → pod (direct) |
| "Don't merge until I finish X" | `SendMessage` pod → department head (escalation) |
| "I'm blocked on your output" | `SendMessage` pod → department head (escalation) |
| "Your work needs to be redone" | `SendMessage` pod → department head (escalation) |
| Sub-agent spawn approval | `SendMessage` pod → department head (cap check) |

**Principle:** Info sharing is peer-to-peer via `SendMessage`. Coordination, blocking, and spawn requests go through department head.

### Team Membership

- **Department head + pod leads** are team members (spawned with `team_name`). They appear in the team config `members` array and can send/receive `SendMessage`.
- **Sub-agents are NOT team members.** They are spawned by pod leads as plain `Agent` calls without `team_name`. This means sub-agents cannot receive broadcasts or direct messages. Pod leads are fully responsible for sub-agent lifecycle.
- This simplifies the agent cap: count team members in config = department head + pod leads + any sub-agents that the department head tracks via approval messages.
- **Broadcast `SendMessage(to: "*")`** only reaches pod leads, not sub-agents. Shutdown broadcasts are safe.

**Pod discovery:** Pod leads read the team config file to discover other pod leads by name for direct messaging.

## Worktree Strategy

### Isolation Model

Each pod lead is spawned via `Agent` with `isolation: "worktree"`. This creates a worktree in `.claude/worktrees/` automatically — the department head does **not** pre-create worktrees.

Sub-agents within a pod work on the same worktree as their pod lead (spawned without `isolation: "worktree"`).

```
main (department head operates here — merges only, no direct code changes)
├── .claude/worktrees/pod-backend/    (pod lead 2 + its sub-agents, sequential)
├── .claude/worktrees/pod-frontend/   (pod lead 1 + its sub-agents, sequential)
└── .claude/worktrees/pod-testing/    (pod lead 3 + its sub-agents, sequential)
```

### Sequential Enforcement Within Pods

Pod leads enforce sequential sub-agent execution by:
1. Spawning sub-agent 1 via `Agent` tool (foreground, blocking)
2. Waiting for Agent tool to return (sub-agent completes)
3. Then spawning sub-agent 2

This is natural — the `Agent` tool blocks until the sub-agent finishes. No special mechanism needed. Pod leads simply don't spawn multiple agents in parallel.

### Merge Flow

1. Pod leads commit their work and send completion message to department head
2. Department head picks merge order based on dependency analysis
3. Department head merges pod branches into main sequentially (this involves git writes to main — the "no direct code changes" rule means no manual edits, but merge operations are expected)
4. Conflicts resolved by department head directly (Opus, full task context)
5. If conflict is too complex, department head sends conflict context to relevant pod lead for resolution

### Cleanup

- `worktree_cleanup: true` (default): department head runs `git worktree remove` after successful merge
- After all merges + shutdown: department head calls `TeamDelete` to clean up `~/.claude/teams/` and `~/.claude/tasks/` directories
- On failure/abort: worktrees left for user inspection, noted in audit log, team NOT deleted (preserves task history for debugging)
- Stale detection: worktrees in `.claude/worktrees/pod-*` with no matching active team
- **Single active team:** Only one `/team` invocation can be active at a time. If `/team` is called while another is running, the user is prompted to cancel the existing team first.

## Task Flow

### End-to-End Example

```
1. INVOKE
   User → /team "build a REST API with auth, rate limiting, and tests"

2. PLAN + CONFIRM (Department Head, Opus)
   - Analyze task → 3 domains: auth, rate-limiting, testing
   - Present plan to user: "3 pods (auth, rate-limiting, testing), ~9 agents total. Proceed?"
   - User confirms (or passes --yes to skip)

3. SETUP (Department Head, Opus)
   - TeamCreate(team_name: "team-api-build", description: "REST API with auth, rate limiting, tests")
   - TaskCreate for each pod's objective + sub-tasks
   - Log: team_start

4. DISPATCH (parallel Agent calls with team_name)
   Agent(name: "pod-auth", model: "opus", isolation: "worktree", team_name: "team-api-build",
         prompt: <pod-lead-prompt.md> + "Objective: implement JWT auth middleware + user model")
   Agent(name: "pod-ratelimit", model: "sonnet", isolation: "worktree", team_name: "team-api-build",
         prompt: <pod-lead-prompt.md> + "Objective: implement token bucket rate limiter")
   Agent(name: "pod-testing", model: "sonnet", isolation: "worktree", team_name: "team-api-build",
         prompt: <pod-lead-prompt.md> + "Objective: integration tests. DEPENDENCY: wait for auth + ratelimit")

5. POD-LEVEL WORK (each pod lead independently)
   Pod lead claims tasks from TaskList, decomposes into sub-tasks.
   For each sub-task: either self-execute or spawn sub-agent (foreground, sequential).
   Pod lead sends advisory messages to other pods via SendMessage as needed.
   On completion: TaskUpdate(status: "completed"), SendMessage to department head.

6. DEPENDENCY HANDLING
   Testing pod lead checks TaskList — sees auth/ratelimit tasks not done.
   Sends: SendMessage(to: "department-head", "Blocked: waiting on auth and ratelimit pods")
   Department head holds testing pod via SendMessage until blockers clear.
   When ready: SendMessage(to: "pod-testing", "Blockers resolved. Proceed.")

7. MERGE (Department Head, sequential)
   - Merge pod-auth worktree branch → main
   - Merge pod-ratelimit worktree branch → main
   - Merge pod-testing worktree branch → main
   - Resolve any conflicts
   - Clean up worktrees
   - Log: team_done

8. SHUTDOWN + CLEANUP
   SendMessage(to: "*", message: {type: "shutdown_request"}) to all pod leads.
   Note: SKILL.md must explicitly authorize department head to originate shutdown_request.
   TeamDelete(team_name: "team-api-build") to clean up team + task files.

9. REPORT
   Department head summarizes to user:
   "Done. 3 pods completed: auth (2 sub-agents), rate-limiting (self-executed),
    testing (3 sub-agents). 1 merge conflict resolved. All tests passing."
```

### Pod Lead Self-Execution

Pod leads can execute tasks directly instead of spawning sub-agents when:
- Task is trivial (single file edit, simple wiring)
- Only one task remains in the pod's scope
- Agent cap is reached and spawning isn't approved

### Error Handling

- **Sub-agent fails:** Pod lead retries once with adjusted prompt (new Agent call)
- **Retry fails:** Pod lead tries different approach or self-executes
- **Pod objective blocked:** Pod lead sends `SendMessage` to department head with context
- **Department head options:** Reassign work, merge partial results, send new instructions, or ask user
- **Agent timeout:** Sub-agents inherit Claude Code's default timeout. Pod leads should set reasonable scope per sub-agent to avoid hangs. Future: configurable per-agent timeout.
- **Catastrophic failure (multiple pods fail / merge fails):** Department head preserves all worktrees, writes final audit log with failure details, reports partial results to user with a list of what succeeded and what didn't. User can inspect worktrees manually or re-run `/team` on the remaining work.

## Invocation

### Entry Points

**1. Slash command:**
```
/team "build the auth system"
/team "refactor the dashboard" --pods 2
/team "fix all lint errors" --max-agents 10
```

**2. Explicit skill with config:**
```
Use team-orchestrator:
  task: "build the auth system"
  pods:
    - name: backend, model: opus
    - name: frontend, model: sonnet
    - name: testing, model: sonnet
  max_agents: 15
```

**3. Auto-detect suggestion:**
When a task spans multiple domains or would benefit from parallelism, the auto-detect skill analyzes:
- Number of distinct domains/subsystems touched (primary signal)
- Estimated file count and spread across directories
- Whether subtasks have low interdependency
- Task decomposability: can the work be split into pods that don't constantly block each other?

If team mode is recommended, it suggests:
> "This looks like a multi-domain task. I'd suggest 3 pods: backend, frontend, testing. Run `/team` to confirm, or tell me a different split."

User confirms or declines. Auto-detect can be disabled via config.

### Configuration

Settings stored in skill config (read by SKILL.md at invocation):

| Setting | Default | Description |
|---------|---------|-------------|
| `max_agents` | 25 | Hard cap across all layers |
| `max_pods` | 5 | Maximum pod leads |
| `max_sub_per_pod` | 5 | Maximum sub-agents per pod |
| `default_head_model` | `opus` | Department head model (`claude-opus-4-6`) |
| `default_lead_model` | `opus` | Pod lead default model |
| `default_sub_model` | `sonnet` | Sub-agent default model |
| `allow_haiku` | true | Whether pod leads can downshift subs to haiku |
| `auto_detect` | true | Suggest team mode on complex tasks |
| `worktree_cleanup` | true | Auto-delete worktrees after merge |

### Mid-Run Controls

- User types in the terminal at any time — department head receives it as a normal conversation turn
- Department head can pause pod dispatches, adjust plan, relay instructions to pods via `SendMessage`
- `/team-cancel` — department head sends `{type: "shutdown_request"}` to all pods, waits for acknowledgment, cleans up worktrees, writes final audit log entry. In-flight sub-agents complete their current Agent call (non-interruptible), but pod leads don't spawn new ones after receiving shutdown.

## Adaptive Sizing

Department head analyzes the task and decides team shape:

| Task Complexity | Pods | Sub-Agents/Pod | Total Agents | Example |
|----------------|------|----------------|--------------|---------|
| Trivial | 1 | 0 | 2 | Fix a typo across files |
| Simple | 1-2 | 1-2 | 4-7 | Add a single feature |
| Medium | 2-3 | 2-3 | 7-12 | Build a module with tests |
| Complex | 3-4 | 3-4 | 13-21 | Multi-domain feature set |
| Maximum | 5 | 3-4 | 21-25 | Large-scale parallel build |

User can override with `--pods N` or explicit pod config.

## Cost Awareness

Running 25 agents with Opus/Sonnet is expensive. The department head should:
1. Present the planned team shape and estimated agent count to the user before dispatching
2. Default to the minimum viable team size (prefer 1-2 pods for most tasks)
3. Only scale to 5 pods / 25 agents when the task genuinely demands it
4. Use model downshifting aggressively — most sub-agent work is Sonnet or Haiku

The user confirmation step (PLAN + CONFIRM in the task flow) is mandatory unless the user passes `--yes` to skip it.

## Security Considerations

- Worktrees inherit the repo's `.claude/settings.json` permissions — no privilege escalation
- Sub-agents spawned without `isolation: "worktree"` operate in their pod lead's worktree (contained)
- Department head is the only agent that merges to `main`
- `.team-log` is single-writer (department head only) — no tampering by sub-agents
- Agent cap enforced via team config member count — not honor-system
- Pod leads cannot spawn more sub-agents than department head approves

## Future Considerations

- Session persistence: resume a `/team` run after interruption (serialize team state)
- Team templates: save and reuse pod configurations for common patterns
- BigEd fleet integration: bridge team orchestrator to fleet supervisor
- Metrics dashboard: parse `.team-log` for performance analytics
- Cross-repo teams: pods working across multiple repositories
- Configurable per-agent timeouts
- Token budget tracking per pod
