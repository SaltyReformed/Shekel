"""
Shekel Budget App -- Loan Posting Service (Build-Order Step 4 + the read switch)

Posts a loan's confirmed history into the append-only double-entry ledger so its
balance is fully reconstructable as ``-(sum of its linked postings)`` -- the
genesis (opening-equity) design that lets the read switch retire the resolver's
read-time replay of confirmed history.  A loan's ledger is TWO kinds of balanced
correction, both PROJECTED from ONE deterministic running-balance walk
(:func:`app.services.loan_ledger.walk_loan_ledger`), invoked per sync -- so the
split and the anchor corrections agree on the balance interest accrues on, never
the drift separate walk implementations would risk:

* **Payment splits** (:mod:`._payments`, Step 4): the real principal / interest /
  escrow / refund split of each confirmed payment, layered as a correction on top
  of the Build-Order Step 2 cash entry so the loan nets to the real principal.
* **Anchor corrections** (:mod:`._anchors`, the read switch): the once-per-loan
  OPENING (``-original_principal`` vs. a per-loan opening-equity account) and
  every user balance TRUE-UP, so the from-origination sum-of-postings reproduces
  the resolver on a trued-up loan.

## Where the walk lives, and why it is not here

**This package is the GENERAL ledger -- the balance sheet, the statements, the
attribution.  It is not the answer to "what do I owe."**  The walk every
correction is projected from is :mod:`app.services.loan_ledger`, a LEAF this
package depends on: a loan's balance is a fold over its event stream, and these
postings are a PROJECTION of that fold onto the chart of accounts.  The walk
lived here until plan step B1, which put the general ledger in the position of
owning the balance and left every other consumer reaching through this package's
privates for the split.  The direction now runs one way, which is what lets the
posted rows become a CHECKED projection of the fold (``sum(postings) ==
fold(ACTUAL events)``, asserted per visible date at every sync since plan step
E1a) instead of the source of truth they were mistaken for.

## Package layout

Split by concern (the module outgrew the size limit; mirrors
:mod:`app.services.loan_resolver`).  The public surface is re-exported here so
``from app.services import loan_posting_service`` and ``loan_posting_service.X``
keep working unchanged:

* :mod:`._payments` -- the per-payment split reconcile + payment-only sync.
* :mod:`._anchors` -- the opening + true-up correction reconcile + anchor-only sync.
* :mod:`._sync` -- the UNIFIED per-scenario sync (``sync_loan_postings``: one walk,
  both reconciles), the loan-GLOBAL all-scenarios sync, the duplicate translation,
  and the historical backfill.
* :mod:`._reader` -- the ATTRIBUTION read side: what each settled payment's
  posted legs say it paid (interest / escrow / real principal per shadow), plus
  the shared load the payment-history table opens with.  It answers no balance:
  the two sum-of-postings balance readers that lived there
  (``confirmed_loan_balance_at`` / ``confirmed_loan_balance_map``) are DELETED
  at plan step E1e, having lost their last ``app/`` caller when the seam cut a
  loan's past onto the event FOLD (steps C3b1 / C3b3) and its confirmed schedule
  rows onto the walk (step E1d-b).  Their remaining job -- the independent window
  the fold and the resolver are graded against -- moved to the oracle's own side
  (``tests/_test_helpers.py``'s ``posted_loan_balance_at``), so no public balance
  producer exists outside the ``balance_at`` seam.  The paid-in-year tax / chip
  figures folded off the postings onto the loan ledger at steps C3c / C6c
  (:mod:`app.services.balance_at`), so this package no longer reads them either.

## Shared infrastructure and isolation

Books through :mod:`app.services.posting_service`'s shared balanced-write path
(``_emit_balanced_entry``), leg DTO (``_PostingLeg``), and linked-ledger resolver
(``_ledger_account_for``), so an unbalanced entry can never be written and every
source shares one leg convention.  The reconcile primitives shared with the
Step-5 account anchor package (``delta_legs`` / ``summed_posting_legs`` /
``posted_correction_legs`` / ``emit_correction_deltas``, which owns the
union-the-keys loop and the ``emit_anchor_correction_entry`` call inside it /
the owner resolver) live in :mod:`app.services._posting_reconcile`, so the two
correction packages can never drift on the delta math, the correction-entry
shape, or the reconcile loop itself.  Reuses the resolver's OWN pure primitives
(``resolve_periods`` / ``monthly_due_date``), so the posted ledger and the
resolver can never drift on the rate path or the anchor boundary.  Flask-isolated:
plain data in, plain values out; flushes but never commits (the caller owns the
transaction boundary).

**Write status.**  Both halves are wired at the go-forward chokepoints via the
unified :func:`sync_loan_postings` (loan-params create / edit, the balance
true-up, the ARM rate / origination-rate change, and every transfer settle /
revert / edit / delete / restore), so a loan's opening, true-ups, and confirmed
payments are all posted as they happen.  Every one of those doors then runs the
step-E1a assert, so a sync that reconciled the ledger away from the fold rolls
back at the write that caused it.

**Read status.**  No displayed balance reads these postings.  The read switch
that once pointed at them is retired: the balance seam folds a loan's past from
SOURCE events (steps C3b1 / C3b3) and its confirmed rows from the walk (step
E1d-b), so a cold or stale posting cache is a repairable inconsistency, never a
read outage.  What DOES read them is the general ledger's own presentation --
the balance sheet and statements (:mod:`app.services.ledger_report_service`) and
this package's per-payment attribution (:mod:`._display`).
"""

from ._anchors import sync_loan_anchor_corrections
from ._display import (
    LoanAnchorDrift,
    LoanPaymentHistoryRow,
    confirmed_loan_payment_history,
    loan_balance_anchor_history,
)
from ._payments import (
    reverse_loan_payment_postings_for_shadow,
    sync_loan_payment_postings,
)
from ._sync import (
    backfill_all_loan_postings,
    resync_user_loan_postings,
    sync_all_scenarios_or_duplicate,
    sync_loan_postings,
    sync_loan_postings_all_scenarios,
)

__all__ = [
    "LoanAnchorDrift",
    "LoanPaymentHistoryRow",
    "backfill_all_loan_postings",
    "confirmed_loan_payment_history",
    "loan_balance_anchor_history",
    "resync_user_loan_postings",
    "reverse_loan_payment_postings_for_shadow",
    "sync_all_scenarios_or_duplicate",
    "sync_loan_anchor_corrections",
    "sync_loan_payment_postings",
    "sync_loan_postings",
    "sync_loan_postings_all_scenarios",
]
