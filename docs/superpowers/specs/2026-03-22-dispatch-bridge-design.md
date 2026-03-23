# Dispatch Bridge — MCP Server for Cowork Dispatch Integration

**Date:** 2026-03-22
**Status:** Approved (design)
**Version target:** v0.190.00b

## Problem

BigEd's fleet has 92 skills, a REST API, HITL gates, and SSE streaming — but no way to reach it from a phone. Claude Cowork Dispatch lets users send tasks from the Claude mobile app to Claude Desktop, which has shell and MCP access on the local machine. By exposing BigEd's fleet as an MCP server, Cowork Dispatch natively discovers and invokes fleet capabilities without CLI wrappers or manual instructions.

## Use Case Priority

1. **Fire-and-forget** (A) — kick off a fleet task from phone, check result later
2. **Monitor and intervene** (C) — watch fleet status, override when needed
3. **Interactive HITL** (B) — approve/reject gates from phone, occasional back-and-forth

## Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ Claude Mobile │────▶│ Cowork Dispatch   │────▶│ Claude Desktop  │
│ App (phone)  │◀────│ (persistent chat) │◀────│ (local machine) │
└──────────────┘     └──────────────────┘     └────────┬────────┘
                                                       │ stdio
                                                       ▼
                                              ┌────────────────┐
                                              │ mcp_server.py  │
                                              │ (FastMCP)      │
                                              └───────┬────────┘
                                                      │ HTTP / direct DB
                                                      ▼
                                              ┌────────────────┐
                                              │ BigEd Fleet    │
                                              │ localhost:5555 │
                                              │ 92 skills      │
                                              └────────────────┘
```

**Key constraint:** No OAuth token routing. Dispatch uses Anthropic's own auth. BigEd uses API keys for its providers. No ToS violations.

## Design

### Section 1: MCP Server — Tool Surface

**File:** `fleet/mcp_server.py`
**Framework:** [FastMCP](https://github.com/jlowin/fastmcp) (pip install, pure Python)
**Transport:** stdio (Claude Desktop default)

#### Tools (6)

| MCP Tool | Internal Target | Description |
|---|---|---|
| `fleet_task` | `parse_intent_with_maintainer()` → `db.post_task()` | Submit natural language task |
| `fleet_dispatch` | `POST /api/trigger` | Explicit skill + payload dispatch |
| `fleet_status` | Dashboard status endpoints | Fleet health, agent counts, queue depth |
| `fleet_catalog` | `GET /api/skill_catalog` | List available skills with descriptions |
| `fleet_task_result` | `GET /api/tasks/<id>` | Check result of a submitted task |
| `fleet_hitl_respond` | `POST /api/tasks/<id>/respond` | Approve/reject HITL gate |

#### Resource (1)

| MCP Resource | Internal Target | Description |
|---|---|---|
| `hitl://pending` | `GET /api/tasks/waiting-human` | Tasks awaiting human approval |

#### Internal wiring

- Lazy imports (`import db`, `import config` inside functions)
- Task submission via direct `db.post_task()` (avoids HTTP round-trip)
- All other calls via `urllib.request` to `localhost:5555` (with `timeout=10`)
- Reads `fleet.toml` via `config.load_config()` for port/base URL
- `creationflags=CREATE_NO_WINDOW` if server needs to spawn subprocesses

### Section 2: HITL Notification Flow

```
Fleet skill hits HITL gate
  → task.status = WAITING_HUMAN
  → mcp_server.py resource hitl://pending returns it
  → Cowork Dispatch polls MCP resources (native behavior)
  → Dispatch relays to Claude mobile app
  → User approves/rejects from phone
  → Dispatch calls fleet_hitl_respond tool
  → Task resumes
```

#### Timeout configuration

```toml
[dispatch_bridge]
hitl_auto_approve_timeout_min = 0   # 0 = off (wait forever)
hitl_auto_approve_max_cost = 0      # 0 = no cost gate
```

- Default: both `0` — wait indefinitely for explicit human approval
- Opt-in: set `hitl_auto_approve_timeout_min > 0` to auto-approve after N minutes
- Cost gate: if `hitl_auto_approve_max_cost > 0`, only auto-approve tasks below that token estimate

No new HITL plumbing — existing dashboard endpoints handle all mechanics.

### Section 3: Registration & Discovery

#### Claude Desktop config registration

On boot, BigEd launcher checks `%APPDATA%/Claude/claude_desktop_config.json` for a `biged-fleet` MCP server entry.

If missing and `[dispatch_bridge] enabled = true`:
- Launcher prompts: "BigEd can connect to Claude Desktop for mobile Dispatch. Register now?"
- On confirm: writes entry to Claude Desktop config:

```json
{
  "mcpServers": {
    "biged-fleet": {
      "command": "python",
      "args": ["C:/Users/max/Projects/Education/fleet/mcp_server.py"],
      "env": {}
    }
  }
}
```

- Sets `registered_claude_desktop = true` in `fleet.toml`
- Does not re-prompt on subsequent launches

#### Discovery chain

Claude Desktop → sees `biged-fleet` stdio MCP server → Cowork inherits tools → Dispatch inherits from Cowork → phone sees `fleet_task`, `fleet_status`, etc.

#### Tunnel-ready (future)

Swap transport without changing tools:
- `mcp.run(transport="http", host="0.0.0.0", port=5556)`
- Claude Desktop config switches from `command` to `url` format
- Put behind ngrok/Cloudflare Tunnel for remote access

### Section 4: CLI Fallback (dispatch_bridge.py)

**File:** `fleet/dispatch_bridge.py` (~120 lines)
**Purpose:** Backup when MCP isn't available, or for direct terminal/SSH use.

```
python fleet/dispatch_bridge.py submit "review worker code"
python fleet/dispatch_bridge.py status [task_id]
python fleet/dispatch_bridge.py catalog
python fleet/dispatch_bridge.py pending-hitl
python fleet/dispatch_bridge.py respond <task_id> "approved"
python fleet/dispatch_bridge.py watch          # tails SSE stream
```

- All commands hit `localhost:5555` dashboard API
- `submit` uses intent parsing via `parse_intent_with_maintainer()`
- `watch` connects to `/api/stream` SSE, prints HITL alerts + task completions
- Output: JSON by default, `--pretty` for human-readable
- Base URL configurable via `--url` flag (tunnel-ready)

**Not a replacement for `lead_client.py`** — lead_client is the full fleet CLI. dispatch_bridge is the Dispatch-shaped subset: submit, check, respond.

### Section 5: Config, Dependencies & Installer

#### fleet.toml

```toml
[dispatch_bridge]
enabled = true
registered_claude_desktop = false
hitl_auto_approve_timeout_min = 0
hitl_auto_approve_max_cost = 0
dashboard_base_url = "http://127.0.0.1:5555"
```

#### Dependencies

- `requirements.txt`: add `fastmcp`
- No Node.js, Docker, or external runtime needed

#### dependency_check.py

New check: `check_fastmcp()` in `mcp` category (required: False).

```python
def check_fastmcp() -> dict:
    """FastMCP — MCP server framework for Dispatch bridge."""
    try:
        import fastmcp
        version = getattr(fastmcp, "__version__", "unknown")
        return {"name": "fastmcp", "category": "mcp", "required": False,
                "found": True, "ok": True, "version": version,
                "detail": f"FastMCP {version}"}
    except ImportError:
        return {"name": "fastmcp", "category": "mcp", "required": False,
                "found": False, "ok": False, "version": None,
                "detail": "pip install fastmcp"}
```

#### Installer scripts

No changes. Both `setup.ps1` and `setup.sh` already run `pip install -r requirements.txt`.

#### Launcher integration

- Boot sequence: after fleet + dashboard are up, check Claude Desktop registration
- Settings panel: new toggle in `BigEd/launcher/ui/settings/mcp.py` for Dispatch bridge enable/disable

## Files Changed

| File | Change Type | Description |
|---|---|---|
| `fleet/mcp_server.py` | **New** | FastMCP server — 6 tools, 1 resource (~200 lines) |
| `fleet/dispatch_bridge.py` | **New** | CLI fallback (~120 lines) |
| `fleet/fleet.toml` | Edit | Add `[dispatch_bridge]` section |
| `fleet/dependency_check.py` | Edit | Add `check_fastmcp()` |
| `requirements.txt` | Edit | Add `fastmcp` |
| `fleet/mcp_manager.py` | Edit | Add Claude Desktop config writer helper |
| `BigEd/launcher/launcher.py` | Edit | Registration prompt on boot |
| `BigEd/launcher/ui/settings/mcp.py` | Edit | Dispatch bridge toggle |

**No changes to:** dashboard.py, db.py, providers.py, supervisor.py, lead_client.py, worker.py, any existing skills.

## Testing Strategy

1. **Unit:** mcp_server.py tools return correct shapes (mock db/dashboard)
2. **Integration:** submit task via MCP tool → verify task appears in fleet.db
3. **HITL round-trip:** submit task → trigger HITL gate → read hitl://pending resource → respond via tool → verify task resumes
4. **CLI fallback:** all dispatch_bridge.py subcommands against running dashboard
5. **Registration:** launcher writes Claude Desktop config correctly, idempotent on re-run
6. **Smoke:** add to `smoke_test.py` — check FastMCP importable, mcp_server.py loadable
