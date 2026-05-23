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
from collections import OrderedDict, defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import case
from sqlalchemy.orm import joinedload, subqueryload

from app import ref_cache
from app.enums import AcctCategoryEnum, StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.models.salary_profile import SalaryProfile
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.services import (
    balance_calculator,
    balance_resolver,
    growth_engine,
    income_service,
    loan_resolver,
    paycheck_calculator,
)
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
    compute_loan_period_balance_map,
)
from app.services.investment_projection import (
    adapt_deductions,
    build_contribution_timeline,
)
from app.services.loan_payment_service import load_loan_context
from app.services.projection_inputs import (
    build_investment_projection_inputs,
    load_active_deductions_for_accounts,
)
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.balance_predicates import balance_excluded_status_ids
from app.services.tax_config_service import load_tax_configs
from app.utils.money import round_money

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
    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        return _full_empty_summary()

    ctx = _load_common_data(user_id, year, scenario)
    return _build_summary(user_id, year, scenario, ctx)


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


def _build_summary(
    user_id: int, year: int, scenario: Scenario, ctx: dict,
) -> dict:
    """Compute each section and assemble the final summary dict.

    Generates amortization schedules once for all debt accounts and
    shares them across mortgage interest, debt progress, and net worth.

    Args:
        user_id: The authenticated user's ID.
        year: The target calendar year.
        scenario: The user's baseline scenario.
        ctx: Common data from :func:`_load_common_data`.  This is the
            sanctioned W-052 load-once bag and stays whole at this
            top-level assembly site -- the assembler legitimately
            reads 8 of 11 keys.  MED-01 / S6-06 narrowing happens
            below: each section helper now declares the specific
            fields it depends on instead of taking the opaque bag.

    Returns:
        Fully assembled year-end summary dict.
    """
    # Pre-compute amortization schedules with properly prepared payments
    # (escrow subtracted, biweekly overlaps redistributed).  Shared by
    # mortgage interest, debt progress, and net worth sections.
    debt_schedules = _generate_debt_schedules(
        ctx["debt_accounts"], scenario.id,
    )

    income_tax = _compute_income_tax(
        user_id, year, ctx["year_periods"], ctx["salary_profiles"],
    )
    mortgage_interest = _compute_mortgage_interest(year, debt_schedules)
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
            debt_schedules=debt_schedules,
            investment_params_map=ctx["investment_params_map"],
            deductions_by_account=ctx["deductions_by_account"],
            salary_gross_biweekly=ctx["salary_gross_biweekly"],
        ),
        "debt_progress": _compute_debt_progress(
            year, ctx["debt_accounts"], debt_schedules,
        ),
        "savings_progress": _compute_savings_progress(
            ctx["savings_accounts"], ctx["year_period_ids"],
            scenario.id, ctx["all_periods"], year, scenario,
            investment_params_map=ctx["investment_params_map"],
            interest_params_map=ctx["interest_params_map"],
            deductions_by_account=ctx["deductions_by_account"],
            salary_gross_biweekly=ctx["salary_gross_biweekly"],
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
    debt_schedules: dict[int, list],
) -> Decimal:
    """Sum mortgage/loan interest paid during the calendar year.

    Uses pre-generated amortization schedules (with properly prepared
    payments) and sums the interest portion of payments whose
    payment_date falls in the target year.

    This number appears on Schedule A (itemized deductions) so
    accuracy is critical.

    Args:
        year: Calendar year to sum interest for.
        debt_schedules: account_id -> list[AmortizationRow] mapping
            from _generate_debt_schedules().

    Returns:
        Total interest paid across all loan accounts in the year.
    """
    total_interest = ZERO

    for schedule in debt_schedules.values():
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
    item_name.  Items whose parent template has is_envelope=True
    receive an entry_breakdown sub-dict with per-purchase aggregates
    queried from TransactionEntry (OP-3).

    Args:
        user_id: User ID for ownership filtering.
        year: Target calendar year for attribution.
        period_ids: IDs of pay periods with start_date in the year.
        scenario_id: Baseline scenario ID.

    Returns:
        List of dicts sorted by group_total descending:
        [{group_name, group_total, items: [{item_name, item_total,
        entry_breakdown?}]}].  entry_breakdown is present only for
        items with at least one tracked, settled, year-attributed
        parent transaction in the period.
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

    spending = _build_spending_hierarchy(groups)

    # OP-3: attach per-entry breakdowns for tracked categories.
    breakdowns = _compute_entry_breakdowns(
        user_id, year, period_ids, scenario_id,
    )
    if breakdowns:
        for group in spending:
            for item in group["items"]:
                key = (group["group_name"], item["item_name"])
                if key in breakdowns:
                    item["entry_breakdown"] = breakdowns[key]

    return spending


def _compute_entry_breakdowns(
    user_id: int,
    year: int,
    period_ids: list[int],
    scenario_id: int,
) -> dict[tuple[str, str], dict]:
    """Aggregate transaction entries for tracked categories in the year.

    Runs one SQL query that joins TransactionEntry through Transaction
    to its template, account, pay period, and category.  Filters with
    the same predicates as _query_settled_expenses (settled expenses
    in the user's year period_ids on the baseline scenario) plus
    is_envelope=True on the parent template.  Aggregates per parent
    transaction so the same _attribution_year filter used by
    _compute_spending_by_category can be applied in Python -- this
    handles transactions whose due_date crosses a calendar year
    boundary identically to the existing category aggregation.

    Args:
        user_id: User ID for ownership filtering.
        year: Target calendar year for attribution.
        period_ids: IDs of pay periods with start_date in the year.
        scenario_id: Baseline scenario ID.

    Returns:
        dict mapping (group_name, item_name) tuple to a breakdown dict
        with entry_count, entry_total, credit_total, debit_total,
        avg_entry, and transaction_count_with_entries.  Categories
        with no tracked entries in the attribution year are absent.
    """
    if not period_ids:
        return {}

    settled_status_ids = _get_settled_status_ids()
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

    rows = (
        db.session.query(
            Category.group_name.label("group_name"),
            Category.item_name.label("item_name"),
            Transaction.id.label("transaction_id"),
            Transaction.due_date.label("due_date"),
            PayPeriod.start_date.label("pp_start_date"),
            db.func.count(TransactionEntry.id).label("entry_count"),
            db.func.sum(TransactionEntry.amount).label("entry_total"),
            db.func.sum(
                case(
                    (
                        TransactionEntry.is_credit.is_(True),
                        TransactionEntry.amount,
                    ),
                    else_=Decimal("0"),
                )
            ).label("credit_total"),
        )
        .join(
            Transaction,
            TransactionEntry.transaction_id == Transaction.id,
        )
        .join(
            TransactionTemplate,
            Transaction.template_id == TransactionTemplate.id,
        )
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .filter(
            Account.user_id == user_id,
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
            Transaction.transaction_type_id == expense_type_id,
            Transaction.status_id.in_(settled_status_ids),
            TransactionTemplate.is_envelope.is_(True),
        )
        .group_by(
            Category.group_name,
            Category.item_name,
            Transaction.id,
            Transaction.due_date,
            PayPeriod.start_date,
        )
        .all()
    )

    breakdowns: dict[tuple[str, str], dict] = {}
    for row in rows:
        # Match _attribution_year(): COALESCE(due_date, pp.start_date).
        attr_year = (
            row.due_date.year if row.due_date is not None
            else row.pp_start_date.year
        )
        if attr_year != year:
            continue
        _accumulate_entry_row(breakdowns, row)

    for bd in breakdowns.values():
        _finalize_entry_breakdown(bd)

    return breakdowns


def _accumulate_entry_row(
    breakdowns: dict[tuple[str, str], dict], row,
) -> None:
    """Add one transaction's entry stats to the running breakdown.

    Looks up (or creates) the breakdown dict keyed by the parent
    transaction's category and increments the running totals.  Missing
    categories are mapped to "Uncategorized" so the key matches what
    _txn_category_names() produces in the existing aggregation.

    Args:
        breakdowns: Mutable mapping from (group_name, item_name) to
            running aggregate dict.  Mutated in place.
        row: One result row from the entry aggregation query.
    """
    key = (
        row.group_name or "Uncategorized",
        row.item_name or "Uncategorized",
    )
    bd = breakdowns.get(key)
    if bd is None:
        bd = {
            "entry_count": 0,
            "entry_total": ZERO,
            "credit_total": ZERO,
            "transaction_count_with_entries": 0,
        }
        breakdowns[key] = bd
    bd["entry_count"] += int(row.entry_count or 0)
    bd["entry_total"] += row.entry_total or ZERO
    bd["credit_total"] += row.credit_total or ZERO
    bd["transaction_count_with_entries"] += 1


def _finalize_entry_breakdown(bd: dict) -> None:
    """Compute derived debit_total and avg_entry on a breakdown dict.

    Mutates the dict in place.  Called after all entry rows have been
    accumulated.  avg_entry is a monetary average (dollars per entry)
    so it is rounded through :func:`app.utils.money.round_money`,
    the project's centralized ROUND_HALF_UP cent boundary (E-26 /
    HIGH-04).  ``debit_total`` is pre-rounded subtraction of two
    already-rounded sums, so no further rounding is needed.

    Args:
        bd: Running aggregate dict produced by _accumulate_entry_row.
    """
    entry_total = bd["entry_total"]
    bd["debit_total"] = entry_total - bd["credit_total"]
    count = bd["entry_count"]
    bd["avg_entry"] = (
        round_money(entry_total / Decimal(count))
        if count > 0 else ZERO
    )


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

    # Routed through the centralized ``balance_excluded_status_ids``
    # accessor (D6-09 / MED-02) so the Credit / Cancelled exclusion
    # set is defined exactly once across the codebase.
    excluded_ids = balance_excluded_status_ids()

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
    debt_schedules: dict[int, list] | None = None,
    *,
    investment_params_map: dict | None = None,
    deductions_by_account: dict | None = None,
    salary_gross_biweekly: Decimal | None = None,
) -> dict:
    """Compute net worth at 12 monthly endpoints for the year.

    For each month, finds the last pay period ending in or before the
    month's last day, then sums all account balances at that period.
    Liability accounts contribute negative values.

    Uses the balance calculator for checking/savings, interest
    calculator for HYSA-type accounts, amortization schedule for loan
    accounts, and growth engine for investment accounts (when the
    investment-params / deductions / salary trio is supplied).

    Args:
        year: Target calendar year.
        accounts: All active user accounts.
        all_periods: All user pay periods (for anchor-based projection).
        scenario: Baseline scenario.
        debt_schedules: Optional account_id -> list[AmortizationRow]
            mapping.  When provided, debt account balances are derived
            from the amortization schedule.
        investment_params_map: MED-01 / S6-06: narrow replacement for
            the previous opaque-ctx parameter -- the only investment-
            side field this helper actually needs.  ``None`` skips the
            growth-engine projection (investments are returned at
            their canonical resolver balance).
        deductions_by_account: Pay-deduction map keyed by account id,
            forwarded to :func:`_build_investment_balance_map` for the
            growth engine's contribution inputs.
        salary_gross_biweekly: The active salary profile's biweekly
            gross, forwarded for the same reason.

    Returns:
        dict with monthly_values (list of 12 {month, month_name,
        balance}), jan1, dec31, delta.
    """
    if not accounts or not all_periods:
        return _empty_net_worth()

    month_end_periods = _get_month_end_periods(year, all_periods)
    account_data = _build_account_data(
        accounts, scenario, all_periods, debt_schedules,
        investment_params_map=investment_params_map,
        deductions_by_account=deductions_by_account,
        salary_gross_biweekly=salary_gross_biweekly,
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
    debt_schedules: dict[int, list] | None = None,
    *,
    investment_params_map: dict | None = None,
    deductions_by_account: dict | None = None,
    salary_gross_biweekly: Decimal | None = None,
) -> list[dict]:
    """Build balance maps for all accounts with liability flags.

    Args:
        accounts: All active user accounts.
        scenario: Baseline scenario.
        all_periods: All user pay periods.
        debt_schedules: Optional account_id -> list[AmortizationRow]
            mapping for debt accounts.
        investment_params_map: MED-01 / S6-06: explicit replacement
            for the previous opaque-ctx parameter -- this helper now
            declares the exact dependency it forwards to
            :func:`_get_account_balance_map`.
        deductions_by_account: Forwarded to
            :func:`_get_account_balance_map` for the investment-side
            growth projection.
        salary_gross_biweekly: Forwarded for the same reason.

    Returns:
        List of dicts with 'balances' and 'is_liability' keys.
    """
    liability_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
    result = []
    for account in accounts:
        balances = _get_account_balance_map(
            account, scenario, all_periods,
            debt_schedules=debt_schedules,
            investment_params_map=investment_params_map,
            deductions_by_account=deductions_by_account,
            salary_gross_biweekly=salary_gross_biweekly,
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


# ── Section 6: Debt Progress ─────────────────────────────────────


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

        params = (
            db.session.query(LoanParams)
            .filter_by(account_id=account.id)
            .first()
        )
        original = params.original_principal if params else ZERO

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


# ── Section 7: Savings Progress ──────────────────────────────────


def _compute_savings_progress(
    savings_accounts: list,
    period_ids: list[int],
    scenario_id: int,
    all_periods: list,
    year: int,
    scenario: Scenario,
    *,
    investment_params_map: dict,
    interest_params_map: dict,
    deductions_by_account: dict,
    salary_gross_biweekly: Decimal,
) -> list[dict]:
    """Compute balance growth, contributions, and returns for savings accounts.

    Dispatches to three calculation paths based on account type:
    - Investment accounts (with InvestmentParams): growth engine with
      employer contributions and assumed annual return.
    - Interest-bearing accounts (with InterestParams): balance
      calculator with interest accrual.
    - Plain savings accounts: standard balance calculator.

    Args:
        savings_accounts: Non-debt, non-checking accounts.
        period_ids: IDs of pay periods in the target year.
        scenario_id: Baseline scenario ID.
        all_periods: All user pay periods.
        year: Target calendar year.
        scenario: Baseline scenario.
        investment_params_map: MED-01 / S6-06: explicit narrow
            replacement for the previous 11-key ``ctx`` opaque bag.
            Maps account_id -> InvestmentParams.
        interest_params_map: account_id -> InterestParams.
        deductions_by_account: account_id -> PaycheckDeduction list,
            forwarded to :func:`_project_investment_for_year`.
        salary_gross_biweekly: Active salary profile gross biweekly,
            forwarded for the same reason.

    Returns:
        List of dicts: [{account_name, account_id, jan1_balance,
        dec31_balance, total_contributions, employer_contributions,
        investment_growth}].
    """
    if not savings_accounts:
        return []

    result = []
    for account in savings_accounts:
        contributions = _sum_shadow_income(
            account.id, period_ids, scenario_id,
        )

        inv_params = investment_params_map.get(account.id)
        int_params = interest_params_map.get(account.id)

        if inv_params:
            jan1_bal, dec31_bal, employer_total, growth_total = (
                _project_investment_for_year(
                    account, inv_params, all_periods, year,
                    scenario, period_ids, scenario_id,
                    deductions_by_account=deductions_by_account,
                    salary_gross_biweekly=salary_gross_biweekly,
                )
            )
        elif int_params:
            balances = _get_account_balance_map(
                account, scenario, all_periods,
            )
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
            balances = _get_account_balance_map(
                account, scenario, all_periods,
            )
            jan1_bal = _lookup_balance_with_anchor_fallback(
                balances, year, 1, all_periods, account,
            )
            dec31_bal = _lookup_balance_with_anchor_fallback(
                balances, year, 12, all_periods, account,
            )
            employer_total = ZERO
            growth_total = ZERO

        result.append({
            "account_name": account.name,
            "account_id": account.id,
            "jan1_balance": jan1_bal,
            "dec31_balance": dec31_bal,
            "total_contributions": contributions,
            "employer_contributions": employer_total,
            "investment_growth": growth_total,
        })

    return result


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
    if not period_ids:
        return ZERO

    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

    # Status filter routes through the centralized
    # ``balance_excluded_status_ids`` accessor (D6-09 / MED-02): one
    # exclusion-set definition shared with every other
    # ``[CREDIT, CANCELLED]`` filter in the codebase.  Filtering by
    # ``Transaction.status_id`` lets the query drop the ``Status``
    # join entirely; the audit-trigger row count is unchanged and
    # the SQL is one INNER JOIN shorter.
    shadow_txns = (
        db.session.query(Transaction)
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
            Transaction.is_deleted.is_(False),
            ~Transaction.status_id.in_(balance_excluded_status_ids()),
        )
        .all()
    )

    total = ZERO
    for txn in shadow_txns:
        total += txn.effective_amount
    return total


def _project_investment_for_year(
    account: Account,
    investment_params: InvestmentParams,
    all_periods: list,
    year: int,
    scenario: Scenario,
    year_period_ids: list[int],
    scenario_id: int,
    *,
    deductions_by_account: dict,
    salary_gross_biweekly: Decimal,
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
        all_periods: All user pay periods.
        year: Target calendar year.
        scenario: Baseline scenario.
        year_period_ids: Pay period IDs in the target year.
        scenario_id: Baseline scenario ID.
        deductions_by_account: MED-01 / S6-06: explicit narrow
            replacement for the previous 11-key ``ctx`` opaque bag.
            Account-id -> PaycheckDeduction list.
        salary_gross_biweekly: Active salary profile gross biweekly.

    Returns:
        Tuple of (jan1_balance, dec31_balance, employer_contributions,
        investment_growth).
    """
    # Get base balance from the balance calculator (anchor + transactions).
    balances = _get_account_balance_map(account, scenario, all_periods)

    # Find pay periods that fall within the target year.
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    year_periods = [
        p for p in all_periods
        if year_start <= p.start_date <= year_end
    ]

    if not year_periods:
        return ZERO, ZERO, ZERO, ZERO

    # Adapt paycheck deductions for calculate_investment_inputs().
    acct_deductions = deductions_by_account.get(account.id, [])
    adapted_deductions = adapt_deductions(acct_deductions)

    # Shadow income transactions in the year for contribution history.
    # Status filter routes through ``balance_excluded_status_ids``
    # (D6-09 / MED-02); see ``_compute_account_contributions`` above.
    # ``joinedload(Transaction.status)`` is retained so downstream
    # consumers (``investment_projection.calculate_investment_inputs``)
    # can read ``txn.status.is_settled`` /
    # ``txn.status.excludes_from_balance`` without an N+1; the
    # explicit INNER JOIN is dropped because the
    # ``Transaction.status_id`` filter no longer needs it.
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    acct_contributions = (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.status),
            joinedload(Transaction.pay_period),
        )
        .filter(
            Transaction.account_id == account.id,
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id.in_(year_period_ids),
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
            Transaction.is_deleted.is_(False),
            ~Transaction.status_id.in_(balance_excluded_status_ids()),
        )
        .all()
    ) if year_period_ids else []

    # Compute periodic contribution and employer params.
    # F-22 / Commit 18: shared kwargs-splat helper.
    inputs = build_investment_projection_inputs(
        account.id, investment_params, adapted_deductions, acct_contributions,
        all_periods, year_periods[0], salary_gross_biweekly,
    )

    # Determine anchor position relative to the year.
    anchor_idx = _get_anchor_period_index(account, all_periods)
    first_year_idx = year_periods[0].period_index

    if anchor_idx is not None and anchor_idx > first_year_idx:
        # Pre-anchor gap: the balance calculator has no data for
        # periods before the anchor.  Reverse-project from the anchor
        # balance to derive the January 1 starting balance.
        anchor_pid = account.current_anchor_period_id
        anchor_bal = (
            balances.get(anchor_pid, account.current_anchor_balance or ZERO)
            if balances else account.current_anchor_balance or ZERO
        )

        # Include all periods from the start of the year through the
        # anchor so the reverse traverses every intervening period.
        reverse_periods = [
            p for p in all_periods
            if first_year_idx <= p.period_index <= anchor_idx
        ]

        reversed_proj = growth_engine.reverse_project_balance(
            anchor_balance=anchor_bal,
            assumed_annual_return=investment_params.assumed_annual_return,
            periods=reverse_periods,
            periodic_contribution=inputs.periodic_contribution,
            employer_params=inputs.employer_params,
        )
        jan1_bal = (
            reversed_proj[0].start_balance if reversed_proj else ZERO
        )
    else:
        # No pre-anchor gap -- the balance map covers January.
        jan1_bal = _lookup_period_balance(
            balances, year, 1, all_periods,
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
        periodic_contribution=inputs.periodic_contribution,
        employer_params=inputs.employer_params,
        annual_contribution_limit=inputs.annual_contribution_limit,
        ytd_contributions_start=ZERO,
        contributions=contributions,
    )

    if not projection:
        return jan1_bal, jan1_bal, ZERO, ZERO

    dec31_bal = projection[-1].end_balance
    employer_total = sum(
        (pb.employer_contribution for pb in projection), ZERO,
    )
    growth_total = sum((pb.growth for pb in projection), ZERO)

    return jan1_bal, dec31_bal, employer_total, growth_total


def _lookup_period_balance(
    balances: dict | None,
    year: int,
    month: int,
    all_periods: list,
) -> Decimal:
    """Look up the balance at the end of a specific month.

    Finds the last pay period ending on or before the month's last day
    and returns its balance from the balance map.

    Args:
        balances: period_id -> Decimal balance map, or None.
        year: Calendar year.
        month: Month number (1-12).
        all_periods: All user pay periods.

    Returns:
        Decimal balance, or ZERO if no matching period.
    """
    if not balances:
        return ZERO

    last_day = date(
        year, month, calendar.monthrange(year, month)[1],
    )
    target_period = _find_period_on_or_before_date(
        last_day, all_periods,
    )
    if target_period is None:
        return ZERO
    return balances.get(target_period.id, ZERO)


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


# ── Amortization Schedule Helpers ────────────────────────────────


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
            params, anchor_events, ctx.payments,
            ctx.rate_changes, today,
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
            ``replay_confirmed_history`` + ``project_forward`` (or any
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


def _build_investment_balance_map(
    account: Account,
    investment_params: InvestmentParams,
    scenario: Scenario,
    periods: list,
    *,
    deductions_by_account: dict,
    salary_gross_biweekly: Decimal,
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
        deductions_by_account: MED-01 / S6-06: explicit narrow
            replacement for the previous 11-key ``ctx`` opaque bag.
            Account-id -> :class:`PaycheckDeduction` list mapping
            used to derive the contribution feed.
        salary_gross_biweekly: The active salary profile's biweekly
            gross, used by
            :func:`calculate_investment_inputs`.

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

    anchor_pid = account.current_anchor_period_id
    anchor_balance = base_balances.get(anchor_pid, ZERO)

    # Find the anchor period's index to split pre/post-anchor.
    anchor_idx = _get_anchor_period_index(account, periods)
    if anchor_idx is None:
        return base_balances

    pre_anchor = [
        p for p in periods if p.period_index < anchor_idx
    ]
    post_anchor = [
        p for p in periods if p.period_index > anchor_idx
    ]

    if not pre_anchor and not post_anchor:
        return base_balances

    # Adapt paycheck deductions and compute projection inputs.
    # Pre-Commit-28 this helper read both keys off a whole 11-key
    # ``ctx`` (S6-06 in ``06_dry_solid.md``); they are now explicit
    # parameters declared in the signature.
    scenario_id = scenario.id

    acct_deductions = deductions_by_account.get(account.id, [])
    adapted_deductions = adapt_deductions(acct_deductions)

    # Shadow income transactions for contribution history.
    # Status filter routes through ``balance_excluded_status_ids``
    # (D6-09 / MED-02); see ``_compute_account_contributions``
    # earlier in this module.
    post_period_ids = [p.id for p in post_anchor]
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    acct_contributions = (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.status),
            joinedload(Transaction.pay_period),
        )
        .filter(
            Transaction.account_id == account.id,
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id.in_(post_period_ids),
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
            Transaction.is_deleted.is_(False),
            ~Transaction.status_id.in_(balance_excluded_status_ids()),
        )
        .all()
    ) if post_period_ids else []

    current_period = post_anchor[0] if post_anchor else pre_anchor[-1]
    # F-22 / Commit 18: shared kwargs-splat helper.
    inputs = build_investment_projection_inputs(
        account.id, investment_params, adapted_deductions, acct_contributions,
        periods, current_period, salary_gross_biweekly,
    )

    # Forward projection for post-anchor periods.
    proj_by_pid = {}
    if post_anchor:
        projection = growth_engine.project_balance(
            current_balance=anchor_balance,
            assumed_annual_return=investment_params.assumed_annual_return,
            periods=post_anchor,
            periodic_contribution=inputs.periodic_contribution,
            employer_params=inputs.employer_params,
            annual_contribution_limit=inputs.annual_contribution_limit,
            ytd_contributions_start=inputs.ytd_contributions,
        )
        proj_by_pid = {
            pb.period_id: pb.end_balance for pb in projection
        }

    # Reverse projection for pre-anchor periods.  Include the anchor
    # period in the reverse list so reverse_project_balance has the
    # correct endpoint (anchor_balance = end of anchor period).
    rev_by_pid = {}
    if pre_anchor:
        anchor_period = next(
            p for p in periods if p.id == anchor_pid
        )
        reverse_periods = pre_anchor + [anchor_period]
        reversed_proj = growth_engine.reverse_project_balance(
            anchor_balance=anchor_balance,
            assumed_annual_return=investment_params.assumed_annual_return,
            periods=reverse_periods,
            periodic_contribution=inputs.periodic_contribution,
            employer_params=inputs.employer_params,
        )
        rev_by_pid = {
            pb.period_id: pb.end_balance
            for pb in reversed_proj
            if pb.period_id != anchor_pid
        }

    # Merge all three sources.
    result = OrderedDict()
    for period in periods:
        if period.id in proj_by_pid:
            result[period.id] = proj_by_pid[period.id]
        elif period.id in base_balances:
            result[period.id] = base_balances[period.id]
        elif period.id in rev_by_pid:
            result[period.id] = rev_by_pid[period.id]

    return result


# ── Internal Helpers ──────────────────────────────────────────────


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


def _get_anchor_period_index(
    account: Account, all_periods: list,
) -> int | None:
    """Return the period_index of the account's anchor period.

    Args:
        account: Account with current_anchor_period_id set.
        all_periods: All user pay periods.

    Returns:
        int period_index, or None if the anchor period is not found.
    """
    anchor_pid = account.current_anchor_period_id
    if anchor_pid is None:
        return None
    for p in all_periods:
        if p.id == anchor_pid:
            return p.period_index
    return None


def _lookup_balance_with_anchor_fallback(
    balances: dict | None,
    year: int,
    month: int,
    all_periods: list,
    account: Account,
) -> Decimal:
    """Look up balance at a period, falling back to anchor balance for pre-anchor periods.

    Unlike _lookup_period_balance which returns ZERO for pre-anchor
    periods (because calculate_balances skips them), this function
    returns the account's anchor balance when the target period is
    before the anchor -- a closer approximation than ZERO.

    A legitimate ZERO balance at a post-anchor period is returned
    as-is; the fallback only triggers when the target period is
    absent from the balance map AND precedes the anchor.

    Args:
        balances: period_id -> Decimal balance map from
            calculate_balances, or None.
        year: Calendar year.
        month: Month number (1-12).
        all_periods: All user pay periods.
        account: Account with current_anchor_balance and
            current_anchor_period_id.

    Returns:
        Decimal balance.
    """
    if not balances:
        # No balance map at all -- fall back to anchor if available.
        return account.current_anchor_balance or ZERO

    last_day = date(
        year, month, calendar.monthrange(year, month)[1],
    )
    target_period = _find_period_on_or_before_date(
        last_day, all_periods,
    )
    if target_period is None:
        return ZERO

    # Period exists in the balance map -- return its value (even if ZERO).
    if target_period.id in balances:
        return balances[target_period.id]

    # Period is NOT in the balance map.  If it precedes the anchor,
    # the anchor balance is the best available approximation.
    anchor_pid = account.current_anchor_period_id
    anchor_period = next(
        (p for p in all_periods if p.id == anchor_pid), None,
    )
    if anchor_period and target_period.period_index < anchor_period.period_index:
        return account.current_anchor_balance or ZERO

    return ZERO


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
    from app.services.interest_projection import calculate_interest  # pylint: disable=import-outside-toplevel

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


def _get_settled_status_ids() -> list[int]:
    """Return status IDs that represent settled transactions."""
    return [
        ref_cache.status_id(StatusEnum.DONE),
        ref_cache.status_id(StatusEnum.RECEIVED),
        ref_cache.status_id(StatusEnum.SETTLED),
    ]


def _get_account_balance_map(
    account: Account,
    scenario: Scenario,
    periods: list,
    debt_schedules: dict[int, list] | None = None,
    *,
    investment_params_map: dict | None = None,
    deductions_by_account: dict | None = None,
    salary_gross_biweekly: Decimal | None = None,
) -> dict | None:
    """Compute period_id -> balance mapping for one account.

    Dispatches to the correct calculation engine based on account type:
    - Amortizing loans: pre-generated amortization schedule
    - Interest-bearing (HYSA, CD, etc.): balance calculator with interest
    - Investment (401k, IRA, etc.): growth engine with employer and returns
    - Everything else: plain balance calculator

    Args:
        account: The account to project.
        scenario: The baseline scenario.
        periods: All user pay periods.
        debt_schedules: Optional account_id -> list[AmortizationRow]
            mapping.  When provided and the account is a debt account,
            balances are derived from the amortization schedule.
        investment_params_map: MED-01 / S6-06: replaces the previous
            ``ctx`` bag.  When ``None`` (or the account has no row in
            the map) the investment branch falls through to the
            canonical balance resolver.
        deductions_by_account: Payroll-deduction map forwarded to the
            investment-balance-map builder.
        salary_gross_biweekly: Forwarded for the same reason.

    Returns:
        OrderedDict mapping period_id to Decimal balance, or None
        if the account has no anchor period.
    """
    if account.current_anchor_period_id is None:
        return None

    # MED-01 / S6-03: single flag-driven classifier replaces the
    # divergent branch ladders that used to express the same
    # taxonomy two different ways here and in
    # ``savings_dashboard_service._compute_account_projections``.
    # Adding a new parameterised type now requires extending
    # :class:`AccountProjectionKind` in one place, not patching two
    # branch ladders with different keying conventions.
    kind = classify_account(account)

    # Amortizing loan accounts: use pre-generated schedule when available.
    # Routed through the loan-side resolver in Commit 15; this commit
    # only handles checking-style reads.
    if (kind is AccountProjectionKind.AMORTIZING
            and debt_schedules and account.id in debt_schedules):
        params = (
            db.session.query(LoanParams)
            .filter_by(account_id=account.id)
            .first()
        )
        original = params.original_principal if params else ZERO
        # F-21 / Commit 19: route through the shared
        # ``compute_loan_period_balance_map`` so the year-end
        # liability column and the savings-dashboard loan card
        # consume the same period-end-keyed balance derivation.
        return compute_loan_period_balance_map(
            debt_schedules[account.id], periods, original,
        )

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

    # Investment accounts: use growth engine when the projection
    # inputs are available.  The base balance feeding the projection
    # comes from the canonical entries-aware producer (E-25 / CRIT-01
    # / R-1).
    if (kind is AccountProjectionKind.INVESTMENT
            and investment_params_map is not None):
        inv_params = investment_params_map.get(account.id)
        if inv_params:
            return _build_investment_balance_map(
                account, inv_params, scenario, periods,
                deductions_by_account=deductions_by_account or {},
                salary_gross_biweekly=(
                    salary_gross_biweekly
                    if salary_gross_biweekly is not None
                    else ZERO
                ),
            )

    # Standard checking/savings (and any unmatched types) route through
    # the canonical entries-aware producer (E-25 / CRIT-01 / F-009 /
    # R-1: Commit 8).  ``balances_for`` owns the transaction query with
    # ``selectinload(Transaction.entries)`` and resolves the anchor via
    # the dated ``AccountAnchorHistory`` SoT, so the net-worth aggregate
    # cannot disagree with the grid for the same input.
    return balance_resolver.balances_for(
        account, scenario.id, periods,
    ).balances


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
