"""The throwaway cluster's one home starts a cluster and stops it leaving nothing behind.

``scripts/private_cluster.py`` holds the life of the cluster ``scripts/test.sh`` gives every
suite run, and that the release rehearsal kit is to start (step X-cx's K1a).  These drive it
with a stub ``docker`` on ``PATH``, so they need no daemon, no image and no database; the
wrapper's own cases (``test_test_runner.py``) drive the same module through ``test.sh``.
What a REAL cluster does is graded by every suite run, which starts one here.
"""
from __future__ import annotations

import shutil
import socket
import tempfile
from pathlib import Path

import pytest

from scripts import private_cluster

_NAME = "shekel-testrun-4242"
_IMAGE = "stub-image:test"


def _stub_docker(tmp_path: Path, *, run_fails: bool = False, ready: bool = True,
                 make_socket: bool = True, survives: bool = False, unreachable: bool = False,
                 hangs_on: str = "") -> Path:
    """Write a stub ``docker`` recording each command it is given; its directory.

    It holds one container as a file: ``run`` makes it (unless ``run_fails``), and the
    socket in the directory mounted at ``/sockets`` as postgres would (unless
    ``make_socket`` is false), and prints a container id; ``rm`` removes it unless it
    ``survives``; ``ps`` lists it while it exists, or exits 1 as an ``unreachable`` daemon
    does; ``exec`` (the readiness probe) answers ``ready``; the command named ``hangs_on``
    sleeps past any timeout a test sets; anything else succeeds.
    """
    binaries = tmp_path / "bin"
    binaries.mkdir()
    calls = tmp_path / "docker-calls"
    held = tmp_path / "container"
    bind = ("        listener.bind(os.path.join(mount.rsplit(':', 1)[0], '.s.PGSQL.5432'))"
            if make_socket else "        pass")
    (binaries / "docker").write_text(
        "#!/usr/bin/env python3\n"
        "import os, socket, sys, time\n"
        "args = sys.argv[1:]\n"
        f"open({str(calls)!r}, 'a').write(' '.join(args) + chr(10))\n"
        "cmd = args[0] if args else ''\n"
        f"if cmd == {hangs_on!r}:\n"
        "    time.sleep(30)\n"
        "if cmd == 'run':\n"
        f"    if {run_fails!r}:\n"
        "        print('no such image', file=sys.stderr)\n"
        "        sys.exit(125)\n"
        f"    open({str(held)!r}, 'w').close()\n"
        "    for mount in [args[i + 1] for i, a in enumerate(args) if a == '-v']:\n"
        "        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        # Bound and deliberately NOT unlinked: the file must outlive the stub.
        f"{bind}\n"
        "    print('c0ffee')\n"
        f"elif cmd == 'rm' and not {survives!r} and os.path.exists({str(held)!r}):\n"
        f"    os.remove({str(held)!r})\n"
        "elif cmd == 'ps':\n"
        f"    if {unreachable!r}:\n"
        "        print('Cannot connect to the Docker daemon', file=sys.stderr)\n"
        "        sys.exit(1)\n"
        f"    if os.path.exists({str(held)!r}):\n"
        "        print('c0ffee')\n"
        "elif cmd == 'exec':\n"
        f"    sys.exit(0 if {ready!r} else 1)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    (binaries / "docker").chmod(0o755)
    return binaries


@pytest.fixture(name="runtime")
def _runtime(monkeypatch: pytest.MonkeyPatch):
    """A SHORT throwaway ``XDG_RUNTIME_DIR``: the socket directory is made under it, and
    pytest's ``tmp_path`` alone is most of a socket path's 107-byte budget."""
    runtime = Path(tempfile.mkdtemp(prefix="skrt", dir="/tmp"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.delenv("READINESS_TIMEOUT_SECONDS", raising=False)
    yield runtime
    shutil.rmtree(runtime, ignore_errors=True)


def _use(monkeypatch: pytest.MonkeyPatch, binaries: Path) -> None:
    """Put the stub ``docker`` in ``binaries`` first on ``PATH``."""
    monkeypatch.setenv("PATH", f"{binaries}:/usr/bin:/bin")


def _calls(tmp_path: Path) -> list[str]:
    """Every command the stub ``docker`` was given, in order."""
    return (tmp_path / "docker-calls").read_text(encoding="utf-8").splitlines()


_PRESENT = "ps --all --quiet --filter name=^shekel\\-testrun\\-4242$"


def test_a_started_cluster_is_reached_by_its_socket_and_stopping_it_leaves_nothing(
        tmp_path, runtime, monkeypatch, capsys):
    """``start`` asks whether the name is free, removes what an earlier cluster of the name
    left at the socket path, makes the directory open to the container (0777), starts the
    container, probes it over ``/sockets`` and prints the socket directory, then the
    container's id; the socket is in it.  ``stop`` removes the container with its volume
    and the directory, asks again by the exact name, names nothing, and exits 0."""
    _use(monkeypatch, _stub_docker(tmp_path))
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
    calls = _calls(tmp_path)
    assert [calls[0], calls[2], calls[3], calls[4]] == [
        _PRESENT, f"exec {_NAME} pg_isready -q -h /sockets -U shekel_user", f"rm -fv {_NAME}",
        _PRESENT]
    assert calls[1].startswith("run ") and len(calls) == 5


def test_the_run_is_the_one_the_suite_reads_argument_for_argument(tmp_path, runtime,
                                                                  monkeypatch):
    """The whole ``docker run``, as one literal: detached, removed when postgres exits, no
    network and no published port, the baked image's user, password, database and PGDATA,
    the socket directory mounted at ``/sockets`` (never the entrypoint's own
    ``/var/run/postgresql``), and every ``-c`` setting the suite relies on, the non-durable
    three among them."""
    _use(monkeypatch, _stub_docker(tmp_path))
    private_cluster.start(_NAME, _IMAGE)
    assert _calls(tmp_path)[1].split() == [
        "run", "-d", "--rm", "--name", _NAME, "--network=none",
        "-e", "POSTGRES_USER=shekel_user", "-e", "POSTGRES_PASSWORD=shekel_pass",
        "-e", "POSTGRES_DB=postgres", "-e", "PGDATA=/pgdata-baked",
        "-v", f"{runtime / _NAME}:/sockets", _IMAGE,
        "-c", "idle_in_transaction_session_timeout=30000", "-c", "lock_timeout=10000",
        "-c", "statement_timeout=30000", "-c", "fsync=off", "-c", "synchronous_commit=off",
        "-c", "full_page_writes=off", "-c", "max_locks_per_transaction=256",
        "-c", "listen_addresses=", "-c", "unix_socket_directories=/sockets"]


@pytest.mark.parametrize(("stub", "env", "said"), [
    ({"run_fails": True}, {}, f"docker could not start {_NAME} from {_IMAGE}: no such image"),
    ({"ready": False}, {"READINESS_TIMEOUT_SECONDS": "0"},
     f"{_NAME} did not accept connections within 0s"),
    ({"make_socket": False}, {}, f"{_NAME} reported ready but left no socket at "),
], ids=["docker refuses", "never ready", "ready without a socket"])
def test_a_start_that_fails_stops_what_it_started(tmp_path, runtime, monkeypatch, capsys,
                                                  stub, env, said):
    """Each way a start can fail once it has made something exits 2 naming it, and leaves
    no container and no socket directory: the start removes what it made before it
    reports."""
    _use(monkeypatch, _stub_docker(tmp_path, **stub))
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert private_cluster.main(["start", _NAME, _IMAGE]) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.startswith(f"[private_cluster] {said}")
    assert "left something behind" not in out.err
    assert f"rm -fv {_NAME}" in _calls(tmp_path)
    assert not (runtime / _NAME).exists()
    assert not (tmp_path / "container").exists()


def test_a_failed_start_names_what_its_own_stop_left_behind(tmp_path, runtime, monkeypatch,
                                                            capsys):
    """The review's MEDIUM-2: a start that fails and then cannot remove its container
    names both, never the failure alone, so a caller that trusts its exit 2 is told the
    cluster is still there."""
    _use(monkeypatch, _stub_docker(tmp_path, ready=False, survives=True))
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "0")
    assert private_cluster.main(["start", _NAME, _IMAGE]) == 2
    assert capsys.readouterr().err == (
        f"[private_cluster] {_NAME} did not accept connections within 0s.  Stopping it left "
        f"something behind: {_NAME} survived teardown -- remove it by hand ('docker rm -fv "
        f"{_NAME}') or the next cluster of that name collides with it.\n")


def test_a_name_a_container_already_holds_is_refused_and_never_taken_over(
        tmp_path, runtime, monkeypatch, capsys):
    """The review's LOW-7: a container already holding the name is not removed, its socket
    directory is not touched, and nothing is started; the start exits 2 naming it."""
    _use(monkeypatch, _stub_docker(tmp_path))
    (tmp_path / "container").write_text("", encoding="utf-8")
    (runtime / _NAME).mkdir()
    (runtime / _NAME / ".s.PGSQL.5432").write_text("", encoding="utf-8")
    assert private_cluster.main(["start", _NAME, _IMAGE]) == 2
    assert capsys.readouterr().err == (
        f"[private_cluster] a container named {_NAME} already exists, and a start never "
        "takes over a name in use: stop that cluster, or name another\n")
    assert _calls(tmp_path) == [_PRESENT]
    assert (runtime / _NAME / ".s.PGSQL.5432").exists()


def test_a_leftover_file_at_the_socket_path_is_removed_not_a_traceback(tmp_path, runtime,
                                                                        monkeypatch, capsys):
    """The review's LOW-8: a FILE left where the socket directory goes is removed, as the
    shell's ``rm -rf`` removed it, and the start goes on."""
    _use(monkeypatch, _stub_docker(tmp_path))
    (runtime / _NAME).write_text("", encoding="utf-8")
    assert private_cluster.main(["start", _NAME, _IMAGE]) == 0
    assert (runtime / _NAME / ".s.PGSQL.5432").is_socket()
    capsys.readouterr()


def test_a_start_ended_by_ctrl_c_stops_what_it_made_and_exits_130_without_a_traceback(
        tmp_path, runtime, monkeypatch, capsys):
    """The review's LOW-5: Ctrl-C during the readiness wait stops the container and the
    socket directory, and the command exits 130 saying nothing (a traceback said nothing
    more)."""
    _use(monkeypatch, _stub_docker(tmp_path))

    def interrupted(*_):
        """The readiness wait, ended by Ctrl-C."""
        raise KeyboardInterrupt

    monkeypatch.setattr(private_cluster, "_wait_ready", interrupted)
    assert private_cluster.main(["start", _NAME, _IMAGE]) == 130
    assert capsys.readouterr() == ("", "")
    assert f"rm -fv {_NAME}" in _calls(tmp_path)
    assert not (runtime / _NAME).exists()


def test_stop_names_a_container_that_survived_and_exits_1(tmp_path, runtime, monkeypatch,
                                                          capsys):
    """The planted leftover: docker still lists the container by its exact name after its
    removal, so ``stop`` names it and how to remove it, and exits 1; it never reports
    clean."""
    _use(monkeypatch, _stub_docker(tmp_path, survives=True))
    (tmp_path / "container").write_text("", encoding="utf-8")
    assert private_cluster.main(["stop", _NAME]) == 1
    assert capsys.readouterr().err == (
        f"[private_cluster] WARNING: {_NAME} survived teardown -- remove it by hand "
        f"('docker rm -fv {_NAME}') or the next cluster of that name collides with it.\n")
    assert not (runtime / _NAME).exists()


@pytest.mark.parametrize(("stub", "said"), [
    ({"unreachable": True}, f"docker could not be asked whether {_NAME} exists: Cannot "
                            "connect to the Docker daemon"),
    ({"hangs_on": "rm"}, "docker rm did not answer within 1s; the daemon may be wedged"),
], ids=["unreachable", "wedged"])
def test_stop_names_a_daemon_it_cannot_ask_never_reporting_clean(tmp_path, runtime,
                                                                monkeypatch, stub, said):
    """A daemon that cannot be asked whether the container survived (``inspect`` exited 1
    alike for "gone" and for that), or that does not answer within the per-command cap
    (here 1 s), is named, never read as a clean teardown."""
    _use(monkeypatch, _stub_docker(tmp_path, **stub))
    monkeypatch.setattr(private_cluster, "_DOCKER_TIMEOUT_SECONDS", 1)
    assert private_cluster.stop(_NAME) == [f"{_NAME} may have survived teardown: {said}"]


def test_stop_names_a_socket_directory_that_survived(tmp_path, runtime, monkeypatch, capsys):
    """A socket directory that cannot be removed (here a file in it this user cannot
    unlink) is named, and ``stop`` exits 1."""
    _use(monkeypatch, _stub_docker(tmp_path))
    held = runtime / _NAME
    held.mkdir()
    (held / "left-behind").write_text("", encoding="utf-8")
    held.chmod(0o500)
    try:
        assert private_cluster.main(["stop", _NAME]) == 1
        assert capsys.readouterr().err == f"[private_cluster] WARNING: {held} survived teardown.\n"
    finally:
        held.chmod(0o700)


def test_stop_of_a_cluster_that_never_started_is_clean(tmp_path, runtime, monkeypatch, capsys):
    """What lets a caller's traps call ``stop`` whenever they fire: nothing to remove is
    nothing survived, exit 0, said by nobody."""
    _use(monkeypatch, _stub_docker(tmp_path))
    assert private_cluster.main(["stop", _NAME]) == 0
    assert capsys.readouterr().err == ""
    assert not (runtime / _NAME).exists()


def test_the_socket_directory_budget_is_the_socket_path_limit_less_the_socket_file():
    """A socket binds at a path of exactly 107 bytes and not at 108 (CPython refuses the
    108th itself, "AF_UNIX path too long": ``sun_path`` holds 108 with its terminator), and
    postgres appends ``/.s.PGSQL.5432``, so the module's directory budget is that limit
    less the socket file, and the guard can neither admit a path that cannot bind nor
    refuse one that can."""
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
    # 107 usable bytes less "/.s.PGSQL.5432" (14): 107 - 14 = 93.
    assert private_cluster._SOCKET_DIR_BYTES == 93


@pytest.mark.parametrize(("directory", "refused"), [
    ("/" + "d" * 92, False),
    ("/" + "d" * 93, True),
    # 47 characters after the slash, 94 bytes: counted in characters it would fit.
    ("/" + "\u00e9" * 46 + "d", True),
    # A byte that is not UTF-8 (``XDG_RUNTIME_DIR`` holding one) is one byte, and counting
    # it must not raise: 93 bytes, accepted.
    ("/" + "\udcff" + "d" * 91, False),
], ids=["93 bytes", "94 bytes", "few characters, many bytes", "a byte that is not UTF-8"])
def test_a_socket_directory_past_the_budget_in_bytes_is_refused(directory, refused):
    """The guard counts BYTES as the kernel does: a directory of 93 bytes is accepted, one
    of 94 is refused naming the 107-byte limit, and so is one of 94 bytes in two-byte
    characters, whose characters would fit."""
    path = Path(directory)
    if refused:
        with pytest.raises(private_cluster.ClusterError, match="cannot exceed 107 bytes"):
            private_cluster._refuse_a_socket_dir_too_long(path)
    else:
        private_cluster._refuse_a_socket_dir_too_long(path)


def test_the_budget_counts_what_would_fit_in_characters_as_too_long():
    """The control for the two-byte case above: its characters would fit the budget."""
    assert len("/" + "\u00e9" * 46 + "d") <= 93 < len(("/" + "\u00e9" * 46 + "d").encode())


@pytest.mark.parametrize("given", ["soon", "-1", "nan", "inf"])
def test_a_readiness_cap_that_is_not_a_finite_number_of_seconds_is_refused(
        tmp_path, runtime, monkeypatch, capsys, given):
    """``READINESS_TIMEOUT_SECONDS`` set to anything but a finite, non-negative number of
    seconds exits 2 naming it, before anything is asked or started (``nan`` and ``inf``
    would make the readiness wait endless: the review's LOW-4)."""
    _use(monkeypatch, _stub_docker(tmp_path))
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", given)
    assert private_cluster.main(["start", _NAME, _IMAGE]) == 2
    assert f"READINESS_TIMEOUT_SECONDS={given!r}" in capsys.readouterr().err
    assert not (tmp_path / "docker-calls").exists()
    assert not (runtime / _NAME).exists()
