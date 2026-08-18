"""
Shekel Budget App -- Pay Period Service

Queries an owner's biweekly pay periods.  Each period is defined by a
start_date (payday) and an end_date (the day before the next payday).

**It no longer WRITES them** (plan step C3-b): ``generate_pay_periods``,
``establish_schedule``, the batch bounds and the forward-only guard moved to
:mod:`app.services.pay_period_write`, which is now the one place in ``app/``
that changes ``budget.pay_periods``.  The reason is C3-a's, one level up --
deciding that a schedule should change and changing it are two concerns, and
the invariant that the stored ``end_date`` / ``period_index`` equal the
derivation over the owner's paydays needs exactly one home for plan steps C4,
C6 and C7 to inherit.  What is left here is the read side, which plan step
**C2-f** points at ``pay_calendar.PayCalendar``.

**Three of that step's six readers are GONE at C2-f1** and their questions are
now the calendar's, each answered by ONE derivation over the owner's paydays
instead of by its own SQL:

======================================= ==================================
retired reader                          what answers it now
======================================= ==================================
``get_overlapping_periods``             ``PayCalendar.overlapping``
``get_next_period``                     ``PayCalendar.period_starting_after``
``get_current_and_future_periods``      ``routes._period_options.period_move_options``
======================================= ==================================

The last one is a FORM-OPTIONS rule rather than a calendar question, which is
why it left this module for the route layer rather than for the value: an
already-closed period is not somewhere a user moves an expense TO, and that is
a policy about editing rather than a fact about the schedule.
``companion_service.get_previous_period`` -- a copy of ``get_next_period``
with ``+ 1`` changed to ``- 1`` -- went with them.

**A FOURTH is gone at C2-f2b**, and it went whole rather than by call
site: ``get_periods_in_range`` -- a window selected by ``period_index``,
which is one of the two derived columns plan step **C4** drops -- had all
three of its ``app/`` call sites in the grid route -- ``page.py`` twice and
``partials.py`` once, that module having become the ``app/routes/grid/``
package the same branch -- so moving the grid onto
:meth:`~app.services.pay_calendar.PayCalendar.window` left it with no caller
at all.  The question it answered is the calendar's:

======================================= ==================================
retired reader                          what answers it now
======================================= ==================================
``get_periods_in_range``                ``PayCalendar.window``
======================================= ==================================

``get_current_period`` and ``get_all_periods`` remain, for the rest of
**C2-f2** and then **C2-f3**: only the GRID leaf of that step has landed, and
``C2-f2c``-``C2-f2e`` still move both readers at ``/investment``,
``/savings``, ``/retirement``, the budget dashboard and ``/accounts/<id>``.
**They travel together and may not be separated** (the C2-f decomposition
ruling, 2026-08-14): 11 functions read both, so splitting them leaves a
dozen context objects holding an ORM row in one field and a
:class:`~app.services.pay_calendar.DerivedPeriod` in another.
"""

from datetime import date

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.utils.dates import display_today


def earliest_recordable_day(user_id: int) -> date:
    """Return the earliest civil day this user's app can honestly date money at.

    ``min(the user's earliest pay period start, today)``.  Taking the EARLIER of
    the two is what keeps the bound from refusing a legitimate entry: a user
    whose periods are all still in the future must be able to record what
    happened today, while nobody may back-date into a past the app has no
    schedule for.

    **It has TWO SERVICE consumers, and it lives here so they cannot drift.**

    * ``anchor_service.resolve_observation_day`` -- an anchor's ``observed_on``,
      for BOTH writers of ``AccountAnchorHistory`` (the account factory's
      origination assertion and the true-up door's).  An unbounded day opens the
      modelled-return window (``balance_at._asset_fold._AccrualWindow``
      materialises EVERY calendar day from it) and fabricates contribution
      history back to it (finding **N-133**).
    * ``status_seam.reject_settle_day_before_the_schedule`` -- a settle day
      (plan step X-f1c, ruling **R-EL**).  An unbounded day is absorbed into the
      opening assertion by ``cash_ledger._walk``, which then resets the running
      total to the asserted balance -- so the row's money silently leaves the
      projection while the row still reads Paid.

    **Four ROUTE consumers read it too, and they are a different use**: the two
    settle-day inputs (``routes/transactions/forms``, ``routes/transfers/forms``)
    and the two anchor date inputs (``routes/accounts/crud.new_account``,
    ``routes/accounts/anchor._anchor_day_bounds``) set an input's ``min`` from
    it so the browser refuses what the service would refuse.  That is a
    convenience and never the guard -- an input bound is captured at RENDER time
    and this floor moves whenever pay periods are generated or truncated.

    It was ``account_service.earliest_observable_day`` until X-f1c needed the
    same bound one module lower.  This module is the right home: the rule is a
    PAY-PERIOD SCHEDULE question with no account in it, and living here keeps it
    reachable from ``status_seam``, which must stay below the services that call
    it.  **The first bullet named ``account_service._reject_undatable_observation``
    and this paragraph named ``account_service.earliest_observable_day`` until
    plan step X-f1c4c deleted both** (ruling R-ER moved the rule to the module
    that owns what an assertion is); three independent reviews of that step
    found this docstring still naming them.

    Args:
        user_id: The owner whose schedule sets the floor.

    Returns:
        The earliest recordable civil day.  Today when the user has no pay
        periods at all -- every caller's own operation then fails on the missing
        schedule, which is a clearer error than a date bound.
    """
    today = display_today()
    earliest = (
        db.session.query(db.func.min(PayPeriod.start_date))
        .filter(PayPeriod.user_id == user_id)
        .scalar()
    )
    if earliest is None:
        return today
    return min(earliest, today)


def get_current_period(user_id, as_of=None):
    """Return the pay period that contains the given date.

    Args:
        user_id: The user's ID.
        as_of:   The reference date (default: today).

    Returns:
        The matching PayPeriod, or None if no period covers that date.
    """
    if as_of is None:
        as_of = date.today()

    return (
        db.session.query(PayPeriod)
        .filter(
            PayPeriod.user_id == user_id,
            PayPeriod.start_date <= as_of,
            PayPeriod.end_date >= as_of,
        )
        .first()
    )


def get_all_periods(user_id):
    """Return all pay periods for a user, ordered by index.

    Args:
        user_id: The user's ID.

    Returns:
        List of PayPeriod objects.
    """
    return (
        db.session.query(PayPeriod)
        .filter_by(user_id=user_id)
        .order_by(PayPeriod.period_index)
        .all()
    )
