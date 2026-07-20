"""
Shekel Budget App -- Savings Cockpit: net-worth producer.

The server-side data producer for the Accounts / Net-Worth cockpit's
net-worth region (Loop B Phase 1): the today figures (net worth, total
assets, total liabilities, liquid) and the forward net-worth trend
series.  No Flask imports; every function takes
plain data (the projected account dicts, ORM rows, the loaded parameter
maps) and returns plain ``Decimal`` / ``dict`` data the route serializes.

Every per-account balance the NET-WORTH reduction reads arrives through the
:mod:`app.services.balance_at` seam, so that path computes no balance of its
own: the seam dispatches non-loan kinds to
:mod:`app.services.net_worth_kernel` (including the investment / 401k growth
sub-chain the forward trend projects forward) and answers an AMORTIZING loan
from its own ``positions()`` fold (plan step C3b3).  What this module owns is
the REDUCTION over those balances -- asset-plus / liability-minus -- not the
balances themselves.  (:func:`compute_property_equity` is the exception that
proves the boundary: it is an EQUITY figure, not a net-worth balance, and it
delegates to :mod:`app.services.home_equity_service`.)

The dense per-account balance maps are built ONCE (over ALL periods, so
``balance_resolver`` always has its anchor seed) and shared by both the
series and the change delta, via :func:`build_account_net_worth_maps`;
the orchestrator builds them and threads the result into both producers.
"""

from decimal import Decimal

from app.models.pay_period import PayPeriod
from app.services import balance_at, home_equity_service
from app.services.amortization_engine import AmortizationRow
from app.services.resolution_context import BalanceContext
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
# The asset/liability rule and the net-worth account-data builder live in
# the shared adapter so every net-worth surface -- these today figures, the
# trend below, and the Horizon band in ``_horizon`` -- classifies an account
# one way; ``_is_liability_account`` keeps its local name (this module's
# other net-worth helpers call it) as an alias over the one definition.
from app.services.net_worth_account_data import (
    is_liability_account as _is_liability_account,
    to_net_worth_account_data,
)
from app.services.savings_dashboard_service._metrics import _sum_liquid_balances

ZERO = Decimal("0.00")

# The net-worth composition bands, keyed by the cockpit category
# (:func:`~app.services.savings_dashboard_service._display.account_category_key`).
# The asset-side bands sum to the asset total, the liability band is the
# liability total, and net worth is their difference -- so the composition
# split reconciles to the ``assets`` / ``liabilities`` / ``net`` totals by
# construction (P-AC1 Loop B P1).
_ASSET_BANDS = ("asset", "retirement", "investment", "other")
_LIABILITY_BAND = "liability"
_COMPOSITION_BANDS = _ASSET_BANDS + (_LIABILITY_BAND,)


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
    ctx: BalanceContext,
    all_periods: list[PayPeriod],
) -> list[dict]:
    """Build each account's dense balance map plus its liability flag.

    The net-worth shape adapter over the balance-at seam.  It asks
    :func:`app.services.balance_at.build_maps` for every account's dense
    period balance map and pairs each with the account's liability flag,
    producing the single build-once structure the forward trend and the
    per-account sparklines both read.  The seam owns the input assembly
    (the debt schedules, investment params, deductions, and engine
    gross-biweekly), so this producer no longer pre-assembles them; the
    seam deliberately returns balances only (liability classification is
    not a balance concern), so this consumer adds ``is_liability`` itself.

    The seam builds each map over ALL periods (never a forward sub-window):
    the entries-aware resolver behind the plain / investment paths must
    include the anchor period to seed its running balance
    (``balance_resolver.balances_for`` -- "Must include the anchor
    period"), so a forward-only period list would starve a pre-anchor or
    current-period account of its seed.  The forward consumers read the
    periods they want back out of the dense map by id.

    Returns the same ``{account_id, balances, is_liability}`` shape
    :func:`compute_net_worth_series` (via
    :func:`_sum_composition_at_period`) and
    :func:`compute_sparklines` consume.  Accounts with no anchor period (no
    dense map) are omitted by the seam and skipped here.

    Args:
        accounts: The user's active accounts.
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`.
            With no baseline scenario on it the seam's resolver path cannot run,
            so an empty list is returned (the degraded no-scenario state)
            WITHOUT calling the seam -- the seam raises on a ``None`` scenario by
            contract, and this caller owns the legitimate empty state.
        all_periods: All of the user's pay periods (the dense domain).

    Returns:
        A list of ``{account_id: int, balances: OrderedDict[int, Decimal],
        is_liability: bool}`` dicts, one per account that has a dense map,
        in ``accounts`` order.  The ``account_id`` lets the per-account
        sparkline producer (:func:`compute_sparklines`) reuse these maps, so
        the sparklines and the net-worth math read one projection, and
        :func:`_sum_composition_at_period` keys each account's composition
        band off it.
    """
    if ctx.scenario is None:
        return []

    balance_maps = balance_at.build_maps(accounts, ctx, all_periods)
    return to_net_worth_account_data(accounts, balance_maps)


def _sum_composition_at_period(
    period_id: int,
    account_maps: list[dict],
    category_by_account_id: dict[int, str],
) -> dict[str, Decimal]:
    """Sum each category band's balance at one period.

    The per-band generalization of the old asset/liability split: each
    non-liability account adds its balance to its category band (asset /
    retirement / investment / other, keyed by *category_by_account_id*),
    and each liability account accumulates its POSITIVE magnitude
    (``abs(bal)``) into the liability band.  The liability flag -- not the
    category key -- decides the sign, so a liability always lands in the
    liability band with a positive magnitude even if its category map entry
    were missing.

    This is the ONE per-period net-worth reduction.  Summing the asset-side
    bands and subtracting the liability band is exactly asset ``+bal`` /
    liability ``-abs(bal)``, and :func:`compute_net_worth_series` derives its
    ``assets`` / ``liabilities`` / ``net`` from these bands rather than
    re-reducing the maps -- so the composition split reconciles to the series
    by construction, not by two producers agreeing.

    Args:
        period_id: The pay period id to read balances at.
        account_maps: The dense ``{account_id, balances, is_liability}``
            maps from :func:`build_account_net_worth_maps`.
        category_by_account_id: Each account's cockpit category key, from
            :func:`~app.services.savings_dashboard_service._display.category_key_by_account_id`
            (built from the SAME classifier the grid grouping uses).  An
            account absent from the map falls to ``"other"`` (defensive; the
            producer passes a total map).

    Returns:
        ``{band: Decimal}`` for every band in :data:`_COMPOSITION_BANDS`.
    """
    sums = {band: ZERO for band in _COMPOSITION_BANDS}
    for data in account_maps:
        bal = data["balances"].get(period_id, ZERO)
        if data["is_liability"]:
            sums[_LIABILITY_BAND] += abs(bal)
        else:
            band = category_by_account_id.get(data["account_id"], "other")
            sums[band] += bal
    return sums


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
# current balance held flat -- today's balance, not the loan's real past --
# see :func:`_loan_schedule_start_index`).  INVESTMENT
# (reverse growth projection) and APPRECIATING (flat-carry) are defined at
# every period, so they never constrain it.
_CASH_GATING_KINDS = frozenset({
    AccountProjectionKind.PLAIN,
    AccountProjectionKind.INTEREST,
})


def _loan_schedule_start_index(
    all_periods: list[PayPeriod],
    schedule_rows: list[AmortizationRow] | None,
) -> int | None:
    """Earliest period_index at which a loan's schedule gives a real balance.

    A loan's schedule has no rows before its first recorded payment, so a
    schedule-derived map returns the loan's CURRENT balance, held flat, for every
    period before that first payment -- today's balance, not the real amortized
    balance the loan actually had then.  (The ledger now owns every BEGUN period,
    so that hazard is gone from the balance map itself; this gate still bounds
    what the TREND is willing to draw.)  So a loan is "honest" only
    from the first period whose ``end_date`` reaches its first schedule row
    onward; before that the trend would carry today's balance flat backward
    through the loan's real past.  For a GENESIS loan the confirmed rows are
    ledger-derived from the loan's FIRST recorded payment (the history read
    switch), so the honest window extends back over the real recorded
    history -- which the C9 splice fills with ledger-real balances -- where a
    replay-fallback loan's rows start at its latest anchor.

    Returns that first honest ``period_index``, or ``None`` when the loan
    does not constrain the window: an empty schedule (a paid-off or
    fully-resolved loan, whose flat current balance IS its real balance) or a
    missing one (``None``), and the degenerate case of a schedule dated
    entirely after the user's last period.

    Args:
        all_periods: All of the user's pay periods, ordered by
            ``period_index``.
        schedule_rows: The loan's amortization rows
            (:func:`app.services.net_worth_kernel.debt_schedule_rows`), or ``None``
            when the context could not resolve the loan.

    Returns:
        The first honest ``period_index``, or ``None`` when the loan does
        not gate the window.
    """
    if not schedule_rows:
        return None
    first_payment = min(row.payment_date for row in schedule_rows)
    for period in all_periods:
        if period.end_date >= first_payment:
            return period.period_index
    return None


def _honest_history_start_index(
    accounts: list,
    all_periods: list[PayPeriod],
    current_period: PayPeriod,
    debt_schedules: dict[int, list[AmortizationRow]],
) -> int:
    """Earliest period_index whose net worth is real for every account.

    The trend's leading "actual" segment must not show an account's balance
    as a fallback value in the past.  Two kinds carry such fallbacks:

    - CASH (PLAIN / INTEREST, the kinds in :data:`_CASH_GATING_KINDS`): no
      balance before the anchor period -- the account would contribute zero
      (cash dropping out of the past).  Gates at its anchor index.
    - AMORTIZING loans: the resolver schedule is today-forward, so periods
      before it report the loan's CURRENT balance held flat -- today's
      balance, not its real past balance.  Gates at its schedule-start index
      (:func:`_loan_schedule_start_index`).

    INVESTMENT (reverse-projected) and APPRECIATING (flat-carried) accounts
    are defined at every period by the seam's own modeling, so they do not
    constrain the window.

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
        debt_schedules: account_id -> the loan's amortization ROW list
            (from :func:`app.services.net_worth_kernel.debt_schedule_rows`), for the
            loan gate.  Not a ``DebtSchedule`` bundle: the rows carry no
            ``projection_seed``, which is the balance the fence keeps out of an
            out-of-cluster consumer's hands.  (A row does carry a
            ``remaining_balance``; this module reads only ``payment_date``.)

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
    debt_schedules: dict[int, list[AmortizationRow]],
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
    loan is within its schedule (none shows today's balance carried flat
    backward through its real past).  Because a loan's schedule is
    today-forward, any
    user with an amortizing loan has an empty tail (forward-only); the tail
    shows only for loan-free users whose cash was trued up in the past (e.g.
    renters), the case the honest tail genuinely serves.

    ALL forward periods are included (not a fixed forward slice): the client
    selects the 6 / 13 / 26 / All forward horizon from the full series, so
    the producer serializes once and the picker never re-fetches.

    The honest-start index (the earliest period whose net worth is real for
    every account) is returned alongside the window; it is the boundary the
    history tail is clamped back to, exposed so a caller can reason about
    where the solid history legitimately begins.

    Args:
        accounts: The user's active accounts.
        all_periods: All of the user's pay periods, ordered by
            ``period_index``.
        current_period: The period containing today, or ``None``.
        debt_schedules: account_id -> the loan's amortization ROW list
            (:func:`app.services.net_worth_kernel.debt_schedule_rows`), for the loan
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
    category_by_account_id: dict[int, str],
) -> dict:
    """Build the net-worth trend over the trend window.

    Reads each trend period's id out of the pre-built dense maps (built
    over ALL periods by :func:`build_account_net_worth_maps`) and produces
    parallel ``net`` / ``assets`` / ``liabilities`` series, the per-category
    ``composition`` split (P-AC1 Loop B P1), and the period descriptors the
    route serializes.  ``net[i]`` equals ``assets[i] - liabilities[i]`` for
    every ``i`` by construction, and ``assets[i]`` equals the sum of the
    asset-side ``composition`` bands while ``liabilities[i]`` equals the
    ``composition["liability"]`` band -- the split and the totals share one
    per-period sum (:func:`_sum_composition_at_period`), so they cannot
    drift.

    The ``trend_periods`` window (from :func:`build_trend_periods`) is the
    honest history tail followed by the full forward projection; this
    producer is window-agnostic -- it sums whatever periods it is given, so
    widening the window from forward-only to history-plus-forward needed no
    change here.

    Takes the pre-built ``account_maps`` rather than the raw accounts so
    the maps are built exactly once and shared with the per-account
    sparklines (the locked build-once invariant); the orchestrator builds
    them via :func:`build_account_net_worth_maps` and threads them into
    both.

    Args:
        account_maps: The dense ``{account_id, balances, is_liability}``
            maps from :func:`build_account_net_worth_maps`.
        trend_periods: The trend window (history tail + current + forward),
            chronological; each must appear in the dense maps' domain.
        category_by_account_id: Each account's cockpit category key, from
            :func:`~app.services.savings_dashboard_service._display.category_key_by_account_id`,
            driving the per-category ``composition`` split.

    Returns:
        dict with ``periods`` (list of ``{end_date, period_index}``),
        ``net``, ``assets``, ``liabilities`` (parallel ``Decimal`` lists,
        one entry per trend period), and ``composition`` (a
        ``{band: [Decimal, ...]}`` map over :data:`_COMPOSITION_BANDS`, each
        band's series parallel to ``periods``).  The orchestrator adds
        ``current_index`` (the solid/dashed boundary) to this dict.
    """
    periods: list[dict] = []
    net: list[Decimal] = []
    assets: list[Decimal] = []
    liabilities: list[Decimal] = []
    composition: dict[str, list[Decimal]] = {
        band: [] for band in _COMPOSITION_BANDS
    }

    for period in trend_periods:
        sums = _sum_composition_at_period(
            period.id, account_maps, category_by_account_id,
        )
        period_assets = sum((sums[band] for band in _ASSET_BANDS), ZERO)
        period_liabilities = sums[_LIABILITY_BAND]
        periods.append({
            "end_date": period.end_date,
            "period_index": period.period_index,
        })
        net.append(period_assets - period_liabilities)
        assets.append(period_assets)
        liabilities.append(period_liabilities)
        for band in _COMPOSITION_BANDS:
            composition[band].append(sums[band])

    return {
        "periods": periods,
        "net": net,
        "assets": assets,
        "liabilities": liabilities,
        "composition": composition,
    }


def compute_property_equity(
    accounts: list,
    ctx: BalanceContext,
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
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`.
            Each secured loan is read from its memo, so the mortgage leg of this
            card is the SAME resolution the debt card and the net-worth liability
            column read -- one resolution, not a fourth one that has to agree.

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
                    account, ctx,
                ),
            })
    return result


# Per-account sparkline window + the "informative" thresholds (rebuild
# decision: a sparkline only where it reads as a trend, else the figure +
# its projected line).
_SPARKLINE_PERIODS = 13                       # forward points (~6 months)
_SPARKLINE_MIN_POINTS = 4                      # fewer can't read as a trend
_SPARKLINE_REL_THRESHOLD = Decimal("0.005")   # 0.5% of the account's magnitude
_SPARKLINE_ABS_FLOOR = Decimal("1.00")        # never informative under $1 spread


def _is_informative(series: list[Decimal]) -> bool:
    """Return whether a sparkline series reads as a trend worth drawing.

    Informative means at least :data:`_SPARKLINE_MIN_POINTS` points AND a
    max-min spread above ``max(_SPARKLINE_ABS_FLOOR, _SPARKLINE_REL_THRESHOLD
    * the account's magnitude)``.  So a flat account (checking with no
    projected movement, a flat-carried Property) is omitted -- its card shows
    the figure + projected line rather than a deceptively flat line -- while
    a trending one (a loan amortizing down, an investment growing) is drawn.
    The relative threshold keeps the test scale-free: a $200 wobble is noise
    on a $400k mortgage but a real move on a $2k account.

    Args:
        series: The forward balance series (``Decimal``) for one account.

    Returns:
        ``True`` when the series has enough points and enough variation.
    """
    if len(series) < _SPARKLINE_MIN_POINTS:
        return False
    spread = max(series) - min(series)
    magnitude = max((abs(value) for value in series), default=ZERO)
    threshold = max(_SPARKLINE_ABS_FLOOR, _SPARKLINE_REL_THRESHOLD * magnitude)
    return spread > threshold


def compute_sparklines(
    account_maps: list[dict], forward_periods: list[PayPeriod],
) -> dict[int, list[Decimal]]:
    """Build each informative account's forward sparkline series.

    Reuses the dense per-account balance maps already built for the
    net-worth trend (:func:`build_account_net_worth_maps`), so the sparkline
    and the net-worth math read ONE projection rather than two that could
    drift.  Slices each account's forward window (up to
    :data:`_SPARKLINE_PERIODS` points from the current period) and keeps only
    the accounts whose window is informative (:func:`_is_informative`); a
    flat account is omitted so its card falls back to the figure + projected
    line.

    Args:
        account_maps: The dense maps from
            :func:`build_account_net_worth_maps`, each carrying
            ``account_id`` and ``balances``.
        forward_periods: The forward window (current period onward),
            chronological.

    Returns:
        ``{account_id: [Decimal, ...]}`` -- the forward balance series for
        each informative account; empty when none qualify.  The route
        normalizes each series to SVG geometry.
    """
    window = forward_periods[:_SPARKLINE_PERIODS]
    result: dict[int, list[Decimal]] = {}
    for data in account_maps:
        balances = data["balances"]
        series = [balances[p.id] for p in window if p.id in balances]
        if _is_informative(series):
            result[data["account_id"]] = series
    return result
