"""
Shekel Budget App -- the plan gate: CROSS-ARC FORKS (conventions.md rule 11)

Split out of ``test_registry_integrity`` on 2026-08-28, when that module went
over pylint's 1,000-line ceiling.  **This project's ruling on an over-ceiling
module is that it SPLITS rather than being shaved** (findings **N-152** /
**N-156** / **N-201**), and the fork controls are the seam that was already
there: they are the one group in that file with a subject of their own -- rule
11's SECOND half, that a fork binds its competing remedies AND its defect row
-- and the one group with its own derived specimen.

The subject is a fork's LIFECYCLE, as against ``test_registry_integrity``'s,
which is what every other registry row must satisfy, and ``test_tables``'s,
which is what IS a row of a registry at all.
"""
from __future__ import annotations

import pytest

import _registry as registry
from _staging import (
    row_of,
    stage_a_fork,
    with_cell,
)


def _a_live_fork(stage):
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

    **And when the corpus holds NONE, one is staged** (:func:`stage_a_fork`).
    The table emptied on 2026-08-28 with `pay_calendar:P16`, whose remedy
    shipped -- the rule working, again -- and an assertion here would have made
    twelve controls red for that reason a second time.  Staging keeps the arms
    graded whether or not the developer has an open fork today, which is the
    argument this class already makes for staging an UNRULED one.

    Args:
        stage: The ``stage`` fixture, used only if the corpus has no fork.

    Returns:
        ``(fork, fork line, one remedy's step line)``.
    """
    found = registry.forks()
    if not found:
        stage_a_fork(stage)
        found = registry.forks()
        assert found, "staging a fork produced none -- the helper is broken"
    fork = found[0]
    line = row_of("steps", f"| {fork.defect} |")
    arc, ident = fork.remedy_keys()[0].split(":", 1)
    return fork, line, row_of("steps", f"| {arc} | {ident} |")


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

    def test_the_live_corpus_grades_every_fork_it_holds(self, stage):
        """An EMPTY fork table is a legitimate state, and a staged one still grades.

        This asserted the corpus held at least one fork, on the ground that a
        rule with no subject is untested by the clean case.  That was right
        about the risk and wrong about the remedy: the table empties whenever
        the last ruled fork's remedy SHIPS, which is the rule working, and it
        emptied on 2026-08-28 when `pay_calendar:P16` closed.  Failing there
        makes a green registry unlandable for having no open disputes.

        So the premise moved from the corpus to the STAGING: whatever the
        corpus holds, a fork can be built from real rows, and the controls
        below have a subject either way.  What is still asserted about the live
        corpus is the part that is about the live corpus -- every fork it does
        hold is RULED.
        """
        found = registry.forks()
        assert all(f.winner for f in found), (
            "every live fork is expected to be RULED as of 2026-08-09"
        )
        _fork, _ruling, _remedy = _a_live_fork(stage)
        assert registry.forks(), (
            "neither the corpus nor the staging produced a fork, so rule 11's "
            "second half grades nothing"
        )

    def test_the_control_fires_when_a_remedy_ships_before_the_ruling(self, stage):
        """Whichever remedy ships first decides for both arcs."""
        _, ruling, remedy = _a_live_fork(stage)
        stage("steps", ruling, with_cell(ruling, -1, "**NOT YET RULED**"))
        stage("steps", remedy, with_cell(remedy, 4, "SHIPPED"))
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
        _, ruling, remedy = _a_live_fork(stage)
        stage("steps", ruling, with_cell(ruling, -1, word))
        stage("steps", remedy, with_cell(remedy, 4, "SHIPPED"))
        problems = registry.fork_violations()
        assert any("NOT YET RULED" in p for p in problems), (word, problems)

    def test_the_control_fires_when_a_ruled_fork_leaves_its_row_unpointed(self, stage):
        """A ruling nobody re-points is a ruling that decided nothing.

        Rule 2 re-points a row when its owner ships, but it only fires on a row
        that NAMES a step -- and an open fork's row names ``developer-decision``
        by design.  Without this arm the row could keep pointing at a decision
        already taken, indefinitely, with every other gate green.
        """
        fork, _, _ = _a_live_fork(stage)
        arc, ident = fork.defect_keys()[0].split(":", 1)
        line = row_of("ledger", f"| {arc} | {ident} |")
        stage("ledger", line, with_cell(line, -1, "developer-decision (2026-08-09)"))
        problems = registry.fork_violations()
        assert any(f"RULED for {fork.winner}" in p for p in problems), problems

    def test_the_control_fires_when_the_defect_names_no_live_row(self, stage):
        """A fork about a row that does not exist decides nothing."""
        _, ruling, _ = _a_live_fork(stage)
        stage("steps", ruling, with_cell(ruling, 0, "pay_calendar:P999"))
        problems = registry.fork_violations()
        assert any("names no live ledger.md row" in p for p in problems), problems
