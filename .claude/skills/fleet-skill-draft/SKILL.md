---
name: fleet-skill-draft
description: Draft a new fleet skill module following the fleet contract, perspective system, and conventions. Use when the user wants to create a new fleet skill.
disable-model-invocation: true
allowed-tools: Read, Write, Glob, Grep, Bash
---

# Fleet Skill Draft Generator

Create a new fleet skill for: $ARGUMENTS

## Fleet Skill Contract

Every fleet skill MUST follow this exact structure:

```python
"""
<Skill name> — <one-line description>.

Payload:
  <key>    <type>   <description>
  ...

Output: knowledge/<subdir>/<filename pattern>
"""
from pathlib import Path

SKILL_NAME = "<skill_name>"
DESCRIPTION = "<same as module docstring first line>"

FLEET_DIR = Path(__file__).parent.parent

def run(payload: dict, config: dict) -> dict:
    """
    Payload keys:
      <key>  <type>  — <description>
    Returns dict with: result, saved_to, error (if any)
    """
    # Implementation
    return {"status": "ok", "result": "..."}
```

## Key Rules

1. **Single entry point**: `run(payload, config)` — always returns a dict
2. **payload** comes from the task queue (user-supplied, treat as untrusted)
3. **config** contains: `config["models"]["ollama_host"]`, `config["models"]["local"]`
4. **Save output** to `knowledge/<subdir>/` and return `saved_to` path
5. **All imports** at module level or inside `run()` — no top-level side effects
6. **Path traversal prevention**: always validate paths against `FLEET_DIR`
7. **Declare `REQUIRES_NETWORK = True`** if the skill needs internet access
8. **Optionally declare `COMPLEXITY`**: `"simple"` | `"medium"` | `"complex"` — influences model routing; omit for simple I/O skills, set `"complex"` for LLM-heavy or multi-step reasoning skills
9. For LLM calls, use `from skills._models import call_complex`

## MCP-Aware Pattern

Skills can check for MCP servers and use them as a preferred data source.
There is no `call_mcp()` helper — make direct HTTP calls to the URL returned by `get_mcp_url()`:

```python
import urllib.request, json
from mcp_manager import is_mcp_available, get_mcp_url

def run(payload: dict, config: dict) -> dict:
    available, mcp_url = is_mcp_available("server_name")
    if available and mcp_url:
        # Direct HTTP call to the MCP server endpoint
        req = urllib.request.Request(
            f"{mcp_url}/endpoint",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
    else:
        # Fall back to local library or HTTP
        result = local_fallback(payload)
    return {"status": "ok", "result": result}
```

### Fallback Chain

Follow the 3-tier fallback pattern (see `browser_crawl.py` as reference):
1. **MCP server** — check via `is_mcp_available()`, call via `get_mcp_url()`
2. **Local library** — use an installed Python package if available
3. **HTTP fallback** — direct HTTP request as last resort

### System Info Integration

Skills can use hardware-aware behavior:

```python
from system_info import get_memory, get_worker_limits

def run(payload: dict, config: dict) -> dict:
    limits = get_worker_limits()
    # Adjust batch size, concurrency, etc. based on system capabilities
    ...
```

## Reference Example

`fleet/skills/model_manager.py` is a well-structured skill with multiple actions
(list, pull, delete, inspect). Use it as a reference for:
- Multi-action dispatch within a single `run()` function
- Proper payload validation for each action
- Clean error handling and return format

## Perspective System

Apply ONE of these perspectives based on the skill's purpose:

| Perspective | Focus |
|-------------|-------|
| **Software Architect** | Clean interface, extensible structure, well-defined payload schema, separation of concerns |
| **Code Critic** | Defensive coding, input validation, explicit error paths, edge case handling |
| **Performance Optimizer** | Async-friendly, minimal I/O, streaming, timeouts on network calls, generators over full lists |

## Workflow

1. Read existing similar skills in `fleet/skills/` for patterns — use Glob `fleet/skills/*.py` to list them
2. Check `fleet/fleet.toml` for config patterns the skill may need
3. Write the skill to `fleet/knowledge/code_drafts/<skill_name>_draft_<YYYYMMDD>.py` (NEVER directly to `skills/`)
4. Validate: ensure `SKILL_NAME`, `DESCRIPTION`, and `run()` are present
5. Show the user the draft and explain what it does

## Output Location

**IMPORTANT**: Drafts go to `fleet/knowledge/code_drafts/<skill_name>_draft_<YYYYMMDD>.py` — NEVER auto-deploy to `fleet/skills/`.
The user or fleet will promote via `skill_promote` -> `deploy_skill` after review.
