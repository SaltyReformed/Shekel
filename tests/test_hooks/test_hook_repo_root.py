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
and what a single-checkout test would never reproduce -- so the cases that grade
the fix build a real git checkout, point ``CLAUDE_PROJECT_DIR`` somewhere else,
and assert against the cwd.

**Which cases are the CONTROL, measured rather than reasoned.**  Substituting
``origin/dev``'s helper and re-running gives **7 failed, 3 passed** (2026-09-04).
The three that pass either way are kept deliberately and are NOT padding --
they are boundary guards: the fallback arm, the trailing-slash strip finding
HOOK/SH-12 added, and the file genuinely outside the project, which is what
stops the fix turning "resolve the right root" into "match everything".

**Two of those seven failures are weaker than the other five, and saying so is
the point.**  The three ``TestTheToolchainRootStaysWithThePrimaryCheckout``
cases fail against dev only because ``hook_toolchain_root`` does not exist
there, so bash errors -- that guards the function being DELETED and says
nothing about it returning a wrong value.  The value-level control is a
separate injection, also run: re-pointing the toolchain resolver at the edited
tree (``hook_toolchain_root() { hook_repo_root; ... }``) fails those same three
and only those three, 3 failed / 7 passed.  Both directions were run; neither
was inferred.

**The toolchain cases grade the review's H-1**, which is a regression this fix
introduced before review caught it: ``hook_repo_root`` had FIVE consumers, and
one of them -- the ``_hook_venv_bin`` PATH prefix -- was asking "where does the
pinned toolchain live", not "which tree is being edited".  For that question the
PRIMARY checkout is the right answer, because worktrees carry no ``.venv`` of
their own.  Re-pointing it at the session's worktree left the venv path naming
a directory that does not exist, so ``pylint`` became unresolvable and the two
gates that invoke it (the Python per-edit gate and the Stop floor) hard-blocked
on "command not found" while blaming a financial-correctness rule.
:class:`TestTheToolchainRootStaysWithThePrimaryCheckout` is what stops that
being re-introduced.
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


@pytest.fixture(name="linked_worktree")
def _linked_worktree(checkout: Path) -> Path:
    """A real ``git worktree`` checkout, whose ``.git`` is a FILE.

    The shape the bug was specific to, and the one ``git init`` cannot produce.
    A worktree needs a commit to branch from, so the fixture makes an empty one
    with identity supplied on the command line rather than from a global config
    the test environment may not have.
    """
    identity = [
        "-c", "user.email=test@example.invalid", "-c", "user.name=Test",
    ]
    subprocess.run(
        ["git", "-C", str(checkout), *identity,
         "commit", "-q", "--allow-empty", "-m", "seed"],
        check=True,
    )
    linked = checkout.parent / "linked-worktree"
    subprocess.run(
        ["git", "-C", str(checkout), "worktree", "add", "-q", str(linked)],
        check=True,
    )
    # The premise, asserted rather than assumed: if this ever became a
    # directory the cases below would silently degrade to the ``git init``
    # shape they exist to be different from.
    assert (linked / ".git").is_file()
    return linked


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

        **Asserts the VALUE, not just the absence of a slash** (adversarial
        review, 2026-09-04).  ``assert not root.endswith("/")`` admits ``""``,
        which is the catastrophic answer: not because ``cd ""`` succeeds (it
        fails with "null directory" and trips the callers' ``||`` block) but
        because ``hook_target_relpath`` realpaths the root, and
        ``os.path.realpath("")`` is the CWD, so an empty root would silently
        normalize every edited file against whatever tree the hook stood in.
        Comparing against *primary* also proves the FALLBACK arm was the one
        taken: this case only reaches it because ``tmp_path`` sits outside
        any repository, and if ``TMPDIR`` were ever pointed inside a checkout
        ``git rev-parse`` would succeed, git output never carries a trailing
        slash, and the strip would go ungraded while the test still passed.
        """
        root = _bash(
            "hook_repo_root", checkout.parent, Path(f"{primary}/"),
        )
        assert root == str(primary)

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


class TestTheShapeTheFixExistsFor:
    """A LINKED worktree, which is the only shape that ever had the bug."""

    def test_a_linked_worktree_resolves_to_ITSELF(
        self, linked_worktree: Path, primary: Path,
    ):
        """``git worktree add``, not ``git init`` -- the production shape.

        Every other case here builds a repository with ``git init``, which
        gives a ``.git`` DIRECTORY.  A linked worktree has a ``.git`` FILE
        holding ``gitdir: /path/to/primary/.git/worktrees/<name>``, and that is
        the only shape this fix exists for: the sessions that lost their gates
        were all ``git worktree`` checkouts.  A suite that grades only the
        ``git init`` shape grades the fix's neighbourhood rather than the fix
        (adversarial review, 2026-09-04).
        """
        assert _bash(
            "hook_repo_root", linked_worktree, primary,
        ) == str(linked_worktree)

    def test_a_SUBDIRECTORY_resolves_to_the_worktree_root(
        self, linked_worktree: Path, primary: Path,
    ):
        """Hooks do not necessarily run at the top of the tree.

        The other discriminating improvement, and it was ungraded: from
        ``<worktree>/app`` the old code returned the primary and the new code
        returns the worktree ROOT, which is what every caller's ``cd`` and
        relpath depend on.
        """
        nested = linked_worktree / "app"
        nested.mkdir(exist_ok=True)
        assert _bash("hook_repo_root", nested, primary) == str(linked_worktree)


class TestTheToolchainRootStaysWithThePrimaryCheckout:
    """The H-1 regression's guard: two questions, two answers.

    ``_hook_venv_bin`` asks where the PINNED toolchain lives, which is not the
    tree being edited.  Worktrees carry no ``.venv`` -- measured across the
    eight on this machine: a real one in the primary, a symlink in one
    worktree, nothing in the other six, and no ``pylint`` anywhere else on the
    box.  So this must keep answering with the primary even from a worktree, or
    the PATH prefix silently vanishes and the two gates that invoke ``pylint``
    (the Python per-edit gate and the Stop floor) hard-block on "command not
    found" while blaming a financial-correctness rule.
    """

    def test_the_toolchain_root_ignores_the_sessions_worktree(
        self, linked_worktree: Path, primary: Path,
    ):
        """From inside a worktree it still answers ``CLAUDE_PROJECT_DIR``.

        The exact inverse of :func:`test_repo_root_prefers_the_sessions_own_checkout`,
        and asserting them side by side is the point: one function must follow
        the cwd and the other must not.
        """
        assert _bash(
            "hook_toolchain_root", linked_worktree, primary,
        ) == str(primary)

    def test_the_two_roots_DISAGREE_from_a_worktree(
        self, linked_worktree: Path, primary: Path,
    ):
        """They must not collapse back into one answer.

        A future edit that re-unified them would pass both cases above only if
        cwd and ``CLAUDE_PROJECT_DIR`` happened to agree -- which they never do
        in the situation this file exists for.  This asserts the divergence
        itself.
        """
        both = _bash(
            "hook_repo_root; hook_toolchain_root", linked_worktree, primary,
        ).split("\n")
        assert both == [str(linked_worktree), str(primary)]

    def test_it_strips_a_trailing_slash_too(
        self, linked_worktree: Path, primary: Path,
    ):
        """The HOOK/SH-12 behaviour belongs to BOTH resolvers now.

        The trailing-slash strip was a property of the single helper; splitting
        it in two is exactly how one half quietly loses it.
        """
        root = _bash(
            "hook_toolchain_root", linked_worktree, Path(f"{primary}/"),
        )
        assert root == str(primary)


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
