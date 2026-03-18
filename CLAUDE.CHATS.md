# Chat Session Notes

Log of session context, decisions, and cross-session continuity for the Education project.

## CLI Environment Map

### Git Bash (primary shell for Claude Code)
| Tool | Version | Path |
|------|---------|------|
| Node.js | v24.14.0 | `C:\Program Files\nodejs\node.exe` |
| npm | 11.9.0 | `C:\Program Files\nodejs\npm.cmd` |
| npx | (bundled) | `C:\Program Files\nodejs\npx.cmd` |
| Python | 3.14.3 | `C:\Users\max\AppData\Local\Python\bin\python.exe` |
| pip | 26.0.1 | (python 3.14) |
| Git | 2.53.0 | `C:\Program Files\Git\cmd\git.exe` |
| Claude Code | 2.1.78 | `C:\Users\max\AppData\Roaming\npm\claude.cmd` |
| VS Code | (installed) | `C:\Program Files\Microsoft VS Code\bin\code` |
| Docker | 28.3.2 | `C:\Program Files\Docker\Docker\resources\bin\docker.exe` |

**Not on Git Bash PATH:** uv, ollama, cargo/rust, gh

### WSL2 Ubuntu (Running)
| Tool | Version | Path |
|------|---------|------|
| Python | 3.12.3 | `/usr/bin/python3` |
| pip | 24.0 | `/usr/bin/pip3` |
| Git | 2.43.0 | `/usr/bin/git` |
| uv | 0.10.9 | `~/.local/bin/uv` (login shell required) |
| Ollama | 0.17.7 | `/usr/local/bin/ollama` |

**Not installed in WSL:** node, cargo/rust, gh

### Docker
- Installed on Windows (v28.3.2), WSL integration available but **not activated**
- `docker-desktop` WSL distro exists but is Stopped
- **Status:** Not yet utilized. Available for agents if containers are needed — activate WSL integration in Docker Desktop settings first.

### CMD / PowerShell
Both share the Windows PATH. Verified tool access:

| Tool | CMD | PowerShell | Notes |
|------|-----|------------|-------|
| Python 3.14.3 | yes | yes | via `WindowsApps` stub + `AppData\Local\Python\bin` |
| pip 26.0.1 | yes | yes | |
| Node.js v24.14.0 | yes | yes | |
| npm 11.9.0 | yes | yes (`.ps1` wrapper) | |
| npx | yes | yes (`.ps1` wrapper) | |
| Git 2.53.0 | yes | yes | |
| Docker 28.3.2 | yes | yes | |
| Claude Code 2.1.78 | yes | yes (`.ps1` wrapper) | |
| VS Code | yes | yes | |
| uv | **no** | **no** | WSL2 only |
| ollama | **no** | **no** | WSL2 only |
| gh | **no** | **no** | not installed anywhere |
| cargo/rustc | **no** | **no** | not installed anywhere |

## Session Log

### 2026-03-17 — Environment audit + project files setup
- Created `CLAUDE.CHATS.md` (this file) and `CLAUDE.C_REVIEW.md`
- Audited all CLI environments and documented tool availability
- uv and ollama only accessible from WSL2 Ubuntu (not on Windows PATH)

### Recent Updates — UI Enhancements & Workspace Segregation
- **Workspace:** Set up a parallel VS Code workspace (`working education`) to keep IDE configurations completely separated from the Git repository, utilizing cross-directory relative paths in `tasks.json` and `launch.json`.
- **BigEd CC Enhancements (`launcher.py`):**
  - Implemented confirmation dialogs for destructive GUI actions.
  - Built the `_customers_ping_all` network sweep feature for non-air-gapped fleet deployments.
  - Added Export/Import Settings (portable JSON) to Backup & Restore, keeping `.secrets` explicitly excluded to maintain a safe-by-default security posture and avoid Git push errors.

### Recent Updates — Roadmap Execution
- **v0.16 Modular Tabs (BigEd CC):** Implemented config-driven UI tabs in `launcher.py`. Disabled CRM, Onboarding, Customers, and Accounts tabs by default to streamline the UI. Added a "Visible Tabs" settings pane to easily toggle these features, which read/writes to `[launcher.tabs]` in `fleet.toml`.
- **v0.21 VS Code Developer Workflow:**
  - Added launch configurations for `smoke_test.py`, `worker.py`, and `lead_client.py`.
  - Created `lint`, `format`, and `build` tasks in `tasks.json`.
  - Updated `smoke_test.py` with `--fast` mode. Skips Ollama/RAG/Thermal and explicitly sets `FLEET_TEST_DB=:memory:` for true DB test isolation, accelerating the local dev loop.
