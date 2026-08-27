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
    """Return the preamble's per-arc count sentence, verbatim.

    DERIVED rather than spelled, so a control's anchor cannot go stale the
    next time a ruling is filed -- three controls needed hand-editing when
    ``balance:R-GZ`` landed, which is the friction that turns a staged
    control into a literal nobody re-reads.

    Returns:
        The ```arc` N, `arc` N`` fragment the preamble states.
    """
    counts = rulings.declared_arc_counts()
    return ", ".join(f"`{arc}` {n}" for arc, n in counts.items())


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
        stage_rulings(declared, f"{declared}, `recurrence` 0")
        assert any("carries no recurrence row" in p
                   for p in rulings.migration_violations())

    def test_an_arc_that_has_not_moved_still_states_its_rulings(self):
        """The three arcs X-ao-2 lifts each still carry their own table."""
        assert set(rulings.ARC_RULING_HEADINGS) == {
            "recurrence", "pay_calendar", "credit_card",
        }
        assert not rulings.migration_violations()

    @pytest.mark.parametrize("arc", sorted(rulings.ARC_RULING_HEADINGS))
    def test_an_unmoved_arc_losing_its_heading_is_caught(self, arc, tmp_path,
                                                        monkeypatch):
        """An arc whose rulings would then be recorded nowhere."""
        source = rulings.ARC_DOCS[arc]
        heading = rulings.ARC_RULING_HEADINGS[arc]
        target = tmp_path / f"{arc}.md"
        target.write_text(source.read_text().replace(heading, "## Something else"))
        monkeypatch.setitem(rulings.ARC_DOCS, arc, target)
        assert any("recorded nowhere" in p for p in rulings.migration_violations())

    @pytest.mark.parametrize("arc", ["balance", "bank_import"])
    def test_a_moved_arc_keeping_its_table_is_caught(self, arc, tmp_path,
                                                    monkeypatch):
        """Two copies of one registry is what this move removes."""
        source = rulings.ARC_DOCS[arc]
        target = tmp_path / f"{arc}.md"
        target.write_text(
            source.read_text() + "\n| ruling | date | what was ruled |\n"
        )
        monkeypatch.setitem(rulings.ARC_DOCS, arc, target)
        assert any("table -- two copies of one registry" in p
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

    def test_an_arc_named_by_neither_side_is_caught(self, monkeypatch):
        """The set-defined-by-omission shape, which this corpus has paid for.

        An arc dropped from ``ARC_RULING_HEADINGS`` without being lifted is
        named by no declaration and no map, so every other arm passes over it.
        """
        monkeypatch.delitem(rulings.ARC_RULING_HEADINGS, "credit_card")
        assert any("neither declared moved nor listed" in p
                   for p in rulings.migration_violations())

    def test_a_moved_arc_left_in_the_not_moved_map_is_caught(self, monkeypatch):
        """An entry that outlives the table it points at."""
        monkeypatch.setitem(rulings.ARC_RULING_HEADINGS, "balance", "## 4.")
        assert any("still has an entry in ARC_RULING_HEADINGS" in p
                   for p in rulings.migration_violations())


class TestRuleFourAppliesToThisFileToo:
    """The per-ROW cap, which is the whole of rule 4 here."""

    def test_the_only_rows_over_the_cap_are_the_lifted_debt(self):
        """The debt is COUNTED, so a new over-cap row is distinguishable.

        These 23 rows were over the cap in the documents that held them and
        were lifted verbatim; rule 5 forbids trimming a live specification to
        fit, and rule 4's own remedy sends the overflow to the owning step's
        specification, which is ``X-ao-2``.  Recording the number rather than
        exempting the rows is what keeps a NEW over-cap row a failure.
        """
        assert len(rulings.row_width_violations()) == rulings.LIFTED_ROWS_OVER_CAP

    def test_the_cap_is_the_ledger_s_own_number(self):
        """Not a number fitted to today's file, which rule 4 forbids."""
        assert rulings.RULINGS_ROW_CAP == registry.LEDGER_ROW_CAP

    def test_a_new_row_over_the_cap_is_caught(self, stage_rulings):
        """The debt count is a floor to compare against, never a licence."""
        row = _a_row_of("balance")
        stage_rulings(row, _with_cell(row, 4, "x" * (rulings.RULINGS_ROW_CAP + 1)))
        assert len(rulings.row_width_violations()) == rulings.LIFTED_ROWS_OVER_CAP + 1

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

    def test_a_declared_arc_that_kept_its_own_table_is_caught(self, tmp_path,
                                                             monkeypatch):
        """Every grammar, not only the one the migrated arcs used.

        The first draft matched ``| ruling | date |`` -- the spelling of the
        two arcs that had ALREADY moved and no longer have a table -- so it
        was blind to exactly the three arcs X-ao-2 must protect.
        """
        source = rulings.ARC_DOCS["balance"]
        target = tmp_path / "balance.md"
        target.write_text(source.read_text() + "\n| fork | ruling |\n|---|---|\n")
        monkeypatch.setitem(rulings.ARC_DOCS, "balance", target)
        assert any("still carries a '| fork | ruling |' table" in p
                   for p in rulings.migration_violations())

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
