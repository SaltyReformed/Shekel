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

from decimal import Decimal

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.services import net_worth_kernel
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


def compute_net_worth_series(
    account_maps: list[dict],
    forward_periods: list[PayPeriod],
) -> dict:
    """Build the forward net-worth trend over the forward window.

    Reads each forward period's id out of the pre-built dense maps
    (built over ALL periods by :func:`build_account_net_worth_maps`) and
    produces parallel ``net`` / ``assets`` / ``liabilities`` series plus
    the period descriptors the route serializes.  ``net[i]`` equals
    ``assets[i] - liabilities[i]`` for every ``i`` by construction (the
    asset-plus / liability-minus split shares one sum with the kernel's
    net-worth reduction).

    Takes the pre-built ``account_maps`` rather than the raw accounts so
    the maps are built exactly once and shared with
    :func:`compute_net_worth_change` (the locked build-once invariant);
    the orchestrator builds them via
    :func:`build_account_net_worth_maps` and threads them into both.

    Args:
        account_maps: The dense ``{balances, is_liability}`` maps from
            :func:`build_account_net_worth_maps`.
        forward_periods: The forward window (current period onward),
            chronological; each must appear in the dense maps' domain.

    Returns:
        dict with ``periods`` (list of ``{end_date, period_index}``),
        ``net``, ``assets``, and ``liabilities`` (parallel ``Decimal``
        lists, one entry per forward period).
    """
    periods: list[dict] = []
    net: list[Decimal] = []
    assets: list[Decimal] = []
    liabilities: list[Decimal] = []

    for period in forward_periods:
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
) -> Decimal | None:
    """Compute the net-worth change from the prior period to the current.

    Returns ``NW(current_period) - NW(prior_period)`` where the prior
    period is the one whose ``period_index`` is exactly
    ``current_period.period_index - 1``.  Returns ``None`` when there is
    no current period or no such immediately-prior period (e.g. the user
    is in their earliest period, ``period_index == 0``) -- a missing
    prior period is structurally different from a zero change, so the
    caller must not coerce ``None`` to zero.

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

    Returns:
        The change as a ``Decimal``, or ``None`` when there is no
        immediately-prior period to compare against.
    """
    if current_period is None:
        return None

    prior_index = current_period.period_index - 1
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
