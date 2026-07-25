"""Balance-seam fence residue: W9906 (call fence) + W9909 (completeness).

**Plan step D3 deleted the name-keyed fence down to its honest residue.**  The
structural rules do the old lists' jobs now: every balance producer is a
PRIVATE submodule of ``app.services.balance_at`` (plan steps D1/D-ctx/D-fold),
and the package-privacy gate W9910 (``shekel-private-module-import``) plus
stock ``protected-access`` make reaching one from outside the seam a hard
failure in every import spelling -- name-INDEPENDENT and fail-closed, so
nothing here can rot open the way the deleted allowlists could (finding N-17:
three stale exemptions permitting calls nobody made).  Named non-goal, shared
with W9910: dynamic reflection (``importlib`` / ``getattr`` chains) is outside
every static gate's sight -- a deliberate evader was always one rename past
the name fence too, so the deletion cedes nothing there.

What remains, each named with its reason and its resolving step:

* **W9906, ONE call surface** -- the two genesis posting readers
  (:data:`_LOAN_LEDGER_READER_PRODUCERS`).  They answer balance-at-T from the
  WRITE cluster's own table, so they cannot be private to the read seam; the
  fence funnels them to their defining package, which since plan step E1d-b is
  their ONLY caller (the resolver's confirmed slice seeds from the WALK now, and
  ``confirmed_loan_view`` -- the injection seam this surface existed for -- is
  deleted).  It deletes whole at plan step E1e, when the readers go
  package-private behind W9910.
* **W9909, the completeness registry on the PUBLIC ingredient packages**
  (:data:`_FENCED_MODULE_RULINGS`).  "Is this new public function a balance
  producer?" is a human judgment no AST rule can decide, and these packages
  hold every ingredient of one (facts, splits, sums, schedules) OUTSIDE
  W9910's protection -- measured four times (N-28/N-31): a public balance-at-T
  born in one rates 10.00/10 with every other gate green.  The registry forces
  that judgment loudly at definition time.  Its rot direction is SAFE: a stale
  classification entry names a dead function (inert, and the reverse-staleness
  meta-test flags it); it can never permit a bypass.  Resolves only if the
  read seam, write cluster, and shared leaves ever move under one private
  super-package boundary (recorded in the plan as a post-E1 option).
"""

from astroid import nodes

from pylint.checkers import BaseChecker

from ._common import _called_name_in, _module_in_allowlist

# The balance-producer deny list and its one-package allowlist are GONE (plan
# step D3).  Every producer is a private ``balance_at`` submodule, so W9910
# already flags every import spelling from outside the seam and W0212 the
# attribute-access residual -- a name list here could only duplicate (and rot
# behind) the structural gate.  The seam-private W9909 rulings the moved
# producers carried (finding N-31's "travel") died with it, for the same
# reason: a producer born inside a private module is unreachable until someone
# deliberately re-exports it on the seam's public ``__init__``, which is the
# reviewed, one-place act the fence always wanted.


# Genesis loan-ledger balance readers -- W9906's ONE remaining call surface
# (the D3 residue), named with its reason and resolving step: they answer
# balance-at-T from the WRITE cluster's own postings table, so they cannot be
# private to the read seam, and the fence funnels them to their defining
# package.  Plan step E1d-b seeded the resolver's confirmed slice from the WALK
# (source facts) instead of the posting view and DELETED ``confirmed_loan_view``,
# so they now have NO caller in ``app/`` at all -- only the reconciliation
# oracle's independent window onto the postings still reads them.  At plan step
# E1e they go package-private behind W9910 and this surface goes with them.
# ``confirmed_loan_history_rows`` was NOT listed here, by the standing SRP line
# (rich schedule-row detail, not a balance-at-T); plan step E1d-b DELETED it
# outright when the confirmed rows cut over to the walk.  The paid-in-year tax /
# chip figures are no longer here at all -- they fold from the loan ledger in the
# balance seam (steps C3c / C6c).
#
# The loan FOLD is no longer here either (plan step D-fold): ``fold_loan_balances``
# and ``fold_from_walk`` moved INTO the seam (``balance_at._fold``) as seam-private
# names -- the past-side twin of the forward fold ``balance_at._plan.fold_forward``,
# which has lived seam-private since step C6a and was ruled OFF the frozen fence at
# step C6b (finding L1).  A balance producer moving DEEPER into the seam sheds its
# name-fence rather
# than gaining one (the D0b lesson: a fence gained by moving inward means the arrow
# was backwards); the private module plus D-gate is what protects it.  And
# ``walk_loan_ledger`` came OFF this set to the leaf's NON-producer ruling: with the
# fold gone from the leaf, the walk yields only FACTS, and no public leaf name turns
# them into a balance, so the walk needs no fence (see the ``loan_ledger`` ruling
# below).
_LOAN_LEDGER_READER_PRODUCERS = frozenset({
    "confirmed_loan_balance_at",
    "confirmed_loan_balance_map",
})
# Who may call the two posting readers: ONLY ``loan_posting_service``, the
# defining package, whose sync / oracle internals compose its own readers.  The
# seam and the ``loan_ledger`` leaf came off at plan step D-fold (the seam reads a
# loan's balance through the FOLD, and the leaf never read the postings);
# ``loan_payment_service`` came off at step E1d-b, when the resolver's confirmed
# slice cut over to the walk and ``confirmed_loan_view`` -- the read switch's one
# injection seam, and this allowlist's whole reason -- was DELETED.  Every removal
# has been a pure TIGHTENING, and the surface now has ZERO callers outside its own
# package: it deletes whole at plan step E1e, when the readers go package-private
# behind W9910.
_LOAN_LEDGER_READER_MODULES = frozenset({
    "app.services.loan_posting_service",
})

# The loan RESOLVER call-fence and the context-memo handle fence are GONE (plan
# step D3), because plan step D2a removed their reason at the ROOT:
# ``LoanState.current_balance`` -- the balance-at-today that made a resolver
# bundle a leak -- is DELETED, so ``resolve_loan_bundle`` / ``resolve_loan_seeded``
# hand back schedule detail of the same sanctioned class ``debt_schedule_rows``
# already exposes.  Plan step E1d-a then moved that whole read INSIDE the seam
# as the private ``balance_at._resolution``, re-exported nowhere, so W9910 is the
# only gate it needs and its completeness scope deleted with it (its
# ``resolve_loan_seeded`` half folded away at E1d-b); the context memo went with
# it, leaving ``BalanceContext.loan_walk``, which hands back the leaf's
# public FACTS (``walk_loan_ledger`` is a ruled NON-producer).  A consumer that
# wants a loan's balance still has exactly one way to get it --
# ``balance_at.balance_at`` -- but by construction now, not by list.

# Every fenced CALL surface: ``(guarded names, modules allowed to reach them)``.
# ``visit_call`` and ``visit_importfrom`` both walk this one table.  ONE surface
# remains (the D3 residue); it deletes whole at plan step E1.
_FENCED_CALL_SURFACES = (
    (_LOAN_LEDGER_READER_PRODUCERS, _LOAN_LEDGER_READER_MODULES),
)

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
# forgetting is now the loud path.
#
# Scope (the D3 residue): the PUBLIC packages that hold balance INGREDIENTS
# outside W9910's protection -- the two genesis loan-ledger packages (the
# confirmed readers and the walk they are built on), the cash ledger leaf, the
# loan resolver tier and its db-facing seeding module, and the account-kind
# classifier.  The ``balance_at`` seam package is deliberately NOT scoped: its
# public functions ARE the seam entries every consumer is supposed to call, so
# "unclassified" is meaningless there, and its private submodules are W9910's.
# Classes are not scoped either -- the historical misses were functions, and a
# dataclass (``DebtSchedule`` / ``BalanceResult``) is data the seam passes
# around, not an answer to "what is the balance at T".
#
# The non-producer rulings are keyed BY MODULE, not pooled into one flat set.
# A pooled set would let a name ruled harmless in one module silently exempt a
# same-named function later added to another (``period_subtotal``,
# ``income_amount``, ``resolve_anchor`` are all generic enough to collide) --
# which is the same fail-open shape, one level down.  Each module owns its own
# ruling.
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
# ``period_subtotals`` + ``round_money`` -- not one fenced name among them --
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

# Per-module rulings: {module: (producer set, non-producer set)}.  Every PUBLIC
# top-level function defined in one of these modules must appear in one of its
# two sets.  Adding a name to a non-producer set is a DELIBERATE ruling that it
# does not answer "what is account A's balance at time T"; if in doubt, it is a
# producer (a false negative is the dangerous mode for a fence).
_FENCED_MODULE_RULINGS = {
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
        # A loader: it selects rows, and carries no balance of any kind.
        "load_balance_transactions",
        # ``_amounts`` -- what ONE row is worth to checking.  An amount per
        # TRANSACTION is not a balance per ACCOUNT: the live override map is
        # what a row is worth right now when its stored amount is a stale
        # cache, the income rule reads that map, and the three-bucket
        # reservation is a decomposition of one row's budget.  The cash analog
        # of ``loan_ledger``'s ``split_*`` rulings below, and carried for the
        # same reason.  The three-bucket reservation formula itself is NOT here:
        # D1c deleted its only external caller, so it went private and needs no
        # ruling -- structure retiring a fence entry, which is Phase D's point.
        "live_amount_overrides",
        "income_amount",
        # ``_flows`` -- what a SET of rows sums to: what MOVED during a period,
        # not what is HELD at a date.  A peer reduction over the same rows a
        # balance folds, not a step toward one.  ``sum_projected`` is the shared
        # engine the balance walk and the per-period subtotals both call, which
        # is what makes ``balances[p] - balances[p-1] == subtotals[p].net`` hold
        # by construction rather than by coincidence.
        "sum_projected",
        "period_subtotal",
        "period_subtotals",
    })),
    # The account-KIND classifier -- why it is scoped is recorded once, at
    # :data:`_KIND_CLASSIFIER_MODULES`.  Its ``find_period_containing_date``
    # went to ``loan_ledger._visible`` at D1b (chronology belongs with the rules
    # built on it), leaving two names.
    "app.services.account_projection": (frozenset(), frozenset({
        # The canonical kind classifier and the payroll-funding predicate:
        # account metadata, not balances.
        "classify_account",
        "is_payroll_deduction_funded",
    })),
    # The loan WALK leaf (plan step B1, renamed ``_fold`` -> ``_walk`` at D-fold):
    # the event stream, the split, and the one running-balance replay over them,
    # which the posting ledger and the read seam both derive from.  Scoped WHOLE
    # for the same reason its sibling below is: a new balance-at-T reader born in
    # any of its submodules would reproduce exactly the hole this check exists to
    # kill.  It DEFINES no balance producer since D-fold moved the fold to the seam
    # -- its producer slot is the shared ``_LOAN_LEDGER_READER_PRODUCERS`` (the two
    # posting readers, defined in the sibling package below), and every public name
    # this leaf defines is a non-producer that must say why.
    "app.services.loan_ledger": (
        _LOAN_LEDGER_READER_PRODUCERS, frozenset({
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
            # its per-payment step, and the pure arithmetic core the step shares
            # with the planned/estimated payment kinds carry the same ruling.
            "compute_loan_payment_splits",
            "split_one_payment",
            "split_payment_cash",
            # Chronology, not balance: each answers WHEN a fact happened or
            # becomes countable, and the walk answers what it COST.
            #   * the event stream's ORDER (which fact the walk applies next).  It
            #     yields the loan's anchor FACTS, which carry an asserted
            #     ``anchor_balance`` -- but a user-asserted stored fact is not a
            #     balance-at-T, the same ruling ``resolve_anchor`` carries above.
            #   * the two visibility rules, the calendar they resolve against, and
            #     the date-to-period locator that resolution is built on: these
            #     return a ``date`` / a ``PayPeriod`` / a list of them, and cannot
            #     yield a figure at all.
            "merge_anchor_and_payment_events",
            "find_period_containing_date",
            "resolve_anchor_pay_period",
            "owner_pay_periods",
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
    # this check exists to kill.
    "app.services.loan_posting_service": (
        _LOAN_LEDGER_READER_PRODUCERS, frozenset({
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
        # The baseline scenario's id -- an int, and the ONE place the
        # no-baseline degradation is expressed.
        "scenario_id",
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
        # A projected payment's LIVE cash (P&I + current escrow + standing
        # extra) -- what a payment is worth, not what an account owes.
        "live_loan_payment_amount",
        "live_loan_transfer_amounts",
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


def _is_public_export_surface(node: nodes.FunctionDef) -> bool:
    """Return whether ``node`` is a PUBLIC name a consumer outside the module can call.

    A name is reachable from outside when it is public AND every scope enclosing
    it is a public CLASS, all the way up to the Module.  That admits, and the
    second of these is the shape the original check missed:

    * a public TOP-LEVEL function, and
    * a public METHOD of a public class (including a method of a public class
      nested in a public class) -- reachable as ``SomeClass(...).method(...)``,
      which is exactly how ``BalanceContext.loan`` handed routes a ``ResolvedLoan``
      with the fence silent.

    Excluded: any private name (leading underscore) at any level -- a private
    class's methods are unreachable because a consumer cannot name the class -- and
    anything nested inside a FUNCTION, which is unreachable from outside. Dunders
    fall out of the same underscore test: ``__post_init__`` is a lifecycle hook,
    not an export. ``@property`` / ``@classmethod`` / ``@staticmethod`` / ``async``
    are all ordinary FunctionDefs here and are all covered.

    **The walk is up the ANCESTOR CHAIN, not a fixed one- or two-level test.**  A
    two-level test (``parent is a ClassDef whose parent is the Module``) silently
    drops a method of a nested class, and a bare ``parent is Module`` test drops a
    top-level function defined inside an ``if`` or ``try``.  Neither shape exists
    in a fenced module today -- and "it cannot happen today" is precisely the
    reasoning that produced both of the holes this checker exists to close.

    Args:
        node: The function-definition node under inspection.

    Returns:
        ``True`` when the name is part of the module's public export surface.
    """
    if node.name.startswith("_"):
        return False
    scope = node.parent
    while scope is not None and not isinstance(scope, nodes.Module):
        # A FunctionDef ancestor makes it a nested (unreachable) def; a private
        # class ancestor makes it unnameable.  Statement wrappers (If / Try /
        # With) are not scopes -- step through them.
        if isinstance(scope, nodes.FunctionDef):
            return False
        if isinstance(scope, nodes.ClassDef) and scope.name.startswith("_"):
            return False
        scope = scope.parent
    return scope is not None


def _fenced_module_ruling(
    node: nodes.NodeNG,
) -> "tuple[frozenset[str], frozenset[str]] | None":
    """Return the (producers, non-producers) ruling for ``node``'s module.

    Matches the enclosing module's fully-qualified name against
    :data:`_FENCED_MODULE_RULINGS` exactly, or as a package prefix -- so a fenced
    module split into a package (or a package's submodules, e.g.
    ``loan_posting_service._reader``) stays scoped.  Returns ``None`` when the
    module defines no fenced producer, in which case the completeness check does
    not apply to it.

    Args:
        node: The function-definition node whose enclosing module is resolved.

    Returns:
        The module's ``(producers, non_producers)`` pair, or ``None``.
    """
    name = node.root().name or ""
    for module, ruling in _FENCED_MODULE_RULINGS.items():
        if name == module or name.startswith(module + "."):
            return ruling
    return None


class ShekelBalanceSeamChecker(BaseChecker):
    """The balance-seam fence's honest residue (plan step D3).

    The structural rules own the old fence's job now: every balance producer is
    a private ``app.services.balance_at`` submodule, unreachable from outside
    the seam under W9910 (``shekel-private-module-import``) in every import
    spelling, so consumers obtain balances through the seam's public entries by
    construction.  What this checker still enforces, each with its resolving
    step (see the module docstring): the two genesis posting readers' call
    surface (W9906, deletes at plan step E1) and the fail-closed classification
    of new public exports in the balance-ingredient packages W9910 cannot
    protect (W9909).  The call fence binds at two layers: the call site
    (:meth:`visit_call`) and the import (:meth:`visit_importfrom`), the latter
    closing the aliased-import evasion (R3): ``from ... import
    confirmed_loan_balance_at as bal`` would otherwise strip the producer's
    name from every subsequent call.
    """

    name = "shekel-balance-seam"
    msgs = {
        "W9906": (
            "Balance producer '%s' called or imported outside its sanctioned "
            "modules; obtain balances through app.services.balance_at instead",
            "shekel-balance-producer-bypass",
            "app.services.balance_at is the single seam through which every "
            "screen must obtain an account's balance over time; the seam owns "
            "all four per-kind boundary rules (cash / loan / investment / "
            "property) in ONE tested place, and every producer lives inside it "
            "as a private submodule the package-privacy gate W9910 protects "
            "structurally (docs/audits/balance_architecture/). This message's "
            "ONE remaining surface (plan step D3) is the pair of genesis "
            "posting readers (confirmed_loan_balance_at / "
            "confirmed_loan_balance_map): they answer balance-at-T from the "
            "WRITE cluster's own table, so they cannot be private to the read "
            "seam, and only their defining loan_posting_service package "
            "may call them. Any other caller is re-inventing the read switch, "
            "which plan step E1d-b retired: the resolver's confirmed slice "
            "seeds from the walk now. This surface deletes at plan step E1e, "
            "when the readers go package-private. The fence also binds at the "
            "import: a non-allowlisted module importing a reader by name "
            "(from ... import confirmed_loan_balance_at as bal) is flagged at "
            "the ImportFrom, closing the aliased-import evasion of call-site "
            "name matching.",
        ),
        "W9909": (
            "Public function '%s' in a fenced module is unclassified; add it to "
            "the producer set (it answers balance-at-T) or the non-producer set "
            "(it does not) in shekel_checkers/balance_seam.py",
            "shekel-unclassified-fenced-export",
            "A name-keyed fence fails OPEN: a new function defined inside a "
            "covered module is unguarded by default until someone remembers to "
            "list it. That shipped twice (investment_base_balance_map, "
            "loan_owed_at_dates -- each read past the seam with the fence "
            "silent) and was re-measured four more times during Phase D "
            "(findings N-28 / N-31). This check inverts the default for the "
            "PUBLIC balance-ingredient packages the package-privacy gate W9910 "
            "cannot protect (the loan_ledger and loan_posting_service "
            "packages, the cash_ledger leaf, the pure loan_resolver tier, and "
            "the account_projection classifier): every public top-level "
            "function or public method "
            "there must be explicitly classified as a producer or a "
            "non-producer, so an unclassified one fails AT ITS DEFINITION "
            "rather than silently becoming a hole a consumer can reach "
            "through. Resolve it by deciding what the function IS: if it "
            "answers 'what is account A's balance at time T' it belongs "
            "INSIDE app.services.balance_at as a private submodule (a public "
            "balance producer outside the seam is not a thing); if it answers "
            "something else -- a loader, a classifier, a reducer over "
            "balances the caller already holds, interest earned, schedule "
            "rows -- add it to its module's non-producer set with a comment "
            "saying why. If in doubt, it is a producer: a false negative is "
            "the dangerous mode for a fence.",
        ),
    }

    def visit_functiondef(self, node: nodes.FunctionDef) -> None:
        """Flag a public function OR METHOD in a fenced module classified as neither.

        The fail-closed half of the fence.  ``node`` is every function
        definition; it is considered when it is PUBLIC (non-underscore) and part
        of a fenced module's reachable export surface -- see
        :func:`_is_public_export_surface` -- and it is reported unless it appears
        in THAT MODULE'S producer set or its deliberate non-producer set.

        **METHODS are classified, not skipped, and that is this checker's second
        design fix.**  The original check returned early for anything whose parent
        was not the Module, on the reasoning that "a fence is about the module's
        public export surface".  A public method of a public class IS that surface:
        ``BalanceContext.loan`` was a public method that handed any caller a whole
        ``ResolvedLoan`` -- ``state.current_balance``, a balance-at-today, one
        attribute read away -- and because methods were never classified, the
        checker could not see it.  That is the exact fail-open shape W9909 was
        written to close, one level down, and it shipped anyway.  A consumer can
        reach ``SomeClass().method()`` every bit as easily as ``some_function()``;
        the fence must see both.

        Nested functions (a def inside a def) are still skipped: they are
        unreachable from outside, so they are not an export surface.  The
        ``balance_at`` seam package carries no ruling -- its public functions ARE
        the entries consumers call.
        """
        if not _is_public_export_surface(node):
            return
        ruling = _fenced_module_ruling(node)
        if ruling is None:
            return
        producers, non_producers = ruling
        if node.name in producers or node.name in non_producers:
            return
        self.add_message(
            "shekel-unclassified-fenced-export", node=node, args=(node.name,),
        )

    # An ``async def`` is a distinct astroid node with its own visit hook, and a
    # fence that only saw ``def`` would be trivially evaded by writing the
    # producer as a coroutine.  There are no async defs in app/ today, so this is
    # a closed door rather than a live path -- which is exactly the point.
    visit_asyncfunctiondef = visit_functiondef

    def visit_call(self, node: nodes.Call) -> None:
        """Flag a balance-producer call made outside its sanctioned modules.

        ``node`` is every call expression; only a call to one of the guarded
        balance producers from a module NOT in its allowlist is reported.  The
        producer-name check (a frozenset lookup on the called name) runs
        first, so the module-identity walk runs only for an actual producer
        call.  ONE surface remains since plan step D3: the genesis loan-ledger
        readers (their defining ``loan_posting_service`` package and the
        ``loan_payment_service`` view seam -- the read switch's single
        injection point).
        """
        for producers, allowlist in _FENCED_CALL_SURFACES:
            name = _called_name_in(node, producers)
            if name is None:
                continue
            if not _module_in_allowlist(node, allowlist):
                self.add_message(
                    "shekel-balance-producer-bypass", node=node, args=(name,),
                )
            return

    def visit_importfrom(self, node: nodes.ImportFrom) -> None:
        """Flag importing a fenced producer NAME into a non-allowlisted module.

        Call-site name matching alone has one evasion class: an aliased import
        (``from app.services.loan_posting_service import
        confirmed_loan_balance_at as bal``) makes every subsequent call read
        ``bal(...)``, which matches no producer name.  Importing the producer's
        NAME is therefore fenced at the ``ImportFrom`` itself, aliased or not
        -- a module outside the allowlist may not call the producer, so it has
        no legitimate reason to import it.  Module imports (``from app.services
        import loan_posting_service``, plain ``import ...``) are untouched: an
        attribute call through a module alias keeps the producer's own name at
        the call site, where :meth:`visit_call` already sees it.
        ``node.names`` holds ``(name, alias)`` pairs; each imported name is
        checked against the same producer-set/allowlist pairs the call check
        uses.
        """
        for name, _alias in node.names:
            for producers, allowlist in _FENCED_CALL_SURFACES:
                if name not in producers:
                    continue
                if not _module_in_allowlist(node, allowlist):
                    self.add_message(
                        "shekel-balance-producer-bypass", node=node,
                        args=(name,),
                    )
                break
