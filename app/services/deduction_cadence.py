"""Shekel Budget App -- A payroll deduction's cadence, in the words a page shows.

Plan step **salary:R15-b** (rulings **R-SAL3**, **R-SAL32**): a deduction's
frequency is a recurrence rule on the row, or no rule for *every paycheck*,
and the salary page's Frequency cell states it through the ONE producer of a
recurrence's phrase, :func:`~app.services.recurrence.describe` -- so a
24-per-year line reads exactly as the recurring surface would read the same
rule ("Every paycheck (at most 2 a month)").  The three hand-worded labels
the cell carried until this step ("26x/yr (every paycheck)", "24x/yr (skip
3rd paycheck)", "12x/yr (monthly)") were a second spelling of a vocabulary
the recurrence package already words.

Flask-free: takes the rows and the owner's calendar, returns strings.
"""

from app.services.pay_calendar import PayCalendar
from app.services.recurrence import (
    RecurrenceResolutionError,
    describe,
    resolved_recurrence,
)

#: What a line with no rule reads as: R-SAL3's NULL, every paycheck.  The
#: recurrence package words the same cadence identically for a rule that
#: states it, so the two cannot drift.
EVERY_PAYCHECK = "Every paycheck"


def cadence_phrase(deduction, calendar: PayCalendar | None) -> str:
    """The phrase the salary page shows for how often *deduction* is taken.

    Args:
        deduction: A :class:`~app.models.paycheck_deduction.PaycheckDeduction`
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
