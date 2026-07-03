"""
Shekel Budget App -- Loan Posting Service (Build-Order Step 4 + the read switch)

Posts a loan's confirmed history into the append-only double-entry ledger so its
balance is fully reconstructable as ``-(sum of its linked postings)`` -- the
genesis (opening-equity) design that lets the read switch retire the resolver's
read-time replay of confirmed history.  A loan's ledger is TWO kinds of balanced
correction, both computed by ONE deterministic running-balance walk function
(:mod:`._walk`), invoked per sync -- so the split and the anchor corrections
agree on the balance interest accrues on, never the drift separate walk
implementations would risk:

* **Payment splits** (:mod:`._payments`, Step 4): the real principal / interest /
  escrow / refund split of each confirmed payment, layered as a correction on top
  of the Build-Order Step 2 cash entry so the loan nets to the real principal.
* **Anchor corrections** (:mod:`._anchors`, the read switch): the once-per-loan
  OPENING (``-original_principal`` vs. a per-loan opening-equity account) and
  every user balance TRUE-UP, so the from-origination sum-of-postings reproduces
  the resolver on a trued-up loan.

## Package layout

Split by concern (the module outgrew the size limit; mirrors
:mod:`app.services.loan_resolver`).  The public surface is re-exported here so
``from app.services import loan_posting_service`` and ``loan_posting_service.X``
keep working unchanged:

* :mod:`._walk` -- the shared foundation: the single chronological walk that
  produces every correction, plus the split / correction dataclasses.
* :mod:`._common` -- the shared reconcile primitive (``delta_legs``) and the
  loan owner loader.
* :mod:`._payments` -- the per-payment split reconcile + payment-only sync.
* :mod:`._anchors` -- the opening + true-up correction reconcile + anchor-only sync.
* :mod:`._sync` -- the UNIFIED per-scenario sync (``sync_loan_postings``: one walk,
  both reconciles), the loan-GLOBAL all-scenarios sync, the duplicate translation,
  and the historical backfill.
* :mod:`._reader` -- the genesis READ side: a loan's balance as ``-(sum of its
  linked postings)`` (``confirmed_loan_balance_at`` / ``confirmed_loan_balance_map``,
  no anchor read, no boundary filter), the ACTUAL interest its confirmed
  payments paid in a year (``confirmed_loan_interest_in_year``, for Schedule A,
  attributed by the payment's display-timezone civil paid date -- the L9
  rule), and the ledger-derived
  amortization HISTORY rows (``confirmed_loan_history_rows``, each confirmed
  payment's actual principal / interest and running balance read from the
  posted legs).  Wired by the read switch.

## Shared infrastructure and isolation

Books through :mod:`app.services.posting_service`'s shared balanced-write path
(``_emit_balanced_entry``), leg DTO (``_PostingLeg``), and linked-ledger resolver
(``_ledger_account_for``), so an unbalanced entry can never be written and every
source shares one leg convention.  Reuses the resolver's OWN pure primitives
(``resolve_periods`` / ``monthly_due_date``), so the posted ledger and the
resolver can never drift on the rate path or the anchor boundary.  Flask-isolated:
plain data in, plain values out; flushes but never commits (the caller owns the
transaction boundary).

**Write status.**  Both halves are wired at the go-forward chokepoints via the
unified :func:`sync_loan_postings` (loan-params create / edit, the balance
true-up, the ARM rate / origination-rate change, and every transfer settle /
revert / edit / delete / restore), so a loan's opening, true-ups, and confirmed
payments are all posted as they happen.  Reads still flow through the resolver /
``balance_at`` seam until the read switch flips them onto ``sum(loan-ledger
postings)``; the anchor-correction postings are therefore inert on displayed
balances until then, and the ``LoanAnchorEvent`` write is retired only once every
reader has moved (the final read-switch commit).
"""

from ._anchors import sync_loan_anchor_corrections
from ._payments import (
    reverse_loan_payment_postings_for_shadow,
    sync_loan_payment_postings,
)
from ._reader import (
    confirmed_loan_balance_at,
    confirmed_loan_balance_map,
    confirmed_loan_history_rows,
    confirmed_loan_interest_in_year,
)
from ._sync import (
    backfill_all_loan_postings,
    resync_user_loan_postings,
    sync_all_scenarios_or_duplicate,
    sync_loan_postings,
    sync_loan_postings_all_scenarios,
)
from ._walk import (
    LoanAnchorCorrection,
    LoanLedgerWalk,
    LoanPaymentSplit,
    compute_loan_payment_splits,
    walk_loan_ledger,
)

__all__ = [
    "LoanAnchorCorrection",
    "LoanLedgerWalk",
    "LoanPaymentSplit",
    "backfill_all_loan_postings",
    "compute_loan_payment_splits",
    "confirmed_loan_balance_at",
    "confirmed_loan_balance_map",
    "confirmed_loan_history_rows",
    "confirmed_loan_interest_in_year",
    "resync_user_loan_postings",
    "reverse_loan_payment_postings_for_shadow",
    "sync_all_scenarios_or_duplicate",
    "sync_loan_anchor_corrections",
    "sync_loan_payment_postings",
    "sync_loan_postings",
    "sync_loan_postings_all_scenarios",
    "walk_loan_ledger",
]
