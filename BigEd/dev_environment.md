# Dev Environment Reference

## Services & Ports

| Service | Port | Config |
|---------|------|--------|
| Ollama | :11434 | `fleet.toml [models]` |
| Dashboard | :5555 | `fleet.toml [dashboard]` |
| OpenClaw gateway | :18789 | `fleet.toml [openclaw]` |

## Ollama API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/tags` | GET | List available models |
| `/api/ps` | GET | Running models + VRAM usage |
| `/api/generate` | POST | Text generation |
| `/api/chat` | POST | Chat completion |

## Key Runtime Files

| File | Location | Updated by |
|------|----------|------------|
| STATUS.md | `fleet/STATUS.md` | supervisor.py (every loop) |
| hw_state.json | `fleet/hw_state.json` | hw_supervisor.py (every 5s) |
| fleet.db | `fleet/fleet.db` | All agents (SQLite WAL) |
| fleet.toml | `fleet/fleet.toml` | Manual config |
| rag.db | `fleet/rag.db` | rag.py (FTS5 index) |
| Logs | `fleet/logs/<role>.log` | Per-worker + supervisors |

## DB Schema (fleet.db)

| Table | Key Columns | Purpose |
|-------|-------------|---------|
| agents | name, role, status, current_task_id, last_heartbeat, pid | Agent registry & heartbeats |
| tasks | id, type, status, assigned_to, payload_json, result_json, parent_id, depends_on | Task queue & DAG |
| messages | from_agent, to_agent, body_json, read_at | Inter-agent messaging |
| locks | name, holder, acquired_at | Distributed locking |

Task statuses: PENDING, WAITING, RUNNING, DONE, FAILED

## WSL Bridge (Windows)

Fleet runs inside WSL Ubuntu. The launcher wraps all commands via:
```
wsl -d Ubuntu -- bash -c "cd /mnt/c/Users/max/Projects/Education/fleet && ..."
```

Path conversion: `C:\Users\...\fleet` → `/mnt/c/Users/.../fleet`

For Linux/macOS: DirectBridge (no WSL wrapper needed).

## RAG Scan Paths

```python
SCAN_PATHS = [
    (".", "*.md"),
    ("fleet", "*.md"),
    ("fleet/knowledge", "**/*.md"),
    ("BigEd", "*.md"),          # ← this file is indexed here
    ("autoresearch", "*.md"),
]
```
