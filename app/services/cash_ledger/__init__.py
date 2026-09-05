"""
Shekel Budget App -- The cash LEDGER leaf: the facts a cash balance folds from.

The cash-side counterpart of :mod:`app.services.loan_ledger`, and deliberately
the same SHAPE.  A loan's balance is a fold over its event stream; so is a cash
account's (plan step X-b, "a cash account is an event stream").  Both folds need
the same things underneath them, and this package owns the cash copies:

==============================  ========================  ======================
question                        here                      loan analog
==============================  ========================  ======================
what is stored?                 :mod:`._facts`            ``loan_loaders``
what happened, when?            :mod:`._events`           ``loan_ledger._events``
where do books open?            :mod:`._books`            (no loan analog)
what IS the amount?             :mod:`._amount_source`    (see below)
what does a pass derive live?   :mod:`._amount_basis`     (no loan analog)
what does a loan resolve to?    :mod:`._loan_pricing`     (no loan analog)
what does an installment cost?  :mod:`._loan_installment` ``loan_resolver``
what was it worth?              :mod:`._amounts`          ``loan_ledger._split``
did the bank show it?           :mod:`._clearing`         (no loan analog)
in what order?                  :mod:`._walk`             ``loan_ledger._walk``
what do they sum to?            :mod:`._flows`            (a peer reduction)
==============================  ========================  ======================

**The clearing row has no loan analog because a loan has no bank statement to
clear against**: its balance is an amortization the app derives, and its
assertions are the user correcting that derivation.  A cash account's balance is
what a third party says it is, so *which statement showed this line* is a fact
the app must be told (ruling **R-FL**) rather than one it can compute.

**The two AMOUNT rows are two questions, not one split in half** (plan
step X-au-b, ruling **R-FI**).  :mod:`._amount_source` answers *where does this
row's amount come from* -- the figure the amount COLUMN holds or would hold,
by a total dispatch over the five sources a row's amount can have.
:mod:`._amounts` answers *what is this row worth to checking*, which composes
that amount with an entered actual, an excluded status and an envelope's
purchases.  The arrow runs one way: the second consumes the first.  The loan
side has no analog for :mod:`._amount_source` because a loan payment's amount
has exactly one source; a cash row's has five, and four of them are derived.

**The LOAN PRICING pair is in this package, and plan step X-au-g-2a is what
put it there.**  Amount rule 4 -- a loan payment's shadow is worth what the
loan says that installment costs -- is a rule about a ROW'S AMOUNT, which is
this package's question; its producer lived in ``loan_payment_service`` until
that step, so :mod:`._amount_source` had to reach UP a tier to price a row and
the loan stack could never name this package back.
:mod:`app.services.row_valuation` exists because of that reach.  The cycle it
created was REAL and pylint could not see it -- a ``TYPE_CHECKING`` import
masked the runtime one -- which is measured, with the arms and their dates, in
:mod:`._loan_installment` and stated nowhere else.  The producer moved DOWN
rather than the readers routing around it, so the arrow runs one way again --
this package
names the loan TERM primitives (``loan_loaders``, ``loan_resolver``,
``escrow_calculator``, ``recurring_transfer_query``), none of which names it --
and the loan READING tier may import this package, which plan step X-au-g-2c-1
SPENT: ``loan_payment_service.get_payment_history`` prices its feed through
:func:`contributions_by_id` rather than through
``row_valuation.owned_contribution``.  ``balance_at._plan._planned_from_shadows``
-- the SECOND unrouted reader, which a census at that step found where the
finding had named one -- went through in the same commit, so no reader of an
unsettled loan-payment row is left outside the model.

**IT BROUGHT A ``budget.transfers`` QUERY WITH IT, AND PLAN STEP X-au-g-2c-2
TOOK IT BACK OUT.**  :mod:`._events` invokes Transfer Invariant 5 as a
principle of this package ("the same reason the projection engine never queries
``Transfer`` directly"), and ``_loan_pricing._load_live_payment_configs`` was
the ONE statement against ``budget.transfers`` in these thirteen modules --
the scenario's transfers INNER-joined through their template to
``loan_payment_settings``, to discover which of them were loan payments.  That
question is asked per ROW off the parent it was handed now (ruling **R-FK**'s
live refinement), because the read-time repair the map fired for is gone, so
the package makes no statement against that table at all.  Invariant 5 binds
the BALANCE CALCULATOR rather than every reader, so nothing was violated while
the query stood; what would have been wrong is its arriving silently, which is
exactly how ``loan_payment_service``'s own "queries ONLY budget.transactions"
sentence came to be false at the top of its file for months.

**The books row has no loan analog because a loan has no books to open.**  Its
origination is ``LoanParams.original_principal``, synthesized rather than
recorded (``loan_loaders.synthesize_origination_anchor``), so there is no
stored day for a movement to fall before.

Nothing here answers "what is the balance at T".  That is the
:mod:`app.services.balance_at` seam's question, and the arrow runs ONE way: the
producers import this package, and this package imports none of them.  The WALK
is facts (what happened, in the order it happened); the FOLD that samples those
facts at a date is the seam's, exactly as plan step D-fold split the loan pair.

**The walk (plan step X-a, ruling R-H).**  :mod:`._events` and :mod:`._walk`
build the account's ONE event stream -- every balance ASSERTION, every SETTLED
row, and every PURCHASE whose bank posting day the owner recorded (ruling
**R-FM**, plan step X-f3b) -- and :mod:`._clearing` answers which assertion
CLEARED each source (ruling **R-FL**), falling to the first assertion dated on
or after it where no statement is recorded as having shown it (ruling R-DH).
**What an assertion then DOES to a running total is not here** and has not been
since plan step X-f3c-1: that is a policy an account's KIND decides (ruling
**R-FO**), this package is kind-blind (ruling **R-J**), and the replay lives in
``balance_at._assertions`` beside the total it is a policy about.  The seam's
read fold TAKES these facts, and since plan step X-c2b2 every cash figure on
every screen is that fold sampled at a date.  The second consumer is still to come: at step
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

# The producer-free half of the valuation lives a tier DOWN
# (:mod:`app.services.row_valuation`), because the loan stack needs it and can
# never name this package.  Re-exported here so a consumer asking what a row's
# money DID names the same module it asks what the row's amount IS -- the same
# reason ``_amounts`` re-exports ``owned_contribution``.
from app.services.row_valuation import (
    recorded_amounts_by_id,
    settled_amounts_by_id,
)
# Re-exported so a caller ABOVE the amount model asks the MODEL for the
# model's own eager load and never has to know the relationship graph (plan
# step X-au-g-2c-2).  It is DEFINED a tier down, in ``app.utils``, because
# ``loan_loaders`` -- which this package imports for its loan term primitives --
# is the loader that most needs it and cannot import this package back.
from app.utils.amount_relationships import (
    pricing_load_options,
    valuation_load_options,
)
# The loan-pricing pair FIRST, because it is the bottom of this package's
# pricing line: ``_loan_installment`` -> ``_loan_pricing`` -> ``_amount_basis``
# -> ``_amount_source``, and the block below reads in tier order.
from ._loan_installment import _resolve_loan_basis
from ._loan_pricing import (
    LoanPricing,
    loan_pricing,
)
from ._amount_basis import (
    AmountBasis,
    amount_basis,
    baseline_amount_basis,
)
from ._amount_source import (
    AmountRule,
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
    owned_amount,
    owned_contribution,
)
from ._cash_leg import (
    cash_leg_of,
    credit_entry_sum,
    off_statement_sum,
    posted_purchase_sum,
    settled_cash_leg,
)
from ._clearing import (
    ClearableLine,
    StatementCoverage,
    statement_coverage,
)
from ._books import (
    books_hold,
    earliest_assertion_day,
    earliest_matched_line_day,
    earliest_recorded_movement_day,
    reject_books_open_after_an_assertion,
    reject_books_open_on_or_after_matched_lines,
    reject_books_open_on_or_after_movements,
    reject_line_before_books_open,
    reject_movement_before_books_open,
)
from ._events import (
    CashAnchorFact,
    CashOpeningFact,
    CashSourceFact,
    account_opening_fact,
    cash_anchor_facts,
    coverage_for,
    governing_account_opening,
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
    CashLedgerWalk,
    dated_deltas,
    walk_cash_ledger,
)

__all__ = [
    "AmountBasis",
    "AmountRule",
    "AnchorPoint",
    "LoanPricing",
    "governing_account_opening",
    "governing_anchor",
    "governing_anchor_on",
    "CashAnchorFact",
    "CashLedgerWalk",
    "CashOpeningFact",
    "CashSourceFact",
    "ClearableLine",
    "ReconciledThrough",
    "StatementCoverage",
    "amount_basis",
    "baseline_amount_basis",
    "books_hold",
    "amount_rule",
    "amounts_by_id",
    "account_opening_fact",
    "cash_anchor_facts",
    "coverage_for",
    "contributed_amount",
    "contribution_of",
    "contributions_by_id",
    "credit_entry_sum",
    "dated_deltas",
    "earliest_assertion_day",
    "earliest_matched_line_day",
    "earliest_recorded_movement_day",
    "loan_pricing",
    "cash_leg_of",
    "off_statement_sum",
    "owned_amount",
    "owned_contribution",
    "planned_cash_rows",
    "posted_purchase_sum",
    "reconciled_through",
    "resolve_anchor",
    "resolve_transaction_amount",
    "resolve_transfer_amount",
    "recorded_amounts_by_id",
    "reject_books_open_after_an_assertion",
    "reject_books_open_on_or_after_matched_lines",
    "reject_books_open_on_or_after_movements",
    "pricing_load_options",
    "valuation_load_options",
    "reject_line_before_books_open",
    "reject_movement_before_books_open",
    "settled_amounts_by_id",
    "settled_cash_facts",
    "settled_cash_leg",
    "statement_coverage",
    "sum_projected",
    "walk_cash_ledger",
    # Re-exported PRIVATE, deliberately, and it moved here whole with rule 4's
    # producer at plan step X-au-g-2a.  The cycle-deletion controls
    # (``test_loan_payment_service.TestALoansPriceDoesNotReadItsOwnPayments``
    # and ``tests/manual/verify_loan_pricing_ignores_payment_feed.py``) assert
    # that this producer reads the loan's TERMS and issues no statement against
    # ``budget.transactions``.  Publishing the name here is the honest public
    # path and keeps the leaf boundary intact; ``shekel-private-module-import``
    # is what would forbid the direct ``._loan_installment`` import from
    # ``app/`` or ``scripts/``.  *The sentence carried from the module this
    # moved out of said that checker forbids it from a TEST, and it does not:
    # ``tests/`` is linted for ``shekel-decimal-from-float`` alone
    # (``.pre-commit-config.yaml``) and CI's pylint step covers ``app/`` and
    # ``scripts/``.  In ``tests/`` it is a convention, not a gate -- worth
    # keeping, worth not overstating.*
    "_resolve_loan_basis",
]
