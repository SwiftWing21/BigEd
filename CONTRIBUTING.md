# Contributing to BigEd CC

We welcome contributions to BigEd CC. This guide covers the workflow, standards, and testing expectations for the project.

---

## Getting Started

1. Fork the repository and clone your fork:
   ```bash
   git clone https://github.com/<your-username>/Education.git
   cd Education
   ```

2. Install dependencies:
   ```bash
   pip install -r BigEd/launcher/requirements.txt
   # or, using uv (recommended):
   uv sync
   ```

3. Install [Ollama](https://ollama.com) and pull the default model:
   ```bash
   ollama pull qwen3:8b
   ```

4. Launch the application:
   ```bash
   python BigEd/launcher/launcher.py
   ```

---

## Development Workflow

- Branch from `main`. All current work happens on `main`.
- Keep PRs focused -- one feature or fix per PR.
- Write descriptive commit messages that explain *why*, not just *what*.
- Before merging, verify your change against the stability gate checklist:
  ```
  - [ ] Smoke tests: 22/22
  - [ ] Soak tests: 13/13
  - [ ] GUI smoke test: pass
  - [ ] TECH_DEBT.md: reviewed, no P0
  - [ ] ROADMAP: version marked DONE with date
  - [ ] git status: clean
  ```

---

## Project Structure

| Directory | Purpose |
|-----------|---------|
| `fleet/` | AI worker fleet -- supervisor, workers, skills, dashboard, config |
| `fleet/skills/` | Individual skill modules (one Python file per skill) |
| `BigEd/` | Desktop GUI launcher (customtkinter) and compliance docs |
| `autoresearch/` | ML training pipeline |

---

## Writing a New Skill

Each skill is a single Python file in `fleet/skills/`. Skills are auto-discovered at boot -- no registration step needed.

**Required exports:**

```python
SKILL_NAME = "my_skill"
DESCRIPTION = "What this skill does in one sentence."

def run(payload: dict, config: dict, log) -> dict:
    """
    Main entry point. Returns a result dict.
    payload — JSON parsed from task's payload_json
    config  — fleet.toml dict from config.load_config()
    log     — worker logger (use log.info/warning/error)
    """
    ...
```

**Optional exports:**

```python
REQUIRES_NETWORK = True          # Default: False. Set True if the skill needs internet.
COMPLEXITY = "simple"            # "simple" | "medium" | "complex" — affects model routing.
```

Follow existing skills in `fleet/skills/` as examples. Drafts should go to `knowledge/code_drafts/` for review before deployment.

---

## Code Standards

- **Python 3.11+** required.
- No auto-formatters are enforced, but keep style consistent with surrounding code.
- Use `psutil` for process management. Do not use shell commands like `pkill` or `pgrep`.
- Use `tomlkit` for TOML writes (it preserves comments and formatting).
- Windows-native by default. Core features must not depend on WSL.
- GUI theme constants live in `BigEd/launcher/ui/theme.py` -- use them instead of hardcoding colors or fonts.

---

## Testing

Run smoke tests (must all pass before any PR is merged):

```bash
cd fleet && python smoke_test.py
```

Run the full test suite:

```bash
cd fleet && python -m pytest tests/
```

- All PRs must pass smoke tests (22/22).
- Security-sensitive changes must also pass `tests/test_security.py`.

---

## Security

- Never commit API keys, tokens, or secrets. Use the `.secrets` file (gitignored) for local key storage.
- DLP scrubbing runs on all skill outputs -- do not bypass it.
- Report security issues privately via email or direct message. Do not file public issues for vulnerabilities.

---

## License

This project is licensed under [Apache 2.0](LICENSE). By submitting a pull request, you agree that your contributions are licensed under the same terms.
