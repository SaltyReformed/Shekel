#!/usr/bin/env bash
# SessionStart: print the multi-session context every Shekel session needs
# before its first action -- where it stands, who coordinates merges, and what
# the plan of record says is next.
#
# It used to report the shared suite slot too.  ``balance:X-br-4`` deleted the
# slot: ``scripts/test.sh`` gives every run a private cluster, so there is no
# shared postmaster left for two runs to serialise against.
#
# stdout on exit 0 is added to the session's context (the SessionStart hook
# contract). A status hook must never block a session, so every probe fails
# soft and the script always exits 0. Deliberately reads $PWD, not
# $CLAUDE_PROJECT_DIR: the latter resolves to the PRIMARY checkout whatever
# worktree the session runs in (measured by two sessions, 2026-08-29), and
# this hook reports the session's OWN checkout.

set -u

branch="$(git -C "$PWD" rev-parse --abbrev-ref HEAD 2>/dev/null)" || branch="not a git checkout"
toplevel="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null)" || toplevel="$PWD"

echo "Shekel session context (SessionStart hook):"
echo "- checkout: ${toplevel}, branch: ${branch}"
echo "- coordination doctrine: CLAUDE.md, Multi-session operation (stated once there, not here)"

steps="${toplevel}/docs/plans/steps.md"
if [ -r "$steps" ]; then
    matches="$(grep -c -E '\| *#1 *\|' "$steps" 2>/dev/null)" || matches=0
    next="$(grep -m 1 -E '\| *#1 *\|' "$steps" 2>/dev/null)" || true
    if [ -n "${next:-}" ]; then
        echo "- next step per docs/plans/steps.md (rank #1): ${next}"
        if [ "${matches}" -gt 1 ]; then
            echo "  WARNING: ${matches} rows carry rank #1 -- an identity class shares the rank,"
            echo "  or a merge duplicated it; read the table itself, not just this line."
        fi
    fi
fi

exit 0
