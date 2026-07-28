"""
Shekel Budget App -- Savings Dashboard: shared bundle dataclasses.

The request-scoped and per-account value objects passed between the
savings-dashboard package's loader, projection, and orchestration
modules so each helper takes a small, cohesive argument list rather than
a long positional parameter list.
"""

from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal

from app.models.account import Account
from app.models.escrow_line import EscrowLine
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.services.balance_at import LoanFigures
from app.services.balance_at import BalanceContext


@dataclass(frozen=True)
class _DashboardCoreData:
    """Read-pass data loaded once at the start of the dashboard build.

    Bundles the accounts, the balance-seam context, and the pay periods so the
    orchestrator passes one object to the projection step instead of a
    long positional parameter list.  Per-account balances come from the
    :mod:`app.services.balance_at` seam (which loads its own transactions),
    so no pre-loaded transaction set rides here.

    Attributes:
        accounts: The user's active accounts, ordered for display.
        balance_ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext` -- the
            baseline scenario, the pinned ``as_of``, and the memo that resolves
            each loan exactly ONCE for the whole build.  It replaces the bare
            ``scenario`` this bundle used to carry: every seam call in the pass
            now shares this object, which is what collapsed a ``/savings``
            render from eleven loan resolutions to one per loan.  Read
            ``balance_ctx.scenario`` where the scenario itself is wanted.
        all_periods: All of the user's pay periods.
        current_period: The period containing ``balance_ctx.as_of``, or
            ``None``.
    """

    accounts: list[Account]
    balance_ctx: BalanceContext
    all_periods: list[PayPeriod]
    current_period: PayPeriod | None


@dataclass(frozen=True)
class _AccountParams:
    """Batch-loaded, account-type-specific parameter maps for the loop.

    Built once per request by :func:`_load_account_params` -- the single
    place all four maps are constructed -- and read per account inside the
    projection loop.  Each map is keyed by ``account_id``.  Request-scoped
    state that is not an account-type parameter (the baseline ``scenario``)
    lives on :class:`_ProjectionContext`, not here.  The growth projection's
    deductions and engine-gross inputs are NOT carried here: each per-account
    tile delegates its projection to the :mod:`app.services.balance_at` seam,
    which assembles those itself, so holding them on this bundle was dead
    state (a per-load deductions query + paycheck-engine call no consumer
    read).
    """

    interest_params_map: dict[int, InterestParams]
    investment_params_map: dict[int, InvestmentParams]
    loan_params_map: dict[int, LoanParams]
    escrow_map: dict[int, list[EscrowLine]]


@dataclass(frozen=True)
class _ProjectionContext:
    """Loop-invariant inputs shared across the per-account projection loop.

    Every account in ``_compute_account_projections`` projects against
    the same periods, current period, loaded parameter maps, and balance
    context; bundling them keeps the per-account helpers to a small,
    cohesive argument list.  The ``balance_ctx`` (not a bare scenario) is held
    because the :mod:`app.services.balance_at` seam every tile reads through
    takes the context -- and because carrying the SAME context the rest of the
    build uses is what guarantees a loan the tile renders and the same loan in
    the net-worth trend came from one resolution, not two that happen to agree.
    """

    all_periods: list[PayPeriod]
    current_period: PayPeriod | None
    params: _AccountParams
    balance_ctx: BalanceContext


@dataclass(frozen=True)
class _LoanAccountResult:
    """One loan account's balance, its params, and the seam's rich figures.

    What ``_compute_loan_account`` returns, so it hands back one cohesive value
    instead of a positional tuple.  The loan tile renders the current balance,
    monthly payment, rate, and payoff date; it shows no projected-balance horizons
    (those are the :mod:`app.services.balance_at` seam's job for the non-loan
    kinds), so none are carried here.

    It COMPOSES :class:`~app.services.balance_at.LoanFigures` rather than
    re-declaring its fields.  It used to copy them, and the copy silently went
    stale the moment the seam grew ``is_originated`` -- a field whose whole purpose
    is to stop a consumer misreading a loan's balance.  A bundle that must be
    hand-synchronised with the seam it mirrors is the seam's fence with a hole in
    it, so the duplication is not merely untidy.

    **It carries the account's ``LoanParams`` for the same reason** (plan step
    X-s2, finding N-105): the projection dict publishes ``loan_params`` beside
    ``loan_figures``, and reading them from two places -- this result and a
    second lookup in ``_ProjectionContext.params.loan_params_map`` -- is what
    lets one be present while the other is not.  Existing here means "this
    account resolved as a configured loan", so the two keys are written from ONE
    value under ONE condition.

    Attributes:
        current_balance: The seam's balance-at-today for the loan
            (:func:`app.services.balance_at.balance_at`).
        figures: The seam's :class:`~app.services.balance_at.LoanFigures` -- the
            rich, non-balance detail (payment, rate, payoff, and whether the loan
            is retired or not yet borrowed).
        params: The account's :class:`~app.models.loan_params.LoanParams` row --
            the loan's contract terms, which ``_compute_principal_paid_fraction``
            reads for ``original_principal``.
    """

    current_balance: Decimal
    figures: LoanFigures
    params: LoanParams


@dataclass(frozen=True)
class _SeamBatches:
    """Every :mod:`app.services.balance_at` read the projection loop consumes.

    Built ONCE per projection by :func:`.._projections._seam_batches` and read
    per account inside the loop, so :func:`.._projections._project_one_account`
    is pure assembly over prebuilt inputs and reaches the seam nowhere itself.

    **That is what lets ONE predicate cover both of this projection's seam
    doors** (plan step X-s2, ruling R-BF, finding N-105).  The seam raises on a
    ``None`` scenario by contract and expects its callers to guard BEFORE
    calling (``balance_at._context.require_scenario``); the projection reaches
    it two ways -- the per-kind balance maps and the per-loan resolution -- and
    only the first was guarded, so four account kinds degraded to a blank
    balance while the fifth raised ``ValueError`` four lines later.  Both now
    sit behind one predicate in :func:`.._projections._seam_batches`.

    The PACKAGE still states that rule in three more places (finding N-107);
    see that function's docstring for where, and for why hoisting them is a
    different step's work.

    Attributes:
        balance_maps: ``{account_id: period_id -> Decimal}`` from
            :func:`app.services.balance_at.build_maps`, for the NON-loan
            accounts the seam answers for.  An account the seam omits (no
            anchor period) is simply absent.
        loan_results: ``{account_id: _LoanAccountResult}`` for the accounts that
            resolved as configured loans.  Membership IS "this account is a
            loan for this projection": an account with a ``LoanParams`` row the
            seam cannot resolve is in neither map and reads as a non-loan with
            no balance.
    """

    balance_maps: dict[int, OrderedDict]
    loan_results: dict[int, _LoanAccountResult]
