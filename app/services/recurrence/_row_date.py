"""
Shekel Budget App -- The DATE a generated row carries (plan step R16-b-2)

One function, :func:`compute_due_date`: the rule-and-period derivation of the
``due_date`` every row a generate pass writes is stamped with.  It lived in
``recurrence_engine._plan`` from the engine's first day until plan step
R16-b-2 moved it here, and the move is ruling **R-R69**'s (developer,
2026-09-11): a loan's forward plan prices an occurrence NO ROW answers yet
from its definition, and that estimate has to carry the date the row WOULD
carry, or the payoff moves the moment the row is written -- the defect class
the R7d and R16 arcs exist to close.  The balance seam cannot import the
engine (124 modules, the session and the write state machine) for one pure
function, and the engine cannot be handed the seam's question; so the leaf
moves to the tier both can reach (``CLAUDE.md`` rule 14's placement clause).
Every caller was re-pointed in the same commit; the engine re-exports
nothing, so the function has ONE import path.

**It is deleted by plan step R5**, which gives a generated row its own
``occurs_on`` (already there since R17) and ``due_on`` and stops dating a row
from its PERIOD at all -- plan ledger row **D18**: at a pay cadence where the
firing month is neither endpoint of the period, this dates the row in the
wrong month.  Nothing here changed in the move; the defect and its owner are
unchanged too.

Pure: a rule and a :class:`~app.services.pay_calendar.DerivedPeriod` in, a
date out.  No Flask, no ORM query, no clock.  A PROJECTED period (one past
the saved horizon, ``period_id`` ``None``) dates a row exactly as a saved one
does, which is what lets the seam date an occurrence the schedule has not
materialised yet.
"""
import calendar as cal
from datetime import date

from app.models.recurrence_rule import RecurrenceRule
from app.services.pay_calendar import DerivedPeriod
from app.services.recurrence._reading import scheduling_day_of_month


def compute_due_date(rule: RecurrenceRule, period: DerivedPeriod) -> date:
    """Compute the due_date for a generated transaction.

    Derives the calendar date the bill is actually due, using the
    recurrence rule's scheduling day and optional due-day override.
    The transfer engine, the transaction engine, the Recurring surface, the
    carry-forward executor and the balance seam's ESTIMATED loan tier all
    derive a row's due date through this same pure helper, so it is
    deliberately part of this package's public surface (like
    :func:`~app.services.recurrence.rule_occurrences`).

    Source priority:
      1. rule.due_day_of_month (if set and differs from the scheduling day)
      2. the rule's SCHEDULING DAY (placed within the period's month context)
      3. period.start_date (for a cadence that names no day of the month)

    **The scheduling day is DERIVED rather than read off a column since plan
    step R7c-c** (developer ruling 2026-08-16, plan ledger row **D37**).  It was
    ``rule.day_of_month``, which the write door encoded from the rule's authored
    columns and that step drops;
    :func:`~app.services.recurrence.scheduling_day_of_month` answers the same
    value from the columns that survive, and was measured equal to the stored
    one for all 46 live rules on a production clone before the column went.
    Both it and this function are deleted by plan step **R5**, which gives a
    generated row its own ``occurs_on`` and ``due_on``.

    Next-month convention: if due_day_of_month < the scheduling day, the due
    date falls in the following calendar month.  Example: a rule scheduled on
    the 22nd with due_day_of_month=1 means the bill is due on the 1st of the
    next month after the scheduling month.

    Month-end clamping: day values exceeding the month's last day are
    clamped (e.g. day 31 in April becomes 30, day 30 in Feb becomes 28).

    Args:
        rule: The RecurrenceRule to date the row from.
        period: The :class:`~app.services.pay_calendar.DerivedPeriod` the
            row was assigned to -- saved, or PROJECTED past the horizon (the
            seam's estimate of a row not yet written, plan step R16-b-2).  It
            reads that period's payday and its last covered day; both are
            DERIVED from the owner's payday set since pay-calendar plan step
            C2-f3c, where they were the stored columns plan step **C4-c**
            dropped.  Measured equal on production the same day: 62 periods,
            zero disagreements.

    Returns:
        A date object representing the due date.

    Raises:
        RecurrenceResolutionError: When the rule names a unit or a placement
            this application does not model -- see
            :func:`~app.services.recurrence.scheduling_day_of_month`.  It could
            not raise while it read a plain column; it now makes the same
            refusal every other reader of this rule already makes, rather than
            dating a row from a cadence nothing can read.
    """
    dom = scheduling_day_of_month(rule)
    due_dom = rule.due_day_of_month

    # A cadence that names no day of the month -- every-paycheck, every-N, and
    # a monthly rule funded from the month's first paycheck -- is dated from
    # its period's start.
    if dom is None:
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
        target_day = min(dom, last_day)
        target = date(dt.year, dt.month, target_day)
        if period.covers(target):
            base_year = dt.year
            base_month = dt.month
            break

    if due_dom is None or due_dom == dom:
        # No separate due date -- use day_of_month in the base month.
        last_day = cal.monthrange(base_year, base_month)[1]
        return date(base_year, base_month, min(dom, last_day))

    # Next-month convention: due_day_of_month < day_of_month means the
    # due date falls in the month after the scheduling month.
    if due_dom < dom:
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
    return date(due_year, due_month, min(due_dom, last_day))


__all__ = ["compute_due_date"]
