"""Which window, which rows, and the trailing series the hero is derived from.

The Spending report's first act: turn a :class:`._types.SpendingWindow` into
period ids, a date span and a human label; load that window's settled expenses;
and walk the same window type backwards to build the trailing series the chart
draws and the hero's vs-prior / vs-average chips are DERIVED from -- so the
chart and the chips cannot disagree.

:func:`_spent_total` lives here rather than with the hero because BOTH read it:
a window's total is what :func:`_load_window` answers for a prior window and
what the hero reports for the chosen one, and two spellings of "what did this
window spend" is the drift the shared series exists to prevent.

Boundary discipline: no Flask import; DB reads only, ``Decimal`` out.
"""

from decimal import Decimal

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import spending_analysis
from app.services.row_valuation import owned_contribution
from app.utils.money import ZERO

from ._types import (
    SeriesPoint,
    SpendingWindow,
    _ResolvedWindow,
    _ScopeIds,
)

# Bars on the hero chart: the chosen window plus its predecessors of the
# same type (D7: a trailing-12 month chart for the exposed month picker).
# Must exceed ``_hero._TRAILING_WINDOW_COUNT`` so the vs-average baseline
# derives from the same series the chart draws.
_CHART_WINDOW_COUNT = 12

_MONTHS_PER_YEAR = 12


def _resolve_window(ids: _ScopeIds, window: SpendingWindow) -> _ResolvedWindow:
    """Resolve a window to its period ids, date span, and human label.

    A ``"pay_period"`` window resolves to its single period with no date
    span (the period is the window).  A ``"month"`` / ``"year"`` window
    resolves to its calendar span -- the attribution fetch runs on the span
    itself (:func:`_window_transactions`) -- plus the overlapping periods
    as the tracked-window signal.

    **The pay-period arm reads the scope's CALENDAR, not the table** (plan
    step C2-f3a).  It was ``db.session.get(PayPeriod, window.period_id)``: a
    second read of ``budget.pay_periods`` on a render that already holds the
    owner's whole schedule, and an UNSCOPED one -- any owner's id resolved,
    and its dates went into the window label.  Nothing reachable submits a
    ``period_id`` to ``/analytics/spending`` today (the route exposes only
    month and year), so this hardens a door rather than closing a live leak;
    the calendar carries ONE owner's periods, so a foreign id now resolves
    nothing by construction.

    Args:
        ids: The report's scope, whose ``calendar`` answers both the overlap
            and the pay-period window's own identity lookup.
        window: The window to resolve.

    Returns:
        The :class:`_ResolvedWindow`.  ``period_ids`` is empty when a
        pay-period window's id names none of this owner's periods, or a
        calendar window overlaps none.  ``calendar_window_bounds`` never
        crosses its bounds (C2-f).
    """
    if window.window_type == "pay_period":
        period = ids.calendar.period_by_id(window.period_id)
        if period is None:
            return _ResolvedWindow([], None, None, "")
        return _ResolvedWindow(
            [period.period_id], None, None, spending_analysis.window_label(
                window.window_type, window.month, window.year, period,
            ),
        )

    first_day, last_day = spending_analysis.calendar_window_bounds(
        window.window_type, window.year, window.month,
    )
    return _ResolvedWindow(
        [p.period_id for p in ids.calendar.overlapping(first_day, last_day)],
        first_day, last_day, spending_analysis.window_label(
            window.window_type, window.month, window.year, None,
        ),
    )


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
    resolved = _resolve_window(ids, window)
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



def _spent_total(txns: list[Transaction]) -> Decimal:
    """Return total settled spend as a non-negative ``Decimal``.

    Args:
        txns: Settled expense transactions.

    Returns:
        The sum of ``abs(owned_contribution(txn))`` over ``txns``.
    """
    return sum((abs(owned_contribution(txn)) for txn in txns), ZERO)
