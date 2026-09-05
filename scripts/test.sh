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
#     TEST_DB_PER_RUN=1 ./scripts/test.sh         # private throwaway cluster
#
# TEST_DB_PER_RUN: a cluster of this run's own
#     Starts a container from the image
#     ``scripts/build_test_db_image.py`` bakes -- the test template already
#     inside it -- reached over a UNIX SOCKET in a per-run directory, and
#     removes it afterwards.  Nothing is shared, so nothing needs
#     coordinating: no suite slot, no hygiene restart, no live-backend
#     probe, no per-worktree template or worker-database prefix.  Those all
#     exist because ONE postmaster serves every worktree.
#
#     NO PORT IS PUBLISHED.  The cluster serves one process on this machine,
#     and on a rootless daemon a published port is a 25-40% failure: docker
#     allocates it inside the container's network namespace and rootlesskit
#     binds that number on the HOST.  See the block at the container start
#     for the measurements.
#
#     It is OPT-IN.  Since ``balance:X-br-3`` it also REFUSES a daemon that
#     is not rootless, because a container per run on the SYSTEM daemon is
#     exactly the churn ``docs/test-harness-isolation.md`` was written to
#     stop: that daemon runs the production database and the homelab
#     wud/cadvisor/alloy stack watches every container on it.  Flipping the
#     DEFAULT is ``balance:X-br-4``, which also deletes the coordination
#     machinery listed above.
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
#     when the clone used ``STRATEGY FILE_COPY``; this branch's own
#     gate collected ~13,000 on 2026-09-04, about 2.4x, and the clone
#     uses ``WAL_LOG`` (tests/conftest.py).  The EXACT dated counts
#     live in ``docs/testing-standards.md``, which is the one home
#     for them; no precise figure is repeated here on purpose.  Three
#     drafts of this sentence carried three wrong numbers -- "a
#     fifth", then a count older than the gate in the same commit,
#     then one the same commit made stale by adding a test -- because
#     a count written into the file it counts is wrong the moment it
#     is written.  The DRIFT is real; every absolute number
#     describing it is from a different suite on a different clone
#     strategy, and re-measuring it belongs to the work that removes
#     the shared cluster entirely.
#
#     One number is not merely stale but self-refuting, which is why
#     ``docs/testing-standards.md`` withdraws it rather than
#     re-pinning it: a CREATE/DROP round-trip "past ~15 ms" was once
#     offered as the signal to restart, and the same table reads
#     14.6 ms on a FRESHLY restarted container and 15.6 ms after ONE
#     run.  So it fired after a single run -- the cutoff sits inside
#     one run's worth of movement, which makes it "restart every
#     time" wearing the clothes of a measurement.
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
#     when it skips the restart.  It names which of five states it
#     found: docker absent, container absent, container paused,
#     container up (with its uptime), or container present but not
#     running (with docker's own status string).
#
# Restarts ONLY when ``RESTART_TEST_DB`` is truthy -- ``1``/``true``/
# ``yes``/``on``; the falsy words do not restart and an unrecognised value
# exits 2 (the full table is under "Environment variables read" below).
# Even when truthy it skips -- loudly -- when:
#   * ``docker`` is not on PATH, or the container does not exist
#     (CI, fresh checkout) -- runs pytest directly so the same
#     wrapper works in both environments.
#   * Another run's backends are live on the container (see the
#     probe below).
#
# Environment variables read:
#     DOCKER_HOST        PER-RUN MODE ONLY.  The daemon this run talks to.
#                        Auto-selected from ``XDG_RUNTIME_DIR`` when unset
#                        and a rootless socket is there, then EXPORTED so
#                        pytest and ``tests/test_deploy``'s conftest see the
#                        same endpoint this script chose.
#     XDG_RUNTIME_DIR    PER-RUN MODE ONLY.  Holds the rootless socket that
#                        is auto-selected, and the per-run socket directory.
#                        Default: ``/run/user/$(id -u)``.
#     SHEKEL_ALLOW_HOST_DOCKER  ``1`` accepts a non-rootless daemon in
#                        per-run mode.  Shares its spelling with
#                        ``tests/test_deploy/conftest.py`` on purpose.
#     CI                 Sanctions a non-rootless daemon, since a CI daemon
#                        is a throwaway nothing observes.  Read through the
#                        same truthy vocabulary as the flags below, NOT by
#                        presence: ``CI=false`` must not sanction anything.
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
#                        Matching is CASE-INSENSITIVE, so ``TRUE``
#                        and ``Off`` work as written.
#                        Deliberately not a bare presence flag: the
#                        spelling it replaced was opt-OUT, so a
#                        careless ``=0`` used to land on "skip" and
#                        would now land on "restart the container
#                        every worktree shares".  The rename must not
#                        turn a harmless typo into a destructive
#                        one.
#     READINESS_TIMEOUT_SECONDS  Cap on the post-restart
#                        ``pg_isready`` wait.  Default: 15.
#     TEST_DB_IMAGE      Per-run mode only.  An image to use INSTEAD of
#                        building and verifying one.  Skips
#                        build_test_db_image.py entirely, so whoever sets
#                        it owns the image's correctness -- CI, which
#                        builds once per job, and tests driving a stub
#                        docker are the two callers that know more than
#                        this script does.  Unset is the developer's case
#                        and is verified on every invocation.
#     TEST_DB_PER_RUN    Truthy (same words as RESTART_TEST_DB) to give
#                        this run its own throwaway cluster.  Overrides
#                        the shared-container path entirely, so
#                        RESTART_TEST_DB, TEST_DB_CONTAINER and the
#                        live-backend probe are all inapplicable and are
#                        skipped.
#     PYTEST_MARKER_EXPR Marker expression handed to pytest.
#                        Default: ``not docker`` (see the note above
#                        the ``exec`` at the bottom of this file).
#                        An explicit ``-m`` in the caller's arguments
#                        wins, because pytest keeps the last one.
#
# Exit codes:
#     Whatever pytest returns, in the ordinary case.  A bootstrap
#     failure exits 2 without running pytest: an unrecognised
#     ``RESTART_TEST_DB`` or ``TEST_DB_PER_RUN`` value, a container
#     that does not answer ``pg_isready`` within
#     READINESS_TIMEOUT_SECONDS, and in per-run mode a daemon that is
#     not rootless or cannot be reached, a socket path over the
#     kernel's 107-byte limit, a cluster that reports ready without
#     leaving a socket, or an image that cannot be prepared.  NO COUNT
#     IS GIVEN, for the reason the state-classifier below gives: every
#     attempt to count them in a comment has been wrong.  Other
#     non-pytest statuses are
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

# Resolve TEST_DB_PER_RUN the same way RESTART_TEST_DB is resolved: shared
# vocabulary, and an unrecognised value refused rather than guessed.  Two
# flags that mean "yes" differently would be their own small trap.
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

# Both flags are resolved BEFORE either branch is taken.  Resolving
# TEST_DB_PER_RUN first and exiting inside its branch meant a typo'd
# RESTART_TEST_DB was silently accepted whenever the other flag was set --
# `TEST_DB_PER_RUN=1 RESTART_TEST_DB=garbage` exited 0 while the same typo
# alone exits 2.  A refusal that depends on which other flag is set is not a
# refusal.
_per_run=""
_per_run_value="${TEST_DB_PER_RUN:-}"
case "${_per_run_value,,}" in
    '' | 0 | false | no | off) _per_run="" ;;
    1 | true | yes | on) _per_run=yes ;;
    *)
        echo "[test.sh] TEST_DB_PER_RUN='${TEST_DB_PER_RUN}' is not a value I" \
            "will guess at.  Use 1/true/yes/on for a private cluster, or" \
            "0/false/no/off (or leave it unset) for the shared one." >&2
        exit 2
        ;;
esac
unset _per_run_value

if [ -n "$_per_run" ]; then
    # The image builder imports app.ref_seeds and app.audit_infrastructure to
    # read the counts it verifies against, so it needs the project's
    # interpreter -- a bare python3 has neither the package nor psycopg2.
    if [ -x "${_REPO_ROOT}/.venv/bin/python" ]; then
        _PYTHON="${_REPO_ROOT}/.venv/bin/python"
    else
        _PYTHON="$(command -v python3 || true)"
    fi
    if [ -z "$_PYTHON" ]; then
        echo "[test.sh] TEST_DB_PER_RUN needs a python interpreter." >&2
        exit 2
    fi
    if ! command -v docker >/dev/null 2>&1; then
        echo "[test.sh] TEST_DB_PER_RUN needs docker, which is not on PATH." >&2
        exit 2
    fi

    # THE DAEMON THIS RUN TALKS TO.
    #
    # Per-run mode starts a container per invocation.  The SYSTEM daemon on
    # this host also runs the production database, and the homelab
    # wud/cadvisor/alloy stack watches every container on it -- exactly the
    # churn ``docs/test-harness-isolation.md`` exists to stop.  So the
    # harness picks a rootless daemon of its own, and REFUSES rather than
    # quietly spamming the system one.  Fail-closed: an absent rootless
    # daemon stops the run with instructions, it does not fall back.
    #
    # The test asks the daemon WHAT IT IS instead of pattern-matching the
    # socket path.  "Rootless" is a property of a daemon; a path is a guess
    # about one, and the guess has a hole -- a tcp:// endpoint pointed at a
    # socket-proxy container reads as isolated while being the production
    # daemon wearing a different address.
    #
    # Exporting DOCKER_HOST also lets ``tests/test_deploy`` run: its conftest
    # reads this variable and treats any non-default endpoint as isolated.
    # That only takes effect for a caller who ALSO asks for those tests with
    # ``PYTEST_MARKER_EXPR=docker``, because this wrapper defaults to
    # ``not docker`` further down and the marker deselects them before the
    # conftest is consulted.  An earlier draft of this comment claimed the
    # export alone un-skipped them; a review measured otherwise.
    if [ -z "${DOCKER_HOST:-}" ]; then
        _rootless_sock="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/docker.sock"
        if [ -S "$_rootless_sock" ]; then
            DOCKER_HOST="unix://${_rootless_sock}"
            export DOCKER_HOST
        fi
        unset _rootless_sock
    fi
    # Capture REACHABILITY separately from the answer.  Folding them together
    # made a stopped daemon report "is not rootless" -- a diagnosis the script
    # never established, about a socket that may well be the rootless one,
    # still on disk after the daemon behind it died.  The shared path below
    # already separates "docker could not answer" from "not what you asked
    # for"; this keeps per-run mode consistent with it.
    #
    # ``timeout`` because a wedged daemon otherwise blocks here forever, at a
    # point where nothing has been started and there is nothing to clean up.
    if _daemon_kind="$(timeout 15 docker info \
        --format '{{.SecurityOptions}}' 2>/dev/null)"; then
        _daemon_answered=yes
    else
        _daemon_answered=""
        _daemon_kind=""
    fi
    case "$_daemon_kind" in
        *rootless*) _daemon_private=yes ;;
        *) _daemon_private="" ;;
    esac
    # ``CI`` goes through the same vocabulary as the two flags above rather
    # than a presence test.  It sanctions a container per run on the daemon
    # that runs PRODUCTION, which is the heaviest consequence in this file,
    # and a bare ``[ -n "$CI" ]`` made ``CI=false`` and ``CI=0`` sanction it.
    # An unrecognised value is NOT sanctioned -- this one cannot exit 2 the
    # way a typo'd operator flag does, because CI sets it, not the operator.
    case "${CI:-}" in
        1 | true | TRUE | True | yes | on) _ci_sanctioned=yes ;;
        *) _ci_sanctioned="" ;;
    esac
    if [ -z "$_daemon_private" ] \
        && [ -z "$_ci_sanctioned" ] \
        && [ "${SHEKEL_ALLOW_HOST_DOCKER:-}" != "1" ]; then
        if [ -z "$_daemon_answered" ]; then
            echo "[test.sh] TEST_DB_PER_RUN could not ask" \
                "${DOCKER_HOST:-the default socket} what it is, so whether a" \
                "container per run would land on the production daemon is" \
                "UNKNOWN and this run stops.  Start the rootless daemon with" \
                "'systemctl --user start docker.service' (see" \
                "docs/test-harness-isolation.md)." >&2
        else
            echo "[test.sh] TEST_DB_PER_RUN refuses this daemon:" \
                "${DOCKER_HOST:-the default socket} is not rootless, so a" \
                "container per run would land on the daemon that runs" \
                "production.  Start the rootless daemon with 'systemctl" \
                "--user start docker.service' (see" \
                "docs/test-harness-isolation.md), or set" \
                "SHEKEL_ALLOW_HOST_DOCKER=1 to accept the churn." >&2
        fi
        exit 2
    fi
    unset _daemon_private _daemon_answered _daemon_kind _ci_sanctioned
    # TEST_DB_IMAGE lets a caller supply an image this run should use and
    # skip the build-and-verify entirely.  It exists for two callers that
    # both know more than this script does: CI, which builds the image once
    # per job and would otherwise re-verify it per invocation, and a test
    # driving a stub docker, which cannot satisfy a real verification.  When
    # it is unset -- the developer's case -- the builder runs and checks the
    # cached image on EVERY invocation rather than trusting the tag, so a
    # stale or damaged image is rebuilt here instead of being cloned from
    # for the whole run.
    if [ -n "${TEST_DB_IMAGE:-}" ]; then
        _image="$TEST_DB_IMAGE"
        echo "[test.sh] using the image given in TEST_DB_IMAGE ($_image);" \
            "it is NOT verified here, so whoever set it owns that" >&2
    else
        _image="$("$_PYTHON" "${_REPO_ROOT}/scripts/build_test_db_image.py" \
            --print-tag 2>/dev/null || true)"
        if [ -z "$_image" ]; then
            echo "[test.sh] could not resolve the test-db image tag; run" \
                "scripts/build_test_db_image.py by hand to see why." >&2
            exit 2
        fi
        if ! "$_PYTHON" "${_REPO_ROOT}/scripts/build_test_db_image.py" >&2; then
            echo "[test.sh] could not prepare $_image" >&2
            exit 2
        fi
    fi

    # The name carries the PID because the whole point is that two runs can
    # coexist -- including two in the SAME worktree, which no amount of
    # TEST_DB_PREFIX ever fixed.
    _run_container="shekel-testrun-$$"

    # NO PUBLISHED PORT.  The cluster serves exactly one process on this
    # machine, so a port buys nothing and costs a whole failure class: on a
    # rootless daemon `dockerd` picks the port inside the container's network
    # namespace and rootlesskit then binds that number on the HOST, where a
    # live outbound connection often already owns it.  `ip_local_port_range`
    # is 32768-60999, byte-identical to docker's publish band, and this host
    # routinely holds thousands of sockets in it.  Measured: 5 of 12 container
    # starts failed, and 3 of 12 still failed with no port ever recycled, so
    # it is not a release race -- it is two allocators in two namespaces
    # sharing one number space.  The root daemon does not hit it (0 of 8),
    # which is why this only surfaced on moving off it.
    #
    # A socket has no allocator and no shared namespace: the run names its
    # own directory, and it already has a unique name in its PID.
    _run_sockdir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/${_run_container}"
    # A unix socket path is capped at 107 bytes by the kernel and postgres
    # appends `/.s.PGSQL.5432` (14).  Refuse at the door rather than let the
    # limit surface as a connection error from deep inside the suite.
    # BYTES, not characters: ``${#var}`` counts characters and ``sun_path``
    # is a byte budget, so a multibyte XDG_RUNTIME_DIR would under-count and
    # admit a path the kernel then rejects.
    _sockdir_bytes="$(printf '%s' "$_run_sockdir" | wc -c)"
    if [ "$_sockdir_bytes" -gt 93 ]; then
        echo "[test.sh] socket directory $_run_sockdir is too long;" \
            "a unix socket path cannot exceed 107 bytes.  Set" \
            "XDG_RUNTIME_DIR to something shorter." >&2
        exit 2
    fi
    unset _sockdir_bytes
    rm -rf "$_run_sockdir"
    mkdir -p "$_run_sockdir"
    # 0777 because rootless docker maps the container's postgres user into
    # the subuid range, so it cannot write a directory owned by this user.
    # The socket is still private: XDG_RUNTIME_DIR itself is 0700.
    chmod 0777 "$_run_sockdir"

    # ``-v`` removes the container's ANONYMOUS VOLUME with it.  The baked
    # image inherits the base's ``VOLUME /var/lib/postgresql`` declaration
    # even though PGDATA lives elsewhere, so every run created a volume that
    # ``docker rm -f`` left behind: measured 101 of 112 volumes on this
    # daemon dangling, the newest in one-second pairs matching this script's
    # own verify+run invocations.  Each holds ~0 B, so the cost is unbounded
    # metadata on a daemon the homelab stack watches, not disk.
    #
    # And the removal REPORTS ITS OWN FAILURE.  Discarding the status,
    # stdout and stderr made the one step whose entire job is cleanup unable
    # to say it had failed: a wedged or unreachable daemon left the
    # container running and the wrapper still exited with pytest's status.
    _teardown_run_container() {
        docker rm -fv "$_run_container" >/dev/null 2>&1 || true
        if docker inspect "$_run_container" >/dev/null 2>&1; then
            echo "[test.sh] WARNING: $_run_container survived teardown --" \
                "remove it by hand ('docker rm -fv $_run_container') or the" \
                "next run reusing that PID collides with it." >&2
        fi
        # The socket directory outlives the container and sits on a tmpfs, so
        # a leak is bounded by the next reboot rather than by anything that
        # prunes it.  A plain rm suffices ONLY because the mount point is
        # /sockets: the entrypoint chowns /var/run/postgresql to its own
        # subuid-mapped user and sets the sticky bit, after which this user
        # cannot unlink the socket that user created -- measured 5 of 5.
        rm -rf "$_run_sockdir" 2>/dev/null || true
        if [ -e "$_run_sockdir" ]; then
            echo "[test.sh] WARNING: $_run_sockdir survived teardown." >&2
        fi
    }

    # Forward the signal to pytest FIRST, so the run stops rather than being
    # abandoned, then tear down and exit with the conventional status.
    # shellcheck disable=SC2329 # invoked only through the INT and TERM traps below, which shellcheck cannot follow
    _stop_run() {
        kill -"$2" "$1" 2>/dev/null || true
        wait "$1" 2>/dev/null || true
        _teardown_run_container
        exit "$3"
    }
    # EXIT alone is not enough: Ctrl-C at the terminal delivers INT to the
    # whole process group, and without these traps the container outlives
    # the run and the next one collides with nothing but a leaked cluster.
    trap '_teardown_run_container' EXIT
    trap '_teardown_run_container; exit 130' INT
    trap '_teardown_run_container; exit 143' TERM

    # ``--network=none`` follows from having no port: the cluster needs no
    # network at all, which also takes the harness off the experimental
    # gvisor-tap-vsock driver the rootless daemon falls back to when
    # slirp4netns is absent.  ``listen_addresses=''`` means it does not even
    # open a TCP listener inside its own namespace.
    #
    # The socket lives at /sockets, NOT the default /var/run/postgresql,
    # because the entrypoint chowns that path to a subuid-mapped user and
    # sticky-bits it, leaving behind files this user cannot delete.
    docker run -d --rm --name "$_run_container" \
        --network=none \
        -e POSTGRES_USER=shekel_user \
        -e POSTGRES_PASSWORD=shekel_pass \
        -e POSTGRES_DB=postgres \
        -e PGDATA=/pgdata-baked \
        -v "${_run_sockdir}:/sockets" \
        "$_image" \
        -c fsync=off \
        -c synchronous_commit=off \
        -c full_page_writes=off \
        -c listen_addresses='' \
        -c unix_socket_directories=/sockets >/dev/null

    # THE NON-DURABLE KNOBS ARE WHAT MAKE THIS AFFORDABLE, and leaving them
    # off is the difference between a design that pays for itself and one
    # that does not.  Measured: the full suite took 753 s in a per-run
    # container against 356 s on the shared cluster -- 2.1x -- purely because
    # the baked image runs with docker's default durability while
    # docker-compose.dev.yml gives the shared test-db `fsync=off`,
    # `synchronous_commit=off` and `full_page_writes=off`.  Per-test
    # drop-and-reclone is thousands of DDL cycles, and
    # docs/testing-standards.md prices one at 1618 ms with fsync on against
    # 31 ms with it off.  The cluster is a throwaway that lives for one run,
    # so a crash losing its last transactions costs a re-run and nothing
    # else -- exactly the argument the compose file already makes, which is
    # why these three are copied from it rather than invented here.
    #
    # Asking over the socket is safe HERE and would not be in the builder.
    # The entrypoint answers on a socket during its initdb window before the
    # real server exists -- but the baked image ships a populated PGDATA, so
    # the entrypoint skips initdb entirely and that window never opens.
    # scripts/build_test_db_image.py, whose bake container DOES run initdb,
    # is the one that still cannot trust a socket.
    _deadline=$(($(date +%s) + READINESS_TIMEOUT_SECONDS))
    until docker exec "$_run_container" \
        pg_isready -q -h /sockets -U shekel_user 2>/dev/null; do
        # shellcheck disable=SC2312 # date +%s always succeeds; it only drives this timeout
        if [ "$(date +%s)" -ge "$_deadline" ]; then
            echo "[test.sh] $_run_container did not accept connections within" \
                "${READINESS_TIMEOUT_SECONDS}s" >&2
            exit 2
        fi
        sleep 0.1
    done

    # libpq reads a ``host`` beginning with ``/`` as a socket DIRECTORY, and
    # SQLAlchemy passes the query string through untouched, so one URL shape
    # serves both.  tests/conftest.py rewrites only the PATH of these URLs to
    # reach a per-worker database, and urlparse/urlunparse preserve the
    # query, so the socket survives that rewrite.
    if [ ! -S "${_run_sockdir}/.s.PGSQL.5432" ]; then
        echo "[test.sh] $_run_container reported ready but left no socket" \
            "at ${_run_sockdir}/.s.PGSQL.5432" >&2
        exit 2
    fi
    export TEST_ADMIN_DATABASE_URL="postgresql://shekel_user:shekel_pass@/postgres?host=${_run_sockdir}"
    export TEST_DATABASE_URL="postgresql://shekel_user:shekel_pass@/shekel_test?host=${_run_sockdir}"
    # The baked template's name, not the worktree's: a private cluster has
    # exactly one template and nothing to collide with.
    export TEST_TEMPLATE_DATABASE=shekel_test_template

    echo "[test.sh] private cluster $_run_container on ${_run_sockdir}" \
        "from $_image" >&2

    PYTEST_MARKER_EXPR="${PYTEST_MARKER_EXPR:-not docker}"
    # NOT `exec`: the container has to be removed after pytest returns, and
    # an exec'd process has no after.  pytest's status is preserved and
    # re-raised so the wrapper stays transparent to callers and to CI.
    #
    # ``env -u TEST_DB_PER_RUN`` is load-bearing.  The flag was INHERITED by
    # pytest, and five cases in
    # tests/test_scripts/test_test_runner_container_states.py re-invoke this
    # wrapper with ``env={**os.environ, ...}`` -- so each re-entered per-run
    # mode, started TWO more containers on the production daemon nested
    # inside the suite, and asserted on a state message it never got.
    # Measured: 8 failed, 18 passed, green on the shared path.  A child must
    # take the SHARED path, which is what those tests grade.
    #
    # BACKGROUNDED, then waited.  Bash does not service a trap while waiting
    # on a FOREGROUND child, so with pytest in the foreground a SIGTERM to
    # the wrapper did nothing until pytest finished -- measured 20 s of
    # silence -- which broke `timeout` and left the SIGKILL escalation
    # leaking a container AND orphaning pytest onto PID 1.  ``wait`` is
    # interruptible, so the traps below can actually run.
    set +e
    env -u TEST_DB_PER_RUN pytest -m "$PYTEST_MARKER_EXPR" "$@" &
    _pytest_pid=$!
    trap '_stop_run "$_pytest_pid" INT 130' INT
    trap '_stop_run "$_pytest_pid" TERM 143' TERM
    wait "$_pytest_pid"
    _pytest_status=$?
    set -e
    _teardown_run_container
    trap - EXIT INT TERM
    exit "$_pytest_status"
fi

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
    _have_docker=""
    _daemon_ok=""
    if command -v docker >/dev/null 2>&1; then
        _have_docker=yes
        # Capture docker's EXIT STATUS, not just its output.  Piping straight
        # into ``head`` and swallowing failure with ``|| true`` made an
        # unreachable daemon indistinguishable from a container that does not
        # exist: both yield an empty string.  They want different messages
        # and different advice -- "docker start" cannot help you when the
        # daemon is down, and the restart path would go on to say the
        # container "does not exist", which is not established.
        # ``if cmd`` suspends errexit for cmd, so a non-zero status here is a
        # branch rather than an abort.
        # ``--filter name=`` is a REGEX match, not a literal one, and the
        # ``^...$`` anchoring hides that rather than fixing it: with
        # TEST_DB_CONTAINER=rev8.test.db the dots matched ``rev8-test-db``
        # and this branch reported a DIFFERENT container's status as though
        # it were the one asked for -- while the restart path, which uses
        # exact-name ``docker inspect``, said the same name did not exist.
        # Two paths, contradictory answers, for one operator-set value.
        # ``.``, ``-`` and ``_`` are all legal in docker names.
        #
        # So: let the filter NARROW, then match the name exactly here.  The
        # loop is a herestring rather than a pipe deliberately -- a pipe
        # into a reader that stops early is the SIGPIPE-under-pipefail abort
        # this file already had to remove once.
        if _db_raw="$(docker ps -a --filter "name=${TEST_DB_CONTAINER}" \
            --format '{{.Names}}|{{.Status}}' 2>/dev/null)"; then
            _daemon_ok=yes
            while IFS='|' read -r _row_name _row_status; do
                if [ "$_row_name" = "$TEST_DB_CONTAINER" ]; then
                    _db_status="$_row_status"
                    break
                fi
            done <<<"$_db_raw"
            unset _row_name _row_status
        else
            # Re-ask for the ERROR, on the failure path only.  The branch
            # below asserts a cause from an exit status; without this it
            # asserts it while discarding the one line that substantiates
            # it, and a DOCKER_HOST typo (a CLI configuration error, healthy
            # daemon) is indistinguishable from a daemon that is genuinely
            # down.  ``2>&1 >/dev/null`` keeps stderr and drops stdout.
            _docker_err="$(docker ps -a --filter "name=${TEST_DB_CONTAINER}" \
                --format '{{.Names}}' 2>&1 >/dev/null || true)"
            _docker_err="${_docker_err%%$'\n'*}"
        fi
        unset _db_raw
    fi
    # The states are enumerated and pinned by
    # tests/test_scripts/test_test_runner_container_states.py against real
    # docker output, deliberately, because every attempt to COUNT them in
    # this comment was wrong: "three", then "four", then "five", each time
    # written while editing the branch it was counting.  The test is the
    # census; this comment no longer offers a number.
    #
    # Two of them are reached before the case: docker missing, and docker
    # present with an unreachable daemon.  Only then does an empty status
    # genuinely mean "no such container".
    #
    # ``*'(Paused)'*`` MUST precede ``Up*``: docker renders a paused
    # container as ``Up 5 minutes (Paused)``, so it matches ``Up*`` and was
    # reported as healthy while its postmaster is SIGSTOPped -- and pytest
    # then HANGS rather than failing.
    #
    # Be exact about what this fixes: the REPORT, not the hang.  The wrapper
    # still execs pytest afterwards, so a paused container still hangs the
    # run; what changed is that the operator is told why instead of being
    # shown an everything-is-fine line.  Whether this state should exit 2
    # like the other two bootstrap failures is a question for the developer,
    # not a change to make on the way past.
    if [ -z "$_have_docker" ]; then
        echo "[test.sh] not restarting $TEST_DB_CONTAINER (docker is not on" \
            "PATH) -- set RESTART_TEST_DB=1 to force the hygiene restart" >&2
    elif [ -z "$_daemon_ok" ]; then
        echo "[test.sh] docker could not answer, so the state of" \
            "$TEST_DB_CONTAINER is UNKNOWN -- not 'missing'.  pytest will" \
            "fail to connect unless the database is reachable another way." \
            "docker said: ${_docker_err:-(no message)}" >&2
    else
        case "$_db_status" in
            '')
                echo "[test.sh] not restarting $TEST_DB_CONTAINER (no such" \
                    "container) -- set RESTART_TEST_DB=1 to force the" \
                    "hygiene restart" >&2
                ;;
            *'(Paused)'*)
                echo "[test.sh] $TEST_DB_CONTAINER is PAUSED ($_db_status)." \
                    "pytest will HANG rather than fail.  Resume it with" \
                    "'docker unpause $TEST_DB_CONTAINER'." >&2
                ;;
            Up*)
                echo "[test.sh] not restarting $TEST_DB_CONTAINER" \
                    "($_db_status) -- set RESTART_TEST_DB=1 to force the" \
                    "hygiene restart" >&2
                ;;
            *)
                echo "[test.sh] $TEST_DB_CONTAINER is NOT RUNNING" \
                    "($_db_status).  pytest will fail to connect.  Start it" \
                    "with 'docker start $TEST_DB_CONTAINER', or run with" \
                    "RESTART_TEST_DB=1, which starts it as part of the" \
                    "restart." >&2
                ;;
        esac
    fi
    unset _db_status _have_docker _daemon_ok _docker_err
elif ! command -v docker >/dev/null 2>&1; then
    echo "[test.sh] docker not on PATH -- skipping container restart" >&2
elif ! docker inspect "$TEST_DB_CONTAINER" >/dev/null 2>&1; then
    # ``docker inspect`` fails the same way for "no such container" and for
    # "cannot reach the daemon", so this branch used to announce a missing
    # container on evidence that did not establish one.  Asking the SERVER
    # first was the previous fix and left a window: with a probe that
    # succeeded and an inspect that then failed on an unreachable daemon,
    # the answer was "does not exist" again.  Ask about the container first
    # and interrogate the daemon only once that has failed -- which also
    # spends one fewer daemon call on the healthy path.
    if docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
        echo "[test.sh] container $TEST_DB_CONTAINER does not exist -- skipping restart" >&2
    else
        echo "[test.sh] docker could not answer, so the state of" \
            "$TEST_DB_CONTAINER is UNKNOWN -- not 'missing'; skipping restart" >&2
    fi
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
