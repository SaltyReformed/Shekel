"""
Shekel Budget App -- the loan ledger: a loan's FACTS and the walk over them.

**A loan's balance is a fold over its event stream.**  This package owns the WALK
half of that -- the event stream, the split, and the running-balance replay over
them -- as the LEAF the rest of the loan architecture derives from.  It yields
FACTS (per-payment splits, per-anchor corrections in contract-time order); turning
those facts into a balance owed on a DATE -- the FOLD -- lives in the balance seam
(:mod:`app.services.balance_at._fold`) as of plan step **D-fold**, above this leaf.
*A fold is a balance; a walk is a fact*, and the two live on opposite sides of the
seam boundary.

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

    loan_posting_service (the general ledger)  ->  loan_ledger (the walk)
    balance_at (the read seam)                 ->  loan_ledger (the walk)

The walk lived INSIDE the posting package until step B0, which put the general
ledger in the position of owning the answer to "what do I owe" and left every
other consumer reaching through its privates for the split.  Moving it here is
what lets the ledger become a CHECKED PROJECTION of the walk
(``sum(postings) == fold(ACTUAL events)``, plan step E1) instead of the source of
truth it was mistaken for.  A consumer holding a walk cannot reach a balance from
any public name here -- the fold that would is seam-private -- which is why the
walk needs no fence (plan step D-fold).

## The modules

* :mod:`._split` -- the ONE split: how one payment's ACTUAL cash divides into
  interest / escrow / principal / refund.  Pure.  **It is TWO rules since plan
  step R16-a**, because fusing them made the payment COUNT the clock: charging a
  month's interest inside the per-payment step means N payments charge N months
  however far apart they fall.  :func:`apply_payment_cash` is the ALLOCATION
  alone -- cash against charges already standing -- and :func:`split_payment_cash`
  is the one-payment-per-month composition that charges a month first.
* :mod:`._events` -- the event stream: the loan's anchors and its settled
  payments, merged into one chronological order.  Reads no clock.
* :mod:`._walk` -- the running-balance replay over that stream (the loan's FACTS:
  per-payment splits and per-anchor corrections), the payment-split view of it,
  and the visible-date re-key of its events (:func:`dated_deltas`, step E1a --
  shared by the seam's fold and the posting writer's checked-projection assert
  so neither carries its own copy of the one clock).  Renamed from ``_fold`` at
  step D-fold, when the fold (the balance) left for the seam and only the walk
  (the facts) remained.
* :mod:`._visible` -- WHEN each fact becomes countable (the ONE clock).
  Chronology only: every name here returns a ``date``, never a figure.  It held
  the owner's CALENDAR and the date-to-period locator until plan step **C2-d**,
  which moved that question to :mod:`app.services.pay_calendar` -- see that
  module's own header for why all three names went at once.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes, no commits, and
-- since plan step **C2-d** -- **no database session of its own**.
``_visible.owner_pay_periods`` was the one query this package ever issued, and
C2-d deleted it.  *That is narrower than "the package is pure", and the
narrower claim is the true one*: :func:`walk_loan_ledger` still takes ids and
reaches :mod:`app.services.loan_loaders`, which holds the session, and
:func:`payment_visible_on` still takes an ORM row.  What changed is that no
name here OWNS a query and no public name returns an ORM
:class:`~app.models.pay_period.PayPeriod` -- which is what retires the fence
caveat :mod:`._visible` records.  All money is
:class:`~decimal.Decimal`.  Depends on the models, the money and date utilities
(:mod:`app.utils.money` / :mod:`app.utils.dates`), the loan LOADERS, and the pure
engines (``loan_resolver`` / ``escrow_calculator`` / ``rate_period_engine``) --
never on a consumer package, so nothing it needs can import it back.  Two
dependencies this list used to name are gone: ``account_projection`` at plan
step D1b (the leaf had been importing a kind CLASSIFIER to reach its own
chronology primitive) and :mod:`app.extensions` at C2-d, with the primitive
itself.

Plan of record: ``docs/audits/balance_architecture/README.md`` (step B1).
"""

from ._events import (
    confirmed_shadows_through,
    merge_anchor_and_payment_events,
)
from ._walk import (
    LoanAnchorCorrection,
    LoanLedgerWalk,
    compute_loan_payment_splits,
    dated_deltas,
    walk_loan_ledger,
)
from ._split import (
    LoanPaymentSplit,
    PaymentCashSplit,
    apply_payment_cash,
    split_one_payment,
    split_payment_cash,
)
from ._visible import (
    anchor_visible_on,
    payment_visible_on,
)

__all__ = [
    "LoanAnchorCorrection",
    "LoanLedgerWalk",
    "LoanPaymentSplit",
    "PaymentCashSplit",
    "anchor_visible_on",
    "apply_payment_cash",
    "compute_loan_payment_splits",
    "confirmed_shadows_through",
    "dated_deltas",
    "merge_anchor_and_payment_events",
    "payment_visible_on",
    "split_one_payment",
    "split_payment_cash",
    "walk_loan_ledger",
]
