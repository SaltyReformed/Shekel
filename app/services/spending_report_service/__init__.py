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

## Package layout

It was ONE module until plan step X-au-c2b, and it sat at exactly its
1000-line ``too-many-lines`` ceiling -- finding **N-270**, recorded three days
earlier as "the NEXT edit to this module, any edit, trips C0302".  Routing its
budget reader through the amount model was that edit.  The split follows the
section banners the module already carried, so each surface's rules live beside
the shapes they fill (the :mod:`app.services.ledger_report_service` precedent;
N-270 named ``year_end_summary_service``, which commit ``3aecceb0`` had already
DELETED):

* :mod:`._types` -- every frozen result shape, plus the three private records
  the helpers pass between them.  No behaviour.
* :mod:`._window` -- window resolution, the settled-expense load, and the
  trailing series both the chart and the hero's baselines derive from.
* :mod:`._breakdown` -- Where It Went and the By-change lens, both reducing
  ONE pair of per-category totals.
* :mod:`._surprises` -- the estimate-surprises list and its net.
* :mod:`._hero` -- the hero band, reading its baselines off the series.

This module holds the ONE public entry point and re-exports the shapes a route
or a template names, so no consumer reaches into a submodule.
"""

from app.models.transaction import Transaction
from app.services import spending_analysis
from app.services.account_resolver import resolve_analytics_account
from app.services.pay_calendar import calendar_for
from app.services.scenario_resolver import get_baseline_scenario

from ._breakdown import _build_breakdown, _build_changes, _totals_by_category
from ._hero import _build_hero
from ._surprises import _build_surprises
from ._types import (
    ChangeRow,
    Comparison,
    HeroFigures,
    SeriesPoint,
    SpendingGroupRow,
    SpendingItemRow,
    SpendingReport,
    SpendingScope,
    SpendingWindow,
    Surprise,
    Surprises,
    _ScopeIds,
)
from ._window import (
    _build_series,
    _load_window,
    _resolve_window,
    _shift_window,
    _window_total,
    _window_transactions,
)

__all__ = [
    "ChangeRow",
    "Comparison",
    "HeroFigures",
    "SeriesPoint",
    "SpendingGroupRow",
    "SpendingItemRow",
    "SpendingReport",
    "SpendingScope",
    "SpendingWindow",
    "Surprise",
    "Surprises",
    "compute_spending_report",
]


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
        ValueError: An invalid window (``validate_window``), or -- as its
            ``PayCalendarError`` subclass, newly reachable at plan step C2-f1
            -- an owner whose paydays cannot define a calendar (finding **P8**).
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
        calendar=calendar_for(user_id),
    )
    resolved = _resolve_window(ids, window)
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
