---
name: fleet-control
description: Control BigEd CC fleet from VS Code — dispatch tasks, respond to agents, manage queue
---

# Fleet Control from VS Code

## BigEd Help

When the user says "BigEd help", "BigEd guide", or asks how to use BigEd from VS Code:
- Read and present the contents of `fleet/VSCODE_README.md`
- This command only works in projects that have a `fleet/` directory (BigEd projects)

When the user wants to interact with the BigEd CC fleet from this VS Code session:

## CLI Commands (lead_client.py)

All commands run from the repo root. The fleet CLI talks directly to `fleet.db` (no server required).

### Status & Health

```bash
# Fleet status — agents, task counts
python fleet/lead_client.py status

# Agent logs (last N lines)
python fleet/lead_client.py logs researcher --tail 50
python fleet/lead_client.py logs coder_1 --tail 100

# Model health — installed vs needed, loaded models
python fleet/lead_client.py model-check

# Token usage and cost breakdown (period: hour/day/week/month)
python fleet/lead_client.py usage --period day

# Cost forecast — project future spend
python fleet/lead_client.py usage-forecast --days 7

# Token budget status per skill
python fleet/lead_client.py budget
```

### Task Dispatch

```bash
# Natural language task (intent parsed by local model)
python fleet/lead_client.py task "review the code in fleet/providers.py"

# With priority (1=low, 10=critical) and wait for result
python fleet/lead_client.py task "search arxiv for RAG papers" --priority 8 --wait

# Explicit skill + JSON payload (used by launcher, scripts)
python fleet/lead_client.py dispatch --skill code_review --payload '{"file": "fleet/worker.py"}'

# Dispatch with agent assignment
python fleet/lead_client.py dispatch --skill security_audit --payload '{"scope": "fleet/"}' --assigned-to security
```

### Task Results & Chains

```bash
# Get result of a specific task
python fleet/lead_client.py result 42

# Task chain status (for multi-step workflows)
python fleet/lead_client.py chain-status 100

# Resume failed chain from checkpoint
python fleet/lead_client.py chain-resume 100
```

### Agent Messaging

```bash
# Send direct message to an agent
python fleet/lead_client.py send researcher "check for new papers on RAG"

# Broadcast to all agents
python fleet/lead_client.py broadcast "pause non-critical work, focus on security review"

# Check an agent's inbox (unread only by default)
python fleet/lead_client.py inbox researcher
python fleet/lead_client.py inbox coder_1 --all --limit 20

# Channel notes (scratchpad)
python fleet/lead_client.py notes fleet
python fleet/lead_client.py notes fleet --post "deploying new skill tomorrow"
```

### Human-in-the-Loop (HITL)

```bash
# List all pending HITL requests from agents
python fleet/lead_client.py hitl

# Respond to an agent's question
python fleet/lead_client.py hitl respond 42 "Yes, approved — deploy to staging"
```

### Security Advisories

```bash
# List pending security advisories
python fleet/lead_client.py advisories

# Dismiss (archive) an advisory
python fleet/lead_client.py advisories dismiss ADV-2026-001
```

### Workflows

```bash
# List available workflow definitions
python fleet/lead_client.py workflow-list

# Validate a workflow without executing
python fleet/lead_client.py workflow-validate security-sweep

# Execute a workflow with variables
python fleet/lead_client.py workflow-run security-sweep --var target=fleet/ --var depth=full
```

### Fleet Config & Portability

```bash
# Export fleet config, skills, curricula to tarball (secrets redacted)
python fleet/lead_client.py export -o fleet-backup.tar.gz

# Import from tarball (dry-run first, then merge)
python fleet/lead_client.py import fleet-backup.tar.gz --dry-run
python fleet/lead_client.py import fleet-backup.tar.gz --merge

# Agent Card metadata
python fleet/lead_client.py agent-cards
python fleet/lead_client.py agent-cards --role security

# Manual backup
python fleet/lead_client.py backup
python fleet/lead_client.py backup --list
```

## REST API (Dashboard at localhost:5555)

The dashboard must be running (auto-started by supervisor or `python fleet/dashboard.py`). All mutating endpoints require an operator or admin token via `X-Token` header if auth is enabled in `fleet.toml`.

### Fleet Status

```bash
# Live agent status + task counts
curl http://localhost:5555/api/status

# Unified health check (all subsystems)
curl http://localhost:5555/api/health

# Agent performance metrics
curl http://localhost:5555/api/agents/performance

# Thermal / hardware state
curl http://localhost:5555/api/thermal

# Registered skills list
curl http://localhost:5555/api/skills
```

### Task Dispatch & Management

```bash
# Dispatch a task via webhook
curl -X POST http://localhost:5555/api/trigger \
  -H "Content-Type: application/json" \
  -d '{"type": "code_review", "payload": {"file": "fleet/worker.py"}}'

# Dispatch with priority and agent assignment
curl -X POST http://localhost:5555/api/trigger \
  -H "Content-Type: application/json" \
  -d '{"type": "security_audit", "payload": {"scope": "fleet/"}, "priority": 9, "assigned_to": "security"}'

# Check trigger configuration and state
curl http://localhost:5555/api/trigger/status
```

### Agent Communication

```bash
# Per-channel message counts + recent activity
curl http://localhost:5555/api/comms

# Discussion threads
curl http://localhost:5555/api/discussions
```

### Cost & Usage

```bash
# Token usage summary (period: hour/day/week/month)
curl "http://localhost:5555/api/usage?period=day"

# Usage comparison between date ranges
curl "http://localhost:5555/api/usage/delta?from_start=2026-03-01&from_end=2026-03-15&to_start=2026-03-15&to_end=2026-03-22"

# Budget status
curl http://localhost:5555/api/usage/budgets

# Full cost dashboard (charts + breakdown)
curl http://localhost:5555/api/usage/dashboard

# Cost regression detection
curl http://localhost:5555/api/usage/regression
```

### Human Feedback (Reinforcement)

```bash
# Submit feedback on an agent output (approved/rejected)
curl -X POST http://localhost:5555/api/feedback \
  -H "Content-Type: application/json" \
  -d '{"output_path": "task:42", "verdict": "approved", "feedback_text": "Good analysis"}'

# Reject with feedback (triggers re-review)
curl -X POST http://localhost:5555/api/feedback \
  -H "Content-Type: application/json" \
  -d '{"output_path": "task:42", "verdict": "rejected", "feedback_text": "Missed the null check in line 89", "agent_name": "coder_1", "skill_type": "code_review"}'

# Query feedback history
curl "http://localhost:5555/api/feedback?verdict=rejected&limit=10"

# Feedback statistics
curl http://localhost:5555/api/feedback/stats
```

### Knowledge & Reviews

```bash
# Knowledge base entries
curl http://localhost:5555/api/knowledge

# Code review outputs
curl http://localhost:5555/api/reviews

# Code stats (index, complexity)
curl http://localhost:5555/api/code_stats

# RAG vector store status
curl http://localhost:5555/api/rag
```

### Worker Control

```bash
# Disable a worker (graceful — finishes current task)
curl -X POST http://localhost:5555/api/fleet/worker/coder_2/disable

# Re-enable a worker
curl -X POST http://localhost:5555/api/fleet/worker/coder_2/enable

# Agent cards metadata
curl http://localhost:5555/api/fleet/agent-cards

# Provider health (Ollama, Claude, Gemini)
curl http://localhost:5555/api/fleet/provider-health
```

### Monitoring & Alerts

```bash
# Active alerts (last 24h)
curl "http://localhost:5555/api/alerts?hours=24"

# Acknowledge an alert
curl -X POST http://localhost:5555/api/alerts/ack/1

# Activity timeline
curl http://localhost:5555/api/timeline

# Idle evolution stats
curl http://localhost:5555/api/evolution

# SLA metrics
curl http://localhost:5555/api/sla

# AI-generated recommendations
curl http://localhost:5555/api/recommendations
```

### Batch Dashboard (Single Call)

```bash
# Fetch multiple panels in one request (reduces round-trips)
curl http://localhost:5555/api/dashboard/batch
```

### SSE Live Stream

```bash
# Subscribe to real-time events (task completions, alerts, feedback)
curl -N http://localhost:5555/api/stream
```

### MCP Servers

```bash
# MCP server registry status
curl http://localhost:5555/api/mcp/status

# Enable/disable an MCP server
curl -X POST http://localhost:5555/api/mcp/server/my-server/enable
curl -X POST http://localhost:5555/api/mcp/server/my-server/disable
```

### Audit & Compliance

```bash
# Audit log (filesystem access, SOC 2)
curl http://localhost:5555/api/audit

# Export audit log
curl http://localhost:5555/api/audit/export

# Data integrity check
curl http://localhost:5555/api/integrity

# Cache stats and invalidation
curl http://localhost:5555/api/cache/stats
curl -X POST http://localhost:5555/api/cache/invalidate
```

## Working with Fleet Files

The fleet stores its work in these directories:
- `fleet/knowledge/code_reviews/` — agent code review outputs
- `fleet/knowledge/code_discussion/` — agent discussion threads
- `fleet/knowledge/code_drafts/` — new skill drafts awaiting human review
- `fleet/knowledge/security/pending/` — security advisories awaiting review
- `fleet/knowledge/security/reviews/` — completed security review reports
- `fleet/knowledge/quality/reviews/` — code quality review reports
- `fleet/knowledge/oss_reviews/` — OSS project review reports
- `fleet/knowledge/refactors/` — code refactor proposals
- `fleet/knowledge/evaluations/` — agent self-evaluation reports
- `fleet/knowledge/marathon/` — long-running marathon session logs
- `fleet/knowledge/evolution/` — idle evolution run logs
- `fleet/logs/` — per-agent log files (researcher.log, coder_1.log, etc.)
- `fleet/task-briefing.md` — context written by BigEd for this session

## Important Notes

- The fleet runs independently — agents continue working while you are in VS Code
- HITL requests from agents appear in the BigEd launcher's Fleet Comm tab and via `lead_client.py hitl`
- You can respond to HITL via CLI (`lead_client.py hitl respond`) or REST API (`/api/feedback`)
- Tasks you dispatch are picked up by the next available agent (or a specific agent if `assigned_to` is set)
- `fleet.toml` is the central config — changes take effect within 5 minutes (auto-reload)
- Skills never auto-deploy: drafts go to `knowledge/code_drafts/`, operator reviews before promotion
- The CLI (`lead_client.py`) talks directly to `fleet.db` and does NOT require the dashboard to be running
- The REST API requires the dashboard (`python fleet/dashboard.py` or auto-started by supervisor)
- Default dashboard port is 5555, configurable in `fleet.toml` under `[dashboard]`
