"""The throwaway cluster's one home starts a cluster and stops it leaving nothing behind.

``scripts/private_cluster.py`` holds the life of the cluster ``scripts/test.sh`` gives every
suite run, and that the release rehearsal kit is to start (step X-cx's K1a).  These drive it
with a stub ``docker`` on ``PATH``, so they need no daemon, no image and no database; the
wrapper's own cases (``test_test_runner.py``) drive the same module through ``test.sh``.
What a REAL cluster does is graded by every suite run, which starts one here.
"""
from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts import private_cluster

_NAME = "shekel-testrun-4242"
_IMAGE = "stub-image:test"
_MODULE = Path(private_cluster.__file__)
_PRESENT = "ps --all --quiet --filter name=^shekel\\-testrun\\-4242$"
_CONTAINER_LEFT = (f"{_NAME} survived teardown -- remove it by hand ('docker rm -fv {_NAME}') "
                   "or the next cluster of that name collides with it.")


@dataclass(frozen=True)
class _Behaviour:
    """How the stub ``docker`` behaves; each default is a daemon that works.

    ``run`` makes the container (unless ``run_fails``) and the socket in the directory
    mounted at ``/sockets`` as postgres would (unless ``make_socket`` is false), then, when
    it ``pins_dir``, takes the write bit off that directory, so the socket in it cannot be
    unlinked and the directory survives a stop; ``rm`` removes the container unless it
    ``survives``; ``ps`` lists it while it exists, or exits 1 as an ``unreachable`` daemon
    does; ``exec`` (the readiness probe) answers ``ready``; the command named ``hangs_on``
    sleeps past any timeout a case sets; anything else succeeds.
    """

    run_fails: bool = False
    ready: bool = True
    make_socket: bool = True
    pins_dir: bool = False
    survives: bool = False
    unreachable: bool = False
    hangs_on: str = ""


_WORKS = _Behaviour()


@dataclass(frozen=True)
class _Stub:
    """A case's stub ``docker``: it lives in ``bin``, records each command it is given in
    ``docker-calls``, and holds its one container as the file ``container``."""

    home: Path

    @property
    def binaries(self) -> Path:
        """The directory the stub is put first on ``PATH`` from."""
        return self.home / "bin"

    @property
    def held(self) -> Path:
        """The file that stands for the stub's container while it exists."""
        return self.home / "container"

    def calls(self) -> list[str]:
        """Every command the stub was given, in order; none if it was never run."""
        recorded = self.home / "docker-calls"
        if not recorded.exists():
            return []
        return recorded.read_text(encoding="utf-8", errors="surrogateescape").splitlines()

    def write(self, behaviour: _Behaviour) -> None:
        """Write the stub ``docker``, behaving as ``behaviour`` says."""
        self.binaries.mkdir()
        bind = ("        listener.bind(os.path.join(host, '.s.PGSQL.5432'))"
                if behaviour.make_socket else "        pass")
        recorded = self.home / "docker-calls"
        (self.binaries / "docker").write_text(
            "#!/usr/bin/env python3\n"
            "import os, socket, sys, time\n"
            "args = sys.argv[1:]\n"
            # surrogateescape: a socket path may hold a byte that is not UTF-8.
            f"open({str(recorded)!r}, 'a', encoding='utf-8', errors='surrogateescape')"
            ".write(' '.join(args) + chr(10))\n"
            "cmd = args[0] if args else ''\n"
            f"if cmd == {behaviour.hangs_on!r}:\n"
            "    time.sleep(30)\n"
            "if cmd == 'run':\n"
            f"    if {behaviour.run_fails!r}:\n"
            "        print('no such image', file=sys.stderr)\n"
            "        sys.exit(125)\n"
            f"    open({str(self.held)!r}, 'w').close()\n"
            "    for mount in [args[i + 1] for i, a in enumerate(args) if a == '-v']:\n"
            "        host = mount.rsplit(':', 1)[0]\n"
            "        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
            # Bound and deliberately NOT unlinked: the file must outlive the stub.
            f"{bind}\n"
            f"        if {behaviour.pins_dir!r}:\n"
            "            os.chmod(host, 0o555)\n"
            "    print('c0ffee')\n"
            f"elif cmd == 'rm' and not {behaviour.survives!r} and os.path.exists("
            f"{str(self.held)!r}):\n"
            f"    os.remove({str(self.held)!r})\n"
            "elif cmd == 'ps':\n"
            f"    if {behaviour.unreachable!r}:\n"
            "        print('Cannot connect to the Docker daemon', file=sys.stderr)\n"
            "        sys.exit(1)\n"
            f"    if os.path.exists({str(self.held)!r}):\n"
            "        print('c0ffee')\n"
            "elif cmd == 'exec':\n"
            f"    sys.exit(0 if {behaviour.ready!r} else 1)\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )
        (self.binaries / "docker").chmod(0o755)


@pytest.fixture(name="runtime", autouse=True)
def _runtime(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A SHORT throwaway ``XDG_RUNTIME_DIR`` for EVERY case, so none can reach the real one:
    the socket directory is made under it, and pytest's ``tmp_path`` alone is most of a
    socket path's 107-byte budget.  The readiness cap is 0, so a stub that is not ready
    fails the wait at once; a ready one answers the first probe, which no cap cuts short."""
    runtime = Path(tempfile.mkdtemp(prefix="skrt", dir="/tmp"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "0")
    yield runtime
    shutil.rmtree(runtime, ignore_errors=True)


@pytest.fixture(name="docker")
def _docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., _Stub]:
    """Put a stub ``docker`` first on ``PATH``, behaving as the case asks; the stub."""

    def install(behaviour: _Behaviour = _WORKS) -> _Stub:
        """Write the stub ``docker`` and put it first on ``PATH``; the stub."""
        stub = _Stub(tmp_path)
        stub.write(behaviour)
        monkeypatch.setenv("PATH", f"{stub.binaries}:/usr/bin:/bin")
        return stub

    return install


def _unpin(directory: Path) -> None:
    """Give a directory a stub pinned back its write bit, so the case's cleanup removes it."""
    if directory.exists():
        directory.chmod(0o700)


def test_a_started_cluster_is_reached_by_its_socket_and_stopping_it_leaves_nothing(
        docker, runtime, capsys):
    """``start`` asks whether the name is free, removes what an earlier cluster of the name
    left at the socket path, makes the directory open to the container (0777), starts the
    container, probes it over ``/sockets`` and prints the socket directory, then the
    container's id; the socket is in it.  ``stop`` removes the container with its volume
    and the directory, asks again by the exact name, names nothing, and exits 0."""
    stub = docker()
    (runtime / _NAME).mkdir()
    (runtime / _NAME / "left-by-an-earlier-run").write_text("", encoding="utf-8")
    assert private_cluster.main(["start", _NAME, _IMAGE]) == 0
    assert capsys.readouterr().out.splitlines() == [str(runtime / _NAME), "c0ffee"]
    assert (runtime / _NAME / ".s.PGSQL.5432").is_socket()
    assert not (runtime / _NAME / "left-by-an-earlier-run").exists()
    assert (runtime / _NAME).stat().st_mode & 0o777 == 0o777
    assert private_cluster.main(["stop", _NAME]) == 0
    assert capsys.readouterr().err == ""
    assert not (runtime / _NAME).exists()
    calls = stub.calls()
    assert [calls[0], calls[2], calls[3], calls[4]] == [
        _PRESENT, f"exec {_NAME} pg_isready -q -h /sockets -U shekel_user", f"rm -fv {_NAME}",
        _PRESENT]
    assert calls[1].startswith("run ") and len(calls) == 5


def test_the_run_is_the_one_the_suite_reads_argument_for_argument(docker, runtime):
    """The whole ``docker run``, as one literal: detached, removed when postgres exits, no
    network and no published port, the baked image's user, password, database and PGDATA,
    the socket directory mounted at ``/sockets`` (never the entrypoint's own
    ``/var/run/postgresql``), and every ``-c`` setting the suite relies on, the non-durable
    three among them."""
    stub = docker()
    private_cluster.start(_NAME, _IMAGE)
    assert stub.calls()[1].split() == [
        "run", "-d", "--rm", "--name", _NAME, "--network=none",
        "-e", "POSTGRES_USER=shekel_user", "-e", "POSTGRES_PASSWORD=shekel_pass",
        "-e", "POSTGRES_DB=postgres", "-e", "PGDATA=/pgdata-baked",
        "-v", f"{runtime / _NAME}:/sockets", _IMAGE,
        "-c", "idle_in_transaction_session_timeout=30000", "-c", "lock_timeout=10000",
        "-c", "statement_timeout=30000", "-c", "fsync=off", "-c", "synchronous_commit=off",
        "-c", "full_page_writes=off", "-c", "max_locks_per_transaction=256",
        "-c", "listen_addresses=", "-c", "unix_socket_directories=/sockets"]


@pytest.mark.parametrize(("behaviour", "said"), [
    (_Behaviour(run_fails=True), f"docker could not start {_NAME} from {_IMAGE}: no such image"),
    (_Behaviour(ready=False), f"{_NAME} did not accept connections within 0s"),
    (_Behaviour(make_socket=False), f"{_NAME} reported ready but left no socket at "),
], ids=["docker refuses", "never ready", "ready without a socket"])
def test_a_start_that_fails_stops_what_it_started(docker, runtime, capsys, behaviour, said):
    """Each way a start can fail once it has made something exits 2 naming it, and leaves
    no container and no socket directory: the start removes what it made before it
    reports."""
    stub = docker(behaviour)
    assert private_cluster.main(["start", _NAME, _IMAGE]) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.startswith(f"[private_cluster] {said}")
    assert "left something behind" not in out.err
    assert f"rm -fv {_NAME}" in stub.calls()
    assert not (runtime / _NAME).exists()
    assert not stub.held.exists()


@pytest.mark.parametrize("pins_dir", [False, True],
                         ids=["the container", "the container and the directory"])
def test_a_failed_start_names_what_its_own_stop_left_behind(docker, runtime, capsys, pins_dir):
    """The review's MEDIUM-2: a start that fails and then cannot remove what it made names
    the failure AND every survivor, never the failure alone, so a caller that trusts its
    exit 2 is told the cluster is still there; with two survivors, both are named."""
    docker(_Behaviour(ready=False, survives=True, pins_dir=pins_dir))
    left = f"{_CONTAINER_LEFT} {runtime / _NAME} survived teardown." if pins_dir else (
        _CONTAINER_LEFT)
    try:
        assert private_cluster.main(["start", _NAME, _IMAGE]) == 2
        assert capsys.readouterr().err == (
            f"[private_cluster] {_NAME} did not accept connections within 0s.  Stopping it "
            f"left something behind: {left}\n")
    finally:
        _unpin(runtime / _NAME)


def test_a_name_a_container_already_holds_is_refused_and_never_taken_over(
        docker, runtime, capsys):
    """The review's LOW-7: a container already holding the name is not removed, its socket
    directory is not touched, and nothing is started; the start exits 3, the status that
    tells its caller nothing of this start exists to stop (the delta review's MEDIUM-1)."""
    stub = docker()
    stub.held.write_text("", encoding="utf-8")
    (runtime / _NAME).mkdir()
    (runtime / _NAME / ".s.PGSQL.5432").write_text("", encoding="utf-8")
    assert private_cluster.main(["start", _NAME, _IMAGE]) == 3
    assert capsys.readouterr().err == (
        f"[private_cluster] a container named {_NAME} already exists, and a start never "
        "takes over a name in use: stop that cluster, or name another\n")
    assert stub.calls() == [_PRESENT]
    assert (runtime / _NAME / ".s.PGSQL.5432").exists()


def test_a_daemon_that_cannot_say_whether_the_name_is_free_refuses_the_start(
        docker, runtime, capsys):
    """Asking whether the name is free is the last thing before anything is made, so a
    daemon that cannot be asked refuses the start with exit 3 too: nothing was made, and a
    stop by the name could not even be told what it removed."""
    stub = docker(_Behaviour(unreachable=True))
    assert private_cluster.main(["start", _NAME, _IMAGE]) == 3
    assert capsys.readouterr().err == (
        f"[private_cluster] docker could not be asked whether {_NAME} exists: Cannot "
        "connect to the Docker daemon\n")
    assert stub.calls() == [_PRESENT]
    assert not (runtime / _NAME).exists()


def test_a_leftover_file_at_the_socket_path_is_removed_not_a_traceback(docker, runtime,
                                                                        capsys):
    """The review's LOW-8: a FILE left where the socket directory goes is removed, as the
    shell's ``rm -rf`` removed it, and the start goes on."""
    docker()
    (runtime / _NAME).write_text("", encoding="utf-8")
    assert private_cluster.main(["start", _NAME, _IMAGE]) == 0
    assert (runtime / _NAME / ".s.PGSQL.5432").is_socket()
    capsys.readouterr()


def test_a_symlink_at_the_socket_path_is_replaced_and_its_target_left(docker, runtime,
                                                                       capsys):
    """A SYMLINK left where the socket directory goes is removed as a link, as the shell's
    ``rm -rf`` removed one: the start makes a real directory in its place, and the directory
    it pointed at keeps what it held.  Followed as a directory, it would be refused instead
    (``shutil.rmtree`` will not remove a symlink)."""
    docker()
    target = runtime / "elsewhere"
    target.mkdir()
    (target / "kept").write_text("", encoding="utf-8")
    (runtime / _NAME).symlink_to(target)
    assert private_cluster.main(["start", _NAME, _IMAGE]) == 0
    capsys.readouterr()
    assert not (runtime / _NAME).is_symlink()
    assert (runtime / _NAME / ".s.PGSQL.5432").is_socket()
    assert (target / "kept").exists()


@pytest.mark.parametrize("blocked", ["leftover", "runtime"],
                         ids=["an unremovable leftover", "an unwritable runtime directory"])
def test_a_socket_directory_that_cannot_be_made_is_named_never_a_traceback(
        docker, runtime, capsys, blocked):
    """A socket directory that cannot be made -- an earlier one this user cannot empty, or a
    ``XDG_RUNTIME_DIR`` this user cannot write -- exits 2 naming the path and why, with what
    the start's own stop then left; it never escapes as an exception."""
    stub = docker()
    held = runtime / _NAME
    if blocked == "leftover":
        held.mkdir()
        (held / "left-behind").write_text("", encoding="utf-8")
        held.chmod(0o500)
    else:
        runtime.chmod(0o500)
    try:
        assert private_cluster.main(["start", _NAME, _IMAGE]) == 2
        err = capsys.readouterr().err
    finally:
        runtime.chmod(0o700)
        _unpin(held)
    assert err.startswith(f"[private_cluster] socket directory {held} cannot be made: "
                          "[Errno 13] Permission denied")
    assert err.endswith(f"Stopping it left something behind: {held} survived teardown.\n"
                        if blocked == "leftover" else f"'{held}'\n")
    assert f"rm -fv {_NAME}" in stub.calls()


def _wait_for(stub: _Stub, command: str) -> None:
    """Wait until the stub has been given ``command``, or fail the case."""
    deadline = time.monotonic() + 20
    while not any(call.split()[0] == command for call in stub.calls()):
        if time.monotonic() >= deadline:
            pytest.fail(f"the start never reached docker {command}: {stub.calls()}")
        time.sleep(0.05)


_WARNED_CONTAINER = f"[private_cluster] WARNING: {_CONTAINER_LEFT}\n"


@pytest.mark.parametrize(("behaviour", "warned"), [
    (_Behaviour(ready=False), ""),
    (_Behaviour(ready=False, survives=True), _WARNED_CONTAINER),
    (_Behaviour(ready=False, survives=True, pins_dir=True),
     _WARNED_CONTAINER + "[private_cluster] WARNING: {directory} survived teardown.\n"),
    (_Behaviour(hangs_on="ps"), ""),
], ids=["during the readiness wait", "its stop leaves the container",
        "its stop leaves the container and the directory", "before anything is made"])
def test_ctrl_c_ends_a_start_by_sigint_once_it_has_stopped_what_it_made(
        docker, runtime, behaviour, warned):
    """The review's LOW-5 and the delta review's LOW-2: Ctrl-C to the start's whole process
    group, as a terminal sends it, stops what the start made, NAMES each thing that stop
    could not remove, and ends the start BY SIGINT with no traceback, so the shell that ran
    it stops too: an exit status of 130 tells a shell the child dealt with the Ctrl-C, and it
    went on to its next command.  Before anything is made there is nothing to stop or remove.

    The case's own process may have SIGINT IGNORED (a backgrounded pytest inherits that),
    and an ignored signal is inherited across exec: bash cannot take back one ignored when
    it started, and Python leaves it ignored, so ``env --default-signal`` gives the shell the
    default first.
    """
    stub = docker(behaviour)
    with subprocess.Popen(
            ["env", "--default-signal=INT", "bash", "-c",
             '"$0" "$1" start "$2" "$3"; echo "carried on: $?"',
             sys.executable, str(_MODULE), _NAME, _IMAGE],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
            env={**os.environ, "READINESS_TIMEOUT_SECONDS": "30"}) as shell:
        try:
            _wait_for(stub, behaviour.hangs_on or "exec")
            os.killpg(shell.pid, signal.SIGINT)
            out, err = shell.communicate(timeout=30)
        finally:
            if shell.poll() is None:
                os.killpg(shell.pid, signal.SIGKILL)
                shell.communicate()
            _unpin(runtime / _NAME)
    made = not behaviour.hangs_on
    assert (shell.returncode, out, err) == (
        -signal.SIGINT, "", warned.format(directory=runtime / _NAME))
    assert stub.held.exists() is behaviour.survives
    assert (runtime / _NAME).exists() is behaviour.pins_dir
    assert (f"rm -fv {_NAME}" in stub.calls()) is made


def test_stop_names_a_container_that_survived_and_exits_1(docker, runtime, capsys):
    """The planted leftover: docker still lists the container by its exact name after its
    removal, so ``stop`` names it and how to remove it, and exits 1; it never reports
    clean."""
    docker(_Behaviour(survives=True)).held.write_text("", encoding="utf-8")
    assert private_cluster.main(["stop", _NAME]) == 1
    assert capsys.readouterr().err == f"[private_cluster] WARNING: {_CONTAINER_LEFT}\n"
    assert not (runtime / _NAME).exists()


@pytest.mark.parametrize(("behaviour", "said"), [
    (_Behaviour(unreachable=True), f"docker could not be asked whether {_NAME} exists: Cannot "
                                   "connect to the Docker daemon"),
    (_Behaviour(hangs_on="rm"), "docker rm did not answer within 1s; the daemon may be wedged"),
], ids=["unreachable", "wedged"])
def test_stop_names_a_daemon_it_cannot_ask_never_reporting_clean(docker, monkeypatch,
                                                                behaviour, said):
    """A daemon that cannot be asked whether the container survived (``inspect`` exited 1
    alike for "gone" and for that), or that does not answer within the per-command cap
    (here 1 s), is named, never read as a clean teardown."""
    docker(behaviour)
    monkeypatch.setattr(private_cluster, "_DOCKER_TIMEOUT_SECONDS", 1)
    assert private_cluster.stop(_NAME) == [f"{_NAME} may have survived teardown: {said}"]


def test_stop_names_a_docker_it_cannot_run_never_a_traceback(tmp_path, monkeypatch):
    """A ``docker`` on ``PATH`` that cannot be executed is a named error, not an exception
    out of ``stop``: ``PATH`` holds ONLY that one, so the real docker is never reached."""
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "docker").write_text("#!/bin/sh\n", encoding="utf-8")
    (binaries / "docker").chmod(0o644)
    monkeypatch.setenv("PATH", str(binaries))
    assert private_cluster.stop(_NAME) == [
        f"{_NAME} may have survived teardown: docker cannot be run: [Errno 13] Permission "
        "denied: 'docker'"]


def test_stop_names_a_socket_directory_that_survived(docker, runtime, capsys):
    """A socket directory that cannot be removed (here a file in it this user cannot
    unlink) is named, and ``stop`` exits 1."""
    docker()
    held = runtime / _NAME
    held.mkdir()
    (held / "left-behind").write_text("", encoding="utf-8")
    held.chmod(0o500)
    try:
        assert private_cluster.main(["stop", _NAME]) == 1
        assert capsys.readouterr().err == f"[private_cluster] WARNING: {held} survived teardown.\n"
    finally:
        held.chmod(0o700)


def test_stop_of_a_cluster_that_never_started_is_clean(docker, runtime, capsys):
    """What lets a caller's traps call ``stop`` whenever they fire: nothing to remove is
    nothing survived, exit 0, said by nobody."""
    docker()
    assert private_cluster.main(["stop", _NAME]) == 0
    assert capsys.readouterr().err == ""
    assert not (runtime / _NAME).exists()


def test_a_socket_path_binds_at_107_bytes_and_not_at_108():
    """A socket binds at a path of exactly 107 bytes and not at 108 (CPython refuses the
    108th itself, "AF_UNIX path too long": ``sun_path`` holds 108 with its terminator).
    Postgres appends ``/.s.PGSQL.5432`` (14 bytes), so a socket directory can take 93; the
    two cases after ``_padded`` hold the module to that boundary."""
    base = Path(tempfile.mkdtemp(prefix="skrt", dir="/tmp"))
    try:
        for size, binds in ((107, True), (108, False)):
            path = base / ("s" * (size - len(f"{base}/".encode())))
            assert len(str(path).encode()) == size
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(path))
                bound = True
            except OSError:
                bound = False
            finally:
                listener.close()
            assert bound is binds, f"a {size}-byte socket path bound: {bound}"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _padded(runtime: Path, size: int, filler: str) -> Path:
    """An ``XDG_RUNTIME_DIR`` under ``runtime`` whose socket directory for :data:`_NAME` is
    ``size`` BYTES: ``filler`` as often as it fits, then ``d`` to the exact count."""
    room = size - len(os.fsencode(runtime / _NAME)) - 1
    each = len(os.fsencode(filler))
    padding = filler * (room // each) + "d" * (room % each)
    assert len(os.fsencode(runtime / padding / _NAME)) == size
    return runtime / padding


def test_a_socket_directory_of_93_bytes_starts_a_cluster_whose_socket_binds_there(
        docker, runtime, monkeypatch):
    """The budget's top: a socket directory of 93 bytes starts a cluster, and its socket
    binds there at 107 bytes, the kernel's limit."""
    docker()
    padded = _padded(runtime, 93, "d")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(padded))
    assert private_cluster.start(_NAME, _IMAGE).socket_dir == padded / _NAME
    assert (padded / _NAME / ".s.PGSQL.5432").is_socket()


def test_a_byte_that_is_not_utf8_is_counted_as_one_byte_and_never_raises(docker, runtime,
                                                                          monkeypatch):
    """A socket directory of 93 BYTES holding bytes that are not UTF-8 (``XDG_RUNTIME_DIR``
    may hold them) is counted as 93, one byte each, and counting it does not raise: the start
    goes on past the guard.  That is all this grades.  The stub binds its socket there; a
    REAL daemon carries the bind as UTF-8 and replaces such a byte, so a real start there
    waits out its readiness cap (measured by the review of 668b53c99; reported, not fixed
    here).

    Every failure is reported in ASCII and in bytes: a failure message holding such a path
    cannot be sent between xdist's processes, and would end the whole session instead of
    failing this case.
    """
    docker()
    padded = _padded(runtime, 93, "\udcff")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(padded))
    try:
        directory = private_cluster.start(_NAME, _IMAGE).socket_dir
    except private_cluster.ClusterError as refused:
        said = ascii(str(refused))
    else:
        said = ""
    if said:
        pytest.fail(said)
    assert os.fsencode(directory) == os.fsencode(padded / _NAME)


@pytest.mark.parametrize("filler", [
    "d",
    # Two-byte characters: counted in characters, the directory would fit.
    "é",
], ids=["94 bytes", "few characters, many bytes"])
def test_a_socket_directory_of_94_bytes_is_refused_before_docker_is_asked(
        docker, runtime, monkeypatch, filler):
    """The guard counts BYTES as the kernel does: a socket directory of 94 bytes is refused
    naming the 107-byte limit, before docker is asked anything (so exit 3), and so is one of
    94 bytes in two-byte characters, whose characters would fit."""
    stub = docker()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(_padded(runtime, 94, filler)))
    with pytest.raises(private_cluster.Refused, match="cannot exceed 107 bytes"):
        private_cluster.start(_NAME, _IMAGE)
    assert stub.calls() == []


def test_the_budget_counts_what_would_fit_in_characters_as_too_long(runtime):
    """The control for the two-byte case above: its characters would fit the budget."""
    directory = _padded(runtime, 94, "é") / _NAME
    assert len(str(directory)) <= 93 < len(os.fsencode(directory))


@pytest.mark.parametrize("given", ["soon", "-1", "nan", "inf"])
def test_a_readiness_cap_that_is_not_a_finite_number_of_seconds_is_refused(
        docker, runtime, monkeypatch, capsys, given):
    """``READINESS_TIMEOUT_SECONDS`` set to anything but a finite, non-negative number of
    seconds exits 3 naming it, before anything is asked or started (``nan`` and ``inf``
    would make the readiness wait endless: the review's LOW-4)."""
    stub = docker()
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", given)
    assert private_cluster.main(["start", _NAME, _IMAGE]) == 3
    assert f"READINESS_TIMEOUT_SECONDS={given!r}" in capsys.readouterr().err
    assert stub.calls() == []
    assert not (runtime / _NAME).exists()
