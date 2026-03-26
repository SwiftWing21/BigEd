# Team Orchestrator — Design Spec

**Date:** 2026-03-26
**Status:** Draft
**Type:** Claude Code Plugin/Skill

## Overview

A Claude Code skill that orchestrates a 3-layer hierarchy of Claude Code agents for complex, multi-domain tasks. The department head decomposes work into pods, pod leads manage sub-agents, and an append-only log provides observability and coordination.

## Goals

- Parallelize complex work across up to 25 agents in a structured hierarchy
- Adaptive sizing: 1 pod for simple tasks, 5 pods for complex multi-domain work
- Smart model routing: Opus for planning/architecture, Sonnet for execution, Haiku for mechanical tasks
- Safe concurrency via git worktrees with clean merge flow
- Observable state via append-only team log

## Non-Goals

- Integration with BigEd fleet (supervisor.py/worker.py) — this is pure Claude Code
- Persistent teams across sessions — each `/team` invocation is self-contained
- Custom agent runtimes — uses Claude Code's native Agent tool only

## Architecture

### Hierarchy

```
Department Head (L0, Opus, 1 instance)
├── Pod Lead 1 (L1, Opus/Sonnet, worktree-1)
│   ├── Sub-Agent 1a (L2, Sonnet/Haiku)
│   ├── Sub-Agent 1b (L2, Sonnet/Haiku)
│   └── ... up to 5
├── Pod Lead 2 (L1, Opus/Sonnet, worktree-2)
│   └── ...
└── ... up to 5 Pod Leads
```

### Model Routing

| Layer | Role | Default Model | Downshift To | Decides Model |
|-------|------|---------------|--------------|---------------|
| L0 | Department Head | Opus | — | User/skill config |
| L1 | Pod Lead | Opus | Sonnet | Department Head |
| L2 | Sub-Agent | Sonnet | Haiku | Pod Lead |

**Routing rules:**
- Department Head is always Opus (planning, decomposition, merge resolution)
- Pod Leads default Opus, downshift to Sonnet when work is execution-only (no architecture decisions)
- Sub-Agents default Sonnet, downshift to Haiku for mechanical tasks (formatting, grep, simple edits, test runs)
- Haiku only appears under a Pod Lead — never unsupervised
- Pod Lead decides sub-agent model based on task difficulty

### Agent Cap

- Hard cap: 25 total across all layers
- Enforced by counting active agents in `.team-log` (`agent_start` - `agent_done`)
- At cap: pod lead queues tasks, waits for a slot
- Department head can preempt by reallocating across pods

## Plugin Structure

```
.claude/skills/team-orchestrator/
├── SKILL.md              # Main skill — department head prompt + orchestration rules
├── pod-lead-prompt.md    # Template prompt injected into pod lead agents
├── sub-agent-prompt.md   # Template prompt injected into sub-agents
├── auto-detect.md        # Analyzes task complexity, suggests team mode
└── team-state/           # Runtime state (gitignored)
    └── .team-log          # Append-only ledger
```

## State: The Team Log

Append-only JSONL file at `.claude/skills/team-orchestrator/team-state/.team-log`. Single source of truth for team state and observability.

### Event Types

```jsonl
{"ts":"...","event":"team_start","task":"build auth system","pods_planned":3}
{"ts":"...","event":"pod_start","pod":"backend","lead_model":"opus","worktree":"wt-pod-backend-abc123"}
{"ts":"...","event":"agent_start","pod":"backend","agent":"sub-1","model":"sonnet","task":"implement JWT middleware"}
{"ts":"...","event":"agent_done","pod":"backend","agent":"sub-1","status":"ok"}
{"ts":"...","event":"agent_done","pod":"backend","agent":"sub-1","status":"failed","error":"type error in middleware"}
{"ts":"...","event":"pod_msg","from":"backend","to":"frontend","msg":"API schema changed","file":"src/api/schema.ts"}
{"ts":"...","event":"pod_ack","from":"frontend","to":"backend","ref":"<msg_ts>","msg":"acknowledged"}
{"ts":"...","event":"pod_wait","pod":"testing","reason":"blocked on auth, rate-limiting"}
{"ts":"...","event":"pod_escalate","pod":"backend","reason":"dependency conflict","to":"department_head"}
{"ts":"...","event":"pod_done","pod":"backend","status":"ok","merge":"clean"}
{"ts":"...","event":"team_done","status":"ok","summary":"3 pods completed, 1 conflict resolved"}
```

### Log Rules

- All layers append, none modify or delete
- Department head reads the full log for situational awareness
- Pod leads read the log for messages addressed to them before each sub-agent dispatch
- Log is cleared on each new `/team` invocation (previous log archived with timestamp)

## Worktree Strategy

### Isolation Model

Worktree per pod, sequential execution within pod.

```
main (department head reads, never writes directly)
├── wt-pod-frontend/   (pod lead 1 + its sub-agents, sequential)
├── wt-pod-backend/    (pod lead 2 + its sub-agents, sequential)
├── wt-pod-testing/    (pod lead 3 + its sub-agents, sequential)
```

### Lifecycle

1. Department head creates worktrees before dispatching pod leads
2. Each pod lead receives its worktree path in the prompt
3. Sub-agents within a pod work sequentially on the same worktree
4. Pod lead commits its pod's work to the worktree branch when done
5. Department head merges pod branches into main one at a time

### Merge Flow

- Department head picks merge order based on dependency analysis
- Conflicts resolved by department head directly (Opus, full context)
- If conflict is too complex, department head re-dispatches to relevant pod lead with conflict context
- Worktrees deleted after successful merge
- On failure/abort: worktrees left for user inspection, logged

### Stale Worktree Detection

If a worktree exists but has no matching `pod_start` without `pod_done` in the log, it's stale and can be cleaned.

## Communication

### Pod-to-Pod Messaging

Pods post advisory messages to other pods via `.team-log`:

```jsonl
{"ts":"...","event":"pod_msg","from":"backend","to":"frontend","msg":"API returns {user, token} not {data}","file":"src/api/schema.ts"}
{"ts":"...","event":"pod_ack","from":"frontend","to":"backend","ref":"<msg_ts>","msg":"acknowledged, updating fetch calls"}
```

### Communication Rules

| Scenario | Channel |
|----------|---------|
| Interface/schema change notification | Pod → Pod message |
| Sharing context another pod needs | Pod → Pod message |
| "Don't merge until I finish X" | Escalate to department head |
| "I'm blocked on your output" | Escalate to department head |
| "Your work needs to be redone" | Escalate to department head |

**Principle:** Info sharing is peer-to-peer. Coordination and blocking go through department head.

## Task Flow

### End-to-End Example

```
1. INVOKE
   User → /team "build a REST API with auth, rate limiting, and tests"

2. ANALYZE (Department Head, Opus)
   - Parse task into domains
   - Read .team-log for prior context
   - Decide: 3 pods — "auth", "rate-limiting", "testing"
   - Log: team_start

3. SETUP
   - Create 3 worktrees
   - Log: pod_start × 3

4. DISPATCH (parallel Agent calls)
   Pod Lead "auth" (Opus, wt-pod-auth)
   Pod Lead "rate-limiting" (Sonnet, wt-pod-rate-limiting)
   Pod Lead "testing" (Sonnet, wt-pod-testing — dependency-aware)

5. POD-LEVEL WORK (each pod lead independently)
   Pod lead decomposes its slice, spawns sub-agents sequentially,
   self-executes trivial tasks, logs all events.

6. DEPENDENCY HANDLING
   Testing pod sees blockers not done → logs pod_wait
   Department head holds testing pod until blockers clear

7. MERGE (Department Head, sequential)
   Merge in dependency order, resolve conflicts, log results.

8. REPORT
   Department head summarizes results to user.
```

### Pod Lead Self-Execution

Pod leads can execute tasks directly instead of spawning sub-agents when:
- Task is trivial (single file edit, simple wiring)
- Only one task remains in the pod's scope
- Agent cap is reached and task doesn't warrant queuing

### Error Handling

- **Sub-agent fails:** Pod lead retries once with adjusted prompt
- **Retry fails:** Pod lead tries different approach or self-executes
- **Pod objective blocked:** Pod lead logs `pod_escalate`, department head intervenes
- **Department head options:** Reassign work, merge partial results, ask user

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
When a task spans multiple domains or would benefit from parallelism, the auto-detect skill suggests team mode. User confirms or declines.

### Configuration

Settings in `.claude/settings.json` or skill-level config:

| Setting | Default | Description |
|---------|---------|-------------|
| `max_agents` | 25 | Hard cap across all layers |
| `max_pods` | 5 | Maximum pod leads |
| `max_sub_per_pod` | 5 | Maximum sub-agents per pod |
| `default_head_model` | opus | Department head model |
| `default_lead_model` | opus | Pod lead default model |
| `default_sub_model` | sonnet | Sub-agent default model |
| `allow_haiku` | true | Whether pod leads can downshift subs to haiku |
| `auto_detect` | true | Suggest team mode on complex tasks |
| `worktree_cleanup` | true | Auto-delete worktrees after merge |

### Mid-Run Controls

- User can message department head at any time in the terminal
- Department head pauses, responds, adjusts plan
- `/team-cancel` — department head cleans up worktrees, logs final state

## Adaptive Sizing

Department head analyzes the task and decides team shape:

| Task Complexity | Pods | Sub-Agents | Total Agents | Example |
|----------------|------|------------|--------------|---------|
| Trivial | 1 | 0 | 2 | Fix a typo across files |
| Simple | 1-2 | 1-2 each | 4-7 | Add a single feature |
| Medium | 2-3 | 2-3 each | 7-12 | Build a module with tests |
| Complex | 3-5 | 3-5 each | 12-25 | Multi-domain feature set |

User can override with `--pods N` or explicit pod config.

## Security Considerations

- Worktrees inherit the repo's `.claude/settings.json` permissions — no escalation
- Sub-agents cannot modify files outside their worktree
- Department head is the only agent that touches `main` branch
- `.team-log` is append-only — agents cannot modify history
- Agent cap prevents runaway spawning

## Future Considerations

- Session persistence: resume a `/team` run after interruption
- Team templates: save and reuse pod configurations
- BigEd fleet integration: bridge team orchestrator to fleet supervisor
- Metrics dashboard: visualize team performance from log data
- Cross-repo teams: pods working across multiple repositories
