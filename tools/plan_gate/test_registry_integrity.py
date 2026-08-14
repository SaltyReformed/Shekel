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

import pathlib
import re

import pytest

import _registry as registry


def _a_live_fork():
    """Return the first live fork, its ``steps.md`` line, and a remedy's line.

    **The fork controls below used to NAME a specimen** --
    ``| pay_calendar:P3 = balance:N-123 |`` and ``| balance | N-123 |`` -- and
    that broke on 2026-08-10, when ``balance:X-ad-a`` shipped the remedy that
    fork was ruled to, its defect row closed, and the fork line left the table.
    Three controls went red for the single reason that the rule they grade had
    WORKED.  A control anchored to one corpus row rots exactly like the prose
    counts these registries exist to remove, so the specimen is now derived:
    whichever fork is live gets graded, and the next one to be settled costs
    this file nothing.

    Returns:
        ``(fork, fork line, one remedy's step line)``.
    """
    found = registry.forks()
    assert found, "no fork in steps.md -- the controls below have no subject"
    fork = found[0]
    line = _row("steps", f"| {fork.defect} |")
    arc, ident = fork.remedy_keys()[0].split(":", 1)
    return fork, line, _row("steps", f"| {arc} | {ident} |")


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
        # Floors far under the live counts, so they
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


class TestStepsStatesItsOwnSize:
    """conventions.md rule 3, on the registry it was not being applied to.

    **All FOUR of these numbers were stale when this class was written**
    (2026-08-11): the header said "112 steps, 96 open" against 113 and 95, and
    the graph line said 93 edges over 58 rows against 94 and 59.  Rule 3 was
    written about `ledger.md` and graded only there, so its sibling carried
    exactly the defect the rule describes -- and it went wrong in BOTH
    directions inside one merge, because one session appended a step while
    another ticked one.

    Each arm is separate because each goes stale for a different reason: the
    size on an append, the open count on a tick, the graph on a new
    ``blocked by`` edge.
    """

    def test_the_stated_counts_match_the_table(self):
        """The stated counts match the table."""
        assert not registry.steps_stated_count_violation()

    def test_the_control_fires_on_a_wrong_step_count(self, stage):
        """The control fires on a wrong step count."""
        rows = registry.step_rows()
        opened = sum(1 for row in rows if not row.shipped)
        stage("steps", f"**{len(rows)} steps, {opened} open.**",
              f"**{len(rows) - 7} steps, {opened} open.**")
        problems = registry.steps_stated_count_violation()
        assert any("holds 113 steps" in p or "steps and the table holds" in p
                   for p in problems), problems

    def test_the_control_fires_on_a_wrong_open_count(self, stage):
        """The control fires on a wrong open count.

        Its own arm because a tick moves this number and not the size, so a
        single count arm would report clean over the edit that ships a step.
        """
        rows = registry.step_rows()
        opened = sum(1 for row in rows if not row.shipped)
        stage("steps", f"**{len(rows)} steps, {opened} open.**",
              f"**{len(rows)} steps, {opened - 3} open.**")
        problems = registry.steps_stated_count_violation()
        assert any("are open and" in p for p in problems), problems

    def test_the_control_fires_on_a_wrong_graph_size(self, stage):
        """The control fires on a wrong ``blocked by`` graph size."""
        rows = registry.step_rows()
        edges = sum(len(row.blocked_keys()) for row in rows)
        carriers = sum(1 for row in rows if row.blocked_keys())
        stage("steps", f"holds {edges} edges over {carriers} rows",
              f"holds {edges - 4} edges over {carriers} rows")
        problems = registry.steps_stated_count_violation()
        assert any("edges and it holds" in p for p in problems), problems

    def test_the_pattern_still_matches_the_live_document(self):
        """Rule 3's own failure mode: a pattern that matches nothing passes.

        The balance README's row-count arm shipped requiring ``rows**`` while
        the document wrote ``rows.**``, so it matched NOTHING, read that as "no
        count is claimed" and passed a planted 38-against-41.  These two arms
        assert their patterns still find something in the REAL file, so the
        same silence cannot happen here.
        """
        text = registry.STEPS.read_text()
        assert registry.STEPS_COUNT_RX.search(text) is not None
        assert registry.BLOCKED_GRAPH_RX.search(text) is not None


class TestEveryFindingNamesALiveOwner:
    """conventions.md rules 1 and 2."""

    def test_no_row_is_unowned_or_owned_by_a_shipped_step(self):
        """No row is unowned or owned by a shipped step."""
        assert not registry.owner_violations()

    def test_the_control_fires_on_an_empty_owner(self, stage):
        """The control fires on an empty owner."""
        line = _row("ledger", "| balance | N-96 |")
        stage("ledger", line, _with_cell(line, -1, ""))
        problems = registry.owner_violations()
        assert any("empty owner" in p for p in problems), problems

    def test_the_control_fires_on_an_owner_naming_no_step(self, stage):
        """The control fires on an owner naming no step."""
        line = _row("ledger", "| balance | N-96 |")
        stage("ledger", line, _with_cell(line, -1, "X-nonexistent"))
        problems = registry.owner_violations()
        assert any("names no step" in p for p in problems), problems

    def test_the_control_fires_on_an_owner_that_has_shipped(self, stage):
        """pay_calendar:C1 is SHIPPED, so a live row may not point at it.

        **Every control here stages a row a shipping step can close, so they
        get re-anchored**: this one off ``pay_calendar:P2`` at C2-b2, nine off
        ``balance:N-128`` at C2-c.  Any live row with a BARE id cell serves.
        """
        assert registry.arc_checkboxes("pay_calendar")["C1"], "C1 must be ticked"
        line = _row("ledger", "| pay_calendar | P16 |")
        stage("ledger", line, _with_cell(line, -1, "C1"))
        problems = registry.owner_violations()
        assert any("SHIPPED" in p and "rule 2" in p for p in problems), problems

    def test_the_control_fires_on_a_prose_owner(self, stage):
        """"Own commit", "folded in" and their siblings all mean nobody.

        The grammar arm.  Its control lived against the deleted per-document
        parser; the behaviour lives here, so the control does too.
        """
        line = _row("ledger", "| balance | N-96 |")
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

    def test_the_control_fires_on_the_first_half_too(self, stage):
        """The mirror of the control above, and it is not decoration.

        Planting the defect only in the second half proves the gate does not
        stop at the first -- and passes just as happily if the gate grades ONLY
        the last. An adversarial review neutered ``owner_violations`` to
        ``split_owners(cell)[-1:]`` and all 79 tests stayed green, which would
        have hidden every defect in a first half: ``P2``'s ``C3 (the writer)``
        and ``N-33``'s ``E2-0``, in both cases the half that ships first.
        """
        line = _row("ledger", "| balance | N-33 |")
        stage("ledger", line, _with_cell(line, -1, "X-nonexistent / E2-n (R-AO)"))
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
        # Re-anchored 2026-08-13 off `balance:N-25`, archived to the loan
        # arc's as-built; a control that names its subject cannot go quiet.
        line = _row("ledger", "| balance | N-138 |")
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
        line = _row("ledger", "| balance | N-96 |")
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
        line = _row("ledger", "| balance | N-96 |")
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
        line = _row("ledger", "| balance | N-96 |")
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


class TestAForkBindsItsRemediesAndItsDefectRow:
    """conventions.md rule 11, second half -- born of the P3 / N-123 collision.

    **The controls STAGE an unruled fork rather than requiring the live corpus
    to hold one.**  Every live fork has been ruled, so a control that asserted
    "an unruled fork exists" would now be red -- and the tempting way to green
    it is to relax the assertion, which is how a predicate quietly stops being
    tested.  Staging the state proves the arm whether or not the developer
    happens to have an open fork today.

    **And the specimen is DERIVED, not named** (:func:`_a_live_fork`): naming
    one made three of these controls fail on 2026-08-10 for the single reason
    that the rule had worked and the fork had left the table.
    """

    def test_no_fork_is_violated_in_the_live_corpus(self):
        """No fork has a premature tick, a dead defect row, or a stale owner."""
        assert not registry.fork_violations()

    def test_the_live_corpus_actually_contains_forks_to_grade(self):
        """A rule with no subject in the corpus is untested by the clean case."""
        found = registry.forks()
        assert found, "no fork at all -- rule 11's second half grades nothing"
        assert all(f.winner for f in found), (
            "every live fork is expected to be RULED as of 2026-08-09"
        )

    def test_the_control_fires_when_a_remedy_ships_before_the_ruling(self, stage):
        """Whichever remedy ships first decides for both arcs."""
        _, ruling, remedy = _a_live_fork()
        stage("steps", ruling, _with_cell(ruling, -1, "**NOT YET RULED**"))
        stage("steps", remedy, _with_cell(remedy, 4, "SHIPPED"))
        problems = registry.fork_violations()
        assert any("NOT YET RULED" in p for p in problems), problems

    @pytest.mark.parametrize("word", ["TBD", "pending", "?", "not yet ruled"])
    def test_a_non_ruling_word_does_not_count_as_a_ruling(self, stage, word):
        """Only NAMING a remedy is a ruling.

        The predicate used to read "is the cell non-empty and not the exact
        phrase NOT YET RULED", so every one of these words made ``is_ruled``
        True -- and a True makes the whole fork arm skip.  This is the rule
        that exists BECAUSE P3 / N-123 went unnoticed from April to 2026-08-09.
        """
        _, ruling, remedy = _a_live_fork()
        stage("steps", ruling, _with_cell(ruling, -1, word))
        stage("steps", remedy, _with_cell(remedy, 4, "SHIPPED"))
        problems = registry.fork_violations()
        assert any("NOT YET RULED" in p for p in problems), (word, problems)

    def test_the_control_fires_when_a_ruled_fork_leaves_its_row_unpointed(self, stage):
        """A ruling nobody re-points is a ruling that decided nothing.

        Rule 2 re-points a row when its owner ships, but it only fires on a row
        that NAMES a step -- and an open fork's row names ``developer-decision``
        by design.  Without this arm the row could keep pointing at a decision
        already taken, indefinitely, with every other gate green.
        """
        fork, _, _ = _a_live_fork()
        arc, ident = fork.defect_keys()[0].split(":", 1)
        line = _row("ledger", f"| {arc} | {ident} |")
        stage("ledger", line, _with_cell(line, -1, "developer-decision (2026-08-09)"))
        problems = registry.fork_violations()
        assert any(f"RULED for {fork.winner}" in p for p in problems), problems

    def test_the_control_fires_when_the_defect_names_no_live_row(self, stage):
        """A fork about a row that does not exist decides nothing."""
        _, ruling, _ = _a_live_fork()
        stage("steps", ruling, _with_cell(ruling, 0, "pay_calendar:P999"))
        problems = registry.fork_violations()
        assert any("names no live ledger.md row" in p for p in problems), problems


class TestTheTwoAlsoRelationsMeanOppositeThings:
    """`= arc:id` was MERGED into this row; `~ arc:id` must NOT be merged.

    Both registries give this distinction a section headed "why conflating them
    deletes work", and until an adversarial review asked, no predicate read the
    column at all -- rewriting every `=` as `~` changed nothing anywhere.
    """

    def test_every_relation_resolves_the_right_way(self):
        """Every relation resolves the right way."""
        assert not registry.also_violations()

    def test_the_live_corpus_contains_both_relations(self):
        """Neither half of the arm is vacuous."""
        cells = " ".join(row.also for row in registry.ledger_rows())
        assert "= " in cells and "~ " in cells

    def test_the_control_fires_when_a_merged_target_is_still_live(self, stage):
        """`=` says the target was absorbed, so it must not still be a row.

        Named ``pay_calendar:P2`` until that row closed at ``C2-b2``; the
        target only has to be a row that still EXISTS and whose id cell is
        BARE, since the `also` grammar takes an unannotated id.
        """
        line = _row("ledger", "| balance | N-96 |")
        stage("ledger", line, _with_cell(line, 2, "= pay_calendar:P16"))
        problems = registry.also_violations()
        assert any("still its own live row" in p for p in problems), problems

    def test_the_control_fires_on_a_distinct_relation_naming_nothing(self, stage):
        """`~` says the target is a live, DISTINCT finding, so it must exist.

        The live instance: after the 2026-08-09 merges N-128's cell still
        named `recurrence:F-10`, a row that no longer existed.
        """
        line = _row("ledger", "| balance | N-96 |")
        stage("ledger", line, _with_cell(line, 2, "~ recurrence:F-10"))
        problems = registry.also_violations()
        assert any("names no live row" in p for p in problems), problems


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


class TestTheBlockedByColumnIsTheDependencyGraph:
    """conventions.md rule 13 -- the column that was parsed and never read.

    Until this class existed ``StepRow.blocked`` was populated by hand, carried
    through the parser and consulted by nothing, so an edge could name a
    deleted step, contradict itself or close a loop with every gate green.  The
    contradiction that proves it was found by BUILDING: ``steps.md`` recorded
    ``R6 blocked by balance:X-an`` while the recurrence document derived ``R6``
    from a column ``R5`` creates behind ``X-f4``.
    """

    def test_the_graph_is_referentially_sound_acyclic_and_alias_coherent(self):
        """The live dependency graph satisfies all five arms."""
        assert not registry.blocked_by_violations()

    def test_the_live_corpus_actually_contains_edges_to_grade(self):
        """A rule with no subject in the corpus is untested by the clean case."""
        edges = [row for row in registry.step_rows() if row.blocked_keys()]
        assert edges, "no step carries a blocker -- rule 13 grades nothing"

    def test_an_annotated_blocker_parses_to_its_key(self):
        """``CC3b`` carries a real annotation, and the key is parsed OUT of it.

        A naive reader would take the whole cell as the key and report the one
        row that documents WHY its blocker is already shipped as broken -- the
        false positive the shared ``aliases`` grammar was written against.

        **Asserted as a PROPERTY, not as that row's exact list.** The first
        version pinned ``== ["balance:X-f1"]`` and went red the moment the
        credit-card arc's own gate was added beside it -- pinning data the
        registry is expected to grow is a test that fails on correct edits,
        which is the kind that gets weakened rather than believed. The claim
        worth holding is that EVERY parsed key is bare.
        """
        by_key = {row.key: row for row in registry.step_rows()}
        annotated = by_key["credit_card:CC3b"]
        assert "(" in annotated.blocked, (
            "CC3b no longer carries an annotated blocker, so this control has "
            f"lost its subject: {annotated.blocked!r}"
        )
        assert "balance:X-f1" in annotated.blocked_keys()
        for row in registry.step_rows():
            for key in row.blocked_keys():
                assert "(" not in key and " " not in key, (
                    f"{row.key}: blocker {key!r} kept its annotation"
                )

    def test_the_control_fires_on_a_blocker_naming_no_step(self, stage):
        """The control fires on a blocker naming no step."""
        line = _row("steps", "| balance | X-x |")
        stage("steps", line, _with_cell(line, -1, "balance:X-gone"))
        problems = registry.blocked_by_violations()
        assert any("X-gone" in p and "no step" in p for p in problems), problems

    def test_the_control_fires_on_a_self_block(self, stage):
        """The control fires on a self-block.

        Checked before the referential arm on purpose: ``balance:X-x`` IS a
        real step, so a self-edge satisfies arm 2 and would otherwise pass.
        """
        line = _row("steps", "| balance | X-x |")
        stage("steps", line, _with_cell(line, -1, "balance:X-x"))
        problems = registry.blocked_by_violations()
        assert any("blocked by ITSELF" in p for p in problems), problems

    def test_the_control_fires_when_a_shipped_step_is_blocked_by_an_open_one(
        self, stage,
    ):
        """A SHIPPED step blocked by an OPEN one is one of two real defects."""
        line = _row("steps", "| balance | X-an |")
        stage("steps", line, _with_cell(line, -1, "balance:X-f3"))
        problems = registry.blocked_by_violations()
        assert any(
            "balance:X-an" in p and "stated prerequisite" in p for p in problems
        ), problems

    def test_the_control_fires_on_a_cycle(self, stage):
        """The control fires on a cycle.

        ``R5`` is already blocked by ``X-f4``; pointing ``X-f4`` back at ``R5``
        closes the loop across two arcs, which is the shape no single arc
        document could have seen.
        """
        line = _row("steps", "| balance | X-f4 |")
        stage("steps", line, _with_cell(line, -1, "recurrence:R5"))
        problems = registry.blocked_by_violations()
        assert any("CYCLE" in p for p in problems), problems

    def test_a_converging_edge_is_not_reported_as_a_cycle(self):
        """Two steps blocked by one third is a DIAMOND, not a loop.

        A two-colour visited set would report it as a cycle, and the live
        corpus already contains one -- ``X-k`` and ``R6`` are both blocked by
        ``R5`` -- so the naive detector would fail the clean case on day one.
        The three-colour walk distinguishes "on the current path" from "already
        finished", which is the whole reason for the GREY mark.
        """
        blockers = {
            row.key: row.blocked_keys() for row in registry.step_rows()
        }
        converging = [k for k, v in blockers.items() if "recurrence:R5" in v]
        assert len(converging) >= 2, (
            f"expected a converging edge in the live corpus, found {converging}"
        )
        assert not [
            p for p in registry.blocked_by_violations() if "CYCLE" in p
        ]

    def test_the_control_fires_when_one_name_of_a_class_carries_the_blocker(
        self, stage,
    ):
        """``C2`` == ``X-l`` == ``R-F12``: a blocker on one binds all three.

        This is the arm that matters most to a reader picking up work: without
        it, recording the blocker on ``C2`` alone leaves ``X-l`` and ``R-F12``
        reading as READY.
        """
        line = _row("steps", "| pay_calendar | C2 |")
        stage("steps", line, _with_cell(line, -1, "balance:X-f3"))
        problems = registry.blocked_by_violations()
        assert any("ONE blocker set" in p for p in problems), problems


class TestTheIndexNamesTheCommitThatShippedAStep:
    """conventions.md rule 7, the INDEX half.

    Rule 7's arc-document arm has always required a ticked entry to OPEN with
    its hash.  The ``steps.md`` ``commit`` column asked the same question and
    nothing read it: three of twelve SHIPPED rows held ``--`` while their own
    arc entries cited a hash.
    """

    def test_every_shipped_row_names_a_commit_and_no_open_row_does(self):
        """Every shipped row names a commit, and no open row does."""
        assert not registry.commit_column_violations()

    def test_the_live_corpus_actually_holds_shipped_rows_to_grade(self):
        """A rule with no subject in the corpus is untested by the clean case."""
        shipped = [row for row in registry.step_rows() if row.shipped]
        assert shipped, "no step has shipped -- this arm grades nothing"

    def test_the_control_fires_on_a_shipped_row_with_no_commit(self, stage):
        """The control fires on a shipped row with no commit."""
        line = _row("steps", "| balance | X-an |")
        stage("steps", line, _with_cell(line, -2, "--"))
        problems = registry.commit_column_violations()
        assert any("balance:X-an" in p and "SHIPPED" in p for p in problems), problems

    def test_the_control_fires_on_an_open_row_that_names_one(self, stage):
        """A step that has not shipped has no commit."""
        line = _row("steps", "| balance | X-f3 |")
        stage("steps", line, _with_cell(line, -2, "`deadbee1`"))
        problems = registry.commit_column_violations()
        assert any("balance:X-f3" in p and "has not shipped" in p
                   for p in problems), problems

    def test_a_non_hash_in_the_cell_is_refused(self, stage):
        """Prose in the commit cell is not a commit.

        The failure the ``--`` case cannot catch: a cell that is populated but
        names something that is not a hash.  ``PR #83`` identifies the same
        ship and is not a thing a reader can `git show`.
        """
        line = _row("steps", "| balance | X-an |")
        stage("steps", line, _with_cell(line, -2, "PR #83"))
        problems = registry.commit_column_violations()
        assert any("balance:X-an" in p for p in problems), problems


class TestAParentTicksWithTheLastOfItsLeaves:
    """conventions.md rule 13, the decomposition half.

    Rule 2 has always said a decomposed parent ticks with its last leaf, and
    nothing graded it.  The arm exists because the readiness question needs it:
    a container is not pickable work, and X-f, X-aj, X-i and X-x all read as
    READY while their own leaves are open.
    """

    def test_no_parent_has_shipped_ahead_of_an_open_leaf(self):
        """No declared parent has shipped ahead of an open leaf."""
        assert not registry.decomposition_violations()

    def test_the_live_corpus_actually_declares_parents_with_leaves(self):
        """A rule with no subject in the corpus is untested by the clean case."""
        rows = registry.step_rows()
        parents = [row for row in rows if row.is_decomposed_parent]
        assert parents, "no row declares itself a parent -- this arm grades nothing"
        withleaves = [
            p for p in parents
            if any(r.arc == p.arc and r.ident != p.ident
                   and r.ident.startswith(p.ident) for r in rows)
        ]
        assert withleaves, f"no declared parent has a leaf in the table: {parents}"

    def test_a_prefix_derivation_would_have_fired_falsely_on_this_corpus(self):
        """The reason the parent set is DECLARED rather than derived.

        ``R-F1`` is SHIPPED and is a string prefix of ``R-F10``, ``R-F12`` and
        ``R-F13``, which are unrelated findings-steps.  Deriving parenthood
        from the id alone would report failures the moment the arm was switched
        on -- and the tempting fix is an exception list, which is finding
        N-147's defect.  This control keeps that measurement alive: if the
        corpus ever stops containing the trap, the reason for the design should
        be re-read rather than assumed.

        **It asserts that at least one prefix-sharer is OPEN, not that all
        are.**  ``R-F10`` shipped at ``pay_calendar:C2-b2``, which turned an
        ``all`` assertion red without the trap having gone anywhere -- a
        control that fails when the corpus merely PROGRESSES is grading the
        wrong thing.  What matters is that a shipped step still shares a prefix
        with an open one, because that is the pair a derived arm would misread.
        """
        rows = {row.ident: row for row in registry.step_rows()
                if row.arc == "recurrence"}
        assert rows["R-F1"].shipped
        tempting = [i for i in rows if i != "R-F1" and i.startswith("R-F1")]
        assert tempting, "the R-F1 prefix trap has left the corpus"
        assert any(not rows[i].shipped for i in tempting), (
            "the trap needs an OPEN prefix-sharer beside the shipped R-F1"
        )
        assert not rows["R-F1"].is_decomposed_parent, (
            "R-F1 must NOT declare itself a parent -- it has no decomposition"
        )

    def test_the_control_fires_when_a_parent_ships_over_an_open_leaf(self, stage):
        """The control fires when a parent ships over an open leaf.

        **Nothing here is pinned to a name, and THREE versions died before
        that was true.**  The first anchored on ``X-an-b`` (archived out of the
        index under rule 5); the second pinned ``X-f2-a`` as an open leaf (it
        SHIPPED in the same merge); the third pinned ``X-f2`` as the PARENT to
        stage, and when ``X-f2-c3`` shipped that parent ticked legitimately --
        so the staged state was TRUE and the arm correctly reported nothing.
        Every time the arm was right and the control was wrong, which is the
        most expensive kind: it teaches a reader to edit the gate until a green
        change goes green.

        So the parent is CHOSEN rather than named -- any declared parent with an
        open leaf -- and the corpus is asserted to hold one so this cannot pass
        vacuously.  The stable claim: a shipped parent is named, and every leaf
        reported against it is genuinely open.
        """
        rows = registry.step_rows()
        by_key = {row.key: row for row in rows}
        candidates = sorted(
            parent.key for parent in rows
            if parent.is_decomposed_parent and not parent.shipped
            and any(
                not by_key[key].shipped
                for key in registry.decomposition_leaf_keys(parent, rows)
            )
        )
        assert candidates, (
            "no declared parent still has an open leaf, so this control has "
            "nothing to stage -- re-anchor it on a live decomposition"
        )
        parent_key = candidates[0]
        arc, ident = parent_key.split(":", 1)
        line = _row("steps", f"| {arc} | {ident} |")
        stage("steps", line, _with_cell(line, 4, "SHIPPED"))
        problems = [p for p in registry.decomposition_violations()
                    if parent_key in p]
        assert problems, registry.decomposition_violations()
        staged = {row.key: row for row in registry.step_rows()}
        named = {
            key for key in registry.decomposition_leaf_keys(
                staged[parent_key], registry.step_rows(),
            )
            if key.split(":", 1)[1] in problems[0]
        }
        assert named, f"the failure names no leaf: {problems}"
        assert not [key for key in named if staged[key].shipped], (
            f"a SHIPPED leaf was reported as open: {problems}"
        )

    def test_an_archived_leaf_set_is_not_a_violation(self):
        """Rule 5 archives a completed span, and rule 13 must not refuse it.

        **This is a LIVE corpus case, not a staged one.**  ``X-an`` is SHIPPED,
        declares itself a DECOMPOSED parent, and its two leaves ``X-an-a`` /
        ``X-an-b`` were condensed into the archive on 2026-08-09 -- so the
        index holds a shipped parent with no leaves at all.  An arm that
        required a declared parent to still HOLD leaves would put rules 5 and
        13 in contradiction, and it would fire on this row today.
        """
        rows = registry.step_rows()
        parent = next(r for r in rows if r.key == "balance:X-an")
        assert parent.shipped and parent.is_decomposed_parent
        assert not [r for r in rows
                    if r.arc == "balance" and r.ident.startswith("X-an-")], (
            "X-an's leaves are back in the index -- this control has lost its "
            "subject and the archive case needs a new one"
        )
        assert not [p for p in registry.decomposition_violations()
                    if "X-an" in p]

    def test_a_shipped_leaf_is_never_reported_as_open(self, stage):
        """``X-f1`` is SHIPPED; a parent over it must not name it.

        The other half of the arm: it reports the OPEN leaves and only those.
        Staging ``X-f`` to SHIPPED fires on ``X-f2``..``X-f6``, which are open,
        and must stay silent about ``X-f1``, which is not.
        """
        line = _row("steps", "| balance | X-f |")
        stage("steps", line, _with_cell(line, 4, "SHIPPED"))
        problems = registry.decomposition_violations()
        assert any("balance:X-f" in p for p in problems), problems
        assert not any("'X-f1'" in p for p in problems), (
            f"a SHIPPED leaf must not be reported as open: {problems}"
        )


    def test_a_parent_is_graded_over_its_whole_identity_class(self, stage):
        """A container's leaves may be filed under a SIBLING's name.

        **This arm re-spelled the leaf derivation inline, per-arc, until
        2026-08-11.**  ``balance:X-l``, ``pay_calendar:C2`` and
        ``recurrence:R-F12`` are ONE step under three names and every leaf of
        the class is a ``pay_calendar:C2-*`` row, so shipping ``X-l`` over five
        open leaves reported NOTHING: the arm looked for a `balance` row whose
        id starts with `X-l` and there is none.  The class row `C2` happened to
        cover it, which is why the hole was invisible -- one rename away from
        being live.
        """
        line = _row("steps", "| balance | X-l |")
        stage("steps", line, _with_cell(line, 4, "SHIPPED"))
        problems = [p for p in registry.decomposition_violations()
                    if "balance:X-l" in p]
        assert problems, registry.decomposition_violations()
        assert "pay_calendar:C2-" in problems[0], (
            f"the failure names no leaf of the class: {problems}"
        )


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
        line = _row("ledger", "| balance | N-96 |")
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
        """A rule cited in a failure message must exist in the file it cites.

        **This reads the citations, not a hard-coded count.**  It asserted only
        that ``conventions.md`` numbers its own rules 1..12, which an
        adversarial review showed is a different claim entirely: making a gate
        message cite a rule number in the forties passed, and adding a
        legitimate 13th rule failed while changing no citation.  Both
        directions were backwards -- blind to the failure it names, and loud
        about a non-failure.

        The scan covers this file too, so an EXAMPLE citation written here is a
        real one as far as the check is concerned.  That is the correct
        behaviour and not worth an exemption: a docstring that cites a rule is
        making the same promise a message does.
        """
        numbers = {
            int(n)
            for n in re.findall(
                r"^(\d{1,2})\. \*\*", registry.CONVENTIONS.read_text(), re.MULTILINE,
            )
        }
        assert numbers, "conventions.md numbers no rules at all"
        cited: dict[int, set[str]] = {}
        for module in sorted(pathlib.Path(__file__).parent.glob("*.py")):
            for found in re.findall(r"conventions\.md rule (\d{1,2})", module.read_text()):
                cited.setdefault(int(found), set()).add(module.name)
        assert cited, "no gate module cites a rule -- the messages name nothing"
        missing = {n: sorted(files) for n, files in cited.items() if n not in numbers}
        assert not missing, (
            f"the gate cites rules conventions.md does not state: {missing}. "
            f"conventions.md states {sorted(numbers)}"
        )


class TestEveryRegistryIsUnderItsCap:
    """conventions.md rule 4, on the five documents it did not used to reach."""

    @pytest.mark.parametrize("name", sorted(registry.REGISTRY_CAPS))
    def test_the_registry_is_within_its_line_cap(self, name):
        """The registry is within its line cap."""
        problems = [p for p in registry.registry_line_cap_violations()
                    if p.startswith(name)]
        assert not problems, problems[0]

    @pytest.mark.parametrize("name", sorted(registry.REGISTRY_CAPS))
    def test_the_cap_still_has_headroom(self, name):
        """A cap already binding cannot absorb the next finding."""
        cap = registry.REGISTRY_CAPS[name]
        actual = len((registry.PLANS / name).read_text().splitlines())
        assert actual <= cap - 20, (
            f"{name} is at {actual} of {cap} -- under 20 lines of headroom. "
            f"conventions.md rule 5: archive a completed span, do not raise the cap"
        )

    def test_the_control_fires_when_a_registry_grows_past_its_cap(self, tmp_path,
                                                                 monkeypatch):
        """A cap nobody has seen fail is a number, not a gate.

        EVERY registry is staged, not just the one being pushed over: the arm
        walks all five, so a directory holding one file raises
        ``FileNotFoundError`` and the control fails for a reason that has
        nothing to do with the cap.
        """
        over = "ledger.md"
        for name, cap in registry.REGISTRY_CAPS.items():
            padding = cap + 1 if name == over else 1
            (tmp_path / name).write_text("filler\n" * padding)
        monkeypatch.setattr(registry, "PLANS", tmp_path)
        problems = registry.registry_line_cap_violations()
        assert len(problems) == 1, problems
        assert problems[0].startswith(over) and "rule 4" in problems[0]


class TestNoLedgerRowHasGrownIntoASpecification:
    """conventions.md rule 4's per-row half -- rule 14's twin for the ledger."""

    def test_no_row_is_over_the_row_cap(self):
        """No row is over the row cap."""
        assert not registry.ledger_row_cap_violations()

    def test_the_arm_has_rows_to_grade(self):
        """Zero violations must mean zero, not "read nothing"."""
        rows = registry.ledger_rows()
        assert len(rows) >= 100, "the ledger parsed almost no rows"

    def test_the_cap_targets_outliers_rather_than_the_corpus(self):
        """A cap most rows crowd is a cap fitted to the file, which rule 4 refuses.

        **Deliberately not a "widest row has headroom" check.**  That is the
        right instrument for a LINE cap, where headroom means the document can
        absorb another finding.  A row has a different remedy always available
        -- move its narrative to the owning step -- so the meaningful property
        is that the cap bites on outliers, not on ordinary rows.  The median
        sat at 409 against a 2,000 cap when this was written; if it ever
        approaches half the cap, the ledger has become a plan document again.
        """
        widths = sorted(row.width for row in registry.ledger_rows())
        median = widths[len(widths) // 2]
        assert median <= registry.LEDGER_ROW_CAP // 2, (
            f"the MEDIAN row is {median} of {registry.LEDGER_ROW_CAP}. The cap "
            "is no longer catching outliers -- the whole table has grown into "
            "specifications. conventions.md rule 4"
        )

    def test_the_control_fires_on_a_row_that_became_a_specification(self, stage):
        """The shape eight rows carried until 2026-08-11.

        Planted on a REAL row so the control exercises the real seven-cell
        parser, not a synthetic table it wrote itself.
        """
        stage("ledger", "| balance | FU-3 |", "| balance | FU-3 " + "x" * 2100 + " |")
        problems = registry.ledger_row_cap_violations()
        assert problems, "a row over the cap must be reported"
        assert "owning step's specification" in problems[0]
