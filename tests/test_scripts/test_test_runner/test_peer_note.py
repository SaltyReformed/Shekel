"""The test runner's note names every other running pytest, whatever its argv holds."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from pathlib import Path

from ._helpers import _stub_tree, _run_wrapper


class TestThePeerNoteNamesEveryRunningPytest:
    """The note naming other live pytest runs reads each peer's whole argv.

    Plan step ``balance:X-dm``.  It asked ``printf '%s\\n' "$_peer_argv" |
    grep -qx ...`` under pipefail, so a ``grep`` that matched and left before
    ``printf`` finished writing could read a running pytest as absent and
    drop it from the note: the race that made ``deploy/shekel-deploy.sh``
    read a listed migration as absent (finding BAL-618).  Each peer here is a
    real process whose argv names a pytest, found by the wrapper's real
    ``pgrep`` and ``/proc``; other pytest runs on the host may be named too,
    and nothing here depends on them.
    """

    @staticmethod
    def _peer(*extra: str) -> subprocess.Popen[bytes]:
        """Start a stand-in peer whose argv names ``/xdm/bin/pytest``.

        ``bash -c`` keeps its own argv, so ``/proc/<pid>/cmdline`` reads
        ``bash``, ``-c``, the script, ``/xdm/bin/pytest`` and then *extra*.
        It leads a session of its own, so the whole group can be stopped.

        Args:
            extra: Further arguments to give it.

        Returns:
            The running process.
        """
        return subprocess.Popen(
            ["bash", "-c", "sleep 60; :", "/xdm/bin/pytest", *extra],
            start_new_session=True,
        )

    @staticmethod
    def _stop(peer: subprocess.Popen[bytes]) -> None:
        """Stop a stand-in peer and the ``sleep`` it started.

        Args:
            peer: A process :meth:`_peer` started.
        """
        os.killpg(peer.pid, signal.SIGTERM)
        peer.wait(timeout=10)

    def test_a_running_pytest_is_named_and_a_collect_only_one_is_not(
        self, tmp_path: Path
    ) -> None:
        """Both questions, one wrapper run: is it pytest, does it collect only."""
        binaries = _stub_tree(tmp_path)
        running = self._peer("-n", "12")
        collecting = self._peer("--collect-only", "-q")
        try:
            result = _run_wrapper(binaries, {})
        finally:
            self._stop(running)
            self._stop(collecting)

        assert result.returncode == 0, result.stderr
        assert f"pid {running.pid}  " in result.stderr, result.stderr
        assert f"pid {collecting.pid}  " not in result.stderr, result.stderr

    def test_a_python_dash_m_pytest_peer_is_named(self, tmp_path: Path) -> None:
        """A bare ``pytest`` argument names a peer as surely as a path does.

        ``python3 -m pytest`` leaves ``pytest`` alone on its line, found by
        the wrapper's ``pgrep -x python3``; this stand-in carries the same
        argument without running pytest.
        """
        python3 = shutil.which("python3")
        assert python3 is not None, "the stand-in peer needs a python3"
        binaries = _stub_tree(tmp_path)
        with subprocess.Popen(
            [python3, "-c", "import time; time.sleep(60)", "pytest"],
            start_new_session=True,
        ) as peer:
            try:
                result = _run_wrapper(binaries, {})
            finally:
                self._stop(peer)

        assert result.returncode == 0, result.stderr
        assert f"pid {peer.pid}  " in result.stderr, result.stderr

    def test_a_peer_whose_argv_outgrows_a_pipe_is_still_named(
        self, tmp_path: Path
    ) -> None:
        """1.3 MB of argv after the pytest line: it is named, every run.

        The pytest line comes early and twenty 64 KiB arguments follow it, so
        the replaced ``printf ... | grep -q`` had far more to write than a
        pipe holds when ``grep`` left, and dropped this peer every run.
        """
        binaries = _stub_tree(tmp_path)
        peer = self._peer(*["x" * 65536] * 20)
        try:
            result = _run_wrapper(binaries, {})
        finally:
            self._stop(peer)

        assert result.returncode == 0, result.stderr
        assert f"pid {peer.pid}  " in result.stderr, result.stderr
