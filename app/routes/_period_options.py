"""The pay periods a row may be MOVED into, answered once for both forms.

Plan step **C2-f** (``docs/plans/implementation_plan_pay_calendar.md``
section 4).  It replaces ``pay_period_service.get_current_and_future_periods``,
whose three call sites -- the transaction full-edit popover, the transfer
full-edit popover, and the transfer branch inside the transaction one -- each
rendered the same ``<select>`` from the same query.

**It is a FORM-OPTIONS rule and not a calendar question**, which is why it
lives here rather than as a fifth view on
:class:`~app.services.pay_calendar.PayCalendar`.  The calendar answers which
periods exist and when they run; "an already-closed period is not somewhere a
user moves an expense TO, except the one the row is in already" is a policy
about editing, and the row's own period is the caller's fact rather than the
schedule's.

**The narrowing is a CONVENIENCE, not the guard**, and saying so is the honest
statement: the PATCH handlers re-resolve the submitted ``pay_period_id``
against the owner (``routes/transactions/_helpers`` and
``routes/transfers/mutations``) and accept any period that owner holds,
including a closed one.  What this list decides is what the browser OFFERS.
"""

from datetime import date

from app.services.pay_calendar import DerivedPeriod, calendar_for
from app.utils.dates import display_today


def period_move_options(
    user_id: int, current_period_id: "int | None",
) -> "list[DerivedPeriod]":
    """Return the periods a row currently in *current_period_id* may move to.

    Every SAVED period that has not yet ended, plus the row's own period even
    when it has.  Forcing the row's own period in is what keeps a transaction
    sitting in a past paycheck SELECTED in the dropdown instead of the browser
    defaulting to the first offered option and the save silently re-pointing
    the row at a paycheck the user never chose.

    **The day is the USER's** (:func:`~app.utils.dates.display_today`), where
    ``get_current_and_future_periods`` defaulted to ``date.today()``.

    **This moves nothing in the deployed container, and saying so is the
    point** (adversarial review of plan step C2-f1, which caught a first draft
    asserting a live production defect this repository refutes).  Both compose
    files pin ``TZ: America/New_York`` -- the 2026-06-12 dev/prod parity
    audit's finding **M01**, taken because the image default of UTC flipped
    ``date.today()`` to the next day at 20:00 Eastern and made exactly this
    dropdown drop the period the user was still in.  With the process clock
    pinned to the display zone the two agree, so the offer set is identical.

    What the change buys is that the rule no longer DEPENDS on that pin: all
    three call sites already compute ``display_today()`` two lines below for
    the settle-day input's ``max``, so one render held two clocks that are
    equal only by a deployment setting.  Reading the day the surface already
    uses removes the second clock rather than adding one, and it fixes the
    environments the pin does not cover -- CI, a script, a bare ``flask run``
    on a non-Eastern host.  The direction is also provably safe: Eastern is
    behind UTC, so where they do differ this offers one period MORE, never
    fewer.

    Args:
        user_id: The owner whose schedule to offer.  The calendar is derived
            from their complete payday set, so a period's end here is the one
            every other surface reports.
        current_period_id: The ``budget.pay_periods.id`` the row sits in
            today, forced into the result.  ``None`` for a caller with no such
            row, which then gets the not-yet-ended periods alone.

    Returns:
        The offerable periods, ``start_date`` ascending.  **A plain list and
        deliberately not a**
        :class:`~app.services.pay_calendar.PeriodWindow`: a row sitting well
        in the past puts its period in the result with every period between it
        and today left out, and a window is a CONTIGUOUS view that refuses
        exactly that shape.  Nothing here reports a balance across the gap --
        these are ``<option>`` rows -- so the hole is the right answer rather
        than a broken one.

    Raises:
        PayCalendarError: The owner's paydays cannot define a calendar (see
            :func:`~app.services.pay_calendar.calendar_for`).
    """
    today: date = display_today()
    return [
        period for period in calendar_for(user_id).saved()
        if period.end_date >= today or period.period_id == current_period_id
    ]
