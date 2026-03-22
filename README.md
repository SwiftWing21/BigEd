# BigEd CC

> Personal educational project — a vibe-coding experiment pushing the limits of what AI-assisted development can build. Welcome to review and scrutiny.

BigEd CC is a local AI fleet platform built almost entirely through AI-assisted "vibe coding." It started as a learning exercise to explore how far you can push model-driven development and evolved into an 85-skill autonomous agent system with enterprise-grade guardrails. It is not a product — it is a reference implementation and playground for anyone curious about the real capabilities and limitations of building with AI.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

## What BigEd CC IS

- **A learning project** — Built to explore the boundaries of AI-assisted development, not to ship a SaaS
- **A reference implementation** — Demonstrates patterns for multi-model orchestration, agent lifecycle, safety guardrails, and fleet management at a non-trivial scale
- **A vibe-coding case study** — Nearly the entire codebase was generated, reviewed, and iterated on through AI models, making it a useful artifact for studying what AI-assisted development actually produces
- **Open to scrutiny** — The code, architecture decisions, and quality tradeoffs are all visible; judge for yourself what works and what doesn't

## What BigEd CC IS NOT

- **Not a product or service** — There is no hosted version, no paying customers, no SaaS pitch
- **Not a custom model** — BigEd orchestrates existing models (Ollama, Claude, Gemini); it does not train or fine-tune its own LLM
- **Not production-hardened** — Currently in beta (0.170.05b); the enterprise features (RBAC, DLP, audit logging) are implemented but not battle-tested at scale
- **Not a low-code tool** — You need Python 3.11+ and a willingness to read code to work with it meaningfully

## How It Was Built — Model Contributions

This project is itself an artifact of multi-model AI development. The rough contribution breakdown:

| Model | Role | Estimated Weight |
|-------|------|-----------------|
| **Claude Code (Opus 4.6)** | Primary architect and implementer — wrote the majority of the codebase, designed the fleet architecture, built the skill system, and drove most refactors | ~70% |
| **Claude (Sonnet 4.6)** | Day-to-day code review, audits, skill generation, quick patches, and iterative improvements | ~15% |
| **Gemini Pro (2.5/3.1)** | Independent reviews, architecture audits, second-opinion analysis, and HA fallback testing | ~10% |
| **Human (Max)** | Direction, judgment calls, testing, integration decisions, and the occasional manual fix when the models got stuck | ~5% |

### Design Guardrails

The project's quality standards come from two key sources:

- **`claude_code.py`** — A skill that wraps Claude Code CLI in headless mode with strict environment boundaries (DLP secret stripping, no shell=True, structured output capture). It enforces SOC 2-style confidentiality by scrubbing non-Anthropic secrets before subprocess execution and persisting all outputs to knowledge/ with immutable timestamps.
- **`FRAMEWORK_BLUEPRINT.md`** — The full architecture spec that defines module contracts, separation of concerns boundaries, data schemas, and the evaluator-optimizer loop. This document, combined with the 12-dimension grading rubric in `AUDIT_TRACKER.md`, forms the quality bar every change is measured against.

Together, these files act as the project's "constitution" — the models follow them, and the audit tracker grades compliance.

## Features

- **One-Click Setup** — Auto-installs Python, Ollama, models, and dependencies
- **85+ AI Skills** — Code review, security audit, web research, ML training, and more
- **Dynamic Agent Scaling** — 4 core agents + demand-based scaling (up to 16)
- **Multi-Model Support** — Ollama (local), Claude, Gemini, MiniMax M2.5
- **Manual Mode** — OAuth integration for Claude Code and Gemini sessions
- **Dr. Ders** — Hardware supervisor with thermal management and model tier scaling
- **Fleet Dashboard** — Real-time web UI at localhost:5555
- **Auto-Save Backup** — Configurable periodic snapshots with integrity verification
- **Cost Intelligence** — Per-call token tracking, budget enforcement, optimization recommendations
- **Enterprise Ready** — RBAC, DLP, audit logging, file access control, air-gap mode

## Quick Start

### Windows
```
Download Setup.exe from Releases → Run → Follow wizard
```

### From Source (All Platforms)
```bash
git clone https://github.com/SwiftWing21/BigEd.git
cd BigEd
python fleet/dependency_check.py          # pre-flight check
python fleet/smoke_test.py --fast         # verify 22/22 tests
python BigEd/launcher/launcher.py         # launch GUI
```

## Architecture

```
BigEd CC
├── BigEd/launcher/     — GUI launcher (customtkinter)
│   ├── ui/             — Boot sequence, settings, consoles, dialogs
│   ├── modules/        — Intelligence, Ingestion, Outputs (pluggable)
│   └── fonts/          — Custom pixel fonts
├── fleet/              — 85-skill AI worker fleet
│   ├── supervisor.py   — Process lifecycle + dynamic scaling
│   ├── hw_supervisor.py — Dr. Ders (thermal + model management)
│   ├── dashboard.py    — Web dashboard (localhost:5555)
│   ├── worker.py       — Generic task executor
│   ├── skills/         — 85 registered skills
│   └── knowledge/      — Agent-generated artifacts
├── autoresearch/       — ML training pipeline (inspired by Karpathy)
├── scripts/            — Setup scripts (Windows/Linux/macOS)
├── docs/specs/         — Enterprise integration specs
└── docs/flowcharts/    — System flow charts (boot, tasks, models, HITL)
```

## Model Support

| Provider | Models | Auth | Cost |
|----------|--------|------|------|
| **Ollama (Local)** | qwen3:8b, 4b, 1.7b, 0.6b | None | Free |
| **Claude** | Haiku, Sonnet, Opus | API key or OAuth | Per-token |
| **Gemini** | Flash, Pro | API key or OAuth | Per-token |
| **MiniMax** | M2.5 | API key | Per-token |

## Enterprise

- **Compliance Tiers**: Basic → Standard → Enterprise (SOC 2 aligned)
- **RBAC**: Admin / Operator / Viewer roles
- **File Access Control**: Per-zone read/read_write/full permissions
- **DLP**: Secret detection, base64 scanning, output scrubbing
- **Audit Trail**: All API calls, file access, config changes logged
- **Air-Gap Mode**: Full offline operation with local models only

## Multi-Machine Fleet Federation

BigEd CC supports multi-device fleet federation. Each device runs its own supervisor and workers, sharing tasks through a federated task queue.

| Feature | Details |
|---------|---------|
| **Device Identity** | `fleet.toml [naming] device_name` identifies each machine |
| **Task Sharing** | Federation broker routes tasks to the best-equipped device |
| **Cross-Platform** | Windows, Linux, macOS nodes in the same fleet |
| **FleetBridge** | `fleet_bridge.py` ABC with WslBridge (Win->WSL) and DirectBridge (Linux/macOS) |
| **Air-Gap Safe** | Each node enforces its own offline/air-gap policy independently |
| **GPU Routing** | Tasks route to nodes with available VRAM via Dr. Ders hw_state reports |

Configuration in `fleet.toml`:
```toml
[naming]
device_name = "workstation-01"    # unique device ID

[federation]
enabled = false                   # enable multi-device mode
broker_url = ""                   # federation broker endpoint
```

## Training Pipeline

Autonomous ML training loop inspired by [Karpathy's build-nanogpt](https://github.com/karpathy/build-nanogpt). Agent-edited `train.py` runs 5-minute experiments, measures val_bpb, keeps or reverts changes.

## Repository Structure

| Repo | Purpose |
|------|---------|
| **[BigEd](https://github.com/SwiftWing21/BigEd)** | Core platform — launcher, fleet, dashboard, skills, ML pipeline |
| **[BigEd-ModuleHub](https://github.com/SwiftWing21/BigEd-ModuleHub)** | Non-core modules — optional UI extensions (CRM, Ingestion, etc.) loaded at runtime |

## Support

If BigEd CC is useful to you as a learning reference, consider supporting development:

[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20BigEd%20CC-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/swiftwing21)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. This is a personal learning project, but PRs, issues, and discussions are welcome — especially if you spot something the models got wrong.

## License

Apache 2.0 — see [LICENSE](LICENSE).

Copyright 2025-2026 Michael Bachaud ([SwiftWing21](https://github.com/SwiftWing21)).
