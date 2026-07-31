"""
Shekel Budget App -- Savings Cockpit: long-horizon net-worth producer.

The server-side data producer for the Accounts / Net-Worth cockpit's
``Horizon`` range (P-AC1 Loop B P1): an ANNUAL net-worth composition +
net-trajectory series that runs from today out to the horizon domain end
(the last loan payoff plus one year, rounded to year end; a loan-free user
gets a fixed :data:`_LOAN_FREE_HORIZON_YEARS`-year window), plus the
milestone flags a long chart raises (loan payoffs, debt-free, and every
``$500k`` net-worth crossing).

Where the ``2 years`` range (:func:`.._net_worth.compute_net_worth_series`)
sums the engine-real biweekly period balances, this range models a distance
the real pay-period calendar does not reach, so each band uses the model
the P-AC1 ruling fixed on worked real-data examples:

* **Retirement and Investment bands REUSE the /retirement engine** verbatim
  (:func:`app.services.retirement_projection.build_projection_context` plus
  the ``project_accounts_with_batch`` probe seam over synthetic biweekly
  periods to the horizon end), sampled annually -- so the band is the
  engine's own projection (the constant-employer-base path every net-worth
  consumer uses, the ruled oracle), never a parallel model.  See
  :func:`_retirement_investment_bands` for why the constant base -- not the
  /retirement readiness page's salary-basis refinement -- is the reuse here.
* **Asset band** = per-account param growth: a Property compounds at its
  ``annual_appreciation_rate``, an interest account at its ``apy``, and plain
  cash holds flat -- every figure traceable to a parameter the account
  carries, all through the ONE :func:`app.services.growth_engine.project_balance`
  compound formula (no parallel math).
* **Liability band** = the :mod:`app.services.balance_at` seam's liability view
  (:func:`app.services.balance_at.liability_owed_at_dates`), which owns both
  forward rules -- an amortizing loan follows its resolver schedule, a debt with
  no forward model (a revolving Credit Card) holds flat -- so this module only
  SUMS what the seam returns.  Same amortization the ``2 years`` band and the
  debt card consume.

The today point (index 0) is each band's real today balance, so the horizon
net at index 0 equals the net-worth hero and the ``2 years`` series' current
point by construction.

No Flask imports; every function takes plain data (the loaded core data, the
per-account :class:`~.._types.AccountProjection` values, the id-based category
map) and returns plain ``Decimal`` / ``dict`` data the route serializes at the
presentation boundary.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from app.services import balance_at, growth_engine, retirement_projection
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.savings_dashboard_service._debt_line import (
    debt_line_loans,
    loan_payoff_outlook,
)
from app.services.savings_dashboard_service._display import LIABILITY_KEY
from app.services.savings_dashboard_service._net_worth import (
    _ASSET_BANDS,
    _COMPOSITION_BANDS,
    ZERO,
)
from app.services.savings_dashboard_service._types import AccountProjection

if TYPE_CHECKING:
    from app.services.savings_dashboard_service._types import _DashboardCoreData

# The horizon window for a user with no active amortizing loans: with no
# payoff to anchor the "last payoff + 1 year" domain, the chart shows a fixed
# forward decade so it still reads as a long-term view.
_LOAN_FREE_HORIZON_YEARS = 10

# Net-worth milestone flags fire at each whole multiple of this step the net
# trajectory crosses inside the domain (D11 structural markers).
_MILESTONE_NET_STEP = Decimal("500000")
_ONE_MILLION = Decimal("1000000")
_ONE_THOUSAND = Decimal("1000")

# The debt-free flag's label, named here because a milestone's label is now its
# only handle (plan step X-s1, ruling R-BC).  The per-loan payoff label is
# interpolated inline at its one site rather than given a constant of its own:
# it has no second reader to keep in step, and a named constant with one use is
# the speculative shape this step exists to remove.  A milestone dict carried a
# machine ``kind`` until X-s1; nothing in ``app/`` read it -- the serializer
# copied it into the payload and the client's flag plugin never looked at it --
# so it was a published key with no consumer, which is finding N-100's own
# defect one level down.  Deleting it leaves the label as the identity, and a
# label a test matches on must come from HERE rather than be re-typed at the
# assertion: plan step X-q3 renamed the debt-free flag ("Debt-free" -> the
# string below) and a hand-typed copy in a test would have gone silently stale.
#
# **The two labels CAN collide, and that is ruled acceptable** (developer,
# 2026-07-28, finding N-110): "<name> paid off" equals this string exactly for
# an account a user names "All loans".  Two flags at two dates are two true
# statements and the chart draws both; DROPPING one to keep labels unique would
# hide a real payoff, which is the worse failure.  What a collision breaks is a
# consumer that identifies a flag by its label ALONE -- so identify one by the
# ``(label, date)`` PAIR, which is unique by construction because a per-loan
# flag fires only strictly before the debt-free date.  Pinned by
# ``TestAMilestoneLabelCanCollide``; re-adding a machine ``kind`` was rejected
# on the same ground X-s3 deleted ``DtiMetrics.gross_monthly_income``: a field
# whose only consumer is a test is a field with no consumer.
_DEBT_FREE_MILESTONE_LABEL = "All loans paid off"

# The most milestone flags the chart's two staggered lanes stay readable
# with (P-AC1 ruling: "flag count capped for lane readability").  The cap
# bounds the net-worth crossings -- the only unbounded set, since a very
# wealthy trajectory crosses many $500k levels; the structural flags (loan
# payoffs + debt-free) are naturally bounded by the user's loan count and
# are always shown in full, filled first.
_MILESTONE_CAP = 8

# The two category bands the /retirement engine owns.
_ENGINE_BANDS = ("retirement", "investment")

# The asset-side bands this module projects itself, from each account's own
# growth parameter: everything the engine does NOT own.  DERIVED rather than
# written out (plan step X-t5, out of X-t's adversarial design review), because
# the union of the three band producers here -- engine, param-growth, liability
# -- must EXHAUST the composition or the Horizon silently publishes a zero
# series for the missing band while the ``2 years`` range beside it (which keys
# off the category map) reports the real money.  That breaks this module's own
# stated invariant, "the horizon net at index 0 equals the net-worth hero", with
# nothing failing: the finding X-t3 gated across four languages, surviving in
# the one language where it could be DERIVED instead.
_PARAM_GROWTH_BANDS = tuple(
    band for band in _ASSET_BANDS if band not in _ENGINE_BANDS
)


@dataclass(frozen=True)
class _HorizonFrame:
    """The horizon's shared time frame, threaded through the band builders.

    A cohesive value object bundling the four axis-related inputs every band
    builder reads, so each helper takes one ``frame`` argument rather than
    four parallel ones.  All fields derive from ``today`` and the resolved
    ``horizon_end``.

    Attributes:
        today: The producer's as-of date (the index-0 "Today" sample and the
            projection axis start); equals ``sample_dates[0]``.
        horizon_end: The domain end (a year end); equals ``sample_dates[-1]``.
        sample_dates: The annual sample dates -- ``today`` followed by each
            calendar year end through ``horizon_end`` -- the output columns.
        axis: The synthetic biweekly period axis
            (:func:`app.services.growth_engine.generate_projection_periods`)
            the row-based bands project over and are sampled from; the same
            axis the /retirement engine generates for this horizon.
    """

    today: date
    horizon_end: date
    sample_dates: list[date]
    axis: list


def _resolve_horizon_domain(
    account_data: list[AccountProjection], today: date,
) -> tuple[date, date | None]:
    """Resolve the horizon domain end and the debt-free flag's date.

    The domain runs to the payoff of the user's last debt-line loan plus one
    year, rounded up to that year's end (so the final sample lands on a year
    end).  The payoff is NOT derived here: it is
    :func:`~.._debt_line.loan_payoff_outlook`, the ONE derivation the cockpit
    caption and the dashboard debt track read as well (plan step X-q), so the
    flag this chart plants and the caption beside it cannot come from two
    membership rules -- which is exactly how they came to be 19 years apart on
    the developer's own data (finding N-98).

    **This producer applies one rule of its own, and it is a RENDERING
    constraint rather than a second opinion**: the axis is today-forward, so a
    payoff that is not in the future cannot size a domain and cannot carry a
    flag -- :func:`app.routes.savings._milestone_axis_x` clamps a target at or
    before ``dates[0]`` to index ``0.0``, so the flag would be planted on the
    "Today" sample rather than on the month the loan actually cleared.  The
    outlook legitimately reports such a date -- an overdue-but-still-projected
    installment that clears the loan folds at a past DUE date, developer ruling
    at plan step X-q -- and this falls back to the fixed
    :data:`_LOAN_FREE_HORIZON_YEARS`-year window for it, exactly as it does
    when there is no date at all.  The caption on the same page reports the
    date either way, which is the ruling's other half.

    **This answers what the AXIS needs and nothing else** (plan step X-q2,
    finding N-100).  The three states the ``None`` date covers -- no loans at
    all, a loan that never clears, and a payoff already behind us -- belong to
    :class:`~.._debt_line.LoanPayoffOutlook`, which is where they are derived;
    the cockpit footer beside this chart renders the same distinction off the
    :class:`~.._debt_line.LoanPayoffOutlook` the debt summary carries WHOLE
    (plan step X-s3 closed finding N-104's field-by-field copy this sentence
    used to describe; corrected at X-t5).  This returned a
    third ``is_loan_free`` element until X-q2; nothing ever read it, and a
    producer republishing another module's derived property is the copy ruling
    R-AW deleted from the projection dict one layer down.

    Args:
        account_data: The per-account projections (a configured loan carries a
            :class:`~.._types.LoanDetail`).
        today: The producer's as-of date.

    Returns:
        ``(horizon_end, debt_free_date)`` -- the year-end domain end, and the
        last FUTURE payoff date (``None`` when loan-free, when a debt-line loan
        never clears, and when the only payoff is already past; a caller that
        must tell those apart reads the outlook, not this).
    """
    outlook = loan_payoff_outlook(account_data)
    if outlook.all_clear_on is None or outlook.all_clear_on <= today:
        return date(today.year + _LOAN_FREE_HORIZON_YEARS, 12, 31), None
    return date(outlook.all_clear_on.year + 1, 12, 31), outlook.all_clear_on


def _build_sample_dates(today: date, horizon_end: date) -> list[date]:
    """Build the annual sample dates: today, then each year end to the domain.

    ``today`` anchors the series at the real current net worth (the hero);
    the subsequent points are the December 31 of each year through
    ``horizon_end``, so the final point coincides with the domain end (a year
    end) and, for the retirement bands, the /retirement projection horizon.
    A year end that is not strictly after ``today`` is dropped, so a run
    exactly on December 31 does not duplicate the anchor.

    Args:
        today: The anchor date (index 0).
        horizon_end: The domain end (a December 31).

    Returns:
        The chronological sample dates, ``today`` first and ``horizon_end``
        last.
    """
    year_ends = [
        date(year, 12, 31)
        for year in range(today.year, horizon_end.year + 1)
    ]
    return [today] + [d for d in year_ends if d > today]


def _period_id_at(axis: list, target: date) -> int | None:
    """Return the axis period id whose interval contains *target*.

    The synthetic axis periods tile the calendar without gaps, so the period
    containing *target* is the last one whose ``start_date`` is on or before
    it (a *target* past the axis end resolves to the final period).  Kept
    local because the axis periods are
    :class:`~app.services.growth_engine.SyntheticPeriod` namedtuples, which
    carry no ``period_index`` for the general
    :func:`app.services.loan_ledger.find_period_containing_date`.

    Args:
        axis: The chronological synthetic period axis.
        target: The date to locate.

    Returns:
        The containing period's ``id``, or ``None`` when *target* precedes
        the first period's start.
    """
    chosen = None
    for period in axis:
        if period.start_date <= target:
            chosen = period
        else:
            break
    return chosen.id if chosen is not None else None


def _sample_projection(
    pid_to_balance: dict[int, Decimal],
    today_value: Decimal,
    frame: _HorizonFrame,
) -> list[Decimal]:
    """Sample a per-axis-period balance map at the annual sample dates.

    The shared sampler for the row-based bands (retirement / investment /
    asset): the today point is the account's real *today_value* (so the
    series starts at the hero), and each later point reads the projected
    end balance of the axis period containing that year end.  An axis period
    absent from *pid_to_balance* (a non-projecting account, whose engine
    rows are empty) falls back to *today_value* -- a flat carry.

    These bands are therefore period-granular: a year-end sample reads the
    biweekly period's END balance (a few days past December 31), where the
    liability band reads the loan schedule at the exact December 31.  At
    annual granularity the few-day offset is immaterial to the dollar
    figures, and ``net`` is derived from these SAME sampled band values
    (:func:`_net_series`), so the reconciliation is exact regardless.

    Args:
        pid_to_balance: ``{axis_period_id: Decimal end balance}`` for one
            account's projection over ``frame.axis``.
        today_value: The account's real balance today (the index-0 point and
            the flat-carry fallback).
        frame: The horizon time frame.

    Returns:
        The account's balance sampled at each of ``frame.sample_dates``.
    """
    series: list[Decimal] = []
    for sample_date in frame.sample_dates:
        if sample_date <= frame.today:
            series.append(today_value)
            continue
        pid = _period_id_at(frame.axis, sample_date)
        series.append(
            pid_to_balance.get(pid, today_value)
            if pid is not None else today_value
        )
    return series


def _add_into(target: list[Decimal], series: list[Decimal]) -> None:
    """Accumulate *series* into *target* element-wise (in place)."""
    for index, value in enumerate(series):
        target[index] += value


def _zero_series(frame: _HorizonFrame) -> list[Decimal]:
    """Return a zero band series aligned with the sample dates."""
    return [ZERO] * len(frame.sample_dates)


def _retirement_investment_bands(
    user_id: int,
    core: "_DashboardCoreData",
    category_by_account_id: dict[int, str],
    frame: _HorizonFrame,
) -> dict[str, list[Decimal]]:
    """Project the retirement and investment bands via the /retirement engine.

    Reuses the retirement dashboard's own context builder and the
    ``project_accounts_with_batch`` probe seam over the horizon axis, so the
    band is the engine's projection, never a parallel model -- it equals the
    ret_probe oracle the P-AC1 ruling anchored on ($1,187,745.83 at
    2049-12-31 on the developer's data).  Each account's per-period rows are
    sampled annually and summed into its category band; the today point is
    the account's model-from-anchor displayed balance, so the band starts at
    the hero's retirement / investment subtotal.

    The employer-contribution base is held CONSTANT (``employer_salary_basis``
    is ``None``), which is what the ruled oracle used and what every
    net-worth consumer does.  **The two bands no longer share a growth MODEL**
    (plan step X-g2b): the ``2 years`` band is the per-period balance map, which
    is now the daily event replay, while this Horizon band still projects
    forward through ``growth_engine`` -- ruling R-U keeps the engine for the
    forward what-if and moves only the balance-at-T half.  What they share is
    their TODAY point (both read the seam's modelled value there) and the
    constant employer base, so the two ranges of one chart still meet where they
    touch.  Only the /retirement READINESS page grows the employer base with
    the projected salary path (its own fork F3 refinement, documented at
    ``retirement_dashboard_service`` as "every other engine consumer keeps
    the constant base"); applying it here would diverge the Horizon range
    from the ``2 years`` range of the same chart.

    The engine is skipped entirely (returning zero bands) when the user has
    no retirement or investment account, so a loan- or cash-only user pays
    for none of its queries.

    Args:
        user_id: The authenticated user's id (the engine loads its own
            retirement / investment accounts for this user).
        core: The loaded dashboard core data (periods + current period).
        category_by_account_id: Each account's id-based category key.
        frame: The horizon time frame (its ``horizon_end`` is the projection
            horizon, its ``axis`` the explicit period axis).

    Returns:
        ``{"retirement": [...], "investment": [...]}`` -- each band's Decimal
        series over ``frame.sample_dates``.
    """
    bands = {band: _zero_series(frame) for band in _ENGINE_BANDS}
    if not any(
        band in _ENGINE_BANDS for band in category_by_account_id.values()
    ):
        return bands

    # The last two args are return_rate_override and employer_salary_basis,
    # both None: no slider override, and the constant employer base every
    # net-worth consumer uses (the ruled oracle; see the docstring).
    ctx = retirement_projection.build_projection_context(
        user_id, core.all_periods, core.current_period,
        frame.horizon_end, None, None,
    )
    batch = retirement_projection.load_projection_batch(ctx)
    projections = retirement_projection.project_accounts_with_batch(
        ctx, batch, frame.axis,
    )
    for projection in projections:
        account = projection["account"]
        band = category_by_account_id.get(account.id)
        if band not in _ENGINE_BANDS:
            # Defensive: the engine loads only retirement / investment types,
            # so this cannot fire; it keeps a mis-typed account out of the
            # asset bands rather than silently mis-banding it.
            continue
        pid_to_balance = {
            row.period_id: row.end_balance
            for row in projection["projection_rows"]
        }
        _add_into(bands[band], _sample_projection(
            pid_to_balance, projection["current_balance"], frame,
        ))
    return bands


def _horizon_growth_rate(account, kind: AccountProjectionKind) -> Decimal:
    """Return the annual compound rate for a non-retirement asset account.

    Property (APPRECIATING) grows at its
    :attr:`~app.models.asset_appreciation_params.AssetAppreciationParams.annual_appreciation_rate`;
    an interest account (INTEREST) at its
    :attr:`~app.models.interest_params.InterestParams.apy`; plain cash (and
    any account whose parameter row is not yet set) holds flat at ``0`` --
    every rate is a value the account carries, never an invented growth
    assumption.

    Args:
        account: The asset-side account.
        kind: The account's :class:`AccountProjectionKind`.

    Returns:
        The annual rate as a ``Decimal`` fraction (``0`` for flat carry).
    """
    if kind is AccountProjectionKind.APPRECIATING:
        params = getattr(account, "asset_appreciation_params", None)
        return params.annual_appreciation_rate if params is not None else ZERO
    if kind is AccountProjectionKind.INTEREST:
        params = getattr(account, "interest_params", None)
        return params.apy if params is not None else ZERO
    return ZERO


def _asset_bands(
    account_data: list[AccountProjection],
    category_by_account_id: dict[int, str],
    frame: _HorizonFrame,
) -> dict[str, list[Decimal]]:
    """Project the non-engine asset bands from each account's growth param.

    Every account whose band is in :data:`_PARAM_GROWTH_BANDS` -- the
    asset-side categories the /retirement engine does not own, today ``asset``
    and the degenerate ``other`` -- is seeded from its
    real today balance and compounded forward over the horizon axis at its
    own rate (:func:`_horizon_growth_rate`) through the one
    :func:`app.services.growth_engine.project_balance` formula: a Property
    appreciates, an interest account earns its APY, plain cash stays flat.
    Retirement / investment accounts are handled by
    :func:`_retirement_investment_bands`, and loans by
    :func:`_liability_band`, so they are skipped here.

    Args:
        account_data: The per-account projections (the ``current_balance``
            seeds each account's growth).
        category_by_account_id: Each account's id-based category key.
        frame: The horizon time frame.

    Returns:
        One Decimal series per :data:`_PARAM_GROWTH_BANDS` band over
        ``frame.sample_dates`` (today: ``asset`` and ``other``).
    """
    bands = {band: _zero_series(frame) for band in _PARAM_GROWTH_BANDS}
    for ad in account_data:
        account = ad.account
        band = category_by_account_id.get(account.id)
        if band not in bands:
            continue
        today_value = ad.current_balance
        rate = _horizon_growth_rate(account, classify_account(account))
        rows = growth_engine.project_balance(
            current_balance=today_value,
            assumed_annual_return=rate,
            periods=frame.axis,
        )
        pid_to_balance = {row.period_id: row.end_balance for row in rows}
        _add_into(bands[band], _sample_projection(
            pid_to_balance, today_value, frame,
        ))
    return bands


def _liability_band(
    account_data: list[AccountProjection],
    core: "_DashboardCoreData",
    frame: _HorizonFrame,
) -> list[Decimal]:
    """Sum the liability band from EVERY liability account.

    Pure band ASSEMBLY: it selects the ``is_liability`` accounts, asks the
    :mod:`app.services.balance_at` seam what each owes at every sample date
    (:func:`~app.services.balance_at.liability_owed_at_dates`), and sums the
    answers into one series.  No debt can silently vanish from the horizon --
    the band includes a revolving Credit Card (no amortization schedule), not
    just amortizing loans -- so the today point reconciles to the net-worth
    hero's liability total.

    The two forward models (an amortizing loan follows its resolver schedule; a
    liability with no forward model holds flat at its current owed magnitude)
    are the SEAM's rules, not this module's -- a balance-at-T boundary rule
    living in a presentation module is the exact pattern the balance seam exists
    to prevent, and this band held half of one until the seam grew the liability
    view (``followup_fence_loan_owed_at_dates.md``).  The seam also owns the
    ``abs`` owed-magnitude convention and the today point, so index 0 is each
    liability's ledger-confirmed current balance by construction.

    Args:
        account_data: The per-account projections (each carrying an
            ``account``, answering ``is_liability``, and carrying the
            ``current_balance`` -- the already-resolved balance the hero
            renders, threaded into the seam rather than re-resolved).
        core: The loaded dashboard core data (its ``scenario`` scopes the loan
            resolver; ``None`` is a valid no-baseline state the seam handles by
            holding every liability flat).
        frame: The horizon time frame.  Its ``sample_dates`` are today plus each
            future year end (all on or after today, the seam's domain), and its
            ``today`` is threaded to the seam as the as-of date, so the sample
            axis and the seam's present/future boundary share ONE clock read
            rather than racing across a midnight boundary.

    Returns:
        The liability band's Decimal series over ``frame.sample_dates`` (a
        positive owed total per point).
    """
    band = _zero_series(frame)
    liability_ads = [ad for ad in account_data if ad.is_liability]
    if not liability_ads:
        return band

    owed_by_account = balance_at.liability_owed_at_dates(
        [ad.account for ad in liability_ads],
        core.balance_ctx,
        frame.sample_dates,
        {ad.account.id: ad.current_balance for ad in liability_ads},
    )
    for ad in liability_ads:
        _add_into(band, owed_by_account[ad.account.id])
    return band


def _net_series(
    composition: dict[str, list[Decimal]], count: int,
) -> list[Decimal]:
    """Derive the net-worth line from the composition bands.

    ``net[k]`` is the sum of the asset-side bands minus the liability band at
    each sample, so the trajectory reconciles to the bands it rides on by
    construction.

    Args:
        composition: The per-band Decimal series map.
        count: The number of sample points.

    Returns:
        The net-worth Decimal series.
    """
    return [
        sum((composition[band][k] for band in _ASSET_BANDS), ZERO)
        - composition[LIABILITY_KEY][k]
        for k in range(count)
    ]


def _format_net_milestone(amount: Decimal) -> str:
    """Format a net-worth crossing amount as a compact milestone label.

    ``$500k`` under a million; ``$1M`` / ``$1.5M`` / ``$10M`` at or above
    (a whole-million multiple drops its ``.0``), matching the horizon mock's
    flag labels.  The integral case is formatted through ``int`` rather than
    ``Decimal.normalize`` so a round ten-million reads ``$10M`` and not
    ``Decimal.normalize``'s scientific ``1E+1``.

    Args:
        amount: The whole ``$500k`` multiple the net crossed.

    Returns:
        A label such as ``"Net $500k"``, ``"Net $1.5M"``, or ``"Net $10M"``.
    """
    if amount >= _ONE_MILLION:
        millions = amount / _ONE_MILLION
        if millions == millions.to_integral_value():
            return f"Net ${int(millions)}M"
        # A $500k step gives only half-million residues (1.5, 2.5, ...); the
        # fractional Decimal formats without an exponent.
        return f"Net ${millions.normalize()}M"
    thousands = int(amount / _ONE_THOUSAND)
    return f"Net ${thousands}k"


def _structural_milestones(
    account_data: list[AccountProjection],
    debt_free_date: date | None,
    frame: _HorizonFrame,
) -> list[dict]:
    """Build the loan-payoff and debt-free milestone flags.

    One "paid off" flag per debt-line loan that retires BEFORE the final one
    (its payoff strictly precedes ``debt_free_date``), then one "Debt-free"
    flag at ``debt_free_date`` -- so the last loan's payoff is not
    double-flagged as both its own payoff and the debt-free moment.  Empty
    for a loan-free user (``debt_free_date`` is ``None``).

    The loan selection is :func:`~.._debt_line.debt_line_loans`, the SAME one
    the domain resolver's outlook folds, so a loan cannot size the axis while
    being skipped by the flags on it -- or the reverse.  A loan that selection
    drops is already retired, and a retired loan's ``payoff_date`` is ``None``
    (there is no forward crossing left to date), so the flag loop's own
    ``payoff is not None`` test would have excluded it too; sharing the
    selection makes that a property of the construction rather than of two
    rules agreeing.

    Args:
        account_data: The per-account projections.
        debt_free_date: The last future loan payoff, or ``None``.
        frame: The horizon time frame (its ``today`` bounds the payoffs).

    Returns:
        The structural milestone dicts (unsorted, unbounded -- the caller
        caps and orders them).
    """
    if debt_free_date is None:
        return []
    result: list[dict] = []
    for ad in debt_line_loans(account_data):
        payoff = ad.loan.figures.payoff_date
        if payoff is not None and frame.today < payoff < debt_free_date:
            result.append({
                "date": payoff,
                "label": f"{ad.account.name} paid off",
            })
    # The label says what the date MEASURES (plan step X-q3, finding N-99):
    # the derivation behind it covers amortizing loans, the only debts with a
    # payoff model, and a revolving balance on the same chart's liability band
    # never reaches zero.
    result.append({
        "date": debt_free_date,
        "label": _DEBT_FREE_MILESTONE_LABEL,
    })
    return result


def _net_crossing_milestones(
    net: list[Decimal], frame: _HorizonFrame,
) -> list[dict]:
    """Build a milestone flag at each ``$500k`` net-worth crossing.

    For every whole :data:`_MILESTONE_NET_STEP` multiple the net trajectory
    reaches inside the domain, flags the first sample date it reaches it --
    but only for a level the net STARTS below (a level already exceeded today
    is not a future crossing).

    Args:
        net: The net-worth series over ``frame.sample_dates``.
        frame: The horizon time frame.

    Returns:
        The net-crossing milestone dicts, ascending by threshold.
    """
    result: list[dict] = []
    if not net:
        return result
    peak = max(net)
    multiple = _MILESTONE_NET_STEP
    while multiple <= peak:
        if net[0] < multiple:
            for k in range(1, len(net)):
                if net[k] >= multiple:
                    result.append({
                        "date": frame.sample_dates[k],
                        "label": _format_net_milestone(multiple),
                    })
                    break
        multiple += _MILESTONE_NET_STEP
    return result


def _build_milestones(
    account_data: list[AccountProjection],
    net: list[Decimal],
    debt_free_date: date | None,
    frame: _HorizonFrame,
) -> list[dict]:
    """Assemble the capped, date-ordered milestone flags.

    Structural flags (loan payoffs + debt-free) are kept first; net-worth
    crossings fill the remaining slots up to :data:`_MILESTONE_CAP` so the
    two staggered flag lanes stay readable.  The combined list is ordered by
    date for the chart's left-to-right layout.

    Args:
        account_data: The per-account projections.
        net: The net-worth series over ``frame.sample_dates``.
        debt_free_date: The last future loan payoff, or ``None``.
        frame: The horizon time frame.

    Returns:
        The milestone dicts (``{date, label}``), ascending by date.

    Note:
        **Both keys are CONSUMED by the presentation boundary** (plan step
        X-s1, finding N-104): ``date`` positions the flag on the annual axis
        (:func:`app.routes.savings._milestone_axis_x`) and ``label`` is the
        chip's text.  There was a third, a machine ``kind``, which the
        serializer copied into the payload and the client's flag plugin never
        read -- so the same remove-a-key-and-require-a-crash guard that pins
        the horizon dict now reaches these dicts too, rather than stopping one
        level above them.
    """
    structural = _structural_milestones(account_data, debt_free_date, frame)
    crossings = _net_crossing_milestones(net, frame)
    remaining = max(_MILESTONE_CAP - len(structural), 0)
    combined = structural + crossings[:remaining]
    combined.sort(key=lambda milestone: milestone["date"])
    return combined


def _assemble_composition(
    user_id: int,
    core: "_DashboardCoreData",
    account_data: list[AccountProjection],
    category_by_account_id: dict[int, str],
    frame: _HorizonFrame,
) -> dict[str, list[Decimal]]:
    """Assemble the per-band composition series over the horizon frame.

    Sums each band from its producer: the retirement / investment bands from
    the /retirement engine (:func:`_retirement_investment_bands`), the asset
    and other bands from per-account param growth (:func:`_asset_bands`), and
    the liability band from the loan schedules (:func:`_liability_band`).

    **The three producers must partition the account set EXACTLY once, and
    since plan step X-z that is a property of construction** (ruling R-CP,
    finding N-118).  Two of them select by category key and the third selects
    by :attr:`~.._types.AccountProjection.is_liability`, which were independent
    id comparisons: an account the two classified differently would land in an
    asset band AND the liability band -- counted twice, with opposite signs, so
    net worth is wrong by double its balance -- or in neither, vanishing from a
    chart whose own docstring says its index 0 equals the net-worth hero.
    Nothing would raise either way.  Both rules now derive from
    :func:`app.services.account_category.account_category`, so
    ``is_liability`` and ``category_key == LIABILITY_KEY`` are one answer.
    ``test_net_worth_band_vocabulary`` pins the complementary half: that the
    three producers' BANDS are disjoint and exhaust the composition.

    Args:
        user_id: The authenticated user's id.
        core: The loaded dashboard core data.
        account_data: The per-account projections.
        category_by_account_id: Each account's id-based category key.
        frame: The horizon time frame.

    Returns:
        The ``{band: [Decimal, ...]}`` map over :data:`_COMPOSITION_BANDS`.
    """
    composition = {band: _zero_series(frame) for band in _COMPOSITION_BANDS}
    engine_bands = _retirement_investment_bands(
        user_id, core, category_by_account_id, frame,
    )
    asset_bands = _asset_bands(account_data, category_by_account_id, frame)
    for band, series in {**engine_bands, **asset_bands}.items():
        _add_into(composition[band], series)
    _add_into(
        composition[LIABILITY_KEY],
        _liability_band(account_data, core, frame),
    )
    return composition


def build_horizon(
    user_id: int,
    core: "_DashboardCoreData",
    account_data: list[AccountProjection],
    category_by_account_id: dict[int, str],
) -> dict | None:
    """Build the long-horizon annual net-worth composition + milestones.

    The reusable core of the ``Horizon`` range producer: it takes the loaded
    core data, the already-projected account dicts, and the id-based category
    map (so a caller that has already run the dashboard build threads them in
    rather than re-projecting), resolves the domain, and assembles every
    band on one annual sample axis.  The bands reconcile to ``net`` by
    construction (:func:`_net_series`), and index 0 is each band's real today
    balance (so the horizon starts at the net-worth hero).

    **Every key here is one the presentation boundary reads** (plan step X-q2,
    finding N-100): :func:`app.routes.savings._serialize_horizon` consumes all
    five and the client's chart renders them.  It published ``horizon_end`` and
    ``is_loan_free`` as well until X-q2, and no serializer, template or script
    named either -- ``horizon_end`` because it is ``dates[-1]`` by construction
    (the domain end is always the last annual sample, so it was one fact under
    two keys), and ``is_loan_free`` because it is
    :attr:`~.._debt_line.LoanPayoffOutlook.is_loan_free`, whose three-state
    distinction the cockpit footer on this same page renders from the same
    derivation.  A key added here that no consumer reads is the defect this
    step closed; the contract is pinned by ``TestHorizonSerialization``, which
    removes each key in turn and requires the serializer to break.

    **The contract now reaches one level DOWN as well** (plan step X-s1,
    finding N-104): the milestone dicts inside ``milestones`` carried a machine
    ``kind`` the client read no more than it read the two keys X-q2 deleted, so
    a dead key rode inside a live one where the guard could not see it.  X-s1
    deleted it at both ends and the guard now removes each MILESTONE key in
    turn as well, which is why this producer's contract is "every key, at every
    level, is subscripted by the serializer" rather than "every top-level key
    is".

    Args:
        user_id: The authenticated user's id (for the /retirement engine
            reuse).
        core: The loaded dashboard core data.
        account_data: The per-account projections from
            ``_compute_account_projections``.
        category_by_account_id: Each account's id-based category key from
            :func:`~app.services.savings_dashboard_service._display.category_key_by_account_id`.

    Returns:
        A dict with ``dates`` (the sample dates, whose last element is the
        domain end), ``current_index`` (always ``0`` -- the whole horizon is
        forward from today), ``composition`` (the ``{band: [Decimal, ...]}``
        map over :data:`_COMPOSITION_BANDS`), ``net`` (the trajectory), and
        ``milestones`` (the ``{date, label}`` flags).  ``None`` when the
        user has no pay periods (no axis to project over).
    """
    if not core.all_periods:
        return None

    today = core.balance_ctx.as_of
    horizon_end, debt_free_date = _resolve_horizon_domain(account_data, today)
    frame = _HorizonFrame(
        today=today,
        horizon_end=horizon_end,
        sample_dates=_build_sample_dates(today, horizon_end),
        axis=growth_engine.generate_projection_periods(today, horizon_end),
    )

    composition = _assemble_composition(
        user_id, core, account_data, category_by_account_id, frame,
    )
    net = _net_series(composition, len(frame.sample_dates))
    milestones = _build_milestones(account_data, net, debt_free_date, frame)

    return {
        "dates": frame.sample_dates,
        "current_index": 0,
        "composition": composition,
        "net": net,
        "milestones": milestones,
    }
