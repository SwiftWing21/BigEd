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
