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

Boundary discipline (``CLAUDE.md``), stated PER MODULE because plan step C2-b1
made one of them impure and a claim about "the package" would then be false of
part of it:

* :mod:`._derive`, :mod:`._searches`, :mod:`._window`, :mod:`._views`,
  :mod:`._calendar` and :mod:`._cadence` -- no Flask symbol, no database
  session, no clock.  Every answer is a pure function of values a caller
  supplies, and that is load-bearing rather than tidy: it is what lets C1's
  harness drive the derivation over production's real 61 paydays and over a
  generated sweep with no database, so the two runs exercise the same code.
  The pure half is a one-way chain -- ``_derive`` -> ``_searches`` ->
  ``_window`` -> ``_views`` -> ``_calendar`` -- split at plan step C2-c and
  again at C2-f3b, each time when the calendar module reached pylint's
  1,000-line ceiling, so a search, a producer of a view, a view over a calendar and the
  calendar itself cannot answer one question differently.
* :mod:`._loader` -- holds the session, and ONLY the session.  It reads an
  owner's paydays and cadence and hands them to the pure half; it computes
  nothing.  One module is the whole impure surface, which is what makes the
  boundary a file rather than a convention.

**One more value landed here at the recurrence arc's plan step R7a-2a**, and it
is in this package because the fact it derives from is:
:class:`~._cadence.PayCadence` answers "how many paychecks does this owner
receive in a year", which is ``budget.pay_schedule.cadence_days`` and nothing
else.  It replaced ``app.utils.money.PAY_PERIODS_PER_YEAR``, a hardcoded
``Decimal("26")`` read by nine files while the cadence it stood for is
user-selectable 1..365 -- so every monthly-equivalent figure on
``/obligations``, ``/savings`` and the Recurring surface was wrong for an owner
who is not paid biweekly.  It is a SEPARATE value from
:class:`~._calendar.PayCalendar` rather than a method on it, because most of
its consumers need the cadence and not the payday set; the calendar exposes
:attr:`~._calendar.PayCalendar.cadence` so a caller holding one never builds a
second answer.
"""

from ._cadence import DAYS_PER_YEAR, PayCadence
from ._calendar import PayCalendar
from ._derive import (
    MAX_CADENCE_DAYS,
    MIN_CADENCE_DAYS,
    DerivedPeriod,
    PayCalendarError,
    derive_periods,
)
from ._loader import cadence_for, calendar_at_cadence, calendar_for
from ._searches import (
    containing_period,
    earliest_start_in_month,
    earliest_started_period,
    final_covered_day,
    latest_started_period,
    opening_payday,
    period_by_id,
)
from ._window import PeriodWindow

__all__ = [
    "DAYS_PER_YEAR",
    "MAX_CADENCE_DAYS",
    "MIN_CADENCE_DAYS",
    "DerivedPeriod",
    "PayCadence",
    "PayCalendar",
    "PayCalendarError",
    "PeriodWindow",
    "cadence_for",
    "calendar_at_cadence",
    "calendar_for",
    "containing_period",
    "derive_periods",
    "earliest_start_in_month",
    "earliest_started_period",
    "final_covered_day",
    "latest_started_period",
    "opening_payday",
    "period_by_id",
]
