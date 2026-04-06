# Fleet Agents

> Last updated: 2026-04-05 | Source: system initialization

## Core Agents (always active)
| Name | Role | Specialization |
|------|------|---------------|
| researcher | Research | Papers, arxiv, web search |
| coder_1 | Code | Architecture, review, quality |
| planner | Planning | Workload planning, task decomposition |
| archivist | Knowledge | Flashcards, knowledge organization, RAG |

## Demand-Scaled Agents (spawn on queue pressure)
| Name | Role | Trigger |
|------|------|---------|
| coder_2, coder_3 | Code | >2 pending code tasks |
| analyst | Analysis | autoresearch results analysis |
| security | Security | Security audits, pen tests |

## Disabled by Default
sales, onboarding, implementation, legal, account_manager, ds_rag, ds_fleet, ds_research

## Related
- [Overview](overview.md) | [Skills](skills.md)
- [Index](../index.md)
