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
from datetime import timedelta
from decimal import Decimal

from app.services.cash_ledger import sum_projected
from app.services.interest_projection import calculate_interest


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


def calculate_balances_with_interest(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    anchor_balance, anchor_period_id, periods, transactions,
    interest_params=None, amount_overrides=None, *, accrual_start,
):
    """Same as calculate_balances but also returns interest earned per period.

    When interest_params is provided (an object with .apy and
    .compounding_frequency_id), interest is projected for each period and
    added to the running balance.

    Args:
        anchor_balance:    Decimal -- the real balance at the anchor period.
        anchor_period_id:  int -- the pay_period.id of the anchor.
        periods:           List of PayPeriod objects, ordered by period_index.
        transactions:      List of Transaction objects (including shadow transactions).
        interest_params:   Object with .apy (Decimal) and .compounding_frequency_id (int).
        amount_overrides:  Optional ``{transaction_id: Decimal}`` map (the live
                           projected-net seam, Workstream B), forwarded verbatim
                           to :func:`calculate_balances`.  Default None
                           preserves the prior behavior byte-identical.
        accrual_start:     ``datetime.date`` -- the first day modelled interest
                           may accrue on (ruling R-L; see
                           :func:`_layer_interest`).  Required and undefaulted
                           on purpose: the only value a default could carry is
                           "each period's own start", which is precisely the
                           rule this parameter exists to retire, so a caller
                           that forgot it would silently get the old answer.

    Returns:
        (balances, interest_by_period) where:
            balances: OrderedDict mapping period_id -> Decimal end balance
            interest_by_period: dict mapping period_id -> Decimal interest earned

    Pylint: ``too-many-arguments`` (7/5) / ``too-many-positional-arguments``
    (6/5) -- these are independent balance-projection inputs, not a
    cohesive entity: this is the sibling :func:`calculate_balances`'s five-arg
    signature plus ``interest_params`` and ``accrual_start``, forwarding five of
    them verbatim, so a param object would force the same bundle onto the clean
    sibling (and its many callers) or split two near-identical signatures.
    Bundling would be stamp coupling, mirroring the ``projection_inputs`` /
    ``growth_engine`` documented disables.
    """
    # First compute base balances without interest.
    base_balances, _ = calculate_balances(
        anchor_balance, anchor_period_id, periods, transactions,
        amount_overrides=amount_overrides,
    )

    if not interest_params or not hasattr(interest_params, "apy"):
        return base_balances, {}

    return _layer_interest(
        base_balances, periods, interest_params, accrual_start,
    )


def _layer_interest(base_balances, periods, interest_params, accrual_start):
    """Layer per-period interest on top of pre-computed base balances.

    Re-walks the periods in order, compounding interest forward: each
    period's interest is computed on its base balance plus the interest
    accrued in prior periods, then folded into the running balance.

    **A period accrues only over the days it holds the ASSERTED balance**
    (ruling R-L, plan step X-c2a): the accrual window is
    ``[max(period.start_date, accrual_start) .. period.end_date]``, so a
    period entirely after the assertion accrues in full, the assertion's own
    period accrues from the day it was asserted, and a period that ended
    before the assertion accrues nothing.  Everything at or before that
    assertion is a bank FACT the user typed in, and modelling interest across
    those days adds money the assertion already contains.  Before this rule
    accrual began at the anchor PERIOD's start, which can be up to 13 days
    early: measured on the real Fidelity Savings (``$5,363.56`` at 3.29% APY,
    asserted 2026-04-06 inside the 03-26..04-08 period), ``$6.77`` over 14 days
    where the honest window earns ``$1.45`` over 3.

    That one ``max`` is the whole rule and it needs no branch:
    :func:`~app.services.interest_projection.calculate_interest` returns zero
    for an inverted window (``period_start >= period_end``), so a period ending
    before *accrual_start* falls out arithmetically rather than through a guard
    a later reader could mistake for a special case.  Such a period keeps its
    place in BOTH returned maps, carrying its base balance and a zero accrual
    -- dropping it would put a hole in a column the caller is projecting.

    Args:
        base_balances: OrderedDict period_id -> Decimal end balance, the
            no-interest balances from :func:`calculate_balances`.
        periods: List of PayPeriod objects, ordered by period_index.
        interest_params: Object with .apy (Decimal) and
            .compounding_frequency_id (int).
        accrual_start: ``datetime.date`` -- the first day interest may accrue
            on, the UTC civil day of the account's LATEST balance assertion
            (the caller reads it off the dated ``AccountAnchorHistory`` source
            of truth).  It is NOT assumed to fall inside any particular
            period: the ``max`` above is total over every relationship between
            it and a period's span.

    Returns:
        (balances, interest_by_period) where balances is an OrderedDict
        period_id -> Decimal end balance with interest layered in, and
        interest_by_period maps period_id -> Decimal interest earned.
    """
    apy = interest_params.apy  # Already Decimal from Numeric(7,5) column.
    compounding_id = interest_params.compounding_frequency_id

    # Re-walk periods, layering interest on top of the base balances.
    balances = OrderedDict()
    interest_by_period = {}
    running_balance = None
    interest_cumulative = Decimal("0.00")

    for period in periods:
        if period.id not in base_balances:
            continue

        base_bal = base_balances[period.id]
        # Add cumulative interest from prior periods.
        running_balance = base_bal + interest_cumulative

        # Calculate interest for this period.  Pay periods carry an
        # INCLUSIVE end_date (a 14-calendar-day period runs
        # start .. start + 13), but calculate_interest treats period_end as
        # the EXCLUSIVE right boundary of a half-open [start, end) window
        # (its (period_end - period_start).days convention, verified by its
        # unit tests).  Pass end_date + 1 day -- the true exclusive boundary,
        # equal to the next period's start_date -- so the money accrues over
        # all 14 calendar days it is held, not 13.  Counting only 13 days
        # understated a HYSA's yield by ~1 day in 14 (~7%), the interest-path
        # twin of the growth_engine day-count defect.
        #
        # The left boundary is the LATER of the period's start and the
        # account's latest assertion (ruling R-L): a day at or before that
        # assertion is a bank fact, not a day to model.  An entirely
        # pre-assertion period inverts the window and earns zero without a
        # branch (see the docstring).
        interest = calculate_interest(
            balance=running_balance,
            apy=apy,
            compounding_frequency_id=compounding_id,
            period_start=max(period.start_date, accrual_start),
            period_end=period.end_date + timedelta(days=1),
        )
        interest_cumulative += interest
        running_balance += interest
        interest_by_period[period.id] = interest
        balances[period.id] = running_balance

    return balances, interest_by_period
