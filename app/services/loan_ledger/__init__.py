"""
Shekel Budget App -- the loan ledger: a loan's balance is a fold over its events.

**A loan's balance is a fold over its event stream.**  This package owns that
fold -- the event stream, the split, and the running-balance walk over them -- as
the LEAF the rest of the loan architecture derives from.

```text
LoanEvent = (event_date, kind, payload)

kind = ASSERTION   balance := anchor_balance       (the opening + every true-up)
     | PAYMENT     balance -= split(cash).principal (settled transfer shadows)

walk_loan_ledger(loan, scenario) = replay(events, seeded at 0.00)
```

## Why it is a leaf, and what depends on it

The posting ledger (:mod:`app.services.loan_posting_service`) is the GENERAL
ledger -- the balance sheet, the statements, the attribution.  It is NOT the
answer to "what do I owe".  It projects THIS walk into the balanced corrections
it reconciles onto the chart of accounts, which is what makes the posted rows a
re-derivable projection of the loan's facts rather than a second opinion about
them.  The dependency runs one way::

    loan_posting_service (the general ledger)  ->  loan_ledger (the fold)
    balance_at (the read seam)                 ->  loan_ledger (the fold)

The walk lived INSIDE the posting package until this commit, which put the
general ledger in the position of owning the answer to "what do I owe" and left
every other consumer reaching through its privates for the split.  Moving it here
is what lets the ledger become a CHECKED PROJECTION of the fold
(``sum(postings) == fold(ACTUAL events)``, plan step E1) instead of the source of
truth it was mistaken for.

## The modules

* :mod:`._split` -- the ONE split function: how one payment's ACTUAL cash divides
  into interest / escrow / principal / refund.  Pure.
* :mod:`._events` -- the event stream: the loan's anchors and its settled
  payments, merged into one chronological order.  Reads no clock.
* :mod:`._fold` -- the running-balance walk over that stream, and the
  payment-split view of it.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes, no commits.  All
money is :class:`~decimal.Decimal`.  Depends only on the models, the loan
LOADERS, and the pure engines (``loan_resolver`` / ``escrow_calculator`` /
``rate_period_engine`` / ``account_projection``) -- never on a consumer package,
so nothing it needs can import it back.

Plan of record: ``docs/audits/balance_architecture/README.md`` (step B1).
"""

from ._events import (
    confirmed_shadows_through,
    merge_anchor_and_payment_events,
)
from ._fold import (
    LoanAnchorCorrection,
    LoanLedgerWalk,
    compute_loan_payment_splits,
    fold_from_walk,
    fold_loan_balances,
    walk_loan_ledger,
)
from ._split import LoanPaymentSplit, split_one_payment
from ._visible import (
    anchor_visible_on,
    owner_pay_periods,
    payment_visible_on,
    resolve_anchor_pay_period,
)

__all__ = [
    "LoanAnchorCorrection",
    "LoanLedgerWalk",
    "LoanPaymentSplit",
    "anchor_visible_on",
    "compute_loan_payment_splits",
    "confirmed_shadows_through",
    "fold_from_walk",
    "fold_loan_balances",
    "merge_anchor_and_payment_events",
    "owner_pay_periods",
    "payment_visible_on",
    "resolve_anchor_pay_period",
    "split_one_payment",
    "walk_loan_ledger",
]
