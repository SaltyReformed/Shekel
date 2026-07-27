"""Balance-at-T seam -- the KIND-CORRECT view (per-account-kind dispatch).

The view the NET-WORTH surfaces read (savings cockpit, year-end summary,
dashboards): a HYSA accrues interest, a loan walks its amortization schedule,
an investment / property compounds.  See the package docstring
(:mod:`app.services.balance_at`) for the "three shapes, one seam" contract and
the four per-kind boundary rules these entries own.

**There are TWO kinds here, not five** (plan step X-g2b).  A configured loan is
its amortization ``positions()``; everything else is ONE event replay
(:mod:`app.services.balance_at._asset_fold`), whose ACCRUAL tier exists only for
an account that models a return and whose CONTRIBUTION tier only for one whose
payroll funds it.  So a HYSA, a brokerage, a Property and a checking account are
not four dispatches -- they are one producer given different facts, which is
what deleted the period-granular arm these entries used to carry for three of
the kinds (finding N-71) and with it ``find_period_containing_date`` and the
pre-horizon anchor fallback (finding N-29).

Also home to :func:`investment_growth_since_anchor`, the growth-vs-contributed
decomposition the investment detail page's chip renders, so no consumer reaches
a raw producer for it.  Its sibling ``investment_seed_map`` is GONE (ruling
R-AE): a chart's pre-growth seed existed only because the previous design could
not express "this account's balance at a DATE", and it can.
"""

from collections import OrderedDict
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)

from ._context import BalanceContext

from . import _asset_fold
from ._inputs import (
    _account_balance_map,
    _assemble_inputs,
    _contribution_inputs,
    _require_scenario,
)
from ._positions import positions
from ._resolution import resolved_loan


def balance_map(
    account: Account, ctx: BalanceContext, periods: list,
) -> "OrderedDict[int, Decimal] | None":
    """Return one account's period_id -> balance map across *periods*.

    The single-account per-period producer.  Assembles THIS account's
    inputs via the shared :func:`._inputs._assemble_inputs` (its debt schedule
    when it is an amortizing loan, its investment params, its deductions when it
    has params, and the engine gross-biweekly) and delegates the per-kind
    dispatch to the kernel via :func:`._inputs._account_balance_map` -- the same
    code path :func:`build_maps` runs per account, so single- and batch-assembly
    cannot drift.

    **There is ONE income basis and the caller does not choose it** (ruling R-Q,
    plan step X-c2b2).  This entry used to take an ``amount_overrides`` map
    whose None-handling differed by kind -- the plain path auto-built a LIVE map
    while the interest path fell back to the STORED ``estimated_amount`` -- so
    two walks of one account could land on two income bases and the difference
    surfaced as interest.  The cash fold builds its own map over its own plan,
    so the argument has nothing left to keep in step and is gone.

    Args:
        account: The account to project.  Its ``user_id`` scopes the
            deduction / gross loaders; its ``account_type`` drives the
            classifier.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the producers; its ``as_of`` is the resolver's
            now, and it memoizes each loan's resolution for the pass).
        periods: The pay periods to project over, ordered by
            ``period_index``.

    Returns:
        The OrderedDict period_id -> Decimal balance, or ``None`` when the
        account has no anchor period.

    Raises:
        ValueError: When ``scenario`` is None -- callers that resolve a
            nullable baseline must guard first.
    """
    # Rerouted callers (e.g. ``build_account_net_worth_maps``) keep their own
    # ``if scenario is None: return []`` guard, so the legitimate empty state
    # is preserved; the seam raising here is the defensive contract that turns
    # a deep AttributeError (or a silent $0 net worth) into a clear failure.
    _require_scenario(ctx)
    inputs = _assemble_inputs([account], ctx)
    return _account_balance_map(account, ctx, periods, inputs)


def build_maps(
    accounts: list[Account],
    ctx: BalanceContext,
    periods: list,
) -> "dict[int, OrderedDict[int, Decimal]]":
    """Return account_id -> period balance map for many accounts (batch).

    The batch producer that preserves the existing N+1 avoidance: it
    assembles ALL inputs ONCE via :func:`._inputs._assemble_inputs` (one
    debt-schedule generation over the loan subset, one investment-params query,
    one deductions query, one gross fetch for the whole set), then loops the
    shared :func:`._inputs._account_balance_map` per account.  This is the
    per-account dense-map build the savings cockpit's
    ``build_account_net_worth_maps`` performs today, internalised behind the
    seam so the assembly lives in one place.

    Accounts whose map is ``None`` (no anchor period) are omitted from the
    result, matching the net-worth section's ``balances is None`` skip.

    Args:
        accounts: The accounts to project (the same user's active set).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        periods: The pay periods to project over (the dense domain -- pass
            ALL of the user's periods so the cash / investment paths have
            their anchor seed).

    Returns:
        A dict mapping ``account.id`` to its OrderedDict period_id ->
        Decimal balance map, for every account that has a map.

    Raises:
        ValueError: When ``scenario`` is None -- callers that resolve a
            nullable baseline must guard first.
    """
    _require_scenario(ctx)
    inputs = _assemble_inputs(accounts, ctx)
    result: "dict[int, OrderedDict[int, Decimal]]" = {}
    for account in accounts:
        balances = _account_balance_map(account, ctx, periods, inputs)
        if balances is None:
            continue
        result[account.id] = balances
    return result


def _modelled_scalar(
    account: Account, ctx: BalanceContext, as_of: date,
) -> Decimal:
    """Return the account's replayed balance at one date.

    The non-loan arm of :func:`balance_at`, for EVERY non-loan kind: a plain
    checking account, an HYSA, a brokerage and a Property all read the one
    replay, which is the cash fold plus whatever modelled tiers the account's
    own parameters put on it.

    **It reaches the replay through the SAME assembly the map does**
    (:func:`._inputs._assemble_inputs` sliced by
    :func:`._inputs._contribution_inputs`), so the scalar and the period map
    cannot be given different contribution feeds for one account -- the shape
    plan Section 8 rules a defect rather than a contract.  The assembly costs
    one indexed investment-params lookup for an account that has none, and its
    deduction and gross loads are already scoped to the accounts that do.

    Measured before the PLAIN branch was routed here: on the real Checking
    account (804 transaction rows, 840-day horizon) the replay costs
    ``92.3 ms`` against the cash fold's ``91.7 ms`` -- the load dominates, and
    an account that models no return adds only the per-day collapse.

    Args:
        account: The account to value; its kind is consulted only by the replay.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold; its ``as_of`` is the reader's NOW).
        as_of: The VALUATION date.

    Returns:
        The cent-quantized ``Decimal`` balance at *as_of*.
    """
    return _asset_fold.fold_asset_balances(
        account, ctx, [as_of],
        _contribution_inputs(account, _assemble_inputs([account], ctx)),
    )[as_of]


def balance_at(
    account: Account, ctx: BalanceContext, as_of: date,
) -> Decimal:
    """Return one account's balance as of a calendar date *as_of*.

    The scalar-at-a-date producer, and since plan step X-g2b it has exactly ONE
    branch: is this a CONFIGURED LOAN?

    * **A configured loan** -> :func:`~app.services.balance_at.positions`: the
      event FOLD over the loan's SOURCE facts for a date at or before the
      resolver's now (the only complete record of the past -- it books the
      true-ups that never appear as schedule rows), and the forward schedule
      projection after (step C3b).  This scalar is also the accessor a consumer
      wanting a loan's PAST balance must use; the seam's forward-only liability
      view (:func:`~app.services.balance_at.liability_owed_at_dates`)
      deliberately refuses a past date.
    * **Everything else** -> the event REPLAY
      (:func:`_modelled_scalar`).  That includes an AMORTIZING account with no
      ``LoanParams`` -- a Mortgage typed but never filled in, which has no
      schedule to fold and whose balance is its transaction rows.  The degrade
      is decided on the resolver's own fact (``resolved_loan(...) is None`` iff
      ``generate_debt_schedules`` would skip it), never on a kind test that
      could disagree with it.

    **Every kind is DATE-precise now** (plan step X-g2b, finding N-71).  INTEREST,
    INVESTMENT and APPRECIATING used to resolve *as_of* to the pay period
    containing it and read the period-keyed map there, so a whole period's
    modelled growth landed on the period's FIRST day: measured at period 30 on
    the prod-shape clone, the scalar returned the IDENTICAL value on that
    period's first and last day while ``$328.50`` accrued inside it on the
    Empower 401(k), ``$261.24`` on the Money Market and ``$114.07`` on the Roth.
    A replay has a step for every day, so there is no period to stand in for a
    date and no ``find_period_containing_date`` call left to make.

    **The pre-horizon fallback is gone with it** (finding N-29).  A date before
    the user's first pay period used to return the LATEST anchor balance
    rounded to cents -- today's balance, reported for a date months earlier
    (the real Roth answered ``$28,000.00`` for 2026-01-15 against a
    back-projected ``$22,909.02``).  The fold is TOTAL: it answers a date before
    every event with ruling R-I's back-projection and a date past the horizon by
    continuing to accrue, so there is no out-of-range state to fall back FROM.
    Finding **N-82** records what the far end costs: past the last pay period
    the ACCRUAL tier keeps running while the CONTRIBUTION tier stops, because a
    contribution is dated on a real payday and there are none out there.

    **Two dates, deliberately distinct.**  ``ctx.as_of`` is the resolver's NOW --
    the moment a loan is RESOLVED at, deciding what is confirmed and what it
    currently owes, and the floor ruling R-G clamps a still-projected row up to.
    *as_of* is the VALUATION date -- the moment to value the account AT, which
    may be long past or far future.  They are the same value on a plain "what is
    it worth today" read, which is exactly why they were conflated for so long:
    "now" was an unnamed ``date.today()`` inside each producer, so a caller
    asking for a historical valuation silently got it measured against a loan
    resolved at today, with no way to say otherwise.

    Args:
        account: The account to value.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the resolver / loan schedule; its ``as_of`` is
            the resolver's NOW -- see above).
        as_of: The calendar date to value the account at.

    Returns:
        The ``Decimal`` balance at *as_of*.

    Raises:
        ValueError: When ``scenario`` is None -- callers that resolve a
            nullable baseline must guard first.
    """
    _require_scenario(ctx)
    if (classify_account(account) is AccountProjectionKind.AMORTIZING
            and resolved_loan(account, ctx) is not None):
        return positions(account, ctx, [as_of])[as_of]
    return _modelled_scalar(account, ctx, as_of)


def investment_growth_since_anchor(
    account: Account, ctx: BalanceContext, current_period,
) -> "tuple[Decimal, Decimal] | None":
    """Return ``(growth, contributed)`` since the anchor, or ``None`` (hidden).

    The seam entry for the investment detail page's growth chip, read off the
    replay's own two modelled tiers rather than re-projected (ruling R-AC).

    **"Since the anchor" needs no window arithmetic**, which is what makes this
    ONE sample rather than a difference of two: ACCRUAL exists only from the
    latest assertion's own day forward (rulings R-L / R-Y) and a CONTRIBUTION
    only strictly after it (ruling R-Z), so the cumulative total at a date IS
    the total since the anchor.  A window subtraction would state the same
    boundary a second time, and a second statement of a boundary is where this
    arc's defects live.

    **It reports through the CURRENT PERIOD'S END, because that is where the
    headline it explains is read.**  ``compute_dashboard_data`` renders
    ``balance_map[current_period]`` -- a period-end figure, the convention every
    net-worth surface has used since plan step X-c2b2 -- so reading the chip at
    today would explain a balance the page is not showing, by the accrual of the
    days between (measured ``$9.65`` to ``$26.05`` on the three real accounts).
    For an account holding no recorded rows in the window,
    ``growth + contributed == balance_map[current] - anchor_balance`` exactly,
    every term being a whole cent (ruling R-X); an account that HAS rows adds a
    cash term the chip does not claim to explain, which the shipped
    decomposition could not express at all.

    **The ``periods`` argument is GONE** (plan step X-g2b).  The shipped entry
    took the user's whole pay-period list to hand the growth engine a
    pre/post-anchor split; the replay walks its own calendar, so the argument
    became one a caller could get wrong for no benefit -- pass a window and the
    shipped producer silently answered about that window instead.

    **``contributed`` counts MODELLED contributions only** (ruling R-R's
    partition): a recorded transfer into the account is a cash event in the fold
    beneath, not a contribution this tier re-applies.  The shipped engine folded
    both feeds into one averaged ``periodic_contribution``, which is exactly
    what made them indistinguishable.

    Args:
        account: The investment account.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        current_period: The current :class:`~app.models.pay_period.PayPeriod`,
            or ``None``.

    Returns:
        ``(growth, contributed)`` cent-precise ``Decimal``s, or ``None`` when
        the account has no investment params or there is no current period --
        the two states in which the page hides the chip.  It NO LONGER hides for
        an account anchored in the current period: ruling R-Y gives that period
        its own accrual (measured ``$105.26`` on the Roth, ``$44.95`` on the
        Traditional IRA, ``$76.59`` on the Empower at their anchor periods), so
        hiding would deny a figure the balance beside it already contains.

    Raises:
        ValueError: When ``scenario`` is None -- callers that resolve a
            nullable baseline must guard first.
    """
    _require_scenario(ctx)
    inputs = _assemble_inputs([account], ctx)
    if inputs.investment_params_map.get(account.id) is None:
        return None
    if current_period is None:
        return None
    return _asset_fold.asset_growth_at(
        account, ctx, current_period.end_date,
        _contribution_inputs(account, inputs),
    )
