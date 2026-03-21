---
name: fleet-security-audit
description: Security audit for fleet code and environment using the fleet's secret patterns, permission checks, and gitignore analysis. Use when the user asks to audit fleet security.
disable-model-invocation: true
allowed-tools: Read, Glob, Grep, Bash
---

# Fleet Security Audit

Audit security for: $ARGUMENTS

## Checks to Run

### 1. Secret Exposure Scan

Search for these patterns in fleet files (mask any matches — never output raw secrets):

| Pattern | Label |
|---------|-------|
| `sk-ant-[A-Za-z0-9_-]{20,}` | Anthropic API key |
| `hf_[A-Za-z0-9]{20,}` | HuggingFace token |
| `(api[_-]?key\|secret\|password\|token)\s*=\s*[A-Za-z0-9_-]{16,}` | Potential credential |
| `BRAVE_API_KEY=[A-Za-z0-9_-]{16,}` | Brave API key |
| `TAVILY_API_KEY=[A-Za-z0-9_-]{16,}` | Tavily API key |

Skip: `.db`, `.pyc`, `.png`, `.jpg`, `.bin`, `.jsonl` files.

### 2. Gitignore Gap Check

Verify `.gitignore` covers:
- Secrets/credentials: `.secrets`, `*.secrets`, `.env`, `.env.*`
- Databases: `fleet/fleet.db` (and WAL files), `fleet/rag.db` (and WAL files)
- Knowledge artifacts: `fleet/knowledge/*.jsonl` or `*.jsonl` wildcards
- TLS certs: `fleet/certs/` directory (project uses directory-level exclusion, not `*.pem`/`*.key` wildcards)
- User config: `CLAUDE.USER.md` (machine-specific info, must not be committed)

### 3. File Permission Check (if on Linux/WSL)

Check these paths have restricted permissions:
- `~/.secrets` — should be 0600
- `~/.ssh/id_rsa` — should be 0600
- `~/.ssh/id_ed25519` — should be 0600

### 4. Code-Level Security

- Path traversal: are file paths validated against `FLEET_DIR`?
- Input validation: are payload fields from the task queue treated as untrusted?
- Command injection: any unsafe subprocess or shell-exec calls with user input?
- Hardcoded secrets: any API keys or tokens in source code?

### 5. Security Module Check (`fleet/security.py`)

`fleet/security.py` is the authoritative security module for the fleet. Verify:
- TLS configuration is current (no deprecated ciphers or protocols)
- RBAC roles and permissions are properly scoped
- Rate-limit settings are reasonable for the deployment context
- CSRF protections are active on all state-changing endpoints

### 6. MCP Exposure Check

- Verify `.mcp.json` does not contain API keys or tokens (they should be in environment variables)
- Check that MCP server URLs in `.mcp.json` do not expose internal network addresses
- Ensure `mcp_manager.py` does not log sensitive MCP configuration

### 7. CLAUDE.USER.md Check

- `CLAUDE.USER.md` must be listed in `.gitignore` (contains machine-specific info, potentially sensitive paths)
- If `CLAUDE.USER.md` exists and is tracked by git, flag as `[HIGH]`

### 8. Template XSS Check

- `fleet/templates/dashboard.html` should not have unescaped user input
- Look for raw variable interpolation (`${...}`, `{{ ... | safe }}`, or f-string injection)
- All dynamic content should be escaped or sanitized before rendering

### 9. `_security.py` Usage Check

`fleet/skills/_security.py` exports shared security helpers. Verify:
- Skills that accept file paths from payloads call `_security.safe_path()` rather than using raw path strings
- Skills that return user-facing filenames call `_security.sanitize_filename()`
- No skill duplicates path-validation logic that belongs in `_security.py`

### 10. CREATE_NO_WINDOW Check (Windows)

- All `subprocess.Popen` calls on Windows must include `creationflags=CREATE_NO_WINDOW`
- This prevents console windows from flashing during background operations
- Check for Popen, run, and call invocations in the subprocess module without the flag
- Only applies to calls that run in the background (not interactive shells)

## Output Format

### Advisory

```
# Security Advisory
**Scope:** <what was audited>
**Findings:** X HIGH, Y MEDIUM, Z LOW

## Findings
- **[SEVERITY]** `<path>` — <description>
  - Fix: `<remediation command or code change>`
```

Save the advisory to `fleet/knowledge/security/pending/advisory_<YYYYMMDD>_<NNN>.md` (e.g. `advisory_20260320_001.md`).
The user must approve before any fixes are applied via `security_apply`.
