"""
Shekel Budget App -- The pay calendar: one derivation of "which paycheck".

A pay period is not three stored facts.  It is ONE fact -- the payday -- and
two values derived from the owner's payday set:  the period's ordinal is its
position in that set, and its last covered day is the day before the next
payday.  ``budget.pay_periods`` stores all three today, which is why a gap, an
overlap and an index out of date order are all EXPRESSIBLE states that five
separate runtime fences have to police.  This package holds the derivation, so
that after plan step C4 none of those states has a subject.

The plan of record is ``docs/plans/implementation_plan_pay_calendar.md``.
**What it says about this package, quoted rather than paraphrased:** nothing.
The plan names no module and no package anywhere -- it specifies BEHAVIOUR per
step, and where that behaviour lives was decided with the developer when C1 was
built (2026-08-08).  The distinction matters, and it is a correction the review
of this step made: an earlier draft of this docstring presented the placement
plan below as though the document contained it.

So, stated as what it is -- **this package's intended shape, not a quotation**:

===== ====================================================================
step  what is expected to land here
===== ====================================================================
C1    :func:`derive_periods` -- the derivation, proven equal to the stored
      columns and called by nothing (this commit).  This row is DONE, not
      intended.
C2    the plan requires that ``PeriodCalendar`` "becomes the one producer"
      and that its constructor stop accepting a partial list.  Housing it
      beside the derivation is the intent; the plan does not say so.
C3    the writer materialises paydays from this derivation instead of
      computing ends from cadence arithmetic.  Which module holds the
      helper is open.
C4    ``end_date`` and ``period_index`` are dropped from the table, and the
      readers named in the plan's section 3 take their bounds from a
      calendar instead of from ``txn.pay_period``.
===== ====================================================================

**Why a package rather than a module** (developer ruling, 2026-08-08): the
calendar value object and the writer's half are expected to land beside the
derivation, and making that a file rename later -- after consumers have begun
importing the name -- buys nothing.  The boundary is also what keeps the
derivation honest: :mod:`._derive` is private, so every consumer depends on
this public surface and the W9910 gate (``shekel-private-module-import``) says
so structurally rather than by convention.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no database session, no
clock.  Everything here is a pure function of values a caller supplies.
"""

from ._derive import (
    MAX_CADENCE_DAYS,
    MIN_CADENCE_DAYS,
    DerivedPeriod,
    PayCalendarError,
    derive_periods,
)

__all__ = [
    "MAX_CADENCE_DAYS",
    "MIN_CADENCE_DAYS",
    "DerivedPeriod",
    "PayCalendarError",
    "derive_periods",
]
