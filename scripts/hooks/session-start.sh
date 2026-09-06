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
#
# **IT ALSO REPORTS STANDING CONSTRAINTS, and that is why** (developer ruling
# 2026-09-05, after it cost two sessions in one evening).  A constraint that
# lives only in coordinator messages is INVISIBLE to any session that clears
# its context, and clearing context is ordinary.  On 2026-09-05 the shared
# main checkout was pinned to a release branch: one session cleared, looked at
# the tree, and concluded IT was driving the release -- because every fact the
# tree offers is about the CHECKOUT (its branch, its HEAD, its slot label) and
# none is about the session reading them.  A second session could not learn
# why its browser pass was impossible, the same pin being the cause.  Neither
# could have found out by looking, and one of them is worktree-isolated and
# could not look at all.
#
# TWO HALVES, because neither covers the other:
#   DERIVED -- the release pin is read off `git worktree list`, so it cannot
#     go stale and nobody has to remember to write it.
#   STATED  -- `docs/plans/STANDING.md` carries what the tree does not imply,
#     such as a service bind-mounting a pinned checkout.  The coordinator
#     writes it and clears it; empty is the normal state, and staying empty is
#     what keeps it READ rather than skimmed.

set -u

branch="$(git -C "$PWD" rev-parse --abbrev-ref HEAD 2>/dev/null)" || branch="not a git checkout"
toplevel="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null)" || toplevel="$PWD"

echo "Shekel session context (SessionStart hook):"
echo "- checkout: ${toplevel}, branch: ${branch}"
echo "- coordination doctrine: CLAUDE.md, Multi-session operation (stated once there, not here)"

# DERIVED: is the SHARED MAIN checkout pinned to a release branch?  Read from
# `git worktree list`, which answers for every worktree from inside any one of
# them -- no cross-directory git call, which a worktree-isolated session's
# harness would refuse anyway.  The main worktree is the FIRST entry.
worktrees="$(git -C "$PWD" worktree list 2>/dev/null)" || worktrees=""
main_line="$(printf '%s\n' "${worktrees}" | head -n 1)" || main_line=""
if [ -n "${main_line}" ]; then
    main_path="${main_line%% *}"
    main_branch="${main_line##*[}"
    main_branch="${main_branch%]*}"
    case "${main_branch}" in
        release/*)
            echo "- STANDING: the shared main checkout ${main_path} is on"
            echo "    ${main_branch} for a release. Do NOT check out, branch from, or"
            echo "    merge into it. Anything bind-mounting that path follows its branch."
            ;;
    esac
fi

# STATED: constraints the tree does not imply.  Absent or empty is normal.
standing="${toplevel}/docs/plans/STANDING.md"
if [ -r "$standing" ] && [ -s "$standing" ]; then
    # A constraint is a BULLET plus the indented lines that continue it, up to
    # the next blank line.  Everything else in the file is guidance for whoever
    # edits it.  Two earlier spellings of this filter were wrong in opposite
    # directions: "not a comment" printed the whole guidance block, and
    # bullet-lines-only printed each constraint truncated at its first wrap --
    # a half-sentence being worse than nothing, because it reads as complete.
    body="$(awk '/^[[:space:]]*-[[:space:]]/ {p=1} /^[[:space:]]*$/ {p=0} p' \
        "$standing" 2>/dev/null)" || body=""
    if [ -n "${body}" ]; then
        echo "- STANDING CONSTRAINTS (docs/plans/STANDING.md):"
        printf '%s\n' "${body}" | sed 's/^/    /'
    fi
fi

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
