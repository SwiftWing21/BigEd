#!/bin/bash
# Run after: gh auth login
# Populates GitHub project board with v0.10+ milestones
# Board: https://github.com/users/SwiftWing21/projects/2

PROJECT=2
REPO="SwiftWing21/Education"

# Helper: create issue and add to project
add_item() {
    local title="$1"
    local body="$2"
    local labels="$3"
    echo "Creating: $title"
    gh issue create --repo "$REPO" --title "$title" --body "$body" ${labels:+--label "$labels"} --project "$PROJECT" 2>/dev/null || \
    echo "  (issue created, may need manual project link)"
}

echo "=== v0.10 — Foundation & Polish ==="
add_item "v0.10a: Smoke test + root dependency tracking" \
"- [x] \`fleet/smoke_test.py\` — 8-check automated startup verification
- [x] \`pyproject.toml\` — root dependency groups (launcher, fleet)" \
"v0.10"

add_item "v0.10b: UI polish — flicker fix, timer consolidation, hysteresis" \
"- [x] Agent table: cached rows, configure() not destroy/recreate
- [x] Remove \`_schedule_agent_tick\`, refresh→4s, hw→3s
- [x] Stats color hysteresis (2 consecutive samples)
- [x] Log tail + advisory badge → background thread" \
"v0.10"

add_item "v0.10c: Chat intelligence — priority dispatch, fleet context, result polling" \
"- [x] Console dispatch priority → 10
- [x] Fleet context injection (all 3 consoles)
- [x] Dispatch result polling (2s, 60s timeout) → chat notification" \
"v0.10"

add_item "v0.10d: Conductor model (qwen3:4b CPU-pinned)" \
"- [x] \`conductor_model\` in fleet.toml
- [x] Pre-load at supervisor startup (num_gpu=0)
- [x] LocalConsole uses conductor
- [x] lead_client intent parser upgraded to conductor" \
"v0.10"

add_item "v0.10e: Resilience — timeouts, affinity, messages" \
"- [x] Skill timeout enforcement (600s default, 900s code_write)
- [x] Planner fleet awareness (\`_survey_fleet()\`)
- [x] Role-based skill affinity (\`[affinity]\` in fleet.toml)
- [x] Actionable message types (ping/pause/resume/config_reload)" \
"v0.10"

echo ""
echo "=== v0.11 — Skill Training Framework ==="
add_item "v0.11: Expand skill_train.py eval harnesses" \
"Add mechanical metrics for:
- \`code_write\` (diff quality, commit success)
- \`lead_research\` (structured fields, company count)
- \`security_audit\` (finding count, severity coverage)
- \`arxiv_fetch\` (result count, abstract quality)" \
"v0.11"

add_item "v0.11: Discovery logging — config/method findings" \
"skill_train.py should log discoveries to knowledge/ even on score-neutral runs:
- Configuration variants tried (prompt templates, API settings)
- New methods/solves found (waterfall approaches, skill chaining)
- Streamlined versions of existing approaches
These feed the knowledge base regardless of metric delta." \
"v0.11"

add_item "v0.11: Skill training scheduler" \
"Planner auto-queues \`skill_train\` for underperforming skills based on:
- Task failure rates (from DB)
- Skills with no training history
- Skills flagged by \`skill_learn.py\` analysis" \
"v0.11"

echo ""
echo "=== v0.12+ — Future Milestones ==="
add_item "v0.12: Training budget + results tracking" \
"- Wall-clock budget per skill training run (like autoresearch 5min)
- \`skill_results.tsv\` tracking across runs (commit-style)
- Trend visualization in dashboard" \
"v0.12"

add_item "v0.12: A/B skill deployment" \
"Run old vs new skill version on live tasks, compare success rates before promoting.
Shadow mode: both versions run, only original result returned, new version scored." \
"v0.12"

add_item "v0.13: Cross-skill knowledge synthesis" \
"Discoveries from skill training feed back into planner context.
Config findings become reusable templates.
Method discoveries can spawn new skill drafts automatically." \
"v0.13"

echo ""
echo "=== Historical Milestones (reference) ==="
for ver in "v0.0: Initial fleet manager baseline (f9c2b07)" \
           "v0.2: Aider integration — code_write skills (63be32b)" \
           "v0.4: Dashboard, RAG/FTS5, self-improvement pipeline (21e1a82)" \
           "v0.5: BigEd CC rebrand + GUI overhaul (6e5d0cb)" \
           "v0.7: Stable Diffusion skill + Settings panel (4ed0a4b)" \
           "v0.8: Ingest skill + Ingestion tab (bee8609)" \
           "v0.9: OOM recovery, hw_supervisor, fleet resilience (0df5c7c)"; do
    add_item "$ver" "Historical reference — see git log for details." "historical"
done

echo ""
echo "Done. Check: https://github.com/users/SwiftWing21/projects/2/views/1"
