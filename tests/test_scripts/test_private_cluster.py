"""The throwaway cluster's one home starts a cluster and stops it leaving nothing behind.

``scripts/private_cluster.py`` holds the life of the cluster ``scripts/test.sh`` gives every
suite run, and that the release rehearsal kit will start (step X-cx's K1a).  These drive it
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


def _stub_docker(tmp_path: Path, *, run_fails: bool = False, ready: bool = True,
                 make_socket: bool = True, survives: bool = False) -> Path:
    """Write a stub ``docker`` recording each command it is given; its directory.

    ``run`` makes the socket in the directory mounted at ``/sockets``, as postgres would
    (unless ``make_socket`` is false) and prints a container id, or fails (``run_fails``);
    ``exec`` (the readiness probe) answers ``ready``; ``inspect`` finds the container only
    when it ``survives`` its removal; anything else succeeds.
    """
    binaries = tmp_path / "bin"
    binaries.mkdir()
    calls = tmp_path / "docker-calls"
    bind = ("        listener.bind(os.path.join(mount.rsplit(':', 1)[0], '.s.PGSQL.5432'))"
            if make_socket else "        pass")
    (binaries / "docker").write_text(
        "#!/usr/bin/env python3\n"
        "import os, socket, sys\n"
        "args = sys.argv[1:]\n"
        f"open({str(calls)!r}, 'a').write(' '.join(args) + chr(10))\n"
        "cmd = args[0] if args else ''\n"
        "if cmd == 'run':\n"
        f"    if {run_fails!r}:\n"
        "        print('no such image', file=sys.stderr)\n"
        "        sys.exit(125)\n"
        "    for mount in [args[i + 1] for i, a in enumerate(args) if a == '-v']:\n"
        "        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        # Bound and deliberately NOT unlinked: the file must outlive the stub.
        f"{bind}\n"
        "    print('c0ffee')\n"
        "elif cmd == 'exec':\n"
        f"    sys.exit(0 if {ready!r} else 1)\n"
        "elif cmd == 'inspect':\n"
        f"    sys.exit(0 if {survives!r} else 1)\n"
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
    monkeypatch.setenv("PATH", f"{binaries}:{Path('/usr/bin')}:{Path('/bin')}")


def _calls(tmp_path: Path) -> list[str]:
    """Every command the stub ``docker`` was given, in order."""
    return (tmp_path / "docker-calls").read_text(encoding="utf-8").splitlines()


def test_a_started_cluster_is_reached_by_its_socket_and_stopping_it_leaves_nothing(
        tmp_path, runtime, monkeypatch, capsys):
    """``start`` prints the socket directory, named after the cluster, then the container's
    id; the socket is in it.  ``stop`` removes the container with its volume and the
    directory, names nothing, and exits 0."""
    _use(monkeypatch, _stub_docker(tmp_path))
    assert private_cluster.main(["start", _NAME, "stub-image:test"]) == 0
    assert capsys.readouterr().out.splitlines() == [str(runtime / _NAME), "c0ffee"]
    assert (runtime / _NAME / ".s.PGSQL.5432").is_socket()
    assert private_cluster.main(["stop", _NAME]) == 0
    assert capsys.readouterr().err == ""
    assert not (runtime / _NAME).exists()
    assert [call.split()[0] for call in _calls(tmp_path)] == ["run", "exec", "rm", "inspect"]
    assert _calls(tmp_path)[2] == f"rm -fv {_NAME}"


def test_the_cluster_has_no_network_no_port_and_the_settings_the_suite_reads(
        tmp_path, runtime, monkeypatch):
    """One ``docker run``: no network and no published port, the socket directory mounted
    at ``/sockets`` (never the entrypoint's own ``/var/run/postgresql``), and every ``-c``
    setting the suite relies on, the non-durable three among them."""
    _use(monkeypatch, _stub_docker(tmp_path))
    private_cluster.start(_NAME, "stub-image:test")
    started = _calls(tmp_path)[0].split()
    assert "--network=none" in started and "-p" not in started
    assert f"{runtime / _NAME}:/sockets" in started
    assert "/var/run/postgresql" not in " ".join(started)
    settings = [started[i + 1] for i, arg in enumerate(started) if arg == "-c"]
    assert settings == [
        "idle_in_transaction_session_timeout=30000", "lock_timeout=10000",
        "statement_timeout=30000", "fsync=off", "synchronous_commit=off",
        "full_page_writes=off", "max_locks_per_transaction=256", "listen_addresses=",
        "unix_socket_directories=/sockets"]


@pytest.mark.parametrize(("stub", "env", "said"), [
    ({"run_fails": True}, {}, "docker could not start shekel-testrun-4242 from stub-image:test: "
                              "no such image"),
    ({"ready": False}, {"READINESS_TIMEOUT_SECONDS": "0"},
     "shekel-testrun-4242 did not accept connections within 0s"),
    ({"make_socket": False}, {}, "shekel-testrun-4242 reported ready but left no socket at "),
], ids=["docker refuses", "never ready", "ready without a socket"])
def test_a_start_that_fails_stops_what_it_started(tmp_path, runtime, monkeypatch, capsys,
                                                  stub, env, said):
    """Each way a start can fail exits 2 naming it, and leaves no container and no socket
    directory: the start removes what it made before it reports."""
    _use(monkeypatch, _stub_docker(tmp_path, **stub))
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert private_cluster.main(["start", _NAME, "stub-image:test"]) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.startswith(f"[private_cluster] {said}")
    assert f"rm -fv {_NAME}" in _calls(tmp_path)
    assert not (runtime / _NAME).exists()


def test_stop_names_a_container_that_survived_and_exits_1(tmp_path, runtime, monkeypatch,
                                                          capsys):
    """The planted leftover: docker still finds the container after its removal, so
    ``stop`` names it and how to remove it, and exits 1; it never reports clean."""
    _use(monkeypatch, _stub_docker(tmp_path, survives=True))
    assert private_cluster.main(["stop", _NAME]) == 1
    assert capsys.readouterr().err == (
        f"[private_cluster] WARNING: {_NAME} survived teardown -- remove it by hand "
        f"('docker rm -fv {_NAME}') or the next cluster of that name collides with it.\n")
    assert not (runtime / _NAME).exists()


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


def test_the_socket_directory_budget_is_the_kernels_limit_less_the_socket_file():
    """``sun_path`` holds 107 bytes and postgres appends ``/.s.PGSQL.5432``: a socket binds
    at a path of exactly 107 bytes and not at 108 (measured here, on this kernel), and the
    module's directory budget is that limit less the socket file, so the guard can neither
    admit a path the kernel rejects nor refuse one it binds."""
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


@pytest.mark.parametrize(("size", "wide", "refused"), [
    (93, False, False), (94, False, True), (94, True, True),
], ids=["93 bytes", "94 bytes", "few characters, many bytes"])
def test_a_socket_directory_past_the_budget_in_bytes_is_refused(size, wide, refused):
    """The guard counts BYTES: a directory of 93 bytes is made, one of 94 is refused naming
    the 107-byte limit, and so is one of 94 bytes in two-byte characters, whose characters
    would fit."""
    base = Path(tempfile.mkdtemp(prefix="skrt", dir="/tmp"))
    try:
        room = size - len(f"{base}/".encode())
        name = "\u00e9" * (room // 2) + "d" * (room % 2) if wide else "d" * room
        directory = base / name
        assert len(str(directory).encode()) == size
        if wide:
            assert len(str(directory)) <= 93, "control: counted in characters it would fit"
        if refused:
            with pytest.raises(private_cluster.ClusterError, match="cannot exceed 107 bytes"):
                private_cluster._make_socket_dir(directory)
            assert not directory.exists()
        else:
            private_cluster._make_socket_dir(directory)
            assert directory.is_dir()
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.mark.parametrize("given", ["soon", "-1"])
def test_a_readiness_cap_that_is_not_a_number_of_seconds_is_refused(tmp_path, runtime,
                                                                     monkeypatch, capsys,
                                                                     given):
    """``READINESS_TIMEOUT_SECONDS`` set to anything but a number of seconds exits 2 naming
    it, before anything is started."""
    _use(monkeypatch, _stub_docker(tmp_path))
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", given)
    assert private_cluster.main(["start", _NAME, "stub-image:test"]) == 2
    assert f"READINESS_TIMEOUT_SECONDS={given!r}" in capsys.readouterr().err
    assert not (tmp_path / "docker-calls").exists()
    assert not (runtime / _NAME).exists()
