# BigEd CC — System Flow Charts

Visual function flow charts showing how BigEd CC works under the hood.

These charts are `.txt` format for portability and version control.
Referenced from the GitHub README for transparency.

## Charts

| File | What It Shows |
|------|---------------|
| `boot_sequence.txt` | Fleet startup: Ollama → models → Dr. Ders → workers |
| `task_lifecycle.txt` | Task: create → claim → execute → score → complete |
| `model_management.txt` | Model loading, tier scaling, failsafe recovery |
| `hitl_flow.txt` | Human-in-the-loop: agent request → user response → follow-up |
| `backup_system.txt` | Auto-save: WAL checkpoint → copy → verify → prune |
| `idle_evolution.txt` | Idle: select skill → evolve → test → review → deploy |
