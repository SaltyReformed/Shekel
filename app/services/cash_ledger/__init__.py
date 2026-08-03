"""
Shekel Budget App -- The cash LEDGER leaf: the facts a cash balance folds from.

The cash-side counterpart of :mod:`app.services.loan_ledger`, and deliberately
the same SHAPE.  A loan's balance is a fold over its event stream; so is a cash
account's (plan step X-b, "a cash account is an event stream").  Both folds need
the same things underneath them, and this package owns the cash copies:

===================  ==============================  ==========================
question             here                            loan analog
===================  ==============================  ==========================
which day is it?     :mod:`._days`                   (the cash question alone)
what is stored?      :mod:`._facts`                  ``loan_loaders``
what happened, when? :mod:`._events`                 ``loan_ledger._events``
what was it worth?   :mod:`._amounts`                ``loan_ledger._split``
in what order?       :mod:`._walk`                   ``loan_ledger._walk``
what do they sum to? :mod:`._flows`                  (a peer reduction)
===================  ==============================  ==========================

:mod:`._days` is the package FLOOR and has no loan twin, because the question it
answers is the cash side's alone: a loan payment is dated by its own due date and
its anchors carry an explicit ``anchor_date``, while a cash movement and a
balance assertion are two DIFFERENT kinds of civil day that the engine must
compare -- and comparing them by hand is what cost production ``$4,001.42``
(ruling R-DH).  Since plan step X-d they are two TYPES with no ordering between
them (ruling R-DJ, closing finding N-135), so that comparison cannot be written.

Nothing here answers "what is the balance at T".  That is the
:mod:`app.services.balance_at` seam's question, and the arrow runs ONE way: the
producers import this package, and this package imports none of them.  The WALK
is facts (what happened, in the order it happened); the FOLD that samples those
facts at a date is the seam's, exactly as plan step D-fold split the loan pair.

**The walk (plan step X-a, ruling R-H).**  :mod:`._events` and :mod:`._walk`
build the account's ONE event stream -- every balance ASSERTION plus every
SETTLED row -- and replay it into per-assertion corrections, partitioned by
CIVIL DAY so an assertion covers exactly the settles dated on or before the day
it is the closing balance for (ruling R-DH).  The seam's
read fold TAKES it, and since plan step X-c2b2 every cash figure on every screen
is that fold sampled at a date.  **The second consumer arrived at plan step
X-d**: the posting WRITER
(:mod:`app.services.account_posting_service`) reads this walk too, and its own
postings-sourced twin was DELETED rather than left unwired -- which is what
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

from ._amounts import (
    ProjectedBasis,
    income_amount,
    live_amount_overrides,
    settled_cash_leg,
)
from ._days import MovedOn, ObservedOn, ReconciledThrough
from ._events import (
    CashAnchorFact,
    CashSourceFact,
    cash_anchor_facts,
    settled_cash_facts,
    settled_civil_day,
)
from ._facts import (
    AnchorPoint,
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
    "AnchorPoint",
    "CashAnchorCorrection",
    "CashAnchorFact",
    "CashLedgerWalk",
    "CashSourceFact",
    "MovedOn",
    "ObservedOn",
    "ProjectedBasis",
    "ReconciledThrough",
    "cash_anchor_facts",
    "dated_deltas",
    "income_amount",
    "live_amount_overrides",
    "planned_cash_rows",
    "reconciled_through",
    "resolve_anchor",
    "settled_cash_facts",
    "settled_cash_leg",
    "settled_civil_day",
    "sum_projected",
    "walk_cash_ledger",
]
