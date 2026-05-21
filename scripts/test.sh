#!/bin/bash
# Shekel Budget App -- Test Runner Wrapper
#
# Restarts the local test-db container before invoking pytest, then
# execs into pytest with whatever arguments were passed.  Forwards all
# arguments verbatim:
#
#     ./scripts/test.sh                           # full suite
#     ./scripts/test.sh tests/test_routes/...     # targeted run
#     ./scripts/test.sh -n 0 -x                   # pass-through flags
#
# Why the restart?
#     Phase 3b per-test isolation drops and re-clones a per-worker
#     database for every test.  Over many back-to-back suite runs the
#     postmaster accumulates shared-memory state (sinval queue,
#     syscache, relcache invalidations) that VACUUM / CHECKPOINT
#     cannot reset -- only restarting the postmaster does.  On a
#     long-lived container, full-suite wall-clock drifts linearly
#     (measured: ~62 s baseline, +2-3 s per suite run, reaching
#     ~220 s after ~50 runs / ~37 h uptime).  A ``docker restart``
#     costs ~3 s and restores the baseline.  At a 65 s suite run
#     that is ~5 % overhead -- invisible compared to the variance
#     it eliminates.  See ``docs/testing-standards.md`` "Catalog
#     fragmentation and the test-runner wrapper" for the full
#     analysis.
#
# Skips the restart when:
#   * The container does not exist (CI, fresh checkout) -- runs
#     pytest directly so the same wrapper works in both
#     environments.
#   * The ``SKIP_DB_RESTART`` environment variable is set to a
#     non-empty value -- escape hatch for chained invocations
#     where the caller wants to amortise the restart across
#     several pytest commands.
#
# Environment variables read:
#     DB_CONTAINER       Test-db container name.  Default:
#                        ``shekel-dev-test-db``.
#     TEST_DATABASE_URL  Set in ``.env``; used to derive
#                        ``TEST_ADMIN_DATABASE_URL`` when the latter
#                        is not already set.  No-op if both are
#                        already in the environment.
#     SKIP_DB_RESTART    See above.
#
# Exit codes:
#     Whatever pytest returns.  Bootstrap failures (docker missing,
#     container hung past the readiness timeout) exit 1 / 2.

set -euo pipefail

DB_CONTAINER="${DB_CONTAINER:-shekel-dev-test-db}"
READINESS_TIMEOUT_SECONDS="${READINESS_TIMEOUT_SECONDS:-15}"

# Read TEST_DATABASE_URL from .env if present and not already in
# the environment.  We do NOT ``source .env`` -- the file is a
# dotenv-style key=value document (read by python-dotenv at app
# startup) and may contain values with unquoted spaces (display
# names, comments) that the shell would mis-parse.  Extract only
# the one line we need, value-after-first-=, single match.
if [ -z "${TEST_DATABASE_URL:-}" ] && [ -f .env ]; then
    _env_value="$(grep -E '^TEST_DATABASE_URL=' .env | head -n1 | cut -d= -f2-)"
    if [ -n "$_env_value" ]; then
        export TEST_DATABASE_URL="$_env_value"
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

if [ -n "${SKIP_DB_RESTART:-}" ]; then
    echo "[test.sh] SKIP_DB_RESTART set -- skipping container restart" >&2
elif ! command -v docker > /dev/null 2>&1; then
    echo "[test.sh] docker not on PATH -- skipping container restart" >&2
elif ! docker inspect "$DB_CONTAINER" > /dev/null 2>&1; then
    echo "[test.sh] container $DB_CONTAINER does not exist -- skipping restart" >&2
else
    docker restart "$DB_CONTAINER" > /dev/null

    # Wait for PostgreSQL to accept connections.  ``pg_isready`` is
    # the standard health probe and is included in the postgres
    # image.  Cap the wait at READINESS_TIMEOUT_SECONDS so a hung
    # container fails loud instead of blocking the test invocation
    # indefinitely.
    deadline=$(( $(date +%s) + READINESS_TIMEOUT_SECONDS ))
    until docker exec "$DB_CONTAINER" pg_isready -q -U shekel_user 2>/dev/null; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
            echo "[test.sh] $DB_CONTAINER did not become ready within ${READINESS_TIMEOUT_SECONDS}s" >&2
            exit 2
        fi
        sleep 0.2
    done
fi

exec pytest "$@"
