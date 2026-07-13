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
# reaching one is already past the seam. NOT listed -- and so never flagged --
# are the rich projection-detail primitives the seam composes:
# ``project_balance`` / ``reverse_project_balance`` (return a ProjectedBalance
# with contribution/growth detail) and ``resolve_loan`` / ``resolve_account_loan``
# (return the full LoanState). Those are a different responsibility (rich detail,
# not a balance map) and stay callable by the chart and loan-route consumers by
# design.
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
    "compute_loan_period_balance_map",
    "compute_forward_loan_period_balance_map",
    "balance_from_schedule_at_date",
    "forward_balance_at_date",
    "amortizing_balance_at",
    "build_account_balance_map",
    "base_account_balance_map",
    "account_balance_map_from_inputs",
    "investment_base_balance_map",
    "build_investment_balance_map",
    "build_appreciation_balance_map",
    # The batch multi-date FORWARD loan projection the seam's
    # ``liability_owed_at_dates`` composes.  It answers "what does loan A owe at
    # date T" for many (A, T) at once, so it is a balance producer in every
    # sense -- but it was born INSIDE the cluster (the horizon liability band,
    # 2026-07-12) and so was never listed, and the horizon consumer called it
    # directly for a month with the fence silent.  That miss is what the W9909
    # completeness check below now makes impossible; see
    # ``docs/audits/balance_architecture/followup_fence_loan_owed_at_dates.md``.
    "loan_owed_at_dates",
})
# Modules allowed to call a balance producer directly: the balance_at seam plus
# the engine cluster it composes (the SOLID dependency direction -- consumers
# depend on the seam, the seam depends on these engines). Listed by their FULLY
# QUALIFIED module name, matched exactly or as a package prefix (see
# :func:`_in_balance_seam_cluster`). The full path -- not the basename -- is
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
    # balance-at-T computation over the posting rows.  Only the ledger package
    # itself calls it (verified 2026-07-13), which the wider allowlist already
    # permits, so fencing it costs nothing and closes the door on a consumer
    # reaching the raw walk instead of a reader.
    "walk_loan_ledger",
})
_LOAN_LEDGER_READER_MODULES = _BALANCE_SEAM_MODULES | frozenset({
    "app.services.loan_posting_service",
    "app.services.loan_payment_service",
})

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
    "app.services.loan_posting_service",
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
        # The resolver bundle (schedule + LoanState current_balance) -- the
        # batch sibling of ``resolve_account_loan``, which the header already
        # rules a rich projection-detail primitive rather than a balance map.
        # Its consumers read ``.schedule`` rows only; the loan tile's displayed
        # balance comes from ``resolve_loan_seeded`` (verified 2026-07-13).
        # NOTE: ``DebtSchedule.current_balance`` IS a balance-at-today, so this
        # ruling holds only while no consumer renders that ATTRIBUTE.  The
        # function fence cannot see an attribute read; that residual is recorded
        # in ``followup_fence_loan_owed_at_dates.md``.
        "generate_debt_schedules",
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
        # A pure COMBINATOR over two maps the caller can only obtain from fenced
        # producers -- safe by construction, since it cannot be reached without
        # already holding producer output.
        "splice_confirmed_and_projected_loan_balances",
        # A period lookup by date, not a balance.
        "find_period_containing_date",
    })),
    "app.services.daily_balance_series": (_BALANCE_PRODUCERS, frozenset()),
    # The genesis loan-ledger package.  Scoped WHOLE, not just ``_reader``: a new
    # balance-at-T reader born in ``_display`` or ``_walk`` would reproduce
    # exactly the hole this check exists to kill.
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
            # The real principal/interest/escrow split of an actual payment --
            # a decomposition of CASH, not an account balance.
            "compute_loan_payment_splits",
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
}


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


def _called_balance_producer(node: nodes.Call) -> str | None:
    """Return the guarded balance-producer name ``node`` calls, or ``None``.

    Thin wrapper over :func:`_called_name_in` for the general balance-producer
    set (:data:`_BALANCE_PRODUCERS`); the genesis loan-ledger readers have
    their own set + allowlist (:data:`_LOAN_LEDGER_READER_PRODUCERS`).
    """
    return _called_name_in(node, _BALANCE_PRODUCERS)


def _in_balance_seam_cluster(node: nodes.NodeNG) -> bool:
    """Return True if ``node``'s module is the seam or an engine-cluster module.

    Thin wrapper over :func:`_module_in_allowlist` for the balance-seam fence
    (:data:`_BALANCE_SEAM_MODULES`); see that helper for the exact/prefix match
    and fail-closed rationale the W9906 fence relies on.

    Args:
        node: The producer-call node whose enclosing module is checked.

    Returns:
        ``True`` if the module may call a balance producer directly.
    """
    return _module_in_allowlist(node, _BALANCE_SEAM_MODULES)


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
        """Flag a public function in a fenced module that is classified as neither.

        The fail-closed half of the fence.  ``node`` is every function
        definition; only a PUBLIC (non-underscore) TOP-LEVEL function whose
        enclosing module carries a ruling (:data:`_FENCED_MODULE_RULINGS` -- the
        engine cluster and the genesis loan-ledger package) is considered, and it
        is reported unless it appears in THAT MODULE'S producer set or its
        deliberate non-producer set.

        Nested functions and methods are skipped (``node.parent`` is not the
        module): a fence is about the module's public export surface, which is
        what a consumer can reach.  The seam package itself carries no ruling --
        its public functions ARE the entries consumers call.
        """
        if not isinstance(node.parent, nodes.Module):
            return
        if node.name.startswith("_"):
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
        producer = _called_balance_producer(node)
        if producer is not None:
            if not _in_balance_seam_cluster(node):
                self.add_message(
                    "shekel-balance-producer-bypass", node=node,
                    args=(producer,),
                )
            return
        reader = _called_name_in(node, _LOAN_LEDGER_READER_PRODUCERS)
        if reader is None:
            return
        if _module_in_allowlist(node, _LOAN_LEDGER_READER_MODULES):
            return
        self.add_message(
            "shekel-balance-producer-bypass", node=node, args=(reader,),
        )

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
            if name in _BALANCE_PRODUCERS:
                if not _in_balance_seam_cluster(node):
                    self.add_message(
                        "shekel-balance-producer-bypass", node=node,
                        args=(name,),
                    )
            elif name in _LOAN_LEDGER_READER_PRODUCERS:
                if not _module_in_allowlist(node, _LOAN_LEDGER_READER_MODULES):
                    self.add_message(
                        "shekel-balance-producer-bypass", node=node,
                        args=(name,),
                    )
