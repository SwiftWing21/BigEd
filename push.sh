#!/usr/bin/env bash
#
# push.sh — Clean, stage, commit, and push to BigEd remote.
#
# Usage:
#   ./push.sh                      # auto-generates commit message from changes
#   ./push.sh "my commit message"  # custom commit message
#   ./push.sh --init               # first-time setup: add remote + initial commit
#   ./push.sh --clean-only         # just purge ignored files from index, no commit
#
# What it does:
#   1. Removes any cached files that are now in .gitignore (safe — doesn't touch working tree)
#   2. Stages all untracked + modified files
#   3. Shows a summary of what will be committed
#   4. Commits and pushes to origin/main
#
# Re-run after editing .gitignore to purge newly-ignored files from the repo.

set -euo pipefail
cd "$(dirname "$0")"

REMOTE_URL="git@github.com:SwiftWing21/BigEd.git"
BRANCH="main"
SSH_KEY="$HOME/.ssh/id_ed25519"

# ── SSH agent ───────────────────────────────────────────────────────────────
if ! ssh-add -l &>/dev/null; then
    eval "$(ssh-agent -s)" > /dev/null
    ssh-add "$SSH_KEY"
fi

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
GOLD='\033[0;33m'
DIM='\033[0;90m'
RESET='\033[0m'

info()  { echo -e "${GREEN}[+]${RESET} $*"; }
warn()  { echo -e "${GOLD}[!]${RESET} $*"; }
err()   { echo -e "${RED}[✗]${RESET} $*"; }
dim()   { echo -e "${DIM}    $*${RESET}"; }

# ── First-time setup ───────────────────────────────────────────────────────
if [[ "${1:-}" == "--init" ]]; then
    info "First-time setup"

    # Ensure we're on the right branch
    current=$(git branch --show-current 2>/dev/null || echo "")
    if [[ "$current" != "$BRANCH" ]]; then
        if git show-ref --verify --quiet "refs/heads/$BRANCH" 2>/dev/null; then
            git checkout "$BRANCH"
        else
            git checkout -b "$BRANCH"
        fi
        info "Switched to branch $BRANCH"
    fi

    # Add remote if missing
    if ! git remote get-url origin &>/dev/null; then
        git remote add origin "$REMOTE_URL"
        info "Remote 'origin' added: $REMOTE_URL"
    else
        existing=$(git remote get-url origin)
        if [[ "$existing" != "$REMOTE_URL" ]]; then
            warn "Remote 'origin' exists but points to: $existing"
            warn "Expected: $REMOTE_URL"
            echo -n "Update remote? [y/N] "
            read -r ans
            if [[ "$ans" =~ ^[Yy]$ ]]; then
                git remote set-url origin "$REMOTE_URL"
                info "Remote updated"
            fi
        fi
    fi

    # Purge ignored files from index
    info "Purging cached files matching .gitignore..."
    git rm -r --cached --quiet . 2>/dev/null || true
    git add .
    ignored_count=$(git diff --cached --name-only --diff-filter=D | wc -l)
    dim "Removed $ignored_count ignored files from index"

    # Stage everything and commit
    git add -A
    info "Staging all files..."

    file_count=$(git diff --cached --name-only | wc -l)
    if [[ "$file_count" -eq 0 ]]; then
        warn "Nothing to commit"
        exit 0
    fi

    info "Committing $file_count files..."
    git commit -m "Initial commit — Fleet Manager App baseline

Fleet Control launcher, 8-agent fleet system, autoresearch pipeline.
Working state: all fleet bugs fixed, thread-safe GUI, unified installer."

    info "Pushing to origin/$BRANCH..."
    git push -u origin "$BRANCH"

    echo ""
    info "Done! Repo live at: https://github.com/SwiftWing21/BigEd"
    exit 0
fi

# ── Clean-only mode ────────────────────────────────────────────────────────
if [[ "${1:-}" == "--clean-only" ]]; then
    info "Purging cached files matching .gitignore..."
    git rm -r --cached --quiet . 2>/dev/null || true
    git add .
    removed=$(git diff --cached --name-only --diff-filter=D)
    if [[ -z "$removed" ]]; then
        info "Index is clean — no ignored files cached"
    else
        count=$(echo "$removed" | wc -l)
        info "Removed $count files from index:"
        echo "$removed" | while read -r f; do dim "$f"; done
        echo ""
        echo -n "Commit this cleanup? [Y/n] "
        read -r ans
        if [[ "${ans:-y}" =~ ^[Yy]$ ]]; then
            git commit -m "chore: purge ignored files from index"
            info "Committed. Run ./push.sh to push."
        fi
    fi
    exit 0
fi

# ── Normal push flow ──────────────────────────────────────────────────────
# Step 1: Purge any newly-ignored files from index
git rm -r --cached --quiet . 2>/dev/null || true
git add .
purged=$(git diff --cached --name-only --diff-filter=D | wc -l)
if [[ "$purged" -gt 0 ]]; then
    warn "Purged $purged newly-ignored files from index"
fi

# Step 2: Stage all changes
git add -A

# Step 3: Check if there's anything to commit
if git diff --cached --quiet; then
    info "Nothing to commit — working tree clean"
    exit 0
fi

# Step 4: Show summary
echo ""
info "Changes to commit:"
echo ""
git diff --cached --stat
echo ""

# Step 5: Build commit message
if [[ -n "${1:-}" ]]; then
    MSG="$1"
else
    info "Generating smart commit message via local AI..."
    # Feed the first 500 lines of the diff to Ollama for a smart commit message
    AI_MSG=$(python3 -c '
import sys, json, urllib.request
diff = sys.stdin.read()
prompt = "Write a concise, conventional git commit message for this diff. Output ONLY the commit message, no markdown formatting, no explanations.\\n\\nDIFF:\\n" + diff
data = json.dumps({"model": "qwen3:8b", "prompt": prompt, "stream": False}).encode("utf-8")
req = urllib.request.Request("http://localhost:11434/api/generate", data=data, headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        resp = json.loads(r.read().decode())["response"]
        print(resp.strip().strip("`").strip("\""))
except Exception:
    pass
' <<< "$(git diff --cached | head -n 500)")

    if [[ -n "$AI_MSG" && "$AI_MSG" != "null" ]]; then
        MSG="$AI_MSG"
    else
        # Fallback to basic file-counting if Ollama is offline or fails
        added=$(git diff --cached --name-only --diff-filter=A | wc -l)
        modified=$(git diff --cached --name-only --diff-filter=M | wc -l)
        deleted=$(git diff --cached --name-only --diff-filter=D | wc -l)

        parts=()
        [[ "$added" -gt 0 ]]    && parts+=("${added} added")
        [[ "$modified" -gt 0 ]] && parts+=("${modified} modified")
        [[ "$deleted" -gt 0 ]]  && parts+=("${deleted} deleted")

        summary=$(IFS=", "; echo "${parts[*]}")

        areas=()
        git diff --cached --name-only | grep -q "^fleet/"           && areas+=("fleet")
        git diff --cached --name-only | grep -q "^Max Stuff/"       && areas+=("launcher")
        git diff --cached --name-only | grep -q "^autoresearch/"    && areas+=("autoresearch")
        area_str=""
        [[ ${#areas[@]} -gt 0 ]] && area_str="[$(IFS=","; echo "${areas[*]}")] "

        MSG="${area_str}Update: ${summary}"
    fi
fi

echo -e "${DIM}Commit message: ${MSG}${RESET}"
echo ""
echo -n "Commit and push? [Y/n] "
read -r ans
if [[ ! "${ans:-y}" =~ ^[Yy]$ ]]; then
    warn "Aborted"
    exit 1
fi

# Step 6: Commit and push
git commit -m "$MSG"
git push origin "$BRANCH"

echo ""
info "Pushed to origin/$BRANCH"
