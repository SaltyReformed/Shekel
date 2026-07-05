"""
Shekel Budget App -- Spending Trend Service

Detects per-category spending trends over rolling windows by comparing
recent-half versus prior-half average spending across completed pay
periods.  Flags categories exceeding a configurable threshold and produces
ranked top-5 lists of increasing and decreasing categories.

Pure-function service -- no Flask imports, no database writes.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import spending_analysis
from app.services.account_resolver import resolve_analytics_account
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.balance_predicates import settled_status_ids
from app.utils.money import round_money

logger = logging.getLogger(__name__)

_TWO_PLACES = Decimal("0.01")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")

# Percentage change below this absolute value is considered flat.
_FLAT_THRESHOLD = Decimal("1")  # 1%

# Maximum items in the top-increasing / top-decreasing lists.
_TOP_N = 5

# F2 -- a category needs spending in at least this many distinct periods
# of the window before it is treated as a trend.  Below it there is no
# time series to fit (a single transaction is not a trend), so the
# category is excluded from the report entirely.
_MIN_ACTIVE_PERIODS = 3

# R1 -- materiality floor.  A category must average at least this many
# dollars of spending per window period to be trended.  Below it a large
# percentage swing is noise on pocket change, so the category is excluded.
_MATERIALITY_FLOOR = Decimal("20.00")

# R2 -- new-baseline floor.  When the prior half of the window averages
# below this, the category has effectively no earlier baseline to divide
# by (it ramped up from ~zero).  A percentage relative to a near-zero base
# is meaningless and explodes, so such a category is reported as emerging
# ("New", pct_change=None) rather than with a fabricated percentage.
_NEW_BASELINE_FLOOR = Decimal("5.00")

# Data-sufficiency window sizes, counted in COMPLETED PAY PERIODS with
# settled spending -- the SAME unit the trend series is built in.  The
# audit's Tab 4 finding was a UNIT MISMATCH: the old gate counted distinct
# calendar MONTHS while the series it then fit was per PAY PERIOD, so the
# banner could flip independently of the series' actual density.  Counting
# the series' native unit -- periods -- closes that gap.  Shekel runs on ~26
# biweekly periods per year, so 12 periods is ~6 months (the full window)
# and 6 periods is ~3 months (the preliminary minimum below which there is
# no two-halved series to fit).
_FULL_WINDOW_PERIODS = 12
_PRELIMINARY_MIN_PERIODS = 6

# Nominal calendar-month labels for each sufficiency tier.  These drive NO
# gate (the gate is period-based, above); they exist only so the still-
# shipping trends template / CSV render a coarse "~N months" human label
# next to the exact ``window_periods`` count.  12 biweekly periods ~= 6
# months (full), 6 ~= 3 months (preliminary).
_FULL_WINDOW_MONTHS = 6
_PRELIMINARY_WINDOW_MONTHS = 3


# ── Data Structures ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ItemTrend:  # pylint: disable=too-many-instance-attributes
    """Trend data for a single category item.

    Pylint: ``too-many-instance-attributes`` (12/7) -- this is a cohesive
    value record -- one category's trend row,
    produced in a single pass by _compute_item_trend -- consumed verbatim
    by row-rendering surfaces: the trends template reads the fields
    interleaved within one list item, and the CSV export emits them as
    adjacent columns.  No consumer reads any subset (identity, magnitude,
    trend metrics, timing) as a unit, and no field owns a section total.
    Every field is an irreducible column of the row; splitting it would
    fragment one domain concept for no design gain.

    ``period_totals`` is the per-period spending series the half-window
    ``pct_change`` / ``absolute_change`` were computed from (zero-filled for
    empty periods, chronological).  It is carried on the row so the Spending
    surface's sparkline reads the SAME series the trend chip's delta came
    from -- one data source, they cannot disagree (the S-P1 build rule).

    ``pct_change`` is ``None`` for an emerging category -- one whose
    prior-half spend is below ``_NEW_BASELINE_FLOOR`` so there is no stable
    base to divide by.  Such a row renders as "New" with a positive
    ``absolute_change`` and ``trend_direction == "up"``; every other field
    stays populated.
    """

    category_id: int
    group_name: str
    item_name: str
    period_average: Decimal
    trend_direction: str
    pct_change: Decimal | None
    absolute_change: Decimal
    is_flagged: bool
    data_points: int
    total_spending: Decimal
    avg_days_before_due: Decimal | None
    period_totals: list[Decimal]


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
    The window covers only completed pay periods (F1).  Each trendable
    category -- one with spending in at least ``_MIN_ACTIVE_PERIODS``
    periods (F2) and an average of at least ``_MATERIALITY_FLOOR`` per
    period (R1) -- is measured by comparing its recent-half versus
    prior-half average spend, yielding percentage change, direction, and
    flagging.  A category ramping up from a near-zero prior half is
    reported as emerging ("New", pct_change=None).

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

    # Determine data sufficiency by counting completed pay periods that
    # carry settled spending -- the SAME unit the series is built in (the
    # unit-mismatch fix; see the ``_FULL_WINDOW_PERIODS`` block).
    distinct_periods = _count_distinct_paid_periods(account.id, scenario.id, user_id)
    if distinct_periods < _PRELIMINARY_MIN_PERIODS:
        return _empty_report(threshold, data_sufficiency="insufficient")

    if distinct_periods >= _FULL_WINDOW_PERIODS:
        window_size = _FULL_WINDOW_PERIODS
        window_months = _FULL_WINDOW_MONTHS
        sufficiency = "sufficient"
    else:
        window_size = _PRELIMINARY_MIN_PERIODS
        window_months = _PRELIMINARY_WINDOW_MONTHS
        sufficiency = "preliminary"

    # The window is the last ``window_size`` COMPLETED pay periods -- a
    # period-native span, so the gate's unit and the series' unit are one.
    periods = _get_window_periods(user_id, window_size)
    if not periods:
        return _empty_report(threshold, data_sufficiency="insufficient")

    txns = spending_analysis.query_settled_expenses(
        scenario.id, [p.id for p in periods], account.id,
    )

    items = _build_item_trends(txns, periods, threshold)
    groups = _build_group_trends(items, threshold)
    top_inc, top_dec = _build_top_lists(items)

    return TrendReport(
        window_months=window_months,
        window_periods=len(periods),
        top_increasing=top_inc,
        top_decreasing=top_dec,
        all_items=sorted(items, key=_all_items_sort_key),
        group_trends=sorted(groups, key=lambda g: abs(g.pct_change), reverse=True),
        data_sufficiency=sufficiency,
        threshold=threshold,
    )


# ── Internal helpers ────────────────────────────────────────────────


def _count_distinct_paid_periods(
    account_id: int,
    scenario_id: int,
    user_id: int,
) -> int:
    """Count completed pay periods that carry settled spending.

    A period is counted when it has fully elapsed (``end_date < today`` --
    the same completed-period gate F1 applies to the window) AND holds at
    least one settled expense on this account / scenario.  This is the
    data-density measure the sufficiency banner reads, counted in PAY
    PERIODS -- the same unit the trend series is built in.  It replaces the
    former distinct-calendar-month count, whose different unit let the
    banner flip independently of the series' actual period density (the
    audit's Tab 4 unit-mismatch finding).
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    today = date.today()

    rows = (
        db.session.query(Transaction.pay_period_id)
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.is_deleted.is_(False),
            Transaction.transaction_type_id == expense_type_id,
            Transaction.status_id.in_(settled_status_ids()),
            PayPeriod.user_id == user_id,
            PayPeriod.end_date < today,
        )
        .distinct()
        .all()
    )
    return len(rows)


def _get_window_periods(
    user_id: int,
    window_size: int,
) -> list[PayPeriod]:
    """Return the last ``window_size`` completed pay periods, chronological.

    A period is admitted only once it has fully elapsed (``end_date <
    today``); the most recent ``window_size`` such periods form the trend
    window, ordered oldest-first so each category's per-period series is
    chronological.  Fewer than ``window_size`` completed periods yields all
    of them.

    F1 -- the in-progress current period and any future periods are
    EXCLUDED.  The app projects ~2 years of pay periods forward, and the
    current period is only partially settled, so their expense rows are
    mostly still ``projected`` (unsettled).  Counting them would zero-fill
    the tail of every category's series, dragging nearly every trend toward
    "decreasing" purely because the latest periods have not been paid yet.
    Only completed periods carry realized spending.

    The window is sized in PAY PERIODS (not a calendar-month lookback) so
    the sufficiency gate's unit, the window's unit, and the series' unit are
    all one -- the audit's Tab 4 unit-mismatch fix.
    """
    today = date.today()
    completed = (
        db.session.query(PayPeriod)
        .filter(
            PayPeriod.user_id == user_id,
            PayPeriod.end_date < today,
        )
        .order_by(PayPeriod.period_index)
        .all()
    )
    return completed[-window_size:]


def _build_item_trends(
    transactions: list[Transaction],
    periods: list[PayPeriod],
    threshold: Decimal,
) -> list[ItemTrend]:
    """Build an ItemTrend for each eligible category item.

    Groups transactions by category_id, computes per-period totals
    (including zero-spending periods), and derives the recent-vs-prior-half
    trend metrics.  Categories that fail the F2 (min active periods) or R1
    (materiality) gates are dropped, so the returned list is already
    filtered to trendable categories.
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
        # _compute_item_trend returns None for categories that fail the
        # F2 (min active periods) or R1 (materiality) gates -- they have no
        # meaningful trend and are excluded from every report surface.
        if item is not None:
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


def _half_window_change(
    period_totals: list[Decimal],
) -> tuple[Decimal, Decimal | None]:
    """Derive the dollar and percentage change between window halves (Design A).

    Splits the chronological per-period totals into a prior half and a
    recent half of equal length.  When the count is odd the middle period
    is dropped, so the two halves share one denominator and the comparison
    stays fair.  ``absolute_change`` is the per-period dollar delta
    ``recent_avg - prior_avg``; ``pct_change`` is that delta relative to
    ``prior_avg``.

    Returns ``pct_change = None`` (emerging / "New") when ``prior_avg`` is
    below ``_NEW_BASELINE_FLOOR``: there is no stable earlier base, so a
    percentage would divide by ~zero and explode.  Callers reach this only
    for materially-spending categories (R1), so a sub-floor prior half
    means the spend is concentrated in the recent half -- a genuine
    ramp-up -- and ``absolute_change`` is positive.

    Because both averages are non-negative, a real percentage is bounded
    below at -100% (recent spend cannot fall below zero), so no separate
    downside clamp is needed.
    """
    n = len(period_totals)
    half = n // 2
    if half == 0:
        return _ZERO, None

    half_dec = Decimal(str(half))
    prior_avg = sum(period_totals[:half]) / half_dec
    recent_avg = sum(period_totals[n - half:]) / half_dec
    absolute_change = round_money(recent_avg - prior_avg)

    if prior_avg < _NEW_BASELINE_FLOOR:
        return absolute_change, None

    pct_change = (recent_avg - prior_avg) / prior_avg * _HUNDRED
    return absolute_change, pct_change.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _compute_item_trend(
    cat_id: int,
    txns: list[Transaction],
    period_index_map: dict[int, int],
    n_periods: int,
    threshold: Decimal,
) -> ItemTrend | None:
    """Compute trend metrics for a single category item (Design A).

    Builds per-period totals (with zeros for empty periods), then applies
    the eligibility gates before measuring any trend:

    - F2: spending in at least ``_MIN_ACTIVE_PERIODS`` periods.  Below it
      there is no time series -- a single transaction is not a trend.
    - R1: an average of at least ``_MATERIALITY_FLOOR`` dollars per period.
      Below it a large percentage swing is noise on pocket change.

    A category failing either gate has no meaningful trend and is excluded
    from the report (returns ``None``).  For eligible categories the change
    is a recent-vs-prior-half comparison; an emerging category (prior half
    below the new-baseline floor) is reported as "New" with
    ``pct_change=None`` and an upward direction.  Also computes
    ``avg_days_before_due`` from the ``days_paid_before_due`` property.
    """
    group_name, item_name = _item_category_names(txns)

    period_totals, data_points = _period_totals_for_item(
        txns, period_index_map, n_periods,
    )

    if data_points < _MIN_ACTIVE_PERIODS:
        return None

    total_spending = sum(period_totals)
    period_average = round_money(
        total_spending / Decimal(str(n_periods))
    ) if n_periods > 0 else _ZERO
    if period_average < _MATERIALITY_FLOOR:
        return None

    absolute_change, pct_change = _half_window_change(period_totals)

    if pct_change is None:
        # Emerging spending: no earlier base to divide by.  Direction is
        # up by construction (material spend concentrated in the recent
        # half) and the row is flagged as a notable change.
        trend_direction = "up"
        is_flagged = True
    else:
        trend_direction = _direction_from_pct(pct_change)
        is_flagged = abs(pct_change) >= threshold * _HUNDRED

    return ItemTrend(
        category_id=cat_id,
        group_name=group_name,
        item_name=item_name,
        period_average=period_average,
        trend_direction=trend_direction,
        pct_change=pct_change,
        absolute_change=absolute_change,
        is_flagged=is_flagged,
        data_points=data_points,
        total_spending=total_spending,
        # OP-3: average days a bill is paid before its due date.
        avg_days_before_due=_compute_avg_days_before_due(txns),
        # The per-period series the half-window change above was computed
        # from, carried so the Spending sparkline and the trend chip share
        # one data source (S-P1 build rule).
        period_totals=period_totals,
    )


def _build_group_trends(
    items: list[ItemTrend],
    threshold: Decimal,
) -> list[GroupTrend]:
    """Build group-level trends from item trends.

    Group pct_change is a spending-weighted average of the item pct_change
    values: items with more spending influence the group trend more.
    Emerging ("New") items carry no percentage (pct_change=None), so they
    cannot enter a weighted average -- they are listed under the group but
    excluded from its weighting; a group with no measurable items reads as
    flat.
    """
    by_group: dict[str, list[ItemTrend]] = defaultdict(list)
    for item in items:
        by_group[item.group_name].append(item)

    groups: list[GroupTrend] = []
    for group_name, group_items in by_group.items():
        sorted_items = sorted(group_items, key=_all_items_sort_key)
        total_spending = sum(i.total_spending for i in group_items)

        measurable = [i for i in group_items if i.pct_change is not None]
        weight_base = sum(i.total_spending for i in measurable)
        if weight_base == _ZERO:
            weighted_pct = _ZERO
        else:
            weighted_pct = sum(
                i.pct_change * i.total_spending for i in measurable
            ) / weight_base

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


def _all_items_sort_key(item: ItemTrend) -> tuple[int, Decimal]:
    """Sort key for the full ``all_items`` list.

    Real-percentage rows come first, ordered by descending percentage
    magnitude; emerging ("New") rows come last, ordered by descending
    dollar change.  A new row has no percentage to rank by, so it is
    grouped at the end rather than interleaved with percentages via an
    apples-to-oranges comparison.
    """
    if item.pct_change is None:
        return (1, -item.absolute_change)
    return (0, -abs(item.pct_change))


def _build_top_lists(
    items: list[ItemTrend],
) -> tuple[list[ItemTrend], list[ItemTrend]]:
    """Build top-5 increasing and decreasing lists from flagged items.

    Only flagged items qualify.  Both lists order by
    :func:`_all_items_sort_key`: increases lead with the largest
    percentage, decreases lead with the most negative percentage, and
    emerging ("New") increases -- which have no percentage -- sort after
    the real-percentage increases by descending dollar change.
    """
    flagged_up = [
        i for i in items
        if i.is_flagged and i.trend_direction == "up"
    ]
    flagged_down = [
        i for i in items
        if i.is_flagged and i.trend_direction == "down"
    ]

    flagged_up.sort(key=_all_items_sort_key)
    flagged_down.sort(key=_all_items_sort_key)

    return flagged_up[:_TOP_N], flagged_down[:_TOP_N]


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
