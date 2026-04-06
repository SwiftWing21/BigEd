# Fleet Skills

> Last updated: 2026-04-05 | Source: system initialization

## Overview
129 skills registered in fleet/skills/. Each implements `run(task, context) -> dict`.

## Skill Categories
| Category | Count | Examples |
|----------|-------|---------|
| Code | ~20 | code_review, code_write, code_discuss, refactor_verify |
| Research | ~10 | web_search, web_crawl, research_loop, rag_query |
| Security | ~8 | security_review, pen_test, secret_rotate, db_encrypt |
| Knowledge | ~10 | rag_index, rag_compress, knowledge_prune, knowledge_digest |
| ML/Data | ~8 | ml_train, dataset_synthesize, benchmark_model, model_recommend |
| DevOps | ~10 | git_suite, deploy_skill, service_manager, api_health_probe |
| Content | ~8 | doc_generate, changelog_generate, marketing, legal_draft |
| Fleet | ~8 | plan_workload, skill_chain, swarm_intelligence, evolution_coordinator |
| Quality | ~5 | quality_flywheel, stability_report, regression_detector |
| Misc | ~15+ | screenshot, billing_ocr, home_assistant, unifi_manage |

## Complexity Routing
- Simple skills → qwen3:4b (fast, CPU conductor)
- Medium/complex → qwen3:8b (quality, GPU)
- Defined in providers.py LOCAL_COMPLEXITY_ROUTING

## Related
- [Agents](agents.md) | [Overview](overview.md)
- [Index](../index.md)
