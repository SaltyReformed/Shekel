"""Balance-at-T seam -- shared input assembly and the fail-loud scenario guard.

The seam's private foundation: the batch-loaded per-account projection inputs
every view assembles from (:class:`_AssembledInputs` / :func:`_assemble_inputs`),
the ONE per-kind dispatch site (:func:`_account_balance_map`), and the
:func:`_require_scenario` guard every public entry (bar the liability view --
see :mod:`._liability`) runs first.

Kept in one leaf submodule so the view modules (:mod:`._kind_correct`,
:mod:`._cash_flow`, :mod:`._grid`, :mod:`._liability`) depend only on these
primitives and never on each other's internals.  The package's SOLID dependency
direction is ``<view module> -> _inputs``, and ``_inputs`` imports nothing back
from the package.
"""

from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal

from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.services import (
    income_service,
    net_worth_kernel,
)
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.projection_inputs import (
    load_active_deductions_for_accounts,
    load_investment_params_for_accounts,
)
from app.services.resolution_context import BalanceContext, require_scenario

ZERO = Decimal("0")


@dataclass(frozen=True)
class _AssembledInputs:
    """The batch-loaded per-account projection inputs for a set of accounts.

    Bundles the four shared-loader outputs that
    :func:`app.services.net_worth_kernel.build_account_balance_map`
    dispatches on -- the amortization schedules, the investment-params map,
    the per-account deductions, and the engine gross-biweekly -- so the
    single-account (:func:`~app.services.balance_at.balance_map`) and batch
    (:func:`~app.services.balance_at.build_maps`) entry points assemble them
    through ONE helper (:func:`_assemble_inputs`) and dispatch through ONE
    helper (:func:`_account_balance_map`).  Mirrors the year-end package's
    ``_ProjectionInputs`` and the savings package's ``_AccountParams``; kept
    seam-local (not shared with either consumer) so each surface still owns its
    own assembly contract.

    Attributes:
        debt_schedules: account_id ->
            :class:`~app.services.net_worth_kernel.DebtSchedule` for the
            amortizing-loan subset (its schedule plus the resolver's current
            balance).  Non-loan accounts are absent.
        investment_params_map: account_id ->
            :class:`~app.models.investment_params.InvestmentParams` for the
            accounts the canonical classifier marks INVESTMENT.  A
            params-less investment, and every non-investment account, is
            absent (callers use ``dict.get``).
        deductions_by_account: account_id -> list of active paycheck
            deductions, loaded ONLY for accounts in
            ``investment_params_map`` (see :func:`_assemble_inputs`).
        salary_gross_biweekly: The raise-aware engine gross per pay period
            (the employer-match cap basis), shared by every investment in
            the set.
    """

    debt_schedules: dict[int, net_worth_kernel.DebtSchedule]
    investment_params_map: dict[int, InvestmentParams]
    deductions_by_account: dict[int, list]
    salary_gross_biweekly: Decimal


def _assemble_inputs(
    accounts: list[Account], ctx: BalanceContext,
) -> _AssembledInputs:
    """Batch-load the per-account projection inputs ONCE for *accounts*.

    The single assembly point shared by
    :func:`~app.services.balance_at.balance_map` (called with a one-element
    list) and :func:`~app.services.balance_at.build_maps` (called with the whole
    set), so single- and batch-assembly run identical loader logic and preserve
    the N+1 avoidance: one
    :func:`~app.services.net_worth_kernel.generate_debt_schedules` over the
    amortizing-loan subset, one investment-params query, one deductions query,
    and one raise-aware gross fetch for the whole set.

    The four loaders are the shared building blocks the savings cockpit's
    ``_load_account_params`` and the year-end summary already use -- this
    seam reuses them rather than writing new inline param queries.

    Assembling per call is what keeps single-account and batch reads from
    drifting -- but it used to mean N seam calls in one request did N LOAN
    RESOLUTIONS.  The context now owns the resolutions, so re-assembling is
    cheap: the second and later assemblies in a pass re-slice the same memoized
    :class:`~app.services.loan_resolution.ResolvedLoan` instead of replaying the
    amortization.  Statelessness is preserved; only the waste is gone.

    Args:
        accounts: The accounts to assemble inputs for, each with its
            ``account_type`` relationship available for the classifier.  An
            empty list returns an empty bundle without issuing any query.
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`
            (it scopes the loan resolver's payment history and memoizes each
            loan's resolution for the pass).

    Returns:
        The :class:`_AssembledInputs` bundle.
    """
    if not accounts:
        return _AssembledInputs(
            debt_schedules={},
            investment_params_map={},
            deductions_by_account={},
            salary_gross_biweekly=ZERO,
        )

    # Every account in the set is owned by one user (the caller's), so the
    # user id for the deductions / gross loaders comes off any of them.
    user_id = accounts[0].user_id

    # Amortizing loans drive the schedule path; resolve their schedules
    # once.  ``generate_debt_schedules`` returns an empty map for an empty
    # subset, so a no-loan set issues no resolver work.
    loan_accounts = [
        account for account in accounts
        if classify_account(account) is AccountProjectionKind.AMORTIZING
    ]
    debt_schedules = net_worth_kernel.generate_debt_schedules(
        loan_accounts, ctx,
    )

    # The shared loader owns the canonical-classifier filter, so a
    # parameterised physical asset (Property -> APPRECIATING) is correctly
    # excluded here rather than re-derived by elimination.
    investment_params_map = load_investment_params_for_accounts(accounts)

    # Deduction-scoping rule (mirrors savings ``_load_account_params``):
    # load deductions ONLY for the investment accounts that HAVE an
    # InvestmentParams row.  ``build_account_balance_map`` feeds deductions
    # to the growth engine ONLY for an INVESTMENT account whose
    # ``investment_params`` is not None, so deductions for a params-less
    # account are never consumed -- scoping to the params map's keys is the
    # canonical rule that keeps this seam, savings, and year-end in
    # agreement on which accounts get a deduction feed.
    deductions_by_account = (
        load_active_deductions_for_accounts(
            user_id, list(investment_params_map.keys()),
        ) if investment_params_map else {}
    )

    # Same investment-only scoping as the deductions above: the gross is the
    # employer-match cap basis the growth engine consumes ONLY on the
    # investment branch of ``build_account_balance_map``, so a set with no
    # investment account never reads it.  Skipping the paycheck-engine fetch
    # there keeps a single-account ``balance_map`` for a cash / interest / loan
    # account free of the engine run (the value would be unused), so routing
    # those reads through the seam stays as cheap as the prior direct producer
    # call -- no O(N) paycheck regression in the year-end savings-progress loop.
    salary_gross_biweekly = (
        income_service.get_current_gross_biweekly(user_id)
        if investment_params_map else ZERO
    )

    return _AssembledInputs(
        debt_schedules=debt_schedules,
        investment_params_map=investment_params_map,
        deductions_by_account=deductions_by_account,
        salary_gross_biweekly=salary_gross_biweekly,
    )


def _account_balance_map(
    account: Account,
    ctx: BalanceContext,
    periods: list,
    inputs: _AssembledInputs,
    amount_overrides: dict[int, Decimal] | None,
) -> OrderedDict[int, Decimal] | None:
    """Dispatch ONE account's per-period balance map from *inputs*.

    The seam's single dispatch site, shared by
    :func:`~app.services.balance_at.balance_map` and
    :func:`~app.services.balance_at.build_maps`.  Delegates to the shared
    :func:`app.services.net_worth_kernel.account_balance_map_from_inputs`,
    which unpacks the bundle for *account* and calls the kernel's per-kind
    dispatcher -- the same unpack the year-end adapter's
    ``_dispatch_account_balance_map`` runs, hoisted into the engine cluster
    so the two cannot drift (R0801).  The seam never re-implements the
    classify ladder; it supplies this account's assembled inputs.

    Args:
        account: The account to project.
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`.
        periods: The pay periods to project over (the output domain).
        inputs: The :class:`_AssembledInputs` bundle for the account's set.
        amount_overrides: Optional ``{transaction_id: Decimal}`` live map,
            forwarded to the kernel's cash path; ``None`` for the net-worth
            batch path, which never applies live overrides.

    Returns:
        The OrderedDict period_id -> Decimal balance, or ``None`` when the
        account has no anchor period (the kernel's own no-anchor contract).
    """
    return net_worth_kernel.account_balance_map_from_inputs(
        account, ctx, periods, inputs, amount_overrides=amount_overrides,
    )


# The seam's fail-loud no-baseline guard.  It lives on the context (the object
# that OWNS the scenario) rather than here, and is re-exported so every seam
# entry keeps calling ``_require_scenario(ctx)`` under one name; see
# :func:`app.services.resolution_context.require_scenario` for the contract and
# the one entry (``liability_owed_at_dates``) that deliberately skips it.
_require_scenario = require_scenario
