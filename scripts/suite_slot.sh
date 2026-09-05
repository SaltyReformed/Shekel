#!/usr/bin/env bash
# Shekel shared-suite slot: ATOMIC acquire/release.
#
# WHY (corrected 2026-09-01 -- the earlier reason here was WRONG):
#   TEMPLATE   is ISOLATED. Each worktree's .env sets TEST_TEMPLATE_DATABASE,
#              honoured by scripts/build_test_template.py:150 and
#              tests/conftest.py:89. Do NOT build a private one on top.
#   WORKER DBs are ISOLATED by TEST_DB_PREFIX.
#   POSTMASTER is SHARED, in two independent ways:
#     RESTART    `RESTART_TEST_DB=1 ./scripts/test.sh` runs `docker restart` before
#              pytest, which KILLS any in-flight run in any worktree.  Since
#              2026-09-04 that is OPT-IN, so an ordinary invocation no longer
#              carries this hazard -- but a gating run, which is exactly the
#              one that should ask for the restart, still does.
#     CONTENTION is real and measured: 859s contended vs 304s alone (2.8x), and
#              both results are void.  This one needs NO restart to bite, which
#              is why inverting the restart default did not make the slot
#              optional.
# A /proc PROBE is not a LOCK -- two sessions can both probe clear and both
# start. `mkdir` is atomic, so exactly one wins.
#
#   scripts/suite_slot.sh acquire <name>  -> exit 0 = yours; 1 = denied; 2 = a
#                                            pytest was already live, so the
#                                            lock auto-released and you may NOT run
#   scripts/suite_slot.sh release <name>  -> frees it
#   scripts/suite_slot.sh status          -> holder + LIVE probe
#
# THE EXEMPTION WAS ONE-DIRECTIONAL, AND THE INVERT MADE IT PARTLY SYMMETRIC.
# A targeted run is exempt from the ROLE hazard (it never touches cluster-
# scoped shekel_app) and, since the restart went opt-in, it no longer restarts
# the postmaster by accident. What survives:
#   * A gating run that sets RESTART_TEST_DB=1 still DESTROYS an in-flight
#     targeted run, whatever prefix either side uses. That direction is
#     unchanged, and it is the reason to acquire before a gating run.
#   * WITHOUT the flag the two are SYMMETRIC: neither kills the other, and
#     CONTENTION voids both results equally (859s vs 304s). That is not an
#     exemption for either side, it is a shared loss.
# So: always acquire before a gating run, whatever else is running -- but do
# not read "targeted runs are not gated" as "targeted runs are free".
# (--collect-only is genuinely exempt both ways: no DB writes, no template.)
#
# ACQUIRE CANNOT PROTECT A RUN ALREADY IN FLIGHT. It probes AFTER taking the
# lock and, if any pytest is live, releases what it just took and exits 2. The
# lock guards the START of a run; there is no retroactive move. If you find a
# run already going, coordinate with its owner -- do not start.
set -u

# The slot must be ONE directory shared by every worktree of this repository,
# and `--git-common-dir` is the only path that is identical from all of them
# (a linked worktree's own .git is a FILE pointing here). It replaced a
# hardcoded home path when this script moved into the repository: a
# version-controlled file may not name one machine's home, and two scripts
# disagreeing about this path would mean no mutual exclusion at all.
if [ -z "${SHEKEL_SLOT_DIR:-}" ]; then
    common=$(git rev-parse --git-common-dir 2>/dev/null) || {
        echo "not inside the Shekel repository, and SHEKEL_SLOT_DIR is unset" >&2
        exit 3
    }
    D="$(cd "$common" && pwd)/suite-slot.d"
else
    D="$SHEKEL_SLOT_DIR"
fi

# STALENESS IS NOT A PID CHECK. Callers invoke this from one-shot shells, so any
# pid recorded here is dead seconds later even while the holder's suite runs --
# an earlier version said "safe to release" over a LIVE suite. A lock is only
# possibly-stale when NOTHING is running anywhere AND it is older than a full
# suite (~7 min, so 15 gives margin). Even then: ask the holder, never assume.
staleness() {
    [ -d "$D" ] || return 0
    ep=$(sed -n 's/^epoch=//p' "$D/owner" 2>/dev/null)
    now=$(date +%s)
    age=$((now - ${ep:-$now}))
    live=$(probe)
    if [ -n "$live" ]; then
        echo "  Holder IS running (${age}s held). NOT stale:"
        # shellcheck disable=SC2001 # ${var//search/replace} cannot prefix EVERY line of a multi-line value; probe() emits one line per live pytest and sed is the construct that indents all of them
        echo "$live" | sed 's/^/    /'
    elif [ "$age" -gt 900 ]; then
        echo "  No pytest anywhere and held ${age}s (>15m): POSSIBLY stale."
        echo "  ASK THE HOLDER FIRST. Never release another session's lock unasked."
    else
        echo "  No pytest running yet, held ${age}s. Holder may be starting. NOT stale."
    fi
}
probe() {
    # shellcheck disable=SC2312 # the loop body is deliberately inside a `done | sort -u` pipeline, which masks every inner return by construction; each masked command is a per-pid FILTER whose non-zero status means "skip this pid" (an exited process, a non-pytest match) and is handled by the trailing continue, never an error to surface
    for p in $(
        pgrep -x python3 -x python 2>/dev/null || true
        pgrep -f "bin/pytest" 2>/dev/null || true
    ); do
        [ -r "/proc/$p/cmdline" ] || continue
        args=$(tr '\0' '\n' <"/proc/$p/cmdline")
        echo "$args" | grep -qxE 'pytest|.*/bin/pytest' || continue
        # --collect-only writes no databases and touches no template: NOT a conflict.
        echo "$args" | grep -qx -- '--collect-only' && continue
        echo "$p $(readlink "/proc/$p/cwd" 2>/dev/null)"
    done | sort -u
}
case "${1:-status}" in
    acquire)
        n="${2:?usage: acquire <name>}"
        if mkdir "$D" 2>/dev/null; then
            # shellcheck disable=SC2312 # pwd and date with literal format strings cannot meaningfully fail; all three values are stamped into the owner file for a human reading `status`, and none is fed to a downstream operation
            {
                echo "holder=$n"
                echo "cwd=$(pwd)"
                echo "since=$(date -Iseconds)"
                echo "epoch=$(date +%s)"
            } >"$D/owner"
            live=$(probe)
            if [ -n "$live" ]; then
                echo "WARNING: lock acquired but a pytest is ALREADY running:"
                echo "$live"
                rm -rf "$D"
                echo "Lock auto-RELEASED (you cannot use it). Do NOT run; coordinate first."
                exit 2
            fi
            echo "ACQUIRED by $n"
            exit 0
        fi
        echo "DENIED. Held by:"
        sed 's/^/  /' "$D/owner" 2>/dev/null
        staleness
        exit 1
        ;;
    release)
        n="${2:?usage: release <name>}"
        h=$(sed -n 's/^holder=//p' "$D/owner" 2>/dev/null)
        [ -d "$D" ] || {
            echo "not held"
            exit 0
        }
        # A MISMATCHED NAME STILL RELEASES. This warns and proceeds, so a typo
        # frees somebody else's lock: copy your own name exactly.
        [ "$h" = "$n" ] || echo "WARNING: held by '$h', released by '$n'"
        rm -rf "$D"
        echo "RELEASED by $n"
        exit 0
        ;;
    *)
        if [ -d "$D" ]; then
            echo "HELD:"
            sed 's/^/  /' "$D/owner" 2>/dev/null
            staleness
        else
            echo "FREE"
        fi
        l=$(probe)
        if [ -n "$l" ]; then
            echo "LIVE pytest:"
            # shellcheck disable=SC2001 # ${var//search/replace} cannot prefix EVERY line of a multi-line value; probe() emits one line per live pytest and sed is the construct that indents all of them
            echo "$l" | sed 's/^/  /'
        else
            echo "LIVE pytest: none"
        fi
        ;;
esac
