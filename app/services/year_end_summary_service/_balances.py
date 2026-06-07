"""
Shekel Budget App -- Year-End Summary: per-account balance projection.

Dispatches each account to the correct engine (amortization schedule,
interest calculator, growth engine, or plain balance resolver) and
builds the period-keyed balance maps the net-worth and savings-progress
sections consume, plus the shared amortization-schedule generation.
"""

from collections import OrderedDict
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_params import LoanParams
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.services import (
    balance_calculator,
    balance_resolver,
    growth_engine,
    loan_resolver,
)
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
    compute_loan_period_balance_map,
)
from app.services.interest_projection import calculate_interest
from app.services.investment_projection import adapt_deductions
from app.services.loan_payment_service import (
    load_loan_context,
    query_shadow_income,
)
from app.services.projection_inputs import build_investment_projection_inputs
from app.services.year_end_summary_service._periods import (
    _get_anchor_period_index,
)
from app.services.year_end_summary_service._types import _ProjectionInputs

ZERO = Decimal("0")


def _generate_debt_schedules(
    debt_accounts: list,
    scenario_id: int,
) -> dict[int, list]:
    """Generate amortization schedules for all debt accounts.

    Runs the loan resolver (E-18 / Commit 13) for each debt account
    and returns its :class:`AmortizationRow` schedule.  Same schedule
    the loan dashboard and /savings debt card consume, so mortgage
    interest, debt progress, and net worth liability all derive
    from the single resolver output (E-18 / Commit 15).

    Args:
        debt_accounts: Accounts with has_amortization=True.
        scenario_id: Baseline scenario ID for payment history.

    Returns:
        dict mapping account_id to list[AmortizationRow].
    """
    schedules: dict[int, list] = {}
    today = date.today()

    for account in debt_accounts:
        params = (
            db.session.query(LoanParams)
            .filter_by(account_id=account.id)
            .first()
        )
        if params is None:
            continue

        ctx = load_loan_context(account.id, scenario_id, params)
        anchor_events = (
            db.session.query(LoanAnchorEvent)
            .filter_by(account_id=account.id)
            .all()
        )
        state = loan_resolver.resolve_loan(
            loan_resolver.LoanInputs(
                params, anchor_events, ctx.payments, ctx.rate_changes,
            ),
            today,
        )
        schedules[account.id] = state.schedule

    return schedules


def _balance_from_schedule_at_date(
    schedule: list,
    target: date,
    original_principal: Decimal,
) -> Decimal:
    """Return the loan balance at a given date from an amortization schedule.

    Finds the last schedule row whose payment_date is on or before
    the target date and returns its remaining_balance.  If the target
    is before the first payment, returns the original principal.

    Args:
        schedule: List of AmortizationRow produced by
            ``replay_schedule`` + ``project_forward`` (or any
            chronologically ordered schedule the engine emits).
        target: The date to look up the balance for.
        original_principal: The loan's original principal (balance
            before any payments).

    Returns:
        Decimal remaining balance at the target date.
    """
    if not schedule:
        return original_principal

    best_balance = original_principal
    for row in schedule:
        if row.payment_date <= target:
            best_balance = row.remaining_balance
        else:
            # Schedule is chronological; no need to check further.
            break

    return best_balance


def _loan_original_principal(account_id: int) -> Decimal:
    """Return a loan account's original principal, or ZERO if unset.

    The original principal is the schedule's pre-payment balance, used as
    the balance-before-first-payment fallback by both the net-worth
    liability column (:func:`_get_account_balance_map`) and the debt
    progress section (``_compute_debt_progress``).

    Args:
        account_id: The loan account's ID.

    Returns:
        Decimal original principal, or ZERO when the account has no
        :class:`LoanParams` row.
    """
    params = (
        db.session.query(LoanParams)
        .filter_by(account_id=account_id)
        .first()
    )
    return params.original_principal if params else ZERO


def _base_account_balance_map(
    account: Account,
    scenario: Scenario,
    periods: list,
) -> dict | None:
    """Compute period_id -> balance for one account WITHOUT dispatch inputs.

    The base path used by the savings-progress section and by
    :func:`_dispatch_account_balance_map`'s fall-through: interest-bearing
    accounts (HYSA, Money Market, CD, HSA) use the balance calculator with
    interest accrual; everything else routes through the canonical
    entries-aware resolver.  It deliberately takes no amortization-schedule
    or growth-engine inputs -- callers that drive those use
    :func:`_dispatch_account_balance_map`.

    Args:
        account: The account to project.
        scenario: The baseline scenario.
        periods: All user pay periods.

    Returns:
        OrderedDict mapping period_id to Decimal balance, or None if the
        account has no anchor period.
    """
    if account.current_anchor_period_id is None:
        return None

    kind = classify_account(account)

    # Interest-bearing accounts (HYSA, Money Market, CD, HSA).  The
    # math-layer silent-degrade seam in
    # ``balance_calculator._entry_aware_amount`` was closed in Commit 5
    # (entries lazy-load via the SQLAlchemy descriptor instead of
    # short-circuiting to ``effective_amount``), so the entries-aware
    # reduction applies here even without ``selectinload``.
    if (kind is AccountProjectionKind.INTEREST
            and hasattr(account, "interest_params")
            and account.interest_params):
        period_ids = [p.id for p in periods]
        transactions = (
            db.session.query(Transaction)
            .filter(
                Transaction.account_id == account.id,
                Transaction.scenario_id == scenario.id,
                Transaction.pay_period_id.in_(period_ids),
                Transaction.is_deleted.is_(False),
            )
            .all()
        )
        anchor_balance = account.current_anchor_balance or ZERO
        balances, _ = balance_calculator.calculate_balances_with_interest(
            anchor_balance=anchor_balance,
            anchor_period_id=account.current_anchor_period_id,
            periods=periods,
            transactions=transactions,
            interest_params=account.interest_params,
        )
        return balances

    # Standard checking/savings (and any unmatched types) route through
    # the canonical entries-aware producer (E-25 / CRIT-01 / F-009 /
    # R-1: Commit 8).  ``balances_for`` owns the transaction query with
    # ``selectinload(Transaction.entries)`` and resolves the anchor via
    # the dated ``AccountAnchorHistory`` SoT, so the net-worth aggregate
    # cannot disagree with the grid for the same input.
    return balance_resolver.balances_for(
        account, scenario.id, periods,
    ).balances


def _dispatch_account_balance_map(
    account: Account,
    scenario: Scenario,
    periods: list,
    inputs: _ProjectionInputs,
) -> dict | None:
    """Compute period_id -> balance for one account, dispatching on type.

    The net-worth path.  Dispatches to the correct calculation engine:
    - Amortizing loans: pre-generated amortization schedule
      (``inputs.debt_schedules``).
    - Investment (401k, IRA, etc.): growth engine with employer and
      returns (``inputs.investment_params_map`` + the contribution trio).
    - Interest-bearing and everything else: the shared
      :func:`_base_account_balance_map`.

    Unlike the base path this always has the pre-loaded ``inputs`` (the
    net-worth caller builds them); the absence of a bundle is no longer
    overloaded to mean "base-balance mode" -- that is the separate, named
    :func:`_base_account_balance_map` now.

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
    if account.current_anchor_period_id is None:
        return None

    # MED-01 / S6-03: single flag-driven classifier replaces the
    # divergent branch ladders that used to express the same taxonomy
    # two different ways here and in
    # ``savings_dashboard_service._compute_account_projections``.
    kind = classify_account(account)

    # Amortizing loan accounts: use pre-generated schedule when available.
    if (kind is AccountProjectionKind.AMORTIZING
            and inputs.debt_schedules and account.id in inputs.debt_schedules):
        original = _loan_original_principal(account.id)
        # F-21 / Commit 19: route through the shared
        # ``compute_loan_period_balance_map`` so the year-end liability
        # column and the savings-dashboard loan card consume the same
        # period-end-keyed balance derivation.
        return compute_loan_period_balance_map(
            inputs.debt_schedules[account.id], periods, original,
        )

    # Investment accounts: use the growth engine.  The base balance
    # feeding the projection comes from the canonical entries-aware
    # producer (E-25 / CRIT-01 / R-1).
    if kind is AccountProjectionKind.INVESTMENT:
        inv_params = inputs.investment_params_map.get(account.id)
        if inv_params:
            return _build_investment_balance_map(
                account, inv_params, scenario, periods, inputs,
            )

    # Interest-bearing and plain accounts share the base path.
    return _base_account_balance_map(account, scenario, periods)


def _build_investment_balance_map(
    account: Account,
    investment_params: InvestmentParams,
    scenario: Scenario,
    periods: list,
    inputs: _ProjectionInputs,
) -> OrderedDict:
    """Build period_id -> balance map using the growth engine.

    Produces balances for all periods by combining three sources:

    - **Pre-anchor periods**: reverse growth engine projection backward
      from the anchor balance.
    - **Anchor period**: canonical entries-aware producer (anchor +
      remaining transactions).
    - **Post-anchor periods**: forward growth engine projection from
      the anchor balance.

    Args:
        account: Investment account.
        investment_params: InvestmentParams for the account.
        scenario: Baseline scenario.
        periods: All user pay periods.
        inputs: Pre-loaded projection parameter maps; this builder reads
            ``deductions_by_account`` (the contribution feed) and
            ``salary_gross_biweekly`` (the employer-match cap basis) via
            :func:`build_investment_projection_inputs`.

    Returns:
        OrderedDict mapping period_id to Decimal balance.
    """
    # Base balances from the canonical entries-aware producer (E-25 /
    # CRIT-01 / F-009 / R-1: Commit 8).  ``balances_for`` owns the
    # transaction query with ``selectinload(Transaction.entries)``,
    # resolves the anchor via the dated ``AccountAnchorHistory`` SoT,
    # and routes through the same engine math as the grid -- so the
    # base balance feeding the growth projection here is identical to
    # the figure rendered on the grid and other surfaces.
    base_balances = balance_resolver.balances_for(
        account, scenario.id, periods,
    ).balances

    # Find the anchor period's index to split pre/post-anchor.
    anchor_idx = _get_anchor_period_index(account, periods)
    if anchor_idx is None:
        return base_balances

    pre_anchor = [p for p in periods if p.period_index < anchor_idx]
    post_anchor = [p for p in periods if p.period_index > anchor_idx]
    if not pre_anchor and not post_anchor:
        return base_balances

    # Adapt paycheck deductions, load the post-anchor shadow-income
    # contribution feed, and compute the growth-engine projection inputs.
    # F-22 / Commit 18: shared kwargs-splat helper.
    acct_contributions = _load_shadow_contributions(
        account.id, scenario.id, [p.id for p in post_anchor],
    )
    proj_inputs = build_investment_projection_inputs(
        investment_params,
        adapt_deductions(inputs.deductions_by_account.get(account.id, [])),
        acct_contributions, periods,
        post_anchor[0] if post_anchor else pre_anchor[-1],
        inputs.salary_gross_biweekly,
    )

    anchor_balance = base_balances.get(
        account.current_anchor_period_id, ZERO,
    )
    anchor_period = next(
        (p for p in periods if p.id == account.current_anchor_period_id),
        None,
    )

    proj_by_pid = _forward_project_periods(
        post_anchor, anchor_balance, investment_params, proj_inputs,
    )
    rev_by_pid = _reverse_project_periods(
        pre_anchor, anchor_period, anchor_balance,
        investment_params, proj_inputs,
    )
    return _merge_balance_sources(
        periods, proj_by_pid, base_balances, rev_by_pid,
    )


def _forward_project_periods(
    post_anchor: list,
    anchor_balance: Decimal,
    investment_params: InvestmentParams,
    proj_inputs,
) -> dict:
    """Forward-project post-anchor period-end balances via the growth engine.

    Args:
        post_anchor: Periods after the anchor (chronological).
        anchor_balance: Balance at the end of the anchor period.
        investment_params: InvestmentParams (for the assumed return).
        proj_inputs: ``build_investment_projection_inputs`` result.

    Returns:
        dict mapping period_id to projected end balance, or ``{}`` when
        there are no post-anchor periods.
    """
    if not post_anchor:
        return {}

    projection = growth_engine.project_balance(
        current_balance=anchor_balance,
        assumed_annual_return=investment_params.assumed_annual_return,
        periods=post_anchor,
        periodic_contribution=proj_inputs.periodic_contribution,
        employer_params=proj_inputs.employer_params,
        annual_contribution_limit=proj_inputs.annual_contribution_limit,
        ytd_contributions_start=proj_inputs.ytd_contributions,
    )
    return {pb.period_id: pb.end_balance for pb in projection}


def _reverse_project_periods(
    pre_anchor: list,
    anchor_period,
    anchor_balance: Decimal,
    investment_params: InvestmentParams,
    proj_inputs,
) -> dict:
    """Reverse-project pre-anchor period-end balances via the growth engine.

    The anchor period is appended to the reverse list so
    ``reverse_project_balance`` has the correct endpoint (the anchor
    balance is the end-of-anchor-period value); the anchor's own entry is
    then dropped from the result so the base-balance map keeps ownership
    of it.

    Args:
        pre_anchor: Periods before the anchor (chronological).
        anchor_period: The anchor PayPeriod (the reverse endpoint), or
            None if it could not be resolved.
        anchor_balance: Balance at the end of the anchor period.
        investment_params: InvestmentParams (for the assumed return).
        proj_inputs: ``build_investment_projection_inputs`` result.

    Returns:
        dict mapping period_id to projected end balance, or ``{}`` when
        there are no pre-anchor periods.
    """
    if not pre_anchor or anchor_period is None:
        return {}

    reversed_proj = growth_engine.reverse_project_balance(
        anchor_balance=anchor_balance,
        assumed_annual_return=investment_params.assumed_annual_return,
        periods=pre_anchor + [anchor_period],
        periodic_contribution=proj_inputs.periodic_contribution,
        employer_params=proj_inputs.employer_params,
    )
    return {
        pb.period_id: pb.end_balance
        for pb in reversed_proj
        if pb.period_id != anchor_period.id
    }


def _merge_balance_sources(
    periods: list,
    proj_by_pid: dict,
    base_balances: dict,
    rev_by_pid: dict,
) -> OrderedDict:
    """Merge the three balance sources into one period-ordered map.

    For each period, prefers the forward projection, then the canonical
    base balance, then the reverse projection.  Periods absent from all
    three sources are omitted.

    Args:
        periods: All user pay periods (defines output order).
        proj_by_pid: Forward post-anchor balances by period_id.
        base_balances: Canonical anchor/base balances by period_id.
        rev_by_pid: Reverse pre-anchor balances by period_id.

    Returns:
        OrderedDict mapping period_id to Decimal balance.
    """
    result = OrderedDict()
    for period in periods:
        if period.id in proj_by_pid:
            result[period.id] = proj_by_pid[period.id]
        elif period.id in base_balances:
            result[period.id] = base_balances[period.id]
        elif period.id in rev_by_pid:
            result[period.id] = rev_by_pid[period.id]
    return result


def _load_shadow_contributions(
    account_id: int,
    scenario_id: int,
    period_ids: list[int],
) -> list:
    """Load settled shadow-income (transfer-in) transactions for an account.

    The contribution-history feed for the growth engine, shared by the
    savings-progress projection (:func:`_project_investment_for_year`)
    and the net-worth investment balance map
    (:func:`_build_investment_balance_map`).  ``status`` and
    ``pay_period`` are eager-loaded so the downstream consumer
    (``investment_projection.calculate_investment_inputs`` /
    ``build_contribution_timeline``) reads ``txn.status.*`` /
    ``txn.pay_period`` without an N+1.  The status filter routes through
    ``balance_excluded_status_ids`` (D6-09 / MED-02), which lets the
    query drop the ``Status`` INNER JOIN while the ``joinedload`` keeps
    the attribute available; the audit-trigger row count is unchanged.

    Args:
        account_id: Target account ID.
        scenario_id: Baseline scenario ID.
        period_ids: Pay period IDs whose shadow income forms the
            contribution history.

    Returns:
        List of shadow-income Transaction objects, or ``[]`` when
        ``period_ids`` is empty.
    """
    if not period_ids:
        return []

    # Shadow-income definition + status/pay_period eager-loads come from
    # the shared ``query_shadow_income`` builder (the R0801 sibling is
    # ``loan_payment_service.get_payment_history``, NOT ``budget_variance``
    # as a prior rationale wrongly claimed); the year-end feed scopes it to
    # this year's periods.
    return (
        query_shadow_income(account_id, scenario_id)
        .filter(Transaction.pay_period_id.in_(period_ids))
        .all()
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

    period_ids = [p.id for p in all_periods]
    transactions = (
        db.session.query(Transaction)
        .filter(
            Transaction.account_id == account.id,
            Transaction.scenario_id == scenario.id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
        )
        .all()
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


def _compute_pre_anchor_interest(
    account: Account,
    interest_params: InterestParams,
    all_periods: list,
    year: int,
) -> Decimal:
    """Estimate interest earned in pre-anchor periods of the target year.

    When the anchor falls after January 1 of the target year,
    calculate_balances_with_interest does not compute interest for
    pre-anchor periods.  This function fills that gap using the
    anchor balance as an approximation of the account balance during
    those periods.

    This slightly overstates interest (the actual balance was lower
    before contributions), but is a reasonable approximation for
    display purposes.

    Args:
        account: Interest-bearing account.
        interest_params: InterestParams for the account.
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

    # Pre-anchor periods in the target year.
    pre_anchor = [
        p for p in all_periods
        if p.start_date.year == year
        and p.start_date < anchor_period.start_date
    ]

    balance = account.current_anchor_balance or ZERO
    total_interest = ZERO
    for period in pre_anchor:
        interest = calculate_interest(
            balance=balance,
            apy=interest_params.apy,
            compounding_frequency=interest_params.compounding_frequency,
            period_start=period.start_date,
            period_end=period.end_date,
        )
        total_interest += interest

    return total_interest
