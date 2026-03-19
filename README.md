# BigEd CC

**Autonomous AI agent fleet orchestration for local-first compute.**

<!-- Badges -->
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
<!-- ![CI](https://img.shields.io/github/actions/workflow/status/SwiftWing21/BigEd-CC/ci.yml?branch=main) -->
<!-- ![Version](https://img.shields.io/badge/alpha-0.25-orange) -->

---

## What is BigEd CC?

BigEd CC (BigEd Command Center) is an autonomous AI agent fleet platform that
orchestrates 74 specialized skills across local and cloud model backends. It
uses a dual-supervisor architecture with swarm intelligence to run unattended
workloads -- research, code review, security audits, ML training, and more --
on consumer-grade hardware. The system supports Ollama, llama.cpp, and
llamafile for local inference, with automatic failover to Claude and Gemini
APIs when needed.

## Key Features

- **74 Skills** -- code review, security audit, research, flashcards, dataset synthesis, marathon ML sessions, and dozens more
- **Fleet Orchestration** -- dual-supervisor system (`supervisor.py` + Dr. Ders) manages process lifecycle, model health, VRAM scaling, and worker respawn
- **Swarm Intelligence** -- three tiers of autonomous behavior: evolution, research, and specialization
- **Human-in-the-Loop Review** -- skill drafts are never auto-deployed; all outputs go through review before promotion
- **Multi-Backend Models** -- local inference via Ollama/llama.cpp/llamafile, with HA fallback to Claude and Gemini APIs (circuit breaker, automatic failover)
- **Security** -- OWASP B+ rating, 26 security controls, SQLCipher-encrypted database, TLS, RBAC, GDPR B compliance
- **Cross-Platform** -- Windows, Linux, and macOS support
- **Offline / Air-Gap Mode** -- full local operation without internet; air-gap mode disables dashboard, secrets, and external APIs
- **Marathon ML Sessions** -- long-running training pipelines with progress tracking and resource-aware scheduling

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/SwiftWing21/BigEd-CC.git
cd BigEd-CC

# 2. Install dependencies
pip install -r BigEd/launcher/requirements.txt
# or, if using uv:
uv sync

# 3. Install and start Ollama, then pull a model
ollama pull qwen3:8b

# 4. Launch BigEd CC
python BigEd/launcher/launcher.py
```

The launcher GUI will start the fleet supervisor, boot workers, and open the
dashboard automatically.

## Architecture

```
BigEd-CC/
  fleet/          Workers, skills, supervisor, dashboard, fleet.db
  BigEd/          Launcher GUI, compliance docs, build tooling
  autoresearch/   ML training pipeline and dataset tools
```

- **Fleet** -- 74 registered skills dispatched to workers by a dual-supervisor system. Workers communicate via SQLite task queue and SSE bridge.
- **Launcher** -- Tkinter GUI that manages fleet boot, configuration, and dev-mode controls.
- **Autoresearch** -- Dataset synthesis, model fine-tuning, and evaluation pipelines.

For deeper architecture details, see [FRAMEWORK_BLUEPRINT.md](FRAMEWORK_BLUEPRINT.md)
and [OPERATIONS.md](OPERATIONS.md).

## Hardware Requirements

| Tier | RAM | GPU | Notes |
|------|-----|-----|-------|
| **Minimum** | 8 GB | Any (or CPU-only) | Use smaller models (`qwen3:4b`, `qwen3:0.6b`); reduced worker count |
| **Recommended** | 32 GB | RTX 3080 Ti (12 GB VRAM) | Full fleet with `qwen3:8b`; 10+ concurrent workers |

BigEd CC runs on CPU-only systems with smaller models. GPU acceleration is
recommended for production workloads and marathon ML sessions.

## Configuration

The primary configuration file is [`fleet/fleet.toml`](fleet/fleet.toml). Key
areas include:

- **Models** -- model tiers, VRAM limits, complexity routing
- **Security** -- TLS, RBAC, SQLCipher, OWASP controls
- **Budgets** -- per-worker token budgets and cost attribution
- **Offline mode** -- `offline_mode` and `air_gap_mode` flags
- **Workers** -- coder count, max workers, idle behavior

## Documentation

- [FRAMEWORK_BLUEPRINT.md](FRAMEWORK_BLUEPRINT.md) -- System architecture and design decisions
- [OPERATIONS.md](OPERATIONS.md) -- Operational procedures and runbooks
- [CROSS_PLATFORM.md](CROSS_PLATFORM.md) -- Platform-specific setup and compatibility
- [BigEd/SECURITY_COMPLIANCE_BLUEPRINT.md](BigEd/SECURITY_COMPLIANCE_BLUEPRINT.md) -- Security controls, OWASP mapping, compliance
- [ROADMAP.md](ROADMAP.md) -- Version scheme and milestone tracking

## Contributing

Contributions are welcome. Please see [CONTRIBUTING.md](CONTRIBUTING.md) for
guidelines on submitting issues and pull requests.

## License

Licensed under the [Apache License, Version 2.0](LICENSE).

Copyright 2025-2026 Michael Bachaud ([SwiftWing21](https://github.com/SwiftWing21)).

## Also Known As

BigEd, BigEdgucation, Big Edge Compute Control
