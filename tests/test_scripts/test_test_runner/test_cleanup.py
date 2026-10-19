"""The test runner removes its private cluster on every path, and stops rather than hand pytest a
container that reported ready without a socket.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

import pytest

from ._helpers import _TEST_RUNNER, _STARTS_THE_CLUSTER, _stub_tree, _run_wrapper, _code_lines


class TestThePrivateClusterIsCleanedUp:
    """The container is removed with its volume, on every path."""

    @pytest.mark.parametrize("signal_name", ["EXIT", "INT", "TERM", "HUP"])
    def test_teardown_is_armed_for_every_signal_that_can_end_a_run(
        self, signal_name: str
    ) -> None:
        """An untrapped fatal signal skips the EXIT trap and leaks the cluster.

        Bash runs the EXIT trap on a normal exit and on a signal it has a
        handler for -- and NOT on an untrapped fatal signal, which terminates
        the shell outright.  So each of these needs its own arm, and each has
        a way of arriving: INT is Ctrl-C at the terminal, TERM is what
        ``timeout`` and process supervisors send, and HUP is the terminal
        closing on a running suite.  HUP was the one missing, while the
        wrapper's own header claimed every exit path was covered.

        A leaked container is not merely untidy: it keeps its postgres running
        on the daemon and its socket directory on the tmpfs, and nothing else
        will ever stop either.

        This reads the SOURCE rather than delivering real signals, as the
        wiring case does -- the behaviour needs a live container and a race,
        and what actually regresses is an arm being dropped from the trap
        list.

        Args:
            signal_name: The signal whose arm must be present.
        """
        source = _TEST_RUNNER.read_text(encoding="utf-8")

        armed = re.findall(r"^trap\s+'[^']*'\s+([A-Z]+)$", source, re.M)

        assert signal_name in armed, (
            f"scripts/test.sh does not trap {signal_name}; it traps {armed}. "
            "A run ended that way leaks its postgres container and its socket directory."
        )

    def test_every_trap_is_armed_before_the_cluster_is_started(self) -> None:
        """The traps come BEFORE the start, so a run killed during the start is
        stopped by name.

        A start ended by a signal it holds stops what it made itself, but one
        killed outright cannot: only the trap's stop, which finds the cluster
        by the token the wrapper made, removes it.  The review of step X-cx's K1a
        measured the traps moved after the start: a TERM to the whole group
        during the start left the container and its socket directory behind.
        Read from the SOURCE, as the wiring case is (:func:`_code_lines`).
        """
        code = _code_lines()
        started = next(index for index, line in enumerate(code)
                       if _STARTS_THE_CLUSTER.search(line))
        armed = {match.group(1): index for index, line in enumerate(code)
                 if (match := re.match(r"trap\s+'_teardown_run_container[^']*'\s+([A-Z]+)$",
                                       line))}

        assert set(armed) == {"EXIT", "INT", "TERM", "HUP"}, armed
        late = sorted(name for name, index in armed.items() if index > started)
        assert not late, f"armed only after the cluster is started: {late}"

    def test_a_container_that_survives_teardown_is_named_and_pytest_s_status_kept(
        self, tmp_path: Path
    ) -> None:
        """A survivor is named on stderr, and the wrapper still exits with
        pytest's status.

        The module's stop exits 1 when something survives.  Under ``set -e``,
        the teardown at the end of the wrapper would then end the run with
        that 1 instead of pytest's status, unless it is told not to (``||
        true``); what survived is reported by the WARNING, not by the exit.
        The stub pytest exits 3, a status nothing else here gives, so a
        wrapper that dropped pytest's status for any other would be seen: the
        delta review's MEDIUM-2 measured ``exit 0`` passing a stub exiting 0.
        """
        result = _run_wrapper(
            _stub_tree(tmp_path, survives=1, pytest_status=3), {})

        assert result.returncode == 3, result.stderr
        assert "survived teardown" in result.stderr, result.stderr

    def test_a_name_another_container_holds_is_left_to_it(
        self, tmp_path: Path
    ) -> None:
        """A container ANOTHER run made, holding the name this run's start
        asks for, is left alone by the start and by the wrapper.

        The coordinator's MEDIUM-1, held where the ruling on commit B put it:
        docker refuses the run ("Conflict"), the start exits 2, and the EXIT
        trap stops by THIS run's token, which that container does not carry.
        A stop by NAME removed it (a leaked cluster of a reused PID, or a live
        one of a run in another PID namespace on the same daemon).
        """
        other = tmp_path / "other-container"
        binaries = _stub_tree(tmp_path)
        other.write_text("", encoding="utf-8")

        result = _run_wrapper(binaries, {})
        calls = (tmp_path / "docker-calls").read_text(encoding="utf-8")

        assert result.returncode == 2, result.stderr
        assert "Conflict" in result.stderr, result.stderr
        assert other.exists(), f"the other run's container was removed; docker saw:\n{calls}"
        assert not [line for line in calls.splitlines() if line.startswith("rm ")], calls

    @pytest.mark.parametrize(("kills", "other", "calls"), [
        ("after making", False, ["info", "run", "ps", "rm", "ps"]),
        ("before making", True, ["info", "run", "ps"]),
    ], ids=["after its run made the container",
            "before it made anything, another run's container holding the name"])
    def test_a_start_killed_outright_is_stopped_by_the_trap_s_token(
        self, tmp_path: Path, kills: str, other: bool, calls: list[str]
    ) -> None:
        """A start killed outright (SIGKILL from its ``docker run``) runs no
        stop of its own; the EXIT trap stops by the token the wrapper made.
        After its run made the container, that stop alone removes it and the
        socket directory (the review of 668b53c99 measured a wrapper that
        disarmed on every failed start passing every case).  BEFORE it made
        anything, with another run's container holding the name (S4 of the
        commit B design), the stop finds nothing carrying this run's token;
        a stop by name removed the other run's container.

        Args:
            tmp_path: scratch directory.
            kills: when the stub ``run`` kills the start.
            other: whether another run's container holds the name.
            calls: the docker commands the run gives, in order.
        """
        runtime = Path(tempfile.mkdtemp(prefix="skrt", dir="/tmp"))
        binaries = _stub_tree(tmp_path)
        (tmp_path / "run-kills-the-start").write_text(kills, encoding="utf-8")
        if other:
            (tmp_path / "other-container").write_text("", encoding="utf-8")
        try:
            result = _run_wrapper(binaries, {"XDG_RUNTIME_DIR": str(runtime)})
            left = list(runtime.glob("shekel-cluster-*"))
        finally:
            shutil.rmtree(runtime, ignore_errors=True)
        seen = (tmp_path / "docker-calls").read_text(encoding="utf-8").splitlines()

        assert result.returncode == 2, result.stderr
        assert [line.split()[0] for line in seen] == calls, seen
        assert not (tmp_path / "stub-container").exists(), (
            f"the killed start's container was left; docker saw: {seen}")
        assert not left, f"the killed start's socket directory was left: {left}"
        assert (tmp_path / "other-container").exists() is other, (
            f"the other run's container was removed; docker saw: {seen}")

    def test_a_container_a_failed_start_could_not_remove_is_removed_by_the_trap(
        self, tmp_path: Path
    ) -> None:
        """A start that exits 2 naming a container its own stop could not
        remove has it removed by the EXIT trap, by the token the wrapper made.

        Delta review 2's LOW-B: nothing graded that the trap still stops after
        a start's exit 2; a wrapper that disarmed then passed every case.  The
        trap's stop is the only cover once the start's own stop has missed
        something.  Here the start fails (its container leaves no socket) and
        the stub container survives the start's removal and not the trap's.

        Args:
            tmp_path: scratch directory.
        """
        held = tmp_path / "stub-container"
        result = _run_wrapper(_stub_tree(tmp_path, make_socket=False, survives=1), {})
        seen = (tmp_path / "docker-calls").read_text(encoding="utf-8").splitlines()

        assert result.returncode == 2, result.stderr
        assert "left no socket" in result.stderr, result.stderr
        assert "Stopping it left something behind" in result.stderr, result.stderr
        assert [line.split()[0] for line in seen] == [
            "info", "run", "exec", "ps", "rm", "ps", "ps", "rm", "ps"], seen
        assert not held.exists(), f"the container the start left was left; docker saw: {seen}"

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
        _run_wrapper(binaries, {})
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
        _run_wrapper(binaries, {})
        calls = (tmp_path / "docker-calls").read_text(encoding="utf-8")

        run_lines = [line for line in calls.splitlines() if line.startswith("run ")]
        assert run_lines, f"no container was started; docker saw:\n{calls}"
        started = run_lines[0]
        for knob in ("fsync=off", "synchronous_commit=off", "full_page_writes=off"):
            assert knob in started, (
                f"{knob} is missing, so the private cluster runs durably and "
                f"the suite pays roughly double: {started}"
            )


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

        result = _run_wrapper(binaries, {})

        assert result.returncode == 2, (
            "a DSN naming a nonexistent socket was handed to pytest: "
            f"{result.stdout}{result.stderr}"
        )
        assert "left no socket" in result.stderr, result.stderr
