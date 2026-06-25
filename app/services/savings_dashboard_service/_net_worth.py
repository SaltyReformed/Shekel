"""
Shekel Budget App -- Savings Cockpit: net-worth producer.

The server-side data producer for the Accounts / Net-Worth cockpit's
net-worth region (Loop B Phase 1): the today figures (net worth, total
assets, total liabilities, liquid), the forward net-worth trend series,
and the change-this-period delta.  No Flask imports; every function takes
plain data (the projected account dicts, ORM rows, the loaded parameter
maps) and returns plain ``Decimal`` / ``dict`` data the route serializes.

The per-account balance projection -- including the investment / 401k
growth sub-chain that the forward trend projects forward -- comes from
the shared :mod:`app.services.net_worth_kernel`, the same math the
year-end net-worth section consumes, so the cockpit trend and the
year-end trend cannot drift onto two copies of it.

The dense per-account balance maps are built ONCE (over ALL periods, so
``balance_resolver`` always has its anchor seed) and shared by both the
series and the change delta, via :func:`build_account_net_worth_maps`;
the orchestrator builds them and threads the result into both producers.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.services import home_equity_service, net_worth_kernel
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.savings_dashboard_service._metrics import _sum_liquid_balances
from app.services.savings_dashboard_service._types import _AccountParams

ZERO = Decimal("0.00")


def _is_liability_account(account) -> bool:
    """Return whether an account's type is in the LIABILITY category.

    Classifies by the account type's integer ``category_id`` against the
    cached LIABILITY category id (IDs for logic, never a ``.name``
    string).  An account with no ``account_type`` (degenerate / partially
    loaded) is treated as a non-liability asset, matching the year-end
    net-worth section's ``account_type is not None`` guard.

    Args:
        account: The :class:`~app.models.account.Account` to classify.

    Returns:
        ``True`` when the account's type's category is LIABILITY,
        ``False`` otherwise.
    """
    liability_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
    return (
        account.account_type is not None
        and account.account_type.category_id == liability_cat_id
    )


def compute_net_worth_today(account_data: list[dict]) -> dict:
    """Compute the today net-worth figures from the projected account data.

    Reduces over each account's ``current_balance`` -- the entries-aware
    resolver figure already in ``account_data`` (E-25), NOT the raw
    ``current_anchor_balance`` cache -- so this hero agrees with the
    per-tile balances the same page renders.  Assets add their balance;
    liabilities (classified by :func:`_is_liability_account`) accumulate
    their POSITIVE magnitude into ``total_liabilities``.  Net worth is
    ``total_assets - total_liabilities``.

    Args:
        account_data: Per-account dicts from
            ``_compute_account_projections`` (each carrying ``account``
            and ``current_balance``).

    Returns:
        dict with ``net_worth``, ``total_assets``, ``total_liabilities``
        (a positive magnitude), and ``liquid`` -- all ``Decimal``.
    """
    total_assets = ZERO
    total_liabilities = ZERO
    for ad in account_data:
        balance = ad["current_balance"] or ZERO
        if _is_liability_account(ad["account"]):
            total_liabilities += abs(balance)
        else:
            total_assets += balance

    return {
        "net_worth": total_assets - total_liabilities,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "liquid": _sum_liquid_balances(account_data),
    }


def build_account_net_worth_maps(
    accounts: list,
    scenario: Scenario | None,
    all_periods: list[PayPeriod],
    params: _AccountParams,
    debt_schedules: dict[int, list],
) -> list[dict]:
    """Build each account's dense balance map plus its liability flag.

    The single, build-once point for the per-account dense maps the
    forward trend and the change delta both read.  Each map is built via
    :func:`app.services.net_worth_kernel.build_account_balance_map` over
    ALL periods (never a forward sub-window): the entries-aware resolver
    behind the plain / investment paths must include the anchor period to
    seed its running balance (``balance_resolver.balances_for`` -- "Must
    include the anchor period"), so a forward-only period list would
    starve a pre-anchor or current-period account of its seed.  The
    forward consumers read the periods they want back out of the dense
    map by id.

    Returns the same ``{balances, is_liability}`` shape
    :func:`app.services.net_worth_kernel.sum_net_worth_at_period`
    consumes.  Accounts with no anchor period (no dense map) are omitted,
    matching the year-end section's ``balances is None`` skip.

    Args:
        accounts: The user's active accounts.
        scenario: The baseline scenario, or ``None``.  With no scenario
            the kernel's resolver path cannot run, so an empty list is
            returned (the degraded no-scenario state).
        all_periods: All of the user's pay periods (the dense domain).
        params: The batch-loaded :class:`_AccountParams` (the investment
            params map, the per-account deductions, and the engine
            gross-biweekly the growth sub-chain needs).
        debt_schedules: account_id -> amortization schedule, from
            :func:`app.services.net_worth_kernel.generate_debt_schedules`.

    Returns:
        A list of ``{balances: OrderedDict[int, Decimal], is_liability:
        bool}`` dicts, one per account that has a dense map.
    """
    if scenario is None:
        return []

    result: list[dict] = []
    for account in accounts:
        balances = net_worth_kernel.build_account_balance_map(
            account, scenario, all_periods,
            debt_schedule=debt_schedules.get(account.id),
            investment_params=params.investment_params_map.get(account.id),
            deductions=params.deductions_by_account.get(account.id, []),
            salary_gross_biweekly=params.salary_gross_biweekly,
        )
        if balances is None:
            continue
        result.append({
            "balances": balances,
            "is_liability": _is_liability_account(account),
        })
    return result


def _sum_assets_and_liabilities_at_period(
    period_id: int, account_maps: list[dict],
) -> tuple[Decimal, Decimal]:
    """Sum asset balances and liability magnitudes at one period.

    The asset total adds each non-liability account's balance; the
    liability total accumulates each liability account's POSITIVE
    magnitude (``abs(bal)``).  Their difference equals
    :func:`app.services.net_worth_kernel.sum_net_worth_at_period` for the
    same period and maps (asset ``+bal`` / liability ``-abs(bal)``), so
    the series' ``assets - liabilities`` reconciles to its ``net`` by
    construction.

    Args:
        period_id: The pay period id to read balances at.
        account_maps: The dense ``{balances, is_liability}`` maps from
            :func:`build_account_net_worth_maps`.

    Returns:
        ``(assets, liabilities)`` -- the asset sum and the liability
        magnitude sum at the period, both ``Decimal``.
    """
    assets = ZERO
    liabilities = ZERO
    for data in account_maps:
        bal = data["balances"].get(period_id, ZERO)
        if data["is_liability"]:
            liabilities += abs(bal)
        else:
            assets += bal
    return assets, liabilities


# Recent-history cap for the net-worth trend's leading "actual" segment.
# The trend opens with up to this many already-elapsed periods (the solid
# history the forward projection extends from) before the current period.
# ~6 periods is ~3 months at the biweekly cadence; the developer's ruling
# (accounts_audit.md, "forward reach + short tail") fixes a SHORT tail
# rather than a full-history axis.
_TREND_HISTORY_PERIODS = 6

# Account kinds whose dense balance map OMITS pre-anchor periods, so they
# constrain how far back the trend's history can honestly reach.  PLAIN
# (checking / savings) and INTEREST (HYSA / Money Market / CD / HSA) both
# carry the running balance forward from the anchor period only
# (``balance_resolver.balances_for`` /
# ``balance_calculator.calculate_balances_with_interest`` -- "Must include
# the anchor period ... pre-anchor periods ... absent from the result"), so
# a period before such an account's anchor has NO balance for it and it
# would silently contribute zero to the net-worth sum (cash dropping out of
# the past).  AMORTIZING loans constrain the window separately (their
# resolver schedule is today-forward, so pre-schedule periods report the
# original principal -- see :func:`_loan_schedule_start_index`).  INVESTMENT
# (reverse growth projection) and APPRECIATING (flat-carry) are defined at
# every period, so they never constrain it.
_CASH_GATING_KINDS = frozenset({
    AccountProjectionKind.PLAIN,
    AccountProjectionKind.INTEREST,
})


def _loan_schedule_start_index(
    all_periods: list[PayPeriod], schedule: list | None,
) -> int | None:
    """Earliest period_index at which a loan's schedule gives a real balance.

    A loan's resolver schedule is TODAY-forward: it projects from the
    current resolved balance to payoff, with no rows for the loan's past.
    :func:`app.services.account_projection.compute_loan_period_balance_map`
    therefore returns the loan's ORIGINAL PRINCIPAL for every period before
    the schedule's first payment -- a flat line at the origination amount,
    not the real amortized balance the loan actually had then.  So a loan
    is "honest" only from the first period whose ``end_date`` reaches its
    first scheduled payment onward; before that the trend would show the
    loan leaping down from its origination balance at "today".

    Returns that first honest ``period_index``, or ``None`` when the loan
    does not constrain the window: an empty schedule (``[]`` -- a
    resolved-but-unpaid loan that sits at its original principal at EVERY
    period, which IS its real balance) or a missing one (``None``), and the
    degenerate case of a schedule dated entirely after the user's last
    period.

    Args:
        all_periods: All of the user's pay periods, ordered by
            ``period_index``.
        schedule: The loan's amortization schedule (the
            :func:`app.services.net_worth_kernel.generate_debt_schedules`
            entry for it), or ``None``.

    Returns:
        The first honest ``period_index``, or ``None`` when the loan does
        not gate the window.
    """
    if not schedule:
        return None
    first_payment = min(row.payment_date for row in schedule)
    for period in all_periods:
        if period.end_date >= first_payment:
            return period.period_index
    return None


def _honest_history_start_index(
    accounts: list,
    all_periods: list[PayPeriod],
    current_period: PayPeriod,
    debt_schedules: dict[int, list],
) -> int:
    """Earliest period_index whose net worth is real for every account.

    The trend's leading "actual" segment must not show an account's balance
    as a fallback value in the past.  Two kinds carry such fallbacks:

    - CASH (PLAIN / INTEREST, the kinds in :data:`_CASH_GATING_KINDS`): no
      balance before the anchor period -- the account would contribute zero
      (cash dropping out of the past).  Gates at its anchor index.
    - AMORTIZING loans: the resolver schedule is today-forward, so periods
      before it report the loan's ORIGINAL PRINCIPAL, not its real past
      balance (the loan would leap down at "today").  Gates at its
      schedule-start index (:func:`_loan_schedule_start_index`).

    INVESTMENT (reverse-projected) and APPRECIATING (flat-carried) accounts
    are defined at every period by the same modeling the year-end summary
    uses, so they do not constrain the window.

    Returns the maximum gating index -- the earliest period at or after
    which every cash account has a real balance AND every loan is within
    its schedule -- clamped to not exceed ``current_period``'s index.
    Returns ``current_period``'s index (no history) when nothing gates
    earlier, so the trend never fabricates a backward run for an
    investment-or-property-only set (those are projected, not "actual").

    Args:
        accounts: The user's active accounts (each with ``account_type``
            eager-loaded for :func:`classify_account`, an ``id``, and a
            ``current_anchor_period_id``).
        all_periods: All of the user's pay periods (maps an anchor period
            id to its index).
        current_period: The period containing today (the upper clamp).
        debt_schedules: account_id -> amortization schedule (from
            :func:`app.services.net_worth_kernel.generate_debt_schedules`),
            for the loan gate.

    Returns:
        The earliest honest history ``period_index`` (``0`` ..
        ``current_period.period_index``).
    """
    index_by_pid = {p.id: p.period_index for p in all_periods}
    gating_indices: list[int] = []
    for account in accounts:
        kind = classify_account(account)
        if kind in _CASH_GATING_KINDS:
            if account.current_anchor_period_id in index_by_pid:
                gating_indices.append(
                    index_by_pid[account.current_anchor_period_id]
                )
        elif kind is AccountProjectionKind.AMORTIZING:
            loan_start = _loan_schedule_start_index(
                all_periods, debt_schedules.get(account.id),
            )
            if loan_start is not None:
                gating_indices.append(loan_start)
    if not gating_indices:
        return current_period.period_index
    return min(max(gating_indices), current_period.period_index)


def build_trend_periods(
    accounts: list,
    all_periods: list[PayPeriod],
    current_period: PayPeriod | None,
    debt_schedules: dict[int, list],
) -> tuple[list[PayPeriod], int, int]:
    """Build the net-worth trend's window, current index, and honest start.

    The window leads with a short honest "actual" history tail, then the
    full forward projection::

        [ history tail ]  current period  [ ... forward ... ]
          solid actual        today          dashed projection

    The tail spans the up-to-:data:`_TREND_HISTORY_PERIODS` periods
    immediately before the current period, but never earlier than
    :func:`_honest_history_start_index` -- so at every history point every
    cash account has a real balance (none drops silently to zero) AND every
    loan is within its schedule (none sits at its original principal then
    leaps down at "today").  Because a loan's schedule is today-forward, any
    user with an amortizing loan has an empty tail (forward-only); the tail
    shows only for loan-free users whose cash was trued up in the past (e.g.
    renters), the case the honest tail genuinely serves.

    ALL forward periods are included (not a fixed forward slice): the client
    selects the 6 / 13 / 26 / All forward horizon from the full series, so
    the producer serializes once and the picker never re-fetches.

    The honest-start index is returned alongside the window so the
    change-this-period delta can reuse it (a period-over-period change is
    only honest when the prior period is within the honest window), keeping
    the trend and the chip on one boundary.

    Args:
        accounts: The user's active accounts.
        all_periods: All of the user's pay periods, ordered by
            ``period_index``.
        current_period: The period containing today, or ``None``.
        debt_schedules: account_id -> amortization schedule, for the loan
            gate.

    Returns:
        ``(periods, current_index, honest_start)`` -- ``periods`` is the
        trend window (history tail + current + forward, chronological),
        ``current_index`` is the position of the current period within it
        (the count of leading history points; the solid/dashed split and
        the "Today" marker key off it), and ``honest_start`` is the earliest
        honest ``period_index`` (the change delta's gate).  ``([], 0, 0)``
        when there is no current period (the degraded no-period state).
    """
    if current_period is None:
        return [], 0, 0

    current_idx = current_period.period_index
    honest_start = _honest_history_start_index(
        accounts, all_periods, current_period, debt_schedules,
    )
    history_start = max(honest_start, current_idx - _TREND_HISTORY_PERIODS)
    periods = [p for p in all_periods if p.period_index >= history_start]
    current_index = sum(1 for p in periods if p.period_index < current_idx)
    return periods, current_index, honest_start


def compute_net_worth_series(
    account_maps: list[dict],
    trend_periods: list[PayPeriod],
) -> dict:
    """Build the net-worth trend over the trend window.

    Reads each trend period's id out of the pre-built dense maps (built
    over ALL periods by :func:`build_account_net_worth_maps`) and produces
    parallel ``net`` / ``assets`` / ``liabilities`` series plus the period
    descriptors the route serializes.  ``net[i]`` equals ``assets[i] -
    liabilities[i]`` for every ``i`` by construction (the asset-plus /
    liability-minus split shares one sum with the kernel's net-worth
    reduction).

    The ``trend_periods`` window (from :func:`build_trend_periods`) is the
    honest history tail followed by the full forward projection; this
    producer is window-agnostic -- it sums whatever periods it is given, so
    widening the window from forward-only to history-plus-forward needed no
    change here.

    Takes the pre-built ``account_maps`` rather than the raw accounts so
    the maps are built exactly once and shared with
    :func:`compute_net_worth_change` (the locked build-once invariant);
    the orchestrator builds them via
    :func:`build_account_net_worth_maps` and threads them into both.

    Args:
        account_maps: The dense ``{balances, is_liability}`` maps from
            :func:`build_account_net_worth_maps`.
        trend_periods: The trend window (history tail + current + forward),
            chronological; each must appear in the dense maps' domain.

    Returns:
        dict with ``periods`` (list of ``{end_date, period_index}``),
        ``net``, ``assets``, and ``liabilities`` (parallel ``Decimal``
        lists, one entry per trend period).  The orchestrator adds
        ``current_index`` (the solid/dashed boundary) to this dict.
    """
    periods: list[dict] = []
    net: list[Decimal] = []
    assets: list[Decimal] = []
    liabilities: list[Decimal] = []

    for period in trend_periods:
        period_assets, period_liabilities = _sum_assets_and_liabilities_at_period(
            period.id, account_maps,
        )
        periods.append({
            "end_date": period.end_date,
            "period_index": period.period_index,
        })
        net.append(period_assets - period_liabilities)
        assets.append(period_assets)
        liabilities.append(period_liabilities)

    return {
        "periods": periods,
        "net": net,
        "assets": assets,
        "liabilities": liabilities,
    }


def compute_net_worth_change(
    account_maps: list[dict],
    current_period: PayPeriod | None,
    all_periods: list[PayPeriod],
    honest_start: int,
) -> Decimal | None:
    """Compute the net-worth change from the prior period to the current.

    Returns ``NW(current_period) - NW(prior_period)`` where the prior
    period is the one whose ``period_index`` is exactly
    ``current_period.period_index - 1``.

    Returns ``None`` -- a missing comparison the caller must not coerce to
    zero -- when the change cannot be computed HONESTLY:

    - no current period, or no immediately-prior period (the user is in
      their earliest period, ``period_index == 0``); or
    - the prior period is before ``honest_start`` (from
      :func:`build_trend_periods`): its net worth would read a cash account
      as absent or a loan at its original principal (the same fallbacks the
      trend's history gate excludes), so the delta would be a fabricated
      jump -- e.g. a loan's whole origination-to-now paydown counted as one
      period.  Because a loan's schedule is today-forward, the prior period
      is pre-schedule for any loan-holder, so the chip honestly reads "--"
      for them rather than a wrong figure.

    Both net-worth values come from the SAME dense maps the series reads
    (built once by :func:`build_account_net_worth_maps`), through the
    kernel's :func:`~app.services.net_worth_kernel.sum_net_worth_at_period`,
    so the change can never disagree with the series' endpoints.

    Args:
        account_maps: The dense ``{balances, is_liability}`` maps from
            :func:`build_account_net_worth_maps`.
        current_period: The user's current :class:`PayPeriod`, or
            ``None``.
        all_periods: All of the user's pay periods (to locate the prior).
        honest_start: The earliest honest ``period_index`` (from
            :func:`build_trend_periods`); the prior period must be at or
            after it for the change to be real.

    Returns:
        The change as a ``Decimal``, or ``None`` when there is no honest
        immediately-prior period to compare against.
    """
    if current_period is None:
        return None

    prior_index = current_period.period_index - 1
    if prior_index < honest_start:
        return None
    prior_period = next(
        (p for p in all_periods if p.period_index == prior_index), None,
    )
    if prior_period is None:
        return None

    current_nw = net_worth_kernel.sum_net_worth_at_period(
        current_period.id, account_maps,
    )
    prior_nw = net_worth_kernel.sum_net_worth_at_period(
        prior_period.id, account_maps,
    )
    return current_nw - prior_nw


def compute_property_equity(
    accounts: list,
    scenario_id: int | None,
    as_of: date,
) -> list[dict]:
    """Resolve each Property account's equity for the cockpit equity card.

    Reuses the same producer the Property detail page uses
    (:func:`app.services.home_equity_service.resolve_home_equity`), so the
    home-equity and loan-to-value figures here equal that page's and the
    mortgage leg equals the resolver-derived balance the debt card and the
    net-worth liability column read -- one figure, never a fork.  Equity
    itself stays emergent (the net-worth sum is untouched); this only
    surfaces the home<->mortgage relationship as a glanceable card.

    An account is a Property when the canonical flag-driven classifier
    (:func:`app.services.account_projection.classify_account`) returns
    :data:`~app.services.account_projection.AccountProjectionKind.APPRECIATING`,
    never a raw ``has_appreciation`` re-check -- the single taxonomy the
    mini-sprint consolidated the inline predicates onto.  An unencumbered
    Property (no secured loans) is included too: its card reports the full
    market value as equity at 0% LTV.

    Args:
        accounts: The user's active accounts.
        scenario_id: The baseline scenario id for the loan resolver, or
            ``None`` when the user has no scenario yet (each secured loan
            then resolves from its anchor with no payment history, exactly
            as the detail page does).
        as_of: The as-of date for the loan resolver.

    Returns:
        A list of ``{account, equity}`` dicts, one per Property account in
        ``accounts`` order, where ``equity`` is a
        :class:`~app.services.home_equity_service.HomeEquity` snapshot.
        Empty when the user has no Property accounts.
    """
    result: list[dict] = []
    for account in accounts:
        if classify_account(account) is AccountProjectionKind.APPRECIATING:
            result.append({
                "account": account,
                "equity": home_equity_service.resolve_home_equity(
                    account, scenario_id, as_of,
                ),
            })
    return result
