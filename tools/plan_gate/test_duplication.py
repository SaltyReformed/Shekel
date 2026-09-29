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

#: A verbatim question's worth of words, past the 400 characters a WRAPPED
#: quote is held to, as ruling rows have quoted since ``balance:R-BAL136``.
_LONG = "which of the two remedies do you want, with the worked numbers " * 8


def _scan(text: str) -> tuple[list[str], list[str]]:
    """Return *text*'s blanked lines and the statements the arms grade in it.

    Args:
        text: A whole document.

    Returns:
        The blanked document split on newlines, and its units.
    """
    # Pylint: protected-access -- these controls grade the scanner below the
    # arms, because an arm reports only what it chained or counted: on
    # 2026-09-25 at ee76c0b9, 56 rows of rulings.md had lost a pipe to a quote
    # from an adjacent row and no arm fired, so an arm-level control alone
    # reads green.
    scanned = duplication._scannable(text)  # pylint: disable=protected-access
    # Pylint: protected-access -- the same, for the splitter the arms read.
    return scanned.split("\n"), duplication._units(scanned)  # pylint: disable=protected-access


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
        # The ids are CHOSEN from the live table, not named.  This fixture
        # pinned four and the corpus later deleted one, silently degrading it
        # to a three-id chain that still passed -- a control that cannot tell
        # four from three cannot notice its own subject rotting, and pinning
        # data the registry is expected to change is how it happened.
        idents = [
            row.ident for row in registry.step_rows() if row.arc == "balance"
        ][:4]
        assert len(idents) == 4, "the balance arc must hold four steps to chain"
        a, b, c, d = idents
        stage_arc(
            "balance",
            "## Where the arc stands",
            f"## Where the arc stands\n\nOrder from here: **{a}**, then **{b}**, "
            f"then **{c}** -> {d}.\n",
        )
        problems = duplication.order_restatement_violations()
        assert problems, "a four-step chain must be reported"
        assert "rule 16" in problems[0]
        assert f"{a} -> {b} -> {c} -> {d}" in problems[0], problems

    def test_the_control_fires_on_the_shortest_chain(self, stage_arc):
        """Two steps and one connective is already a sequence.

        The recurrence plan's copy was exactly this long -- "NEXT = R7a-2 ...,
        then R7b" -- so an arm that demanded three members would have passed it.

        The planted ids track the LIVE table, because the arm can only see a
        chain of keys it can resolve: ``R7b`` DECOMPOSED on 2026-08-12, and a
        control naming a retired id would have stopped firing silently -- which
        is the one failure a control exists to make impossible.

        **They are DERIVED from that table at plan step R8-a, because writing
        them out is the exact rot the paragraph above warns about.**  This case
        named ``R7b-1``; R8-a ARCHIVED the four R7b leaves out of the index
        under ``conventions.md`` rule 5, the id stopped resolving, and the
        control stopped firing -- caught by the gate rather than by anybody
        re-reading it.  Its sibling above was already derived for the same
        reason and did not move.
        """
        idents = [
            row.ident for row in registry.step_rows()
            if row.arc == "recurrence"
        ][:2]
        assert len(idents) == 2, "the recurrence arc must hold two steps"
        first, second = idents
        stage_arc(
            "recurrence",
            "## The rulings",
            f"## The rulings\n\nBuild {first}, then {second}.\n",
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
            "## The rulings",
            "## The rulings\n\nR6 reads a column R5 creates, so it cannot ship "
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
            "## The rulings",
            "## The rulings\n\n```text\nR5 -> R6 -> R9 : 3 files\n```\n",
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


class TestAQuotedSpanNeverLeavesItsTableRow:
    """Scanning note 3: a markdown row is one line, so its quotes pair on it.

    Blanking the whole document in one pass let a quote past the wrapped-span
    bound fail to pair, and its CLOSING mark then paired with the next quote
    along, in its own row or the next: the pipes between were blanked, a row
    that lost its leading pipe was read as prose and folded into a paragraph,
    and the quoted words were left for the arms to read.
    """

    def test_two_rows_quoting_past_the_bound_stay_two_rows(self, stage_arc):
        """Two ruling rows in the shape ``R-BAL136`` gave them, side by side.

        Each question quotes an ORDER, which is a citation of the developer's
        words and not a claim, so the order arm must not read it -- and each
        row must keep every pipe, so the arms see two rows of cells rather
        than one paragraph.
        """
        a, b = [row.ident for row in registry.step_rows() if row.arc == "balance"][:2]
        rows = [
            f'| balance | R-T1 | Question: "{a}, then {b}? {_LONG}" Answer: "{_LONG}" |',
            f'| balance | R-T2 | Question: "{b}, then {a}? {_LONG}" Answer: "{_LONG}" |',
        ]
        stage_arc("balance", "## Where the arc stands",
                  "## Where the arc stands\n\n" + "\n".join(rows) + "\n")
        text = registry.ARC_DOCS["balance"].read_text(encoding="utf-8")
        lines, units = _scan(text)
        for index, row in enumerate(rows, start=text.split("\n").index(rows[0])):
            blanked = lines[index]
            assert blanked.count("|") == row.count("|"), blanked
            assert "then" not in blanked, f"a quote in the row was not blanked: {blanked}"
        assert " R-T1 " in units and " R-T2 " in units, "a row was folded into a paragraph"
        assert not duplication.order_restatement_violations()

    def test_a_wrapped_quote_below_a_table_is_still_a_citation(self, stage_arc):
        """The documented wrap, with a long-quoting row directly above it.

        The row's closing mark is the nearest quote before the prose's opening
        one, which is the pairing a single pass took.  The wrap itself is the
        case ``test_a_citation_wrapped_across_a_line_is_still_a_citation``
        pins; this one grades that a table above cannot take it apart.
        """
        stage_arc(
            "balance",
            "## Where the arc stands",
            f'## Where the arc stands\n\n| balance | R-T1 | Question: "{_LONG}" |\n\n'
            'Its own sentence read "The\nledger stands at 166 rows", which was right.\n',
        )
        assert not duplication.foreign_count_violations()

    @pytest.mark.parametrize(
        ("stray", "below"),
        [
            ('| R-T1 | a 12" rule, whose quote mark pairs with nothing |',
             '| R-T2 | 98 open findings, beside a "quoted" word |'),
            ('| R-T1 | a 12" rule, whose quote mark pairs with nothing |',
             '\nThe blocks partition all 98 open findings, beside a "quoted" word.'),
            ('A 12" rule, whose quote mark pairs with nothing.',
             '| R-T2 | 98 open findings, beside a "quoted" word |'),
        ],
        ids=["row into the next row", "last row into the prose under it",
             "prose into the first row under it"],
    )
    def test_a_stray_quote_blanks_nothing_across_a_row_edge(self, stage_arc, stray, below):
        """A stray mark must not HIDE a real claim on the other side of a row edge.

        Each direction is its own way to be wrong: a pattern that only refuses
        to ENTER a row still lets a stray quote in a table's LAST row pair with
        the first quote of the prose below, and a blanker that kept a table's
        first row in one block with the prose above it lets a stray quote there
        pair into the row.
        """
        stage_arc("balance", "## Where the arc stands",
                  f"## Where the arc stands\n\n{stray}\n{below}\n")
        text = registry.ARC_DOCS["balance"].read_text(encoding="utf-8")
        lines, _ = _scan(text)
        assert lines[text.split("\n").index(stray)] == stray
        problems = duplication.foreign_count_violations()
        assert any("98 open findings" in p for p in problems), problems


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
        """The rulings registry records superseded rulings by design.

        Four rulings read this way -- ``R-R3``'s subtype superseded by
        ``R-R13`` is one, and since ``balance:X-ao-2a`` it is a row in
        ``rulings.md`` rather than a row in this document.  An arm keying on
        the bare word would report every one of them, which is a gate nobody
        keeps, and the specimen is planted in an arc document because that is
        where ``retired_section_violations`` reads.
        """
        stage_arc(
            "recurrence",
            "## The rulings",
            "## The rulings\n\nR-R3's subtype is SUPERSEDED by R-R13.\n",
        )
        assert not duplication.retired_section_violations()

    def test_pointing_at_a_section_is_not_declaring_it_dead(self, stage_arc):
        """"see section 0" is the commonest thing said about a section.

        The arm matched the bare word ``section`` on its first run and reported
        two clean documents for exactly this.
        """
        stage_arc(
            "recurrence",
            "## The rulings",
            "## The rulings\n\nThat claim is SUPERSEDED; see section 0 for the "
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
