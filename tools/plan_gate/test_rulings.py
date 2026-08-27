"""Controls for the RULINGS registry (conventions.md rules 3, 9 and 10).

Every arm here has a companion that plants the defect in a COPY of the real
file and asserts the arm FIRES.  A predicate nobody has seen fail is a number,
not a gate -- this package's own phrasing, and the reason the arm that grades
ticked entries was found to have been arguing for its own exemption off counts
that had gone stale in both directions.

The specimens are DERIVED from the live registry rather than named here, for
the reason ``_staging.a_prefix_trap`` exists: three docstrings naming one
specimen went stale, and a control anchored on a row somebody later edits
fails for a reason that has nothing to do with what it grades.
"""
from __future__ import annotations

import re
from collections import Counter

import pytest

import _registry as registry
import _rulings as rulings


def _a_row_of(arc: str) -> str:
    """Return the live registry's first row for *arc*, verbatim.

    Args:
        arc: The arc slug whose row to take.

    Returns:
        The whole markdown line, for a control to plant a defect into.
    """
    for line in rulings.RULINGS.read_text().splitlines():
        if line.startswith(f"| {arc} |"):
            return line
    raise AssertionError(f"no {arc} row in rulings.md to derive a specimen from")


def _declaration() -> str:
    """Return the preamble's per-arc count sentence, VERBATIM from the file.

    Taken from the document rather than rebuilt from
    :func:`_rulings.declared_arc_counts`, and the difference is not cosmetic:
    the sentence names five arcs and a formatter wraps it, so a rebuilt
    ```arc` N, `arc` N`` string is not a substring of the file and every
    control staging a defect into it fails for a reason that has nothing to do
    with what it grades.

    DERIVED rather than spelled, so a control's anchor cannot go stale the
    next time a ruling is filed -- three controls needed hand-editing when
    ``balance:R-GZ`` landed, which is the friction that turns a staged control
    into a literal nobody re-reads.

    Returns:
        The ```arc` N, `arc` N`` fragment the preamble states, newlines and all.
    """
    match = rulings.MIGRATED_RX.search(rulings.RULINGS.read_text())
    assert match is not None, "the preamble states no per-arc declaration"
    return match.group("arcs")


def _with_cell(row: str, index: int, value: str) -> str:
    """Return *row* with cell *index* replaced by *value*.

    Args:
        row: A whole markdown row, outer pipes included.
        index: The zero-based cell position -- 0 ``arc``, 1 ``id``, 2 ``also``,
            3 ``date``, 4 ``what was ruled``.
        value: The replacement cell text.

    Returns:
        The rebuilt row.
    """
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    cells[index] = value
    return "| " + " | ".join(cells) + " |"


class TestTheRegistryIsWellFormed:
    """Rule 10: the key is ``(arc, id)`` and it names exactly one ruling."""

    def test_every_row_names_exactly_one_ruling(self):
        """No duplicate key, unknown arc, malformed id, or empty cell."""
        assert not rulings.key_violations()

    def test_the_registry_is_not_empty(self):
        """A restructured file reading as EMPTY passes every other arm.

        ``rows_under`` raises when no table carries the header, which is the
        real guard; this states the consequence the other arms depend on.
        """
        assert len(rulings.ruling_rows()) > 0

    def test_a_duplicate_key_is_caught(self, stage_rulings):
        """Two rows with one ``(arc, id)`` is N-217 and N-367's own defect."""
        row = _a_row_of("balance")
        stage_rulings(row, f"{row}\n{row}")
        problems = rulings.key_violations()
        assert any("names 2 rulings" in p for p in problems), problems

    def test_a_malformed_id_is_caught(self, stage_rulings):
        """A ruling nobody can cite is a ruling that is not recorded."""
        row = _a_row_of("balance")
        stage_rulings(row, _with_cell(row, 1, "not an id"))
        assert any("not a citable ruling id" in p for p in rulings.key_violations())

    def test_an_unknown_arc_is_caught(self, stage_rulings):
        """Rule 10's key with half of it invented."""
        row = _a_row_of("balance")
        stage_rulings(row, _with_cell(row, 0, "nonesuch"))
        assert any("is not one of" in p for p in rulings.key_violations())

    def test_a_row_stating_no_rule_is_caught(self, stage_rulings):
        """N-220: a ruling row must state a rule."""
        row = _a_row_of("balance")
        stage_rulings(row, _with_cell(row, 4, "--"))
        assert any("states no rule" in p for p in rulings.key_violations())

    def test_a_row_stating_no_date_is_caught(self, stage_rulings):
        """Only the date orders two rulings on one subject."""
        row = _a_row_of("balance")
        stage_rulings(row, _with_cell(row, 3, "--"))
        assert any("states no date" in p for p in rulings.key_violations())

    def test_an_alias_shadowing_a_live_id_is_caught(self, stage_rulings):
        """The collision this registry ends, arriving through the alias column."""
        rows = rulings.ruling_rows()
        victim = next(r for r in rows if r.arc == "balance")
        other = next(r for r in rows
                     if r.arc == "balance" and r.key != victim.key)
        row = _a_row_of("balance")
        stage_rulings(row, _with_cell(row, 2, other.bare_ident))
        assert any("resolves to two rulings in one arc" in p
                   for p in rulings.key_violations())


class TestTheRegistryStatesItsOwnSize:
    """Rule 3: a registry that states its size has that number CHECKED."""

    def test_the_stated_count_matches_the_table(self):
        """The self-count is live."""
        assert rulings.stated_count_violation() is None

    def test_a_stale_count_is_caught(self, stage_rulings):
        """Every one of steps.md's four self-counts was stale when arms landed."""
        actual = len(rulings.ruling_rows())
        stage_rulings(
            f"**The ruling registry stands at {actual} rows.**",
            f"**The ruling registry stands at {actual + 1} rows.**",
        )
        assert "conventions.md rule 3" in (rulings.stated_count_violation() or "")

    def test_a_missing_count_is_caught(self, stage_rulings):
        """A sentence the gate cannot find reads as no claim, not as a pass."""
        actual = len(rulings.ruling_rows())
        stage_rulings(f"**The ruling registry stands at {actual} rows.**", "")
        assert "states no row count" in (rulings.stated_count_violation() or "")

    def test_the_runaway_backstop_is_not_binding(self):
        """It is a backstop, never a forcing function (rule 4)."""
        assert rulings.runaway_violation() is None
        assert len(rulings.ruling_rows()) < rulings.RULINGS_RUNAWAY_ROWS

    def test_the_runaway_backstop_fires(self, monkeypatch):
        """A duplicated table or a generator loop fails loudly.

        No staging: the defect is a row COUNT, and lowering the backstop to 1
        against the real 105-row table exercises it exactly.  This control
        used to call ``stage_rulings`` with an identical replacement, which
        staged nothing and named a defect it was not planting.
        """
        monkeypatch.setattr(rulings, "RULINGS_RUNAWAY_ROWS", 1)
        assert "runaway backstop" in (rulings.runaway_violation() or "")


class TestTheMigrationCannotSitHalfDone:
    """An arc's rulings are in ONE document and the gate says which."""

    def test_the_declaration_agrees_with_the_documents(self):
        """Both directions, over the registry AND the arc documents."""
        assert not rulings.migration_violations()

    def test_every_declared_arc_has_rows(self):
        """A declaration is not evidence; the rows are."""
        present = {row.arc for row in rulings.ruling_rows()}
        assert set(rulings.migrated_arcs()) == present

    def test_an_arc_with_rows_but_no_declaration_is_caught(self, stage_rulings):
        """A reader must never have to guess which document is authoritative."""
        declared = _declaration()
        stage_rulings(declared, declared.split(", ", maxsplit=1)[0])
        assert any("does not declare bank_import moved" in p
                   for p in rulings.migration_violations())

    def test_a_declared_arc_with_no_rows_is_caught(self, stage_rulings):
        """The preamble claiming an arc the table does not hold."""
        declared = _declaration()
        stage_rulings(declared, f"{declared}, `envelopes` 3")
        assert any("carries no envelopes row" in p
                   for p in rulings.migration_violations())

    def test_no_arc_document_states_a_ruling(self):
        """The finished state, which is what replaced the map.

        ``ARC_RULING_HEADINGS`` listed the arcs that had not moved and was
        read in both directions while the migration ran.  ``X-ao-2`` emptied
        and DELETED it: there is no half-moved state left to describe, and an
        arc that never enters a map is invisible to every arm that reads one.
        """
        assert not hasattr(rulings, "ARC_RULING_HEADINGS")
        assert set(rulings.migrated_arcs()) == set(rulings.ARC_DOCS)
        assert not rulings.migration_violations()

    @pytest.mark.parametrize("arc", sorted(registry.ARC_DOCS))
    @pytest.mark.parametrize("header", rulings.RULING_TABLE_HEADERS)
    def test_an_arc_keeping_a_rulings_table_is_caught(self, arc, header,
                                                     tmp_path, monkeypatch):
        """Two copies of one registry is what this move removes.

        Over EVERY arc and EVERY grammar.  Parametrizing on the arcs that had
        moved was the shape of the first draft's hole: the arms that mattered
        were never run against the arcs still to come.
        """
        target = tmp_path / f"{arc}.md"
        target.write_text(
            registry.ARC_DOCS[arc].read_text() + f"\n{header}\n|---|---|\n"
        )
        monkeypatch.setitem(registry.ARC_DOCS, arc, target)
        assert any(f"carries a {header!r} table" in p
                   for p in rulings.migration_violations())

    @pytest.mark.parametrize("declaration", [
        "| **A new fork** | **The answer. R-R99, ruled 2026-08-27** |",
        "**Ruling R-R99 (2026-08-27): the answer.** Because of the evidence.",
        "The bound is derived, R-PC99, archived -- see the as-built record.",
    ])
    def test_a_ruling_declared_in_an_arc_document_is_caught(
            self, declaration, tmp_path, monkeypatch):
        """The arm that has no map behind it, in all three live spellings.

        The middle specimen is ``recurrence:R-R28``'s own shape: a live ruling
        that sat in section 4 PROSE, outside every table, while ``steps.md``
        cited it as ``R13``'s.  No table arm could see it and no heading map
        would have looked there.
        """
        target = tmp_path / "recurrence.md"
        target.write_text(
            registry.ARC_DOCS["recurrence"].read_text() + f"\n{declaration}\n"
        )
        monkeypatch.setitem(registry.ARC_DOCS, "recurrence", target)
        assert any("DECLARES a ruling at line" in p
                   for p in rulings.migration_violations())

    @pytest.mark.parametrize("citation", [
        "It takes the DEFINITION and not the loan (ruling **R-R35**).",
        "**Ruling R-AP, taken AGAINST the recommendation**: the cluster stays.",
        "### Phase X -- the anchor half (ruling R-EB; runs FIRST)",
    ])
    def test_a_ruling_citation_is_not_a_declaration(self, citation, tmp_path,
                                                    monkeypatch):
        """The arm's other half, and the one a wider pattern would break.

        All three are live text in arc documents today.  A declaration carries
        a DATE beside the id and a citation does not, which is the whole
        discriminator -- an arm that fired on every ``R-xx`` would make citing
        a ruling impossible in the documents whose job is to cite them.
        """
        target = tmp_path / "balance.md"
        target.write_text(
            registry.ARC_DOCS["balance"].read_text() + f"\n{citation}\n"
        )
        monkeypatch.setitem(registry.ARC_DOCS, "balance", target)
        assert not [p for p in rulings.migration_violations()
                    if "DECLARES a ruling" in p]

    @pytest.mark.parametrize("stated", [
        "1. **Re-account model.** Marking a purchase Credit MOVES it.",
        "- **A charged expense stays visible in place** on the source grid.",
        "| **A fork** | **An answer** |",
    ])
    def test_a_pointer_section_that_states_rulings_is_caught(
            self, stated, tmp_path, monkeypatch):
        """The arm for an ID-LESS block, which nothing else here can see.

        ``credit_card``'s eight locked rulings were a numbered LIST under a
        heading, with no ids and no table, and they sat unparsed for five
        weeks: the residual-table arm read table HEADERS, so that arc could
        have kept its whole registry through the lift with every other arm
        green.  The first specimen is one of those eight, verbatim in shape.
        """
        text = registry.ARC_DOCS["credit_card"].read_text()
        marker = "## The rulings\n"
        assert marker in text, "the pointer section this control needs is gone"
        target = tmp_path / "credit_card.md"
        target.write_text(text.replace(marker, f"{marker}\n{stated}\n", 1))
        monkeypatch.setitem(registry.ARC_DOCS, "credit_card", target)
        assert any("it is a POINTER" in p for p in rulings.migration_violations())

    def test_a_rulings_section_that_points_nowhere_is_caught(self, tmp_path,
                                                            monkeypatch):
        """A heading a reader lands on that names no forwarding address.

        Its sibling below covers the section going MISSING.  The two were one
        control until a mutation run measured what that cost: neutralising
        either the section arm or a whole-document "names rulings.md somewhere"
        arm left this test green, because removing every mention fired both.
        The redundant arm is deleted and each survivor has its own specimen.
        """
        text = registry.ARC_DOCS["pay_calendar"].read_text()
        target = tmp_path / "pay_calendar.md"
        target.write_text(text.replace("rulings.md", "somewhere else"))
        monkeypatch.setitem(registry.ARC_DOCS, "pay_calendar", target)
        assert any("does not name" in p and "rulings.md" in p
                   for p in rulings.migration_violations())

    @pytest.mark.parametrize("arc", sorted(registry.ARC_DOCS))
    def test_an_arc_document_with_no_rulings_section_is_caught(
            self, arc, tmp_path, monkeypatch):
        """The section itself going missing, which no other arm can see.

        Over every arc, because the balance README carried a heading no
        anchored pattern matched -- ``## 4. Decisions that govern the
        remaining work`` -- until ``X-ao-2`` renamed it. An arm that grades
        four arcs and reports on five is the failure this whole registry is
        an instance of.
        """
        text = registry.ARC_DOCS[arc].read_text()
        heading = next(line for line in text.splitlines()
                       if rulings.RULINGS_HEADING_RX.match(line))
        target = tmp_path / f"{arc}.md"
        target.write_text(text.replace(heading, "## Decisions", 1))
        monkeypatch.setitem(registry.ARC_DOCS, arc, target)
        assert any("carries no rulings section" in p
                   for p in rulings.migration_violations())

    def test_an_arc_the_preamble_omits_is_caught(self, stage_rulings):
        """The set-defined-by-omission shape, which this corpus has paid for.

        With the map gone, an arc missing from the declaration is the only way
        an arc can go unaccounted for -- and it now fails rather than reading
        as "not moved yet".
        """
        declared = _declaration()
        stage_rulings(declared,
                      re.sub(r",\s*`credit_card` 12", "", declared))
        assert any("rulings.md does not declare it" in p
                   for p in rulings.migration_violations())


class TestTheLiftLostNothing:
    """What the migration itself has to be true for, stated as predicates."""

    def test_each_arc_carries_the_count_its_preamble_declares(self):
        """Per-arc counts, reconciled -- not a literal pinned in this file.

        The first draft pinned ``== 74`` and ``== 31`` here, which is the
        shape CLAUDE.md rule 5 forbids as a class: legitimate work (a new
        ruling) turns the test red and the remedy is to edit the test.  It was
        also the ONLY guard against row loss, and it defended the wrong
        direction -- deleting 73 balance rows satisfies "exactly 74" only if
        the merge also corrects the total, which is precisely what a
        take-ours resolution does.

        The count now lives in the registry's own preamble, where rule 3 puts
        a self-count, and this asserts the two agree.
        """
        actual = Counter(row.arc for row in rulings.ruling_rows())
        assert rulings.declared_arc_counts() == dict(actual)

    def test_no_rule_text_was_split_by_an_unescaped_pipe(self):
        """bank_import:R-FW's own defect, as a predicate over every row.

        ``rows_under`` DROPS a row whose cell count differs from the header, so
        a split row does not fail any other arm here -- it silently stops
        being a ruling.  The count arm above is what catches it, and this
        states the reason.
        """
        text = rulings.RULINGS.read_text()
        header = "| arc | id | also | date | what was ruled |"
        body = [
            line for line in text.splitlines()
            if line.startswith("| ") and line != header
            and not set(line) <= set("|- ")
        ]
        assert len(body) == len(rulings.ruling_rows()), (
            "a row was dropped by rows_under, which means an unescaped pipe "
            "split it -- escape it as \\| (see _tables.UNESCAPED_PIPE_RX)"
        )

    def test_the_ambiguous_id_the_registry_now_makes_visible(self):
        """R-GU names two rulings, and here that is legal AND findable.

        The point of the pair key, stated as a control: before this file the
        collision was invisible to every gate; now it is a row-level fact a
        reader can enumerate, which is what X-ao-3 grades citations against.
        """
        by_id: dict[str, set[str]] = {}
        for row in rulings.ruling_rows():
            by_id.setdefault(row.bare_ident, set()).add(row.arc)
        ambiguous = {i: a for i, a in by_id.items() if len(a) > 1}
        assert ambiguous == {"R-GU": {"balance", "bank_import"}}, ambiguous
        assert not rulings.key_violations()


class TestTheArmsThatHadNoControl:
    """Every arm proven to FAIL, including the three that never had a companion.

    This module's opening claim -- that every arm here has a companion that
    plants the defect and asserts it fires -- was FALSE for three arms when it
    was written, and an adversarial review measured which by neutralising each
    arm in turn and watching all 26 tests still pass.  One of the three was the
    arm :func:`_rulings.migration_violations` singles out as closing the hole
    the others leave.  A safety nobody has seen fail is a number.
    """

    def test_an_alias_claimed_by_two_rows_is_caught(self, stage_rulings):
        """One id cannot be two rulings' former name."""
        rows = rulings.ruling_rows()
        donor = next(r for r in rows if r.also_keys())
        alias = donor.also.split(" (")[0].strip()
        victim = next(r for r in rows
                      if r.arc == donor.arc and r.key != donor.key
                      and not r.also_keys())
        row = _a_row_of(victim.arc)
        assert row.split(" | ")[1] == victim.bare_ident, row[:60]
        stage_rulings(row, _with_cell(row, 2, alias))
        assert any(f"{alias} is claimed as an `also` id by 2 rows" in p
                   for p in rulings.key_violations())

    def test_the_map_that_replaced_these_two_controls_is_gone(self):
        """Their subject was ``ARC_RULING_HEADINGS`` and X-ao-2 deleted it.

        Both graded the map against itself -- an arc dropped from it, and an
        entry that outlived its table -- and both were real while a migration
        was in flight.  What replaced them grades the DOCUMENTS: a ruling
        declaration, a rulings table in any grammar, and a pointer section
        that has started stating decisions.  None of those needs an arc to
        have been remembered.
        """
        assert not hasattr(rulings, "ARC_RULING_HEADINGS")
        assert rulings.RULING_DECLARATION_RX.search("R-R99, ruled 2026-08-27")
        assert rulings.RULINGS_HEADING_RX.match("## The rulings")


class TestRuleFourAppliesToThisFileToo:
    """The per-ROW cap, which is the whole of rule 4 here."""

    def test_the_only_rows_over_the_cap_are_the_lifted_debt(self):
        """The debt is KEYED, so a new over-cap row is distinguishable.

        These rows were over the cap in the documents that held them and were
        lifted verbatim; rule 5 forbids trimming a live specification to fit,
        and rule 4's own remedy sends the overflow to the as-built record of
        the step that shipped the ruling, or to that step's live specification
        when it has not shipped -- which is ``X-ao-2b``.  Recording WHICH rows
        rather than exempting them is what keeps a NEW over-cap row a failure.
        """
        assert not rulings.row_width_violations()
        over = {row.key for row in rulings.ruling_rows()
                if row.width > rulings.RULINGS_ROW_CAP}
        assert over == rulings.LIFTED_ROWS_OVER_CAP

    def test_a_new_row_over_the_cap_is_caught(self, stage_rulings):
        """The debt is a named set to compare against, never a licence."""
        row = _a_row_of("balance")
        stage_rulings(row, _with_cell(row, 4, "x" * (rulings.RULINGS_ROW_CAP + 1)))
        assert any("against the" in p and "row cap" in p
                   for p in rulings.row_width_violations())

    def test_a_debt_row_that_came_under_the_cap_is_caught(self, stage_rulings):
        """The direction a COUNT could not see, which is why it is a SET.

        ``LIFTED_ROWS_OVER_CAP = 23`` could not tell 23 rows from a different
        23: trim one row under the cap while another swells past it and the
        total is still 23 and the arm is still green.  Keyed, the trim is
        REPORTED -- which is how ``X-ao-2b`` shows its own progress instead of
        asserting it.
        """
        widest = max(rulings.ruling_rows(), key=lambda r: r.width)
        row = next(line for line in rulings.RULINGS.read_text().splitlines()
                   if line.startswith(f"| {widest.arc} | {widest.bare_ident} |"))
        stage_rulings(row, _with_cell(row, 4, "the rule, and nothing else"))
        assert any(f"{widest.key} is recorded in LIFTED_ROWS_OVER_CAP" in p
                   for p in rulings.row_width_violations())

    def test_the_cap_is_the_ledger_s_own_number(self):
        """Not a number fitted to today's file, which rule 4 forbids."""
        assert rulings.RULINGS_ROW_CAP == registry.LEDGER_ROW_CAP

    def test_the_widest_lifted_row_is_the_one_recorded(self):
        """16,087 characters against a 529-character median.

        Named so the debt has a face: it is the arc document's argument living
        in the registry, which is the sentence LEDGER_ROW_CAP was written for.
        """
        widest = max(rulings.ruling_rows(), key=lambda r: r.width)
        assert widest.key == "bank_import:R-GD"
        assert widest.width > 16000


class TestTheHalfMigrationCannotHideARowLoss:
    """The three states two adversarial reviews used to break the first draft."""

    def test_a_dropped_row_is_caught_even_when_the_total_is_corrected(
            self, stage_rulings):
        """A take-ours merge resolution, which is how rows actually go."""
        row = _a_row_of("balance")
        actual = len(rulings.ruling_rows())
        stage_rulings(
            f"{row}\n", "",
        )
        stage_rulings(
            f"**The ruling registry stands at {actual} rows.**",
            f"**The ruling registry stands at {actual - 1} rows.**",
        )
        assert any("balance rulings and carries" in p
                   for p in rulings.migration_violations())

    def test_a_moved_arc_that_lost_its_pointer_is_caught(self, tmp_path,
                                                        monkeypatch):
        """A section heading with no decisions under it and no forwarding address."""
        source = rulings.ARC_DOCS["balance"]
        target = tmp_path / "balance.md"
        target.write_text(source.read_text().replace("rulings.md", "elsewhere"))
        monkeypatch.setitem(rulings.ARC_DOCS, "balance", target)
        assert any("does not name rulings.md" in p
                   for p in rulings.migration_violations())


class TestThePreambleDoesNotDecay:
    """Rule 3 over the preamble, now that every arc has moved.

    This class used to grade a parenthesised count per UNMIGRATED arc and a
    total derived from them -- derived values beside no reconciler, which is
    the root cause three of these arcs exist to remove, and they shipped that
    way in the first draft.  ``X-ao-2`` deleted the sentence and the arms with
    it: with no arc left unmoved there is nothing to count, and what remains
    is the per-arc declaration, which :class:`TestTheLiftLostNothing` grades.
    """

    def test_the_unmigrated_count_machinery_is_gone(self):
        """A sentence with no subject is deleted, not left reading zero."""
        for name in ("UNMIGRATED_RX", "UNMIGRATED_TOTAL_RX",
                     "unmigrated_arc_counts", "unmigrated_count_violations"):
            assert not hasattr(rulings, name), name
        assert "need ids MINTED" not in rulings.RULINGS.read_text()

    def test_the_declaration_survives_a_line_break(self, stage_rulings):
        """A formatter may wrap between an arc name and its count.

        The mechanism, graded directly rather than through its effect: the
        pattern reads whitespace, so re-flowing the paragraph cannot silence
        it.  That is not hypothetical -- `rumdl fmt` re-wrapped this preamble
        once and put a newline between an arc name and its count, and the arm
        that read it went silently blind rather than red.
        """
        declared = _declaration()
        assert "`credit_card` 12" in declared
        stage_rulings(declared, declared.replace("`credit_card` 12",
                                                 "`credit_card`\n12"))
        assert rulings.declared_arc_counts().get("credit_card") == 12
        assert not rulings.migration_violations()
