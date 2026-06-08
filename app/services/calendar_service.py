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

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import RecurrencePatternEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services.account_resolver import resolve_analytics_account
from app.services.balance_resolver import balance_as_of_date
from app.services.pay_period_service import get_overlapping_periods
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.balance_predicates import (
    balance_contributing_clause,
    is_balance_contributing,
    monthly_attribution_clause,
)

logger = logging.getLogger(__name__)


class CalendarAccountNotResolvableError(LookupError):
    """Raised when the calendar cannot resolve a backing account or scenario.

    After Commits 3-8 of the main remediation locked the E-19 / CRIT-01
    invariant ("anchor is never NULL; ``resolve_anchor`` raises or
    returns a valid ``AnchorPoint``"), an ``account is None`` /
    ``scenario is None`` outcome from
    :func:`~app.services.account_resolver.resolve_analytics_account` or
    :func:`~app.services.scenario_resolver.get_baseline_scenario`
    indicates an *upstream* defect (deleted analytics account, missing
    baseline scenario), not a normal "empty calendar" state.  Pre-F-2
    the service silently substituted a zeroed :class:`MonthSummary` /
    :class:`YearOverview`, which masked the upstream bug behind a
    ``$0.00`` calendar shown to the user with no error.  Raising
    instead lets the route layer answer with the project-standard 404
    ("404 for both 'not found' and 'not yours'", see
    :mod:`app.utils.auth_helpers`).
    """


# Recurrence patterns considered "infrequent" -- less frequent than monthly.
_INFREQUENT_PATTERNS = frozenset({
    RecurrencePatternEnum.QUARTERLY,
    RecurrencePatternEnum.SEMI_ANNUAL,
    RecurrencePatternEnum.ANNUAL,
    RecurrencePatternEnum.ONCE,
})


@dataclass(frozen=True)
class DayEntry:  # pylint: disable=too-many-instance-attributes
    """A single transaction's representation on a calendar day.

    Pylint: ``too-many-instance-attributes`` (10/7) -- suppressed
    because this is a cohesive value record -- one transaction's row on a
    calendar day -- consumed verbatim by the calendar surface: the CSV
    month export reads the display fields as adjacent columns (folding the
    booleans into single Income/Expense, Status, Large, and Infrequent
    columns), the month-detail table renders name/category/amount and the
    income/paid flags as individual cells, and the route reads
    amount/is_income for day totals.  The two category fields are read as
    independent columns, never as a unit.  Every field is an irreducible
    column of the row; splitting it would fragment one domain concept and
    break every consumer for no design gain.
    """

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
class MonthSummary:  # pylint: disable=too-many-instance-attributes
    """Aggregated data for one calendar month.

    Pylint: ``too-many-instance-attributes`` (9/7) -- suppressed
    because this is a cohesive single-return aggregate -- one calendar
    month's summary -- whose fields are flat columns read together by the
    calendar surface: the month and year templates render the money fields
    and is_third_paycheck_month, and the CSV year export emits them as one
    row per month.  The three money fields are the month's headline
    numbers read individually, not a sub-object read as a unit, so there is
    no section to extract; nesting would fragment one contract across the
    templates and the exporter for no design gain.
    """

    year: int
    month: int
    total_income: Decimal
    total_expenses: Decimal
    net: Decimal
    projected_end_balance: Decimal
    is_third_paycheck_month: bool
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
    account = resolve_analytics_account(user_id, account_id)
    if account is None:
        raise CalendarAccountNotResolvableError(
            f"Analytics account not resolvable for user_id={user_id} "
            f"account_id={account_id} year={year} month={month}",
        )

    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        raise CalendarAccountNotResolvableError(
            f"Baseline scenario not resolvable for user_id={user_id} "
            f"year={year} month={month}",
        )

    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])

    periods = get_overlapping_periods(user_id, first_day, last_day)
    transactions = _query_transactions_for_range(
        account.id, scenario.id, user_id, first_day, last_day,
    )

    ctx = _MonthBuildContext(
        year=year, account=account, periods=periods,
        transactions=transactions, large_threshold=large_threshold,
        scenario=scenario,
    )
    return _build_month_summary(month, ctx)


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
    account = resolve_analytics_account(user_id, account_id)
    if account is None:
        raise CalendarAccountNotResolvableError(
            f"Analytics account not resolvable for user_id={user_id} "
            f"account_id={account_id} year={year}",
        )

    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        raise CalendarAccountNotResolvableError(
            f"Baseline scenario not resolvable for user_id={user_id} "
            f"year={year}",
        )

    first_day = date(year, 1, 1)
    last_day = date(year, 12, 31)
    periods = get_overlapping_periods(user_id, first_day, last_day)
    all_txns = _query_transactions_for_range(
        account.id, scenario.id, user_id, first_day, last_day,
    )

    ctx = _MonthBuildContext(
        year=year, account=account, periods=periods,
        transactions=all_txns, large_threshold=large_threshold,
        scenario=scenario,
    )
    months = [_build_month_summary(m, ctx) for m in range(1, 13)]

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

    Per F-3 / HIGH-02 / W-065, the row-set is constrained by
    :func:`~app.utils.balance_predicates.balance_contributing_clause`
    (``is_deleted=False AND status_id NOT IN (Credit, Cancelled)``)
    rather than the prior inline ``is_deleted=False``-only gate.  This
    is the locked Choice-2 semantic from
    ``remediation_follow_up_plan.md`` Section 2: calendar day cells
    display realized payments at their settled date, so the predicate
    is "balance-contributing" (Projected + Settled, excludes Credit and
    Cancelled) -- intentionally wider than the grid period subtotal's
    Projected-only predicate.  The two surfaces diverge by design.
    """
    # Pylint: ``duplicate-code`` -- the overlapping-periods preamble +
    # eager-loaded ``Transaction`` query below (``get_overlapping_periods``
    # then ``query(Transaction).options(joinedload(category), joinedload(
    # status), ...)``) is incidental SQLAlchemy boilerplate structurally
    # parallel to ``budget_variance_service._query_by_date_range`` (the
    # R0801 preamble cluster).  The genuinely shared logic has already been
    # lifted out: the monthly-attribution business rule into
    # ``monthly_attribution_clause`` (called by both), and both queries
    # apply the IDENTICAL balance-contributing gate -- calendar's
    # ``balance_contributing_clause()`` is exactly budget-variance's
    # ``is_deleted.is_(False)`` + ``~status_id.in_(balance_excluded_status_ids())``
    # (NOT a "Projected-only" gate, as a prior rationale wrongly claimed).
    # The only genuine divergence is eager-loads: calendar adds
    # ``joinedload(template -> recurrence_rule)`` for ``_is_infrequent``'s
    # day-cell display; budget-variance never reads templates.  A shared
    # query builder would parameterize that per-consumer eager-load set for
    # no logic saved (coding-standards rule 13), so the preamble stays a
    # documented one-sided disable.
    # pylint: disable=duplicate-code
    overlapping = get_overlapping_periods(user_id, first_day, last_day)
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
            balance_contributing_clause(),
            monthly_attribution_clause(first_day, last_day, period_ids),
        )
        .all()
    )
    # pylint: enable=duplicate-code


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

    Per F-3 / W-065, every transaction is re-checked against
    :func:`~app.utils.balance_predicates.is_balance_contributing`
    before being assigned to a day.  This is the belt-and-suspenders
    half of the locked Choice-2 predicate: the SQL filter in
    :func:`_query_transactions_for_range` already constrains the row
    set, but reapplying the Python predicate here ensures a future
    regression that drops the SQL filter alone (or routes a different
    query into this helper) cannot leak Cancelled / Credit rows into
    the day-cell display.  ``is_balance_contributing`` is generated
    from the same ``ref_cache`` accessors as the SQL clause so the
    two predicates cannot disagree.
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
        if not is_balance_contributing(txn):
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


@dataclass(frozen=True)
class _MonthBuildContext:
    """The pre-queried data and config shared across a year's month summaries.

    ``get_year_overview`` resolves the account, scenario, overlapping
    periods, and transaction set once and builds twelve summaries from
    them, varying only the month (``get_month_detail`` builds one).
    Bundling these into the context the build shares keeps
    :func:`_build_month_summary` a two-argument call and makes that
    resolved-once-reused relationship explicit.
    """

    year: int
    account: Account
    periods: list[PayPeriod]
    transactions: list[Transaction]
    large_threshold: int
    scenario: Scenario


def _build_month_summary(month: int, ctx: _MonthBuildContext) -> MonthSummary:
    """Assemble a MonthSummary for one month from pre-queried context.

    Assigns each transaction to a calendar day via _get_display_day,
    deduplicating by transaction ID to prevent double-counting when
    periods overlap month boundaries.

    Args:
        month: Target calendar month (1-12).
        ctx: The year/account/periods/transactions/threshold/scenario
            shared across the build (see :class:`_MonthBuildContext`).

    Returns:
        A MonthSummary for the target month.
    """
    day_entries, total_income, total_expenses = _assign_transactions_to_days(
        ctx.transactions, ctx.year, month, ctx.large_threshold,
    )

    end_balance = _compute_month_end_balance(ctx.account, ctx.year, month, ctx.scenario)
    third_paycheck_months = _detect_third_paycheck_months(ctx.periods, ctx.year)

    paycheck_days = sorted({
        p.start_date.day
        for p in ctx.periods
        if p.start_date.year == ctx.year and p.start_date.month == month
    })

    return MonthSummary(
        year=ctx.year,
        month=month,
        total_income=total_income,
        total_expenses=total_expenses,
        net=total_income - total_expenses,
        projected_end_balance=end_balance,
        is_third_paycheck_month=month in third_paycheck_months,
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
    scenario: Scenario,
) -> Decimal:
    """Project the checking balance at the true calendar month-end (E-27).

    Routes through :func:`~app.services.balance_resolver.balance_as_of_date`
    at the actual last day of the month.  This is the HIGH-02 / W-277
    fix: pre-remediation the calendar walked a separate code path
    that (a) selected the last pay period whose ``end_date`` was on or
    before the calendar month-end (up to ~13 days stale when the
    period straddled the month boundary) and (b) issued a transaction
    query without ``selectinload(Transaction.entries)``, silently
    degrading to ``effective_amount`` (the F-009 seam on a second
    surface).  Both defects collapse into the single canonical
    "balance as of date D" producer.

    Args:
        account: The :class:`~app.models.account.Account` to summarize.
        year: Target calendar year.
        month: Target calendar month (1-12).
        scenario: The baseline :class:`~app.models.scenario.Scenario`.

    Returns:
        ``Decimal`` -- the projected balance on the last day of the
        target month, quantized to cents via
        :func:`~app.utils.money.round_money` inside the resolver.
    """
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    return balance_as_of_date(account, scenario.id, last_day)
