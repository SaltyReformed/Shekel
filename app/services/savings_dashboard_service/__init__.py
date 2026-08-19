"""
Shekel Budget App -- Savings Dashboard Service (package)

Orchestrates account balance projections, savings goal progress, and
emergency fund metrics for the savings dashboard.  Extracted from the
route handler (L-06) so the route contains only Flask request handling
and template rendering.  All functions accept plain data (user_id, ORM
objects) and return plain dicts/lists; no Flask imports.

Split of the historical monolithic ``savings_dashboard_service.py``
(1379 lines after the Phase 1 function decomposition) into a package of
per-concern modules, following the ``app/routes/salary/`` precedent.
The public entry point ``compute_dashboard_data`` is re-exported below so
``from app.services import savings_dashboard_service`` and
``savings_dashboard_service.compute_dashboard_data(...)`` resolve
unchanged.  Private helpers live in their sub-modules and are imported
from there directly (e.g. tests use
``from ...savings_dashboard_service._metrics import _get_dti_label``).

Module map:

* :mod:`app.services.savings_dashboard_service._types` -- the value objects.
  ``AccountProjection`` (with its ``LoanDetail``) is the per-account result
  EVERY surface in this package reduces over and both cockpit templates render;
  beside it are the request-scoped bundles (``_DashboardCoreData``,
  ``_ProjectionContext``, ``_LoanAccountResult``, ``_SeamBatches``).
  ``AccountProjection`` is deliberately NOT re-exported below: the route
  receives one from ``compute_account_balance_cell`` and forwards it to a
  template without naming the type, so it has no importer outside this package
  -- the same test ``DebtSummary``, ``GoalProgress`` and ``NetWorthRegion``
  pass and ``DtiMetrics`` failed.
* :mod:`app.services.savings_dashboard_service._data` -- batch data
  loaders (accounts / scenario / periods / transactions, the
  account-type parameter maps, archived accounts).
* :mod:`app.services.savings_dashboard_service._projections` -- the
  per-account balance projection dispatch (interest / loan / investment
  / default).
* :mod:`app.services.savings_dashboard_service._goals` -- savings-goal
  progress, contributions, and trajectory.
* :mod:`app.services.savings_dashboard_service._metrics` -- emergency-fund
  expenses, the debt summary + DTI, and the canonical paycheck-breakdown
  producer.
* :mod:`app.services.savings_dashboard_service._debt_line` -- the ONE
  derivation of "which loans still have a debt line" and "when does the last
  of them end" (plan step X-q), read by both ``_metrics``' debt summary and
  ``_horizon``'s domain and milestone flags, which each used to answer it
  with a membership rule of their own.
* :mod:`app.services.savings_dashboard_service._display` -- account
  grouping and the shared id-based category classifier.
* :mod:`app.services.savings_dashboard_service._net_worth` -- the
  ``2 years`` net-worth region: today figures, the trend series with its
  per-category composition split, and the per-account sparklines.
* :mod:`app.services.savings_dashboard_service._horizon` -- the ``Horizon``
  range producer (P-AC1 Loop B P1): the annual net-worth composition +
  net-trajectory series with milestone flags, reusing the /retirement
  engine, per-account growth params, and the loan resolver schedules.
* :mod:`app.services.savings_dashboard_service._orchestrator` --
  ``compute_dashboard_data`` (the full-page entry point),
  ``compute_debt_summary`` (the narrow debt producer behind the
  dashboard's debt track, rail marker included; deep-hunt #82, Loop B B-1),
  ``compute_goal_progress`` (the narrow savings-goal producer behind the
  dashboard's savings tracks), and ``compute_account_balance_cell`` (the
  narrow per-account producer behind the cockpit's inline-edit Cancel /
  Escape revert, ``savings.cockpit_balance``).  The dashboard tracks
  consumers all live in ``dashboard_service.compute_tracks_section``.
  There were TWO debt producers until plan step X-u (ruling R-BS, finding
  N-109) and the tracks section called both, so one render ran the debt
  pipeline twice; the marker's fraction is a ``DebtSummary`` field now.
"""

# Re-export the public entry points so consumers that
# ``from app.services import savings_dashboard_service`` (notably
# ``app/routes/savings.py`` and ``dashboard_service``) resolve
# them without an edit.
#
# ``DebtSummary`` is re-exported for the same reason (plan step X-s3): it
# crosses this package's boundary, so naming it here is what keeps its consumer
# off ``_metrics`` directly, which the W9910 package-privacy checker forbids.
# There were TWO such consumers until plan step X-u: ``dashboard_service._pulse``
# named the type to annotate the ``DebtTrack`` wrapper it composed, and X-u
# deleted both the wrapper and that import.  ONE is left -- the dashboard
# route's ``_DebtTrackView`` annotation (``app/routes/dashboard.py``) -- and one
# live consumer is all this line has ever needed.
#
# The first draft of this block also exported ``DtiMetrics`` and
# ``LoanPayoffOutlook``, and X-s3's adversarial review found neither had a
# single importer anywhere -- their in-package users take them from
# ``_debt_line`` / ``_metrics`` directly.  Exporting a name against a consumer
# that might one day want it is the defect that step deleted elsewhere;
# a future outside consumer adds its own line here, with a caller to point at.
#
# **``GoalProgress`` and ``NetWorthRegion`` joined at plan step X-w6** (ruling
# R-CN), because X-w put both in exactly ``DebtSummary``'s position and X-w's
# adversarial review found the rule stated here and not applied.
# ``compute_goal_progress`` publicly returns ``list[GoalProgress]`` and
# ``dashboard_service._pulse`` consumes it; ``NetWorthRegion`` is what
# ``compute_dashboard_data`` puts under ``net_worth`` and what
# ``app/routes/savings.py`` and both cockpit templates read.  Leaving them
# unexported is what made two signatures drop their type hints, against
# ``.claude/rules/coding.md`` -- an out-of-package annotation had no name it was
# allowed to say.
from app.services.savings_dashboard_service._goals import GoalProgress
from app.services.savings_dashboard_service._metrics import DebtSummary
from app.services.savings_dashboard_service._net_worth import NetWorthRegion
from app.services.savings_dashboard_service._orchestrator import (
    compute_account_balance_cell,
    compute_dashboard_data,
    compute_debt_summary,
    compute_goal_progress,
)

__all__ = [
    "DebtSummary",
    "GoalProgress",
    "NetWorthRegion",
    "compute_account_balance_cell",
    "compute_dashboard_data",
    "compute_debt_summary",
    "compute_goal_progress",
]
