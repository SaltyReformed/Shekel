"""``hook_repo_root`` resolves the SESSION's checkout, not the primary one.

**The first tests any hook helper has had**, and the absence is why this
regressed silently.  ``hook_repo_root`` was introduced to be the single
authority for repo-root resolution (finding HOOK/SH-12, after the question was
spelled three different ways in three scripts) -- and it resolved the wrong
tree for every session working in a git WORKTREE, which under this project's
Multi-session doctrine is every session.

**What that cost, measured 2026-09-04 in ``~/projects/shekel-xauf``.**
``hook_target_relpath`` normalizes the edited file against the resolved root; a
worktree file is not under the primary checkout, so it came back ABSOLUTE, and
every caller's ``case`` pattern missed.  The gates did not fail -- they SKIPPED.
``Decimal(0.1)`` written into ``app/`` through both Write and Edit was not
blocked, though pylint reports W9901 (``shekel-decimal-from-float``) and exits 4
on that file.  ``stop-check.sh`` meanwhile cd'd to the primary checkout and
linted a tree the session was not editing, reporting a peer's uncommitted work
as this lane's failure while passing this lane's real one.

**Why these cases are shaped as they are.**  The bug needs the session's cwd and
``CLAUDE_PROJECT_DIR`` to DISAGREE, which is exactly what a worktree produces
and what a single-checkout test would never reproduce -- so each case builds a
real git repository, points ``CLAUDE_PROJECT_DIR`` somewhere else, and asserts
against the cwd.  Run against the pre-fix helper every case here fails, which is
the control: ``hook_repo_root`` returned ``CLAUDE_PROJECT_DIR`` unconditionally,
so it answered the decoy in :func:`test_repo_root_prefers_the_sessions_own_checkout`
and ``hook_target_relpath`` answered an absolute path in
:func:`test_an_app_file_normalizes_RELATIVE_from_a_worktree`.
"""

import json
import subprocess
from pathlib import Path

import pytest


#: The helper under test, resolved from THIS file rather than from a cwd --
#: these cases deliberately run bash with cwd set elsewhere.
_HOOKLIB = Path(__file__).resolve().parents[2] / "scripts" / "hooks" / "_hooklib.sh"


def _bash(script: str, cwd: Path, project_dir: Path, stdin: str = "") -> str:
    """Source the hook library and run *script*, returning its stdout.

    Args:
        script: Bash to run after the library is sourced.
        cwd: The working directory to run in -- stands in for the session's
            own checkout.
        project_dir: The value of ``CLAUDE_PROJECT_DIR`` -- stands in for the
            PRIMARY checkout, which is what the harness actually sets it to.
        stdin: Payload piped to the script, for the helpers that read it.

    Returns:
        Stdout, stripped.
    """
    completed = subprocess.run(
        ["bash", "-c", f'set -uo pipefail; . "{_HOOKLIB}"; {script}'],
        cwd=cwd,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(cwd),
            "CLAUDE_PROJECT_DIR": str(project_dir),
        },
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"hook helper exited {completed.returncode}: {completed.stderr}"
    )
    return completed.stdout.strip()


@pytest.fixture(name="checkout")
def _checkout(tmp_path: Path) -> Path:
    """A real git repository standing in for the session's own worktree.

    A real one rather than a stubbed directory: the helper resolves the root
    with ``git rev-parse --show-toplevel``, so a fake would grade the fallback
    branch instead of the fix.
    """
    root = tmp_path / "session-checkout"
    (root / "app").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


@pytest.fixture(name="primary")
def _primary(tmp_path: Path) -> Path:
    """A DIFFERENT directory, standing in for the primary checkout.

    This is the decoy: the harness sets ``CLAUDE_PROJECT_DIR`` to the primary
    checkout whatever worktree the session is in, and the pre-fix helper
    returned it unconditionally.
    """
    root = tmp_path / "primary-checkout"
    root.mkdir()
    return root


class TestTheRootResolvesToTheSessionsCheckout:
    """The resolution itself, and the decoy it must not follow."""

    def test_repo_root_prefers_the_sessions_own_checkout(
        self, checkout: Path, primary: Path,
    ):
        """cwd's git toplevel wins over ``CLAUDE_PROJECT_DIR``.

        The whole bug in one assertion.  Pre-fix this returned *primary*.
        """
        assert _bash("hook_repo_root", checkout, primary) == str(checkout)

    def test_a_trailing_slash_is_still_stripped(
        self, checkout: Path, primary: Path,
    ):
        """The behaviour finding HOOK/SH-12 added is not lost to the fix.

        A trailing slash silently defeated the relpath strip once already; the
        fallback path is the one that can still carry one, so it is what this
        exercises.
        """
        root = _bash(
            "hook_repo_root", checkout.parent, Path(f"{primary}/"),
        )
        assert not root.endswith("/")

    def test_it_FALLS_BACK_outside_a_git_checkout(
        self, tmp_path: Path, primary: Path,
    ):
        """Outside a repository the old answer is still the right one.

        ``git rev-parse`` fails there, and failing to the documented
        environment variable is better than failing to a bare ``$PWD`` that
        names no project at all.
        """
        loose = tmp_path / "not-a-repo"
        loose.mkdir()
        assert _bash("hook_repo_root", loose, primary) == str(primary)


class TestTheEditedFileNormalizesAgainstThatRoot:
    """The consequence: an absolute path here is a SKIPPED gate."""

    def test_an_app_file_normalizes_RELATIVE_from_a_worktree(
        self, checkout: Path, primary: Path,
    ):
        """``app/foo.py``, not ``/abs/path/app/foo.py``.

        This is the assertion that maps to the live defect.  Every caller
        matches the result against ``app/*.py``; an absolute path matches
        nothing, so the hook exits 0 and the edit sails through unlinted.
        Pre-fix this returned the absolute path.
        """
        edited = checkout / "app" / "foo.py"
        edited.write_text("x = 1\n", encoding="utf-8")
        payload = json.dumps({"tool_input": {"file_path": str(edited)}})

        assert _bash(
            "hook_target_relpath", checkout, primary, stdin=payload,
        ) == "app/foo.py"

    def test_a_file_OUTSIDE_the_checkout_stays_absolute(
        self, checkout: Path, primary: Path, tmp_path: Path,
    ):
        """The skip that IS correct, so the fix does not over-reach.

        A file genuinely outside the project must still fall out of every
        caller's ``case``.  Keeping this arm is what stops the fix from turning
        "resolve the right root" into "match everything".
        """
        outside = tmp_path / "elsewhere.py"
        outside.write_text("x = 1\n", encoding="utf-8")
        payload = json.dumps({"tool_input": {"file_path": str(outside)}})

        assert _bash(
            "hook_target_relpath", checkout, primary, stdin=payload,
        ) == str(outside)
