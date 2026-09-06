"""``TEST_DB_PER_RUN`` gives a run its own cluster and leaves nothing behind.

Step ``balance:X-br-2``.  The suite clones a per-worker database from
``shekel_test_template`` for every test, and that template has always lived on
ONE container shared by every worktree -- which is why the repo carries a
suite-slot lock, a hygiene-restart flag, a live-backend probe,
``TEST_DB_PREFIX`` and ``TEST_TEMPLATE_DATABASE``.  Per-run mode gives the run
a private cluster from the image ``scripts/build_test_db_image.py`` bakes, so
none of that coordination applies.

**None of this block was graded when it was written**, and a review said so.
The two defects that found is what these tests pin:

* ``TEST_DB_PER_RUN`` was INHERITED by pytest, and five cases in the sibling
  module re-invoke the wrapper with ``env={**os.environ, ...}``.  Each
  re-entered per-run mode, started two more containers on the production
  daemon nested inside the suite, and asserted on a message it never got:
  8 failed against the same module's 26 green on the shared path.  A child
  must take the SHARED path, which is what those cases grade.
* A signal to the wrapper could not be serviced while pytest ran in the
  FOREGROUND, so ``timeout`` stopped working and the SIGKILL escalation left
  a live container plus an orphaned pytest.  pytest is backgrounded and
  ``wait``ed now, because ``wait`` is interruptible.

These drive a stub ``docker`` and a stub ``pytest`` on ``PATH``, so they need
no daemon, no image and no database, and they run in CI.  The suite-scale
behaviour they stand in for was measured separately: 13,156 passed in 342.8 s
in a private cluster against 356.4 s on the shared one.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEST_RUNNER = _REPO_ROOT / "scripts/test.sh"


def _stub_tree(
    tmp_path: Path, *, rootless: bool = True, make_socket: bool = True
) -> Path:
    """Write a stub ``docker`` and ``pytest`` into a directory for ``PATH``.

    The docker stub answers only what the per-run branch asks and records
    each subcommand.  The pytest stub records the ENVIRONMENT it was handed,
    which is what the flag-scrub assertion reads.

    Args:
        tmp_path: pytest-provided scratch directory.
        rootless: Whether the stub daemon reports itself rootless.  Since
            ``balance:X-br-3`` the wrapper asks before starting anything, so
            a stub that answers nothing is refused -- which is the point.
        make_socket: Whether the stub container leaves a socket behind.  A
            container can report ready and still not have one; the wrapper
            refuses to build a DSN naming a socket that is not there.

    Returns:
        The directory to prepend to ``PATH``.
    """
    binaries = tmp_path / "bin"
    binaries.mkdir()
    security_options = (
        "[name=seccomp,profile=builtin name=rootless]"
        if rootless
        else "[name=seccomp,profile=builtin]"
    )
    socket_arm = "            listener.bind(os.path.join(host, '.s.PGSQL.5432'))"
    if not make_socket:
        socket_arm = "            pass"
    calls = tmp_path / "docker-calls"
    env_dump = tmp_path / "pytest-env.json"

    (binaries / "docker").write_text(
        "#!/usr/bin/env python3\n"
        "import os, socket, sys\n"
        "args = sys.argv[1:]\n"
        f"open({str(calls)!r}, 'a').write(' '.join(args) + chr(10))\n"
        "cmd = args[0] if args else ''\n"
        "if cmd == 'info':\n"
        f"    print({security_options!r})\n"
        "elif cmd == 'run':\n"
        # The wrapper mounts the run's socket directory at /sockets; a real
        # postgres would create the socket there, so the stub must too.
        "    mounts = [args[i + 1] for i, a in enumerate(args) if a == '-v']\n"
        "    for mount in mounts:\n"
        "        if mount.endswith(':/sockets'):\n"
        "            host = mount.rsplit(':', 1)[0]\n"
        "            os.makedirs(host, exist_ok=True)\n"
        "            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        # Bound and deliberately NOT unlinked: the file must outlive the stub.
        f"{socket_arm}\n"
        "    print('stubcontainer')\n"
        "elif cmd == 'inspect':\n"
        "    sys.exit(1)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    (binaries / "pytest").write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"json.dump(dict(os.environ), open({str(env_dump)!r}, 'w'))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    for name in ("docker", "pytest"):
        (binaries / name).chmod(0o755)
    return binaries


def _run_wrapper(
    binaries: Path, env_extra: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Invoke the wrapper with the stubs first on ``PATH``.

    ``XDG_RUNTIME_DIR`` defaults to a SHORT throwaway directory because the
    per-run socket lives under it and a unix socket path cannot exceed 107
    bytes -- pytest's own ``tmp_path`` is already most of that budget, so a
    test that let it through would be asserting on the wrapper's length
    refusal rather than on the behaviour it means to grade.

    ``DOCKER_HOST``, ``CI`` and ``SHEKEL_ALLOW_HOST_DOCKER`` are all scrubbed
    unless the caller sets them, so no case in this module can depend on which
    daemon the RUNNING SUITE happens to be using, nor on whether it is running
    in CI.  Each of the three has already been observed inverting a case.

    Args:
        binaries: Directory holding the stubs.
        env_extra: Environment overrides.

    Returns:
        The completed process.
    """
    runtime = env_extra.get("XDG_RUNTIME_DIR")
    scratch = None
    if runtime is None:
        scratch = tempfile.mkdtemp(prefix="skrt", dir="/tmp")
        runtime = scratch
    try:
        return subprocess.run(
            [str(_TEST_RUNNER), "tests/does_not_matter"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env={
                **os.environ,
                "PATH": f"{binaries}:{os.environ['PATH']}",
                "TEST_DB_IMAGE": "stub-image:test",
                "XDG_RUNTIME_DIR": runtime,
                # Every variable the daemon gate reads is scrubbed unless a
                # case sets it deliberately, so each case states its own
                # premise instead of inheriting one.  CI is the load-bearing
                # one: GitHub always sets CI=true, which SANCTIONS a
                # non-rootless daemon, so the two refusal cases would have
                # asserted the opposite of what happens and taken the
                # branch-protected merge gate red with them.
                # SHEKEL_ALLOW_HOST_DOCKER does the same for a developer who
                # followed docs/testing-standards.md and exported it.
                "CI": "",
                "SHEKEL_ALLOW_HOST_DOCKER": "",
                # DOCKER_HOST is scrubbed unless a case sets it deliberately.
                # The wrapper EXPORTS it in per-run mode -- correctly, since
                # that is what un-skips tests/test_deploy -- so when the suite
                # itself runs per-run, pytest inherits one and a child wrapper
                # sees it already set.  The auto-selection branch then never
                # runs, and the case grading it silently asserts on the
                # parent's value.  Caught by the full suite; every targeted
                # run passed, because a developer shell has no DOCKER_HOST.
                "DOCKER_HOST": "",
                **env_extra,
            },
            check=False,
        )
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


class TestThePerRunFlagIsResolvedNotGuessed:
    """The flag agrees with ``RESTART_TEST_DB``'s vocabulary."""

    @pytest.mark.parametrize("value", ["banana", "2", "yes please"])
    def test_an_unrecognised_value_is_refused(
        self, tmp_path: Path, value: str
    ) -> None:
        """An unknown value exits 2 rather than being guessed at.

        Args:
            tmp_path: scratch directory.
            value: The unrecognised value.
        """
        result = _run_wrapper(_stub_tree(tmp_path), {"TEST_DB_PER_RUN": value})

        assert result.returncode == 2, result.stderr
        assert "is not a value I will guess at" in result.stderr

    def test_a_typo_in_the_other_flag_is_still_refused(
        self, tmp_path: Path
    ) -> None:
        """``RESTART_TEST_DB`` is validated even when per-run mode is on.

        Both flags are resolved before either branch is taken.  Resolving
        per-run first and exiting inside its branch made a typo'd
        ``RESTART_TEST_DB`` silently acceptable whenever the other flag was
        set -- a refusal that depends on which other flag is present is not
        a refusal.
        """
        result = _run_wrapper(
            _stub_tree(tmp_path),
            {"TEST_DB_PER_RUN": "1", "RESTART_TEST_DB": "garbage"},
        )

        assert result.returncode == 2, result.stderr
        assert "RESTART_TEST_DB" in result.stderr


class TestThePerRunFlagDoesNotReachPytest:
    """The child takes the SHARED path, or the suite re-enters itself."""

    def test_the_flag_is_scrubbed_from_pytest_s_environment(
        self, tmp_path: Path
    ) -> None:
        """``TEST_DB_PER_RUN`` must not be inherited by pytest.

        Five cases in the sibling module re-invoke this wrapper with the
        whole environment.  With the flag inherited, each re-entered per-run
        mode, started two more containers nested inside the suite, and
        asserted on a state message it never received: measured 8 failed.
        """
        binaries = _stub_tree(tmp_path)
        result = _run_wrapper(binaries, {"TEST_DB_PER_RUN": "1"})
        env_dump = tmp_path / "pytest-env.json"

        assert env_dump.exists(), (
            f"the stub pytest was never reached: rc={result.returncode} "
            f"stderr={result.stderr.strip()!r}"
        )
        handed = json.loads(env_dump.read_text(encoding="utf-8"))

        assert "TEST_DB_PER_RUN" not in handed, (
            "pytest inherited TEST_DB_PER_RUN, so any child that re-invokes "
            "this wrapper re-enters per-run mode and spawns nested containers"
        )
        # The DSNs it DOES need must still be there, or the child would talk
        # to the wrong cluster -- the scrub has to be surgical.
        for required in (
            "TEST_ADMIN_DATABASE_URL",
            "TEST_TEMPLATE_DATABASE",
        ):
            assert required in handed, f"{required} was not handed to pytest"
        assert "?host=/" in handed["TEST_ADMIN_DATABASE_URL"], (
            "the admin DSN does not name the private cluster's socket "
            f"directory: {handed['TEST_ADMIN_DATABASE_URL']}"
        )


class TestThePrivateClusterIsCleanedUp:
    """The container is removed with its volume, on every path."""

    def test_the_container_is_removed_with_its_volume(
        self, tmp_path: Path
    ) -> None:
        """Teardown uses ``rm -fv``, not ``rm -f``.

        The baked image inherits the base image's
        ``VOLUME /var/lib/postgresql`` declaration even though PGDATA lives
        outside it, so a plain ``docker rm -f`` left an anonymous volume
        behind on every run -- measured as 101 of 112 volumes dangling on
        the development daemon, in pairs matching this script's own
        invocations.
        """
        binaries = _stub_tree(tmp_path)
        _run_wrapper(binaries, {"TEST_DB_PER_RUN": "1"})
        calls = (tmp_path / "docker-calls").read_text(encoding="utf-8")

        removals = [line for line in calls.splitlines() if line.startswith("rm ")]
        assert removals, f"nothing was removed; docker saw:\n{calls}"
        assert all("-fv" in line for line in removals), (
            f"a removal did not take the volume with it: {removals}"
        )

    def test_the_run_container_is_started_non_durably(
        self, tmp_path: Path
    ) -> None:
        """The cluster runs with fsync off, which is the whole cost argument.

        Measured: the full suite took 753 s in a per-run container against
        356 s on the shared cluster -- 2.1x -- purely because the baked image
        runs with docker's default durability while the shared test-db is
        given these three.  Per-test drop-and-reclone is thousands of DDL
        cycles, priced at 1618 ms with fsync on against 31 ms with it off.
        With them the same suite is 342.8 s, marginally FASTER than shared.
        """
        binaries = _stub_tree(tmp_path)
        _run_wrapper(binaries, {"TEST_DB_PER_RUN": "1"})
        calls = (tmp_path / "docker-calls").read_text(encoding="utf-8")

        run_lines = [line for line in calls.splitlines() if line.startswith("run ")]
        assert run_lines, f"no container was started; docker saw:\n{calls}"
        started = run_lines[0]
        for knob in ("fsync=off", "synchronous_commit=off", "full_page_writes=off"):
            assert knob in started, (
                f"{knob} is missing, so the private cluster runs durably and "
                f"the suite pays roughly double: {started}"
            )


class TestPerRunModeRefusesTheProductionDaemon:
    """Step ``balance:X-br-3``: the harness gets a daemon of its own.

    A container per run is affordable only somewhere it is not noticed.  The
    system daemon on the maintainer's box runs the production database and is
    watched by wud/cadvisor/alloy, so per-run mode asks the daemon what it is
    and refuses a non-rootless one.  Fail-closed on purpose: an absent
    rootless daemon stops the run with instructions rather than silently
    falling back to the daemon the whole step exists to get off.

    The check reads ``docker info``, not the socket path, because "rootless"
    is a property of a daemon and a path is only a guess about one -- a
    ``tcp://`` endpoint aimed at a socket-proxy container reads as isolated
    while being the production daemon at a different address.
    """

    def test_a_non_rootless_daemon_is_refused(self, tmp_path: Path) -> None:
        """The default socket must not receive a container per run."""
        result = _run_wrapper(
            _stub_tree(tmp_path, rootless=False),
            {"TEST_DB_PER_RUN": "1", "DOCKER_HOST": "unix:///var/run/docker.sock"},
        )

        assert result.returncode == 2, result.stderr
        assert "is not rootless" in result.stderr
        assert "SHEKEL_ALLOW_HOST_DOCKER=1" in result.stderr, (
            "a refusal must name the way past it"
        )

    def test_nothing_is_started_on_a_refused_daemon(self, tmp_path: Path) -> None:
        """Refusing after starting the container would defeat the point."""
        binaries = _stub_tree(tmp_path, rootless=False)
        _run_wrapper(
            binaries,
            {"TEST_DB_PER_RUN": "1", "DOCKER_HOST": "unix:///var/run/docker.sock"},
        )
        calls = (tmp_path / "docker-calls").read_text(encoding="utf-8").split("\n")

        started = [line for line in calls if line.startswith("run ")]
        assert not started, f"a container was started on a refused daemon: {started}"

    @pytest.mark.parametrize(
        "sanction", [{"SHEKEL_ALLOW_HOST_DOCKER": "1"}, {"CI": "true"}]
    )
    def test_a_sanctioned_run_proceeds(
        self, tmp_path: Path, sanction: dict[str, str]
    ) -> None:
        """CI's daemon is a throwaway; the override is a deliberate choice.

        Without this arm the refusal could be unconditional and the two tests
        above would not notice.
        """
        binaries = _stub_tree(tmp_path, rootless=False)
        result = _run_wrapper(
            binaries,
            {
                "TEST_DB_PER_RUN": "1",
                "DOCKER_HOST": "unix:///var/run/docker.sock",
                **sanction,
            },
        )

        assert result.returncode == 0, result.stderr
        assert "is not rootless" not in result.stderr

    @pytest.mark.parametrize("value", ["false", "0", "", "no"])
    def test_a_falsy_ci_does_not_sanction_the_production_daemon(
        self, tmp_path: Path, value: str
    ) -> None:
        """``CI`` decides by VOCABULARY, not by presence.

        A bare ``[ -n "$CI" ]`` made ``CI=false`` and ``CI=0`` sanction a
        container per run on the daemon that runs production -- the heaviest
        consequence in this file, reached by the reading of an environment
        variable that this same file refuses to guess at everywhere else.
        """
        result = _run_wrapper(
            _stub_tree(tmp_path, rootless=False),
            {
                "TEST_DB_PER_RUN": "1",
                "DOCKER_HOST": "unix:///var/run/docker.sock",
                "CI": value,
            },
        )

        assert result.returncode == 2, (
            f"CI={value!r} sanctioned the production daemon: {result.stderr}"
        )

    def test_an_unreachable_daemon_is_not_reported_as_non_rootless(
        self, tmp_path: Path
    ) -> None:
        """A stopped daemon is a different fact from a wrong one.

        The socket file outlives the daemon behind it, so the wrapper can
        select a genuinely rootless endpoint and then fail to reach it. Saying
        "is not rootless" there asserts a cause never established, about the
        one endpoint that would have been correct.
        """
        binaries = _stub_tree(tmp_path, rootless=False)
        # A docker whose `info` fails outright, rather than answering.
        (binaries / "docker").write_text(
            "#!/usr/bin/env bash\n"
            'case "${1:-}" in\n'
            "    info) exit 1 ;;\n"
            "    inspect) exit 1 ;;\n"
            "esac\n"
            "exit 0\n",
            encoding="utf-8",
        )
        (binaries / "docker").chmod(0o755)

        result = _run_wrapper(binaries, {"TEST_DB_PER_RUN": "1"})

        assert result.returncode == 2, result.stderr
        assert "could not ask" in result.stderr, result.stderr
        assert "is not rootless" not in result.stderr, (
            "an unreachable daemon was diagnosed as a non-rootless one: "
            f"{result.stderr}"
        )

    def test_an_unset_endpoint_selects_the_rootless_socket(
        self, tmp_path: Path
    ) -> None:
        """The operator should not have to remember to export it."""
        # Short, because the per-run socket lives under this directory and
        # the kernel caps a unix socket path at 107 bytes.
        runtime_dir = Path(tempfile.mkdtemp(prefix="skrt", dir="/tmp"))
        sock_path = runtime_dir / "docker.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(sock_path))
            binaries = _stub_tree(tmp_path)
            result = _run_wrapper(
                binaries,
                {"TEST_DB_PER_RUN": "1", "XDG_RUNTIME_DIR": str(runtime_dir)},
            )
            assert result.returncode == 0, result.stderr
            handed = json.loads(
                (tmp_path / "pytest-env.json").read_text(encoding="utf-8")
            )
            assert handed.get("DOCKER_HOST") == f"unix://{sock_path}"
        finally:
            listener.close()
            shutil.rmtree(runtime_dir, ignore_errors=True)


class TestThePrivateClusterHasNoPort:
    """Step ``balance:X-br-3``: the cluster is reached by socket, not TCP.

    A port bought nothing -- one process on this machine talks to it -- and
    cost a whole failure class.  On a rootless daemon ``dockerd`` picks the
    port inside the container's network namespace and rootlesskit binds that
    number on the HOST, where a live outbound connection often owns it;
    ``ip_local_port_range`` is 32768-60999, byte-identical to docker's
    publish band.  Measured 5 of 12 starts failing, and still 3 of 12 with no
    port ever recycled, which rules out a release race.  A socket has no
    allocator and no shared namespace to contend for.
    """

    def test_no_port_is_published(self, tmp_path: Path) -> None:
        """A published port re-opens the collision class outright."""
        binaries = _stub_tree(tmp_path)
        _run_wrapper(binaries, {"TEST_DB_PER_RUN": "1"})
        calls = (tmp_path / "docker-calls").read_text(encoding="utf-8")

        started = [ln for ln in calls.splitlines() if ln.startswith("run ")]
        assert started, f"no container was started; docker saw:\n{calls}"
        assert " -p " not in started[0], (
            f"the private cluster publishes a port again: {started[0]}"
        )

    def test_the_cluster_has_no_network_and_no_listener(
        self, tmp_path: Path
    ) -> None:
        """No port implies no network, which also drops the net driver.

        The rootless daemon falls back to the experimental gvisor-tap-vsock
        driver when slirp4netns is absent; a cluster on ``--network=none``
        does not care which driver is in use.
        """
        binaries = _stub_tree(tmp_path)
        _run_wrapper(binaries, {"TEST_DB_PER_RUN": "1"})
        started = [
            ln
            for ln in (tmp_path / "docker-calls")
            .read_text(encoding="utf-8")
            .splitlines()
            if ln.startswith("run ")
        ][0]

        assert "--network=none" in started.split(), started
        # The TOKEN, not a prefix of it: `listen_addresses=` is an empty
        # value and `listen_addresses=*` -- the opposite of what this test
        # names -- would satisfy a substring check.
        assert "listen_addresses=" in started.split(), (
            f"the cluster opens a TCP listener after all: {started}"
        )

    def test_the_socket_is_not_mounted_over_the_entrypoint_s_directory(
        self, tmp_path: Path
    ) -> None:
        """``/sockets``, never ``/var/run/postgresql``.

        The postgres entrypoint chowns its default socket directory to a
        user that rootless maps into the subuid range and sets the sticky
        bit, after which the invoking user cannot unlink the socket -- five
        cleanup attempts out of five failed against that path, and succeeded
        against ``/sockets``.
        """
        binaries = _stub_tree(tmp_path)
        _run_wrapper(binaries, {"TEST_DB_PER_RUN": "1"})
        started = [
            ln
            for ln in (tmp_path / "docker-calls")
            .read_text(encoding="utf-8")
            .splitlines()
            if ln.startswith("run ")
        ][0]

        assert any(tok.endswith(":/sockets") for tok in started.split()), started
        assert "unix_socket_directories=/sockets" in started.split(), started
        assert "/var/run/postgresql" not in started, (
            "mounting over the entrypoint's own socket directory leaves "
            f"files this user cannot delete: {started}"
        )

    def test_the_socket_directory_is_removed_afterwards(
        self, tmp_path: Path
    ) -> None:
        """It sits on a tmpfs, so a leak is bounded only by the next reboot."""
        runtime = Path(tempfile.mkdtemp(prefix="skrt", dir="/tmp"))
        try:
            binaries = _stub_tree(tmp_path)
            result = _run_wrapper(
                binaries,
                {"TEST_DB_PER_RUN": "1", "XDG_RUNTIME_DIR": str(runtime)},
            )
            assert result.returncode == 0, result.stderr
            leftovers = list(runtime.glob("shekel-testrun-*"))
            assert not leftovers, f"socket directories survived: {leftovers}"
        finally:
            shutil.rmtree(runtime, ignore_errors=True)

    def test_an_over_long_socket_path_is_refused_at_the_door(
        self, tmp_path: Path
    ) -> None:
        """107 bytes is a kernel limit, not a preference.

        Left to surface on its own it arrives as a connection error from
        inside the suite, pointing at the database rather than at the path.
        """
        runtime = Path(tempfile.mkdtemp(prefix="skrt" + "x" * 90, dir="/tmp"))
        try:
            binaries = _stub_tree(tmp_path)
            result = _run_wrapper(
                binaries,
                {"TEST_DB_PER_RUN": "1", "XDG_RUNTIME_DIR": str(runtime)},
            )

            assert result.returncode == 2, result.stdout + result.stderr
            assert "107 bytes" in result.stderr, result.stderr
        finally:
            shutil.rmtree(runtime, ignore_errors=True)


class TestTheSocketPathBudgetIsArithmeticNotAGuess:
    """The 93-byte threshold is derived, and nothing was pinning it.

    ``sun_path`` is 108 bytes including the terminator, so 107 usable, and
    postgres appends ``/.s.PGSQL.5432``.  A review measured the boundary
    empirically -- 93 binds, 94 fails with "AF_UNIX path too long" -- and
    found the refusal test used a directory far past it, so the constant
    could have drifted by several bytes with every case still green.
    """

    def test_the_threshold_and_the_suffix_account_for_the_whole_budget(
        self,
    ) -> None:
        """93 + len("/.s.PGSQL.5432") must equal the 107-byte limit."""
        source = _TEST_RUNNER.read_text(encoding="utf-8")
        match = re.search(r'\$_sockdir_bytes" -gt ([0-9]+)', source)

        assert match, "the socket-path length guard is gone or was respelled"
        threshold = int(match.group(1))
        assert threshold + len("/.s.PGSQL.5432") == 107, (
            f"threshold {threshold} plus the socket suffix does not fill the "
            "107-byte sun_path budget; one of the two moved without the other"
        )

    def test_the_guard_measures_bytes_not_characters(self) -> None:
        """A multibyte XDG_RUNTIME_DIR under-counts with ``${#var}``.

        ``sun_path`` is a byte budget; ``${#var}`` counts characters, so a
        path with non-ASCII would pass the guard and then be rejected by the
        kernel.
        """
        source = _TEST_RUNNER.read_text(encoding="utf-8")

        assert '"${#_run_sockdir}" -gt' not in source, (
            "the guard counts characters again; sun_path is a byte budget"
        )
        assert "wc -c" in source


class TestAReadyContainerWithNoSocketIsRefused:
    """Readiness and a usable socket are two facts, not one.

    A mutation harness caught this hole rather than a reviewer: deleting the
    guard entirely left every case green, so nothing graded it. Without it the
    wrapper hands pytest a DSN naming a socket that does not exist, and the
    failure surfaces as a connection error from inside the suite -- pointing
    at the database rather than at the container that never made one.
    """

    def test_the_run_stops_rather_than_handing_out_a_dead_dsn(
        self, tmp_path: Path
    ) -> None:
        """`pg_isready` answering is not proof the socket is on the host."""
        binaries = _stub_tree(tmp_path, make_socket=False)

        result = _run_wrapper(binaries, {"TEST_DB_PER_RUN": "1"})

        assert result.returncode == 2, (
            "a DSN naming a nonexistent socket was handed to pytest: "
            f"{result.stdout}{result.stderr}"
        )
        assert "left no socket" in result.stderr, result.stderr
