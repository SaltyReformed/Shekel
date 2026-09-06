"""The SessionStart hook reports STANDING CONSTRAINTS, and both arms fire.

**Why the hook grew this at all** (developer ruling 2026-09-05).  A constraint
that lives only in coordinator messages is invisible to any session that clears
its context, and clearing context is ordinary.  On 2026-09-05 the shared main
checkout was pinned to a release branch: one session cleared, looked at the
tree, and concluded IT was driving the release -- because every fact a shared
tree offers is about the CHECKOUT and none is about the session reading them.  A
second session could not learn why its browser pass was impossible, the same
pin being the cause, and is worktree-isolated so it could not have looked.

**Two arms, because neither covers the other.**  The DERIVED arm reads the pin
off ``git worktree list`` and cannot go stale.  The STATED arm reads
``docs/plans/STANDING.md`` for what the tree does not imply.  Each is mutated in
its own direction below, and the two silences are what stop the arms firing on
every session forever -- a warning that always prints is not a warning.

**Two earlier spellings of the STATED filter were wrong in OPPOSITE
directions**, which is why the "prints no guidance" and "prints the whole
constraint" cases both exist: filtering "not a comment" printed the file's whole
guidance block, and filtering to bullet lines only printed each constraint
TRUNCATED at its first wrap -- a half-sentence being worse than nothing, because
it reads as complete.
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

HOOK = pathlib.Path(__file__).resolve().parents[2] / "scripts/hooks/session-start.sh"


def _git(cwd: pathlib.Path, *args: str) -> None:
    subprocess.run(("git", "-C", str(cwd), *args), check=True,
                   capture_output=True, text=True)


@pytest.fixture(name="checkouts")
def _checkouts(tmp_path):
    """Return (main, linked): a real repo on a branch, plus a worktree of it.

    A real git worktree rather than a fixture double, because the arm reads
    ``git worktree list`` and the whole defect lives in the difference between
    the session's checkout and the shared main one.  A single-checkout test
    could not reproduce it.
    """
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "dev")
    _git(main, "config", "user.email", "t@example.com")
    _git(main, "config", "user.name", "t")
    (main / "seed").write_text("seed\n")
    _git(main, "add", "seed")
    _git(main, "commit", "-qm", "seed")
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "-b", "feat/lane", str(linked))
    return main, linked


def _run(cwd: pathlib.Path) -> str:
    done = subprocess.run(("bash", str(HOOK)), cwd=str(cwd),
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    return done.stdout


class TestTheDerivedArmReadsThePinOffTheTree:
    """The release pin, which nobody has to remember to write down."""

    def test_a_release_branch_on_the_main_checkout_is_reported(self, checkouts):
        """The arm fires, and it names the path a session must not touch."""
        main, linked = checkouts
        _git(main, "checkout", "-q", "-b", "release/2026-09-05")

        out = _run(linked)
        assert "STANDING: the shared main checkout" in out
        assert str(main) in out
        assert "release/2026-09-05" in out
        assert "Do NOT check out, branch from, or" in out

    def test_an_ordinary_branch_is_SILENT(self, checkouts):
        """The other direction, and the reason the arm is worth having.

        A line printed on every session is furniture.  This is what keeps the
        warning meaning something on the day it appears.
        """
        main, linked = checkouts
        assert _git(main, "checkout", "-q", "dev") is None
        assert "STANDING: the shared main checkout" not in _run(linked)

    def test_it_reads_the_MAIN_checkout_and_not_the_session_s_own(self, checkouts):
        """The bug this exists for: the session's own branch is not the subject.

        A session on its own feature branch, in a worktree, must still learn
        that the SHARED checkout is pinned -- and a hook that reported ``$PWD``'s
        branch would say nothing at all.
        """
        main, linked = checkouts
        _git(main, "checkout", "-q", "-b", "release/2026-09-05")

        out = _run(linked)
        assert "branch: feat/lane" in out, "the session's own branch is still reported"
        assert "release/2026-09-05" in out, "and the shared pin is reported beside it"


class TestTheStatedArmReadsTheConstraintsFile:
    """What the tree does not imply, and the two ways a filter gets it wrong."""

    @staticmethod
    def _write(linked: pathlib.Path, body: str) -> None:
        plans = linked / "docs" / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        (plans / "STANDING.md").write_text(body)

    def test_a_constraint_is_printed_WHOLE_and_not_cut_at_its_first_wrap(
            self, checkouts):
        """A half-sentence is worse than nothing: it reads as complete."""
        _, linked = checkouts
        self._write(linked, "# Standing constraints\n\nGuidance paragraph.\n\n"
                            "- 2026-09-05: the first line of the constraint,\n"
                            "  its second line,\n"
                            "  and its third.\n")
        out = _run(linked)
        assert "the first line of the constraint," in out
        assert "its second line," in out
        assert "and its third." in out

    def test_the_files_own_GUIDANCE_is_not_printed(self, checkouts):
        """The first spelling of this filter printed the whole file."""
        _, linked = checkouts
        self._write(linked, "# Standing constraints\n\n"
                            "Guidance nobody needs at session start.\n\n"
                            "- 2026-09-05: the constraint.\n")
        out = _run(linked)
        assert "the constraint." in out
        assert "Guidance nobody needs" not in out

    def test_a_file_with_no_bullets_is_SILENT(self, checkouts):
        """EMPTY IS THE NORMAL STATE, so an empty file prints no heading."""
        _, linked = checkouts
        self._write(linked, "# Standing constraints\n\nGuidance only.\n")
        assert "STANDING CONSTRAINTS" not in _run(linked)

    def test_an_absent_file_is_SILENT(self, checkouts):
        """The hook must never block or complain -- it is a status hook."""
        _, linked = checkouts
        assert "STANDING CONSTRAINTS" not in _run(linked)
