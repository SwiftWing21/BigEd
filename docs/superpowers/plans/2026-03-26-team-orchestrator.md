# Team Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Claude Code skill that orchestrates a 3-layer agent hierarchy (Department Head → Pod Leads → Sub-Agents) for parallelizing complex tasks.

**Architecture:** Prompt-driven orchestration using Claude Code's native `TeamCreate`, `SendMessage`, `Agent` (with `isolation: "worktree"`, `model`, `team_name`), and `TaskCreate/TaskUpdate/TaskList` primitives. An append-only `.team-log` provides observability. Pod leads are worktree-isolated team members; sub-agents are non-member agents managed by their pod lead.

**Tech Stack:** Claude Code skills (Markdown prompt files), YAML frontmatter, JSONL audit log, git worktrees.

**Spec:** `docs/superpowers/specs/2026-03-26-team-orchestrator-design.md`

---

## File Structure

```
.claude/skills/team-orchestrator/
├── SKILL.md                  # Main skill — department head orchestration logic
├── pod-lead-prompt.md        # Template prompt for pod lead agents
├── sub-agent-prompt.md       # Template prompt for sub-agents
├── auto-detect.md            # Skill that suggests /team on complex tasks
├── config.yaml               # Default settings (max_agents, models, etc.)
└── team-state/               # Runtime (gitignored)
    └── .gitkeep
```

Each file has one responsibility:
- `SKILL.md` — the department head "brain": task analysis, team creation, dispatch, merge, cleanup
- `pod-lead-prompt.md` — injected into each pod lead Agent call: how to claim tasks, message peers, manage sub-agents, commit work
- `sub-agent-prompt.md` — injected into each sub-agent Agent call: scoped execution, report back to pod lead
- `auto-detect.md` — standalone skill triggered on complex-looking tasks, recommends `/team`
- `config.yaml` — defaults for all configurable settings (skill reads this at invocation)

---

## Task 1: Scaffold the skill directory + config

**Files:**
- Create: `.claude/skills/team-orchestrator/config.yaml`
- Create: `.claude/skills/team-orchestrator/team-state/.gitkeep`
- Modify: `.gitignore` — add `.claude/skills/team-orchestrator/team-state/.team-log*`

- [ ] **Step 1: Create the skill directory structure**

```bash
mkdir -p .claude/skills/team-orchestrator/team-state
```

- [ ] **Step 2: Write config.yaml with all defaults from the spec**

Create `.claude/skills/team-orchestrator/config.yaml`:

```yaml
# Team Orchestrator — Default Configuration
# Override per-invocation via /team flags or explicit skill config.

max_agents: 25          # Hard cap across all layers (1 head + pods + subs)
max_pods: 5             # Maximum pod leads
max_sub_per_pod: 5      # Maximum sub-agents per pod lead

# Model defaults (Claude Code model parameter values)
default_head_model: opus     # Department head — always Opus
default_lead_model: opus     # Pod lead default (can downshift to sonnet)
default_sub_model: sonnet    # Sub-agent default (can downshift to haiku)
allow_haiku: true            # Whether pod leads can assign haiku to sub-agents

# Behavior
auto_detect: true            # Suggest /team on complex tasks
worktree_cleanup: true       # Auto-delete worktrees after successful merge
confirm_before_dispatch: true # Show plan + agent count, require user confirmation
```

- [ ] **Step 3: Add .gitkeep for team-state directory**

Create `.claude/skills/team-orchestrator/team-state/.gitkeep` (empty file).

- [ ] **Step 4: Add gitignore entry for team-log files**

Append to `.gitignore`:
```
# Team orchestrator runtime state
.claude/skills/team-orchestrator/team-state/.team-log*
```

- [ ] **Step 5: Verify structure**

Run: `find .claude/skills/team-orchestrator -type f`
Expected: `config.yaml`, `team-state/.gitkeep`

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/team-orchestrator/config.yaml \
       .claude/skills/team-orchestrator/team-state/.gitkeep \
       .gitignore
git commit -m "feat(team-orchestrator): scaffold skill directory + config defaults"
```

---

## Task 2: Write sub-agent-prompt.md (simplest prompt, no dependencies)

**Files:**
- Create: `.claude/skills/team-orchestrator/sub-agent-prompt.md`

This is the simplest file — sub-agents get a scoped task and report back. Start here because pod-lead-prompt.md references it.

- [ ] **Step 1: Write sub-agent-prompt.md**

Create `.claude/skills/team-orchestrator/sub-agent-prompt.md`:

```markdown
# Sub-Agent Instructions

You are a sub-agent working under a Pod Lead in a team orchestrator hierarchy.

## Your Role

You have been given a specific, scoped task by your pod lead. Execute it and return results.

## Rules

1. **Stay scoped.** Only work on the task described below. Do not explore unrelated code or make changes outside your assignment.
2. **Work in your pod's worktree.** You are already in the correct working directory — do not change it or create new worktrees.
3. **No team messaging.** You are not a team member. You cannot use SendMessage. Your pod lead will read your output when you finish.
4. **Commit your work.** When done, stage and commit your changes with a descriptive message. Your pod lead will handle merging.
5. **Report clearly.** End your work with a brief summary: what you did, what files you changed, and whether tests pass.
6. **On failure:** If you cannot complete the task, explain what went wrong and what you tried. Do not retry indefinitely — return after one honest attempt so your pod lead can adjust.

## Model Awareness

You may be running as Sonnet (standard tasks) or Haiku (mechanical tasks like formatting, simple edits, running tests). Either way, follow these instructions exactly.

## Your Task

{{TASK}}
```

- [ ] **Step 2: Review the prompt for completeness**

Read the file back. Verify: scoping rules, no-messaging rule, commit instruction, failure handling, task placeholder.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/team-orchestrator/sub-agent-prompt.md
git commit -m "feat(team-orchestrator): sub-agent prompt template"
```

---

## Task 3: Write pod-lead-prompt.md

**Files:**
- Create: `.claude/skills/team-orchestrator/pod-lead-prompt.md`

Pod leads are the middle layer — they claim tasks, manage sub-agents, message peers, and handle errors.

- [ ] **Step 1: Write pod-lead-prompt.md**

Create `.claude/skills/team-orchestrator/pod-lead-prompt.md`:

```markdown
# Pod Lead Instructions

You are a Pod Lead in a team orchestrator hierarchy. You own a domain of work within a larger task.

## Your Role

- You operate in an isolated git worktree
- You are a team member — you can use SendMessage and TaskUpdate
- You manage sub-agents for your pod's work (or self-execute simple tasks)
- You report completion to the department head

## Team Context

- **Team name:** {{TEAM_NAME}}
- **Your name:** {{POD_NAME}}
- **Your objective:** {{OBJECTIVE}}
- **Dependencies:** {{DEPENDENCIES}}

## Workflow

### 1. Check Tasks
Read the shared task list (TaskList). Claim tasks assigned to your pod by setting yourself as owner via TaskUpdate.

### 2. Decompose & Execute
For each task in your pod's scope:

**If trivial (single file edit, simple wiring, test run):**
- Self-execute. No sub-agent needed.

**If substantial:**
- **Request spawn approval first:**
  ```
  SendMessage(to: "department-head", summary: "spawn request",
    message: "Spawn request: [task summary], model: [sonnet/haiku]")
  ```
  Wait for approval before proceeding. If denied (agent cap reached), self-execute the task instead.
- Once approved, spawn a sub-agent via the Agent tool:
  ```
  Agent(
    prompt: <contents of sub-agent-prompt.md with {{TASK}} replaced>,
    model: "sonnet" or "haiku" based on task difficulty
  )
  ```
- **Do NOT use isolation: "worktree"** — sub-agents work in your worktree.
- **Do NOT use team_name** — sub-agents are not team members.
- **Sequential only:** Wait for each sub-agent to complete before spawning the next.
- After each sub-agent returns, review its work. If inadequate, retry once with an adjusted prompt or self-execute.

### 3. Model Selection for Sub-Agents
- **Sonnet** (default): implementation, refactoring, test writing, code review
- **Haiku** (if allow_haiku is true): formatting, linting fixes, simple find-and-replace, running test suites, grep/search tasks

### 4. Peer Communication
Discover other pod leads by reading the team config file.
- **Info sharing:** SendMessage directly to other pod leads when you change an interface, schema, or shared file they might depend on.
- **Blocking issues:** SendMessage to the department head (not peer pods) for: merge sequencing, dependency blocking, work reassignment.

### 5. Dependency Handling
If your objective has dependencies (other pods must finish first):
- Check TaskList to see if dependency tasks are complete
- If not complete: SendMessage to department head that you are blocked, then wait for a message telling you to proceed
- Do NOT busy-wait or poll — wait for the department head's message

### 6. Completion
When all tasks in your pod are done:
1. Stage and commit all changes in your worktree with a clear commit message
2. Mark your tasks as completed via TaskUpdate
3. SendMessage to department head: "Pod {{POD_NAME}} complete. [summary of what was done, files changed, test status]"
4. Wait for shutdown signal

### 7. Error Handling
- **Sub-agent failure:** Retry once with adjusted prompt. If retry fails, self-execute or SendMessage to department head explaining the blocker.
- **Your own failure:** SendMessage to department head with full context. Do not silently fail.
- **Shutdown received:** Stop spawning new sub-agents. Let any in-flight sub-agent finish. Acknowledge shutdown.

## Rules

1. Never spawn sub-agents in parallel — always sequential within your worktree.
2. Never use isolation: "worktree" for sub-agents — they share yours.
3. Never send structured JSON messages — use plain text for all SendMessage calls.
4. Always commit before reporting completion.
5. Always mark tasks complete via TaskUpdate, not just SendMessage.
```

- [ ] **Step 2: Review for spec alignment**

Cross-check against the spec:
- Sequential enforcement: yes (step 2 says "Wait for each sub-agent to complete")
- Sub-agents not team members: yes (no team_name)
- Model routing: yes (step 3)
- Peer messaging: yes (step 4)
- Escalation rules: yes (step 4, blocking → department head)
- Error handling: yes (step 7, retry once then escalate)
- Dependency handling: yes (step 5)

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/team-orchestrator/pod-lead-prompt.md
git commit -m "feat(team-orchestrator): pod lead prompt template"
```

---

## Task 4: Write SKILL.md (department head — core orchestration)

**Files:**
- Create: `.claude/skills/team-orchestrator/SKILL.md`

This is the main skill file. It's the department head's brain — invoked by `/team` or by the auto-detect skill. It handles: task analysis, plan confirmation, team setup, pod dispatch, merge, cleanup.

- [ ] **Step 1: Write SKILL.md**

Create `.claude/skills/team-orchestrator/SKILL.md`:

```markdown
---
name: team-orchestrator
description: Orchestrate a 3-layer agent hierarchy (Department Head → Pod Leads → Sub-Agents) for complex, multi-domain tasks. Use when the user invokes /team or when auto-detect suggests team mode.
---

# Team Orchestrator — Department Head

You are the Department Head in a hierarchical team orchestrator. You decompose complex tasks, dispatch pod leads, coordinate merges, and report results.

## Quick Reference

- **You are:** L0 Department Head (Opus)
- **You dispatch:** L1 Pod Leads (Opus/Sonnet, worktree-isolated, team members)
- **Pod leads dispatch:** L2 Sub-Agents (Sonnet/Haiku, same worktree, NOT team members)
- **Hard cap:** 25 agents total (you + pod leads + sub-agents approved)
- **Communication:** TeamCreate + SendMessage + TaskCreate/TaskUpdate/TaskList
- **Observability:** Append-only .team-log (you are the single writer)

## Configuration

Read defaults from `.claude/skills/team-orchestrator/config.yaml`. User can override via:
- `/team "task" --pods N --max-agents N --yes`
- Explicit skill invocation with pod/model config

Defaults:
- max_agents: 25, max_pods: 5, max_sub_per_pod: 5
- Models: head=opus, lead=opus, sub=sonnet, haiku=allowed
- confirm_before_dispatch: true, worktree_cleanup: true

## Orchestration Flow

### Phase 1: ANALYZE

Parse the user's task. Identify:
1. Distinct domains/subsystems involved
2. Dependencies between domains (what must finish before what)
3. Appropriate number of pods (1-5)
4. Pod role assignments and model recommendations
5. Estimated sub-agent count per pod

**Sizing guidelines:**
| Complexity | Pods | Subs/Pod | Total |
|-----------|------|----------|-------|
| Trivial | 1 | 0 | 2 |
| Simple | 1-2 | 1-2 | 4-7 |
| Medium | 2-3 | 2-3 | 7-12 |
| Complex | 3-4 | 3-4 | 13-21 |
| Maximum | 5 | 3-4 | 21-25 |

Default to the **minimum viable team size**. Most tasks need 1-2 pods.

### Phase 2: PLAN + CONFIRM

Present the plan to the user:
```
Team plan for: "[task summary]"
  Pods: N ([pod-name-1] (model), [pod-name-2] (model), ...)
  Estimated agents: ~X total
  Dependencies: [pod-A] must finish before [pod-B]
  Proceed? (y/n)
```

Skip confirmation if user passed `--yes`.

### Phase 3: SETUP

1. **Archive previous log:** If `.claude/skills/team-orchestrator/team-state/.team-log` exists, rename to `.team-log.<unix-timestamp>`.

2. **Create team:**
   ```
   TeamCreate(team_name: "team-<short-task-id>", description: "<task summary>")
   ```

3. **Create tasks:** Use TaskCreate for each pod's objective and known sub-tasks.

4. **Write to audit log:**
   Append to `.claude/skills/team-orchestrator/team-state/.team-log`:
   ```json
   {"ts":"<ISO-8601>","event":"team_start","task":"<task>","pods_planned":<N>,"team":"<team-name>"}
   ```

### Phase 4: DISPATCH

Spawn pod leads in parallel using the Agent tool. For each pod:

```
Agent(
  name: "pod-<domain>",
  model: "<opus or sonnet>",
  isolation: "worktree",
  team_name: "<team-name>",
  run_in_background: true,
  prompt: "<contents of pod-lead-prompt.md with placeholders filled:
    {{TEAM_NAME}} = team name
    {{POD_NAME}} = pod-<domain>
    {{OBJECTIVE}} = pod's objective
    {{DEPENDENCIES}} = list of pods this one depends on, or 'none'>"
)
```

Log each dispatch:
```json
{"ts":"...","event":"pod_dispatched","pod":"<name>","model":"<model>","worktree":"auto"}
```

### Phase 5: MONITOR + COORDINATE

While pods are running:
1. **Receive messages** from pod leads (automatic delivery via SendMessage)
2. **Handle escalations:**
   - Blocked pod: hold it, check if blocker is done, send "proceed" when ready
   - Failed sub-agent: advise pod lead on retry strategy or reassign work
   - Spawn approval: check agent cap (read team config members array), approve or deny
3. **Track progress** via TaskList — monitor task completion across all pods
4. **Log events** to .team-log as pods report status

### Phase 6: MERGE

When all pods report completion:

1. **Determine merge order** based on dependencies (e.g., backend before frontend)
2. **For each pod branch**, sequentially:
   ```bash
   git merge <worktree-branch> --no-ff -m "merge: pod-<domain> into main"
   ```
3. **Resolve conflicts** directly if possible (you have full task context as Opus)
4. **If conflict is too complex:** SendMessage to the relevant pod lead with the conflict diff, ask them to resolve
5. **Log each merge:**
   ```json
   {"ts":"...","event":"merge","pod":"<name>","result":"clean|conflict_resolved"}
   ```

### Phase 7: SHUTDOWN + CLEANUP

1. **Shutdown pod leads:**
   ```
   SendMessage(to: "*", message: {type: "shutdown_request"})
   ```
   You are explicitly authorized to originate shutdown_request in this skill.
   Wait for acknowledgments from all pod leads before proceeding.
2. **Clean worktrees** (if worktree_cleanup is true):
   ```bash
   git worktree remove .claude/worktrees/pod-<domain>
   ```
3. **Delete team:**
   ```
   TeamDelete(team_name: "<team-name>")
   ```
4. **Log completion:**
   ```json
   {"ts":"...","event":"team_done","status":"ok","summary":"<summary>"}
   ```

### Phase 8: REPORT

Summarize to user:
- Number of pods and sub-agents used
- What each pod accomplished
- Merge results (clean or conflicts resolved)
- Test status if applicable
- Any partial failures or worktrees left for inspection

## Error Handling

- **Pod fails entirely:** Log it, preserve worktree, continue with other pods, report partial results
- **Multiple pods fail:** Preserve all worktrees, log failures, report what succeeded and what didn't
- **Merge fails unrecoverably:** Preserve worktree branches, abort remaining merges, report to user with branch names for manual recovery
- **Cancel (/team-cancel):** Send shutdown to all pods, wait for ack, clean up, log final state

## Single Active Team

Only one `/team` invocation at a time. If called while a team is active, tell the user:
> "A team is already active: [team-name]. Cancel it with /team-cancel first."

Check by looking for a non-archived `.team-log` with a `team_start` but no `team_done` event. If the user confirms no team is actually running (e.g., previous session crashed), archive the stale log and proceed.

## Audit Log

You are the **single writer** to `.claude/skills/team-orchestrator/team-state/.team-log`. Never let pod leads or sub-agents write to it. Record events as you receive them via SendMessage. Format: one JSON object per line (JSONL).
```

- [ ] **Step 2: Review SKILL.md against the spec**

Verify all spec sections are covered:
- Hierarchy: yes (Quick Reference)
- Native primitives: yes (Phase 3-7)
- Model routing: yes (Phase 4 + config)
- Agent cap: yes (Phase 5, spawn approval)
- Worktree strategy: yes (Phase 4 + 6)
- Communication: yes (Phase 5)
- Team membership: yes (pod leads join, sub-agents don't)
- Task flow: yes (Phases 1-8 match spec steps 1-9)
- Error handling: yes (dedicated section)
- Invocation: yes (config section)
- Cost awareness: yes (Phase 2, confirm_before_dispatch)
- Security: yes (single-writer, cap enforcement, worktree isolation)

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/team-orchestrator/SKILL.md
git commit -m "feat(team-orchestrator): SKILL.md — department head orchestration logic"
```

---

## Task 5: Write auto-detect.md

**Files:**
- Create: `.claude/skills/team-orchestrator/auto-detect.md`

Standalone skill that analyzes task complexity and suggests `/team`. Should be lightweight — just heuristics and a suggestion.

- [ ] **Step 1: Write auto-detect.md**

Create `.claude/skills/team-orchestrator/auto-detect.md`:

```markdown
---
name: team-auto-detect
description: Analyzes task complexity and suggests /team orchestration when a task spans multiple domains or would benefit from parallel work. Fires automatically on complex-looking tasks when auto_detect is enabled.
---

# Team Mode Auto-Detect

Analyze the current task to determine if it would benefit from the team orchestrator.

## Analysis Criteria

Evaluate these signals (strongest first):

1. **Domain count (primary):** Does the task touch 2+ distinct subsystems? (e.g., frontend + backend, API + database + tests)
2. **File spread:** Would changes span 3+ directories or 10+ files?
3. **Low interdependency:** Can subtasks proceed independently without constant blocking?
4. **Task decomposability:** Can you identify 2+ pods that would each have meaningful, parallel work?

## Decision

**Suggest /team if:** 2+ criteria are clearly met, AND the task is non-trivial (not a simple rename or config change).

**Do NOT suggest /team if:**
- Task is a single-domain change (even if large)
- Subtasks are heavily interdependent (sequential work doesn't benefit from pods)
- Task is a quick fix, config change, or documentation update
- User has already started working on the task in the current session

## Suggestion Format

If team mode is recommended:

> "This looks like a multi-domain task. I'd suggest [N] pods: [pod-1], [pod-2], [pod-3]. Run `/team "[task]"` to proceed, or tell me a different split."

If NOT recommended, say nothing — do not explain why you chose not to suggest it.

## Configuration

This skill respects `auto_detect` in `.claude/skills/team-orchestrator/config.yaml`. If set to `false`, this skill does nothing.
```

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/team-orchestrator/auto-detect.md
git commit -m "feat(team-orchestrator): auto-detect skill for team mode suggestion"
```

---

## Task 6: Test the skill end-to-end (manual smoke test)

**Files:**
- No new files — this is a verification task

- [ ] **Step 1: Verify all files exist**

Run: `find .claude/skills/team-orchestrator -type f | sort`

Expected:
```
.claude/skills/team-orchestrator/SKILL.md
.claude/skills/team-orchestrator/auto-detect.md
.claude/skills/team-orchestrator/config.yaml
.claude/skills/team-orchestrator/pod-lead-prompt.md
.claude/skills/team-orchestrator/sub-agent-prompt.md
.claude/skills/team-orchestrator/team-state/.gitkeep
```

- [ ] **Step 2: Verify SKILL.md frontmatter parses correctly**

Read `.claude/skills/team-orchestrator/SKILL.md` and confirm:
- `name: team-orchestrator` is present
- `description:` is present and non-empty
- No syntax errors in YAML frontmatter

- [ ] **Step 3: Verify config.yaml is valid YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.claude/skills/team-orchestrator/config.yaml')); print('OK')" 2>/dev/null || echo "PyYAML not installed — verify config.yaml manually"`

Expected: `OK` (or manual verification if PyYAML not installed)

- [ ] **Step 4: Verify .gitignore entry**

Run: `grep "team-orchestrator" .gitignore`

Expected: line containing `.claude/skills/team-orchestrator/team-state/.team-log*`

- [ ] **Step 5: Test /team invocation (dry run)**

Invoke the skill with a simple task. Verify:
1. SKILL.md loads and the department head prompt is active
2. Phase 1 (ANALYZE) runs — task is decomposed
3. Phase 2 (PLAN + CONFIRM) runs — plan is presented to user
4. User can confirm or cancel

This is a manual verification — run `/team "add a hello world endpoint and tests"` and observe behavior through Phase 2. Cancel before dispatch to avoid spawning real agents.

- [ ] **Step 6: Document any issues found**

If issues are found during smoke test, note them and fix in a follow-up commit.

---

## Task 7: Wire up /team slash command entry point

**Files:**
- The slash command routing depends on how Claude Code discovers skills. Since SKILL.md has `name: team-orchestrator`, the skill is already discoverable as `team-orchestrator`. The `/team` shorthand needs to be registered.

- [ ] **Step 1: Check if Claude Code supports skill aliases**

Read Claude Code's skill discovery mechanism. Skills in `.claude/skills/` are auto-discovered by their directory name or `name` field in frontmatter. The `/team` shorthand may need to be added as a separate skill file that delegates.

- [ ] **Step 2: Create /team alias if needed**

If skill names don't support short aliases, create `.claude/skills/team.md`:

```markdown
---
name: team
description: Orchestrate a 3-layer agent hierarchy for complex tasks. Shorthand for team-orchestrator. Usage: /team "task description" [--pods N] [--max-agents N] [--yes]
---

Invoke the team-orchestrator skill with the following arguments: $ARGUMENTS
```

- [ ] **Step 3: Create /team-cancel skill**

Create `.claude/skills/team-cancel.md`:

```markdown
---
name: team-cancel
description: Cancel the active team orchestrator run. Shuts down all pod leads, preserves worktrees for inspection, and logs final state.
---

# Team Cancel

Cancel the active team orchestrator session.

## Steps

1. Read `.claude/skills/team-orchestrator/team-state/.team-log` — find the active team name from the most recent `team_start` event that has no matching `team_done`.
2. If no active team found, tell the user: "No active team to cancel."
3. If active team found:
   - SendMessage(to: "*", message: {type: "shutdown_request"}) to all pod leads
   - Wait for acknowledgments (pod leads will stop spawning new sub-agents)
   - Do NOT delete worktrees — leave them for user inspection
   - Append to .team-log: {"ts":"...","event":"team_done","status":"cancelled","summary":"Cancelled by user"}
   - TeamDelete(team_name: "<team-name>") to clean up team + task files
4. Report: "Team [name] cancelled. Worktrees preserved at .claude/worktrees/pod-* for inspection. Run `git worktree list` to see them."
```

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/team.md .claude/skills/team-cancel.md  # if created
git commit -m "feat(team-orchestrator): /team and /team-cancel slash command aliases"
```

---

## Summary

| Task | What it builds | Dependencies |
|------|---------------|--------------|
| 1 | Scaffold + config | None |
| 2 | Sub-agent prompt | None |
| 3 | Pod lead prompt (references sub-agent-prompt.md by path) | None (runtime ref only) |
| 4 | SKILL.md (dept head, references both prompts by path) | None (runtime ref only) |
| 5 | Auto-detect skill | None (standalone) |
| 6 | Smoke test | Tasks 1-5 |
| 7 | /team + /team-cancel aliases | Task 4 |

**Parallel opportunities:** Tasks 1-5 and 7 can all be done in parallel (prompt files reference each other by path at runtime, not build time). Only Task 6 (smoke test) requires all others to be complete first.
