#!/bin/bash
# Shekel Budget App -- Test Runner Wrapper
#
# Execs into pytest with whatever arguments were passed, forwarded
# verbatim.  On request it first restarts the local test-db
# container:
#
#     ./scripts/test.sh                           # full suite
#     ./scripts/test.sh tests/test_routes/...     # targeted run
#     ./scripts/test.sh -n 0 -x                   # pass-through flags
#     RESTART_TEST_DB=1 ./scripts/test.sh         # + hygiene restart
#
# Why a restart exists at all
#     Phase 3b per-test isolation drops and re-clones a per-worker
#     database for every test.  Over many back-to-back suite runs the
#     postmaster accumulates shared-memory state (sinval queue,
#     syscache, relcache invalidations) that VACUUM / CHECKPOINT
#     cannot reset -- only restarting the postmaster does.  On a
#     long-lived container, full-suite wall-clock drifts linearly
#     (~62 s baseline, +2-3 s per suite run, reaching ~220 s after
#     ~50 runs / ~37 h uptime).  A ``docker restart`` restores the
#     baseline.  See ``docs/testing-standards.md`` "Catalog
#     fragmentation and the test-runner wrapper" for the full
#     analysis.
#
#     TREAT THOSE FIGURES AS A SHAPE, NOT A SCALE.  They were
#     measured at ``c1e9c775`` (2026-05-20) against ~5,504 tests,
#     when the clone used ``STRATEGY FILE_COPY``; the suite is
#     ~11,788 now (docs/testing-standards.md, measured 2026-08-30,
#     so roughly double) and the clone uses ``WAL_LOG``
#     (tests/conftest.py).  The DRIFT is real; every absolute number
#     describing it is from a different suite on a different clone
#     strategy, and re-measuring it belongs to the work that removes
#     the shared cluster entirely.
#
#     One number is not merely stale but self-refuting, which is why
#     ``docs/testing-standards.md`` withdraws it rather than
#     re-pinning it: a CREATE/DROP round-trip "past ~15 ms" was once
#     offered as the signal to restart, and the same table reads
#     14.6 ms on a FRESHLY restarted container and 15.6 ms after one
#     run.  It fired immediately or never.
#
# Why it is OPT-IN, and was not always (inverted 2026-09-04)
#     The restart used to happen on EVERY invocation unless
#     ``SKIP_DB_RESTART`` was set -- opt-IN to safety.  Two
#     independent reasons make opt-OUT the wrong default, and only
#     the second one is a shared-cluster artifact:
#
#     1. The cost is FIXED and the benefit is PROPORTIONAL to how
#        much DDL the run does.  One test is one drop+reclone cycle,
#        so a targeted run of a few dozen tests contributes a few
#        dozen cycles of drift and a full suite contributes one per
#        test in the suite -- yet both paid the SAME seconds.  The
#        restart's share of a run therefore grows without bound as
#        the run shrinks, which is backwards: the runs causing the
#        least drift were charged the most for it.  This survives any
#        change to how the cluster is hosted.
#     2. The container is SHARED by every worktree.  ``docker
#        restart`` terminates every backend, so an opt-out default
#        made every invocation -- including a three-second targeted
#        one -- a potential killer of a peer checkout's in-flight
#        suite.  The live-backend probe below narrows that window but
#        is a race, not a lock; see ``.claude/rules/testing.md``.
#
#     So: reach for ``RESTART_TEST_DB=1`` before a gating full-suite
#     run, and otherwise read the container state this wrapper prints
#     when it skips the restart -- uptime when the container is up,
#     and the exit status when it is not.
#
# Restarts ONLY when ``RESTART_TEST_DB`` is set to a non-empty value, and
# even then skips -- loudly -- when:
#   * ``docker`` is not on PATH, or the container does not exist
#     (CI, fresh checkout) -- runs pytest directly so the same
#     wrapper works in both environments.
#   * Another run's backends are live on the container (see the
#     probe below).
#
# Environment variables read:
#     TEST_DB_CONTAINER  Test-db container name.  Default:
#                        ``shekel-dev-test-db``.  ENVIRONMENT ONLY --
#                        alone among this wrapper's four ``TEST_*``
#                        knobs it is NOT read from ``.env``, so a
#                        checkout pointing at its own container must
#                        export it per shell while its siblings live
#                        in the dotenv.  Left as-is deliberately: the
#                        resolution order would have to change, and
#                        the per-run-container work deletes this
#                        variable rather than extending it.
#                        Prefixed ``TEST_``
#                        so the name itself says WHICH database it
#                        means, because the bare
#                        ``DB_CONTAINER`` it used to answer to is
#                        ALSO read -- from the same environment -- by
#                        ``scripts/backup.sh``, ``restore.sh`` and
#                        ``verify_backup.sh``, where it means the
#                        PRODUCTION container.  One exported value
#                        therefore aimed a test-db restart at
#                        production, or a `restore.sh` DROP at a test
#                        container.  ``deploy/shekel-deploy.sh`` had
#                        already dodged this with
#                        ``SHEKEL_DB_CONTAINER``; this follows it.
#     TEST_DATABASE_URL  Set in ``.env``; used to derive
#                        ``TEST_ADMIN_DATABASE_URL`` when the latter
#                        is not already set.  No-op if both are
#                        already in the environment.
#     TEST_TEMPLATE_DATABASE  Optional; passed through from ``.env``
#                        for parallel-checkout template isolation
#                        (see tests/conftest.py).  Absent = default
#                        ``shekel_test_template``.
#     TEST_DB_PREFIX     Optional; passed through from ``.env``.
#                        Renames the PER-WORKER databases (see
#                        tests/conftest.py).  Absent = default
#                        ``shekel_test``.
#     RESTART_TEST_DB    See above.  ``1``/``true``/``yes``/``on``
#                        restart; unset, empty, ``0``/``false``/
#                        ``no``/``off`` do not; anything else is
#                        REFUSED with exit 2 rather than guessed.
#                        Deliberately not a bare presence flag: the
#                        spelling it replaced was opt-OUT, so a
#                        careless ``=0`` used to land on "skip" and
#                        would now land on "restart the container
#                        every worktree shares".  The rename must not
#                        turn a harmless typo into a destructive
#                        one.
#     READINESS_TIMEOUT_SECONDS  Cap on the post-restart
#                        ``pg_isready`` wait.  Default: 15.
#     PYTEST_MARKER_EXPR Marker expression handed to pytest.
#                        Default: ``not docker`` (see the note above
#                        the ``exec`` at the bottom of this file).
#                        An explicit ``-m`` in the caller's arguments
#                        wins, because pytest keeps the last one.
#
# Exit codes:
#     Whatever pytest returns, in the ordinary case.  Two bootstrap
#     failures exit 2 without running pytest: a container that does
#     not answer ``pg_isready`` within READINESS_TIMEOUT_SECONDS
#     after a requested restart, and an unrecognised
#     ``RESTART_TEST_DB`` value.  Other non-pytest statuses are
#     possible and are docker's or the shell's, not this script's --
#     ``docker restart`` failing under ``set -e`` surfaces docker's
#     status, and ``exec pytest`` with pytest off PATH gives 127.
#     A missing docker or a missing container is NOT a failure: that
#     is the CI / fresh-checkout path and the wrapper execs pytest
#     anyway.

set -euo pipefail

TEST_DB_CONTAINER="${TEST_DB_CONTAINER:-shekel-dev-test-db}"
READINESS_TIMEOUT_SECONDS="${READINESS_TIMEOUT_SECONDS:-15}"

# Read TEST_DATABASE_URL from .env if present and not already in
# the environment.  We do NOT ``source .env`` -- the file is a
# dotenv-style key=value document (read by python-dotenv at app
# startup) and may contain values with unquoted spaces (display
# names, comments) that the shell would mis-parse.  Extract only
# the one line we need, value-after-first-=, single match.
_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -z "${TEST_DATABASE_URL:-}" ] && [ -f "${_REPO_ROOT}/.env" ]; then
    # Resolved via BASH_SOURCE, not the invoker's cwd, so a run from
    # outside the repo root still finds the dotenv (OPS/SH-26).
    # ``|| true``: under ``set -euo pipefail`` a grep miss (the key
    # absent from .env -- a legal state) would otherwise abort the
    # whole runner via the command substitution's exit status.
    _env_value="$(grep -E '^TEST_DATABASE_URL=' "${_REPO_ROOT}/.env" | head -n1 | cut -d= -f2- || true)"
    if [ -n "$_env_value" ]; then
        export TEST_DATABASE_URL="$_env_value"
    fi
    unset _env_value
fi

# Pass TEST_TEMPLATE_DATABASE through from .env the same way (see
# tests/conftest.py: a parallel checkout whose migration head differs
# from another live checkout's sets its own template name so neither
# suite clones the other's schema).  Environment wins over .env.
# Absence is the NORMAL case (single-checkout default), hence ``|| true``.
if [ -z "${TEST_TEMPLATE_DATABASE:-}" ] && [ -f "${_REPO_ROOT}/.env" ]; then
    _env_value="$(grep -E '^TEST_TEMPLATE_DATABASE=' "${_REPO_ROOT}/.env" | head -n1 | cut -d= -f2- || true)"
    if [ -n "$_env_value" ]; then
        export TEST_TEMPLATE_DATABASE="$_env_value"
    fi
    unset _env_value
fi

# Pass TEST_DB_PREFIX through the same way.  It renames the PER-WORKER
# databases (tests/conftest.py), and it is the other half of the same story:
# TEST_TEMPLATE_DATABASE isolates what a run clones FROM, this isolates what
# it clones INTO.  Without it two checkouts both claim
# ``shekel_test_gw0``..``gw11`` -- both default to ``-n 12`` -- and the second
# run dies in hundreds of setup errors that read like a code regression.
if [ -z "${TEST_DB_PREFIX:-}" ] && [ -f "${_REPO_ROOT}/.env" ]; then
    _env_value="$(grep -E '^TEST_DB_PREFIX=' "${_REPO_ROOT}/.env" | head -n1 | cut -d= -f2- || true)"
    if [ -n "$_env_value" ]; then
        export TEST_DB_PREFIX="$_env_value"
    fi
    unset _env_value
fi

# Derive TEST_ADMIN_DATABASE_URL from TEST_DATABASE_URL when the
# admin URL is not explicitly set.  Both URLs share host, port and
# credentials; only the trailing database name differs (the admin
# DSN connects to ``postgres`` so it can DROP / CREATE the per-
# worker databases).  Without this fallback the bootstrap defaults
# to ``postgresql:///postgres`` which assumes a host-local socket
# the dev container does not expose.
if [ -z "${TEST_ADMIN_DATABASE_URL:-}" ] && [ -n "${TEST_DATABASE_URL:-}" ]; then
    # Strip the database segment from TEST_DATABASE_URL and append
    # ``/postgres``.  Tolerates query strings (``?sslmode=...``).
    TEST_ADMIN_DATABASE_URL="$(printf '%s' "$TEST_DATABASE_URL" \
        | sed -E 's|(://[^/]+)/[^?]+|\1/postgres|')"
    export TEST_ADMIN_DATABASE_URL
fi

# Resolve RESTART_TEST_DB to a decision, refusing anything ambiguous.  Bare
# presence would be simpler, and is wrong here for one specific reason: the
# variable this replaced was opt-OUT, so ``SKIP_DB_RESTART=0`` -- a natural
# way to write "no" -- landed on the HARMLESS branch.  Under an opt-IN
# spelling the identical typo would land on ``docker restart`` against a
# container every worktree shares, which is the hazard reason 2 in the
# header is about.  Renaming a knob must not invert which mistake is
# expensive, so the falsy words are honoured and an unrecognised value is
# refused loudly rather than guessed (scripts/ coding rule: validate inputs
# and fail loud, never with a silent default).
_restart_requested=""
# ``${v,,}`` is bash's own lower-casing.  An earlier draft piped through
# ``tr``, which put two subprocesses and a PATH dependency on the path of
# EVERY invocation -- including the default one this change exists to make
# cheap -- and degraded confusingly when PATH was minimal.
_restart_value="${RESTART_TEST_DB:-}"
case "${_restart_value,,}" in
    '' | 0 | false | no | off) _restart_requested="" ;;
    1 | true | yes | on) _restart_requested=yes ;;
    *)
        echo "[test.sh] RESTART_TEST_DB='${RESTART_TEST_DB}' is not a value I" \
            "will guess at.  Use 1/true/yes/on to restart" \
            "$TEST_DB_CONTAINER, or 0/false/no/off (or leave it unset) not" \
            "to." >&2
        exit 2
        ;;
esac
unset _restart_value

if [ -z "$_restart_requested" ]; then
    # The DEFAULT, and it reports the container's STATE rather than merely
    # announcing itself.  The restart is the only thing that resets the
    # fragmentation drift described in the header, and with it opt-in there
    # is otherwise NO instrument for that drift anywhere in the repo -- the
    # operator would be left remembering a threshold they cannot measure,
    # which is how an invert turns into a band-aid.  Uptime is the driver
    # the header's own curve is stated against ("~50 runs / ~37 h uptime").
    # Unlike a CREATE / DROP probe it takes no locks, writes nothing, and
    # cannot collide with a peer worktree's run on this shared container.
    #
    # ``docker ps -a``, not ``docker ps``: a STOPPED container is invisible
    # to the latter, so it would print the same bare line as "docker is not
    # installed" and pytest would then run against a dead DSN.  There are
    # stopped test-db containers on this host today.  The -a form reports
    # ``Exited (0) 4 days ago`` and the three states stay distinguishable --
    # and the stopped case is called out, because the old opt-out default
    # silently STARTED such a container and this one does not.
    _db_status=""
    if command -v docker >/dev/null 2>&1; then
        _db_status="$(docker ps -a --filter "name=^${TEST_DB_CONTAINER}$" \
            --format '{{.Status}}' 2>/dev/null | head -n1 || true)"
    fi
    case "$_db_status" in
        '')
            echo "[test.sh] not restarting $TEST_DB_CONTAINER (no such" \
                "container, or docker unavailable) -- set RESTART_TEST_DB=1" \
                "to force the hygiene restart" >&2
            ;;
        Up*)
            echo "[test.sh] not restarting $TEST_DB_CONTAINER ($_db_status)" \
                "-- set RESTART_TEST_DB=1 to force the hygiene restart" >&2
            ;;
        *)
            echo "[test.sh] $TEST_DB_CONTAINER is NOT RUNNING ($_db_status)." \
                "pytest will fail to connect.  Start it with" \
                "'docker start $TEST_DB_CONTAINER', or run with" \
                "RESTART_TEST_DB=1, which starts it as part of the restart." >&2
            ;;
    esac
    unset _db_status
elif ! command -v docker >/dev/null 2>&1; then
    echo "[test.sh] docker not on PATH -- skipping container restart" >&2
elif ! docker inspect "$TEST_DB_CONTAINER" >/dev/null 2>&1; then
    echo "[test.sh] container $TEST_DB_CONTAINER does not exist -- skipping restart" >&2
elif _live_test_backends="$(docker exec "$TEST_DB_CONTAINER" \
    psql -U shekel_user -d postgres -tAc \
    "SELECT count(*) FROM pg_stat_activity
     WHERE datname IS NOT NULL
       AND datname NOT IN ('postgres', 'template0', 'template1')" \
    2>/dev/null)" && [ "${_live_test_backends:-0}" -gt 0 ]; then
    # ANOTHER run is using this container.  The restart below terminates every
    # backend, so performing it would kill that run mid-test with "server
    # closed the connection unexpectedly" -- observed 2026-08-08, 208 setup
    # errors in a second checkout's suite, and indistinguishable from a real
    # regression at the point where it surfaces.  The restart is shared-memory
    # HYGIENE, not a correctness gate (see the header), so skipping it costs a
    # few seconds of drift and nothing else.  TEST_DB_PREFIX keeps the two
    # runs' databases apart; this keeps them from killing each other's
    # connections.
    #
    # **The probe asks which databases have BACKENDS, not which are named
    # "test", and plan step R7b-2 measured why.**  It matched
    # ``datname LIKE '%test%'`` -- but the per-worker databases are named from
    # TEST_DB_PREFIX, and the live prefixes are values like ``r7a2`` and
    # ``xf2c3`` (the checkout, not the word "test").  So the guard was blind to
    # exactly the runs it exists to protect: three consecutive full-suite runs
    # in one checkout were voided by another checkout's restart, each surfacing
    # as 138-462 setup errors that read like a code regression.  This container
    # is dedicated to the suite, so any database on it other than the admin and
    # template ones IS a test run's; counting backends there sees every naming
    # scheme, including ones no one has invented yet.  A leaked idle connection
    # would now disable the hygiene restart until it is closed, which is the
    # safe direction to fail: the header explains the restart buys wall-clock,
    # not correctness.
    echo "[test.sh] $TEST_DB_CONTAINER has live test connections (another run) --" \
        "skipping restart so it is not killed" >&2
else
    docker restart "$TEST_DB_CONTAINER" >/dev/null

    # Wait for PostgreSQL to accept connections.  ``pg_isready`` is
    # the standard health probe and is included in the postgres
    # image.  Cap the wait at READINESS_TIMEOUT_SECONDS so a hung
    # container fails loud instead of blocking the test invocation
    # indefinitely.
    deadline=$(($(date +%s) + READINESS_TIMEOUT_SECONDS))
    until docker exec "$TEST_DB_CONTAINER" pg_isready -q -U shekel_user 2>/dev/null; do
        # shellcheck disable=SC2312 # date +%s always succeeds; its timestamp only drives the readiness-timeout comparison, no destructive/financial path
        if [ "$(date +%s)" -ge "$deadline" ]; then
            echo "[test.sh] $TEST_DB_CONTAINER did not become ready within ${READINESS_TIMEOUT_SECONDS}s" >&2
            exit 2
        fi
        sleep 0.2
    done
fi

# Local default: skip container-spawning tests (marked ``docker``) so a
# routine run never touches the production Docker daemon that the homelab
# stack + wud/cadvisor/alloy share (see docs/test-harness-isolation.md).
# Opt back in by selecting the marker; on this host the tests/test_deploy
# conftest guard then still skips them unless you accept the prod-daemon
# churn, so the full local opt-in is:
#   SHEKEL_ALLOW_HOST_DOCKER=1 PYTEST_MARKER_EXPR=docker ./scripts/test.sh tests/test_deploy/...
# (or point DOCKER_HOST at an isolated daemon).  CI is unaffected: it
# invokes pytest directly, not this wrapper, so it still runs the full
# set.  An explicit ``-m`` in the caller's arguments takes precedence
# (pytest keeps the last ``-m`` on the command line).
PYTEST_MARKER_EXPR="${PYTEST_MARKER_EXPR:-not docker}"

exec pytest -m "$PYTEST_MARKER_EXPR" "$@"
