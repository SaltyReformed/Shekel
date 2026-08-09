"""The shared machinery's negative controls, run against a synthetic document.

Every arm in ``_plan_gate`` is shown here to BITE on a planted defect.  A guard
whose control does not fire is not a guard, and the balance arc paid three
hand-passes in two days to learn that a ledger rule which is not a predicate is
not a rule.

**These controls live here, not in the per-document gate file, because they
test machinery that is SHARED.**  What stays with a document is what is
genuinely its own: its caps, its headings, and any control whose defect is
shaped by its own structure.  Those are in ``test_arc_documents.py``.

**The ledger arms are gone, and their controls went with them.**  This module
once parsed a findings ledger for every document; the findings now live in one
``docs/plans/ledger.md`` graded by ``_registry``, so ``parse_ledger``,
``parse_steps``, ``stated_count_violation`` and ``owner_violations`` were
deleted rather than left tested-with-no-caller.  What survived that deletion is
the owner GRAMMAR, which ``_registry`` imports rather than re-implements -- so
its controls survived too, retargeted at the primitives themselves.  A control
pointed at a function no live gate calls proves the parser reads what it wrote.

The synthetic :data:`SPEC` deliberately uses no real document's headings or
ids, so nothing here can pass by accident because it happened to match a live
file.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from _plan_gate import (
    CONVENTIONS_DOC,
    LEDGER_DOC,
    OWNER_RX,
    PlanSpec,
    arc_state_violation,
    line_count_violation,
    split_owners,
    step_entries,
    ticked_entry_violations,
)

_MAX_LINES = 40
_MAX_ARC_STATE_LINES = 6
_MAX_TICKED_ENTRY_LINES = 4

#: A document that exists nowhere.  ``path`` is never read: every function
#: under test takes the document TEXT, and only the live per-document gate
#: calls ``SPEC.read()``.  Pointing it at a real file would let a control pass
#: because that file happened to satisfy it.
SPEC = PlanSpec(
    path=Path("/nonexistent/synthetic-plan.md"),
    steps_heading="## S.",
    steps_label="Section S",
    line_cap=_MAX_LINES,
    arc_state_heading="## Orientation",
    arc_state_cap=_MAX_ARC_STATE_LINES,
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

## Z. Something after the steps
"""


class TestThePremise:
    """The synthetic document is valid, so each planted defect stands alone."""

    def test_the_clean_document_passes_every_arm(self):
        """No arm fires on the unmodified synthetic document."""
        assert arc_state_violation(DOC, SPEC) is None
        assert line_count_violation(DOC, SPEC) is None
        assert not ticked_entry_violations(DOC, SPEC)


class TestTheOwnerGrammar:
    """The primitive ``_registry`` imports rather than re-implements.

    ``_registry.owner_violations`` is the live predicate and its own controls
    are in ``test_registry_integrity.py``, staged against the real ledger.
    What is pinned HERE is the grammar itself: the two measured false positives
    that would make a correct owner cell report as broken, and a gate that
    cries wolf is uninstalled rather than fixed.
    """

    def test_a_slash_inside_an_annotation_is_not_a_second_owner(self):
        """``P-b (display / cache)`` is ONE annotated owner, not two broken ones.

        The split is taken at parenthesis depth zero.  A plain
        ``cell.split(" / ")`` tears this cell in half and reports two grammar
        violations on a cell that is fine.
        """
        assert split_owners("P-b (display / cache)") == ["P-b (display / cache)"]

    def test_a_genuine_two_owner_cell_still_splits(self):
        """Depth-zero splitting must not cost the split it exists to allow."""
        assert split_owners("X-j (display) / X-e (cache)") == [
            "X-j (display)", "X-e (cache)",
        ]

    def test_an_annotated_id_parses_to_the_id(self):
        """The id is parsed OUT of the cell, not scanned for anywhere in it.

        Scanning would try to validate the ``N-73`` inside this annotation,
        which names a FINDING and never a step.
        """
        match = OWNER_RX.match("X-e (widened 2026-07-27; see also N-73)")
        assert match is not None and match.group("owner") == "X-e"

    def test_prose_is_not_an_owner(self):
        """"Own commit", "folded in" and their siblings all mean nobody."""
        assert OWNER_RX.match("own commit") is None


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
        # The relocation advice must name places that EXIST.  These were
        # per-document label fields until the registries merged; a message
        # still sending a reader to "Section 6" would send them to a section
        # whose contents moved.
        assert LEDGER_DOC in violation and CONVENTIONS_DOC in violation
        assert SPEC.steps_label in violation

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

        This is the arm's sharpest edge: the live documents cite migration
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

        The live documents group steps under ``###`` headings, so a step
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

    def test_a_fence_cannot_truncate_the_steps_section(self):
        """A ``##`` line inside a code sample must not end the steps section.

        The live documents carry fenced diagrams inside their steps sections.
        Without fence-blanking, every step after the fence vanishes and the
        ticked-entry arm silently grades a document it stopped reading -- a
        pass that proves nothing, which is worse than a failure.
        """
        fenced = DOC.replace(
            "* [ ] **P-b1 THE LEAF** a decomposed leaf, live",
            "```text\n## sample heading inside a fence\n```\n\n"
            "* [ ] **P-b1 THE LEAF** a decomposed leaf, live",
        )
        assert [step for step, _, _ in step_entries(fenced, SPEC)] == [
            "P-a", "P-b", "P-b1",
        ]

    def test_a_checkbox_inside_a_fence_is_not_a_step(self):
        """A code sample is an illustration, not a record.

        Blanking rather than ignoring is what makes this true: an unblanked
        ``- [ ]`` inside a fenced example would be indexed as a real step, and
        rule 12 would then demand an index row for a step nobody wrote.
        """
        illustrated = DOC.replace(
            "## Z. Something after the steps",
            "```text\n- [ ] **P-zz** an example of the grammar\n```\n\n"
            "## Z. Something after the steps",
        )
        assert "P-zz" not in [step for step, _, _ in step_entries(illustrated, SPEC)]
