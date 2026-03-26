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
