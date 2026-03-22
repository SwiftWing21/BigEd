# Documentation Cleanup Tracker

> Generated 2026-03-21 by doc audit. Each item categorized as STALE (outdated), WRONG (factually incorrect), or MISSING (should exist but doesn't). Items marked [x] were fixed directly. Items marked [ ] need human review or are too complex for auto-fix.

---

## CLAUDE.md (project root)

- [x] **WRONG — Version in header:** Says `0.051.04b`, should be `0.170.05b` (line 1)
- [x] **WRONG — Skill count in Structure section:** Says `80-skill` (line 45), should be `85-skill`
- [x] **WRONG — Skill count in Fleet Status:** Says `Skills: 80` (line 54), should be `Skills: 85`
- [x] **WRONG — Dashboard endpoint count:** Says `50+ endpoints` (line 54), actual is `58 endpoints`
- [x] **STALE — Fleet Status version range:** Says `v0.050.00b-0.051.04b` (line 58), should reference up to `0.170.05b`
- [x] **WRONG — Dashboard endpoint count in Docs section:** Says `45+ endpoints` (line 20), should be `58 endpoints`

## fleet/CLAUDE.md

- [x] **WRONG — Skill count:** Says `Skills: 80 registered` (line 26), should be `Skills: 85 registered`
- [x] **WRONG — Dashboard endpoint count:** Says `Dashboard: 45+ endpoints` (line 26), should be `Dashboard: 58 endpoints`

## README.md

- [x] **WRONG — Version reference:** Says `v0.051.04b` (line 20), should be `0.170.05b`
- [x] **WRONG — Skill count in intro paragraph:** Says `80-skill` (line 5), should be `85-skill`
- [x] **WRONG — Skill count in Features:** Says `80+ AI Skills` (line 46), should be `85+ AI Skills`
- [x] **WRONG — Skill count in Architecture tree:** Says `80-skill AI worker fleet` (line 80) and `80 registered skills` (line 85), should both be `85`

## docs/WHAT_IS_BIGED.md

- [x] **WRONG — Skill count in description:** Says `84 registered` (line 14), should be `85`
- [x] **WRONG — Skill count in Feature Summary header:** Says `84 skills` (line 27), should be `85 skills`
- [x] **WRONG — Skills by Category total:** Says `84 total` (line 38), should be `85 total`
- [x] **WRONG — DB table count:** Says `11 tables` (line 56), should be `12 tables` (added output_feedback, audit_log, audit_runs)
- [x] **STALE — Version footer:** Says `0.170.04b` (line 186), should be `0.170.05b`
- [x] **WRONG — Skill count in footer:** Says `85 skills` (line 188) — correct, but `11 DB tables` should be `12`
- [x] **STALE — DB table list:** Missing output_feedback, audit_log, audit_runs from table list description (line 56)
- [ ] **STALE — Skills by Category breakdown:** Category counts sum to 84, need to identify which category gained the 85th skill and update. Needs manual audit of which category the new skill belongs to.

## FRAMEWORK_BLUEPRINT.md

- [ ] **STALE — Version in title:** Says `v0.41` (line 1), current is `0.170.05b`. Major rewrite needed.
- [ ] **STALE — Skill count:** Says `66 skill modules` (line 42), actual is 85. Major drift.
- [ ] **STALE — Dashboard endpoints:** Says `40 endpoints` (line 38), actual is 58.
- [ ] **STALE — Smoke test count:** Says `10-check` (line 39), actual is 22/22.
- [ ] **STALE — Architecture tree:** References `db.py` as DAL (line 34), actual DAL is `data_access.py`. `db.py` still exists but `data_access.py` is the primary interface.
- [ ] **STALE — Module list:** Missing mod_intelligence.py, mod_manual_mode.py (added post-v0.41). Only lists 6 modules, actual is 9.
- [ ] **STALE — launcher.py line count:** Says `~4700 lines` (line 13). Cowork refactor removed ~1200 lines, significant UI extraction happened. Needs re-count.
- [ ] **STALE — Overall scope:** This file is frozen at v0.41. Nearly every section has drifted. Recommend a full rewrite or deprecation notice at the top linking to current CLAUDE.md + WHAT_IS_BIGED.md.

## OPERATIONS.md

- [ ] **STALE — `uv run` usage throughout:** CLAUDE.md gotcha says "No `uv run` on Windows: use native `python`". OPERATIONS.md uses `uv run` in ~18 places (Quick Start, CLI Reference, Skill Authoring, Deployment). Should either note `uv run` is Linux/WSL only, or replace with `python` for cross-platform instructions.
- [ ] **STALE — `db.py` direct API references:** Lines 126-127, 461 reference `import db; db.init_db(); db.recover_stale_tasks()` etc. The DAL migrated to `data_access.py` (FleetDB class). These commands may still work (db.py exists) but are not the recommended interface.
- [ ] **STALE — Companion doc reference:** Line 5 references `ROADMAP_v030_v040.md` which does not exist (merged into `ROADMAP.md`).
- [ ] **STALE — Dashboard reference:** Says `Flask web dashboard v2` but does not mention current endpoint count or recent features (SSE, federation, audit viewer, feedback).

## CONTRIBUTING.md

- [x] **OK — Stability gate smoke test count:** Line 41 correctly says `Smoke tests: 22/22`. No fix needed.

## ROADMAP.md

- [ ] **STALE — Release Gate checklist:** Line 86 says `Smoke tests: 10/10`. This is the historical template from early versions. Consider updating the template to current counts (22/22) or noting it is version-dependent.
- [ ] **STALE — Audit Coverage Check section:** Line 1341 says "Reviewed at v0.110.00b (2026-03-20)". Should be updated to reflect 0.170.05b. The P-issue counts are correct (all 0).
- [ ] **MINOR — Duplicate 0.051.06b placement:** MiniMax integration (0.051.06b, line 724) appears after 0.053.01b (line 715), breaking chronological order.

## Cross-Document Consistency Issues

- [x] **WRONG — Skill count inconsistency:** CLAUDE.md (80), README (80), fleet/CLAUDE.md (80), WHAT_IS_BIGED (84/85), actual codebase (85). All updated to 85.
- [x] **WRONG — Dashboard endpoint inconsistency:** CLAUDE.md (50+), fleet/CLAUDE.md (45+), WHAT_IS_BIGED (45+), actual codebase (58). All updated to 58.
- [x] **WRONG — Version inconsistency:** CLAUDE.md (0.051.04b), README (v0.051.04b), WHAT_IS_BIGED (0.170.04b), ROADMAP latest (0.170.05b). Updated where auto-fixable.

## Missing Items

- [ ] **MISSING — skill_picker.py not mentioned in any doc.** Located at `BigEd/launcher/ui/skill_picker.py`. Should be referenced in FRAMEWORK_BLUEPRINT or WHAT_IS_BIGED architecture tree.
- [ ] **MISSING — New fleet/ files not documented in architecture trees:** event_triggers.py, reinforcement.py, context_manager.py, cache_manager.py, audit.py are all active fleet modules not listed in any architecture diagram. FRAMEWORK_BLUEPRINT and WHAT_IS_BIGED should reference them.
- [ ] **MISSING — create_usb_media.py not in README architecture tree.** Listed in WHAT_IS_BIGED deployment options but not in README.

---

## Summary

| Category | Count | Auto-Fixed | Needs Review |
|----------|-------|------------|--------------|
| WRONG    | 17    | 14         | 3            |
| STALE    | 15    | 2          | 13           |
| MISSING  | 3     | 0          | 3            |
| **Total**| **35**| **16**     | **19**       |

### Priority Recommendations

1. **FRAMEWORK_BLUEPRINT.md** is the most stale doc — frozen at v0.41 with 66 skills, 40 endpoints, old module list. Recommend adding a deprecation banner or scheduling a full rewrite.
2. **OPERATIONS.md** `uv run` references need a cross-platform pass — either add platform notes or standardize on `python`.
3. **ROADMAP.md** release gate template and audit coverage check need version bumps.
