"""Loan recurring-payment end-date sync (Risk R-4: off the GET path).

Keeps a loan's recurring-payment :class:`~app.models.recurrence_rule.RecurrenceRule`
``end_date`` equal to the loan's PROJECTED PAYOFF, so the recurrence engine stops
generating shadow transactions past payoff.  This used to run as a write on the
loan-detail GET (documented Risk R-4); it now runs at every chokepoint that can
MOVE the projected payoff -- a params / rate edit, a balance true-up, and every
transfer settle / revert / edit / delete / restore of a loan payment (where an
extra-principal payment shifts payoff earliest) -- so ``end_date`` tracks payoff
without any read-path write.

**The bound is DERIVED from the balance, not persisted from a schedule walk**
(plan step C8d, finding B-14).  It used to read the last row of the resolver's
committed schedule -- a walk that amortizes one contractual installment per month
whether or not a payment stands behind it -- so the date this column persisted
could disagree with the payoff every screen showed.  It now reads the seam's
:func:`app.services.balance_at.loan_payoff_date`: the date the loan's BALANCE
folds to zero, the same figure the loan card's chip, the /savings cockpit, and the
property equity chart render.  One derivation, one answer, and the stored copy is
a projection of it rather than a second opinion.

Idempotent, and a genuine fixpoint: it recomputes the payoff and writes only when
``end_date`` actually changes.  Writing ``end_date = D`` stops shadow generation
after D, but the balance already reached zero AT D, so the payments the bound
removes are exactly the ones the fold ignored -- a re-run at the new state derives
D again.  The payoff is always measured in the owner's BASELINE scenario (the loan
card's trajectory), whatever scenario triggered the sync.  Flask-isolated: plain
``account_id`` in, no ``request`` / ``session`` reads; flushes into the caller's
transaction and never commits (the caller owns the transaction boundary).
"""

import logging
from datetime import date

from app.extensions import db
from app.models.account import Account
from app.services import balance_at
from app.services.recurring_transfer_query import (
    active_recurring_transfer_template,
)
from app.services.resolution_context import BalanceContext
from app.utils.log_events import (
    BUSINESS,
    EVT_LOAN_RECURRENCE_END_DATE_UPDATED,
    log_event,
)

logger = logging.getLogger(__name__)


def recurrence_end_date(
    payoff_date: date | None, is_retired: bool, as_of: date,
) -> date | None:
    """Return the recurrence end_date a loan's derived payoff implies.

    The three states of the DERIVED payoff
    (:attr:`~app.services.balance_at.LoanFigures.payoff_date`), mapped onto the
    recurrence bound.  ``payoff_date`` is ``None`` for two different loans, and
    ``is_retired`` is what tells them apart -- collapsing them would either leave a
    finished loan generating payments forever or halt a loan that still owes:

    * **Pays off on a date** -- that date, so recurrence stops the month the
      balance reaches zero.
    * **Already RETIRED** (``None`` and owing nothing) -- *as_of*, the pass's own
      now: the loan plans no further payments.  (The pre-C8d writer used the last
      schedule row for a retired loan WITH history and its ``origination_date``
      for one without -- two dates for one state; *as_of* is ONE rule.)

      **This bounds generation from the NEXT period, not from today.**
      ``recurrence_engine.match_periods`` admits a period when
      ``period.start_date <= end_date``, so the CURRENT period -- which started
      before today -- still matches, and only ``should_skip_period`` (an existing
      row) stops a further payment being generated into it for a loan that owes
      nothing.  A bound that excluded the current period outright would have to be
      that period's start minus a day, which is a different fix; recorded as
      **N-19** rather than smuggled in here.  Note also that a retired loan whose
      payoff-affecting mutations span days rewrites this to each new day, so
      "idempotent" is idempotent WITHIN a day.
    * **Never pays off** (``None`` and NOT retired -- negative amortization, or an
      underpayment too severe to clear the plan's post-contractual extension) --
      ``None``, leaving recurrence indefinite until the user raises the payment.
      That is what C7's payment-drift warning exists to prompt.

    Args:
        payoff_date: The loan's derived payoff, or ``None``.
        is_retired: Whether the loan has originated and owes nothing
            (:attr:`~app.services.balance_at.LoanFigures.is_retired`).
        as_of: The read pass's as-of, the retired loan's bound.

    Returns:
        The recurrence ``end_date``, or ``None`` to leave generation indefinite.
    """
    if payoff_date is not None:
        return payoff_date
    return as_of if is_retired else None


def sync_recurring_payment_end_date(account_id: int) -> None:
    """Sync a loan's recurring-payment end_date to its projected payoff (R-4).

    The relocated end-date write: build a read pass for the loan's owner, read the
    seam's derived payoff and retired predicate for it, find its active recurring
    payment template, and set the recurrence ``end_date`` to the bound they imply
    (:func:`recurrence_end_date`) when it differs.  A no-op -- returning before any
    write -- when the account is not a configured loan, has no baseline scenario,
    has no recurring payment, or is already at the right end_date.  Flushes into
    the caller's transaction (does NOT commit).

    **A FRESH context per call, deliberately.**  This runs mid-mutation, so it
    must see the loan as the just-flushed write left it; a
    :class:`~app.services.resolution_context.BalanceContext` is a plain value with
    a memo scoped to one read, never a request cache, so building one here is how
    a writer reads post-write state (see that module's "read pass, not request").

    Called from every chokepoint that can move the projected payoff: loan-params
    create / update, the ARM / origination-rate change, the balance true-up, the
    recurring-transfer creation, and the transfer settle / revert / edit / delete
    / restore paths (via :mod:`app.services._transfer_loan_posting`).

    Args:
        account_id: The loan account whose recurring-payment end_date to sync.
    """
    account = db.session.get(Account, account_id)
    if account is None:
        return
    # The template lookup comes FIRST: with no recurring payment there is no
    # end_date to bound, and deriving the payoff means folding the loan's whole
    # forward plan.  Cheapest disqualifying check first.
    template = active_recurring_transfer_template(account_id, account.user_id)
    if template is None or template.recurrence_rule is None:
        return
    ctx = BalanceContext.build(account.user_id)
    if ctx.scenario is None:
        # No baseline scenario: the seam cannot value this loan (and would raise),
        # and there is no trajectory to bound the recurrence by.
        return
    figures = balance_at.loan_figures(account, ctx)
    if figures is None:
        # Not a configured loan (no LoanParams) -- nothing to bound.
        return

    new_end_date = recurrence_end_date(
        figures.payoff_date, figures.is_retired, ctx.as_of,
    )
    rule = template.recurrence_rule
    if rule.end_date == new_end_date:
        return

    old_end_date = rule.end_date
    rule.end_date = new_end_date
    log_event(
        logger, logging.INFO,
        EVT_LOAN_RECURRENCE_END_DATE_UPDATED, BUSINESS,
        "Updated recurrence rule end date to projected payoff",
        account_id=account_id,
        template_id=template.id,
        old_end_date=str(old_end_date),
        new_end_date=str(new_end_date),
    )
