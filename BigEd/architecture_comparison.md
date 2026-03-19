# Architecture Comparison: BigEd CC vs Industry (2025-2026)

> Generated from web research comparing BigEd CC against CrewAI, AutoGen, LangGraph, OpenAI Agents SDK, Google ADK, and production patterns.

## Overall Assessment

| Topic | BigEd CC Status | vs. Industry |
|-------|----------------|-------------|
| Orchestration | Dual-supervisor + affinity routing | **Ahead** on hardware-awareness, behind on graph formalism |
| Communication | 4-layer channels + notes | **Ahead** on structure, behind on standards (A2A/MCP) |
| Task DAG | depends_on + cascade fail | **Aligned** on core, behind on validation and branching |
| Model fallback | 3-provider cascade + VRAM tiers | **Ahead** on VRAM-awareness, missing circuit breaker |
| Cost tracking | CT-1 through CT-4 | **Aligned**, needs enforcement modes |
| Safety | Evaluator + watchdog + HITL + DLP | **Ahead** on integration, missing input-side guards |
| Desktop GUI | CustomTkinter + Flask dashboard | **Unique** — no comparable open-source exists |

## Tier 1 — High Impact (0.01.01)

1. Circuit breaker for provider fallback (providers.py)
2. Input-side guardrails (scan payloads before LLM)
3. Configurable budget enforcement modes (warn/throttle/block)
4. DAG validation (cycle detection in post_task_chain)
5. Add Gemini pricing to PRICING dict

## Tier 2 — Medium Impact (0.01.02+)

6. Conditional DAG edges (branch on task results)
7. Agent Card metadata (JSON capability descriptors)
8. Cost-aware task routing (complexity → model selection)
9. Provider health probes in hw_supervisor
10. PII detection alongside secret scrubbing
11. Embedded charts in launcher/dashboard
12. Message schema versioning
13. Pipeline checkpointing
14. Weekly/monthly budget periods

## Tier 3 — Future (backlog)

15. Declarative workflow definition (TOML/DSL)
16. A2A protocol compatibility
17. NeMo Guardrails integration
18. Web-accessible launcher variant
19. DAG visualization endpoint
20. Cost forecasting
