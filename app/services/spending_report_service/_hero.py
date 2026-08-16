"""The hero band: what this window spent, against its own history.

The window's settled spend total, versus the prior window and versus the
trailing-window average -- both baselines read off the SERIES
(:mod:`._window`) rather than recomputed, so the chart the user sees and the
chips beside it are two readings of one set.

Boundary discipline: no Flask import, no query.  All money is ``Decimal``.
"""

from decimal import Decimal

from app.models.transaction import Transaction
from app.services import spending_analysis
from app.utils.money import ZERO, round_money

from ._types import HeroFigures, Comparison, SeriesPoint
from ._window import _spent_total

# Number of prior same-type windows averaged for the hero's vs-average chip.
# It must stay BELOW ``_window._CHART_WINDOW_COUNT`` so this baseline derives
# from the same series the chart draws -- an invariant that spans two modules
# since the package split, and is therefore stated at both ends rather than
# only at the one that happens to be read first.
_TRAILING_WINDOW_COUNT = 6


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
