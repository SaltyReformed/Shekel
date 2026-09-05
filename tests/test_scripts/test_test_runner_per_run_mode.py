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
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEST_RUNNER = _REPO_ROOT / "scripts/test.sh"


def _stub_tree(tmp_path: Path) -> Path:
    """Write a stub ``docker`` and ``pytest`` into a directory for ``PATH``.

    The docker stub answers only what the per-run branch asks and records
    each subcommand.  The pytest stub records the ENVIRONMENT it was handed,
    which is what the flag-scrub assertion reads.

    Args:
        tmp_path: pytest-provided scratch directory.

    Returns:
        The directory to prepend to ``PATH``.
    """
    binaries = tmp_path / "bin"
    binaries.mkdir()
    calls = tmp_path / "docker-calls"
    env_dump = tmp_path / "pytest-env.json"

    (binaries / "docker").write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> {calls}\n'
        'case "${1:-}" in\n'
        '    run)  echo stubcontainer ;;\n'
        '    port) echo "127.0.0.1:59123" ;;\n'
        '    exec) exit 0 ;;\n'
        '    inspect) exit 1 ;;\n'
        '    rm)   exit 0 ;;\n'
        '    *)    exit 0 ;;\n'
        "esac\n",
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

    Args:
        binaries: Directory holding the stubs.
        env_extra: Environment overrides.

    Returns:
        The completed process.
    """
    return subprocess.run(
        [str(_TEST_RUNNER), "tests/does_not_matter"],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env={
            **os.environ,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            "TEST_DB_IMAGE": "stub-image:test",
            **env_extra,
        },
        check=False,
    )


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
        assert "59123" in handed["TEST_ADMIN_DATABASE_URL"], (
            "the admin DSN does not name the private cluster's port"
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
