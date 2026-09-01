"""
Shekel Budget App -- Dashboard: the hero-shaped balance fragment.

:func:`compute_balance_section` is the narrow producer behind
``dashboard.balance_section`` -- the GET endpoint the dashboard anchor
editor's Cancel / Escape (and the 409-conflict retry) reverts to.  It is
its own module because it is its own ENDPOINT: the pulse region beside it
is a full projection walk, and this one folds a single balance.

Pure aggregation -- no Flask imports, no database writes.
"""

from app.services import balance_at

from ._section import DashboardSection


def compute_balance_section(section: DashboardSection | None) -> dict:
    """Compute the hero-shaped balance fragment for the anchor-edit revert.

    The narrow producer behind ``dashboard.balance_section`` -- the GET
    endpoint the dashboard anchor editor's Cancel / Escape (and the
    409-conflict retry) reverts to (``accounts._anchor_revert_url`` maps
    ``revert=dashboard`` here).  It re-renders ``_pulse_balance.html``, the
    ``#balance-display`` control the editor replaced, so it returns a
    dict shaped like the pulse hero: a ``hero`` sub-dict carrying the
    current period's projected END ``balance`` and the ``account_id`` the
    control needs.

    The ``balance`` is the seam's cash-flow scalar read at the CURRENT
    PERIOD'S ``end_date`` -- the exact figure the pulse hero shows (which
    reads the same date off its period map), so the reverted fragment and the
    main pulse region agree to the cent, and both match the label
    ``_pulse_balance.html`` renders them under ("End of this period").  It
    used to read the scalar at TODAY, which was the same number only because
    that scalar was period-FLAT; the fold makes it date-precise (plan step
    X-c2b2, finding cash D2), so the date has to be stated.  This surface
    reads the CASH-FLOW view (the pure transaction running balance), not the
    kind-correct ``balance_at`` scalar: the dashboard account is
    ``resolve_grid_account``'s pick, which may be ANY kind (a user can point
    the dashboard at an HYSA, or the fallback can land on a non-checking
    account), and ``/dashboard`` asks a RUNWAY question -- modelled growth
    inflates the balance a user reads as "what I have to spend", which is a
    property of this page's question and not of the grid's.

    **The agreement argument this docstring used to give beside that one was
    FALSE and is deleted** (finding N-87, ruling R-AK), matching the correction
    ``_pulse`` took at plan step X-g3a.  It claimed the
    kind-correct scalar would diverge "from the grid, which deliberately keeps
    the same account on the cash-flow view".  The grid has layered an accrual
    for an INTEREST account since PR #47 and renders the MODELLED balance for
    every kind since plan step X-g3b, so this hero and the grid already
    disagree by design: measured at the current period on the prod-shape clone
    2026-07-27, the hero reads ``$31,070.06`` for the Empower 401(k) against
    the grid's ``$31,751.40``, and ``resolve_grid_account`` returns that very
    account on the developer's own data -- so it is the DEFAULT ``/dashboard``
    against the DEFAULT ``/grid``.  The divergence is RECORDED, not fixed:
    whether this page's runway question should read a modelled balance is its
    own ruling with its own measurement, and it is not made inside a render
    cutover.

    **With no period containing today it reads the seam at TODAY, not a stored
    balance** (ruling R-EM, plan step X-f1c3a).  This arm used to answer
    ``account.current_anchor_balance`` on the stated ground that "the seam
    cannot project to today" -- which was never true of the seam, only of this
    call: ``cash_balance_at`` takes a DATE and a period was being used to supply
    one.  The old arm rendered the last balance the user ASSERTED under a label
    that says "End of this period", so every settled movement since that
    assertion was silently missing from the runway figure.  Reading
    ``ctx.as_of`` is the same producer answering the same question one day
    earlier, which is the honest figure when there is no period end to name.

    **The period it names is DERIVED and the day it reads is the pass's**
    (pay-calendar plan step C2-f2e).  Both used to be
    ``pay_period_service.get_current_period(user_id)``: SQL over the stored
    ``start_date`` / ``end_date`` span, resolving a ``date.today()`` of its own.
    The fragment therefore answered against a clock the enclosing pulse region
    had already read separately, and against a span the calendar need not
    agree with (plan finding **P1**).  The route hands this producer the render's
    one read pass and the section derives its period from that pass's calendar,
    so the reverted control and the region it swaps back into cannot name
    different paychecks.

    **It began deriving a calendar where it had not**, and reading
    ``section.current_period`` is what does it: the retired
    ``get_current_period`` was SQL and touched no calendar.  That widened
    ledger row **P35** to this fragment -- an owner whose paydays could not
    define a calendar, being a legacy period stored before
    ``budget.pay_schedule`` existed whose span ``resolve_cadence``'s fallback
    read back as a cadence outside 1..365, would meet ``PayCalendarError``
    here.  *Plan step C4-b-2 CLOSED that row: ``fk_pay_periods_schedule``
    makes the owner unstorable and the fallback is deleted, so the cadence can
    only come from a column bounded to the same 1..365 the derivation
    enforces.*  The containment argument that stood beside it -- ``/`` itself
    already raises for that owner, so the editor this fragment reverts cannot
    be opened -- is kept because it never depended on the fallback.

    Args:
        section: The render's :class:`~._section.DashboardSection`, or ``None``
            when the owner has no resolvable grid account.  ``None`` is a real
            input here rather than a caller's mistake: the anchor editor is
            opened from a rendered page and reverts through this endpoint, so
            an account deactivated in between leaves a live fragment with no
            account to edit -- which is the state ``_pulse_balance.html``'s
            own else-branch renders.

    Returns:
        A dict with key ``hero`` -> ``{balance, account_id}``, or
        ``{"hero": None}`` when the user has no resolvable account.
    """
    if section is None:
        return {"hero": None}

    current_period = section.current_period
    balance = balance_at.cash_balance_at(
        section.account,
        section.balance_ctx,
        current_period.end_date if current_period is not None
        else section.balance_ctx.as_of,
    )
    return {
        "hero": {"balance": balance, "account_id": section.account.id},
    }
