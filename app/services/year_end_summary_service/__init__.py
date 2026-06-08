"""
Shekel Budget App -- Year-End Summary Service (package)

Aggregates a full calendar year of financial data for the year-end
summary tab.  Produces seven sections: income/tax breakdown (W-2-style),
mortgage interest, spending by category, transfers summary, net worth
trend (12 monthly points), debt progress, and savings progress, plus
payment-timeliness metrics (OP-2).

Primary use case is tax preparation -- the income/tax section mirrors
W-2 line items.  All monetary computation uses Decimal arithmetic.
This is a read-only aggregation service: no database writes, no Flask
request/session imports.

Split of the historical monolithic ``year_end_summary_service.py`` (2437
lines after the Phase 1 function decomposition) into a package of
per-concern modules, following the ``savings_dashboard_service/``
precedent.  The public entry point ``compute_year_end_summary`` is
re-exported below so ``from app.services import year_end_summary_service``
and ``year_end_summary_service.compute_year_end_summary(...)`` resolve
unchanged.  Private helpers live in their sub-modules and are imported
from there directly (e.g. tests use
``from ...year_end_summary_service._spending import _compute_entry_breakdowns``).

Module map:

* :mod:`._types` -- the loop-invariant bundle dataclasses
  (``_ProjectionInputs``, ``_YearContext``).
* :mod:`._data` -- batch data loaders (periods / accounts / salary
  profiles and the per-account parameter maps).
* :mod:`._periods` -- pay-period lookups and anchor-aware balance reads.
* :mod:`._balances` -- per-account balance projection dispatch
  (amortization / interest / investment / default) and schedule
  generation.
* :mod:`._income_tax` -- Section 1 (income/tax) and Section 2 (mortgage
  interest).
* :mod:`._spending` -- Section 3 (spending by category) and the OP-2
  payment-timeliness metrics.
* :mod:`._transfers` -- Section 4 (transfers summary).
* :mod:`._net_worth` -- Section 5 (net worth trend) and Section 6 (debt
  progress).
* :mod:`._savings` -- Section 7 (savings progress).
* :mod:`._orchestrator` -- ``compute_year_end_summary``, the public
  entry point.
"""

# Re-export the public entry point so consumers that
# ``from app.services import year_end_summary_service`` (notably
# ``app/routes/analytics.py`` and ``csv_export_service``) resolve
# ``compute_year_end_summary`` without an edit.
from app.services.year_end_summary_service._orchestrator import (
    compute_year_end_summary,
)

__all__ = ["compute_year_end_summary"]
