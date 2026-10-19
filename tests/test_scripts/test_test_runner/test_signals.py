"""A signal ends the test runner promptly and BY that signal, having stopped what this run made and
nothing else.

The wrapper runs its two long children, the cluster's start and then pytest, in the background
and waits for them, each with SIGINT at its default, so its traps run while a child does: they
pass a TERM or HUP on to the child and never an INT (a terminal's Ctrl-C reaches the whole group
already, and a second one cuts pytest's own shutdown short), wait for it, stop by the run's
token, and end the wrapper by the signal (step X-cx's K1a, commit B2).  On the launch before it
(the start in the foreground, pytest in the background with SIGINT ignored, each trap exiting
130, 143 or 129), measured over these stubs on 2026-10-19: a Ctrl-C during pytest waited out
pytest's whole run (12.1 s for a stub running 12), a TERM during the readiness wait waited out
the whole cap (29.8 s of 30), a TERM and then a KILL to the wrapper during ``docker run`` left
the container and its socket directory, and a shell that ran the wrapper carried on after a
Ctrl-C.

Every case starts the wrapper as a terminal's shell starts a command (:func:`_launch_wrapper`),
over stubs that need no daemon, and holds it to :data:`_BOUND`.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from ._helpers import _launch_wrapper, _stub_tree

# How long a stub child runs (pytest's wait, the readiness cap) when nothing cuts it short.
_STUB_SECONDS = "20"

# How long after its signal the wrapper may take to end: half of what a wrapper that waited for
# its child's own end would take over these stubs.  The ends measured on 2026-10-19 were under
# 0.6 s; the rest is for a loaded machine.
_BOUND = 10.0

# How long a case waits for the wrapper, and for a trigger, before it calls the wrapper stuck:
# past every stub's own end, and inside pytest.ini's per-test timeout.
_PATIENCE = 40.0

# Each signal a case sends, and whether it goes to the wrapper's whole process group, as a
# terminal sends a Ctrl-C, or to its PID alone, as a supervisor sends a TERM.
_BY_SIGNAL = pytest.mark.parametrize(("signum", "group"), [
    (signal.SIGINT, True),
    (signal.SIGTERM, False),
    (signal.SIGHUP, False),
], ids=["Ctrl-C to the group", "TERM to the wrapper", "HUP to the wrapper"])


@pytest.fixture(name="runtime")
def _runtime() -> Iterator[Path]:
    """A SHORT throwaway ``XDG_RUNTIME_DIR`` (:func:`_run_wrapper` says why short), removed after.

    Yields:
        The directory.
    """
    made = Path(tempfile.mkdtemp(prefix="skrt", dir="/tmp"))
    yield made
    shutil.rmtree(made, ignore_errors=True)


def _end(running: subprocess.Popen[str]) -> subprocess.CompletedProcess[str]:
    """Wait for ``running`` to end, and for every process still holding its output; what it gave.

    A wrapper still running after :data:`_PATIENCE` is KILLed with its whole group, so a case that
    fails leaves no stub behind; its status is then the KILL's.

    Args:
        running: A process :func:`_launch_wrapper` started.

    Returns:
        Its status and output.
    """
    try:
        out, err = running.communicate(timeout=_PATIENCE)
    except subprocess.TimeoutExpired:
        os.killpg(running.pid, signal.SIGKILL)
        out, err = running.communicate()
    return subprocess.CompletedProcess(running.args, running.returncode, out, err)


def _wait_for(reached: Callable[[], bool], running: subprocess.Popen[str]) -> None:
    """Return once ``reached()`` holds; fail the case, naming why, if the wrapper ends first or
    :data:`_PATIENCE` runs out.

    Args:
        reached: The trigger.
        running: The wrapper.
    """
    deadline = time.monotonic() + _PATIENCE
    while not reached():
        if running.poll() is not None or time.monotonic() > deadline:
            ended = _end(running)
            pytest.fail(f"the trigger was never reached: status {ended.returncode}, "
                        f"stderr {ended.stderr!r}")
        time.sleep(0.01)


def _send(running: subprocess.Popen[str], signum: int, group: bool) -> float:
    """Send ``signum`` to the wrapper's process group, or to its PID alone; when it was sent.

    Args:
        running: The wrapper.
        signum: The signal.
        group: Whether it goes to the whole group, as a terminal's Ctrl-C does.

    Returns:
        ``time.monotonic()`` as it was sent.
    """
    sent = time.monotonic()
    if group:
        os.killpg(running.pid, signum)
    else:
        running.send_signal(signum)
    return sent


def _gone(pid: int) -> bool:
    """Whether the process ``pid`` has ended (a zombie has).

    Args:
        pid: The process.

    Returns:
        True once it no longer runs.
    """
    try:
        state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return True
    return state == "Z"


def _calls(tmp_path: Path) -> list[str]:
    """The docker subcommands the stub was given, in order.

    Args:
        tmp_path: The stubs' scratch directory.

    Returns:
        Each call's first word.
    """
    seen = tmp_path / "docker-calls"
    if not seen.exists():
        return []
    return [line.split()[0] for line in seen.read_text(encoding="utf-8").splitlines()]


class TestASignalEndsTheRunByItself:
    """Each trap passes TERM and HUP on, not INT, waits, stops by the token, and ends BY the
    signal, within :data:`_BOUND` of it."""

    @_BY_SIGNAL
    def test_a_signal_during_pytest_ends_the_run_by_it(
        self, tmp_path: Path, runtime: Path, signum: int, group: bool
    ) -> None:
        """pytest is reached with SIGINT at its default, a Ctrl-C reaches it ONCE, a TERM or HUP
        is passed on to it, and the wrapper waits for it, stops the cluster and dies by the
        signal.

        The stub pytest runs 20 s unless a signal ends it.  On the launch before commit B2 a
        Ctrl-C waited all of it (pytest had SIGINT ignored), and every trap exited with a status
        (130, 143, 129).  A second after the first SIGINT it writes how many it was sent, so a
        wrapper that passed the Ctrl-C on as well is seen (two), and whether its parent, the
        wrapper, was still alive then, so one that tore the cluster down and ended without
        waiting for pytest is seen too (orphaned).  The wrapper's own end cannot show that:
        pytest holds its output, so reading that output waits for pytest either way.

        Args:
            tmp_path: The stubs' scratch directory.
            runtime: ``XDG_RUNTIME_DIR``.
            signum: The signal sent.
            group: Whether it goes to the whole group.
        """
        (tmp_path / "pytest-waits").write_text(_STUB_SECONDS, encoding="utf-8")
        running = _launch_wrapper(_stub_tree(tmp_path), runtime, {})
        ready = tmp_path / "pytest-running"
        _wait_for(ready.exists, running)
        pid, ignores_int = ready.read_text(encoding="utf-8").split()
        sent = _send(running, signum, group)
        ended = _end(running)
        took = time.monotonic() - sent
        interrupts = tmp_path / "pytest-interrupts"

        assert ended.returncode == -signum, ended.stderr
        assert took < _BOUND, f"the wrapper took {took:.2f}s to end: {ended.stderr}"
        assert ignores_int == "0", "pytest was handed SIGINT ignored"
        assert _gone(int(pid)), "pytest outlived the wrapper"
        if signum == signal.SIGINT:
            count, waited = interrupts.read_text(encoding="utf-8").split()
            assert count == "1", f"pytest was sent {count} SIGINTs for one Ctrl-C"
            assert waited == "waited", "the wrapper ended before pytest had finished"
        assert not (tmp_path / "stub-container").exists(), _calls(tmp_path)
        assert not list(runtime.glob("shekel-cluster-*")), "the socket directory was left"

    def test_a_shell_that_ran_the_wrapper_stops_at_its_ctrl_c(
        self, tmp_path: Path, runtime: Path
    ) -> None:
        """A shell running the wrapper and then another command does not run that command
        after a Ctrl-C ended the wrapper.

        A shell that sees its child end BY SIGINT stops too; one that sees an exit status,
        however high, takes it that the child handled the Ctrl-C and carries on: the wrapper's
        ``exit 130`` did that (measured 2026-10-19), which is the defect the cluster's start ends
        by its signal to prevent, one level up.

        Args:
            tmp_path: The stubs' scratch directory.
            runtime: ``XDG_RUNTIME_DIR``.
        """
        binaries = _stub_tree(tmp_path)
        (tmp_path / "pytest-waits").write_text(_STUB_SECONDS, encoding="utf-8")
        running = _launch_wrapper(binaries, runtime, {}, then='echo "CARRIED ON"')
        _wait_for((tmp_path / "pytest-running").exists, running)
        _send(running, signal.SIGINT, True)
        ended = _end(running)

        assert "CARRIED ON" not in ended.stdout, ended.stderr
        assert ended.returncode == -signal.SIGINT, ended.stderr

    def test_a_term_then_a_kill_to_the_wrapper_during_docker_run_leaves_nothing(
        self, tmp_path: Path, runtime: Path
    ) -> None:
        """A TERM to the wrapper while the start's ``docker run`` is in flight is passed on to
        the start, which stops what it made though a KILL then ends the wrapper.

        A supervisor's escalation (``timeout -k``) sends exactly this.  On the launch before
        commit B2 the start ran in the foreground, so bash held the TERM until it returned, the
        KILL ended the wrapper with the TERM unserviced, and the start, going on alone, left its
        container and socket directory (measured 2026-10-19).  The stub ``run`` takes 1.5 s;
        the case waits for every process holding the wrapper's output, the start among them.

        Args:
            tmp_path: The stubs' scratch directory.
            runtime: ``XDG_RUNTIME_DIR``.
        """
        binaries = _stub_tree(tmp_path)
        (tmp_path / "run-seconds").write_text("1.5", encoding="utf-8")
        running = _launch_wrapper(binaries, runtime, {})
        _wait_for(lambda: "run" in _calls(tmp_path), running)
        _send(running, signal.SIGTERM, False)
        time.sleep(0.2)
        _send(running, signal.SIGKILL, False)
        ended = _end(running)

        assert ended.returncode == -signal.SIGKILL, "the wrapper ended before the KILL"
        assert not (tmp_path / "stub-container").exists(), _calls(tmp_path)
        assert not list(runtime.glob("shekel-cluster-*")), "the socket directory was left"

    def test_a_kill_alone_to_the_wrapper_during_docker_run_leaves_nothing(
        self, tmp_path: Path, runtime: Path
    ) -> None:
        """A KILL to the wrapper's PID alone while the start's ``docker run`` is in flight: the
        start goes on, finds nobody left to hand the cluster over to, and stops what carries its
        token, naming why.

        Nothing else can stop it: a KILL runs no trap, so the wrapper that made the token is
        gone.  On commit B2 the start wrote its two lines into stdout's buffer and returned, the
        write failed only at the interpreter's exit (BrokenPipeError, exit 120), and the
        container and its socket directory were left, three of three (measured 2026-10-19,
        T10; K1a commit B3).  The stub ``run`` takes 1.5 s; the case waits for every process
        holding the wrapper's output, the start among them, so the start's last words are the
        last on that output.

        Args:
            tmp_path: The stubs' scratch directory.
            runtime: ``XDG_RUNTIME_DIR``.
        """
        binaries = _stub_tree(tmp_path)
        (tmp_path / "run-seconds").write_text("1.5", encoding="utf-8")
        running = _launch_wrapper(binaries, runtime, {})
        _wait_for(lambda: "run" in _calls(tmp_path), running)
        _send(running, signal.SIGKILL, False)
        ended = _end(running)

        assert ended.returncode == -signal.SIGKILL, "the wrapper ended before the KILL"
        assert not (tmp_path / "stub-container").exists(), _calls(tmp_path)
        assert not list(runtime.glob("shekel-cluster-*")), "the socket directory was left"
        assert ended.stderr.endswith(
            "[private_cluster] container stubcontainer accepts connections but could not be "
            "handed over: EPIPE (Broken pipe)\n"), ended.stderr

    def test_the_start_is_handed_sigint_at_its_default(
        self, tmp_path: Path, runtime: Path
    ) -> None:
        """The cluster's start is launched with SIGINT at its default, as pytest is.

        Bash starts a background child of a non-interactive shell with SIGINT ignored.  The
        start holds its caller's signals once it begins making the cluster, and its handler
        replaces an ignored disposition, so what an ignored SIGINT would cost is a Ctrl-C
        landing BEFORE that, in the interpreter's start-up and the start's own checks: sat
        out, it lets the start make a whole cluster for the trap to remove.  That window is
        milliseconds long and no stub can time a signal into it (a start launched without
        ``trap - INT`` passed every other case here), so this reads the disposition the start
        was handed, from a ``sitecustomize`` that runs before the start's own code.  The start
        ran in the foreground before commit B2, with SIGINT at its default; the regression this
        guards is B2's own.

        Args:
            tmp_path: The stubs' scratch directory.
            runtime: ``XDG_RUNTIME_DIR``.
        """
        site = tmp_path / "site"
        site.mkdir()
        handed = tmp_path / "start-ignores-int"
        (site / "sitecustomize.py").write_text(
            "import signal, sys\n"
            "if sys.argv[0].endswith('private_cluster.py') and sys.argv[1:2] == ['start']:\n"
            "    status = open('/proc/self/status').read().splitlines()\n"
            "    ignored = int(next(line.split()[1] for line in status\n"
            "                       if line.startswith('SigIgn:')), 16)\n"
            f"    open({str(handed)!r}, 'w').write(str(ignored >> (signal.SIGINT - 1) & 1))\n",
            encoding="utf-8")
        running = _launch_wrapper(_stub_tree(tmp_path), runtime, {"PYTHONPATH": str(site)})
        ended = _end(running)

        assert ended.returncode == 0, ended.stderr
        assert handed.read_text(encoding="utf-8") == "0", "the start was handed SIGINT ignored"

    @_BY_SIGNAL
    def test_a_signal_during_the_start_leaves_another_run_s_container(
        self, tmp_path: Path, runtime: Path, signum: int, group: bool
    ) -> None:
        """A signal while the start's ``docker run`` is in flight, a container ANOTHER run made
        holding the name, removes nothing of that container: the start's stop and the trap's
        both stop by this run's token, which it does not carry.

        The coordinator's 16:28 10-18 (ii), where commit B put it: the guarantee must hold in
        every signal window, and a trap serviced during the start widens the windows a signal
        can land in.  The stub ``run`` takes a second and then refuses as docker refuses a name
        in use.

        Args:
            tmp_path: The stubs' scratch directory.
            runtime: ``XDG_RUNTIME_DIR``.
            signum: The signal sent.
            group: Whether it goes to the whole group.
        """
        binaries = _stub_tree(tmp_path)
        other = tmp_path / "other-container"
        other.write_text("", encoding="utf-8")
        (tmp_path / "run-seconds").write_text("1", encoding="utf-8")
        running = _launch_wrapper(binaries, runtime, {})
        _wait_for(lambda: "run" in _calls(tmp_path), running)
        sent = _send(running, signum, group)
        ended = _end(running)
        took = time.monotonic() - sent

        assert ended.returncode == -signum, ended.stderr
        assert took < _BOUND, f"the wrapper took {took:.2f}s to end: {ended.stderr}"
        assert other.exists(), f"the other run's container was removed: {_calls(tmp_path)}"
        assert "rm" not in _calls(tmp_path), _calls(tmp_path)

    @pytest.mark.parametrize(("signum", "group"), [
        (signal.SIGINT, True),
        (signal.SIGTERM, False),
    ], ids=["Ctrl-C to the group", "TERM to the wrapper"])
    def test_a_signal_during_the_readiness_wait_ends_the_run_within_the_bound(
        self, tmp_path: Path, runtime: Path, signum: int, group: bool
    ) -> None:
        """A signal while the start waits for the cluster ends the wrapper after the probe it
        landed in, not after the readiness cap.

        On the launch before commit B2 a TERM waited out the whole cap (29.8 s of 30, measured
        2026-10-19), since bash held it until the start returned; a Ctrl-C reached the start
        directly.  Here the cap is 20 s and the cluster is never ready.  A start launched with
        SIGINT ignored, as bash launches a background child it does not reset, would sit out
        the Ctrl-C to the cap.

        Args:
            tmp_path: The stubs' scratch directory.
            runtime: ``XDG_RUNTIME_DIR``.
            signum: The signal sent.
            group: Whether it goes to the whole group.
        """
        binaries = _stub_tree(tmp_path)
        (tmp_path / "never-ready").write_text("", encoding="utf-8")
        running = _launch_wrapper(binaries, runtime, {"READINESS_TIMEOUT_SECONDS": _STUB_SECONDS})
        _wait_for(lambda: "exec" in _calls(tmp_path), running)
        sent = _send(running, signum, group)
        ended = _end(running)
        took = time.monotonic() - sent

        assert ended.returncode == -signum, ended.stderr
        assert took < _BOUND, f"the wrapper took {took:.2f}s to end: {ended.stderr}"
        assert not (tmp_path / "stub-container").exists(), _calls(tmp_path)
        assert not list(runtime.glob("shekel-cluster-*")), "the socket directory was left"
