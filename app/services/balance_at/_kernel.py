"""
Shekel Budget App -- Net-Worth Kernel (shared per-account balance chain).

The single, Flask-free home for the per-account balance-map projection
chain the ``balance_at`` seam dispatches through.  Promoted out of
``year_end_summary_service._balances`` (Loop B Phase 1) when the year-end
summary and the savings cockpit computed net worth from two copies of the
same math that could drift; the year-end consumer has since been deleted
(plan step F2), so what remains here is ONE dispatch (investment growth engine
/ appreciation growth curve / the cash fold, with modelled interest layered on
for an INTEREST account).  AMORTIZING loans are NOT dispatched here: the seam
reads its own ``positions()``-based map for them (plan step C3b3).

**Every cash branch here is the fold** (plan step X-c2b2): the PLAIN
fall-through IS
:func:`app.services.balance_at._cash_fold.cash_period_balances`, and the
INTEREST branch is that same map with
:mod:`app.services.balance_at._interest`'s accrual layered on.  So the
net-worth surfaces and the budget grid read one running total rather than two
producers that a test keeps in step -- which is what closes findings cash D1
(settled money counted by no producer), cash D2 (the scalar/daily fork) and
cash D3 / B-18 (the pre-anchor fabrication) on the net-worth side too.  The
INVESTMENT and APPRECIATING branches still seed off
``_cash_engine.balances_for``; windowing them onto the fold is plan step
X-c2c, deliberately separate because their pre-anchor tiers are ruled models
(a reverse growth projection, a flat anchor carry) that the fold must not
silently replace (finding N-43).

The cockpit's forward net-worth trend PROJECTS investment and retirement
growth forward, so the investment growth sub-chain lives here too (the
SCOPE B move locked 2026-06-24), not just the plain balance dispatch.

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"): this
module imports no Flask symbol and performs no database writes.  Since plan
step X-c2b3 it issues no QUERY either -- every row it dispatches over is loaded
by the leaf or the fold below it.  Its own
``load_account_period_transactions`` deleted there: it was the interest
accrual's transaction feed, and once ruling R-L's accrual layered over the cash
FOLD rather than walking rows itself, nothing called it (finding N-53).  All
money is :class:`~decimal.Decimal`; ``float`` belongs only at a route's
Chart.js serialization boundary, never here.

The public producers take loose, per-account parameters (the single
account's :class:`~app.models.investment_params.InvestmentParams`, its
adapted deductions, and the engine gross-biweekly) rather than a caller's
bundle, so a consumer need not construct a value object to call the kernel.
:func:`account_balance_map_from_inputs` is the one entry that DOES take a
bundle, duck-typed, and it slices it into those loose parameters here --
beside the dispatcher it feeds.
"""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)

from ._context import BalanceContext
from ._fold import fold_from_walk
from ._resolution import ResolvedLoan, resolved_loan
from . import _cash_fold, _interest, _investment


@dataclass(frozen=True)
class DebtSchedule:
    """Everything the FORWARD projection needs to value one loan at any date.

    The outputs of ONE resolution
    (:func:`~app.services.balance_at._resolution.resolved_loan`), bundled so
    the schedule, its seed, and the loan's origination cannot come from
    different places and drift.

    Attributes:
        schedule: The loan's :class:`AmortizationRow` list (today-forward:
            confirmed-history rows plus committed forward rows).  May be empty
            for a fully-resolved / paid-off loan.
        projection_seed: The balance the forward projection STARTS from -- the
            balance in effect before the first unconfirmed row.  See
            :func:`_projection_seed`.  It is NOT "what is owed now": for an
            upcoming mortgage the loan owes ``0.00`` today and the projection
            must still start from its opening balance once it closes.  The two
            coincide for every live loan, which is why one field served both jobs
            and why the old name was a lie.  Read a balance-at-T from the
            ``balance_at`` seam.
        owed_from: The loan's ``origination_date``.  A loan owes nothing before
            it exists, and the forward plan fold enforces that
            (:func:`app.services.balance_at._plan.fold_forward` returns ``0.00``
            for a date before ``owed_from``).
    """

    schedule: list
    projection_seed: Decimal
    owed_from: date


def generate_debt_schedules(
    debt_accounts: list,
    ctx: "BalanceContext",
) -> dict[int, "DebtSchedule"]:
    """Return each debt account's :class:`DebtSchedule` from the pass's resolutions.

    Projects the read pass's memoized loan resolutions
    (:func:`~app.services.balance_at._resolution.resolved_loan`) into the narrow
    ``(schedule, projection_seed, owed_from)`` bundle the balance dispatcher
    needs.
    Same resolver output the loan dashboard and the /savings debt card consume,
    so mortgage interest, debt progress, and net-worth liability all derive from
    ONE resolution per loan (E-18 / Commit 15).

    It no longer resolves anything itself, and that is the point.  It used to
    call the resolver per account against its own ``date.today()`` -- so the five
    surfaces that called it in a single ``/savings`` render each re-resolved
    every loan, against five independently-read clocks.  Now the context owns
    both the clock and the resolution, so calling this twice in one pass costs
    one dict comprehension, not two amortization walks
    (``docs/audits/balance_architecture/followup_redundant_loan_resolution.md``).

    Args:
        debt_accounts: The amortizing loan accounts to bundle.  An account the
            context cannot resolve (no ``LoanParams`` -- not a configured loan)
            is absent from the result, and the caller's per-kind dispatch then
            falls through to its non-loan path.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (it pins the scenario and the as-of, and memoizes each resolution).

    Returns:
        dict mapping account_id to :class:`DebtSchedule`.
    """
    schedules: dict[int, DebtSchedule] = {}
    for account in debt_accounts:
        resolved = resolved_loan(account, ctx)
        if resolved is None:
            continue
        origination = resolved.params.origination_date
        schedules[account.id] = DebtSchedule(
            schedule=resolved.state.schedule,
            projection_seed=_projection_seed(resolved, account, ctx),
            owed_from=origination,
        )
    return schedules


def _projection_seed(
    resolved: ResolvedLoan, account: Account, ctx: "BalanceContext",
) -> Decimal:
    """Return the balance the loan's forward projection starts from.

    See :attr:`DebtSchedule.projection_seed` for the contract.  The fork is the
    loan's own existence:

    * **Originated by the pass's ``as_of``** -- the FOLD of the loan's recorded
      events at ``as_of`` (:func:`~app.services.balance_at._fold.fold_from_walk`
      over the read pass's memoized walk): the confirmed present, which is what
      the projection amortizes down.  This is the SAME derivation
      :func:`app.services.balance_at.positions` reads the past through, so the
      balance a page shows at ``as_of`` and the seed its forward figures start
      from cannot fork -- including for a loan whose posting ledger cannot
      answer, which the pre-D2a seed (``LoanState.current_balance``) answered
      from the money-blind anchor replay while every displayed balance folded.
    * **NOT originated yet** -- the fold correctly reports ``0.00`` owed (the
      loan does not exist), but the projection still has to know what it will owe
      the day it closes.  That is the loan's OPENING ANCHOR balance -- the same
      fact the genesis walk posts as the ``loan_opening``
      (:func:`app.services.loan_loaders._opening_anchor_fact`).  ONE fact, two
      readers, split on the boundary the architecture turns on: the ledger owns the
      origination once it has HAPPENED, the projection until it does.

    Sourced from the opening anchor and NEVER from the raw
    ``params.original_principal`` column, deliberately.  The two are equal for a
    loan that has not originated (nothing can supersede an origination that has
    not happened), but keeping ONE definition of "the balance this loan opens at"
    keeps a not-yet-originated loan's OPENING from being confused with an EXISTING
    loan's balance: reporting an existing loan's balance AS its origination amount
    is a different, wrong statement (the F-21 / PR #44 defect), and this is the one
    controlled path the seed reaches the forward fold through, so that confusion
    has no call site to recur at.

    Args:
        resolved: The pass's :class:`~app.services.balance_at._resolution.ResolvedLoan`.
        account: The loan account, for the pass's memoized walk.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its ``as_of`` is the resolver's NOW; its walk memo serves the fold).

    Returns:
        The projection's seed as a ``Decimal``.
    """
    if resolved.params.origination_date <= ctx.as_of:
        return fold_from_walk(ctx.loan_walk(account), [ctx.as_of])[ctx.as_of]
    opening = next(
        fact for fact in resolved.anchor_facts if fact.is_opening
    )
    return opening.anchor_balance


def debt_schedule_rows(
    debt_accounts: list,
    ctx: "BalanceContext",
) -> dict[int, list]:
    """Return each debt account's amortization ROWS -- no balance attached.

    The accessor every out-of-cluster consumer of the loan schedules reads,
    instead of :func:`generate_debt_schedules`.  They want the
    :class:`AmortizationRow` list -- today, the net-worth trend's
    honest-history gate needs a first-payment date -- and none of them wants a
    balance.  (The year-end and Schedule A interest hybrids read it too until
    plan steps F2 / C3c folded them onto the balance seam.)

    Handing them rows rather than the :class:`DebtSchedule` bundle is what keeps
    a balance out of an out-of-cluster consumer's hands.  The name-keyed fence of
    the day bound on function NAMES: it flagged a consumer that CALLED a balance
    producer.  It could not see an ATTRIBUTE read, and
    ``DebtSchedule.projection_seed`` is a loan balance.  So
    while any consumer could call :func:`generate_debt_schedules`, one line --
    ``schedules[account.id].projection_seed`` in a template context -- would put a
    balance on a screen without passing the seam, with every gate silent
    (``docs/audits/balance_architecture/followup_debt_schedule_attribute_fence.md``).
    The bundle exists precisely so the forward projection CAN seed from a balance;
    that made it a loaded gun whose safety was a docstring.  The rows carry no
    seed, so a consumer that wants a balance has no choice but
    ``balance_at.balance_at`` -- which is the point.
    :func:`generate_debt_schedules` lives in this PRIVATE seam module (W9910
    structurally stops any outside import since plan step D3 retired its name
    fence), and its callers are all inside the seam.

    Args:
        debt_accounts: The amortizing loan accounts whose rows to return.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (each loan is resolved at most once for the pass).

    Returns:
        ``{account_id: [AmortizationRow, ...]}`` -- the loan's schedule
        (confirmed history plus committed forward rows).  A loan the context
        cannot resolve (no ``LoanParams``) is absent, matching
        :func:`generate_debt_schedules`.
    """
    return {
        account_id: schedule.schedule
        for account_id, schedule in generate_debt_schedules(
            debt_accounts, ctx,
        ).items()
    }


def _account_interest_projection(
    account: Account,
    ctx: "BalanceContext",
    periods: list,
    interest_params: InterestParams,
) -> "tuple[OrderedDict[int, Decimal], dict[int, Decimal]]":
    """Fold the account's cash, then layer its modelled accrual on top.

    The single home for the "sample this account's cash fold at every period
    end and accrue over it" sequence shared by the interest BALANCE path
    (:func:`base_account_balance_map`, which keeps the balances and
    discards the interest) and the interest-EARNED accessor
    (:func:`interest_by_period_for_account`, which keeps the interest and
    discards the balances).  Folding the two into one helper keeps the base
    and the accrual window identical between the balance figure a screen
    renders and the interest figure the account-detail chip reports -- they
    cannot drift onto two copies of the same walk (R0801).  That sharing is
    also why ruling R-L moved BOTH readers at once (plan finding N-47).

    **The SEED is the fold (ruling R-L's second half, plan step X-c2b2).**  It
    was the ``current_anchor_balance`` CACHE column carried forward over
    still-Projected rows only, so the accrual compounded on a balance that had
    dropped every row settled since the last assertion and fabricated the
    pre-anchor past.  On the real Money Market the base was ``$2,000.00`` high,
    and because the grid derives its "Interest" row from the gap between the
    kind-correct and cash maps, seeding the accrual off the cache while the cash
    map folded would have rendered that missing money as ``$2,007.01`` of
    interest EARNED (finding N-49).  Both halves move here, together.

    Args:
        account: The interest-bearing account.  The caller is responsible for
            the no-anchor guard.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold; its ``as_of`` is the reader's NOW,
            the floor a still-projected row is clamped up to).
        periods: The pay periods to walk (ordered by ``period_index``).
        interest_params: The account's
            :class:`~app.models.interest_params.InterestParams` (APY +
            compounding frequency) the accrual layers from.

    Returns:
        ``(balances, interest_by_period)`` -- the period_id -> Decimal
        end-balance map (interest layered in) and the period_id -> Decimal
        interest-earned map.
    """
    return _interest.layer_account_interest(
        account,
        ctx,
        periods,
        _cash_fold.cash_period_balances(
            account, ctx.scenario.id, ctx.as_of, periods,
        ),
        interest_params,
    )


def base_account_balance_map(
    account: Account,
    ctx: "BalanceContext",
    periods: list,
) -> "OrderedDict[int, Decimal] | None":
    """Compute period_id -> balance for one account WITHOUT dispatch inputs.

    The base path used by :func:`build_account_balance_map`'s fall-through:
    interest-bearing accounts (HYSA, Money Market, CD, HSA) fold their cash and
    layer modelled interest on top; everything else IS its cash fold.  It
    deliberately takes no amortization-schedule or growth-engine inputs --
    callers that drive those use :func:`build_account_balance_map`.

    **One income basis, and the caller no longer chooses it** (ruling R-Q, plan
    step X-c2b2).  The live override map -- recomputed salary income and derived
    loan debits -- used to be an ARGUMENT threaded down from the seam, and its
    None-handling differed by kind (the plain path auto-built a live map, the
    interest path did not), so an interest account left on the default read cash
    on the LIVE basis against a kind-correct walk on the STORED one.  The fold
    builds its own map over its own plan, so there is no argument left for a
    caller to get wrong.  Measured on the prod-shape clone 2026-07-26: the
    stored and live bases differ on ZERO of 60 columns for every real account.

    Args:
        account: The account to project.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        periods: All user pay periods.

    Returns:
        OrderedDict mapping period_id to Decimal balance, or None if the
        account has no anchor period.
    """
    if account.current_anchor_period_id is None:
        return None

    # Interest-bearing accounts (HYSA, Money Market, CD, HSA): the cash fold
    # with the modelled accrual layered on (``_interest``).
    interest_params = _interest.accrual_params(account)
    if interest_params is not None:
        balances, _ = _account_interest_projection(
            account, ctx, periods, interest_params,
        )
        return balances

    # Standard checking/savings (and any unmatched types) ARE their cash fold:
    # every assertion replayed, every settled row counted from the day it
    # moved, the still-projected plan clamped forward (plan step X-c2b2).  The
    # net-worth aggregate therefore reads the same running total the grid does
    # -- one fold, not two producers that agree by test.
    return _cash_fold.cash_period_balances(
        account, ctx.scenario.id, ctx.as_of, periods,
    )


def interest_projection_for_account(
    account: Account,
    ctx: "BalanceContext",
    periods: list,
    interest_params: InterestParams,
) -> "tuple[OrderedDict[int, Decimal], dict[int, Decimal]]":
    """Return an interest account's BALANCES and its earned interest, together.

    The account-detail page renders both -- the balance chart / hero and the
    "Interest, next 12 mo" chip -- and they must be the same walk or the chip
    would explain a balance change the page does not show.  Reading them
    through one call is what makes that structural instead of a claim: the
    alternative it replaced was ``balance_map`` followed by
    :func:`interest_by_period_for_account`, each of which discarded the half
    the other wanted and, since plan step X-c2b2, each of which ran a FULL
    cash fold -- two walks, two plan loads and two live-override builds (the
    ~90 ms salary / loan recompute) for one render.

    Args:
        account: The interest-bearing account.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold; its ``as_of`` is the reader's NOW).
        periods: All user pay periods (the walk domain; the caller filters to
            the periods it wants).
        interest_params: The account's
            :class:`~app.models.interest_params.InterestParams`.

    Returns:
        ``(balances, interest_by_period)`` -- the interest-accrued end balance
        per period id and the interest earned in each.  ``(OrderedDict(), {})``
        when the account has no anchor period.
    """
    if account.current_anchor_period_id is None:
        return OrderedDict(), {}
    return _account_interest_projection(
        account, ctx, periods, interest_params,
    )


def interest_by_period_for_account(
    account: Account,
    ctx: "BalanceContext",
    periods: list,
    interest_params: InterestParams,
) -> dict[int, Decimal]:
    """Return period_id -> interest earned for an interest-bearing account.

    The seam accessor the account-detail page's "Interest, next 12 mo" chip
    (``app.routes.accounts.detail``, its only caller) reads instead of reaching
    the accrual producer directly: interest EARNED is rich projection detail,
    not a balance-at-T figure, so it is not a ``balance_at`` view -- yet it must
    be the SAME walk the balance came from.  This accessor keeps that call
    inside the kernel (beside :func:`base_account_balance_map`, which shares the
    same :func:`_account_interest_projection`) while the consumer sees only the
    interest map it needs.

    A None-anchor account earns no projectable interest, returned as the empty
    map so a caller's windowed sum is ``Decimal("0")``.

    A caller that ALSO wants the balances reads
    :func:`interest_projection_for_account` instead -- one walk, both halves.
    This entry survives for the consumer that genuinely wants the interest
    alone.

    Args:
        account: The interest-bearing account.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold; its ``as_of`` is the reader's NOW).
        periods: All user pay periods (the walk domain; the caller
            filters to the periods whose interest it wants).
        interest_params: The account's
            :class:`~app.models.interest_params.InterestParams`.

    Returns:
        ``dict`` mapping period_id to the ``Decimal`` interest earned in
        that period; ``{}`` when the account has no anchor period.
    """
    if account.current_anchor_period_id is None:
        return {}
    _, interest_by_period = _account_interest_projection(
        account, ctx, periods, interest_params,
    )
    return interest_by_period


def build_account_balance_map(  # pylint: disable=too-many-arguments
    account: Account,
    ctx: "BalanceContext",
    periods: list,
    *,
    investment_params: InvestmentParams | None,
    deductions: list,
    salary_gross_biweekly: Decimal,
) -> "OrderedDict[int, Decimal] | None":
    """Compute period_id -> balance for one NON-loan account, dispatching on type.

    The net-worth path for every kind EXCEPT amortizing loans.  Dispatches to
    the correct calculation engine:

    - Investment (401k, IRA, etc.): the growth engine, fed by this
      account's ``investment_params`` plus its ``deductions`` and the
      engine ``salary_gross_biweekly``.
    - Appreciating physical assets (Property): the appreciation growth curve.
    - Interest-bearing and everything else: the shared
      :func:`base_account_balance_map`.

    **AMORTIZING loans are dispatched by the seam, not here** (plan step C3b3):
    the seam's :func:`app.services.balance_at._account_balance_map` reads its own
    positions()-based per-period map for a loan, because that producer sits ABOVE
    this kernel and the kernel cannot import it back.  A loan therefore never
    reaches this dispatcher through the seam; every branch below is non-loan.

    Takes loose per-account parameters (this account's params, deductions, and
    the engine gross-biweekly) rather than the savings package's projection
    bundle, so a caller need not construct that value object.

    Pylint: ``too-many-arguments`` (6/5) -- the keyword-only group is this
    account's three independent projection inputs (its investment params, its
    deductions, the engine gross-biweekly).  They are not a cohesive named
    concept that would survive as a value object, and re-creating a
    kernel-specific bundle no other caller shares would be the stamp coupling
    the standards reject.  Keyword-only keeps the call sites self-documenting
    (and exempts the positional-count rule).

    Args:
        account: The account to project.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
            Only its ``scenario`` is read here -- every remaining branch is a
            leaf producer with no clock -- so the branches take ``ctx.scenario``.
            The whole context is threaded (rather than a bare ``scenario``)
            because the seam's :func:`account_balance_map_from_inputs` holds it
            and the loan arm that DID need the clock has moved to the seam.
        periods: All user pay periods.
        investment_params: This account's
            :class:`~app.models.investment_params.InvestmentParams`, or
            ``None`` when it is not a parameterized investment account.
        deductions: This account's active paycheck deductions (the
            growth engine's contribution feed; adapted internally).
        salary_gross_biweekly: Raise-aware engine gross per pay period
            (the employer-match cap basis).

    Returns:
        OrderedDict mapping period_id to Decimal balance, or None if the
        account has no anchor period.
    """
    if account.current_anchor_period_id is None:
        return None

    # MED-01 / S6-03: single flag-driven classifier replaces the
    # divergent branch ladders that used to express the same taxonomy
    # two different ways here and in
    # ``savings_dashboard_service._compute_account_projections``.
    kind = classify_account(account)

    # AMORTIZING loans are NOT dispatched here: the seam
    # (:func:`app.services.balance_at._account_balance_map`) reads its own
    # positions()-based per-period map for them (plan step C3b3), because that
    # producer sits ABOVE this kernel and this module cannot import it back.  A
    # loan therefore never reaches this dispatcher through the seam; the branches
    # below are cash / interest / investment / appreciation only.

    # Investment accounts: use the growth engine.  The base balance
    # feeding the projection comes from the canonical entries-aware
    # producer (E-25 / CRIT-01 / R-1).  The investment growth sub-chain
    # (``_investment``, extracted at the module-size ceiling) composes this
    # kernel's ``investment_base_balance_map`` seed; it is a plain sibling
    # import now that both live in the seam package (it imports nothing back,
    # so there is no cycle -- the reason the old cross-module lazy import is
    # gone, plan step D1d).
    if kind is AccountProjectionKind.INVESTMENT and investment_params is not None:
        return _investment.build_investment_balance_map(
            account, investment_params, ctx.scenario, periods,
            deductions, salary_gross_biweekly,
        )

    # Appreciating physical assets (Property): the user-set market value
    # compounds forward at its annual rate.  The rate rides on the
    # account's eager ``asset_appreciation_params`` backref, so no new
    # dispatch kwarg is needed; the helper flat-carries when the params
    # row is absent.  Same ``_investment`` sibling as the investment branch.
    if kind is AccountProjectionKind.APPRECIATING:
        return _investment.build_appreciation_balance_map(
            account, ctx.scenario, periods,
        )

    # Interest-bearing and plain accounts share the base path: the cash fold,
    # with the modelled accrual layered on for INTEREST.
    return base_account_balance_map(account, ctx, periods)


def account_balance_map_from_inputs(
    account: Account,
    ctx: "BalanceContext",
    periods: list,
    inputs,
) -> "OrderedDict[int, Decimal] | None":
    """Unpack a per-set projection bundle for one account and dispatch.

    The ``balance_at`` seam's unpack-and-dispatch site for NON-loan accounts
    (:func:`app.services.balance_at._account_balance_map` calls it for both
    the single-account and batch paths, after routing amortizing loans to its
    own positions()-based map): it slices the three projection inputs
    :func:`build_account_balance_map` needs for *account* out of a pre-assembled
    bundle and calls it.  Kept here in the engine cluster, beside the dispatcher
    it feeds, so the bundle-field-to-kwarg slice rule lives with
    :func:`build_account_balance_map` rather than in the seam.  The seam is
    its sole caller.  (The year-end summary's adapter sliced an identical
    bundle here too -- the R0801 this shared site closed -- until that package
    was deleted at plan step F2.)

    ``inputs`` is duck-typed: any bundle exposing ``investment_params_map``,
    ``deductions_by_account``, and ``salary_gross_biweekly`` qualifies (the
    bundle's ``debt_schedules`` is read by the seam's loan arm, not here).  The
    seam's
    :class:`app.services.balance_at._AssembledInputs` satisfies this
    contract.  It is intentionally left unannotated: that concrete bundle
    type lives in a consumer package this engine module must not import
    (the dependency direction), so the structural contract is documented
    here rather than expressed by a shared type.

    Args:
        account: The account to project.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        periods: The pay periods to project over.
        inputs: The per-set projection bundle (see the duck-typed contract
            above).

    Returns:
        OrderedDict mapping period_id to Decimal balance, or None when the
        account has no anchor period.
    """
    return build_account_balance_map(
        account, ctx, periods,
        investment_params=inputs.investment_params_map.get(account.id),
        deductions=inputs.deductions_by_account.get(account.id, []),
        salary_gross_biweekly=inputs.salary_gross_biweekly,
    )
