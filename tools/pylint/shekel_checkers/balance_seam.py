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
    "balance_from_schedule_at_date",
    "build_account_balance_map",
    "base_account_balance_map",
    "account_balance_map_from_inputs",
    "investment_base_balance_map",
    "_build_investment_balance_map",
    "_build_appreciation_balance_map",
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
})
_LOAN_LEDGER_READER_MODULES = _BALANCE_SEAM_MODULES | frozenset({
    "app.services.loan_posting_service",
    "app.services.loan_payment_service",
})


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
    }

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
