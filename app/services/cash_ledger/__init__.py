"""
Shekel Budget App -- The cash LEDGER leaf: the facts a cash balance folds from.

The cash-side counterpart of :mod:`app.services.loan_ledger`, and deliberately
the same SHAPE.  A loan's balance is a fold over its event stream; so is a cash
account's (plan step X2, "a cash account is an event stream").  Both folds need
the same three things underneath them, and this package owns the cash copies:

===================  ==============================  ==========================
question             here                            loan analog
===================  ==============================  ==========================
what happened?       :mod:`._facts`                  ``loan_ledger._events``
what was it worth?   :mod:`._amounts`                ``loan_ledger._split``
what do they sum to? :mod:`._flows`                  (the fold, in the seam)
===================  ==============================  ==========================

Nothing here answers "what is the balance at T".  That is the
:mod:`app.services.balance_at` seam's question, and the arrow runs ONE way: the
producers import this package, and this package imports none of them.

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

Fence status, stated precisely because the two halves differ.  This package is
NOT on the W9906 call allowlist: it calls no balance producer, and W9906
correctly flags it if it ever tries.  It IS scoped for the W9909 completeness
check, so a new public function in ANY submodule must be classified as a
producer or a non-producer rather than defaulting to unguarded.  D1a's
adversarial review proved that half load-bearing: a cash balance-at-T folded
from these names touches no fenced NAME, so without the scope it -- and a route
rendering it -- both rated 10.00/10.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01).  Plain data
in, frozen dataclasses out; no Flask symbol, no writes.  All money is
:class:`~decimal.Decimal`.
"""

from ._amounts import income_amount, live_amount_overrides
from ._facts import (
    AnchorPoint,
    load_balance_transactions,
    resolve_anchor,
)
from ._flows import (
    PeriodSubtotal,
    period_subtotal,
    period_subtotals,
    sum_projected,
)

__all__ = [
    "AnchorPoint",
    "PeriodSubtotal",
    "income_amount",
    "live_amount_overrides",
    "load_balance_transactions",
    "period_subtotal",
    "period_subtotals",
    "resolve_anchor",
    "sum_projected",
]
