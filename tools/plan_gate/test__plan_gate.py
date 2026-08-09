"""The parser's own negative controls, run once against a synthetic document.

Every arm in ``_plan_gate`` is shown here to BITE on a planted defect.  A guard
whose control does not fire is not a guard, and the balance arc paid three
hand-passes in two days to learn that a ledger rule which is not a predicate is
not a rule.

**These controls live here, not in the per-document gate files, because they
test the PARSER and the parser is now shared.**  Before this file existed the
two gates carried 21 same-named controls between them, 220 identical lines,
and five ``R0801`` duplicate-code clusters -- and most of those controls
touched nothing document-specific: a line-cap boundary check feeds
``"x\\n" * N`` and never reads a heading.  A third document adopting the gate
would have copied them all again.

What stays with a document is what is genuinely ITS: the premise floors (is
the parser reading the real file at all), the live-spelling arm (does the
count pattern match the real sentence), the section-bounding arm (do this
document's headings carve out the right regions), and any control whose defect
is shaped by that document's own structure.

The synthetic :data:`SPEC` deliberately uses neither real document's headings
or ids, so nothing here can pass by accident because it happened to match a
live file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _plan_gate import (
    PlanSpec,
    STATED_COUNT_RX,
    arc_state_violation,
    line_count_violation,
    owner_violations,
    stated_count_violation,
    step_entries,
    ticked_entry_violations,
)

_MAX_LINES = 40
_MAX_ARC_STATE_LINES = 6
_MAX_TICKED_ENTRY_LINES = 4

#: A document that exists nowhere.  ``path`` is never read: every function
#: under test takes the document TEXT, and only the live per-document gates
#: call ``SPEC.read()``.  Pointing it at a real file would let a control pass
#: because that file happened to satisfy it.
SPEC = PlanSpec(
    path=Path("/nonexistent/synthetic-plan.md"),
    steps_heading="## S.",
    steps_label="Section S",
    ledger_heading="## L.",
    ledger_label="Section L",
    ledger_columns=5,
    owner_rule="Section R rule 1",
    ship_rule="Rule 2",
    line_cap=_MAX_LINES,
    line_cap_rule="Section R rule 4",
    archive_rule="rule 5",
    stated_count_rx=STATED_COUNT_RX,
    arc_state_heading="## Orientation",
    arc_state_cap=_MAX_ARC_STATE_LINES,
    rules_label="Section R",
    ticked_entry_cap=_MAX_TICKED_ENTRY_LINES,
)

#: A valid document, so every planted defect below is the ONLY defect.
DOC = """\
## Orientation

the signpost

## S. The steps

- [x] **P-a -- the shipped one.** `a1b2c3d` -- what it did, in one sentence.
- [ ] **P-b** the live one
* [ ] **P-b1 THE LEAF** a decomposed leaf, live

## L. The findings ledger

**The ledger stands at 5 rows.**

| id | finding (one line) | worst measured | status | owned by |
|---|---|---|---|---|
| N-1 | a thing | -- | OPEN | P-b |
| N-2 | a thing with a `Decimal \\| None` pipe | -- | OPEN | P-b1 (annotated) |
| N-3 | two halves | -- | OPEN | P-b (display) / P-b1 (cache) |
| FU-1 | an operator question | -- | OPEN | operator (unchanged) |
| N-4 | a taken fork | -- | OPEN | developer-decision (dated 2026-07-27) |

## Z. Something after the ledger
"""


class TestThePremise:
    """The synthetic document is valid, so each planted defect stands alone."""

    def test_the_clean_document_passes_every_arm(self):
        """No arm fires on the unmodified synthetic document."""
        assert not owner_violations(DOC, SPEC)
        assert stated_count_violation(DOC, SPEC) is None
        assert arc_state_violation(DOC, SPEC) is None
        assert line_count_violation(DOC, SPEC) is None
        assert not ticked_entry_violations(DOC, SPEC)


class TestTheOwnerArmFires:
    """Every way an owner can fail to answer "who closes this?"."""

    def test_an_owner_that_has_shipped_is_caught(self):
        """The class that went unnoticed for weeks, three times."""
        broken = DOC.replace("| OPEN | P-b |", "| OPEN | P-a |")
        violations = owner_violations(broken, SPEC)
        assert len(violations) == 1, violations
        assert "N-1" in violations[0] and "SHIPPED" in violations[0]

    def test_an_owner_naming_no_step_is_caught(self):
        """An id that is not a checkbox cannot answer "did its owner ship?"."""
        broken = DOC.replace("| OPEN | P-b |", "| OPEN | P-zz |")
        violations = owner_violations(broken, SPEC)
        assert len(violations) == 1, violations
        assert "'P-zz'" in violations[0] and "TICKABLE" in violations[0]

    def test_a_prose_owner_is_refused(self):
        """"Own commit", "folded in" and their siblings all mean nobody."""
        broken = DOC.replace("| OPEN | P-b |", "| OPEN | own commit |")
        violations = owner_violations(broken, SPEC)
        assert len(violations) == 1, violations
        assert "not an owner" in violations[0]

    def test_an_empty_owner_cell_is_caught(self):
        """A row with no owner is unfinished work, not a recorded finding."""
        broken = DOC.replace("| OPEN | P-b |", "| OPEN |  |")
        violations = owner_violations(broken, SPEC)
        assert len(violations) == 1, violations
        assert "no owner at all" in violations[0]

    def test_one_bad_half_of_a_two_owner_cell_is_caught(self):
        """Both halves of ``A / B`` must be live, not just the first."""
        broken = DOC.replace(
            "P-b (display) / P-b1 (cache)", "P-b (display) / P-a (cache)",
        )
        violations = owner_violations(broken, SPEC)
        assert len(violations) == 1, violations
        assert "N-3" in violations[0] and "SHIPPED" in violations[0]

    def test_a_bare_vocabulary_word_is_refused(self):
        """``operator`` states its question and ``developer-decision`` is dated."""
        bare = DOC.replace("operator (unchanged)", "operator")
        violations = owner_violations(bare, SPEC)
        assert len(violations) == 1, violations
        assert "carries no annotation" in violations[0]

        undated = DOC.replace(
            "developer-decision (dated 2026-07-27)",
            "developer-decision (the fork)",
        )
        violations = owner_violations(undated, SPEC)
        assert len(violations) == 1, violations
        assert "is not DATED" in violations[0]

    def test_a_slash_inside_an_annotation_is_not_a_second_owner(self):
        """``P-b (display / cache)`` is ONE annotated owner, not two broken ones.

        The split is taken at parenthesis depth zero.  A plain
        ``cell.split(" / ")`` reports two grammar violations on a cell that is
        fine -- a gate that cries wolf gets uninstalled, not fixed.
        """
        annotated = DOC.replace("| OPEN | P-b |", "| OPEN | P-b (display / cache) |")
        assert not owner_violations(annotated, SPEC)


class TestTheSilentParseHolesAreClosed:
    """Three ways the gate could pass while seeing nothing.

    None is caught by a premise floor: each keeps the parsed counts plausible,
    which is exactly what makes them dangerous.
    """

    def test_an_unescaped_pipe_is_reported_as_a_split_row(self):
        """A broken row fails as a broken ROW, not as a mystery owner.

        The distinction is the whole reason the parser splits on unescaped
        pipes: a real ledger carries ``Decimal \\| None`` inside a cell, and a
        parser blind to the escape would report that correct row as having the
        wrong owner.
        """
        broken = DOC.replace(r"`Decimal \| None`", "`Decimal | None`")
        with pytest.raises(AssertionError) as caught:
            owner_violations(broken, SPEC)
        assert "6 cells" in str(caught.value) and "N-2" in str(caught.value)

    def test_a_row_with_an_empty_id_cell_is_still_graded(self):
        """An empty id cell must not be mistaken for the delimiter row.

        ``set("") <= {"-", ":"}`` is ``True``, so a bare subset test drops the
        row and never reads its owner.
        """
        broken = DOC.replace("| N-1 | a thing |", "|  | a thing |")
        broken = broken.replace("| OPEN | P-b |", "| OPEN | P-a |")
        violations = owner_violations(broken, SPEC)
        assert len(violations) == 1, violations
        assert "SHIPPED" in violations[0], violations

    def test_a_duplicate_checkbox_id_is_refused(self):
        """A step re-listed in the steps section would silently un-tick itself.

        The last occurrence wins, so re-listing a SHIPPED step as unticked
        blinds the arm this gate exists for.  It must fail as a duplicate, not
        pass as a live owner.
        """
        broken = DOC.replace(
            "* [ ] **P-b1 THE LEAF** a decomposed leaf, live",
            "* [ ] **P-b1 THE LEAF** a decomposed leaf, live\n"
            "- [ ] **P-a** re-listed in a later summary",
        )
        with pytest.raises(AssertionError) as caught:
            owner_violations(broken, SPEC)
        assert "'P-a'" in str(caught.value)
        assert "more than one checkbox" in str(caught.value)

    def test_a_fence_cannot_truncate_the_steps_section(self):
        """A ``##`` line inside a code sample must not end the steps section.

        Both live documents carry fenced diagrams inside their steps sections.
        Without fence-blanking every step after the fence vanishes, and the
        rows owning them fail accusing the LEDGER of naming a non-checkbox -- a
        true failure with a false diagnosis, which is worse than no gate.
        """
        broken = DOC.replace(
            "* [ ] **P-b1 THE LEAF** a decomposed leaf, live",
            "* [ ] **P-b1 THE LEAF** a decomposed leaf, live\n\n"
            "```text\n## sample heading inside a fence\n```",
        )
        assert not owner_violations(broken, SPEC)


class TestTheCountArmFires:
    """The ledger's stated size matches the table it describes."""

    def test_a_stated_count_that_is_wrong_is_caught(self):
        """Present-and-correct passes, present-and-wrong fires, absent passes.

        **Both punctuations are exercised, and that is not decoration.** A live
        document closes the bold AFTER the full stop (``**... 41 rows.**``);
        the first draft of this arm required ``rows**``, matched that file
        nowhere, and therefore reported a planted 38-against-41 as clean.
        """
        for sentence in ("5 rows", "5 rows."):
            correct = DOC.replace(
                "**The ledger stands at 5 rows.**",
                f"**The ledger stands at {sentence}**",
            )
            assert stated_count_violation(correct, SPEC) is None, sentence

            wrong = correct.replace(f"stands at {sentence}", "stands at 38 rows")
            violation = stated_count_violation(wrong, SPEC)
            assert violation is not None, sentence
            assert "38" in violation and "5" in violation

        silent = DOC.replace("**The ledger stands at 5 rows.**\n\n", "")
        assert stated_count_violation(silent, SPEC) is None

    def test_a_row_added_without_updating_the_sentence_is_caught(self):
        """The live drift: rows change, the prose about them does not."""
        grown = DOC.replace(
            "| N-4 | a taken fork | -- | OPEN | developer-decision (dated 2026-07-27) |",
            "| N-4 | a taken fork | -- | OPEN | developer-decision (dated 2026-07-27) |\n"
            "| N-5 | a new one | -- | OPEN | P-b |",
        )
        violation = stated_count_violation(grown, SPEC)
        assert violation is not None
        assert "5 rows and the table carries 6" in violation


class TestTheCapArmsFire:
    """The whole-document cap and the orientation-section cap."""

    def test_a_document_over_the_line_cap_is_caught(self):
        """The cap bites, and only past the boundary.

        Three cases, because an off-by-one is the difference between a gate
        that fires on the commit that breaks the rule and one that fires on the
        commit after; and the message states both numbers so the reader knows
        how much has to move.
        """
        assert line_count_violation("x\n" * _MAX_LINES, SPEC) is None
        assert line_count_violation("x\n" * (_MAX_LINES - 1), SPEC) is None

        violation = line_count_violation("x\n" * (_MAX_LINES + 1), SPEC)
        assert violation is not None
        assert str(_MAX_LINES + 1) in violation and str(_MAX_LINES) in violation
        assert "Archive" in violation

    def test_an_orientation_section_that_became_a_log_is_caught(self):
        """The append-only failure mode, planted as one more session note."""
        at_cap = DOC.replace(
            "the signpost", "\n".join(["a line"] * (_MAX_ARC_STATE_LINES - 3)),
        )
        assert arc_state_violation(at_cap, SPEC) is None

        over = DOC.replace(
            "the signpost", "\n".join(["a line"] * (_MAX_ARC_STATE_LINES + 1)),
        )
        violation = arc_state_violation(over, SPEC)
        assert violation is not None
        assert "REPLACED" in violation
        assert "Section L" in violation and "Section R" in violation

    def test_a_missing_orientation_section_is_not_a_silent_pass(self):
        """Deleting the section must fail loudly, not read as "within cap"."""
        gone = DOC.replace("## Orientation\n\nthe signpost\n\n", "")
        with pytest.raises(AssertionError) as caught:
            arc_state_violation(gone, SPEC)
        assert SPEC.arc_state_heading in str(caught.value)


class TestTheTickedEntryArmFires:
    """A shipped step is a POINTER: a sentence and a commit hash.

    The rule the developer stated (2026-08-05): *"The narrative for completed
    work should be concise and include the commit SHA for the code commits so a
    future reviewer can read the actual code and not prose that is regularly
    wrong about the code."*  The balance arc had already paid for it -- its own
    rule 5 records carrying an invented provenance line, a drifted count and a
    citation to a deleted producer into records of shipped work.
    """

    def test_a_ticked_step_with_no_commit_hash_is_caught(self):
        """Ticking a box without citing a commit leaves only prose."""
        broken = DOC.replace(
            "- [x] **P-a -- the shipped one.** `a1b2c3d` -- what it did, in one sentence.",
            "- [x] **P-a -- the shipped one.** It did a great deal, described here.",
        )
        violations = ticked_entry_violations(broken, SPEC)
        assert len(violations) == 1, violations
        assert "P-a" in violations[0] and "OPEN with its commit hash" in violations[0]

    def test_a_hex_identifier_that_is_not_the_commit_cannot_stand_in(self):
        """An Alembic revision id is 12 hex characters and is NOT a commit.

        This is the arm's sharpest edge: both documents cite migration
        revisions, which satisfy a naive "contains a hash" test.  Requiring the
        hash to be the FIRST backticked token is what separates them.
        """
        broken = DOC.replace(
            "- [x] **P-a -- the shipped one.** `a1b2c3d` -- what it did, in one sentence.",
            "- [x] **P-a -- the shipped one.** Migration `e7a4d95c2b18` landed.",
        )
        violations = ticked_entry_violations(broken, SPEC)
        assert len(violations) == 1, violations
        assert "OPEN with its commit hash" in violations[0]

    def test_a_ticked_step_that_grew_into_a_narrative_is_caught(self):
        """The entry cap bites, and only past the boundary."""
        at_cap = DOC.replace(
            "- [x] **P-a -- the shipped one.** `a1b2c3d` -- what it did, in one sentence.",
            "- [x] **P-a -- the shipped one.** `a1b2c3d` -- what it did.\n"
            + "still describing it\n" * (_MAX_TICKED_ENTRY_LINES - 1),
        )
        assert not ticked_entry_violations(at_cap, SPEC)

        over = DOC.replace(
            "- [x] **P-a -- the shipped one.** `a1b2c3d` -- what it did, in one sentence.",
            "- [x] **P-a -- the shipped one.** `a1b2c3d` -- what it did.\n"
            + "still describing it\n" * _MAX_TICKED_ENTRY_LINES,
        )
        violations = ticked_entry_violations(over, SPEC)
        assert len(violations) == 1, violations
        assert "POINTER" in violations[0] and str(_MAX_TICKED_ENTRY_LINES) in violations[0]

    def test_an_unticked_step_may_be_as_long_as_it_needs(self):
        """A LIVE step is a specification and the arm must never touch it.

        The cap exists to shrink the record of what is DONE.  Applying it to
        work that has not happened is the precise mistake the line-cap
        message warns against, one level down.
        """
        long_live_step = DOC.replace(
            "- [ ] **P-b** the live one",
            "- [ ] **P-b** the live one\n" + "specification line\n" * 40,
        )
        assert not ticked_entry_violations(long_live_step, SPEC)

    def test_the_arm_is_off_when_no_cap_is_set(self):
        """``ticked_entry_cap=None`` means the document has not adopted the rule.

        Off deliberately, and only where the spec says why -- the balance
        README's spec records the measurement (``X-f1``: 18 lines, no hash)
        that keeps it off there rather than silently skipping.
        """
        import dataclasses  # pylint: disable=import-outside-toplevel

        off = dataclasses.replace(SPEC, ticked_entry_cap=None)
        broken = DOC.replace(
            "- [x] **P-a -- the shipped one.** `a1b2c3d` -- what it did, in one sentence.",
            "- [x] **P-a -- the shipped one.** no hash at all, and\n" + "long\n" * 30,
        )
        assert not ticked_entry_violations(broken, off)


class TestStepEntryBounding:
    """An entry ends at the next checkbox, a ``###`` heading, or the section."""

    def test_a_sub_heading_ends_an_entry(self):
        """Without this, the last step before a group heading absorbs the group.

        Both live documents group steps under ``###`` headings, so a step
        sitting immediately above one would otherwise be measured as far longer
        than it is -- and the ticked-entry cap would fire on prose that is not
        its own.
        """
        grouped = DOC.replace(
            "* [ ] **P-b1 THE LEAF** a decomposed leaf, live",
            "* [ ] **P-b1 THE LEAF** a decomposed leaf, live\n\n"
            "### A group of carried steps\n\n"
            "prose belonging to the group, not to P-b1\n",
        )
        entries = {step: body for step, _, body in step_entries(grouped, SPEC)}
        assert "prose belonging to the group" not in entries["P-b1"]

    def test_every_checkbox_becomes_exactly_one_entry(self):
        """The entry list and the checkbox list cannot drift apart."""
        entries = step_entries(DOC, SPEC)
        assert [step for step, _, _ in entries] == ["P-a", "P-b", "P-b1"]
        assert [ticked for _, ticked, _ in entries] == [True, False, False]
