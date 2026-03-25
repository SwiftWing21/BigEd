#!/usr/bin/env bash
# ── public_sync.sh ────────────────────────────────────────────────────────────
# Sync curated changes from private repo (origin) to public repo (upstream).
#
# Usage:
#   bash scripts/public_sync.sh              # Dry run (shows what would sync)
#   bash scripts/public_sync.sh --push       # Actually push to upstream
#
# Workflow:
#   1. Creates a clean 'public-sync' branch from main
#   2. Removes all internal/dev-only files
#   3. Commits the clean state
#   4. Pushes to upstream/main (if --push)
#   5. Cleans up the sync branch
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

PUSH=false
if [[ "${1:-}" == "--push" ]]; then
    PUSH=true
fi

# ── Internal files/dirs to EXCLUDE from public repo ──────────────────────────
# These are removed from the sync branch before pushing upstream.
EXCLUDE_PATTERNS=(
    # Worktree / temp artifacts
    ".clone/"
    "ziXHiPfz"
    "model-manager.plugin"
    "repo-descriptions.txt"

    # Internal design assets
    "figma-export/"

    # Deployment scripts
    "push.sh"

    # AI agent tooling
    ".claude/skills/"
    "docs/superpowers/"

    # Rust rewrite (not consumer-ready)
    "biged-rs/"

    # Session/handoff files
    "SESSION_HANDOFF.md"
    "GEMINI_DOC_CLEANUP.md"

    # Temp fleet files
    "fleet/tmp*.json"

    # Cross-project docs that landed here by mistake
    "BigEd_Architecture_Report.docx"
    "BookKeeper_Architecture.docx"
    "BookKeeper_Architecture.pdf"
    "BookKeeper_Language_Consensus.md"

    # Knowledge runtime dirs (freshness, outcomes, etc.)
    "fleet/knowledge/freshness/"
    "fleet/knowledge/outcomes/"
    "fleet/knowledge/prompt_optimization/"
    "fleet/knowledge/sync/"
)

echo "=== BigEd Public Sync ==="
echo ""

# ── Preflight checks ─────────────────────────────────────────────────────────
if ! git remote get-url upstream &>/dev/null; then
    echo "ERROR: No 'upstream' remote configured."
    echo "Add it: git remote add upstream https://github.com/SwiftWing21/BigEd.git"
    exit 1
fi

# Check for uncommitted changes
if ! git diff-index --quiet HEAD -- 2>/dev/null; then
    echo "WARNING: You have uncommitted changes. Stashing them."
    git stash push -m "public-sync stash"
    STASHED=true
else
    STASHED=false
fi

CURRENT_BRANCH=$(git branch --show-current)

# ── Create clean sync branch ────────────────────────────────────────────────
echo "Creating sync branch from main..."
git checkout main --quiet
git branch -D public-sync 2>/dev/null || true
git checkout -b public-sync --quiet

# ── Remove internal files ───────────────────────────────────────────────────
removed=0
for pattern in "${EXCLUDE_PATTERNS[@]}"; do
    if git ls-files "$pattern" 2>/dev/null | grep -q .; then
        git rm -r --cached "$pattern" --quiet 2>/dev/null || true
        echo "  Excluded: $pattern"
        ((removed++)) || true
    fi
done

echo ""
echo "Excluded $removed items from sync."

# ── Commit clean state ──────────────────────────────────────────────────────
if git diff --cached --quiet; then
    echo "Nothing to sync — public branch already clean."
else
    git commit -m "chore: prepare public release — remove internal files" --quiet
    echo "Clean commit created."
fi

# ── Show diff summary ───────────────────────────────────────────────────────
echo ""
echo "Commits to sync to upstream:"
git log upstream/main..public-sync --oneline 2>/dev/null || echo "  (upstream/main not fetched — run: git fetch upstream)"
echo ""

# ── Push or dry-run ─────────────────────────────────────────────────────────
if $PUSH; then
    echo "Pushing to upstream/main..."
    git push upstream public-sync:main
    echo ""
    echo "PUBLIC REPO UPDATED."
else
    echo "DRY RUN — not pushing. Run with --push to publish:"
    echo "  bash scripts/public_sync.sh --push"
fi

# ── Cleanup ─────────────────────────────────────────────────────────────────
git checkout "$CURRENT_BRANCH" --quiet
git branch -D public-sync --quiet 2>/dev/null || true

if $STASHED; then
    git stash pop --quiet
fi

echo ""
echo "Done. Back on branch: $CURRENT_BRANCH"
