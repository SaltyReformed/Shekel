"""
Shekel Budget App -- Year-End Summary: batch data loaders.

Loads the shared per-request data (pay periods, accounts, salary
profiles) and the per-account parameter maps (investment / interest
params, payroll deductions) the section helpers project against.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload, subqueryload

from app import ref_cache
from app.enums import AcctCategoryEnum, AcctTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.models.pay_period import PayPeriod
from app.models.salary_profile import SalaryProfile
from app.models.scenario import Scenario
from app.services import income_service
from app.services.projection_inputs import load_active_deductions_for_accounts


def _load_common_data(
    user_id: int, year: int, scenario: Scenario,
) -> dict:
    """Load all shared data needed by the section helpers.

    Includes investment/interest params and paycheck deductions for
    the savings progress section's growth engine calculations.

    Args:
        user_id: The authenticated user's ID.
        year: The target calendar year.
        scenario: The user's baseline scenario.

    Returns:
        dict with year_periods, all_periods, accounts,
        salary_profiles, year_period_ids, debt_accounts,
        savings_accounts, investment_params_map,
        interest_params_map, deductions_by_account,
        salary_gross_biweekly.
    """
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)

    year_periods = (
        db.session.query(PayPeriod)
        .filter(
            PayPeriod.user_id == user_id,
            PayPeriod.start_date >= year_start,
            PayPeriod.start_date <= year_end,
        )
        .order_by(PayPeriod.period_index)
        .all()
    )

    all_periods = (
        db.session.query(PayPeriod)
        .filter(PayPeriod.user_id == user_id)
        .order_by(PayPeriod.period_index)
        .all()
    )

    accounts = (
        db.session.query(Account)
        .options(joinedload(Account.account_type))
        .filter(
            Account.user_id == user_id,
            Account.is_active.is_(True),
        )
        .all()
    )

    salary_profiles = (
        db.session.query(SalaryProfile)
        .options(
            subqueryload(SalaryProfile.raises),
            subqueryload(SalaryProfile.deductions),
        )
        .filter(
            SalaryProfile.user_id == user_id,
            SalaryProfile.scenario_id == scenario.id,
            SalaryProfile.is_active.is_(True),
        )
        .all()
    )

    liability_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
    checking_id = _get_primary_checking_id(accounts)

    savings_accounts = [
        a for a in accounts
        if a.account_type
        and not a.account_type.has_amortization
        and a.account_type.category_id != liability_cat_id
        and a.id != checking_id
    ]

    return {
        "year_periods": year_periods,
        "all_periods": all_periods,
        "accounts": accounts,
        "salary_profiles": salary_profiles,
        "year_period_ids": [p.id for p in year_periods],
        "debt_accounts": [
            a for a in accounts
            if a.account_type and a.account_type.has_amortization
        ],
        "savings_accounts": savings_accounts,
        "investment_params_map": _load_investment_params(savings_accounts),
        "interest_params_map": _load_interest_params(savings_accounts),
        "deductions_by_account": _load_deductions_by_account(
            savings_accounts, user_id,
        ),
        "salary_gross_biweekly": _load_salary_gross_biweekly(
            user_id, scenario,
        ),
    }


def _load_investment_params(
    accounts: list,
) -> dict[int, InvestmentParams]:
    """Batch-load InvestmentParams for investment/retirement accounts.

    Filters to accounts whose account_type has has_parameters=True and
    does not have has_interest or has_amortization (i.e., investment
    and retirement accounts that use the growth engine).

    Args:
        accounts: List of Account objects with loaded account_type.

    Returns:
        dict mapping account_id to InvestmentParams.
    """
    inv_ids = [
        a.id for a in accounts
        if a.account_type
        and getattr(a.account_type, "has_parameters", False)
        and not a.account_type.has_interest
        and not a.account_type.has_amortization
    ]
    if not inv_ids:
        return {}

    params_list = (
        db.session.query(InvestmentParams)
        .filter(InvestmentParams.account_id.in_(inv_ids))
        .all()
    )
    return {p.account_id: p for p in params_list}


def _load_interest_params(
    accounts: list,
) -> dict[int, InterestParams]:
    """Batch-load InterestParams for interest-bearing accounts.

    Filters to accounts whose account_type has has_interest=True
    (HYSA, Money Market, CD, HSA).

    Args:
        accounts: List of Account objects with loaded account_type.

    Returns:
        dict mapping account_id to InterestParams.
    """
    interest_ids = [
        a.id for a in accounts
        if a.account_type and a.account_type.has_interest
    ]
    if not interest_ids:
        return {}

    params_list = (
        db.session.query(InterestParams)
        .filter(InterestParams.account_id.in_(interest_ids))
        .all()
    )
    return {p.account_id: p for p in params_list}


def _load_deductions_by_account(
    accounts: list,
    user_id: int,
) -> dict[int, list]:
    """Load paycheck deductions targeting investment accounts.

    Returns deductions grouped by target_account_id.  Each deduction
    has the SalaryProfile eagerly loaded for access to annual_salary
    and pay_periods_per_year.

    F-22 / Commit 18: delegates to the shared
    :func:`load_active_deductions_for_accounts` so the filter shape
    is defined once across the four consumer services.  The
    investment-account selection (parameters but neither interest nor
    amortization) stays here because it is the year-end aggregation's
    business rule, not a property of the deduction query.

    Args:
        accounts: List of Account objects.
        user_id: User ID for SalaryProfile ownership.

    Returns:
        dict mapping account_id to list of PaycheckDeduction.
    """
    inv_ids = [
        a.id for a in accounts
        if a.account_type
        and getattr(a.account_type, "has_parameters", False)
        and not a.account_type.has_interest
        and not a.account_type.has_amortization
    ]
    return load_active_deductions_for_accounts(user_id, inv_ids)


def _load_salary_gross_biweekly(
    user_id: int,
    scenario: Scenario,
) -> Decimal:
    """Load the user's gross biweekly pay from their active salary profile.

    Thin delegator over :func:`income_service.get_current_gross_biweekly`
    so the year-end summary's salary-derived inputs (employer-match
    cap basis, investment-projection contribution feed) agree with the
    paycheck engine.  Pre-Commit-17 this read
    ``profile.annual_salary / pay_periods_per_year`` directly, which
    silently dropped any applicable ``SalaryRaise`` row -- the audit's
    F-20 / MED-06 / F-032 defect.

    Returns ``Decimal("0")`` if no active salary profile exists in the
    given scenario or no pay period covers today.

    Args:
        user_id: User ID.
        scenario: Baseline scenario.  Year-end aggregates within one
            scenario, so the profile filter scopes by
            ``scenario.id`` -- ``income_service`` accepts the optional
            ``scenario_id`` keyword for this.

    Returns:
        Decimal gross biweekly pay.
    """
    return income_service.get_current_gross_biweekly(
        user_id, scenario_id=scenario.id,
    )


def _get_primary_checking_id(accounts: list) -> int | None:
    """Return the ID of the first checking account, or None.

    Used to exclude the primary checking account from the savings
    progress section (it is not a savings vehicle).
    """
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    for a in accounts:
        if a.account_type_id == checking_type_id:
            return a.id
    return None
