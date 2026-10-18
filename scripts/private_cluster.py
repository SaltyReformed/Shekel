#!/usr/bin/env python3
"""A throwaway PostgreSQL cluster of one caller's own: the ONE home of its life.

``scripts/test.sh`` gives every suite run a cluster of its own (``balance:X-br-4``), and
starts and stops it here; the release rehearsal kit (step X-cx's K1, not yet built) is to
start its own here too, so there is one ``docker run``, one readiness rule and one
teardown (the coordinator's K1 leaf-plan ruling, 2026-10-18, point 1).  The caller names the
cluster; it is a container of that name on this run's daemon, reached over a UNIX SOCKET in
a directory of the same name under ``XDG_RUNTIME_DIR``, with no network and no port.

Usage::

    python scripts/private_cluster.py start <name> <image>
    python scripts/private_cluster.py stop <name>

``start`` prints the socket directory, then the container's id, one per line, once the
cluster accepts connections there.  It refuses a name a container already holds, and a
start that fails stops what it made, naming anything that stop left behind; one killed
outright (TERM, HUP, KILL) cannot, which is why the caller NAMES the cluster before
starting it and stops it by that name (``test.sh`` names its cluster after its own PID).
``stop`` removes the container with its anonymous volume and the socket directory, and
names on stderr anything that survived or could not be asked about; it is safe to run on
a cluster that never started, or twice, which is what lets a caller's signal traps call it
whenever they fire.

Exit status: 0 done; 1 ``stop`` left something behind, each named; 2 ``start`` could not
give a cluster that accepts connections (each reason printed, and anything its own stop
left behind), or the command line was wrong; 130 ``start`` was ended by Ctrl-C, having
stopped what it made.

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
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# The superuser the image was baked with (``scripts/build_test_db_image.py``'s
# ``_BUILD_USER``); ``test.sh`` builds its connection URLs with the same name and password.
_USER = "shekel_user"
_PASSWORD = "shekel_pass"

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
# The conventional exit status of a process ended by Ctrl-C (128 + SIGINT).
_INTERRUPTED = 130


class ClusterError(RuntimeError):
    """The cluster could not be started, or is not usable; the message says which and why."""


@dataclass(frozen=True)
class Cluster:
    """A started cluster: its container's id, and the socket directory it is reached by."""

    container: str
    socket_dir: Path


def socket_dir(name: str) -> Path:
    """The socket directory of the cluster ``name``: a directory of that name under
    ``XDG_RUNTIME_DIR`` (``/run/user/<uid>`` when unset or empty), which is 0700, so the
    socket stays private though its own directory must be open to the container."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / name


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    """One docker command, its output captured; a daemon that does not answer within
    :data:`_DOCKER_TIMEOUT_SECONDS` is reported as one, where a shell waited forever.

    Raises:
        ClusterError: when docker cannot be run or does not answer in time.
    """
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True, check=False,
                              timeout=_DOCKER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise ClusterError(f"docker {args[0]} did not answer within "
                           f"{_DOCKER_TIMEOUT_SECONDS}s; the daemon may be wedged") from exc
    except OSError as exc:
        raise ClusterError(f"docker cannot be run: {exc}") from exc


def _present(name: str) -> bool:
    """Whether docker holds a container named exactly ``name``, running or not.  Asked by
    an ANCHORED name filter: docker's ``name`` filter otherwise matches every name that
    CONTAINS it, and ``inspect`` exits 1 alike for a container that is gone and a daemon
    that cannot be asked, so a teardown reading it would call an unreachable daemon clean.

    Raises:
        ClusterError: when docker cannot be asked.
    """
    asked = _docker("ps", "--all", "--quiet", "--filter", f"name=^{re.escape(name)}$")
    if asked.returncode:
        raise ClusterError(f"docker could not be asked whether {name} exists: "
                           f"{(asked.stderr or asked.stdout).strip()}")
    return bool(asked.stdout.strip())


def _readiness_seconds() -> float:
    """The cap on the readiness wait, from ``READINESS_TIMEOUT_SECONDS``.

    Raises:
        ClusterError: when the variable is set to anything but a finite, non-negative
            number of seconds (``nan`` and ``inf`` would make the wait endless).
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


def _refuse_a_socket_dir_too_long(directory: Path) -> None:
    """Refuse a socket directory past the kernel's budget for a socket path, counted in
    BYTES as the kernel counts them (``os.fsencode``): refused at the door rather than let
    the limit surface as a connection error from deep inside the caller.

    Raises:
        ClusterError: when the directory is too long.
    """
    if len(os.fsencode(directory)) > _SOCKET_DIR_BYTES:
        raise ClusterError(f"socket directory {directory} is too long; a unix socket path "
                           f"cannot exceed {_SUN_PATH_BYTES} bytes.  Set XDG_RUNTIME_DIR to "
                           "something shorter.")


def _make_socket_dir(directory: Path) -> None:
    """Make the socket directory afresh, open to the container's postgres user.

    0777 because rootless docker maps the container's postgres user into the subuid range,
    so it cannot write a directory owned by this user.  The socket is still private:
    ``XDG_RUNTIME_DIR`` itself is 0700.  Whatever an earlier cluster of this name left at
    the path, a directory or a file, is removed first.

    Raises:
        ClusterError: when the leftover cannot be removed or the directory made.
    """
    try:
        if directory.is_dir() and not directory.is_symlink():
            shutil.rmtree(directory)
        else:
            directory.unlink(missing_ok=True)
        directory.mkdir(parents=True)
        directory.chmod(0o777)
    except OSError as exc:
        raise ClusterError(f"socket directory {directory} cannot be made: {exc}") from exc


def _run_container(name: str, image: str, directory: Path) -> str:
    """Start the container ``name`` from ``image``; its id.

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
    rest.

    Raises:
        ClusterError: when docker refuses to start it.
    """
    started = _docker(
        "run", "-d", "--rm", "--name", name, "--network=none",
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


def _wait_ready(name: str, directory: Path, seconds: float) -> None:
    """Block until the cluster accepts connections on its socket, and the socket is on this
    side of the mount.

    Asking over the socket is safe for an image that ships a populated PGDATA, as the
    baked test image does: the entrypoint skips initdb, so the window in which its
    temporary server answers on a socket before the real server exists never opens.
    ``scripts/build_test_db_image.py``, whose bake container DOES run initdb, is the one
    that cannot trust a socket.  Readiness and a usable socket are two facts: a container
    can answer ``pg_isready`` and still have left no socket here, and a URL naming a socket
    that does not exist would fail deep inside its caller, pointing at the database.

    Raises:
        ClusterError: when it is not ready within ``seconds``, or left no socket.
    """
    deadline = time.monotonic() + seconds
    while _docker("exec", name, "pg_isready", "-q", "-h", _SOCKETS, "-U", _USER).returncode:
        if time.monotonic() >= deadline:
            raise ClusterError(f"{name} did not accept connections within {seconds:g}s")
        time.sleep(0.1)
    socket_file = directory / _SOCKET_FILE
    if not (socket_file.exists() and stat.S_ISSOCK(socket_file.stat().st_mode)):
        raise ClusterError(f"{name} reported ready but left no socket at {socket_file}")


def start(name: str, image: str) -> Cluster:
    """Start the cluster ``name`` from ``image`` and wait until it accepts connections.

    ``image`` ships a populated PGDATA at :data:`_PGDATA`, as the baked test image does
    (:func:`_wait_ready` says why readiness depends on it).  Nothing is made until the
    readiness cap, the socket path's length and the name are known good: a name a container
    already holds is refused, never taken over, so a start only ever stops what it made.  A
    start refused after that stops what it made, and its error names anything that stop
    left behind.  One ended by Ctrl-C stops what it made and re-raises; what that stop
    could not remove, the caller's own stop names.  One killed outright (TERM, HUP) stops
    nothing, so its caller, which named the cluster, stops it by that name.

    Raises:
        ClusterError: when any step fails, naming it.
        KeyboardInterrupt: when Ctrl-C ends the start, after it stops what it made.
    """
    seconds = _readiness_seconds()
    directory = socket_dir(name)
    _refuse_a_socket_dir_too_long(directory)
    if _present(name):
        raise ClusterError(f"a container named {name} already exists, and a start never "
                           "takes over a name in use: stop that cluster, or name another")
    try:
        _make_socket_dir(directory)
        container = _run_container(name, image, directory)
        _wait_ready(name, directory, seconds)
    except ClusterError as refused:
        survived = stop(name)
        if survived:
            raise ClusterError(f"{refused}.  Stopping it left something behind: "
                               + " ".join(survived)) from refused
        raise
    except KeyboardInterrupt:
        stop(name)
        raise
    return Cluster(container, directory)


def stop(name: str) -> list[str]:
    """Remove the cluster ``name``'s container, with its anonymous volume, and its socket
    directory; one sentence per thing that survived, or that could not be asked about.

    ``-v`` removes the ANONYMOUS VOLUME too: the baked image inherits the base's ``VOLUME
    /var/lib/postgresql`` though PGDATA lives elsewhere, and plain ``docker rm -f`` left one
    behind per run -- measured 101 of 112 volumes on the development daemon dangling, the
    newest in one-second pairs matching a run's own verify and start.  Each holds ~0 B, so
    the cost is unbounded metadata on a daemon the homelab stack watches, not disk.  A
    survivor is NAMED, never discarded: a teardown that cannot say it failed left a wedged
    or unreachable daemon's container running while its caller exited clean, which is why
    whether it survived is asked of :func:`_present`, which tells "gone" from "cannot be
    asked".  The socket directory sits on a tmpfs, so a leak is bounded only by the next
    reboot; a plain removal suffices because it is mounted at ``/sockets`` (see
    :data:`_SOCKETS`).
    """
    survived = []
    try:
        _docker("rm", "-fv", name)
        if _present(name):
            survived.append(f"{name} survived teardown -- remove it by hand ('docker rm -fv "
                            f"{name}') or the next cluster of that name collides with it.")
    except ClusterError as exc:
        survived.append(f"{name} may have survived teardown: {exc}")
    directory = socket_dir(name)
    shutil.rmtree(directory, ignore_errors=True)
    if directory.exists():
        survived.append(f"{directory} survived teardown.")
    return survived


def main(argv: list[str] | None = None) -> int:
    """Start or stop a cluster, as the module says; its exit status."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    starting = commands.add_parser("start", help="start a cluster; print its socket "
                                   "directory, then its container id")
    starting.add_argument("name")
    starting.add_argument("image")
    stopping = commands.add_parser("stop", help="stop a cluster, naming what survived")
    stopping.add_argument("name")
    args = parser.parse_args(argv)
    if args.command == "start":
        try:
            cluster = start(args.name, args.image)
        except ClusterError as exc:
            print(f"[private_cluster] {exc}", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            # The start stopped what it made; a traceback here would say nothing more.
            return _INTERRUPTED
        print(cluster.socket_dir)
        print(cluster.container)
        return 0
    survived = stop(args.name)
    for line in survived:
        print(f"[private_cluster] WARNING: {line}", file=sys.stderr)
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
