#!/usr/bin/env bash
# ── public_cleanup.sh ─────────────────────────────────────────────────────────
# Remove internal/dev-only files from git tracking before pushing to upstream.
# Run from project root: bash scripts/public_cleanup.sh
#
# This does NOT delete files from disk — only removes them from git's index
# so they won't appear in the public repo. Files remain in your working directory.
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

echo "=== BigEd Public Repo Cleanup ==="
echo ""

# ── Files/dirs to remove from public tracking ────────────────────────────────
REMOVE_LIST=(
    # Worktree artifacts
    ".clone/"

    # Temp/garbage files
    "ziXHiPfz"
    "model-manager.plugin"
    "repo-descriptions.txt"

    # Internal design assets
    "figma-export/"

    # Deployment script (exposes internal workflow)
    "push.sh"

    # AI agent configuration (competitive advantage)
    ".claude/skills/"

    # Internal planning & specs
    "docs/superpowers/"

    # Rust rewrite (not consumer-facing yet)
    "biged-rs/"

    # Session coordination
    "SESSION_HANDOFF.md"

    # Temp fleet files
    "fleet/tmp*.json"

    # Generated docs (internal)
    "BigEd_Architecture_Report.docx"
    "BookKeeper_Architecture.docx"
    "BookKeeper_Architecture.pdf"
    "BookKeeper_Language_Consensus.md"
    "GEMINI_DOC_CLEANUP.md"
)

removed=0
for item in "${REMOVE_LIST[@]}"; do
    # Check if tracked by git
    if git ls-files --error-unmatch "$item" &>/dev/null 2>&1; then
        echo "  Removing from git: $item"
        git rm -r --cached "$item" 2>/dev/null || true
        ((removed++))
    elif [ -e "$item" ]; then
        echo "  Already untracked: $item (exists on disk only)"
    fi
done

echo ""
echo "Removed $removed items from git tracking."
echo ""
echo "Next steps:"
echo "  1. Review changes: git status"
echo "  2. Commit: git commit -m 'chore: remove internal files from public tracking'"
echo "  3. Push to upstream: git push upstream main"
echo ""
echo "Files remain on disk — only removed from git index."
