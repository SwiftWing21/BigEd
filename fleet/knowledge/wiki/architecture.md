# Architecture

> Last updated: 2026-04-05 | Source: system initialization

## Dual-Track Design
BigEd CC uses two implementation tracks that share a database, skill contract, and config format:

### Python Track (fleet/)
- **Role:** Development, full feature surface
- **UI:** Flask + Jinja + vanilla JS (256+ endpoints, 19 blueprints)
- **Skills:** 129 Python modules in fleet/skills/
- **Modules:** 9 UI tab plugins (CRM, accounts, ingestion, outputs, etc.)
- **Use case:** Daily development, prototyping, full dashboard

### Rust Track (biged-rs/)
- **Role:** Production deployment
- **Binary:** 11 MB release, 51 REST endpoints (axum)
- **GUI:** 5-section egui operator UI (Overview, Fleet, Tasks, Config, Logs)
- **Bridge:** PyO3 for skill execution (Python skills callable from Rust)
- **Use case:** Customer deployments, edge/air-gap, signed binary audits

### Shared Contracts
1. **Database:** fleet.db (SQLite, WAL, 34 tables)
2. **Skills:** `run(task: dict, context: dict) -> dict`
3. **Config:** fleet.toml parsed by both tracks

## Key Components
| Component | File | Purpose |
|-----------|------|---------|
| Supervisor | supervisor.py | Worker lifecycle, Ollama, scaling |
| Dr. Ders | hw_supervisor.py | Thermal, VRAM, model health |
| Dashboard | dashboard.py + 19 blueprints | Web UI + REST API |
| RAG | rag.py | FTS5/BM25 search + optional vectors |
| Audit | audit_scorer.py | 12-dimension scoring + claim schema |
| Providers | providers.py | Claude/Gemini/Local LLM routing |

## Related
- [Overview](overview.md) | [Security](security.md) | [Skills](skills.md)
- DEPLOYMENT.md (Rust track)
- SHARED_CONTRACTS.md (contract rules)
- [Index](../index.md)
