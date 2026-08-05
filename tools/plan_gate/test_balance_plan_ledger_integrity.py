"""Every finding in the balance plan has a LIVE owner (Section 9 rule 6).

``docs/audits/balance_architecture/README.md`` is the balance arc's only live
planning document.  Its Section 6 ledger carries the findings that remain, and
its last column names who resolves each one.  Section 9 rule 6 fixes that
column's vocabulary and requires every value in it to be answerable:

* **a live (unticked) Section 5 step ID** -- the normal case;
* **``operator``** -- a question only the developer can answer from outside the
  code;
* **``developer-decision``** -- a fork the developer has taken, dated.

**Prose does not enforce itself, and the count is why this file exists.**  The
2026-07-27 triage found 29 of 41 open rows with no owner at all, and FOUR of
them naming a step that had already SHIPPED -- N-14, N-33, N-40, N-56, found by
reading the code rather than the document, weeks after the fact.  One commit
later X-o's trace found six more, every one naming a TICKED step: N-43, N-72,
N-78, N-85 and N-95 pointed at ``X-g4`` (annotated four different ways) and
N-46 at ``X-c2c4``.  That is three hand-passes in two days finding the same
class, and the document's own Section 8 says a safety that is a predicate is
not a safety -- this one was not even a predicate.

**It lives in ``tools/`` and not in ``tests/``, and that is what makes it a
gate rather than a habit.**  Under ``tests/`` it inherited the autouse ``db``
fixture -- so grading a markdown file needed a running PostgreSQL -- and it ran
only when someone chose to run the full suite, or at PR time.  Since pull
requests here open at the END of an arc, that is a dozen steps after a stale
owner appears, which is roughly when the hand-passes found the last four.  A
gate whose own trigger depends on discipline is the thing this file exists to
replace.

It now sits in the database-free tier the custom pylint checkers already use
(``tools/pylint/tests``, run as ``pytest <dir> -c /dev/null``): a pre-commit
hook scoped to the plan documents and to this directory, so EDITING THE LEDGER
is what runs it, plus the same CI step that runs the checker tests.  Reading a
repository file by path and asserting on its text is an established shape here
-- ``test_template_no_money_arithmetic.py`` and ``test_posting_ref_seed_parity.py``
both do it.

**The parser moved to ``_plan_gate`` when a second document adopted these
rules** (the recurrence redesign, ``test_recurrence_plan_ledger_integrity.py``).
It is shared rather than copied because it carries five measured
false-positive fixes -- annotated owner cells, two-owner cells split at paren
depth zero, owner-column-only checkbox resolution, unescaped-pipe row
splitting, and fenced-region blanking -- and a copy would have to inherit every
one of them by hand.  This file keeps what is genuinely the balance document's:
its spec, its premise floors, and its negative controls.
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
)

#: Section 9 rule 4's hard cap on the whole document, in lines.
#:
#: The rule this replaces was a ~500-line prose TARGET that exempted "growth
#: from marking work COMPLETED" -- ticking boxes, as-built step detail, moving
#: findings to closed.  Every one of those is a real record, and the exemption
#: is nonetheless how the file reached **6,688 lines**: the exempt category has
#: no ceiling and it is the category that grows every step.  The cap is
#: therefore on the WHOLE file with no exemption, and rule 5 names the only
#: legal remedy -- archive a completed span, condensed to one line per step.
#: Shrink the record of what is DONE, never the specification of what remains.
#:
#: **The number is measured, not chosen.**  At the 2026-08-04 trim the document
#: was reduced to its live content -- Section 5's remaining-step
#: specifications, the findings ledger (93 rows today), the one-line ruling index, the
#: 19-line signpost and Sections 1-3 and 7-9 -- and landed at 976, from 6,688.
#: A 1,000-line cap would have left roughly twenty lines of headroom and failed
#: within a step or two, which is how a gate gets uninstalled rather than fixed
#: (this file's own thesis, and the reason its parser is strict about false
#: positives).  1,200 is that measured floor plus room to work.  **Raising it
#: again is not the answer when it binds**: the floor moves DOWN as steps ship,
#: because a shipped step's specification becomes one line in an as-built
#: record.  If it is not moving down, the archive move is overdue.
_MAX_LINES = 1200

#: The orientation section's cap.  Measured at 20 lines when the section was
#: rebuilt as a signpost after being found at 1,019 lines of append-only log;
#: 30 is that plus room, and it is deliberately too small to hold a narrative
#: -- a paragraph of detail does not fit, which is the enforcement.  See
#: ``_plan_gate.arc_state_violation`` for the failure mode itself.
_MAX_ARC_STATE_LINES = 30

#: **The ticked-entry arm is deliberately OFF for this document, and the
#: measurement is why.**  Three of its four ticked steps already follow the
#: convention the arm enforces -- ``X-ae`` (2 lines, ``a778703f``), ``X-af``
#: (2, ``dbee3812``), ``X-aj1`` (4, ``1688f508``) -- but ``X-f1``'s entry is 18
#: lines and cites no hash at all.  That is a real violation of a rule this arc
#: already practises and states in its own rule 5 ("prose nobody re-verifies is
#: worse than a hash anyone can check"), NOT a false positive.  Turning the arm
#: on here would fail a document belonging to an arc that is mid-flight, which
#: is that arc's call and not this commit's.  Recorded rather than silently
#: skipped: set ``ticked_entry_cap`` once X-f1's entry is condensed.
_TICKED_ENTRY_CAP = None

#: The balance arc's document, anchored on this file rather than on the working
#: directory so the gate grades the same document however it is invoked.
SPEC = PlanSpec(
    path=(
        Path(__file__).resolve().parents[2]
        / "docs" / "audits" / "balance_architecture" / "README.md"
    ),
    steps_heading="## 5.",
    steps_label="Section 5",
    ledger_heading="## 6.",
    ledger_label="Section 6",
    ledger_columns=5,
    owner_rule="Section 9 rule 6",
    ship_rule="Rule 2",
    line_cap=_MAX_LINES,
    line_cap_rule="Section 9 rule 4",
    archive_rule="rule 5",
    stated_count_rx=STATED_COUNT_RX,
    arc_state_heading="## Where the arc stands",
    arc_state_cap=_MAX_ARC_STATE_LINES,
    rules_label="Sections 7-9",
    ticked_entry_cap=_TICKED_ENTRY_CAP,
)


class TestTheBalancePlanLedgerHasNoUnownedRows:
    """The gate rule 6 calls for: this document's owners are all answerable."""

    def test_the_parser_finds_the_document_it_is_grading(self):
        """Premise: the steps and the ledger are actually being read.

        Asserted first and separately because every check below passes
        vacuously against an empty parse -- a regex that matched nothing would
        report a perfect ledger.  The floors are far under the live counts (27
        checkboxes and 38 ledger ROWS when this was written -- the table also
        carries a header and a delimiter line, so a naive count of lines
        starting with ``|`` gives 40 and is not what is floored here) so
        ordinary growth and ordinary archiving do not touch them, while a
        parser that silently stops working does.

        A floor is not enough on its own, which is why the three silent-parse
        holes the adversarial review found are closed at their source rather
        than covered here: an empty id cell, a duplicate checkbox id, and a
        ``##`` line inside a fence would each keep the counts plausible.
        """
        text = SPEC.read()
        steps = parse_steps(text, SPEC)
        rows = parse_ledger(text, SPEC)
        assert len(steps) >= 15, (
            f"parsed only {len(steps)} Section 5 checkboxes: {sorted(steps)}"
        )
        assert any(not ticked for ticked in steps.values()), (
            "parsed no LIVE step at all -- every owner would fail"
        )
        assert any(ticked for ticked in steps.values()), (
            "parsed no SHIPPED step at all -- the stale-owner arm could never "
            "fire, which is the arm this gate exists for"
        )
        assert len(rows) >= 25, f"parsed only {len(rows)} ledger rows"

    def test_every_finding_has_a_live_owner(self):
        """No row names a shipped step, an unknown id, or a retired word."""
        violations = owner_violations(SPEC.read(), SPEC)
        assert violations == [], (
            "docs/audits/balance_architecture/README.md Section 6 violates "
            "Section 9 rule 6:\n  " + "\n  ".join(violations)
        )

    def test_the_ledger_states_its_own_size_correctly(self):
        """The "stands at N rows" sentence matches the table it describes.

        Added 2026-07-28 at plan step X-u, which found the sentence reading 38
        against a 40-row table -- drift left by a step that updated the rows and
        not the prose about them.  Correcting the number by hand is what had
        already been done and is what drifted; this makes it a predicate.
        """
        violation = stated_count_violation(SPEC.read(), SPEC)
        assert violation is None, violation

    def test_the_document_is_within_its_line_cap(self):
        """Section 9 rule 4's cap, as a predicate rather than a target.

        Added 2026-08-04, when the file was 6,688 lines against a ~500-line
        prose target it had never been graded on.  The remedy the message
        names is rule 5's archive move; see
        ``_plan_gate.line_count_violation`` for why it names one at all.
        """
        violation = line_count_violation(SPEC.read(), SPEC)
        assert violation is None, violation

    def test_the_orientation_section_is_still_an_orientation(self):
        """The section the next session reads first has not become a log again.

        Added 2026-08-04 with the section's rebuild.  This is the arm most
        likely to fire in ordinary use, and that is the point: it fires on the
        commit that would have started the next running narrative, when moving
        the paragraph to its real home is still one edit.
        """
        violation = arc_state_violation(SPEC.read(), SPEC)
        assert violation is None, violation


class TestTheArmsSeeThisDocument:
    """The arms that can only be checked against the REAL document.

    The parser's negative controls live in ``test__plan_gate.py``, run once
    against a synthetic document rather than duplicated per plan.  What remains
    here is what a synthetic document cannot prove: that this file's own
    spelling, sections and counts are the ones the spec points at.
    """


    def test_the_count_arm_reads_the_LIVE_documents_spelling(self):
        """The arm matches the real file's sentence, not just the synthetic one.

        The gate's own §8 lesson, one axis over: a pattern that is never
        exercised against the artifact it grades can be blind to it and still
        pass every synthetic control. This asserts the live document's count
        sentence is FOUND -- if Section 6 stops stating a count the arm becomes
        vacuous, and this is what says so.
        """
        body = section(SPEC.read(), SPEC.ledger_heading, label=str(SPEC.path))
        assert STATED_COUNT_RX.search(body), (
            "Section 6 no longer states a row count in the spelling this arm "
            "matches -- the drift check is now vacuous. Restore the sentence "
            "or delete the arm; do not leave it passing on nothing."
        )

