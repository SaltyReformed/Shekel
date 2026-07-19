"""Balance-seam fence: ``shekel-balance-producer-bypass`` (W9906).

Flags any module OUTSIDE the ``app.services.balance_at`` seam and the engine
cluster it composes from calling (or importing by name) a balance producer
directly. The seam owns all four per-kind balance-at-T boundary rules (cash /
loan / investment / property) in ONE tested place; a consumer re-inventing that
boundary is how the loan/investment balance-bug family kept recurring across
files for months (``docs/audits/balance_architecture/``).
"""

from astroid import nodes

from pylint.checkers import BaseChecker

from ._common import _called_name_in, _module_in_allowlist

# Balance producers (W9906): the functions that answer "what is account A's
# balance at time T?". Every screen must obtain a balance through the
# app.services.balance_at seam; a module outside the seam + engine cluster
# calling one of these directly re-invents the per-kind boundary rule the seam
# centralizes -- the recurrence generator behind the months-long
# loan/investment balance-bug family (docs/audits/balance_architecture/). The
# private (``_build_*``) producers are listed by their bare name: a consumer
# reaching one is already past the seam.
#
# ``project_balance`` / ``reverse_project_balance`` are NOT listed: they return a
# ProjectedBalance carrying contribution/growth detail, a different
# responsibility from a balance map, and the charts compose them legitimately.
#
# The LOAN RESOLVER entries (``resolve_account_loan`` / ``resolve_loan_seeded`` /
# ``resolve_loan_bundle``) WERE excluded on the same "rich detail, not a balance"
# reasoning -- and that was the hole. ``LoanState`` bundles the rich detail WITH
# ``current_balance``, a balance-at-today, and a name-keyed fence cannot see an
# attribute read. So the loan's displayed balance -- the /savings tile, the
# net-worth hero, the debt card, the Horizon's index-0 point, the equity card's
# mortgage leg, /debt-strategy -- was produced outside the seam with every gate
# silent, agreeing with it only because both paths happened to bottom out in the
# same ledger. They are fenced now (:data:`_LOAN_RESOLVER_PRODUCERS`), and
# consumers take ``balance_at.loan_figures`` (rich detail, deliberately WITHOUT a
# balance) plus ``balance_at.balance_at`` (the balance).
#
# ``investment_base_balance_map`` IS guarded (below).  It returns a
# DISPLAY-shaped cash-basis (pre-growth) map -- the one balance-map accessor a
# consumer could have rendered as if it were a real balance (the investment
# understatement bug the seam exists to kill), so the seam wraps it as
# ``balance_at.investment_seed_map`` and the chart-seed consumers (investment /
# retirement / year-end growth) read the seed through THAT seam entry.  The
# kernel producer itself is fenced to the cluster, so every balance map -- the
# modeled one a screen displays AND the pre-growth one a chart seeds from --
# now flows through the seam (the plan's "full fence, zero exceptions").
# ``test_flags_investment_base_balance_map_from_consumer`` locks the guard.
#
# One engine-cluster accessor that DOES return a per-period map is still
# excluded by the SRP line, and must NOT be added here:
#   * ``interest_by_period_for_account`` -- interest EARNED per period, not a
#     balance-at-T figure.  The seam owns the balance to DISPLAY at time T;
#     this owns a projection INPUT (and is semantically distinct, not a
#     balance map a consumer could mistake for one).
_BALANCE_PRODUCERS = frozenset({
    "balances_for",
    "balance_as_of_date",
    "build_daily_series",
    "calculate_balances",
    "calculate_balances_with_interest",
    "build_account_balance_map",
    "base_account_balance_map",
    "account_balance_map_from_inputs",
    "investment_base_balance_map",
    "build_investment_balance_map",
    "build_appreciation_balance_map",
    # The resolver bundle: an amortization schedule AND the loan's
    # ledger-confirmed ``current_balance``.  It was ruled a non-producer while
    # the claim "no consumer reads that attribute" held -- but the fence binds on
    # function NAMES and cannot see an attribute read, so the ruling rested on a
    # fact about the tree, not a property of the code, and one line
    # (``schedules[a.id].current_balance`` in a template context) would have put
    # a balance-at-T on a screen with every gate silent
    # (``followup_debt_schedule_attribute_fence.md``).  Fenced now: the
    # out-of-cluster consumers all wanted rows, and they take
    # ``debt_schedule_rows`` (which carries no balance), so the bundle's only
    # remaining callers are inside the cluster.  A consumer that wants a loan's
    # balance has no choice but ``balance_at.balance_at`` -- which is the point.
    "generate_debt_schedules",
})
# Modules allowed to call a balance producer directly: the balance_at seam plus
# the engine cluster it composes (the SOLID dependency direction -- consumers
# depend on the seam, the seam depends on these engines). Listed by their FULLY
# QUALIFIED module name, matched exactly or as a package prefix (see
# :func:`_module_in_allowlist`). The full path -- not the basename -- is
# deliberate: a same-named module in another package (a hypothetical
# ``app/routes/balance_at.py``) must NOT be exempted, or the fence could be
# silently bypassed by a name collision (a false negative is the dangerous mode
# for a fence). Every gate runs pylint from the repo root, so a cluster module
# always resolves to ``app.services.<name>`` (``pylint app/``, the per-edit hook
# on a single file, and pre-commit all agree); the prefix match additionally
# keeps a cluster module's submodules inside the fence if one is ever split into
# a package.
_BALANCE_SEAM_MODULES = frozenset({
    "app.services.balance_at",
    "app.services.balance_resolver",
    "app.services.balance_calculator",
    "app.services.account_projection",
    "app.services.net_worth_kernel",
    # Investment growth sub-chain, extracted from net_worth_kernel (which hit
    # its module-size ceiling): builds an investment account's modeled balance
    # map (build_investment_balance_map, the kernel dispatches here) and the
    # growth-since-anchor decomposition off the SAME forward projection. It
    # composes the kernel's investment_base_balance_map seed, so it lives
    # inside the cluster rather than re-inventing the boundary from outside it.
    "app.services.net_worth_investment",
    # Daily distribution producer: composes ``balance_as_of_date`` for its
    # per-day seed and ``sum_projected`` for the per-day nets, then exposed
    # through the seam as ``balance_at.cash_daily_balance_series``.  It is a
    # balance producer (daily granularity), so it lives inside the cluster
    # rather than re-inventing the boundary from outside it.
    "app.services.daily_balance_series",
})

# Genesis loan-ledger balance readers (W9906, the read switch's final commit):
# the confirmed balance-at-T readers over the posting ledger.  Same fence, one
# WIDER allowlist: beyond the seam + engine cluster, exactly two modules may
# call them -- ``loan_posting_service`` (the defining package; its sync/oracle
# internals legitimately compose its own readers) and ``loan_payment_service``
# (whose ``confirmed_loan_view`` is the ONE sanctioned injection seam the
# resolver-bound consumers read the ledger through).  Any other caller is
# re-inventing the read switch's seam.  NOT listed, by the same SRP line the
# header comment draws: ``confirmed_loan_history_rows`` (rich schedule-row
# detail the view seam composes, the ledger analog of ``resolve_loan``) and
# ``confirmed_loan_interest_in_year`` (a tax figure, not a balance-at-T).
_LOAN_LEDGER_READER_PRODUCERS = frozenset({
    "confirmed_loan_balance_at",
    "confirmed_loan_balance_map",
    # The running-balance walk the two readers above are built on: it IS the
    # balance-at-T computation over a loan's events.  It now lives in the
    # ``loan_ledger`` LEAF that both the posting ledger and the read seam derive
    # from (plan step B0), so its allowlist admits its own defining package; the
    # fence still keeps a consumer from reaching the raw walk instead of a reader.
    "walk_loan_ledger",
    # The fold's read side: "what did this loan owe on date D", answered from the
    # loan's SOURCE events rather than the postings (plan step B1).  A balance-at-T
    # in the most literal sense there is, and fenced from birth -- the two holes
    # this checker exists to close (``investment_base_balance_map``,
    # ``loan_owed_at_dates``) were both producers that went unlisted while they had
    # no consumer yet.  It has none today (B2's oracle is a test); C3 makes the
    # seam's AMORTIZING dispatch its first, and the seam is already allowlisted.
    "fold_loan_balances",
    # The date-sampling core of ``fold_loan_balances``, taking an already-computed
    # walk so a read pass can walk one loan ONCE and sample the memoized walk at
    # several date lists (step C3b: the scalar, the map, and the liability band all
    # read the fold through ``balance_at.positions``).  A balance-at-T producer like
    # its parent, and fenced the same -- only the seam (which memoizes the walk on
    # its context) and the leaf itself reach it.
    "fold_from_walk",
})
_LOAN_LEDGER_READER_MODULES = _BALANCE_SEAM_MODULES | frozenset({
    "app.services.loan_ledger",
    "app.services.loan_posting_service",
    "app.services.loan_payment_service",
    # The read pass's memo (``BalanceContext.loan_walk``) walks a loan's ledger
    # ONCE per pass and hands the memoized walk to ``fold_from_walk``, so the
    # scalar, map, and liability band that all fold the same loan (step C3b) do not
    # each re-walk it -- the same redundant-derivation the context's resolver memo
    # already kills.  It composes the walk exactly as it composes the resolver, so
    # it joins this allowlist beside the resolver one it already sits on.
    "app.services.resolution_context",
})

# ── The loan RESOLVER entries (W9906) ───────────────────────────────
#
# ``LoanState`` bundles rich projection detail (schedule, payment, rate, payoff)
# with ``current_balance`` -- a balance-at-TODAY.  The fence binds on function
# NAMES and cannot see an attribute read, so for as long as any consumer could
# call these and hold a ``LoanState``, the loan's DISPLAYED balance reached the
# screen without passing the seam: the /savings loan tile, the net-worth hero
# that reduces over it, the debt card, the Horizon's index-0 liability, the
# property-equity card's mortgage leg, and /debt-strategy were ALL produced
# outside the one tested place, and every gate stayed silent.  They agreed with
# the seam only because both paths happened to bottom out in the same genesis
# ledger -- agreement by luck, which is the exact failure signature of the
# balance-bug family this fence exists to end.
#
# Consumers now take ``balance_at.loan_figures`` (rich detail, deliberately
# WITHOUT a balance) plus ``balance_at.balance_at`` (the balance), so a consumer
# holding loan figures cannot render a wrong balance even by accident.
_LOAN_RESOLVER_PRODUCERS = frozenset({
    "resolve_account_loan",
    "resolve_loan_seeded",
    "resolve_loan_bundle",
})
# Who may still resolve a loan directly:
#   * ``loan_resolution`` -- defines them.
#   * ``resolution_context`` -- the read pass's memo; the ONE place a loan is
#     resolved for a read, and what the seam's loan entries compose.
#   * ``loan_payment_service`` -- the live grid/transfer amount producer, inside
#     the loan cluster (it composes the resolver for a payment's P&I, not for a
#     displayed account balance).
#   * ``loan_recurrence_sync`` / ``_transfer_loan_posting`` -- WRITE paths.  They
#     resolve mid-mutation, which is exactly why the read-pass context is not a
#     request-scoped cache; a writer is not a balance reader.
#   * ``app.routes.loan`` -- the loan DETAIL page, a genuine rich-primitive
#     consumer (the amortization table, the payoff and refinance calculators).
#     It reads the resolver through its own ``_resolve`` seam.
_LOAN_RESOLVER_MODULES = frozenset({
    "app.services.loan_resolution",
    "app.services.resolution_context",
    "app.services.loan_payment_service",
    "app.services.loan_recurrence_sync",
    "app.services._transfer_loan_posting",
    "app.routes.loan",
})

# ── The read pass's memoized loan handle (W9906) ─────────────────────
#
# ``BalanceContext.resolved_loan`` hands the caller a whole ``ResolvedLoan`` --
# and ``resolved.state.current_balance`` is a balance-at-today, one attribute read
# away.  Fencing the three resolver FUNCTIONS above and then exposing the same
# bundle as a public METHOD was the hole: the fence binds on names, and the method
# was called ``loan``, which is far too generic to guard (it would collide with
# every unrelated ``.loan`` attribute in the codebase).  Proven silent before this
# commit: a probe consumer in ``app/routes/companion.py`` reading
# ``ctx.loan_state(account).current_balance`` rated 10.00/10.
#
# The rename to ``resolved_loan`` is therefore load-bearing, not cosmetic -- a
# distinctive name is what makes the call site catchable.  Its sibling
# ``loan_state`` was DELETED rather than renamed: it had zero callers, and its
# entire purpose was to hand a consumer the ``LoanState`` this fence exists to
# keep out of consumer hands.
#
# The allowlist is deliberately NARROWER than the resolver's: the seam and the
# kernel cluster compose the memo, and nothing else may touch it.  Routes and
# dashboards get ``balance_at.loan_figures`` (rich detail, no balance) plus
# ``balance_at.balance_at`` (the balance).
_CONTEXT_LOAN_PRODUCERS = frozenset({
    "resolved_loan",
    # The read pass's memoized loan WALK (``BalanceContext.loan_walk``), the fold
    # analog of ``resolved_loan``: it hands back a ``LoanLedgerWalk``, which a
    # single ``fold_from_walk`` turns into a balance-at-T, so it is fenced like the
    # resolver memo beside it -- only the seam and the kernel cluster compose it,
    # and a consumer that wants a balance takes ``balance_at.balance_at``.
    "loan_walk",
})
_CONTEXT_LOAN_MODULES = frozenset({
    "app.services.balance_at",
    "app.services.net_worth_kernel",
    "app.services.resolution_context",
})

# Every fenced CALL surface: ``(guarded names, modules allowed to reach them)``.
# ``visit_call`` and ``visit_importfrom`` both walk this one table, so a new
# fenced surface is a data change here rather than a third copy of the same
# branch in two visitors (the duplication that let the earlier surfaces drift).
_FENCED_CALL_SURFACES = (
    (_BALANCE_PRODUCERS, _BALANCE_SEAM_MODULES),
    (_LOAN_LEDGER_READER_PRODUCERS, _LOAN_LEDGER_READER_MODULES),
    (_LOAN_RESOLVER_PRODUCERS, _LOAN_RESOLVER_MODULES),
    (_CONTEXT_LOAN_PRODUCERS, _CONTEXT_LOAN_MODULES),
)

# ── Fail-closed completeness (W9909) ────────────────────────────────
#
# The producer lists above are a DENY list keyed on function NAME, and for two
# years that made the fence fail OPEN: a new function added inside an
# allowlisted module was unguarded by default, invisible to W9906 until a human
# remembered to list it.  That is not a hypothetical -- it shipped twice:
#   * ``investment_base_balance_map`` (closed by Level-1 Commit 10), and
#   * ``loan_owed_at_dates`` (closed above; a consumer called it directly for a
#     month with the fence silent).
# Two identical misses is a design defect in the FENCE, not a lapse in
# diligence, so the default is inverted here: every PUBLIC top-level function
# defined in a fenced module must be explicitly classified as either a producer
# (fenced) or a non-producer (deliberately callable), and one that is neither is
# an error AT ITS DEFINITION -- the moment it is typed, in the same per-edit hook
# run.  A producer can no longer slip through by omission; forgetting is now the
# loud path, and exporting one to consumers is a deliberate act recorded in one
# place.
#
# Scope: every module that DEFINES a fenced producer -- the engine cluster (the
# balance producers) and the whole genesis loan-ledger package (the confirmed
# balance readers and the walk they are built on).  The ``balance_at`` seam
# package is deliberately NOT scoped: its public functions ARE the seam entries
# every consumer is supposed to call, so "unclassified" is meaningless there.
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
_ENGINE_CLUSTER_MODULES = _BALANCE_SEAM_MODULES - frozenset({
    "app.services.balance_at",
})
_LOAN_LEDGER_DEFINING_MODULES = frozenset({
    "app.services.loan_ledger",
    "app.services.loan_posting_service",
})
# The loan-RESOLVER defining modules.  They were fenced as a CALL surface
# (:data:`_LOAN_RESOLVER_PRODUCERS`) and then left out of the completeness check
# -- the same fail-open shape the check exists to close, reintroduced by the very
# commit that added the surface.  Proven: a new public
# ``loan_balance_right_now()`` added to ``loan_resolution`` rated 10.00/10, and
# ``contractual_schedule_from_origination`` sat there public and unclassified.
_LOAN_RESOLVER_DEFINING_MODULES = frozenset({
    "app.services.loan_resolution",
    "app.services.resolution_context",
})

# Per-module rulings: {module: (producer set, non-producer set)}.  Every PUBLIC
# top-level function defined in one of these modules must appear in one of its
# two sets.  Adding a name to a non-producer set is a DELIBERATE ruling that it
# does not answer "what is account A's balance at time T"; if in doubt, it is a
# producer (a false negative is the dangerous mode for a fence).
_FENCED_MODULE_RULINGS = {
    "app.services.net_worth_kernel": (_BALANCE_PRODUCERS, frozenset({
        # A transaction loader, not a balance.
        "load_account_period_transactions",
        # A REDUCER over balance maps the caller already obtained through the
        # seam: it sums given maps (asset-plus / liability-minus), it does not
        # compute a balance from an account.
        "sum_net_worth_at_period",
        # The loan's amortization ROWS -- no balance attached.  This is what the
        # out-of-cluster consumers (the honest-history gate's first-payment date,
        # the year-end + Schedule A interest hybrids) actually want, and handing
        # them rows rather than the ``DebtSchedule`` bundle is what CLOSED the
        # attribute hole: ``generate_debt_schedules`` is a producer now (below),
        # so no consumer can reach ``DebtSchedule.current_balance`` at all.
        "debt_schedule_rows",
        # Interest EARNED per period -- a projection INPUT and an explanatory
        # row, not a balance-at-T figure.
        "interest_by_period_for_account",
    })),
    "app.services.net_worth_investment": (_BALANCE_PRODUCERS, frozenset({
        # An index into the period list, not a balance.
        "get_anchor_period_index",
        # A (growth, contributed) DECOMPOSITION of a balance the seam already
        # owns, and itself wrapped by the seam's counterpart entry.
        "investment_growth_since_anchor",
    })),
    "app.services.balance_resolver": (_BALANCE_PRODUCERS, frozenset({
        # The stored anchor SoT row (a user-asserted FACT plus its date), not a
        # computed projection.  Consumers read it for the "as of" caption; their
        # balances come from the seam.
        "resolve_anchor",
        "load_balance_transactions",
        "live_amount_overrides",
        # Per-period NET sums (what moved), not a running balance (what is held).
        "period_subtotal",
        "period_subtotals",
    })),
    "app.services.balance_calculator": (_BALANCE_PRODUCERS, frozenset({
        "entry_checking_impact",
        "income_amount",
        "sum_projected",
    })),
    "app.services.account_projection": (_BALANCE_PRODUCERS, frozenset({
        # The canonical kind classifier and the payroll-funding predicate:
        # account metadata, not balances.
        "classify_account",
        "is_payroll_deduction_funded",
        # A period lookup by date, not a balance.
        "find_period_containing_date",
    })),
    "app.services.daily_balance_series": (_BALANCE_PRODUCERS, frozenset()),
    # The loan FOLD leaf (plan step B1): the event stream, the split, and the one
    # running-balance walk over them, which the posting ledger and the read seam
    # both derive from.  Scoped WHOLE for the same reason its sibling below is: a
    # new balance-at-T reader born in any of its submodules would reproduce
    # exactly the hole this check exists to kill.  Its walk IS a balance-at-T
    # computation and is fenced as one (:data:`_LOAN_LEDGER_READER_PRODUCERS`);
    # everything else it exports must say why it is not.
    "app.services.loan_ledger": (
        _LOAN_LEDGER_READER_PRODUCERS, frozenset({
            # The real principal/interest/escrow split of a payment -- a
            # decomposition of CASH, not an account balance.  The whole-loan list,
            # its per-payment step, and the pure arithmetic core the step shares
            # with the planned/estimated payment kinds carry the same ruling.
            "compute_loan_payment_splits",
            "split_one_payment",
            "split_payment_cash",
            # A generic prefix-sum sampler over (date, delta) steps: it reads a
            # running total at dates, but knows nothing of loans or accounts --
            # the shared date-sampling core the past and forward folds both use.
            "sample_cumulative",
            # Chronology, not balance: each answers WHEN a fact happened or
            # becomes countable, and the walk answers what it COST.
            #   * the event stream's ORDER (which fact the walk applies next).  It
            #     yields the loan's anchor FACTS, which carry an asserted
            #     ``anchor_balance`` -- but a user-asserted stored fact is not a
            #     balance-at-T, the same ruling ``resolve_anchor`` carries above.
            #   * the two visibility rules and the calendar they resolve against:
            #     these return a ``date`` / a ``PayPeriod`` / a list of them, and
            #     cannot yield a figure at all.
            "merge_anchor_and_payment_events",
            "resolve_anchor_pay_period",
            "owner_pay_periods",
            "anchor_visible_on",
            "payment_visible_on",
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
            # Rich row detail the view seam composes (the ledger analog of
            # ``resolve_loan``), and the yearly tax figures.  Rows and totals,
            # not a balance-at-T.
            "confirmed_loan_history_rows",
            "confirmed_loan_interest_in_year",
            "confirmed_loan_principal_in_year",
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
    # The loan-resolver seeding layer.  Its three db-facing wrappers ARE the
    # fenced resolver producers; everything else it exports must say why it is
    # not.
    "app.services.loan_resolution": (_LOAN_RESOLVER_PRODUCERS, frozenset({
        # PURE contractual rows from the loan's IMMUTABLE params (its origination
        # principal / term / rate feed), for the property chart's pre-tracking
        # back-projection.  It reads no ledger, takes no scenario, and answers no
        # "what is owed at T" -- it says what the origination TERMS imply, which
        # is why it is the one legitimate ``confirmed_view=None`` composer call in
        # the codebase.  The rows it returns carry ``remaining_balance``, but they
        # are reachable only through the seam's ``secured_loan_series``, which is
        # the sanctioned rows-to-a-consumer path (the same ruling
        # ``debt_schedule_rows`` carries).
        "contractual_schedule_from_origination",
    })),
    # The read pass's resolution context.  ``resolved_loan`` is the fenced memo
    # (:data:`_CONTEXT_LOAN_PRODUCERS`); its public siblings are plumbing.
    "app.services.resolution_context": (
        _CONTEXT_LOAN_PRODUCERS, frozenset({
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
            # The memoized forward PLAN -- a list of PlannedPayment RECORDS
            # carrying cash, NOT a balance-at-T (the same ruling
            # ``merge_anchor_and_payment_events`` carries).  Folding it into a
            # balance takes the seam-internal ``fold_forward``, which is protected
            # by the private ``balance_at._plan`` module boundary rather than a
            # name-fence.  That is a WEAKER guard than ``loan_walk``'s (a balance
            # one NAME-FENCED ``fold_from_walk`` away, both ends fenced);
            # name-fencing ``fold_forward`` to close the gap is a Phase-D candidate,
            # kept off the frozen fence here.
            "loan_plan",
        }),
    ),
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
    """Forbid obtaining an account balance outside the balance_at seam.

    Every screen must read an account's balance-at-T through
    ``app.services.balance_at`` -- the single seam that owns all four per-kind
    boundary rules (cash / loan / investment / property) in ONE tested place.
    A module outside the seam and the engine cluster it composes calling a
    balance producer directly re-invents that boundary; that re-invention is how
    the loan/investment balance-bug family kept recurring across different files
    for months (docs/audits/balance_architecture/). This checker is the
    deterministic fence Level 1 adds: the seam + engine cluster may call the
    producers (they compose each other), and everything else must depend on the
    seam.  The fence binds at two layers: the call site (:meth:`visit_call`)
    and the import (:meth:`visit_importfrom`), the latter closing the
    aliased-import evasion the 2026-07-02 adversarial review named (R3):
    ``from ... import balances_for as bf`` would otherwise strip the
    producer's name from every subsequent call.
    """

    name = "shekel-balance-seam"
    msgs = {
        "W9906": (
            "Balance producer '%s' called or imported outside the balance_at "
            "seam; obtain balances through app.services.balance_at instead",
            "shekel-balance-producer-bypass",
            "app.services.balance_at is the single seam through which every "
            "screen must obtain an account's balance over time (balance_map / "
            "build_maps / balance_at, plus the cash-flow views cash_balance_map "
            "/ cash_balance_at). Six producers historically answered 'what is "
            "account A's balance at time T?', and the three recompute-at-read "
            "kinds (loan, investment, property) each bolted on their own "
            "pre-first-data-point boundary rule; every new surface re-invented "
            "that boundary and shipped a balance bug at least once "
            "(docs/audits/balance_architecture/). The seam centralizes all four "
            "per-kind rules, so consumers (routes, savings, year-end, "
            "dashboards) must depend on it, never on a producer directly -- the "
            "SOLID dependency direction consumers -> seam -> engines. Only the "
            "seam and the engine cluster it composes (balance_resolver, "
            "balance_calculator, account_projection, net_worth_kernel) may "
            "call a producer. The genesis loan-ledger "
            "readers (confirmed_loan_balance_at / confirmed_loan_balance_map) "
            "are fenced the same way with a wider allowlist: their defining "
            "loan_posting_service package and loan_payment_service, whose "
            "confirmed_loan_view is the read switch's single injection seam. "
            "The rich projection-detail "
            "primitives project_balance and resolve_loan / resolve_account_loan "
            "are NOT producers and are not flagged -- they return "
            "ProjectedBalance / LoanState detail the seam composes, kept "
            "callable by the chart and loan-route consumers by design. The "
            "fence also binds at the import: a non-allowlisted module "
            "importing a producer by name (from ... import balances_for as "
            "bf) is flagged at the ImportFrom, closing the aliased-import "
            "evasion of call-site name matching.",
        ),
        "W9909": (
            "Public function '%s' in a fenced module is unclassified; add it to "
            "the producer set (it answers balance-at-T) or the non-producer set "
            "(it does not) in shekel_checkers/balance_seam.py",
            "shekel-unclassified-fenced-export",
            "The W9906 producer lists are keyed on function NAME, which made the "
            "fence fail OPEN: a new function defined inside an allowlisted "
            "engine-cluster module was unguarded by default until someone "
            "remembered to list it. That shipped twice -- "
            "investment_base_balance_map (Level-1 Commit 10) and "
            "loan_owed_at_dates (a consumer read it past the seam for a month "
            "with the fence silent). This check inverts the default: every "
            "public top-level function in a fenced module (the engine cluster "
            "that defines balance producers, and the genesis loan-ledger "
            "_reader) must be explicitly classified as a producer or a "
            "non-producer, so an unclassified one fails AT ITS DEFINITION rather "
            "than silently becoming a hole a consumer can reach through. "
            "Resolve it by deciding what the function IS: if it answers 'what is "
            "account A's balance at time T' it belongs in the producer set (and "
            "consumers must reach it through an app.services.balance_at seam "
            "entry); if it answers something else -- a loader, a classifier, a "
            "reducer over balances the caller already holds, interest earned -- "
            "add it to the non-producer set with a comment saying why. If in "
            "doubt, it is a producer: a false negative is the dangerous mode for "
            "a fence.",
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
        call.  Two producer sets share the one message: the general balance
        producers (seam + engine cluster only) and the genesis loan-ledger
        readers (additionally the defining ``loan_posting_service`` package
        and the ``loan_payment_service`` view seam -- the read switch's single
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
        (``from app.services.balance_calculator import calculate_balances as
        calc``) makes every subsequent call read ``calc(...)``, which matches
        no producer name.  Importing the producer's NAME is therefore fenced at
        the ``ImportFrom`` itself, aliased or not -- a module outside the
        allowlist may not call the producer, so it has no legitimate reason to
        import it.  Module imports (``from app.services import
        balance_calculator``, plain ``import ...``) are untouched: an attribute
        call through a module alias keeps the producer's own name at the call
        site, where :meth:`visit_call` already sees it.  ``node.names`` holds
        ``(name, alias)`` pairs; each imported name is checked against the same
        two producer-set/allowlist pairs the call check uses.
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
