# BigEd CC — Early Training History

> **Context:** This data represents BigEd CC's initial autonomous training phase
> (pre-v0.050.00b). During this period, the fleet's idle evolution system was
> running `skill_test` as its primary training task at high frequency. This was
> before we implemented dynamic task creation, cooldown limits, and diversified
> skill rotation.
>
> The raw data has been archived. This file serves as a reference for the
> volume and pattern of early fleet training activity.

## Training Statistics (Pre-Beta)

| Metric | Value |
|--------|-------|
| Total tasks executed | 32,959 |
| skill_test tasks | 31,490 (95.5%) |
| Other tasks | 1,469 (4.5%) |
| Agents involved | researcher, coder_1, coder_2, coder_3, planner, archivist, analyst, security |
| Training period | March 2026 (continuous) |
| Average throughput | ~120 tok/s (qwen3:8b GPU) |

## Task Distribution (Non-Training)

| Skill | Done | Failed | Waiting |
|-------|------|--------|---------|
| evolution_coordinator | 189 | — | — |
| research_loop | 144 | — | — |
| skill_evolve | 40 | 8 | 7 |
| web_search | 35 | — | — |
| summarize | 24 | 17 | — |
| model_recommend | 14 | — | — |
| discuss | 7 | — | — |
| key_manager | 7 | — | — |
| analyze_results | 4 | — | — |

## What Changed After v0.050.00b

1. **skill_test removed from idle rotation** — was 95.5% of all tasks
2. **Idle evolution cooldown** — 60s minimum between runs (was 3s)
3. **Idle threshold** — 30 polls before evolution (was 3 polls)
4. **Diversified skills** — code_quality, benchmark, skill_evolve, code_review, summarize
5. **3-failure backoff** — stops idle evolution after 3 consecutive failures
6. **Global dedup** — skips evolution if 3+ tasks already pending
7. **Adaptive polling** — workers sleep 0.1s/0.5s/2s based on queue depth

## Lessons Learned

- Uncapped idle evolution creates a task flood that dominates the entire system
- Tight polling (1s) + low idle threshold (3 polls) = 1 idle task per 3 seconds per agent
- With 4+ agents, this generates 4,800+ tasks/hour of low-value repetitive work
- The system needs backpressure: don't create idle work when real work exists
- Model performance (tok/s) is a better health metric than task count
