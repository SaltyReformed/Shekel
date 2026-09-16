"""Shekel Budget App -- What a payroll line's cadence MEANS, stated once.

``deduction_cadence`` until plan step salary:R18-b, when the earning kinds
joined the lines it words (ruling **R-SAL38**); every function here reads a
line of any kind the same way.

Plan step **salary:R15-b** (rulings **R-SAL3**, **R-SAL32**): a deduction's
frequency is a recurrence rule on the row, or no rule for *every paycheck*,
and the salary page's Frequency cell states it through the ONE producer of a
recurrence's phrase, :func:`~app.services.recurrence.describe` -- so a
24-per-year line reads exactly as the recurring surface would read the same
rule ("Every paycheck (at most 2 a month)").  The three hand-worded labels
the cell carried until this step ("26x/yr (every paycheck)", "24x/yr (skip
3rd paycheck)", "12x/yr (monthly)") were a second spelling of a vocabulary
the recurrence package already words.

**Plan step salary:R15-c added the two facts the deduction FORM needs that a
template form does not**, because a payroll line's rule is authored with
fewer controls than a bill's (ruling **R-SAL31**):

* :func:`first_occurrence` -- where a line's rule STARTS, which the form
  never asks (ruling **R-SAL30**: a payroll line has no effective date; it
  is taken from the owner's opening payday).  For a calendar cadence the
  first occurrence is also the cycle's DAY, and the day is the unit's own
  zero at the opening (ruling **R-SAL36**, developer 2026-09-14): the 1st of
  the opening payday's month for a monthly line, so "every month, funded
  from the first paycheck on or after" is the month's FIRST paycheck --
  exactly the shape migration ``542c61e48ee8`` wrote for a 12 and the only
  one its downgrade reads back.  A monthly line anchored on the opening
  payday's own day would be "monthly on the 15th", the month's second
  paycheck most months, an accident of when the schedule was generated.
* :func:`is_every_paycheck` -- the ONE spelling of every paycheck, which is
  NO rule (ruling **R-SAL29**: "26 = no rule, and a rule that would fire
  every paycheck with no ceiling canonicalises to no rule at the door", the
  shape ruling **R-R17** gives an annual cadence spelled in months).  A
  deduction-specific rule, deliberately: for a bill "every 1 paycheck" is a
  cadence and "no rule" is *does not repeat*, so the recurrence package's
  own door cannot canonicalise it and this module holds it beside the
  phrase that reads the NULL back.

Flask-free: takes the rows, the owner's calendar and plain values; returns
strings, dates and booleans.
"""

from datetime import date

from app.enums import RecurrenceUnitEnum
from app.services.pay_calendar import PayCalendar
from app.services.recurrence import (
    RecurrenceResolutionError,
    canonical_cadence,
    describe,
    resolved_recurrence,
)

#: What a line with no rule reads as: R-SAL3's NULL, every paycheck.  The
#: recurrence package words the same cadence identically for a rule that
#: states it, so the two cannot drift.
EVERY_PAYCHECK = "Every paycheck"

#: Each authorable unit's ZERO at the owner's opening payday (ruling
#: **R-SAL36**): the opening payday itself for a paycheck cadence, the 1st of
#: its month for a monthly one, January 1st of its year for a yearly one.  A
#: dict rather than an ``if`` chain so a unit the picker starts offering
#: (``WEEK``, plan step R8-b) raises at the lookup rather than silently
#: taking a neighbour's day -- the idiom
#: ``cash_ledger._amount_rule`` records for its rule table.
_ZERO_AT_THE_OPENING = {
    RecurrenceUnitEnum.PERIOD: lambda opening: opening,
    RecurrenceUnitEnum.MONTH: lambda opening: opening.replace(day=1),
    RecurrenceUnitEnum.YEAR: lambda opening: opening.replace(month=1, day=1),
}


def first_occurrence(
    unit: RecurrenceUnitEnum, interval_n: int, calendar: PayCalendar,
) -> date:
    """The day a deduction rule of this cadence first fires: its unit's zero at the opening.

    **The unit the rule is STORED with, not the one the form spelled.**  The
    write door canonicalises a whole number of years authored in months to
    the ``YEAR`` unit (ruling **R-R17**, :func:`~app.services.recurrence
    .canonical_cadence`), and the edit form then reads that unit back -- so
    a zero derived off the stated ``MONTH`` for "every 12 months" would be
    the 1st of the opening month on the create and January 1st on the next
    amount-only edit, which is the R7c-c / **D1** defect class (an unrelated
    edit re-phasing a rule) reached through the free interval box.  An
    adversarial review of plan step salary:R15-c traced it on a March
    opening; deriving off the canonical unit is what makes the stored
    ``starts_on`` a function of the stored cadence alone, so the same
    submission and its own read-back derive the same day.

    Args:
        unit: The cadence unit the form chose.
        interval_n: Its interval, which decides the canonical unit.
        calendar: The owner's pay calendar.

    Returns:
        The opening payday for ``PERIOD``; the 1st of its month for
        ``MONTH``; January 1st of its year for ``YEAR`` (ruling **R-SAL36**)
        -- each read off the CANONICAL unit.

    Raises:
        RecurrenceResolutionError: The calendar holds no saved pay period, so
            there is no opening payday to start from -- the profile's own
            paycheck rule was authored from one, so an empty schedule here is
            a broken invariant rather than a state to paper over (the same
            refusal ``recurrence.resolve`` makes for a paycheck cadence).
        KeyError: The canonical unit is one this module states no zero for
            -- the picker offers the three above and no other (ruling
            **R-SAL37**), so a fourth reaching here is a new unit that owes
            this table an entry.
    """
    opening = calendar.opening_bound()
    if opening is None:
        raise RecurrenceResolutionError(
            f"user {calendar.user_id} has no pay periods, so a payroll "
            f"deduction's rule has no opening payday to start from (ruling "
            f"R-SAL30); the profile's paycheck rule was authored from one, so "
            f"an empty schedule here is a broken invariant."
        )
    stored_unit = canonical_cadence(interval_n, unit).unit
    return _ZERO_AT_THE_OPENING[stored_unit](opening)


def is_every_paycheck(
    unit: RecurrenceUnitEnum, interval_n: int, max_per_month: int | None,
) -> bool:
    """Whether a submitted cadence is the every-paycheck spelling, which is NO rule.

    Exactly ``every 1 paycheck`` with no ceiling (ruling **R-SAL29**).  A
    ceiling that happens not to bind at the owner's pay cadence today (at
    most 3 a month on a biweekly schedule) is NOT folded in: it states what
    the owner said, and it binds the day the schedule turns weekly.

    Args:
        unit: The cadence unit the form chose.
        interval_n: Its interval.
        max_per_month: Its per-month ceiling, or ``None`` for none.

    Returns:
        ``True`` for the one spelling a line with no rule already means.
    """
    return (
        unit is RecurrenceUnitEnum.PERIOD
        and interval_n == 1
        and max_per_month is None
    )


def cadence_phrase(deduction, calendar: PayCalendar | None) -> str:
    """The phrase the salary page shows for how often *deduction* is taken.

    Args:
        deduction: A :class:`~app.models.paycheck_line.PaycheckLine`
            (or anything carrying ``recurrence_rule``).
        calendar: The owner's pay calendar, which a rule is resolved against;
            ``None`` when the caller has none, which is only ever the case
            for an owner whose lines carry no rule (a rule is authored against
            a calendar, so a line with one has one).

    Returns:
        :data:`EVERY_PAYCHECK` for a line with no rule, else the rule's
        described cadence.

    Raises:
        ValueError: A line carries a rule and no calendar was given -- the
            caller's contract, stated rather than a ``None`` three frames down.
        RecurrenceResolutionError: The rule cannot be resolved against the
            calendar, or the calendar holds no pay periods -- a rule was
            authored against one, so this is a broken invariant, and
            reporting the line as *every paycheck* would misstate a cadence
            (the recurring surface refuses the same case the same way).
    """
    rule = deduction.recurrence_rule
    if rule is None:
        return EVERY_PAYCHECK
    if calendar is None:
        raise ValueError(
            f"deduction {deduction.id!r} carries a recurrence rule, which can "
            f"only be described against its owner's calendar; the caller "
            f"passed none."
        )
    resolved = resolved_recurrence(rule, calendar)
    if resolved is None:
        raise RecurrenceResolutionError(
            f"deduction {deduction.id!r}'s recurrence rule {rule.id!r} could "
            f"not be resolved against its owner's schedule, which holds no "
            f"pay periods; a rule is authored against one, so this is a "
            f"broken invariant rather than a line taken every paycheck."
        )
    return describe(resolved).cadence


def cadence_phrases(deductions, calendar: PayCalendar | None) -> dict[int, str]:
    """:func:`cadence_phrase` for every row, keyed by deduction id.

    Args:
        deductions: The profile's deduction rows.
        calendar: See :func:`cadence_phrase`.

    Returns:
        ``{deduction id: phrase}``.
    """
    return {
        deduction.id: cadence_phrase(deduction, calendar)
        for deduction in deductions
    }
