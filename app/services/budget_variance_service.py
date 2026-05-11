"""
Shekel Budget App -- Budget Variance Service

Compares estimated vs. actual transaction amounts grouped by category
across three time windows (pay period, month, year).  Supports
drill-down from category group to individual transactions.

Pure-function service -- no Flask imports, no database writes.
"""

import calendar as cal_mod
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services.account_resolver import resolve_analytics_account
from app.services.pay_period_service import get_overlapping_periods
from app.services.scenario_resolver import get_baseline_scenario

logger = logging.getLogger(__name__)

_VALID_WINDOW_TYPES = frozenset({"pay_period", "month", "year"})

_TWO_PLACES = Decimal("0.01")
_HUNDRED = Decimal("100")


# ── Data Structures ─────────────────────────────────────────────────


@dataclass(frozen=True)
class TransactionVariance:
    """Variance data for a single transaction."""

    transaction_id: int
    name: str
    estimated: Decimal
    actual: Decimal
    variance: Decimal
    variance_pct: Decimal | None
    is_paid: bool
    due_date: date | None


@dataclass(frozen=True)
class CategoryItemVariance:
    """Variance data for a category item (e.g., 'Car Payment')."""

    category_id: int
    group_name: str
    item_name: str
    estimated_total: Decimal
    actual_total: Decimal
    variance: Decimal
    variance_pct: Decimal | None
    transaction_count: int
    transactions: list[TransactionVariance]


@dataclass(frozen=True)
class CategoryGroupVariance:
    """Variance data for a category group (e.g., 'Auto')."""

    group_name: str
    estimated_total: Decimal
    actual_total: Decimal
    variance: Decimal
    variance_pct: Decimal | None
    items: list[CategoryItemVariance]


@dataclass(frozen=True)
class VarianceReport:
    """Complete variance report for a time window."""

    window_type: str
    window_label: str
    groups: list[CategoryGroupVariance]
    total_estimated: Decimal
    total_actual: Decimal
    total_variance: Decimal
    total_variance_pct: Decimal | None
    transaction_count: int


# ── Public API ──────────────────────────────────────────────────────


def compute_variance(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    user_id: int,
    window_type: str,
    period_id: int | None = None,
    month: int | None = None,
    year: int | None = None,
    account_id: int | None = None,
) -> VarianceReport:
    """Compute budget variance for the given time window.

    Compares estimated vs. actual amounts for every transaction in the
    window, grouped by category (group -> item -> transactions).
    Results are sorted by absolute variance descending so the biggest
    budget deviations surface first.

    Args:
        user_id: The user's ID.
        window_type: One of ``"pay_period"``, ``"month"``, ``"year"``.
        period_id: Required when window_type is ``"pay_period"``.
        month: Required (with year) when window_type is ``"month"``.
        year: Required when window_type is ``"month"`` or ``"year"``.
        account_id: Account to scope to.  Defaults to the user's
            first active checking account.

    Returns:
        A VarianceReport with the full group -> item -> txn hierarchy.

    Raises:
        ValueError: If window_type is invalid or required parameters
            are missing.
    """
    _validate_params(window_type, period_id, month, year)

    transactions, period = _get_transactions_for_window(
        user_id, window_type, period_id, month, year, account_id,
    )

    groups = _build_group_hierarchy(transactions)
    total_est = sum(g.estimated_total for g in groups)
    total_act = sum(g.actual_total for g in groups)
    total_var = total_act - total_est
    txn_count = sum(len(tv.transactions) for g in groups for tv in g.items)

    return VarianceReport(
        window_type=window_type,
        window_label=_build_window_label(window_type, period, month, year),
        groups=groups,
        total_estimated=total_est,
        total_actual=total_act,
        total_variance=total_var,
        total_variance_pct=_pct(total_var, total_est),
        transaction_count=txn_count,
    )


# ── Internal helpers ────────────────────────────────────────────────


def _validate_params(
    window_type: str,
    period_id: int | None,
    month: int | None,
    year: int | None,
) -> None:
    """Raise ValueError for invalid or missing parameters."""
    if window_type not in _VALID_WINDOW_TYPES:
        raise ValueError(
            f"Invalid window_type {window_type!r}. "
            f"Must be one of {sorted(_VALID_WINDOW_TYPES)}."
        )
    if window_type == "pay_period" and period_id is None:
        raise ValueError("period_id is required when window_type is 'pay_period'.")
    if window_type == "month" and (month is None or year is None):
        raise ValueError("Both month and year are required when window_type is 'month'.")
    if window_type == "year" and year is None:
        raise ValueError("year is required when window_type is 'year'.")


def _get_transactions_for_window(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    user_id: int,
    window_type: str,
    period_id: int | None,
    month: int | None,
    year: int | None,
    account_id: int | None,
) -> tuple[list[Transaction], PayPeriod | None]:
    """Query transactions for the specified time window.

    Returns the filtered transaction list and the PayPeriod object
    (only populated for pay_period window type, used for label building).

    Filters: baseline scenario, not deleted, status does not exclude
    from balance (removes Credit and Cancelled).  Consistent with
    calendar service patterns for monthly attribution.

    Transfer shadows are included for consistency with the calendar
    service -- they are regular Transaction rows and participate in
    budget tracking.
    """
    account = resolve_analytics_account(user_id, account_id)
    if account is None:
        return [], None

    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        return [], None

    # Status IDs that exclude from balance (Credit, Cancelled).
    excluded_status_ids = [
        ref_cache.status_id(StatusEnum.CREDIT),
        ref_cache.status_id(StatusEnum.CANCELLED),
    ]

    if window_type == "pay_period":
        return _query_by_period(
            account.id, scenario.id, period_id, excluded_status_ids,
        )

    if window_type == "month":
        first_day = date(year, month, 1)
        last_day = date(year, month, cal_mod.monthrange(year, month)[1])
    else:
        first_day = date(year, 1, 1)
        last_day = date(year, 12, 31)

    txns = _query_by_date_range(
        account.id, scenario.id, user_id,
        first_day, last_day, excluded_status_ids,
    )
    return txns, None


def _query_by_period(
    account_id: int,
    scenario_id: int,
    period_id: int,
    excluded_status_ids: list[int],
) -> tuple[list[Transaction], PayPeriod | None]:
    """Query transactions for a specific pay period.

    Returns the transaction list and the PayPeriod object.
    """
    period = db.session.get(PayPeriod, period_id)
    txns = (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.category),
            joinedload(Transaction.status),
            joinedload(Transaction.pay_period),
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id == period_id,
            Transaction.is_deleted.is_(False),
            ~Transaction.status_id.in_(excluded_status_ids),
        )
        .all()
    )
    return txns, period


def _query_by_date_range(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    account_id: int,
    scenario_id: int,
    user_id: int,
    first_day: date,
    last_day: date,
    excluded_status_ids: list[int],
) -> list[Transaction]:
    """Query transactions attributed to a date range via due_date.

    Uses the same monthly attribution logic as the calendar service:
    due_date in range, or (due_date NULL and period overlaps range).
    """
    overlapping = get_overlapping_periods(user_id, first_day, last_day)
    period_ids = [p.id for p in overlapping]

    return (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.category),
            joinedload(Transaction.status),
            joinedload(Transaction.pay_period),
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.is_deleted.is_(False),
            ~Transaction.status_id.in_(excluded_status_ids),
            or_(
                Transaction.due_date.between(first_day, last_day),
                Transaction.due_date.is_(None) & Transaction.pay_period_id.in_(
                    period_ids if period_ids else [-1],
                ),
            ),
        )
        .all()
    )


def _build_group_hierarchy(
    transactions: list[Transaction],
) -> list[CategoryGroupVariance]:
    """Group transactions by category and compute variance at each level.

    Builds: group_name -> item_name -> list[TransactionVariance].
    Sorts groups, items, and transactions by abs(variance) descending.
    """
    # Group by (group_name, category_id, item_name).
    item_map: dict[tuple[str, int, str], list[TransactionVariance]] = defaultdict(list)

    for txn in transactions:
        group_name = txn.category.group_name if txn.category else "Uncategorized"
        item_name = txn.category.item_name if txn.category else "Uncategorized"
        cat_id = txn.category.id if txn.category else 0

        tv = _build_txn_variance(txn)
        item_map[(group_name, cat_id, item_name)].append(tv)

    # Build CategoryItemVariance for each item.
    group_items: dict[str, list[CategoryItemVariance]] = defaultdict(list)
    for (group_name, cat_id, item_name), txn_vars in item_map.items():
        txn_vars.sort(key=lambda t: abs(t.variance), reverse=True)
        est = sum(t.estimated for t in txn_vars)
        act = sum(t.actual for t in txn_vars)
        var = act - est
        group_items[group_name].append(CategoryItemVariance(
            category_id=cat_id,
            group_name=group_name,
            item_name=item_name,
            estimated_total=est,
            actual_total=act,
            variance=var,
            variance_pct=_pct(var, est),
            transaction_count=len(txn_vars),
            transactions=txn_vars,
        ))

    # Build CategoryGroupVariance for each group.
    groups: list[CategoryGroupVariance] = []
    for group_name, items in group_items.items():
        items.sort(key=lambda i: abs(i.variance), reverse=True)
        est = sum(i.estimated_total for i in items)
        act = sum(i.actual_total for i in items)
        var = act - est
        groups.append(CategoryGroupVariance(
            group_name=group_name,
            estimated_total=est,
            actual_total=act,
            variance=var,
            variance_pct=_pct(var, est),
            items=items,
        ))

    groups.sort(key=lambda g: abs(g.variance), reverse=True)
    return groups


def _build_txn_variance(txn: Transaction) -> TransactionVariance:
    """Compute variance for a single transaction.

    Settled transactions use actual_amount (falling back to
    estimated_amount if actual is NULL).  Projected transactions
    use estimated_amount for both sides, yielding zero variance.
    """
    estimated = txn.estimated_amount
    actual = _compute_actual(txn)
    variance = actual - estimated

    return TransactionVariance(
        transaction_id=txn.id,
        name=txn.name,
        estimated=estimated,
        actual=actual,
        variance=variance,
        variance_pct=_pct(variance, estimated),
        is_paid=bool(txn.status and txn.status.is_settled),
        due_date=txn.due_date,
    )


def _compute_actual(txn: Transaction) -> Decimal:
    """Extract the 'actual' amount for variance computation.

    For settled transactions: actual_amount if not None, else
    estimated_amount (handles done-without-actual edge case).
    For projected transactions: estimated_amount (no actual yet,
    so individual variance is always zero).
    """
    if txn.status and txn.status.is_settled:
        if txn.actual_amount is not None:
            return txn.actual_amount
        return txn.estimated_amount
    return txn.estimated_amount


def _pct(variance: Decimal, estimated: Decimal) -> Decimal | None:
    """Compute variance percentage, guarding against division by zero.

    Returns (variance / estimated) * 100 rounded to 2 decimal places,
    or None if estimated is zero.
    """
    if estimated == Decimal("0"):
        return None
    return (variance / estimated * _HUNDRED).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _build_window_label(
    window_type: str,
    period: PayPeriod | None = None,
    month: int | None = None,
    year: int | None = None,
) -> str:
    """Format the human-readable window label.

    Pay period: 'Jan 02 - Jan 15, 2026'.
    Month: 'January 2026'.
    Year: '2026'.
    """
    if window_type == "pay_period" and period is not None:
        start = period.start_date
        end = period.end_date
        return f"{start.strftime('%b %d')} - {end.strftime('%b %d')}, {end.year}"

    if window_type == "month" and month is not None and year is not None:
        month_name = date(year, month, 1).strftime("%B")
        return f"{month_name} {year}"

    if window_type == "year" and year is not None:
        return str(year)

    return ""
