"""
Shekel Budget App -- Balance Calculator Service

A pure function that computes projected balances across pay periods.
No database writes, no side effects -- given an anchor and transactions,
it returns balances.  Called on every grid load.

Calculation rules:
  - Anchor period: end_balance = anchor_balance + remaining_income - remaining_expenses
    where "remaining" means projected items not yet reflected in the anchor.
  - Subsequent periods:
    end_balance[n] = end_balance[n-1] + remaining_income[n] - remaining_expenses[n]
  - All periods use only projected (unsettled) items:
      done / received -> excluded (already settled)
      projected       -> effective_amount (actual if populated, else estimated)
      credit          -> excluded (does not affect checking balance)

Transfer effects are included automatically via shadow transactions
(expense and income Transaction rows with transfer_id IS NOT NULL).
The calculator does NOT query or process Transfer objects directly.
This eliminates the double-counting risk described in design doc section 16.1.

This module is a PRODUCER: everything it defines answers "what is the balance
at T".  What one row is WORTH, and what a set of rows SUMS TO, are not balance
questions and live in the :mod:`app.services.cash_ledger` leaf this walk folds
over (plan step D1c) -- ``sum_projected`` and the per-row valuation rules moved
there, having been stranded here since before the fence existed (finding N-30).
"""

from collections import OrderedDict
from decimal import Decimal

from app.services.cash_ledger import sum_projected


def _detect_stale_anchor(periods, anchor_period_id, txn_by_period):
    """Return True if a settled (done/received) transaction exists in any
    post-anchor period -- a signal the anchor balance may be stale.

    Settled post-anchor transactions are excluded from the balance
    calculation (the anchor already reflects them IF it was true-up'd); but
    if the anchor was NOT updated, the projection will be wrong, so this is
    surfaced as an informational warning.  Operates on the already-grouped
    in-memory ``txn_by_period`` -- it issues NO query (Transfer Invariant 5
    binds the caller that builds ``transactions``, not this scan).
    """
    past_anchor = False
    for period in periods:
        if period.id == anchor_period_id:
            past_anchor = True
            continue  # Skip the anchor period itself.
        if not past_anchor:
            continue
        for txn in txn_by_period.get(period.id, []):
            if txn.status and txn.status.is_settled:
                return True
    return False


def calculate_balances(anchor_balance, anchor_period_id, periods, transactions,
                       amount_overrides=None):
    """Compute projected end balances from the anchor forward.

    Args:
        anchor_balance:    Decimal -- the real checking balance at the anchor period.
        anchor_period_id:  int -- the pay_period.id of the anchor.
        periods:           List of PayPeriod objects, ordered by period_index.
                           Must start at or before the anchor period.
        transactions:      List of Transaction objects covering all supplied periods.
                           Should exclude is_deleted=True rows before passing in.
                           Shadow transactions (transfer_id IS NOT NULL) participate
                           identically to regular transactions.
        amount_overrides:  Optional dict mapping transaction id -> Decimal
                           (the live projected-net seam, Workstream B).  An
                           income transaction whose id is a key uses the
                           override in place of its stored effective_amount;
                           default None preserves the prior behavior
                           byte-identical.

    Returns:
        (balances, stale_anchor_warning) where:
            balances: OrderedDict mapping period_id -> Decimal end balance
            stale_anchor_warning: bool -- True if done/received transactions
                exist in post-anchor periods, indicating the anchor balance
                may not reflect recent activity.  Informational only -- does
                not change the calculated balances.
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
            # Anchor period: start from the real balance, add the projected
            # remainder (settled items are already in the anchor balance).
            income, expenses = sum_projected(period_txns, amount_overrides)
            running_balance = anchor_balance + income - expenses

        elif running_balance is not None:
            # Post-anchor: roll forward from the previous end balance, adding
            # this period's projected income and expenses.
            income, expenses = sum_projected(period_txns, amount_overrides)
            running_balance = running_balance + income - expenses

        else:
            # Pre-anchor period -- we don't calculate balances before the anchor.
            continue

        balances[period.id] = running_balance

    # Detect stale anchor: a settled transaction in a post-anchor period
    # suggests the anchor balance may not reflect recent activity.  Purely
    # informational -- it does not change the calculated balances.
    stale_anchor_warning = _detect_stale_anchor(
        periods, anchor_period_id, txn_by_period,
    )

    return balances, stale_anchor_warning


# The interest-layering half of this module MOVED to
# ``app.services.balance_at._interest`` at plan step X-c2b2, with the base it
# layered over: an INTEREST account's balance is now the cash FOLD plus a
# modelled accrual, so "compute a base then layer" stopped being one function
# whose base this module owned.  ``calculate_balances_with_interest`` went with
# it -- its whole body was that composition and it had no caller left.
