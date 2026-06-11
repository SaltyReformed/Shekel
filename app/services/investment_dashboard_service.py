"""
Shekel Budget App -- Investment Dashboard Service (MED-01 / S6-01)

Pure-data orchestration for the investment / retirement dashboard
template (``investment/dashboard.html``) and the HTMX growth-chart
fragment (``investment/_growth_chart.html``).  Pre-Commit-28 these
two surfaces lived as 295/241-line route bodies in
``app/routes/investment.py`` mixing HTTP, 8 inline ORM queries, and
business logic in one function (S6-01 in
``docs/audits/financial_calculations/06_dry_solid.md``).  The extraction
collapses the duplicated salary-profile / deduction / contribution /
projection-inputs loading that previously appeared verbatim in both
route bodies into one shared helper, and reduces ``investment.py`` to
a thin delegator mirroring the long-standing ``savings.py`` shape
(``app/routes/savings.py:107-113``).

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"):
this module imports no Flask symbol.  The route handles ``current_user``,
``request``, ``url_for``, and the 404 / 302 HTTP responses; this service
owns only ``Decimal``-money math, ORM queries, and the projection-engine
calls.  The dashboard service returns plain dicts the route renders;
``salary_profile_url`` resolution lives in the route because it depends
on :func:`flask.url_for`.

Outputs are byte-identical to the pre-Commit-28 route bodies: this is a
pure structural refactor (S6-01 facet of the MED-01 finding); no
financial value changes.  The route-level
``tests/test_routes/test_investment.py`` regression suite is the
load-bearing assert-unchanged gate.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from app import ref_cache
from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.pay_period import PayPeriod
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.salary_profile import SalaryProfile
from app.models.transfer_template import TransferTemplate
from app.models.user import UserSettings
from app.services import (
    balance_resolver,
    growth_engine,
    income_service,
    pay_period_service,
)
from app.services.account_projection import is_payroll_deduction_funded
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
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.money import percent_complete, round_money

logger = logging.getLogger(__name__)

_FALLBACK_HORIZON_YEARS = 10

# A period-like row in a projection: a real ``PayPeriod`` (the dashboard's
# future periods) or a synthetic horizon period from
# ``growth_engine.generate_projection_periods`` (the chart fragment).  Both
# expose ``.id`` / ``.start_date`` / ``.end_date`` -- all the projection
# primitives read off a period.
_PeriodList = list[PayPeriod | growth_engine.SyntheticPeriod]


@dataclass(frozen=True)
class _ProjectionContext:
    """Every per-account input the dashboard + growth-chart both consume.

    Built once per request by :func:`_load_projection_context` and
    threaded through the projection primitives and card builders so the
    two public entry points stay thin orchestration.  Bundling these seven
    values into one frozen struct removes the parallel-load duplication
    the dashboard and the chart fragment previously each carried inline
    (S6-01): the entries-aware current balance, the projection inputs,
    and the contribution timeline were resolved the same way in both
    bodies.

    This is a load-once *feed* object: it is resolved in one place and its
    fields fan out to different consumers (``contributions`` -> the growth
    projection; ``deductions`` / ``active_profile`` -> the contribution
    prompt), so consumers read subsets rather than the whole struct as a
    unit.  Note the annual contribution limit is reachable two ways --
    ``params.annual_contribution_limit`` and ``inputs.annual_contribution_limit``
    (copied from params in ``calculate_investment_inputs``); read it from
    one place consistently if this struct is ever tightened.

    Attributes:
        params: The account's :class:`InvestmentParams` row, or ``None``
            when the user has not configured the account.  ``None`` is a
            valid dashboard state (the projection and chart degrade to
            empty containers); the growth-chart fragment guards it out
            earlier and never reaches a context with ``params is None``.
        current_balance: The canonical entries-aware END-of-current-period
            balance (E-25 / CRIT-01 / F-009 / R-1: Commit 8) -- the
            displayed "current balance" tile.
        projection_seed: ``current_balance`` with the current period's own
            transfer contribution removed.  The growth projection seeds
            from this, not ``current_balance``, while still including the
            current period in its window: the engine re-applies that
            contribution for the current period, so subtracting it from the
            seed first leaves it applied exactly once (deep-quality-hunt
            #9).  Only the transfer contribution is removed -- every other
            current-period movement (expenses, deposits) stays, because the
            engine never re-creates those.  It is also the base of the
            chart's cumulative-contribution series.
        inputs: The :class:`InvestmentInputs` the growth engine needs
            (periodic contribution, employer params, annual contribution
            limit, YTD contributions).
        contributions: The per-period contribution timeline (deductions
            plus transfer receipts) fed to ``project_balance``.
        deductions: The raw :class:`PaycheckDeduction` rows targeting
            this account; drives the contribution-prompt decision.
        active_profile: The user's active :class:`SalaryProfile`, or
            ``None``; drives the deduction-path salary-profile link.
    """

    params: InvestmentParams | None
    current_balance: Decimal
    projection_seed: Decimal
    inputs: InvestmentInputs
    contributions: list[growth_engine.ContributionRecord]
    deductions: list[PaycheckDeduction]
    active_profile: SalaryProfile | None


# ── Shared loaders ─────────────────────────────────────────────────


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


def _resolve_current_balance(
    account: Account,
    scenario,
    current_period,
    all_periods: list,
) -> Decimal:
    """Return the canonical entries-aware "current balance" for *account*.

    Routes through :func:`balance_resolver.balances_for` (E-25 /
    CRIT-01 / F-009 / R-1: Commit 8) so the dashboard's "current
    balance" tile cannot disagree with the grid for the same
    account / scenario / period.  This is the END-of-current-period
    balance; the forward-projection seed is derived from it in
    :func:`_load_projection_context` by removing the current period's
    own transfer contribution (deep-quality-hunt #9).  Falls back to
    :attr:`Account.current_anchor_balance` when no scenario is
    configured or the anchor period is unset.
    """
    anchor_balance = account.current_anchor_balance or Decimal("0.00")
    if scenario is None or account.current_anchor_period_id is None:
        return anchor_balance
    balances = balance_resolver.balances_for(
        account, scenario.id, all_periods,
    ).balances
    if current_period is None:
        return anchor_balance
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
    current_balance = _resolve_current_balance(
        account, get_baseline_scenario(user_id), current_period, all_periods,
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
    # Seed for the forward projection: the end-of-current balance with the
    # current period's own transfer contribution removed, so the engine --
    # which re-applies that contribution when its window includes the
    # current period -- does not double-count it (deep-quality-hunt #9).
    # Other current-period balance movements (expenses, deposits) stay in
    # the seed because the engine never re-creates them.
    projection_seed = current_balance - current_period_transfer_contribution(
        acct_contributions, current_period,
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
    )


# ── Shared projection primitives ───────────────────────────────────


def _run_growth_projection(
    ctx: _ProjectionContext, periods: _PeriodList,
) -> list[growth_engine.ProjectedBalance]:
    """Project balances across *periods* from the shared growth context.

    The single home for the ``growth_engine.project_balance`` splat the
    dashboard and the growth-chart fragment both issue with identical
    arguments -- only the period list differs (the dashboard's future
    real periods vs. the chart's synthetic horizon periods).  Callers
    must guard ``ctx.params is not None`` before calling.

    Seeds from ``ctx.projection_seed`` (the START-of-current-period
    balance) and ``ctx.inputs.ytd_contributions_seed`` (YTD strictly
    before the current period), not the end-of-current-period tile, so
    the current period -- which the window includes -- has its growth and
    contribution applied exactly once (deep-quality-hunt #9 / #10).
    """
    return growth_engine.project_balance(
        current_balance=ctx.projection_seed,
        assumed_annual_return=ctx.params.assumed_annual_return,
        periods=periods,
        periodic_contribution=ctx.inputs.periodic_contribution,
        employer_params=ctx.inputs.employer_params,
        annual_contribution_limit=ctx.params.annual_contribution_limit,
        ytd_contributions_start=ctx.inputs.ytd_contributions_seed,
        contributions=ctx.contributions,
    )


def _build_chart_series(
    projection: list[growth_engine.ProjectedBalance],
    periods: _PeriodList,
    seed_balance: Decimal,
) -> tuple[list[str], list[str], list[str]]:
    """Build the chart's ``(labels, balances, contributions)`` string lists.

    The single home for the cumulative-contribution chart loop the
    dashboard and the growth-chart fragment both ran inline with
    different variable names (so R0801 never clustered them).  Labels
    resolve against *periods*; because :func:`growth_engine.project_balance`
    emits exactly one row per input period, every ``pb.period_id`` is
    present in the map and the three lists stay equal length.  The
    contribution series is the running ``seed_balance + cumulative
    employee + employer`` total per period, where ``seed_balance`` is the
    projection's start-of-first-period seed (deep-quality-hunt #9) so the
    invested-principal line and the with-growth line share one origin.
    """
    period_map = {p.id: p for p in periods}
    labels: list[str] = []
    balances: list[str] = []
    contributions: list[str] = []
    cumulative_contrib = Decimal("0")
    for pb in projection:
        period = period_map.get(pb.period_id)
        if period:
            labels.append(period.start_date.strftime("%b %Y"))
        balances.append(str(round_money(pb.end_balance)))
        cumulative_contrib += pb.contribution + pb.employer_contribution
        contributions.append(
            str(round_money(seed_balance + cumulative_contrib))
        )
    return labels, balances, contributions


# ── Dashboard helpers ──────────────────────────────────────────────


def _compute_limit_info(
    investment_params: InvestmentParams | None,
    ytd_contributions: Decimal,
) -> dict | None:
    """Return the contribution-limit card's data, or ``None`` to hide it.

    E-12 / HIGH-06 (Commit 24): the predicate is ``is not None``, not
    Python truthiness.  A stored ``Decimal("0")`` is a meaningful state
    ("user explicitly capped contributions at zero this year") -- the
    card renders ``$0`` with 100% used at any positive YTD, matching
    the growth engine's ``min(period_contribution, 0) = 0`` semantics.
    ``None`` continues to mean "no cap configured" and hides the card.
    """
    if investment_params is None:
        return None
    limit = investment_params.annual_contribution_limit
    if limit is None:
        return None
    if limit > 0:
        # Canonical money.percent_complete (ROUND_HALF_UP, clamped [0, 100],
        # Decimal) -- the one "percent funded" contract the budget-dashboard
        # savings cards and the companion entry view also use, so a fractional
        # YTD rounds the same everywhere instead of truncating only here
        # (deep-quality-hunt #78).  limit > 0 guards the divide, so
        # percent_complete's own target <= 0 branch never collides with the
        # E-12 zero-cap semantics below.
        pct = percent_complete(ytd_contributions, limit)
    elif ytd_contributions > 0:
        # Cap is zero, contributions exist -> 100% used (over).  Kept explicit
        # (not percent_complete, which returns 0 for a <= 0 target) to preserve
        # the E-12 / HIGH-06 zero-cap semantics matching the growth engine's
        # min(contribution, 0) = 0.
        pct = Decimal("100")
    else:
        # Cap and YTD both zero -> 0% used.
        pct = Decimal("0")
    return {
        "limit": limit,
        "ytd": ytd_contributions,
        "pct": pct,
    }


def _compute_default_horizon(user_id: int, all_periods: list) -> int:
    """Return the chart slider's default horizon in years.

    Order of preference: the user's planned retirement year if set,
    else the last projection period's year, else the
    :data:`_FALLBACK_HORIZON_YEARS` constant.  Always >= 1.
    """
    settings = (
        db.session.query(UserSettings)
        .filter_by(user_id=user_id)
        .first()
    )
    if settings and settings.planned_retirement_date:
        return max(
            1, settings.planned_retirement_date.year - date.today().year,
        )
    if all_periods:
        last_period = all_periods[-1]
        return max(1, (last_period.end_date.year - date.today().year) + 1)
    return _FALLBACK_HORIZON_YEARS


def _compute_suggested_contribution(
    investment_params: InvestmentParams,
    ytd_contributions: Decimal,
    all_periods: list,
    current_period: PayPeriod | None,
) -> Decimal:
    """Return the per-period contribution suggestion under the annual limit.

    E-12 / HIGH-06 (Commit 24): same ``is not None`` convention as
    :func:`_compute_limit_info`.  A stored zero cap produces a zero
    suggestion (no contribution within the cap), not the legacy
    $500 fallback that truthiness conflated with the "no cap
    configured" state.  When no cap is configured the suggestion is
    zero (Brokerage-style accounts -- no IRS limit to spread over
    remaining periods).

    ``remaining_periods`` is anchored on ``current_period.start_date`` --
    the SAME boundary the subtracted ``ytd_contributions`` uses
    (:func:`investment_projection._current_year_period_ids`: same
    calendar year, ``<= current_period.start_date``).  So the current
    period is counted once -- in YTD (already contributed) -- and the
    remaining limit is spread over the periods STRICTLY AFTER it.
    Anchoring on ``date.today()`` instead double-counted the current
    period on the single calendar day a period begins
    (``today == period start``), where it landed in BOTH the YTD window
    and the remaining spread (deep-quality-hunt #59).  When there is no
    current period (today falls outside every period, so YTD is zero)
    the boundary falls back to today -- behaviour-identical there, since
    no period can start on a day no period covers.
    """
    if investment_params.annual_contribution_limit is None:
        return Decimal("0")
    boundary = (
        current_period.start_date if current_period is not None
        else date.today()
    )
    remaining_periods = sum(
        1 for p in all_periods
        if p.start_date.year == boundary.year
        and p.start_date > boundary
    )
    remaining_limit = max(
        investment_params.annual_contribution_limit
        - (ytd_contributions or Decimal("0")),
        Decimal("0"),
    )
    return round_money(remaining_limit / max(remaining_periods, 1))


def _compute_employer_per_period(inputs: InvestmentInputs) -> Decimal:
    """Return the per-period employer contribution at the capped employee rate.

    HIGH-07 / F-043 / F-055: feeds the limit-capped contribution to
    :func:`growth_engine.calculate_employer_contribution` so the
    per-period employer card matches the growth chart's employer line
    and the year-end ``year_summary_employer_total`` -- all three
    surfaces read the same capped value.  Returns ``Decimal("0")`` when
    the account configures no employer match.
    """
    capped_contribution = growth_engine.cap_contribution_at_limit(
        inputs.periodic_contribution,
        inputs.annual_contribution_limit,
        inputs.ytd_contributions,
    )
    if not inputs.employer_params:
        return Decimal("0")
    return growth_engine.calculate_employer_contribution(
        inputs.employer_params, capped_contribution,
    )


def _load_transfer_source_accounts(
    user_id: int, exclude_account_id: int,
) -> tuple[list[Account], int | None]:
    """Return source accounts for a contribution transfer plus a default ID.

    The default is the first checking-type account in the list,
    matching the pre-Commit-28 selection order.  Returns
    ``(accounts, default_source_id)`` where ``default_source_id``
    is ``None`` when no checking account is found.
    """
    source_accounts = (
        db.session.query(Account)
        .filter(
            Account.user_id == user_id,
            Account.is_active.is_(True),
            Account.id != exclude_account_id,
        )
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    default_source_id: int | None = None
    for acct in source_accounts:
        if acct.account_type_id == checking_type_id:
            default_source_id = acct.id
            break
    return source_accounts, default_source_id


def _has_active_recurring_transfer_to(account_id: int, user_id: int) -> bool:
    """Return True iff an active recurring transfer targets *account_id*."""
    template = (
        db.session.query(TransferTemplate)
        .filter(
            TransferTemplate.user_id == user_id,
            TransferTemplate.to_account_id == account_id,
            TransferTemplate.is_active.is_(True),
            TransferTemplate.recurrence_rule_id.isnot(None),
        )
        .first()
    )
    return template is not None


# ── Public entry points ────────────────────────────────────────────


def compute_dashboard_data(user_id: int, account: Account) -> dict:
    """Build the full template context for ``investment/dashboard.html``.

    Mirrors :func:`savings_dashboard_service.compute_dashboard_data`:
    plain inputs (user id + the already-ownership-checked account
    instance), plain dict output, no Flask reads.

    The returned dict carries two underscore-prefixed keys
    (``_salary_profile_action`` / ``_active_profile_id``) that the
    route consumes via :func:`flask.url_for` to fill the template's
    ``salary_profile_url`` slot; the underscore prefix marks them as
    internal to the route-service contract.  Every other key is a
    template-facing context value.

    Args:
        user_id: ID of the authenticated user.
        account: The pre-ownership-checked
            :class:`~app.models.account.Account` instance the route
            already loaded via
            :func:`app.utils.auth_helpers.get_or_404`.

    Returns:
        A dict with the template context plus the two route-side
        URL-resolution hints.
    """
    params = _load_investment_params(account.id)
    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)
    ctx = _load_projection_context(
        user_id, account, params, all_periods, current_period,
    )

    return {
        "account": account,
        "params": params,
        "current_balance": ctx.current_balance,
        "periodic_contribution": ctx.inputs.periodic_contribution,
        "employer_contribution_per_period": _compute_employer_per_period(
            ctx.inputs,
        ),
        "employer_params": ctx.inputs.employer_params,
        "limit_info": _compute_limit_info(params, ctx.inputs.ytd_contributions),
        "default_horizon": _compute_default_horizon(user_id, all_periods),
        # Projection + chart series (``projection`` / ``chart_labels`` /
        # ``chart_balances`` / ``chart_contributions``).
        **_project_dashboard_balances(ctx, all_periods, current_period),
        # Contribution-prompt block, including the two underscore-prefixed
        # ``_salary_profile_action`` / ``_active_profile_id`` route hints.
        **_compute_contribution_prompt(
            user_id, account, ctx, all_periods, current_period,
        ),
    }


def _project_dashboard_balances(
    ctx: _ProjectionContext,
    all_periods: list,
    current_period,
) -> dict:
    """Run the dashboard growth projection and build the chart series.

    Returns the four template-context keys (``projection`` plus
    ``chart_labels`` / ``chart_balances`` / ``chart_contributions``) so
    :func:`compute_dashboard_data` can merge them with ``**``.  Empty
    results (no params or no current period) produce the four empty
    containers the template expects, preserving the pre-Commit-28
    default-empty behaviour exactly.
    """
    projection: list = []
    chart_labels: list[str] = []
    chart_balances: list[str] = []
    chart_contributions: list[str] = []

    if ctx.params and current_period:
        future_periods = [
            p for p in all_periods
            if p.period_index >= current_period.period_index
        ]
        projection = _run_growth_projection(ctx, future_periods)
        chart_labels, chart_balances, chart_contributions = _build_chart_series(
            projection, future_periods, ctx.projection_seed,
        )

    return {
        "projection": projection,
        "chart_labels": chart_labels,
        "chart_balances": chart_balances,
        "chart_contributions": chart_contributions,
    }


def _compute_contribution_prompt(
    user_id: int,
    account: Account,
    ctx: _ProjectionContext,
    all_periods: list,
    current_period: PayPeriod | None,
) -> dict:
    """Decide whether to show the "set up contributions" prompt + how.

    Returns the seven template-context keys (the five
    ``show_contribution_prompt`` / ``is_deduction_path`` /
    ``source_accounts`` / ``default_source_id`` / ``suggested_amount``
    values plus the two underscore-prefixed ``_salary_profile_action`` /
    ``_active_profile_id`` route hints) so
    :func:`compute_dashboard_data` can merge them with ``**``.

    Three states:

    * **Hidden** when no params row exists or a deduction / recurring
      transfer is already linked.
    * **Deduction-path** when the account type is payroll-deduction
      funded (S6-04 centralised helper:
      :func:`account_projection.is_payroll_deduction_funded`).  The
      route renders a link to the salary profile -- this helper
      returns the action + profile id so the route can
      ``url_for`` the result.
    * **Transfer-path** otherwise: returns a suggested per-period
      amount, eligible source accounts, and the default source id.
    """
    result = {
        "show_contribution_prompt": False,
        "is_deduction_path": False,
        "source_accounts": [],
        "default_source_id": None,
        "suggested_amount": Decimal("0"),
        "_salary_profile_action": None,
        "_active_profile_id": None,
    }
    if not ctx.params:
        return result

    has_linked_deduction = bool(ctx.deductions)
    has_recurring_transfer = _has_active_recurring_transfer_to(
        account.id, user_id,
    )
    show = not has_linked_deduction and not has_recurring_transfer
    result["show_contribution_prompt"] = show
    if not show:
        return result

    is_deduction_path = is_payroll_deduction_funded(
        account.account_type_id, ref_cache,
    )
    result["is_deduction_path"] = is_deduction_path

    if is_deduction_path:
        if ctx.active_profile is not None:
            result["_salary_profile_action"] = "edit"
            result["_active_profile_id"] = ctx.active_profile.id
        else:
            result["_salary_profile_action"] = "list"
        return result

    # Transfer-path: compute the suggested per-period amount and
    # load eligible source accounts.
    result["suggested_amount"] = _compute_suggested_contribution(
        ctx.params, ctx.inputs.ytd_contributions, all_periods, current_period,
    )
    result["source_accounts"], result["default_source_id"] = (
        _load_transfer_source_accounts(user_id, account.id)
    )
    return result


def compute_growth_chart_data(
    user_id: int,
    account: Account,
    horizon_years: int,
    what_if_raw: str | None,
) -> dict:
    """Build the context for the ``investment/_growth_chart.html`` fragment.

    Args:
        user_id: ID of the authenticated user.
        account: The pre-ownership-checked
            :class:`~app.models.account.Account` instance.
        horizon_years: Slider value, post-validation; the caller is
            expected to clamp to ``[1, 40]`` but this helper does
            so defensively as well.
        what_if_raw: Optional unparsed ``what_if_contribution`` query
            value.  Invalid or negative inputs degrade gracefully to
            the single-line chart; ``Decimal("0")`` is a valid
            growth-only scenario.

    Returns:
        A dict with the chart fragment's context keys.  Returns the
        empty-chart shape when no :class:`InvestmentParams` row
        exists or the engine produced no periods.  Returns ``None``
        instead of the dict when the chart engine could not build any
        periods at all (the caller renders the empty chart in that
        case too).  The caller distinguishes ``None`` from "empty
        chart with what-if fields" by the presence/absence of the
        ``chart_labels`` key.
    """
    params = _load_investment_params(account.id)
    if not params:
        return {
            "chart_labels": [],
            "chart_balances": [],
            "chart_contributions": [],
        }

    horizon_years = max(1, min(horizon_years, 40))

    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)

    # Synthetic future periods for the chosen horizon.
    end_date = date.today() + timedelta(days=horizon_years * 365)
    periods = growth_engine.generate_projection_periods(
        start_date=date.today(),
        end_date=end_date,
    )
    if not periods:
        return {
            "chart_labels": [],
            "chart_balances": [],
            "chart_contributions": [],
        }

    ctx = _load_projection_context(
        user_id, account, params, all_periods, current_period,
    )
    projection = _run_growth_projection(ctx, periods)
    return _growth_chart_context(ctx, periods, projection, what_if_raw)


def _growth_chart_context(
    ctx: _ProjectionContext,
    periods: _PeriodList,
    projection: list[growth_engine.ProjectedBalance],
    what_if_raw: str | None,
) -> dict:
    """Assemble the growth-chart fragment's full template context.

    Builds the committed-projection chart series plus the optional
    what-if overlay and comparison card.  Split out of
    :func:`compute_growth_chart_data` so that orchestrator stays a thin
    load-project-render sequence.
    """
    chart_labels, chart_balances, chart_contributions = _build_chart_series(
        projection, periods, ctx.projection_seed,
    )

    what_if_amount = _parse_what_if(what_if_raw)
    what_if_balances, comparison = _compute_what_if_overlay(
        what_if_amount, ctx, periods, projection,
    )

    return {
        "chart_labels": chart_labels,
        "chart_balances": chart_balances,
        "chart_contributions": chart_contributions,
        "what_if_balances": what_if_balances,
        "what_if_amount": what_if_amount,
        "comparison": comparison,
    }


def _parse_what_if(what_if_raw: str | None) -> Decimal | None:
    """Parse the what-if string, returning ``None`` for invalid / negative input.

    Zero is a valid input ("growth-only scenario: what if I stop
    contributing?").  Anything that fails :class:`Decimal` parsing
    or is strictly negative degrades to ``None`` -- the caller
    interprets ``None`` as "no what-if overlay" and renders the
    single-line chart.
    """
    if not what_if_raw:
        return None
    try:
        value = Decimal(what_if_raw)
    except (InvalidOperation, ValueError):
        return None
    if value < Decimal("0"):
        return None
    return value


def _compute_what_if_overlay(
    what_if_amount: Decimal | None,
    ctx: _ProjectionContext,
    periods: _PeriodList,
    projection: list[growth_engine.ProjectedBalance],
) -> tuple[list[str], dict | None]:
    """Run the what-if projection (when an amount is supplied) plus comparison.

    Returns:
        ``(what_if_balances, comparison)`` where ``what_if_balances``
        is a list of string-formatted end balances (one per period)
        and ``comparison`` is ``None`` or a 5-key dict describing
        committed-vs-what-if end balances.
    """
    if what_if_amount is None or not periods:
        return [], None

    # contributions=None forces the engine to use periodic_contribution
    # for every period (a flat-rate what-if).  Employer match is
    # recalculated automatically because the per-period loop passes
    # each period's contribution to ``calculate_employer_contribution``.
    what_if_projection = growth_engine.project_balance(
        current_balance=ctx.projection_seed,
        assumed_annual_return=ctx.params.assumed_annual_return,
        periods=periods,
        periodic_contribution=what_if_amount,
        employer_params=ctx.inputs.employer_params,
        annual_contribution_limit=ctx.params.annual_contribution_limit,
        ytd_contributions_start=ctx.inputs.ytd_contributions_seed,
        contributions=None,
    )

    what_if_balances = [
        str(round_money(pb.end_balance))
        for pb in what_if_projection
    ]

    comparison = None
    if projection and what_if_projection:
        committed_end = round_money(projection[-1].end_balance)
        whatif_end = round_money(what_if_projection[-1].end_balance)
        difference = round_money(whatif_end - committed_end)
        comparison = {
            "committed_end": committed_end,
            "whatif_end": whatif_end,
            "difference": difference,
            "is_positive": difference > Decimal("0"),
            "is_zero": difference == Decimal("0"),
        }
    return what_if_balances, comparison
