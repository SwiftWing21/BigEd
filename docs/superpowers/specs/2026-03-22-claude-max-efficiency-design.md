# Claude Maximum Efficiency — Design Spec

**Date:** 2026-03-22
**Version:** 0.1
**Status:** Draft

---

## Overview

A skill + plugin that implements every known token-saving technique for Claude API usage. Reduces cost by 30-60% through caching, batching, MCP tool routing, and capacity window detection.

| Component | Purpose |
|-----------|---------|
| `claude_efficiency` | Fleet skill — auto-optimizes all Claude API calls |
| `.claude/skills/max-efficiency.md` | Plugin — teaches any Claude Code session to self-optimize |
| Capacity tracker | Detects 2x usage windows, auto-scales during bonus periods |

---

## Token Saving Techniques

### 1. Prompt Caching (30-60% input reduction)

```python
# cache_control on stable system prompts — 90% discount on cache hits
messages = [
    {"role": "user", "content": [
        {"type": "text", "text": system_prompt,
         "cache_control": {"type": "ephemeral"}},  # cached for 5 min
        {"type": "text", "text": user_message},
    ]}
]
```

**Where to apply in BigEd:**
- `_call_claude()` in providers.py — already has `cache_system` param but not all callers use it
- Every skill that calls `call_complex()` with a static system prompt should cache it
- CLAUDE.md content injected as cached prefix (read once, reuse across calls)

### 2. Message Batches API (50% cost reduction)

```python
# Non-realtime bulk processing — half price, 24hr completion window
POST /v1/messages/batches
{
    "requests": [
        {"custom_id": "task_1", "params": {"model": "claude-sonnet-4-6", ...}},
        {"custom_id": "task_2", "params": {...}},
    ]
}
```

**Where to apply:**
- Idle evolution tasks (not time-sensitive)
- Bulk code reviews (batch 5-10 files)
- Dataset synthesis (generate training data in bulk)
- Any skill with `COMPLEXITY = "simple"` that doesn't need realtime response

### 3. MCP Tool Routing (reduce round-trips)

Use MCP servers to do work without additional API calls:

| MCP Server | Saves | How |
|-----------|-------|-----|
| **filesystem** | File reads without API tokens | Agent reads files via MCP, not via prompts asking "read file X" |
| **sequential-thinking** | Multi-step reasoning without chain-of-thought tokens | Structured reasoning via tool calls |
| **memory** | Cross-session context without re-sending | Persistent key-value store |
| **playwright** | Web content without browser_crawl skill API call | Direct DOM access |

### 4. Capacity Window Detection (2x bonus usage)

Track Claude promotion windows and auto-scale during bonus periods:

```python
# Example promotion schedule
CAPACITY_WINDOWS = {
    "2026-03-13_to_2026-03-27": {
        "weekday_bonus_hours": [(0, 8), (14, 24)],  # ET: outside 8AM-2PM
        "weekend_bonus": True,  # all day
        "multiplier": 2.0,
    }
}
```

**Auto-scaling behavior during bonus windows:**
- Prefer Claude over local Ollama (free capacity)
- Auto-deploy 2 parallel agents for Claude tasks
- Route medium-complexity skills to Claude (normally local-only)
- Disable budget throttling during bonus hours (doesn't count against limits)

---

## Skill: claude_efficiency

### Contract

```python
SKILL_NAME = "claude_efficiency"
DESCRIPTION = "Optimize Claude API usage — caching, batching, MCP routing, capacity windows"
COMPLEXITY = "simple"
REQUIRES_NETWORK = False  # analyzes config, doesn't make API calls itself
```

### Actions

| Action | What it does |
|--------|-------------|
| `audit` | Analyze current API usage patterns, find optimization opportunities |
| `optimize` | Apply recommended optimizations to fleet.toml + providers.py config |
| `capacity_check` | Check if current time is in a bonus capacity window |
| `batch_queue` | Queue non-urgent tasks for batch processing |
| `report` | Show savings report (before/after cost comparison) |

### Audit Output

```json
{
    "current_monthly_cost": 12.50,
    "optimizations": [
        {
            "technique": "prompt_caching",
            "current": "3 of 15 skills use caching",
            "potential": "all 15 Claude-routed skills",
            "estimated_savings": "30% on input tokens ($3.75/month)"
        },
        {
            "technique": "batch_api",
            "current": "0 skills use batching",
            "potential": "8 non-realtime skills eligible",
            "estimated_savings": "50% on batch-eligible tasks ($2.10/month)"
        },
        {
            "technique": "capacity_windows",
            "current": "no window tracking",
            "potential": "shift 40% of Claude calls to bonus hours",
            "estimated_savings": "effectively 2x capacity at same cost"
        }
    ],
    "projected_monthly_cost": 6.65,
    "savings_pct": 47
}
```

---

## Capacity Tracker Integration

### fleet.toml config

```toml
[capacity]
enabled = true
provider = "claude"
# Promotion windows (updated manually or via skill)
[[capacity.windows]]
start = "2026-03-13"
end = "2026-03-27"
weekday_bonus_start_et = 14   # 2PM ET — bonus starts after this
weekday_bonus_end_et = 8      # 8AM ET — bonus ends before this
weekend_all_day = true
multiplier = 2.0

[capacity.auto_scale]
deploy_agents = 2              # auto-deploy N extra agents during bonus
prefer_claude = true           # route to Claude instead of local during bonus
disable_budget_throttle = true # bonus usage doesn't count against limits
```

### Supervisor Integration

In the supervisor main loop (every 5 min):

```python
if now - last_capacity_check >= 300:
    last_capacity_check = now
    try:
        from skills.claude_efficiency import is_in_bonus_window
        in_bonus = is_in_bonus_window(config)
        if in_bonus and not _bonus_active:
            _bonus_active = True
            log.info("Capacity bonus window active — scaling up Claude usage")
            # Deploy extra agents
            # Switch routing preference
        elif not in_bonus and _bonus_active:
            _bonus_active = False
            log.info("Capacity bonus window ended — restoring normal routing")
    except Exception:
        pass
```

### Provider Routing Adjustment

In `providers.py get_optimal_model()`:

```python
# During bonus window, prefer Claude for medium tasks
if _is_bonus_active():
    if complexity == "medium":
        return "claude"  # normally would be local
```

---

## Claude Code Plugin

### `.claude/skills/max-efficiency.md`

Teaches Claude Code sessions to self-optimize:

1. **Always cache system prompts** — use `cache_control: {type: "ephemeral"}` on any content that's reused across messages
2. **Batch when possible** — for non-urgent multi-file operations, suggest batching
3. **Use MCP tools first** — read files via filesystem MCP, not by asking "what's in file X"
4. **Reference CLAUDE.md** — don't re-derive conventions that are documented
5. **Minimize re-sends** — don't repeat prior context in follow-up messages

---

## File Layout

```
fleet/skills/claude_efficiency.py     # Fleet skill
.claude/skills/max-efficiency.md      # Claude Code plugin
```

---

## Dependencies

| Dependency | Status | Used For |
|-----------|--------|----------|
| cost_tracking.py | Exists | Usage data for audit/report |
| providers.py | Exists | Routing adjustment during bonus |
| supervisor.py | Exists | Capacity check in main loop |
| fleet.toml | Exists | [capacity] config section |
| event_triggers.py | Exists | Scheduled batch processing |

---

## Success Criteria

1. `audit` correctly identifies optimization opportunities from usage data
2. `capacity_check` accurately detects bonus windows from config
3. Prompt caching reduces input token costs by 30%+
4. Batch API reduces cost on eligible tasks by 50%
5. Bonus window auto-scaling increases Claude throughput during promotions
6. Plugin teaches any Claude Code session to save tokens without fleet dependency
