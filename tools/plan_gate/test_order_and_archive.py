"""Controls for the EXECUTION ORDER arms and the archive banner.

Split out of ``test_registry_integrity.py`` when that module reached its
1000-line ceiling, on the same ruling that split ``_order.py`` and
``_archive.py`` out of ``_registry.py``: an over-ceiling module is split,
never shaved (findings N-152 / N-156).  The staging fixtures are imported
from the sibling rather than copied -- two spellings of "plant a defect in a
registry copy" would let the two suites disagree about what they are
grading, which is the denormalization these registries exist to remove.
"""
from __future__ import annotations

import pytest

import _archive as archive
import _order as order
import _registry as registry
from test_registry_integrity import _row, _with_cell


class TestTheOrderIsATotalOrderTheGraphAllows:
    """conventions.md rule 14, the arm the ordered document exists for."""

    def test_the_live_order_is_clean(self):
        """The live order is clean."""
        assert not order.rank_violations()

    def test_the_live_corpus_actually_has_an_order_to_grade(self):
        """A rule with no subject is untested by the clean case.

        Three premises, because each arm below is blind without one: there ARE
        ranked rows, there ARE containers held out of the order, and at least
        one ranked row is blocked by unshipped work -- without the third, the
        graph-consistency arm grades nothing at all.
        """
        rows = registry.step_rows()
        steps = {row.key: row for row in rows}
        assert sum(1 for row in rows if row.rank is not None) >= 50
        assert any(row.is_container for row in rows), "no container to hold out"
        assert any(
            row.rank is not None
            and any(
                key in steps and not steps[key].shipped
                for key in row.blocked_keys()
            )
            for row in rows
        ), "no ranked row waits on unshipped work -- arm 4 grades nothing"

    def test_the_control_fires_when_a_rank_precedes_its_own_blocker(self, stage):
        """The defect this rule exists for: a stated order the graph forbids."""
        line = _row("steps", "| balance | X-f4 |")
        stage("steps", line, _with_cell(line, 4, "#1"))
        problems = order.rank_violations()
        assert any("balance:X-f4" in p and "forbids" in p for p in problems), problems

    def test_the_control_fires_on_a_hole_in_the_sequence(self, stage):
        """A gap makes "the first row that is not done" ambiguous."""
        line = _row("steps", "| balance | X-ap |")
        stage("steps", line, _with_cell(line, 4, "#999"))
        assert any("has no #2" in p for p in order.rank_violations())

    def test_the_control_fires_on_an_unparseable_order_cell(self, stage):
        """A row a reader cannot place in the sequence."""
        line = _row("steps", "| balance | X-am |")
        stage("steps", line, _with_cell(line, 4, "soon"))
        problems = order.rank_violations()
        assert any("balance:X-am" in p and "'soon'" in p for p in problems), problems

    def test_two_unrelated_steps_may_not_share_one_rank(self, stage):
        """A rank repeats only where two names are ONE commit."""
        line = _row("steps", "| balance | X-ad-b |")
        stage("steps", line, _with_cell(line, 4, "#26"))
        assert any("not one identity class" in p for p in order.rank_violations())

    def test_an_identity_class_sharing_one_rank_is_not_a_violation(self):
        """`C5a` and `R-F10` are one commit at one position, and that is legal.

        The live corpus carries the class this exemption exists for, so the
        clean case above already proves the arm does not fire on it -- but a
        reader deleting the exemption would not learn that from a passing
        suite, which is what this test is for.
        """
        rows = {row.key: row for row in registry.step_rows()}
        left, right = rows["pay_calendar:C5a"], rows["recurrence:R-F10"]
        assert left.rank is not None
        assert left.rank == right.rank
        assert right.key in left.alias_keys()


class TestTheStartsCellIsDerivedAndReconciled:
    """conventions.md rule 14: a stored derived value with its reconciler."""

    def test_the_live_starts_column_is_clean(self):
        """The live starts column is clean."""
        assert not order.starts_violations()

    def test_the_live_corpus_contains_both_states_to_grade(self):
        """Both a READY row and a WAITING row exist, or one arm is blind."""
        heads = [row.blocked.split(" / ")[0].strip() for row in registry.step_rows()
                 if not row.shipped]
        assert any(head == "NOW" for head in heads), "no ready row"
        assert any(head.startswith("after #") for head in heads), "no waiting row"
        assert any(head.startswith("ticks with #") for head in heads), "no container"

    def test_the_control_fires_on_a_stale_now(self, stage):
        """A stale NOW sends a reader at work they cannot start."""
        line = _row("steps", "| balance | X-f4 |")
        stage("steps", line, _with_cell(line, 6, "NOW / balance:X-f3"))
        problems = order.starts_violations()
        assert any("balance:X-f4" in p and "stale NOW" in p for p in problems), problems

    def test_the_control_fires_when_the_named_rank_is_not_the_latest(self, stage):
        """Naming an earlier blocker understates the wait."""
        line = _row("steps", "| credit_card | CC0a |")
        stage("steps", line, _with_cell(line, 6, "after #5 / balance:X-f4 / balance:X-am"))
        problems = order.starts_violations()
        assert any("credit_card:CC0a" in p and "#32" in p for p in problems), problems

    def test_the_control_fires_when_a_ready_row_claims_to_be_blocked(self, stage):
        """A stale `after` hides work the reader can pick up today."""
        line = _row("steps", "| balance | X-ar |")
        stage("steps", line, _with_cell(line, 6, "after #4 / balance:X-aq (shipped)"))
        problems = order.starts_violations()
        assert any("balance:X-ar" in p and "has SHIPPED" in p for p in problems), problems

    def test_the_control_fires_on_a_container_naming_the_wrong_leaf(self, stage):
        """A container ticks with its LAST leaf, not an earlier one."""
        line = _row("steps", "| balance | X-i |")
        stage("steps", line, _with_cell(line, 6, "ticks with #41"))
        problems = order.starts_violations()
        assert any("balance:X-i" in p and "#42" in p for p in problems), problems

    def test_the_control_fires_on_an_unparseable_head(self, stage):
        """Every other spelling of readiness used to read as legal."""
        line = _row("steps", "| balance | X-am |")
        stage("steps", line, _with_cell(line, 6, "whenever"))
        problems = order.starts_violations()
        assert any("balance:X-am" in p and "'whenever'" in p for p in problems), problems


class TestEveryStepSaysWhatItIsInOneSentence:
    """conventions.md rule 14: the 38-row truncation class."""

    def test_the_live_descriptions_are_clean(self):
        """The live descriptions are clean."""
        assert not order.description_violations()

    @pytest.mark.parametrize("truncated", [
        "the DECOMPOSED parent,",
        "`feat(cash): the ledger is sum-of-postings` -- **THE",
        "**RULED as the follow-on, not an",
        "one balanced",
    ])
    def test_the_control_fires_on_a_real_truncation(self, stage, truncated):
        """Each specimen is a VERBATIM cell from the index this rule replaced.

        Reconstructed rather than invented: a synthetic fragment would prove
        the pattern matches something, not that it matches the defect.
        """
        line = _row("steps", "| balance | X-f3 |")
        stage("steps", line, _with_cell(line, 3, truncated))
        problems = order.description_violations()
        assert any("balance:X-f3" in p and "TRUNCATED" in p for p in problems), problems

    def test_the_control_fires_on_an_empty_description(self, stage):
        """Every step says what it is."""
        line = _row("steps", "| balance | X-f3 |")
        stage("steps", line, _with_cell(line, 3, "--"))
        assert any("no description" in p for p in order.description_violations())

    def test_the_control_fires_over_the_cap(self, stage):
        """A specification that will not fit belongs in the arc document."""
        line = _row("steps", "| balance | X-f3 |")
        stage("steps", line, _with_cell(line, 3, "word " * 100 + "end."))
        problems = order.description_violations()
        assert any("against a" in p and "cap" in p for p in problems), problems

    def test_the_cap_is_a_forcing_function_and_not_sized_to_fit(self):
        """Rule 4: a cap with no headroom has already stopped forcing anything.

        The inverse matters too and is why the floor is here: a cap raised far
        above the longest live cell grades nothing, which is how a limit
        becomes decoration.
        """
        longest = max(len(row.title.strip()) for row in registry.step_rows())
        assert longest < order.DESCRIPTION_CAP, "the live table is over its cap"
        assert order.DESCRIPTION_CAP < longest * 2, (
            f"the cap ({order.DESCRIPTION_CAP}) is more than double the "
            f"longest live description ({longest}) and forces nothing"
        )


class TestAnArchivedDocumentSaysSoOnItsFirstLine:
    """conventions.md rule 15."""

    def test_every_archived_document_carries_the_banner(self):
        """Every archived document carries the banner."""
        assert not archive.archive_banner_violations()

    def test_the_archive_actually_holds_documents_to_grade(self):
        """A floor far under the live count, so a broken walk is visible.

        **The floor came DOWN from 100 to 40 on 2026-08-11, and the reason is
        the point**: 100 sat just under a 124-document archive, so it was
        really tracking the corpus size rather than proving the walk works.
        Deleting an archived document is now an EXPECTED operation -- 25 went
        that day, referenced by nothing -- and a floor set just under the
        current count turns every such deletion into a red suite. The floor's
        only job is catching a walk that has stopped finding files at all.
        """
        assert len(list(archive.archived_docs())) >= 40

    def test_the_walk_covers_every_archived_tree(self):
        """Three separate trees hold archived documents, and all three count.

        ``docs/plans/historical`` and ``docs/historical`` are the ones a
        directory walk rooted at the balance arc's archive would miss, and the
        document that started this rule was reachable from none of them.
        """
        parents = {str(p.relative_to(registry.REPO).parent)
                   for p in archive.archived_docs()}
        for tree in ("docs/audits/balance_architecture/archive",
                     "docs/historical",
                     "docs/plans/historical"):
            assert tree in parents, f"{tree} is not being walked"

    def test_the_control_fires_on_a_document_that_does_not_say_it_is_archived(
        self, tmp_path, monkeypatch,
    ):
        """The defect: an archived plan a session reads as the plan of record."""
        fake = tmp_path / "docs" / "plans" / "historical"
        fake.mkdir(parents=True)
        (fake / "implementation_plan_something.md").write_text(
            "# Implementation Plan: Something\n\nThis is the plan of record.\n",
        )
        monkeypatch.setattr(registry, "REPO", tmp_path)
        problems = archive.archive_banner_violations()
        assert len(problems) == 1, problems
        assert "implementation_plan_something.md" in problems[0]

    def test_a_live_document_outside_an_archive_is_not_graded(
        self, tmp_path, monkeypatch,
    ):
        """The negative control: the arm keys on the DIRECTORY, not the name."""
        live = tmp_path / "docs" / "plans"
        live.mkdir(parents=True)
        (live / "implementation_plan_something.md").write_text("# Live\n")
        monkeypatch.setattr(registry, "REPO", tmp_path)
        assert not archive.archive_banner_violations()
