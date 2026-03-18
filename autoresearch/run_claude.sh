#!/bin/bash
# Launch Claude Code (subscription auth) in the autoresearch Education directory.
# Runs inside WSL Ubuntu with nvm-managed Node.

source "$HOME/.nvm/nvm.sh"

AUTODIR="/mnt/c/Users/max/Projects/Education/autoresearch"
cd "$AUTODIR" || { echo "Directory not found: $AUTODIR"; exit 1; }

echo "Claude Code $(claude --version)"
echo "Auth: $(claude auth status 2>/dev/null | grep -E 'email|subscriptionType|loggedIn' | tr -d '\",' | xargs)"
echo "Working dir: $AUTODIR"
echo ""

exec claude "$@"
