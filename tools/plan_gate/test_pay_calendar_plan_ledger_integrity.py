"""Every finding in the pay-calendar plan has a LIVE owner (section 7 rule 1).

``docs/plans/implementation_plan_pay_calendar.md`` is the pay-calendar arc's only
live planning document.  Its section 5 ledger carries every defect the arc has
measured, and its last column names the step that closes each one.  Section 7
rule 1 fixes that column's vocabulary and requires every value in it to be
answerable:

* **a live (unticked) section 4 step ID** -- the normal case;
* **``operator``** -- a question only the developer can answer from outside the
  code;
* **``developer-decision``** -- a fork the developer has taken, dated.

**This document adopted the rules on day one rather than after the rot.**  The
balance README grew to 6,688 lines with 29 of 41 open findings unowned before
its gate was written, and the recurrence plan adopted the same rules at 747
lines.  Installing the gate against a 378-line document that has shipped nothing
costs one file; installing it at 6,000 lines is a salvage operation.

**The machinery is shared, the spec and the controls are not.**  ``_plan_gate``
holds the parser -- five measured false-positive fixes' worth of it -- and this
file holds what is genuinely this document's: where its sections are, what its
caps are, and the controls only the REAL document can carry.

**One premise the other two gates have and this one cannot.**  Both of those
assert that their document contains at least one TICKED step, so the
"owner names a step that already shipped" arm is known to be reachable.  This
arc has shipped nothing, so that assertion would fail on a healthy document.
:meth:`TestTheArmsSeeThisDocument.test_the_stale_owner_arm_fires_on_this_document`
replaces it with something stronger: it ticks a step in a COPY of the live text
and requires the rows owning that step to be reported.  A guard that is not
shown to fire is the thing being fixed.
"""

from __future__ import annotations

from pathlib import Path

from _plan_gate import (
    PlanSpec,
    STATED_COUNT_RX,
    arc_state_violation,
    line_count_violation,
    owner_violations,
    parse_ledger,
    parse_steps,
    section,
    stated_count_violation,
    ticked_entry_violations,
)

#: Section 7 rule 4's hard cap on the whole document, in lines.
#:
#: **A forcing function, not a ceiling sized to fit the work.**  The document
#: stands at 488 with all six steps specified and nothing shipped, which is the
#: largest this arc's live specification ever needs to be: from here the file
#: only shrinks, because rule 7 turns each shipped step's ~14-line specification
#: into a 2-line pointer.  560 is that plus working room, and it is deliberately
#: reachable -- the balance README's 1,200 and the recurrence plan's 900 are
#: sized to their own documents for the same reason.
#:
#: **This number was 460 for about an hour, and raising it was NOT the failure
#: rule 4 forbids.**  Saying so here is the point, because "the cap bound so I
#: raised it" is exactly how a gate gets uninstalled.  460 was sized against the
#: FIRST DRAFT, which adversarial review then showed was under-measured: it
#: missed three findings (P12, P13, P14), a constraint the migration must drop,
#: a class of reader whose shape C4 changes, and a whole step (C6).  The
#: document did not grow by narrative -- it grew because it had been wrong, and
#: rule 5's archive remedy was unavailable by construction since nothing has
#: shipped to archive.  **The next time this binds the answer is different**: by
#: then C1 or C2 will have shipped, and surrendering a specification for a
#: pointer is what buys the room.  If the floor is not moving down as steps
#: ship, the archive move is overdue and raising the cap is the wrong edit.
_MAX_LINES = 560

#: The "Where this stands" cap.  The signpost was written at 16 lines; 20 is
#: that plus room, and it is deliberately too small to hold a narrative -- a
#: paragraph of session detail does not fit, which is the enforcement.  The same
#: number as the recurrence plan's, and for the same reason: a single linear
#: step sequence with no held or parked branch to describe.
_MAX_ARC_STATE_LINES = 20

#: A ticked step's entry cap (section 7 rule 7).  Measured across the other two
#: documents: their convention-following ticked entries stand at 2, 2, 4, 4 and
#: 5 lines.  6 is that plus room -- deliberately too small to hold a narrative.
_MAX_TICKED_ENTRY_LINES = 6

#: The pay-calendar arc's document, anchored on this file rather than on the
#: working directory so the gate grades the same document however it is invoked.
SPEC = PlanSpec(
    path=(
        Path(__file__).resolve().parents[2]
        / "docs" / "plans" / "implementation_plan_pay_calendar.md"
    ),
    steps_heading="## 4.",
    steps_label="section 4",
    ledger_heading="## 5.",
    ledger_label="Section 5",
    ledger_columns=5,
    owner_rule="section 7 rule 1",
    ship_rule="Rule 2",
    line_cap=_MAX_LINES,
    line_cap_rule="section 7 rule 4",
    archive_rule="rule 5",
    stated_count_rx=STATED_COUNT_RX,
    arc_state_heading="## Where this stands",
    arc_state_cap=_MAX_ARC_STATE_LINES,
    rules_label="section 7",
    ticked_entry_cap=_MAX_TICKED_ENTRY_LINES,
)


class TestThePayCalendarPlanLedgerHasNoUnownedRows:
    """The gate rule 1 calls for: this document's owners are all answerable."""

    def test_the_parser_finds_the_document_it_is_grading(self):
        """Premise: the steps and the ledger are actually being read.

        Asserted first and separately because every check below passes
        vacuously against an empty parse -- a regex that matched nothing would
        report a perfect ledger.

        **The step floor is 6 and that is the live count exactly.**  Unlike a
        ledger row, a checkbox never LEAVES this section: a step that ships is
        ticked in place (rule 7), and carried steps are only ever added.  So the
        tightest possible floor is also a stable one, and a looser floor would
        buy nothing.

        **The ledger floor is 3 against 14 rows, and that is deliberate.**  C4
        alone closes five rows (P1, P4, P5, P8, P9), so a floor anywhere near
        the live count would fail on ordinary closure -- which is how a gate
        gets uninstalled rather than fixed.  3 still catches the failure this
        floor exists for, a parser that silently returns nothing.
        """
        text = SPEC.read()
        steps = parse_steps(text, SPEC)
        rows = parse_ledger(text, SPEC)
        assert len(steps) >= 6, (
            f"parsed only {len(steps)} section 4 checkboxes: {sorted(steps)}"
        )
        assert any(not ticked for ticked in steps.values()), (
            "parsed no LIVE step at all -- every owner would fail"
        )
        assert len(rows) >= 3, f"parsed only {len(rows)} ledger rows"

    def test_every_finding_has_a_live_owner(self):
        """No row names a shipped step, an unknown id, or a retired word."""
        violations = owner_violations(SPEC.read(), SPEC)
        assert violations == [], (
            "docs/plans/implementation_plan_pay_calendar.md section 5 "
            "violates section 7 rule 1:\n  " + "\n  ".join(violations)
        )

    def test_the_ledger_states_its_own_size_correctly(self):
        """The "stands at N rows" sentence matches the table it describes.

        The balance ledger's read 38 against a 40-row table -- drift left by a
        step that updated the rows and not the prose about them.
        """
        violation = stated_count_violation(SPEC.read(), SPEC)
        assert violation is None, violation

    def test_the_document_is_within_its_line_cap(self):
        """Section 7 rule 4's cap, as a predicate rather than a target."""
        violation = line_count_violation(SPEC.read(), SPEC)
        assert violation is None, violation

    def test_every_shipped_step_is_a_pointer_to_its_commit(self):
        """Section 7 rule 7: a ticked step cites its commit and stays short.

        Vacuous today -- this arc has shipped nothing -- and installed now so
        that the FIRST tick is graded rather than the second.  The arm's ability
        to see this document is asserted separately below.
        """
        violations = ticked_entry_violations(SPEC.read(), SPEC)
        assert violations == [], (
            "docs/plans/implementation_plan_pay_calendar.md section 4 "
            "violates section 7 rule 7:\n  " + "\n  ".join(violations)
        )

    def test_the_orientation_section_is_still_an_orientation(self):
        """The section the next session reads first has not become a log."""
        violation = arc_state_violation(SPEC.read(), SPEC)
        assert violation is None, violation

    def test_the_steps_section_is_bounded_by_the_next_section(self):
        """Premise: section 4's body holds every step and stops at section 5.

        The parser bounds a section at the next ``## `` line.  This document has
        no ``## 4a.`` sibling today, but the recurrence plan grew one, so the
        failure mode is a live edit away -- and it would look like a broken
        document rather than a mis-specified gate.
        """
        body = section(SPEC.read(), SPEC.steps_heading, label=str(SPEC.path))
        assert "C5" in body, (
            "section 4's body stops before its last step -- the steps_heading "
            "prefix is bounding the section too early"
        )
        assert "## 5." not in body, (
            "section 4's body absorbed the ledger heading -- the steps_heading "
            "prefix is matching more than one section"
        )


class TestTheArmsSeeThisDocument:
    """The arms that can only be checked against the REAL document.

    The parser's negative controls live in ``test__plan_gate.py``, run once
    against a synthetic document rather than duplicated per plan.  What remains
    here is what a synthetic document cannot prove: that this file's own
    spelling, sections and caps are the ones the spec points at.
    """

    def test_the_stale_owner_arm_fires_on_this_document(self):
        """Tick a live step in a copy of the real text; its rows must be caught.

        This replaces the "the document contains a ticked step" premise the
        other two gates carry, which cannot hold for an arc that has shipped
        nothing.  It is the stronger control: rather than asserting the arm
        COULD fire, it drives the arm against this document's own owners and
        requires the report.

        C4 is the mutation target because five rows name it, so a parser that
        read the ledger but not the owner column, or the owner column of the
        wrong table, could not produce this result by accident.
        """
        text = SPEC.read()
        mutated = text.replace(
            "- [ ] **C4 -- drop the derived columns.**",
            "- [x] **C4 -- drop the derived columns.**",
        )
        assert mutated != text, (
            "the C4 checkbox is not spelled the way this control expects -- "
            "the mutation planted nothing and the assertion below would pass "
            "on an unchanged document"
        )
        violations = owner_violations(mutated, SPEC)
        assert violations, (
            "ticking C4 produced no violation: the live-owner arm is not "
            "reading this document's owner column"
        )
        assert all("SHIPPED" in violation for violation in violations), (
            f"expected only stale-owner violations, got: {violations}"
        )
        named = {violation.split(":")[0] for violation in violations}
        assert named == {"P1", "P4", "P5", "P8", "P9"}, (
            f"the rows owning C4 are {sorted(named)}; section 5 and this "
            "control disagree about who owns the column drop"
        )

    def test_the_count_arm_reads_the_LIVE_documents_spelling(self):
        """The arm matches the real file's sentence, not just the synthetic one.

        A pattern never exercised against the artifact it grades can be blind to
        it and still pass every synthetic control.  This asserts the live
        document's count sentence is FOUND -- if section 5 stops stating a count
        the arm becomes vacuous, and this is what says so.
        """
        body = section(SPEC.read(), SPEC.ledger_heading, label=str(SPEC.path))
        assert STATED_COUNT_RX.search(body), (
            "section 5 no longer states a row count in the spelling this arm "
            "matches -- the drift check is now vacuous. Restore the sentence "
            "or delete the arm; do not leave it passing on nothing."
        )

    def test_the_count_arm_fires_on_this_document(self):
        """A wrong count in a copy of the real text is reported.

        The spelling assertion above proves the sentence is found; this proves
        the comparison behind it is live against these 11 rows.
        """
        text = SPEC.read()
        mutated = text.replace(
            "**The ledger stands at 14 rows.**",
            "**The ledger stands at 13 rows.**",
        )
        assert mutated != text, (
            "the count sentence is not spelled the way this control expects"
        )
        assert stated_count_violation(mutated, SPEC) is not None, (
            "a stated count of 13 against 14 rows was not reported"
        )

    def test_the_line_cap_can_actually_be_reached(self):
        """The cap is a gate, not decoration: the document is under it, near it.

        A cap far above any plausible size never fires and teaches nothing --
        the failure mode the balance README's ~500-line prose target had for
        three months while the file grew to 6,688.
        """
        lines = len(SPEC.read().splitlines())
        assert lines <= _MAX_LINES, f"{lines} lines against a {_MAX_LINES} cap"
        assert lines >= _MAX_LINES // 2, (
            f"the document is {lines} lines against a {_MAX_LINES}-line cap -- "
            "the cap is more than twice the document and can never fire. "
            "Lower it to the measured size plus working room."
        )
