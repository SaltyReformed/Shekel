"""Balance-at-T seam -- the KIND-CORRECT view (per-account-kind dispatch).

The view the NET-WORTH surfaces read (savings cockpit, year-end summary,
dashboards): a HYSA accrues interest, a loan walks its amortization schedule,
an investment / property compounds.  See the package docstring
(:mod:`app.services.balance_at`) for the "three shapes, one seam" contract and
the four per-kind boundary rules these entries own.

Also home to the two investment PROJECTION-INPUT accessors the seam fences --
:func:`investment_seed_map` (the pre-growth seed a chart compounds FROM, not a
balance to display) and :func:`investment_growth_since_anchor` (the
growth-vs-contributed decomposition) -- so no consumer reaches the raw kernel
producers they wrap.
"""

from collections import OrderedDict
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.services import (
    cash_ledger,
    pay_period_service,
)
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.loan_ledger import find_period_containing_date
from app.utils.money import round_money

from ._context import BalanceContext

from . import _cash_fold, _investment
from ._inputs import _account_balance_map, _assemble_inputs, _require_scenario
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


def _cash_scalar(
    account: Account, ctx: BalanceContext, as_of: date,
) -> Decimal:
    """Return the account's folded cash balance at one date.

    The kind-correct scalar's cash arm, reached by its PLAIN branch and by its
    degraded-AMORTIZING branch (an amortizing account with no ``LoanParams``,
    which has no schedule to fold and is valued over its own transaction rows).
    Both took the same producer before the cutover and both take the same fold
    after; naming it once is what keeps them from drifting apart, since they are
    the same question asked about two kinds (plan finding N-47).

    Args:
        account: The account to value; its kind is not consulted here.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold; its ``as_of`` is the reader's NOW).
        as_of: The VALUATION date.

    Returns:
        The cent-quantized ``Decimal`` balance at *as_of*.
    """
    return _cash_fold.fold_cash_balances(
        account, ctx.scenario.id, ctx.as_of, [as_of],
    )[as_of]


def balance_at(
    account: Account, ctx: BalanceContext, as_of: date,
) -> Decimal:
    """Return one account's balance as of a calendar date *as_of*.

    The scalar-at-a-date producer, dispatched by
    :func:`~app.services.account_projection.classify_account`:

    * **PLAIN (checking / plain savings)** -> the date-precise cash FOLD
      (:func:`app.services.balance_at._cash_fold.fold_cash_balances`), which is
      the SAME call :func:`~app.services.balance_at.cash_balance_at` makes --
      deliberately, because for a plain account the kind-correct balance IS the
      cash-flow balance, so /savings and the dashboard cannot answer one date
      two ways (plan finding N-47).
    * **AMORTIZING (loan)** -> :func:`~app.services.balance_at.positions`: the event
      FOLD over the loan's SOURCE facts for a date at or before the resolver's now
      (the only complete record of the past -- it books the true-ups that never
      appear as schedule rows), and the forward schedule projection after (step
      C3b).  An AMORTIZING account with no ``LoanParams`` has no schedule to fold and
      degrades to the cash producer here (``positions()`` is loan-only).  This
      scalar is also the accessor a consumer wanting a loan's PAST balance must use;
      the seam's forward-only liability view
      (:func:`~app.services.balance_at.liability_owed_at_dates`) deliberately refuses
      a past date.
    * **INTEREST / INVESTMENT / APPRECIATING** -> the value of
      :func:`balance_map` at the period containing *as_of* (these kinds are
      period-granular: their model is period-keyed, so a date resolves to its
      period).  INTEREST is here -- NOT on the cash path -- so the scalar
      stays consistent with the map: an HYSA's kind-correct balance ACCRUES
      interest (``balance_at(d) == balance_map[period containing d]``); a
      caller that wants the no-interest transaction balance of an
      interest-bearing account asks
      :func:`~app.services.balance_at.cash_balance_at` instead.

    Granularity note: PLAIN and loan are DATE-precise -- PLAIN sums dated rows
    up to *as_of*, and the loan walks its amortization schedule to the exact
    *as_of* date.  INTEREST / INVESTMENT / APPRECIATING are period-granular:
    they answer "what is the balance at the end of the period containing
    *as_of*?"  This matches how each kind is actually stored.

    Out-of-range / no-map behavior (INTEREST / INVESTMENT / APPRECIATING):
    when *as_of* falls BEFORE the user's entire pay-period horizon (no period
    contains or precedes it -- ``find_period_containing_date`` falls back to
    the latest period that ENDED earlier, so a date after the horizon reads
    that period's balance rather than this fallback) or the account has no
    projectable map, the seam returns the canonical anchor balance from
    :func:`~app.services.cash_ledger.resolve_anchor`, rounded to cents.
    A genuinely corrupt account with no anchor history makes ``resolve_anchor``
    raise, which is the correct loud failure rather than a silently wrong
    number.  The PLAIN and loan branches above need no such fallback: both are
    TOTAL folds that answer any date, including one before the user's first pay
    period.

    **Two dates, deliberately distinct.**  ``ctx.as_of`` is the resolver's NOW --
    the moment a loan is RESOLVED at, deciding what is confirmed and what it
    currently owes.  *as_of* is the VALUATION date -- the moment to value the
    account AT, which may be long past or far future.  They are the same value on
    a plain "what is it worth today" read, which is exactly why they were
    conflated for so long: "now" was an unnamed ``date.today()`` inside each
    producer, so a caller asking for a historical valuation silently got it
    measured against a loan resolved at today, with no way to say otherwise.

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
    kind = classify_account(account)

    # PLAIN is the only kind whose kind-correct balance IS its transaction
    # balance, so it alone takes the date-precise cash fold.  INTEREST is
    # NOT here: its kind-correct balance accrues interest, so it falls through
    # to the period-granular ``balance_map`` path below -- keeping the scalar
    # consistent with the map for an HYSA (the no-interest transaction balance
    # is ``cash_balance_at``'s job, not this kind-correct scalar's).
    if kind is AccountProjectionKind.PLAIN:
        return _cash_scalar(account, ctx, as_of)

    if kind is AccountProjectionKind.AMORTIZING:
        # A configured loan reads the ONE total producer positions(): the FOLD over
        # source events for a past date, the schedule projection after (step C3b).
        # An AMORTIZING account with no LoanParams -- a Mortgage typed but never
        # filled in -- has no schedule to fold, so it degrades to the cash fold
        # over its own transaction rows.  positions() fails loud for such an
        # account, so the degrade is decided HERE on the resolver's fact
        # (``resolved_loan(...) is None`` iff generate_debt_schedules would skip it).
        if resolved_loan(account, ctx) is None:
            return _cash_scalar(account, ctx, as_of)
        return positions(account, ctx, [as_of])[as_of]

    # INTEREST / INVESTMENT / APPRECIATING: locate the period containing as_of
    # and read the period-keyed map's value there.  INTEREST routes here (not
    # the cash branch above) so the scalar accrues interest in step with
    # balance_map for an HYSA.
    periods = pay_period_service.get_all_periods(account.user_id)
    balances = balance_map(account, ctx, periods)
    target_period = find_period_containing_date(periods, as_of)
    if balances is not None and target_period is not None:
        located = balances.get(target_period.id)
        if located is not None:
            # Returned verbatim (no re-round): the interest / growth /
            # appreciation end balances are already cent-quantized from
            # round_money'd components, so balance_at == balance_map[period]
            # penny-exact.
            return located

    # as_of precedes the user's pay-period horizon, or the account has no
    # projectable map: fall back to the canonical anchor balance.
    return round_money(
        cash_ledger.resolve_anchor(account, ctx.scenario.id).balance,
    )


def investment_seed_map(
    account: Account, ctx: BalanceContext, periods: list,
) -> "OrderedDict[int, Decimal]":
    """Return an investment's cash-basis (pre-growth) SEED map.

    The transaction-sum balance an investment account holds from its anchor
    plus contributions, with NO modeled growth layered on.  This is NOT a
    balance to DISPLAY -- that is :func:`balance_map`, which layers the
    modeled growth on top.  It is the projection INPUT a forward growth chart
    compounds FROM: the investment / retirement dashboards' growth curves and
    the year-end savings-progress re-projection each seed off this pre-growth
    map, so none re-derives the seed and -- critically -- none seeds off the
    already-modeled :func:`balance_map` (which would compound growth on top of
    growth, re-growing the current period).

    The seam owns this read (delegating to
    :func:`~app.services.balance_at._investment.investment_base_balance_map`) so
    that EVERY balance map -- the modeled one a screen DISPLAYS and the
    pre-growth one a chart SEEDS from -- flows through this one package, and the
    raw producer sits in a private seam module W9910 protects.  A consumer
    that needs the seed reads it HERE, never the kernel function directly; the
    distinct name (``investment_seed_map`` vs ``balance_map``) is the signal
    that its value is a projection seed, not a balance to render.

    Args:
        account: The investment account.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the resolver).
        periods: The pay periods to span (ordered by ``period_index``; must
            include the anchor so the resolver has its running seed).

    Returns:
        The ``OrderedDict`` period_id -> Decimal cash-basis (pre-growth)
        balance.

    Raises:
        ValueError: When ``scenario`` is None -- callers that resolve a
            nullable baseline must guard first.
    """
    _require_scenario(ctx)
    return _investment.investment_base_balance_map(
        account, ctx.scenario, periods,
    )


def investment_growth_since_anchor(
    account: Account, ctx: BalanceContext, periods: list, current_period,
) -> "tuple[Decimal, Decimal] | None":
    """Return ``(growth, contributed)`` since the anchor, or ``None`` (hidden).

    The fenced seam entry for the investment detail page's growth chip:
    assembles this account's inputs via the same
    :func:`._inputs._assemble_inputs` the balance maps use -- so the
    decomposition reconciles with :func:`balance_map` to the cent -- and
    delegates to
    :func:`~app.services.balance_at._investment.investment_growth_since_anchor`
    (its docstring owns the reconciliation contract).  ``None`` when the
    account has no investment params / anchor / post-anchor window; raises
    ``ValueError`` when ``scenario`` is None.
    """
    _require_scenario(ctx)
    inputs = _assemble_inputs([account], ctx)
    params = inputs.investment_params_map.get(account.id)
    if params is None:
        return None
    return _investment.investment_growth_since_anchor(
        account, params, ctx.scenario, periods,
        inputs.deductions_by_account.get(account.id, []),
        inputs.salary_gross_biweekly, current_period,
    )
