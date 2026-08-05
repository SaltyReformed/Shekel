"""Every finding in the recurrence plan has a LIVE owner (section 7 rule 1).

``docs/plans/implementation_plan_recurrence_redesign.md`` is the recurrence
redesign's only live planning document.  Its section 5 ledger carries every
defect the arc has measured, and its last column names the step that closes
each one.  Section 7 rule 1 fixes that column's vocabulary and requires every
value in it to be answerable:

* **a live (unticked) section 4 step ID** -- the normal case;
* **``operator``** -- a question only the developer can answer from outside the
  code;
* **``developer-decision``** -- a fork the developer has taken, dated.

**This document adopted the balance arc's rules rather than inventing a second
shape**, and it did so before it had the rot those rules were written for.
``docs/audits/balance_architecture/README.md`` grew to 6,688 lines with 29 of
41 open findings unowned and four naming steps that had already SHIPPED, all
found by hand-passes weeks after the fact; its gate
(``test_balance_plan_ledger_integrity.py``) exists because prose does not
enforce itself.  The recurrence plan is 747 lines with 9 findings today.
Installing the same gate now is cheap; installing it at 6,000 lines is a
salvage operation.

**The machinery is shared, the spec and the controls are not.**  ``_plan_gate``
holds the parser -- five measured false-positive fixes' worth of it -- and this
file holds what is genuinely this document's: where its sections are, what its
caps are, and its own negative controls.  Every arm below is shown to bite on a
planted defect, because a guard whose control does not fire is not a guard.

**One arm is this document's alone.**  Section 7 rule 7 -- a shipped step
OPENS with its commit hash and stays under 6 lines -- is enabled here and
deliberately off for the balance README, whose spec records the measurement
that keeps it off.  The three shared caps differ in their numbers only, and
each number is measured: see :data:`_MAX_LINES`, :data:`_MAX_ARC_STATE_LINES`
and :data:`_MAX_TICKED_ENTRY_LINES`.
"""

from __future__ import annotations

import re
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
#: **A forcing function, not a ceiling sized to fit the work**, and the
#: arithmetic is stated rather than implied.  The document stands at 747.
#: EIGHT steps remain unspecified (R2c, R3-R9); specifying each at the ~45
#: lines R2b's took would add ~293 and land at ~1,021 -- over the cap.  Rule 7
#: is what closes that gap: a step that ships surrenders its specification, and
#: R1 + R2a went from 45 lines to 11 when rule 7 was applied to them.  At ~35
#: lines returned per ship against ~45 spent per specification the file
#: breathes rather than grows, so the cap binds only when the archive move is
#: genuinely overdue.
#:
#: The balance README's cap is 1,200 against 1,082 lines and a 93-row ledger;
#: this document is smaller and its ledger is 7 rows, so copying that number
#: would have been a cap that could never fire -- which is not a gate.
#: **Raising it is not the answer when it binds**: the floor moves DOWN as
#: steps ship, because a shipped step's specification becomes one line of
#: as-built record.  If it is not moving down, the archive move is overdue.
_MAX_LINES = 900

#: The "Where this stands" cap.  The signpost was written at 16 lines; 20 is
#: that plus room, and it is deliberately too small to hold a narrative -- a
#: paragraph of session detail does not fit, which is the enforcement.
#:
#: Smaller than the balance README's 30 on purpose.  That section earned its
#: extra ten lines by having more in flight (a held branch, a parked branch, an
#: interleaving arc); this one has a single linear step sequence, and a cap
#: that never binds teaches nothing.
_MAX_ARC_STATE_LINES = 20

#: A ticked step's entry cap (section 7 rule 7).  Measured: the three
#: convention-following ticked entries in the balance README stand at 2, 2 and
#: 4 lines, and this document's condensed R1 and R2a at 4 and 5.  6 is that
#: plus one line of room -- deliberately too small to hold a narrative, which
#: is the enforcement.
_MAX_TICKED_ENTRY_LINES = 6

#: The recurrence redesign's document, anchored on this file rather than on the
#: working directory so the gate grades the same document however it is
#: invoked.
SPEC = PlanSpec(
    path=(
        Path(__file__).resolve().parents[2]
        / "docs" / "plans" / "implementation_plan_recurrence_redesign.md"
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


class TestTheRecurrencePlanLedgerHasNoUnownedRows:
    """The gate rule 1 calls for: this document's owners are all answerable."""

    def test_the_parser_finds_the_document_it_is_grading(self):
        """Premise: the steps and the ledger are actually being read.

        Asserted first and separately because every check below passes
        vacuously against an empty parse -- a regex that matched nothing would
        report a perfect ledger.  The floors sit under the live counts (14
        checkboxes and 7 ledger ROWS when this was written; the table also
        carries a header and a delimiter line, so a naive count of lines
        starting with ``|`` gives 9 and is not what is floored here) so
        ordinary growth does not touch them, while a parser that silently
        stops working does.

        **The ledger floor is 5 against 7 rows, and that is deliberate**: this
        ledger is small enough that a generous floor would be reached by a
        parser reading two rows.  Three of the seven are carried findings that
        leave when their steps ship, so a floor of 7 would fail on ordinary
        closure -- which is how a gate gets uninstalled rather than fixed.
        """
        text = SPEC.read()
        steps = parse_steps(text, SPEC)
        rows = parse_ledger(text, SPEC)
        assert len(steps) >= 10, (
            f"parsed only {len(steps)} section 4 checkboxes: {sorted(steps)}"
        )
        assert any(not ticked for ticked in steps.values()), (
            "parsed no LIVE step at all -- every owner would fail"
        )
        assert any(ticked for ticked in steps.values()), (
            "parsed no SHIPPED step at all -- the stale-owner arm could never "
            "fire, which is the arm this gate exists for"
        )
        assert len(rows) >= 5, f"parsed only {len(rows)} ledger rows"

    def test_every_finding_has_a_live_owner(self):
        """No row names a shipped step, an unknown id, or a retired word."""
        violations = owner_violations(SPEC.read(), SPEC)
        assert violations == [], (
            "docs/plans/implementation_plan_recurrence_redesign.md section 5 "
            "violates section 7 rule 1:\n  " + "\n  ".join(violations)
        )

    def test_the_ledger_states_its_own_size_correctly(self):
        """The "stands at N rows" sentence matches the table it describes.

        The balance ledger's read 38 against a 40-row table -- drift left by a
        step that updated the rows and not the prose about them.  This
        document's ledger is small enough that the same drift is easy to miss
        by eye, which is the argument for making it a predicate rather than a
        habit.
        """
        violation = stated_count_violation(SPEC.read(), SPEC)
        assert violation is None, violation

    def test_the_document_is_within_its_line_cap(self):
        """Section 7 rule 4's cap, as a predicate rather than a target.

        The remedy the message names is rule 5's archive move; see
        ``_plan_gate.line_count_violation`` for why it names one at all -- a
        cap invites trimming whatever is nearest, and what is nearest when this
        fires is the specification of work that has not happened yet.
        """
        violation = line_count_violation(SPEC.read(), SPEC)
        assert violation is None, violation

    def test_every_shipped_step_is_a_pointer_to_its_commit(self):
        """Section 7 rule 7: a ticked step cites its commit and stays short.

        The developer's rule (2026-08-05): the narrative for completed work is
        concise and carries the commit SHA, "so a future reviewer can read the
        actual code and not prose that is regularly wrong about the code".
        This is the arm that makes it a predicate rather than a habit -- and it
        is what keeps rule 4's line cap reachable, because a shipped step that
        stays a narrative never gives its lines back.
        """
        violations = ticked_entry_violations(SPEC.read(), SPEC)
        assert violations == [], (
            "docs/plans/implementation_plan_recurrence_redesign.md section 4 "
            "violates section 7 rule 7:\n  " + "\n  ".join(violations)
        )

    def test_the_orientation_section_is_still_an_orientation(self):
        """The section the next session reads first has not become a log.

        This is the arm most likely to fire in ordinary use, and that is the
        point: it fires on the commit that would have started the next running
        narrative, when moving the paragraph to its real home is still one
        edit.
        """
        violation = arc_state_violation(SPEC.read(), SPEC)
        assert violation is None, violation

    def test_the_steps_section_is_bounded_by_the_next_section(self):
        """Premise: ``## 4a.`` does not swallow section 4's checkbox list.

        This document numbers a section ``4a`` immediately after ``4``, which
        the balance README has no equivalent of.  The parser bounds a section
        at the next ``## `` line, so ``## 4a.`` correctly ends section 4 -- but
        a heading prefix of ``"## 4"`` rather than ``"## 4. Step sequence"``
        would have matched BOTH headings and made the parser assert.  Pinned
        because the failure would otherwise look like a broken document rather
        than a mis-specified gate.
        """
        text = SPEC.read()
        body = section(text, SPEC.steps_heading, label=str(SPEC.path))
        assert "## 4a." not in body, (
            "section 4's body absorbed the 4a heading -- the steps_heading "
            "prefix is matching more than one section"
        )
        assert "R-F3" in body, (
            "section 4's body stops before its last step -- the carried-steps "
            "block is outside what the gate reads"
        )


class TestTheArmsSeeThisDocument:
    """The arms that can only be checked against the REAL document.

    The parser's negative controls live in ``test__plan_gate.py``, run once
    against a synthetic document rather than duplicated per plan -- the two
    gates previously carried 21 same-named controls between them.  What remains
    here is what a synthetic document cannot prove: that this file's own
    spelling, sections and caps are the ones the spec points at.
    """


    def test_the_count_arm_reads_the_LIVE_documents_spelling(self):
        """The arm matches the real file's sentence, not just the synthetic one.

        A pattern that is never exercised against the artifact it grades can be
        blind to it and still pass every synthetic control.  This asserts the
        live document's count sentence is FOUND -- if section 5 stops stating a
        count the arm becomes vacuous, and this is what says so.
        """
        body = section(SPEC.read(), SPEC.ledger_heading, label=str(SPEC.path))
        assert STATED_COUNT_RX.search(body), (
            "section 5 no longer states a row count in the spelling this arm "
            "matches -- the drift check is now vacuous. Restore the sentence "
            "or delete the arm; do not leave it passing on nothing."
        )

    def test_the_line_cap_can_actually_be_reached(self):
        """The cap is a gate, not decoration: the document is under it, near it.

        A cap far above any plausible size never fires and teaches nothing --
        the failure mode the balance README's ~500-line prose target had for
        three months while the file grew to 6,688.  This asserts the live
        document is genuinely bounded by :data:`_MAX_LINES` rather than
        nowhere near it, so a future edit that raises the cap to silence a
        failure has to defeat this too.
        """
        lines = len(SPEC.read().splitlines())
        assert lines <= _MAX_LINES, f"{lines} lines against a {_MAX_LINES} cap"
        assert lines >= _MAX_LINES // 2, (
            f"the document is {lines} lines against a {_MAX_LINES}-line cap -- "
            "the cap is more than twice the document and can never fire. "
            "Lower it to the measured size plus working room."
        )

