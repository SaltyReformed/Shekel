"""
Shekel Budget App -- Investment Dashboard Service (MED-01 / S6-01)

Pure-data orchestration for the investment / retirement dashboard
template (``investment/dashboard.html``) and the HTMX growth-chart
fragment (``investment/_growth_chart.html``).  Extracted from the route
bodies in Commit 28 (S6-01 / MED-01), collapsing the duplicated
salary-profile / deduction / contribution / projection-inputs loading
into one shared feed (:class:`._context._ProjectionContext`) so
``investment.py`` stays a thin delegator mirroring ``savings.py``.

**A PACKAGE since plan step X-g2b-1**, on the same four-part shape its sibling
``savings_dashboard_service`` has carried since Loop B: the shared feed
(:mod:`._context`), the cards beside the chart (:mod:`._cards`), the chart
itself (:mod:`._chart`), and the two orchestrators the route delegates to
(:mod:`._orchestrator`).  It split at the 1000-line module ceiling, which it
had reached EXACTLY -- so the next change of any size would have fired the gate
whatever it was.  Splitting on a cohesion line rather than trimming a docstring
to fit is plan step D1c's rule, and it leaves the two dashboards organised one
way instead of two.

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"):
this package imports no Flask symbol.  The route owns ``current_user`` /
``request`` / ``url_for`` and the HTTP responses; this service owns the
``Decimal``-money math, ORM queries, and projection-engine calls, and
returns plain dicts (``salary_profile_url`` is resolved route-side).

The initial dashboard chart and the HTMX fragment share ONE synthetic-period
basis at the slider default horizon (so they cannot disagree), carrying a
modeled-history series with Today / retirement markers; the measured
growth-since-anchor chip reconciles with the displayed balance via the
``balance_at`` seam.
"""

from ._cards import (
    CONTRIBUTION_FUNDING_DEDUCTION,
    CONTRIBUTION_FUNDING_NONE,
    CONTRIBUTION_FUNDING_TRANSFER,
)
from ._chart import compute_growth_chart_data
from ._orchestrator import compute_balance_hero_cell, compute_dashboard_data

__all__ = [
    "CONTRIBUTION_FUNDING_DEDUCTION",
    "CONTRIBUTION_FUNDING_NONE",
    "CONTRIBUTION_FUNDING_TRANSFER",
    "compute_balance_hero_cell",
    "compute_dashboard_data",
    "compute_growth_chart_data",
]
