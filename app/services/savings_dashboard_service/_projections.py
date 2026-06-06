"""
Shekel Budget App -- Savings Dashboard: per-account balance projections.

Dispatches each account to the appropriate projection engine -- the loan
resolver for loans, the growth engine for investments, and the canonical
balance calculator for everything else -- and assembles the per-account
dict the dashboard template renders.  No Flask imports.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.loan_anchor_event import LoanAnchorEvent
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
    find_period_containing_date,
)
from app.services.investment_projection import adapt_deductions
from app.services.loan_payment_service import load_loan_context
from app.services.projection_inputs import build_investment_projection_inputs
from app.services.savings_dashboard_service._types import _LoanAccountResult
from app.utils.period_projections import project_balance_horizons


def _compute_base_balances(acct, kind, acct_interest_params, ctx):
    """Compute the base per-period balance map and current balance.

    Interest-bearing accounts (with a params row) layer interest on top
    of the base calculation; every other account routes through the
    canonical entries-aware resolver.  Loan and investment accounts
    still get a balances map here, but their ``current_balance`` is
    overridden later from the amortization / growth projection.

    Args:
        acct: The Account instance.
        kind: The account's :class:`AccountProjectionKind`.
        acct_interest_params: The account's InterestParams row, or None.
        ctx: The shared :class:`_ProjectionContext`.

    Returns:
        ``(balances, current_bal)`` -- the period-id-keyed balance map
        and the current-period balance (falling back to the anchor
        balance when there is no current period).
    """
    anchor_balance = acct.current_anchor_balance or Decimal("0.00")
    anchor_period_id = acct.current_anchor_period_id or (
        ctx.current_period.id if ctx.current_period else None
    )
    scenario_id = ctx.scenario_id

    balances = {}
    # MED-01 / S6-03: one flag-driven classifier shared with the
    # year-end summary's ``_get_account_balance_map``.  The
    # ``interest_params`` row presence still guards the interest path so
    # an account that flags ``has_interest=True`` but has no params row
    # falls through to the canonical resolver (the pre-Commit-28 behavior
    # the param-map-presence check delivered as a happy accident; the
    # classifier preserves it explicitly).
    if kind is AccountProjectionKind.INTEREST and acct_interest_params:
        # HYSA / interest-bearing path.  Continues to layer interest on
        # top of the base balance calculation.  Entry-aware reduction
        # for any envelope expenses on this account is applied
        # unconditionally by ``_entry_aware_amount`` post-Commit-5 (the
        # seam was removed at the math layer: an unloaded ``entries``
        # relationship now lazy-loads via the SQLAlchemy descriptor
        # rather than silently degrading to ``effective_amount``).
        if anchor_period_id:
            acct_transactions = [
                txn for txn in ctx.all_transactions
                if txn.account_id == acct.id
            ]
            balances, _ = balance_calculator.calculate_balances_with_interest(
                anchor_balance=anchor_balance,
                anchor_period_id=anchor_period_id,
                periods=ctx.all_periods,
                transactions=acct_transactions,
                interest_params=acct_interest_params,
            )
    elif scenario_id is not None:
        # Non-interest checking / savings / loan / investment accounts
        # route through the canonical entries-aware producer (CRIT-01 /
        # F-009 / E-25).  ``balances_for`` owns its own transaction query
        # with ``selectinload(Transaction.entries)`` and resolves the
        # anchor via the dated ``AccountAnchorHistory`` source of truth,
        # so the per-tile checking balance no longer silently disagrees
        # with the grid (symptom #1: $160 on grid vs $114.29 here
        # pre-fix, because /savings did not eager-load entries and
        # ``_entry_aware_amount`` returned ``effective_amount``
        # unchanged).  Loan and investment accounts still compute a
        # ``balances`` map here, but ``current_bal`` is overridden below
        # from the amortization / growth projection; the resolver call
        # is cheap and uniform.
        result = balance_resolver.balances_for(
            acct, scenario_id, ctx.all_periods,
        )
        balances = result.balances

    current_bal = (
        balances.get(ctx.current_period.id)
        if ctx.current_period else anchor_balance
    )
    return balances, current_bal


def _loan_projected_horizons(schedule, all_periods, original_principal, today):
    """Project a loan's balance at the 3 / 6 / 12-month horizons.

    Routes through the shared ``compute_loan_period_balance_map`` (F-21 /
    Commit 19) so the dashboard's projected balances agree to the cent
    with the year-end net-worth liability and debt-progress sections
    (both consumers read the same period-end-keyed map).  Pre-F-21 this
    site ran a parallel target-month-first walk over the schedule that
    answered a slightly different question and produced cents-precise
    drift across the two surfaces; see ``F-21`` in
    ``docs/audits/financial_calculations/remediation_follow_up.md`` and
    ``account_projection.compute_loan_period_balance_map`` for the
    locked semantic.

    Args:
        schedule: The resolver's amortization schedule.
        all_periods: All pay periods for the user.
        original_principal: The loan's original principal (keys the map).
        today: The reference date the horizon offsets advance from.

    Returns:
        Dict mapping a horizon label ("3 months" / "6 months" /
        "1 year") to the projected period-end balance, omitting horizons
        with no matching period.
    """
    balance_map = compute_loan_period_balance_map(
        schedule, all_periods, original_principal,
    )
    projected = {}
    for label, month_offset in [
        ("3 months", 3), ("6 months", 6), ("1 year", 12),
    ]:
        target_m = today.month + month_offset
        target_y = today.year + (target_m - 1) // 12
        target_m = (target_m - 1) % 12 + 1
        target_dt = date(target_y, target_m, 1)
        target_period = find_period_containing_date(all_periods, target_dt)
        if target_period is not None and target_period.id in balance_map:
            projected[label] = balance_map[target_period.id]
    return projected


def _loan_ever_paid_off(acct_loan_params, anchor_events, loan_ctx):
    """Return whether confirmed payments have EVER retired this loan.

    Distinct from "balance is zero as of today": the per-tile current
    balance correctly excludes settled payments dated in the future,
    whereas this flag asks "have my confirmed payments ever retired this
    loan?", regardless of when those payments are dated.  A resolver call
    with ``as_of=date.max`` replays every confirmed payment forward and
    answers that directly.  Requires at least one confirmed payment so a
    brand-new loan with a zero anchor balance (degenerate input) does not
    render as "paid off" -- preserves the historical ``_check_loan_paid_off``
    semantic.

    Args:
        acct_loan_params: The account's LoanParams.
        anchor_events: The account's LoanAnchorEvent rows.
        loan_ctx: The loaded loan context (payments + rate changes).

    Returns:
        True when confirmed payments have ever retired the loan.
    """
    has_confirmed = any(p.is_confirmed for p in loan_ctx.payments)
    if not has_confirmed:
        return False
    ever_state = loan_resolver.resolve_loan(
        loan_resolver.LoanInputs(
            acct_loan_params, anchor_events,
            loan_ctx.payments, loan_ctx.rate_changes,
        ),
        date.max,
    )
    return ever_state.current_balance == Decimal("0.00")


def _compute_loan_account(acct, acct_loan_params, scenario_id, all_periods):
    """Resolve current balance, payment, payoff, and projection for a loan.

    Loads the loan context (payments + escrow + rate changes) and runs
    the loan resolver (E-18 / Commit 13), which is the source of truth
    for current_balance, monthly_payment, schedule, and payoff_date --
    the same dollar figures rendered on the loan card and the year-end
    net-worth liability.  The resolver-derived ``current_balance``
    replaces the stored ``LoanParams.current_principal`` read that pre-
    E-18 produced the F-008 stored-vs-engine divergence on this tile.

    Args:
        acct: The loan Account instance.
        acct_loan_params: The account's LoanParams.
        scenario_id: The baseline scenario id (or None).
        all_periods: All pay periods for the user.

    Returns:
        A :class:`_LoanAccountResult` with the resolver-derived figures.
    """
    loan_ctx = load_loan_context(acct.id, scenario_id, acct_loan_params)
    anchor_events = (
        db.session.query(LoanAnchorEvent)
        .filter_by(account_id=acct.id)
        .all()
    )
    today = date.today()
    state = loan_resolver.resolve_loan(
        loan_resolver.LoanInputs(
            acct_loan_params, anchor_events,
            loan_ctx.payments, loan_ctx.rate_changes,
        ),
        today,
    )
    projected = _loan_projected_horizons(
        state.schedule, all_periods,
        acct_loan_params.original_principal, today,
    )
    return _LoanAccountResult(
        current_balance=state.current_balance,
        monthly_payment=state.monthly_payment,
        payoff_date=state.payoff_date,
        projected=projected,
        is_paid_off=_loan_ever_paid_off(
            acct_loan_params, anchor_events, loan_ctx,
        ),
    )


def _compute_needs_setup(
    acct, kind, acct_interest_params, acct_loan_params, acct_investment_params,
):
    """Return whether a parameterized account still needs its params row.

    MED-01 / S6-03: consults the same flag-driven classifier the
    projection dispatcher uses, so the "needs setup" predicate and the
    projection path agree on one account-type taxonomy.

    Args:
        acct: The Account instance.
        kind: The account's :class:`AccountProjectionKind`.
        acct_interest_params: The InterestParams row, or None.
        acct_loan_params: The LoanParams row, or None.
        acct_investment_params: The InvestmentParams row, or None.

    Returns:
        True when the account flags ``has_parameters`` but its
        type-specific params row is missing.
    """
    if not (acct.account_type and acct.account_type.has_parameters):
        return False
    if kind is AccountProjectionKind.INTEREST:
        return acct_interest_params is None
    if kind is AccountProjectionKind.AMORTIZING:
        return acct_loan_params is None
    if kind is AccountProjectionKind.INVESTMENT:
        return acct_investment_params is None
    return False


def _investment_horizons(projection, all_periods, current_period):
    """Map a growth projection to the 3 / 6 / 12-month horizon balances.

    Args:
        projection: The growth engine's per-period projection.
        all_periods: All pay periods for the user.
        current_period: The current :class:`PayPeriod`.

    Returns:
        Dict mapping a horizon label to the projected end balance,
        omitting horizons that fall outside the projection.
    """
    proj_by_idx = {
        p.period_index: pb.end_balance
        for pb in projection
        for p in all_periods
        if p.id == pb.period_id
    }
    projected = {}
    for offset_label, offset_count in [
        ("3 months", 6), ("6 months", 13), ("1 year", 26),
    ]:
        target_idx = current_period.period_index + offset_count
        if target_idx in proj_by_idx:
            projected[offset_label] = proj_by_idx[target_idx]
    return projected


def _project_investment(acct, investment_params, current_bal, ctx):
    """Compute growth projections for an investment/retirement account."""
    acct_deductions = ctx.params.deductions_by_account.get(acct.id, [])
    adapted_deductions = adapt_deductions(acct_deductions)
    acct_contributions = [
        t for t in ctx.all_shadow_income
        if t.account_id == acct.id
    ]

    inputs = build_investment_projection_inputs(
        investment_params, adapted_deductions, acct_contributions,
        ctx.all_periods, ctx.current_period,
        ctx.params.salary_gross_biweekly,
    )

    future_periods = [
        p for p in ctx.all_periods
        if p.period_index >= ctx.current_period.period_index
    ]
    if not future_periods:
        return {}

    projection = growth_engine.project_balance(
        current_balance=current_bal,
        assumed_annual_return=investment_params.assumed_annual_return,
        periods=future_periods,
        periodic_contribution=inputs.periodic_contribution,
        employer_params=inputs.employer_params,
        annual_contribution_limit=inputs.annual_contribution_limit,
        ytd_contributions_start=inputs.ytd_contributions,
    )
    return _investment_horizons(
        projection, ctx.all_periods, ctx.current_period,
    )


def _project_one_account(acct, ctx):
    """Compute the projection dict for a single account.

    Dispatches to the appropriate projection engine based on account
    type: the loan resolver for loans, the growth engine for
    investments, and the canonical balance calculator for everything
    else.

    Args:
        acct: The Account instance.
        ctx: The shared :class:`_ProjectionContext`.

    Returns:
        A dict with keys: account, current_balance, projected,
        needs_setup, is_paid_off, plus optional type-specific params
        (interest_params / investment_params / loan_params +
        monthly_payment + payoff_date).
    """
    kind = classify_account(acct)
    acct_interest_params = ctx.params.interest_params_map.get(acct.id)
    acct_loan_params = ctx.params.loan_params_map.get(acct.id)
    acct_investment_params = ctx.params.investment_params_map.get(acct.id)

    balances, current_bal = _compute_base_balances(
        acct, kind, acct_interest_params, ctx,
    )

    loan_result = None
    if acct_loan_params:
        loan_result = _compute_loan_account(
            acct, acct_loan_params, ctx.scenario_id,
            ctx.all_periods,
        )
        current_bal = loan_result.current_balance
        projected = loan_result.projected
    elif acct_investment_params and ctx.current_period:
        projected = _project_investment(
            acct, acct_investment_params, current_bal, ctx,
        )
    else:
        projected = project_balance_horizons(
            ctx.current_period, ctx.all_periods, balances,
        )

    needs_setup = _compute_needs_setup(
        acct, kind, acct_interest_params, acct_loan_params,
        acct_investment_params,
    )

    ad = {
        "account": acct,
        "current_balance": current_bal,
        "projected": projected,
        "needs_setup": needs_setup,
        "is_paid_off": loan_result.is_paid_off if loan_result else False,
    }
    if acct_interest_params:
        ad["interest_params"] = acct_interest_params
    if acct_investment_params:
        ad["investment_params"] = acct_investment_params
    if acct_loan_params:
        ad["loan_params"] = acct_loan_params
        ad["monthly_payment"] = loan_result.monthly_payment
        ad["payoff_date"] = loan_result.payoff_date
    return ad


def _compute_account_projections(accounts, ctx):
    """Compute balance projections for each account.

    Args:
        accounts: List of Account model instances.
        ctx: The shared :class:`_ProjectionContext` bundling the
            pre-loaded transactions, periods, current period, and
            type-specific parameter maps.

    Returns:
        A list of per-account dicts (see :func:`_project_one_account`).
    """
    return [_project_one_account(acct, ctx) for acct in accounts]
