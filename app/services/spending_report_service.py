"""
Shekel Budget App -- Unified Spending Report Service (S-P1, rebuilt for D7)

The producer behind the analytics Spending surface (the S14 "months lead"
cockpit renders it).  It answers "where did the money actually go" for one
chosen window (pay-period / month / year) on a window-over-window change
basis (D7 ruling 2026-07-10: month-over-month for the exposed month picker):

* **Trailing series** -- the chosen window plus its eleven predecessors of
  the same type, each with its settled spend total (``None`` for a window
  before the user's pay-period history).  The route serializes it for the
  hero band's emphasis month chart, and the hero's vs-prior / vs-average
  comparisons are DERIVED from this same series, so the chart and the chips
  cannot disagree.
* **Where It Went** -- settled expenses in the chosen window, grouped by
  category group with drill-down items, each carrying its dollar amount,
  its share of the window total, and its signed window-over-window dollar
  delta (producer-computed; templates do no math).
* **Changes** -- the flat By-change lens rows: every category with settled
  spend in the chosen window OR its prior window, with both totals and the
  signed delta, sorted by delta magnitude.  Categories with prior spend but
  none in the chosen window appear as zero-current rows (the D7
  zero-month-rows rider), so a bill that STOPPED is as visible as one that
  grew.
* **Estimate surprises** -- the settled rows whose entered actual differed
  from the estimate (the retired Variance tab's one real signal, reused via
  the shared :func:`spending_analysis.resolved_actual_amount` kernel), a
  capped ranked list plus the net.
* **Hero band** -- window spent total, versus the prior window, versus the
  trailing-window average, and payment timing (the year-end timeliness rule
  scoped to the window).

The former per-period trend enrichment (sparkline series, half-window delta
chips, top movers) retired with D7: the per-period trend basis misled on a
month-anchored page, so the surface's only change basis is now
window-over-window.

The Spending surface is MEASURED: settled-only, scoped to the user's active
checking account (the audit's target-IA row).  It carries the account
name / id and the settled-only flag so the template can label the scope on
screen.  Settled-only is why figures price through ``owned_contribution`` (X-au-c2).

Boundary discipline: no Flask import.  The route resolves the window from
query params and passes a :class:`SpendingWindow`; every figure is a
``Decimal`` (``float`` conversion is the route layer's Chart.js boundary).
DB reads live in the service layer, mirroring the sibling analytics
producers.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import spending_analysis
from app.services.account_resolver import resolve_analytics_account
from app.services.row_valuation import owned_contribution
from app.services.pay_period_service import get_overlapping_periods
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.money import ZERO, round_money

# Longest surprises list the rail shows, the ranked-rail top-N convention.
_MAX_SURPRISES = 5

# Number of prior same-type windows averaged for the hero's vs-average chip.
_TRAILING_WINDOW_COUNT = 6

# Bars on the hero chart: the chosen window plus its predecessors of the
# same type (D7: a trailing-12 month chart for the exposed month picker).
# Must exceed _TRAILING_WINDOW_COUNT so the vs-average baseline derives
# from the same series the chart draws.
_CHART_WINDOW_COUNT = 12

_MONTHS_PER_YEAR = 12


# ── Data structures ─────────────────────────────────────────────────


@dataclass(frozen=True)
class SpendingWindow:
    """The time window a spending report is computed over.

    A discriminated selector mirroring
    :class:`app.services.ledger_report_service.StatementWindow`:
    ``window_type`` decides which of the other fields are meaningful --
    ``period_id`` for ``"pay_period"``, ``month`` + ``year`` for
    ``"month"``, ``year`` for ``"year"``.  A deliberately separate value
    object per its own bounded context (the retrospective spending surface);
    the route layer shares only the parameter parsing.

    Attributes:
        window_type: One of ``"pay_period"`` / ``"month"`` / ``"year"``.
        period_id: The pay period id (``"pay_period"`` windows only).
        month: The calendar month 1-12 (``"month"`` windows only).
        year: The calendar year (``"month"`` and ``"year"`` windows).
    """

    # Pylint: ``duplicate-code`` -- incidental structural similarity with the
    # ``StatementWindow`` four-field selector.  They are deliberately separate
    # value objects in separate bounded contexts (a spending window scopes
    # settled-expense attribution; a statement window scopes a confirmed-ledger
    # paid-date basis), so a shared base would couple two unrelated attribution
    # rules (coding-standards rule 13).  One-sided disable so ``StatementWindow``
    # stays the un-disabled anchor.
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
class SeriesPoint:
    """One bar of the hero chart's trailing same-type window series.

    Attributes:
        window: The window this point covers, or ``None`` when the step
            walked past the user's pay-period history (a ``"pay_period"``
            window with no earlier period; calendar windows always shift).
        total: The window's settled spend, or ``None`` when the window
            overlaps no pay period (before the user's history) -- the chart
            renders such a point as a baseline tick, and the vs-average
            derivation excludes it.  A window with periods but no settled
            spend is ``Decimal("0")`` (a real lean window that DOES count
            toward the average).
    """

    window: SpendingWindow | None
    total: Decimal | None


@dataclass(frozen=True)
class SpendingItemRow:
    """One 'Where It Went' drill-down item (a single category).

    Attributes:
        category_id: The category's id (``0`` for the Uncategorized bucket).
        item_name: The category item label.
        amount: Settled spend for this category in the window.
        share: ``amount`` as a fraction of the window total (a full-precision
            ``Decimal`` in ``[0, 1]``; templates render, never compute).
        delta: ``amount`` minus the category's prior-window spend (signed;
            the D7 window-over-window change basis).
        is_new: ``True`` when the category had no prior-window spend, so the
            whole amount is new spending (rendered as a "new" badge instead
            of a percent of zero).
    """

    category_id: int
    item_name: str
    amount: Decimal
    share: Decimal
    delta: Decimal
    is_new: bool


@dataclass(frozen=True)
class SpendingGroupRow:
    """One 'Where It Went' group row with its drill-down items.

    Attributes:
        group_name: The category group label.
        amount: Settled spend for the whole group in the window.
        share: ``amount`` as a fraction of the window total.
        delta: ``amount`` minus the group's prior-window spend (signed).
            The prior side sums EVERY prior-window category in the group,
            including categories with no spend in the chosen window, so a
            group whose big bill stopped shows the drop.
        is_new: ``True`` when the group had no prior-window spend at all.
        items: The group's :class:`SpendingItemRow` items, amount-descending.
    """

    group_name: str
    amount: Decimal
    share: Decimal
    delta: Decimal
    is_new: bool
    items: list[SpendingItemRow]


@dataclass(frozen=True)
class ChangeRow:
    """One By-change lens row: a category's window-over-window movement.

    Every category with settled spend in the chosen window OR its prior
    window gets a row -- including zero-current rows (prior spend, none
    now; the D7 rider), so a stopped bill is as visible as a grown one.

    Attributes:
        category_id: The category's id (``0`` for the Uncategorized bucket).
        group_name: The category group label.
        item_name: The category item label.
        current: The chosen window's settled spend (``0`` when none).
        prior: The prior window's settled spend (``0`` when none).
        delta: ``current - prior`` (signed).
        is_new: ``True`` when ``prior`` is zero and ``current`` is not.
    """

    category_id: int
    group_name: str
    item_name: str
    current: Decimal
    prior: Decimal
    delta: Decimal
    is_new: bool


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
class SpendingScope:
    """The page-context facts the template renders as scope labels.

    Attributes:
        account_id: The checking account the report is scoped to.
        account_name: That account's display name (the on-screen scope
            label the audit's cross-cutting fix requires).
        settled_only: Always ``True`` -- the surface is measured
            (settled-only); carried so the measured chip reads it rather
            than hard-coding the basis.
        window_label: The human window label (e.g. ``"January 2026"``).
    """

    account_id: int
    account_name: str
    settled_only: bool
    window_label: str


@dataclass(frozen=True)
class SpendingReport:
    """The complete Spending surface dataset for one window.

    Attributes:
        scope: The account / basis / window page context.
        hero: The hero band (spent, vs-prior, vs-average, payment timing).
        series: The trailing same-type window series, oldest first, with
            the chosen window last (:data:`_CHART_WINDOW_COUNT` points).
        breakdown: The 'Where It Went' group rows, amount-descending.
        changes: The By-change lens rows, delta-magnitude-descending.
        surprises: The capped estimate-surprises list and its net.
    """

    scope: SpendingScope
    hero: HeroFigures
    series: list[SeriesPoint]
    breakdown: list[SpendingGroupRow]
    changes: list[ChangeRow]
    surprises: Surprises


@dataclass(frozen=True)
class _ResolvedWindow:
    """A window resolved to the period set and date span it covers.

    ``first_day`` / ``last_day`` are ``None`` for a ``"pay_period"`` window
    (the period IS the span; ``period_ids`` drives the fetch); for a
    ``"month"`` / ``"year"`` window they bound the COALESCE(due_date, pay
    period start) attribution fetch, and ``period_ids`` (the overlapping
    periods) serves only as the tracked-window signal for the None-vs-zero
    total rule (:func:`_window_total`).
    """

    period_ids: list[int]
    first_day: date | None
    last_day: date | None
    label: str


@dataclass(frozen=True)
class _CategoryTotal:
    """A category's settled spend in one window, with its display labels."""

    group_name: str
    item_name: str
    amount: Decimal


@dataclass(frozen=True)
class _ScopeIds:
    """The identifier triple every settled-spend window load is scoped by.

    One cohesive concept -- WHOSE data a window reads (the user's periods,
    the checking account, the baseline scenario) -- bundled so the window
    loaders take the scope as one argument instead of three parallel ids.
    """

    user_id: int
    account_id: int
    scenario_id: int


# ── Public API ──────────────────────────────────────────────────────


def compute_spending_report(
    user_id: int,
    window: SpendingWindow,
) -> SpendingReport | None:
    """Compute the Spending surface dataset for *user_id* over *window*.

    Resolves the user's active checking account and baseline scenario, then
    builds the trailing window series, the category breakdown with
    window-over-window deltas, the By-change rows, the estimate surprises,
    and the hero band over the chosen window's settled expenses.  The
    hero's vs-prior and vs-average baselines are derived from the series,
    so the chart and the chips agree by construction.

    Args:
        user_id: The owning user (scopes every query).
        window: The chosen :class:`SpendingWindow`.

    Returns:
        The populated :class:`SpendingReport`, or ``None`` when the user has
        no active checking account or no baseline scenario (the template
        renders an empty state).  A resolvable user whose window simply has
        no settled spend gets a populated report with an empty breakdown and
        a zero spent total (the documented empty shape), never ``None``.

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

    ids = _ScopeIds(
        user_id=user_id, account_id=account.id, scenario_id=scenario.id,
    )
    resolved = _resolve_window(user_id, window)
    txns = _window_transactions(ids, resolved)
    viewed_total = _window_total(resolved, txns)

    # The prior window loads once: its transactions feed the per-category
    # deltas AND its total feeds both the series' step-1 point and the
    # hero's vs-prior baseline (one load, three consumers that must agree).
    prior_window = _shift_window(user_id, window, 1)
    if prior_window is None:
        prior_txns: list[Transaction] = []
        prior_total = None
    else:
        prior_txns, prior_total = _load_window(ids, prior_window)

    series = _build_series(
        ids, window,
        viewed_total=viewed_total,
        prior_window=prior_window,
        prior_total=prior_total,
    )

    current_by_cat = _totals_by_category(txns)
    prior_by_cat = _totals_by_category(prior_txns)

    return SpendingReport(
        scope=SpendingScope(
            account_id=account.id,
            account_name=account.name,
            settled_only=True,
            window_label=resolved.label,
        ),
        hero=_build_hero(txns, series),
        series=series,
        breakdown=_build_breakdown(current_by_cat, prior_by_cat),
        changes=_build_changes(current_by_cat, prior_by_cat),
        surprises=_build_surprises(txns),
    )


# ── Window resolution ───────────────────────────────────────────────


def _resolve_window(user_id: int, window: SpendingWindow) -> _ResolvedWindow:
    """Resolve a window to its period ids, date span, and human label.

    A ``"pay_period"`` window resolves to its single period with no date
    span (the period is the window).  A ``"month"`` / ``"year"`` window
    resolves to its calendar span -- the attribution fetch runs on the span
    itself (:func:`_window_transactions`) -- plus the overlapping periods
    as the tracked-window signal.

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
    ids: _ScopeIds, resolved: _ResolvedWindow,
) -> list[Transaction]:
    """Load the settled expenses attributed to a resolved window.

    A calendar window selects by the attribution rule itself --
    COALESCE(due_date, pay period start) inside the window span, across
    ALL the user's periods
    (:func:`spending_analysis.query_settled_expenses_in_span`) -- so a
    bill due in month M belongs to M even when its funding period does not
    overlap M (the former period-overlap pre-filter attributed such a row
    to NO month window).  A pay-period window applies no date filter: the
    period IS the window.

    Args:
        ids: The report's scope ids.
        resolved: The resolved window (period ids + optional span).

    Returns:
        The window's settled expense :class:`Transaction` rows.
    """
    if resolved.first_day is None:
        return spending_analysis.query_settled_expenses(
            ids.scenario_id, resolved.period_ids, ids.account_id,
        )
    return spending_analysis.query_settled_expenses_in_span(
        ids.scenario_id, ids.account_id, ids.user_id,
        resolved.first_day, resolved.last_day,
    )


def _window_total(
    resolved: _ResolvedWindow, txns: list[Transaction],
) -> Decimal | None:
    """Return a loaded window's spend total, or ``None`` when untracked.

    ``None`` means the window overlaps no pay period AND holds no
    attributed rows (before the user's history) so callers exclude it from
    averages and the chart draws a tick.  A tracked window with no spend is
    ``Decimal("0")`` -- a real lean window that DOES count -- and a window
    that is untracked but still holds attributed rows (a due date outside
    every period's span) sums them rather than hiding real settled money.

    Args:
        resolved: The resolved window (the tracked signal).
        txns: The window's loaded settled expenses.

    Returns:
        The settled spend total, or ``None``.
    """
    if not resolved.period_ids and not txns:
        return None
    return _spent_total(txns)


def _load_window(
    ids: _ScopeIds, window: SpendingWindow,
) -> tuple[list[Transaction], Decimal | None]:
    """Resolve and load a window's settled expenses plus its spend total.

    Args:
        ids: The report's scope ids.
        window: The window to load.

    Returns:
        ``(transactions, total)``; ``total`` per :func:`_window_total`.
    """
    resolved = _resolve_window(ids.user_id, window)
    txns = _window_transactions(ids, resolved)
    return txns, _window_total(resolved, txns)


# ── Trailing series ─────────────────────────────────────────────────


def _build_series(
    ids: _ScopeIds,
    window: SpendingWindow,
    *,
    viewed_total: Decimal | None,
    prior_window: SpendingWindow | None,
    prior_total: Decimal | None,
) -> list[SeriesPoint]:
    """Build the trailing same-type window series, oldest first.

    The series is :data:`_CHART_WINDOW_COUNT` points: the chosen window
    (last) plus its predecessors, each stepped back with
    :func:`_shift_window`.  A step past the user's pay-period history (a
    ``"pay_period"`` walk that runs out of earlier periods) still occupies
    its slot with an all-``None`` point, so index arithmetic on the series
    is stable: the chosen window is always ``series[-1]`` and its prior is
    always ``series[-2]``.

    The chosen and prior windows' totals are passed in rather than
    re-loaded: the caller already loaded both windows' transactions for the
    breakdown and change rows, and reusing the totals keeps the chart bar,
    the hero figure, and the ledger summing one dataset.

    Args:
        ids: The report's scope ids.
        window: The chosen window (the series' last point).
        viewed_total: The chosen window's settled spend (``None`` when the
            window overlaps no pay period).
        prior_window: The step-1 window, or ``None`` when none exists.
        prior_total: The step-1 window's settled spend, or ``None``.

    Returns:
        The :class:`SeriesPoint` list, oldest first.
    """
    points: list[SeriesPoint] = []
    for step in range(_CHART_WINDOW_COUNT - 1, 0, -1):
        if step == 1:
            points.append(SeriesPoint(window=prior_window, total=prior_total))
            continue
        shifted = _shift_window(ids.user_id, window, step)
        if shifted is None:
            points.append(SeriesPoint(window=None, total=None))
            continue
        _, total = _load_window(ids, shifted)
        points.append(SeriesPoint(window=shifted, total=total))
    points.append(SeriesPoint(window=window, total=viewed_total))
    return points


def _shift_window(
    user_id: int, window: SpendingWindow, steps: int,
) -> SpendingWindow | None:
    """Return the window shifted ``steps`` back, or ``None``.

    For a ``"pay_period"`` window the shift walks ``period_index`` back by
    ``steps`` and returns ``None`` when no such earlier period exists (before
    the user's first period).  For a ``"month"`` / ``"year"`` window the
    shift is pure calendar arithmetic and always yields a window (whether it
    holds any data is decided by :func:`_load_window`).

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


# ── Category totals, breakdown, and change rows ─────────────────────


def _totals_by_category(txns: list[Transaction]) -> dict[int, _CategoryTotal]:
    """Sum settled spend per category id, carrying the display labels.

    Category id ``0`` is the Uncategorized bucket (rows with no category),
    so an uncategorized row never collides with a real category.  Labels
    come from the first row seen for the id: a real category id maps to
    exactly one ``(group, item)`` pair, and the Uncategorized bucket's
    labels are fixed by :func:`spending_analysis.category_names`.

    Args:
        txns: One window's settled expenses -- every row owns its figure.

    Returns:
        ``category_id -> _CategoryTotal`` (labels + summed spend).
    """
    amounts: dict[int, Decimal] = defaultdict(lambda: ZERO)
    labels: dict[int, tuple[str, str]] = {}
    for txn in txns:
        cat_id = txn.category_id if txn.category_id is not None else 0
        amounts[cat_id] += abs(owned_contribution(txn))
        if cat_id not in labels:
            labels[cat_id] = spending_analysis.category_names(txn)
    return {
        cat_id: _CategoryTotal(
            group_name=labels[cat_id][0],
            item_name=labels[cat_id][1],
            amount=amount,
        )
        for cat_id, amount in amounts.items()
    }


def _build_breakdown(
    current_by_cat: dict[int, _CategoryTotal],
    prior_by_cat: dict[int, _CategoryTotal],
) -> list[SpendingGroupRow]:
    """Build the amount-descending 'Where It Went' group rows.

    Groups the chosen window's per-category totals by group name, computes
    every row's share of the window total, and attaches the signed
    window-over-window delta per item and per group (the D7 change basis).
    A group's prior side sums EVERY prior-window category in that group --
    including categories with no current spend -- so a stopped bill still
    moves its group's delta.

    Args:
        current_by_cat: The chosen window's per-category totals.
        prior_by_cat: The prior window's per-category totals.

    Returns:
        The group rows, amount-descending, each with amount-descending items.
    """
    total = sum((cat.amount for cat in current_by_cat.values()), ZERO)

    items_by_group: dict[str, list[SpendingItemRow]] = defaultdict(list)
    for cat_id, cat in current_by_cat.items():
        prior = prior_by_cat.get(cat_id)
        prior_amount = prior.amount if prior is not None else ZERO
        items_by_group[cat.group_name].append(SpendingItemRow(
            category_id=cat_id,
            item_name=cat.item_name,
            amount=cat.amount,
            share=_share(cat.amount, total),
            delta=cat.amount - prior_amount,
            is_new=prior_amount == ZERO,
        ))

    prior_group_totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for cat in prior_by_cat.values():
        prior_group_totals[cat.group_name] += cat.amount

    rows = [
        _group_row(
            group_name, items, total, prior_group_totals[group_name],
        )
        for group_name, items in items_by_group.items()
    ]
    rows.sort(key=lambda row: row.amount, reverse=True)
    return rows


def _group_row(
    group_name: str,
    items: list[SpendingItemRow],
    total: Decimal,
    prior_group_amount: Decimal,
) -> SpendingGroupRow:
    """Assemble one group row from its (to-be-sorted) item rows.

    Args:
        group_name: The category group label.
        items: The group's item rows (sorted in place, amount-descending).
        total: The window total (the group share denominator).
        prior_group_amount: The group's prior-window spend across ALL its
            categories (zero when the group had none).

    Returns:
        The :class:`SpendingGroupRow`.
    """
    items.sort(key=lambda row: row.amount, reverse=True)
    group_amount = sum((row.amount for row in items), ZERO)
    return SpendingGroupRow(
        group_name=group_name,
        amount=group_amount,
        share=_share(group_amount, total),
        delta=group_amount - prior_group_amount,
        is_new=prior_group_amount == ZERO,
        items=items,
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


def _build_changes(
    current_by_cat: dict[int, _CategoryTotal],
    prior_by_cat: dict[int, _CategoryTotal],
) -> list[ChangeRow]:
    """Build the By-change rows over the union of both windows' categories.

    Every category with settled spend in either window gets a row, so a
    category that stopped (prior spend, zero current -- the D7 zero-month
    rider) is as visible as one that grew.  Labels prefer the chosen
    window's rows (a rename shows its current name); a zero-current row
    falls back to the prior window's labels.

    Args:
        current_by_cat: The chosen window's per-category totals.
        prior_by_cat: The prior window's per-category totals.

    Returns:
        The :class:`ChangeRow` list sorted by ``abs(delta)`` descending,
        ties broken by current spend descending, then item name.
    """
    rows: list[ChangeRow] = []
    for cat_id in current_by_cat.keys() | prior_by_cat.keys():
        current = current_by_cat.get(cat_id)
        prior = prior_by_cat.get(cat_id)
        labels = current if current is not None else prior
        current_amount = current.amount if current is not None else ZERO
        prior_amount = prior.amount if prior is not None else ZERO
        rows.append(ChangeRow(
            category_id=cat_id,
            group_name=labels.group_name,
            item_name=labels.item_name,
            current=current_amount,
            prior=prior_amount,
            delta=current_amount - prior_amount,
            is_new=prior_amount == ZERO and current_amount > ZERO,
        ))
    rows.sort(key=lambda r: (-abs(r.delta), -r.current, r.item_name.lower()))
    return rows


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
    txns: list[Transaction],
    series: list[SeriesPoint],
) -> HeroFigures:
    """Build the hero band: spent total, vs-prior, vs-average, timing.

    Both comparison baselines are DERIVED FROM THE SERIES so the hero chips
    and the chart cannot disagree: vs-prior reads the step-1 point
    (``series[-2]``), and vs-average averages the trailing
    :data:`_TRAILING_WINDOW_COUNT` points before the chosen window that
    exist (a point with pay periods but zero spend counts as zero; a point
    before the user's history is skipped).  Both degrade to a ``None``
    comparison when no baseline exists.

    Args:
        txns: The chosen window's settled expenses (the spent-total and
            payment-timing source, reused so the hero and the breakdown
            agree by construction).
        series: The trailing window series (chosen window last).

    Returns:
        The :class:`HeroFigures`.
    """
    spent_total = _spent_total(txns)
    prior_total = series[-2].total

    trailing = [
        point.total
        for point in series[-(_TRAILING_WINDOW_COUNT + 1):-1]
        if point.total is not None
    ]
    avg_spent = (
        round_money(sum(trailing, ZERO) / Decimal(len(trailing)))
        if trailing else None
    )

    return HeroFigures(
        spent_total=spent_total,
        vs_prior=Comparison.of(spent_total, prior_total),
        vs_average=Comparison.of(spent_total, avg_spent),
        payment_timing=spending_analysis.payment_timeliness_from_txns(txns),
    )


def _spent_total(txns: list[Transaction]) -> Decimal:
    """Return total settled spend as a non-negative ``Decimal``.

    Args:
        txns: Settled expense transactions.

    Returns:
        The sum of ``abs(owned_contribution(txn))`` over ``txns``.
    """
    return sum((abs(owned_contribution(txn)) for txn in txns), ZERO)
