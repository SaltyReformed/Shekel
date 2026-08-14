"""Loan recurring-payment VALIDITY WINDOW sync (Risk R-4: off the GET path).

Keeps a loan's recurring-payment :class:`~app.models.recurrence_rule.RecurrenceRule`
bounded at BOTH ends by the loan's own facts, so the recurrence engine generates
a payment only while the loan actually exists and owes:

* ``start_date`` = the loan's FIRST CONTRACTUAL INSTALLMENT (plan step C9a), so
  nothing generates before the loan originates.  A pre-origination payment is
  not merely early -- the fold ERASES it (it splits against a zero balance and
  the origination anchor resets over it: $0.00 principal, the whole payment to
  Refund) while the cash side still debits it, so a mortgage closing one month
  out projected $3,220.92 of payments for a loan that did not exist.
* ``end_date`` = the loan's PROJECTED PAYOFF (Risk R-4), so nothing generates
  past payoff.

This used to run as a write on the loan-detail GET (documented Risk R-4); it now
runs at every chokepoint that can MOVE either bound -- a params / rate edit, a
balance true-up, and every transfer settle / revert / edit / delete / restore of
a loan payment (where an extra-principal payment shifts payoff earliest) -- so
the window tracks the loan without any read-path write.  The two bounds share
ONE entry (:func:`sync_recurring_payment_bounds`) precisely so no chokepoint can
move one and leave the other stale.

**The END bound is DERIVED from the balance, not persisted from a schedule walk**
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
from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING

from app.extensions import db
from app.models.account import Account
from app.services import balance_at, loan_loaders, rate_period_engine
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    NEVER_ENDS,
    EndsOnDate,
    end_bound_from_columns,
    reauthor_rule,
    recurrence_spec,
)
from app.services.recurring_transfer_query import (
    active_recurring_transfer_template,
)
from app.services.balance_at import BalanceContext
from app.utils.log_events import (
    BUSINESS,
    EVT_LOAN_RECURRENCE_END_DATE_UPDATED,
    EVT_LOAN_RECURRENCE_START_DATE_UPDATED,
    log_event,
)

if TYPE_CHECKING:  # pragma: no cover -- typing only; these are ORM row types
    from app.models.loan_params import LoanParams
    from app.models.recurrence_rule import RecurrenceRule

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

      **This bounds the OCCURRENCE, and plan step R4a is what changed that.**
      ``end_date`` used to bound PERIODS -- a period was admitted when
      ``period.start_date <= end_date``, so the CURRENT period, which started
      before *as_of*, still matched and only ``should_skip_period`` stopped a
      further payment being generated into it for a loan that owes nothing.
      Forward generation stops at the first occurrence past the bound, so a
      retired loan's next installment is simply never emitted: finding **N-19**
      (a bound that excluded the current period outright would have to be that
      period's start minus a day) is closed by the model rather than by a
      different bound.  Note also that a retired loan whose
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


def _sync_loan_cadence(rule: "RecurrenceRule", params: "LoanParams") -> None:
    """Bring a loan recurrence's DAY and opening BOUND onto the loan's contract.

    Both values this writes derive from ONE fact -- the loan's
    ``payment_day`` -- so they are synced together.  Syncing either alone is
    the B-14 shape (a persisted copy drifting from its derivation), and here the
    two disagreeing is worse than either being stale:

    * ``start_date`` = the loan's FIRST CONTRACTUAL INSTALLMENT.  A payment
      cannot precede the loan.  Generation before origination is not merely
      early -- the fold ERASES it (the payment splits against a zero balance and
      the origination anchor then resets over it: $0.00 principal, the whole
      payment to Refund) while the cash side still debits it, so a mortgage
      closing one month out projected $3,220.92 of payments for a loan that did
      not exist (plan step C9a).
    * ``day_of_month`` = the contractual ``payment_day``, when the rule bills on
      a day at all.  Nothing else re-points it after a ``payment_day`` edit, and
      with only ``start_date`` moving the two contradict each other: measured on
      a mortgage whose ``payment_day`` went 1 -> 20, the bound advanced to the
      20th while the rule still matched the 1st, so the surviving period
      contained no matching day and the recurrence generated **nothing at all**.

    Skipped for a rule carrying NO ``day_of_month`` (an every-paycheck or
    every-N transfer): those schedule by pay period and have no contractual due
    day to keep in step, and writing one would re-date every generated instance.
    A rule whose typed ``day_of_month`` never matched the loan's ``payment_day``
    is corrected onto the contract here rather than left billing a day the
    servicer does not.

    **Scenario-INDEPENDENT, which is why it is separated from the end bound**
    (the C8e lesson): both values are functions of the loan's params alone, so
    they resolve for a user with NO baseline scenario, where the payoff-derived
    end bound cannot.  Keeping this ahead of the caller's scenario guard is what
    stops a missing baseline from silently leaving a loan unbounded at the start.

    Reads no clock: these are contract facts, not functions of when the sync
    happened to run (the A3 rule).  Idempotent -- writes only on a change, which
    happens at most once per ``payment_day`` edit.

    Args:
        rule: The recurring payment's :class:`RecurrenceRule`.
        params: The loan's :class:`~app.models.loan_params.LoanParams`.
    """
    new_start = rate_period_engine.first_installment_date(
        params.origination_date, params.payment_day,
    )
    # A day-less rule schedules by pay period; leave its day alone.
    new_day = (
        params.payment_day if rule.day_of_month is not None
        else rule.day_of_month
    )
    if rule.start_date == new_start and rule.day_of_month == new_day:
        return
    old_start, old_day = rule.start_date, rule.day_of_month
    # RE-AUTHORED, not assigned: a rule is written whole through one door, so
    # ``offset_periods`` is re-derived from the rule's own start period rather
    # than left holding the phase a previous cadence implied.  Both values
    # here also feed the rule's first occurrence, which is DERIVED on read
    # (plan step R2d) and so cannot lag the contract this edit states.  The
    # schedule is loaded only on this side of the no-change guard above, so
    # the ordinary settle / revert path (which reaches this function on every
    # loan-payment mutation) still costs no extra query.
    reauthor_rule(
        rule,
        replace(
            recurrence_spec(rule),
            start_date=new_start, day_of_month=new_day,
        ),
        calendar_for(rule.user_id),
    )
    log_event(
        logger, logging.INFO,
        EVT_LOAN_RECURRENCE_START_DATE_UPDATED, BUSINESS,
        "Updated recurrence rule start date to first contractual installment",
        account_id=params.account_id,
        rule_id=rule.id,
        old_start_date=str(old_start),
        new_start_date=str(new_start),
        old_day_of_month=old_day,
        new_day_of_month=new_day,
    )


def bind_rule_to_loan(rule: "RecurrenceRule", account_id: int) -> None:
    """Bound a NEWLY built recurrence rule to its destination account's loan life.

    The creation-time entry point, for a route that has just built a rule and is
    about to generate against it.  A no-op unless *account_id* is a configured
    loan, so a caller may call it for ANY destination without a type check.

    Takes the rule DIRECTLY rather than looking it up from the account, which
    :func:`sync_recurring_payment_bounds` must do: that lookup returns the
    account's FIRST active recurring template, so a second recurring payment
    created into the same loan would leave the NEW rule unbounded while
    re-bounding the old one -- silently reopening the very hole this closes.

    Args:
        rule: The just-built :class:`RecurrenceRule`, before generation.
        account_id: The transfer's destination account (any kind).
    """
    params = loan_loaders.load_loan_params(account_id)
    if params is None:
        return
    _sync_loan_cadence(rule, params)


def sync_recurring_payment_bounds(account_id: int) -> None:
    """Sync a loan's recurring-payment validity window to the loan's own facts.

    The ONE entry every chokepoint calls, syncing BOTH ends of the recurrence's
    window so no caller can move one and leave the other stale:

    * ``start_date`` -- the loan's first contractual installment
      (:func:`_sync_start_date`); a payment cannot precede the loan.
    * ``end_date`` -- the loan's derived payoff (R-4,
      :func:`recurrence_end_date`); a payment cannot follow the payoff.

    The two are deliberately NOT symmetric in what they require: the start is a
    contract fact and resolves with no scenario, while the end is a fold over
    the forward plan and needs a baseline.  So the start is written FIRST, ahead
    of the scenario guard -- a user with no baseline still gets a correctly
    bounded start rather than an unbounded one (the C8e lesson: a loan's
    contract terms are not scenario-scoped).

    A no-op -- returning before any write -- when the account is not a
    configured loan, has no recurring payment, or is already at the right
    bounds; the end half additionally no-ops without a baseline scenario.
    Flushes into the caller's transaction (does NOT commit).

    **A FRESH context per call, deliberately.**  This runs mid-mutation, so it
    must see the loan as the just-flushed write left it; a
    :class:`~app.services.balance_at.BalanceContext` is a plain value with
    a memo scoped to one read, never a request cache, so building one here is how
    a writer reads post-write state (see that module's "read pass, not request").

    Called from every chokepoint that can move the projected payoff: loan-params
    create / update, the ARM / origination-rate change, the balance true-up, the
    recurring-transfer creation, and the transfer settle / revert / edit / delete
    / restore paths (via :mod:`app.services.transfer_service._loan_posting`).

    Args:
        account_id: The loan account whose recurring-payment validity window
            (``start_date`` / ``end_date``) to sync.
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
    rule = template.recurrence_rule

    # The START bound first: it needs the loan's params and NOTHING else, so it
    # must not sit behind the scenario guard below (C8e -- a loan's contract
    # terms are not scenario-scoped).
    params = loan_loaders.load_loan_params(account_id)
    if params is None:
        # Not a configured loan (no LoanParams) -- neither bound is defined.
        return
    _sync_loan_cadence(rule, params)

    ctx = BalanceContext.build(account.user_id)
    if ctx.scenario is None:
        # No baseline scenario: the seam cannot value this loan (and would raise),
        # and there is no trajectory to bound the recurrence END by.  The start
        # bound above is already written -- it needed no scenario.
        return
    figures = balance_at.loan_figures(account, ctx)
    if figures is None:
        # Not a configured loan (no LoanParams) -- nothing to bound.
        return

    new_end_date = recurrence_end_date(
        figures.payoff_date, figures.is_retired, ctx.as_of,
    )
    new_bound = (
        NEVER_ENDS if new_end_date is None else EndsOnDate(on=new_end_date)
    )
    # The idempotence guard compares BOUNDS, not the date column (plan step
    # R7b-3).  Reading ``rule.end_date`` alone is the two-independent-fields
    # shape this step removed, and it is wrong in a way that matters: a rule
    # carrying a COUNT bound has ``end_date IS NULL``, so against a loan that
    # never pays off (``new_end_date is None``) the column test would compare
    # ``None == None`` and return -- leaving a count bound on a loan payment
    # whose stop this module owns.  Frozen dataclasses, so ``==`` is the whole
    # comparison.
    old_bound = end_bound_from_columns(rule.end_date, rule.max_occurrences)
    if old_bound == new_bound:
        return

    # The whole OLD BOUND, not its date half: when this fires because the old
    # bound was a COUNT -- the case the comparison above exists for -- reading
    # ``rule.end_date`` logs ``None`` and loses the fact a count bound was
    # discarded.  Repr'd rather than str'd so the shape is named.
    old_end_date = repr(old_bound)
    # Re-authored like the cadence above, and for the same reason: a rule is
    # written whole through one door, so there is no field-at-a-time write to
    # leave some other column holding a value this edit invalidated.
    # ``end_date`` is not an input to any derived value, so on this path the
    # re-author is ordinarily a no-op on every column but the one named --
    # which is the point of a uniform rule rather than one applied only where
    # it happens to matter.
    #
    # **The whole BOUND is replaced, not the date half of one** (plan step
    # R7b-3), and that is what keeps this line correct now that a rule can
    # also stop after a COUNT of occurrences.  While the bound was two
    # independent columns, ``replace(spec, end_date=payoff)`` wrote a date
    # beside a count the rule already carried and the pair reached the flush
    # as a ``CheckViolation`` on ``ck_recurrence_rules_single_end_bound`` --
    # an ordinary loan edit, 500ing.  An
    # :class:`~app.services.recurrence.EndBound` has three shapes and holds
    # one, so naming the new one discards whatever it replaces and there is no
    # second field for this writer to remember to clear.
    reauthor_rule(
        rule,
        replace(
            recurrence_spec(rule),
            end_bound=new_bound,
        ),
        calendar_for(account.user_id),
    )
    log_event(
        logger, logging.INFO,
        EVT_LOAN_RECURRENCE_END_DATE_UPDATED, BUSINESS,
        "Updated recurrence rule end date to projected payoff",
        account_id=account_id,
        template_id=template.id,
        old_end_date=old_end_date,
        new_end_date=str(new_end_date),
    )
