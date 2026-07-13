"""
Shekel Budget App -- Balance-at-T seam (Level 1, Option D Build-Order Step 1).

The single public way any screen obtains an account's balance over time.
Six producers historically answered "what is account A's balance at time
T?", and the three recompute-at-read kinds (loan, investment, property)
each bolted on their own rule for periods before an account's first known
data point; every new surface re-invented that boundary and shipped a bug
at least once.  This package owns all four per-kind boundary rules in ONE
place, documented and tested together (the documented-once contract):

* **PLAIN / INTEREST (cash)** -- pre-anchor periods are OMITTED.  Cash
  balances are materialized transaction sums carried forward from the
  anchor period; flat-carrying them backward would fabricate balances the
  account never had.
* **AMORTIZING (loan)** -- the genesis LEDGER for a date at or before today
  (the only complete record of the past: it books the balance events -- true-ups
  above all -- that never appear as schedule rows), and the forward schedule
  projection after.  Periods before the first scheduled payment, and every
  period of an empty / paid-off schedule, return the resolver's current_balance
  held flat -- NEVER the loan's original principal (which made the liability
  leap down the moment the first payment landed).
* **INVESTMENT** -- model-from-anchor: the anchor compounded forward at the
  assumed return (plus contributions) for post-anchor periods, and
  reverse-projected backward for pre-anchor periods.
* **APPRECIATING (property)** -- the user-set market value compounds
  forward at its annual rate; a manually-asserted valuation has no
  historical basis to compound backward from, so pre-anchor periods
  flat-carry the anchor value.

The seam does NOT reimplement any of that math.  It assembles each
account's inputs (its debt schedule, investment params, deductions, and the
engine gross-biweekly) from the shared loaders and DELEGATES the per-kind
dispatch to :func:`app.services.net_worth_kernel.build_account_balance_map`
-- the one dispatcher both the savings cockpit and the year-end summary
already build on.  Centralising the dispatch the two existing dispatchers
duplicate is the whole point: a third copy is exactly the duplication this
work exists to kill.

**Three shapes, one seam.**

* The KIND-CORRECT entries (:func:`balance_map`, :func:`build_maps`,
  :func:`balance_at`, plus the investment projection-input accessors
  :func:`investment_seed_map` / :func:`investment_growth_since_anchor`) dispatch
  per account kind -- a HYSA accrues interest, a loan walks its amortization
  schedule, an investment / property compounds -- the view the NET-WORTH
  surfaces (savings cockpit, year-end summary, dashboards) want.  See
  :mod:`._kind_correct`.
* The CASH-FLOW entries (:func:`cash_balance_map`, :func:`cash_balance_at`,
  :func:`cash_daily_balance_series`) always return the account's pure
  transaction running-balance with NO kind dispatch -- the view the
  single-account cash-flow surfaces (grid, obligations, calendar, checking
  detail) need, where the balance must reconcile with the on-screen transaction
  rows, and where the account is NOT guaranteed cash
  (``resolve_grid_account`` can point at any kind, so accruing interest into the
  grid balance row while its subtotal stays transaction-based would break
  ``balances[p] - balances[p-1] == subtotals[p].net``).  See :mod:`._cash_flow`,
  and :mod:`._grid` for the kind-aware :func:`grid_balance_view` that layers an
  INTEREST account's accrual back on for the grid.
* The LIABILITY entry (:func:`liability_owed_at_dates`) answers every debt's
  owed magnitude at a list of FORWARD calendar dates in one resolution pass --
  the shape a long-horizon liability band needs, which neither the period-keyed
  maps nor the scalar can serve without re-resolving each loan per date.  See
  :mod:`._liability`.

Every family routes through this one package, so no screen reaches a producer
directly (the W9906 ``shekel-balance-producer-bypass`` fence).

Package layout.  The seam outgrew a single module (the 1000-line cap), so each
view lives in its own private submodule and this ``__init__`` re-exports the
public surface -- consumers keep importing exactly as before
(``from app.services import balance_at``; ``balance_at.balance_map(...)``).  The
W9906 fence follows automatically: its allowlist prefix-matches, so every
``app.services.balance_at.*`` submodule stays inside the seam.

Dependency direction (SOLID).  Consumers (routes, savings, year-end,
dashboards) depend on this seam; the seam depends only on the engine cluster
(``net_worth_kernel`` / ``net_worth_investment`` / ``account_projection`` /
``balance_resolver`` / ``daily_balance_series`` / ``projection_inputs`` /
``income_service`` / ``pay_period_service``) and the models -- never a consumer
package.  Inside the package the direction is
``_grid -> {_cash_flow, _kind_correct} -> _inputs`` and ``_liability -> _inputs``
-- a DAG with ``_inputs`` as the single leaf, so no view module imports a
sibling that imports it back.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes.  All money
is :class:`~decimal.Decimal`; ``float`` only at a serialization boundary.

Liability classification is NOT a balance concern: the balance MAPS here are
balances only, and the net-worth sum's asset-plus / liability-minus rule lives
in :func:`~app.services.net_worth_kernel.sum_net_worth_at_period`.
(:func:`liability_owed_at_dates` is the one entry that takes a caller's
already-classified liability set and returns owed MAGNITUDES, matching that
same sum's ``abs`` convention.)
"""

from ._cash_flow import (
    cash_balance_at,
    cash_balance_map,
    cash_daily_balance_series,
)
from ._grid import GridBalanceView, _accruing_grid_view, grid_balance_view
from ._inputs import (
    ZERO,
    _account_balance_map,
    _AssembledInputs,
    _assemble_inputs,
    _require_scenario,
)
from ._kind_correct import (
    balance_at,
    balance_map,
    build_maps,
    investment_growth_since_anchor,
    investment_seed_map,
)
from ._liability import liability_owed_at_dates
from ._loan_figures import LoanFigures, loan_figures

# The seam's public surface, re-exported so every consumer's existing
# ``balance_at.<entry>`` attribute access keeps working unchanged after the
# module -> package split.  The underscore-prefixed names are
# internal-but-tested (``tests/test_services/test_balance_at.py`` reaches
# ``balance_at._assemble_inputs`` / ``._AssembledInputs`` /
# ``._account_balance_map`` / ``._accruing_grid_view`` to pin the assembly and
# accrual contracts directly); they are listed here so the re-export is
# explicit rather than an unused import.
__all__ = [
    "ZERO",
    "GridBalanceView",
    "LoanFigures",
    "_AssembledInputs",
    "_account_balance_map",
    "_accruing_grid_view",
    "_assemble_inputs",
    "_require_scenario",
    "balance_at",
    "balance_map",
    "build_maps",
    "cash_balance_at",
    "cash_balance_map",
    "cash_daily_balance_series",
    "grid_balance_view",
    "investment_growth_since_anchor",
    "investment_seed_map",
    "liability_owed_at_dates",
    "loan_figures",
]
