"""The gate over the shared planning registries.

Grades ``docs/plans/ledger.md``, ``docs/plans/steps.md`` and
``docs/plans/conventions.md`` against the rules those files state, including
the four CROSS-ARC rules no per-document gate could express:

* **rule 10** -- ``(arc, id)`` is the key, and it is unique across the corpus;
* **rule 11** -- an identity class shares ONE tick state (``C2`` == ``X-l`` ==
  ``R-F12``), and an UNRULED FORK refuses a tick on either competing remedy;
* **rule 12** -- ``steps.md`` and the arc documents agree in BOTH directions.

**Every predicate here has a negative control that is shown to fire**, because
a gate that cannot be made to fail has not been shown to grade anything.  Each
control mutates a COPY of the real registry and points the module at it, so
the control exercises the same parser on the same shapes the live files use --
a synthetic fixture would prove only that the parser reads what it wrote.
"""
from __future__ import annotations

import re

import pytest

import _registry as registry


@pytest.fixture(name="stage")
def _stage(tmp_path, monkeypatch):
    """Return a helper that mutates a registry copy and re-points the module."""

    def _apply(which: str, old: str, new: str) -> None:
        source = {"ledger": registry.LEDGER, "steps": registry.STEPS}[which]
        text = source.read_text()
        assert old in text, f"control anchor {old!r} is not in the real {which}"
        target = tmp_path / source.name
        target.write_text(text.replace(old, new, 1))
        monkeypatch.setattr(registry, which.upper(), target)

    return _apply


@pytest.fixture(name="stage_arc")
def _stage_arc(tmp_path, monkeypatch):
    """Return a helper that mutates an ARC DOCUMENT copy and re-points the map.

    Separate from ``stage`` because ``ARC_DOCS`` is a dict rather than a module
    attribute, and because the defects it plants are in the SPECIFICATIONS
    rather than in a registry table.
    """

    def _apply(arc: str, old: str, new: str) -> None:
        source = registry.ARC_DOCS[arc]
        text = source.read_text()
        assert old in text, f"control anchor {old!r} is not in the real {arc} doc"
        target = tmp_path / source.name
        target.write_text(text.replace(old, new, 1))
        monkeypatch.setitem(registry.ARC_DOCS, arc, target)

    return _apply


def _row(which: str, prefix: str) -> str:
    """Return the one live row of *which* registry starting with *prefix*."""
    source = {"ledger": registry.LEDGER, "steps": registry.STEPS}[which]
    matches = [ln for ln in source.read_text().splitlines()
               if ln.startswith(prefix)]
    assert len(matches) == 1, f"{prefix!r} matched {len(matches)} rows, expected 1"
    return matches[0]


def _with_cell(line: str, index: int, value: str) -> str:
    """Return *line* with cell *index* replaced, KEEPING the column count.

    Rebuilding from cells rather than string-replacing the old value: a
    mutation that appends a cell makes the row the wrong WIDTH, the parser
    skips it as malformed, and the control then passes for the wrong reason --
    which is how the first version of the shipped-owner control below reported
    a clean corpus while proving nothing.

    **The delimiting pipes are stripped before the split and restored after.**
    Splitting the raw line on ``" | "`` leaves the border pipes attached to the
    first and last cells, so ``index=-1`` addresses ``"X-l |"`` and ``-2``
    addresses the STATUS column -- the second way these controls passed while
    mutating the wrong field.  A cell's own escaped ``\\|`` is not a delimiter
    and survives, because ``" \\| "`` does not contain ``" | "``.
    """
    body = line.strip()
    assert body.startswith("| ") and body.endswith(" |"), line
    cells = body[2:-2].split(" | ")
    cells[index] = value
    return "| " + " | ".join(cells) + " |"


class TestThePremiseThatAnythingIsBeingRead:
    """A gate reporting a clean corpus it never opened is the failure mode."""

    def test_the_registries_exist_and_are_populated(self):
        """The registries exist and are populated."""
        assert registry.LEDGER.exists(), "docs/plans/ledger.md is missing"
        assert registry.STEPS.exists(), "docs/plans/steps.md is missing"
        assert registry.CONVENTIONS.exists(), "docs/plans/conventions.md is missing"
        # Floors far under the live counts (138 findings, 92 steps), so they
        # catch a parser that has stopped seeing rows without becoming a second
        # place the true numbers are written down.
        assert len(registry.ledger_rows()) >= 100
        assert len(registry.step_rows()) >= 70
        assert len(registry.forks()) >= 1

    def test_every_arc_document_is_present(self):
        """Every arc document is present."""
        for arc, path in registry.ARC_DOCS.items():
            assert path.exists(), f"{arc} document missing at {path}"
            assert registry.arc_checkboxes(arc), f"{arc} document has no checkboxes"


class TestTheLedgerStatesItsOwnSize:
    """conventions.md rule 3."""

    def test_the_stated_count_matches_the_table(self):
        """The stated count matches the table."""
        assert registry.stated_count_violation() is None

    def test_the_control_fires_on_a_wrong_count(self, stage):
        """The control fires on a wrong count."""
        actual = len(registry.ledger_rows())
        stage("ledger", f"**The ledger stands at {actual} rows.**",
              f"**The ledger stands at {actual - 7} rows.**")
        problem = registry.stated_count_violation()
        assert problem is not None and "rule 3" in problem


class TestEveryFindingNamesALiveOwner:
    """conventions.md rules 1 and 2."""

    def test_no_row_is_unowned_or_owned_by_a_shipped_step(self):
        """No row is unowned or owned by a shipped step."""
        assert not registry.owner_violations()

    def test_the_control_fires_on_an_empty_owner(self, stage):
        """The control fires on an empty owner."""
        line = _row("ledger", "| balance | N-128 |")
        stage("ledger", line, _with_cell(line, -1, ""))
        problems = registry.owner_violations()
        assert any("empty owner" in p for p in problems), problems

    def test_the_control_fires_on_an_owner_naming_no_step(self, stage):
        """The control fires on an owner naming no step."""
        line = _row("ledger", "| balance | N-128 |")
        stage("ledger", line, _with_cell(line, -1, "X-nonexistent"))
        problems = registry.owner_violations()
        assert any("names no step" in p for p in problems), problems

    def test_the_control_fires_on_an_owner_that_has_shipped(self, stage):
        """pay_calendar:C1 is SHIPPED, so a live row may not point at it."""
        assert registry.arc_checkboxes("pay_calendar")["C1"], "C1 must be ticked"
        line = _row("ledger", "| pay_calendar | P2 |")
        stage("ledger", line, _with_cell(line, -1, "C1"))
        problems = registry.owner_violations()
        assert any("SHIPPED" in p and "rule 2" in p for p in problems), problems

    def test_the_control_fires_on_a_prose_owner(self, stage):
        """"Own commit", "folded in" and their siblings all mean nobody.

        The grammar arm.  Its control lived against the deleted per-document
        parser; the behaviour lives here, so the control does too.
        """
        line = _row("ledger", "| balance | N-128 |")
        stage("ledger", line, _with_cell(line, -1, "own commit"))
        problems = registry.owner_violations()
        assert any("owner grammar" in p for p in problems), problems

    def test_the_control_fires_on_one_bad_half_of_a_two_owner_cell(self, stage):
        """BOTH halves of ``A / B`` must be live, not just the first.

        The row is real: ``balance:N-33`` is owned by ``E2-0 / E2-n (R-AO)``
        because its two halves close at different levels.  A gate that stopped
        at the first half would grade the commoner cell and miss the other.
        """
        line = _row("ledger", "| balance | N-33 |")
        assert " / " in line, "the anchor row no longer has a two-owner cell"
        stage("ledger", line, _with_cell(line, -1, "E2-0 / X-nonexistent"))
        problems = registry.owner_violations()
        assert any("X-nonexistent" in p and "names no step" in p for p in problems), (
            problems
        )

    def test_the_control_fires_on_a_bare_vocabulary_word(self, stage):
        """``operator`` states its question and ``developer-decision`` is dated.

        Both halves are what make the value an answer rather than a shrug.  A
        bare ``operator`` is indistinguishable from the "someone will get to
        it" values rule 1 retired by name.
        """
        line = _row("ledger", "| recurrence | F-4 |")
        stage("ledger", line, _with_cell(line, -1, "operator"))
        problems = registry.owner_violations()
        assert any("must state the question" in p for p in problems), problems

    def test_the_control_fires_on_an_undated_developer_decision(self, stage):
        """A fork with no date cannot be told from a fork nobody has taken."""
        line = _row("ledger", "| balance | N-25 |")
        stage("ledger", line, _with_cell(line, -1, "developer-decision (the fork)"))
        problems = registry.owner_violations()
        assert any("must carry the date" in p for p in problems), problems


class TestTheKeyIsTheArcAndTheId:
    """conventions.md rule 10 -- the D4 problem, as a predicate."""

    def test_no_duplicate_keys(self):
        """No duplicate keys."""
        assert not registry.unique_key_violations()

    def test_the_control_fires_on_a_duplicated_key(self, stage):
        """The control fires on a duplicated key."""
        line = _row("ledger", "| balance | N-128 |")
        stage("ledger", line, line + "\n" + line)
        problems = registry.unique_key_violations()
        assert any("duplicate key" in p for p in problems), problems

    def test_the_control_fires_on_an_empty_id_cell(self, stage):
        """A row whose id is blank has no key, and nothing else could see it.

        **This is the one silent hole the other arms do not backstop.**  An
        empty ARC cell is the FIRST cell, so ``_rows`` drops the row, the table
        shrinks and rule 3's count arm reports it (the control below).  An
        empty ID is the SECOND cell: the row parses, the count still agrees,
        and the key silently becomes ``balance:`` -- every later predicate then
        grades a finding that has no name.  Measured before this arm existed:
        138 rows in, 138 rows out, every arm SILENT.
        """
        line = _row("ledger", "| balance | N-128 |")
        stage("ledger", line, _with_cell(line, 1, ""))
        problems = registry.unique_key_violations()
        assert any("empty arc or id" in p for p in problems), problems

    def test_an_empty_arc_cell_is_caught_by_the_count_arm(self, stage):
        """The row vanishes instead, so rule 3 is what reports it.

        Recorded as a control rather than as prose because the two empty-cell
        cases fail through DIFFERENT arms, and a reader who assumes one arm
        covers both would delete the wrong one.
        """
        before = len(registry.ledger_rows())
        line = _row("ledger", "| balance | N-128 |")
        stage("ledger", line, _with_cell(line, 0, ""))
        assert len(registry.ledger_rows()) == before - 1, "the row must vanish"
        problem = registry.stated_count_violation()
        assert problem is not None and "rule 3" in problem, problem


class TestAnIdentityClassSharesOneTickState:
    """conventions.md rule 11, first half -- C2 == X-l == R-F12."""

    def test_no_alias_class_is_half_ticked(self):
        """No alias class is half ticked."""
        assert not registry.alias_violations()

    def test_the_live_corpus_actually_contains_the_class_this_rule_exists_for(self):
        """A rule with no subject in the corpus is untested by the clean case."""
        keys = {row.key: row.alias_keys() for row in registry.step_rows()}
        assert "balance:X-l" in keys["pay_calendar:C2"]
        assert "recurrence:R-F12" in keys["pay_calendar:C2"]

    def test_the_control_fires_when_one_name_ships_without_the_others(self, stage):
        """The control fires when one name ships without the others."""
        line = _row("steps", "| pay_calendar | C2 |")
        stage("steps", line, _with_cell(line, 4, "SHIPPED"))
        problems = registry.alias_violations()
        assert any("ONE step under two names" in p for p in problems), problems


class TestAnUnruledForkRefusesBothRemedies:
    """conventions.md rule 11, second half -- the P3 / N-123 collision."""

    def test_no_unruled_fork_has_a_shipped_remedy(self):
        """No unruled fork has a shipped remedy."""
        assert not registry.fork_violations()

    def test_the_live_corpus_contains_an_unruled_fork(self):
        """The live corpus contains an unruled fork."""
        unruled = [f for f in registry.forks() if not f.is_ruled]
        assert unruled, "the rule has no subject, so the clean case proves nothing"
        assert any("balance:X-ad" in f.remedy_keys() for f in unruled)

    def test_the_control_fires_when_a_remedy_ships_before_the_ruling(self, stage):
        """The control fires when a remedy ships before the ruling."""
        line = _row("steps", "| balance | X-ad |")
        stage("steps", line, _with_cell(line, 4, "SHIPPED"))
        problems = registry.fork_violations()
        assert any("NOT YET RULED" in p for p in problems), problems


class TestTheIndexAndTheSpecificationsAgree:
    """conventions.md rule 12 -- both directions."""

    def test_every_indexed_step_has_a_specification_and_the_reverse(self):
        """Every indexed step has a specification and the reverse."""
        assert not registry.index_agreement_violations()

    def test_the_control_fires_on_an_index_row_with_no_specification(self, stage):
        """The control fires on an index row with no specification."""
        line = _row("steps", "| pay_calendar | C6 |")
        stage("steps", line, _with_cell(line, 1, "C99"))
        problems = registry.index_agreement_violations()
        # BOTH directions must fire: C99 is indexed with no specification, and
        # C6 is specified with no index row.
        assert any("C99" in p and "NO specification" in p for p in problems), problems
        assert any("C6" in p and "NOT" in p for p in problems), problems

    def test_the_control_fires_on_a_tick_state_that_disagrees(self, stage):
        """The control fires on a tick state that disagrees."""
        line = _row("steps", "| pay_calendar | C1 |")
        stage("steps", line, _with_cell(line, 4, "open"))
        problems = registry.index_agreement_violations()
        assert any("C1" in p and "ticked" in p for p in problems), problems


class TestTheParserSurvivesTheShapesTheRealFilesUse:
    """The measured false positives, kept as controls rather than as prose."""

    def test_an_escaped_pipe_does_not_split_a_row(self):
        """The balance N-73 row carries a literal ``Decimal \\| None``."""
        rows = {row.key: row for row in registry.ledger_rows()}
        assert "balance:N-73" not in rows or "|" in rows["balance:N-73"].finding

    def test_an_unescaped_pipe_does_not_pass_silently(self, stage):
        """The false-negative direction of the arm above.

        ``_rows`` SKIPS a row of the wrong width rather than asserting on it,
        which is what lets one table hold two shapes.  The row therefore
        vanishes, and rule 3's count is the only thing standing between that
        and a finding nobody sees again.
        """
        before = len(registry.ledger_rows())
        line = _row("ledger", "| balance | N-128 |")
        stage("ledger", line, _with_cell(line, 3, "an unescaped X | Y pipe"))
        assert len(registry.ledger_rows()) == before - 1, "the row must vanish"
        problem = registry.stated_count_violation()
        assert problem is not None and "rule 3" in problem, problem

    def test_a_fenced_heading_does_not_truncate_a_checkbox_scan(self):
        """A ``##`` inside a fence must not end the steps scan."""
        # credit_card's steps section contains fenced blocks; if fencing were
        # mishandled the scan would stop early and lose its later phases.
        assert "CC5b" in registry.arc_checkboxes("credit_card")

    def test_a_duplicate_checkbox_does_not_silently_un_tick_a_shipped_step(
        self, stage_arc,
    ):
        """The LAST checkbox wins, so re-listing a shipped step un-ticks it.

        These documents re-parent and re-list steps routinely, so the collision
        is one edit away.  ``arc_checkboxes`` does not refuse the duplicate --
        **rule 12 catches it instead**, because the document then disagrees
        with ``steps.md`` about a step that has SHIPPED.  Recorded as a control
        because "another arm covers it" is a claim, and an uncontrolled claim
        about a gate is how this corpus got here.
        """
        assert registry.arc_checkboxes("pay_calendar")["C1"] is True
        line = [
            ln for ln in registry.ARC_DOCS["pay_calendar"].read_text().splitlines()
            if ln.startswith("- [x] **C1 ")
        ]
        assert len(line) == 1, f"C1's entry matched {len(line)} lines, expected 1"

        stage_arc(
            "pay_calendar",
            line[0],
            line[0] + "\n- [ ] **C1** re-listed in a later summary",
        )
        assert registry.arc_checkboxes("pay_calendar")["C1"] is False, (
            "the premise: the last checkbox wins, so the shipped step reads unticked"
        )
        problems = registry.index_agreement_violations()
        assert any("C1" in p and "SHIPPED in steps.md" in p for p in problems), problems

    def test_conventions_still_states_every_rule_the_gate_cites(self):
        """A rule cited in a failure message must exist in the file it cites."""
        text = registry.CONVENTIONS.read_text()
        numbered = re.findall(r"^(\d{1,2})\. \*\*", text, re.MULTILINE)
        assert [int(n) for n in numbered] == list(range(1, 13)), (
            f"conventions.md numbers its rules {numbered}, expected 1..12"
        )
