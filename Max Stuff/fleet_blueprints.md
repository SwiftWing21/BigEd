# Fleet Architecture Blueprints

Synthesized from Anthropic engineering + developer community best practices.
Status column: ✅ done | 🔧 partial | ⬜ not started

---

## Architectural Patterns

### 1. Orchestrator-Workers Workflow ✅
**What it is:** Central LLM decomposes tasks, delegates to specialists, synthesizes results.
**Our implementation:** `supervisor.py` → dispatches to researcher/coder/security/etc.
**Gap:** Supervisor currently uses static role routing. Upgrade path: let supervisor LLM
decide which worker(s) to assign based on task content rather than skill name alone.

### 2. Long-Running Project Harness 🔧
**What it is:** Structural wrapper that forces decomposition, memory, accountability across sessions.
Treats git history + markdown logs as state layer (no vector DB needed).
**Our implementation:** `dispatch_marathon.py` + `STATUS.md` partially cover this.
**Gap:** No progress log per marathon run. Each 8-hour session should write a
`knowledge/marathon/YYYY-MM-DD.md` with: goal, completed steps, next step, blockers.
Workers read this on startup to avoid context drift.

### 3. Evaluator-Optimizer Loop ⬜
**What it is:** Generator LLM produces output → evaluator LLM critiques against criteria → iterate.
**Our implementation:** Nothing yet.
**Application:** Security agent drafts advisory → Claude Console (Sonnet) evaluates against
HIPAA/CVE criteria before writing to `knowledge/security/pending/`. Add an `evaluate`
skill that wraps any skill output with a Sonnet critique pass before finalizing.

### 4. Routing + Parallelization 🔧
**What it is:** Route by complexity (local vs cloud). Parallelize independent subtasks or
run N instances for voting on high-stakes outputs.
**Our implementation:** eco_mode toggles local vs GPU, but no cloud routing logic.
**Application:**
- Simple/high-volume → qwen3:8b (local)
- Complex synthesis / executive decisions → claude-sonnet-4-6
- Guardrails: run a second local model instance concurrently to screen task outputs

---

## Tool & System Design Rules

| Rule | Status | Notes |
|------|--------|-------|
| Absolute paths in WSL tools | ✅ | `wsl()` always cds to fleet dir |
| Minimal formatting overhead | 🔧 | Some skills return markdown-heavy blobs; prefer plain text |
| No heavy abstraction frameworks | ✅ | Direct API calls, no LangChain etc. |
| Dedicated checker agent | ⬜ | Add narrow-context log-monitor skill to `security` idle curriculum |
| Mistake-proof tool args | 🔧 | `lead_client.py` accepts free-text task — add type validation |

---

## Priority Implementation Order

1. **Marathon progress log** — low effort, high value for long runs
2. **Evaluator skill** — wrap security + research outputs through Sonnet before saving
3. **Complexity router** — `supervisor.py` checks task complexity score, routes to Sonnet vs local
4. **Checker agent** — security idle curriculum item that tails logs and flags drift patterns
