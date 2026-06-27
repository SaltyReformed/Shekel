"""
Shekel Budget App -- Savings Dashboard: batch data loaders.

Loads the request-scoped core data (accounts, scenario, periods), the
account-type-specific parameter maps that drive the projection loop, and
the archived-account list.  No Flask imports; every function takes plain
data and returns plain data.
"""

from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.interest_params import InterestParams
from app.models.loan_features import EscrowComponent
from app.models.loan_params import LoanParams
from app.models.ref import AccountType
from app.services import income_service, pay_period_service
from app.services.projection_inputs import (
    load_active_deductions_for_accounts,
    load_investment_params_for_accounts,
)
from app.services.scenario_resolver import get_baseline_scenario
from app.services.savings_dashboard_service._types import (
    _AccountParams,
    _DashboardCoreData,
)


def _load_dashboard_core_data(user_id):
    """Load the accounts, scenario, and periods for the dashboard.

    Per-account balances are produced by the
    :mod:`app.services.balance_at` seam, which loads its own scenario-scoped
    transactions, so this loader no longer pre-fetches a transaction set.

    Args:
        user_id: Integer ID of the current user.

    Returns:
        A :class:`_DashboardCoreData` with active accounts (ordered for
        display), the baseline scenario, all pay periods, and the current
        period.
    """
    accounts = (
        db.session.query(Account)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )

    return _DashboardCoreData(
        accounts=accounts,
        scenario=get_baseline_scenario(user_id),
        all_periods=pay_period_service.get_all_periods(user_id),
        current_period=pay_period_service.get_current_period(user_id),
    )


def _load_loan_params_and_escrow(accounts):
    """Batch-load LoanParams and EscrowComponent maps for loan accounts.

    Amortizing loan types are metadata-driven via ``has_amortization``.

    Args:
        accounts: List of Account model instances.

    Returns:
        ``(loan_params_map, escrow_map)`` -- the first maps account_id
        to its :class:`LoanParams`; the second maps account_id to a
        list of :class:`EscrowComponent` (for the debt-summary PITI
        total).  Both are empty when no loan accounts exist.
    """
    amort_type_ids = {
        at.id for at in db.session.query(AccountType).filter_by(has_amortization=True).all()
    }
    loan_account_ids = [a.id for a in accounts if a.account_type_id in amort_type_ids]

    loan_params_map = {}
    escrow_map = {}
    if loan_account_ids:
        for lp in db.session.query(LoanParams).filter(
            LoanParams.account_id.in_(loan_account_ids)
        ).all():
            loan_params_map[lp.account_id] = lp

        # Escrow components for loan accounts (for debt summary PITI).
        for ec in db.session.query(EscrowComponent).filter(
            EscrowComponent.account_id.in_(loan_account_ids),
        ).all():
            escrow_map.setdefault(ec.account_id, []).append(ec)

    return loan_params_map, escrow_map


def _load_account_params(
    user_id: int, accounts: list[Account],
) -> _AccountParams:
    """Batch-load all account-type-specific parameters.

    Returns an :class:`_AccountParams` with the six account-type
    parameter maps (each keyed by ``account_id``) the projection loop
    reads.  This is the single place all six are constructed.
    """
    interest_params_map = {}
    interest_account_ids = [
        a.id for a in accounts
        if a.account_type and a.account_type.has_interest
    ]
    if interest_account_ids:
        for hp in db.session.query(InterestParams).filter(
            InterestParams.account_id.in_(interest_account_ids)
        ).all():
            interest_params_map[hp.account_id] = hp

    # Investment/retirement accounts use the growth engine.  The shared
    # loader owns the canonical-classifier filter + InvestmentParams
    # query (its single home; the balance_at seam will share it), so a
    # parameterised physical asset (Property -> APPRECIATING) is
    # correctly excluded there rather than re-derived "by elimination".
    investment_params_map = load_investment_params_for_accounts(accounts)

    # F-22 / Commit 18: shared deduction batch loader; replaces the
    # filter-shape duplicate that previously lived inline here and in
    # retirement_dashboard_service / year_end_summary_service.
    deductions_by_account = load_active_deductions_for_accounts(
        user_id, list(investment_params_map.keys()),
    ) if investment_params_map else {}

    # F-20 / MED-06 / F-032: raise-aware gross-biweekly from the
    # paycheck engine, not the off-engine
    # ``annual_salary / pay_periods_per_year`` recompute which silently
    # dropped any applicable SalaryRaise row.  ``income_service`` wraps
    # ``calculate_paycheck`` so this producer agrees with the engine
    # value the DTI denominator (and every other income-derived
    # surface) consumes downstream.
    salary_gross_biweekly = income_service.get_current_gross_biweekly(
        user_id,
    )

    loan_params_map, escrow_map = _load_loan_params_and_escrow(accounts)

    return _AccountParams(
        interest_params_map=interest_params_map,
        investment_params_map=investment_params_map,
        deductions_by_account=deductions_by_account,
        salary_gross_biweekly=salary_gross_biweekly,
        loan_params_map=loan_params_map,
        escrow_map=escrow_map,
    )


def _load_archived_accounts(user_id: int) -> list[dict]:
    """Load archived accounts with minimal data for the collapsed section.

    Archived accounts do not receive balance projections, engine calls,
    or goal calculations -- they are historical.  Each dict contains
    the Account ORM object and its last known balance.

    Args:
        user_id: Integer ID of the current user.

    Returns:
        List of dicts with keys: account, current_balance.
    """
    accounts = (
        db.session.query(Account)
        .filter_by(user_id=user_id, is_active=False)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    result = []
    for acct in accounts:
        result.append({
            "account": acct,
            "current_balance": acct.current_anchor_balance or Decimal("0.00"),
        })
    return result
