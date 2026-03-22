"""
Shekel Budget App -- Balance Calculator Service

A pure function that computes projected balances across pay periods.
No database writes, no side effects -- given an anchor and transactions,
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
from decimal import Decimal, ROUND_HALF_UP

from app.services.interest_projection import calculate_interest

logger = logging.getLogger(__name__)

# Status names that indicate "already settled" -- amounts baked into anchor.
SETTLED_STATUSES = frozenset({"done", "received"})


def calculate_balances(anchor_balance, anchor_period_id, periods, transactions,
                       transfers=None, account_id=None):
    """Compute projected end balances from the anchor forward.

    Args:
        anchor_balance:    Decimal -- the real checking balance at the anchor period.
        anchor_period_id:  int -- the pay_period.id of the anchor.
        periods:           List of PayPeriod objects, ordered by period_index.
                           Must start at or before the anchor period.
        transactions:      List of Transaction objects covering all supplied periods.
                           Should exclude is_deleted=True rows before passing in.
        transfers:         Optional list of Transfer objects. When provided with
                           account_id, transfer effects are factored into balances.
        account_id:        The account ID to calculate balances for. Required when
                           transfers are provided.

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

    # Group transfers by pay_period_id if provided.
    xfer_by_period = {}
    if transfers and account_id:
        for xfer in transfers:
            xfer_by_period.setdefault(xfer.pay_period_id, []).append(xfer)

    balances = OrderedDict()
    running_balance = None  # Set when we reach the anchor period.

    for period in periods:
        period_txns = txn_by_period.get(period.id, [])
        period_xfers = xfer_by_period.get(period.id, [])

        if period.id == anchor_period_id:
            # Anchor period: start from the real balance, add only remaining items.
            income, expenses = _sum_remaining(period_txns)
            xfer_in, xfer_out = _sum_transfer_effects_remaining(
                period_xfers, account_id
            )
            running_balance = (
                anchor_balance + income - expenses + xfer_in - xfer_out
            )

        elif running_balance is not None:
            # Post-anchor: roll forward from previous end balance.
            income, expenses = _sum_all(period_txns)
            xfer_in, xfer_out = _sum_transfer_effects_all(
                period_xfers, account_id
            )
            running_balance = (
                running_balance + income - expenses + xfer_in - xfer_out
            )

        else:
            # Pre-anchor period -- we don't calculate balances before the anchor.
            continue

        balances[period.id] = running_balance

    return balances


def calculate_balances_with_interest(
    anchor_balance, anchor_period_id, periods, transactions,
    transfers=None, account_id=None, hysa_params=None,
):
    """Same as calculate_balances but also returns interest earned per period.

    When hysa_params is provided (an object with .apy and .compounding_frequency),
    interest is projected for each period and added to the running balance.

    Args:
        anchor_balance:    Decimal -- the real balance at the anchor period.
        anchor_period_id:  int -- the pay_period.id of the anchor.
        periods:           List of PayPeriod objects, ordered by period_index.
        transactions:      List of Transaction objects.
        transfers:         Optional list of Transfer objects.
        account_id:        The account ID for transfer calculations.
        hysa_params:       Object with .apy (Decimal) and .compounding_frequency (str).

    Returns:
        (balances, interest_by_period) where:
            balances: OrderedDict mapping period_id → Decimal end balance
            interest_by_period: dict mapping period_id → Decimal interest earned
    """
    # First compute base balances without interest.
    base_balances = calculate_balances(
        anchor_balance, anchor_period_id, periods, transactions,
        transfers=transfers, account_id=account_id,
    )

    interest_by_period = {}

    if not hysa_params or not hasattr(hysa_params, "apy"):
        return base_balances, interest_by_period

    apy = Decimal(str(hysa_params.apy))
    compounding = hysa_params.compounding_frequency

    # Re-walk periods, layering interest on top of the base balances.
    balances = OrderedDict()
    running_balance = None
    interest_cumulative = Decimal("0.00")

    for period in periods:
        if period.id not in base_balances:
            continue

        base_bal = base_balances[period.id]
        # Add cumulative interest from prior periods.
        running_balance = base_bal + interest_cumulative

        # Calculate interest for this period.
        interest = calculate_interest(
            balance=running_balance,
            apy=apy,
            compounding_frequency=compounding,
            period_start=period.start_date,
            period_end=period.end_date,
        )
        interest_cumulative += interest
        running_balance += interest
        interest_by_period[period.id] = interest
        balances[period.id] = running_balance

    return balances, interest_by_period


def calculate_balances_with_amortization(
    anchor_balance, anchor_period_id, periods, transactions,
    transfers=None, account_id=None, loan_params=None,
):
    """Calculate balances for a debt account (mortgage or auto loan).

    For debt accounts, transfers INTO the account represent payments.
    Only the principal portion (determined by amortization) reduces the balance.
    Interest portion is the cost of the loan.

    Args:
        anchor_balance:    Decimal -- the current principal at the anchor period.
        anchor_period_id:  int -- the pay_period.id of the anchor.
        periods:           List of PayPeriod objects, ordered by period_index.
        transactions:      List of Transaction objects.
        transfers:         Optional list of Transfer objects.
        account_id:        The account ID for transfer calculations.
        loan_params:       Object with .current_principal, .interest_rate,
                           .term_months, .origination_date, .payment_day.

    Returns:
        (balances, principal_by_period) where:
            balances: OrderedDict mapping period_id → Decimal end balance
            principal_by_period: dict mapping period_id → Decimal principal paid
    """
    from app.services.amortization_engine import calculate_monthly_payment

    # First compute base balances (transfer effects applied normally).
    base_balances = calculate_balances(
        anchor_balance, anchor_period_id, periods, transactions,
        transfers=transfers, account_id=account_id,
    )

    principal_by_period = {}

    if not loan_params or not hasattr(loan_params, "interest_rate"):
        return base_balances, principal_by_period

    annual_rate = Decimal(str(loan_params.interest_rate))
    remaining_months = loan_params.term_months
    monthly_payment = calculate_monthly_payment(
        Decimal(str(loan_params.current_principal)),
        annual_rate,
        remaining_months,
    )

    monthly_rate = annual_rate / 12 if annual_rate > 0 else Decimal("0")

    # Re-walk periods, tracking the loan balance reduction by principal only.
    balances = OrderedDict()
    running_principal = None

    # Group transfers by period for fast lookup.
    xfer_by_period = {}
    if transfers and account_id:
        for xfer in transfers:
            xfer_by_period.setdefault(xfer.pay_period_id, []).append(xfer)

    for period in periods:
        if period.id not in base_balances:
            continue

        if period.id == anchor_period_id:
            running_principal = Decimal(str(anchor_balance))
        elif running_principal is None:
            continue

        # Calculate how much principal is paid this period.
        period_xfers = xfer_by_period.get(period.id, [])
        total_payment_in = Decimal("0.00")
        for xfer in period_xfers:
            status_name = xfer.status.name if xfer.status else "projected"
            if status_name in ("cancelled",):
                continue
            if xfer.to_account_id == account_id:
                total_payment_in += Decimal(str(xfer.amount))

        # For each payment, split into interest and principal.
        if total_payment_in > 0 and running_principal > 0:
            interest_portion = (running_principal * monthly_rate).quantize(
                Decimal("0.01"), ROUND_HALF_UP
            )
            principal_portion = total_payment_in - interest_portion
            principal_portion = max(principal_portion, Decimal("0.00"))
            principal_portion = min(principal_portion, running_principal)

            running_principal -= principal_portion
            running_principal = max(running_principal, Decimal("0.00"))
            principal_by_period[period.id] = principal_portion
        else:
            principal_by_period[period.id] = Decimal("0.00")

        balances[period.id] = running_principal

    return balances, principal_by_period


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

        # Settled items are already in the anchor -- skip.
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

    Done/received and credit items are excluded -- they are either already
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


def _sum_transfer_effects_remaining(transfers, account_id):
    """Sum transfer effects for the anchor period (remaining/projected only).

    Settled transfers (done/received) are already reflected in the anchor
    balance, so we exclude them.

    Returns:
        (incoming, outgoing) as Decimal tuple.
    """
    incoming = Decimal("0.00")
    outgoing = Decimal("0.00")

    for xfer in transfers:
        status_name = xfer.status.name if xfer.status else "projected"
        if status_name in SETTLED_STATUSES or status_name in ("cancelled",):
            continue

        amount = Decimal(str(xfer.amount))
        if xfer.to_account_id == account_id:
            incoming += amount
        if xfer.from_account_id == account_id:
            outgoing += amount

    return incoming, outgoing


def _sum_transfer_effects_all(transfers, account_id):
    """Sum transfer effects for a post-anchor period (projected only).

    Returns:
        (incoming, outgoing) as Decimal tuple.
    """
    incoming = Decimal("0.00")
    outgoing = Decimal("0.00")

    for xfer in transfers:
        status_name = xfer.status.name if xfer.status else "projected"
        if status_name in ("cancelled", "done", "received"):
            continue

        amount = Decimal(str(xfer.amount))
        if xfer.to_account_id == account_id:
            incoming += amount
        if xfer.from_account_id == account_id:
            outgoing += amount

    return incoming, outgoing
