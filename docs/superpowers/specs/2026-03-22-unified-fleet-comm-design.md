# Unified Fleet Comm Console — Design Spec

**Date:** 2026-03-22
**Status:** Approved (brainstormed with operator)

## Goal

Consolidate Fleet Comm's manual chat into a single unified console supporting all three providers (Local/Claude/Gemini) with clear model indicators, usage tracking, and a streamlined HITL workflow via VS Code. Move standalone power-user consoles to Settings.

## Architecture

Fleet Comm's chat section becomes a **single-stream, multi-provider console**. Provider pill buttons (Local, Claude, Gemini) select which API receives the next message. An "OAuth" pill opens VS Code for Claude Code / Gemini CLI / future OAuth providers. Messages in the chat are tagged and color-coded by provider. A compact usage status bar shows cost/performance metrics at all times.

## Components

### 1. Provider Selector (pill buttons)

- Colored pill buttons: `Local` (gold), `Claude` (blue), `Gemini` (green), `OAuth` (purple)
- Each pill has a small connection dot:
  - Green = reachable (Ollama running / API key valid)
  - Red = unreachable (Ollama down / key missing)
  - Gray = not configured
- Active provider pill has a pulsing border/glow while streaming
- Selecting Local/Claude/Gemini switches which API receives the next message
- OAuth opens VS Code with the existing briefing flow

### 2. Model Swapper (inline, right-aligned on pill row)

- Changes dynamically based on selected provider:
  - Local: dropdown of installed Ollama models from `/api/tags` (with "NEW" badges on unregistered models)
  - Claude: Haiku / Sonnet / Opus picker
  - Gemini: model picker from config
- Compact — sits right-aligned on the provider pill row

### 3. Chat Stream (single, tagged messages)

- Single scrolling chat history (CTkTextbox)
- Each assistant message tagged with provider icon + model name (e.g. `[qwen3:8b]`, `[claude-sonnet-4-6]`)
- Provider-colored left border or prefix on assistant messages (gold/blue/green)
- User messages remain neutral
- **Quarantine for unregistered models:** responses get a warning border + "Accept / Reject / Flag" mini-panel. Accepting N responses marks model as trusted in fleet.db.

### 4. Compact Usage Status Bar (always visible, below provider selector)

- One line per active provider this session:
  - `Local: 3.2k tok | 45 tok/s | qwen3:8b loaded`
  - `Claude: 8.1k tok | $0.12 | 4 calls`
  - `Gemini: 5.4k tok | $0.03 | 2 calls`
- Click any line -> popover with all-time totals from fleet.db + "Reset Session" button
- Collapsible via Settings toggle (default: visible)

### 5. Connection Status + Active Indicator

- **Connection dots** on provider pills (persistent): polls Ollama `/api/tags`, checks env vars for API keys
- **Streaming indicator**: pulsing glow on active provider pill + "thinking..." animation in chat while response generates

### 6. Agent Request Cards (updated)

- Keep collapsible request section above chat
- Each card gets two reply buttons:
  - **"Reply in Local Chat"** — pre-fills entry, routes through local Ollama
  - **"Reply in VS Code"** — creates `fleet/hitl-response-{task_id}.md`, opens in VS Code
- File poller in BigEd detects save -> auto-sends response -> clears card

### 7. OAuth Button

- Replaces "Claude Code (VS Code)" / "Gemini CLI (VS Code)" dropdown entries
- Single "OAuth" pill that opens VS Code with briefing, pre-loads skills + .md files
- Works for Claude Code, Gemini Code Assist, future OAuth providers

### 8. Claude Code Skill: `/hitl-respond`

- Companion skill for VS Code HITL reply flow
- When invoked, reads pending `fleet/hitl-response-{task_id}.md`
- Shows agent context, question, related files
- User collaborates with Claude Code to draft response
- Save triggers BigEd's poller to deliver it

### 9. Power User Consoles -> Settings

- `ClaudeConsole`, `GeminiConsole`, `LocalConsole` standalone windows moved to "Developer Consoles" section in Settings
- Remove console buttons from sidebar
- Settings section has "Open Claude Console", "Open Gemini Console", "Open Local Console" buttons

## Data Flow

```
User selects provider pill -> types message -> Send
  |- Local -> Ollama /api/chat (model from swapper)
  |    |- If model unregistered -> quarantine response (Accept/Reject/Flag)
  |- Claude -> Anthropic API (model from swapper)
  |- Gemini -> Google GenAI API (model from swapper)
  |- OAuth -> write task-briefing.md -> launch VS Code

Usage tracker increments per-call -> updates status bar
  |- Session counters (in-memory, reset on close)
  |- All-time counters (fleet.db usage table, persist)

Agent HITL request -> card with "Reply in Local Chat" / "Reply in VS Code"
  |- Local Chat path: pre-fills entry, routes through selected provider
  |- VS Code path:
       Create hitl-response-{id}.md -> open in VS Code
       -> user edits (optionally with /hitl-respond skill)
       -> file poller detects save -> send response -> clear card
```

## Files Affected

| File | Action | Purpose |
|------|--------|---------|
| `BigEd/launcher/launcher.py` | Modify | Rewrite `_build_tab_comm()`, add provider pills, usage bar, unified routing |
| `BigEd/launcher/ui/consoles.py` | Modify | Extract API call methods into reusable functions |
| `BigEd/launcher/ui/settings/__init__.py` | Modify | Add "Developer Consoles" nav section |
| `BigEd/launcher/ui/settings/consoles.py` | Create | New settings panel for power-user console launchers |
| `BigEd/launcher/ui/theme.py` | Modify | Add provider color constants |
| `fleet/db.py` | Modify | Add `trusted_models` table, session usage helpers |
| `fleet/cost_tracking.py` | Modify | Add session-scoped counters |
| `fleet/hitl_responder.py` | Create | File poller for hitl-response-{id}.md files |
| `fleet/skills/hitl_respond.py` | Create | Claude Code companion skill |

## Non-Goals

- Not replacing the standalone consoles — just relocating them
- Not changing the VS Code briefing content format
- Not adding streaming responses (keep `stream: False` for now)
- Not changing the AI draft feature on agent request cards
