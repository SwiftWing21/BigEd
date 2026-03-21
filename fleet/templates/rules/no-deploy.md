# Rule: No Auto-Deploy

Skills **must never self-deploy** or trigger deployment without explicit operator action.

---

## Prohibited Patterns

The following patterns are strictly forbidden inside any skill module:

- Importing and calling `deploy_skill.run(...)` from within another skill
- Using `shutil.copy` or `shutil.move` to write files into `fleet/skills/`
- Using `exec()`, `eval()`, or `importlib.import_module` to dynamically load draft code at runtime
- Spawning a subprocess that writes to `fleet/skills/`

---

## Why

Auto-deployment bypasses:

1. **Human review** — an operator must inspect every draft before it goes live.
2. **`skill_promote` validation** — which checks the signature, metadata, and test coverage.
3. **`deploy_skill` rollback** — which snapshots the previous version for recovery.
4. **`FileSystemGuard`** — `fleet/skills/` is a read-only zone for workers; only `deploy_skill`
   has write access via its `[filesystem.overrides]` entry in `fleet.toml`.

---

## Correct Workflow

```
skill_draft  →  knowledge/code_drafts/<name>_draft_<date>.py
                         ↓  (operator reviews)
              skill_promote  →  stages for deployment
                         ↓
              deploy_skill   →  copies to fleet/skills/ with rollback
```

Operators trigger `skill_promote` and `deploy_skill` manually via:
- The BigEd CC launcher GUI (Intelligence tab → Prompt Queue)
- `python fleet/lead_client.py task "deploy_skill <draft_name>"`

---

## Enforcement

The `security_audit` skill flags any code path that calls `deploy_skill` outside the
standard promote workflow. Claude Code reviews should reject PRs that add auto-deploy logic.
