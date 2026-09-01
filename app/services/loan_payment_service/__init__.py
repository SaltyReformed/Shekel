"""
Shekel Budget App -- Loan Payment Service.

Reads a debt account's payments and answers what one installment costs.  Four
leaves, one per verb, and the public surface is re-exported here so no caller
learns the split:

  * :mod:`._engine_prep` -- the corrections a recorded payment needs before the
    amortization engine can replay it (escrow subtraction, biweekly
    redistribution).  Imports no sibling.
  * :mod:`._basis` -- what ONE installment costs: the loan's monthly P&I and
    payment day, and the DERIVE / MANUAL rules that price a shadow against
    them.  Imports no sibling.
  * :mod:`._context` -- the account's loaded context and its
    :class:`PaymentRecord` feed.  Sits above ``_engine_prep``.
  * :mod:`._pricing` -- amount rule 4's read-pass derivation
    (:class:`LoanPricing`).  Sits above ``_basis``.

**Why it is a PACKAGE.**  This was one module and it stood at 1009 of pylint's
1000-line ceiling once the pricing cycle's deletion was argued in the
docstrings that argue it.  It is the SIXTH module in this codebase to reach
that cap after ``transfer_service`` (**N-152**), ``pay_period_locks``
(**N-156**), ``anchor_service`` (**N-201**), pay-calendar **P31** and
``transaction_service`` -- and every one of those rows says the same thing:
three lines of headroom is not a design, and the structural answer is a package
with one private leaf per verb.  Shaving the prose was the alternative and this
project has already ruled against it five times, so this makes the shape
instead.

**The dependency graph is a line, not a web**, which is what makes the cut by
verb rather than by size: ``_engine_prep`` and ``_basis`` import no sibling,
``_context`` imports ``_engine_prep``, ``_pricing`` imports ``_basis``.  Nothing
imports upward, so there is no cycle to keep out by convention.

This package reads the POSTED LEDGER NOWHERE, as of plan step E1d-b
(``docs/audits/balance_architecture/README.md``).  It used to host
``confirmed_loan_view``, the read switch's single injection point into the
genesis posting readers, which made it the one module whose resolver-feeding
loaders had to be fenced at FUNCTION granularity to keep the reconciliation
oracle's parallel run honest.  The loan resolver's confirmed slice now seeds
from the event WALK inside the balance seam (``balance_at.confirmed_view``), so
that allowlist is gone and this package is ledger-free whole.  The whole-loan
read that composes these loaders with the pure resolver lives in
``app.services.balance_at._resolution``; that module imports THIS one, and this
one imports nothing from the seam, so there is no cycle.

Shadow income transactions represent payments received by a debt account via
transfers.  When a user transfers money from checking to a mortgage account,
the transfer service creates two shadow transactions: an expense on checking
(money out) and an income on the mortgage (money in).  The payment feed reads
the income side (via the :mod:`app.services.loan_loaders` leaf, which owns the
row loaders and the shadow-income query this package used to host) to discover
all payments into a loan account.

**What queries what, corrected.**  This file used to assert that the service
"queries ONLY budget.transactions (transfer invariant #5); it NEVER queries
budget.transfers", and that was FALSE where it mattered most -- at the top of
the file, about a critical invariant.  :func:`._pricing._load_live_payment_configs`
queries ``budget.transfers``, joined through the template to
``loan_payment_settings``, and must: discovering WHICH transfers are loan
payments is a question about that table.  Invariant 5 binds the BALANCE
CALCULATOR ("Balance calculator queries ONLY budget.transactions"), not every
loan reader, and the payment-history tier (:mod:`._context`) does honour the
narrower rule -- its feed comes from ``budget.transactions`` alone.

Shared by:
  - ``app/routes/loan/`` (dashboard and payoff calculator)
  - ``app/services/savings_dashboard_service.py`` (savings projections)
  - ``app/services/year_end_summary_service.py`` (annual aggregation)
  - ``app/routes/debt_strategy.py`` (debt payoff strategies)
"""

from ._basis import _resolve_loan_basis
from ._context import LoanContext, get_payment_history, load_loan_context
from ._engine_prep import compute_contractual_pi, prepare_payments_for_engine
from ._pricing import LoanPricing, loan_pricing

__all__ = [
    "LoanContext",
    "LoanPricing",
    "compute_contractual_pi",
    "get_payment_history",
    "load_loan_context",
    "loan_pricing",
    "prepare_payments_for_engine",
    # Re-exported PRIVATE, deliberately: the cycle-deletion controls
    # (``test_loan_payment_service.TestALoansPriceDoesNotReadItsOwnPayments``
    # and ``tests/manual/verify_loan_pricing_ignores_payment_feed.py``) assert
    # that this producer reads the loan's terms and issues no statement against
    # ``budget.transactions``.  A test outside the package may not import
    # ``._basis`` directly -- ``shekel-private-module-import`` forbids reaching
    # into a package's private MODULES -- so the name is published here, which
    # that checker permits and which keeps the leaf boundary intact.
    "_resolve_loan_basis",
]
