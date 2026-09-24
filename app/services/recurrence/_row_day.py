"""
Shekel Budget App -- The DAY a generated row carries, from a cadence's own coordinates

The pure core behind :func:`~app.services.recurrence.compute_due_date` and
:func:`~app.services.recurrence.scheduling_day_of_month`: which day of the
month a cadence schedules its rows on (:func:`cadence_scheduled_day`), and the
calendar day a row placed in a pay period is then due (:func:`date_row`).

**Split out at plan step ``pay_calendar:C18-a`` (ruling R-PC86) so the
occurrence WALK can date a placement exactly as the row would be dated.**
Ruling **R-PC85** made the walk drop an occurrence whose row would land on or
before the books of an account the definition moves money in, and R-PC86 chose
the row's CASH day -- its due day -- as the day compared.  The walk
(``_placement._placements``) holds a :class:`~._resolution.ResolvedRecurrence`,
not a rule row, and could not reach the one producer of that day: it lived in
``_row_date.compute_due_date``, which reads the rule through ``_reading``, and
``_reading`` imports the walk.  Rather than state the dating a second time
over the resolved value, the body MOVED here, below both: ``compute_due_date``
and ``scheduling_day_of_month`` feed it from a rule, the resolved value feeds
it from its own fields, and it is written once (``CLAUDE.md`` rule 14's
placement clause -- move the leaf, not the logic).

Nothing here changed in the move, including the defect: :func:`date_row`
picks the base month from the period's two endpoint months (plan ledger row
**D18**), and plan step **R5** still deletes it with ``compute_due_date``.

Pure: no Flask, no ORM, no clock, no database.  It imports only the leaves
``_resolution`` already imports, so the walk can import it with no cycle.
"""
import calendar as cal
from datetime import date

from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.services.pay_calendar import DerivedPeriod
from app.services.recurrence._nominal_day import cadence_day_of_month
from app.services.recurrence._offer import fires_on_day_of_month


def cadence_scheduled_day(
    unit: RecurrenceUnitEnum,
    placement: PeriodPlacementEnum,
    starts_on: date,
    nominal_day: int | None,
) -> int | None:
    """Return the day of the month a cadence's generated rows are SCHEDULED on.

    **The gate and the join, stated once** -- the body
    :func:`~app.services.recurrence.scheduling_day_of_month` carried until
    plan step ``pay_calendar:C18-a`` moved it here so a resolved value can ask
    it too.  The day the cadence fires on is
    :func:`~app.services.recurrence._nominal_day.cadence_day_of_month` (the
    join of *starts_on*'s day with *nominal_day*, the same join
    :attr:`~app.services.recurrence.ResolvedRecurrence.day_of_month` reads),
    gated on the ``(unit, placement)`` PAIR by
    :func:`~app.services.recurrence._offer.fires_on_day_of_month`: a MONTH
    rule funded from a month's first paycheck fires on a day of the month but
    its ROW is dated from the paycheck, which is what ``None`` here means.
    See ``scheduling_day_of_month`` for why the gate is that predicate and not
    ``has_day_of_month_coordinate``.

    **It asks no refusal.**  A unit whose occurrences no generated row can
    carry the date of (the ``WEEK`` unit) is refused by the caller FIRST --
    ``scheduling_day_of_month`` and
    :meth:`~app.services.recurrence.ResolvedRecurrence.row_date` both ask
    :func:`~app.services.recurrence._offer.require_row_date_coordinate`
    before this -- because that refusal is a property of the unit alone and
    is asked before the placement is consulted.

    Args:
        unit: The cadence unit.
        placement: Which pay period funds an occurrence.
        starts_on: The rule's first occurrence.
        nominal_day: The day the rule means when *starts_on*'s month was too
            short to hold it, or ``None``.

    Returns:
        The day 1-31 the cadence's rows are scheduled on, or ``None`` for a
        cadence whose rows are dated from their PAYCHECK.
    """
    if not fires_on_day_of_month(unit, placement):
        return None
    return cadence_day_of_month(unit, starts_on, nominal_day)


def date_row(
    scheduled_day: int | None,
    due_day_of_month: int | None,
    period: DerivedPeriod,
) -> date:
    """Return the calendar day a row scheduled on *scheduled_day* in *period* is due.

    **The body of** :func:`~app.services.recurrence.compute_due_date`,
    **moved rather than restated** (plan step ``pay_calendar:C18-a``).  That
    function reads a rule's two coordinates and hands them here; the
    occurrence walk reads a resolved value's two and hands them here too, so
    the day a row carries and the day the walk bounds by the books (ruling
    **R-PC86**) cannot come apart.

    Source priority:
      1. *due_day_of_month* (if set and it differs from the scheduling day)
      2. *scheduled_day*, placed within the period's month context
      3. ``period.start_date`` (for a cadence that names no day of the month)

    Next-month convention: if *due_day_of_month* < *scheduled_day*, the due
    date falls in the following calendar month.  Example: a rule scheduled on
    the 22nd with a due day of 1 is due on the 1st of the month after the
    scheduling month.  Month-end clamping: a day past the month's last day is
    clamped (day 31 in April becomes 30).

    Args:
        scheduled_day: The day the cadence schedules rows on, from
            :func:`cadence_scheduled_day`, or ``None``.
        due_day_of_month: The real bill due day when it differs from the
            scheduling day, or ``None``.
        period: The :class:`~app.services.pay_calendar.DerivedPeriod` the row
            is placed in -- saved, or PROJECTED past the horizon.

    Returns:
        The due day.
    """
    # A cadence that names no day of the month -- every-paycheck, every-N, and
    # a monthly rule funded from the month's first paycheck -- is dated from
    # its period's start.
    if scheduled_day is None:
        return period.start_date

    # Determine the base month by finding which month within the period
    # contains the scheduling-day target.  This is the LAST reader of the
    # endpoint-month scan plan step R4a deleted from period selection, and it
    # carries the same defect: at a cadence where the firing month is neither
    # endpoint the row is dated in the wrong month entirely (plan ledger row
    # D18).  Plan step R5 owns it, with the due-date model it rewrites.
    #
    # The containment test is the PERIOD's own rule since pay-calendar plan
    # step C4-a-3 (``DerivedPeriod.covers``, ruling R-PC31); it was
    # ``period.start_date <= target <= period.end_date`` open-coded here, one
    # of the three sites that spelled it out.
    base_year = period.start_date.year
    base_month = period.start_date.month

    for dt in (period.start_date, period.end_date):
        last_day = cal.monthrange(dt.year, dt.month)[1]
        target_day = min(scheduled_day, last_day)
        target = date(dt.year, dt.month, target_day)
        if period.covers(target):
            base_year = dt.year
            base_month = dt.month
            break

    if due_day_of_month is None or due_day_of_month == scheduled_day:
        # No separate due date -- use the scheduling day in the base month.
        last_day = cal.monthrange(base_year, base_month)[1]
        return date(base_year, base_month, min(scheduled_day, last_day))

    # Next-month convention: a due day before the scheduling day means the
    # due date falls in the month after the scheduling month.
    if due_day_of_month < scheduled_day:
        if base_month == 12:
            due_year = base_year + 1
            due_month = 1
        else:
            due_year = base_year
            due_month = base_month + 1
    else:
        due_year = base_year
        due_month = base_month

    last_day = cal.monthrange(due_year, due_month)[1]
    return date(due_year, due_month, min(due_day_of_month, last_day))
