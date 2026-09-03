#!/usr/bin/env bash
# PreToolUse (Bash): route `gh pr create` / `gh pr merge` through the
# coordinator session (developer ruling 2026-09-02: any session may push to
# back up work; PRs and merges are coordinated when several sessions run).
#
# Mechanism: guard-migrations.sh's proven "ask" pattern -- the harness pauses
# the command and puts the decision in front of the developer, rather than
# hard-denying. The session the developer designates as coordinator exports
# SHEKEL_PR_COORDINATOR=1 and is never prompted. This is an advisory control,
# not a security boundary: any session COULD set the variable, and doing so
# against the doctrine leaves a visible trace in its transcript.

set -uo pipefail

if [ -n "${SHEKEL_PR_COORDINATOR:-}" ]; then
    exit 0
fi

# The matcher is "Bash", so this hook runs on EVERY Bash call in every
# session. A raw-substring pre-filter keeps the common case to one shell
# `case` with no python3 spawn: the two gated verbs contain no JSON-escaped
# characters, so a payload without the literal substring cannot carry either
# command, and only a candidate pays for the precise parse below.
payload="$(cat)"
case "$payload" in
    *"gh pr create"* | *"gh pr merge"*) ;;
    *) exit 0 ;;
esac

cmd="$(printf '%s' "$payload" | python3 -c '
import json
import sys

try:
    payload = json.load(sys.stdin)
except ValueError as exc:
    print(f"hook payload is not valid JSON: {exc}", file=sys.stderr)
    sys.exit(1)
print(payload.get("tool_input", {}).get("command", ""))
')" || {
    echo "guard-pr-actions: could not read the Bash command from stdin; failing closed" \
        "(docs/coding-standards.md: gates fail with a clear message, never silently open)." >&2
    exit 2
}

case "$cmd" in
    *"gh pr create"* | *"gh pr merge"*)
        REASON="PRs and merges are coordinated through the coordinator session when \
multiple sessions are active (developer ruling 2026-09-02; CLAUDE.md, Multi-session \
operation). Approve if this session is the coordinator or is acting with its \
agreement; deny and message the coordinator otherwise."
        python3 -c '
import json
import sys

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": sys.argv[1],
    }
}))
' "$REASON"
        ;;
esac
exit 0
