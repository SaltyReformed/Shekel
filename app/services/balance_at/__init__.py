"""
Shekel Budget App -- Balance-at-T seam (Level 1, Option D Build-Order Step 1).

The single public way any screen obtains an account's balance over time.
Six producers historically answered "what is account A's balance at time
T?", and the three recompute-at-read kinds (loan, investment, property)
each bolted on their own rule for periods before an account's first known
data point; every new surface re-invented that boundary and shipped a bug
at least once.  This package owns all four per-kind boundary rules in ONE
place, documented and tested together (the documented-once contract):

* **PLAIN / INTEREST (cash)** -- a FOLD over the account's event stream
  (assertions + settled rows + the still-projected plan), so every period is
  answered: a past one reads the balance in force THEN, replayed from the
  assertions, and a period before the account's FIRST assertion reads that
  assertion back-projected over the records it already contains (ruling R-I).
  Pre-anchor periods used to be OMITTED, on the reasoning that a cash balance
  is a transaction sum carried forward from the anchor and flat-carrying it
  backward would fabricate balances the account never had -- true of the
  carry, but it left every past column blank while the scalar answered the
  same dates with TODAY's balance (finding cash D3 / B-18, closed at plan step
  X-c2b2).
* **AMORTIZING (loan)** -- ONE total producer, :func:`positions` (plan C3):
  the FOLD of the loan's recorded events (anchors + settled payments -- the
  complete record of the past, true-ups above all) for a date at or before the
  pass's as-of, and the forward PLAN fold (payment records, then contractual
  synthesis) after.  A date before any event folds to ``0.00`` (the loan does
  not exist yet), and a paid-off loan holds its folded ``0.00`` flat -- NEVER
  the loan's original principal (which made the liability leap down the moment
  the first payment landed).
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
dispatch -- to
:func:`app.services.balance_at._kernel.build_account_balance_map` for every
NON-loan kind, and to its own ``positions()`` fold for an AMORTIZING loan,
whose producer sits ABOVE the kernel and so cannot be dispatched from inside
it (plan step C3b3).  Centralising a dispatch that two consumers had each
grown their own copy of is the whole point: a third copy is exactly the
duplication this work exists to kill.

**Five shapes, one seam.**

* The KIND-CORRECT entries (:func:`balance_map`, :func:`build_maps`,
  :func:`balance_at`, plus the growth decomposition
  :func:`investment_growth_since_anchor`) answer "what is this account WORTH" --
  a HYSA accrues interest, a loan walks its amortization schedule, an investment
  / property compounds -- the view the NET-WORTH surfaces (the savings cockpit
  and the dashboards) want.  Since plan step X-g2b they dispatch on ONE
  question, not five: a configured loan is its ``positions()``, and every other
  kind is one event replay whose modelled tiers exist only if the account's own
  parameters put them there (ruling R-AD).  See :mod:`._kind_correct`.
  ``investment_seed_map`` was a sixth entry here and is GONE (ruling R-AE): a
  chart's pre-growth SEED existed only because the previous design could not
  express "this account's balance at a DATE", so a caller now reads
  :func:`balance_at` the day before its projection window opens and there is
  nothing to keep in step.
* The CASH-FLOW entries (:func:`cash_balance_map`, :func:`cash_balance_at`,
  :func:`cash_daily_balance_series`) always return the account's pure
  transaction running-balance with NO kind dispatch -- the view the
  single-account cash-flow surfaces (grid, dashboard pulse, calendar, cash
  detail) need, where the balance must reconcile with the on-screen transaction
  rows, and where the account is NOT guaranteed cash
  (``resolve_grid_account`` can point at any kind, so accruing a modelled
  return into the grid balance row without a row to explain it would leave a
  balance change the screen cannot account for -- ruling R-K's
  ``balance[p] - balance[p-1]
  == net[p] + reconciliation[p] + contribution[p] + accrual[p]``, whose
  FOURTH term is ruling R-AH's correction: a modelled asset has TWO modelled
  tiers, and on the real Empower 401(k) the CONTRIBUTION is the larger of them,
  so the three-term form breaks on 53 of 59 real period pairs).
  See :mod:`._cash_flow`,
  and :mod:`._grid` for the kind-aware :func:`grid_balance_view` that layers a
  modelled account's tiers back on for the grid.
* The LIABILITY entry (:func:`liability_owed_at_dates`) answers every debt's
  owed magnitude at a list of FORWARD calendar dates in one resolution pass --
  the shape a long-horizon liability band needs, which neither the period-keyed
  maps nor the scalar can serve without re-resolving each loan per date.  See
  :mod:`._liability`.
* The LOAN-FIGURES entry (:func:`loan_figures`) answers everything a loan tile
  wants BESIDE its balance -- the payment, the rate, the payoff date, whether it
  is retired -- and deliberately carries NO balance, so a consumer holding it
  cannot render a wrong one.  See :mod:`._loan_figures`.
* The SECURED-DEBT entry (:func:`secured_loan_series`) packs each loan a property
  secures into the rows its equity chart draws a debt line from.  That line is a
  balance-at-T series, and the assembly used to live in the property ROUTE, which
  had to hold a whole ``ResolvedLoan`` to do it -- one attribute read from an
  unfenced loan balance.  See :mod:`._secured_debt`.

Every family routes through this one package, so no screen reaches a producer
directly -- structurally, since plan step D3: every producer is a PRIVATE
submodule here, and the package-privacy gate W9910
(``shekel-private-module-import``) hard-fails any outside import of one in any
spelling.  That is what let the name-keyed producer fence delete: down to one
call surface at D3, and out of existence at plan step E1e, when the last public
balance producer outside this package (the genesis posting readers) was itself
deleted.

Package layout.  The seam outgrew a single module (the 1000-line cap), so each
view lives in its own private submodule and this ``__init__`` re-exports the
public surface -- consumers keep importing exactly as before
(``from app.services import balance_at``; ``balance_at.balance_map(...)``).
Re-exporting a name HERE is what makes it public: this file is the one
reviewed door a new seam entry ships through.

Dependency direction (SOLID).  Consumers (routes, savings, analytics,
dashboards) depend on this seam; the seam depends only on the outer engine
inputs (``account_projection`` / ``projection_inputs`` / ``income_service`` /
``pay_period_service``) and the ``cash_ledger`` leaf the cash producers fold
over, the LOAN leaves it composes the loan shapes from (``loan_resolver`` /
``loan_ledger`` / ``loan_loaders`` / ``loan_payment_service`` /
``amortization_engine``), and the models -- never a consumer package.  **Plan
step D1d moved ALL of the balance PRODUCERS INSIDE this package as private
submodules**, so the fence is one package boundary: the CASH chain
(``_cash_engine`` = the anchor-forward roll-up, ``_calculator`` = the pure
carry-forward walk it delegates to) and the NET-WORTH chain (``_kernel`` = the
per-kind balance dispatch, ``_investment`` = the growth / appreciation
sub-chain, ``_interest`` = the modelled accrual an INTEREST account layers on
its folded cash).  **Plan step X-c2b2 then made the cash FOLD (``_cash_fold``)
the one cash producer every view reads**, so ``_cash_engine`` / ``_calculator``
survive only for the investment and appreciation bases.  Plan step **X-g**
replaces those bases with the modelled-asset REPLAY and X-c2c4 then deletes both
modules; the window that was to stand between (X-c2c3) is CANCELLED, ruling
R-V.  **Plan step X-c2b3 then DELETED what the cutover replaced**: the
per-day producer ``_daily_series`` whole (the calendar's running-balance line is
the fold sampled at every day of its range), ``_cash_engine``'s date-precise
scalar ``balance_as_of_date`` with its prefix walk, and that module's
``BalanceResult`` wrapper -- whose ``stale_anchor_warning`` field the fold makes
unrepresentable, a settled row after the last assertion now MOVING the balance
rather than warning that it might not have (findings cash D1 / D2, N-50).
**Plan step D-ctx then moved the read
pass's resolution CONTEXT in too** (``_context`` = ``BalanceContext`` /
``require_scenario``, re-exported below as the seam's public read-pass handle):
it sits at the internal DAG's FLOOR, importing only the outer loan leaves it
memoizes and depended on by every producer that folds a loan.  **Plan step E1d-a
then moved the db-facing WHOLE-LOAN read in** (``_resolution`` =
``resolved_loan`` / ``ResolvedLoan`` / ``contractual_schedule_from_origination``,
formerly the public module ``app.services.loan_resolution``): its only production
caller was this seam, and step E1d makes its confirmed seed a FOLD, so the
composer that consumes a balance-at-T now sits on the same side of the boundary
as the producer of one.  It is re-exported NOWHERE -- W9910 alone protects it,
in every import spelling -- which is what lets its hand-written W9909
completeness scope DELETE rather than shrink or travel.  ``_kernel``'s ``debt_schedule_rows`` and
``interest_by_period_for_account`` are re-exported below as the two non-balance
seam entries the out-of-cluster consumers (the account-detail route, the savings
orchestrator) read.  ``property_equity_chart`` and ``home_equity_service`` import
FROM here, not the other way round.  Inside the package the direction is
``_grid -> {_cash_fold, _interest, _inputs}``,
``{_cash_flow, _kind_correct} -> _cash_fold -> _fold``,
``{_inputs, _positions, _loan_interest} -> _kernel -> _asset_fold ->
{_asset_contributions, _cash_fold}``, ``_kind_correct -> {_asset_fold,
_inputs}``,
``_liability -> _inputs``, ``_secured_debt -> {_loan_figures, _positions,
_inputs}``, and ``_loan_figures -> _positions -> _plan`` (the figures' payoff is
the fold to zero, plan step C8d) -- a DAG with ``_fold`` / ``_calculator`` /
``_interest`` / ``_investment`` at the producer floor, so no module imports a
sibling that imports it back.  Every loan producer also imports ``_resolution`` for the read
pass's ONE whole-loan read; ``_resolution`` imports only ``_context`` among its
siblings, plus ``_confirmed_view`` for the confirmed seed it threads into every
resolution (plan step E1d-b); ``_confirmed_view`` imports ``_context`` and
``_fold``, so that sub-chain is a DAG too.  ``_context`` sits
BELOW every floor: it imports NONE of its
siblings at runtime (``_plan``'s ``PlannedPayment`` and ``_resolution``'s
``ResolvedLoan`` are type-only edges typing the caches the seam FILLS), so the
arrow stays one-way.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes.  All money
is :class:`~decimal.Decimal`; ``float`` only at a serialization boundary.

Liability classification is NOT a balance concern: the balance MAPS here are
balances only, and the net-worth reduction lives with its consumers in
``savings_dashboard_service``.  Two of them turn a signed balance into an owed
MAGNITUDE with ``abs`` -- ``_net_worth.compute_net_worth_today`` (the hero) and
``_net_worth._sum_composition_at_period`` (per-period, banded; the net-worth
series derives assets / liabilities / net from those bands).  The third,
``_horizon._liability_band``, does NOT: it takes its magnitudes from
:func:`liability_owed_at_dates` below, which is the one seam entry that accepts
a caller's already-classified liability set and returns owed magnitudes on that
same ``abs`` convention.  All three classify asset-vs-liability through the one
``net_worth_account_data.is_liability_account`` home.
"""

from ._cash_flow import (
    cash_balance_at,
    cash_balance_map,
    cash_daily_balance_series,
)
from ._confirmed_view import confirmed_view
from ._context import BalanceContext, require_scenario
from ._grid import (
    GridBalanceView,
    GridColumn,
    GridRowFlags,
    empty_grid_view,
    grid_balance_view,
)
from ._inputs import (
    ZERO,
    _account_balance_map,
    _AssembledInputs,
    _assemble_inputs,
    _require_scenario,
)
from ._kernel import (
    debt_schedule_rows,
    interest_by_period_for_account,
    interest_projection_for_account,
)
from ._kind_correct import (
    balance_at,
    balance_map,
    build_maps,
    investment_growth_since_anchor,
)
from ._liability import liability_owed_at_dates
from ._loan_figures import (
    LoanFigures,
    LoanTerms,
    loan_figures,
    loan_terms,
)
from ._loan_interest import (
    loan_interest_in_year,
    loan_interest_paid_in_year,
    loan_principal_paid_in_year,
)
from ._positions import (
    loan_payoff_date,
    loan_required_extra,
    positions,
    positions_period_map,
)
from ._secured_debt import (
    TIER_CONFIRMED,
    TIER_ESTIMATED,
    TIER_PROJECTED,
    SecuredLoanSeries,
    secured_loan_series,
)

# The seam's public surface, re-exported so every consumer's existing
# ``balance_at.<entry>`` attribute access keeps working unchanged after the
# module -> package split.  The underscore-prefixed names are
# internal-but-tested (``tests/test_services/test_balance_at.py`` reaches
# ``balance_at._assemble_inputs`` / ``._AssembledInputs`` /
# ``._account_balance_map`` to pin the assembly contract directly); they are
# listed here so the re-export is explicit rather than an unused import.
# ``._accruing_balances`` was a fourth until plan step X-c2b2 deleted it: the
# grid's Interest row IS the accrual map ``_interest`` returns, not the
# difference of two independently-computed balance maps (finding N-52).
__all__ = [
    "ZERO",
    "BalanceContext",
    "GridBalanceView",
    "GridColumn",
    "GridRowFlags",
    "LoanFigures",
    "LoanTerms",
    "SecuredLoanSeries",
    "TIER_CONFIRMED",
    "TIER_ESTIMATED",
    "TIER_PROJECTED",
    "_AssembledInputs",
    "_account_balance_map",
    "_assemble_inputs",
    "_require_scenario",
    "balance_at",
    "balance_map",
    "build_maps",
    "cash_balance_at",
    "cash_balance_map",
    "cash_daily_balance_series",
    "confirmed_view",
    "debt_schedule_rows",
    "empty_grid_view",
    "grid_balance_view",
    "interest_by_period_for_account",
    "interest_projection_for_account",
    "investment_growth_since_anchor",
    "liability_owed_at_dates",
    "loan_figures",
    "loan_terms",
    "loan_interest_in_year",
    "loan_interest_paid_in_year",
    "loan_payoff_date",
    "loan_required_extra",
    "loan_principal_paid_in_year",
    "positions",
    "positions_period_map",
    "require_scenario",
    "secured_loan_series",
]
