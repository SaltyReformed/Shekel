"""Loan recurring-payment end-date sync (Risk R-4: off the GET path).

Keeps a loan's recurring-payment :class:`~app.models.recurrence_rule.RecurrenceRule`
``end_date`` equal to the loan's PROJECTED PAYOFF, so the recurrence engine stops
generating shadow transactions past payoff.  This used to run as a write on the
loan-detail GET (documented Risk R-4); it now runs at every chokepoint that can
MOVE the projected payoff -- a params / rate edit, a balance true-up, and every
transfer settle / revert / edit / delete / restore of a loan payment (where an
extra-principal payment shifts payoff earliest) -- so ``end_date`` tracks payoff
without any read-path write.

Idempotent: it recomputes the payoff and writes only when ``end_date`` actually
changes, so re-running at the same state is a no-op.  The payoff is always
measured in the owner's BASELINE scenario (the loan card's trajectory), whatever
scenario triggered the sync.  Flask-isolated: plain ``account_id`` in, no
``request`` / ``session`` reads; flushes into the caller's transaction and never
commits (the caller owns the transaction boundary).
"""

import logging
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.services.loan_resolution import resolve_account_loan
from app.services.recurring_transfer_query import (
    active_recurring_transfer_template,
)
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.log_events import (
    BUSINESS,
    EVT_LOAN_RECURRENCE_END_DATE_UPDATED,
    log_event,
)

logger = logging.getLogger(__name__)

_ZERO_MONEY = Decimal("0.00")


def projected_payoff_end_date(schedule: list, origination_date: date) -> date | None:
    """Return the recurrence end_date a loan's projected schedule implies.

    Three cases (unchanged from the retired GET-path writer):

    * **Normal payoff** -- the last scheduled payment's date, so recurrence stops
      the month the loan reaches zero.
    * **Already paid off** (empty schedule) -- the loan's ``origination_date``, a
      past date that halts future generation (mirrors the old summary fallback).
    * **No payoff within term** (the schedule ends with a positive remaining
      balance, e.g. a negative-amortization plan paying under the monthly
      interest) -- ``None``, leaving recurrence indefinite until the user adjusts
      the payment.

    Args:
        schedule: The loan's projected :class:`~app.services.amortization_engine.AmortizationRow`
            list (``LoanState.schedule`` -- confirmed history + committed forward).
        origination_date: The loan's origination date, the empty-schedule fallback.

    Returns:
        The projected payoff date, or ``None`` when the loan does not pay off
        within its projected term.
    """
    if not schedule:
        return origination_date
    if schedule[-1].remaining_balance > _ZERO_MONEY:
        return None
    return schedule[-1].payment_date


def sync_recurring_payment_end_date(account_id: int) -> None:
    """Sync a loan's recurring-payment end_date to its projected payoff (R-4).

    The relocated end-date write: resolve the loan in its owner's baseline
    scenario, find its active recurring payment template, and set the recurrence
    ``end_date`` to the projected payoff (:func:`projected_payoff_end_date`) when
    it differs.  A no-op -- returning before any write -- when the account is not
    a configured loan, has no baseline scenario, has no recurring payment, or is
    already at the right end_date.  Flushes into the caller's transaction (does
    NOT commit).

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
    scenario = get_baseline_scenario(account.user_id)
    if scenario is None:
        return
    resolved = resolve_account_loan(account_id, scenario.id, date.today())
    if resolved is None:
        # Not a configured loan (no LoanParams) -- nothing to bound.
        return
    params, state = resolved
    template = active_recurring_transfer_template(account_id, account.user_id)
    if template is None or template.recurrence_rule is None:
        return

    new_end_date = projected_payoff_end_date(
        state.schedule, params.origination_date,
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
