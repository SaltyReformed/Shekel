"""
Shekel Budget App -- The DAY a generated row carries, from a cadence's own coordinates

The pure core behind :func:`~app.services.recurrence.compute_due_date` and
:func:`~app.services.recurrence.scheduling_day_of_month`: which day of the
month a cadence schedules its rows on (:func:`cadence_scheduled_day`), and the
calendar day a row answering an occurrence is then due (:func:`date_row`).

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

**:func:`date_row` reads the OCCURRENCE since plan step recurrence:R5-a**
(rulings **R-R94** / **R-R95**).  It moved here unchanged, defect included:
it picked the base month from the period's two endpoint months, so at a pay
cadence where the firing month is neither endpoint the row was dated in the
wrong month, and two occurrences seated in one paycheck of 30 days or more
shared one date (plan ledger row **D18**).  Both callers hold the placement
the walk seated, so the day the cadence names is in hand and the scan is
deleted rather than fixed; the due-day arm went with the rule's
``due_day_of_month`` column (ruling **R-R96**).

Pure: no Flask, no ORM, no clock, no database.  It imports only the leaves
``_resolution`` already imports, so the walk can import it with no cycle.
"""
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
    occurrence: date, period: DerivedPeriod, *, from_paycheck: bool,
) -> date:
    """Return the calendar day a row answering *occurrence* in *period* is due.

    **The body of** :func:`~app.services.recurrence.compute_due_date`,
    **moved rather than restated** (plan step ``pay_calendar:C18-a``).  That
    function reads a rule's coordinates and hands the answer here; the
    occurrence walk reads a resolved value's and hands it here too, so the day
    a row carries and the day the walk bounds by the books (ruling
    **R-PC86**) cannot come apart.

    **Ruling R-R94's formula over the placed occurrence** (plan step
    recurrence:R5-a): a cadence dated from a day of the month is due ON the
    occurrence, and one dated from its paycheck -- every paycheck, every N
    paychecks, a calendar cadence funded from a LATER paycheck -- is due on
    its FUNDING paycheck's payday (ruling **R-R95**).

    Args:
        occurrence: The date the cadence names for this row, off the
            placement that seated it.
        period: The :class:`~app.services.pay_calendar.DerivedPeriod` that
            placement seated it in -- saved, or PROJECTED past the horizon.
            Read for its payday alone.
        from_paycheck: ``True`` when the cadence schedules its rows on no day
            of the month -- :func:`cadence_scheduled_day` answered ``None``,
            which each caller asks after its own refusal.

    Returns:
        The due day.
    """
    if from_paycheck:
        return period.start_date
    return occurrence
