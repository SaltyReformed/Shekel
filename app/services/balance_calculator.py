"""
Shekel Budget App — Balance Calculator Service

A pure function that computes projected balances across pay periods.
No database writes, no side effects — given an anchor and transactions,
it returns balances.  Called on every grid load.

Calculation rules:
  - Anchor period: end_balance = anchor_balance + remaining_income - remaining_expenses
    where "remaining" means projected items not yet reflected in the anchor.
  - Subsequent periods: end_balance[n] = end_balance[n-1] + remaining_income[n] - remaining_expenses[n]
  - All periods use only projected (unsettled) items:
      done / received → excluded (already settled)
      projected       → estimated_amount
      credit          → excluded (does not affect checking balance)
"""

import logging
from collections import OrderedDict
from decimal import Decimal

logger = logging.getLogger(__name__)

# Status names that indicate "already settled" — amounts baked into anchor.
SETTLED_STATUSES = frozenset({"done", "received"})


def calculate_balances(anchor_balance, anchor_period_id, periods, transactions):
    """Compute projected end balances from the anchor forward.

    Args:
        anchor_balance:    Decimal — the real checking balance at the anchor period.
        anchor_period_id:  int — the pay_period.id of the anchor.
        periods:           List of PayPeriod objects, ordered by period_index.
                           Must start at or before the anchor period.
        transactions:      List of Transaction objects covering all supplied periods.
                           Should exclude is_deleted=True rows before passing in.

    Returns:
        OrderedDict mapping period_id → Decimal end balance, in period order.
    """
    if anchor_balance is None:
        anchor_balance = Decimal("0.00")
    else:
        anchor_balance = Decimal(str(anchor_balance))

    # Group transactions by pay_period_id for fast lookup.
    txn_by_period = {}
    for txn in transactions:
        txn_by_period.setdefault(txn.pay_period_id, []).append(txn)

    balances = OrderedDict()
    running_balance = None  # Set when we reach the anchor period.

    for period in periods:
        period_txns = txn_by_period.get(period.id, [])

        if period.id == anchor_period_id:
            # Anchor period: start from the real balance, add only remaining items.
            income, expenses = _sum_remaining(period_txns)
            running_balance = anchor_balance + income - expenses

        elif running_balance is not None:
            # Post-anchor: roll forward from previous end balance.
            income, expenses = _sum_all(period_txns)
            running_balance = running_balance + income - expenses

        else:
            # Pre-anchor period — we don't calculate balances before the anchor.
            continue

        balances[period.id] = running_balance

    return balances


def _sum_remaining(transactions):
    """Sum only REMAINING (projected) transactions for the anchor period.

    Items marked done/received are already reflected in the anchor balance
    the user entered, so we exclude them.  Credit items are always excluded.

    Returns:
        (total_income, total_expenses) as Decimal tuple.
    """
    income = Decimal("0.00")
    expenses = Decimal("0.00")

    for txn in transactions:
        status_name = txn.status.name if txn.status else "projected"

        # Credit transactions never affect checking balance.
        if status_name in ("credit", "cancelled"):
            continue

        # Settled items are already in the anchor — skip.
        if status_name in SETTLED_STATUSES:
            continue

        # Remaining projected items.
        amount = Decimal(str(txn.estimated_amount))
        if txn.is_income:
            income += amount
        elif txn.is_expense:
            expenses += amount

    return income, expenses


def _sum_all(transactions):
    """Sum remaining (projected) transactions for a non-anchor period.

    Done/received and credit items are excluded — they are either already
    reflected in the anchor balance or represent settled items that should
    not change the projected balance.  Only projected items contribute.

    Returns:
        (total_income, total_expenses) as Decimal tuple.
    """
    income = Decimal("0.00")
    expenses = Decimal("0.00")

    for txn in transactions:
        status_name = txn.status.name if txn.status else "projected"

        # Credit and settled transactions excluded from projected balance.
        if status_name in ("credit", "cancelled", "done", "received"):
            continue

        amount = Decimal(str(txn.estimated_amount))
        if txn.is_income:
            income += amount
        elif txn.is_expense:
            expenses += amount

    return income, expenses
