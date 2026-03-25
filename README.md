# BigEd CC — Big Edge Compute Command

BigEd CC is a centralized AI agent orchestration platform for managing local and cloud LLMs from a single interface. It coordinates a fleet of 130+ specialized AI agents across code review, security auditing, research, ML training, and knowledge management — with enterprise-grade security, multi-tenant support, and hardware-aware scaling.

Built through AI-assisted development, BigEd CC is both a functional platform and a case study in directing complex software architecture through iterative AI collaboration. The human role was architectural direction, quality control, debugging, and system design — the kind of work that doesn't show up in lines-of-code metrics but determines whether a system works.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

## What It Does

- **Model Management** — Switch between local (Ollama), Claude, and Gemini from one interface. See which model is active, what it costs, and how fast it responds.
- **130+ AI Skills** — Code review, security audit, web research, ML training, knowledge indexing, and more — all dispatched to agents automatically.
- **Dynamic Agent Scaling** — 4 core agents + demand-based scaling up to 16, based on your hardware.
- **Dr. Ders** — A dual-supervisor hardware governor that monitors thermals, VRAM, and model health with event-driven wake-up timing.
- **Fleet Dashboard** — Real-time web UI at localhost:5555 showing agent status, task queues, and performance metrics via SSE.
- **One-Click Setup** — Installer handles Python, Ollama, models, and dependencies. There's also a first-run walkthrough in the GUI.
- **Auto-Save Backup** — Periodic snapshots so you don't lose fleet state.
- **Cost Tracking** — Per-call token usage and estimated costs for API providers.

## Development Approach

BigEd CC was built through thousands of iterative AI-assisted development sessions. The architecture, quality standards, debugging direction, and system design decisions were human-directed throughout.

| Role | Contributor | What This Means |
|------|------------|----------------|
| **Architecture & System Design** | Max (human) | All architectural decisions, system scope, feature prioritization, quality standards, and debugging direction |
| **Code Generation** | Claude Code (Opus 4.6) | Primary code author under human direction — wrote implementations from architectural specs and prompt-driven requirements |
| **Review & Iteration** | Claude (Sonnet 4.6) | Code review, audits, skill generation, iterative improvements |
| **Independent Audit** | Gemini Pro (2.5/3.1) | Architecture audits, second opinions, cross-validation |
| **Quality Assurance** | Max (human) | Testing, debugging, catch-and-correct for AI-introduced bugs, documentation maintenance |

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
python fleet/smoke_test.py --fast         # verify smoke tests
python BigEd/launcher/launcher.py         # launch GUI
```

## Architecture

```
BigEd CC
├── BigEd/launcher/     — GUI launcher (customtkinter)
│   ├── ui/             — Settings, consoles, dialogs, boot sequence
│   ├── modules/        — Pluggable modules (Intelligence, Ingestion, Outputs)
│   └── fonts/          — Custom pixel fonts
├── fleet/              — 130+ skill AI worker fleet
│   ├── supervisor.py   — Process lifecycle + dynamic scaling
│   ├── hw_supervisor.py — Dr. Ders (thermal + model management)
│   ├── dashboard.py    — Web dashboard (localhost:5555)
│   ├── worker.py       — Generic task executor
│   ├── skills/         — 130+ registered skills
│   └── knowledge/      — Agent-generated artifacts
├── autoresearch/       — ML training pipeline (inspired by Karpathy)
├── scripts/            — Setup scripts (Windows/Linux/macOS)
└── docs/               — Specs, flowcharts, design docs
```

## Model Support

| Provider | Models | Auth | Cost |
|----------|--------|------|------|
| **Ollama (Local)** | qwen3:8b, 4b, 1.7b, 0.6b | None | Free |
| **Claude** | Haiku, Sonnet, Opus | API key or OAuth | Per-token |
| **Gemini** | Flash, Pro | API key or OAuth | Per-token |

OAuth models (Claude Code, Gemini CLI) work through VS Code with pre-configured project files — BigEd writes a task briefing, opens VS Code, and you're ready to go.

## Enterprise & Security Features

- **File access control** — SOC 2 compliant per-zone permissions (read / read-write / full)
- **DLP** — Secret detection and output scrubbing before API calls
- **Audit logging** — Tamper-evident HMAC-signed logs for API calls, file access, and config changes
- **RBAC** — Role-based access control with 5 roles and 7 permission levels
- **Air-gap mode** — Full offline operation with local models only
- **Fleet federation** — Multi-device task routing with peer overflow
- **Multi-tenant architecture** — 4-tier tenancy scaling from single-user to enterprise
- **Training pipeline** — Autonomous ML training loop inspired by [Karpathy's build-nanogpt](https://github.com/karpathy/build-nanogpt)

## Repository Structure

| Repo | Purpose |
|------|---------|
| **[BigEd](https://github.com/SwiftWing21/BigEd)** | Core platform — launcher, fleet, dashboard, skills, ML pipeline |
| **[BigEd-ModuleHub](https://github.com/SwiftWing21/BigEd-ModuleHub)** | Optional modules — UI extensions loaded at runtime |

## Support

If BigEd CC is interesting or useful to you as a reference, consider supporting development:

[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20BigEd%20CC-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/swiftwing21)

## MCP Server Config (VS Code)

BigEd ships with `.vscode/launch.json` and `.vscode/tasks.json` for shared dev
configs (debug launchers, smoke test tasks). These are tracked in git.

MCP server configuration is **not tracked** — it lives in `.vscode/mcp.json`
and is gitignored because it may contain credentials or machine-specific
server URLs. On first clone you won't have one, so either:

**Option A — Generate a starter config:**
```bash
python fleet/mcp_manager.py --init-vscode
```

**Option B — Create it manually:**
```json
{
  "servers": {},
  "inputs": []
}
```
Save as `.vscode/mcp.json` and add your MCP servers as needed.

The root `.mcp.json` (used by Claude Code CLI) is also gitignored and managed
at runtime by `fleet/mcp_manager.py`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. PRs, issues, and honest feedback are welcome.

## License

Apache 2.0 — see [LICENSE](LICENSE).

Copyright 2025-2026 Michael Bachaud ([SwiftWing21](https://github.com/SwiftWing21)).
