"""The test runner refuses the production daemon, and its private cluster has no port, no network
and no listener.
"""

from __future__ import annotations

import json
import shutil
import socket
import tempfile
from pathlib import Path

import pytest

from ._helpers import _stub_tree, _run_wrapper


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
            {"DOCKER_HOST": "unix:///var/run/docker.sock"},
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
            {"DOCKER_HOST": "unix:///var/run/docker.sock"},
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

        result = _run_wrapper(binaries, {})

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
                {"XDG_RUNTIME_DIR": str(runtime_dir)},
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
        _run_wrapper(binaries, {})
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
        _run_wrapper(binaries, {})
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
        _run_wrapper(binaries, {})
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
                {"XDG_RUNTIME_DIR": str(runtime)},
            )
            assert result.returncode == 0, result.stderr
            leftovers = list(runtime.glob("shekel-cluster-*"))
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
                {"XDG_RUNTIME_DIR": str(runtime)},
            )

            assert result.returncode == 2, result.stdout + result.stderr
            assert "107 bytes" in result.stderr, result.stderr
        finally:
            shutil.rmtree(runtime, ignore_errors=True)
