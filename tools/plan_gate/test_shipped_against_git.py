"""Controls for the arms that grade ``steps.md`` against GIT.

Every other suite here plants a defect in a DOCUMENT.  These plant one in the
relationship between a document and the repository, which is the relationship
nothing in this package graded until ``balance:X-au-f-2`` shipped on 2026-09-10
and sat ranked ``#17`` for a day with the gate green at 320/320.

**The first test is the one that matters most**, and it is not a defect control:
it asserts the arms can actually RUN.  Rule 7's hash arm reasons that resolving
a hash "would need git, and CI checks out shallow ... a gate that passes on
nothing" -- correct about the shallow checkout, and the other way out is to
stop checking out shallow.  ``ci.yml`` now passes ``fetch-depth: 0``, and this
assertion is what fails if that ever comes back out, instead of the arms
silently grading nothing.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import _registry as registry
import _shipped

#: A git identity for the commits these controls write, scoped per command so
#: nothing about the developer's configuration is read or written -- and so a
#: CI runner, which has none, does not die with ``fatal: empty ident name``.
_IDENT = ("-c", "user.name=plan gate control", "-c", "user.email=plan-gate@localhost")


def _dangling_commit(message: str, cwd: Path | None = None) -> str:
    """Write a commit on no branch, over HEAD's tree, and return its full sha.

    A control needs a commit that EXISTS in the object database and is not an
    ancestor of ``HEAD``, so the ancestry clause is told apart from the
    resolves-at-all clause: a fabricated hash would fire the first and leave
    the second ungraded.  ``commit-tree`` writes exactly that.  The default
    root is read at CALL time, so a control that has re-pointed
    ``registry.REPO`` gets the repository it re-pointed to.
    """
    return subprocess.run(
        ("git", *_IDENT, "commit-tree", "HEAD^{tree}", "-m", message),
        cwd=cwd or registry.REPO, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _isolate(monkeypatch, root: Path) -> None:
    """Pin every git call this process makes to ``root``, whatever it inherited.

    **Under the pre-commit hook in a LINKED WORKTREE, git exports ``GIT_DIR``
    (``.git/worktrees/<name>``) and ``GIT_INDEX_FILE`` to the hook, and
    pre-commit hands them to a ``language: system`` entry untouched.**  With
    ``GIT_DIR`` set, the cwd no longer decides which repository a command
    operates on: measured 2026-09-11 on a replica, ``git init`` in a fresh
    temporary directory under that environment printed ``re-init``, wrote
    ``core.bare=true`` into the DEVELOPER'S shared config, and left the main
    checkout answering *this operation must be run in a work tree*.  The gate
    hook fires on this very file, so a scratch fixture without this pin would
    have done that to the live repository on its first commit from a worktree.

    Fail-closed: every inherited ``GIT_*`` is dropped, then the two that decide
    the repository are set explicitly.  The pin is process-wide so the module
    under test, whose ``_git`` inherits ``os.environ``, is pinned too.

    **The environment is not the only route in.**  The builder's commits are
    real ``git commit`` calls, and the developer's GLOBAL and SYSTEM config
    reach them through ``HOME``: a ``core.hooksPath`` runs the developer's
    hooks against the scratch, a ``commit.gpgsign`` asks for a signature, an
    ``init.templateDir`` seeds hooks.  Measured on the second review of this
    file (a hostile ``HOME``: the builder failed at its first commit and the
    foreign hook's marker was written).  So both config files are pointed at
    ``os.devnull`` as well; ``-c user.*`` on each command ADDS config and
    excludes nothing.
    """
    for name in [n for n in os.environ if n.startswith("GIT_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_DIR", str(root / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(root))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)


def _git(cwd: Path, *args: str) -> str:
    """Run one git command in a scratch repository and return its stdout."""
    return subprocess.run(
        ("git", *_IDENT, *args), cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _build_merge_in_waiting(monkeypatch, root: Path, claim: str) -> str:
    """Build the scratch repository the merge-in-progress controls grade.

    ``main`` holds A then C; ``other`` holds A then B.  C keeps the merge from
    fast-forwarding, which is what leaves ``MERGE_HEAD`` behind when the merge
    is stopped before its commit.  B carries ``claim`` in its body, so the
    unticked-leaf arm -- which reads history through its own ``git log`` --
    has something to find only once B is carried.  Returns B's sha.

    The isolation is done HERE and not by the caller, so the control that
    plants a hostile environment grades the builder every fixture uses.
    """
    _isolate(monkeypatch, root)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "commit", "-q", "--allow-empty", "-m", "A: the base")
    _git(root, "checkout", "-q", "-b", "other")
    _git(root, "commit", "-q", "--allow-empty", "-m",
         f"B: on the branch being merged\n\n{claim}")
    other = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", "main")
    _git(root, "commit", "-q", "--allow-empty", "-m", "C: on the receiving branch")
    return other


def _live(ident: str) -> registry.StepRow:
    """Return a live step row by bare ident, failing the control if it is gone."""
    for row in registry.step_rows():
        if row.ident == ident:
            return row
    pytest.fail(f"{ident} has left steps.md; re-anchor this control on a live row")
    raise AssertionError  # unreachable, and pylint wants a return path


def _open_leaf() -> registry.StepRow:
    """Return a live ranked leaf, so a scratch commit can claim it by name."""
    for row in registry.step_rows():
        if not row.shipped and not row.is_container:
            return row
    pytest.fail("steps.md has no open leaf left; these controls need one to claim")
    raise AssertionError  # unreachable, and pylint wants a return path


class TestTheArmsCanRun:
    """The blindness guard: an arm that cannot run says so and fails loudly."""

    def test_this_checkout_can_be_graded_against_git(self):
        """The live tree is a full clone, so both arms below actually grade."""
        assert _shipped.history_is_gradeable(), (
            "this checkout cannot be graded against git -- it is shallow, or it "
            "is not a repository. CI must pass fetch-depth: 0 to actions/checkout; "
            "without it these arms grade NOTHING and report clean"
        )

    def test_a_shallow_checkout_reports_blindness_rather_than_passing(self, monkeypatch):
        """A shallow clone turns both arms off, and the guard above is what catches it."""
        monkeypatch.setattr(_shipped, "history_is_gradeable", lambda: False)
        assert not _shipped.shipped_commit_violations()
        assert not _shipped.unticked_leaf_violations()


class TestEveryShippedRowNamesACommitThisTreeCarries:
    """Rule 7: a tick names a commit a reader can go and read."""

    def test_the_live_corpus_is_clean(self):
        """No shipped row cites an unresolvable or unmerged commit."""
        assert not _shipped.shipped_commit_violations()

    def test_the_control_fires_on_a_hash_that_is_not_a_commit(self, stage):
        """A tick citing a non-commit is refused."""
        row = next(r for r in registry.step_rows() if r.shipped)
        stage("steps", f"| {row.commit.strip()} |", "| `0123456789ab` |")
        problems = _shipped.shipped_commit_violations()
        assert any("is not a commit" in p for p in problems), problems

    def test_the_control_fires_on_a_commit_that_has_not_merged(self, stage):
        """A tick citing a real commit this tree does not carry is refused.

        Staged with a commit that EXISTS in the object database and is not an
        ancestor of HEAD, so the arm's two clauses are told apart: a control
        using a fabricated hash would fire the first clause and leave the
        second ungraded.

        **The identity is supplied rather than inherited.**  ``commit-tree``
        writes an author and a committer, and a CI runner has neither
        ``user.name`` nor ``user.email`` configured -- this control passed on a
        workstation and died on GitHub with ``fatal: empty ident name``, an
        exit 128 that reads as the arm being broken rather than the control
        needing a name. ``-c`` scopes it to this one command, so nothing about
        the developer's git configuration is read or written.
        """
        dangling = _dangling_commit("not on any branch")
        row = next(r for r in registry.step_rows() if r.shipped)
        stage("steps", f"| {row.commit.strip()} |", f"| `{dangling[:8]}` |")
        problems = _shipped.shipped_commit_violations()
        assert any("not an ancestor of HEAD" in p for p in problems), problems

    def test_a_commit_reachable_only_through_the_second_head_is_merged(self, stage, monkeypatch):
        """The same tick passes once the grade is asked of a head that carries it.

        The pair with the control above: one ``graded_heads`` apart, the same staged
        row and the same dangling commit, and the arm must answer differently.
        This is the arm-level half of the merge-in-progress fix; the half that
        proves ``graded_heads`` itself reads ``MERGE_HEAD`` is
        :class:`TestAMergeBeingCommittedIsGradedAgainstBothParents`.

        **The dangling head is ADDED to the live heads, not substituted for
        them, and the answer is read by the staged ROW.**  The pre-commit hook
        runs this control while a merge is being committed, and its first
        version replaced ``("HEAD", "MERGE_HEAD")`` with ``("HEAD", dangling)``
        -- which refused every tick dev had made -- then filtered the messages
        by the dangling sha, which every message carried in its "ancestor of
        HEAD or <sha>" text.  It failed on the first conflicted resync after
        the fix merged (2026-09-11), the exact state the fix exists for.
        """
        dangling = _dangling_commit("reachable through the second head only")
        heads = (*_shipped.graded_heads(), dangling)
        monkeypatch.setattr(_shipped, "graded_heads", lambda: heads)
        row = next(r for r in registry.step_rows() if r.shipped)
        stage("steps", f"| {row.commit.strip()} |", f"| `{dangling[:8]}` |")
        problems = _shipped.shipped_commit_violations()
        assert not [p for p in problems if p.startswith(f"{row.key} is SHIPPED")], problems


class TestNoOpenLeafHasAlreadyShipped:
    """Rules 7 and 2: the index and the repository agree on what is done."""

    def test_the_live_corpus_is_clean(self):
        """No commit in this tree claims a step steps.md still ranks."""
        assert not _shipped.unticked_leaf_violations()

    def test_the_control_fires_on_the_specimen_this_arm_exists_for(self, stage):
        """Un-ticking a shipped leaf whose commit names it is refused.

        ``balance:X-au-f-2`` is the live specimen: its commit ``cb4239a2`` says
        ``Plan step balance:X-au-f-2`` in its own body, so putting the row back
        the way 2026-09-10 left it reproduces the exact defect.
        """
        row = _live("X-au-f-2")
        stage("steps", f"| SHIPPED | {row.commit.strip()} | -- |", "| #17 | -- | NOW |")
        problems = _shipped.unticked_leaf_violations()
        assert any("X-au-f-2" in p for p in problems), problems

    def test_a_container_is_never_graded(self):
        """A commit naming a container is naming its SPAN, not a step it shipped.

        The exemption is measured, not assumed: grading containers raised five
        false positives on the live corpus against one true finding, each a leaf
        commit citing the family it belongs to.
        """
        containers = [r for r in registry.step_rows() if r.is_container]
        assert containers, "no container left to grade this exemption against"
        claimed = {p.split(":")[1].split(" ")[0] for p in _shipped.unticked_leaf_violations()}
        assert not claimed & {r.ident for r in containers}

    def test_a_commit_that_declines_the_tick_is_not_a_violation(self, monkeypatch):
        """A commit saying it does NOT tick its step is honest, not a miss.

        ``1cd4e61b`` is the live specimen: it shipped a rehearsed runbook for
        ``balance:X-f3c-2b-2c`` and states that the tick waits on a production
        deploy.  An arm that graded it would punish the one behaviour it wants.
        """
        row = _live("X-f3c-2b-2c")
        monkeypatch.setattr(_shipped, "_commits", lambda: [
            ("0" * 40, f"test(x): a runbook\n\nPlan step {row.arc}:{row.ident}. "
                       "It does NOT tick its step: the repair needs a deploy."),
        ])
        assert not _shipped.unticked_leaf_violations()

    def test_the_control_fires_when_that_disclaimer_is_absent(self, monkeypatch):
        """The same commit without its disclaimer IS a violation.

        The pair is what proves the exemption discriminates: one staged string
        apart, and the arm must answer differently.
        """
        row = _live("X-f3c-2b-2c")
        monkeypatch.setattr(_shipped, "_commits", lambda: [
            ("0" * 40, f"test(x): a runbook\n\nPlan step {row.arc}:{row.ident}."),
        ])
        assert any(row.ident in p for p in _shipped.unticked_leaf_violations())


class TestAMergeBeingCommittedIsGradedAgainstBothParents:
    """A merge commit has two parents, and the grade is asked of both.

    **Measured, not hypothetical.**  On 2026-09-11 the first conflicted registry
    resync after these arms merged -- `fix/rec516-undated-row-skip` taking dev
    at `584b30fa` -- was refused by pre-commit on ``balance:X-au-f-2``'s
    ``cb4239a2``: an ancestor of ``MERGE_HEAD``, not of the branch's old tip,
    and the commit being made descended from both.  Every conflicted resync
    that stages a gated file would have failed the same way.

    These controls build the merge state in a SCRATCH repository and pin the
    process's git environment to it (:func:`_isolate`), rather than writing
    ``MERGE_HEAD`` into the live checkout: a control that crashed half way
    would otherwise leave the developer's tree looking mid-merge.  ``steps.md``
    is still read from the live tree, since the registry paths are bound at
    import and only the git root moves.
    """

    @pytest.fixture(name="scratch")
    def _scratch(self, tmp_path, monkeypatch) -> dict[str, str]:
        """The scratch repository, with the module's git root pointed at it."""
        leaf = _open_leaf()
        other = _build_merge_in_waiting(
            monkeypatch, tmp_path, f"Plan step {leaf.arc}:{leaf.ident}.",
        )
        monkeypatch.setattr(registry, "REPO", tmp_path)
        return {"root": str(tmp_path), "other": other, "claimed": leaf.key}

    def test_outside_a_merge_only_head_is_asked(self, scratch):
        """With no merge in progress the grade is HEAD's alone, as it always was."""
        assert _shipped.graded_heads() == ("HEAD",)
        assert not _shipped.is_carried(scratch["other"])
        assert not [p for p in _shipped.unticked_leaf_violations() if scratch["claimed"] in p]

    def test_a_merge_being_committed_asks_both_parents(self, scratch):
        """Once MERGE_HEAD exists, a commit reachable only through it is carried.

        ``--no-commit`` stops the merge exactly where the pre-commit hook runs:
        both parents known, the merge commit not yet written.  The unticked-leaf
        arm is asked as well as the ancestry predicate, because it reads the
        history through its own ``git log`` and could disagree.
        """
        root = Path(scratch["root"])
        _git(root, "merge", "-q", "--no-commit", "other")
        assert _git(root, "rev-parse", "MERGE_HEAD") == scratch["other"]
        assert _shipped.graded_heads() == ("HEAD", "MERGE_HEAD")
        assert _shipped.is_carried(scratch["other"])
        claimed = [p for p in _shipped.unticked_leaf_violations() if scratch["claimed"] in p]
        assert claimed and scratch["other"][:9] in claimed[0], claimed

    def test_a_merge_in_progress_still_refuses_a_commit_neither_parent_carries(self, scratch):
        """Two heads widen what is carried; they do not stop grading it.

        The discriminating half: a dangling commit is an ancestor of neither
        parent, and the arm must still say so while the merge is in progress.
        """
        root = Path(scratch["root"])
        _git(root, "merge", "-q", "--no-commit", "other")
        dangling = _dangling_commit("on neither parent", cwd=root)
        assert _shipped.graded_heads() == ("HEAD", "MERGE_HEAD")
        assert not _shipped.is_carried(dangling)


class TestTheScratchRepositoryCannotReachTheDevelopersRepository:
    """The builder above is safe under the environment the pre-commit hook exports.

    The hazard is in :func:`_isolate`'s docstring; these are the controls that
    would have fired.  A VICTIM repository with a linked worktree stands in for
    the developer's; the process environment is set as git sets it for a hook
    running from that worktree in the two variables that decide the repository
    (an absolute ``GIT_DIR`` at the worktree's gitdir and ``GIT_INDEX_FILE``
    beside it, no ``GIT_WORK_TREE``); the builder runs; and the victim must be
    untouched: not re-initialised bare, no branch added, no commit landed, its
    linked worktree's HEAD symref byte-identical.  With the environment pin
    removed from the builder the first control fails on ``core.bare``; with
    the config pin removed the second fails at the builder's first commit.
    """

    @staticmethod
    def _victim(tmp_path, monkeypatch) -> tuple[Path, Path, dict[str, str]]:
        """Build the victim and its linked worktree; return both roots and a snapshot."""
        victim = tmp_path / "victim"
        victim.mkdir()
        _isolate(monkeypatch, victim)
        _git(victim, "init", "-q", "-b", "main")
        _git(victim, "commit", "-q", "--allow-empty", "-m", "the developer's commit")
        _git(victim, "worktree", "add", "-q", "-b", "wt", str(tmp_path / "victim-wt"))
        gitdir = victim / ".git" / "worktrees" / "victim-wt"
        assert gitdir.is_dir(), "the linked worktree's gitdir is where git puts it"
        before = {
            "main": _git(victim, "rev-parse", "main"),
            "wt": _git(victim, "rev-parse", "wt"),
            "branches": _git(victim, "branch", "--format=%(refname:short)"),
            "wt HEAD": (gitdir / "HEAD").read_text(),
        }
        return victim, gitdir, before

    def test_the_builder_leaves_a_hooked_worktree_untouched(self, tmp_path, monkeypatch):
        """Plant the hook's environment, build the scratch, compare the victim."""
        victim, gitdir, before = self._victim(tmp_path, monkeypatch)
        monkeypatch.setenv("GIT_DIR", str(gitdir))
        monkeypatch.setenv("GIT_INDEX_FILE", str(gitdir / "index"))
        monkeypatch.delenv("GIT_WORK_TREE")

        scratch = tmp_path / "scratch"
        scratch.mkdir()
        _build_merge_in_waiting(monkeypatch, scratch, "Plan step nothing:NOTHING.")

        _isolate(monkeypatch, victim)
        bare = subprocess.run(
            ("git", "config", "--get", "core.bare"),
            cwd=victim, capture_output=True, text=True, check=False,
        ).stdout.strip()
        assert bare == "false", f"the victim was re-initialised: core.bare={bare!r}"
        assert (scratch / ".git").is_dir(), "the scratch never got a repository of its own"
        assert _git(victim, "rev-parse", "main") == before["main"]
        assert _git(victim, "rev-parse", "wt") == before["wt"]
        assert _git(victim, "branch", "--format=%(refname:short)") == before["branches"]
        assert (gitdir / "HEAD").read_text() == before["wt HEAD"]

    def test_the_builder_ignores_the_developers_global_config(self, tmp_path, monkeypatch):
        """A global hooksPath and gpgsign neither block the builder nor run against it.

        ``HOME`` is pointed at a config whose ``core.hooksPath`` names a hook
        that writes a marker and refuses the commit, and whose ``commit.gpgsign``
        would demand a key.  The builder must finish and the marker must never
        appear; the victim is built first so the environment is the realistic
        one rather than a bare shell.
        """
        self._victim(tmp_path, monkeypatch)
        hooks = tmp_path / "hooks"
        hooks.mkdir()
        marker = tmp_path / "the-developers-hook-ran"
        hook = hooks / "pre-commit"
        hook.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
        hook.chmod(0o755)
        home = tmp_path / "home"
        home.mkdir()
        (home / ".gitconfig").write_text(
            f"[core]\n\thooksPath = {hooks}\n[commit]\n\tgpgsign = true\n",
        )
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))

        scratch = tmp_path / "scratch"
        scratch.mkdir()
        _build_merge_in_waiting(monkeypatch, scratch, "Plan step nothing:NOTHING.")

        assert not marker.exists(), "the developer's global hook ran against the scratch"
        assert _git(scratch, "rev-parse", "--verify", "other"), "the builder did not finish"
