"""
Shekel Budget App -- Route-layer half of pay-period population.

**The HTTP boundary's half of :mod:`app.services.period_population`**, and the
two names mirror each other on purpose: that module fills newly recorded pay
periods with each active template's recurring rows, and this one opens the READ
PASS it fills them in.  The split is ruling **R-R38** (plan step R7d-c-1).

Why the pass cannot be opened below here
----------------------------------------

A generate pass resolves each rule against the owner's pay calendar, and
**since plan step R7d-c-2 it also resolves each loan payment's closing bound by
folding that loan** (``recurrence_engine.resolve_generation_plan`` reads
``recurring_definition.read_definition``); both facts live on a
:class:`~app.services.balance_at.BalanceContext`, which memoizes them for the
pass's whole life.  So a pass built BEFORE the pay-period write it repopulates
holds the pre-write calendar and the pre-write loan, and only the first of
those is caught: ``GenerationSchedule``'s window check refuses ids the calendar
does not hold, while a stale loan would simply answer a stale payoff.  That the
memo behaves that way is measured rather than assumed (2026-08-27, production
clone: a pass held across the deletion of the Van Loan's five already-due
transfers went on answering ``2029-02-22`` where a fresh pass said
``2029-04-22``, with nothing raised).

**What the post-write pass reads for the LOAN is the schedule mid-rebuild,
and that is plan ledger row D46's mechanism, stated here because this is the
one place that opens such a pass.**  The loan's forward plan prices a slot from
its ROW where one exists and from the standing payment's ESTIMATE where none
does -- but only for slots still AHEAD of ``as_of``; a slot behind it with no
row is priced by neither tier (finding B-9's fix).  So an occurrence dated
before ``as_of`` that no row answers at the moment the pass reads the loan
makes the payoff read one installment LATE, and generation -- bounded by that
reading -- writes one installment past the loan's life.  Measured on the
branch that took the door (``$1,200`` at 3% over three months, installments
2026-02-15 / 03-15 / 04-15, ``as_of`` 2026-03-01): the pass reads
``ClosesOn(2026-05-15)``, writes FOUR rows, and a fresh pass over them reads
``2026-04-15``.  Three doors reach that state.  ``reset_pay_periods`` wipes
STARTED periods too, so the February row is gone when the repopulation reads
the loan -- and until R7d-c-2 the column, synced before the wipe, bounded that
generation at the true payoff, which this step gives up.  A payment created
AFTER its first installment date has never had that row, and the loan door's
own pre-generate sync wrote the same late payoff into the column, so that
shape is as old as the column.  And a *Monthly First* payment authored on the
generic transfer form is funded by the first paycheck on or after its
contractual day, so between the two a regenerate retires a not-yet-started
period holding a past slot's row.  ``regenerate_pay_periods`` alone cannot
reach it for the loan door's own payment: that rule fires ON the contractual
day (``CONTAINING_DATE``), a past slot's row sits in a period that has STARTED
and is kept, and through that door on a production clone (2026-09-11) the
window read in the hole was ``2029-02-22``, the same as before and after.
``$0.00`` on the developer's data through every door -- both loan payments are
``CONTAINING_DATE`` and every saved horizon stops years before either payoff.
**Ruled 2026-09-11 (developer), root cause first**: the fold is to price every
occurrence a definition names that no row IN ANY STATE answers, past or future,
from the definition -- a deleted or cancelled row still answers its occurrence,
so B-9's honest delinquency keeps its meaning and only "no record at all" stops
meaning "unpaid" -- and that rule lands with the ESTIMATED tier's rewrite
(plan step R16-b-2), AHEAD of the step that moved generation onto the door.
Once the fold reads the plan that way the payoff no longer depends on whether
generation has run, and this paragraph describes a state that cannot occur;
``recurrence_engine._plan``'s docstring lists the shape beside the three
others that leave the payoff off its fixed point.

The doors therefore RECORD and return -- ``extend_pay_periods``,
``regenerate_pay_periods``, ``reset_pay_periods`` and, through the first of
those, ``pay_period_rolling.top_up_rolling_window`` -- and this function runs
after them, so "the pass is resolved after the periods exist and before the
rows do" is the ORDER OF TWO CALLS rather than a paragraph a future writer has
to find.  The 2026-08-16 layer ruling (``pay_calendar:C11``) puts
``BalanceContext.build`` at the HTTP boundary and nowhere below it; this module
is that boundary for the four doors above.

**FIVE HTTP doors reach this function** -- extend, regenerate, reset, generate,
and the rolling top-up through ``/grid`` and ``/dashboard`` -- and naming them
is the census, not an impression.  ``POST /pay-periods/generate`` was the one
that did NOT until this step: it reads as first-time-only and is not, because
``record_paydays``' forward-only rule accepts any payday past the owner's last,
so on an owner who already had a schedule it appended periods and skipped every
template (ledger row **D58**, pre-existing, closed here).

**ONE write path in ``app/`` creates pay periods and does not come here**, and
it is correct twice over: ``registration_service.register_user`` records the new
owner's first schedule, where no template can exist yet AND the baseline
scenario is created after that call, so a repopulation would return 0 on
``ctx.scenario is None``.

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
