#!/usr/bin/env python3
"""A throwaway PostgreSQL cluster of one caller's own: the ONE home of its life.

``scripts/test.sh`` gives every suite run a cluster of its own (``balance:X-br-4``), and
starts and stops it here; the release rehearsal kit (step X-cx's K1, not yet built) is to
start its own here too, so there is one ``docker run``, one readiness rule and one
teardown (the coordinator's K1 leaf-plan ruling, 2026-10-18, point 1).  It is a container on
this run's daemon, reached over a UNIX SOCKET in a directory under ``XDG_RUNTIME_DIR``, with
no network and no port.

A cluster is OWNED BY A TOKEN its caller makes before starting it (the coordinator's ruling on
K1a commit B, 2026-10-19).  The start labels the container with the token as ``docker run``
creates it, and names the socket directory after it, so whatever a start made carries its
token from the instant it exists, and the daemon itself can say which containers are this
caller's.  ``stop`` removes exactly what carries the token and nothing else, so a caller can
stop by its token in EVERY path -- a normal end, a failed or refused start, a signal, a start
killed outright -- and never remove a container another caller made, whatever that container
is named.  The caller still names the container, for the humans reading ``docker ps``;
correctness never reads the name.

Usage::

    python scripts/private_cluster.py token
    python scripts/private_cluster.py start <name> <token> <image>
    python scripts/private_cluster.py stop <token>

``token`` prints a new token.  ``start`` prints the socket directory, then the container's
id, one per line, once the cluster accepts connections there.  A start that fails stops
what it made, naming anything that stop left behind; one killed outright (KILL) cannot,
which is why the caller makes the token before starting and stops by it.  ``stop`` removes
the token's container with its anonymous volume, and its socket directory, and names on
stderr anything that survived or could not be asked about; it is safe to run on a cluster
that never started, or twice, which is what lets a caller's signal traps call it whenever
they fire.

From just before it makes anything until it has handed over a cluster that accepts
connections, or has stopped what it made, the start HOLDS its caller's signals (INT, TERM,
HUP): it acts on one between docker commands, never inside one, and finishes its own stop
before ending BY that signal, naming what the stop left, so a calling shell sees the signal
and stops too.  Every docker command runs in a session of its own, so a terminal's Ctrl-C
reaches this process and never a docker command it is waiting on: aborting the CLI of
``docker run`` does not abort the create, which the daemon finished after the stop had
looked (the delta review of 668b53c99 + bb2f1492a, MEDIUM-A).

Exit status: 0 done; 1 ``stop`` left something behind, each named; 2 ``start`` could not give
a cluster that accepts connections, or refused before making anything (each reason printed,
and anything its own stop left behind), or the command line was wrong.  A start ended by a
held signal ends BY that signal, as Python ends on an unhandled Ctrl-C; ``main`` ends the
process it runs in that way, so a caller in process wants :func:`start`, which raises
:class:`Interrupted` instead.

Environment variables read:
    XDG_RUNTIME_DIR    Where the socket directory is made.  Default ``/run/user/<uid>``.
    READINESS_TIMEOUT_SECONDS  Cap on the wait for the cluster to accept connections.
                       Default 15.
    DOCKER_HOST        The daemon, as docker itself reads it.  Which daemon may take a
                       container per run is decided before this runs (``test.sh``).

Standard library only: ``test.sh`` runs it before any environment is known to hold more.
"""
from __future__ import annotations

import argparse
import math
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType, TracebackType

# The superuser the image was baked with (``scripts/build_test_db_image.py``'s
# ``_BUILD_USER``); ``test.sh`` builds its connection URLs with the same name and password.
_USER = "shekel_user"
_PASSWORD = "shekel_pass"

# The label ``docker run`` puts on the container, its value the caller's token: how the
# daemon says which containers a caller made.  A ``label=<key>=<value>`` filter matches the
# value EXACTLY (measured 2026-10-19 on the rootless daemon: ``abcd1234`` did not list a
# container labelled ``abcd12345``, and ``abcd`` listed neither).
_TOKEN_LABEL = "shekel.private_cluster.token"

# A token is 128 random bits in lowercase hex: never guessed, never reused, and safe as a
# label value, in a container name and as a path component.
_TOKEN_BYTES = 16
_TOKEN_SHAPE = re.compile(rf"[0-9a-f]{{{2 * _TOKEN_BYTES}}}")

# The socket directory is named after the token, so a stop that holds only the token finds
# it, and a token's directory can belong to no other cluster.
_SOCKET_DIR_PREFIX = "shekel-cluster-"

# Where the image keeps its cluster: outside the base image's declared VOLUME, which
# ``docker commit`` does not capture (``scripts/build_test_db_image.py``'s ``_BAKED_PGDATA``).
_PGDATA = "/pgdata-baked"

# Inside the container, the socket directory is mounted HERE and not over the default
# ``/var/run/postgresql``: the entrypoint chowns that path to a subuid-mapped user and
# sticky-bits it, after which this user cannot unlink the socket that user made --
# measured 5 of 5.  At ``/sockets`` a plain ``rm`` removes it.
_SOCKETS = "/sockets"

# PostgreSQL's socket file, which it makes in the socket directory.
_SOCKET_FILE = ".s.PGSQL.5432"

# A unix socket path is capped by the kernel: ``sun_path`` is 108 bytes with its terminator,
# so 107 usable, and postgres appends ``/`` and the socket file to the directory.  BYTES,
# not characters: a multibyte ``XDG_RUNTIME_DIR`` counted in characters would pass a path
# the kernel then rejects (a review measured the boundary: 93 binds, 94 fails).
_SUN_PATH_BYTES = 107
_SOCKET_DIR_BYTES = _SUN_PATH_BYTES - len(f"/{_SOCKET_FILE}".encode())

# The cluster's settings.  A CENSUS of the deleted shared cluster's, not a selection from
# it: the compose service ``balance:X-br-4`` removed ran with TEN ``-c`` flags and the
# per-run branch was first written with THREE, which was survivable while this path was
# opt-in and is not now that it is the only one.  Three flags here are not from it: the
# two socket flags are this cluster's own mechanics, and the lock table's size has its own
# paragraph below.
#
# The three TIMEOUTS are carried because the suite QUOTES one of them as a mechanism.
# ``tests/_test_helpers.py`` (twice) and three migration tests state that a conflicting DDL
# "dies on the cluster's 10-second ``lock_timeout``" -- PostgreSQL's default is 0, wait
# forever, so without the flag that becomes the per-test pytest-timeout naming a timeout
# instead of a lock, with four docstrings telling the author to expect the other signature.
# ``statement_timeout`` and ``idle_in_transaction_session_timeout`` come along for the same
# reason: a runaway is bounded by the cluster, loudly, rather than by the test runner.
#
# The three ``tcp_keepalives_*`` flags are DELIBERATELY NOT carried, and this is the one
# place that says so: they configure TCP sockets, and this cluster has none, so they would
# be inert rather than wrong.  So would the tenth, ``file_copy_method=clone``, which is not
# carried either: it chooses how ``CREATE DATABASE ... STRATEGY FILE_COPY`` copies a
# template's files (the deleted service paired it with a btrfs PGDATA for reflinks), and
# every clone the suite makes is ``STRATEGY WAL_LOG`` (tests/conftest.py).
#
# THE NON-DURABLE KNOBS ARE WHAT MAKE A CLUSTER PER RUN AFFORDABLE.  Measured: the full
# suite took 753 s in a per-run container against 356 s on the shared cluster -- 2.1x --
# purely because the baked image runs with docker's default durability while the shared
# test-db had ``fsync=off``, ``synchronous_commit=off`` and ``full_page_writes=off``.
# Per-test drop-and-reclone is thousands of DDL cycles, and docs/testing-standards.md
# prices one at 1618 ms with fsync on against 31 ms with it off.  The cluster lives for one
# run, so a crash losing its last transactions costs a re-run and nothing else.
#
# THE LOCK TABLE IS SIZED FOR THE SUITE rather than left at PostgreSQL's default of 64
# (finding BAL-570).  The table is shared by the whole cluster and a lock keeps its slot
# until its transaction ends, so what has to fit is every worker's open transaction at the
# same moment.  Two transactions in the suite lock the whole schema at once, and both grow
# with every migration: ``_empty_the_database`` in
# tests/test_scripts/test_init_database_one_transaction.py drops the five application
# schemas, and the first boot it then runs (``init_fresh_database``) creates them again.
# Five tests run that pair side by side in collection order, so ``-n 12`` can hand all
# five to five workers at once.  Measured 2026-09-30 on dev at 337341152 (image
# shekel-test-db:0aa2eb4287c5, PostgreSQL 18.6), from ``pg_locks`` inside each transaction
# and from a sampler reading the whole cluster's ``pg_locks`` through a run (a sampled peak
# is a floor, not the peak):
#
#   the DROP's transaction              2,829 locks
#   the first boot, at its commit       1,519 locks
#   heaviest single backend, full run   2,827 (that DROP; none heavier seen)
#   cluster peak, a run of that file   13,052 (four DROPs, one boot)
#   cluster peak, full run             10,869 (three DROPs, one boot)
#   one transaction can hold, at 64    14,912 before "out of shared memory"
#   one transaction can hold, at 256   55,760
#
# Five of those DROPs in flight are 14,145 of the 14,912 before any other worker holds a
# lock.  That is what the full run that surfaced BAL-570 hit: four of the five failed on
# the DROP with "out of shared memory ... increase max_locks_per_transaction".  At 256, all
# twelve of pytest.ini's workers inside a 2,829-lock transaction at the same moment would
# hold 33,948, so the heaviest transaction can grow to ~4,600 locks (64% more) before even
# that schedule overflows.  It costs 11 MB of shared memory (161 MB against 150).  On
# PostgreSQL 18 the same setting also sizes each backend's fast-path slots (64 to 256),
# which only moves weak locks off the shared table; the DROP's AccessExclusive locks never
# take that path.
#
# This sizes the suite's CONCURRENCY, not the application's need.  Neither production
# compose file sets it, so as far as this repository says production runs 64, where one
# transaction tops out near the 14,912 above (the first boot holds 1,519).  At 256 the
# suite no longer fails when one transaction outgrows that ceiling, so it is no alarm for
# one.
_SETTINGS = (
    "idle_in_transaction_session_timeout=30000",
    "lock_timeout=10000",
    "statement_timeout=30000",
    "fsync=off",
    "synchronous_commit=off",
    "full_page_writes=off",
    "max_locks_per_transaction=256",
    # No TCP listener at all, even inside the container's own namespace.
    "listen_addresses=",
    f"unix_socket_directories={_SOCKETS}",
)

_DEFAULT_READINESS_SECONDS = 15
# How long one docker command may take before the daemon is taken to be wedged.
_DOCKER_TIMEOUT_SECONDS = 60
# The signals by which a caller asks a start to end: Ctrl-C, and what supervisors and a
# closing terminal send.  KILL cannot be held, which is why the caller holds the token.
_CALLERS_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class ClusterError(RuntimeError):
    """The cluster could not be started, or is not usable; the message says which and why."""


class Interrupted(KeyboardInterrupt):
    """A caller's signal ended a start after it began making the cluster.  The start has
    stopped what it made; :attr:`signum` is the signal, and the arguments are what the stop
    could not remove, one sentence each.

    A KeyboardInterrupt, and so not an ``Exception``, for the caller that runs :func:`start`
    in process: this module's own cases, under a pytest that stops its session on a Ctrl-C
    and records an ``Exception`` as one failed case before going on to the next.  As an
    ``Exception`` it would turn that caller's Ctrl-C into a failure it carries on past, which
    is the defect :func:`main` ends by the signal to prevent in a calling shell.  :func:`main`
    itself catches it by name and needs neither base.
    """

    def __init__(self, signum: int, survived: list[str]) -> None:
        super().__init__(*survived)
        self.signum = signum


@dataclass
class _HeldSignals:
    """The caller's signals, HELD while a start makes its cluster and while it stops it.

    A handler that raises would raise inside whatever docker command the start is waiting
    on, and ``subprocess.run`` answers an exception by killing that command: for ``docker
    run`` the daemon finishes the create anyway, after the start's stop has looked (MEDIUM-A).
    So each signal is only RECORDED, and the start acts on it between docker commands
    (:meth:`act`).  A recording handler holds in any caller, where blocking the signals
    would not: a signal mask is per thread, and a caller with a second thread (xdist's) has a
    process-directed signal delivered there instead.  Handlers run in the main thread only,
    so :func:`start` must run there (``signal.signal`` refuses any other).

    On the way out the caller's own handlers are restored, and a signal recorded after the
    last :meth:`act` is raised again under them, so none is lost.
    """

    signals: list[int] = field(default_factory=list)
    _before: dict[int, signal.Handlers | object] = field(default_factory=dict)
    _acted: bool = False

    def __enter__(self) -> _HeldSignals:
        """Hold the caller's signals, keeping its handlers to restore."""
        for signum in _CALLERS_SIGNALS:
            self._before[signum] = signal.signal(signum, self._record)
        return self

    def _record(self, signum: int, _frame: FrameType | None) -> None:
        """The handler: note the signal, and carry on with what was interrupted."""
        self.signals.append(signum)

    def act(self) -> None:
        """End the start here if its caller has sent a signal: raise KeyboardInterrupt, which
        :func:`start` answers by stopping what it made.

        Raises:
            KeyboardInterrupt: when a signal is held.
        """
        if self.signals:
            self._acted = True
            raise KeyboardInterrupt

    def __exit__(self, kind: type[BaseException] | None, value: BaseException | None,
                 traceback: TracebackType | None) -> None:
        """Restore the caller's handlers, and raise again under them a signal recorded after
        the last :meth:`act` of a start that is handing its cluster over."""
        for signum, handler in self._before.items():
            signal.signal(signum, handler)
        if self.signals and not self._acted and kind is None:
            signal.raise_signal(self.signals[0])


@dataclass(frozen=True)
class Cluster:
    """A started cluster: its container's id, and the socket directory it is reached by."""

    container: str
    socket_dir: Path


def new_token() -> str:
    """A new token, for a caller to own one cluster by (the module says how)."""
    return secrets.token_hex(_TOKEN_BYTES)


def socket_dir(token: str) -> Path:
    """The socket directory of the cluster owned by ``token``: a directory named after it
    under ``XDG_RUNTIME_DIR`` (``/run/user/<uid>`` when unset or empty), which is 0700, so
    the socket stays private though its own directory must be open to the container.

    Raises:
        ClusterError: when ``token`` is not one :func:`new_token` makes.  It becomes a path
            component that :func:`stop` removes, so ``..`` or a ``/`` in it would point that
            removal elsewhere.
    """
    if not _TOKEN_SHAPE.fullmatch(token):
        raise ClusterError(f"{token!r} is not a cluster token: one is {2 * _TOKEN_BYTES} "
                           "lowercase hex digits ('private_cluster.py token' makes one)")
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / f"{_SOCKET_DIR_PREFIX}{token}"


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    """One docker command, its output captured; a daemon that does not answer within
    :data:`_DOCKER_TIMEOUT_SECONDS` is reported as one, where a shell waited forever.

    It runs in a SESSION OF ITS OWN, so a terminal's Ctrl-C, which goes to the terminal's
    foreground process group, reaches the caller and never the docker command: the CLI of
    ``docker run`` aborted that way leaves the daemon to finish the create after whatever
    stop the caller then runs has looked.  Measured with the delta review of bb2f1492a's race
    probe on the rootless daemon, 12 rounds a shape: a container left in 4 of 12 rounds of a
    bare start and 3 of 12 in test.sh's shape on 731b2aa08, and in 0 of 12 of each once the
    CLI ran apart and the start held its caller's signals (2026-10-19).

    Its output is decoded with ``errors="replace"``: a byte it prints that is not UTF-8
    becomes U+FFFD in the message that quotes it, where a strict decode raised a
    UnicodeDecodeError that is no ClusterError, so a failed start skipped its own stop and
    ended in a traceback.  Nothing else reads the text for more than that: a container's id
    is hex.

    Raises:
        ClusterError: when docker cannot be run or does not answer in time.
    """
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True,
                              errors="replace", check=False, start_new_session=True,
                              timeout=_DOCKER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise ClusterError(f"docker {args[0]} did not answer within "
                           f"{_DOCKER_TIMEOUT_SECONDS}s; the daemon may be wedged") from exc
    except OSError as exc:
        raise ClusterError(f"docker cannot be run: {exc}") from exc


def _labelled(token: str) -> list[str]:
    """The ids of every container carrying ``token``'s label, running or not.  A daemon
    that cannot be asked is an error, never an empty answer, so a teardown reading it never
    calls an unreachable daemon clean.

    Raises:
        ClusterError: when docker cannot be asked.
    """
    asked = _docker("ps", "--all", "--quiet", "--filter", f"label={_TOKEN_LABEL}={token}")
    if asked.returncode:
        raise ClusterError(f"docker could not be asked which containers carry {token}: "
                           f"{(asked.stderr or asked.stdout).strip()}")
    return asked.stdout.split()


def _readiness_seconds() -> float:
    """The cap on the readiness wait, from ``READINESS_TIMEOUT_SECONDS``.

    Raises:
        ClusterError: when the variable is set to anything but a finite, non-negative number
            of seconds (``nan`` and ``inf`` would make the wait endless).
    """
    given = os.environ.get("READINESS_TIMEOUT_SECONDS", "")
    if not given:
        return _DEFAULT_READINESS_SECONDS
    try:
        seconds = float(given)
    except ValueError as exc:
        raise ClusterError(f"READINESS_TIMEOUT_SECONDS={given!r} is not a number of "
                           "seconds") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise ClusterError(f"READINESS_TIMEOUT_SECONDS={given!r} is not a finite, "
                           "non-negative number of seconds")
    return seconds


def _refuse_a_socket_dir_docker_cannot_bind(directory: Path) -> None:
    """Refuse a socket directory the cluster's socket cannot be bound in and reached at, by
    the one rule on its BYTES (``os.fsencode``, as the kernel and docker are handed them):
    they are UTF-8, and they fit the kernel's budget for a socket path.  Refused at the door,
    where nothing has been made, rather than surfacing later as a connection error from deep
    inside the caller or a readiness wait that runs out its cap.

    UTF-8 first, so the length refusal's message never holds a byte that is not UTF-8, and
    this one quotes each such byte escaped.  A byte that is not UTF-8 reaches the daemon as
    U+FFFD, so it mounts ANOTHER directory and the socket never appears in this one:
    measured twice on the rootless daemon (the reviews of 668b53c99 and bb2f1492a), a real
    start waited out its whole cap, exited 2 and left the replaced directory, which its stop
    could not name.

    Raises:
        ClusterError: when the directory is not UTF-8, or is too long.
    """
    encoded = os.fsencode(directory)
    try:
        encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ClusterError(f"socket directory {encoded.decode('utf-8', 'backslashreplace')} "
                           "is not UTF-8, so docker would mount another directory in its "
                           "place.  Set XDG_RUNTIME_DIR to a path that is.") from exc
    if len(encoded) > _SOCKET_DIR_BYTES:
        raise ClusterError(f"socket directory {directory} is too long; a unix socket path "
                           f"cannot exceed {_SUN_PATH_BYTES} bytes.  Set XDG_RUNTIME_DIR to "
                           "something shorter.")


def _make_socket_dir(directory: Path) -> None:
    """Make the socket directory EXCLUSIVELY, open to the container's postgres user.

    Anything already at the path -- a directory, a file, a symlink -- is refused, never
    taken over: the path is the token's, and a token is new, so whatever is there was made
    by no start of this cluster, and opening a symlink's target to the container would hand
    it a directory elsewhere.  0777 because rootless docker maps the container's postgres
    user into the subuid range, so it cannot write a directory owned by this user.  The
    socket is still private: ``XDG_RUNTIME_DIR`` itself is 0700.

    Raises:
        ClusterError: when anything is at the path, or the directory cannot be made.
    """
    try:
        directory.mkdir(parents=True)
        directory.chmod(0o777)
    except OSError as exc:
        raise ClusterError(f"socket directory {directory} cannot be made: {exc}") from exc


def _run_container(name: str, token: str, image: str, directory: Path) -> str:
    """Start the container ``name`` from ``image``, labelled with ``token`` as it is
    created; its id.

    NO PUBLISHED PORT.  The cluster serves one process on this machine, so a port buys
    nothing and costs a whole failure class: on a rootless daemon ``dockerd`` picks the port
    inside the container's network namespace and rootlesskit then binds that number on the
    HOST, where a live outbound connection often already owns it.  ``ip_local_port_range``
    is 32768-60999, byte-identical to docker's publish band, and this host routinely holds
    thousands of sockets in it.  Measured: 5 of 12 container starts failed, and 3 of 12
    still failed with no port ever recycled, so it is not a release race -- it is two
    allocators in two namespaces sharing one number space.  The root daemon does not hit it
    (0 of 8), which is why this only surfaced on moving off it.  A socket has no allocator
    and no shared namespace: the cluster's directory carries its own name, which its caller
    made unique.

    ``--network=none`` follows from having no port, which also takes the cluster off the
    experimental gvisor-tap-vsock driver the rootless daemon falls back to when slirp4netns
    is absent.  ``--rm`` removes a container whose postgres exits; :func:`stop` removes the
    rest.  The label is given here, to the create itself, so no instant exists in which the
    container is this caller's and does not say so.  A name another container already holds
    makes docker refuse the run ("Conflict"), and the start's stop then removes nothing of
    that container, which does not carry this token.

    Raises:
        ClusterError: when docker refuses to start it.
    """
    started = _docker(
        "run", "-d", "--rm", "--name", name, "--label", f"{_TOKEN_LABEL}={token}",
        "--network=none",
        "-e", f"POSTGRES_USER={_USER}", "-e", f"POSTGRES_PASSWORD={_PASSWORD}",
        "-e", "POSTGRES_DB=postgres", "-e", f"PGDATA={_PGDATA}",
        "-v", f"{directory}:{_SOCKETS}",
        image,
        *(arg for setting in _SETTINGS for arg in ("-c", setting)),
    )
    if started.returncode:
        raise ClusterError(f"docker could not start {name} from {image}: "
                           f"{(started.stderr or started.stdout).strip()}")
    return started.stdout.strip()


def _wait_ready(name: str, container: str, directory: Path, seconds: float,
                held: _HeldSignals) -> None:
    """Block until the cluster accepts connections on its socket, and the socket is on this
    side of the mount; a signal its caller sent ends the wait after the probe it landed in.
    The probe asks the container by its id, which is this start's; ``name`` is for the
    messages.

    Asking over the socket is safe for an image that ships a populated PGDATA, as the
    baked test image does: the entrypoint skips initdb, so the window in which its
    temporary server answers on a socket before the real server exists never opens.
    ``scripts/build_test_db_image.py``, whose bake container DOES run initdb, is the one
    that cannot trust a socket.  Readiness and a usable socket are two facts: a container
    can answer ``pg_isready`` and still have left no socket here, and a URL naming a socket
    that does not exist would fail deep inside its caller, pointing at the database.

    Raises:
        ClusterError: when it is not ready within ``seconds``, or left no socket.
        KeyboardInterrupt: when its caller has sent a signal (:meth:`_HeldSignals.act`).
    """
    deadline = time.monotonic() + seconds
    while True:
        probed = _docker("exec", container, "pg_isready", "-q", "-h", _SOCKETS, "-U", _USER)
        held.act()
        if not probed.returncode:
            break
        if time.monotonic() >= deadline:
            raise ClusterError(f"{name} did not accept connections within {seconds:g}s")
        time.sleep(0.1)
    socket_file = directory / _SOCKET_FILE
    if not (socket_file.exists() and stat.S_ISSOCK(socket_file.stat().st_mode)):
        raise ClusterError(f"{name} reported ready but left no socket at {socket_file}")


def start(name: str, token: str, image: str) -> Cluster:
    """Start a cluster owned by ``token``, its container named ``name``, from ``image``, and
    wait until it accepts connections.

    ``image`` ships a populated PGDATA at :data:`_PGDATA`, as the baked test image does
    (:func:`_wait_ready` says why readiness depends on it).  Nothing is made until the
    readiness cap, the token and the socket path's bytes are known good.  From just before
    the socket directory is made -- the first thing a start makes, the ``docker run`` the
    second -- until the cluster is handed over or stopped, the caller's signals are HELD
    (:class:`_HeldSignals`).  A start that fails after that stops what carries its token,
    and its error names anything that stop left behind.  One ended by a signal stops it too,
    and raises :class:`Interrupted` carrying the signal and what the stop could not remove;
    the signal wins over a failure it met, since the caller asked the start to end.  One
    killed outright (KILL) stops nothing, so its caller, which holds the token, stops by it.

    Once this returns, the cluster is its caller's: a signal recorded after the start's last
    look is raised again under the caller's own handlers, so it reaches the caller, which
    holds the token.  A signal that lands after the start has decided how to end, inside
    its own stop or after, does not change that end.

    Raises:
        ClusterError: when the start is refused or fails, naming why and what its stop left.
        Interrupted: when a signal ends the start once it has begun making the cluster.
    """
    seconds = _readiness_seconds()
    directory = socket_dir(token)
    _refuse_a_socket_dir_docker_cannot_bind(directory)
    with _HeldSignals() as held:
        try:
            _make_socket_dir(directory)
            held.act()
            container = _run_container(name, token, image, directory)
            held.act()
            _wait_ready(name, container, directory, seconds, held)
        except KeyboardInterrupt as interrupted:
            # Raised by ``held.act()``, or in process by the caller's own Ctrl-C handler.
            raise Interrupted(held.signals[0] if held.signals else signal.SIGINT,
                              stop(token)) from interrupted
        except ClusterError as failed:
            survived = stop(token)
            if held.signals:
                raise Interrupted(held.signals[0], survived) from failed
            if survived:
                raise ClusterError(f"{failed}.  Stopping it left something behind: "
                                   + " ".join(survived)) from failed
            raise
    return Cluster(container, directory)


def stop(token: str) -> list[str]:
    """Remove what carries ``token``: its container, with its anonymous volume, and its
    socket directory; one sentence per thing that survived, or that could not be asked about.

    It NEVER removes what does not carry the token: the container is found by its LABEL and
    removed by its id, never by a name, so a container another caller made is left alone
    whatever it is named -- the guarantee the coordinator's MEDIUM-1 asked for, now held by
    the daemon's own record of who made what rather than by a caller deciding when not to
    stop (the ruling on K1a commit B, 2026-10-19).

    ``-v`` removes the ANONYMOUS VOLUME too: the baked image inherits the base's ``VOLUME
    /var/lib/postgresql`` though PGDATA lives elsewhere, and plain ``docker rm -f`` left one
    behind per run -- measured 101 of 112 volumes on the development daemon dangling, the
    newest in one-second pairs matching a run's own verify and start.  Each holds ~0 B, so
    the cost is unbounded metadata on a daemon the homelab stack watches, not disk.  A
    survivor is NAMED, never discarded: a teardown that cannot say it failed left a wedged
    or unreachable daemon's container running while its caller exited clean, which is why
    the containers are asked for again after the removal, by :func:`_labelled`, which tells
    "none" from "cannot be asked".  The socket directory sits on a tmpfs, so a leak is
    bounded only by the next reboot; a plain removal suffices because it is mounted at
    ``/sockets`` (see :data:`_SOCKETS`).  A symlink at its path is named, never followed.

    Raises:
        ClusterError: when ``token`` is not a token (:func:`socket_dir`).
    """
    directory = socket_dir(token)
    survived = []
    try:
        made = _labelled(token)
        if made:
            _docker("rm", "-fv", *made)
            survived.extend(f"container {container} of cluster {token} survived teardown "
                            f"-- remove it by hand ('docker rm -fv {container}')."
                            for container in _labelled(token))
    except ClusterError as exc:
        survived.append(f"the container of cluster {token} may have survived teardown: "
                        f"{exc}")
    shutil.rmtree(directory, ignore_errors=True)
    if directory.exists(follow_symlinks=False):
        survived.append(f"{directory} survived teardown.")
    return survived


def _warn(survived: list[str]) -> None:
    """Name on stderr each thing a stop left behind."""
    for line in survived:
        print(f"[private_cluster] WARNING: {line}", file=sys.stderr)


def _end_by(signum: int) -> int:
    """End this process BY ``signum``, as Python ends on a Ctrl-C nothing handled, so a
    calling shell sees the signal and stops too: an exit status of 130 tells a shell the
    child dealt with the Ctrl-C itself, and the shell carries on with its next command.

    The default disposition skips the interpreter's own flush, and nothing needs it here:
    no arm that ends this way has printed to stdout, and stderr is line-buffered, so each
    warning is written as it is printed.  The status is returned only should the signal not
    end the process, which is CPython's own fallback in ``exit_sigint`` (128 + the signal).
    """
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)
    return 128 + signum


def _start(args: argparse.Namespace) -> int:
    """``start``, as the module says; its exit status."""
    try:
        cluster = start(args.name, args.token, args.image)
    except ClusterError as exc:
        print(f"[private_cluster] {exc}", file=sys.stderr)
        return 2
    except Interrupted as interrupted:
        _warn(list(interrupted.args))
        return _end_by(interrupted.signum)
    print(cluster.socket_dir)
    print(cluster.container)
    return 0


def _stop(args: argparse.Namespace) -> int:
    """``stop``, as the module says; its exit status."""
    try:
        survived = stop(args.token)
    except ClusterError as exc:
        print(f"[private_cluster] {exc}", file=sys.stderr)
        return 2
    _warn(survived)
    return 1 if survived else 0


def main(argv: list[str] | None = None) -> int:
    """Make a token, or start or stop a cluster, as the module says; its exit status."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("token", help="print a new token, to own a cluster by")
    starting = commands.add_parser("start", help="start a cluster owned by a token; print "
                                   "its socket directory, then its container id")
    starting.add_argument("name")
    starting.add_argument("token")
    starting.add_argument("image")
    stopping = commands.add_parser("stop", help="stop what carries a token, naming what "
                                   "survived")
    stopping.add_argument("token")
    args = parser.parse_args(argv)
    if args.command == "token":
        print(new_token())
        return 0
    return _start(args) if args.command == "start" else _stop(args)


if __name__ == "__main__":
    sys.exit(main())
