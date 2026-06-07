"""
Shekel Budget App -- Spending Trend Service

Detects per-category spending trends over rolling windows using
linear regression.  Flags categories exceeding a configurable
threshold and produces ranked top-5 lists of increasing and
decreasing categories.

Pure-function service -- no Flask imports, no database writes.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services.account_resolver import resolve_analytics_account
from app.services.scenario_resolver import get_baseline_scenario

logger = logging.getLogger(__name__)

_TWO_PLACES = Decimal("0.01")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")

# Percentage change below this absolute value is considered flat.
_FLAT_THRESHOLD = Decimal("1")  # 1%

# Settled status IDs -- only paid transactions count for trends.
_SETTLED_STATUSES = frozenset({
    StatusEnum.DONE,
    StatusEnum.RECEIVED,
    StatusEnum.SETTLED,
})

# Maximum items in the top-increasing / top-decreasing lists.
_TOP_N = 5


# ── Data Structures ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ItemTrend:  # pylint: disable=too-many-instance-attributes
    """Trend data for a single category item.

    Pylint: ``too-many-instance-attributes`` (11/7) -- this is a cohesive
    value record -- one category's trend row,
    produced in a single pass by _compute_item_trend -- consumed verbatim
    by row-rendering surfaces: the trends template reads the fields
    interleaved within one list item, and the CSV export emits them as
    adjacent columns.  No consumer reads any subset (identity, magnitude,
    trend metrics, timing) as a unit, and no field owns a section total.
    Every field is an irreducible column of the row; splitting it would
    fragment one domain concept for no design gain.
    """

    category_id: int
    group_name: str
    item_name: str
    period_average: Decimal
    trend_direction: str
    pct_change: Decimal
    absolute_change: Decimal
    is_flagged: bool
    data_points: int
    total_spending: Decimal
    avg_days_before_due: Decimal | None


@dataclass(frozen=True)
class GroupTrend:
    """Aggregated trend for a category group."""

    group_name: str
    total_spending: Decimal
    pct_change: Decimal
    trend_direction: str
    is_flagged: bool
    items: list[ItemTrend]


@dataclass(frozen=True)
class TrendReport:  # pylint: disable=too-many-instance-attributes
    """Complete trend detection report.

    Pylint: ``too-many-instance-attributes`` (8/7) -- this is one cohesive
    result aggregate for the trends tab.  The
    four window fields (window_months, window_periods, data_sufficiency,
    threshold) describe and gate the report; the four collections
    (top_increasing, top_decreasing, all_items, group_trends) are its
    ranked, grouped, and full item views.  Every field is read scattered
    across the trends template, the CSV export, and the route -- never as
    a sub-group -- so there is no section to extract; nesting would
    fragment one contract for no design gain.
    """

    window_months: int
    window_periods: int
    top_increasing: list[ItemTrend]
    top_decreasing: list[ItemTrend]
    all_items: list[ItemTrend]
    group_trends: list[GroupTrend]
    data_sufficiency: str
    threshold: Decimal


# ── Public API ──────────────────────────────────────────────────────


def compute_trends(
    user_id: int,
    threshold: Decimal = Decimal("0.1000"),
    account_id: int | None = None,
) -> TrendReport:
    """Compute spending trends across all categories.

    Determines the window size based on data sufficiency (6 months
    preferred, 3 months if preliminary, insufficient if < 3 months).
    For each category item with paid expense transactions, fits a
    linear regression to per-period totals and computes percentage
    change, direction, and flagging.

    Args:
        user_id: The user's ID.
        threshold: Fractional threshold (0-1) for flagging trends.
            Default 0.1 = 10%.
        account_id: Account to scope to.  Defaults to the user's
            first active checking account.

    Returns:
        A TrendReport with item-level and group-level trends.
    """
    account = resolve_analytics_account(user_id, account_id)
    if account is None:
        return _empty_report(threshold)

    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        return _empty_report(threshold)

    # Determine data sufficiency by counting distinct months with paid data.
    distinct_months = _count_distinct_paid_months(account.id, scenario.id, user_id)
    if distinct_months < 3:
        return _empty_report(threshold, data_sufficiency="insufficient")

    window_months = 6 if distinct_months >= 6 else 3
    sufficiency = "sufficient" if distinct_months >= 6 else "preliminary"

    # Determine date range for the window.
    periods = _get_window_periods(user_id, window_months)
    if not periods:
        return _empty_report(threshold, data_sufficiency="insufficient")

    txns = _query_paid_expenses(account.id, scenario.id, [p.id for p in periods])

    items = _build_item_trends(txns, periods, threshold)
    groups = _build_group_trends(items, threshold)
    top_inc, top_dec = _build_top_lists(items)

    return TrendReport(
        window_months=window_months,
        window_periods=len(periods),
        top_increasing=top_inc,
        top_decreasing=top_dec,
        all_items=sorted(items, key=lambda i: abs(i.pct_change), reverse=True),
        group_trends=sorted(groups, key=lambda g: abs(g.pct_change), reverse=True),
        data_sufficiency=sufficiency,
        threshold=threshold,
    )


# ── Internal helpers ────────────────────────────────────────────────


def _count_distinct_paid_months(
    account_id: int,
    scenario_id: int,
    user_id: int,
) -> int:
    """Count distinct calendar months with at least one paid expense.

    Uses COALESCE(due_date, pay_period.start_date) for monthly
    attribution, consistent with the calendar and variance services.
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    settled_ids = _get_settled_status_ids()

    rows = (
        db.session.query(Transaction)
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.is_deleted.is_(False),
            Transaction.transaction_type_id == expense_type_id,
            Transaction.status_id.in_(settled_ids),
            PayPeriod.user_id == user_id,
        )
        .all()
    )

    months: set[tuple[int, int]] = set()
    for txn in rows:
        ref_date = txn.due_date if txn.due_date is not None else txn.pay_period.start_date
        months.add((ref_date.year, ref_date.month))
    return len(months)


def _get_window_periods(
    user_id: int,
    window_months: int,
) -> list[PayPeriod]:
    """Return pay periods whose start_date falls within the window.

    The window ends at the end of the current month and extends
    back window_months months.
    """
    today = date.today()
    # End of current month.
    end_year = today.year
    end_month = today.month

    # Start of window: window_months before current month.
    start_month = end_month - window_months
    start_year = end_year
    while start_month < 1:
        start_month += 12
        start_year -= 1

    first_day = date(start_year, start_month, 1)

    return (
        db.session.query(PayPeriod)
        .filter(
            PayPeriod.user_id == user_id,
            PayPeriod.start_date >= first_day,
        )
        .order_by(PayPeriod.period_index)
        .all()
    )


def _query_paid_expenses(
    account_id: int,
    scenario_id: int,
    period_ids: list[int],
) -> list[Transaction]:
    """Load paid expense transactions for the given periods.

    Filters: settled status only (Done/Received/Settled), expense
    type only, not deleted, not cancelled (cancelled is excluded by
    requiring settled status).  Transfer shadows are included for
    consistency with sibling services.

    Eager-loads category and pay_period to prevent N+1 queries.
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    settled_ids = _get_settled_status_ids()

    # Pylint: ``duplicate-code`` -- settled-expense query for the
    # spending-trend report.  The account / scenario / period /
    # expense-type filter core coincides with ``dashboard_service``'s
    # expense query, but the two diverge on the parts that matter
    # (eager-loads and the settled-vs-projected status gate), so a shared
    # builder would need both as parameters and save no logic
    # (coding-standards rule 13).  One-sided ``duplicate-code`` disable.
    # pylint: disable=duplicate-code
    return (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.category),
            joinedload(Transaction.pay_period),
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
            Transaction.transaction_type_id == expense_type_id,
            Transaction.status_id.in_(settled_ids),
        )
        .all()
    )
    # pylint: enable=duplicate-code


def _build_item_trends(
    transactions: list[Transaction],
    periods: list[PayPeriod],
    threshold: Decimal,
) -> list[ItemTrend]:
    """Build ItemTrend for each category item with data.

    Groups transactions by category_id, computes per-period totals
    (including zero-spending periods), runs linear regression, and
    derives trend metrics.
    """
    # Group transactions by category_id.
    by_category: dict[int, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        cat_id = txn.category_id if txn.category_id is not None else 0
        by_category[cat_id].append(txn)

    # Map period_id to chronological index.
    period_index_map = {p.id: idx for idx, p in enumerate(periods)}
    n_periods = len(periods)

    items: list[ItemTrend] = []
    for cat_id, cat_txns in by_category.items():
        item = _compute_item_trend(
            cat_id, cat_txns, period_index_map, n_periods, threshold,
        )
        items.append(item)

    return items


def _item_category_names(txns: list[Transaction]) -> tuple[str, str]:
    """Resolve the (group_name, item_name) labels for a category's rows.

    Read from the first transaction's category, falling back to
    "Uncategorized" for rows with no category.
    """
    sample = txns[0]
    group_name = sample.category.group_name if sample.category else "Uncategorized"
    item_name = sample.category.item_name if sample.category else "Uncategorized"
    return group_name, item_name


def _period_totals_for_item(
    txns: list[Transaction],
    period_index_map: dict[int, int],
    n_periods: int,
) -> tuple[list[Decimal], int]:
    """Bucket a category's transactions into per-period spending totals.

    Returns the per-period totals (zero-filled for periods with no
    activity) and the count of distinct periods that had a data point.
    """
    period_totals: list[Decimal] = [_ZERO] * n_periods
    data_point_periods: set[int] = set()

    for txn in txns:
        idx = period_index_map.get(txn.pay_period_id)
        if idx is None:
            continue
        period_totals[idx] += abs(txn.effective_amount)
        data_point_periods.add(idx)

    return period_totals, len(data_point_periods)


def _trend_change(period_totals: list[Decimal], n_periods: int) -> tuple[Decimal, Decimal]:
    """Derive the absolute and percentage change across the window.

    Runs linear regression over the per-period totals: ``absolute_change``
    is the per-period slope; ``pct_change`` is the regression-predicted
    change from the first to the last period.
    """
    slope, intercept = _compute_linear_regression(period_totals)
    absolute_change = slope.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)

    first_predicted = intercept
    last_predicted = intercept + slope * Decimal(str(n_periods - 1))
    pct_change = _safe_pct_change(first_predicted, last_predicted)

    return absolute_change, pct_change


def _compute_item_trend(
    cat_id: int,
    txns: list[Transaction],
    period_index_map: dict[int, int],
    n_periods: int,
    threshold: Decimal,
) -> ItemTrend:
    """Compute trend metrics for a single category item.

    Builds per-period totals (with zeros for empty periods), runs
    regression, and derives direction/flagging.  Also computes
    avg_days_before_due from the days_paid_before_due property.
    """
    group_name, item_name = _item_category_names(txns)

    period_totals, data_points = _period_totals_for_item(
        txns, period_index_map, n_periods,
    )

    total_spending = sum(period_totals)
    period_average = (
        total_spending / Decimal(str(n_periods))
    ).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP) if n_periods > 0 else _ZERO

    absolute_change, pct_change = _trend_change(period_totals, n_periods)

    return ItemTrend(
        category_id=cat_id,
        group_name=group_name,
        item_name=item_name,
        period_average=period_average,
        trend_direction=_direction_from_pct(pct_change),
        pct_change=pct_change,
        absolute_change=absolute_change,
        is_flagged=abs(pct_change) >= threshold * _HUNDRED,
        data_points=data_points,
        total_spending=total_spending,
        # OP-3: average days a bill is paid before its due date.
        avg_days_before_due=_compute_avg_days_before_due(txns),
    )


def _build_group_trends(
    items: list[ItemTrend],
    threshold: Decimal,
) -> list[GroupTrend]:
    """Build group-level trends from item trends.

    Group pct_change is a spending-weighted average of item pct_change
    values: items with more spending influence the group trend more.
    """
    by_group: dict[str, list[ItemTrend]] = defaultdict(list)
    for item in items:
        by_group[item.group_name].append(item)

    groups: list[GroupTrend] = []
    for group_name, group_items in by_group.items():
        sorted_items = sorted(group_items, key=lambda i: abs(i.pct_change), reverse=True)
        total_spending = sum(i.total_spending for i in group_items)

        if total_spending == _ZERO:
            weighted_pct = _ZERO
        else:
            weighted_pct = sum(
                i.pct_change * i.total_spending for i in group_items
            ) / total_spending

        weighted_pct = weighted_pct.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
        direction = _direction_from_pct(weighted_pct)
        is_flagged = abs(weighted_pct) >= threshold * _HUNDRED

        groups.append(GroupTrend(
            group_name=group_name,
            total_spending=total_spending,
            pct_change=weighted_pct,
            trend_direction=direction,
            is_flagged=is_flagged,
            items=sorted_items,
        ))

    return groups


def _build_top_lists(
    items: list[ItemTrend],
) -> tuple[list[ItemTrend], list[ItemTrend]]:
    """Build top-5 increasing and decreasing lists from flagged items.

    Only flagged items qualify.  Increasing sorted by pct_change
    descending; decreasing sorted by pct_change ascending (most
    negative first).
    """
    flagged_up = [
        i for i in items
        if i.is_flagged and i.trend_direction == "up"
    ]
    flagged_down = [
        i for i in items
        if i.is_flagged and i.trend_direction == "down"
    ]

    flagged_up.sort(key=lambda i: i.pct_change, reverse=True)
    flagged_down.sort(key=lambda i: i.pct_change)

    return flagged_up[:_TOP_N], flagged_down[:_TOP_N]


def _compute_linear_regression(
    values: list[Decimal],
) -> tuple[Decimal, Decimal]:
    """Simple OLS linear regression over equally-spaced data points.

    Returns (slope, intercept) using Decimal arithmetic throughout.
    x-values are integer indices [0, 1, 2, ..., n-1].

    Guards:
    - Empty input raises ValueError.
    - Single value returns (Decimal("0"), value).
    - Zero denominator returns (Decimal("0"), mean).

    Args:
        values: Per-period spending totals, ordered chronologically.

    Returns:
        Tuple of (slope, intercept) as Decimal values.

    Raises:
        ValueError: If values is empty.
    """
    n = len(values)
    if n == 0:
        raise ValueError("Cannot compute regression on empty input.")
    if n == 1:
        return _ZERO, values[0]

    n_dec = Decimal(str(n))
    # Closed-form sums for x = 0, 1, ..., n-1.
    sum_x = n_dec * (n_dec - 1) / Decimal("2")
    sum_x2 = n_dec * (n_dec - 1) * (2 * n_dec - 1) / Decimal("6")
    sum_y = sum(values)
    sum_xy = sum(Decimal(str(i)) * v for i, v in enumerate(values))

    denominator = n_dec * sum_x2 - sum_x * sum_x
    if denominator == _ZERO:
        return _ZERO, values[0]

    slope = (n_dec * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n_dec

    return slope, intercept


def _safe_pct_change(
    first_predicted: Decimal,
    last_predicted: Decimal,
) -> Decimal:
    """Compute percentage change between regression endpoints.

    Returns Decimal("0") if first_predicted is zero (cannot divide).
    Rounds to 2 decimal places.
    """
    if first_predicted == _ZERO:
        return _ZERO
    change = (last_predicted - first_predicted) / first_predicted * _HUNDRED
    return change.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _direction_from_pct(pct_change: Decimal) -> str:
    """Determine trend direction from percentage change.

    'flat' if abs(pct_change) < 1%, otherwise 'up' or 'down'.
    """
    if abs(pct_change) < _FLAT_THRESHOLD:
        return "flat"
    return "up" if pct_change > _ZERO else "down"


def _compute_avg_days_before_due(txns: list[Transaction]) -> Decimal | None:
    """Compute average days paid before due date (OP-3).

    Positive means paid early on average, negative means late.
    Returns None if no transactions have both paid_at and due_date.
    """
    days_values: list[int] = []
    for txn in txns:
        dpbd = txn.days_paid_before_due
        if dpbd is not None:
            days_values.append(dpbd)

    if not days_values:
        return None

    total = sum(days_values)
    return (
        Decimal(str(total)) / Decimal(str(len(days_values)))
    ).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _get_settled_status_ids() -> list[int]:
    """Return status IDs for settled statuses (Done, Received, Settled)."""
    return [ref_cache.status_id(s) for s in _SETTLED_STATUSES]


def _empty_report(
    threshold: Decimal,
    data_sufficiency: str = "insufficient",
) -> TrendReport:
    """Return a TrendReport with empty data."""
    return TrendReport(
        window_months=0,
        window_periods=0,
        top_increasing=[],
        top_decreasing=[],
        all_items=[],
        group_trends=[],
        data_sufficiency=data_sufficiency,
        threshold=threshold,
    )
