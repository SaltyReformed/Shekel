"""
Shekel Budget App -- Pay-Period Template Population

Fills a set of pay periods with each active template's recurring rows --
transactions AND transfers -- in one pass.  This is the orchestrator the
extend and regenerate operations run after creating new, empty periods.

It lives in its own module rather than inside either recurrence engine
because it must call BOTH: the transaction engine
(``recurrence_engine.generate_for_template``) and the transfer engine
(``transfer_recurrence.generate_for_template``).  The transfer engine
already imports the transaction engine, so co-locating this orchestrator
with either one would create an import cycle; a neutral module that
imports both (and is imported by neither) keeps the graph acyclic.

**It OPENS the generate pass's read context.**  ``pay_calendar:C11``'s layer
predicate is "no module under ``app/services/**`` calls
``BalanceContext.build``", and it carves out a WRITER; this module joins that
carve-out, on the ground that a pass opened by a writer AFTER its own write is
not a second read pass but the first one that can see the write.  **The count
is SIX modules and this is the sixth, not "one of two"** -- a first draft of
this paragraph said two and an adversarial review of plan step R7d-c-1
measured it: ``calendar_service``, ``investment_dashboard_service/_context``
and ``/_orchestrator``, ``loan_recurrence_sync``, ``tax_report_service`` and
this one.  C11 empties the first five; this one is the exception its predicate
must name, and **that carve-out is a FORK C11 states and does not rule** ("the
rule carves it out or takes it from its caller"), so it is registered in
``steps.md``'s cross-arc forks table rather than settled here.

The alternative -- taking a pass from the caller -- was measured wrong here in
two ways.  The calendar half fails LOUDLY *for the constructor this module
uses*: every caller creates pay periods immediately before calling this, and a
pass whose calendar memo was filled before that write does not hold the new
ids, which ``GenerationSchedule.__post_init__`` refuses.  (It does NOT catch
the same mistake through ``for_pass``, whose window is the calendar itself --
see that method's own docstring.)  The LOAN half fails
SILENTLY, and that is the deciding one: from plan step R7d-c-2 a loan payment's
closing bound is a fold over the loan's forward plan, and a pass memoizes each
loan's resolution for its whole life.  Measured on a production clone
(2026-08-27): with a pass built first, deleting the Van Loan's 5 already-due
transfers moved its derived payoff ``2029-02-22`` -> ``2029-04-22`` on a FRESH
pass while the pre-write pass went on answering ``2029-02-22``, and nothing
raised -- the stale date came back as a figure.  **No caller of this function
can make that particular delete**, and an adversarial review measured it after
a first draft claimed both destructive doors did: ``regenerate_pay_periods``
KEEPS every period that has started, which is where all five of those rows sit,
and ``reset_pay_periods`` is refused outright for an owner holding settled
rows.  What survives is the MECHANISM -- a pass memoizes a loan for its whole
life and nothing reconciles it -- and the rule that a writer opens its pass
after its write is what keeps a future caller from having to discover that.
There is also no pass to take on
one of the four paths in: ``/grid`` and ``/dashboard`` run the rolling top-up
inside their own ``write_transaction()`` and build the render's pass only
afterwards, precisely so the render sees the periods the top-up committed.

Flask-isolated: takes and returns plain data, flushes via the engines,
never commits.
"""

from app.extensions import db
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import transfer_recurrence
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.recurrence_engine import generate_for_template


def populate_periods_from_active_templates(user_id, period_ids):
    """Generate recurring transactions AND transfers into a set of periods.

    The repopulation step extend and regenerate run after creating new,
    empty periods.  ``pay_period_write.record_paydays`` creates blank periods
    and does NOT call the recurrence engine, so a freshly-appended period has
    none of its rent / paychecks / recurring transfers until this runs.
    This re-runs BOTH engines -- transactions and transfers, so a new
    period never silently misses a recurring transfer -- over the
    specific ``periods``, into the user's baseline scenario (multi-scenario
    repopulation is reserved for later).

    Both engines' shared ``should_skip_period`` skips any period that
    already holds a template-linked row, so this is safe to re-run: a
    retried extend / top-up creates nothing and cannot violate the
    ``(template, period, scenario)`` unique partial index.

    **This is the caller plan step R4b-1 was written for.**  *period_ids* is
    the newly created batch, and until R4b-1 it was handed to each engine as
    BOTH the schedule the rule was resolved against and the window to write
    into -- so every extend re-read every rule as though the owner's pay
    history began at the new batch.  It produced duplicate ``Monthly First``
    rows and stored a third paycheck $502.45 low on production; the
    measurements live in
    :class:`~app.services.generation_schedule.GenerationSchedule`.  The
    owner's calendar is derived ONCE here and the batch states only the
    window.

    **ONE read pass serves every template** (plan step R7d-c-1), so the batch
    holds one derivation of the owner's calendar, one baseline-scenario
    resolution, and -- from plan step R7d-c-2 -- one answer per loan to "when
    does this stop", pinned at the state the pay-period write left.  A
    per-template pass would derive the owner's 62-payday calendar once per
    definition.

    **What that does NOT buy is order-independence, and a first draft of this
    paragraph claimed it.**  The claim was that a transfer this loop generates
    into a loan joins that loan's payment feed, so a pass rebuilt per template
    would answer the second definition's bound against rows the first had just
    written.  Measured on a production clone (2026-08-27, the Van Loan):
    deleting all 23 FUTURE transfers into it moved the derived payoff by zero
    days, and HALVING all 23 moved it by zero days -- because the ESTIMATED
    tier prices every uncovered future installment from the DEFINITION (plan
    step R7d-a), so a future row neither adds to the plan nor changes it.  What
    does move it is the PAST: dropping the 5 already-due transfers moved the
    payoff ``2029-02-22`` -> ``2029-04-22``.  This loop creates future rows
    only, so within one batch the order is not observable and sharing the pass
    is a cost and single-derivation argument rather than a correctness one.

    **The pass is opened HERE, after the write** -- see the module docstring
    for why it may not be taken from the caller.  The calendar it derives is
    therefore the post-write one, which is the value that must hold
    *period_ids*, and a pass built any earlier could not.

    **It takes IDS and forwards no boundary since pay-calendar plan step
    C2-f3c.**  It took ORM rows, read two things off them -- ``.id`` for the
    window and ``periods[0].start_date`` for a boundary -- and forwarded that
    boundary to each engine.  Both are gone, and neither was a behaviour
    change: the window is the batch, every period of the batch ENDS on or
    after the batch's own opening payday, so bounding placements by that date
    could never drop one.  The ``effective_from`` parameter that let a caller
    override it went with the boundary; no caller in ``app/`` or in the suite
    ever passed one, which is the speculative shape ``CLAUDE.md`` rule 13
    forbids.  A caller that genuinely needs a later bound calls the engines
    directly, as the unarchive paths already do.

    Args:
        user_id: The owning user's id.
        period_ids: The ``budget.pay_periods.id`` values to populate.  Must
            already be flushed, and must be this owner's -- both are what
            ``pay_period_write.record_paydays`` returns.  An empty set is a
            no-op.

    Returns:
        The number of template-linked records created (transactions plus
        transfers; a transfer counts once, not its two shadow rows).
    """
    window = frozenset(period_ids)
    if not window:
        return 0

    # The baseline scenario is read as one of the pass's three pins rather
    # than through a second lookup beside it: ``BalanceContext.build``
    # resolves it, and it is the same resolution every producer this pass
    # reaches will read.  ``scenario`` (the nullable) rather than
    # ``scenario_id`` (which raises), because an owner with no baseline is a
    # no-op here and always has been -- ruling R-R30 decides the FORM READ
    # path and leaves this one alone.
    ctx = BalanceContext.build(user_id)
    if ctx.scenario is None:
        return 0

    schedule = GenerationSchedule.for_period_ids(ctx, window)

    created = 0
    txn_templates = (
        db.session.query(TransactionTemplate)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )
    for template in txn_templates:
        created += len(generate_for_template(
            template, schedule, ctx.scenario_id,
        ))

    transfer_templates = (
        db.session.query(TransferTemplate)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )
    for template in transfer_templates:
        created += len(transfer_recurrence.generate_for_template(
            template, schedule, ctx.scenario_id,
        ))

    return created
