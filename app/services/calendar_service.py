"""
Shekel Budget App -- Calendar Service

Groups transactions by calendar month and day, computes per-month
income/expense/net totals, detects 3rd-paycheck months, flags
large/infrequent transactions, and projects month-end balances.

Pure-function service -- no Flask imports, no database writes.
"""

import calendar
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import AcctTypeEnum, RecurrencePatternEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import balance_calculator

logger = logging.getLogger(__name__)

# Recurrence patterns considered "infrequent" -- less frequent than monthly.
_INFREQUENT_PATTERNS = frozenset({
    RecurrencePatternEnum.QUARTERLY,
    RecurrencePatternEnum.SEMI_ANNUAL,
    RecurrencePatternEnum.ANNUAL,
    RecurrencePatternEnum.ONCE,
})


@dataclass(frozen=True)
class DayEntry:
    """A single transaction's representation on a calendar day."""

    transaction_id: int
    name: str
    amount: Decimal
    is_income: bool
    is_paid: bool
    is_large: bool
    is_infrequent: bool
    category_group: str | None
    category_item: str | None
    due_date: date | None


@dataclass(frozen=True)
class MonthSummary:
    """Aggregated data for one calendar month."""

    year: int
    month: int
    total_income: Decimal
    total_expenses: Decimal
    net: Decimal
    projected_end_balance: Decimal
    is_third_paycheck_month: bool
    large_transactions: list[DayEntry]
    day_entries: dict[int, list[DayEntry]]
    paycheck_days: list[int]


@dataclass(frozen=True)
class YearOverview:
    """12-month year overview data."""

    year: int
    months: list[MonthSummary]
    annual_income: Decimal
    annual_expenses: Decimal
    annual_net: Decimal


def get_month_detail(
    user_id: int,
    year: int,
    month: int,
    account_id: int | None = None,
    large_threshold: int = 500,
) -> MonthSummary:
    """Compute calendar data for a single month.

    Queries transactions for pay periods that overlap the given month,
    assigns each transaction to a calendar day via due_date (falling
    back to pay period start_date), and computes income/expense totals,
    projected month-end balance, and large/infrequent flags.

    Args:
        user_id: The user's ID.
        year: Calendar year.
        month: Calendar month (1-12).
        account_id: Account to scope transactions to.  Defaults to
            the user's first active checking account.
        large_threshold: Amount at or above which a transaction is
            flagged as large.

    Returns:
        A MonthSummary with day-level and aggregate data.
    """
    account = _resolve_account(user_id, account_id)
    if account is None:
        return _empty_month(year, month)

    scenario = _get_baseline_scenario(user_id)
    if scenario is None:
        return _empty_month(year, month)

    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])

    periods = _get_overlapping_periods(user_id, first_day, last_day)
    transactions = _query_transactions_for_range(
        account.id, scenario.id, user_id, first_day, last_day,
    )

    return _build_month_summary(
        year, month, account, periods, transactions,
        large_threshold, user_id, scenario,
    )


def get_year_overview(
    user_id: int,
    year: int,
    account_id: int | None = None,
    large_threshold: int = 500,
) -> YearOverview:
    """Compute 12-month overview for a calendar year.

    Fetches all transactions for the year in a single query, then
    partitions by month in Python to avoid 12 database round trips.

    Args:
        user_id: The user's ID.
        year: Calendar year.
        account_id: Account to scope to.  Defaults to checking.
        large_threshold: Large transaction threshold.

    Returns:
        A YearOverview with 12 MonthSummary entries (Jan-Dec).
    """
    account = _resolve_account(user_id, account_id)
    if account is None:
        return _empty_year(year)

    scenario = _get_baseline_scenario(user_id)
    if scenario is None:
        return _empty_year(year)

    first_day = date(year, 1, 1)
    last_day = date(year, 12, 31)
    periods = _get_overlapping_periods(user_id, first_day, last_day)
    all_txns = _query_transactions_for_range(
        account.id, scenario.id, user_id, first_day, last_day,
    )

    months = [
        _build_month_summary(
            year, m, account, periods, all_txns,
            large_threshold, user_id, scenario,
        )
        for m in range(1, 13)
    ]

    annual_income = sum(ms.total_income for ms in months)
    annual_expenses = sum(ms.total_expenses for ms in months)

    return YearOverview(
        year=year,
        months=months,
        annual_income=annual_income,
        annual_expenses=annual_expenses,
        annual_net=annual_income - annual_expenses,
    )


# ── Internal helpers ────────────────────────────────────────────────


def _resolve_account(
    user_id: int,
    account_id: int | None,
) -> Account | None:
    """Return the account to scope calendar queries to.

    If account_id is given, verifies ownership and returns it.
    Otherwise falls back to the user's first active checking account.
    Returns None if no suitable account exists.
    """
    if account_id is not None:
        acct = db.session.get(Account, account_id)
        if acct and acct.user_id == user_id and acct.is_active:
            return acct
        return None

    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    return (
        db.session.query(Account)
        .filter_by(
            user_id=user_id,
            is_active=True,
            account_type_id=checking_type_id,
        )
        .order_by(Account.sort_order, Account.id)
        .first()
    )


def _get_baseline_scenario(user_id: int) -> Scenario | None:
    """Load the user's baseline scenario."""
    return (
        db.session.query(Scenario)
        .filter_by(user_id=user_id, is_baseline=True)
        .first()
    )


def _get_overlapping_periods(
    user_id: int,
    first_day: date,
    last_day: date,
) -> list[PayPeriod]:
    """Return pay periods that overlap the given date range.

    A period overlaps if start_date <= last_day AND end_date >= first_day.
    """
    return (
        db.session.query(PayPeriod)
        .filter(
            PayPeriod.user_id == user_id,
            PayPeriod.start_date <= last_day,
            PayPeriod.end_date >= first_day,
        )
        .order_by(PayPeriod.period_index)
        .all()
    )


def _query_transactions_for_range(
    account_id: int,
    scenario_id: int,
    user_id: int,
    first_day: date,
    last_day: date,
) -> list[Transaction]:
    """Load transactions that belong to the given date range.

    Includes transactions via two paths to avoid missing those whose
    due_date falls outside their pay period's date range:
      1. Transactions with due_date in [first_day, last_day].
      2. Transactions with no due_date in periods overlapping the range
         (fallback assignment uses the period's start_date).

    Eager-loads category, status, template -> recurrence_rule, and
    pay_period to prevent N+1 queries downstream.
    """
    overlapping = _get_overlapping_periods(user_id, first_day, last_day)
    period_ids = [p.id for p in overlapping]

    return (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.category),
            joinedload(Transaction.status),
            joinedload(Transaction.template).joinedload(
                TransactionTemplate.recurrence_rule,
            ),
            joinedload(Transaction.pay_period),
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.is_deleted.is_(False),
            or_(
                Transaction.due_date.between(first_day, last_day),
                Transaction.due_date.is_(None) & Transaction.pay_period_id.in_(
                    period_ids if period_ids else [-1],
                ),
            ),
        )
        .all()
    )


def _build_day_entry(
    txn: Transaction,
    income_type_id: int,
    threshold: Decimal,
) -> DayEntry:
    """Create a DayEntry from a transaction.

    Args:
        txn: The transaction to convert.
        income_type_id: Ref ID for the Income transaction type.
        threshold: Amount at or above which a transaction is large.

    Returns:
        A frozen DayEntry dataclass.
    """
    amount = txn.effective_amount
    return DayEntry(
        transaction_id=txn.id,
        name=txn.name,
        amount=amount,
        is_income=txn.transaction_type_id == income_type_id,
        is_paid=bool(txn.status and txn.status.is_settled),
        is_large=abs(amount) >= threshold,
        is_infrequent=_is_infrequent(txn),
        category_group=txn.category.group_name if txn.category else None,
        category_item=txn.category.item_name if txn.category else None,
        due_date=txn.due_date,
    )


def _assign_transactions_to_days(
    transactions: list[Transaction],
    year: int,
    month: int,
    large_threshold: int,
) -> tuple[dict[int, list[DayEntry]], Decimal, Decimal]:
    """Assign transactions to calendar days and compute totals.

    Returns the day_map, total_income, and total_expenses for the
    target month.  Deduplicates by transaction ID to prevent
    double-counting when periods overlap month boundaries.
    """
    threshold = Decimal(str(large_threshold))
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

    seen_ids: set[int] = set()
    day_map: dict[int, list[DayEntry]] = defaultdict(list)
    total_income = Decimal("0")
    total_expenses = Decimal("0")

    for txn in transactions:
        if txn.id in seen_ids:
            continue
        display_day = _get_display_day(txn, month, year)
        if display_day is None:
            continue

        seen_ids.add(txn.id)
        entry = _build_day_entry(txn, income_type_id, threshold)
        day_map[display_day].append(entry)

        if entry.is_income:
            total_income += entry.amount
        else:
            total_expenses += abs(entry.amount)

    # Sort each day's entries by abs(amount) descending.
    for day in day_map:
        day_map[day].sort(key=lambda e: abs(e.amount), reverse=True)

    return dict(day_map), total_income, total_expenses


def _build_month_summary(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    year: int,
    month: int,
    account: Account,
    periods: list[PayPeriod],
    transactions: list[Transaction],
    large_threshold: int,
    user_id: int,
    scenario: Scenario,
) -> MonthSummary:
    """Assemble a MonthSummary from pre-queried data.

    Assigns each transaction to a calendar day via _get_display_day,
    deduplicating by transaction ID to prevent double-counting when
    periods overlap month boundaries.

    Args:
        year: Target calendar year.
        month: Target calendar month (1-12).
        account: The account being summarized.
        periods: All periods overlapping the date range.
        transactions: All transactions across those periods.
        large_threshold: Amount threshold for large flags.
        user_id: The user's ID (for balance calculation).
        scenario: The baseline scenario.

    Returns:
        A MonthSummary for the target month.
    """
    day_entries, total_income, total_expenses = _assign_transactions_to_days(
        transactions, year, month, large_threshold,
    )

    large_txns = [
        e for entries in day_entries.values() for e in entries if e.is_large
    ]

    end_balance = _compute_month_end_balance(account, year, month, user_id, scenario)
    third_paycheck_months = _detect_third_paycheck_months(periods, year)

    paycheck_days = sorted({
        p.start_date.day
        for p in periods
        if p.start_date.year == year and p.start_date.month == month
    })

    return MonthSummary(
        year=year,
        month=month,
        total_income=total_income,
        total_expenses=total_expenses,
        net=total_income - total_expenses,
        projected_end_balance=end_balance,
        is_third_paycheck_month=month in third_paycheck_months,
        large_transactions=large_txns,
        day_entries=day_entries,
        paycheck_days=paycheck_days,
    )


def _get_display_day(
    txn: Transaction,
    target_month: int,
    target_year: int,
) -> int | None:
    """Determine the calendar day to display a transaction on.

    Returns the day-of-month if the transaction belongs in the target
    month, or None if it does not (preventing double-counting across
    month boundaries).

    Primary: txn.due_date -- assigned by the recurrence engine.
    Fallback: txn.pay_period.start_date (for transactions without
    a due_date, which should be rare after the Commit 2 backfill).
    """
    if txn.due_date is not None:
        if txn.due_date.month == target_month and txn.due_date.year == target_year:
            return txn.due_date.day
        return None

    # Fallback to pay period start_date.
    start = txn.pay_period.start_date
    if start.month == target_month and start.year == target_year:
        return start.day
    return None


def _is_infrequent(txn: Transaction) -> bool:
    """Check if a transaction's recurrence is less frequent than monthly.

    Returns True for Quarterly, Semi-Annual, Annual, and Once patterns.
    Returns False for Every Period, Every N Periods, Monthly, Monthly
    First, and for transactions with no template or no recurrence rule.
    """
    if txn.template is None:
        return False
    rule = txn.template.recurrence_rule
    if rule is None:
        return False

    for pattern_enum in _INFREQUENT_PATTERNS:
        if rule.pattern_id == ref_cache.recurrence_pattern_id(pattern_enum):
            return True
    return False


def _detect_third_paycheck_months(
    periods: list[PayPeriod],
    year: int,
) -> set[int]:
    """Identify months with 3+ pay period start_dates in the given year.

    Standard biweekly pay produces exactly 2 such months per year.
    """
    month_counts: dict[int, int] = defaultdict(int)
    for p in periods:
        if p.start_date.year == year:
            month_counts[p.start_date.month] += 1

    return {m for m, count in month_counts.items() if count >= 3}


def _compute_month_end_balance(
    account: Account,
    year: int,
    month: int,
    user_id: int,
    scenario: Scenario,
) -> Decimal:
    """Compute the projected balance at the end of the given month.

    Finds the last pay period whose end_date is in or before the target
    month-end, runs the balance calculator, and returns the projected
    end balance for that period.  Returns Decimal("0") if no suitable
    period or anchor exists.
    """
    if account.current_anchor_period_id is None:
        return Decimal("0")

    last_day = date(year, month, calendar.monthrange(year, month)[1])

    all_periods = (
        db.session.query(PayPeriod)
        .filter_by(user_id=user_id)
        .order_by(PayPeriod.period_index)
        .all()
    )

    # Find the last period whose end_date <= last_day of month.
    target_period = None
    for p in all_periods:
        if p.end_date <= last_day:
            target_period = p

    if target_period is None:
        return Decimal("0")

    period_ids = [p.id for p in all_periods]
    all_txns = (
        db.session.query(Transaction)
        .filter(
            Transaction.account_id == account.id,
            Transaction.scenario_id == scenario.id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
        )
        .all()
    )

    balances, _ = balance_calculator.calculate_balances(
        account.current_anchor_balance,
        account.current_anchor_period_id,
        all_periods,
        all_txns,
    )

    return balances.get(target_period.id, Decimal("0"))


def _empty_month(year: int, month: int) -> MonthSummary:
    """Return a MonthSummary with zero totals and empty collections."""
    return MonthSummary(
        year=year,
        month=month,
        total_income=Decimal("0"),
        total_expenses=Decimal("0"),
        net=Decimal("0"),
        projected_end_balance=Decimal("0"),
        is_third_paycheck_month=False,
        large_transactions=[],
        day_entries={},
        paycheck_days=[],
    )


def _empty_year(year: int) -> YearOverview:
    """Return a YearOverview with 12 empty MonthSummaries."""
    return YearOverview(
        year=year,
        months=[_empty_month(year, m) for m in range(1, 13)],
        annual_income=Decimal("0"),
        annual_expenses=Decimal("0"),
        annual_net=Decimal("0"),
    )
