"""
Shekel Budget App -- Year-End Summary: per-account balance adapter + interest.

Thin adapter over the shared :mod:`app.services.net_worth_kernel`: the
per-account balance-map dispatch (amortization schedule / interest
calculator / growth engine / plain resolver) and the amortization-schedule
generation now live in the kernel so the year-end net-worth section and
the savings cockpit compute net worth from one set of math (Loop B Phase
1).  This module unpacks the year-end ``_ProjectionInputs`` bundle into
the kernel's loose per-account parameters and keeps the year-end-specific
interest helpers (full-year interest, pre-anchor interest reverse-derive,
the settled-net walk) that the savings-progress section consumes.
"""

from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.interest_params import InterestParams
from app.models.scenario import Scenario
from app.services import balance_calculator, net_worth_kernel
from app.services.interest_projection import calculate_interest
# Re-exported from the kernel so the year-end savings-progress section
# (:mod:`._savings`) and the net-worth section (:mod:`._net_worth`) keep
# their existing ``from ._balances import ...`` paths after the Loop B
# Phase 1 move, and the loan-unified-figures integration test keeps
# calling ``_balances._generate_debt_schedules``.  The kernel is the one
# definition; these names are stable aliases over it (listed in
# ``__all__`` so the re-export is intentional, not an unused import).
from app.services.net_worth_kernel import (
    _load_shadow_contributions,
    base_account_balance_map as _base_account_balance_map,
    generate_debt_schedules as _generate_debt_schedules,
)
from app.services.year_end_summary_service._types import _ProjectionInputs

ZERO = Decimal("0")

# The kernel names re-exported above are consumed by sibling year-end
# modules and the loan-unified-figures integration test through this
# module's namespace; declaring them here marks the re-export as
# deliberate (pylint ``unused-import`` otherwise flags them, since it
# cannot see the cross-module consumers).
__all__ = [
    "_base_account_balance_map",
    "_compute_interest_for_year",
    "_compute_pre_anchor_interest",
    "_dispatch_account_balance_map",
    "_generate_debt_schedules",
    "_load_shadow_contributions",
    "_settled_net_by_period",
    "_sum_shadow_income",
]


def _dispatch_account_balance_map(
    account: Account,
    scenario: Scenario,
    periods: list,
    inputs: _ProjectionInputs,
) -> dict | None:
    """Compute period_id -> balance for one account, dispatching on type.

    Thin adapter (Loop B Phase 1): unpacks the year-end
    ``_ProjectionInputs`` bundle into the per-account parameters
    :func:`app.services.net_worth_kernel.build_account_balance_map`
    takes -- this account's debt schedule, its
    :class:`~app.models.investment_params.InvestmentParams`, its
    deductions, and the engine gross-biweekly -- so the kernel owns the
    dispatch math (amortization schedule / growth engine / interest /
    plain resolver) and the year-end net-worth section and the savings
    cockpit cannot drift onto two copies of it.

    Args:
        account: The account to project.
        scenario: The baseline scenario.
        periods: All user pay periods.
        inputs: Pre-loaded projection parameter maps (MED-01 / S6-06):
            ``debt_schedules`` selects the schedule path for debt accounts
            and the investment trio drives the growth-engine path.

    Returns:
        OrderedDict mapping period_id to Decimal balance, or None if the
        account has no anchor period.
    """
    return net_worth_kernel.build_account_balance_map(
        account, scenario, periods,
        debt_schedule=inputs.debt_schedules.get(account.id),
        investment_params=inputs.investment_params_map.get(account.id),
        deductions=inputs.deductions_by_account.get(account.id, []),
        salary_gross_biweekly=inputs.salary_gross_biweekly,
    )


def _sum_shadow_income(
    account_id: int,
    period_ids: list[int],
    scenario_id: int,
) -> Decimal:
    """Sum shadow income transactions (transfers in) for an account.

    Args:
        account_id: Target account ID.
        period_ids: Pay period IDs to query.
        scenario_id: Baseline scenario ID.

    Returns:
        Decimal total contributions from shadow income transactions.
    """
    # Reuse the shared shadow-income loader so the contribution sum and the
    # contribution timeline can never disagree on which rows count.  An
    # empty ``period_ids`` flows through ``_load_shadow_contributions``'s
    # own empty-list guard, so the loop yields ZERO.
    total = ZERO
    for txn in _load_shadow_contributions(account_id, scenario_id, period_ids):
        total += txn.effective_amount
    return total


def _compute_interest_for_year(
    account: Account,
    interest_params: InterestParams,
    scenario: Scenario,
    all_periods: list,
    year: int,
) -> Decimal:
    """Compute total interest earned on an account during the year.

    Calls calculate_balances_with_interest() and sums the interest
    from periods whose start_date falls in the target year.

    Args:
        account: Interest-bearing account.
        interest_params: InterestParams for the account.
        scenario: Baseline scenario.
        all_periods: All user pay periods.
        year: Target calendar year.

    Returns:
        Decimal total interest earned in the year.
    """
    if account.current_anchor_period_id is None:
        return ZERO

    transactions = net_worth_kernel.load_account_period_transactions(
        account.id, scenario.id, [p.id for p in all_periods],
    )

    anchor_balance = account.current_anchor_balance or ZERO
    _, interest_by_period = balance_calculator.calculate_balances_with_interest(
        anchor_balance=anchor_balance,
        anchor_period_id=account.current_anchor_period_id,
        periods=all_periods,
        transactions=transactions,
        interest_params=interest_params,
    )

    total = ZERO
    for period in all_periods:
        if period.start_date.year == year:
            total += interest_by_period.get(period.id, ZERO)
    return total


def _settled_net_by_period(
    account: Account,
    scenario: Scenario,
    period_ids: list[int],
) -> dict[int, Decimal]:
    """Sum settled income-minus-expenses per pay period for an account.

    Returns the net balance change each period's *settled* transactions
    contributed -- income ``effective_amount`` minus expense
    ``effective_amount`` -- keyed by ``pay_period_id``.  Only settled
    (done / received / settled) rows are summed because those are the
    transactions that actually moved money to build the captured anchor
    balance, so subtracting them walks the balance backward correctly;
    projected rows are not yet reflected in the anchor, exactly as the
    forward balance walk excludes them past the anchor (E-25).
    ``effective_amount`` returns the settled ``actual_amount`` for these
    rows.

    Args:
        account: The interest-bearing account.
        scenario: Baseline scenario (query scope).
        period_ids: Pay period IDs to sum.

    Returns:
        dict mapping ``pay_period_id`` to net Decimal (income minus
        expenses); a period with no settled income/expense rows is absent.
    """
    if not period_ids:
        return {}

    # Shared loader (Loop B Phase 1).  ``Transaction.status`` is
    # ``lazy="joined"`` on the model, so the settled-net check below reads
    # ``txn.status.is_settled`` off these rows without an N+1 -- the
    # explicit ``joinedload(status)`` this query carried pre-move was
    # redundant with that model-level strategy and is dropped here.
    transactions = net_worth_kernel.load_account_period_transactions(
        account.id, scenario.id, period_ids,
    )

    net_by_period: dict[int, Decimal] = {}
    for txn in transactions:
        if txn.status is None or not txn.status.is_settled:
            continue
        if txn.is_income:
            delta = txn.effective_amount
        elif txn.is_expense:
            delta = -txn.effective_amount
        else:
            continue
        net_by_period[txn.pay_period_id] = (
            net_by_period.get(txn.pay_period_id, ZERO) + delta
        )

    return net_by_period


def _compute_pre_anchor_interest(
    account: Account,
    interest_params: InterestParams,
    scenario: Scenario,
    all_periods: list,
    year: int,
) -> Decimal:
    """Estimate interest earned in pre-anchor periods of the target year.

    When the anchor falls after January 1 of the target year,
    ``calculate_balances_with_interest`` produces no balance for the
    pre-anchor periods, so :func:`_compute_interest_for_year` does not
    count their interest.  This function fills that gap.

    The balance during those periods was lower than the anchor balance
    (the contributions that built it up had not yet arrived), so accruing
    interest on the flat anchor balance overstates it.  Instead this
    reverse-derives each pre-anchor period's end balance: it walks
    backward from the anchor balance, subtracting the net settled activity
    (income minus expenses) that occurred after each period -- the
    transactions that actually built the balance up to the captured anchor
    -- and accrues interest on that lower, period-correct balance via the
    same ``calculate_interest`` the forward path (``_layer_interest``)
    uses on each period's end balance.  Only settled activity is
    un-walked, matching how the anchor reflects settled rows only.  The
    second-order interest-on-interest term is not un-walked (sub-dollar
    over a partial year); the dominant contribution-driven bias is removed.

    Args:
        account: Interest-bearing account.
        interest_params: InterestParams for the account.
        scenario: Baseline scenario (transaction-query scope).
        all_periods: All user pay periods.
        year: Target calendar year.

    Returns:
        Decimal estimated interest for pre-anchor year periods.
    """
    anchor_pid = account.current_anchor_period_id
    if anchor_pid is None:
        return ZERO

    anchor_period = next(
        (p for p in all_periods if p.id == anchor_pid), None,
    )
    if anchor_period is None:
        return ZERO

    year_start = date(year, 1, 1)
    if anchor_period.start_date <= year_start:
        return ZERO  # No pre-anchor gap in this year.

    # Pre-anchor periods in the target year, chronological.
    pre_anchor = sorted(
        (
            p for p in all_periods
            if p.start_date.year == year
            and p.start_date < anchor_period.start_date
        ),
        key=lambda p: p.start_date,
    )
    if not pre_anchor:
        return ZERO

    # Net settled activity per period; subtracting it walks the balance
    # backward through the pre-anchor gap.  The anchor period is included
    # so its own settled activity can be removed first.
    net_by_period = _settled_net_by_period(
        account, scenario, [p.id for p in pre_anchor] + [anchor_pid],
    )

    # The latest pre-anchor period's end balance is the anchor period's
    # start balance: the anchor balance minus the anchor period's own
    # settled activity.
    balance = (account.current_anchor_balance or ZERO) - net_by_period.get(
        anchor_pid, ZERO,
    )

    total_interest = ZERO
    for period in reversed(pre_anchor):
        total_interest += calculate_interest(
            balance=balance,
            apy=interest_params.apy,
            compounding_frequency_id=interest_params.compounding_frequency_id,
            period_start=period.start_date,
            period_end=period.end_date,
        )
        # Step back to the prior period's end balance.
        balance -= net_by_period.get(period.id, ZERO)

    return total_interest
