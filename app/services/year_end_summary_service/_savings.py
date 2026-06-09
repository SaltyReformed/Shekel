"""
Shekel Budget App -- Year-End Summary: savings progress.

Section 7: balance growth, contributions, and returns for each savings
account, dispatching to the growth engine for investment accounts and
the interest calculator for interest-bearing accounts.
"""

from decimal import Decimal

from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.services import growth_engine
from app.services.investment_projection import (
    adapt_deductions,
    build_contribution_timeline,
)
from app.services.projection_inputs import build_investment_projection_inputs
from app.services.year_end_summary_service._balances import (
    _base_account_balance_map,
    _compute_interest_for_year,
    _compute_pre_anchor_interest,
    _load_shadow_contributions,
    _sum_shadow_income,
)
from app.services.year_end_summary_service._periods import (
    _get_anchor_period_index,
    _lookup_balance_with_anchor_fallback,
    _lookup_period_balance,
)
from app.services.year_end_summary_service._types import (
    _ProjectionInputs,
    _YearContext,
)

ZERO = Decimal("0")


def _compute_savings_progress(
    savings_accounts: list,
    year_ctx: _YearContext,
    inputs: _ProjectionInputs,
) -> list[dict]:
    """Compute balance growth, contributions, and returns for savings accounts.

    Delegates each account to :func:`_savings_progress_for_account`,
    which dispatches to one of three calculation paths based on account
    type:

    - Investment accounts (with InvestmentParams): growth engine with
      employer contributions and assumed annual return.
    - Interest-bearing accounts (with InterestParams): balance
      calculator with interest accrual.
    - Plain savings accounts: standard balance calculator.

    Args:
        savings_accounts: Non-debt, non-checking accounts.
        year_ctx: The target year, baseline scenario, full period list,
            and the year's period IDs.
        inputs: Pre-loaded projection parameter maps; this section reads
            ``investment_params_map`` / ``interest_params_map`` (account
            dispatch) and the investment trio, leaving ``debt_schedules``
            untouched.

    Returns:
        List of dicts: [{account_name, account_id, jan1_balance,
        dec31_balance, total_contributions, employer_contributions,
        investment_growth}].
    """
    return [
        _savings_progress_for_account(account, year_ctx, inputs)
        for account in savings_accounts
    ]


def _savings_progress_for_account(
    account: Account,
    year_ctx: _YearContext,
    inputs: _ProjectionInputs,
) -> dict:
    """Compute the savings-progress row for one account.

    Dispatches on the account's parameter type: investment accounts use
    the growth-engine projection; interest-bearing accounts use the
    interest calculator (plus a pre-anchor estimate); plain savings
    accounts report balances with no growth.  Contributions are the
    settled shadow-income (transfer-in) total for the year regardless of
    type.

    Args:
        account: A non-debt, non-checking savings account.
        year_ctx: The target year, baseline scenario, full period list,
            and the year's period IDs.
        inputs: Pre-loaded projection parameter maps.

    Returns:
        dict with account_name, account_id, jan1_balance, dec31_balance,
        total_contributions, employer_contributions, investment_growth.
    """
    all_periods = year_ctx.all_periods
    year = year_ctx.year
    scenario = year_ctx.scenario

    contributions = _sum_shadow_income(
        account.id, year_ctx.year_period_ids, scenario.id,
    )
    inv_params = inputs.investment_params_map.get(account.id)
    int_params = inputs.interest_params_map.get(account.id)

    if inv_params:
        jan1_bal, dec31_bal, employer_total, growth_total = (
            _project_investment_for_year(
                account, inv_params, year_ctx, inputs,
            )
        )
    elif int_params:
        balances = _base_account_balance_map(account, scenario, all_periods)
        jan1_bal = _lookup_balance_with_anchor_fallback(
            balances, year, 1, all_periods, account,
        )
        dec31_bal = _lookup_balance_with_anchor_fallback(
            balances, year, 12, all_periods, account,
        )
        employer_total = ZERO
        growth_total = _compute_interest_for_year(
            account, int_params, scenario, all_periods, year,
        )
        growth_total += _compute_pre_anchor_interest(
            account, int_params, all_periods, year,
        )
    else:
        balances = _base_account_balance_map(account, scenario, all_periods)
        jan1_bal = _lookup_balance_with_anchor_fallback(
            balances, year, 1, all_periods, account,
        )
        dec31_bal = _lookup_balance_with_anchor_fallback(
            balances, year, 12, all_periods, account,
        )
        employer_total = ZERO
        growth_total = ZERO

    return {
        "account_name": account.name,
        "account_id": account.id,
        "jan1_balance": jan1_bal,
        "dec31_balance": dec31_bal,
        "total_contributions": contributions,
        "employer_contributions": employer_total,
        "investment_growth": growth_total,
    }


def _project_investment_for_year(
    account: Account,
    investment_params: InvestmentParams,
    year_ctx: _YearContext,
    inputs: _ProjectionInputs,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Project investment account balance through the target year.

    Uses the growth engine with employer contributions and assumed
    annual return.  When the account's anchor period is after January 1
    of the target year, the January balance is derived via reverse
    projection from the anchor balance -- the balance calculator does
    not compute pre-anchor periods.

    Args:
        account: The investment account.
        investment_params: InvestmentParams for the account.
        year_ctx: The target year, baseline scenario, full period list,
            and the year's period IDs.
        inputs: Pre-loaded projection parameter maps; this projection
            reads ``deductions_by_account`` and ``salary_gross_biweekly``
            (the growth engine's contribution inputs).

    Returns:
        Tuple of (jan1_balance, dec31_balance, employer_contributions,
        investment_growth).
    """
    all_periods = year_ctx.all_periods

    # Get base balance from the balance calculator (anchor + transactions).
    balances = _base_account_balance_map(
        account, year_ctx.scenario, all_periods,
    )

    # Pay periods that fall within the target year (Jan 1 - Dec 31).
    year_periods = [
        p for p in all_periods if p.start_date.year == year_ctx.year
    ]
    if not year_periods:
        return ZERO, ZERO, ZERO, ZERO

    # Adapt paycheck deductions and load the year's shadow contributions
    # (the growth engine's contribution-history feed).
    adapted_deductions = adapt_deductions(
        inputs.deductions_by_account.get(account.id, []),
    )
    acct_contributions = _load_shadow_contributions(
        account.id, year_ctx.scenario.id, year_ctx.year_period_ids,
    )

    # Compute periodic contribution and employer params.
    # F-22 / Commit 18: shared kwargs-splat helper.
    proj_inputs = build_investment_projection_inputs(
        investment_params, adapted_deductions, acct_contributions,
        all_periods, year_periods[0], inputs.salary_gross_biweekly,
    )

    jan1_bal = _derive_investment_jan1(
        account, investment_params, balances, year_ctx, proj_inputs,
    )

    # F-19: feed a per-period contribution timeline so a lump-sum
    # settled transfer (e.g. an end-of-year 401(k) contribution) lands
    # in the period it actually occurred in, not averaged across every
    # period as ``calculate_investment_inputs`` Step 2 would do.  This
    # mirrors the shape the investment dashboard route already uses.
    contributions = build_contribution_timeline(
        deductions=adapted_deductions,
        contribution_transactions=acct_contributions,
        periods=year_periods,
    )

    # Forward-project the full year from the (now correct) Jan 1 balance.
    projection = growth_engine.project_balance(
        current_balance=jan1_bal,
        assumed_annual_return=investment_params.assumed_annual_return,
        periods=year_periods,
        periodic_contribution=proj_inputs.periodic_contribution,
        employer_params=proj_inputs.employer_params,
        annual_contribution_limit=proj_inputs.annual_contribution_limit,
        ytd_contributions_start=ZERO,
        contributions=contributions,
    )

    return _summarize_investment_projection(projection, jan1_bal)


def _derive_investment_jan1(
    account: Account,
    investment_params: InvestmentParams,
    balances: dict | None,
    year_ctx: _YearContext,
    proj_inputs,
) -> Decimal:
    """Derive an investment account's January 1 balance for the year.

    When the account's anchor period is after the first pay period of the
    target year, the balance calculator has no data for the pre-anchor
    periods, so the January balance is reverse-projected from the anchor
    balance back through every intervening period.  Otherwise the balance
    map already covers January and is looked up directly.

    Args:
        account: The investment account.
        investment_params: InvestmentParams (for the assumed return).
        balances: period_id -> Decimal base-balance map, or None.
        year_ctx: The target year and full ordered period list.
        proj_inputs: The ``build_investment_projection_inputs`` result
            (periodic contribution and employer params).

    Returns:
        Decimal January 1 starting balance.
    """
    all_periods = year_ctx.all_periods
    year = year_ctx.year
    year_periods = [p for p in all_periods if p.start_date.year == year]
    if not year_periods:
        return ZERO

    first_year_idx = year_periods[0].period_index
    anchor_idx = _get_anchor_period_index(account, all_periods)

    if anchor_idx is None or anchor_idx <= first_year_idx:
        # No pre-anchor gap -- the balance map covers January.
        return _lookup_period_balance(balances, year, 1, all_periods)

    # Pre-anchor gap: reverse-project from the anchor balance across every
    # period from the start of the year through the anchor.
    anchor_pid = account.current_anchor_period_id
    anchor_bal = (
        balances.get(anchor_pid, account.current_anchor_balance or ZERO)
        if balances else account.current_anchor_balance or ZERO
    )
    reverse_periods = [
        p for p in all_periods
        if first_year_idx <= p.period_index <= anchor_idx
    ]
    # DH-#28: thread the annual contribution limit so the reverse caps each
    # period exactly as the forward path does; otherwise a maxed-out account's
    # derived Jan-1 balance is too low.  ytd_contributions_start=ZERO because
    # reverse_periods begins at the first period of the target year (its YTD is
    # zero at the year boundary), matching the forward re-projection below which
    # also seeds ytd_contributions_start=ZERO.
    reversed_proj = growth_engine.reverse_project_balance(
        anchor_balance=anchor_bal,
        assumed_annual_return=investment_params.assumed_annual_return,
        periods=reverse_periods,
        periodic_contribution=proj_inputs.periodic_contribution,
        employer_params=proj_inputs.employer_params,
        annual_contribution_limit=proj_inputs.annual_contribution_limit,
        ytd_contributions_start=ZERO,
    )
    return reversed_proj[0].start_balance if reversed_proj else ZERO


def _summarize_investment_projection(
    projection: list, jan1_bal: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Reduce a forward growth projection to the savings-progress tuple.

    Args:
        projection: List of period-balance rows from
            ``growth_engine.project_balance`` (may be empty).
        jan1_bal: The January 1 starting balance (the Dec 31 fallback
            when the projection produced no rows).

    Returns:
        Tuple of (jan1_balance, dec31_balance, employer_contributions,
        investment_growth).
    """
    if not projection:
        return jan1_bal, jan1_bal, ZERO, ZERO

    dec31_bal = projection[-1].end_balance
    employer_total = sum(
        (pb.employer_contribution for pb in projection), ZERO,
    )
    growth_total = sum((pb.growth for pb in projection), ZERO)
    return jan1_bal, dec31_bal, employer_total, growth_total
