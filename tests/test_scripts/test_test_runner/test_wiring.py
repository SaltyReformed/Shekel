"""The test runner's own source: it is valid bash, it starts and stops its cluster only through the
cluster's one home, an image beginning with a dash reaches the start as the image, and the DSNs
pytest is handed name this run's socket.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from ._helpers import _TEST_RUNNER, _STARTS_THE_CLUSTER, _stub_tree, _run_wrapper, _code_lines


class TestTheWrapperItself:
    """Properties of the script that every other case here presupposes."""

    def test_the_wrapper_is_valid_bash(self) -> None:
        """``scripts/test.sh`` parses. Nothing else in the repo checks this.

        Kept from the deleted container-state module, which is where it was
        earned: that module stripped shell quoting from the state
        classifier's arm patterns, so its canonical spelling of the paused arm
        was ``*(Paused)*`` -- precisely the spelling that is a SYNTAX ERROR in
        the shell.  Removing the quotes at the source was measured to leave
        every test there green while ``bash -n scripts/test.sh`` failed and
        the wrapper refused to run at all, taking every local test invocation
        with it.  shellcheck and shfmt both accept that form, so without this
        assertion the breakage reaches a developer's terminal before any gate.
        The classifier went with the shared path; a syntax error in this file
        still stops the whole repository's testing, so the check outlives what
        motivated it.
        """
        result = subprocess.run(
            ["bash", "-n", str(_TEST_RUNNER)],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, (
            f"scripts/test.sh is not valid bash: {result.stderr.strip()}. "
            "Every test invocation in this repo goes through that wrapper, "
            "so this breaks all local testing."
        )

    def test_the_cluster_is_started_and_stopped_through_its_one_home(
        self,
    ) -> None:
        """The wrapper starts and stops its cluster through
        ``scripts/private_cluster.py`` and spells no container command of its own.

        Step X-cx's K1a moved the cluster's life into that module so the
        release rehearsal kit starts its cluster the same way: one ``docker
        run``, one readiness rule, one teardown.  A wrapper that spelled its
        own ``docker run`` again would pass every end-to-end case below, since
        the stub docker cannot tell who called it; this reads the SOURCE for
        that reason, as the trap-arm case does, a command at a time
        (:func:`_code_lines`).
        """
        code = _code_lines()

        assert any('scripts/private_cluster.py" stop "$_token"' in line
                   for line in code), "the teardown no longer stops through the module"
        assert any(_STARTS_THE_CLUSTER.search(line) for line in code), (
            "the cluster is no longer started through the module")
        # Global flags may come before the subcommand (``docker -H <host> rm``,
        # ``docker --context=x run``); the delta review's D17 slipped one past.
        spelled = [line for line in code if re.search(
            r"\bdocker\s+(-\S+\s+(?:[^-\s]\S*\s+)?)*(container\s+)?"
            r"(run|create|start|stop|kill|rm|exec|inspect)\b",
            line)]
        assert not spelled, (
            f"scripts/test.sh spells a container command of its own again: {spelled}"
        )

    @pytest.mark.parametrize("image", ["--help", "-x"])
    def test_an_image_beginning_with_a_dash_reaches_the_start_as_the_image(
        self, tmp_path: Path, image: str
    ) -> None:
        """A ``TEST_DB_IMAGE`` beginning with ``-`` is handed to the cluster's
        start as the image, never read as one of its options.

        The review of 668b53c99's nit: with no ``--`` ending the start's
        options, ``-x`` was read as an option, and the start refused for want
        of an image (exit 2); ``--help`` printed the start's usage and exited
        0, and the wrapper handed pytest that usage as the socket directory
        (measured on stubs, 2026-10-18).  The stub docker starts any image; a
        real one reads such an image as a flag of its own and refuses the
        run, which the start names as an error (measured the same day on the
        rootless daemon, from both images: exit 2, nothing left).

        Args:
            tmp_path: scratch directory.
            image: The image the wrapper is given.
        """
        binaries = _stub_tree(tmp_path)
        result = _run_wrapper(binaries, {"TEST_DB_IMAGE": image})
        seen = (tmp_path / "docker-calls").read_text(encoding="utf-8").splitlines()
        started = [line.split() for line in seen if line.startswith("run ")]

        assert started, f"nothing was started from {image}; docker saw {seen}: {result.stderr}"
        assert started[0][started[0].index("-v") + 2] == image, started[0]
        assert result.returncode == 0, result.stderr


class TestTheClusterTheChildTalksTo:
    """pytest is handed DSNs naming THIS run's socket, and nothing else.

    The wrapper used to read ``TEST_DATABASE_URL`` out of ``.env`` -- naming
    the shared container on ``localhost:5433`` -- and the per-run branch
    overwrote it afterwards.  ``balance:X-br-4`` deleted that read along with
    the shared path, so the only DSNs that can reach pytest are the ones built
    from the socket directory this invocation made.  Restoring a dotenv read
    would point the suite back at a cluster other runs share, which is the
    defect this arc exists to remove; these assertions are what would notice.
    """

    def test_both_dsns_name_this_run_s_own_socket(self, tmp_path: Path) -> None:
        """The admin and the application DSN both reach the private cluster.

        Args:
            tmp_path: scratch directory.
        """
        binaries = _stub_tree(tmp_path)
        result = _run_wrapper(binaries, {})
        env_dump = tmp_path / "pytest-env.json"

        assert env_dump.exists(), (
            f"the stub pytest was never reached: rc={result.returncode} "
            f"stderr={result.stderr.strip()!r}"
        )
        handed = json.loads(env_dump.read_text(encoding="utf-8"))

        for required in ("TEST_ADMIN_DATABASE_URL", "TEST_DATABASE_URL"):
            assert required in handed, f"{required} was not handed to pytest"
            # The socket DIRECTORY, and this RUN's: a bare ``?host=/`` would
            # pass for any socket-shaped DSN, a dotenv's included.  The
            # cluster's module names the directory after the run's token.
            assert "?host=" in handed[required], (
                f"{required} does not name a unix socket at all, so it is not "
                f"this run's private cluster: {handed[required]}"
            )
            assert "/shekel-cluster-" in handed[required], (
                f"{required} does not name a directory this invocation made "
                f"-- it may be coming from somewhere shared: {handed[required]}"
            )
