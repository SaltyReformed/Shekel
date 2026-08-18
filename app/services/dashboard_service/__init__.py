"""
Shekel Budget App -- Dashboard Service (package)

The producers behind the Terminal Road budget dashboard (``/``), its two
HTMX fragments (``/dashboard/pulse``, ``/dashboard/balance``), and
nothing else.  All of them accept plain data and return plain
dicts / value objects; no Flask imports, no database writes.

**A PACKAGE since pay-calendar plan step C2-f2e, and the reason is that
it always was one.**  It shipped as two top-level modules,
``dashboard_service`` and ``dashboard_pulse_service``, split for one
stated reason -- "so neither module exceeds the 1000-line pylint cap" --
and the split cost what a split for a line count costs: the pulse module
imported FOUR private names across a module boundary
(``_DEFAULT_STALENESS_DAYS``, ``_get_user_settings``,
``_query_unpaid_expense_rows``, ``_resolve_section_context``), which is
package-private sharing spelled without a package.  The package-privacy
gate W9910 deliberately does not cover a private NAME in a public module,
so nothing could see it.  Here the same sharing is intra-package and
structural, the way ``savings_dashboard_service``,
``investment_dashboard_service`` and ``app/routes/grid/`` already state
it, and the public surface is this file.

Module map:

* :mod:`app.services.dashboard_service._section` -- WHAT the page is
  about: the account, the settings and the pay period, resolved once per
  render out of the route's one read pass.
* :mod:`app.services.dashboard_service._bills` -- the shared
  Projected-expense query and the render-ready bill dict (with the E-21
  single-base entry progress) both bill surfaces read.
* :mod:`app.services.dashboard_service._balance` -- the hero-shaped
  balance fragment the anchor editor's Cancel / Escape reverts to.
* :mod:`app.services.dashboard_service._pulse` -- the ``balanceChanged``
  refresh region: hero, chart, trough, peak, still-due, street, due-soon.
* :mod:`app.services.dashboard_service._tracks` -- the page-load-only
  position tier: the savings-goal metro tracks and the debt track.
"""

# ``DashboardSection`` is exported for the ANNOTATION, which is the test
# ``DebtSummary``, ``GoalProgress`` and ``NetWorthRegion`` passed in
# ``savings_dashboard_service`` and ``DtiMetrics`` failed: a type named in a
# public signature needs a name an out-of-package reader is allowed to say.
# **The route does NOT name it** -- it calls ``resolve_section`` and passes the
# value anonymously -- so the export rests on the annotation alone; an earlier
# draft of this comment claimed the route built one, and ``git grep
# DashboardSection`` outside this package refutes that.
from app.services.dashboard_service._balance import compute_balance_section
from app.services.dashboard_service._bills import txn_to_bill_dict
from app.services.dashboard_service._pulse import compute_pulse_section
from app.services.dashboard_service._section import (
    DashboardSection,
    resolve_section,
)
from app.services.dashboard_service._tracks import compute_tracks_section

__all__ = [
    "DashboardSection",
    "compute_balance_section",
    "compute_pulse_section",
    "compute_tracks_section",
    "resolve_section",
    "txn_to_bill_dict",
]
