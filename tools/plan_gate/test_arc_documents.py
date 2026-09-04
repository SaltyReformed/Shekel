"""The gate over the ARC documents -- what stays after the registries left.

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
shape.  It now carries ONLY what these four functions read --
``line_count_violation``, ``arc_state_violation``, ``ticked_entry_violations``
and ``step_entries``.  Its ledger fields were briefly kept and marked inert
after the findings moved to ``ledger.md``; they have since been deleted, along
with the three rule-citation fields every spec below set to the same string now
that there is one ``conventions.md``.
"""
from __future__ import annotations

import pytest

import _registry as registry
from _tables import UNESCAPED_PIPE_RX
from _plan_gate import (
    PlanSpec,
    arc_state_violation,
    line_count_violation,
    step_entries,
    ticked_entry_violations,
)

#: Each cap is the document's live-content floor plus room to work, NOT a
#: ceiling sized to fit today's file.  They all came DOWN when the registries
#: left: balance 1,200 -> 1,000, recurrence 900 -> 850, pay-calendar 560 -> 500,
#: and credit-card was capped at 400 for the first time.  Lowering them is the
#: point -- the space the duplication occupied must not silently become room to
#: duplicate again.  No line count is quoted here: the three that were went
#: stale within two commits, and `test_the_cap_still_has_headroom` measures the
#: live files anyway.
#:
#: **What rule 4 actually says, corrected 2026-09-01.**  This comment read
#: "conventions.md rule 4: raising a cap is never the answer when it binds",
#: and rule 4 says no such thing.  It says a binding cap is **a QUESTION FOR
#: THE DEVELOPER** -- "rule 5 answers it while a completed span is left to
#: archive; where none is, say so and ask" -- and that "a cap is never raised
#: without being asked for".  Those are different rules: one forbids raising,
#: the other requires asking first.  The file already held the counterexample
#: (bank_import, raised by the developer 2026-08-30) while the comment above it
#: said raising is never the answer, so the misquote survived its own refutation
#: sitting eight lines below it.
CAPS = {
    # **RAISED 1000 -> 1180 by the developer, 2026-09-01**, on rule 4's terms
    # and by bank_import's OWN calibration below.  The cap BOUND: the README
    # sat at 971 of an effective 980, NINE lines, against 55 open steps still
    # owing specifications -- 0.2 lines per open step.  Rule 5's escape was
    # spent first: `balance:X-f3c-2b-2c`'s registry pass was reworked to net
    # ZERO lines to fit, after a first draft at 986 was refused by this arm.
    # The number is not invented.  bank_import's 260 gave that arc 3.4 lines
    # per open step when the developer set it, so 55 x 3.4 = 187 of room, and
    # 971 + 187 + 20 headroom = 1178, rounded to 1180.  It stays under the
    # 1,200 this document carried before the registries left.
    # **RAISED 1180 -> 1200 by the developer, 2026-09-03**, on rule 4's terms:
    # the decision sweep minted EIGHT balance steps (X-bl..X-bp, X-bj-1/2,
    # X-f3c-2b-2d) whose specifications landed the README at exactly 1180,
    # rule 5's escape was spent first (every shipped entry already a pointer
    # of six lines or fewer, no completed span left), and the file's own
    # calibration would allow 8 x 3.4 = 27 more; 1200 is the minimum that
    # clears the 20-line headroom and does not exceed what the document
    # carried before the registries left.
    "balance": 1200,
    # **RAISED 850 -> 900 by the developer, 2026-09-03**, on rule 4's terms and
    # by the same calibration that raised the balance README.  The cap BOUND
    # while minting `recurrence:R7d-h`: the document sat at 843 with 23 open
    # steps still owing specifications -- 0.3 lines per open step, the same
    # bind balance hit at 0.2.  **Rule 5's escape was spent FIRST and found
    # EXHAUSTED**: every shipped step in that document is already condensed to
    # one line, done on 2026-09-02, so there is no completed span left to
    # archive.  The number is not invented: bank_import's 260 gave that arc 3.4
    # lines per open step, and 23 x 3.4 = 78 of room; 843 + 78 = 921, trimmed
    # to 900 because R7d-h's own specification was already compressed four
    # times to chase the old cap and 900 still leaves 2.5 lines per open step.
    "recurrence": 900,
    # **RAISED 500 -> 520 by the developer, 2026-09-01**, same terms and the
    # same arithmetic: 466 of an effective 480 is FOURTEEN lines against 10
    # open steps (1.4 each), and rule 5's escape was spent twice in one day --
    # `pay_calendar:C4-b-1` condensed the COMPLETE `C4-a` span and then the
    # COMPLETE reader census, keeping each one's PREDICATE, which is the half
    # that cannot go stale.  10 x 3.4 = 34 of room: 466 + 34 + 20 = 520.
    # Deliberately NOT back to the 560 this document carried before the
    # registries left; that space was removed on purpose and 520 does not
    # return it.
    "pay_calendar": 520,
    "credit_card": 400,
    # **RAISED 200 -> 260 by the developer, 2026-08-30**, on rule 4's own
    # terms: the cap BOUND, and rule 5's escape was spent first.  The shipped
    # X-gb..X-gf-3b-2 span had already been condensed to one line per step,
    # every other shipped entry in the file was already one line, and the
    # document sat at exactly 180 -- the headroom floor -- with `X-gj-1b`,
    # `X-gj-1c`, `X-gj-3` and `X-gj-4` all still owing specifications.
    # This arc carries the largest open leaf set of the five against the
    # second-lowest cap; 260 restores about the room that family needs and
    # leaves the forcing function well below the balance README's.
    "bank_import": 260,
    # **SET at 260 by the developer, 2026-09-03**, when the arc was minted: the
    # bank_import number, eight open steps owing a few lines each plus the
    # argument, and the forcing function biting well before the balance
    # README's scale.  Raised only when it binds and he is asked (rule 4).
    "salary": 260,
}

#: The signpost's cap, per document.  The balance README's reached 1,019 lines
#: as an append-only log before it was capped at all.
SIGNPOST_CAPS = {
    "balance": 30, "recurrence": 20, "pay_calendar": 20, "bank_import": 30,
    "salary": 20,
}

#: A SHIPPED step's entry cap (rule 7).
#:
#: **The balance HOLE is CLOSED (2026-08-09) and the number it was justified by
#: had gone stale in both directions.**  The exemption read "four ticked steps
#: (X-ae, X-af, X-aj1, X-f1) ... all four open with prose instead of their
#: commit hash, and X-f1's entry runs 18 lines".  Measured before closing it:
#: SIX steps were ticked, only X-f1 lacked a hash, and its entry was 11 lines
#: -- three violations, not five, and X-ae / X-af / X-aj1 / X-an had quietly
#: been fixed with nobody re-reading the exemption that cited them.  **A
#: disabled arm carries a claim about the corpus, and that claim rots exactly
#: like any other**; this one had been arguing for its own existence off
#: numbers that were no longer true.
#:
#: Closing it took one hash (`8d812662`, PR #83) and two condensations, and
#: `conventions.md:8` now lists rule 7 as a PREDICATE without an exception.
#: X-f1's live sentence -- a row is settled iff it carries a settle day -- did
#: NOT go with the trim: rule 5 forbids a live sentence depending on an
#: archived one, so it moved UP to Section 3.1, which defines the "settled
#: transaction rows" it is about.
TICKED_CAPS = {
    "bank_import": 6,
    "balance": 6,
    "recurrence": 6,
    "pay_calendar": 6,
    "credit_card": 6,
    "salary": 6,
}

SPECS = {
    "balance": PlanSpec(
        path=registry.ARC_DOCS["balance"],
        steps_heading="## 5.", steps_label="Section 5",
        line_cap=CAPS["balance"],
        arc_state_heading="## Where the arc stands",
        arc_state_cap=SIGNPOST_CAPS["balance"],
        ticked_entry_cap=TICKED_CAPS["balance"],
    ),
    "recurrence": PlanSpec(
        path=registry.ARC_DOCS["recurrence"],
        steps_heading="## 4.", steps_label="section 4",
        line_cap=CAPS["recurrence"],
        arc_state_heading="## Where this stands",
        arc_state_cap=SIGNPOST_CAPS["recurrence"],
        ticked_entry_cap=TICKED_CAPS["recurrence"],
    ),
    "pay_calendar": PlanSpec(
        path=registry.ARC_DOCS["pay_calendar"],
        steps_heading="## 4.", steps_label="section 4",
        line_cap=CAPS["pay_calendar"],
        arc_state_heading="## Where this stands",
        arc_state_cap=SIGNPOST_CAPS["pay_calendar"],
        ticked_entry_cap=TICKED_CAPS["pay_calendar"],
    ),
    "bank_import": PlanSpec(
        path=registry.ARC_DOCS["bank_import"],
        steps_heading="## The steps", steps_label="The steps",
        line_cap=CAPS["bank_import"],
        arc_state_heading="## Context",
        arc_state_cap=SIGNPOST_CAPS["bank_import"],
        ticked_entry_cap=TICKED_CAPS["bank_import"],
    ),
    "credit_card": PlanSpec(
        path=registry.ARC_DOCS["credit_card"],
        steps_heading="## The steps", steps_label="The steps",
        line_cap=CAPS["credit_card"],
        # This document has never had an orientation section.  It is graded for
        # its CAP and its ticked entries, not for a signpost it does not carry --
        # inventing one so the parametrization is uniform would be a gate
        # grading a section nobody wrote.
        arc_state_heading="## Context",
        arc_state_cap=40,
        ticked_entry_cap=TICKED_CAPS["credit_card"],
    ),
    "salary": PlanSpec(
        path=registry.ARC_DOCS["salary"],
        steps_heading="## 4.", steps_label="section 4",
        line_cap=CAPS["salary"],
        arc_state_heading="## Where this stands",
        arc_state_cap=SIGNPOST_CAPS["salary"],
        ticked_entry_cap=TICKED_CAPS["salary"],
    ),
}

#: Every arc document, for the rules that apply to all of them.
ARCS = sorted(SPECS)

#: Only those that carry an orientation section (rule 6).
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
        """pay_calendar C1 is ticked and opens with `f9d148fe`.

        **The planted edit must not depend on the PUNCTUATION after the hash.**
        It did until 2026-08-10: it replaced ``` `f9d148fe`, ``` including the
        comma, so when C1's entry was condensed and the comma became a period
        the replacement matched NOTHING, the text went through unmodified, and
        the control passed a document it had not mutated -- reporting the arm
        healthy without exercising it.  Stripping the hash alone is punctuation
        independent, and the assertion below proves the mutation landed before
        asking whether the arm fired.
        """
        spec = SPECS["pay_calendar"]
        text = spec.read()
        assert "`f9d148fe`" in text, "the control's anchor commit is gone"
        planted = text.replace("`f9d148fe`", "shipped")
        assert planted != text, "the control mutated nothing"
        assert ticked_entry_violations(planted, spec), (
            "a ticked entry with no opening hash must be reported"
        )


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
            cells = UNESCAPED_PIPE_RX.split(line)[1:-1]
            assert not (len(cells) == 5 and cells[0].strip() == "id"), (
                f"{arc} has re-grown a findings table; findings live in ledger.md"
            )

    def test_the_credit_card_document_points_at_them_too(self):
        """The one document that never had rules of its own."""
        text = registry.ARC_DOCS["credit_card"].read_text()
        assert "ledger.md" in text and "conventions.md" in text
