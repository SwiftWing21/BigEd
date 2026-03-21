# BigEd CC — CLAUDE.md Template

Copy this file to the root of your BigEd CC project as `CLAUDE.md` and fill in
any bracketed placeholders before committing.

---

## Project Overview

**BigEd CC** is an on-premise AI worker fleet built on Ollama and the Anthropic/Gemini APIs.
It consists of:

- `fleet/` — 77-skill AI worker fleet (Ollama + Claude/Gemini fallback)
- `BigEd/` — Customtkinter launcher GUI with a dashboard module system
- `autoresearch/` — ML training pipeline and benchmark tooling
- `fleet/fleet.toml` — single source of runtime truth (models, workers, budgets, security)

The fleet runs 24/7 and auto-scales workers based on available RAM (see `fleet/system_info.py`).

---

## Key Directories

| Path | Purpose |
|------|---------|
| `fleet/skills/` | All executable skill modules — never edit without review |
| `fleet/knowledge/` | All skill outputs (reviews, drafts, indexes, audit results) |
| `fleet/knowledge/code_drafts/` | Skill drafts — operator reviews before promotion |
| `fleet/logs/` | Per-worker logs + `config_audit.log` |
| `BigEd/launcher/modules/` | Tab modules loaded by the launcher GUI |
| `BigEd/launcher/ui/` | Theme, dialogs, settings panels |
| `fleet/fleet.toml` | Runtime config — change via UI or tomlkit, never raw string replace |

---

## Coding Conventions

### Python

- **Python 3.11+** required everywhere.
- **Process management:** use `psutil` — never `pkill`, `pgrep`, or `os.kill` on Windows.
- **TOML writes:** always use `tomlkit` (preserves comments and formatting).
  ```python
  import tomlkit
  doc = tomlkit.parse(Path("fleet/fleet.toml").read_text(encoding="utf-8"))
  doc["section"]["key"] = value
  Path("fleet/fleet.toml").write_text(tomlkit.dumps(doc), encoding="utf-8")
  ```
- **Skill signature:** every skill must export `run(payload: dict, config: dict) -> dict`.
- **DB access:** always through `fleet/data_access.py` (FleetDB) or `fleet/rag.py`.
  Never use raw `sqlite3` outside of `fleet/db.py`.
- **Subprocess on Windows:** all `subprocess.Popen` calls must include:
  ```python
  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
  ```
- **No `uv run` on Windows** — use `python` directly. `uv run` is WSL-only.
- **Secrets:** never log or return API keys. `fleet/skills/_watchdog.py` scrubs 17 patterns.
- **Config audit:** wrap every `tomlkit` write with `_log_config_change(section, key, old, new)`
  to maintain `fleet/logs/config_audit.log`.

### Skill authoring

- Drafts output to `fleet/knowledge/code_drafts/` — never to `fleet/skills/` directly.
- Use `from skills._models import call_complex` for LLM calls (not raw API).
- Declare `REQUIRES_NETWORK = True` if the skill needs internet.
- Include `SKILL_NAME`, `DESCRIPTION`, and `run(payload, config)` exports.

---

## What NOT To Do

- **Never modify `fleet.db` directly** — all DB access goes through `data_access.py`.
- **Never auto-promote skills** — drafts in `code_drafts/` require human review before
  being promoted via `skill_promote` → `deploy_skill`.
- **Never remove or bypass `FileSystemGuard`** (`fleet/filesystem_guard.py`).
  It enforces SOC 2 file access zones.
- **Never hardcode URLs or ports** in skills — read from `fleet.toml` via `config.py`.
- **Never use `pkill`, `pgrep`, or `taskkill`** — use `psutil.Process(pid).terminate()`.
- **Never skip the HITL gate** in `fleet/manual_mode.py` without operator approval.
- **Never commit `CLAUDE.USER.md`** — it is gitignored and machine-specific.

---

## Running Tests

```bash
# Pre-flight dependency check (11 checks)
python fleet/dependency_check.py

# Fast smoke tests (22 tests, ~30s)
python fleet/smoke_test.py --fast

# Full smoke suite
python fleet/smoke_test.py

# Soak test (runs fleet for extended period)
python fleet/smoke_test.py --soak
```

All 22 smoke tests must pass before merging to `main`.

---

## Fleet Quick Reference

```bash
python fleet/lead_client.py status              # fleet health
python fleet/lead_client.py task "instruction"  # dispatch a task
python BigEd/launcher/launcher.py               # launch GUI
```

---

## Version

Current: `[INSERT VERSION]`
Scheme: `0.XX.YY` (alpha patches) → `0.XX.00` (milestones) → `1.000.00` (production)
