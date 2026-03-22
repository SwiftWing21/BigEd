---
name: max-efficiency
description: Optimize Claude API usage — caching, batching, MCP tools, minimize re-sends
---

# Claude Maximum Efficiency

Apply these optimizations automatically in every response:

## 1. Prompt Caching
- Use `cache_control: {type: "ephemeral"}` on any stable content block (system prompts, CLAUDE.md content, tool definitions)
- Cache breakpoints at stable prefix boundaries — put changing content AFTER cached content
- 90% discount on cached input tokens (5-minute TTL)

## 2. Minimize Re-sends
- Never repeat prior context in follow-up messages
- Reference previous results by name, don't re-paste them
- Use "as discussed above" rather than restating

## 3. MCP Tools First
- Read files via filesystem MCP tool, not by asking "show me the contents of X"
- Use sequential-thinking MCP for multi-step reasoning instead of chain-of-thought in the prompt
- Use memory MCP to persist cross-session data instead of re-deriving

## 4. Batch Operations
- For non-urgent multi-file operations, suggest the Message Batches API (50% discount)
- Group related edits into single tool calls rather than one-per-file

## 5. Reference Don't Repeat
- Read CLAUDE.md once, reference its rules by name thereafter
- Don't re-derive conventions that are documented — cite them
- If context was loaded in a prior message, refer to it, don't re-include
