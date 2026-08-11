"""The gate over rule 16: a registry's content is stated ONCE.

Rules 1-15 each grade a claim where it lives, so none of them can see a SECOND
copy somewhere else -- and the copy is what rots, because nothing reconciles it.
Every arm here was written against a measured defect in a live document
(``_duplication`` names all three), and every arm has a control that plants the
defect back and is SHOWN to fire.

**The clean-corpus assertions and the controls are both load-bearing.**  A clean
run alone proves only that the arm found nothing, which is also what a broken
parser returns; the ``..._has_a_subject_to_grade`` tests assert the arm is
looking at real documents with real step ids in them, so "zero violations"
means zero rather than "read nothing".
"""
from __future__ import annotations

import re

import pytest

import _duplication as duplication
import _registry as registry


class TestTheOrderIsStatedOnlyInStepsMd:
    """conventions.md rule 16, first arm."""

    def test_no_live_document_restates_the_order(self):
        """No live document restates the order."""
        assert not duplication.order_restatement_violations()

    def test_the_arm_has_a_subject_to_grade(self):
        """A clean run must mean "found none", not "read nothing".

        **Deliberately NOT written with the module's own scanner.**  Asking
        ``_step_positions`` whether there is anything to scan is the producer
        acting as its own oracle -- if the tokenizer silently stopped matching,
        both it and this check would report zero and agree.  A plain search for
        the ids ``steps.md`` indexes is an independent instrument, which is
        ``verification.md`` clause 2 applied to the gate itself.
        """
        idents = {row.ident for row in registry.step_rows()}
        assert len(idents) >= 70, "steps.md indexes almost no steps"
        named = [
            name
            for name, path in duplication.live_docs().items()
            if name != "steps"
            and path.exists()
            and any(
                re.search(rf"(?<![A-Za-z0-9-]){re.escape(i)}(?![A-Za-z0-9-])", text)
                for i in idents
                for text in [path.read_text(encoding="utf-8")]
            )
        ]
        assert named, "no live document names a step id -- the arm grades nothing"

    def test_the_control_fires_on_a_restated_sequence(self, stage_arc):
        """The exact shape the balance signpost carried until 2026-08-11."""
        stage_arc(
            "balance",
            "## Where the arc stands",
            "## Where the arc stands\n\nOrder from here: **X-ar**, then **X-ap**, "
            "then **X-f3** -> X-f4.\n",
        )
        problems = duplication.order_restatement_violations()
        assert problems, "a four-step chain must be reported"
        assert "rule 16" in problems[0]

    def test_the_control_fires_on_the_shortest_chain(self, stage_arc):
        """Two steps and one connective is already a sequence.

        The recurrence plan's copy was exactly this long -- "NEXT = R7a-2 ...,
        then R7b" -- so an arm that demanded three members would have passed it.
        """
        stage_arc(
            "recurrence",
            "## Rulings",
            "## Rulings\n\nBuild R7b, then R7c.\n",
        )
        assert duplication.order_restatement_violations()

    def test_prose_naming_two_steps_without_a_connective_is_not_a_chain(
        self, stage_arc,
    ):
        """Argument prose must survive: an arm that cries wolf gets uninstalled.

        This is the live sentence the recurrence plan states about R5 and R6.
        It names two steps and a real dependency between them, and it is not an
        order restatement -- the ORDER that follows from it is steps.md's.
        """
        stage_arc(
            "recurrence",
            "## Rulings",
            "## Rulings\n\nR6 reads a column R5 creates, so it cannot ship "
            "first.\n",
        )
        assert not duplication.order_restatement_violations()

    def test_one_id_named_twice_is_not_a_chain(self, stage_arc):
        """``X-j -> X-j`` orders nothing, and the arm reported it on its first run."""
        stage_arc(
            "balance",
            "## Where the arc stands",
            "## Where the arc stands\n\nX-j takes the display half, and then "
            "X-j closes the cache half.\n",
        )
        assert not duplication.order_restatement_violations()

    def test_a_fenced_measurement_is_not_a_chain(self, stage_arc):
        """The recurrence plan lists step ids in a fence beside file counts."""
        stage_arc(
            "recurrence",
            "## Rulings",
            "## Rulings\n\n```text\nR5 -> R6 -> R9 : 3 files\n```\n",
        )
        assert not duplication.order_restatement_violations()


class TestARegistrySizeIsStatedOnlyInItsOwnRegistry:
    """conventions.md rule 16, second arm."""

    def test_no_live_document_states_a_foreign_count(self):
        """No live document states a foreign count."""
        assert not duplication.foreign_count_violations()

    def test_the_registries_still_state_their_own_counts(self):
        """The arm must not have been made vacuous by silencing rule 3's sentences."""
        assert registry.STATED_COUNT_RX.search(registry.LEDGER.read_text())
        assert registry.STEPS_COUNT_RX.search(registry.STEPS.read_text())

    @pytest.mark.parametrize(
        "planted",
        [
            "The blocks partition all 98 open findings.",
            "This arc holds 31 of the ledger's 166 rows.",
            "The corpus stands at 113 steps, 95 open.",
        ],
    )
    def test_the_control_fires_on_a_copied_count(self, stage_arc, planted):
        """Each shape is one the live corpus actually carried."""
        stage_arc("balance", "## Where the arc stands",
                  f"## Where the arc stands\n\n{planted}\n")
        problems = duplication.foreign_count_violations()
        assert problems, f"{planted!r} must be reported"
        assert "rule 16" in problems[0]

    def test_a_quoted_count_is_a_citation_and_not_a_claim(self, stage_arc):
        """conventions.md must be able to quote the wording it grades.

        Rule 3 cites the stale text it caught, and rule 16 cites the stale
        count.  A rule quoting a defect is not committing it -- without this,
        the rules file would be the only document the rules file fails.
        """
        stage_arc(
            "balance",
            "## Where the arc stands",
            '## Where the arc stands\n\nIt once read "98 open findings" and was '
            "wrong.\n",
        )
        assert not duplication.foreign_count_violations()

    def test_a_citation_wrapped_across_a_line_is_still_a_citation(self, stage_arc):
        """These documents are hard-wrapped, so a quote lands where fmt puts it.

        This shape is not hypothetical: ``rumdl fmt`` produced exactly it in
        ``conventions.md`` on 2026-08-11, the quotes stopped pairing on one
        line, and the arm reported the RULES file for a count it was citing.
        An exemption that depends on where a formatter chose to wrap is not an
        exemption.
        """
        stage_arc(
            "balance",
            "## Where the arc stands",
            '## Where the arc stands\n\nIts own sentence read "The\nledger '
            'stands at 166 rows", which was right.\n',
        )
        assert not duplication.foreign_count_violations()


class TestALiveDocumentDoesNotDeclareItsOwnSectionsDead:
    """conventions.md rule 16, third arm."""

    def test_no_live_document_carries_a_self_declared_dead_section(self):
        """No live document carries a self-declared dead section."""
        assert not duplication.retired_section_violations()

    def test_the_control_fires_on_the_shape_the_card_plan_carried(self, stage_arc):
        """The credit-card plan's discharged sequencing, as it stood."""
        stage_arc(
            "credit_card",
            "## Context",
            "## Context\n\nThe sequencing in this section is DISCHARGED and "
            "must never be re-read as a live gate.\n",
        )
        problems = duplication.retired_section_violations()
        assert problems, "a self-declared dead section must be reported"
        assert "rule 16" in problems[0] and "rule 15" in problems[0]

    def test_a_superseded_ruling_is_not_a_dead_section(self, stage_arc):
        """The rulings tables record superseded rulings by design.

        Four live rulings read this way.  An arm keying on the bare word would
        report every one of them, which is a gate nobody keeps.
        """
        stage_arc(
            "recurrence",
            "## Rulings",
            "## Rulings\n\nR-R3's subtype is SUPERSEDED by R-R13.\n",
        )
        assert not duplication.retired_section_violations()

    def test_pointing_at_a_section_is_not_declaring_it_dead(self, stage_arc):
        """"see section 0" is the commonest thing said about a section.

        The arm matched the bare word ``section`` on its first run and reported
        two clean documents for exactly this.
        """
        stage_arc(
            "recurrence",
            "## Rulings",
            "## Rulings\n\nThat claim is SUPERSEDED; see section 0 for the "
            "measurement.\n",
        )
        assert not duplication.retired_section_violations()


class TestTheArcDocumentsPointAtTheVerificationStandard:
    """The standard was extracted; a pointer is what must be left behind."""

    @pytest.mark.parametrize("arc", sorted(registry.ARC_DOCS))
    def test_every_arc_document_names_it(self, arc):
        """Two arcs had no verification standard at all before the extraction."""
        text = registry.ARC_DOCS[arc].read_text(encoding="utf-8")
        assert "verification.md" in text, (
            f"{arc} does not point at docs/plans/verification.md -- the "
            "recurrence and pay-calendar arcs were held to nothing written "
            "down until 2026-08-11"
        )

    def test_the_standard_exists_and_is_not_a_stub(self):
        """A pointer to an empty file is worse than no pointer."""
        path = duplication.live_docs()["verification"]
        assert path.exists(), "docs/plans/verification.md is missing"
        assert len(path.read_text(encoding="utf-8").splitlines()) >= 30
