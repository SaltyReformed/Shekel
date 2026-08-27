"""
Shekel Budget App -- Route-layer half of pay-period population.

**The HTTP boundary's half of :mod:`app.services.period_population`**, and the
two names mirror each other on purpose: that module fills newly recorded pay
periods with each active template's recurring rows, and this one opens the READ
PASS it fills them in.  The split is ruling **R-R38** (plan step R7d-c-1).

Why the pass cannot be opened below here
----------------------------------------

A generate pass resolves each rule against the owner's pay calendar, and TODAY
that is all it reads off the pass: every one of generation's four reads is
``schedule.calendar``.  **From plan step R7d-c-2 it will also resolve each loan
payment's closing bound by folding that loan**, and both facts live on a
:class:`~app.services.balance_at.BalanceContext`, which memoizes them for the
pass's whole life.  So a pass built BEFORE the pay-period write it repopulates
holds the pre-write calendar and -- once R7d-c-2 lands -- the pre-write loan,
and only the first of those is caught: ``GenerationSchedule``'s window check
refuses ids the calendar does not hold, while a stale loan would simply answer
a stale payoff.  That the memo behaves that way is measured rather than
assumed (2026-08-27, production clone: a pass held across the deletion of the
Van Loan's five already-due transfers went on answering ``2029-02-22`` where a
fresh pass said ``2029-04-22``, with nothing raised) -- it is the LOAN half's
reachability that is prospective, not the mechanism.

The doors therefore RECORD and return -- ``extend_pay_periods``,
``regenerate_pay_periods``, ``reset_pay_periods`` and, through the first of
those, ``pay_period_rolling.top_up_rolling_window`` -- and this function runs
after them, so "the pass is resolved after the periods exist and before the
rows do" is the ORDER OF TWO CALLS rather than a paragraph a future writer has
to find.  The 2026-08-16 layer ruling (``pay_calendar:C11``) puts
``BalanceContext.build`` at the HTTP boundary and nowhere below it; this module
is that boundary for the four doors above.

**TWO other write paths in ``app/`` create pay periods and do NOT come here**,
named so this docstring is a census rather than an impression.
``auth_service.register_user`` records the new owner's first schedule, which is
correct twice over: no template can exist yet, and the baseline scenario is
created AFTER that call, so a repopulation would return 0 on ``ctx.scenario is
None``.  ``POST /pay-periods/generate`` records a schedule too -- and its
forward-only rule accepts any payday past the owner's last, so on an owner who
already has one it is an EXTEND that skips every template.  That is ledger row
**D57**'s sibling **D58**, PRE-EXISTING and not this step's to fix.

There is ONE spelling of it here rather than five at the call sites, because
the ordering rule is one rule and five copies are five places for it to come
apart.  What keeps the five honest is not this module: it is that each of the
five doors has a ROUTE-level test asserting the recurring ROWS rather than the
period count (``tests/test_routes/test_pay_period_admin.py``,
``TestEveryDoorThatCreatesAPeriodPopulatesIt``), so a door that records and
does not populate is a red test rather than a silently empty paycheck.
"""

from app.services.balance_at import BalanceContext
from app.services.period_population import populate_periods_from_active_templates


def populate_new_periods(user_id, new_periods):
    """Open the generate pass and fill *new_periods* from the active templates.

    Call it immediately after the door that recorded *new_periods*, inside the
    same transaction, and never before: the pass this opens memoizes the
    owner's calendar and each loan's walk at the moment of its first read, and
    the module docstring carries what a pass opened too early answers.

    **An empty batch opens NOTHING**, which is not a micro-optimisation: the
    rolling top-up runs on every ``/grid`` and ``/dashboard`` render and
    creates periods on almost none of them, so a pass built here
    unconditionally would be a second pay-calendar derivation and a second
    baseline-scenario query on every render of the app's two main screens.
    ``test_one_read_pass_per_render`` holds those renders at their measured
    counts.

    Args:
        user_id: The owning user's id.
        new_periods: The :class:`~app.models.pay_period.PayPeriod` rows the
            door just recorded, exactly as it returned them.  Already flushed
            (``record_paydays`` flushes, which is where their ids come from).
            Empty is a no-op.

    Returns:
        The number of template-linked records created (transactions plus
        transfers; a transfer counts once, not its two shadow rows).
    """
    period_ids = {period.id for period in new_periods}
    if not period_ids:
        return 0
    return populate_periods_from_active_templates(
        BalanceContext.build(user_id), period_ids,
    )
