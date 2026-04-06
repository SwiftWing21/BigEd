# Fleet Overview

> Last updated: 2026-04-05 | Source: system initialization

## Current State
- **Skills:** 129 registered across fleet/skills/
- **Endpoints:** 256+ across dashboard.py + 19 blueprints
- **DB tables:** 34 in fleet.db (WAL mode)
- **Agents:** 14 registered (4 core active, 10 scaled on demand)
- **Smoke tests:** 51/52 passing
- **Pytest:** 852 collected

## Architecture
BigEd CC runs a dual-track architecture:
- **Python track** — development, full dashboard, 228+ endpoints, all skills and modules
- **Rust track** — production deployment, 51 endpoints, egui operator GUI, PyO3 skill bridge

See [Architecture](architecture.md) for details.

## Recent Activity
Initialized from system state 2026-04-05. Future updates populated by agent activity.

## Related
- [Agents](agents.md) | [Skills](skills.md) | [Security](security.md) | [Architecture](architecture.md)
- [Index](../index.md)
