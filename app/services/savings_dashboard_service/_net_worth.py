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
:mod:`app.services.balance_at._kernel` (including the investment / 401k growth
sub-chain the forward trend projects forward) and answers an AMORTIZING loan
from its own ``positions()`` fold (plan step C3b3).  What this module owns is
the REDUCTION over those balances -- asset-plus / liability-minus -- not the
balances themselves.  (:func:`compute_property_equity` is the exception that
proves the boundary: it is an EQUITY figure, not a net-worth balance, and it
delegates to :mod:`app.services.home_equity_service`.)

**Every producer here that reduces over BALANCES takes ONE shape: the
per-account :class:`~.._types.AccountProjection` list** (plan step X-w, ruling
R-CG, finding N-114) -- the today figures, the trend series and the card
sparklines.  The today figures took it and the series took a parallel
``{account_id, balances, is_liability}`` dict -- the same accounts, on the same
render, in two containers, the second STORING the liability rule the first
derives.  ``build_account_net_worth_maps`` was this module's builder for that
dict and is deleted; the dense period map is now
:attr:`~.._types.AccountProjection.balances`, built once by the seam inside
:func:`.._projections._seam_batches` for every kind including loans.

The two producers that take raw ``Account`` rows are NOT balance reductions and
that is why they are exempt: :func:`build_trend_periods` computes a WINDOW from
each account's kind and loan schedule, and :func:`compute_property_equity`
resolves an EQUITY figure through another service.  (This paragraph claimed
"every producer here" until plan step X-w6's adversarial review counted them --
the same overclaim X-t2 made about this package's seam doors, two steps on.)

The maps are still built over ALL periods, never a forward sub-window.  Since
plan step X-g2b every kind is a TOTAL fold, so no path NEEDS the dense domain to
find a seed any more -- the reason the rule was written (the INVESTMENT and
APPRECIATING paths seeding off an anchor-forward producer that had to be handed
its anchor period) is gone with the producer.  What survives is the rule itself:
a window is a window, and the forward consumers read the periods they want back
out by id, so passing everything keeps one caller from asking about a slice and
rendering it as a whole.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.services import home_equity_service
from app.services.home_equity_service import HomeEquity
from app.services.amortization_engine import AmortizationRow
from app.services.balance_at import BalanceContext
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
# Nothing from :mod:`app.services.account_category` is imported here any more
# (the module was ``net_worth_account_data`` until plan step X-z, ruling R-CQ).
# Plan step X-t1 moved the today reduction onto
# :attr:`~.._types.AccountProjection.is_liability` (which IS that module's
# classifier), and plan step X-w deleted ``to_net_worth_account_data`` with the
# second per-account container it built -- so every net-worth surface in this
# module now reaches the asset/liability rule through the projection, and an
# alias whose only remaining uses were docstrings would be a name that reads as
# a call site and is not one (finding N-63's class).
from app.services.savings_dashboard_service._display import (
    LIABILITY_KEY,
    _CATEGORY_ORDER,
    category_key,
)
from app.services.savings_dashboard_service._metrics import _sum_liquid_balances
from app.services.savings_dashboard_service._types import AccountProjection

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class NetWorthToday:
    """The cockpit hero and its three chips: what the user is worth right now.

    A frozen value object since plan step X-w3 (ruling R-CI).  It was a four-key
    dict SPREAD into the region beside ``series`` and ``horizon``
    (``{**today, ...}``), so "what does the net-worth region publish" was
    answerable only by reading two producers and a spread operator, and the
    hero's contract lived in a template comment.

    Attributes:
        net_worth: ``total_assets - total_liabilities`` -- the hero figure.
        total_assets: The sum of every non-liability account's balance today.
        total_liabilities: The POSITIVE magnitude owed across every liability.
        liquid: The subset of assets in liquid account types (the
            emergency-fund basis), from :func:`.._metrics._sum_liquid_balances`.
    """

    net_worth: Decimal
    total_assets: Decimal
    total_liabilities: Decimal
    liquid: Decimal


@dataclass(frozen=True)
class TrendPoint:
    """One x-position on the ``2 years`` trend: the pay period's end date.

    **It carried a ``period_index`` too, and plan step X-w6 deleted it**
    (ruling R-CL).  Nothing in ``app/`` read it: the presentation boundary
    (:func:`app.routes.savings._serialize_net_worth_chart`) formats
    ``end_date`` and nothing else, the cockpit tests the list for truthiness,
    and no script names it -- so it was a published field with no consumer,
    which is finding N-100's defect, written by the same step that deleted
    ``goal_mode_id`` for exactly that.  X-s3's precedent, one step later: that
    step's own review found ``DtiMetrics.gross_monthly_income`` had no reader
    and the field it had just written went.

    The RECORD survives its one field, deliberately.  The name is what says the
    trend's x-axis is a DATE per pay period rather than a bare list of dates,
    and both the serializer and the real-data harness read it by that name; a
    ``list[date]`` would save a line and lose the sentence.

    Attributes:
        end_date: The pay period's end date -- the chart's x-axis label, and
            the only field any consumer reads.
    """

    end_date: date


@dataclass(frozen=True)
class NetWorthSeries:
    """The ``2 years`` trend: the net line, its band split, and the today mark.

    **Built ONCE, and that is the point** (plan step X-w3, ruling R-CI).  It was
    a three-key dict that :func:`.._orchestrator._compute_net_worth_section`
    then MUTATED a fourth key into after the producer returned
    (``series["current_index"] = ...``) -- so the object a template and a
    serializer read was never fully constructed anywhere, and "which keys does
    the series have" needed both modules in call order to answer.  That is
    byte-for-byte the shape ruling R-BD deleted from :class:`~.._metrics.DebtSummary`,
    whose DTI keys were mutated in by a separate applier.

    Attributes:
        periods: The trend window's :class:`TrendPoint` descriptors,
            chronological (history tail, then the current period, then the
            forward projection).
        net: The net-worth figure at each period, parallel to :attr:`periods`.
        composition: ``{band: [Decimal, ...]}`` over
            :data:`_COMPOSITION_BANDS`, each band's series parallel to
            :attr:`periods`.  The asset-side bands sum to total assets and the
            liability band IS total liabilities, so ``net[i]`` is their
            difference by construction.
        current_index: The position of the CURRENT period within
            :attr:`periods` -- the solid-history / dashed-projection boundary
            and the "Today" marker.  Equivalently, the count of leading history
            points.
    """

    periods: list[TrendPoint]
    net: list[Decimal]
    composition: dict[str, list[Decimal]]
    current_index: int


@dataclass(frozen=True)
class PropertyEquity:
    """One Property account paired with its resolved equity snapshot.

    A frozen value object since plan step X-w3 (ruling R-CI); it was an untyped
    ``{account, equity}`` dict.  Both fields are read by the cockpit's per-cell
    equity caption.

    Attributes:
        account: The APPRECIATING :class:`~app.models.account.Account`.
        equity: Its :class:`~app.services.home_equity_service.HomeEquity`
            snapshot -- the SAME producer the Property detail page reads, so the
            two surfaces cannot report different equity for one home.
    """

    account: Account
    equity: HomeEquity


@dataclass(frozen=True)
class NetWorthRegion:
    """The cockpit's whole net-worth region: today, the trend, the horizon.

    A frozen value object since plan step X-w3 (ruling R-CI), and PUBLIC and
    living beside its own two field types since plan step X-w6 (ruling R-CN).
    It was ``_orchestrator._NetWorthRegion`` -- the one type the route and both
    cockpit templates actually read, and the only value object this arc's step
    created that was underscore-private, in a module its fields' types did not
    live in.  That placement is what forced
    :func:`app.routes.savings._serialize_net_worth_chart` to drop its parameter
    annotation, against the coding standard.  It was a dict
    assembled by SPREADING one producer's four keys beside two more
    (``{**today, "series": ..., "horizon": ...}``), which is why "what does this
    region publish" needed three producers and a spread operator to answer, and
    why the template's own contract lived in a header comment.

    The today figures are COMPOSED rather than flattened, which is ruling
    R-AW's rule: a bundle that mirrors another value object field by field goes
    stale the moment that object grows, and this package has paid for that twice
    (findings B-16 and N-104).

    Attributes:
        today: The :class:`NetWorthToday` hero and its chips.
        series: The ``2 years`` :class:`NetWorthSeries`.
        horizon: The ``Horizon`` range from
            :func:`~.._horizon.build_horizon`, or ``None`` when the user has no
            pay periods.  **Deliberately still a dict** (ruling R-CI): its key
            set at EVERY level is pinned by ``TestHorizonSerialization``, which
            removes each key in turn and requires the serializer to raise -- a
            stronger contract than a dataclass, because it proves every
            published key is READ.  Findings N-100 and N-104 bought that guard.
    """

    today: NetWorthToday
    series: NetWorthSeries
    horizon: dict | None

# The net-worth composition bands: the cockpit CATEGORIES, split into the
# asset side and the one liability band.  The asset-side bands sum to the asset
# total, the liability band is the liability total, and net worth is their
# difference -- so the composition split reconciles to the ``net`` total by
# construction (P-AC1 Loop B P1).
#
# DERIVED from the display vocabulary rather than restated (plan step X-t3,
# finding N-108).  A band IS a category key
# (:func:`~app.services.savings_dashboard_service._display.category_key` names
# one per account), so listing them again here made the same vocabulary
# answerable two ways in one package -- and a band this producer sums that the
# grid does not group by would put money in a chart with no card behind it.
# The categories themselves come from :class:`~app.enums.AcctCategoryEnum` plus
# the ``other`` fall-through; see ``_display._CATEGORY_ORDER``.
#
# **The liability band's own key came out of this module at plan step X-z**
# (ruling R-CP, finding N-118).  It was ``_LIABILITY_BAND = "liability"`` here,
# a second spelling of the string ``_display`` assigns -- so the band this
# producer sums and the key that module hands each account were equal by
# reading.  ``_display.LIABILITY_KEY`` is the one home and this module imports
# it, which is what makes "the account in the liability band is exactly the
# account :func:`~app.services.account_category.is_liability_account` answers
# True for" a property of construction.
_ASSET_BANDS = tuple(
    key for key in _CATEGORY_ORDER if key != LIABILITY_KEY
)
_COMPOSITION_BANDS = _ASSET_BANDS + (LIABILITY_KEY,)


def compute_net_worth_today(
    account_data: list[AccountProjection],
) -> NetWorthToday:
    """Compute the today net-worth figures from the projected account data.

    Reduces over each account's ``current_balance`` -- the entries-aware
    resolver figure already in ``account_data`` (E-25), NOT the raw
    ``current_anchor_balance`` cache -- so this hero agrees with the
    per-tile balances the same page renders.  Assets add their balance;
    liabilities accumulate their POSITIVE magnitude into
    ``total_liabilities``.  Net worth is ``total_assets - total_liabilities``.

    The liability question is the projection's own
    :attr:`~.._types.AccountProjection.is_liability` (plan step X-t1, finding
    N-111).  It used to re-derive it here from the account, while the grid cell
    beside it read a STORED key on the same projection -- one rule asked two
    ways over one set of balances, which is the shape this arc keeps finding.
    The property IS
    :func:`app.services.account_category.is_liability_account`, so the
    classifier is unchanged and the answer cannot differ.

    Args:
        account_data: Per-account projections from
            ``_compute_account_projections`` (each carrying ``account``
            and ``current_balance``).

    Returns:
        The :class:`NetWorthToday` value object (a four-key dict until plan
        step X-w3, ruling R-CI).
    """
    total_assets = ZERO
    total_liabilities = ZERO
    for ad in account_data:
        balance = ad.current_balance
        if ad.is_liability:
            total_liabilities += abs(balance)
        else:
            total_assets += balance

    return NetWorthToday(
        net_worth=total_assets - total_liabilities,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        liquid=_sum_liquid_balances(account_data),
    )


def _sum_composition_at_period(
    period_id: int,
    account_data: list[AccountProjection],
) -> dict[str, Decimal]:
    """Sum each category band's balance at one period.

    The per-band generalization of the old asset/liability split: each
    non-liability account adds its balance to its category band (asset /
    retirement / investment / other, from its own resolved category),
    and each liability account accumulates its POSITIVE magnitude
    (``abs(bal)``) into the liability band.

    **The SIGN comes from the projection's own
    :attr:`~.._types.AccountProjection.is_liability`, which is the predicate
    :func:`compute_net_worth_today` reduces with**, and that shared predicate is
    what makes this page's stated identity -- the hero equals this series at the
    current period equals the Horizon's index 0 -- a property of construction.
    The category key decides only which ASSET band a non-liability lands in.

    **The two answers are ONE answer since plan step X-z** (ruling R-CP, finding
    N-118).  They were independent comparisons of ``account_type.category_id``
    against the same cached id -- equivalent by reading, and by nothing else --
    so this reducer asked one rule two ways over one set of balances.  Both now
    read :func:`app.services.account_category.account_category`, and the display
    mapping is injective, so ``is_liability`` is exactly
    ``category_key(ad.category) == LIABILITY_KEY``: since plan step X-z7 both
    read ONE resolved member off the projection, so there is no second lookup
    left to disagree with.

    This is the ONE per-period net-worth reduction.  Summing the asset-side
    bands and subtracting the liability band is exactly asset ``+bal`` /
    liability ``-abs(bal)``, and :func:`compute_net_worth_series` derives its
    ``assets`` / ``liabilities`` / ``net`` from these bands rather than
    re-reducing the maps -- so the composition split reconciles to the series
    by construction, not by two producers agreeing.

    Args:
        period_id: The pay period id to read balances at.
        account_data: The per-account projections, each carrying its dense
            :attr:`~.._types.AccountProjection.balances` map (plan step X-w,
            ruling R-CG).  It took a parallel
            ``{account_id, balances, is_liability}`` dict built from the same
            accounts on the same render, whose stored flag is finding N-114.
        account_data: (see above) -- each projection also carries its own
            :attr:`~.._types.AccountProjection.category`, which names the band a
            non-liability's balance lands in

    Returns:
        ``{band: Decimal}`` for every band in :data:`_COMPOSITION_BANDS`.

    Raises:
        KeyError: When an account's dense map has no column for *period_id*, or
            when the category map has no entry for the account.  Both are
            producer defects, never display states -- see the balance read
            below.
    """
    sums = {band: ZERO for band in _COMPOSITION_BANDS}
    for ad in account_data:
        # INDEXED, not ``.get(period_id, ZERO)`` (plan step X-w6, ruling R-CK).
        # The trend window is a slice of the SAME period list the seam built
        # every map over, so a missing column is a defect in the seam or in the
        # window -- and answering it with ZERO banks a real account's balance at
        # nothing for that band, so the composition silently stops reconciling
        # to the hero the page asserts it equals.  Ruling R-CJ's rule, which
        # X-w1 applied to the category map one line down and not to this one.
        bal = ad.balances[period_id]
        if ad.is_liability:
            sums[LIABILITY_KEY] += abs(bal)
        else:
            sums[category_key(ad.category)] += bal
    return sums


# Recent-history cap for the net-worth trend's leading "actual" segment.
# The trend opens with up to this many already-elapsed periods (the solid
# history the forward projection extends from) before the current period.
# ~6 periods is ~3 months at the biweekly cadence; the developer's ruling
# (accounts_audit.md, "forward reach + short tail") fixes a SHORT tail
# rather than a full-history axis.
_TREND_HISTORY_PERIODS = 6

# Account kinds whose past balances are RECORDED rather than modelled.  Since
# plan step X-c2b2 a cash account's balance is a fold over its own assertions,
# so it is real at every period and CONSTRAINS the history window nowhere
# (finding N-44) -- but its presence is still what makes a backward run actual
# at all, which is why it participates in the gate with the no-constraint
# index below instead of being skipped.  INVESTMENT (reverse growth
# projection) and APPRECIATING (flat anchor carry) are MODELLED before their
# anchor, so a set holding only those has no actual history to draw and falls
# through to the no-history default.
_RECORDED_HISTORY_KINDS = frozenset({
    AccountProjectionKind.PLAIN,
    AccountProjectionKind.INTEREST,
})

# The gate index an account contributes when it constrains nothing: the
# earliest period there could be.  ``max`` over the gating indices then leaves
# the window to whatever genuinely does constrain it (a loan's schedule) or to
# the ``_TREND_HISTORY_PERIODS`` cap.
_UNCONSTRAINED_INDEX = 0


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
            (:func:`app.services.balance_at.debt_schedule_rows`), or ``None``
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
    as a fallback value in the past.  ONE kind still carries such a fallback:

    - AMORTIZING loans: the resolver schedule is today-forward, so periods
      before it report the loan's CURRENT balance held flat -- today's
      balance, not its real past balance.  Gates at its schedule-start index
      (:func:`_loan_schedule_start_index`).

    Every NON-loan kind is defined at every period by the seam's one event
    replay, so none of them constrains the window.  This paragraph named the
    INVESTMENT reverse projection and the APPRECIATING flat carry until plan
    step X-g4b: those were two arms of a dispatch ladder ruling R-AD deleted,
    and the property that mattered -- defined everywhere -- is now a
    consequence of the fold being TOTAL rather than of each arm modelling its
    own past.

    **Cash no longer CONSTRAINS the window (plan step X-c2b2, finding N-44).**
    PLAIN and INTEREST accounts used to gate at their anchor period, because
    the projection carried the running balance forward from the anchor and a
    pre-anchor period had NO balance for them -- so cash silently dropped out
    of the past and the trend refused to draw it.  That gate was a compensator
    for finding cash D3, and the fold closes it: every assertion is replayed,
    so a past period reads the balance the account really held then.  Keeping
    it would have made it a compensator for nothing that suppressed real
    history -- measured on the prod-shape clone 2026-07-26, both real cash
    accounts are anchored in the CURRENT period, so the cash arm equalled the
    current index and ``/savings`` drew ZERO history points; without it the
    loan arm gates at index 1 and the trend draws 6 (the
    :data:`_TREND_HISTORY_PERIODS` cap).  It was still load-bearing at HEAD --
    at those same indexes neither cash account had any balance at all, so
    deleting it one commit early would have drawn six history points
    understated by about ``$6,460``.

    A cash account still PARTICIPATES, at :data:`_UNCONSTRAINED_INDEX`, and
    that is load-bearing rather than decorative: dropping it from the loop
    entirely would put a LOAN-FREE user into the no-history default below and
    take away the very history this change exists to restore.  What the
    default protects is a set whose past is wholly MODELLED -- investments and
    property only -- and cash is exactly the thing that makes it not so.

    Returns the maximum gating index -- the earliest period at or after
    which every cash account has a real balance AND every loan is within
    its schedule -- clamped to not exceed ``current_period``'s index.
    Returns ``current_period``'s index (no history) when nothing gates
    earlier, so the trend never fabricates a backward run for an
    investment-or-property-only set (those are projected, not "actual").

    Args:
        accounts: The user's active accounts (each with ``account_type``
            eager-loaded for :func:`classify_account` and an ``id``).
        all_periods: All of the user's pay periods (the loan gate maps a
            first-payment date to its period index).
        current_period: The period containing today (the upper clamp).
        debt_schedules: account_id -> the loan's amortization ROW list
            (from :func:`app.services.balance_at.debt_schedule_rows`), for the
            loan gate.  Not a ``DebtSchedule`` bundle: the rows carry no
            ``projection_seed``, which is the balance the fence keeps out of an
            out-of-cluster consumer's hands.  (A row does carry a
            ``remaining_balance``; this module reads only ``payment_date``.)

    Returns:
        The earliest honest history ``period_index`` (``0`` ..
        ``current_period.period_index``).
    """
    gating_indices: list[int] = []
    for account in accounts:
        kind = classify_account(account)
        if kind in _RECORDED_HISTORY_KINDS:
            gating_indices.append(_UNCONSTRAINED_INDEX)
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
    loan is within its schedule (none shows today's balance carried flat
    backward through its real past).  Cash no longer constrains it at all
    (finding N-44): the fold replays every assertion, so a past period reads
    the balance each cash account really held then.

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
            (:func:`app.services.balance_at.debt_schedule_rows`), for the loan
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
    account_data: list[AccountProjection],
    trend_periods: list[PayPeriod],
    current_index: int,
) -> NetWorthSeries:
    """Build the net-worth trend over the trend window.

    Reads each trend period's id out of each projection's dense
    :attr:`~.._types.AccountProjection.balances` map (built over ALL periods by
    the seam, inside :func:`.._projections._seam_batches`) and produces the
    ``net`` series, the per-category ``composition`` split (P-AC1 Loop B P1),
    and the period descriptors the route serializes.

    **It published ``assets`` and ``liabilities`` as well, and plan step X-s1
    deleted them** (finding N-104's residue).  They were one fact under two
    keys: the split and the totals came from ONE per-period sum
    (:func:`_sum_composition_at_period`), so ``assets[i]`` was by construction
    the sum of the asset-side ``composition`` bands and ``liabilities[i]`` was
    the ``composition["liability"]`` band.  The chart payload carried both
    across to a client that reads neither, and once X-s1 stopped copying them
    the producer keys had ZERO ``app/`` readers (AST-verified) -- the shape
    rulings R-AZ ("one fact under two keys") and R-BA ("dead surface kept
    honest by its own tests") each deleted once already.  A consumer that wants
    the totals sums the bands, which is what every reader now does; ``net[i]``
    still equals that difference by construction.

    The ``trend_periods`` window (from :func:`build_trend_periods`) is the
    honest history tail followed by the full forward projection; this
    producer is window-agnostic -- it sums whatever periods it is given, so
    widening the window from forward-only to history-plus-forward needed no
    change here.

    Takes the already-projected ``account_data`` rather than the raw accounts,
    so the maps are built exactly once for the whole render and shared with the
    per-account sparklines, the tiles and the hero (the locked build-once
    invariant).  Until plan step X-w (ruling R-CG) the shared structure was a
    SECOND per-account container the orchestrator built beside the projections;
    it is now the projections themselves, so "one projection" is what the type
    says rather than what two builders agree on.

    Args:
        account_data: The per-account projections, each carrying its dense
            :attr:`~.._types.AccountProjection.balances` map.
        trend_periods: The trend window (history tail + current + forward),
            chronological; each must appear in the dense maps' domain.
        current_index: The current period's position within *trend_periods*,
            from :func:`build_trend_periods` -- the solid/dashed boundary and
            the "Today" marker.  **It is an ARGUMENT because the result is
            built ONCE** (plan step X-w3, ruling R-CI): the caller used to
            MUTATE it onto the returned dict, so the series a template read was
            never fully constructed in any one place.  The window and its index
            come from ONE producer (:func:`build_trend_periods` returns both),
            so the only caller hands over a matched pair.  (This said "a caller
            CANNOT pass an index for a different window"; nothing in the
            signature enforces that, and this step's own edge-case test passes
            ``([], [], {}, 0)``.  Corrected at plan step X-w6 -- killing the
            post-return mutation is the real gain, and overstating it as a
            construction guarantee is the class this arc keeps paying for.)

    Returns:
        The :class:`NetWorthSeries` for this window.
    """
    periods: list[TrendPoint] = []
    net: list[Decimal] = []
    composition: dict[str, list[Decimal]] = {
        band: [] for band in _COMPOSITION_BANDS
    }

    for period in trend_periods:
        sums = _sum_composition_at_period(period.id, account_data)
        period_assets = sum((sums[band] for band in _ASSET_BANDS), ZERO)
        period_liabilities = sums[LIABILITY_KEY]
        periods.append(TrendPoint(end_date=period.end_date))
        net.append(period_assets - period_liabilities)
        for band in _COMPOSITION_BANDS:
            composition[band].append(sums[band])

    return NetWorthSeries(
        periods=periods,
        net=net,
        composition=composition,
        current_index=current_index,
    )


def compute_property_equity(
    accounts: list,
    ctx: BalanceContext,
) -> list[PropertyEquity]:
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

    **It is a seam DOOR, and it owns its no-baseline state** (plan step X-t5,
    finding N-107's residue).  :func:`~app.services.home_equity_service.resolve_home_equity`
    calls :func:`app.services.balance_at.loan_figures` on each secured loan --
    its own ``Raises:`` block says so -- which runs the seam's
    ``require_scenario``.  Plan step X-t2 hoisted the rule for the net-worth
    region and its docstrings then claimed this PACKAGE had exactly two seam
    doors; this was the third, reached unguarded from the same page, and a
    borrower with a Property securing a mortgage and no baseline got a
    ``ValueError`` where the other tiles degraded.  Both of X-t's adversarial
    reviews found it independently and one EXECUTED it; it pre-dates the step
    (the same probe raises at ``33cb3e8f``), which is exactly why a census that
    counted call sites instead of reading the call graph is worth recording.

    Args:
        accounts: The user's active accounts.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
            Each secured loan is read from its memo, so the mortgage leg of this
            card is the SAME resolution the debt card and the net-worth liability
            column read -- one resolution, not a fourth one that has to agree.
    Returns:
        A list of :class:`PropertyEquity` values, one per Property account in
        ``accounts`` order.  Empty when the user has no Property accounts.
        (They were untyped ``{account, equity}`` dicts until plan step X-w3,
        ruling R-CI.)

        The no-baseline guard this opened with went at plan step X-v2 (ruling
        R-BW).  It was added at X-t5 because this is the package's THIRD seam
        door -- ``compute_property_equity`` -> ``home_equity_service`` ->
        ``loan_figures`` -- and it raised a 500 on `/savings` for a borrower
        with a Property securing a mortgage.  The raise is now ANSWERED rather
        than forestalled, by the same handler that answers the other two doors,
        so a fourth door discovered tomorrow needs no fourth guard.
    """
    result: list[PropertyEquity] = []
    for account in accounts:
        if classify_account(account) is AccountProjectionKind.APPRECIATING:
            result.append(PropertyEquity(
                account=account,
                equity=home_equity_service.resolve_home_equity(account, ctx),
            ))
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
    account_data: list[AccountProjection], forward_periods: list[PayPeriod],
) -> dict[int, list[Decimal]]:
    """Build each informative account's forward sparkline series.

    Reads each projection's dense
    :attr:`~.._types.AccountProjection.balances` map -- the one the tile's
    balance, the net-worth trend and the group subtotal all come from -- so the
    sparkline and the net-worth math read ONE projection rather than two that
    could drift.  Slices each account's forward window (up to
    :data:`_SPARKLINE_PERIODS` points from the current period) and keeps only
    the accounts whose window is informative (:func:`_is_informative`); a
    flat account is omitted so its card falls back to the figure + projected
    line.

    Args:
        account_data: The per-account projections, each carrying its dense
            ``balances`` map.  It took a parallel container built beside the
            projections until plan step X-w (ruling R-CG).
        forward_periods: The forward window (current period onward),
            chronological.

    Returns:
        ``{account_id: [Decimal, ...]}`` -- the forward balance series for
        each informative account; empty when none qualify.  The route
        normalizes each series to SVG geometry.

    Raises:
        KeyError: When an account's dense map has no column for a period in the
            forward window -- a producer defect, never a display state.
    """
    window = forward_periods[:_SPARKLINE_PERIODS]
    result: dict[int, list[Decimal]] = {}
    for ad in account_data:
        # INDEXED over the WHOLE window, with no ``if p.id in balances`` filter
        # (plan step X-w6, ruling R-CK).  The filter was the third spelling of
        # "is this map total?" in this module, and it was the most dangerous:
        # :func:`app.routes.savings._serialize_sparklines` normalizes on series
        # LENGTH (``x = (index / last) * _SPARK_VIEW_W``), so silently dropping
        # one point does not leave a gap -- it moves EVERY remaining point on
        # that card.  The forward window is a slice of the same period list the
        # maps are built over, so a missing column is a defect and says so.
        series = [ad.balances[p.id] for p in window]
        if _is_informative(series):
            result[ad.account.id] = series
    return result
