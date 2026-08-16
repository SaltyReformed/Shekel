"""Tests for the 48e2c7ee593d "add due_date to transfers" migration.

The revision adds ``budget.transfers.due_date`` and backfills it, recomputing
projected template-linked rows from their recurrence rule.  What is graded here
is the SECOND backfill step's arithmetic, which plan step **R7c-c** had to
freeze into the migration.

**Why this file exists at all.**  The recompute called the live
``recurrence_engine.compute_due_date`` through ``SimpleNamespace`` stand-ins.
R7c-c dropped ``budget.recurrence_rules.day_of_month`` and pointed that function
at a derivation over ``unit_id`` / ``placement_id`` / ``starts_on`` /
``nominal_day`` -- attributes the namespaces do not carry and columns that do
not exist at this revision -- so every replay over a NON-EMPTY database raised
``AttributeError`` and aborted ``flask db upgrade`` mid-chain.

That defect was invisible to the whole suite and to
``scripts/build_test_template.py``, because both replay the chain against an
EMPTY database: the recompute loop runs zero times, so the call is never made.
It fires only on the replay that matters -- a restored production or dev dump
stamped at or before this revision, migrated forward.  A test that asserted
anything about the migrated SCHEMA would have stayed green through it, which is
why this file drives the arithmetic directly and asserts the import is gone.

The frozen copy was graded against the original by differential sweep while
R7c-c was built: ``_compute_due_date`` as it stood at commit ``0d66ee16``, over
every ``(day_of_month, due_day_of_month)`` pair including both ``None`` spellings
and 153,600 period combinations spanning biweekly, semi-monthly and calendar
month windows -- 0 disagreements.  The cases below are that sweep's branches,
stated as the values a reader can check by hand.
"""
from __future__ import annotations

import ast
import pathlib
from datetime import date

from tests._test_helpers import load_migration_module

_MIGRATION_FILENAME = "48e2c7ee593d_add_due_date_to_transfers.py"
_MIGRATION = load_migration_module(_MIGRATION_FILENAME)
_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "migrations" / "versions" / _MIGRATION_FILENAME
)
_MIGRATION_SOURCE = _MIGRATION_PATH.read_text(encoding="utf-8")

#: The frozen arithmetic under test.
_due_date = _MIGRATION._due_date_at_this_revision  # pylint: disable=protected-access


class TestChaining:
    """The revision sits where it claims in the Alembic chain."""

    def test_revision_and_down_revision(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "48e2c7ee593d"
        assert _MIGRATION.down_revision == "c2a2c508e103"


class TestItImportsNoApplicationCode:
    """The revision computes from its own frozen mapping, never from ``app``.

    **The regression guard for the defect this file records**, and it is an
    AST assertion rather than a behavioural one because behaviour cannot see it:
    an import of a function whose signature later moves is green until a replay
    meets a row, and the suite's replays never do.
    """

    def test_no_import_of_app_code_anywhere_in_the_module(self):
        """No statement in the module imports from the ``app`` package.

        Deferred (function-local) imports count: the one this replaces was
        inside ``upgrade`` precisely so it loaded at upgrade time, which is what
        made it a runtime failure instead of an import-time one.  ``ast.walk``
        therefore visits the whole tree rather than the module body.
        """
        offenders = []
        for node in ast.walk(ast.parse(_MIGRATION_SOURCE)):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "app" or module.startswith("app."):
                    offenders.append(f"from {module} import ...")
            elif isinstance(node, ast.Import):
                offenders.extend(
                    f"import {alias.name}" for alias in node.names
                    if alias.name == "app" or alias.name.startswith("app.")
                )
        assert offenders == [], (
            "48e2c7ee593d must state its own mapping: an import of app code "
            f"makes a shipped migration change meaning ({offenders})"
        )


class TestACadenceThatNamesNoDayOfMonth:
    """Rule with ``day_of_month IS NULL`` -- every-paycheck and every-N."""

    def test_it_is_dated_from_the_period_start(self):
        """The pay period's first day, whatever the due-day column holds.

        This is the branch that makes the migration's ``IS DISTINCT FROM``
        guard a no-op for every-paycheck rows: they already carried the period
        start.
        """
        assert _due_date(
            None, None, date(2026, 6, 5), date(2026, 6, 18),
        ) == date(2026, 6, 5)

    def test_a_due_day_beside_no_scheduling_day_changes_nothing(self):
        """``due_day_of_month`` is a REFINEMENT of a scheduling day.

        With no day to refine, the period start still wins -- asserted because
        reading the two columns independently would date this row on the 10th.
        """
        assert _due_date(
            None, 10, date(2026, 6, 5), date(2026, 6, 18),
        ) == date(2026, 6, 5)


class TestTheSchedulingDayInsideThePeriod:
    """Which of a straddling period's two months holds the occurrence."""

    def test_a_day_inside_the_start_month_dates_there(self):
        """Period 2026-06-05..2026-06-18, day 15 -> 2026-06-15.

        Both endpoints are in June and June 15 falls inside the window.
        """
        assert _due_date(
            15, None, date(2026, 6, 5), date(2026, 6, 18),
        ) == date(2026, 6, 15)

    def test_a_straddling_period_takes_the_month_that_contains_the_day(self):
        """Period 2026-06-25..2026-07-08, day 3 -> 2026-07-03.

        June 3 is BEFORE the window, so the start month loses and the end
        month's July 3 -- inside the window -- is the answer.
        """
        assert _due_date(
            3, None, date(2026, 6, 25), date(2026, 7, 8),
        ) == date(2026, 7, 3)

    def test_a_day_no_endpoint_month_places_inside_falls_back_to_the_start(self):
        """Period 2026-06-05..2026-06-18, day 25 -> 2026-06-25.

        Neither June 25 nor (the same month's) target lands inside the window,
        so the base month stays the START month and the date is outside the
        period.  That is plan ledger row **D18**'s mechanism, recorded here as
        the behaviour this revision froze rather than as a defect it fixes: the
        migration reproduces what the engine wrote, and R5 owns the model.
        """
        assert _due_date(
            25, None, date(2026, 6, 5), date(2026, 6, 18),
        ) == date(2026, 6, 25)


class TestMonthEndClamping:
    """A day beyond a month's last day is clamped, never rolled over."""

    def test_day_31_in_a_30_day_month(self):
        """Period 2026-04-20..2026-05-03, day 31 -> 2026-04-30.

        April holds 30 days, so the target clamps to April 30, which IS inside
        the window and therefore selects April as the base month.
        """
        assert _due_date(
            31, None, date(2026, 4, 20), date(2026, 5, 3),
        ) == date(2026, 4, 30)

    def test_day_30_in_february(self):
        """Period 2026-02-20..2026-03-05, day 30 -> 2026-02-28.

        2026 is not a leap year; February clamps to the 28th.
        """
        assert _due_date(
            30, None, date(2026, 2, 20), date(2026, 3, 5),
        ) == date(2026, 2, 28)

    def test_day_30_in_a_leap_february(self):
        """Period 2028-02-20..2028-03-04, day 30 -> 2028-02-29.

        The clamp reads the real month length, so a leap year gains the 29th.
        """
        assert _due_date(
            30, None, date(2028, 2, 20), date(2028, 3, 4),
        ) == date(2028, 2, 29)


class TestTheSeparateDueDay:
    """``due_day_of_month`` overrides, and its month depends on the order."""

    def test_a_due_day_equal_to_the_scheduling_day_is_not_an_override(self):
        """Day 15 due on the 15th -> the scheduling branch, 2026-06-15."""
        assert _due_date(
            15, 15, date(2026, 6, 5), date(2026, 6, 18),
        ) == date(2026, 6, 15)

    def test_a_due_day_above_the_scheduling_day_stays_in_the_same_month(self):
        """Scheduled on the 5th, due on the 20th -> 2026-06-20.

        Period 2026-06-01..2026-06-14 selects June; the 20th is later in that
        same month, so no roll-forward applies even though the date falls
        outside the pay period.
        """
        assert _due_date(
            5, 20, date(2026, 6, 1), date(2026, 6, 14),
        ) == date(2026, 6, 20)

    def test_a_due_day_below_the_scheduling_day_rolls_to_the_next_month(self):
        """Scheduled on the 22nd, due on the 1st -> 2026-07-01.

        The next-month convention the module docstring states: a bill scheduled
        late in one month and due on the 1st is due the 1st of the FOLLOWING
        month.
        """
        assert _due_date(
            22, 1, date(2026, 6, 15), date(2026, 6, 28),
        ) == date(2026, 7, 1)

    def test_the_roll_forward_crosses_the_year_boundary(self):
        """Scheduled on the 22nd of December, due on the 1st -> 2027-01-01.

        The one branch a whole-year sweep would otherwise pass over: December
        rolls to January of the NEXT year, not to month 13.
        """
        assert _due_date(
            22, 1, date(2026, 12, 15), date(2026, 12, 28),
        ) == date(2027, 1, 1)

    def test_the_due_day_is_clamped_in_its_own_month(self):
        """Scheduled on the 5th, due on the 31st of April -> 2026-04-30.

        The clamp is applied to the DUE month, which can differ from the
        scheduling month.
        """
        assert _due_date(
            5, 31, date(2026, 4, 1), date(2026, 4, 14),
        ) == date(2026, 4, 30)

    def test_the_rolled_month_is_what_clamps_a_due_day(self):
        """Scheduled on the 30th of January, due on the 29th -> 2026-02-28.

        29 is below 30, so the due date rolls into February -- and February
        2026 holds 28 days, so the clamp uses the month it rolled INTO.
        """
        assert _due_date(
            30, 29, date(2026, 1, 20), date(2026, 2, 2),
        ) == date(2026, 2, 28)
