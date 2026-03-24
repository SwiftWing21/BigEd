# Console Upgrade Plan — Claude Code + VS Code Integration

> **Goal:** Replace custom API consoles with native Claude Code integration for superior code review, task execution, and fleet management. Keep Gemini + Local consoles for non-code tasks.

---

## Current State

| Console | Implementation | Limitations |
|---------|---------------|-------------|
| Claude Console | Custom chat UI wrapping `anthropic` SDK | No tool use, no file access, no MCP, limited context |
| Gemini Console | Custom chat UI wrapping `google-genai` SDK | Same limitations |
| Local Console | Custom chat UI wrapping Ollama HTTP API | Same limitations |

All three are basic text-in/text-out chat windows. They can't browse files, run commands, or use tools.

---

## Upgrade Path

### Tier 1: Claude Code Headless (`claude -p`)

**For automated fleet tasks — no UI needed.**

```python
# fleet/skills/claude_code.py
import subprocess

def run(payload, config):
    prompt = payload.get("prompt", "")
    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True, text=True, timeout=300,
        cwd=payload.get("cwd", str(FLEET_DIR.parent)),
    )
    return json.dumps({"output": result.stdout, "error": result.stderr})
```

**Use cases:**
- Code review: `claude -p "Review fleet/worker.py for bugs"`
- Refactoring: `claude -p "Refactor this function" < file.py`
- Documentation: `claude -p "Document this module's API"`
- Test generation: `claude -p "Write tests for fleet/db.py"`

**Advantages over current console:**
- Full codebase context (Claude Code reads files)
- Tool use (can edit files, run commands)
- MCP server access
- No custom UI to maintain

### Tier 2: VS Code Launch Integration

**For human-interactive code review and development.**

```python
# In launcher.py — replace "Claude Console" sidebar button
def _open_claude_code_session(self):
    """Launch VS Code with Claude Code for interactive fleet development."""
    import shutil
    code = shutil.which("code")
    if not code:
        self._show_toast("VS Code not found — install from code.visualstudio.com", RED)
        return
    project_dir = str(FLEET_DIR.parent)
    subprocess.Popen([code, project_dir])
    self._show_toast("VS Code opened with fleet context", GREEN)
```

**Sidebar integration:**
```
CONSOLES (renamed to INTERACT)
  🖥 Claude Code     → opens VS Code + Claude Code in project
  🤖 Claude Chat     → headless claude -p for quick queries
  ✦ Gemini Chat      → keep existing (for non-code tasks)
  ⚡ Local Chat       → keep existing (for offline/private tasks)
```

### Tier 3: Claude Code SDK (Programmatic Pipeline)

**For fleet skills that need Claude Code as a tool.**

```python
# fleet/skills/code_agent.py — uses Claude Code SDK
# npm install @anthropic-ai/claude-code (Node.js)
# Or via subprocess: claude -p with structured prompts

def _claude_code_review(file_path, config):
    """Use Claude Code headless for deep code review."""
    result = subprocess.run(
        ["claude", "-p", f"Review {file_path} for bugs, security issues, and improvements. "
         "Output a structured JSON report with findings."],
        capture_output=True, text=True, timeout=120,
        cwd=str(FLEET_DIR.parent),
    )
    return result.stdout
```

**Integration with existing skills:**
- `code_review.py` → can delegate to `claude -p` for deeper analysis
- `skill_evolve.py` → can use Claude Code to rewrite skills
- `security_audit.py` → can use Claude Code for code-level security scan
- `refactor_verify.py` → can use Claude Code to validate refactors

---

## Implementation Plan

### Phase 1: Headless Skill (0.21.02)
- Create `fleet/skills/claude_code.py` — wraps `claude -p`
- Actions: review, refactor, document, test, custom prompt
- Detects claude CLI availability via `shutil.which`
- Falls back to existing `call_complex()` if claude CLI not found

### Phase 2: Launcher Integration (0.21.03)
- Replace "Claude Console" button with "Claude Code" button
- Opens VS Code in project directory if available
- Keep "Claude Chat" as secondary option (current console)
- Add "Quick Review" button on agent cards → launches `claude -p` on agent's last task output

### Phase 3: Skill Enhancement (0.21.04)
- `code_review.py` upgrades to use `claude -p` when available
- `skill_evolve.py` uses Claude Code for skill rewriting
- `security_audit.py` uses Claude Code for deep code analysis
- Evolution pipeline dispatches Claude Code for review stage

### Phase 4: Fleet-Wide Code Agent (0.22.xx)
- Dedicated "code agent" worker that runs Claude Code sessions
- Can handle complex multi-file refactors
- Integrates with git_manager for branch/commit/PR workflow
- Uses Claude Code SDK for programmatic control

---

## Prerequisites

| Requirement | Status | Install |
|-------------|--------|---------|
| VS Code | Check `shutil.which("code")` | https://code.visualstudio.com |
| Claude Code CLI | Check `shutil.which("claude")` | `npm install -g @anthropic-ai/claude-code` |
| Node.js (for SDK) | Check `shutil.which("node")` | https://nodejs.org |
| ANTHROPIC_API_KEY | Already in ~/.secrets | Already configured |

---

## Migration Strategy

1. **Don't remove existing consoles** — keep them as fallback
2. **Add new options alongside** — "Claude Code" button next to "Claude Chat"
3. **Gradual skill migration** — skills that benefit from Claude Code switch one by one
4. **Feature detection** — every feature checks for CLI availability, falls back gracefully
