"""
Shekel Budget App — Chart Data Service

Orchestrates existing services to reshape data into chart-ready dicts
(labels + datasets) for the Charts dashboard. Does not duplicate
business logic — calls balance_calculator, amortization_engine,
growth_engine, paycheck_calculator, etc. and transforms their output.

All methods return plain Python dicts suitable for Jinja2 ``tojson``.
"""

import logging
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.auto_loan_params import AutoLoanParams
from app.models.category import Category
from app.models.mortgage_params import MortgageParams
from app.models.pay_period import PayPeriod
from app.models.salary_profile import SalaryProfile
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.ref import AccountType, Status
from app.services import (
    amortization_engine,
    balance_calculator,
    paycheck_calculator,
)

logger = logging.getLogger(__name__)

# Mapping of account type categories to y-axis assignment for dual-axis.
_LEFT_AXIS_CATEGORIES = {"asset"}

# Named period range → count of periods to look back from current.
_RANGE_LOOKBACK = {
    "last_3": 2,
    "last_6": 5,
    "last_12": 11,
}


def _empty_chart():
    """Return an empty chart data structure.

    Returns:
        dict: Empty labels and datasets.
    """
    return {"labels": [], "datasets": []}


def _get_baseline_scenario(user_id):
    """Load the user's baseline scenario.

    Args:
        user_id (int): The user's ID.

    Returns:
        Scenario: Baseline scenario or None.
    """
    return (
        db.session.query(Scenario)
        .filter_by(user_id=user_id, is_baseline=True)
        .first()
    )


def _get_periods(user_id, start=None, end=None):
    """Load pay periods for a user, optionally filtered by date range.

    Args:
        user_id (int): The user's ID.
        start (str): Optional start date string (YYYY-MM-DD).
        end (str): Optional end date string (YYYY-MM-DD).

    Returns:
        list: PayPeriod objects ordered by start_date.
    """
    query = (
        db.session.query(PayPeriod)
        .filter_by(user_id=user_id)
        .order_by(PayPeriod.start_date)
    )
    if start:
        query = query.filter(PayPeriod.start_date >= start)
    if end:
        query = query.filter(PayPeriod.end_date <= end)
    return query.all()


def _find_current_period_index(all_periods, today):
    """Find the index of the current or most recent period.

    Args:
        all_periods (list): All pay periods ordered by start_date.
        today (date): Reference date.

    Returns:
        int: Index of the current or most recent period.
    """
    current_idx = 0
    for i, period in enumerate(all_periods):
        if period.start_date <= today <= period.end_date:
            return i
        if period.end_date < today:
            current_idx = i
    return current_idx


def _get_period_range(user_id, period_range):
    """Get pay periods based on a named range relative to the current date.

    Args:
        user_id (int): The user's ID.
        period_range (str): One of 'current', 'last_3', 'last_6',
            'last_12', 'ytd'.

    Returns:
        list: PayPeriod objects.
    """
    today = date.today()
    all_periods = (
        db.session.query(PayPeriod)
        .filter_by(user_id=user_id)
        .order_by(PayPeriod.start_date)
        .all()
    )
    if not all_periods:
        return []

    current_idx = _find_current_period_index(all_periods, today)

    # Handle lookback ranges (last_3, last_6, last_12).
    lookback = _RANGE_LOOKBACK.get(period_range)
    if lookback is not None:
        start_idx = max(0, current_idx - lookback)
        return all_periods[start_idx:current_idx + 1]

    if period_range == "ytd":
        year_start = date(today.year, 1, 1)
        return [p for p in all_periods
                if year_start <= p.start_date <= today]

    # Default: current period.
    return [all_periods[current_idx]]


def _format_period_label(period):
    """Format a pay period as a short date label.

    Args:
        period (PayPeriod): Pay period object.

    Returns:
        str: Formatted label like 'Jan 02'.
    """
    return period.start_date.strftime("%b %d")


def _calculate_account_balances(account, scenario, periods):
    """Calculate projected balances for a single account.

    Args:
        account (Account): The account to project.
        scenario (Scenario): The baseline scenario.
        periods (list): Pay periods to project across.

    Returns:
        dict: Mapping of period_id to Decimal balance, or None if skipped.
    """
    if account.current_anchor_period_id is None:
        return None

    period_ids = [p.id for p in periods]

    # Load transactions for this account.
    transactions = (
        db.session.query(Transaction)
        .filter_by(scenario_id=scenario.id)
        .filter(Transaction.pay_period_id.in_(period_ids))
        .filter(
            db.or_(
                Transaction.template.has(account_id=account.id),
                Transaction.is_override.is_(True),
            )
        )
        .all()
    )

    transfers = (
        db.session.query(Transfer)
        .filter_by(scenario_id=scenario.id)
        .filter(Transfer.pay_period_id.in_(period_ids))
        .all()
    )

    acct_type = account.account_type.name if account.account_type else ""
    base_args = {
        "anchor_balance": account.current_anchor_balance,
        "anchor_period_id": account.current_anchor_period_id,
        "periods": periods,
        "transactions": transactions,
        "transfers": transfers,
        "account_id": account.id,
    }

    if acct_type == "hysa" and account.hysa_params:
        balances, _ = balance_calculator.calculate_balances_with_interest(
            **base_args, hysa_params=account.hysa_params,
        )
        return balances

    if acct_type in ("mortgage", "auto_loan"):
        loan_params = _get_loan_params(account)
        if loan_params:
            balances, _ = balance_calculator.calculate_balances_with_amortization(
                **base_args, loan_params=loan_params,
            )
            return balances

    return balance_calculator.calculate_balances(**base_args)


def _get_loan_params(account):
    """Load loan parameters for a mortgage or auto loan account.

    Args:
        account (Account): The loan account.

    Returns:
        MortgageParams or AutoLoanParams, or None.
    """
    acct_type = account.account_type.name if account.account_type else ""
    if acct_type == "mortgage":
        return (
            db.session.query(MortgageParams)
            .filter_by(account_id=account.id)
            .first()
        )
    if acct_type == "auto_loan":
        return (
            db.session.query(AutoLoanParams)
            .filter_by(account_id=account.id)
            .first()
        )
    return None


# ── C1: Balance Over Time ──────────────────────────────────────────


def get_balance_over_time(user_id, account_ids=None, start=None, end=None):
    """Build multi-line chart data for account balances over time.

    Args:
        user_id (int): The user's ID.
        account_ids (list): Optional list of account IDs to include.
        start (str): Optional start date string (YYYY-MM-DD).
        end (str): Optional end date string (YYYY-MM-DD).

    Returns:
        dict: Keys: labels, datasets, accounts.
    """
    periods = _get_periods(user_id, start, end)
    if not periods:
        return _empty_chart()

    scenario = _get_baseline_scenario(user_id)
    if not scenario:
        return _empty_chart()

    accounts = (
        db.session.query(Account)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    if account_ids:
        accounts = [a for a in accounts if a.id in account_ids]
    if not accounts:
        return _empty_chart()

    labels = [_format_period_label(p) for p in periods]
    datasets = []
    all_accounts_info = []

    for account in accounts:
        category = account.account_type.category if account.account_type else "asset"
        axis = "y" if category in _LEFT_AXIS_CATEGORIES else "y1"

        balances = _calculate_account_balances(account, scenario, periods)
        if balances is None:
            continue

        data = [float(balances.get(p.id, Decimal("0"))) for p in periods]
        datasets.append({
            "label": account.name, "data": data,
            "account_id": account.id, "axis": axis,
        })
        all_accounts_info.append({
            "id": account.id, "name": account.name, "category": category,
        })

    return {"labels": labels, "datasets": datasets, "accounts": all_accounts_info}


# ── C2: Spending by Category ──────────────────────────────────────


def _get_expense_transactions(scenario_id, period_ids, status_filter=None):
    """Load expense transactions for given periods.

    Args:
        scenario_id (int): The scenario ID.
        period_ids (list): List of pay period IDs.
        status_filter (list): Optional list of status IDs to filter by.

    Returns:
        list: Transaction objects.
    """
    query = (
        db.session.query(Transaction)
        .join(Category, Transaction.category_id == Category.id)
        .filter(
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.transaction_type.has(name="expense"),
            Transaction.is_deleted.is_(False),
        )
    )
    if status_filter:
        query = query.filter(Transaction.status_id.in_(status_filter))
    return query.all()


def get_spending_by_category(user_id, period_range="current"):
    """Build horizontal bar chart data for spending grouped by category.

    Only includes expense transactions with status 'done' or 'projected'.

    Args:
        user_id (int): The user's ID.
        period_range (str): Named range ('current', 'last_3', 'last_6',
            'last_12', 'ytd').

    Returns:
        dict: Keys: labels, data.
    """
    empty = {"labels": [], "data": []}

    periods = _get_period_range(user_id, period_range)
    if not periods:
        return empty

    scenario = _get_baseline_scenario(user_id)
    if not scenario:
        return empty

    done = db.session.query(Status).filter_by(name="done").first()
    projected = db.session.query(Status).filter_by(name="projected").first()
    if not done or not projected:
        return empty

    transactions = _get_expense_transactions(
        scenario.id, [p.id for p in periods],
        status_filter=[done.id, projected.id],
    )

    # Group by category group_name and sum.
    totals = {}
    for txn in transactions:
        group = txn.category.group_name if txn.category else "Uncategorized"
        amount = txn.actual_amount if txn.actual_amount is not None else txn.estimated_amount
        if amount:
            totals[group] = totals.get(group, Decimal("0")) + amount

    sorted_groups = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    return {
        "labels": [g[0] for g in sorted_groups],
        "data": [float(g[1]) for g in sorted_groups],
    }


# ── C3: Budget vs. Actuals ────────────────────────────────────────


def get_budget_vs_actuals(user_id, period_range="current"):
    """Build grouped bar chart data comparing estimated vs. actual amounts.

    Args:
        user_id (int): The user's ID.
        period_range (str): Named range ('current', 'last_3', 'last_6',
            'last_12', 'ytd').

    Returns:
        dict: Keys: labels, estimated, actual.
    """
    empty = {"labels": [], "estimated": [], "actual": []}

    periods = _get_period_range(user_id, period_range)
    if not periods:
        return empty

    scenario = _get_baseline_scenario(user_id)
    if not scenario:
        return empty

    transactions = _get_expense_transactions(
        scenario.id, [p.id for p in periods],
    )

    estimated_totals = {}
    actual_totals = {}
    for txn in transactions:
        group = txn.category.group_name if txn.category else "Uncategorized"
        estimated_totals[group] = (
            estimated_totals.get(group, Decimal("0"))
            + (txn.estimated_amount or Decimal("0"))
        )
        actual_totals[group] = (
            actual_totals.get(group, Decimal("0"))
            + (txn.actual_amount or Decimal("0"))
        )

    groups = sorted(
        estimated_totals.keys(),
        key=lambda g: estimated_totals[g],
        reverse=True,
    )
    return {
        "labels": groups,
        "estimated": [float(estimated_totals.get(g, Decimal("0"))) for g in groups],
        "actual": [float(actual_totals.get(g, Decimal("0"))) for g in groups],
    }


# ── C4: Amortization Breakdown ────────────────────────────────────


def get_loan_accounts(user_id):
    """Get all mortgage and auto loan accounts for a user.

    Args:
        user_id (int): The user's ID.

    Returns:
        list: Dicts with id, name, and type.
    """
    accounts = (
        db.session.query(Account)
        .join(AccountType, Account.account_type_id == AccountType.id)
        .filter(
            Account.user_id == user_id,
            Account.is_active.is_(True),
            AccountType.name.in_(["mortgage", "auto_loan"]),
        )
        .order_by(Account.name)
        .all()
    )
    return [{"id": a.id, "name": a.name, "type": a.account_type.name}
            for a in accounts]


def _find_loan_account(user_id, account_id=None):
    """Find a specific or default loan account.

    Args:
        user_id (int): The user's ID.
        account_id (int): Optional specific account ID.

    Returns:
        Account: The loan account or None.
    """
    if account_id:
        account = db.session.get(Account, account_id)
        if account and account.user_id == user_id:
            return account
        return None
    return (
        db.session.query(Account)
        .join(AccountType, Account.account_type_id == AccountType.id)
        .filter(
            Account.user_id == user_id,
            Account.is_active.is_(True),
            AccountType.name.in_(["mortgage", "auto_loan"]),
        )
        .order_by(Account.name)
        .first()
    )


def get_amortization_breakdown(user_id, account_id=None):
    """Build stacked area chart data for principal vs. interest.

    Args:
        user_id (int): The user's ID.
        account_id (int): Optional specific loan account ID.

    Returns:
        dict: Keys: labels, principal, interest, account_name.
    """
    empty = {"labels": [], "principal": [], "interest": [], "account_name": ""}

    account = _find_loan_account(user_id, account_id)
    if not account:
        return empty

    params = _get_loan_params(account)
    if not params:
        return empty

    remaining_months = amortization_engine.calculate_remaining_months(
        params.origination_date, params.term_months,
    )
    if remaining_months <= 0:
        return empty

    schedule = amortization_engine.generate_schedule(
        Decimal(str(params.current_principal)),
        Decimal(str(params.interest_rate)),
        remaining_months,
        payment_day=params.payment_day,
    )

    return {
        "labels": [row.payment_date.strftime("%b %Y") for row in schedule],
        "principal": [float(row.principal) for row in schedule],
        "interest": [float(row.interest) for row in schedule],
        "account_name": account.name,
    }


# ── C5: Net Worth Over Time ───────────────────────────────────────


def get_net_worth_over_time(user_id, start=None, end=None):
    """Build a single-line chart of total assets minus liabilities.

    Args:
        user_id (int): The user's ID.
        start (str): Optional start date string.
        end (str): Optional end date string.

    Returns:
        dict: Keys: labels, data.
    """
    balance_data = get_balance_over_time(user_id=user_id, start=start, end=end)

    if not balance_data["labels"]:
        return {"labels": [], "data": []}

    num_points = len(balance_data["labels"])
    net_worth = [0.0] * num_points

    for ds in balance_data["datasets"]:
        acct_info = next(
            (a for a in balance_data.get("accounts", [])
             if a["id"] == ds["account_id"]),
            None,
        )
        category = acct_info["category"] if acct_info else "asset"
        sign = -1 if category == "liability" else 1

        for i, val in enumerate(ds["data"]):
            net_worth[i] += sign * val

    return {
        "labels": balance_data["labels"],
        "data": [round(v, 2) for v in net_worth],
    }


# ── C6: Net Pay Trajectory ────────────────────────────────────────


def get_salary_profiles(user_id):
    """Get all active salary profiles for a user.

    Args:
        user_id (int): The user's ID.

    Returns:
        list: Dicts with id and name.
    """
    profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(SalaryProfile.sort_order, SalaryProfile.name)
        .all()
    )
    return [{"id": p.id, "name": p.name} for p in profiles]


def _load_tax_configs(user_id, profile):
    """Load tax configuration for paycheck calculation.

    Args:
        user_id (int): The user's ID.
        profile (SalaryProfile): Profile with filing_status_id and state_code.

    Returns:
        dict: Keys: bracket_set, state_config, fica_config.
    """
    # pylint: disable=import-outside-toplevel
    from app.models.tax_config import (
        TaxBracketSet,
        StateTaxConfig,
        FicaConfig,
    )

    tax_year = date.today().year

    bracket_set = (
        db.session.query(TaxBracketSet)
        .filter_by(
            user_id=user_id,
            filing_status_id=profile.filing_status_id,
            tax_year=tax_year,
        )
        .first()
    )
    state_config = (
        db.session.query(StateTaxConfig)
        .filter_by(user_id=user_id, state_code=profile.state_code)
        .first()
    )
    fica_config = (
        db.session.query(FicaConfig)
        .filter_by(user_id=user_id, tax_year=tax_year)
        .first()
    )

    return {
        "bracket_set": bracket_set,
        "state_config": state_config,
        "fica_config": fica_config,
    }


def _load_salary_profile(user_id, profile_id=None):
    """Load a salary profile by ID or default to the first active one.

    Args:
        user_id (int): The user's ID.
        profile_id (int): Optional specific profile ID.

    Returns:
        SalaryProfile: The profile or None.
    """
    if profile_id:
        profile = db.session.get(SalaryProfile, profile_id)
        if profile and profile.user_id == user_id:
            return profile
        return None
    return (
        db.session.query(SalaryProfile)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(SalaryProfile.sort_order)
        .first()
    )


def _build_step_data(breakdowns, periods):
    """Build step chart data from paycheck breakdowns.

    Only includes points where net pay changes and the final point.

    Args:
        breakdowns (list): PaycheckBreakdown objects.
        periods (list): PayPeriod objects.

    Returns:
        tuple: (labels, data, gross_data) lists.
    """
    period_map = {p.id: p for p in periods}
    labels = []
    data = []
    gross_data = []
    prev_net = None

    for breakdown in breakdowns:
        net = float(breakdown.net_pay)
        gross = float(breakdown.gross_biweekly)

        if prev_net is None or net != prev_net:
            period = period_map.get(breakdown.period_id)
            if period:
                labels.append(period.start_date.strftime("%b %Y"))
                data.append(net)
                gross_data.append(gross)
                prev_net = net

    # Always include the last data point to close the step.
    if breakdowns and periods:
        last_label = periods[-1].start_date.strftime("%b %Y")
        if not labels or labels[-1] != last_label:
            labels.append(last_label)
            data.append(float(breakdowns[-1].net_pay))
            gross_data.append(float(breakdowns[-1].gross_biweekly))

    return labels, data, gross_data


def get_net_pay_trajectory(user_id, profile_id=None):
    """Build step line chart data for net pay over time with raises.

    Args:
        user_id (int): The user's ID.
        profile_id (int): Optional specific salary profile ID.

    Returns:
        dict: Keys: labels, data, gross_data.
    """
    empty = {"labels": [], "data": [], "gross_data": []}

    profile = _load_salary_profile(user_id, profile_id)
    if not profile:
        return empty

    periods = (
        db.session.query(PayPeriod)
        .filter_by(user_id=user_id)
        .order_by(PayPeriod.start_date)
        .all()
    )
    if not periods:
        return empty

    tax_configs = _load_tax_configs(user_id, profile)

    try:
        breakdowns = paycheck_calculator.project_salary(
            profile, periods, tax_configs,
        )
    except Exception:  # pylint: disable=broad-except
        logger.exception("Failed to project salary for profile %d", profile.id)
        return empty

    labels, data, gross_data = _build_step_data(breakdowns, periods)
    return {"labels": labels, "data": data, "gross_data": gross_data}
