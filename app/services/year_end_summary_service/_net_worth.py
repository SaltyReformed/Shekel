"""
Shekel Budget App -- Year-End Summary: net worth and debt progress.

Section 5 (net worth at 12 monthly endpoints) and Section 6 (principal
paid per debt account during the year).
"""

import calendar
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.models.scenario import Scenario
from app.services.year_end_summary_service._balances import (
    _balance_from_schedule_at_date,
    _get_account_balance_map,
    _loan_original_principal,
)
from app.services.year_end_summary_service._periods import (
    _find_period_before_date,
    _get_month_end_periods,
)
from app.services.year_end_summary_service._types import (
    _ProjectionInputs,
    _YearContext,
)

ZERO = Decimal("0")


def _compute_net_worth(
    accounts: list,
    year_ctx: _YearContext,
    inputs: _ProjectionInputs,
) -> dict:
    """Compute net worth at 12 monthly endpoints for the year.

    For each month, finds the last pay period ending in or before the
    month's last day, then sums all account balances at that period.
    Liability accounts contribute negative values.

    Uses the balance calculator for checking/savings, interest
    calculator for HYSA-type accounts, amortization schedule for loan
    accounts, and growth engine for investment accounts.  The per-account
    balance derivation reads ``inputs.debt_schedules`` and the investment
    trio (``investment_params_map`` / ``deductions_by_account`` /
    ``salary_gross_biweekly``).

    Args:
        accounts: All active user accounts.
        year_ctx: The target year plus its scenario and the full ordered
            period list (used for anchor-based projection).
        inputs: Pre-loaded projection parameter maps; this section reads
            ``debt_schedules`` and the investment trio and leaves
            ``interest_params_map`` untouched.

    Returns:
        dict with monthly_values (list of 12 {month, month_name,
        balance}), jan1, dec31, delta.
    """
    all_periods = year_ctx.all_periods
    year = year_ctx.year
    if not accounts or not all_periods:
        return _empty_net_worth()

    month_end_periods = _get_month_end_periods(year, all_periods)
    account_data = _build_account_data(
        accounts, year_ctx.scenario, all_periods, inputs,
    )

    jan1_period = _find_period_before_date(date(year, 1, 1), all_periods)
    jan1_nw = (
        _sum_net_worth_at_period(jan1_period.id, account_data)
        if jan1_period else ZERO
    )

    monthly_values = _compute_monthly_values(
        month_end_periods, account_data, jan1_nw,
    )

    dec31_nw = monthly_values[11]["balance"]
    return {
        "monthly_values": monthly_values,
        "jan1": jan1_nw,
        "dec31": dec31_nw,
        "delta": dec31_nw - jan1_nw,
    }


def _build_account_data(
    accounts: list,
    scenario: Scenario,
    all_periods: list,
    inputs: _ProjectionInputs,
) -> list[dict]:
    """Build balance maps for all accounts with liability flags.

    Args:
        accounts: All active user accounts.
        scenario: Baseline scenario.
        all_periods: All user pay periods.
        inputs: Pre-loaded projection parameter maps forwarded to
            :func:`_get_account_balance_map` (``debt_schedules`` selects
            the amortization-schedule path for debt accounts; the
            investment trio drives the growth-engine path for investment
            accounts).

    Returns:
        List of dicts with 'balances' and 'is_liability' keys.
    """
    liability_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
    result = []
    for account in accounts:
        balances = _get_account_balance_map(
            account, scenario, all_periods, inputs,
        )
        if balances is None:
            continue
        result.append({
            "balances": balances,
            "is_liability": (
                account.account_type is not None
                and account.account_type.category_id == liability_cat_id
            ),
        })
    return result


def _compute_monthly_values(
    month_end_periods: dict, account_data: list, initial: Decimal,
) -> list[dict]:
    """Compute net worth at 12 monthly endpoints.

    Carries forward the last known balance for months without a
    matching period.

    Args:
        month_end_periods: month number -> last PayPeriod mapping.
        account_data: Balance maps from _build_account_data.
        initial: Net worth at the start of the year (Jan 1).

    Returns:
        List of 12 dicts with month, month_name, balance.
    """
    values = []
    last_known = initial
    for month in range(1, 13):
        if month in month_end_periods:
            last_known = _sum_net_worth_at_period(
                month_end_periods[month].id, account_data,
            )
        values.append({
            "month": month,
            "month_name": calendar.month_name[month],
            "balance": last_known,
        })
    return values


def _compute_debt_progress(
    year: int,
    debt_accounts: list,
    debt_schedules: dict[int, list],
) -> list[dict]:
    """Compute principal paid for each debt account during the year.

    Uses the pre-generated amortization schedules to find the loan
    balance at Jan 1 and Dec 31 of the target year.  This matches the
    amortization engine used by the loan dashboard, ensuring consistent
    values.

    Args:
        year: Target calendar year.
        debt_accounts: Accounts with has_amortization=True.
        debt_schedules: account_id -> list[AmortizationRow] mapping
            from _generate_debt_schedules().

    Returns:
        List of dicts: [{account_name, account_id, jan1_balance,
        dec31_balance, principal_paid}].
    """
    if not debt_accounts:
        return []

    result = []
    for account in debt_accounts:
        schedule = debt_schedules.get(account.id)
        if not schedule:
            continue

        original = _loan_original_principal(account.id)

        # Jan 1 balance = balance at end of prior year, BEFORE any
        # payments in the target year.  Use Dec 31 of the prior year
        # so a Jan 1 payment is not counted in the starting balance.
        jan1_bal = _balance_from_schedule_at_date(
            schedule, date(year - 1, 12, 31), original,
        )
        dec31_bal = _balance_from_schedule_at_date(
            schedule, date(year, 12, 31), original,
        )
        principal_paid = jan1_bal - dec31_bal

        result.append({
            "account_name": account.name,
            "account_id": account.id,
            "jan1_balance": jan1_bal,
            "dec31_balance": dec31_bal,
            "principal_paid": principal_paid,
        })

    return result


def _sum_net_worth_at_period(
    period_id: int, account_data: list[dict],
) -> Decimal:
    """Sum net worth across all accounts at a given period.

    Liabilities are subtracted from the total.

    Args:
        period_id: The pay period ID to look up balances for.
        account_data: List of dicts with 'balances' (period_id ->
            Decimal) and 'is_liability' (bool).

    Returns:
        Net worth at the period.
    """
    total = ZERO
    for data in account_data:
        bal = data["balances"].get(period_id, ZERO)
        if data["is_liability"]:
            total -= abs(bal)
        else:
            total += bal
    return total


def _empty_net_worth() -> dict:
    """Return a net worth section with empty monthly values."""
    monthly_values = [
        {"month": m, "month_name": calendar.month_name[m], "balance": ZERO}
        for m in range(1, 13)
    ]
    return {
        "monthly_values": monthly_values,
        "jan1": ZERO,
        "dec31": ZERO,
        "delta": ZERO,
    }
