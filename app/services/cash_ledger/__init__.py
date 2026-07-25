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
what was it worth?   :mod:`._amounts`                ``loan_ledger._split``
in what order?       :mod:`._walk`                   ``loan_ledger._walk``
what do they sum to? :mod:`._flows`                  (a peer reduction)
===================  ==============================  ==========================

Nothing here answers "what is the balance at T".  That is the
:mod:`app.services.balance_at` seam's question, and the arrow runs ONE way: the
producers import this package, and this package imports none of them.  The WALK
is facts (what happened, in the order it happened); the FOLD that samples those
facts at a date is the seam's, exactly as plan step D-fold split the loan pair.

**The walk (plan step X-a, ruling R-H).**  :mod:`._events` and :mod:`._walk`
build the account's ONE event stream -- every balance ASSERTION plus every
SETTLED row -- and replay it into per-assertion corrections, partitioned by
INSTANT so an assertion covers exactly the settles that preceded it.  Two
consumers will take it: the seam's read fold (step X-b) and, at step X-d, the
posting writer, replacing the postings-sourced
:func:`app.services.account_posting_service.walk_account_ledger`.  Until then the
two coexist deliberately and this one has no consumer at all -- it is additive,
so no balance can move because of it.  Still-Projected rows are NOT in the walk:
their effective date depends on the reader's as-of (ruling R-G), so they are the
seam fold's tier.

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

from ._amounts import (
    income_amount,
    live_amount_overrides,
    settled_cash_leg,
)
from ._events import (
    CashAnchorFact,
    CashSourceFact,
    attribution_instant,
    cash_anchor_facts,
    merge_anchor_and_cash_events,
    settled_cash_facts,
)
from ._facts import (
    AnchorPoint,
    load_balance_transactions,
    planned_cash_rows,
    resolve_anchor,
)
from ._flows import (
    PeriodSubtotal,
    period_subtotal,
    period_subtotals,
    sum_projected,
)
from ._walk import (
    CashAnchorCorrection,
    CashLedgerWalk,
    dated_deltas,
    walk_cash_ledger,
)

__all__ = [
    "AnchorPoint",
    "CashAnchorCorrection",
    "CashAnchorFact",
    "CashLedgerWalk",
    "CashSourceFact",
    "PeriodSubtotal",
    "attribution_instant",
    "cash_anchor_facts",
    "dated_deltas",
    "income_amount",
    "live_amount_overrides",
    "load_balance_transactions",
    "merge_anchor_and_cash_events",
    "period_subtotal",
    "period_subtotals",
    "planned_cash_rows",
    "resolve_anchor",
    "settled_cash_facts",
    "settled_cash_leg",
    "sum_projected",
    "walk_cash_ledger",
]
