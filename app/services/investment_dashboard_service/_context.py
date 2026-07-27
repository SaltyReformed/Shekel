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
from datetime import date
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
    current_period_transfer_contribution,
)
from app.services.projection_inputs import (
    build_investment_projection_inputs,
    load_active_deductions_for_account,
    load_shadow_income_contributions_for_account,
)
from app.utils.dates import to_display_date

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
            total).  DISPLAY ONLY: the projection seeds from the cash basis
            instead (see ``projection_seed``).
        projection_seed: The CASH-BASIS end-of-current-period balance (the
            pre-growth contribution total from
            :func:`_resolve_seed_balance`, NOT the modeled
            ``current_balance``) with the current period's own transfer
            contribution removed.  The growth projection seeds from this
            while still including the current period in its window: the
            engine re-applies that contribution for the current period, so
            subtracting it from the seed first leaves it applied exactly
            once (deep-quality-hunt #9).  Seeding from the cash basis (not
            the modeled headline, which already grew the anchor forward to
            today) likewise leaves the current period's GROWTH applied
            exactly once.  Only the transfer contribution is removed --
            every other current-period movement (expenses, deposits) stays,
            because the engine never re-creates those.  It is also the base
            of the chart's cumulative-contribution series.
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
    the cockpit "as of" uses), NOT the anchor period's ``start_date``.  The
    UTC ``created_at`` is converted to display tz
    (:func:`~app.utils.dates.to_display_date`); ``None`` when no baseline
    scenario is configured.
    """
    if balance_ctx.scenario is None:
        return None
    anchor = cash_ledger.resolve_anchor(account, balance_ctx.scenario.id)
    return to_display_date(anchor.created_at)


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
    basis).  DISPLAY only -- the projection seeds from the cash basis
    (:func:`_resolve_seed_balance`) to avoid re-growing today.  Falls back to
    :attr:`Account.current_anchor_balance` with no scenario / anchor / period.
    """
    anchor_balance = account.current_anchor_balance or Decimal("0.00")
    if balance_ctx.scenario is None or current_period is None:
        return anchor_balance
    balances = balance_at.balance_map(account, balance_ctx, all_periods)
    if balances is None:
        return anchor_balance
    return balances.get(current_period.id, anchor_balance)


def _resolve_seed_balance(
    account: Account,
    balance_ctx: BalanceContext,
    current_period,
    all_periods: list,
) -> Decimal:
    """Return the cash-basis balance the forward growth projection seeds from.

    The end-of-current balance with NO modeled growth, read through the
    seam's cash-basis seed accessor
    (:func:`~app.services.balance_at.investment_seed_map`) so the projection
    compounds from the cash basis, not the modeled headline (which already
    grew the anchor to today -- seeding from it would double-count the current
    period's growth, deep-quality-hunt #9; the seed producer is reachable only
    through the seam, being a private submodule of it).  Falls back to
    :attr:`Account.current_anchor_balance` with no scenario / anchor / period.
    """
    anchor_balance = account.current_anchor_balance or Decimal("0.00")
    if (balance_ctx.scenario is None
            or account.current_anchor_period_id is None
            or current_period is None):
        return anchor_balance
    balances = balance_at.investment_seed_map(
        account, balance_ctx, all_periods,
    )
    return balances.get(current_period.id, anchor_balance)


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
    # The headline tile shows the model-from-anchor balance (so it agrees
    # with /savings and the net-worth trend); the forward projection seeds
    # from the cash basis instead, so the two are resolved separately.
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
    # Seed for the forward projection: the CASH-BASIS end-of-current balance
    # (:func:`_resolve_seed_balance`, NOT the modeled ``current_balance``
    # headline) with the current period's own transfer contribution removed,
    # so the engine -- which re-applies that contribution when its window
    # includes the current period -- does not double-count it
    # (deep-quality-hunt #9).  Seeding from the cash basis (the modeled
    # headline already grew the anchor forward to today) likewise leaves the
    # current period's GROWTH applied exactly once.  Other current-period
    # balance movements (expenses, deposits) stay in the seed because the
    # engine never re-creates them.
    projection_seed = (
        _resolve_seed_balance(account, balance_ctx, current_period, all_periods)
        - current_period_transfer_contribution(
            acct_contributions, current_period,
        )
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
