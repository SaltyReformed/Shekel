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

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services.account_resolver import resolve_analytics_account
from app.services.pay_period_service import get_overlapping_periods
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.balance_predicates import balance_excluded_status_ids

logger = logging.getLogger(__name__)

_VALID_WINDOW_TYPES = frozenset({"pay_period", "month", "year"})

_TWO_PLACES = Decimal("0.01")
_HUNDRED = Decimal("100")


# ── Data Structures ─────────────────────────────────────────────────


@dataclass(frozen=True)
class VarianceWindow:
    """The time window a variance report is computed over.

    A discriminated selector: ``window_type`` decides which of the other
    fields are meaningful -- ``period_id`` for ``"pay_period"``,
    ``month`` + ``year`` for ``"month"``, ``year`` for ``"year"``.  The
    three service helpers that read these fields (``_validate_params``,
    ``_get_transactions_for_window``, ``_build_window_label``) all consume
    them as a unit, so they are one cohesive value rather than four loose
    arguments; the analytics route builds one solely to call
    :func:`compute_variance`.  Validate one with :func:`_validate_params`.
    """

    window_type: str
    period_id: int | None = None
    month: int | None = None
    year: int | None = None


@dataclass(frozen=True)
class VarianceFigures:
    """Estimated vs. actual amounts with the derived variance and percentage.

    The (estimated, actual, variance, variance_pct) quad that every level
    of the variance hierarchy reports.  Build one with :meth:`of` so the
    variance and percentage are derived identically at every level rather
    than recomputed by hand for each transaction, item, group, and total.
    """

    estimated: Decimal
    actual: Decimal
    variance: Decimal
    variance_pct: Decimal | None

    @classmethod
    def of(cls, estimated: Decimal, actual: Decimal) -> "VarianceFigures":
        """Build figures from an estimated/actual pair.

        ``variance`` is ``actual - estimated``; ``variance_pct`` is that
        variance as a percentage of the estimated base, or ``None`` when
        the base is zero (see :func:`_pct`).

        Args:
            estimated: The budgeted/estimated amount (the percentage base).
            actual: The realized actual amount.

        Returns:
            A VarianceFigures with the derived variance and percentage.
        """
        variance = actual - estimated
        return cls(
            estimated=estimated,
            actual=actual,
            variance=variance,
            variance_pct=_pct(variance, estimated),
        )


@dataclass(frozen=True)
class TransactionVariance:
    """Variance data for a single transaction."""

    transaction_id: int
    name: str
    figures: VarianceFigures
    is_paid: bool
    due_date: date | None


@dataclass(frozen=True)
class CategoryItemVariance:
    """Variance data for a category item (e.g., 'Car Payment')."""

    category_id: int
    group_name: str
    item_name: str
    figures: VarianceFigures
    transaction_count: int
    transactions: list[TransactionVariance]


@dataclass(frozen=True)
class CategoryGroupVariance:
    """Variance data for a category group (e.g., 'Auto')."""

    group_name: str
    figures: VarianceFigures
    items: list[CategoryItemVariance]


@dataclass(frozen=True)
class VarianceReport:
    """Complete variance report for a time window."""

    window_type: str
    window_label: str
    groups: list[CategoryGroupVariance]
    figures: VarianceFigures
    transaction_count: int


# ── Public API ──────────────────────────────────────────────────────


def compute_variance(
    user_id: int,
    window: VarianceWindow,
    account_id: int | None = None,
) -> VarianceReport:
    """Compute budget variance for the given time window.

    Compares estimated vs. actual amounts for every transaction in the
    window, grouped by category (group -> item -> transactions).
    Results are sorted by absolute variance descending so the biggest
    budget deviations surface first.

    Args:
        user_id: The user's ID.
        window: The time window to report over (see :class:`VarianceWindow`).
        account_id: Account to scope to.  Defaults to the user's
            first active checking account.

    Returns:
        A VarianceReport with the full group -> item -> txn hierarchy.

    Raises:
        ValueError: If the window is invalid or missing required fields.
    """
    _validate_params(window)

    transactions, period = _get_transactions_for_window(user_id, window, account_id)

    groups = _build_group_hierarchy(transactions)
    figures = VarianceFigures.of(
        sum(g.figures.estimated for g in groups),
        sum(g.figures.actual for g in groups),
    )
    txn_count = sum(len(tv.transactions) for g in groups for tv in g.items)

    return VarianceReport(
        window_type=window.window_type,
        window_label=_build_window_label(window, period),
        groups=groups,
        figures=figures,
        transaction_count=txn_count,
    )


# ── Internal helpers ────────────────────────────────────────────────


def _validate_params(window: VarianceWindow) -> None:
    """Raise ValueError for an invalid or under-specified window."""
    if window.window_type not in _VALID_WINDOW_TYPES:
        raise ValueError(
            f"Invalid window_type {window.window_type!r}. "
            f"Must be one of {sorted(_VALID_WINDOW_TYPES)}."
        )
    if window.window_type == "pay_period" and window.period_id is None:
        raise ValueError("period_id is required when window_type is 'pay_period'.")
    if window.window_type == "month" and (window.month is None or window.year is None):
        raise ValueError("Both month and year are required when window_type is 'month'.")
    if window.window_type == "year" and window.year is None:
        raise ValueError("year is required when window_type is 'year'.")


@dataclass(frozen=True)
class _QueryScope:
    """The resolved account/scenario/exclusion scope shared by both queries.

    ``_get_transactions_for_window`` resolves these three once and passes
    them to whichever of :func:`_query_by_period` / :func:`_query_by_date_range`
    runs, so the two query paths cannot drift on what they scope to.
    """

    account_id: int
    scenario_id: int
    excluded_status_ids: frozenset[int]


def _get_transactions_for_window(
    user_id: int,
    window: VarianceWindow,
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
    # Routed through the centralized
    # ``balance_excluded_status_ids`` accessor (D6-09 / MED-02) so the
    # exclusion set is defined once rather than independently
    # re-derived here AND in ``year_end_summary_service``.
    scope = _QueryScope(
        account_id=account.id,
        scenario_id=scenario.id,
        excluded_status_ids=balance_excluded_status_ids(),
    )

    if window.window_type == "pay_period":
        return _query_by_period(scope, window.period_id)

    if window.window_type == "month":
        last_dom = cal_mod.monthrange(window.year, window.month)[1]
        first_day = date(window.year, window.month, 1)
        last_day = date(window.year, window.month, last_dom)
    else:
        first_day = date(window.year, 1, 1)
        last_day = date(window.year, 12, 31)

    txns = _query_by_date_range(scope, user_id, first_day, last_day)
    return txns, None


def _query_by_period(
    scope: _QueryScope,
    period_id: int,
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
            Transaction.account_id == scope.account_id,
            Transaction.scenario_id == scope.scenario_id,
            Transaction.pay_period_id == period_id,
            Transaction.is_deleted.is_(False),
            ~Transaction.status_id.in_(scope.excluded_status_ids),
        )
        .all()
    )
    return txns, period


def _query_by_date_range(
    scope: _QueryScope,
    user_id: int,
    first_day: date,
    last_day: date,
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
            Transaction.account_id == scope.account_id,
            Transaction.scenario_id == scope.scenario_id,
            Transaction.is_deleted.is_(False),
            ~Transaction.status_id.in_(scope.excluded_status_ids),
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
        txn_vars.sort(key=lambda t: abs(t.figures.variance), reverse=True)
        group_items[group_name].append(CategoryItemVariance(
            category_id=cat_id,
            group_name=group_name,
            item_name=item_name,
            figures=VarianceFigures.of(
                sum(t.figures.estimated for t in txn_vars),
                sum(t.figures.actual for t in txn_vars),
            ),
            transaction_count=len(txn_vars),
            transactions=txn_vars,
        ))

    # Build CategoryGroupVariance for each group.
    groups: list[CategoryGroupVariance] = []
    for group_name, items in group_items.items():
        items.sort(key=lambda i: abs(i.figures.variance), reverse=True)
        groups.append(CategoryGroupVariance(
            group_name=group_name,
            figures=VarianceFigures.of(
                sum(i.figures.estimated for i in items),
                sum(i.figures.actual for i in items),
            ),
            items=items,
        ))

    groups.sort(key=lambda g: abs(g.figures.variance), reverse=True)
    return groups


def _build_txn_variance(txn: Transaction) -> TransactionVariance:
    """Compute variance for a single transaction.

    Settled transactions use actual_amount (falling back to
    estimated_amount if actual is NULL).  Projected transactions
    use estimated_amount for both sides, yielding zero variance.
    """
    return TransactionVariance(
        transaction_id=txn.id,
        name=txn.name,
        figures=VarianceFigures.of(txn.estimated_amount, _compute_actual(txn)),
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
    window: VarianceWindow,
    period: PayPeriod | None = None,
) -> str:
    """Format the human-readable window label.

    Pay period: 'Jan 02 - Jan 15, 2026'.
    Month: 'January 2026'.
    Year: '2026'.
    """
    if window.window_type == "pay_period" and period is not None:
        start = period.start_date
        end = period.end_date
        return f"{start.strftime('%b %d')} - {end.strftime('%b %d')}, {end.year}"

    if window.window_type == "month" and window.month is not None and window.year is not None:
        month_name = date(window.year, window.month, 1).strftime("%B")
        return f"{month_name} {window.year}"

    if window.window_type == "year" and window.year is not None:
        return str(window.year)

    return ""
