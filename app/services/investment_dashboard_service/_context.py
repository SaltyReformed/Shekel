"""Investment dashboard -- the shared per-account projection feed.

The loaders and the one bundle (:class:`_ProjectionContext`) every surface of
this package reads: the dashboard's cards, its growth chart, and the balance
hero cell.  Collapsing the duplicated salary-profile / deduction /
contribution / projection-inputs loading into one shared feed is what Commit 28
(S6-01 / MED-01) extracted from the route bodies, and it is what keeps the two
public entries from loading the same account twice.

Boundary discipline (``CLAUDE.md``): no Flask symbol, all money is
:class:`~decimal.Decimal`.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.pay_period import PayPeriod
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.salary_profile import SalaryProfile
from app.models.user import UserSettings
from app.services import (
    balance_at,
    cash_ledger,
    growth_engine,
    income_service,
)
from app.services.balance_at import BalanceContext
from app.services.investment_projection import (
    InvestmentInputs,
    adapt_deductions,
    build_contribution_timeline,
)
from app.services.projection_inputs import (
    build_investment_projection_inputs,
    load_active_deductions_for_account,
    load_shadow_income_contributions_for_account,
)

# A period-like row in a projection: a real ``PayPeriod`` (the dashboard's
# future periods) or a synthetic horizon period from
# ``growth_engine.generate_projection_periods`` (the chart fragment).  Both
# expose ``.id`` / ``.start_date`` / ``.end_date`` -- all the projection
# primitives read off a period.
_PeriodList = list[PayPeriod | growth_engine.SyntheticPeriod]


@dataclass(frozen=True)
class _ProjectionContext:  # pylint: disable=too-many-instance-attributes
    """Every per-account input the dashboard + growth-chart both consume.

    Pylint: ``too-many-instance-attributes`` (11/7) -- a cohesive load-once
    *feed*, not a god-object: every field is a per-account projection input
    resolved once by :func:`_load_projection_context` and fanned out to
    different consumers (``contributions`` -> the growth projection;
    ``deductions`` / ``active_profile`` -> the contribution prompt; ``scenario``
    / ``all_periods`` -> the history chart and anchor caption, so they read the
    SAME inputs the headline resolved against).  Bundling them removes the
    parallel-load duplication the dashboard and chart fragment each carried
    inline (S6-01).  The annual contribution limit is reachable two ways
    (``params.annual_contribution_limit`` /
    ``inputs.annual_contribution_limit``, copied in
    ``calculate_investment_inputs``); read it from one place.

    Attributes:
        params: The account's :class:`InvestmentParams` row, or ``None``
            when the user has not configured the account.  ``None`` is a
            valid dashboard state (the projection and chart degrade to
            empty containers); the growth-chart fragment guards it out
            earlier and never reaches a context with ``params is None``.
        current_balance: The model-from-anchor END-of-current-period
            balance from the :mod:`app.services.balance_at` seam -- the
            displayed "current balance" tile, which agrees to the cent with
            the /savings net-worth tile, the year-end asset aggregate, and
            the net-worth trend (an anchor-in-past investment shows its
            modeled market value, not the flat cash-basis contribution
            total).  It is read at the CURRENT PERIOD's end, which is the
            convention every net-worth surface uses; the projection seeds from
            the same curve one day before its own window opens (see
            ``projection_start`` / ``projection_seed``).
        projection_start: The day the projection's window OPENS -- the day
            after the history line's last valued point
            (:func:`_projection_start`, ruling R-AF).
        projection_ytd: The year-to-date employee contribution the growth
            engine's annual-limit accounting must START from
            (:func:`_projection_ytd`).  It is the THROUGH-current total on this
            surface, because :attr:`projection_start` puts the current pay
            period outside the projection window.
        projection_seed: The account's MODELLED balance on the day before
            :attr:`projection_start`, so it IS the history line's last point and
            the two lines meet (:func:`_resolve_seed_balance`).  Nothing is
            filtered out of it and nothing is subtracted from it: the window
            opens strictly after its date, so the growth engine can neither
            re-grow a day it already grew nor re-apply a contribution it already
            holds (findings N-80 / N-84).  It is also the base of the chart's
            cumulative-contribution series.
        inputs: The :class:`InvestmentInputs` the growth engine needs
            (periodic contribution, employer params, annual contribution
            limit, YTD contributions).
        contributions: The per-period contribution timeline (deductions
            plus transfer receipts) fed to ``project_balance``.
        deductions: The raw :class:`PaycheckDeduction` rows targeting
            this account; drives the contribution-prompt decision.
        active_profile: The user's active :class:`SalaryProfile`, or
            ``None``; drives the deduction-path salary-profile link.
        balance_ctx: The read pass's ``BalanceContext``; the history chart and
            anchor caption read it so both agree with the headline balance.
        anchor_as_of: Display-tz date of the account's latest anchor event
            (C1 hero caption), or ``None`` when no baseline scenario exists.
        all_periods: The user's full pay-period calendar (C2 history basis).
        current_period: The current :class:`PayPeriod`, or ``None``.
    """

    params: InvestmentParams | None
    current_balance: Decimal
    projection_start: date
    projection_ytd: Decimal
    projection_seed: Decimal
    inputs: InvestmentInputs
    contributions: list[growth_engine.ContributionRecord]
    deductions: list[PaycheckDeduction]
    active_profile: SalaryProfile | None
    balance_ctx: BalanceContext
    anchor_as_of: date | None
    all_periods: list
    current_period: PayPeriod | None


def _load_active_salary_profile(user_id: int) -> SalaryProfile | None:
    """Return the user's active salary profile, or ``None`` if none exists."""
    return (
        db.session.query(SalaryProfile)
        .filter_by(user_id=user_id, is_active=True)
        .first()
    )


def _load_investment_params(account_id: int) -> InvestmentParams | None:
    """Return :class:`InvestmentParams` for *account_id* or ``None``."""
    return (
        db.session.query(InvestmentParams)
        .filter_by(account_id=account_id)
        .first()
    )


def _resolve_anchor_as_of(
    account: Account, balance_ctx: BalanceContext,
) -> date | None:
    """Return the display-tz date of the account's latest anchor EVENT (C1).

    Dates the hero's "anchored <date>" caption against the dated anchor SoT
    (the latest :class:`AccountAnchorHistory` row via
    :func:`~app.services.cash_ledger.resolve_anchor`, the same accessor
    the cockpit "as of" uses), NOT the anchor period's ``start_date``.  The UTC
    ``created_at`` is converted to display tz
    (the stored ``observed_on``).

    The ``return None`` for a user with no baseline went at plan step X-v2
    (ruling R-BW): ``scenario_id`` raises and one application-level handler
    answers, so a hidden caption is no longer this function's way of saying
    "the app cannot compute anything for you".
    """
    anchor = cash_ledger.resolve_anchor(account, balance_ctx.scenario_id)
    # The day the balance was TRUE, not the day it was typed (ruling R-DH,
    # plan step 2).  This caption sits beside a "growth since" figure whose
    # accrual window ``balance_at._asset_fold`` opens on exactly this day, so
    # reading ``created_at`` would put the caption and the figure on different
    # days for any back-dated opening.
    return anchor.observed_on


def _resolve_current_balance(
    account: Account,
    balance_ctx: BalanceContext,
    current_period,
    all_periods: list,
) -> Decimal:
    """Return the model-from-anchor "current balance" headline for *account*.

    The displayed tile, read from the :mod:`app.services.balance_at` seam's
    :func:`~app.services.balance_at.balance_map` at the current period so it
    agrees to the cent with /savings and the net-worth trend (an investment
    anchored in the past shows its modeled market value, not the flat cash
    basis).  The projection seeds from the SAME curve, read one day before its
    own window opens (:func:`_resolve_seed_balance`) -- it used to seed from a
    flat cash basis, which discarded every cent earned since the last assertion
    (finding N-80).  Falls back to :attr:`Account.current_anchor_balance` with
    no anchor / period.

    **The no-baseline arm of that fallback is GONE** (plan step X-v2, ruling
    R-CA).  It presented the raw ``current_anchor_balance`` CACHE COLUMN as
    this account's *current balance* -- a figure the app cannot know in that
    state, which is finding N-103's complaint one screen over and the same
    class as the ``$0.00`` net-worth hero deleted in the same pass.  The seam
    raises and the page is answered by the repair card instead.
    """
    anchor_balance = account.current_anchor_balance or Decimal("0.00")
    if current_period is None:
        return anchor_balance
    balances = balance_at.balance_map(account, balance_ctx, all_periods)
    if balances is None:
        return anchor_balance
    return balances.get(current_period.id, anchor_balance)


def _projection_start(current_period) -> date:
    """Return the day the projection's window opens (ruling R-AF).

    The day AFTER the history line's last valued point.
    ``_chart._build_history_series`` plots one point per real pay period
    through the current one, each valued at its ``end_date``, so a projection
    that opens the next day CONTINUES that line -- and MEETS it, because
    :func:`_resolve_seed_balance` reads the seed on that same last day.

    It used to be ``date.today()``, 10-13 days short of the history line's last
    point, so the chart carried a step at the Today marker whose size and sign
    nobody had chosen: measured -$301.96 on the real Empower 401(k) and
    +$105.26 on the Roth IRA.  Verified on both real databases, opening here
    makes the first projected step ($105.66) indistinguishable from the second
    ($106.07).  A biweekly axis from here also lands on the user's REAL
    pay-period boundaries, so the engine's dated contribution lookup matches its
    records over the near horizon instead of falling back to a flat average
    (the near half of finding N-79).

    Args:
        current_period: The current :class:`~app.models.pay_period.PayPeriod`,
            or ``None``.

    Returns:
        The current period's ``end_date`` plus one day, or today when there is
        no current period -- there is then no history line to continue.
    """
    if current_period is None:
        return date.today()
    return current_period.end_date + timedelta(days=1)


def _resolve_seed_balance(
    account: Account,
    balance_ctx: BalanceContext,
    current_period,
) -> Decimal:
    """Return the balance the forward growth projection seeds from.

    The account's MODELLED balance on the day before :func:`_projection_start`,
    read through the seam's date-precise scalar -- the same number the history
    line's last point renders (rulings R-AB / R-AE).

    Nothing is filtered out of it and nothing is subtracted from it: the window
    opens strictly after its date, so the engine can neither re-grow a day it
    already grew nor re-apply a contribution it already holds.  Both
    compensators this function used to carry corrected an overlap that existed
    only because the seed was read at the current period's END while the window
    opened at TODAY -- and the modelled-growth filter, had it survived the date
    change, would have started the projection line BELOW the history line by
    every cent earned since the last balance assertion (findings N-80 / N-84).

    Args:
        account: The investment account.
        balance_ctx: The read pass's ``BalanceContext``.
        current_period: The current pay period, or ``None``.

    Returns:
        The seed balance, falling back to
        :attr:`Account.current_anchor_balance` with no anchor period -- the
        state in which the seam cannot answer.  The no-baseline arm of that
        fallback went at plan step X-v2 (ruling R-CA), for the reason
        :func:`_resolve_current_balance` above carries: it seeded a projection
        CHART from a cache column, so the whole forward line was drawn from a
        figure the app could not know.
    """
    if account.current_anchor_period_id is None:
        return account.current_anchor_balance or Decimal("0.00")
    return balance_at.balance_at(
        account, balance_ctx,
        _projection_start(current_period) - timedelta(days=1),
    )


def _projection_ytd(inputs: InvestmentInputs, projection_start: date,
                    current_period) -> Decimal:
    """Return the YTD contribution the growth engine's limit walk starts from.

    The annual limit is consumed by contributions the engine does NOT project
    plus the ones it does, so the seed must hold exactly the periods outside the
    window: ``ytd_contributions_seed`` is the total STRICTLY BEFORE the current
    period, ``ytd_contributions`` the total THROUGH it, and which is right
    depends on whether the window contains the current period.

    **On this surface it does not** (ruling R-AF), and that is a change.  The
    axis used to open at ``date.today()``, so its first synthetic period stood
    in for the rest of the current period and the engine applied that period's
    contribution itself -- which is precisely why the seed excluded it
    (deep-quality-hunt #10).  The axis now opens the day AFTER the current
    period ends, so the engine never applies it, and seeding the strictly-before
    total leaves the annual limit one period's contribution too roomy: on a
    $23,500 limit with $1,000 a period and today in the year's 15th period, the
    engine would price $9,500 of remaining room where $8,500 is left, project an
    extra contribution inside the calendar year, and compound it for the whole
    horizon.  It would also disagree with the limit CARD on the same page, which
    has always read the through-current total.

    Derived HERE, beside the window it depends on, rather than chosen at each
    ``project_balance`` call: two call sites picking between two YTD fields is
    the argument-a-caller-can-get-wrong shape the balance plan's Section 8 rules
    a defect rather than a contract.  ``retirement_projection`` keeps the
    strictly-before seed and is right to: both of its axes open at or inside the
    current period, so its engine does apply that period.

    Args:
        inputs: The account's :class:`InvestmentInputs`.
        projection_start: The day the projection's window opens.
        current_period: The current pay period, or ``None``.

    Returns:
        The ``Decimal`` YTD to seed the engine's limit accounting with.
    """
    if current_period is None or projection_start <= current_period.end_date:
        return inputs.ytd_contributions_seed
    return inputs.ytd_contributions


def _load_projection_context(
    user_id: int,
    account: Account,
    params: InvestmentParams | None,
    all_periods: list,
    current_period,
) -> _ProjectionContext:
    """Load every per-account input the dashboard + chart fragment share.

    Centralises the projection feed both surfaces need: the canonical
    current balance, the salary-profile-derived projection inputs, the
    deductions targeting this account, the shadow-income contribution
    stream, and the per-period contribution timeline.  Both the
    entries-aware balance resolution and the timeline build previously
    sat near-verbatim in ``compute_dashboard_data`` and
    ``compute_growth_chart_data`` (the S6-01 duplication this collapses);
    bundling the result in :class:`_ProjectionContext` keeps the two
    public entry points thin.

    *params* is supplied by the caller (loaded once for its own guard)
    rather than re-queried here, so neither surface issues a second
    :class:`InvestmentParams` lookup.

    Args:
        user_id: ID of the authenticated user.
        account: The pre-ownership-checked account instance.
        params: The account's :class:`InvestmentParams`, or ``None``.
        all_periods: All pay periods for the user.
        current_period: The current :class:`PayPeriod`, or ``None``.

    Returns:
        A :class:`_ProjectionContext` carrying the seven per-account
        values the projection primitives and card builders consume.
    """
    balance_ctx = BalanceContext.build(user_id)
    # The headline tile shows the model-from-anchor balance at the current
    # period's END (so it agrees with /savings and the net-worth trend); the
    # projection seeds from the same curve one day before its own window opens,
    # so the two are read at different DATES rather than off different bases.
    current_balance = _resolve_current_balance(
        account, balance_ctx, current_period, all_periods,
    )
    active_profile = _load_active_salary_profile(user_id)
    # F-20 / MED-06 / F-032: raise-aware paycheck-engine value, not the
    # off-engine ``annual_salary / pay_periods_per_year`` recompute that
    # silently dropped any applicable ``SalaryRaise`` row pre-Commit-17.
    salary_gross_biweekly = income_service.get_current_gross_biweekly(user_id)
    deductions = load_active_deductions_for_account(user_id, account.id)
    adapted_deductions = adapt_deductions(deductions)
    acct_contributions = load_shadow_income_contributions_for_account(
        account.id, [p.id for p in all_periods],
    )
    # Seed for the forward projection: the account's MODELLED balance on the
    # day before the window opens, which is the history line's own last point
    # (rulings R-AB / R-AE).  Nothing is filtered out of it and nothing is
    # subtracted from it -- the window opens strictly after the seed's date, so
    # there is no overlap for a compensator to correct.
    projection_seed = _resolve_seed_balance(
        account, balance_ctx, current_period,
    )
    inputs = build_investment_projection_inputs(
        params, adapted_deductions, acct_contributions,
        all_periods, current_period, salary_gross_biweekly,
    )
    contributions = build_contribution_timeline(
        deductions=adapted_deductions,
        contribution_transactions=acct_contributions,
        periods=all_periods,
    )
    return _ProjectionContext(
        params=params,
        current_balance=current_balance,
        projection_start=_projection_start(current_period),
        projection_ytd=_projection_ytd(
            inputs, _projection_start(current_period), current_period,
        ),
        projection_seed=projection_seed,
        inputs=inputs,
        contributions=contributions,
        deductions=deductions,
        active_profile=active_profile,
        balance_ctx=balance_ctx,
        # C1 anchor caption date (inlined to stay under the locals limit).
        anchor_as_of=_resolve_anchor_as_of(account, balance_ctx),
        all_periods=all_periods,
        current_period=current_period,
    )


def _load_planned_retirement_date(user_id: int) -> date | None:
    """Return the user's planned retirement date, or ``None`` if unset (C2).

    Shared by :func:`_compute_default_horizon` and :func:`_build_chart_markers`.
    """
    settings = (
        db.session.query(UserSettings)
        .filter_by(user_id=user_id)
        .first()
    )
    return settings.planned_retirement_date if settings else None
