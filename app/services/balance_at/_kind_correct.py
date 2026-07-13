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
from app.models.scenario import Scenario
from app.services import (
    balance_resolver,
    net_worth_investment,
    net_worth_kernel,
    pay_period_service,
)
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
    find_period_containing_date,
)
from app.utils.money import round_money

from ._inputs import _account_balance_map, _assemble_inputs, _require_scenario


def balance_map(
    account: Account,
    scenario: Scenario,
    periods: list,
    *,
    amount_overrides: "dict[int, Decimal] | None" = None,
) -> "OrderedDict[int, Decimal] | None":
    """Return one account's period_id -> balance map across *periods*.

    The single-account per-period producer.  Assembles THIS account's
    inputs via the shared :func:`._inputs._assemble_inputs` (its debt schedule
    when it is an amortizing loan, its investment params, its deductions when it
    has params, and the engine gross-biweekly) and delegates the per-kind
    dispatch to the kernel via :func:`._inputs._account_balance_map` -- the same
    code path :func:`build_maps` runs per account, so single- and batch-assembly
    cannot drift.

    ``amount_overrides`` is forwarded through the kind dispatch only on the
    cash sub-path of a plain / interest account (loan, investment, and
    appreciation derive from schedules / growth curves and ignore it -- the
    map only carries cash-account transaction ids).  Its None-handling is NOT
    uniform across kinds, and that asymmetry is load-bearing for callers:

    * **PLAIN** routes to
      :func:`~app.services.balance_resolver.balances_for`, which AUTO-BUILDS a
      live projected-net map when ``amount_overrides`` is None -- so omitting
      it yields LIVE income.
    * **INTEREST** routes to
      :func:`~app.services.balance_calculator.calculate_balances_with_interest`,
      which does NOT auto-build -- so omitting it yields STORED income (the
      stored ``estimated_amount``), not live.

    A caller that needs live income on an interest account must therefore pass
    an explicit live map.  That is exactly what
    :func:`~app.services.balance_at.grid_balance_view` does: it threads one map
    to both its cash and kind-correct walks so an interest grid account's
    accrual stays pure and its balance row reconciles with the grid's
    live-income subtotal row.  The investment base independently builds its own
    live overrides inside ``balances_for``, so it is live regardless.  The
    passthrough is parity-tested in ``tests/test_services/test_balance_at.py``.

    Args:
        account: The account to project.  Its ``user_id`` scopes the
            deduction / gross loaders; its ``account_type`` drives the
            classifier.
        scenario: The baseline scenario.
        periods: The pay periods to project over, ordered by
            ``period_index``.
        amount_overrides: Optional ``{transaction_id: Decimal}`` live
            projected-net / loan-derive map, forwarded to the cash sub-path of
            a plain / interest account.  None-handling differs by kind (see
            the note above): the PLAIN path (``balances_for``) auto-builds a
            live map from None, but the INTEREST path
            (``calculate_balances_with_interest``) does NOT and uses the
            stored amounts -- so pass an explicit live map to put an interest
            account on live income (``grid_balance_view`` does this).

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
    _require_scenario(scenario)
    inputs = _assemble_inputs([account], scenario)
    return _account_balance_map(
        account, scenario, periods, inputs, amount_overrides,
    )


def build_maps(
    accounts: list[Account],
    scenario: Scenario,
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

    The net-worth batch path never applies live amount overrides, so each
    per-account dispatch passes ``amount_overrides=None``.

    Accounts whose map is ``None`` (no anchor period) are omitted from the
    result, matching the net-worth section's ``balances is None`` skip.

    Args:
        accounts: The accounts to project (the same user's active set).
        scenario: The baseline scenario.
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
    _require_scenario(scenario)
    inputs = _assemble_inputs(accounts, scenario)
    result: "dict[int, OrderedDict[int, Decimal]]" = {}
    for account in accounts:
        balances = _account_balance_map(
            account, scenario, periods, inputs, None,
        )
        if balances is None:
            continue
        result[account.id] = balances
    return result


def balance_at(
    account: Account, scenario: Scenario, as_of: date,
) -> Decimal:
    """Return one account's balance as of a calendar date *as_of*.

    The scalar-at-a-date producer, dispatched by
    :func:`~app.services.account_projection.classify_account`:

    * **PLAIN (checking / plain savings)** -> the date-precise
      :func:`~app.services.balance_resolver.balance_as_of_date`, which owns
      its own period loading and intra-period entry-date precision.  PLAIN is
      the only kind whose KIND-CORRECT balance IS its transaction balance, so
      the scalar can answer it date-precisely.
    * **AMORTIZING (loan)** -> :func:`~app.services.net_worth_kernel.amortizing_balance_at`:
      the genesis LEDGER for a date at or before today (the only complete record
      of the past -- it books the true-ups that never appear as schedule rows),
      and the forward schedule projection after.  This is also the accessor a
      consumer wanting a loan's PAST balance must use; the seam's forward-only
      liability view (:func:`~app.services.balance_at.liability_owed_at_dates`)
      deliberately refuses a past date.
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
    when *as_of*
    falls before the user's entire pay-period horizon (no period contains or
    precedes it) or the account has no projectable map, the seam returns the
    canonical anchor balance from
    :func:`~app.services.balance_resolver.resolve_anchor`, rounded to cents.
    This mirrors :func:`~app.services.balance_resolver.balance_as_of_date`'s
    pre-anchor convention (a date the projection cannot reach returns the
    anchor balance), so every kind answers an unreachable date the same way.
    A genuinely corrupt account with no anchor history makes
    ``resolve_anchor`` raise, which is the correct loud failure rather than
    a silently wrong number.

    Args:
        account: The account to value.
        scenario: The baseline scenario (its id scopes the resolver / loan
            schedule).
        as_of: The calendar date to value the account at.

    Returns:
        The ``Decimal`` balance at *as_of*.

    Raises:
        ValueError: When ``scenario`` is None -- callers that resolve a
            nullable baseline must guard first.
    """
    _require_scenario(scenario)
    kind = classify_account(account)

    # PLAIN is the only kind whose kind-correct balance IS its transaction
    # balance, so it alone takes the date-precise cash producer.  INTEREST is
    # NOT here: its kind-correct balance accrues interest, so it falls through
    # to the period-granular ``balance_map`` path below -- keeping the scalar
    # consistent with the map for an HYSA (the no-interest transaction balance
    # is ``cash_balance_at``'s job, not this kind-correct scalar's).
    if kind is AccountProjectionKind.PLAIN:
        return balance_resolver.balance_as_of_date(account, scenario.id, as_of)

    if kind is AccountProjectionKind.AMORTIZING:
        # Ledger for the past, forward projection after -- the kernel producer
        # that keeps this scalar on the same source as the loan card and the
        # ``2 years`` band's begun periods.
        return net_worth_kernel.amortizing_balance_at(account, scenario, as_of)

    # INTEREST / INVESTMENT / APPRECIATING: locate the period containing as_of
    # and read the period-keyed map's value there.  INTEREST routes here (not
    # the cash branch above) so the scalar accrues interest in step with
    # balance_map for an HYSA.
    periods = pay_period_service.get_all_periods(account.user_id)
    balances = balance_map(account, scenario, periods)
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
        balance_resolver.resolve_anchor(account, scenario.id).balance,
    )


def investment_seed_map(
    account: Account, scenario: Scenario, periods: list,
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
    :func:`~app.services.net_worth_investment.investment_base_balance_map`) so
    that EVERY balance map -- the modeled one a screen DISPLAYS and the
    pre-growth one a chart SEEDS from -- flows through this one package, and the
    raw kernel producer stays fenced behind the W9906 seam checker.  A consumer
    that needs the seed reads it HERE, never the kernel function directly; the
    distinct name (``investment_seed_map`` vs ``balance_map``) is the signal
    that its value is a projection seed, not a balance to render.

    Args:
        account: The investment account.
        scenario: The baseline scenario (its id scopes the resolver).
        periods: The pay periods to span (ordered by ``period_index``; must
            include the anchor so the resolver has its running seed).

    Returns:
        The ``OrderedDict`` period_id -> Decimal cash-basis (pre-growth)
        balance.

    Raises:
        ValueError: When ``scenario`` is None -- callers that resolve a
            nullable baseline must guard first.
    """
    _require_scenario(scenario)
    return net_worth_investment.investment_base_balance_map(
        account, scenario, periods,
    )


def investment_growth_since_anchor(
    account: Account, scenario: Scenario, periods: list, current_period,
) -> "tuple[Decimal, Decimal] | None":
    """Return ``(growth, contributed)`` since the anchor, or ``None`` (hidden).

    The fenced seam entry for the investment detail page's growth chip:
    assembles this account's inputs via the same
    :func:`._inputs._assemble_inputs` the balance maps use -- so the
    decomposition reconciles with :func:`balance_map` to the cent -- and
    delegates to
    :func:`~app.services.net_worth_investment.investment_growth_since_anchor`
    (its docstring owns the reconciliation contract).  ``None`` when the
    account has no investment params / anchor / post-anchor window; raises
    ``ValueError`` when ``scenario`` is None.
    """
    _require_scenario(scenario)
    inputs = _assemble_inputs([account], scenario)
    params = inputs.investment_params_map.get(account.id)
    if params is None:
        return None
    return net_worth_investment.investment_growth_since_anchor(
        account, params, scenario, periods,
        inputs.deductions_by_account.get(account.id, []),
        inputs.salary_gross_biweekly, current_period,
    )
