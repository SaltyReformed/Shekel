#!/usr/bin/env bash
# PostToolUse: Warn when requirements.txt is modified.

FILE="$1"

[[ "$FILE" != "requirements.txt" ]] && exit 0

echo "=== DEPENDENCY CHANGE DETECTED ==="
echo "requirements.txt was modified. New dependencies require developer approval."
echo "Verify: is this a new package or a version bump? Was it discussed?"
echo ""
git diff requirements.txt 2>/dev/null || diff /dev/null "$FILE"
exit 1
