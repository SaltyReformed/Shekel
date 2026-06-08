#!/usr/bin/env bash
# PostToolUse (Write|Edit|MultiEdit): block when requirements.txt is modified.
# New runtime dependencies require developer approval (CLAUDE.md: "Dependencies
# pinned in requirements.txt -- no new packages without approval"). Reads the
# edited path from the stdin JSON payload (see _hooklib.sh). Exit 2 surfaces the
# change to Claude so it pauses and confirms rather than proceeding silently.

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_hooklib.sh"

FILE="$(hook_target_relpath)"
[ "$FILE" = "requirements.txt" ] || exit 0

{
    echo "=== DEPENDENCY CHANGE: requirements.txt modified ==="
    echo "New runtime dependencies require developer approval. Confirm this package"
    echo "(or version bump) was discussed before proceeding."
    echo ""
    git -C "${CLAUDE_PROJECT_DIR:-.}" diff -- requirements.txt 2>/dev/null
} >&2
exit 2
