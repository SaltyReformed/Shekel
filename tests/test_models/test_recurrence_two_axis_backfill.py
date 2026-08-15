"""The R7c-a backfill, graded against the write door rather than argued.

Plan step **R7c-a** adds the two-axis columns and backfills them from the
closed-set columns they will replace.  Migration ``f2a94c7e1b60`` carries that
derivation in SQL, and it is a SECOND implementation of
``app.services.recurrence._resolution``'s -- deliberately, because plan step
R7c-c deletes the functions an importing migration would depend on, and a
migration that stops being runnable is one that cannot be trusted to have run.

**A second implementation is only safe if something grades it**, and nothing in
the suite otherwise executes that SQL: the Alembic chain runs it against an
empty table on a fresh test database, so it would ship on the strength of its
own docstring.  The precedent is ``test_recurrence_start_bound_fold.py``, written
for the previous recurrence migration's only non-DDL logic for the same reason.

How it grades
-------------

The write door (``recurrence._authoring._author``) writes the same five columns
from ``resolve`` on every author, so it is the Python producer.  Each case
builds a schedule, authors a matrix of rules through that door, **poisons all
five columns with wrong-but-valid values**, runs the migration's own statement
text, and asserts every column comes back to what the door wrote.

The poison is the FIRING CONTROL (``docs/plans/verification.md`` standard 4).
Without it a backfill that updated NOTHING would pass every assertion, because
the door had already written the right answers -- which is precisely the free
pass standard 3 warns about.  It poisons in BOTH directions: ``nominal_day``
goes to a non-NULL value on the rules that should end NULL, so the statement
has to CLEAR it as well as set it.

Which axes production cannot exercise
-------------------------------------

The 46 live rules were checked directly and matched on all 5 columns, but they
vary almost nothing: every one is ``interval_n = 1``, all but one is
``CONTAINING_DATE``, none is bounded past the horizon, none has a day above 28
that its own month cannot hold, and the biweekly schedule puts a payday in
every calendar month.  The schedules below are chosen for exactly the branches
that leaves untested -- a 90-day cadence (months with no payday at all, which
is what ``Monthly First``'s scan has to skip), a weekly one, bounds past the
materialised horizon (the projection arm), and day-29/30/31 rules anchored in
short months (the clamp arm).
"""
from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.services import pay_period_write
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    RecurrenceSpec,
    author_rule,
    first_occurrence,
    recurrence_spec,
    resolve,
)
from tests._test_helpers import load_migration_module

#: The revision module, loaded by FILENAME through the shared helper.
#:
#: Loading it rather than re-typing its SQL is what makes this a test OF the
#: migration instead of a test of a copy that agrees with it today.  The
#: importlib boilerplate lives in ``tests._test_helpers`` because
#: ``migrations/versions`` has no ``__init__`` and three suites need the same
#: four lines -- that helper's own docstring records it as a duplicate-code
#: finding.
_REVISION = load_migration_module(
    "f2a94c7e1b60_add_the_two_axis_recurrence_columns.py",
)

#: The schedules the rule matrix is authored against.
#:
#: ``(label, first payday, cadence days, how many)``.  The 90-day one is the
#: shape ``recurrence._occurrence``'s own docstring names as the condition for
#: several occurrences in one paycheck, and it is the only one here that leaves
#: calendar months with no payday.
_SCHEDULES: tuple[tuple[str, date, int, int], ...] = (
    ("biweekly", date(2026, 1, 2), 14, 30),
    ("weekly", date(2026, 3, 11), 7, 40),
    ("quarterly-ish", date(2026, 6, 1), 90, 8),
    ("monthly-ish", date(2026, 2, 27), 30, 20),
)

#: Every cadence the closed pattern set can store, which is the set the write
#: door accepts until plan step R7c-c frees the interval.
_CADENCES: tuple[tuple[int, RecurrenceUnitEnum, PeriodPlacementEnum], ...] = (
    (1, RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE),
    (3, RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE),
    (5, RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE),
    (1, RecurrenceUnitEnum.MONTH, PeriodPlacementEnum.CONTAINING_DATE),
    (3, RecurrenceUnitEnum.MONTH, PeriodPlacementEnum.CONTAINING_DATE),
    (6, RecurrenceUnitEnum.MONTH, PeriodPlacementEnum.CONTAINING_DATE),
    (1, RecurrenceUnitEnum.YEAR, PeriodPlacementEnum.CONTAINING_DATE),
    (
        1, RecurrenceUnitEnum.MONTH,
        PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
    ),
)

#: Days chosen for the month-end clamp: 28 can never clamp, 29 clamps only in a
#: common February, 30 clamps in February, 31 clamps in seven months.
_DAYS: tuple[int, ...] = (1, 15, 28, 29, 30, 31)

#: Cycle-start months spanning every residue class a 3-, 6- and 12-month step
#: can name.
_MONTHS: tuple[int, ...] = (1, 2, 3, 4, 6, 7, 11, 12)


def _bounds_for(first_payday: date, cadence_days: int, count: int):
    """Return the opening bounds a rule is authored with, for one schedule.

    Args:
        first_payday: The schedule's opening payday.
        cadence_days: Days between paydays.
        count: How many paydays the schedule holds.

    Returns:
        tuple of ``date | None``: no bound, one below the opening, one inside
        the schedule, one landing on a payday, and one PAST the materialised
        horizon -- the arm that makes the derivation project forward rather
        than answer the last saved payday.
    """
    horizon_payday = first_payday.toordinal() + cadence_days * (count - 1)
    return (
        None,
        date.fromordinal(first_payday.toordinal() - 400),
        date.fromordinal(first_payday.toordinal() + cadence_days * 3 + 5),
        date.fromordinal(first_payday.toordinal() + cadence_days * 4),
        date.fromordinal(horizon_payday + cadence_days * 3 + 11),
    )


def _authored_matrix(user_id: int, calendar, bounds):
    """Author one rule per (cadence, day, month, bound) the shape allows.

    Args:
        user_id: The owner.
        calendar: The owner's :class:`PayCalendar`.
        bounds: The opening bounds from :func:`_bounds_for`.

    Returns:
        list[RecurrenceRule]: every flushed rule.
    """
    rules = []
    for interval_n, unit, placement in _CADENCES:
        for bound in bounds:
            # The day and month coordinates are read only by the calendar
            # family; varying them for the other two would author the same rule
            # many times over and say nothing new.  Rotating rather than taking
            # the cross product keeps the matrix at a size the suite can afford
            # while still reaching every day and every month on some cadence.
            if unit is RecurrenceUnitEnum.PERIOD:
                day_months = ((None, None),)
            elif placement is PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER:
                day_months = ((None, None),)
            else:
                day_months = tuple(
                    (day, month)
                    for day in _DAYS
                    for month in (
                        _MONTHS if interval_n * (
                            12 if unit is RecurrenceUnitEnum.YEAR else 1
                        ) > 1 else (None,)
                    )
                )
            for day_of_month, month_of_year in day_months:
                rules.append(author_rule(
                    RecurrenceSpec(
                        user_id=user_id,
                        unit=unit,
                        interval_n=interval_n,
                        placement=placement,
                        day_of_month=day_of_month,
                        month_of_year=month_of_year,
                        start_date=bound,
                    ),
                    calendar,
                ))
    return rules


def _ref_id(table: str, name: str) -> int:
    """Return a ``ref`` row's id by NAME, for a row this test must plant.

    The one place a test resolves a ``ref`` id from a string.  It is legitimate
    here and nowhere else: the row being built is one no application door can
    write, so ``ref_cache``'s enum-keyed accessors -- which is what production
    code must use -- have nothing to key on.

    Args:
        table: The ``ref`` table name.
        name: The row's ``name``.

    Returns:
        The row id.
    """
    return db.session.execute(
        sa.text(f"SELECT id FROM ref.{table} WHERE name = :name"),
        {"name": name},
    ).scalar_one()


def _expected(rule, calendar):
    """Return what the write door's own producer says the five columns hold.

    Args:
        rule: The stored rule.
        calendar: Its owner's schedule.

    Returns:
        dict: column name -> expected value.
    """
    resolved = resolve(recurrence_spec(rule), calendar)
    return {
        "starts_on": first_occurrence(resolved, calendar),
        "unit_id": ref_cache.recurrence_unit_id(resolved.unit),
        "placement_id": ref_cache.period_placement_id(resolved.placement),
        "shift_id": ref_cache.business_day_shift_id(resolved.shift),
        "nominal_day": resolved.nominal_day,
    }


def _poison(rule_ids):
    """Overwrite the five columns with wrong-but-valid values.

    The firing control.  Without it a statement that updated nothing would pass
    every assertion below, because the write door has already written the right
    answers -- and the poison runs in BOTH directions, since ``nominal_day``
    goes non-NULL on rules whose correct value is NULL.

    ``1999-01-01`` is chosen so the ``ck_recurrence_rules_nominal_day`` CHECK
    admits the poisoned pair: its day is 1, so any value 29-31 exceeds it.

    Args:
        rule_ids: The rules to poison.
    """
    db.session.execute(
        sa.text(
            "UPDATE budget.recurrence_rules SET "
            "starts_on = DATE '1999-01-01', "
            "unit_id = (SELECT id FROM ref.recurrence_units "
            "           WHERE name = 'week'), "
            "placement_id = (SELECT id FROM ref.period_placements "
            "                WHERE name = 'period_starting_on_or_after'), "
            "shift_id = (SELECT id FROM ref.business_day_shifts "
            "            WHERE name = 'prior'), "
            "nominal_day = 29 "
            "WHERE id IN :ids"
        ).bindparams(sa.bindparam("ids", expanding=True)),
        {"ids": list(rule_ids)},
    )
    db.session.flush()


@pytest.mark.parametrize(
    ("label", "first_payday", "cadence_days", "count"),
    _SCHEDULES,
    ids=[schedule[0] for schedule in _SCHEDULES],
)
@pytest.mark.usefixtures("app")
def test_backfill_reproduces_the_write_door(
    db, bare_user, label, first_payday, cadence_days, count,
):  # pylint: disable=redefined-outer-name,unused-argument
    """The migration's SQL answers what ``_author`` writes, on every shape.

    Args:
        db: The session fixture.
        bare_user: An owner with no schedule of his own.
        label: The schedule's name, for the parametrize id.
        first_payday: The schedule's opening payday.
        cadence_days: Days between paydays.
        count: How many paydays to record.
    """
    user_id = bare_user["user"].id
    pay_period_write.record_paydays(
        user_id=user_id, first_payday=first_payday,
        num_periods=count, cadence_days=cadence_days,
    )
    db.session.flush()
    calendar = calendar_for(user_id)

    rules = _authored_matrix(
        user_id, calendar, _bounds_for(first_payday, cadence_days, count),
    )
    assert len(rules) > 100, (
        f"the {label} matrix authored only {len(rules)} rules; a matrix this "
        f"small is not exercising the cadence / bound / day cross product"
    )
    expected = {rule.id: _expected(rule, calendar) for rule in rules}
    # At least one shape must end with a CLAMPED day and one with none, or the
    # poison's two directions are not both exercised.
    clamped = [rid for rid, cols in expected.items()
               if cols["nominal_day"] is not None]
    assert clamped, (
        f"no rule in the {label} matrix anchors on a clamped day, so the "
        f"nominal_day arm of the backfill is untested here"
    )
    assert len(clamped) < len(expected), "every rule clamped -- matrix is wrong"

    _poison(expected)
    db.session.execute(sa.text(_REVISION._BACKFILL_SQL))  # noqa: SLF001
    db.session.flush()
    db.session.expire_all()

    stored = {
        rule.id: {
            "starts_on": rule.starts_on,
            "unit_id": rule.unit_id,
            "placement_id": rule.placement_id,
            "shift_id": rule.shift_id,
            "nominal_day": rule.nominal_day,
        }
        for rule in db.session.query(RecurrenceRule)
        .filter(RecurrenceRule.id.in_(list(expected)))
        .all()
    }
    mismatched = {
        rule_id: (stored[rule_id], expected[rule_id])
        for rule_id in expected
        if stored[rule_id] != expected[rule_id]
    }
    assert not mismatched, (
        f"{len(mismatched)} of {len(expected)} rules on the {label} schedule "
        f"disagree between the migration's SQL and the write door: "
        f"{dict(list(mismatched.items())[:5])}"
    )


@pytest.mark.usefixtures("app")
def test_poison_is_a_firing_control(db, bare_user):  # pylint: disable=redefined-outer-name
    """A backfill that ran on NOTHING would fail the assertions above.

    Standard 4 of ``docs/plans/verification.md``: a guard whose control does
    not fire is not a guard.  This drives the poison and then does NOT run the
    statement, so it proves the previous test would have caught an ``UPDATE``
    that matched no rows -- the exact failure the migration's own predecessor
    shipped, where an unscoped statement ran before its check and erased the
    evidence.

    Args:
        db: The session fixture.
        bare_user: The owner.
    """
    user_id = bare_user["user"].id
    pay_period_write.record_paydays(
        user_id=user_id, first_payday=date(2026, 1, 2),
        num_periods=10, cadence_days=14,
    )
    db.session.flush()
    calendar = calendar_for(user_id)
    rule = author_rule(
        RecurrenceSpec(
            user_id=user_id, unit=RecurrenceUnitEnum.MONTH, interval_n=1,
            placement=PeriodPlacementEnum.CONTAINING_DATE, day_of_month=15,
        ),
        calendar,
    )
    expected = _expected(rule, calendar)
    assert expected["starts_on"] == date(2026, 1, 15)

    _poison([rule.id])
    db.session.expire_all()
    poisoned = db.session.get(RecurrenceRule, rule.id)
    assert poisoned.starts_on == date(1999, 1, 1)
    assert poisoned.nominal_day == 29
    assert poisoned.unit_id != expected["unit_id"]
    assert poisoned.placement_id != expected["placement_id"]
    assert poisoned.shift_id != expected["shift_id"]


#: What each new column looks like at R7c-a's head: nullable, and typed.
#:
#: All five are NULLABLE here and R7c-b tightens the four that become NOT NULL
#: -- the documented three-step, with the tighten in the leaf whose readers
#: make a NULL matter.  Asserting the shape rather than assuming it is what
#: replaced ``test_recurrence_anchor_subtypes_migration``'s
#: ``TestTheDerivedColumnsAreAbsent``, whose own docstring said this step
#: deletes it: that test proved the columns were absent, and something has to
#: prove they arrived.
_EXPECTED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("unit_id", "integer", "YES"),
    ("placement_id", "integer", "YES"),
    ("shift_id", "integer", "YES"),
    ("starts_on", "date", "YES"),
    ("nominal_day", "smallint", "YES"),
)


@pytest.mark.usefixtures("app")
def test_the_five_columns_exist_with_the_shape_r7c_a_gives_them(db):  # pylint: disable=redefined-outer-name
    """Each new column is present, correctly typed, and still nullable.

    Args:
        db: The session fixture.
    """
    rows = db.session.execute(sa.text(
        "SELECT column_name, data_type, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = 'budget' AND table_name = 'recurrence_rules' "
        "  AND column_name = ANY(:cols)"
    ), {"cols": [name for name, _type, _null in _EXPECTED_COLUMNS]}).all()
    found = {row.column_name: (row.data_type, row.is_nullable) for row in rows}
    assert found == {
        name: (data_type, nullable)
        for name, data_type, nullable in _EXPECTED_COLUMNS
    }


@pytest.mark.usefixtures("app")
def test_the_nominal_day_check_refuses_a_day_the_date_already_holds(db, bare_user):  # pylint: disable=redefined-outer-name
    """``nominal_day`` records a CLAMP, so a redundant day is refused.

    The firing control for ``ck_recurrence_rules_nominal_day``.  April cannot
    hold a 31st, so ``(2026-04-30, 31)`` is the state the column exists for;
    May can, so ``(2026-05-31, 31)`` would be a second statement of a day
    ``starts_on`` already carries and the CHECK refuses it.

    Args:
        db: The session fixture.
        bare_user: The owner.
    """
    def _plant(starts_on, nominal_day):
        db.session.execute(sa.text(
            "INSERT INTO budget.recurrence_rules "
            "(user_id, pattern_id, interval_n, offset_periods, starts_on, "
            " nominal_day) "
            "VALUES (:uid, :pid, 1, 0, :starts_on, :nominal_day)"
        ), {
            "uid": bare_user["user"].id,
            "pid": _ref_id("recurrence_patterns", "Monthly"),
            "starts_on": starts_on,
            "nominal_day": nominal_day,
        })
        db.session.flush()

    _plant(date(2026, 4, 30), 31)

    with pytest.raises(IntegrityError) as caught:
        _plant(date(2026, 5, 31), 31)
    assert "ck_recurrence_rules_nominal_day" in str(caught.value)
    db.session.rollback()


@pytest.mark.usefixtures("app")
def test_a_legacy_owner_with_no_pay_schedule_row_is_DERIVED_not_refused(db, bare_user):  # pylint: disable=redefined-outer-name
    """An owner with periods but no schedule row migrates, matching the app.

    **The hole an adversarial review of this leaf found.**  The first cut
    REFUSED this owner, on the reasoning that ``derive_periods`` will not build
    periods without a cadence -- which the code it named contradicts:
    ``calendar_for`` resolves the cadence through
    ``pay_schedule_service.resolve_cadence``, whose documented legacy fallback
    INFERS it from the last period's stored length.  The app serves such an
    owner correctly, so refusing would have aborted a deploy (migrations run
    from the container entrypoint) over a state that is not a defect.

    The bound is deliberately past the HORIZON, because that is the only arm
    that reads the cadence at all: inside the schedule the derivation finds a
    saved payday and never divides by anything.

    Args:
        db: The session fixture.
        bare_user: The owner, stripped of its schedule row below.
    """
    user_id = bare_user["user"].id
    pay_period_write.record_paydays(
        user_id=user_id, first_payday=date(2026, 1, 2),
        num_periods=6, cadence_days=14,
    )
    db.session.flush()
    # The legacy shape: periods, no schedule row.  No door can create this any
    # more -- every batch that records a payday upserts the row -- so it is
    # made rather than found.
    db.session.execute(
        sa.text("DELETE FROM budget.pay_schedule WHERE user_id = :uid"),
        {"uid": user_id},
    )
    db.session.flush()

    calendar = calendar_for(user_id)
    assert calendar.cadence_days == 14, (
        "the app's own loader stopped inferring the cadence, so this test no "
        "longer describes a state the application supports"
    )
    rule = author_rule(
        RecurrenceSpec(
            user_id=user_id,
            unit=RecurrenceUnitEnum.PERIOD,
            placement=PeriodPlacementEnum.CONTAINING_DATE,
            start_date=date(2026, 6, 1),
        ),
        calendar,
    )
    db.session.flush()
    expected = _expected(rule, calendar)
    assert expected["starts_on"] > calendar.horizon(), (
        "the bound no longer lands past the horizon, so this case stopped "
        "exercising the projection arm that reads the cadence"
    )

    # Nothing to refuse, and the backfill reproduces the app's answer.
    _REVISION.refuse_underivable(db.session)
    _poison([rule.id])
    db.session.execute(sa.text(_REVISION._BACKFILL_SQL))  # noqa: SLF001
    db.session.flush()
    _REVISION.verify_backfilled(db.session)
    db.session.expire_all()

    stored = db.session.get(RecurrenceRule, rule.id)
    assert stored.starts_on == expected["starts_on"]
    assert stored.unit_id == expected["unit_id"]


@pytest.mark.usefixtures("app")
def test_the_upgrade_REFUSES_rather_than_migrating_an_underivable_rule(db, bare_user):  # pylint: disable=redefined-outer-name
    """The refusal is not just a SELECT -- the migration ACTS on it.

    ``test_underivable_rule_is_refused_by_name`` proves the query FINDS the
    row; this proves the upgrade stops.  Without it the query could be correct
    while nothing consulted its answer, and this leaf's only protection
    against seating a recurring bill on a fabricated date would never have
    been executed.

    It calls the migration's OWN function -- which is why that function has a
    name -- rather than re-raising a copy of its message, and rather than
    running a real upgrade, which inside an xdist worker would move the whole
    session's schema.

    Args:
        db: The session fixture.
        bare_user: An owner with NO pay periods, which is the refused state.
    """
    db.session.add(RecurrenceRule(
        user_id=bare_user["user"].id,
        pattern_id=_ref_id("recurrence_patterns", "Every Period"),
        interval_n=1,
        offset_periods=0,
    ))
    db.session.flush()

    with pytest.raises(RuntimeError) as caught:
        _REVISION.refuse_underivable(db.session)

    message = str(caught.value)
    assert "cannot derive a first occurrence" in message
    assert "owner has no pay periods" in message


@pytest.mark.usefixtures("app")
def test_the_verify_half_catches_a_row_the_backfill_missed(db, bare_user):  # pylint: disable=redefined-outer-name
    """A NULL left behind is NAMED, not shipped as a successful upgrade.

    The three-step's verify half, driven on a row planted to look exactly like
    what a ``ref`` seed short a row would leave: every input arm green, the
    ``UPDATE`` unable to resolve an id, and no constraint to catch it because
    the ``NOT NULL`` is a leaf away.

    Args:
        db: The session fixture.
        bare_user: The owner.
    """
    pay_period_write.record_paydays(
        user_id=bare_user["user"].id, first_payday=date(2026, 1, 2),
        num_periods=6, cadence_days=14,
    )
    db.session.flush()
    db.session.add(RecurrenceRule(
        user_id=bare_user["user"].id,
        pattern_id=_ref_id("recurrence_patterns", "Every Period"),
        interval_n=1,
        offset_periods=0,
        starts_on=date(2026, 1, 2),
        placement_id=_ref_id("period_placements", "containing_date"),
        shift_id=_ref_id("business_day_shifts", "none"),
        # ``unit_id`` deliberately absent: the shape a missing ``ref`` row
        # leaves behind.
    ))
    db.session.flush()

    # The INPUT arm is green -- which is the point: it grades the inputs, and
    # this row's inputs are fine.
    _REVISION.refuse_underivable(db.session)

    with pytest.raises(RuntimeError) as caught:
        _REVISION.verify_backfilled(db.session)
    assert "left a NULL" in str(caught.value)


@pytest.mark.usefixtures("app")
def test_underivable_rule_is_refused_by_name(db, bare_user):  # pylint: disable=redefined-outer-name
    """A rule whose owner has no schedule is NAMED, not given a date.

    The migration refuses rather than letting the ``NOT NULL`` fail, because a
    bare ``null value in column "starts_on"`` says neither which rule nor why,
    and because the alternative to refusing is to invent an occurrence date for
    a recurring bill.

    Args:
        db: The session fixture.
        bare_user: An owner with NO pay periods, which is the refused state.
    """
    rule = RecurrenceRule(
        user_id=bare_user["user"].id,
        pattern_id=_ref_id("recurrence_patterns", "Every Period"),
        interval_n=1,
        offset_periods=0,
        unit_id=_ref_id("recurrence_units", "period"),
        placement_id=_ref_id("period_placements", "containing_date"),
        shift_id=_ref_id("business_day_shifts", "none"),
        starts_on=date(2026, 1, 1),
    )
    db.session.add(rule)
    db.session.flush()

    rows = db.session.execute(
        sa.text(_REVISION._REFUSE_UNDERIVABLE_SQL),  # noqa: SLF001
    ).all()
    named = {row.id: row.reason for row in rows}
    assert rule.id in named, (
        "a rule whose owner has no pay periods must be named by the "
        "migration's refusal query, or the NOT NULL fails with no diagnosis"
    )
    assert named[rule.id] == "owner has no pay periods"
