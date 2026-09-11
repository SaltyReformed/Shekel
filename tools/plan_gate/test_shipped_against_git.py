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

import subprocess

import pytest

import _registry as registry
import _shipped


def _live(ident: str) -> registry.StepRow:
    """Return a live step row by bare ident, failing the control if it is gone."""
    for row in registry.step_rows():
        if row.ident == ident:
            return row
    pytest.fail(f"{ident} has left steps.md; re-anchor this control on a live row")
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
        dangling = subprocess.run(
            ("git",
             "-c", "user.name=plan gate control",
             "-c", "user.email=plan-gate@localhost",
             "commit-tree", "HEAD^{tree}", "-m", "not on any branch"),
            cwd=registry.REPO, capture_output=True, text=True, check=True,
        ).stdout.strip()
        row = next(r for r in registry.step_rows() if r.shipped)
        stage("steps", f"| {row.commit.strip()} |", f"| `{dangling[:8]}` |")
        problems = _shipped.shipped_commit_violations()
        assert any("not an ancestor of HEAD" in p for p in problems), problems


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
