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

**Plan step R7c-b RE-RUNS the same statement** (migration ``b6d41f0a9c27``,
step 1), which is what moved the text into
:mod:`migrations._recurrence_two_axis_backfill` -- so this file grades a
statement two migrations execute rather than one.

How it grades
-------------

The write door (``recurrence._authoring._author``) writes the same five columns
from ``resolve`` on every author, so it is the Python producer.  Each case
builds a schedule, authors a matrix of rules through that door, **plants the
closed-set coordinates R7c-a's door wrote** (:func:`_encode_legacy_columns`),
**poisons all five two-axis columns with wrong-but-valid values**, runs the
statement, and asserts every column comes back to what the door wrote.

The planting step arrived with plan step R7c-b and it is not plumbing.  That
step's write door writes neither ``start_date`` nor ``month_of_year``, two of
the three columns the derivation reads -- so a rule authored today carries two
thirds of the statement's input missing, and running it over one answers the
schedule's OPENING rather than the rule's own date.  Planting the encoding
makes the matrix assert the property the re-run actually needs: **the SQL is
the INVERSE of the encoding.**  The unplanted case is not swept away; it is
driven directly by
:func:`test_a_start_below_the_opening_is_the_shape_the_downgrade_refuses`, as
the firing control for ``b6d41f0a9c27.downgrade``'s refusal to revert.

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

import calendar as calendar_module
from datetime import date, timedelta

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
    recurrence_spec,
    resolve,
)
from app.services.recurrence._months import MONTHS_PER_YEAR
# The derivation itself, IMPORTED rather than loaded from a revision by
# filename.  Plan step R7c-b lifted the statement and its two guards out of the
# R7c-a revision into ``migrations._recurrence_two_axis_backfill``, because
# R7c-b re-runs the SAME text and two copies that merely agreed would be one
# more thing to keep agreeing.  ``migrations`` is a PEP 420 namespace package,
# so this is an ordinary import -- and the ``load_migration_module`` call plus
# the ``SLF001`` waivers a private name needed go with it.
from migrations import _recurrence_two_axis_backfill as _BACKFILL
from migrations.versions import (  # noqa: E501  pylint: disable=line-too-long
    b6d41f0a9c27_the_two_axis_columns_become_authoritative as _R7CB,
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


def _first_occurrences(calendar, bounds):
    """Return the first-occurrence dates the matrix authors, per cadence shape.

    **Plan step R7c-b turned this file's inputs inside out**, and the reason is
    worth stating because it changes what the matrix proves.  The rules used to
    be authored as ``(day_of_month, month_of_year, start_date)`` -- the closed
    set's own coordinates -- and the write door DERIVED the first occurrence
    from them.  That derivation is deleted: ``starts_on`` is authored (ruling
    **R-R16**), and :class:`RecurrenceSpec` no longer accepts any of the three.
    So the matrix states DATES, and the legacy coordinates are written back
    onto each row by :func:`_encode_legacy_columns` -- which is the direction
    R7c-a's door wrote them in, and the direction this file's subject reads.

    The dates are built from the same *bounds* the old matrix used, crossed
    with the same day and month coordinates, so the branches the SQL has --
    inside the schedule, on a payday, past the horizon, month-end clamped --
    are still reached.

    **The below-opening bound is raised to the opening here**, and that is the
    one arm the inverse cannot cover: ``GREATEST(s.opening, r.start_date)``
    means the closed encoding CANNOT represent a first occurrence that precedes
    the owner's first payday, so authoring one would test a mismatch rather
    than an inverse.  The mismatch is real and it is not swept away --
    :func:`test_a_start_below_the_opening_is_the_shape_the_downgrade_refuses`
    drives it directly, as the firing control for
    ``b6d41f0a9c27.downgrade``'s refusal.

    Args:
        calendar: The owner's :class:`PayCalendar`.
        bounds: The reference dates from :func:`_bounds_for`.

    Yields:
        ``(interval_n, unit, placement, starts_on, nominal_day)`` tuples.
    """
    opening = calendar.opening_bound()
    for interval_n, unit, placement in _CADENCES:
        for bound in bounds:
            # ``None`` was the old matrix's "states no bound" arm, and what the
            # retired derivation answered for it was the schedule's opening.
            floor = opening if bound is None else max(bound, opening)
            if unit is RecurrenceUnitEnum.PERIOD:
                yield interval_n, unit, placement, floor, None
                continue
            if placement is PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER:
                # MONTHLY_FIRST is the ONE cadence whose first occurrence the
                # closed set cannot encode as a day: the retired derivation
                # scanned the schedule's months and answered a month's FIRST,
                # never a day the rule states.  A start on any other day
                # round-trips to the 1st of the following qualifying month --
                # measured, 2026-03-15 comes back 2026-04-01 -- so the matrix
                # authors it where the encoding is faithful, which is also
                # where all five live rules of this shape sit.
                yield interval_n, unit, placement, opening.replace(day=1), None
                continue
            # The day and month coordinates are read only by the calendar
            # family; varying them for the other two would author the same rule
            # many times over and say nothing new.  Rotating rather than taking
            # the cross product keeps the matrix at a size the suite can afford
            # while still reaching every day and every month on some cadence.
            months = (
                _MONTHS if interval_n * (
                    12 if unit is RecurrenceUnitEnum.YEAR else 1
                ) > 1 else (None,)
            )
            for day in _DAYS:
                for month in months:
                    starts_on = _on_or_after(floor, day, month)
                    yield (
                        interval_n, unit, placement, starts_on,
                        # ``nominal_day`` where the month CLAMPED the day, which
                        # is what makes the pair the CHECK admits and what keeps
                        # the poison's two directions both exercised.
                        day if day > starts_on.day else None,
                    )


def _on_or_after(floor: date, day: int, month: int | None) -> date:
    """Return the first date matching ``(day, month)`` at or after *floor*.

    The test-authoring translation of "a quarterly bill on the 31st, in
    January" into the date such a rule first fires on -- which is what
    ``starts_on`` holds since plan step R7c-b.

    **It reproduces no application derivation and does not have to.**  The
    first occurrence is AUTHORED now, so this only has to produce a VARIED set
    of real dates at or above the schedule's opening; what the matrix then
    grades is that the SQL inverts the encoding of whatever date it is handed.
    The day is month-end clamped, which is what puts a ``nominal_day`` on some
    of the rules and keeps that arm of the backfill exercised.

    Args:
        floor: The earliest acceptable date.
        day: The day of the month the cadence fires on, 1-31.
        month: The month of the year the cadence fires in, or ``None`` for a
            cadence that fires every month.

    Returns:
        The :class:`datetime.date` to author.
    """
    def _in(year: int, target_month: int) -> date:
        last = calendar_module.monthrange(year, target_month)[1]
        return date(year, target_month, min(day, last))

    candidate = _in(floor.year, floor.month if month is None else month)
    if candidate >= floor:
        return candidate
    if month is not None:
        return _in(floor.year + 1, month)
    return _in(
        floor.year + floor.month // MONTHS_PER_YEAR,
        floor.month % MONTHS_PER_YEAR + 1,
    )


def _encode_legacy_columns(rules) -> None:
    """Write the closed-set coordinates R7c-a's write door wrote.

    **The inputs this file's subject reads**, and the reason they have to be
    planted rather than authored.  :data:`_BACKFILL.BACKFILL_SQL` derives the
    two-axis columns from ``(start_date, day_of_month, month_of_year)``, and
    plan step R7c-b's write door writes only the third of those -- so a rule
    authored today carries two thirds of the derivation's input missing, and
    running the statement over it would answer the schedule's opening rather
    than the rule's own date.  (That is not hypothetical: it is exactly why
    ``b6d41f0a9c27.downgrade`` refuses.)

    What is written is the ENCODING of each rule's stored first occurrence,
    which is what R7c-a's door produced for the same rule: the date itself as
    the opening bound, and its own month as the cycle's residue class.  The
    matrix below then asserts that the SQL is that encoding's INVERSE, which is
    the property the re-backfill actually needs -- run over rows carrying a
    faithful legacy encoding, it must reproduce the two-axis state rather than
    move it.

    ``day_of_month`` is left ALONE: the R7c-b door already writes it, from the
    same accessor R7c-a's did, and overwriting it here would replace the
    subject with a copy of itself.

    Args:
        rules: The flushed rules to encode.
    """
    db.session.execute(
        sa.text(
            "UPDATE budget.recurrence_rules SET "
            "start_date = starts_on, "
            "month_of_year = CASE WHEN unit_id IN ("
            "  SELECT id FROM ref.recurrence_units WHERE name IN "
            "  ('month', 'year')) "
            "  THEN EXTRACT(month FROM starts_on)::int END "
            "WHERE id IN :ids"
        ).bindparams(sa.bindparam("ids", expanding=True)),
        {"ids": [rule.id for rule in rules]},
    )
    db.session.flush()


def _authored_matrix(user_id: int, calendar, bounds):
    """Author one rule per (cadence, day, month, bound) the shape allows.

    Args:
        user_id: The owner.
        calendar: The owner's :class:`PayCalendar`.
        bounds: The reference dates from :func:`_bounds_for`.

    Returns:
        list[RecurrenceRule]: every flushed rule, legacy columns encoded.
    """
    rules = [
        author_rule(
            RecurrenceSpec(
                user_id=user_id,
                unit=unit,
                interval_n=interval_n,
                placement=placement,
                starts_on=starts_on,
                nominal_day=nominal_day,
            ),
            calendar,
        )
        for interval_n, unit, placement, starts_on, nominal_day
        in _first_occurrences(calendar, bounds)
    ]
    db.session.flush()
    _encode_legacy_columns(rules)
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
        "starts_on": resolved.starts_on,
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

    ``2001-02-28`` paired with ``29`` is the shape plan step R7c-b's two new
    CHECKs leave for a poison.  ``ck_recurrence_rules_nominal_day`` gained the
    clamp equality -- the date must be its own month's last day and the value
    must be what that month clamped -- so ``1999-01-01``, which R7c-a's weaker
    two-conjunct form admitted because 29 merely exceeded its day, is refused;
    and ``ck_recurrence_rules_starts_on_range`` bounds the date to
    2000-01-01..2100-12-31, so the year has to move too.  A NON-LEAP February
    is a real clamp of a 29, and no rule this matrix authors lands in 2001, so
    the pair is both storable and wrong.

    Args:
        rule_ids: The rules to poison.
    """
    db.session.execute(
        sa.text(
            "UPDATE budget.recurrence_rules SET "
            "starts_on = DATE '2001-02-28', "
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
    db.session.execute(sa.text(_BACKFILL.BACKFILL_SQL))
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
            placement=PeriodPlacementEnum.CONTAINING_DATE,
            starts_on=date(2026, 1, 15),
        ),
        calendar,
    )
    expected = _expected(rule, calendar)
    assert expected["starts_on"] == date(2026, 1, 15)

    _poison([rule.id])
    db.session.expire_all()
    poisoned = db.session.get(RecurrenceRule, rule.id)
    assert poisoned.starts_on == date(2001, 2, 28)
    assert poisoned.nominal_day == 29
    assert poisoned.unit_id != expected["unit_id"]
    assert poisoned.placement_id != expected["placement_id"]
    assert poisoned.shift_id != expected["shift_id"]


#: What each column looks like at R7c-b's head: four TIGHTENED, one nullable.
#:
#: R7c-a added all five nullable and R7c-b tightens the four every reader now
#: takes -- the documented three-step (add nullable, backfill, tighten), with
#: the tighten in the leaf whose readers make a NULL matter.  Asserting the
#: shape rather than assuming it is what replaced
#: ``test_recurrence_anchor_subtypes_migration``'s
#: ``TestTheDerivedColumnsAreAbsent``, whose own docstring said this step
#: deletes it: that test proved the columns were absent, and something has to
#: prove they arrived.
#:
#: ``nominal_day`` stays ``YES`` and always will.  Its ABSENCE is the
#: discriminator (ruling **R-R3**) -- it means "the date holds the day itself"
#: -- so tightening it would delete the ordinary case rather than a defect.
_EXPECTED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("unit_id", "integer", "NO"),
    ("placement_id", "integer", "NO"),
    ("shift_id", "integer", "NO"),
    ("starts_on", "date", "NO"),
    ("nominal_day", "smallint", "YES"),
)


@pytest.mark.usefixtures("app")
def test_the_five_columns_carry_the_shape_r7c_b_tightens_them_to(db):  # pylint: disable=redefined-outer-name
    """Each column is present, correctly typed, and NOT NULL where it must be.

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
        # The three id columns are stated because plan step R7c-b made them
        # NOT NULL: without them the INSERT dies on a null before the CHECK
        # under test is evaluated, and both failures are IntegrityError.
        db.session.execute(sa.text(
            "INSERT INTO budget.recurrence_rules "
            "(user_id, pattern_id, interval_n, offset_periods, "
            " unit_id, placement_id, shift_id, starts_on, nominal_day) "
            "VALUES (:uid, :pid, 1, 0, :unit, :placement, :shift, "
            "        :starts_on, :nominal_day)"
        ), {
            "uid": bare_user["user"].id,
            "pid": _ref_id("recurrence_patterns", "Monthly"),
            "unit": _ref_id("recurrence_units", "month"),
            "placement": _ref_id("period_placements", "containing_date"),
            "shift": _ref_id("business_day_shifts", "none"),
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
            starts_on=date(2026, 6, 1),
        ),
        calendar,
    )
    db.session.flush()
    _encode_legacy_columns([rule])
    expected = _expected(rule, calendar)
    assert expected["starts_on"] > calendar.horizon(), (
        "the bound no longer lands past the horizon, so this case stopped "
        "exercising the projection arm that reads the cadence"
    )

    # Nothing to refuse, and the backfill reproduces the app's answer.
    _BACKFILL.refuse_underivable(db.session)
    _poison([rule.id])
    db.session.execute(sa.text(_BACKFILL.BACKFILL_SQL))
    db.session.flush()
    _BACKFILL.verify_backfilled(db.session)
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
        # Storable at THIS head (plan step R7c-b's NOT NULLs) so the row
        # exists for the refusal to find.  What makes it underivable is the
        # OWNER's empty schedule, which no column of the rule can express.
        unit_id=_ref_id("recurrence_units", "period"),
        placement_id=_ref_id("period_placements", "containing_date"),
        shift_id=_ref_id("business_day_shifts", "none"),
        starts_on=date(2026, 1, 1),
    ))
    db.session.flush()

    with pytest.raises(RuntimeError) as caught:
        _BACKFILL.refuse_underivable(db.session)

    message = str(caught.value)
    assert "cannot derive a first occurrence" in message
    assert "owner has no pay periods" in message


@pytest.mark.usefixtures("app")
def test_the_verify_half_catches_a_row_the_backfill_missed(db, bare_user):  # pylint: disable=redefined-outer-name
    """A NULL left behind is NAMED, not shipped as a successful upgrade.

    The three-step's verify half, driven on a row planted to look exactly like
    what a ``ref`` seed short a row would leave: every input arm green, the
    ``UPDATE`` unable to resolve an id, and no constraint to catch it.

    **The column is loosened first, and that is the case rather than a
    workaround.**  ``verify_backfilled`` runs inside BOTH revisions, and at
    ``f2a94c7e1b60`` -- where it does its real work -- the four columns are
    still nullable; plan step R7c-b's tighten lands a leaf later, which is the
    documented three-step's whole point.  Planting the row at this head would
    therefore be impossible, and skipping the case would leave the guard
    untested on the chain replayed from base, which is where it fires.  The
    ``db`` fixture drops and re-clones this worker's database per test, so the
    ``ALTER`` cannot outlive the case.

    Args:
        db: The session fixture.
        bare_user: The owner.
    """
    pay_period_write.record_paydays(
        user_id=bare_user["user"].id, first_payday=date(2026, 1, 2),
        num_periods=6, cadence_days=14,
    )
    db.session.flush()
    db.session.execute(sa.text(
        "ALTER TABLE budget.recurrence_rules ALTER COLUMN unit_id DROP NOT NULL"
    ))
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
    _BACKFILL.refuse_underivable(db.session)

    with pytest.raises(RuntimeError) as caught:
        _BACKFILL.verify_backfilled(db.session)
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
        sa.text(_BACKFILL.REFUSE_UNDERIVABLE_SQL),
    ).all()
    named = {row.id: row.reason for row in rows}
    assert rule.id in named, (
        "a rule whose owner has no pay periods must be named by the "
        "migration's refusal query, or the NOT NULL fails with no diagnosis"
    )
    assert named[rule.id] == "owner has no pay periods"


@pytest.mark.usefixtures("app")
def test_a_start_below_the_opening_is_the_shape_the_downgrade_refuses(db, bare_user):  # pylint: disable=redefined-outer-name
    """The one state the closed encoding cannot hold, driven rather than argued.

    **The firing control for ``b6d41f0a9c27.downgrade``'s refusal.**  That
    function raises rather than reverting, and its whole justification is that
    re-running :data:`_BACKFILL.BACKFILL_SQL` over rules THIS revision's write
    door authored would move their first occurrences.  A refusal justified by a
    claim nobody executes is a refusal nobody knows is warranted, so the claim
    is measured here.

    Two things make the mismatch, and both are properties of the closed
    encoding rather than of the SQL:

    * ``GREATEST(s.opening, r.start_date)`` -- the encoding cannot represent a
      first occurrence BEFORE the owner's first payday, so a rule authored
      there is re-derived from the opening instead;
    * the R7c-b write door writes neither ``start_date`` nor ``month_of_year``,
      so on a real re-upgrade there is not even a stale bound to clamp -- the
      derivation starts from the opening for every rule.

    This drives the second, which is the deployed shape: the rule is authored
    and NOT encoded, exactly as the door leaves it.

    Args:
        db: The session fixture.
        bare_user: The owner.
    """
    user_id = bare_user["user"].id
    pay_period_write.record_paydays(
        user_id=user_id, first_payday=date(2026, 1, 2),
        num_periods=26, cadence_days=14,
    )
    db.session.flush()
    rule = author_rule(
        RecurrenceSpec(
            user_id=user_id,
            unit=RecurrenceUnitEnum.MONTH,
            placement=PeriodPlacementEnum.CONTAINING_DATE,
            starts_on=date(2026, 9, 15),
        ),
        calendar_for(user_id),
    )
    db.session.flush()
    assert rule.start_date is None, (
        "the R7c-b write door started writing start_date again, which would "
        "make b6d41f0a9c27's downgrade refusal unnecessary -- re-check it "
        "rather than deleting this case"
    )

    db.session.execute(sa.text(_BACKFILL.BACKFILL_SQL))
    db.session.flush()
    db.session.expire_all()

    # 2026-01-15: the 15th of the SCHEDULE's opening month, eight months
    # earlier than the date the user authored.  Every month between would
    # generate a backdated row into a pay period that has already closed.
    assert db.session.get(RecurrenceRule, rule.id).starts_on == date(
        2026, 1, 15,
    )


# ── The R7c-b migration's own pre-DDL refusals ───────────────────────
#
# ``b6d41f0a9c27`` names each row a new CHECK would refuse BEFORE running the
# ``ALTER TABLE``, because ``ADD CHECK`` reports the table and the constraint
# and never the row: an operator meeting it mid-deploy would have a failed
# migration and no way to see which rule to repair.  Both guards are FUNCTIONS
# for the reason ``refuse_underivable`` is -- so a test can drive them without
# running real DDL inside an xdist worker -- and their own docstring says so.
#
# They had no caller outside the migration until these cases, which makes them
# refusals nobody had seen work.


@pytest.mark.usefixtures("app")
def test_the_nominal_day_guard_names_the_rule_before_the_ddl_runs(db, bare_user):  # pylint: disable=redefined-outer-name
    """``refuse_inconsistent_nominal_days`` finds the row and says which.

    The state is unreachable through the application -- R7c-a's backfill
    writes the day only where it exceeds the date's, and every write door
    refuses the pair -- so anything this finds was hand-edited or restored.
    That is exactly why it must be driven from a test: it can never fire in
    the suite by accident.

    Planted with the CHECK dropped, because the completed constraint refuses
    the row this guard exists to REPORT: it is the migration's pre-flight, so
    the state it grades is one that exists only before the DDL runs.

    Args:
        db: The session fixture.
        bare_user: The owner.
    """
    db.session.execute(sa.text(
        "ALTER TABLE budget.recurrence_rules "
        "DROP CONSTRAINT ck_recurrence_rules_nominal_day"
    ))
    # April 15 was never clamped by anything, so a nominal day beside it names
    # a day the rule does not fire on -- the pair the completed CHECK closed.
    rule_id = db.session.execute(sa.text(
        "INSERT INTO budget.recurrence_rules "
        "(user_id, pattern_id, interval_n, offset_periods, "
        " unit_id, placement_id, shift_id, starts_on, nominal_day) "
        "VALUES (:uid, :pid, 1, 0, :unit, :placement, :shift, "
        "        DATE '2026-04-15', 30) RETURNING id"
    ), {
        "uid": bare_user["user"].id,
        "pid": _ref_id("recurrence_patterns", "Monthly"),
        "unit": _ref_id("recurrence_units", "month"),
        "placement": _ref_id("period_placements", "containing_date"),
        "shift": _ref_id("business_day_shifts", "none"),
    }).scalar_one()
    db.session.flush()

    with pytest.raises(RuntimeError) as caught:
        _R7CB.refuse_inconsistent_nominal_days(db.session)

    message = str(caught.value)
    assert f"id={rule_id}" in message, "the refusal must name the offending rule"
    assert "2026-04-15" in message and "nominal_day=30" in message, (
        "and both halves of the pair, or an operator cannot repair it"
    )
    db.session.rollback()


@pytest.mark.usefixtures("app")
def test_the_nominal_day_guard_admits_a_real_clamp(db, bare_user):  # pylint: disable=redefined-outer-name
    """The control: the guard grades the pair, not the presence of a day.

    April has no 31st, so ``(2026-04-30, 31)`` is precisely the state
    ``nominal_day`` exists for.  Without this arm a guard that refused every
    populated ``nominal_day`` -- or one whose query matched every row -- would
    pass the case above and abort a deploy on healthy data.

    Args:
        db: The session fixture.
        bare_user: The owner.
    """
    db.session.execute(sa.text(
        "INSERT INTO budget.recurrence_rules "
        "(user_id, pattern_id, interval_n, offset_periods, "
        " unit_id, placement_id, shift_id, starts_on, nominal_day) "
        "VALUES (:uid, :pid, 1, 0, :unit, :placement, :shift, "
        "        DATE '2026-04-30', 31)"
    ), {
        "uid": bare_user["user"].id,
        "pid": _ref_id("recurrence_patterns", "Monthly"),
        "unit": _ref_id("recurrence_units", "month"),
        "placement": _ref_id("period_placements", "containing_date"),
        "shift": _ref_id("business_day_shifts", "none"),
    })
    db.session.flush()

    # Must not raise; the assertion is the absence of the refusal.
    _R7CB.refuse_inconsistent_nominal_days(db.session)
    db.session.rollback()


@pytest.mark.usefixtures("app")
def test_the_calendar_window_guard_names_the_rule_before_the_ddl_runs(db, bare_user):  # pylint: disable=redefined-outer-name
    """``refuse_out_of_range_starts`` finds a date past the app's calendar.

    ``ck_recurrence_rules_starts_on_range`` backs a MEASURED 500: past the
    saved horizon the pay calendar projects the covering paycheck by adding
    ``cadence_days`` to a start, so a date near ``date.max`` raises
    ``OverflowError`` from outside the recurrence package's error hierarchy.
    This is what names the rule that would block the constraint being added.

    Args:
        db: The session fixture.
        bare_user: The owner.
    """
    db.session.execute(sa.text(
        "ALTER TABLE budget.recurrence_rules "
        "DROP CONSTRAINT ck_recurrence_rules_starts_on_range"
    ))
    rule_id = db.session.execute(sa.text(
        "INSERT INTO budget.recurrence_rules "
        "(user_id, pattern_id, interval_n, offset_periods, "
        " unit_id, placement_id, shift_id, starts_on) "
        "VALUES (:uid, :pid, 1, 0, :unit, :placement, :shift, "
        "        DATE '9999-12-31') RETURNING id"
    ), {
        "uid": bare_user["user"].id,
        "pid": _ref_id("recurrence_patterns", "Monthly"),
        "unit": _ref_id("recurrence_units", "month"),
        "placement": _ref_id("period_placements", "containing_date"),
        "shift": _ref_id("business_day_shifts", "none"),
    }).scalar_one()
    db.session.flush()

    with pytest.raises(RuntimeError) as caught:
        _R7CB.refuse_out_of_range_starts(db.session)

    message = str(caught.value)
    assert f"id={rule_id}" in message
    assert "9999-12-31" in message


@pytest.mark.usefixtures("app")
def test_the_calendar_window_guard_admits_a_date_inside_it(db, bare_user):  # pylint: disable=redefined-outer-name
    """The control: a rule inside 2000-2100 passes, so a deploy is not blocked.

    The 46 live rules span 2026-03-01 to 2027-03-16, comfortably inside the
    window; a guard that refused them would abort every deploy.

    Args:
        db: The session fixture.
        bare_user: The owner.
    """
    db.session.execute(sa.text(
        "INSERT INTO budget.recurrence_rules "
        "(user_id, pattern_id, interval_n, offset_periods, "
        " unit_id, placement_id, shift_id, starts_on) "
        "VALUES (:uid, :pid, 1, 0, :unit, :placement, :shift, "
        "        DATE '2026-03-01')"
    ), {
        "uid": bare_user["user"].id,
        "pid": _ref_id("recurrence_patterns", "Monthly"),
        "unit": _ref_id("recurrence_units", "month"),
        "placement": _ref_id("period_placements", "containing_date"),
        "shift": _ref_id("business_day_shifts", "none"),
    })
    db.session.flush()

    # Must not raise; the assertion is the absence of the refusal.
    _R7CB.refuse_out_of_range_starts(db.session)
    db.session.rollback()


@pytest.mark.usefixtures("app")
def test_the_first_of_month_fallback_arm_is_reached_and_correct(db, bare_user):  # pylint: disable=redefined-outer-name
    """``BACKFILL_SQL``'s ``COALESCE`` branch, which the matrix cannot reach.

    The ``monthly_first`` arm answers "the 1st of the earliest month whose own
    first payday falls on or after the effective start", and ``COALESCE``\\ s a
    fallback under it: "the 1st of the month AFTER the effective one", for when
    the schedule reaches NO such month.

    :func:`_authored_matrix` authors every ``MONTHLY_FIRST`` rule at the 1st of
    the schedule's OPENING month, because that is the only shape the closed set
    encodes faithfully -- and it is therefore always inside the schedule, so
    the subquery always matches and the fallback is dead to the whole matrix.
    Plan step R7c-b re-runs this statement against production data, so a branch
    with no test is a branch nobody has seen answer.

    Reached by planting a rule whose first occurrence is a YEAR past the last
    payday.  The expected date is computed here from the SQL's own stated rule
    rather than copied from its output, so the case grades the branch instead
    of recording it.

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
    beyond = calendar.horizon() + timedelta(days=365)
    # The 1st of the month AFTER the effective start -- the fallback's own
    # rule, and the reason the effective start is the authored date: it is
    # already above the schedule's opening, so ``GREATEST`` keeps it.
    expected = (
        date(beyond.year + 1, 1, 1) if beyond.month == 12
        else date(beyond.year, beyond.month + 1, 1)
    )

    rule = author_rule(
        RecurrenceSpec(
            user_id=user_id,
            unit=RecurrenceUnitEnum.MONTH,
            placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            starts_on=beyond,
        ),
        calendar,
    )
    db.session.flush()
    _encode_legacy_columns([rule])

    db.session.execute(sa.text(_BACKFILL.BACKFILL_SQL))
    db.session.flush()
    db.session.refresh(rule)

    assert rule.starts_on == expected, (
        "the COALESCE fallback must answer the 1st of the month after the "
        "effective start when the schedule reaches no qualifying month"
    )
    # The premise, asserted rather than assumed: without it a schedule that
    # happened to cover ``beyond`` would send this down the subquery arm and
    # the case would grade the branch it is not about.
    assert beyond > calendar.horizon()
