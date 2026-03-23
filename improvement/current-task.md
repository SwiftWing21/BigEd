# Task: skill-quality-audit
## Type: refactor
## Desired Outcome: All fleet skills conform to the skill contract (SKILL_NAME, DESCRIPTION, REQUIRES_NETWORK, lazy imports, db._retry_write, proper error handling) with zero contract violations
## Non-Goals: Changing skill behavior/logic, adding new skills, modifying the worker dispatch system
## Hard Gates: All .py files pass syntax check; smoke_test.py --fast passes (22/22); skill import test passes
## Primary Metric: Number of contract violations across all skills (target: 0)
## Secondary Metrics: Skills with missing REQUIRES_NETWORK, skills with module-level db import, skills with bare except
## Iteration Budget: 4 loops
## Rollback Plan: git stash / git checkout -- fleet/skills/
