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

* :mod:`app.services.savings_dashboard_service._types` -- the
  request-scoped / per-account bundle dataclasses (``_DashboardCoreData``,
  ``_ProjectionContext``, ``_LoanAccountResult``).
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
* :mod:`app.services.savings_dashboard_service._display` -- account
  grouping for the template.
* :mod:`app.services.savings_dashboard_service._orchestrator` --
  ``compute_dashboard_data`` (the full-page entry point),
  ``compute_debt_summary`` (the narrow debt-card producer behind the
  dashboard's debt track; deep-hunt #82),
  ``compute_debt_principal_progress`` (the narrow principal-paid fraction
  producer behind the dashboard's debt track marker; Loop B B-1), and
  ``compute_goal_progress`` (the narrow savings-goal producer behind the
  dashboard's savings tracks).  The dashboard consumers all live in
  ``dashboard_pulse_service.compute_tracks_section``.
"""

# Re-export the public entry points so consumers that
# ``from app.services import savings_dashboard_service`` (notably
# ``app/routes/savings.py`` and ``dashboard_service``) resolve
# them without an edit.
from app.services.savings_dashboard_service._orchestrator import (
    compute_dashboard_data,
    compute_debt_principal_progress,
    compute_debt_summary,
    compute_goal_progress,
)

__all__ = [
    "compute_dashboard_data",
    "compute_debt_principal_progress",
    "compute_debt_summary",
    "compute_goal_progress",
]
