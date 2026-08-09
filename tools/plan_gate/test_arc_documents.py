"""The gate over the four ARC documents -- what stays after the registries left.

An arc document now holds argument only: root cause, evidence, target model,
rulings, alternatives rejected, and its own step SPECIFICATIONS.  Its findings
are rows in ``docs/plans/ledger.md`` and its steps are indexed in
``docs/plans/steps.md``, both graded by ``test_registry_integrity.py``.

What is still per-document, and therefore still graded here:

* **rule 4** -- each document's line cap;
* **rule 6** -- the signpost is capped, so a paragraph cannot be appended to it
  without something else leaving;
* **rule 7** -- a SHIPPED step's specification is a POINTER that OPENS with its
  commit hash.

The specs reuse :class:`_plan_gate.PlanSpec` rather than growing a second
shape.  Its ``ledger_*`` and ``stated_count_rx`` fields are INERT here -- the
ledger they described has moved -- and only the four functions that never read
them are called: ``line_count_violation``, ``arc_state_violation``,
``ticked_entry_violations`` and ``step_entries``.  They are given each
document's real pointer-section heading so a message still cites something a
reader can find.
"""
from __future__ import annotations

import pytest

import _registry as registry
from _plan_gate import (
    STATED_COUNT_RX,
    PlanSpec,
    arc_state_violation,
    line_count_violation,
    step_entries,
    ticked_entry_violations,
)

#: Each cap is the document's live-content floor plus room to work, NOT a
#: ceiling sized to fit today's file.  They came DOWN when the registries left:
#: balance 1,200 -> 1,000 against 952 lines, recurrence 900 -> 850 against 806,
#: pay-calendar 560 -> 500 against 449.  Lowering them is the point -- the space
#: the duplication occupied must not silently become room to duplicate again.
#: conventions.md rule 4: raising a cap is never the answer when it binds.
CAPS = {
    "balance": 1000,
    "recurrence": 850,
    "pay_calendar": 500,
    "credit_card": 400,
}

#: The signpost's cap, per document.  The balance README's reached 1,019 lines
#: as an append-only log before it was capped at all.
SIGNPOST_CAPS = {"balance": 30, "recurrence": 20, "pay_calendar": 20}

#: A SHIPPED step's entry cap (rule 7).  ``None`` where a document has never
#: ticked a step, so no entry exists to grade.
TICKED_CAPS = {
    "balance": None,
    "recurrence": 6,
    "pay_calendar": 6,
    "credit_card": 6,
}

SPECS = {
    "balance": PlanSpec(
        path=registry.ARC_DOCS["balance"],
        steps_heading="## 5.", steps_label="Section 5",
        ledger_heading="## 6.", ledger_label="Section 6 (now ledger.md)",
        ledger_columns=7,
        owner_rule="conventions.md rule 1", ship_rule="conventions.md rule 2",
        line_cap=CAPS["balance"], line_cap_rule="conventions.md rule 4",
        archive_rule="conventions.md rule 5",
        stated_count_rx=STATED_COUNT_RX,
        arc_state_heading="## Where the arc stands",
        arc_state_cap=SIGNPOST_CAPS["balance"],
        rules_label="conventions.md",
        ticked_entry_cap=TICKED_CAPS["balance"],
    ),
    "recurrence": PlanSpec(
        path=registry.ARC_DOCS["recurrence"],
        steps_heading="## 4.", steps_label="section 4",
        ledger_heading="## 5.", ledger_label="section 5 (now ledger.md)",
        ledger_columns=7,
        owner_rule="conventions.md rule 1", ship_rule="conventions.md rule 2",
        line_cap=CAPS["recurrence"], line_cap_rule="conventions.md rule 4",
        archive_rule="conventions.md rule 5",
        stated_count_rx=STATED_COUNT_RX,
        arc_state_heading="## Where this stands",
        arc_state_cap=SIGNPOST_CAPS["recurrence"],
        rules_label="conventions.md",
        ticked_entry_cap=TICKED_CAPS["recurrence"],
    ),
    "pay_calendar": PlanSpec(
        path=registry.ARC_DOCS["pay_calendar"],
        steps_heading="## 4.", steps_label="section 4",
        ledger_heading="## 5.", ledger_label="section 5 (now ledger.md)",
        ledger_columns=7,
        owner_rule="conventions.md rule 1", ship_rule="conventions.md rule 2",
        line_cap=CAPS["pay_calendar"], line_cap_rule="conventions.md rule 4",
        archive_rule="conventions.md rule 5",
        stated_count_rx=STATED_COUNT_RX,
        arc_state_heading="## Where this stands",
        arc_state_cap=SIGNPOST_CAPS["pay_calendar"],
        rules_label="conventions.md",
        ticked_entry_cap=TICKED_CAPS["pay_calendar"],
    ),
    "credit_card": PlanSpec(
        path=registry.ARC_DOCS["credit_card"],
        steps_heading="## The steps", steps_label="The steps",
        ledger_heading="## Consumer inventory",
        ledger_label="no ledger of its own (see ledger.md)",
        ledger_columns=7,
        owner_rule="conventions.md rule 1", ship_rule="conventions.md rule 2",
        line_cap=CAPS["credit_card"], line_cap_rule="conventions.md rule 4",
        archive_rule="conventions.md rule 5",
        stated_count_rx=STATED_COUNT_RX,
        # This document has never had an orientation section.  It is graded for
        # its CAP and its ticked entries, not for a signpost it does not carry --
        # inventing one so the parametrization is uniform would be a gate
        # grading a section nobody wrote.
        arc_state_heading="## Context",
        arc_state_cap=40,
        rules_label="conventions.md",
        ticked_entry_cap=TICKED_CAPS["credit_card"],
    ),
}

#: Every arc document, for the rules that apply to all four.
ARCS = sorted(SPECS)

#: Only the three that carry an orientation section (rule 6).
SIGNPOST_ARCS = sorted(SIGNPOST_CAPS)


class TestEveryDocumentIsUnderItsCap:
    """conventions.md rule 4."""

    @pytest.mark.parametrize("arc", ARCS)
    def test_the_document_is_within_its_line_cap(self, arc):
        """The document is within its line cap."""
        assert line_count_violation(SPECS[arc].read(), SPECS[arc]) is None

    @pytest.mark.parametrize("arc", ARCS)
    def test_the_control_fires_when_the_cap_is_exceeded(self, arc):
        """A cap nobody has seen fail is a number, not a gate."""
        spec = SPECS[arc]
        padded = spec.read() + "\nfiller\n" * (CAPS[arc] + 1)
        problem = line_count_violation(padded, spec)
        assert problem is not None and "rule 4" in problem

    @pytest.mark.parametrize("arc", ARCS)
    def test_the_cap_still_has_headroom(self, arc):
        """A cap already binding cannot absorb the next finding."""
        actual = len(SPECS[arc].read().splitlines())
        assert actual <= CAPS[arc] - 20, (
            f"{arc} is at {actual} of {CAPS[arc]} -- under 20 lines of headroom. "
            f"conventions.md rule 5: archive a completed span, do not raise the cap"
        )


class TestTheSignpostIsStillASignpost:
    """conventions.md rule 6."""

    @pytest.mark.parametrize("arc", SIGNPOST_ARCS)
    def test_the_orientation_section_is_within_its_cap(self, arc):
        """The orientation section is within its cap."""
        assert arc_state_violation(SPECS[arc].read(), SPECS[arc]) is None

    @pytest.mark.parametrize("arc", SIGNPOST_ARCS)
    def test_the_control_fires_on_an_appended_paragraph(self, arc):
        """The control fires on an appended paragraph."""
        spec = SPECS[arc]
        text = spec.read()
        marker = spec.arc_state_heading
        assert marker in text, f"{arc} has no {marker!r} section"
        bloated = text.replace(
            marker, marker + "\n" + "appended log line\n" * (spec.arc_state_cap + 5), 1,
        )
        problem = arc_state_violation(bloated, spec)
        assert problem is not None


class TestAShippedStepIsAPointer:
    """conventions.md rule 7."""

    @pytest.mark.parametrize("arc", [a for a in ARCS if TICKED_CAPS[a]])
    def test_every_ticked_entry_opens_with_its_commit(self, arc):
        """Every ticked entry opens with its commit."""
        assert not ticked_entry_violations(SPECS[arc].read(), SPECS[arc])

    def test_the_live_corpus_contains_a_ticked_entry_to_grade(self):
        """Rule 7 with no subject is untested by the clean case."""
        ticked = [
            (arc, ident)
            for arc in ARCS
            for ident, is_ticked, _ in step_entries(SPECS[arc].read(), SPECS[arc])
            if is_ticked
        ]
        assert ticked, "no ticked step anywhere -- rule 7 grades nothing"

    def test_the_control_fires_when_a_ticked_entry_drops_its_hash(self):
        """pay_calendar C1 is ticked and opens with `f9d148fe`."""
        spec = SPECS["pay_calendar"]
        text = spec.read()
        assert "`f9d148fe`" in text, "the control's anchor commit is gone"
        problem = ticked_entry_violations(text.replace("`f9d148fe`,", "shipped,"), spec)
        assert problem, "a ticked entry with no opening hash must be reported"


class TestTheDocumentsPointAtTheRegistries:
    """The strip left a pointer, not a summary -- a summary is a second copy."""

    @pytest.mark.parametrize("arc", ARCS)
    def test_the_moved_sections_name_their_new_home(self, arc):
        """The moved sections name their new home."""
        text = SPECS[arc].read()
        assert "ledger.md" in text, f"{arc} does not point at the shared ledger"
        assert "conventions.md" in text, f"{arc} does not point at the shared rules"

    @pytest.mark.parametrize("arc", ARCS)
    def test_no_arc_document_still_carries_a_findings_table(self, arc):
        """The five-column ledger shape must not reappear in an arc document."""
        for line in SPECS[arc].read().splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = registry.UNESCAPED_PIPE_RX.split(line)[1:-1]
            assert not (len(cells) == 5 and cells[0].strip() == "id"), (
                f"{arc} has re-grown a findings table; findings live in ledger.md"
            )

    def test_the_credit_card_document_points_at_them_too(self):
        """The one document that never had rules of its own."""
        text = registry.ARC_DOCS["credit_card"].read_text()
        assert "ledger.md" in text and "conventions.md" in text
