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
      done / received -> excluded (already settled)
      projected       -> effective_amount (actual if populated, else estimated)
      credit          -> excluded (does not affect checking balance)

Transfer effects are included automatically via shadow transactions
(expense and income Transaction rows with transfer_id IS NOT NULL).
The calculator does NOT query or process Transfer objects directly.
This eliminates the double-counting risk described in design doc section 16.1.
"""

import logging
from collections import OrderedDict
from decimal import Decimal

from app.services.interest_projection import calculate_interest
from app.utils.balance_predicates import is_projected

logger = logging.getLogger(__name__)


def calculate_balances(anchor_balance, anchor_period_id, periods, transactions,
                       income_overrides=None):
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
        income_overrides:  Optional dict mapping transaction id -> Decimal
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
            # Anchor period: start from the real balance, add only remaining items.
            income, expenses = _sum_remaining(period_txns, income_overrides)
            running_balance = anchor_balance + income - expenses

        elif running_balance is not None:
            # Post-anchor: roll forward from previous end balance.
            income, expenses = _sum_all(period_txns, income_overrides)
            running_balance = running_balance + income - expenses

        else:
            # Pre-anchor period -- we don't calculate balances before the anchor.
            continue

        balances[period.id] = running_balance

    # Detect stale anchor: check for done/received transactions in
    # post-anchor periods.  These are excluded from balance calculations
    # (correctly -- the anchor already reflects them IF it was updated),
    # but if the anchor was NOT updated, projections will be wrong.
    stale_anchor_warning = False
    past_anchor = False
    for period in periods:
        if period.id == anchor_period_id:
            past_anchor = True
            continue  # Skip the anchor period itself.
        if not past_anchor:
            continue
        for txn in txn_by_period.get(period.id, []):
            # Settled transactions in post-anchor periods suggest the
            # anchor balance may be stale (not yet true-up'd).
            if txn.status and txn.status.is_settled:
                stale_anchor_warning = True
                break
        if stale_anchor_warning:
            break

    return balances, stale_anchor_warning


def calculate_balances_with_interest(
    anchor_balance, anchor_period_id, periods, transactions,
    interest_params=None, income_overrides=None,
):
    # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    # The six inputs are cohesive balance-projection parameters (anchor
    # balance, anchor period id, period list, transactions, interest config,
    # and the Workstream B income-override seam forwarded verbatim to
    # calculate_balances).  A params object would be gratuitous churn across
    # every caller of a pure function; the arg/local counts are inherent to
    # a balance projection, not a decomposition smell.
    """Same as calculate_balances but also returns interest earned per period.

    When interest_params is provided (an object with .apy and
    .compounding_frequency), interest is projected for each period and
    added to the running balance.

    Args:
        anchor_balance:    Decimal -- the real balance at the anchor period.
        anchor_period_id:  int -- the pay_period.id of the anchor.
        periods:           List of PayPeriod objects, ordered by period_index.
        transactions:      List of Transaction objects (including shadow transactions).
        interest_params:   Object with .apy (Decimal) and .compounding_frequency (str).
        income_overrides:  Optional ``{transaction_id: Decimal}`` map (the live
                           projected-net seam, Workstream B), forwarded verbatim
                           to :func:`calculate_balances`.  Default None
                           preserves the prior behavior byte-identical.

    Returns:
        (balances, interest_by_period) where:
            balances: OrderedDict mapping period_id -> Decimal end balance
            interest_by_period: dict mapping period_id -> Decimal interest earned
    """
    # First compute base balances without interest.
    base_balances, _ = calculate_balances(
        anchor_balance, anchor_period_id, periods, transactions,
        income_overrides=income_overrides,
    )

    interest_by_period = {}

    if not interest_params or not hasattr(interest_params, "apy"):
        return base_balances, interest_by_period

    apy = interest_params.apy  # Already Decimal from Numeric(7,5) column.
    compounding = interest_params.compounding_frequency

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


def _entry_aware_amount(txn):
    """Compute the checking-balance impact for a single expense transaction.

    For projected expenses with entries (loaded eagerly or
    lazy-loaded on demand), the formula partitions debit entries into
    cleared and uncleared buckets, then holds back only the portion
    of the budget that has not yet been reconciled with the anchor:

        cleared_debit   = sum(entries where not is_credit and     is_cleared)
        uncleared_debit = sum(entries where not is_credit and not is_cleared)
        sum_credit      = sum(entries where is_credit)

        checking_impact = max(
            estimated_amount - cleared_debit - sum_credit,
            uncleared_debit,
        )

    Semantics:
      - A cleared debit is already reflected in the checking anchor
        balance, so it should not come out of the projection again --
        we subtract it from the reservation.
      - An uncleared debit has hit real checking but is NOT yet in the
        anchor, so the full estimated amount must still be held back
        (the max() floor handles this and also handles overspend where
        uncleared debits exceed the remaining reservation).
      - A credit entry never hits checking directly -- it flows through
        a CC Payback sibling transaction -- so it only reduces the
        reservation.
      - With every is_cleared = FALSE (the default for new entries),
        cleared_debit = 0 and the formula reduces to
        max(estimated - sum_credit, uncleared_debit), which matches
        the pre-cleared-flag behavior from scope doc section 4.2.

    Example (the user's grocery bug):
      est = 500, three cleared debit purchases summing to 462.34.
      checking_impact = max(500 - 462.34 - 0, 0) = 37.66, which is the
      remaining budget to hold back now that the anchor reflects the
      first three purchases.

    Seam removed (Commit 5 / CRIT-01 / F-009 / E-25): the pre-Commit-5
    implementation guarded the entry formula behind an
    eager-load presence check on the relationship (the ``entries``
    key in the SQLAlchemy instance dict), and returned
    ``txn.effective_amount`` whenever that check missed.  That
    silently degraded to the non-entries-aware value whenever the
    consuming query had not issued
    ``selectinload(Transaction.entries)``.  Symptom #1 ($160 on grid
    vs $114.29 on /savings for the same data) is exactly that seam in
    production: the grid eager-loaded entries and computed the
    reduction; /savings did not and got back ``estimated_amount``
    unchanged.  E-25's correction makes the canonical producer
    ``app.services.balance_resolver.balances_for`` always
    eager-load entries, so this function never sees an unloaded
    relationship from a routed caller.  The remaining
    ``getattr(txn, "entries", ())`` access below covers two safe
    cases:

      * **Not-yet-routed ORM callers** (savings/accounts/calendar/
        year-end/investment/retirement, fixed in Commits 6-9): the
        SQLAlchemy descriptor lazy-loads the relationship.  The
        caller now gets the CORRECT entries-aware value with one
        extra SELECT per transaction (acceptable for the transition;
        the producer routing eliminates the extra query).
      * **Non-ORM test fakes** with no ``entries`` attribute:
        ``getattr`` returns the default ``()``, the empty-entries
        early return fires, and the function returns
        ``effective_amount`` -- the same behavior pre-Commit-5 had
        for test fakes.

    What is no longer possible: the same Projected envelope expense
    yielding two different values for two different consumers based
    purely on whether their query happened to ``selectinload``.

    Args:
        txn: A Transaction object.  The ``entries`` relationship may
            be eager-loaded (canonical producer), unloaded
            (transitional caller; lazy-loads on demand), or absent
            (test fake).

    Returns:
        Decimal -- the amount this transaction contributes to checking
        balance.
    """
    # ``getattr`` with a default of ``()`` handles both unloaded ORM
    # relationships (descriptor lazy-loads via the session) and
    # non-ORM fakes (no attribute defined).  The empty-tuple default
    # passes the falsy check below, mirroring the original empty-list
    # short-circuit and keeping non-ORM tests stable.
    entries = getattr(txn, "entries", ())
    if not entries:
        return txn.effective_amount

    # Only apply the entry formula to projected transactions.
    # Settled, cancelled, and credit statuses are already handled
    # correctly by effective_amount (returns 0 for excluded statuses,
    # actual_amount for settled statuses).  Routed through the
    # centralized ``is_projected`` predicate (D6-09 / MED-02) so
    # this entry-formula gate cannot drift from the other
    # Projected-only filters in this module and in the balance
    # resolver.
    if not is_projected(txn):
        return txn.effective_amount

    # Three-bucket partition: cleared debit, uncleared debit, credit.
    cleared_debit = Decimal("0")
    uncleared_debit = Decimal("0")
    sum_credit = Decimal("0")
    for entry in entries:
        if entry.is_credit:
            sum_credit += entry.amount
        elif entry.is_cleared:
            cleared_debit += entry.amount
        else:
            uncleared_debit += entry.amount

    # Cleared debits are already in the anchor -- subtract them from the
    # reservation.  Uncleared debits act as a floor (the reservation can
    # never be smaller than uncleared checking hits).
    return max(
        txn.estimated_amount - cleared_debit - sum_credit,
        uncleared_debit,
    )


def _income_amount(txn, income_overrides):
    """Return the income contribution for ``txn``, honoring a live override.

    ``income_overrides`` is the live projected-net seam (Workstream B):
    a dict mapping transaction id -> Decimal produced by
    :func:`app.services.income_service.live_projected_net`.  When the
    transaction's id is present, the live-recomputed net is used in
    place of the stored ``effective_amount`` so a projected salary
    paycheck reflects the current salary profile rather than a cached
    amount a later profile/calibration/code change may have invalidated.
    ``income_overrides=None`` (the default everywhere this module is
    called without the seam) returns ``effective_amount`` unchanged, so
    the pre-seam behavior is byte-identical.

    Args:
        txn: An income Transaction.
        income_overrides: Optional ``{transaction_id: Decimal}`` map, or
            None.

    Returns:
        Decimal -- the override amount when present, else
        ``txn.effective_amount``.
    """
    if income_overrides is not None:
        override = income_overrides.get(txn.id)
        if override is not None:
            return override
    return txn.effective_amount


def _sum_remaining(transactions, income_overrides=None):
    """Sum only REMAINING (projected) transactions for the anchor period.

    Items marked done/received are already reflected in the anchor balance
    the user entered, so we exclude them.  Credit items are always excluded.

    Income uses effective_amount (actual if set, else estimated).
    Expenses use _entry_aware_amount, which applies the entry-checking
    formula for projected expenses with loaded entries, falling back
    to effective_amount otherwise.

    Returns:
        (total_income, total_expenses) as Decimal tuple.
    """
    income = Decimal("0.00")
    expenses = Decimal("0.00")

    for txn in transactions:
        # Only projected items remain to be settled -- everything else
        # is either already in the anchor or excluded from balance.
        # Routed through the centralized ``is_projected`` predicate
        # (D6-09 / MED-02) so the anchor-period Projected filter
        # shares one definition with ``_sum_all`` and
        # ``_entry_aware_amount`` below.
        if not is_projected(txn):
            continue

        if txn.is_income:
            income += _income_amount(txn, income_overrides)
        elif txn.is_expense:
            expenses += _entry_aware_amount(txn)

    return income, expenses


def _sum_all(transactions, income_overrides=None):
    """Sum remaining (projected) transactions for a non-anchor period.

    Only projected items contribute to the projected balance.  Settled,
    credit, and cancelled transactions are excluded.

    Income uses effective_amount (actual if set, else estimated).
    Expenses use _entry_aware_amount, which applies the entry-checking
    formula for projected expenses with loaded entries, falling back
    to effective_amount otherwise.

    Returns:
        (total_income, total_expenses) as Decimal tuple.
    """
    income = Decimal("0.00")
    expenses = Decimal("0.00")

    for txn in transactions:
        # Only projected items affect the projected balance.
        # Routed through the centralized ``is_projected`` predicate
        # (D6-09 / MED-02) so the post-anchor Projected filter
        # shares one definition with ``_sum_remaining`` above and
        # ``_entry_aware_amount``.
        if not is_projected(txn):
            continue

        if txn.is_income:
            income += _income_amount(txn, income_overrides)
        elif txn.is_expense:
            expenses += _entry_aware_amount(txn)

    return income, expenses
