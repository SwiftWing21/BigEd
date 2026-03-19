#!/usr/bin/env bash
# BigEd CC — backup non-tracked runtime data (databases, knowledge, secrets registry)
# Usage: bash scripts/backup.sh
# Keeps last 10 backups; older are pruned automatically.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="$HOME/BigEd-backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

echo "Backing up to $BACKUP_DIR ..."

# Databases
cp "$PROJECT_DIR/fleet/fleet.db"              "$BACKUP_DIR/" 2>/dev/null && echo "  fleet.db" || echo "  fleet.db (not found)"
cp "$PROJECT_DIR/fleet/rag.db"                "$BACKUP_DIR/" 2>/dev/null && echo "  rag.db" || echo "  rag.db (not found)"
cp "$PROJECT_DIR/BigEd/launcher/data/tools.db" "$BACKUP_DIR/" 2>/dev/null && echo "  tools.db" || echo "  tools.db (not found)"

# Knowledge artifacts
if [ -d "$PROJECT_DIR/fleet/knowledge" ]; then
    cp -r "$PROJECT_DIR/fleet/knowledge/" "$BACKUP_DIR/knowledge/"
    echo "  knowledge/"
fi

# Keys registry (not secrets themselves)
cp "$PROJECT_DIR/fleet/keys_registry.toml" "$BACKUP_DIR/" 2>/dev/null && echo "  keys_registry.toml" || true

echo ""
echo "Backup complete: $BACKUP_DIR"
du -sh "$BACKUP_DIR" 2>/dev/null || true

# Prune old backups (keep last 10)
ls -dt "$HOME/BigEd-backups/"*/ 2>/dev/null | tail -n +11 | xargs rm -rf 2>/dev/null || true
echo "Pruned old backups (keeping last 10)"
