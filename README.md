# BigEd CC

> One-click local AI fleet deployment. No terminal required.

BigEd CC eliminates manual CLI setup for local AI. Deploy Ollama models and a 74-skill agent fleet with one click. Use OAuth Manual Mode (Claude Code / Gemini) with pre-loaded context from agent requests, or let the fleet work autonomously via API.

**All platforms. Enterprise-ready. SOC 2 aligned.**

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

## Features

- **One-Click Setup** — Auto-installs Python, Ollama, models, and dependencies
- **74+ AI Skills** — Code review, security audit, web research, ML training, and more
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
├── fleet/              — 74-skill AI worker fleet
│   ├── supervisor.py   — Process lifecycle + dynamic scaling
│   ├── hw_supervisor.py — Dr. Ders (thermal + model management)
│   ├── dashboard.py    — Web dashboard (localhost:5555)
│   ├── worker.py       — Generic task executor
│   ├── skills/         — 74 registered skills
│   └── knowledge/      — Agent-generated artifacts
├── autoresearch/       — ML training pipeline (inspired by Karpathy)
├── scripts/            — Setup scripts (Windows/Linux/macOS)
└── docs/specs/         — Enterprise integration specs
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

## Training Pipeline

Autonomous ML training loop inspired by [Karpathy's build-nanogpt](https://github.com/karpathy/build-nanogpt). Agent-edited `train.py` runs 5-minute experiments, measures val_bpb, keeps or reverts changes.

## Support

If BigEd CC is useful to you, consider supporting development:

[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20BigEd%20CC-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/swiftwing21)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

Apache 2.0 — see [LICENSE](LICENSE).

Copyright 2025-2026 Michael Bachaud ([SwiftWing21](https://github.com/SwiftWing21)).
