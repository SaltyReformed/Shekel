"""
Shekel Budget App -- The cash LEDGER leaf: the facts a cash balance folds from.

The cash-side counterpart of :mod:`app.services.loan_ledger`, and deliberately
the same SHAPE.  A loan's balance is a fold over its event stream; so is a cash
account's (plan step X-b, "a cash account is an event stream").  Both folds need
the same things underneath them, and this package owns the cash copies:

===================  ==============================  ==========================
question             here                            loan analog
===================  ==============================  ==========================
what is stored?      :mod:`._facts`                  ``loan_loaders``
what happened, when? :mod:`._events`                 ``loan_ledger._events``
what IS the amount?  :mod:`._amount_source`          (see below)
what was it worth?   :mod:`._amounts`                ``loan_ledger._split``
did the bank show it? :mod:`._clearing`              (no loan analog)
in what order?       :mod:`._walk`                   ``loan_ledger._walk``
what do they sum to? :mod:`._flows`                  (a peer reduction)
===================  ==============================  ==========================

**The clearing row has no loan analog because a loan has no bank statement to
clear against**: its balance is an amortization the app derives, and its
assertions are the user correcting that derivation.  A cash account's balance is
what a third party says it is, so *which statement showed this line* is a fact
the app must be told (ruling **R-FL**) rather than one it can compute.

**The third and fourth rows are two questions, not one split in half** (plan
step X-au-b, ruling **R-FI**).  :mod:`._amount_source` answers *where does this
row's amount come from* -- the figure the amount COLUMN holds or would hold,
by a total dispatch over the five sources a row's amount can have.
:mod:`._amounts` answers *what is this row worth to checking*, which composes
that amount with an entered actual, an excluded status and an envelope's
purchases.  The arrow runs one way: the second consumes the first.  The loan
side has no analog for the third row because a loan payment's amount has
exactly one source; a cash row's has five, and four of them are derived.

Nothing here answers "what is the balance at T".  That is the
:mod:`app.services.balance_at` seam's question, and the arrow runs ONE way: the
producers import this package, and this package imports none of them.  The WALK
is facts (what happened, in the order it happened); the FOLD that samples those
facts at a date is the seam's, exactly as plan step D-fold split the loan pair.

**The walk (plan step X-a, ruling R-H).**  :mod:`._events` and :mod:`._walk`
build the account's ONE event stream -- every balance ASSERTION, every SETTLED
row, and every PURCHASE whose bank posting day the owner recorded (ruling
**R-FM**, plan step X-f3b) -- and replay it into per-assertion corrections, each
source assigned to the assertion that CLEARED it (:mod:`._clearing`, ruling
R-FL) and, where no statement has recorded showing it, to the first assertion
dated on or after it (ruling R-DH).  The seam's
read fold TAKES it, and since plan step X-c2b2 every cash figure on every screen
is that fold sampled at a date.  The second consumer is still to come: at step
X-d the posting WRITER reads this walk too, replacing the postings-sourced
:func:`app.services.account_posting_service.walk_account_ledger`, which is what
makes a stale posting a detectable cache inconsistency rather than a second
opinion.  Still-Projected rows are NOT in the walk: their effective date depends
on the reader's as-of (ruling R-G), so they are the seam fold's tier.

**Why a package (plan step D1c).**  These names were spread across three flat
modules -- ``cash_events``, ``period_flows``, and five functions stranded inside
``balance_calculator``, a PRODUCER module (finding N-30).  The scattering had a
cost beyond tidiness: the fence could only cover them with a hand-written module
list (``_CASH_EVENT_SOURCE_MODULES``), which D1b's review found SELF-ATTESTING
-- deleting the constant and the registry entry together passed green while
re-opening the hole.  A package is scoped by ONE registry key that prefix-
matches, exactly as ``app.services.loan_ledger`` is, so a module created inside
it inherits the scope instead of escaping it.  That is the Section 8 lesson --
"a fail-CLOSED gate is scoped by module identity, so creating a module is how
you escape it" -- closed structurally rather than by a literal-string test.

Fence status (post-D3).  This package calls no balance producer -- since plan
step D3 the producers are private ``balance_at`` submodules, so any attempt to
reach one fails structurally at the import (W9910).  It IS scoped for the
W9909 completeness check, so a new public function in ANY submodule must be
classified as a producer or a non-producer rather than defaulting to
unguarded.  D1a's
adversarial review proved that half load-bearing: a cash balance-at-T folded
from these names touches no fenced NAME, so without the scope it -- and a route
rendering it -- both rated 10.00/10.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01).  Plain data
in, frozen dataclasses out; no Flask symbol, no writes.  All money is
:class:`~decimal.Decimal`.
"""

from ._amount_source import (
    AmountBasis,
    AmountRule,
    amount_basis,
    amount_rule,
    amounts_by_id,
    resolve_transaction_amount,
    resolve_transfer_amount,
)
from ._amounts import (
    ReconciledThrough,
    contributed_amount,
    contribution_of,
    contributions_by_id,
    credit_entry_sum,
    income_amount,
    live_amounts,
    live_override,
    owned_amount,
    owned_contribution,
    posted_purchase_sum,
    settled_cash_leg,
)
from ._clearing import (
    ClearableLine,
    StatementCoverage,
    statement_coverage,
)
from ._events import (
    CashAnchorFact,
    CashSourceFact,
    cash_anchor_facts,
    coverage_for,
    settled_cash_facts,
)
from ._facts import (
    AnchorPoint,
    governing_anchor,
    governing_anchor_on,
    planned_cash_rows,
    reconciled_through,
    resolve_anchor,
)
from ._flows import sum_projected
from ._walk import (
    CashAnchorCorrection,
    CashLedgerWalk,
    dated_deltas,
    walk_cash_ledger,
)

__all__ = [
    "AmountBasis",
    "AmountRule",
    "AnchorPoint",
    "governing_anchor",
    "governing_anchor_on",
    "CashAnchorCorrection",
    "CashAnchorFact",
    "CashLedgerWalk",
    "CashSourceFact",
    "ClearableLine",
    "ReconciledThrough",
    "StatementCoverage",
    "amount_basis",
    "amount_rule",
    "amounts_by_id",
    "cash_anchor_facts",
    "coverage_for",
    "contributed_amount",
    "contribution_of",
    "contributions_by_id",
    "credit_entry_sum",
    "dated_deltas",
    "income_amount",
    "live_amounts",
    "live_override",
    "owned_amount",
    "owned_contribution",
    "planned_cash_rows",
    "posted_purchase_sum",
    "reconciled_through",
    "resolve_anchor",
    "resolve_transaction_amount",
    "resolve_transfer_amount",
    "settled_cash_facts",
    "settled_cash_leg",
    "statement_coverage",
    "sum_projected",
    "walk_cash_ledger",
]
