"""
Shekel Budget App -- Net-Worth Kernel (shared per-account balance chain).

The single, Flask-free home for the per-account balance-map projection
chain the ``balance_at`` seam dispatches through.  Promoted out of
``year_end_summary_service._balances`` (Loop B Phase 1) when the year-end
summary and the savings cockpit computed net worth from two copies of the
same math that could drift; the year-end consumer has since been deleted
(plan step F2).

**There is no per-kind dispatch left here** (plan step X-g2b, ruling R-AD).
Every non-loan account's map is ONE event replay
(:func:`app.services.balance_at._asset_fold.asset_period_view`): an account
that models a return gets its ACCRUAL and CONTRIBUTION tiers, and an account
that models none IS its cash fold, which is the same statement rather than a
fall-through.  The ladder this module used to hold -- INVESTMENT to the growth
engine, APPRECIATING to the appreciation curve, INTEREST to the accrual layer,
everything else to the cash fold -- had four branches for one question, and
each branch answered a period rather than a date (finding N-71) and spliced
three sources by a preference order (findings N-43 / N-74).  Verified before
the ladder was deleted: on both real databases the replay reproduces the PLAIN
fall-through to the cent on every one of 60 columns, for all three real plain
accounts.

AMORTIZING loans are NOT dispatched here at all: the seam reads its own
``positions()``-based map for them (plan step C3b3), because that producer sits
above this module.  An AMORTIZING account with no ``LoanParams`` does reach
here, and models no return, so it is its cash fold -- the same degrade the
seam's scalar makes.

The cockpit's forward net-worth trend PROJECTS investment and retirement
growth forward, and that forward WHAT-IF keeps ``growth_engine`` (ruling R-U);
what moved here is the balance-at-T half.  The loan SCHEDULE bundle
(:class:`DebtSchedule`) still lives here because the trend needs it.

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"): this
module imports no Flask symbol and performs no database writes.  Since plan
step X-c2b3 it issues no QUERY either -- every row it dispatches over is loaded
by the leaf or the fold below it.  All money is :class:`~decimal.Decimal`;
``float`` belongs only at a route's Chart.js serialization boundary, never
here.

Its public producers take ONE per-account bundle
(:class:`~app.services.balance_at._asset_contributions.ContributionInputs`)
rather than the three loose parameters the growth-engine dispatch needed, and
the wider seam bundle is sliced into it by :mod:`._inputs`, where that bundle
is defined -- so this module no longer duck-types a value object it must not
import (plan step X-g2b; the slice used to live here as
``account_balance_map_from_inputs``).
"""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.account import Account

from ._asset_contributions import ContributionInputs
from ._context import BalanceContext
from ._fold import fold_from_walk
from ._resolution import ResolvedLoan, resolved_loan
from . import _asset_fold


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

def _modelled_columns(
    account: Account,
    ctx: "BalanceContext",
    periods: list,
    inputs: ContributionInputs,
) -> "OrderedDict[int, _asset_fold.AssetPeriodFigures]":
    """Resolve *account*'s modelled column for each period -- ONE replay.

    The single home for "replay this account's event stream and read it at
    every period end", shared by the two public readers below: the BALANCE
    map (:func:`build_account_balance_map`, which keeps
    :attr:`~app.services.balance_at._asset_fold.AssetPeriodFigures.balance`)
    and the modelled-return accessors
    (:func:`interest_projection_for_account` /
    :func:`interest_by_period_for_account`, which also keep
    :attr:`~app.services.balance_at._asset_fold.AssetPeriodFigures.accrual`).
    Folding the two into one helper is what keeps the balance a screen renders
    and the accrual figure beside it from being two walks -- the reason ruling
    R-L had to move both readers at once (finding N-47), preserved here now
    that both read the replay instead of the layered accrual.

    Args:
        account: The account to project.  Its kind is consulted only by the
            replay, to decide whether it models a return at all.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold and the contribution feed; its
            ``as_of`` is ruling R-G's clamp floor).
        periods: The pay periods to walk (the output domain).
        inputs: The account's
            :class:`~app.services.balance_at._asset_contributions.ContributionInputs`.

    Returns:
        ``OrderedDict`` period id ->
        :class:`~app.services.balance_at._asset_fold.AssetPeriodFigures`, one
        per requested period.
    """
    return _asset_fold.asset_period_view(account, ctx, periods, inputs)


def interest_projection_for_account(
    account: Account,
    ctx: "BalanceContext",
    periods: list,
) -> "tuple[OrderedDict[int, Decimal], dict[int, Decimal]]":
    """Return an interest account's BALANCES and its earned interest, together.

    The account-detail page renders both -- the balance chart / hero and the
    "Interest, next 12 mo" chip -- and they must be the same walk or the chip
    would explain a balance change the page does not show.  Reading them
    through one call is what makes that structural instead of a claim: the
    alternative it replaced was ``balance_map`` followed by
    :func:`interest_by_period_for_account`, each of which discarded the half
    the other wanted and each of which ran a FULL fold -- two walks, two plan
    loads and two live-override builds (the ~90 ms salary / loan recompute) for
    one render.

    **It takes no ``interest_params`` argument any more** (plan step X-g2b).
    The replay reads the account's own accrual rule through the ONE predicate
    :func:`app.services.balance_at._interest.accrual_params`, so the rate can
    no longer arrive from a caller that loaded a different row than the one the
    account carries -- the argument-a-caller-can-get-wrong shape the plan's
    Section 8 rules a defect rather than a contract.

    Args:
        account: The interest-bearing account.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold; its ``as_of`` is the reader's NOW).
        periods: All user pay periods (the walk domain; the caller filters to
            the periods it wants).

    Returns:
        ``(balances, interest_by_period)`` -- the interest-accrued end balance
        per period id and the interest earned in each.  ``(OrderedDict(), {})``
        when the account has no anchor period.
    """
    if account.current_anchor_period_id is None:
        return OrderedDict(), {}
    columns = _modelled_columns(
        account, ctx, periods, ContributionInputs.absent(),
    )
    return (
        OrderedDict(
            (period_id, column.balance)
            for period_id, column in columns.items()
        ),
        {
            period_id: column.accrual
            for period_id, column in columns.items()
        },
    )


def interest_by_period_for_account(
    account: Account,
    ctx: "BalanceContext",
    periods: list,
) -> dict[int, Decimal]:
    """Return period_id -> interest earned for an interest-bearing account.

    The seam accessor for a consumer that wants the interest EARNED without the
    balances: interest earned is rich projection detail, not a balance-at-T
    figure, so it is not a ``balance_at`` view -- yet it must be the SAME walk
    the balance came from, which is what keeping it beside
    :func:`interest_projection_for_account` on one shared
    :func:`_modelled_columns` guarantees.

    A None-anchor account earns no projectable interest, returned as the empty
    map so a caller's windowed sum is ``Decimal("0")``.

    **It has no production caller today** -- the account-detail page reads
    :func:`interest_projection_for_account` for both halves (finding N-64) --
    so this entry survives on its own tests, which is the dead-code-alive-for-
    its-own-tests shape plan steps C3b4 / D2a / F2 / E1e each deleted.  It is
    kept here rather than deleted because plan step X-g2b's contract is to move
    producers onto the replay, not to prune the seam's surface (rule 6);
    finding **N-85** records it for the deletion step.

    Args:
        account: The interest-bearing account.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold; its ``as_of`` is the reader's NOW).
        periods: All user pay periods (the walk domain; the caller
            filters to the periods whose interest it wants).

    Returns:
        ``dict`` mapping period_id to the ``Decimal`` interest earned in
        that period; ``{}`` when the account has no anchor period.
    """
    _, interest_by_period = interest_projection_for_account(
        account, ctx, periods,
    )
    return interest_by_period


def build_account_balance_map(
    account: Account,
    ctx: "BalanceContext",
    periods: list,
    inputs: ContributionInputs,
) -> "OrderedDict[int, Decimal] | None":
    """Compute period_id -> balance for one NON-loan account.

    The net-worth path for every kind EXCEPT amortizing loans, and since plan
    step X-g2b it dispatches on NOTHING: the account's balance is its event
    replay, whose ACCRUAL tier exists only if the account models a return and
    whose CONTRIBUTION tier exists only if its payroll funds it.  An INTEREST
    account, an INVESTMENT, a Property and a plain checking account are one
    question asked once (ruling R-AD).

    **What that replaced, and why the branches were the defect rather than the
    structure.**  The ladder here routed INVESTMENT to
    ``growth_engine.project_balance`` spliced over a cash base by a preference
    order, APPRECIATING to an appreciation curve over a flat anchor carry, and
    INTEREST to a second pass over a finished base map.  The splice overrode 12
    of the 15 balance assertions the three modelled accounts actually carry
    (findings N-43 / N-74), the second pass accrued on a period's END balance
    while the curve grew its START (two conventions for one question), and all
    three answered a PERIOD where the caller asked for a DATE (finding N-71).
    One replay has no join to get wrong, no boundary convention to pick, and a
    step for every day.

    **AMORTIZING loans are dispatched by the seam, not here** (plan step C3b3):
    :func:`app.services.balance_at._inputs._account_balance_map` reads its own
    positions()-based per-period map for a configured loan, because that
    producer sits ABOVE this kernel and the kernel cannot import it back.  An
    AMORTIZING account with NO ``LoanParams`` does arrive here, models no
    return, and is therefore its cash fold -- the same degrade the seam's
    scalar makes for it.

    Args:
        account: The account to project.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold and the contribution feed; its
            ``as_of`` is ruling R-G's clamp floor).
        periods: All user pay periods.
        inputs: This account's
            :class:`~app.services.balance_at._asset_contributions.ContributionInputs`
            -- its investment params, its deductions and the engine
            gross-biweekly, loaded by
            :func:`app.services.balance_at._inputs._contribution_inputs_for_accounts`.
            Its ``absent()`` constructor is the explicit token for an account
            that cannot have a contribution feed.

    Returns:
        OrderedDict mapping period_id to Decimal balance, or None if the
        account has no anchor period.
    """
    if account.current_anchor_period_id is None:
        return None
    return OrderedDict(
        (period_id, column.balance)
        for period_id, column in _modelled_columns(
            account, ctx, periods, inputs,
        ).items()
    )
