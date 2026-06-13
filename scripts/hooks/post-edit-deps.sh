#!/usr/bin/env bash
# PostToolUse (Write|Edit|MultiEdit): block when a requirements file is modified.
# New dependencies require developer approval (CLAUDE.md: "Dependencies pinned in
# requirements.txt -- no new packages without approval"). Reads the edited path
# from the stdin JSON payload (see _hooklib.sh). Exit 2 surfaces the change to
# Claude so it pauses and confirms rather than proceeding silently.
#
# Matches requirements*.txt, not just requirements.txt (polyglot audit
# 2026-06-12, HOOK/SH-10): requirements-dev.txt is the file CI actually
# installs, so an unapproved dev package is exactly as much a supply-chain
# event as a runtime one.

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_hooklib.sh"

FILE="$(hook_target_relpath)" || exit 2
case "$FILE" in
    requirements*.txt) ;;
    *) exit 0 ;;
esac

{
    echo "=== DEPENDENCY CHANGE: $FILE modified ==="
    echo "New dependencies require developer approval (runtime AND dev: CI installs"
    echo "requirements-dev.txt). Confirm this package (or version bump) was discussed"
    echo "before proceeding."
    echo ""
    # hook_repo_root is local-assign + printf of a param expansion, so it
    # effectively always succeeds; the captured root only feeds a read-only
    # `git diff` whose output is informational before the unconditional exit 2.
    # shellcheck disable=SC2312
    git -C "$(hook_repo_root)" diff -- "$FILE" 2>/dev/null
} >&2
exit 2
