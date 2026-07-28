"""
Shekel Budget App -- Savings Dashboard: shared bundle dataclasses.

The request-scoped and per-account value objects passed between the
savings-dashboard package's loader, projection, and orchestration
modules so each helper takes a small, cohesive argument list rather than
a long positional parameter list.

:class:`AccountProjection` is the one every consumer reads: the per-account
result the cockpit, the net-worth bands, the debt summary, the debt line and
the goals all reduce over.  It was an untyped dict with optional keys standing
in for a type discriminator until plan step X-t1 (finding N-111); its docstring
carries what that container cost.
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
from app.services.net_worth_account_data import is_liability_account


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
class LoanDetail:
    """A configured loan's non-balance detail: the seam's figures and its terms.

    The loan-only half of an :class:`AccountProjection`.  Existing AT ALL means
    "this account resolved as a configured loan for this projection", which is
    what makes loan-ness ONE structural question rather than a key membership
    test a consumer can spell four ways (plan step X-t1, finding N-111).

    It COMPOSES :class:`~app.services.balance_at.LoanFigures` rather than
    re-declaring its fields.  The projection dict used to copy them, and the copy
    silently went stale the moment the seam grew ``is_originated`` -- a field
    whose whole purpose is to stop a consumer misreading a loan's balance.  A
    bundle that must be hand-synchronised with the seam it mirrors is the seam's
    fence with a hole in it, so the duplication is not merely untidy; plan step
    X-r deleted it and this is where the whole value now lives.

    **``params`` rides beside ``figures`` for the same reason** (plan step X-s2,
    finding N-105): the projection published ``loan_params`` beside
    ``loan_figures``, and reading them from two places -- the loan result and a
    second lookup in ``_ProjectionContext.params.loan_params_map`` -- is what
    let one be present while the other was not.  Here they are ONE value written
    under ONE condition, so neither can arrive without the other.

    The BALANCE is deliberately not here: every account kind has one, and it is
    :attr:`AccountProjection.current_balance` for all five.  Carrying a second
    copy for the loan kind alone would be one fact under two keys, which is the
    shape ruling R-AZ deletes.

    Attributes:
        figures: The seam's :class:`~app.services.balance_at.LoanFigures` -- the
            rich, non-balance detail (payment, rate, payoff, and whether the loan
            is retired or not yet borrowed).
        params: The account's :class:`~app.models.loan_params.LoanParams` row --
            the loan's contract terms, which ``_compute_principal_paid_fraction``
            reads for ``original_principal``.
    """

    figures: LoanFigures
    params: LoanParams


@dataclass(frozen=True)
class _LoanAccountResult:
    """One loan account's seam balance and its :class:`LoanDetail`.

    What ``_compute_loan_account`` returns, so it hands back one cohesive value
    instead of a positional tuple.  The loan tile renders the current balance,
    monthly payment, rate, and payoff date; it shows no projected-balance
    horizons (those are the :mod:`app.services.balance_at` seam's job for the
    non-loan kinds), so none are carried here.

    Both halves land on the projection under ONE condition:
    :attr:`current_balance` becomes the account's balance (the same field every
    other kind fills from its balance map) and :attr:`detail` becomes its
    :attr:`AccountProjection.loan`.

    Attributes:
        current_balance: The seam's balance-at-today for the loan
            (:func:`app.services.balance_at.balance_at`).
        detail: The loan's :class:`LoanDetail` -- the seam's figures and the
            account's contract terms, carried whole.
    """

    current_balance: Decimal
    detail: LoanDetail


@dataclass(frozen=True)
class AccountProjection:
    """One account's projected figures: THE shape this package renders from.

    The per-account value every savings-cockpit surface reduces over -- the
    net-worth hero and its bands, the grid cells and their group subtotals, the
    debt summary, the debt line, the Horizon's asset and liability bands, the
    goal balances, and the Jinja cockpit itself.  Built ONLY by
    :func:`.._projections._project_one_account`.

    **It was an untyped dict whose KEY MEMBERSHIP was the type discriminator**
    (plan step X-t1, finding N-111): five required keys, four optional, and
    ``"loan_figures" in ad`` standing in for "this account is a loan".  Two
    measured defects came out of that container.  **B-16**: the dict FLATTENED
    the seam's :class:`~app.services.balance_at.LoanFigures` field by field and
    the field it never copied was ``is_retired``, so a retired loan was reported
    as debt the user still carried -- nothing could fail, because a consumer
    cannot miss a key that was never there, so it asks the nearest question the
    dict CAN answer.  **N-98**: two surfaces on one page then disagreed by 19
    years about when the user leaves debt.  A field the seam grows now arrives at
    every consumer by construction, and an attribute a consumer mistypes raises.

    (Not to be confused with
    :class:`app.services.account_projection.AccountProjectionKind`, the
    flag-driven KIND taxonomy this projection's builder dispatches on.  That
    module classifies an account; this one carries the result of projecting it.)

    **Optional means absent, never half-present.**  The three nullable fields
    each stand for a whole state: an account with no interest / investment
    parameter row, and an account that is not a configured loan.  ``loan`` is
    ONE field rather than the two the dict carried, so "is this a loan" has one
    answer and the figures and the terms row cannot arrive apart.

    Attributes:
        account: The :class:`~app.models.account.Account` this projects.
        current_balance: The account's balance today, from the
            :mod:`app.services.balance_at` seam -- the SAME figure the hero, the
            grid cell and the group subtotal render.  ``None`` is the deliberate
            "no balance here yet" state (a current period exists but the seam's
            map omits it, e.g. a cash account anchored after it), which the hero
            and goal reducers treat as zero; it is never a real zero balance.
        projected: The 3 / 6 / 12-month horizon balances by label, from
            :func:`app.utils.period_projections.project_balance_horizons`.
            Empty for a loan (a loan tile renders no horizons) and for an
            account with no current period.
        needs_setup: Whether the account flags ``has_parameters`` but its
            type-specific parameter row is missing -- a DIFFERENT question from
            "did it resolve as a loan", and answerable for an AMORTIZING account
            that has no :class:`~app.models.loan_params.LoanParams` at all.
            STORED rather than derived (unlike :attr:`is_liability` below)
            because it reads the parameter MAPS, which this value does not carry:
            the state it reports is an account whose params row is absent.
        interest_params: The account's
            :class:`~app.models.interest_params.InterestParams`, or ``None``.
        investment_params: The account's
            :class:`~app.models.investment_params.InvestmentParams`, or ``None``.
        loan: The account's :class:`LoanDetail` when the seam resolved it as a
            configured loan, else ``None``.  This IS the loan predicate: every
            consumer that asks "is this a loan" asks it here.
    """

    account: Account
    current_balance: Decimal | None
    projected: dict[str, Decimal]
    needs_setup: bool
    interest_params: InterestParams | None = None
    investment_params: InvestmentParams | None = None
    loan: LoanDetail | None = None

    @property
    def is_liability(self) -> bool:
        """Whether this account's category is LIABILITY (the danger-ink rule).

        DERIVED, not stored, so the flag and the classifier cannot contradict
        each other -- the same reason
        :attr:`~.._metrics.DtiMetrics.label` and
        :attr:`~.._debt_line.LoanPayoffOutlook.is_loan_free` are properties.
        It was a stored key, and the page ALREADY asked the question both ways:
        the grid cell read the stored flag while
        :func:`~.._net_worth.compute_net_worth_today` -- summing the very
        balances those cells show -- re-derived it from the account beside it.
        Two spellings of one rule is how they come to disagree; there is now one
        (plan step X-t1, finding N-111).

        The classifier is the canonical id-based one
        (:func:`app.services.net_worth_account_data.is_liability_account`,
        comparing the account type's ``category_id`` against the cached
        LIABILITY id -- IDs for logic, never a ``.name`` string), so the cell
        balance takes the danger token the group subtotal, chip and bar segment
        already do (polish audit P-AC4).

        Returns:
            ``True`` when the account's type is in the LIABILITY category.
        """
        return is_liability_account(self.account)


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
