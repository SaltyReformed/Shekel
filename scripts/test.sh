#!/bin/bash
# Shekel Budget App -- Test Runner Wrapper
#
# Gives the run a PRIVATE POSTGRES CLUSTER, then runs pytest against it
# with whatever arguments were passed, forwarded verbatim:
#
#     ./scripts/test.sh                           # full suite
#     ./scripts/test.sh tests/test_routes/...     # targeted run
#     ./scripts/test.sh -n 0 -x                   # pass-through flags
#
# A cluster of this run's own
#     The container comes from the image ``scripts/build_test_db_image.py``
#     bakes -- the test template already inside it -- and is reached over a
#     UNIX SOCKET in a per-run directory.  It is removed afterwards.
#
#     NOTHING IS SHARED, SO NOTHING NEEDS COORDINATING.  Until
#     ``balance:X-br-4`` this wrapper also carried a hygiene restart, a
#     live-backend probe, a per-worktree template name and a per-worktree
#     worker-database prefix, and the repo carried a suite-slot lock beside
#     them.  Every one of those existed because ONE postmaster served every
#     worktree; a cluster per run leaves each of them nothing to protect, so
#     they were deleted rather than kept as a second way to do nothing.  It
#     also fixes what none of them ever did: two runs in the SAME worktree.
#
#     WHAT THE DELETION DOES NOT FIX is that concurrent runs still share the
#     host's cores.  That is a resource fact rather than a defect, so this
#     script REPORTS the other runs it can see and proceeds -- see the block
#     above the container name for the measurement and the argument.
#
#     What it costs, measured 2026-09-05 on ``tests/test_utils/`` (385
#     tests), same host, back to back: 12.7 s against the shared cluster,
#     14.1 s here with the image already built and its layers warm, 22.0 s
#     on the first run after a rebuild.  The fixed part is the image
#     verification (1.4 s) plus the container's whole life -- start,
#     readiness and removal -- which is 0.33 s.
#
#     NO PORT IS PUBLISHED.  The cluster serves one process on this machine,
#     and on a rootless daemon a published port is a 25-40% failure: docker
#     allocates it inside the container's network namespace and rootlesskit
#     binds that number on the HOST.  See the block at the container start
#     for the measurements.
#
#     THE DAEMON IS NOT THE SYSTEM ONE.  A container per run on the system
#     daemon is exactly the churn ``docs/test-harness-isolation.md`` was
#     written to stop: that daemon runs the production database and the
#     homelab wud/cadvisor/alloy stack watches every container on it.  So
#     this refuses a non-rootless daemon rather than quietly spamming it.
#
# Environment variables read:
#     DOCKER_HOST        The daemon this run talks to.  Auto-selected from
#                        ``XDG_RUNTIME_DIR`` when unset and a rootless socket
#                        is there, then EXPORTED so pytest and
#                        ``tests/test_deploy``'s conftest see the same
#                        endpoint this script chose.
#     XDG_RUNTIME_DIR    Holds the rootless socket that is auto-selected, and
#                        the per-run socket directory.  Default:
#                        ``/run/user/$(id -u)``.
#     SHEKEL_ALLOW_HOST_DOCKER  ``1`` accepts a non-rootless daemon.  Shares
#                        its spelling with ``tests/test_deploy/conftest.py``
#                        on purpose.
#     CI                 Sanctions a non-rootless daemon, since a CI daemon
#                        is a throwaway nothing observes.  Read through a
#                        truthy vocabulary, NOT by presence: ``CI=false``
#                        must not sanction anything.
#     READINESS_TIMEOUT_SECONDS  Cap on the ``pg_isready`` wait.  Default: 15.
#     TEST_DB_IMAGE      An image to use INSTEAD of building and verifying
#                        one.  Skips build_test_db_image.py entirely, so
#                        whoever sets it owns the image's correctness.  Its
#                        one caller today is
#                        tests/test_scripts/test_test_runner.py, which drives
#                        a stub docker that cannot satisfy a real
#                        verification.  It is NOT set by CI: CI does not use
#                        this wrapper at all and builds the TEMPLATE, not the
#                        image.  Unset is the developer's case and every real
#                        run, and is verified on every invocation.
#     PYTEST_MARKER_EXPR Marker expression handed to pytest.
#                        Default: ``not docker`` (see the note above the
#                        pytest invocation at the bottom of this file).
#                        An explicit ``-m`` in the caller's arguments wins,
#                        because pytest keeps the last one.
#
# What it does NOT read, and where those went:
#     TEST_DATABASE_URL / TEST_ADMIN_DATABASE_URL are EXPORTED by this
#     script, not read from ``.env``: they name this run's socket, which no
#     dotenv can know.  ``TEST_TEMPLATE_DATABASE`` and ``TEST_DB_PREFIX``
#     are gone entirely -- a private cluster holds exactly one template and
#     one run's worker databases, so there is nothing to rename them away
#     from.  Their single spellings now live as constants in
#     ``tests/conftest.py``, ``scripts/build_test_template.py`` and
#     ``scripts/build_test_db_image.py``, which must agree.
#
# Exit codes:
#     Whatever pytest returns, in the ordinary case.  A bootstrap failure
#     exits 2 without running pytest: no python interpreter, no docker, a
#     daemon that is not rootless or cannot be reached, a socket path over
#     the kernel's 107-byte limit, an image that cannot be prepared, a
#     cluster that does not accept connections within
#     READINESS_TIMEOUT_SECONDS, and one that reports ready without leaving
#     a socket.  NO COUNT IS GIVEN: every attempt to count them in a comment
#     has been wrong.  Other non-pytest statuses are docker's or the
#     shell's, not this script's.

set -euo pipefail

READINESS_TIMEOUT_SECONDS="${READINESS_TIMEOUT_SECONDS:-15}"

# Resolved via BASH_SOURCE, not the invoker's cwd, so a run from outside the
# repo root still finds the project (OPS/SH-26).
_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The image builder imports app.ref_seeds and app.audit_infrastructure to
# read the counts it verifies against, so it needs the project's
# interpreter -- a bare python3 has neither the package nor psycopg2.
if [ -x "${_REPO_ROOT}/.venv/bin/python" ]; then
    _PYTHON="${_REPO_ROOT}/.venv/bin/python"
else
    _PYTHON="$(command -v python3 || true)"
fi
if [ -z "$_PYTHON" ]; then
    echo "[test.sh] needs a python interpreter to prepare the test-db image." >&2
    exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
    echo "[test.sh] needs docker, which is not on PATH." >&2
    exit 2
fi

# THE DAEMON THIS RUN TALKS TO.
#
# The SYSTEM daemon on this host runs the production database, and the
# homelab wud/cadvisor/alloy stack watches every container on it -- exactly
# the churn ``docs/test-harness-isolation.md`` exists to stop.  So the
# harness picks a rootless daemon of its own, and REFUSES rather than
# quietly spamming the system one.  Fail-closed: an absent rootless daemon
# stops the run with instructions, it does not fall back.
#
# The test asks the daemon WHAT IT IS instead of pattern-matching the socket
# path.  "Rootless" is a property of a daemon; a path is a guess about one,
# and the guess has a hole -- a tcp:// endpoint pointed at a socket-proxy
# container reads as isolated while being the production daemon wearing a
# different address.
#
# Exporting DOCKER_HOST also lets ``tests/test_deploy`` run: its conftest
# reads this variable and treats any non-default endpoint as isolated.  That
# only takes effect for a caller who ALSO asks for those tests with
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
# never established, about a socket that may well be the rootless one, still
# on disk after the daemon behind it died.
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
# ``CI`` goes through a vocabulary rather than a presence test.  It sanctions
# a container per run on the daemon that runs PRODUCTION, which is the
# heaviest consequence in this file, and a bare ``[ -n "$CI" ]`` made
# ``CI=false`` and ``CI=0`` sanction it.  An unrecognised value is NOT
# sanctioned -- this one cannot exit 2 the way a typo'd operator flag would,
# because CI sets it, not the operator.
case "${CI:-}" in
    1 | true | TRUE | True | yes | on) _ci_sanctioned=yes ;;
    *) _ci_sanctioned="" ;;
esac
if [ -z "$_daemon_private" ] \
    && [ -z "$_ci_sanctioned" ] \
    && [ "${SHEKEL_ALLOW_HOST_DOCKER:-}" != "1" ]; then
    if [ -z "$_daemon_answered" ]; then
        echo "[test.sh] could not ask" \
            "${DOCKER_HOST:-the default socket} what it is, so whether a" \
            "container per run would land on the production daemon is" \
            "UNKNOWN and this run stops.  Start the rootless daemon with" \
            "'systemctl --user start docker.service' (see" \
            "docs/test-harness-isolation.md)." >&2
    else
        echo "[test.sh] refuses this daemon:" \
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

# TEST_DB_IMAGE lets a caller supply an image this run should use and skip
# the build-and-verify entirely.  It exists for a caller that knows more than
# this script does: today that is this wrapper's own tests, which drive a stub
# docker that cannot satisfy a real verification.  CI is NOT such a caller --
# it never invokes this wrapper.  When the variable is unset -- the
# developer's case, and every real run -- the builder runs and checks the
# cached image on EVERY invocation rather than trusting the tag, so a stale or
# damaged image is rebuilt here instead of being cloned from for the whole run.
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

# WHO ELSE IS RUNNING, AND WHY THIS IS A NOTE RATHER THAN A LOCK.
#
# ``scripts/suite_slot.sh`` was a real mkdir-based mutex every gating run had
# to take, and ``balance:X-br-4`` deleted it.  Its header named TWO hazards and
# a cluster per run only kills one of them:
#
#   * CORRECTNESS is now structural.  Nothing can restart another run's
#     postmaster and nothing can collide on a database name, because there is
#     no shared postmaster and no shared name space.  A lock bought exactly
#     this and no longer buys it.
#   * CONTENTION SURVIVES, because the cores do not multiply.  Measured
#     2026-09-05 on this 24-core host: one suite alone finished in 349 s; with
#     THREE running (two private clusters plus a peer's gating run) two of them
#     reached only ~38% in 13 minutes, at a run-queue of 32, ~950,000 context
#     switches/sec and 28% iowait.  No test FAILED in either -- the slowest
#     single test is 2.58 s against pytest.ini's 30 s per-test timeout, so
#     there is roughly 11x of headroom and that measurement was sitting on it.
#
# So what survives is a resource fact, not a defect, and the right instrument
# for a resource fact is information rather than a mutex: this prints what else
# is live and proceeds.  Nothing to acquire, nothing to release, nothing to go
# stale, and no run that cannot start because somebody forgot to release.
#
# **The CWD is the identity, not the argv.**  Every worktree on this host
# shares ONE venv at the main checkout, so a peer's pytest reads
# ``/home/josh/projects/Shekel/.venv/bin/pytest`` in its command line whatever
# tree it is testing.  Reading ``/proc/<pid>/cwd`` was already the doctrine's
# rule for killing by PID; it is the same rule for naming a peer, and an
# argv-based version of this block would have named the main checkout for
# every session on the box.
#
# Fails soft in every direction: no ``pgrep``, an unreadable ``/proc`` entry, a
# process that exits mid-scan -- all just mean a quieter note.  A status
# report may never stop a run.
if command -v pgrep >/dev/null 2>&1; then
    _peers=""
    # ``--collect-only`` writes no database and takes no worker, so it is not
    # a peer for this purpose; ``suite_slot.sh`` exempted it too.
    for _peer_pid in $(
        {
            pgrep -x python3 2>/dev/null || true
            pgrep -x python 2>/dev/null || true
            pgrep -f 'bin/pytest' 2>/dev/null || true
        } | sort -u
    ); do
        if [ -r "/proc/${_peer_pid}/cmdline" ]; then
            _peer_argv="$(tr '\0' '\n' <"/proc/${_peer_pid}/cmdline" 2>/dev/null || true)"
            if printf '%s\n' "$_peer_argv" | grep -qxE 'pytest|.*/bin/pytest'; then
                if ! printf '%s\n' "$_peer_argv" | grep -qx -- '--collect-only'; then
                    _peers="${_peers}[test.sh]   pid ${_peer_pid}  $(readlink "/proc/${_peer_pid}/cwd" 2>/dev/null || echo '(cwd unreadable)')
"
                fi
            fi
        fi
    done
    if [ -n "$_peers" ]; then
        echo "[test.sh] NOTE: another pytest is running on this host:" >&2
        printf '%s' "$_peers" >&2
        echo "[test.sh]   Your run is ISOLATED and cannot corrupt it -- but you" \
            "share this host's cores. Measured 2026-09-05: 349 s alone," \
            "against ~38% in 13 minutes with three suites running. Consider" \
            "waiting if either run is a gate." >&2
    fi
    unset _peers _peer_pid _peer_argv
fi

# The name carries the PID because the whole point is that two runs can
# coexist -- including two in the SAME worktree, which no per-worktree
# database prefix ever fixed.
_run_container="shekel-testrun-$$"

# NO PUBLISHED PORT.  The cluster serves exactly one process on this
# machine, so a port buys nothing and costs a whole failure class: on a
# rootless daemon `dockerd` picks the port inside the container's network
# namespace and rootlesskit then binds that number on the HOST, where a live
# outbound connection often already owns it.  `ip_local_port_range` is
# 32768-60999, byte-identical to docker's publish band, and this host
# routinely holds thousands of sockets in it.  Measured: 5 of 12 container
# starts failed, and 3 of 12 still failed with no port ever recycled, so it
# is not a release race -- it is two allocators in two namespaces sharing one
# number space.  The root daemon does not hit it (0 of 8), which is why this
# only surfaced on moving off it.
#
# A socket has no allocator and no shared namespace: the run names its own
# directory, and it already has a unique name in its PID.
_run_sockdir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/${_run_container}"
# A unix socket path is capped at 107 bytes by the kernel and postgres
# appends `/.s.PGSQL.5432` (14).  Refuse at the door rather than let the
# limit surface as a connection error from deep inside the suite.
# BYTES, not characters: ``${#var}`` counts characters and ``sun_path`` is a
# byte budget, so a multibyte XDG_RUNTIME_DIR would under-count and admit a
# path the kernel then rejects.
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
# 0777 because rootless docker maps the container's postgres user into the
# subuid range, so it cannot write a directory owned by this user.  The
# socket is still private: XDG_RUNTIME_DIR itself is 0700.
chmod 0777 "$_run_sockdir"

# ``-v`` removes the container's ANONYMOUS VOLUME with it.  The baked image
# inherits the base's ``VOLUME /var/lib/postgresql`` declaration even though
# PGDATA lives elsewhere, so every run created a volume that ``docker rm -f``
# left behind: measured 101 of 112 volumes on this daemon dangling, the
# newest in one-second pairs matching this script's own verify+run
# invocations.  Each holds ~0 B, so the cost is unbounded metadata on a
# daemon the homelab stack watches, not disk.
#
# And the removal REPORTS ITS OWN FAILURE.  Discarding the status, stdout and
# stderr made the one step whose entire job is cleanup unable to say it had
# failed: a wedged or unreachable daemon left the container running and the
# wrapper still exited with pytest's status.
_teardown_run_container() {
    docker rm -fv "$_run_container" >/dev/null 2>&1 || true
    if docker inspect "$_run_container" >/dev/null 2>&1; then
        echo "[test.sh] WARNING: $_run_container survived teardown --" \
            "remove it by hand ('docker rm -fv $_run_container') or the" \
            "next run reusing that PID collides with it." >&2
    fi
    # The socket directory outlives the container and sits on a tmpfs, so a
    # leak is bounded by the next reboot rather than by anything that prunes
    # it.  A plain rm suffices ONLY because the mount point is /sockets: the
    # entrypoint chowns /var/run/postgresql to its own subuid-mapped user and
    # sets the sticky bit, after which this user cannot unlink the socket
    # that user created -- measured 5 of 5.
    rm -rf "$_run_sockdir" 2>/dev/null || true
    if [ -e "$_run_sockdir" ]; then
        echo "[test.sh] WARNING: $_run_sockdir survived teardown." >&2
    fi
}

# Forward the signal to pytest FIRST, so the run stops rather than being
# abandoned, then tear down and exit with the conventional status.
# shellcheck disable=SC2329 # invoked only through the INT, TERM and HUP traps below, which shellcheck cannot follow
_stop_run() {
    kill -"$2" "$1" 2>/dev/null || true
    wait "$1" 2>/dev/null || true
    _teardown_run_container
    exit "$3"
}
# EXIT alone is not enough: an untrapped fatal signal terminates bash WITHOUT
# running the EXIT trap, so the container outlives the run and the next one
# collides with nothing but a leaked cluster.  Ctrl-C (INT, delivered to the
# whole process group) is the common way to strand one; HUP is the same class
# and was missing -- closing the terminal on a running suite leaked both the
# container and the socket directory, while this file's own prose claimed
# every exit path was covered.  Adding the arm was the way to make that
# sentence true.
trap '_teardown_run_container' EXIT
trap '_teardown_run_container; exit 130' INT
trap '_teardown_run_container; exit 143' TERM
trap '_teardown_run_container; exit 129' HUP

# ``--network=none`` follows from having no port: the cluster needs no
# network at all, which also takes the harness off the experimental
# gvisor-tap-vsock driver the rootless daemon falls back to when slirp4netns
# is absent.  ``listen_addresses=''`` means it does not even open a TCP
# listener inside its own namespace.
#
# The socket lives at /sockets, NOT the default /var/run/postgresql, because
# the entrypoint chowns that path to a subuid-mapped user and sticky-bits it,
# leaving behind files this user cannot delete.
docker run -d --rm --name "$_run_container" \
    --network=none \
    -e POSTGRES_USER=shekel_user \
    -e POSTGRES_PASSWORD=shekel_pass \
    -e POSTGRES_DB=postgres \
    -e PGDATA=/pgdata-baked \
    -v "${_run_sockdir}:/sockets" \
    "$_image" \
    -c idle_in_transaction_session_timeout=30000 \
    -c lock_timeout=10000 \
    -c statement_timeout=30000 \
    -c fsync=off \
    -c synchronous_commit=off \
    -c full_page_writes=off \
    -c listen_addresses='' \
    -c unix_socket_directories=/sockets >/dev/null

# THE SETTINGS ABOVE ARE A CENSUS OF THE DELETED SHARED CLUSTER'S, not a
# selection from it, and saying so is the point: the compose service that
# ``balance:X-br-4`` removed ran with NINE ``-c`` flags and the per-run branch
# was written with THREE.  That was survivable while this path was opt-in and
# is not now that it is the only one, so the census was taken.
#
# The three TIMEOUTS are carried because the suite QUOTES one of them as a
# mechanism.  ``tests/_test_helpers.py`` (twice) and three migration tests
# state that a conflicting DDL "dies on the cluster's 10-second
# ``lock_timeout``" -- PostgreSQL's default is 0, meaning wait forever, so
# without the flag that becomes a 30-second pytest-timeout naming a timeout
# instead of a lock, with four docstrings telling the author to expect the
# other signature.  ``statement_timeout`` and
# ``idle_in_transaction_session_timeout`` come along for the same reason: a
# runaway is bounded by the cluster, loudly, rather than by the test runner.
#
# The three ``tcp_keepalives_*`` flags are DELIBERATELY NOT carried, and this
# is the one place that says so: they configure TCP sockets, and this cluster
# has none -- ``--network=none``, ``listen_addresses=''`` and a unix socket.
# They would be inert rather than wrong.
#
# THE NON-DURABLE KNOBS ARE WHAT MAKE THIS AFFORDABLE, and leaving them off
# is the difference between a design that pays for itself and one that does
# not.  Measured: the full suite took 753 s in a per-run container against
# 356 s on the shared cluster -- 2.1x -- purely because the baked image runs
# with docker's default durability while docker-compose.dev.yml gives the
# shared test-db `fsync=off`, `synchronous_commit=off` and
# `full_page_writes=off`.  Per-test drop-and-reclone is thousands of DDL
# cycles, and docs/testing-standards.md prices one at 1618 ms with fsync on
# against 31 ms with it off.  The cluster is a throwaway that lives for one
# run, so a crash losing its last transactions costs a re-run and nothing
# else -- exactly the argument the compose file already makes, which is why
# these three are copied from it rather than invented here.
#
# Asking over the socket is safe HERE and would not be in the builder.  The
# entrypoint answers on a socket during its initdb window before the real
# server exists -- but the baked image ships a populated PGDATA, so the
# entrypoint skips initdb entirely and that window never opens.
# scripts/build_test_db_image.py, whose bake container DOES run initdb, is
# the one that still cannot trust a socket.
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
# reach a per-worker database, and urlparse/urlunparse preserve the query, so
# the socket survives that rewrite.
if [ ! -S "${_run_sockdir}/.s.PGSQL.5432" ]; then
    echo "[test.sh] $_run_container reported ready but left no socket" \
        "at ${_run_sockdir}/.s.PGSQL.5432" >&2
    exit 2
fi
export TEST_ADMIN_DATABASE_URL="postgresql://shekel_user:shekel_pass@/postgres?host=${_run_sockdir}"
export TEST_DATABASE_URL="postgresql://shekel_user:shekel_pass@/shekel_test?host=${_run_sockdir}"

echo "[test.sh] private cluster $_run_container on ${_run_sockdir}" \
    "from $_image" >&2

# Local default: skip container-spawning tests (marked ``docker``) so a
# routine run never touches the production Docker daemon that the homelab
# stack + wud/cadvisor/alloy share (see docs/test-harness-isolation.md).
# Opt back in by selecting the marker; DOCKER_HOST is already exported above,
# so on this host the tests/test_deploy conftest guard sees an isolated
# endpoint and the full local opt-in is just:
#   PYTEST_MARKER_EXPR=docker ./scripts/test.sh tests/test_deploy/...
# CI is unaffected: it invokes pytest directly, not this wrapper, so it still
# runs the full set.  An explicit ``-m`` in the caller's arguments takes
# precedence (pytest keeps the last ``-m`` on the command line).
PYTEST_MARKER_EXPR="${PYTEST_MARKER_EXPR:-not docker}"

# NOT `exec`: the container has to be removed after pytest returns, and an
# exec'd process has no after.  pytest's status is preserved and re-raised so
# the wrapper stays transparent to callers and to CI.
#
# BACKGROUNDED, then waited.  Bash does not service a trap while waiting on a
# FOREGROUND child, so with pytest in the foreground a SIGTERM to the wrapper
# did nothing until pytest finished -- measured 20 s of silence -- which broke
# `timeout` and left the SIGKILL escalation leaking a container AND orphaning
# pytest onto PID 1.  ``wait`` is interruptible, so the traps below can
# actually run.
set +e
pytest -m "$PYTEST_MARKER_EXPR" "$@" &
_pytest_pid=$!
trap '_stop_run "$_pytest_pid" INT 130' INT
trap '_stop_run "$_pytest_pid" TERM 143' TERM
trap '_stop_run "$_pytest_pid" HUP 129' HUP
wait "$_pytest_pid"
_pytest_status=$?
set -e
_teardown_run_container
trap - EXIT INT TERM HUP
exit "$_pytest_status"
