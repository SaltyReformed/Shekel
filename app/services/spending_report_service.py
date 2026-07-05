"""
Shekel Budget App -- Unified Spending Report Service (S-P1)

The producer behind the analytics Spending surface (S-P2 renders it).  It
answers "where did the money actually go" for one chosen window
(pay-period / month / year) and enriches each category with a drift signal:

* **Where It Went** -- settled expenses in the chosen window, grouped by
  category group with drill-down items, each carrying its dollar amount and
  its share of the window total (producer-computed; templates do no math).
  Generalizes the year-end summary's Section 3 category breakdown to any
  window.
* **Trend cell per category** -- the per-period sparkline series and the
  half-window delta chip, sourced from the SAME per-category series the
  Trends engine (:mod:`spending_trend_service`) computes, so the visual and
  the chip cannot disagree (the S-P1 build rule).  Each cell also carries a
  flat-guard so a sub-percent wiggle is not auto-scaled to full height.
* **Estimate surprises** -- the settled rows whose entered actual differed
  from the estimate (the Variance tab's one real signal, reused via the
  shared :func:`spending_analysis.resolved_actual_amount` kernel), a capped
  ranked list plus the net.
* **Top movers** -- the Trends engine's ranked up / down category lists.
* **Hero band** -- window spent total, versus the prior window of the same
  type, versus the trailing-window average, and payment timing (the
  year-end timeliness rule scoped to the window).

The Spending surface is MEASURED: settled-only, scoped to the user's active
checking account (the audit's target-IA row).  It carries the account
name / id and the settled-only flag so S-P2 can label the scope on screen.

Boundary discipline: no Flask import.  The route (S-P2) resolves the window
from query params and passes a :class:`SpendingWindow`; every figure is a
``Decimal`` (the sparkline series stays Decimal here -- ``float`` conversion
is S-P2's Chart.js boundary).  DB reads live in the service layer, mirroring
the sibling analytics producers.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import spending_analysis, spending_trend_service
from app.services.account_resolver import resolve_analytics_account
from app.services.pay_period_service import get_overlapping_periods
from app.services.scenario_resolver import get_baseline_scenario
from app.services.spending_trend_service import ItemTrend, TrendReport
from app.utils.money import ZERO, round_money

# Sparkline flat-guard: a per-period series whose spread (max - min) is under
# this fraction of its mean reads as flat, so S-P2 renders it centered rather
# than auto-scaling sub-percent noise to full cell height (the audit's
# Housing +0.4% exaggeration).  5% is a spread small enough that the drift is
# visual noise, not a shape worth stretching; the delta chip still shows the
# exact number, so nothing is hidden.
_FLAT_SPARK_RANGE_RATIO = Decimal("0.05")

# Longest surprises list the rail shows.  Matches the Trends engine's top-N
# convention so the two ranked rail sections read at the same length.
_MAX_SURPRISES = 5

# Number of prior same-type windows averaged for the hero's vs-average chip.
_TRAILING_WINDOW_COUNT = 6

_MONTHS_PER_YEAR = 12


# ── Data structures ─────────────────────────────────────────────────


@dataclass(frozen=True)
class SpendingWindow:
    """The time window a spending report is computed over.

    A discriminated selector mirroring
    :class:`app.services.budget_variance_service.VarianceWindow` and
    :class:`app.services.ledger_report_service.StatementWindow`:
    ``window_type`` decides which of the other fields are meaningful --
    ``period_id`` for ``"pay_period"``, ``month`` + ``year`` for
    ``"month"``, ``year`` for ``"year"``.  A deliberately separate value
    object per its own bounded context (the retrospective spending surface),
    so it does not couple to the Variance tab that is retired in S-P2; the
    route layer shares only the parameter parsing.

    Attributes:
        window_type: One of ``"pay_period"`` / ``"month"`` / ``"year"``.
        period_id: The pay period id (``"pay_period"`` windows only).
        month: The calendar month 1-12 (``"month"`` windows only).
        year: The calendar year (``"month"`` and ``"year"`` windows).
    """

    # Pylint: ``duplicate-code`` -- incidental structural similarity with the
    # ``VarianceWindow`` / ``StatementWindow`` four-field selectors.  They are
    # deliberately separate value objects in separate bounded contexts (a
    # spending window scopes settled-expense attribution; the others scope a
    # due-date variance and a confirmed-ledger paid-date basis), so a shared
    # base would couple three unrelated attribution rules (coding-standards
    # rule 13).  One-sided disable, mirroring the StatementWindow precedent.
    # pylint: disable=duplicate-code
    window_type: str
    period_id: int | None = None
    month: int | None = None
    year: int | None = None
    # pylint: enable=duplicate-code


@dataclass(frozen=True)
class Comparison:
    """A hero comparison: a baseline plus the signed delta and percent.

    Build one with :meth:`of` so the delta and percent are derived
    identically for both the vs-prior and vs-average chips, and every field
    is ``None`` together when the comparison has no baseline (no prior
    window, or no trailing windows to average).

    Attributes:
        baseline: The comparison baseline (prior spend, or trailing
            average), or ``None`` when no baseline exists.
        delta: ``current - baseline`` (signed), or ``None``.
        pct: ``delta`` as a percent of ``baseline``, or ``None`` -- also
            ``None`` when ``baseline`` is zero (an empty prior window).
    """

    baseline: Decimal | None
    delta: Decimal | None
    pct: Decimal | None

    @classmethod
    def of(cls, current: Decimal, baseline: Decimal | None) -> "Comparison":
        """Build a comparison of ``current`` against ``baseline``.

        Args:
            current: The chosen window's spend.
            baseline: The comparison baseline, or ``None`` when none exists
                (all three fields then come back ``None``).

        Returns:
            The :class:`Comparison`.  When ``baseline`` is zero the delta is
            still real (``current``) but ``pct`` is ``None`` (no percent of
            zero -- via :func:`spending_analysis.signed_pct`).
        """
        if baseline is None:
            return cls(baseline=None, delta=None, pct=None)
        delta = current - baseline
        return cls(
            baseline=baseline,
            delta=delta,
            pct=spending_analysis.signed_pct(delta, baseline),
        )


@dataclass(frozen=True)
class HeroFigures:
    """The Spending hero band.

    Attributes:
        spent_total: Total settled spend in the chosen window.
        vs_prior: Comparison against the immediately preceding window of the
            same type.
        vs_average: Comparison against the trailing same-type window average.
        payment_timing: The window's timeliness dict
            (``total_bills_paid`` / ``paid_on_time`` / ``paid_late`` /
            ``avg_days_before_due``), or ``None`` when no bill in the window
            has both a paid date and a due date.
    """

    spent_total: Decimal
    vs_prior: Comparison
    vs_average: Comparison
    payment_timing: dict | None


@dataclass(frozen=True)
class SparkTrend:
    """One category's trend cell: the sparkline series and its delta chip.

    ``series`` is the per-period spending series the Trends engine already
    computed for this category, and ``delta_pct`` / ``delta_abs`` /
    ``direction`` are the half-window metrics derived from THAT SAME series
    -- one data source, so the sparkline and the chip cannot disagree (the
    S-P1 build rule).  ``series`` stays ``Decimal`` here; S-P2 converts to
    ``float`` only at the Chart.js boundary.

    Attributes:
        series: The chronological per-period totals (zero-filled for empty
            periods) -- the sparkline's y-values.
        delta_pct: The half-window percent change, or ``None`` for an
            emerging ("New") category with no prior-half baseline.
        delta_abs: The half-window per-period dollar change (signed).
        direction: ``"up"`` / ``"down"`` / ``"flat"``.
        is_flat: ``True`` when the series spread is under
            :data:`_FLAT_SPARK_RANGE_RATIO` of its mean, so S-P2 renders the
            line flat/centered instead of auto-scaling noise.
    """

    series: list[Decimal]
    delta_pct: Decimal | None
    delta_abs: Decimal
    direction: str
    is_flat: bool


@dataclass(frozen=True)
class SpendingItemRow:
    """One 'Where It Went' drill-down item (a single category).

    Attributes:
        category_id: The category's id (``0`` for the Uncategorized bucket).
        item_name: The category item label.
        amount: Settled spend for this category in the window.
        share: ``amount`` as a fraction of the window total (a full-precision
            ``Decimal`` in ``[0, 1]``; templates render, never compute).
        trend: The category's :class:`SparkTrend`, or ``None`` when the
            category is not trendable in the Trends engine's rolling window
            (too little history, or below its materiality floor).
    """

    category_id: int
    item_name: str
    amount: Decimal
    share: Decimal
    trend: SparkTrend | None


@dataclass(frozen=True)
class SpendingGroupRow:
    """One 'Where It Went' group row with its drill-down items.

    Attributes:
        group_name: The category group label.
        amount: Settled spend for the whole group in the window.
        share: ``amount`` as a fraction of the window total.
        items: The group's :class:`SpendingItemRow` items, amount-descending.
        delta_pct: The group's spending-weighted half-window percent change
            from the Trends engine, or ``None`` when the group has no
            measurable trend.
        direction: The group's trend direction (``"flat"`` when no trend).
    """

    group_name: str
    amount: Decimal
    share: Decimal
    items: list[SpendingItemRow]
    delta_pct: Decimal | None
    direction: str


@dataclass(frozen=True)
class Surprise:
    """A settled row whose entered actual differed from its estimate.

    Attributes:
        transaction_id: The settled transaction's id.
        name: The transaction name.
        group_name: Its category group label.
        item_name: Its category item label.
        estimated: The estimate at entry.
        actual: The entered actual at settle.
        delta: ``actual - estimated`` (signed; positive = over estimate).
    """

    transaction_id: int
    name: str
    group_name: str
    item_name: str
    estimated: Decimal
    actual: Decimal
    delta: Decimal


@dataclass(frozen=True)
class Surprises:
    """The capped surprises list plus the net across ALL surprises.

    Attributes:
        rows: The surprises sorted by ``abs(delta)`` descending and capped at
            :data:`_MAX_SURPRISES`.
        net: The signed sum of EVERY surprise's delta (not just the capped
            rows) -- the window's net over/under estimate.
    """

    rows: list[Surprise]
    net: Decimal


@dataclass(frozen=True)
class Movers:
    """The Trends engine's ranked category movers for the rail.

    Attributes:
        up: The top increasing categories (:class:`ItemTrend`).
        down: The top decreasing categories (:class:`ItemTrend`).
    """

    up: list[ItemTrend]
    down: list[ItemTrend]


@dataclass(frozen=True)
class SpendingScope:
    """The page-context facts S-P2 renders as scope labels.

    Attributes:
        account_id: The checking account the report is scoped to.
        account_name: That account's display name (the on-screen scope
            label the audit's cross-cutting fix requires).
        settled_only: Always ``True`` -- the surface is measured
            (settled-only); carried so S-P2's measured chip reads it rather
            than hard-coding the basis.
        window_label: The human window label (e.g. ``"January 2026"``).
        trend_sufficiency: The Trends engine's banner state
            (``"insufficient"`` / ``"preliminary"`` / ``"sufficient"``) for
            the sparkline reliability caption.
    """

    account_id: int
    account_name: str
    settled_only: bool
    window_label: str
    trend_sufficiency: str


@dataclass(frozen=True)
class SpendingReport:
    """The complete Spending surface dataset for one window.

    Attributes:
        scope: The account / basis / window / sufficiency page context.
        hero: The hero band (spent, vs-prior, vs-average, payment timing).
        breakdown: The 'Where It Went' group rows, amount-descending.
        surprises: The capped estimate-surprises list and its net.
        movers: The top up / down category movers.
    """

    scope: SpendingScope
    hero: HeroFigures
    breakdown: list[SpendingGroupRow]
    surprises: Surprises
    movers: Movers


@dataclass(frozen=True)
class _ResolvedWindow:
    """A window resolved to the period set and date span it covers.

    ``first_day`` / ``last_day`` are ``None`` for a ``"pay_period"`` window
    (the period IS the span, so no date attribution filter is applied); for
    a ``"month"`` / ``"year"`` window they bound the COALESCE(due_date, pay
    period start) attribution.
    """

    period_ids: list[int]
    first_day: date | None
    last_day: date | None
    label: str


# ── Public API ──────────────────────────────────────────────────────


def compute_spending_report(
    user_id: int,
    window: SpendingWindow,
    *,
    trend_threshold: Decimal = Decimal("0.1000"),
) -> SpendingReport | None:
    """Compute the Spending surface dataset for *user_id* over *window*.

    Resolves the user's active checking account and baseline scenario, then
    builds the category breakdown, estimate surprises, hero band, and top
    movers over the chosen window's settled expenses -- enriched with the
    Trends engine's per-category series (over its own rolling window, scoped
    to the same account and threshold).

    Args:
        user_id: The owning user (scopes every query).
        window: The chosen :class:`SpendingWindow`.
        trend_threshold: The fractional flag threshold passed to the Trends
            engine (0-1; default 0.10 = 10%).

    Returns:
        The populated :class:`SpendingReport`, or ``None`` when the user has
        no active checking account or no baseline scenario (S-P2 renders an
        empty state).  A resolvable user whose window simply has no settled
        spend gets a populated report with an empty breakdown and a zero
        spent total (the documented empty shape), never ``None``.

    Raises:
        ValueError: If the window is an invalid type or omits a field its
            type requires (via
            :func:`app.services.spending_analysis.validate_window`).
    """
    spending_analysis.validate_window(
        window.window_type, window.period_id, window.month, window.year,
    )

    account = resolve_analytics_account(user_id, None)
    if account is None:
        return None
    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        return None

    resolved = _resolve_window(user_id, window)
    txns = _window_transactions(scenario.id, account.id, resolved)

    trend_report = spending_trend_service.compute_trends(
        user_id, trend_threshold, account.id,
    )

    return SpendingReport(
        scope=SpendingScope(
            account_id=account.id,
            account_name=account.name,
            settled_only=True,
            window_label=resolved.label,
            trend_sufficiency=trend_report.data_sufficiency,
        ),
        hero=_build_hero(user_id, account.id, scenario.id, window, txns),
        breakdown=_build_breakdown(txns, trend_report),
        surprises=_build_surprises(txns),
        movers=Movers(
            up=trend_report.top_increasing,
            down=trend_report.top_decreasing,
        ),
    )


# ── Window resolution ───────────────────────────────────────────────


def _resolve_window(user_id: int, window: SpendingWindow) -> _ResolvedWindow:
    """Resolve a window to its period ids, date span, and human label.

    A ``"pay_period"`` window resolves to its single period with no date
    span (the period is the window).  A ``"month"`` / ``"year"`` window
    resolves to every pay period overlapping its calendar span, filtered
    downstream by COALESCE(due_date, period start) attribution
    (:func:`_window_transactions`).

    Args:
        user_id: The owning user (scopes the overlapping-period lookup).
        window: The window to resolve.

    Returns:
        The :class:`_ResolvedWindow`.  ``period_ids`` is empty when a
        pay-period window's id resolves no row, or a calendar window
        overlaps no pay period (before the user's history).
    """
    if window.window_type == "pay_period":
        period = db.session.get(PayPeriod, window.period_id)
        if period is None:
            return _ResolvedWindow([], None, None, "")
        return _ResolvedWindow(
            [period.id], None, None, _window_label(window, period),
        )

    first_day, last_day = spending_analysis.calendar_window_bounds(
        window.window_type, window.year, window.month,
    )
    overlapping = get_overlapping_periods(user_id, first_day, last_day)
    return _ResolvedWindow(
        [p.id for p in overlapping],
        first_day,
        last_day,
        _window_label(window, None),
    )


def _window_label(window: SpendingWindow, period: PayPeriod | None) -> str:
    """Return the human label for a window.

    Args:
        window: The window to label.
        period: The resolved period for a ``"pay_period"`` window (``None``
            for calendar windows).

    Returns:
        ``"Feb 21 - Mar 06, 2026"`` (pay period), ``"January 2026"``
        (month), ``"2026"`` (year), or ``""`` when a pay-period window has
        no resolved period.
    """
    if window.window_type == "pay_period":
        if period is None:
            return ""
        return (
            f"{period.start_date:%b %d} - {period.end_date:%b %d}, "
            f"{period.end_date.year}"
        )
    if window.window_type == "month":
        return f"{date(window.year, window.month, 1):%B} {window.year}"
    return str(window.year)


def _window_transactions(
    scenario_id: int, account_id: int, resolved: _ResolvedWindow,
) -> list[Transaction]:
    """Load the settled expenses attributed to a resolved window.

    Reads the shared settled-expense query for the window's periods, then --
    for a calendar window -- keeps only rows whose COALESCE(due_date, pay
    period start) falls inside the window span.  This is the year-end
    Section 3 attribution rule generalized from a single year to any date
    span; a pay-period window applies no date filter (the period is the
    window).

    Args:
        scenario_id: The baseline scenario id.
        account_id: The checking account id.
        resolved: The resolved window (period ids + optional span).

    Returns:
        The window's settled expense :class:`Transaction` rows.
    """
    txns = spending_analysis.query_settled_expenses(
        scenario_id, resolved.period_ids, account_id,
    )
    if resolved.first_day is None:
        return txns
    return [
        txn for txn in txns
        if resolved.first_day <= _attribution_day(txn) <= resolved.last_day
    ]


def _attribution_day(txn: Transaction) -> date:
    """Return the day a settled expense is attributed to (unclamped).

    The COALESCE(due_date, pay period start) rule the year-end summary uses
    for calendar-year attribution, here yielding a full date so a calendar
    window can range-filter on it.

    Args:
        txn: The transaction (``pay_period`` eager-loaded by the query).

    Returns:
        ``txn.due_date`` when set, else the owning period's ``start_date``.
    """
    if txn.due_date is not None:
        return txn.due_date
    return txn.pay_period.start_date


# ── Breakdown ───────────────────────────────────────────────────────


def _build_breakdown(
    txns: list[Transaction], trend_report: TrendReport,
) -> list[SpendingGroupRow]:
    """Build the amount-descending 'Where It Went' group rows.

    Sums settled spend per category, joins each category to its Trends-engine
    :class:`ItemTrend` (for the sparkline + delta chip) and each group to its
    ``GroupTrend`` (for the group delta), and computes every row's share of
    the window total in the producer (templates do no math).

    Args:
        txns: The window's settled expenses.
        trend_report: The Trends engine result supplying per-category series
            and group deltas.

    Returns:
        The group rows, amount-descending, each with amount-descending items.
    """
    total = _spent_total(txns)
    item_trend_by_cat = {i.category_id: i for i in trend_report.all_items}
    group_trend_by_name = {
        g.group_name: g for g in trend_report.group_trends
    }

    items_by_group = _group_item_rows(txns, total, item_trend_by_cat)
    rows = [
        _group_row(group_name, items, total, group_trend_by_name.get(group_name))
        for group_name, items in items_by_group.items()
    ]
    rows.sort(key=lambda row: row.amount, reverse=True)
    return rows


def _group_item_rows(
    txns: list[Transaction],
    total: Decimal,
    item_trend_by_cat: dict[int, ItemTrend],
) -> dict[str, list[SpendingItemRow]]:
    """Sum settled spend per category into item rows, grouped by group name.

    Args:
        txns: The window's settled expenses.
        total: The window total (each row's share denominator).
        item_trend_by_cat: Per-category-id Trends-engine rows.

    Returns:
        ``group_name -> list[SpendingItemRow]`` (unsorted; the caller orders
        each group).
    """
    # Flat (group, category id, item) -> summed amount, keyed so an
    # Uncategorized row (category id 0) never collides with a real category.
    totals: dict[tuple[str, int, str], Decimal] = defaultdict(lambda: ZERO)
    for txn in txns:
        group_name, item_name = spending_analysis.category_names(txn)
        cat_id = txn.category_id if txn.category_id is not None else 0
        totals[(group_name, cat_id, item_name)] += abs(txn.effective_amount)

    items_by_group: dict[str, list[SpendingItemRow]] = defaultdict(list)
    for (group_name, cat_id, item_name), amount in totals.items():
        trend = item_trend_by_cat.get(cat_id)
        items_by_group[group_name].append(SpendingItemRow(
            category_id=cat_id,
            item_name=item_name,
            amount=amount,
            share=_share(amount, total),
            trend=_spark_trend(trend) if trend is not None else None,
        ))
    return items_by_group


def _group_row(
    group_name: str,
    items: list[SpendingItemRow],
    total: Decimal,
    group_trend,
) -> SpendingGroupRow:
    """Assemble one group row from its (to-be-sorted) item rows.

    Args:
        group_name: The category group label.
        items: The group's item rows (sorted in place, amount-descending).
        total: The window total (the group share denominator).
        group_trend: The group's Trends-engine ``GroupTrend``, or ``None``.

    Returns:
        The :class:`SpendingGroupRow`.
    """
    items.sort(key=lambda row: row.amount, reverse=True)
    group_amount = sum((row.amount for row in items), ZERO)
    return SpendingGroupRow(
        group_name=group_name,
        amount=group_amount,
        share=_share(group_amount, total),
        items=items,
        delta_pct=group_trend.pct_change if group_trend else None,
        direction=group_trend.trend_direction if group_trend else "flat",
    )


def _share(amount: Decimal, total: Decimal) -> Decimal:
    """Return ``amount / total`` as a full-precision fraction, or zero.

    Args:
        amount: The row's spend.
        total: The window's total spend (the share denominator).

    Returns:
        ``amount / total`` when ``total`` is positive, else ``Decimal("0")``
        (an empty window has no shares to compute).
    """
    if total <= ZERO:
        return ZERO
    return amount / total


def _spark_trend(item: ItemTrend) -> SparkTrend:
    """Build a category's sparkline + delta cell from its ``ItemTrend``.

    The series and the delta come from the SAME ``ItemTrend`` -- the series
    is the one the half-window delta was computed from -- so the sparkline
    and the chip cannot disagree.

    Args:
        item: The category's Trends-engine trend row.

    Returns:
        The :class:`SparkTrend` (series kept ``Decimal``; flat-guard applied).
    """
    return SparkTrend(
        series=item.period_totals,
        delta_pct=item.pct_change,
        delta_abs=item.absolute_change,
        direction=item.trend_direction,
        is_flat=_is_flat_series(item.period_totals),
    )


def _is_flat_series(series: list[Decimal]) -> bool:
    """Return ``True`` when a per-period series is visually flat.

    Flat means the spread (``max - min``) is under
    :data:`_FLAT_SPARK_RANGE_RATIO` of the series mean; an all-zero or
    empty series is flat by definition.  The guard governs only the
    sparkline's auto-scale (so sub-percent noise is not stretched to full
    height); the delta chip still shows the exact number.

    Args:
        series: The chronological per-period totals.

    Returns:
        ``True`` when the series should render flat/centered.
    """
    if not series:
        return True
    total = sum(series, ZERO)
    if total <= ZERO:
        return True
    mean = total / Decimal(len(series))
    spread = max(series) - min(series)
    return spread < mean * _FLAT_SPARK_RANGE_RATIO


# ── Surprises ───────────────────────────────────────────────────────


def _build_surprises(txns: list[Transaction]) -> Surprises:
    """Build the estimate-surprises list and its net over the window.

    A surprise is a settled row whose resolved actual (via the shared
    :func:`spending_analysis.resolved_actual_amount` kernel) differs from its
    estimate.  The list is ranked by ``abs(delta)`` descending and capped at
    :data:`_MAX_SURPRISES`; the net sums EVERY surprise's delta so the
    headline reflects the whole window, not just the shown rows.

    Args:
        txns: The window's settled expenses.

    Returns:
        The :class:`Surprises` (capped rows + full net).
    """
    surprises: list[Surprise] = []
    net = ZERO
    for txn in txns:
        actual = spending_analysis.resolved_actual_amount(txn)
        delta = actual - txn.estimated_amount
        if delta == ZERO:
            continue
        group_name, item_name = spending_analysis.category_names(txn)
        surprises.append(Surprise(
            transaction_id=txn.id,
            name=txn.name,
            group_name=group_name,
            item_name=item_name,
            estimated=txn.estimated_amount,
            actual=actual,
            delta=delta,
        ))
        net += delta

    surprises.sort(key=lambda s: abs(s.delta), reverse=True)
    return Surprises(rows=surprises[:_MAX_SURPRISES], net=net)


# ── Hero band ───────────────────────────────────────────────────────


def _build_hero(
    user_id: int,
    account_id: int,
    scenario_id: int,
    window: SpendingWindow,
    txns: list[Transaction],
) -> HeroFigures:
    """Build the hero band: spent total, vs-prior, vs-average, timing.

    The vs-prior comparison uses the immediately preceding same-type window;
    the vs-average uses the trailing :data:`_TRAILING_WINDOW_COUNT` same-type
    windows that exist (a window with pay periods but zero spend counts as
    zero; a window before the user's history is skipped).  Both degrade to a
    ``None`` comparison when no baseline exists.

    Args:
        user_id: The owning user.
        account_id: The checking account id.
        scenario_id: The baseline scenario id.
        window: The chosen window.
        txns: The chosen window's settled expenses (the spent-total source,
            reused so the hero and the breakdown agree by construction).

    Returns:
        The :class:`HeroFigures`.
    """
    spent_total = _spent_total(txns)

    prior_window = _shift_window(user_id, window, 1)
    prior_spent = (
        _window_spent_total(user_id, account_id, scenario_id, prior_window)
        if prior_window is not None else None
    )

    trailing: list[Decimal] = []
    for step in range(1, _TRAILING_WINDOW_COUNT + 1):
        shifted = _shift_window(user_id, window, step)
        if shifted is None:
            continue
        spent = _window_spent_total(
            user_id, account_id, scenario_id, shifted,
        )
        if spent is not None:
            trailing.append(spent)
    avg_spent = (
        round_money(sum(trailing, ZERO) / Decimal(len(trailing)))
        if trailing else None
    )

    return HeroFigures(
        spent_total=spent_total,
        vs_prior=Comparison.of(spent_total, prior_spent),
        vs_average=Comparison.of(spent_total, avg_spent),
        payment_timing=spending_analysis.payment_timeliness_from_txns(txns),
    )


def _shift_window(
    user_id: int, window: SpendingWindow, steps: int,
) -> SpendingWindow | None:
    """Return the window shifted ``steps`` back, or ``None``.

    For a ``"pay_period"`` window the shift walks ``period_index`` back by
    ``steps`` and returns ``None`` when no such earlier period exists (before
    the user's first period).  For a ``"month"`` / ``"year"`` window the
    shift is pure calendar arithmetic and always yields a window (whether it
    holds any data is decided by :func:`_window_spent_total`).

    Args:
        user_id: The owning user (scopes the pay-period lookup).
        window: The reference window.
        steps: How many windows to step back (>= 1).

    Returns:
        The shifted :class:`SpendingWindow`, or ``None``.
    """
    if window.window_type == "pay_period":
        current = db.session.get(PayPeriod, window.period_id)
        if current is None:
            return None
        prior = (
            db.session.query(PayPeriod)
            .filter(
                PayPeriod.user_id == user_id,
                PayPeriod.period_index == current.period_index - steps,
            )
            .first()
        )
        if prior is None:
            return None
        return SpendingWindow(window_type="pay_period", period_id=prior.id)

    if window.window_type == "month":
        year, month = _shift_month(window.year, window.month, steps)
        return SpendingWindow(window_type="month", month=month, year=year)

    return SpendingWindow(window_type="year", year=window.year - steps)


def _shift_month(year: int, month: int, steps: int) -> tuple[int, int]:
    """Return ``(year, month)`` shifted ``steps`` calendar months back.

    Args:
        year: The reference year.
        month: The reference month (1-12).
        steps: Months to step back (>= 1).

    Returns:
        The ``(year, month)`` pair, rolling the year over correctly (e.g.
        one month before January 2026 is December 2025).
    """
    absolute = year * _MONTHS_PER_YEAR + (month - 1) - steps
    return absolute // _MONTHS_PER_YEAR, absolute % _MONTHS_PER_YEAR + 1


def _window_spent_total(
    user_id: int,
    account_id: int,
    scenario_id: int,
    window: SpendingWindow,
) -> Decimal | None:
    """Return a comparison window's settled spend, or ``None``.

    Resolves the window and sums its settled expenses.  Returns ``None``
    when the window overlaps no pay period (before the user's history) so
    the caller excludes it from an average; a window with periods but no
    spend returns ``Decimal("0")`` (a real lean window that DOES count).

    Args:
        user_id: The owning user.
        account_id: The checking account id.
        scenario_id: The baseline scenario id.
        window: The comparison window.

    Returns:
        The window's settled spend, or ``None`` when it has no periods.
    """
    resolved = _resolve_window(user_id, window)
    if not resolved.period_ids:
        return None
    return _spent_total(_window_transactions(scenario_id, account_id, resolved))


def _spent_total(txns: list[Transaction]) -> Decimal:
    """Return total settled spend as a non-negative ``Decimal``.

    Args:
        txns: Settled expense transactions.

    Returns:
        The sum of ``abs(effective_amount)`` over ``txns``.
    """
    return sum((abs(txn.effective_amount) for txn in txns), ZERO)
