"""
Shekel Budget App -- Year-End Summary Service

Aggregates a full calendar year of financial data for the year-end
summary tab.  Produces six sections: income/tax breakdown (W-2-style),
spending by category, transfers summary, net worth trend (12 monthly
points), debt progress, and savings progress.  Also computes payment
timeliness metrics (OP-2).

Primary use case is tax preparation -- the income/tax section mirrors
W-2 line items.  All monetary computation uses Decimal arithmetic.

This is a read-only aggregation service: no database writes, no Flask
request/session imports.
"""

import calendar
import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import joinedload, subqueryload

from app import ref_cache
from app.enums import AcctCategoryEnum, StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.models.ref import Status
from app.models.salary_profile import SalaryProfile
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    amortization_engine,
    balance_calculator,
    paycheck_calculator,
)
from app.services.loan_payment_service import get_payment_history
from app.services.tax_config_service import load_tax_configs

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


# ── Main Entry Point ──────────────────────────────────────────────


def compute_year_end_summary(user_id: int, year: int) -> dict:
    """Aggregate annual financial data for the specified calendar year.

    Loads common data once (baseline scenario, pay periods, accounts,
    salary profiles) then delegates to section helpers.  Each section
    degrades gracefully when its required data is missing.

    Args:
        user_id: The authenticated user's ID.
        year: The four-digit calendar year to summarize.

    Returns:
        dict with keys: income_tax, spending_by_category,
        transfers_summary, net_worth, debt_progress,
        savings_progress, payment_timeliness.
    """
    scenario = _get_baseline_scenario(user_id)
    if scenario is None:
        return _full_empty_summary()

    ctx = _load_common_data(user_id, year, scenario)
    return _build_summary(user_id, year, scenario, ctx)


def _load_common_data(
    user_id: int, year: int, scenario: Scenario,
) -> dict:
    """Load all shared data needed by the section helpers.

    Args:
        user_id: The authenticated user's ID.
        year: The target calendar year.
        scenario: The user's baseline scenario.

    Returns:
        dict with year_periods, all_periods, accounts,
        salary_profiles, year_period_ids, debt_accounts,
        savings_accounts.
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
        "savings_accounts": [
            a for a in accounts
            if a.account_type
            and not a.account_type.has_amortization
            and a.account_type.category_id != liability_cat_id
            and a.id != checking_id
        ],
    }


def _build_summary(
    user_id: int, year: int, scenario: Scenario, ctx: dict,
) -> dict:
    """Compute each section and assemble the final summary dict.

    Args:
        user_id: The authenticated user's ID.
        year: The target calendar year.
        scenario: The user's baseline scenario.
        ctx: Common data from _load_common_data.

    Returns:
        Fully assembled year-end summary dict.
    """
    income_tax = _compute_income_tax(
        user_id, year, ctx["year_periods"], ctx["salary_profiles"],
    )
    mortgage_interest = _compute_mortgage_interest(
        year, ctx["debt_accounts"], scenario.id,
    )
    income_tax["mortgage_interest_total"] = mortgage_interest

    return {
        "income_tax": income_tax,
        "spending_by_category": _compute_spending_by_category(
            user_id, year, ctx["year_period_ids"], scenario.id,
        ),
        "transfers_summary": _compute_transfers_summary(
            user_id, ctx["year_period_ids"], scenario.id,
        ),
        "net_worth": _compute_net_worth(
            year, ctx["accounts"], ctx["all_periods"], scenario,
        ),
        "debt_progress": _compute_debt_progress(
            year, ctx["debt_accounts"], ctx["all_periods"], scenario,
        ),
        "savings_progress": _compute_savings_progress(
            ctx["savings_accounts"], ctx["year_period_ids"], scenario.id,
        ),
        "payment_timeliness": _compute_payment_timeliness(
            user_id, year, ctx["year_period_ids"], scenario.id,
        ),
    }


# ── Section 1: Income & Tax Breakdown ─────────────────────────────


def _compute_income_tax(
    user_id: int,
    year: int,
    periods: list,
    salary_profiles: list,
) -> dict:
    """Aggregate W-2-style income and tax totals for the year.

    Calls the paycheck calculator for each active salary profile
    across all pay periods in the year, then sums the results.

    Pre-tax and post-tax deductions are grouped by deduction name
    so each deduction type (e.g. 401k, HSA) shows its annual total.

    Args:
        user_id: User ID for loading tax configs.
        year: Calendar year for tax config lookup.
        periods: Pay periods with start_date in the target year.
        salary_profiles: Active SalaryProfile objects with loaded
            raises and deductions.

    Returns:
        dict with gross_wages, federal_tax, state_tax,
        social_security_tax, medicare_tax, pretax_deductions,
        posttax_deductions, total_pretax, total_posttax,
        net_pay_total.  mortgage_interest_total is added by the
        caller after computing Section 2.
    """
    if not periods or not salary_profiles:
        return _empty_income_tax()

    # Accumulate totals across all profiles and periods.
    totals = {k: ZERO for k in (
        "gross", "federal", "state", "ss", "medicare", "net",
    )}
    pretax_by_name: dict[str, Decimal] = {}
    posttax_by_name: dict[str, Decimal] = {}

    for profile in salary_profiles:
        breakdowns = _compute_profile_breakdowns(
            user_id, year, profile, periods,
        )
        for bd in breakdowns:
            totals["gross"] += bd.gross_biweekly
            totals["federal"] += bd.federal_tax
            totals["state"] += bd.state_tax
            totals["ss"] += bd.social_security
            totals["medicare"] += bd.medicare
            totals["net"] += bd.net_pay

            for ded in bd.pre_tax_deductions:
                pretax_by_name[ded.name] = (
                    pretax_by_name.get(ded.name, ZERO) + ded.amount
                )
            for ded in bd.post_tax_deductions:
                posttax_by_name[ded.name] = (
                    posttax_by_name.get(ded.name, ZERO) + ded.amount
                )

    return _assemble_income_result(
        totals, pretax_by_name, posttax_by_name,
    )


def _compute_profile_breakdowns(
    user_id: int, year: int, profile: SalaryProfile, periods: list,
) -> list:
    """Run the paycheck calculator for one profile across all periods.

    Loads tax configs for the target year with a fallback to the
    current year if the target year has no configs (follows the
    recurrence_engine.py pattern).

    Args:
        user_id: User ID for tax config lookup.
        year: Target calendar year.
        profile: SalaryProfile with loaded raises and deductions.
        periods: Pay periods in the target year.

    Returns:
        List of PaycheckBreakdown from project_salary.
    """
    tax_configs = load_tax_configs(user_id, profile, tax_year=year)
    if all(v is None for v in tax_configs.values()):
        tax_configs = load_tax_configs(user_id, profile)

    return paycheck_calculator.project_salary(
        profile, periods, tax_configs,
    )


def _assemble_income_result(
    totals: dict, pretax_by_name: dict, posttax_by_name: dict,
) -> dict:
    """Build the income_tax section dict from accumulated totals.

    Args:
        totals: dict mapping short keys to Decimal sums.
        pretax_by_name: deduction name -> annual total.
        posttax_by_name: deduction name -> annual total.

    Returns:
        Fully structured income_tax section dict.
    """
    pretax_list = [
        {"name": k, "annual_total": v}
        for k, v in sorted(pretax_by_name.items())
    ]
    posttax_list = [
        {"name": k, "annual_total": v}
        for k, v in sorted(posttax_by_name.items())
    ]

    return {
        "gross_wages": totals["gross"],
        "federal_tax": totals["federal"],
        "state_tax": totals["state"],
        "social_security_tax": totals["ss"],
        "medicare_tax": totals["medicare"],
        "pretax_deductions": pretax_list,
        "posttax_deductions": posttax_list,
        "total_pretax": sum(
            (d["annual_total"] for d in pretax_list), ZERO,
        ),
        "total_posttax": sum(
            (d["annual_total"] for d in posttax_list), ZERO,
        ),
        "net_pay_total": totals["net"],
    }


# ── Section 2: Mortgage Interest ──────────────────────────────────


def _compute_mortgage_interest(
    year: int,
    loan_accounts: list,
    scenario_id: int,
) -> Decimal:
    """Sum mortgage/loan interest paid during the calendar year.

    For each loan account with amortization parameters, generates the
    full amortization schedule from origination (using payment history
    for accuracy) and sums the interest portion of payments whose
    payment_date falls in the target year.

    This number appears on Schedule A (itemized deductions) so
    accuracy is critical.

    Args:
        year: Calendar year to sum interest for.
        loan_accounts: Accounts with has_amortization=True.
        scenario_id: Baseline scenario ID for payment history.

    Returns:
        Total interest paid across all loan accounts in the year.
    """
    if not loan_accounts:
        return ZERO

    total_interest = ZERO

    for account in loan_accounts:
        params = (
            db.session.query(LoanParams)
            .filter_by(account_id=account.id)
            .first()
        )
        if params is None:
            continue

        payments = get_payment_history(account.id, scenario_id)

        schedule = amortization_engine.generate_schedule(
            current_principal=params.original_principal,
            annual_rate=params.interest_rate,
            remaining_months=params.term_months,
            origination_date=params.origination_date,
            payment_day=params.payment_day,
            original_principal=params.original_principal,
            term_months=params.term_months,
            payments=payments if payments else None,
        )

        for row in schedule:
            if row.payment_date.year == year:
                total_interest += row.interest

    return total_interest


# ── Section 3: Spending by Category ───────────────────────────────


def _compute_spending_by_category(
    user_id: int,
    year: int,
    period_ids: list[int],
    scenario_id: int,
) -> list[dict]:
    """Group paid expense transactions by category hierarchy.

    Queries settled expense transactions in the year's pay periods,
    attributes each to the year using COALESCE(due_date,
    pay_period.start_date), and groups by category group_name then
    item_name.

    Args:
        user_id: User ID for ownership filtering.
        year: Target calendar year for attribution.
        period_ids: IDs of pay periods with start_date in the year.
        scenario_id: Baseline scenario ID.

    Returns:
        List of dicts sorted by group_total descending:
        [{group_name, group_total, items: [{item_name, item_total}]}]
    """
    if not period_ids:
        return []

    transactions = _query_settled_expenses(
        user_id, period_ids, scenario_id,
    )

    # Group by category using COALESCE(due_date, pp.start_date).
    groups: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: ZERO),
    )
    for txn in transactions:
        if _attribution_year(txn) != year:
            continue
        group_name, item_name = _txn_category_names(txn)
        groups[group_name][item_name] += abs(txn.effective_amount)

    return _build_spending_hierarchy(groups)


# ── Section 4: Transfers Summary ──────────────────────────────────


def _compute_transfers_summary(
    user_id: int,
    period_ids: list[int],
    scenario_id: int,
) -> list[dict]:
    """Group transfers by destination account for the year.

    Args:
        user_id: User ID for ownership filtering.
        period_ids: IDs of pay periods with start_date in the year.
        scenario_id: Baseline scenario ID.

    Returns:
        List of dicts sorted by total_amount descending:
        [{destination_account, destination_account_id, total_amount}]
    """
    if not period_ids:
        return []

    excluded_ids = _get_excluded_status_ids()

    transfers = (
        db.session.query(Transfer)
        .options(joinedload(Transfer.to_account))
        .filter(
            Transfer.user_id == user_id,
            Transfer.scenario_id == scenario_id,
            Transfer.pay_period_id.in_(period_ids),
            Transfer.is_deleted.is_(False),
            ~Transfer.status_id.in_(excluded_ids),
        )
        .all()
    )

    by_dest: dict[int, dict] = {}
    for t in transfers:
        acct_id = t.to_account_id
        if acct_id not in by_dest:
            by_dest[acct_id] = {
                "destination_account": t.to_account.name,
                "destination_account_id": acct_id,
                "total_amount": ZERO,
            }
        by_dest[acct_id]["total_amount"] += t.amount

    result = list(by_dest.values())
    result.sort(key=lambda x: x["total_amount"], reverse=True)
    return result


# ── Section 5: Net Worth Trend ────────────────────────────────────


def _compute_net_worth(
    year: int,
    accounts: list,
    all_periods: list,
    scenario: Scenario,
) -> dict:
    """Compute net worth at 12 monthly endpoints for the year.

    For each month, finds the last pay period ending in or before the
    month's last day, then sums all account balances at that period.
    Liability accounts contribute negative values.

    Uses the balance calculator for checking/savings, interest
    calculator for HYSA-type accounts, and amortization calculator
    for loan accounts -- matching the pattern in chart_data_service.

    Args:
        year: Target calendar year.
        accounts: All active user accounts.
        all_periods: All user pay periods (for anchor-based projection).
        scenario: Baseline scenario.

    Returns:
        dict with monthly_values (list of 12 {month, month_name,
        balance}), jan1, dec31, delta.
    """
    if not accounts or not all_periods:
        return _empty_net_worth()

    month_end_periods = _get_month_end_periods(year, all_periods)
    account_data = _build_account_data(accounts, scenario, all_periods)

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
    accounts: list, scenario: Scenario, all_periods: list,
) -> list[dict]:
    """Build balance maps for all accounts with liability flags.

    Args:
        accounts: All active user accounts.
        scenario: Baseline scenario.
        all_periods: All user pay periods.

    Returns:
        List of dicts with 'balances' and 'is_liability' keys.
    """
    liability_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
    result = []
    for account in accounts:
        balances = _get_account_balance_map(account, scenario, all_periods)
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


# ── Section 6: Debt Progress ─────────────────────────────────────


def _compute_debt_progress(
    year: int,
    debt_accounts: list,
    all_periods: list,
    scenario: Scenario,
) -> list[dict]:
    """Compute principal paid for each debt account during the year.

    Uses the balance calculator with amortization to get accurate
    loan balances at the start and end of the year.

    Args:
        year: Target calendar year.
        debt_accounts: Accounts with has_amortization=True.
        all_periods: All user pay periods.
        scenario: Baseline scenario.

    Returns:
        List of dicts: [{account_name, account_id, jan1_balance,
        dec31_balance, principal_paid}].
    """
    if not debt_accounts:
        return []

    jan1_period = _find_period_before_date(date(year, 1, 1), all_periods)
    dec31_period = _find_period_on_or_before_date(
        date(year, 12, 31), all_periods,
    )

    result = []
    for account in debt_accounts:
        balances = _get_account_balance_map(
            account, scenario, all_periods,
        )
        if balances is None:
            continue

        jan1_bal = ZERO
        if jan1_period is not None:
            jan1_bal = abs(balances.get(jan1_period.id, ZERO))

        dec31_bal = ZERO
        if dec31_period is not None:
            dec31_bal = abs(balances.get(dec31_period.id, ZERO))

        principal_paid = jan1_bal - dec31_bal

        result.append({
            "account_name": account.name,
            "account_id": account.id,
            "jan1_balance": jan1_bal,
            "dec31_balance": dec31_bal,
            "principal_paid": principal_paid,
        })

    return result


# ── Section 7: Savings Progress ──────────────────────────────────


def _compute_savings_progress(
    savings_accounts: list,
    period_ids: list[int],
    scenario_id: int,
) -> list[dict]:
    """Compute balance growth and contributions for savings accounts.

    For each savings/investment account, computes total contributions
    (shadow income transactions from transfers into the account)
    and approximate balances.

    Args:
        savings_accounts: Non-debt, non-checking accounts.
        period_ids: IDs of pay periods in the target year.
        scenario_id: Baseline scenario ID.

    Returns:
        List of dicts: [{account_name, account_id, jan1_balance,
        dec31_balance, total_contributions}].
    """
    if not savings_accounts:
        return []

    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

    result = []
    for account in savings_accounts:
        # Get contributions: shadow income transactions from transfers.
        contributions = ZERO
        if period_ids:
            shadow_txns = (
                db.session.query(Transaction)
                .join(Transaction.status)
                .filter(
                    Transaction.account_id == account.id,
                    Transaction.scenario_id == scenario_id,
                    Transaction.pay_period_id.in_(period_ids),
                    Transaction.transfer_id.isnot(None),
                    Transaction.transaction_type_id == income_type_id,
                    Transaction.is_deleted.is_(False),
                    Status.excludes_from_balance.is_(False),
                )
                .all()
            )
            for txn in shadow_txns:
                contributions += txn.effective_amount

        # Balances are computed via the main net_worth section
        # using the balance calculator.  For savings progress we
        # report contributions only -- balances come from the
        # account's anchor and transaction history.
        jan1_bal = account.current_anchor_balance or ZERO
        dec31_bal = jan1_bal + contributions

        result.append({
            "account_name": account.name,
            "account_id": account.id,
            "jan1_balance": jan1_bal,
            "dec31_balance": dec31_bal,
            "total_contributions": contributions,
        })

    return result


# ── OP-2: Payment Timeliness ──────────────────────────────────────


def _compute_payment_timeliness(
    user_id: int,
    year: int,
    period_ids: list[int],
    scenario_id: int,
) -> dict | None:
    """Compute bill payment timeliness metrics for the year.

    Examines settled expense transactions that have both paid_at
    and due_date populated.  Counts how many were paid on time
    vs. late, and computes the average days paid before the due date.

    Args:
        user_id: User ID for ownership filtering.
        year: Target calendar year for attribution.
        period_ids: IDs of pay periods in the target year.
        scenario_id: Baseline scenario ID.

    Returns:
        dict with total_bills_paid, paid_on_time, paid_late,
        avg_days_before_due.  Returns None if no applicable
        transactions exist.
    """
    if not period_ids:
        return None

    transactions = _query_settled_expenses(
        user_id, period_ids, scenario_id,
    )

    # Filter to transactions with both paid_at and due_date,
    # attributed to the target year.
    applicable = [
        txn for txn in transactions
        if txn.paid_at is not None
        and txn.due_date is not None
        and _attribution_year(txn) == year
    ]

    if not applicable:
        return None

    paid_on_time = 0
    paid_late = 0
    total_days = 0

    for txn in applicable:
        days_before = (txn.due_date - txn.paid_at.date()).days
        total_days += days_before
        if txn.paid_at.date() <= txn.due_date:
            paid_on_time += 1
        else:
            paid_late += 1

    avg_days = (
        Decimal(str(total_days)) / Decimal(str(len(applicable)))
    ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    return {
        "total_bills_paid": len(applicable),
        "paid_on_time": paid_on_time,
        "paid_late": paid_late,
        "avg_days_before_due": avg_days,
    }


# ── Query and Attribution Helpers ──────────────────────────────────


def _query_settled_expenses(
    user_id: int, period_ids: list[int], scenario_id: int,
) -> list:
    """Load settled expense transactions for the given periods.

    Args:
        user_id: User ID for ownership filtering.
        period_ids: Pay period IDs to query.
        scenario_id: Baseline scenario ID.

    Returns:
        List of Transaction objects with eager-loaded category
        and pay_period.
    """
    return (
        db.session.query(Transaction)
        .join(Transaction.pay_period)
        .join(Transaction.account)
        .options(
            joinedload(Transaction.category),
            joinedload(Transaction.pay_period),
        )
        .filter(
            Account.user_id == user_id,
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
            Transaction.transaction_type_id == ref_cache.txn_type_id(
                TxnTypeEnum.EXPENSE,
            ),
            Transaction.status_id.in_(_get_settled_status_ids()),
        )
        .all()
    )


def _attribution_year(txn: Transaction) -> int:
    """Return the calendar year a transaction is attributed to.

    Uses COALESCE(due_date, pay_period.start_date), consistent with
    calendar and variance services.
    """
    attr_date = (
        txn.due_date if txn.due_date is not None
        else txn.pay_period.start_date
    )
    return attr_date.year


def _txn_category_names(txn: Transaction) -> tuple[str, str]:
    """Return (group_name, item_name) for a transaction's category."""
    if txn.category is None:
        return ("Uncategorized", "Uncategorized")
    return (txn.category.group_name, txn.category.item_name)


def _build_spending_hierarchy(
    groups: dict[str, dict[str, Decimal]],
) -> list[dict]:
    """Convert grouped spending data to a sorted hierarchical list.

    Args:
        groups: group_name -> {item_name -> total} mapping.

    Returns:
        List of dicts sorted by group_total descending.
    """
    result = []
    for group_name, items in groups.items():
        item_list = [
            {"item_name": k, "item_total": v}
            for k, v in items.items()
        ]
        item_list.sort(key=lambda x: x["item_total"], reverse=True)
        group_total = sum(
            (i["item_total"] for i in item_list), ZERO,
        )
        result.append({
            "group_name": group_name,
            "group_total": group_total,
            "items": item_list,
        })
    result.sort(key=lambda x: x["group_total"], reverse=True)
    return result


# ── Internal Helpers ──────────────────────────────────────────────


def _get_baseline_scenario(user_id: int) -> Scenario | None:
    """Return the baseline scenario for the user, or None."""
    return (
        db.session.query(Scenario)
        .filter_by(user_id=user_id, is_baseline=True)
        .first()
    )


def _get_primary_checking_id(accounts: list) -> int | None:
    """Return the ID of the first checking account, or None.

    Used to exclude the primary checking account from the savings
    progress section (it is not a savings vehicle).
    """
    from app.enums import AcctTypeEnum  # pylint: disable=import-outside-toplevel
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    for a in accounts:
        if a.account_type_id == checking_type_id:
            return a.id
    return None


def _get_settled_status_ids() -> list[int]:
    """Return status IDs that represent settled transactions."""
    return [
        ref_cache.status_id(StatusEnum.DONE),
        ref_cache.status_id(StatusEnum.RECEIVED),
        ref_cache.status_id(StatusEnum.SETTLED),
    ]


def _get_excluded_status_ids() -> list[int]:
    """Return status IDs that should be excluded from summaries."""
    return [
        ref_cache.status_id(StatusEnum.CREDIT),
        ref_cache.status_id(StatusEnum.CANCELLED),
    ]


def _get_account_balance_map(
    account: Account,
    scenario: Scenario,
    periods: list,
) -> dict | None:
    """Compute period_id -> balance mapping for one account.

    Follows the pattern in chart_data_service._calculate_account_balances:
    uses interest calculator for HYSA-type accounts, amortization
    calculator for loans, and plain calculator for everything else.

    Args:
        account: The account to project.
        scenario: The baseline scenario.
        periods: All user pay periods.

    Returns:
        OrderedDict mapping period_id to Decimal balance, or None
        if the account has no anchor period.
    """
    if account.current_anchor_period_id is None:
        return None

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
    base_args = {
        "anchor_balance": anchor_balance,
        "anchor_period_id": account.current_anchor_period_id,
        "periods": periods,
        "transactions": transactions,
    }

    acct_type = account.account_type

    # Interest-bearing accounts (HYSA, Money Market, CD, HSA).
    if (acct_type and acct_type.has_interest
            and hasattr(account, "interest_params")
            and account.interest_params):
        balances, _ = balance_calculator.calculate_balances_with_interest(
            **base_args, interest_params=account.interest_params,
        )
        return balances

    # Amortizing loan accounts (Mortgage, Auto Loan, etc.).
    if acct_type and acct_type.has_amortization:
        loan_params = (
            db.session.query(LoanParams)
            .filter_by(account_id=account.id)
            .first()
        )
        if loan_params:
            balances, _ = (
                balance_calculator.calculate_balances_with_amortization(
                    **base_args,
                    account_id=account.id,
                    loan_params=loan_params,
                )
            )
            return balances

    # Standard checking/savings.
    balances, _ = balance_calculator.calculate_balances(**base_args)
    return balances


def _get_month_end_periods(
    year: int, all_periods: list,
) -> dict[int, PayPeriod]:
    """Map each month (1-12) to the last period ending on or before
    that month's last day.

    Args:
        year: Target calendar year.
        all_periods: All user pay periods sorted by period_index.

    Returns:
        dict mapping month number to PayPeriod.
    """
    month_periods: dict[int, PayPeriod] = {}

    for month in range(1, 13):
        last_day = date(
            year, month, calendar.monthrange(year, month)[1],
        )
        best = _find_period_on_or_before_date(last_day, all_periods)
        if best is not None:
            month_periods[month] = best

    return month_periods


def _find_period_before_date(
    target: date, all_periods: list,
) -> PayPeriod | None:
    """Find the last period whose end_date is strictly before target."""
    best = None
    for p in all_periods:
        if p.end_date < target:
            if best is None or p.period_index > best.period_index:
                best = p
    return best


def _find_period_on_or_before_date(
    target: date, all_periods: list,
) -> PayPeriod | None:
    """Find the last period whose end_date is on or before target."""
    best = None
    for p in all_periods:
        if p.end_date <= target:
            if best is None or p.period_index > best.period_index:
                best = p
    return best


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


def _empty_income_tax() -> dict:
    """Return an income/tax section with all zeros."""
    return {
        "gross_wages": ZERO,
        "federal_tax": ZERO,
        "state_tax": ZERO,
        "social_security_tax": ZERO,
        "medicare_tax": ZERO,
        "pretax_deductions": [],
        "posttax_deductions": [],
        "total_pretax": ZERO,
        "total_posttax": ZERO,
        "net_pay_total": ZERO,
        "mortgage_interest_total": ZERO,
    }


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


def _full_empty_summary() -> dict:
    """Return a complete summary with all sections empty/zero."""
    income_tax = _empty_income_tax()
    return {
        "income_tax": income_tax,
        "spending_by_category": [],
        "transfers_summary": [],
        "net_worth": _empty_net_worth(),
        "debt_progress": [],
        "savings_progress": [],
        "payment_timeliness": None,
    }
