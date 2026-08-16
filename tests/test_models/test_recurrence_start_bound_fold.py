"""The R7b-4 migration's fold, driven rather than argued.

Plan step **R7b-4** collapsed a recurrence's TWO opening bounds --
``start_period_id`` (the form's "First paycheck") and ``start_date`` -- into
the date alone.  Migration ``c4e1a8b70f36`` is what moved the data, and its
whole correctness claim is one predicate:

    a start period is worth writing down only when it is the term that DECIDES
    ``max(opening payday, start_date, start_period.start_date)``

**Nothing in the suite would otherwise execute that SQL.**  The Alembic chain
runs it against an empty table on a fresh test database, so the statement ships
on the strength of its own docstring -- the precedent this file follows is
``test_amount_ownership.py``, written for the previous migration's only
non-DDL logic for exactly the same reason.  An adversarial review of R7b-4
asked for it by name.

Each case below builds one shape, runs the migration's OWN statement text
(imported from the revision module, so a future edit to the SQL is tested
rather than a copy of it), and asserts the resulting bound.  The five shapes
are the complete space of the predicate's inputs: which of the three terms is
present, and which dominates.

The last two are the FIRING CONTROLS (``docs/plans/verification.md`` standard
4): the survivor assertion refuses a bound the fold could not read, and a test
that only exercised resolvable rows would pass against a migration that
silently erased the others -- which is what the first cut of this migration
did, because its ``UPDATE`` was unscoped and ran before the check.
"""
from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from app import ref_cache
from app.enums import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrencePatternEnum,
    RecurrenceUnitEnum,
)
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule

#: A ``starts_on`` that makes a row STORABLE, and nothing this file asserts on.
#:
#: The migration under test predates the column and reads only ``start_date``
#: and ``start_period_id``; this exists so the INSERT clears the ``NOT NULL``
#: plan step R7c-b added.  Inside the calendar window, so
#: ``ck_recurrence_rules_starts_on_range`` admits it.
_A_STORABLE_START = date(2026, 1, 2)

#: The revision module, loaded by PATH because ``migrations/versions`` is not
#: an importable package.  Loading it rather than re-typing its SQL is what
#: makes this a test OF the migration instead of a test of a copy that agrees
#: with it today.
_REVISION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations" / "versions"
    / "c4e1a8b70f36_fold_start_period_into_start_date.py"
)


def _load_revision():
    """Return the migration module, imported from its path.

    Returns:
        The loaded revision module.
    """
    spec = importlib.util.spec_from_file_location(
        "r7b4_fold_revision", _REVISION_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_REVISION = _load_revision()


def _run_fold():
    """Execute the migration's two data statements, in its own order.

    Returns:
        The ids any rule still names a start period on -- what
        ``_SURVIVOR_SQL`` selects and the migration raises on.
    """
    db.session.execute(sa.text(_REVISION._FOLD_SQL))   # noqa: SLF001
    db.session.execute(sa.text(_REVISION._CLEAR_SQL))  # noqa: SLF001
    db.session.flush()
    rows = db.session.execute(sa.text(_REVISION._SURVIVOR_SQL))  # noqa: SLF001
    return [row[0] for row in rows]


def _rule(seed_user, *, start_date=None, start_period=None):
    """Create and flush a rule carrying the given opening bound(s).

    **Built column by column and NOT through the write door**, which is not a
    shortcut: this file's subject is a migration that ran BEFORE plan step
    R7c-a existed, over rows whose only opening bounds were the two columns
    R7b-4 folded.  ``author_rule`` cannot produce that shape -- it writes
    ``starts_on`` and never writes ``start_date`` at all -- so a rule authored
    through it would carry no input for the fold to read.

    The four columns plan step R7c-b made ``NOT NULL`` are stated so the row
    is STORABLE.  They are the two-axis reading of an every-paycheck cadence
    and its opening payday, which is what R7c-a's backfill would have written
    for this shape; the fold below reads none of them, so no case here turns
    on their values.

    Args:
        seed_user: The seeded owner fixture.
        start_date: The rule's ``start_date``, or ``None``.
        start_period: The :class:`PayPeriod` to name, or ``None``.

    Returns:
        The flushed :class:`RecurrenceRule`.
    """
    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=ref_cache.recurrence_pattern_id(
            RecurrencePatternEnum.EVERY_PERIOD,
        ),
        start_date=start_date,
        start_period_id=None if start_period is None else start_period.id,
        unit_id=ref_cache.recurrence_unit_id(RecurrenceUnitEnum.PERIOD),
        placement_id=ref_cache.period_placement_id(
            PeriodPlacementEnum.CONTAINING_DATE,
        ),
        shift_id=ref_cache.business_day_shift_id(BusinessDayShiftEnum.NONE),
        starts_on=_A_STORABLE_START,
    )
    db.session.add(rule)
    db.session.flush()
    return rule


@pytest.mark.usefixtures("app")
class TestTheFoldWritesOnlyTheDominantTerm:
    """The predicate, over the complete space of its three terms."""

    def test_a_period_at_the_schedule_opening_writes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """The 40-of-43 shape on production, and the one that mattered.

        A rule pointing at period index 0 is not stating a start -- it is the
        ABSENCE of one, and ``reset_pay_periods`` re-pointed the FK on every
        rebuild so the meaning travelled with the schedule.  Writing that
        payday as an absolute date would pin the rule to it, and a schedule
        later rebuilt to an EARLIER first payday would generate nothing before
        the old opening.
        """
        with app.app_context():
            opening = min(p.start_date for p in seed_periods)
            first = next(
                p for p in seed_periods if p.start_date == opening
            )
            rule = _rule(seed_user, start_period=first)

            assert _run_fold() == []
            db.session.refresh(rule)
            assert rule.start_date is None
            assert rule.start_period_id is None

    def test_a_period_ABOVE_the_opening_is_written(
        self, app, db, seed_user, seed_periods,
    ):
        """The user chose a later paycheck, so the date has to carry it."""
        with app.app_context():
            later = max(seed_periods, key=lambda p: p.start_date)
            rule = _rule(seed_user, start_period=later)

            assert _run_fold() == []
            db.session.refresh(rule)
            assert rule.start_date == later.start_date
            assert rule.start_period_id is None

    def test_a_dominated_start_date_survives_untouched(
        self, app, db, seed_user, seed_periods,
    ):
        """Production rule 40 (Mortgage): a loan bound below the opening.

        ``loan_recurrence_sync`` OWNS ``start_date`` for a loan payment -- it
        is the first contractual installment -- so a fold that overwrote it
        with a period that does not dominate would destroy another module's
        value to change no answer.
        """
        with app.app_context():
            opening = min(p.start_date for p in seed_periods)
            first = next(
                p for p in seed_periods if p.start_date == opening
            )
            ancient = opening - timedelta(days=2000)
            rule = _rule(seed_user, start_date=ancient, start_period=first)

            assert _run_fold() == []
            db.session.refresh(rule)
            assert rule.start_date == ancient
            assert rule.start_period_id is None

    def test_a_period_dominating_a_stated_date_is_written(
        self, app, db, seed_user, seed_periods,
    ):
        """Production rule 48 (Van Payment): the period wins the maximum.

        The only shape where the fold overwrites a ``start_date``, and it must:
        the old reader took the maximum, so preserving today's answer needs
        the period.
        """
        with app.app_context():
            opening = min(p.start_date for p in seed_periods)
            later = max(seed_periods, key=lambda p: p.start_date)
            rule = _rule(
                seed_user,
                start_date=opening - timedelta(days=500),
                start_period=later,
            )

            assert _run_fold() == []
            db.session.refresh(rule)
            assert rule.start_date == later.start_date

    def test_a_rule_with_neither_bound_is_untouched(
        self, app, db, seed_user, seed_periods,
    ):
        """The control: the fold writes nothing it was not asked to."""
        with app.app_context():
            rule = _rule(seed_user)

            assert _run_fold() == []
            db.session.refresh(rule)
            assert rule.start_date is None
            assert rule.start_period_id is None


@pytest.mark.usefixtures("app")
class TestTheSurvivorAssertionCanFire:
    """A bound the fold cannot READ is refused, not silently erased.

    The first cut of this migration cleared ``start_period_id`` unconditionally
    and then asked which rules still carried one -- so the answer was empty by
    construction and the check asserted nothing, while the one row it was
    supposed to catch had already lost its bound. Both statements are scoped to
    a FK resolving to one of the OWNER's periods now, which is what leaves the
    survivor there to be found.
    """

    def test_a_cross_user_start_period_survives_and_is_reported(
        self, app, db, seed_user, seed_second_user, seed_second_periods,
        seed_periods,
    ):
        """Another owner's period is not this owner's bound.

        ``calendar.period_by_id`` searched only the OWNER's periods, so such a
        FK contributed nothing to the old maximum -- folding it would import a
        stranger's payday into this rule's bound, and clearing it without
        folding would drop a bound the app was applying. Neither is acceptable,
        so the migration refuses and names the row.
        """
        with app.app_context():
            foreign = seed_second_periods[0]
            rule = _rule(seed_user, start_period=foreign)

            survivors = _run_fold()

            assert survivors == [rule.id]
            db.session.refresh(rule)
            # Untouched: neither folded nor cleared, so nothing was lost
            # before the refusal was raised.
            assert rule.start_period_id == foreign.id
            assert rule.start_date is None
