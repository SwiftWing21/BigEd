# User & Environment — Max's Dev Machine

## Hardware
- **GPU:** RTX 3080 Ti, 12GB VRAM (10GB soft cap, 6.9GB typical with qwen3:8b)
- **RAM:** 32GB — max_workers: 10, RAM-based scaling to 13
- **Platform:** Windows 11 Pro (win32), shell: bash (Git Bash / WSL)
- OOM recovery: hw_supervisor detects, evicts model, restarts worker

## Environment
- Python: native `python` on Windows, `uv run` in WSL
- Ollama: native Windows (`http://localhost:11434`)
- Keys: HF_TOKEN, ANTHROPIC_API_KEY, VRAM_LIMIT_GB=10
- Paths: `C:\Users\max\...`, forward slashes in bash

## MCP Servers

### Active
| Server | Transport | URL/Command | Purpose |
|--------|-----------|-------------|---------|
| playwright | HTTP | `http://localhost:8931` | Browser automation (browser_crawl skill) |

### Config Locations
- **Project:** `.mcp.json` (project-level MCP servers)
- **Claude Code settings:** `.claude/settings.local.json` (`enabledMcpjsonServers`)
- **Docker:** `docker-compose.yml` → `playwright-mcp` service (port 8931)

### Available MCP Ecosystem (not yet connected)
| Server | Transport | Use Case | Fleet Skill |
|--------|-----------|----------|-------------|
| filesystem | stdio/npx | File ops outside project | ingest, code_write |
| github | stdio/npx | Issues, PRs, code search | github_sync |
| postgres/sqlite | stdio/npx | Direct DB queries | analyze_results |
| slack | stdio/npx | Team notifications | — (new) |
| memory | stdio/npx | Persistent knowledge | rag_index |
| sequential-thinking | stdio/npx | Multi-step reasoning | plan_workload |
| fetch | stdio/npx | HTTP requests | web_search, web_crawl |
| brave-search | stdio/npx | Web search API | web_search |

## Claude API Cost
- `cache_control: { type: "ephemeral" }` on stable/reused content
- Message Batches API for non-real-time bulk (50% savings)
- Cache breakpoints at stable prefix boundaries (system prompt, tool defs)

## Model Routing (this machine)
- **Local default:** qwen3:8b (~6.9GB VRAM, ~45 tok/s)
- **CPU conductor:** qwen3:4b (~89 tok/s, 0 VRAM)
- **CPU maintainer:** qwen3:0.6b (fast, minimal)
- **API fallback:** Claude → Gemini → Local (circuit breaker)
