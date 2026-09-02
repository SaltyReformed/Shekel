"""W9909's classification registry: which public names are NOT producers.

The DATA half of the balance-seam fence.  :mod:`.balance_seam` holds the
checker -- what W9909 asks and where it asks it -- and this holds the answers
it looks up.

**Why they are two modules** (plan step ``balance:X-f3c-2b-2a``).  W9909 is
FAIL-CLOSED: every public top-level function in a scoped package must be
explicitly classified, so this registry grows whenever any of those packages
gains a name, while the checker beside it changes only when the RULE changes.
Held together they put a monotonically growing table inside a file with
pylint's 1000-line ceiling over it -- and that ceiling BOUND, at exactly 1000,
the first time a step added five names.  A fail-closed gate whose registry
cannot grow refuses the next honest edit rather than the next unsafe one,
which is a fence that has stopped being structural.

Nothing else moved with it: the scope sets, the rulings and every rationale are
here verbatim, and :mod:`.balance_seam` imports the two names it reads.
"""

from __future__ import annotations

# ── Fail-closed completeness (W9909) ────────────────────────────────
#
# A name-keyed deny list fails OPEN: a new function added inside a covered
# module is unguarded by default until a human remembers to list it.  That is
# not a hypothetical -- it shipped twice (``investment_base_balance_map``,
# ``loan_owed_at_dates``: each called past the seam with the fence silent), and
# the same hole was re-measured four more times during Phase D (findings
# N-28 / N-31).  Two identical misses is a design defect in the FENCE, not a
# lapse in diligence, so the default is inverted here: every PUBLIC top-level
# function defined in a scoped module must be explicitly classified as either a
# producer (fenced) or a non-producer (deliberately callable), and one that is
# neither is an error AT ITS DEFINITION -- the moment it is typed, in the same
# per-edit hook run.  A producer can no longer slip through by omission;
# forgetting is now the loud path.  Since plan step E1e every scoped package's
# PRODUCER set is empty, so the only classification a new public name here can
# legitimately receive is NON-producer: if it answers balance-at-T it belongs
# inside ``balance_at`` as a private submodule instead.
#
# Scope (the D3 residue): the PUBLIC packages that hold balance INGREDIENTS
# outside W9910's protection -- the two genesis loan-ledger packages (the
# posting attribution reads and the walk they are built on), the cash ledger
# leaf, the loan resolver tier and its db-facing seeding module, and the
# account-kind classifier.  The ``balance_at`` seam package is deliberately NOT
# scoped: its public functions ARE the seam entries every consumer is supposed
# to call, so "unclassified" is meaningless there, and its private submodules
# are W9910's.  ``ledger_report_service`` is not scoped either, and that one is
# a measured GAP rather than a ruling (finding N-35: a public balance-at-T born
# there rates 10.00/10 with every gate silent) -- closing it is its own step,
# because every public name in that package must then be classified.
# Classes themselves are not scoped -- the historical misses were functions, and
# a dataclass (``AnchorPoint`` / ``CashLedgerWalk``) is data the seam folds over,
# not an answer to "what is the balance at T".  Their public METHODS ARE scoped
# (see :func:`_is_public_export_surface`), which is why ``visible_on`` and
# ``delta`` carry rulings below.
#
# The non-producer rulings are keyed BY MODULE, not pooled into one flat set.
# A pooled set would let a name ruled harmless in one module silently exempt a
# same-named function later added to another (``dated_deltas``,
# ``income_amount``, ``resolve_anchor`` are all generic enough to collide -- the
# first is in fact ruled TWICE below, once per ledger leaf) -- which is the same
# fail-open shape, one level down.  Each module owns its own ruling.
# The five SEAM-PRIVATE ENGINE rulings are GONE (plan step D3).  They existed
# only as N-31's "travel": a name-keyed fence could not see a producer born
# inside the seam, so each moved module kept a classification entry UNTIL the
# package boundary became structural.  D-gate shipped (W9910): a private
# ``balance_at`` submodule cannot be imported from outside the package in any
# spelling, so a producer born there is unreachable until someone re-exports it
# on the seam's public ``__init__`` -- the deliberate, reviewed act the
# classification existed to force.  The ``balance_at`` package itself stays
# deliberately un-scoped: its public functions ARE the seam entries every
# consumer calls.
#
# ``_context`` is the ONE seam-private module whose ruling SURVIVES D3, because
# the engine rationale above does not reach it (D3's own adversarial review
# measured the gap): ``BalanceContext`` is already re-exported on the seam's
# public ``__init__``, so a new public METHOD on it reaches every route holder
# with NO ``__init__`` edit -- a probe ``ctx.balance_now(account)`` folding the
# memoized walk rated 10.00/10 with the ruling deleted.  W9910 sees imports,
# never attribute access on an object a consumer legitimately holds, so W9909
# is the only gate on this module's surface; a producer belongs in a private
# seam MODULE, never on the context.
_SEAM_PRIVATE_CONTEXT_MODULES = frozenset({
    "app.services.balance_at._context",
})
_LOAN_LEDGER_DEFINING_MODULES = frozenset({
    "app.services.loan_ledger",
    "app.services.loan_posting_service",
})
# The loan-payment LOADER module (plan step D3, from its adversarial review).
# Its PRIVILEGE is gone -- plan step E1d-b took it off the reader allowlist above
# with ``confirmed_loan_view`` -- but the scope STAYS, because privilege was never
# the only reason: this is a public module holding every ingredient of a loan
# balance (the payment feed, the escrow-netted amounts, the contractual P&I, the
# whole loaded ``LoanContext``) OUTSIDE W9910's protection, exactly like
# ``loan_ledger`` and the ``loan_resolver`` tier beside it.  The measured shape
# that put it here: a documented public wrapper ``loan_balance_for_tile()``
# returning ``confirmed_loan_balance_at(...)`` rated 10.00/10 under the full
# fail-on set (the N-28 shape).  Dropping the scope now would be a LOOSENING
# bundled with E1d-b's tightening, which is the D1b lesson.
_LOAN_PAYMENT_SEAM_MODULES = frozenset({
    "app.services.loan_payment_service",
})
# The PURE loan-resolver package (plan step D3, closing finding B-12): the
# schedule composer, the rate-period machinery, and the anchor replay -- the
# "wholly unfenced producer tier below the fence" the findings ledger named.
# Until D2a its ``resolve_loan`` returned a bundle carrying a balance-at-T with
# NO fence entry anywhere; the field is deleted, and this scope makes the
# package fail CLOSED the same way ``cash_ledger`` does: a new public
# balance-at-T born in any of its submodules errors at its definition instead
# of shipping reachable with every gate green (the N-28 shape, measured four
# times elsewhere).  A package key, prefix-matched, so a new submodule is
# scoped the day it is written.
_LOAN_RESOLVER_ENGINE_MODULES = frozenset({
    "app.services.loan_resolver",
})
# The cash LEDGER leaf (plan step D1c), scoped for the W9909 COMPLETENESS
# check: "may this module CALL a producer?" and "must a new public function
# here be CLASSIFIED?" are different questions, and D1a's own adversarial
# review proved that conflating them opens the exact hole W9909 exists to
# close.  Measured: a new
# public ``running_balance_map`` folded from ``resolve_anchor`` +
# ``period_subtotals`` + ``round_money`` (that reduction has since been deleted
# at plan step X-c2b3; ``sum_projected`` is the surviving one, and the probe
# reassembles from it unchanged) -- not one fenced name among them --
# rated 10.00/10, AND so did a route consuming it.  A real balance-at-T on a
# screen outside the seam with every gate silent, which is the third instance of
# the miss this checker's header calls "a design defect in the FENCE, not a
# lapse in diligence".
#
# This leaf is the likeliest birthplace of the next one: it holds every
# ingredient of a cash balance-at-T -- the anchor FACT, the row loader, what one
# row is WORTH, and what a set of rows SUMS TO -- and plan step X2 ("a cash
# account is an event stream") builds the cash fold directly on top of it.
#
# **It is ONE package key, and that is the point (plan step D1c).**  Until D1c
# these names lived in two flat modules (``cash_events`` / ``period_flows``)
# plus five stranded inside ``balance_calculator``, so the scope had to be a
# hand-written LIST -- which fails open exactly one level up: creating a sibling
# module is how you escape a module-keyed gate (finding N-28, and Section 8's
# "a fail-CLOSED gate is scoped by module identity").  A package is matched by
# PREFIX (:func:`_fenced_module_ruling`), so a submodule added inside it is
# scoped the day it is written, with no constant to remember.  Same shape as
# ``app.services.loan_ledger`` below, for the same reason.
_CASH_LEDGER_MODULES = frozenset({
    "app.services.cash_ledger",
})
# The account-KIND classifier (plan step D1b).  Same asymmetry as the cash-event
# sources above, reached from the other direction: it came OFF the W9906
# allowlist (it calls no producer) and STAYED scoped here.  It is not a cash
# event source, so it carries its own constant rather than joining that set --
# each scope entry names the reason it exists.
#
# **This is the canonical rationale for that entry; the registry entry below and
# the module's own docstring point HERE rather than restating it.**  D1b as
# planned deleted BOTH memberships, and dropping this one was measured unsafe:
#
#   * the module DEFINED the loan forward-projection producers through all of
#     Phase C and shed the last at ``f445aa77``, ONE DAY before D1b -- so "it
#     holds no producer" is a fact about a very recent tree, not a property; and
#   * a module's reachability surface is its PARAMETERS, not its imports.
#     ``classify_account`` takes a live ORM ``Account``, so a public
#     ``balance_on(account, target)`` folding ``account.transactions`` needs no
#     import this module lacks.  Measured with the entry dropped: that function,
#     and a route rendering it, both rated 10.00/10.
#
# What W9909 actually covers is public FUNCTIONS and public METHODS of public
# classes (see :func:`_is_public_export_surface`).  It does not see a balance
# computed in a dunder and exposed as an attribute, nor a module-level alias of a
# private function -- both rate 10.00/10 here and in every other scoped module.
# That gap is structural and pre-dates D1b; it is named so this entry is not read
# as a stronger guarantee than it is.
_KIND_CLASSIFIER_MODULES = frozenset({
    "app.services.account_projection",
})

# The producer-free half of the cash valuation (plan step X-au-c2).  It was
# defined inside ``cash_ledger`` and moved DOWN a tier because the loan stack
# needs ``owned_contribution`` and could never import that package: its
# ``_amount_source`` reached UP into ``loan_payment_service`` for amount rule
# 4's producer, so any loan-stack module naming ``cash_ledger`` closed an import
# cycle.  ``cash_ledger`` re-exports ``owned_contribution``, the only one of
# the three that was ever public, so no consumer moved.
#
# **THAT REACH IS GONE as of plan step X-au-g-2a**, which moved rule 4's
# producer into ``cash_ledger`` -- so the loan stack CAN name that package now,
# and this module's split is no longer forced by an import cycle.  The scope
# entry stays for the reason stated below (a fenced module's contents extracted
# into an unfenced neighbour is the N-28 shape), which never depended on the
# cycle; whether the module itself should fold back into ``cash_ledger`` is a
# separate question this step deliberately did not take, and it is not answered
# by silence here.
#
# **It is scoped here the day it is created, and that is the whole N-28
# lesson applied to its own remedy.**  This module's rationale for keying the
# cash ledger on a PACKAGE says a fail-closed gate is escaped by adding a
# sibling -- and a new TOP-LEVEL module is that escape one level further out.
# Extracting a fenced module's contents into an unfenced neighbour would have
# silently un-ruled ``owned_contribution``, which is the exact shape (a fence
# that fails open when the code moves) findings N-28 / N-31 are about.  A flat
# module rather than a package, so the key is exact rather than prefix-matched;
# if it ever becomes a package the prefix match already covers the submodules.
_ROW_VALUATION_MODULES = frozenset({
    "app.services.row_valuation",
})
# The recurring DEFINITION reader (plan step R7d-a).  It held one loan figure
# already -- the standing overpayment threaded into every payoff projection --
# and that step moved the loan-payment SETTINGS reads here off
# ``loan_payment_service``, whose 1000-line ceiling was what the move paid for.
# Two of the moved names answer a MONEY question ("what does this loan's own
# definition say one installment costs"), so the scope follows them rather than
# letting a fenced module's contents leave the fence by changing address --
# which is the loosening-bundled-with-a-move shape the D1b lesson names.
_RECURRING_DEFINITION_MODULES = frozenset({
    "app.services.recurring_transfer_query",
})

# Per-module rulings: {module: (producer set, non-producer set)}.  Every PUBLIC
# top-level function defined in one of these modules must appear in one of its
# two sets.  Adding a name to a non-producer set is a DELIBERATE ruling that it
# does not answer "what is account A's balance at time T"; if in doubt, it is a
# producer (a false negative is the dangerous mode for a fence).
_FENCED_MODULE_RULINGS = {
    # The producer-free half of the cash valuation
    # (:data:`_ROW_VALUATION_MODULES`).  The EMPTY producer set is the same D3
    # invariant its parent package carries, and it is even easier to hold here:
    # this module imports no producer and cannot -- that is the property that
    # made it a separate module.
    "app.services.row_valuation": (frozenset(), frozenset({
        # The four arms of what one row is worth that need no producer, ruled
        # on exactly the ground the ``cash_ledger._amounts`` valuation family
        # below stands on: each answers what ONE ROW is worth, and none folds,
        # dates, sums, or reads an anchor.  ``fixed_contribution`` is the
        # status / soft-delete / entered-actual gate every other form shares,
        # ``own_figure`` is the refusal that keeps the amount model TOTAL (a
        # row owning its amount must store one), ``owned_amount`` is that
        # refusal applied to a transaction's own column, and
        # ``owned_contribution`` composes it with the gate for a reader that
        # can only ever see rows owning their figure.  The last two are the
        # BUDGET / WORTH pair plan step X-au-c2b completed: one answers what a
        # row's amount IS, the other what it is worth, and a reader takes
        # whichever question it is asking.
        "fixed_contribution",
        "own_figure",
        "owned_amount",
        "owned_contribution",
        # The settlement RECORD's three names (plan step X-au-c3), ruled on the
        # same ground: each answers about ONE ROW, from that row's own columns
        # and children, and none folds, dates, sums or reads an anchor.
        # ``settled_figure`` is what the row RECORDED as having moved -- a
        # stored fact, not a projection of one -- ``purchases_total`` is the
        # reduction an envelope's record defers to (it takes entries, not even a
        # row), and ``settled_amounts_by_id`` is ``settled_figure`` mapped over
        # a row set for a render, which is a LOOP over a non-producer and not a
        # fold toward a balance: it dates nothing and sums nothing.  The batch
        # is the one to look at twice, and it is ``display_amounts_by_id``'s
        # sibling one tier up -- that map needs an ``AmountBasis`` because a
        # plan may be DERIVED, and this one needs none because a record is the
        # row's own, which is exactly why it is down here.
        # ``recorded_figure`` and ``recorded_amounts_by_id`` are
        # ``settled_figure``'s TOTAL twin and its batch, ruled non-producers on
        # exactly the same ground and by the same reading: each answers about
        # ONE ROW from that row's own columns, and the batch is a LOOP over the
        # single-row form.  The one clause between the pairs decides nothing a
        # balance sees -- a settled row that RECORDS NOTHING answers ``None``
        # here and raises there -- because these two are read by the EDIT
        # DOORS, which prefill a box rather than count anything.  A balance
        # reader taking this pair instead would be the substitution the fence
        # exists to catch, and the refusing pair beside it is what it must take.
        "purchases_total",
        "recorded_amounts_by_id",
        "recorded_figure",
        "settled_amounts_by_id",
        "settled_figure",
    })),
    # The cash LEDGER leaf (plan steps D1a + D1c): the facts a cash balance is
    # folded from, what one row is WORTH, and what a set of rows SUMS TO.
    # Scoped WHOLE for completeness but never call-allowlisted -- see
    # :data:`_CASH_LEDGER_MODULES` for why, and why the scope is a package
    # rather than a list of modules.  The EMPTY producer set is the D3
    # invariant stated as data: a public balance producer outside the seam is
    # not a thing, so a new public name here is either ruled a NON-producer
    # (with its why) or it belongs inside ``balance_at`` -- there is no third
    # classification.
    "app.services.cash_ledger": (frozenset(), frozenset({
        # ``_facts`` -- the stored anchor SoT row (a user-asserted FACT plus its
        # date), not a computed projection.  Consumers read it for the "as of"
        # caption; their balances come from the seam.
        "resolve_anchor",
        # The same stored FACT, asked as of a civil day instead of as of now
        # (ruling R-EQ, plan step X-f1c4b).  Same row, same tie-breaks, same
        # query -- the horizon is the only difference, so it carries
        # ``resolve_anchor``'s ruling verbatim.  It is emphatically NOT a
        # balance-at-T: it answers which assertion the user had made by day D,
        # never what the account held then, which is the seam's question and
        # folds settled movements this function cannot see.  Its only callers
        # are the two anchor WRITE doors, deciding whether a submission changes
        # anything.
        "governing_anchor_on",
        # The NON-RAISING twin of ``resolve_anchor``, added at plan step
        # X-f3a-1 for the reconcile panel: same row, same tie-breaks, same
        # ``_governing_row`` query, and the only difference is that "this
        # account has never had a balance declared" is an honest empty state to
        # a panel where it is a broken invariant to a reader.  It carries
        # ``resolve_anchor``'s ruling verbatim -- a stored user-asserted FACT,
        # not a computed projection.
        "governing_anchor",
        # The PLAN loader (plan step X-b), a non-producer on the same ground
        # as its settled twin ``settled_cash_facts`` below: it SELECTS rows and
        # returns them unchanged.  Its WINDOWED sibling
        # ``load_balance_transactions`` carried this same ruling until plan step
        # X-g4b deleted it with the anchor-forward producer that was its last
        # caller.  It is the weaker of the two shapes, deliberately -- the
        # settled loader returns facts
        # already VALUED and DATED, while this one cannot date anything, because
        # a projected row's effective date is ``max(attribution, as_of + 1d)``
        # (ruling R-G) and this package reads no clock.  Rows in, rows out; the
        # dating, the valuation and the prefix-sum that make them a balance are
        # all seam-private in ``balance_at._cash_fold``.
        "planned_cash_rows",
        # ``_amounts`` -- what ONE row is worth to checking.  An amount per
        # TRANSACTION is not a balance per ACCOUNT: the live override lookup is
        # what a row is worth right now when its stored amount is a stale
        # cache, the income rule reads it, and the three-bucket
        # reservation is a decomposition of one row's budget.  The cash analog
        # of ``loan_ledger``'s ``split_*`` rulings below, and carried for the
        # same reason.  The three-bucket reservation formula itself is NOT here:
        # D1c deleted its only external caller, so it went private and needs no
        # ruling -- structure retiring a fence entry, which is Phase D's point.
        #
        # The VALUATION family (plan step X-au-c2) joins them on exactly that
        # ground, and it is the same question ``Transaction.effective_amount``
        # answered as a model property: ``contributed_amount`` composes a
        # resolved amount with the status, the soft delete and an entered
        # actual; ``contribution_of`` and ``contributions_by_id`` are the
        # one-row and batch forms that resolve first.  Each answers what ONE
        # ROW is worth -- none folds, dates, sums, or reads an anchor, and the
        # batch is a dict keyed by row id rather than anything per account.
        # ``owned_contribution`` and ``owned_amount`` are ruled with them,
        # under :data:`_ROW_VALUATION_MODULES` -- both are DEFINED one module
        # down and only re-exported here, and this fence keys on where a
        # function is DEFINED.
        "live_override",
        "live_amounts",
        # The COMPOSITION of the two questions a screen asks -- what a row's
        # amount resolves to, superseded by a live recompute (plan step
        # X-au-c2b).  A non-producer on the same ground both of its terms are:
        # a dict keyed by ROW ID, nothing per account, nothing folded or dated.
        # It exists because an adversarial review found that composition
        # written twice and differently across the grid and the fragments.
        "display_amounts_by_id",
        "contributed_amount",
        "contribution_of",
        "contributions_by_id",
        "income_amount",
        # ``_amount_source`` -- WHERE one row's amount comes from (plan step
        # X-au-b, ruling R-FI).  Four names, one ruling, because they are one
        # question at two tiers: ``amount_basis`` resolves the live producers
        # ONCE for a row set, ``amount_rule`` says which of the five sources
        # prices a row, and the two ``resolve_*_amount`` entries answer what one
        # row's AMOUNT COLUMN holds or would hold.  Non-producers on exactly the
        # ground their ``_amounts`` siblings above stand on, and one tier
        # further from a balance than those: an amount per ROW is not a balance
        # per ACCOUNT, and this tier does not even value the row against
        # checking -- it answers a figure the valuation rules then compose with
        # a status, an entered actual and an envelope's purchases.  Nothing here
        # folds, dates, sums, or reads an anchor.
        "amount_basis",
        # Its BASELINE-pinned form (plan step X-au-j): the same constructor
        # with the Phase-1 scenario pin resolved, stated once for the three
        # surfaces that make it.  Non-producer for the identical reason --
        # it resolves nothing, folds nothing, and reads no anchor.
        "baseline_amount_basis",
        "amount_rule",
        "resolve_transaction_amount",
        "resolve_transfer_amount",
        # The BATCH form of the same answer (plan step X-au-c2b), on the same
        # ground as ``contributions_by_id`` above: a dict keyed by ROW ID, one
        # entry per row the caller loaded, and nothing per account.  It differs
        # from that sibling in the question rather than the tier -- what a row's
        # amount IS (ruling E-21's budget base) rather than what it is worth --
        # so a reader that needs a budget stops reaching for a contribution.
        "amounts_by_id",
        # The ONE statement of "is this movement already inside the balance the
        # user declared" (ruling R-DH (a)), and since the one-partition step it
        # is a METHOD on ``ReconciledThrough`` rather than a free function, so
        # that a raw ``<=`` against the boundary is a TypeError instead of a
        # fifth answer.  The read fold, the posting walk and the entry
        # reservation all reach this one implementation -- they had three, in
        # three different units, and one of them cost production ``$4,001.42``.
        # It answers nothing about how much an account HOLDS; it answers
        # whether one event precedes one assertion.
        "covers",
        # THREE surfaces, one name, because it is one question asked of one
        # assertion, of a whole walk, or of an account: the property on
        # ``CashAnchorFact``, the property on ``CashLedgerWalk``, and the
        # module-level SQL form in ``_facts`` for callers holding no walk (one
        # indexed ``MAX(observed_on)``, no rows materialised, no anchor
        # resolution).  All three return a ``ReconciledThrough`` over a date;
        # none reads a balance.  It says WHEN the user last declared one,
        # never what it was.
        "reconciled_through",
        # The SETTLED per-row rule (plan step X-a), moved here from
        # ``posting_service._signed_cash_leg`` so the ledger WRITER and the cash
        # WALK value one row the same way by construction.  A non-producer for
        # exactly the reason its projected siblings above are: an amount per
        # TRANSACTION is not a balance per ACCOUNT.
        "settled_cash_leg",
        # One TERM of the rule above -- ``Sigma(credit entry amounts)`` for one
        # row -- published at plan step X-f2-c3 so the reconcile panel can print
        # what a STATEMENT shows beside what a tick books (finding **N-226**)
        # without writing ``entry.is_credit`` a second time.  A non-producer by
        # the same reasoning one step further along: it is not even an amount
        # per transaction, it is a component of one.
        "credit_entry_sum",
        # The OTHER term ruling **R-FM** adds to that rule (plan step X-f3b) --
        # ``Sigma(posted debit purchases)`` for one row -- published for exactly
        # the reason its sibling above is, and refused for the same one: it is a
        # component of an amount per transaction, not a balance per account.
        "posted_purchase_sum",
        # The SUM of the two terms above, published at plan step
        # ``bank_import:X-f6a-2`` because THREE readers now ask for it -- the
        # rule below, the reconcile panel's "what a statement shows" caption,
        # and the statement matcher -- and two additions written out is one
        # place for them to drift.  A non-producer for its components' own
        # reason: a component of an amount per transaction is not a balance per
        # account.
        "off_statement_sum",
        # ``settled_cash_leg`` with its first term supplied, so one rule serves
        # a SETTLED row (whose gross it owns) and a PROJECTED one (whose gross
        # is what settling would book) alike.  Its second caller is the
        # statement matcher, which must value a row the bank names whether the
        # app has settled it or not.  Non-producing for the same reason as the
        # function it generalises: it answers what ONE ROW moves, never what an
        # account HOLDS.
        "cash_leg_of",
        # ``_flows`` -- what a SET of rows sums to: what MOVED, not what is HELD
        # at a date.  A peer reduction over the same rows a balance folds, not a
        # step toward one.  ``sum_projected`` is the shared engine BOTH cash
        # bases reduce through -- the seam's fold and the retiring anchor-forward
        # walk -- which is what keeps one entries-aware expense rule and one
        # live-override basis across them.  Its per-period ``period_subtotal`` /
        # ``period_subtotals`` siblings carried this same ruling until plan step
        # X-c2b3 deleted them: ruling R-K changed what a subtotal COUNTS, so
        # ``_cash_periods.period_view_of`` is their successor and two rulings
        # went with the names (else the reverse-staleness meta-test flags them).
        "sum_projected",
        # ``_events`` (plan step X-a) -- the cash EVENT STREAM, the exact
        # counterpart of the ``loan_ledger`` non-producer rulings below and
        # non-producers for the same reason: each answers "what happened, and
        # when", never "what is held at time T".  ``settled_civil_day`` is the
        # ONE statement of which civil day a settled source's cash moved on;
        # ``cash_anchor_facts`` and ``settled_cash_facts`` are LOADERS of stored
        # assertions and per-row signed effects.  The stream ORDERING that
        # ``merge_anchor_and_cash_events`` used to return as a third list is
        # gone: both walks now advance their own sources against
        # ``ReconciledThrough.covers``, so the order is applied where the
        # replay happens rather than published as a fact of its own.
        # ``observed_on`` and ``delta`` LEFT this set at plan step X-f3c-1 with
        # the class that carried them: ``CashAnchorCorrection`` is
        # ``balance_at._assertions``' now, because its ``balance_before`` is a
        # prefix sum and this package's producer set is empty and stays empty.
        # A ruling for a name the package no longer defines classifies nothing
        # and would un-fence whatever took the name, which is what
        # ``test_classification_sets_match_the_real_fenced_modules`` is for --
        # it caught exactly this pair.
        #
        # ``settled_civil_day`` REPLACED ``attribution_instant`` at ruling R-DH
        # (2026-07-31) and ``visible_on`` left with it: both facts carry their
        # civil day as a FIELD resolved once at construction, so no public
        # method is left for this set to rule on.  A day is not a balance.
        "cash_anchor_facts",
        "settled_cash_facts",
        # ``account_opening_fact`` (X-f3c-2a, R-GX) -- a LOADER of the stored
        # ``account_openings`` row: returning a recorded balance is not
        # computing one.  The FOLD seeds from it.
        "account_opening_fact",
        # Its NON-RAISING twin (X-f3c-2b-2a), carrying its ruling verbatim for
        # the reason ``governing_anchor`` carries ``resolve_anchor``'s: same
        # row, same query, and only the empty-state policy differs.
        "governing_account_opening",
        # ``_books`` (X-f3c-2b, N-378; X-f3c-2b-2a; X-f3c-2b-2b, N-383) -- FIVE
        # REFUSALS stating the books boundary, each returning nothing, plus the
        # three DAYS they bound against and the one COMPARISON they share.
        # (It read "FOUR ... two DAYS" until the counts were taken against the
        # module: it states five ``reject_*`` and three ``earliest_*``, and its
        # own docstring says so.  A count in a ruling is a claim like any
        # other.)  A
        # refusal compares ONE date against a stored row, and "may this day be
        # recorded" is the opposite direction from "what is held at T"; the
        # readers are a bare ``MIN`` over a date column, public because the
        # restatement form renders both, and a day is not a balance (the
        # ``latest_statement_day`` hatch, ruled below).
        #
        # ``books_hold`` is the strongest case in the set rather than the
        # weakest: it takes two dates, returns a ``bool`` and reads nothing at
        # all, so it cannot answer a balance whatever a caller does with it.
        # It is public because the SCREEN asks it -- ``statement_match``
        # splits its bank lines on the same comparison the doors refuse on, and
        # a second spelling of it there is the drift this whole set exists to
        # make visible.
        "books_hold",
        "reject_books_open_after_an_assertion",
        # ``reject_books_open_on_or_after_matched_lines`` and its reader
        # (X-f3c-2b-2b) carry the same ruling as the movement pair beside
        # them, over the second row set: a ``MIN`` over ``posted_on`` on the
        # bank lines an account's matches name, and a refusal comparing one
        # date against it.  A bank line's posting day is the BANK's record of
        # when money moved, which is the same kind of fact as a settle day and
        # is no more a balance than one.
        "reject_books_open_on_or_after_matched_lines",
        "earliest_matched_line_day",
        "reject_books_open_on_or_after_movements",
        # ``reject_line_before_books_open`` (X-f3c-2b-2b) takes a LOADED
        # opening rather than an account id, which is the one shape difference
        # in this group and does not change the ruling: it is handed the fact
        # by the pass that already read it, compares one date against it and
        # returns nothing.
        "reject_line_before_books_open",
        "reject_movement_before_books_open",
        "earliest_assertion_day",
        "earliest_recorded_movement_day",
        # ``_walk`` (plan step X-a) -- the account's FACT stream and the
        # visible-day re-key of its source events.  Ruled NON-producers on
        # exactly the grounds ``loan_ledger``'s twins below are, and since plan
        # step X-f3c-1 the ruling rests on less: the walk holds no running
        # balance at all, returning settled sources ascending by settle day and
        # assertions ascending by BUSINESS date, while ``dated_deltas`` returns
        # what each source contributed and when.  Applying an assertion to a
        # running total, and the PREFIX-SUM that turns either into "what is held
        # at time T", are both seam-private.  If either moved into this package
        # these two would become producers -- which is the same thing as saying
        # they belong inside ``balance_at``, since this package's producer set
        # is empty and stays empty.
        "dated_deltas",
        "walk_cash_ledger",
        # ``_loan_pricing`` / ``_loan_installment`` (plan step X-au-g-2a) --
        # amount rule 4's producer, moved DOWN into this package from
        # ``loan_payment_service`` because the amount model is the lower tier
        # and should not reach up into a loan service to price a row (the
        # unwind :mod:`app.services.row_valuation` says ``X-au-g`` owes).
        #
        # **FOUR names were ruled here at X-au-g-2a and TWO remain**: plan step
        # X-au-g-2c-2 deleted ``live_cash`` and ``config_by_transfer`` with the
        # read-time repair they existed for, and their entries are removed here
        # in the same edit rather than left standing.  A ruling for a function
        # its module no longer defines is not inert -- it would silently
        # un-fence whatever name later takes it, which is findings N-28 / N-31's
        # shape, and it is what
        # ``test_classification_sets_match_the_real_fenced_modules`` caught.
        #
        # The derive-mode arm of a projected payment's LIVE cash (P&I + that
        # installment's escrow + standing extra) -- what a PAYMENT is worth, not
        # what an account owes.  Its wording is TIGHTENED from the
        # ``loan_payment_service`` ruling rather than copied, and saying
        # "verbatim" would be a claim licensing a reader not to diff: "current
        # escrow" became "that installment's escrow", because the derivation
        # resolves it on the shadow's own due date (ruling D5), never on a
        # current one.  ``live_cash`` was the non-derive arm beside it and is
        # gone; a shadow DECLARES ``parent_transfer`` now and the amount model
        # answers what the override used to.
        "derive_cash",
        # The named constructor for the derivation.  Ingredients; it resolves
        # nothing when it is called.  Word for word from the
        # ``loan_payment_service`` entry.  ``config_by_transfer`` -- the
        # scenario-wide loan-payment CONFIG map that sat beside it -- went with
        # ``live_cash``, and it was the cash-ledger package's ONLY
        # ``budget.transfers`` query.
        "loan_pricing",
        # ``_clearing`` (plan step X-f3a-1, ruling **R-FL**) -- WHICH STATEMENT
        # showed a line, which is the recorded fact that replaced ``covers``'
        # date comparison on the cash side.  Five names, one ruling, because
        # they are one question at three surfaces:
        # ``clearing_anchor_id`` names the assertion that cleared ONE line,
        # ``is_cleared`` is that answer reduced to a bool for the entry
        # reservation, ``statement_coverage`` builds the rule from an account's
        # assertion facts, ``coverage_for`` is its database twin for a caller
        # holding no walk, and ``coverage`` is the property on ``CashLedgerWalk``
        # for a caller holding one.
        #
        # Non-producers on exactly the ground ``covers`` and
        # ``reconciled_through`` stand on, and the classification did not change
        # when the fact did: each answers whether ONE event is inside ONE
        # assertion, or which assertion it is inside.  None of them reads a
        # figure -- an ``account_anchor_history`` id and a bool are not money --
        # and none folds, dates, sums or samples anything.
        #
        # ``latest_statement_day`` is the deliberate escape hatch, ruled here
        # rather than left to a call site: it returns the raw civil DAY the
        # entry list captions and the reconcile panel bounds its offer set with,
        # and it is the exact twin of ``ReconciledThrough.observed_day``, which
        # the same argument admits.  A day is not a balance.
        "clearing_anchor_id",
        "coverage",
        "coverage_for",
        "is_cleared",
        "latest_statement_day",
        "statement_coverage",
    })),
    # The account-KIND classifier -- why it is scoped is recorded once, at
    # :data:`_KIND_CLASSIFIER_MODULES`.  Its ``find_period_containing_date``
    # went to ``loan_ledger._visible`` at D1b (chronology belongs with the rules
    # built on it), leaving two names -- and on to ``pay_calendar`` at C2-d,
    # which DELETED it: two relocations for one primitive, because "the rules
    # built on it" were themselves a copy of a question the calendar owns.
    "app.services.account_projection": (frozenset(), frozenset({
        # The canonical kind classifier and the payroll-funding predicate:
        # account metadata, not balances.
        "classify_account",
        "is_payroll_deduction_funded",
    })),
    # The loan WALK leaf (plan step B1, renamed ``_fold`` -> ``_walk`` at D-fold):
    # the event stream, the split, and the one running-balance replay over them,
    # which the posting ledger and the read seam both derive from.  Scoped WHOLE
    # for the same reason its sibling below is: a new balance-at-T reader born in any of its
    # submodules would reproduce the hole this check kills.  Its producer set is EMPTY and stays
    # that way -- D-fold moved the fold into the seam -- so every public name this leaf defines
    # is a non-producer that must say why, and one that DOES answer balance-at-T belongs inside
    # ``balance_at``.
    "app.services.loan_ledger": (
        frozenset(), frozenset({
            # The running-balance WALK: it replays the loan's events into
            # per-payment splits and per-anchor corrections -- FACTS in
            # CONTRACT-time order, not a balance-at-T.  Turning those facts into a
            # balance owed on a DATE is the FOLD (re-key by visible date,
            # prefix-sum), which moved INTO the balance seam (``balance_at._fold``)
            # at plan step D-fold.  So a consumer holding a walk cannot reach a
            # balance from a public leaf name, and the walk needs no fence -- it was
            # fenced only while the fold was one call away in the same leaf.  Both
            # sides take it: the posting writer projects it into corrections, the
            # seam's read pass folds it.
            "walk_loan_ledger",
            # The real principal/interest/escrow split of a payment -- a
            # decomposition of CASH, not an account balance.  The whole-loan list,
            # its per-payment step, and the two halves R16-a made of the arithmetic
            # core (allocation alone; the month-charging composition over it) carry
            # one ruling: cash in, four parts out, no balance-at-T.
            "apply_payment_cash",
            "compute_loan_payment_splits",
            "split_one_payment",
            "split_payment_cash",
            # Chronology, not balance: each answers WHEN a fact happened or
            # becomes countable, and the walk answers what it COST.
            #   * the event stream's ORDER (which fact the walk applies next).  It
            #     yields the loan's anchor FACTS, which carry an asserted
            #     ``anchor_balance`` -- but a user-asserted stored fact is not a
            #     balance-at-T, the same ruling ``resolve_anchor`` carries above.
            #   * the two visibility rules: each returns a ``date`` and cannot
            #     yield a figure at all.  **Three names left this entry at plan
            #     step C2-d** -- ``owner_pay_periods``,
            #     ``find_period_containing_date`` and
            #     ``resolve_anchor_pay_period``, the owner's calendar and the
            #     date-to-period chain the anchor writers filed against, now
            #     ``pay_calendar.PayCalendar.filing_period``.  Their ruling had
            #     to carry a caveat ("a ``PayPeriod`` is an ORM row, so money is
            #     reachable by relationship; a period is not an account's
            #     balance") that the two survivors do not need.
            "merge_anchor_and_payment_events",
            "anchor_visible_on",
            "payment_visible_on",
            # The walk's events re-keyed by their visible dates (plan step
            # E1a): what ONE event contributed and when it counts -- dated
            # FACTS a consumer can already read off the public splits and
            # corrections.  Shared by the seam's fold AND the posting writer's
            # checked-projection assert precisely so neither carries its own
            # copy of the one clock; the prefix-sum that turns the list into a
            # balance-at-T stays seam-private (``balance_at._fold``).
            "dated_deltas",
            # A date-bounded loader of settled payment ROWS.  It selects records,
            # and carries no balance of any kind.
            "confirmed_shadows_through",
        }),
    ),
    # The genesis loan-ledger package.  Scoped WHOLE, not just ``_reader``: a new
    # balance-at-T reader born in ``_display`` would reproduce exactly the hole
    # this check exists to kill.  Its producer set is EMPTY as of plan step E1e,
    # which DELETED the two sum-of-postings balance readers that were the last
    # public producers outside the seam anywhere in ``app/`` (see the header
    # comment); this package is the general ledger -- balance sheet, statements,
    # attribution -- and never the answer to "what do I owe" (plan Section 3).
    "app.services.loan_posting_service": (
        frozenset(), frozenset({
            # Rich row detail: the payment-history table's per-payment cash /
            # principal / interest / escrow split.  Rows, not a balance-at-T.
            # (Its schedule-row sibling ``confirmed_loan_history_rows`` was
            # deleted at plan step E1d-b; the yearly tax / paid-YTD figures folded
            # off the postings onto the loan ledger at steps C3c / C6c, so this
            # package no longer exposes either.)
            "confirmed_loan_payment_history",
            # The anchor EVENT rows (the source documents behind a balance), not
            # the balance itself.
            "loan_balance_anchor_history",
            # WRITERS.  Everything below emits or reconciles postings; a writer
            # is not a balance reader, and the ledger-write path has its own
            # seams (``posting_service._emit_balanced_entry``).
            "reconcile_loan_anchor_corrections",
            "sync_loan_anchor_corrections",
            "reconcile_loan_payment_splits",
            "sync_loan_payment_postings",
            "reverse_loan_payment_postings_for_shadow",
            "sync_loan_postings",
            "sync_loan_postings_all_scenarios",
            "sync_all_scenarios_or_duplicate",
            "backfill_all_loan_postings",
            "resync_user_loan_postings",
        }),
    ),
    # The read pass's context (:data:`_SEAM_PRIVATE_CONTEXT_MODULES`) -- the
    # ONE seam-private ruling D3 keeps, because ``BalanceContext`` is publicly
    # re-exported and W9910 cannot see a method on an object a consumer holds.
    # A new public method here MUST be classified, and the answer is always
    # "non-producer or move it into a private seam module".
    "app.services.balance_at._context": (frozenset(), frozenset({
        # The context CONSTRUCTOR: it resolves the baseline scenario and pins
        # the as-of.  It builds the object a producer is called WITH; it
        # computes no balance.
        "build",
        # The baseline scenario's id -- an int, and the form the loaders and
        # the resolver take.  It RAISES for a pass with no baseline since plan
        # step X-v2 (ruling R-BX), which is what keeps a query from being
        # scoped to ``NULL`` and reading as an empty account; it still answers
        # nothing about a balance.
        "scenario_id",
        # The same id, nullable, for the two seam-internal rules that HAVE an
        # answer for a missing baseline (the loan resolution's payment feed and
        # the confirmed view).  Its docstring names both.  Same classification
        # and the same reason as ``scenario_id``: an id, not a balance.
        "scenario_id_or_none",
        # The fail-loud no-baseline guard.  It raises or returns None; it
        # answers nothing about an account.
        "require_scenario",
        # The read pass's ONE remaining memo handle, un-FENCED at D3 and ruled
        # a NON-producer: ``loan_walk`` hands the leaf's public FACTS
        # (``walk_loan_ledger``'s own ruling), and the fold that would turn
        # them into money is seam-private (plan step D-fold).  The RESOLUTION,
        # PLAN, and PAYOFF are pass-through data caches, not methods: their
        # derivations live in the seam modules above this one (plan steps
        # D-ctx-b / E1d-a), which is where a public balance producer would have
        # to be born to be reachable, and W9910 owns that.
        "loan_walk",
        # The read pass's PAY CALENDAR memo, and the reporting window read off
        # it (plan step C2-c).  Both NON-producers, on the same ground
        # ``loan_walk`` stands on: a calendar is the owner's paydays with the
        # two derived columns computed from them -- DATES, with no money
        # anywhere in the value -- and ``pay_calendar`` is a PUBLIC leaf below
        # this seam that any consumer may call directly for the identical
        # answer.  These hand back nothing a caller could not obtain from
        # ``pay_calendar.calendar_for`` itself; what they add is that the seam
        # and its caller cannot end up on two different calendars in one
        # render.
        "calendar",
        "reported_periods",
        # The read pass's AMOUNT-MODEL memo (plan step X-au-c2b).  A
        # NON-producer on the ground ``calendar`` stands on: it hands back an
        # ``AmountBasis``, which carries the two live DERIVATIONS a row's
        # amount is priced from and no balance-at-T of any kind -- and
        # ``cash_ledger.amount_basis`` is a public leaf BELOW this seam that any
        # consumer may call directly for the identical value.  What this adds is
        # that the seam and its caller cannot end up pricing one render's rows
        # two ways.
        "amounts",
        # Its NULLABLE sibling (plan step X-au-g-2c), classified on identical
        # ground and named separately because that is what this fence is for:
        # a method added beside a classified one inherits nothing.  It answers
        # the same ``AmountBasis`` or ``None`` -- ruling **R-BX**'s spelling for
        # the no-baseline pass, matching ``scenario_id_or_none`` beside it -- so
        # it carries no balance-at-T either, and ``None`` is the ABSENCE of a
        # derivation rather than a figure.
        "amounts_or_none",
    })),
    # The loan-payment LOADER module (:data:`_LOAN_PAYMENT_SEAM_MODULES`).  It
    # was "the one reader-allowlisted module outside the defining package" until
    # plan step E1d-b took it off that allowlist with ``confirmed_loan_view``; the
    # scope stays on the surviving ground (see the constant's comment): a PUBLIC
    # module holding every ingredient of a loan balance -- the payment feed, the
    # escrow-netted amounts, the contractual P&I, the whole loaded
    # ``LoanContext`` -- outside W9910's protection, so a public balance wrapper
    # born here would still be the N-28 shape.
    "app.services.loan_payment_service": (frozenset(), frozenset({
        # The unified loan-context loader: params, payments, rate changes,
        # escrow lines -- inputs a resolution is built FROM, no balance.
        "load_loan_context",
        # Payment ROWS (the transfer-shadow feed), chronological -- records,
        # not a balance.
        "get_payment_history",
        # The contractual P&I sizing rule -- a payment amount, not a balance.
        "compute_contractual_pi",
        # PaymentRecord adaptation for the amortization engine -- shaping, no
        # figure of any kind.
        "prepare_payments_for_engine",
        # ``live_cash`` / ``derive_cash`` / ``config_by_transfer`` /
        # ``loan_pricing`` were ruled HERE until plan step X-au-g-2a moved
        # amount rule 4's producer down into ``cash_ledger`` -- and X-au-g-2c-2
        # then DELETED two of the four, so only ``derive_cash`` and
        # ``loan_pricing`` still have entries anywhere.  Their rulings
        # moved with them to the ``app.services.cash_ledger`` entry above --
        # the names did not change STANDING, only address, and the one wording
        # change is named there rather than passed off as a copy.  Said out
        # loud because a name silently leaving a fenced module's ruled set is
        # the failure mode this registry exists to prevent (findings N-28 /
        # N-31), and a reader who remembers them here needs to be told where
        # they went rather than concluding they were dropped.
    })),
    # The recurring DEFINITION reader (:data:`_RECURRING_DEFINITION_MODULES`).
    # An EMPTY producer set, and it is a strong claim rather than a weak one:
    # nothing here loads a loan, a schedule or a payment history, so no name
    # defined in this module can answer what an account is WORTH.  What it
    # answers is what a repeating definition SAYS.
    "app.services.recurring_transfer_query": (frozenset(), frozenset({
        # The query itself: which template pays into this account.  A row, not
        # a figure.
        "active_recurring_transfer_template",
        # Two BOOLEAN-and-a-Decimal settings off a transfer template: does this
        # payment's cash derive from the loan, and what standing extra rides on
        # it.  Public since plan step X-au-b, whose amount resolver has to know
        # the MODE before it can price a payment; a configuration read, and the
        # furthest thing here from a balance-at-T.  It moved from
        # ``loan_payment_service`` at plan step R7d-a and keeps its ruling.
        "loan_payment_config",
        # The standing monthly overpayment, in its two scopings.  A single
        # stored parameter of the definition; the payoff projection THREADS it,
        # which is a different thing from this answering one.
        "loan_standing_extra",
        "loan_standing_extra_for_account",
        # What the definition says one installment costs -- the whole value and
        # the rule that reads it.  A PAYMENT amount, ruled on exactly the ground
        # ``compute_contractual_pi`` is above: what one payment moves, never
        # what an account owes.  The rule is PURE and takes the loan's own
        # contribution (the contractual P&I, the installment's escrow) as
        # arguments precisely so it needs no producer to answer.
        "standing_payment",
        "standing_installment_cash",
    })),
    # The PURE loan-resolver tier (:data:`_LOAN_RESOLVER_ENGINE_MODULES`,
    # closing finding B-12).  Package-scoped, so a new submodule is covered the
    # day it is written; every public name is a ruled non-producer, and a new
    # balance-at-T born here errors at its definition.
    "app.services.loan_resolver": (frozenset(), frozenset({
        # The state producer: payment, rate, committed schedule, life-of-loan
        # interest.  Its balance field was DELETED at plan step D2a; the
        # schedule rows it carries are the sanctioned display class.
        "resolve_loan",
        # The scenario composer: replayed history rows + projected forward
        # slices (schedule ROWS and aggregate interest, no balance-at-T).
        "compute_payoff_scenarios",
        # Rate-period machinery: the level P&I and the annual rate of the
        # period containing a date -- contract terms, not balances.
        "compute_monthly_payment_baseline",
        "current_rate_baseline",
        "resolve_periods",
        "engine_terms",
        # The latest anchor FACT (a user-asserted row plus its date) -- the
        # same ruling ``resolve_anchor`` carries on the cash side.
        "select_latest_anchor",
    })),
}
