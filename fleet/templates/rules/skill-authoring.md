# Rule: Skill Authoring

These rules apply whenever writing a new skill for the BigEd CC fleet.

---

## Required Exports

Every skill module in `fleet/skills/` **must** export:

```python
SKILL_NAME: str    # unique snake_case identifier matching the filename
DESCRIPTION: str   # one-line human-readable purpose
run(payload: dict, config: dict) -> dict  # single entry point
```

Optional:
```python
REQUIRES_NETWORK: bool = False  # set True if skill needs internet
```

---

## Signature Contract

- `payload` is **untrusted** — validate all keys before use.
- `config` comes from `fleet.toml` — do not mutate it.
- Return a `dict`. On failure, return `{"error": str, "status": "error"}`.
- On success, return `{"status": "ok", ...}` with relevant output keys.

---

## Model Calls

Use the shared model helpers — never instantiate raw API clients:

```python
from skills._models import call_complex, call_medium, call_simple
```

| Helper | Use for |
|--------|---------|
| `call_simple` | Haiku / qwen3:4b — high-volume, low-cost tasks |
| `call_medium` | Sonnet / qwen3:8b — reviews, analysis |
| `call_complex` | Opus / qwen3:8b — planning, multi-step reasoning |

---

## Complexity Limits

- Keep individual skills focused on one task.
- Skills over 400 lines should be split or use helper modules in `skills/`.
- Budget-sensitive skills should check `config.get("budgets", {})` and log warnings.

---

## Output Path

Skill output files **must** go to `fleet/knowledge/` or a subdirectory:

| Output type | Target directory |
|-------------|-----------------|
| Code drafts | `fleet/knowledge/code_drafts/` |
| Reviews | `fleet/knowledge/code_reviews/` |
| Security | `fleet/knowledge/security/` |
| General | `fleet/knowledge/` |

Never write output directly to `fleet/skills/` — that is a read-only zone for workers.

---

## Metadata

Include a module docstring with:
- What the skill does
- Input payload keys
- Output dict keys

---

## Draft Workflow

1. `skill_draft` generates to `knowledge/code_drafts/`
2. Human or fleet reviews the draft
3. `skill_promote` validates and stages
4. `deploy_skill` copies to `skills/` with rollback

**Never call `deploy_skill` from within a skill.** Promotion is always a human-triggered step.
