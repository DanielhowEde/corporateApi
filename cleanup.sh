#!/bin/bash
# cleanup.sh - Remove JSON files older than 1 day from data directories
#
# Usage: ./cleanup.sh [--dry-run]
#
# Cleans the following directories for both corporate and low-side services:
#   - data/messages/  (received messages)
#   - data/tmp/       (temporary files from failed writes)
#   - data/errors/    (error records)
#
# Schedule with cron to run daily:
#   0 2 * * * /path/to/API-DMZ-API/cleanup.sh >> /var/log/dmz-cleanup.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DRY_RUN=false

if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=true
    echo "[DRY RUN] No files will be deleted."
fi

echo "=== DMZ API Cleanup - $(date -Iseconds) ==="

cleanup_dir() {
    local dir="$1"
    local label="$2"

    if [ ! -d "$dir" ]; then
        echo "  SKIP $label ($dir does not exist)"
        return
    fi

    local count
    count=$(find "$dir" -name "*.json" -type f -mtime +1 | wc -l)

    if [ "$count" -eq 0 ]; then
        echo "  OK   $label - no files older than 1 day"
        return
    fi

    if [ "$DRY_RUN" = true ]; then
        echo "  WOULD DELETE $count file(s) from $label"
        find "$dir" -name "*.json" -type f -mtime +1 -print
    else
        find "$dir" -name "*.json" -type f -mtime +1 -delete -print
        echo "  DELETED $count file(s) from $label"
    fi
}

# Corporate side
echo ""
echo "--- Corporate ---"
cleanup_dir "$SCRIPT_DIR/corporate/data/messages" "corporate/messages"
cleanup_dir "$SCRIPT_DIR/corporate/data/tmp"      "corporate/tmp"
cleanup_dir "$SCRIPT_DIR/corporate/data/errors"    "corporate/errors"

# Low-side
echo ""
echo "--- Low-Side ---"
cleanup_dir "$SCRIPT_DIR/low_side/data/messages"   "low_side/messages"
cleanup_dir "$SCRIPT_DIR/low_side/data/tmp"         "low_side/tmp"
cleanup_dir "$SCRIPT_DIR/low_side/data/errors"      "low_side/errors"

# Mock gateway received
echo ""
echo "--- Mock Gateway ---"
cleanup_dir "$SCRIPT_DIR/mock_gateway/received"     "mock_gateway/received"

echo ""
echo "=== Cleanup complete ==="
