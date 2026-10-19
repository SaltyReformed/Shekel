"""The rehearsal's bench (step X-cx's K1a-2): one private cluster holding a dump restored once
and cloned per state, and a detached worktree of this repository per state, all torn down on
the way out by a census that names anything left.

What it is built from, each its one home's:

* The image is ``scripts/build_test_db_image.py``'s, built or verified on every run as
  ``test.sh`` does, and the cluster's whole life -- the token, the start, the stop and the URL
  a caller connects by -- is ``scripts/private_cluster.py``'s.  Both run as subprocesses: the
  orchestrator imports nothing from ``scripts`` (the package docstring says why).
* Git runs through :mod:`tools.ci.gitcmd`, which strips the variables that bind git to
  another repository (a hook run from a linked worktree sets them), and every worktree is
  ``git worktree add --detach``, never a checkout.
* The dump is restored by the host's ``pg_restore`` over the cluster's socket, at the URL
  ``private_cluster.py url`` gives; a host with no ``pg_restore``, or one too old for the
  dump, is refused by name.

THE CLUSTER IS OWNED BY A TOKEN made before anything starts, and the stop by it is handed to
the teardown before the start runs, so a failure or a Ctrl-C at any later point stops what the
start made.  One window outlives that stop, the cluster module's own: a start KILLed while its
``docker run`` is in flight leaves its socket directory, empty.

THE BENCH CHOOSES NO DAEMON.  The cluster goes to the daemon docker itself picks: the one
``DOCKER_HOST`` names, or else docker's default context, which on this host is the SYSTEM
daemon that runs production.  ``test.sh`` refuses that daemon and the bench does not (K1a-2's
review, B1: nothing it does can name or stop another container, so it is churn beside
production, not a risk to its data), so run it with ``DOCKER_HOST`` at the rootless daemon:
``export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/docker.sock``.

A DUMP FROM ANOTHER POSTGRESQL MAJOR IS REFUSED before it is restored.  Every custom-format
archive records the server that made it (``Dumped from database version``), so production's
major is read from the dump on every run, never by reaching into production.

EACH SIDE RUNS UNDER A VENV OF ITS OWN PINS, never the developer's: THAT side's
``requirements.txt`` plus the ``time-machine`` pin of its ``requirements-dev.txt`` (the side
probe pins its clock with it), so a pin that moves between base and cut -- a Dependabot change
-- is measured rather than masked by one shared venv.  It is CACHED outside git, under
``${XDG_CACHE_HOME:-~/.cache}/shekel-rehearsal/venvs/``, by the SHA-256 of those lines and the
interpreter's version, so two sides with the same pins share one and a later run builds
nothing; the cache is the one thing a teardown deliberately leaves.  A venv counts as built
only once its marker is written, the last step of a build that succeeded, so one that failed
or was killed is never taken for built: the next run removes it and builds again.  It is
built where it stays, never moved, because a venv's scripts hold its path in their shebangs.
Two runs building the same venv at the same moment would collide in its directory; one kit
runs at a time.

Usage, which brings a bench up and tears it down, printing what each part took::

    python -m tools.rehearsal.bench --dump <pg_dump -Fc file> --state <name>=<revision> ...

Exit status: 0 the bench came up and left nothing; 2 it could not, or it left something, each
thing named.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType

import psycopg
from psycopg import sql

from tools.ci import arcs, gitcmd

_CLUSTER = arcs.REPO / "scripts" / "private_cluster.py"
_IMAGE_BUILDER = arcs.REPO / "scripts" / "build_test_db_image.py"
#: The database the dump is restored into once; each state's is a TEMPLATE clone of it.
PRISTINE = "rehearsal_pristine"
#: A state's name becomes a database name and a directory name, so it is held to both.
_STATE_NAME = re.compile(r"[a-z][a-z0-9_]{0,29}")
#: The line of ``pg_restore -l`` naming the server a custom-format archive was made by.
_DUMPED_FROM = re.compile(r"^;\s+Dumped from database version: (\d+)", re.MULTILINE)
#: ``server_version_num`` is the major times 10,000 plus the minor (180006 is 18.6).
_VERSION_NUM_PER_MAJOR = 10_000
#: The one line of a side's ``requirements-dev.txt`` its venv takes beside ``requirements.txt``.
_TIME_MACHINE_PIN = re.compile(r"^time-machine==\S+$", re.MULTILINE)
#: Written into a venv as the last step of a build that succeeded: what makes it built.
_BUILT_MARKER = ".rehearsal-built"
#: How much of a failed pip's stderr a refusal quotes.
_QUOTED_LINES = 20


class BenchError(RuntimeError):
    """The bench could not be made, or its teardown left something; the message names it."""


@dataclass(frozen=True)
class State:
    """One state of the rehearsal: its name, the commit its worktree holds, the worktree,
    the URL of its own clone of the dump, and the interpreter of its venv."""

    name: str
    commit: str
    worktree: Path
    url: str
    python: Path


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` to its end, its output captured and decoded with ``errors="replace"``.

    A terminal's Ctrl-C reaches the child as it reaches this process (one process group), so
    the child is WAITED FOR and never killed: ``subprocess.run`` SIGKILLs a child 0.25 s after
    the interrupt, which cuts short the child's own answer to it.  For the cluster's start that
    is the leak the start's held signals exist to prevent (``private_cluster.py``: a start killed
    while ``docker run`` is in flight leaves a container its stop could not see).

    Raises:
        BenchError: when ``argv[0]`` cannot be run.
        KeyboardInterrupt: once the child has ended, when a Ctrl-C reached this process.
    """
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, errors="replace")
    except OSError as exc:
        raise BenchError(f"{argv[0]} cannot be run: {exc}") from exc
    with process:
        try:
            out, err = process.communicate()
        except KeyboardInterrupt:
            process.communicate()
            raise
    return subprocess.CompletedProcess(argv, process.returncode, out, err)


def cluster(*args: str) -> str:
    """One ``private_cluster.py`` command, run by this interpreter; its standard output.

    Raises:
        BenchError: when it exits non-zero, with what it said.
    """
    done = _run([sys.executable, str(_CLUSTER), *args])
    if done.returncode:
        raise BenchError(f"private_cluster.py {args[0]} exited {done.returncode}: "
                         f"{(done.stderr or done.stdout).strip()}")
    return done.stdout


def image_tag() -> str:
    """The test-db image's tag, built or verified first, as ``test.sh`` has it done.

    Raises:
        BenchError: when the image cannot be prepared.
    """
    done = _run([sys.executable, str(_IMAGE_BUILDER), "--json"])
    if done.returncode:
        raise BenchError(f"build_test_db_image.py exited {done.returncode}: "
                         f"{(done.stderr or done.stdout).strip()}")
    tag = re.search(r'^\{"tag": "([^"]+)"', done.stdout, re.MULTILINE)
    if tag is None:
        raise BenchError(f"build_test_db_image.py named no tag: {done.stdout.strip()}")
    return tag.group(1)


def dump_major(listing: str) -> int:
    """The PostgreSQL major that made a dump, from its ``pg_restore -l`` listing.

    Raises:
        BenchError: when the listing names no server, so it is no custom-format archive.
    """
    found = _DUMPED_FROM.search(listing)
    if found is None:
        raise BenchError("the dump's listing names no server it was dumped from, so it is not "
                         "a pg_dump -Fc archive")
    return int(found.group(1))


def refuse_another_major(dump: int, server_version_num: int) -> None:
    """Refuse a dump whose PostgreSQL major is not the cluster's: its rows would be read by
    another server than the one that wrote them.

    Raises:
        BenchError: naming both majors.
    """
    server = server_version_num // _VERSION_NUM_PER_MAJOR
    if dump != server:
        raise BenchError(f"the dump was made by PostgreSQL {dump} and the cluster runs "
                         f"PostgreSQL {server}; the image must match production's major")


def side_pins(worktree: Path) -> str:
    """The pins a side's venv is built from: its ``requirements.txt``, then the ``time-machine``
    line of its ``requirements-dev.txt``.

    Raises:
        BenchError: when either file cannot be read, or no ``time-machine`` pin is there.
    """
    try:
        runtime = (worktree / "requirements.txt").read_text(encoding="utf-8")
        dev = (worktree / "requirements-dev.txt").read_text(encoding="utf-8")
    except OSError as exc:
        raise BenchError(f"{worktree} cannot give its pins: {exc}") from exc
    pin = _TIME_MACHINE_PIN.search(dev)
    if pin is None:
        raise BenchError(f"{worktree / 'requirements-dev.txt'} pins no time-machine, which the "
                         "side probe pins its clock with")
    return runtime.rstrip("\n") + "\n" + pin.group(0) + "\n"


def venv_for(worktree: Path) -> Path:
    """The interpreter of the venv a side runs under: the cached one for its pins, or one
    built now (the module says how the cache is keyed and when a venv counts as built).

    Raises:
        BenchError: when the venv cannot be made or its pins cannot be installed.
    """
    pins = side_pins(worktree)
    key = hashlib.sha256(f"{sys.version}\n{pins}".encode()).hexdigest()[:16]
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    venv = cache / "shekel-rehearsal" / "venvs" / key
    python = venv / "bin" / "python"
    if (venv / _BUILT_MARKER).is_file():
        return python
    shutil.rmtree(venv, ignore_errors=True)
    venv.parent.mkdir(parents=True, exist_ok=True)
    made = _run([sys.executable, "-m", "venv", str(venv)])
    if made.returncode:
        raise BenchError(f"python -m venv {venv} exited {made.returncode}: "
                         f"{made.stderr.strip()}")
    pinned = venv / "rehearsal-requirements.txt"
    pinned.write_text(pins, encoding="utf-8")
    installed = _run([str(python), "-m", "pip", "install", "--quiet",
                      "--disable-pip-version-check", "-r", str(pinned)])
    if installed.returncode:
        quoted = "\n".join(installed.stderr.strip().splitlines()[-_QUOTED_LINES:])
        raise BenchError(f"pip could not install {worktree}'s pins into {venv} (exit "
                         f"{installed.returncode}):\n{quoted}")
    (venv / _BUILT_MARKER).write_text(pins, encoding="utf-8")
    return python


def parse_states(given: list[str]) -> dict[str, str]:
    """``name=revision`` pairs as a mapping, each name a valid state name, none twice.

    Raises:
        BenchError: naming the first pair that is not one.
    """
    states: dict[str, str] = {}
    for pair in given:
        name, sep, revision = pair.partition("=")
        if not sep or not revision or not _STATE_NAME.fullmatch(name):
            raise BenchError(f"{pair!r} is not <name>=<revision>, the name lowercase letters, "
                             "digits and _ from a letter, at most 30")
        if name in states:
            raise BenchError(f"state {name!r} is given twice")
        states[name] = revision
    if not states:
        raise BenchError("no state given")
    return states


@dataclass
class Bench:
    """The bench as a context manager: entering makes it, leaving tears it down and refuses
    (:class:`BenchError`) when anything is left.  ``states`` maps each state's name to the
    revision of this repository its worktree holds."""

    dump: Path
    states: dict[str, str]
    #: What each part took, in seconds, by part.
    timings: dict[str, float] = field(default_factory=dict)
    #: Each state, once made.
    made: dict[str, State] = field(default_factory=dict)
    socket_dir: Path | None = None
    _stack: ExitStack = field(default_factory=ExitStack)
    _left: list[str] = field(default_factory=list)

    def _timed(self, part: str, began: float) -> None:
        """Record that ``part`` took the time since ``began``."""
        self.timings[part] = time.monotonic() - began

    def __enter__(self) -> Bench:
        """Make the bench; a failure, or a Ctrl-C, tears down what was made and re-raises.

        Raises:
            BenchError: when any part cannot be made, or the teardown left something.
            BaseException: whatever else ended the making (a Ctrl-C's KeyboardInterrupt),
                again once the teardown has run.
        """
        try:
            self._make()
        except BaseException:
            self._tear_down()
            raise
        return self

    def __exit__(self, kind: type[BaseException] | None, value: BaseException | None,
                 traceback: TracebackType | None) -> None:
        """Tear the bench down.

        Raises:
            BenchError: when the teardown left something, each thing named.
        """
        self._tear_down()

    def _make(self) -> None:
        """Make each part in turn, handing the teardown each one's undoing as it is made."""
        began = time.monotonic()
        image = image_tag()
        self._timed("image", began)
        scratch = Path(tempfile.mkdtemp(prefix="shekel-rehearsal-"))
        self._stack.callback(self._census, scratch)
        self._stack.callback(shutil.rmtree, scratch, ignore_errors=True)
        token = cluster("token").strip()
        self._stack.callback(self._stop_cluster, token)
        began = time.monotonic()
        socket_dir, _container = cluster(
            "start", "--", f"shekel-rehearsal-{os.getpid()}-{token[:8]}", token,
            image).splitlines()
        self.socket_dir = Path(socket_dir)
        self._timed("start", began)
        self._restore()
        for name, revision in self.states.items():
            self._make_state(name, revision, scratch / name)

    def _url(self, database: str) -> str:
        """The URL of ``database`` on this bench's cluster (``private_cluster.py url``)."""
        return cluster("url", str(self.socket_dir), database).strip()

    def _restore(self) -> None:
        """Restore the dump into :data:`PRISTINE`, once its major is known to be the cluster's.

        Raises:
            BenchError: when the dump is another major's, or cannot be read or restored.
        """
        listed = _run(["pg_restore", "--list", str(self.dump)])
        if listed.returncode:
            raise BenchError(f"pg_restore cannot read {self.dump}: {listed.stderr.strip()}")
        admin = self._url("postgres")
        with psycopg.connect(admin, autocommit=True) as connection:
            refuse_another_major(dump_major(listed.stdout), connection.info.server_version)
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(PRISTINE)))
        began = time.monotonic()
        restored = _run(["pg_restore", "--exit-on-error", "--no-owner", "--no-privileges",
                         "--dbname", self._url(PRISTINE), str(self.dump)])
        if restored.returncode:
            raise BenchError(f"pg_restore of {self.dump} exited {restored.returncode}: "
                             f"{restored.stderr.strip()}")
        self._timed("restore", began)

    def _make_state(self, name: str, revision: str, worktree: Path) -> None:
        """Clone :data:`PRISTINE` for state ``name``, add its detached ``worktree``, and find
        or build its venv.

        Raises:
            BenchError: when the clone, the worktree or the venv cannot be made.
        """
        database = f"rehearsal_{name}"
        began = time.monotonic()
        with psycopg.connect(self._url("postgres"), autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(database), sql.Identifier(PRISTINE)))
        self._timed(f"clone {name}", began)
        began = time.monotonic()
        self._stack.callback(self._remove_worktree, worktree)
        try:
            gitcmd.git(arcs.REPO, "worktree", "add", "--detach", str(worktree), revision)
            commit = gitcmd.git(worktree, "rev-parse", "HEAD").strip()
        except gitcmd.GitError as exc:
            raise BenchError(f"state {name}'s worktree at {revision!r}: {exc}") from exc
        self._timed(f"worktree {name}", began)
        began = time.monotonic()
        python = venv_for(worktree)
        self._timed(f"venv {name}", began)
        self.made[name] = State(name, commit, worktree, self._url(database), python)

    def _remove_worktree(self, worktree: Path) -> None:
        """Remove one state's worktree; a refusal is noted for the census, which names it."""
        if not worktree.exists():
            return
        try:
            gitcmd.git(arcs.REPO, "worktree", "remove", "--force", str(worktree))
        except gitcmd.GitError as exc:
            self._left.append(f"worktree {worktree} could not be removed: {exc}")

    def _stop_cluster(self, token: str) -> None:
        """Stop what carries ``token``; what the stop names as surviving is noted."""
        done = _run([sys.executable, str(_CLUSTER), "stop", token])
        if done.returncode:
            self._left.append(f"private_cluster.py stop exited {done.returncode}: "
                              f"{done.stderr.strip()}")

    def _census(self, scratch: Path) -> None:
        """The teardown's last step: the scratch directory, and with it every worktree path,
        is gone, and git lists no worktree under it; anything left is noted."""
        listed = gitcmd.run(arcs.REPO, "worktree", "list", "--porcelain")
        under = str(scratch) + os.sep
        self._left.extend(f"git still lists worktree {path}" for path in (
            line.removeprefix("worktree ") for line in listed.stdout.splitlines()
            if line.startswith("worktree ")) if path.startswith(under))
        if scratch.exists():
            self._left.append(f"{scratch} survived teardown")

    def _tear_down(self) -> None:
        """Undo every part made, in reverse, the census last.

        Raises:
            BenchError: naming everything left.
        """
        began = time.monotonic()
        self._stack.close()
        self._timed("teardown", began)
        if self._left:
            raise BenchError("the teardown left something: " + "; ".join(self._left))


def main(argv: list[str] | None = None) -> int:
    """Bring a bench up on a dump and tear it down, printing what each part took."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dump", type=Path, required=True, help="a pg_dump -Fc archive")
    parser.add_argument("--state", action="append", default=[], metavar="NAME=REVISION",
                        help="a state, its worktree at REVISION of this repository")
    args = parser.parse_args(argv)
    try:
        bench = Bench(args.dump, parse_states(args.state))
        with bench:
            for state in bench.made.values():
                print(f"state {state.name}: {state.commit} at {state.worktree}, "
                      f"under {state.python}")
    except BenchError as exc:
        print(f"[rehearsal] {exc}", file=sys.stderr)
        return 2
    for part, seconds in bench.timings.items():
        print(f"{part}: {seconds:.2f} s")
    print("teardown left nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
