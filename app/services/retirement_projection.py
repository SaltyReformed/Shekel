"""
Shekel Budget App -- Retirement Account Projection

The per-account forward projection machinery for the retirement
dashboard, split out of :mod:`app.services.retirement_dashboard_service`
when the readiness rebuild (P1) pushed that module past the 1000-line
ceiling.  Mirrors the :mod:`app.services.year_end_summary_service`
package precedent: a large service surface decomposes into cohesive
single-responsibility modules.

Given a projection context (accounts + period / horizon inputs) this
projects each retirement / investment account forward to the retirement
date via ``growth_engine.project_balance``, returning per-account dicts
carrying the displayed balance, the projected balance, the per-period
projection rows (summed into the readiness "your path" chart), the
per-account contribution facts, and the traditional/return metadata the
gap calculator and templates read.

All functions accept plain data / ORM instances and return plain data.
No Flask imports.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.pay_period import PayPeriod
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.salary_profile import SalaryProfile
from app.models.transaction import Transaction
from app.services import (
    account_service,
    balance_at,
    growth_engine,
    income_service,
    pension_calculator,
)
from app.services.investment_projection import adapt_deductions
from app.services.projection_inputs import (
    build_investment_projection_inputs,
    load_active_deductions_for_accounts,
    load_shadow_income_contributions_for_accounts,
)
from app.services.balance_at import BalanceContext
from app.utils.money import round_money


@dataclass(frozen=True)
class _RetirementProjectionContext:  # pylint: disable=too-many-instance-attributes
    """Read-only inputs shared by the per-account projection helpers.

    Built by :func:`build_projection_context` and threaded through
    :func:`_load_projection_batch`, :func:`_resolve_displayed_balances`,
    and :func:`_project_one_account` so the projection takes one
    parameter instead of eight.  All fields are inputs (no derived
    state); the once-loaded batch data lives in :class:`_ProjectionBatch`.

    Pylint: ``too-many-instance-attributes`` (8/7) -- suppressed because
    this is a cohesive read-only input bundle whose whole purpose is to
    collapse eight independent projection inputs into one parameter (the
    alternative is threading all eight through every projection helper).
    Every field is a distinct input -- identity, the account set, the two
    parts of the period calendar, the horizon, the pre-tax type set, the
    slider override, and the P1b employer-base resolver -- so splitting the
    bundle would fragment one concept for no design gain, mirroring the
    ``growth_engine.ProjectedBalance`` value-record precedent.

    Attributes:
        user_id: The authenticated user's ID.
        accounts: The active retirement / investment accounts to project.
        all_periods: Every pay period for the user.
        current_period: The current pay period, or ``None``.
        planned_retirement_date: The horizon the synthetic projection
            periods run to, or ``None`` (no horizon -> remaining real
            periods only).
        traditional_type_ids: Account-type IDs that are pre-tax (drives
            each projection's ``is_traditional`` flag).
        return_rate_override: Optional slider-supplied annual return that
            overrides each account's stored ``assumed_annual_return``.
        employer_salary_basis: Optional ``period -> Decimal gross_biweekly``
            resolver (P1b / fork F3) grown with the P1a salary path,
            forwarded to ``growth_engine.project_balance`` so the
            employer-contribution base tracks the projected salary rather
            than freezing at today's gross; ``None`` when there is no
            salary profile or no horizon (constant-base fallback).
    """

    user_id: int
    accounts: list[Account]
    all_periods: list[PayPeriod]
    current_period: PayPeriod | None
    planned_retirement_date: date | None
    traditional_type_ids: frozenset[int]
    return_rate_override: Decimal | None
    employer_salary_basis: Callable | None


@dataclass(frozen=True)
class _ProjectionBatch:
    """Per-request data loaded once and reused across every account.

    Built by :func:`load_projection_batch` before the per-account loop
    so the shared deduction / contribution / params / salary / balance
    queries run a single time rather than once per account.  Every FIELD is
    date-independent -- the projection's period axis is a separate argument to
    :func:`project_accounts_with_batch` -- so the P2b retire-later probes load
    ONE batch and re-project it against many candidate horizons without
    repeating any query.  The one axis-dependent value the projection needs is
    a keyed MEMO rather than a field, for exactly that reason (see
    :attr:`seed_memo` below).

    Attributes:
        deductions_by_account: Active paycheck deductions keyed by
            account ID.
        contributions: Shadow-income contribution transactions across all
            projected accounts (filtered per account in the loop).
        params_by_account: :class:`InvestmentParams` keyed by account ID
            (accounts with no params row are absent) -- one ``IN`` query
            replacing the pre-P2 per-account ``first()`` lookups.
        salary_gross_biweekly: The raise-aware engine gross-biweekly used
            as the employer-match cap basis.
        balance_map: The model-from-anchor END-of-current-period balance
            keyed by account ID -- the DISPLAYED current balance (and the
            weight in ``compute_slider_defaults``' return-rate average),
            read from the :mod:`app.services.balance_at` seam so it agrees
            with the /savings net-worth tile and the /investment dashboard
            (an account anchored in the past shows its modeled market
            value, not the flat cash-basis contribution total).
        balance_ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.  It is HERE
            rather than rebuilt per projection because it memoizes each loan's
            resolution and each account's walk for the pass, and the P2b
            retire-later probes call :func:`project_accounts_with_batch` once
            per candidate horizon: rebuilding it there would throw those memos
            away on every probe.  It is date-independent in the sense this
            bundle means -- its ``as_of`` is the request's today, which no
            candidate horizon changes.

    **The forward projection's SEED is deliberately NOT here** (plan step
    X-g2b, rulings R-AB / R-AE).  It is read the day BEFORE the projection
    window opens, so it is a function of the AXIS -- and the axis is this
    bundle's whole point of NOT being one.  It used to sit here as
    ``seed_map``, a current-period read that made the date-independence claim
    above false in spirit and forced a compensating subtraction at every use;
    :func:`project_accounts_with_batch` now resolves it once per axis instead.
    """

    deductions_by_account: dict[int, list[PaycheckDeduction]]
    contributions: list[Transaction]
    params_by_account: dict[int, InvestmentParams]
    salary_gross_biweekly: Decimal
    balance_map: dict[int, Decimal]
    balance_ctx: BalanceContext
    seed_memo: "dict[tuple[date, tuple[int, ...]], dict[int, Decimal]]" = (
        field(default_factory=dict, repr=False, compare=False)
    )


@dataclass(frozen=True)
class _AccountProjectionResult:
    """The projecting-branch outputs for one account.

    Returned by :func:`_run_account_projection` so :func:`_project_one_account`
    stays within the local-variable budget: the projecting branch's own
    intermediates (seed, inputs, per-period rows) live inside the helper
    and only these five results cross back out.

    Attributes:
        projected_balance: The account's balance at the retirement horizon.
        effective_return: The annual return the projection applied (stored
            rate or slider override), or ``None`` for a non-projecting
            account.
        projection_rows: The per-period
            :class:`~app.services.growth_engine.ProjectedBalance` list
            (aligned 1:1 with the synthetic periods), empty when the
            account does not project.
        employee_per_period: The capped current-period employee
            contribution fact.
        employer_per_period: The current-period employer contribution fact
            (at today's gross).
    """

    projected_balance: Decimal
    effective_return: Decimal | None
    projection_rows: list
    employee_per_period: Decimal
    employer_per_period: Decimal


def build_employer_salary_basis(
    salary_profiles: list[SalaryProfile],
    planned_retirement_date: date | None,
    merit_horizon_years: int,
) -> Callable | None:
    """Build the per-period employer-contribution gross basis (P1b / F3).

    Grows the employer-contribution base with the SAME P1a salary path the
    pension / income-target projection uses: for each projected year the
    annual salary is divided by the primary profile's pay-periods-per-year
    and quantized to cents (house money rules), and the returned resolver
    maps a projection period to its year's gross biweekly.  Periods whose
    year falls outside the projected range (defensive only -- the synthetic
    periods run today..retirement, exactly the projected span) clamp to the
    nearest projected year's gross.

    Returns ``None`` when there is no salary profile or no horizon, so
    ``growth_engine.project_balance`` falls back to the constant
    ``employer_params["gross_biweekly"]`` -- the behavior every other
    engine consumer keeps.

    Args:
        salary_profiles: The user's active salary profiles (the first is
            the primary profile whose gross drives the employer base).
        planned_retirement_date: The projection horizon, or ``None``.
        merit_horizon_years: The merit-raise horizon forwarded to the
            salary projection.

    Returns:
        A ``period -> Decimal gross_biweekly`` callable, or ``None``.
    """
    if not salary_profiles or planned_retirement_date is None:
        return None

    profile = salary_profiles[0]
    pay_periods_per_year = profile.pay_periods_per_year or 26
    salary_by_year = pension_calculator.project_profile_salaries(
        profile, date.today().year, planned_retirement_date.year,
        merit_horizon_years,
    )
    if not salary_by_year:
        return None

    gross_by_year = {
        year: round_money(salary / pay_periods_per_year)
        for year, salary in salary_by_year
    }
    min_year = min(gross_by_year)
    max_year = max(gross_by_year)

    def _resolver(period):
        clamped_year = min(max(period.start_date.year, min_year), max_year)
        return gross_by_year[clamped_year]

    return _resolver


def build_projection_context(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    user_id: int,
    all_periods: list[PayPeriod],
    current_period: PayPeriod | None,
    planned_retirement_date: date | None,
    return_rate_override: Decimal | None,
    employer_salary_basis: Callable | None,
) -> _RetirementProjectionContext:
    """Load the retirement accounts and assemble the projection context.

    Queries the user's active retirement / investment accounts and the
    pre-tax (traditional) account-type IDs, then bundles them with the
    pay-period and horizon inputs into the read-only context the
    projection helpers consume.

    Pylint: ``too-many-arguments`` (6/5) / ``too-many-positional-arguments``
    (6/5) -- suppressed because these six are heterogeneous, independently
    varying projection inputs (identity, the period calendar's two parts,
    the horizon, the slider override, the employer-base resolver), not a
    cohesive concept.  Bundling the period calendar back into a ``_CurrentPay``
    snapshot here would reintroduce a cross-module import cycle with
    ``retirement_dashboard_service`` (which owns that snapshot); passing the
    two period fields directly keeps this module a projection leaf.

    Args:
        user_id: The authenticated user's ID.
        all_periods: Every pay period for the user.
        current_period: The current pay period, or ``None``.
        planned_retirement_date: The projection horizon, or ``None``.
        return_rate_override: Optional slider-supplied annual return.
        employer_salary_basis: Optional per-period gross resolver (P1b /
            fork F3) grown with the P1a salary path; ``None`` keeps the
            constant employer-contribution base.

    Returns:
        A :class:`_RetirementProjectionContext` ready for
        :func:`project_retirement_accounts`.
    """
    retirement_types = (
        account_service.list_retirement_investment_account_types()
    )
    retirement_type_ids = {rt.id for rt in retirement_types}
    traditional_type_ids = frozenset(
        rt.id for rt in retirement_types if rt.is_pretax
    )
    accounts = (
        db.session.query(Account)
        .filter(
            Account.user_id == user_id,
            Account.account_type_id.in_(retirement_type_ids),
            Account.is_active.is_(True),
        )
        .all()
    )
    return _RetirementProjectionContext(
        user_id=user_id,
        accounts=accounts,
        all_periods=all_periods,
        current_period=current_period,
        planned_retirement_date=planned_retirement_date,
        traditional_type_ids=traditional_type_ids,
        return_rate_override=return_rate_override,
        employer_salary_basis=employer_salary_basis,
    )


def project_retirement_accounts(
    ctx: _RetirementProjectionContext,
) -> list[dict]:
    """Project each retirement / investment account forward to retirement.

    Loads the shared per-request projection inputs once
    (:func:`load_projection_batch`), resolves the period axis from the
    context's horizon, then projects each account via
    :func:`project_accounts_with_batch`.

    Args:
        ctx: The read-only projection context (accounts + period/horizon
            inputs).

    Returns:
        A list of per-account projection dicts (see
        :func:`_project_one_account` for the keys).
    """
    batch = load_projection_batch(ctx)
    return project_accounts_with_batch(
        ctx, batch, _resolve_projection_axis(ctx),
    )


def project_accounts_with_batch(
    ctx: _RetirementProjectionContext,
    batch: _ProjectionBatch,
    projection_periods: list,
) -> list[dict]:
    """Project every account over an explicit period axis.

    The probe-friendly core of :func:`project_retirement_accounts`: the
    P2b retire-later solver calls this directly with one batch and a
    different *projection_periods* axis (plus a horizon-shifted context)
    per probe, so no query re-runs between probes.

    Args:
        ctx: The read-only projection context.
        batch: The date-independent per-request inputs from
            :func:`load_projection_batch`.
        projection_periods: The ordered period axis to project over (an
            empty list leaves every account non-projecting).

    Returns:
        A list of per-account projection dicts (see
        :func:`_project_one_account` for the keys).
    """
    seeds = _resolve_seed_balances(ctx, batch, projection_periods)
    return [
        _project_one_account(acct, ctx, batch, projection_periods, seeds)
        for acct in ctx.accounts
    ]


def _resolve_projection_axis(ctx: _RetirementProjectionContext) -> list:
    """Resolve the default period axis for the context's horizon.

    Synthetic biweekly periods from today to the planned retirement date
    when a horizon is set; otherwise the remaining REAL periods from the
    current period onward (the pre-P2 fallback), and an empty list when
    there is neither.

    Args:
        ctx: The read-only projection context.

    Returns:
        The ordered list of periods to project over (possibly empty).
    """
    if ctx.planned_retirement_date:
        return growth_engine.generate_projection_periods(
            start_date=date.today(),
            end_date=ctx.planned_retirement_date,
        )
    if ctx.current_period:
        return [
            p for p in ctx.all_periods
            if p.period_index >= ctx.current_period.period_index
        ]
    return []


def load_projection_batch(
    ctx: _RetirementProjectionContext,
) -> _ProjectionBatch:
    """Load the per-request data shared across all account projections.

    Runs the deduction, shadow-income, investment-params, salary-gross,
    and entries-aware balance queries a single time (F-22 / Commit 18 for
    the shared batch loaders) so the per-account loop does no repeated
    I/O.  Everything loaded here is date-independent, so the P2b probes
    reuse one batch across every candidate retirement date.

    Args:
        ctx: The read-only projection context.

    Returns:
        A :class:`_ProjectionBatch` with all shared inputs.
    """
    account_ids = [a.id for a in ctx.accounts]
    period_ids = [p.id for p in ctx.all_periods]

    # F-22 / Commit 18: shared batch loaders replace the filter-shape
    # duplicate that previously lived inline here and in
    # savings_dashboard_service / year_end_summary_service.
    deductions_by_account = load_active_deductions_for_accounts(
        ctx.user_id, account_ids,
    )
    contributions = load_shadow_income_contributions_for_accounts(
        account_ids, period_ids,
    )

    # One IN query for the params rows (P2: replaces the per-account
    # ``first()`` inside the loop so probes never re-query).  Guarded so an
    # account-less user issues no ``IN ()`` against PostgreSQL.
    params_by_account: dict[int, InvestmentParams] = {}
    if account_ids:
        for params in (
            db.session.query(InvestmentParams)
            .filter(InvestmentParams.account_id.in_(account_ids))
            .all()
        ):
            params_by_account[params.account_id] = params

    # F-20 / MED-06 / F-032: raise-aware engine gross-biweekly (not the
    # off-engine ``annual_salary / pay_periods_per_year`` recompute that
    # dropped any applicable SalaryRaise); feeds the employer-match cap.
    salary_gross_biweekly = income_service.get_current_gross_biweekly(
        ctx.user_id,
    )

    # The displayed per-account balance is the model-from-anchor value at the
    # current period's end (so it agrees with /savings and the /investment
    # dashboard).  The forward projection seeds from the same curve, read a day
    # before its own AXIS opens, which is why the seed is not a field of this
    # bundle -- see :class:`_ProjectionBatch`.  Both read the one baseline
    # scenario, resolved once here and shared for the whole pass.
    balance_ctx = BalanceContext.build(ctx.user_id)
    balance_map = _resolve_displayed_balances(ctx, balance_ctx)
    return _ProjectionBatch(
        deductions_by_account=deductions_by_account,
        contributions=contributions,
        params_by_account=params_by_account,
        salary_gross_biweekly=salary_gross_biweekly,
        balance_map=balance_map,
        balance_ctx=balance_ctx,
    )


def _resolve_displayed_balances(
    ctx: _RetirementProjectionContext, balance_ctx: BalanceContext,
) -> dict[int, Decimal]:
    """Resolve each account's DISPLAYED current balance.

    The model-from-anchor balance from the :mod:`app.services.balance_at` seam
    (:func:`~app.services.balance_at.build_maps`) read at the current period, so
    the per-account "current balance" (and the weight in
    ``compute_slider_defaults``' return-rate average) matches the /savings
    net-worth tile and the /investment dashboard (the cross-page invariant: an
    account anchored in the past shows its modeled market value, not the flat
    cash-basis contribution total).

    **It used to return a second map beside it** -- the pre-growth cash basis
    the forward projection seeded from.  That seed is a function of the
    projection AXIS, not of the batch (plan step X-g2b, ruling R-AB), so it
    moved to :func:`_resolve_seed_balances`, which the axis-taking entry calls.

    Empty when there is no scenario or no periods (each account then falls back
    to its anchor balance in :func:`_project_one_account`).

    Args:
        ctx: The read-only projection context.
        balance_ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext` (its
            scenario may be ``None``).

    Returns:
        ``{account_id: displayed balance}``.
    """
    # The seam's own precondition, read off the context rather than spelled
    # out here (plan step X-t2, finding N-107).
    if not balance_ctx.has_baseline or not ctx.all_periods:
        return {}
    return _pick_current_period_balances(
        ctx, balance_at.build_maps(ctx.accounts, balance_ctx, ctx.all_periods),
    )


def _resolve_seed_balances(
    ctx: _RetirementProjectionContext,
    batch: "_ProjectionBatch",
    projection_periods: list,
) -> dict[int, Decimal]:
    """Resolve each account's balance the day BEFORE the window opens.

    Ruling R-AB's seed, read once per AXIS rather than once per batch.  Every
    event inside the window is then the growth engine's to apply and none of
    them is in the seed, which is what let the
    ``current_period_transfer_contribution`` subtraction this projection used to
    carry DELETE rather than be ported (deep-quality-hunt #14): the compensator
    existed because the seed was read at the current period's END while the
    window opened at that period's START, so a recorded contribution on the
    window's first payday was in the seed AND re-applied by the engine.

    **Nothing is filtered out of it** (ruling R-AE).  The window opens strictly
    after the seed's date, so the engine cannot re-grow a day the seed already
    grew -- and filtering the modelled return, the correction the earlier design
    needed, would instead drop every cent earned since the account's last
    balance assertion.

    **The consequence, stated so it is not discovered later:** a recorded
    NON-contribution row dated inside the window (a withdrawal next week) leaves
    the seed and the engine never re-creates it, so it drops out of the
    projection.  Today it is smuggled in through the seed and then compounded
    for the whole window, which is wrong in the other direction.  It costs
    ``$0.00`` on both real databases -- all three investment accounts hold zero
    transaction rows (ruling R-R's measurement).

    **Memoized on the batch by (SEED DATE, account set).**  The P2b retire-later probes call
    :func:`project_accounts_with_batch` once per candidate horizon and every one
    of those axes opens at today, so without the memo each account is re-folded
    once per probe: measured when this was written, the /retirement lever page
    went 377 ms -> 682 ms without it.  The key is the DATE rather than a flag,
    so an axis that genuinely opens somewhere else still gets its own seed.

    Args:
        ctx: The read-only projection context.
        batch: The per-request bundle -- its ``balance_ctx`` scopes the read and
            memoizes the pass's loan resolutions, its ``seed_memo`` holds one
            map per distinct seed date.
        projection_periods: The axis about to be projected.  Its FIRST period's
            ``start_date`` is when the window opens.

    Returns:
        ``{account_id: seed balance}``; empty when there is no scenario, no
        periods, or no axis (each account then falls back to its anchor).
    """
    # The seam's own precondition (plan step X-t2, finding N-107).
    if (not batch.balance_ctx.has_baseline
            or not ctx.all_periods
            or not projection_periods):
        return {}
    # Keyed on the account SET as well as the date: the map is a function of
    # both, and ``ctx`` arrives separately from ``batch``, so a caller reusing
    # one batch across contexts with different account sets would otherwise get
    # a hit for the first set and silently fall back to the stored anchor for
    # every account the second set added.  Not reachable in-tree today (the P2b
    # probes rebuild the context with ``replace``, which preserves ``accounts``)
    # -- which is exactly why the key is widened rather than the invariant
    # documented.
    key = (
        projection_periods[0].start_date - timedelta(days=1),
        tuple(acct.id for acct in ctx.accounts),
    )
    if key not in batch.seed_memo:
        batch.seed_memo[key] = {
            acct.id: balance_at.balance_at(acct, batch.balance_ctx, key[0])
            for acct in ctx.accounts
            if acct.current_anchor_period_id is not None
        }
    return batch.seed_memo[key]


def _pick_current_period_balances(
    ctx: _RetirementProjectionContext,
    maps_by_account: dict[int, dict[int, Decimal]],
) -> dict[int, Decimal]:
    """Pick each account's current-period balance from its per-period map.

    The current-period extractor for the model-from-anchor map
    :func:`_resolve_displayed_balances` builds: a per-account
    ``period_id -> balance`` map read at the current period.  It served a second
    (cash-basis) map until plan step X-g2b made the forward projection's seed a
    dated read rather than a period one.  An account absent from
    *maps_by_account* (no anchor period, so the seam / accessor omitted it) or
    with no current period falls back to its stored anchor balance --
    ``current_anchor_balance`` is NOT NULL, so no ``or Decimal("0")`` guard is
    needed (the prior truthiness was dead defence on a stored zero).

    Args:
        ctx: The read-only projection context.
        maps_by_account: ``{account_id: period_id -> Decimal}`` for the
            accounts the producer returned a map for.

    Returns:
        A mapping of account ID to its current-period balance.
    """
    result: dict[int, Decimal] = {}
    for acct in ctx.accounts:
        anchor = acct.current_anchor_balance
        per_period = maps_by_account.get(acct.id)
        if per_period is not None and ctx.current_period is not None:
            result[acct.id] = per_period.get(ctx.current_period.id, anchor)
        else:
            result[acct.id] = anchor
    return result


def _run_account_projection(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    acct: Account,
    ctx: _RetirementProjectionContext,
    batch: _ProjectionBatch,
    params: InvestmentParams,
    projection_periods: list,
    seed: Decimal,
) -> _AccountProjectionResult:
    """Run the forward growth projection for one projecting account.

    Builds the account's investment projection inputs from the batch's
    shared data and runs ``growth_engine.project_balance`` over
    *projection_periods* from the supplied *seed* (passing the P1b per-period
    employer salary basis).

    Also computes the per-account contribution facts (capped current-period
    employee amount and its employer match at today's gross, mirroring the
    investment dashboard's HIGH-07 per-period card).

    Pylint: ``too-many-arguments`` (6/5) / ``too-many-positional-arguments``
    (6/5) -- the seed joined the five because ruling R-AB makes it a function
    of the AXIS rather than of the batch, and it is resolved once per axis by
    :func:`_resolve_seed_balances` rather than per account here.  Bundling it
    back into ``batch`` is precisely what that ruling undid.

    Args:
        acct: The account to project.
        ctx: The read-only projection context.
        batch: The shared per-request projection inputs.
        params: The account's :class:`InvestmentParams` (non-None).
        projection_periods: The non-empty period axis to project over.
        seed: The account's balance the day before the window opens
            (:func:`_resolve_seed_balances`).

    Returns:
        An :class:`_AccountProjectionResult`.
    """
    acct_contributions = [
        t for t in batch.contributions if t.account_id == acct.id
    ]
    adapted_deductions = adapt_deductions(
        batch.deductions_by_account.get(acct.id, []),
    )
    inputs = build_investment_projection_inputs(
        params, adapted_deductions, acct_contributions,
        ctx.all_periods, ctx.current_period, batch.salary_gross_biweekly,
    )
    annual_return = (
        ctx.return_rate_override
        if ctx.return_rate_override is not None
        else params.assumed_annual_return
    )
    proj = growth_engine.project_balance(
        current_balance=seed,
        assumed_annual_return=annual_return,
        periods=projection_periods,
        periodic_contribution=inputs.periodic_contribution,
        employer_params=inputs.employer_params,
        annual_contribution_limit=inputs.annual_contribution_limit,
        ytd_contributions_start=inputs.ytd_contributions_seed,
        salary_basis=ctx.employer_salary_basis,
    )
    # P1c per-account contribution facts: the capped current-period employee
    # amount and its employer match at today's gross.
    employee_per_period = growth_engine.cap_contribution_at_limit(
        inputs.periodic_contribution,
        inputs.annual_contribution_limit,
        inputs.ytd_contributions_seed,
    )
    return _AccountProjectionResult(
        projected_balance=proj[-1].end_balance if proj else seed,
        effective_return=annual_return,
        projection_rows=proj,
        employee_per_period=employee_per_period,
        employer_per_period=growth_engine.calculate_employer_contribution(
            inputs.employer_params, employee_per_period,
        ),
    )


def _project_one_account(
    acct: Account,
    ctx: _RetirementProjectionContext,
    batch: _ProjectionBatch,
    projection_periods: list,
    seeds: dict[int, Decimal],
) -> dict:
    """Project a single account forward over the given period axis.

    Delegates the projecting branch to :func:`_run_account_projection`; an
    account with no :class:`InvestmentParams` or no projectable periods
    keeps its displayed balance as the projected balance and contributes
    nothing per period.  Contribution linkage is computed independently of
    whether the account projects, so a params-less account still reports
    "none linked" when no deduction and no transfer feed it (finding D2's
    design half).

    Args:
        acct: The account to project.
        ctx: The read-only projection context.
        batch: The shared per-request projection inputs.
        projection_periods: The ordered period axis to project over (an
            empty list leaves the account non-projecting).
        seeds: Ruling R-AB's ``{account_id: balance}`` the day before the
            window opens (:func:`_resolve_seed_balances`), resolved once for
            this axis.

    Returns:
        A projection dict with keys ``account``, ``current_balance``,
        ``projected_balance``, ``is_traditional``, ``annual_return_rate``,
        ``projection_rows`` (the per-period rows, empty for a non-projecting
        account), ``employee_per_period`` / ``employer_per_period`` (the
        contribution facts for the accounts table), ``none_linked``, and
        ``annual_contribution_limit`` (the account's stored annual cap, or
        ``None`` for no-params / uncapped accounts -- the P2a headroom
        input).
    """
    params = batch.params_by_account.get(acct.id)
    balance = batch.balance_map.get(acct.id, acct.current_anchor_balance)
    acct_deductions = batch.deductions_by_account.get(acct.id, [])
    acct_contributions = [
        t for t in batch.contributions if t.account_id == acct.id
    ]
    none_linked = not acct_deductions and not acct_contributions

    if params is not None and projection_periods:
        result = _run_account_projection(
            acct, ctx, batch, params, projection_periods,
            seeds.get(acct.id, acct.current_anchor_balance),
        )
    else:
        result = _AccountProjectionResult(
            projected_balance=balance,
            effective_return=None,
            projection_rows=[],
            employee_per_period=Decimal("0"),
            employer_per_period=Decimal("0"),
        )

    return {
        "account": acct,
        "current_balance": balance,
        "projected_balance": result.projected_balance,
        "is_traditional": acct.account_type_id in ctx.traditional_type_ids,
        "annual_return_rate": result.effective_return,
        # P1c: per-period rows (summed into the "your path" chart series)
        # and the per-account contribution facts + none-linked flag.
        "projection_rows": result.projection_rows,
        "employee_per_period": result.employee_per_period,
        "employer_per_period": result.employer_per_period,
        "none_linked": none_linked,
        # P2a headroom input: the stored annual cap (None = no params row
        # or an uncapped account -- either way no finite limit is known).
        "annual_contribution_limit": (
            params.annual_contribution_limit if params is not None else None
        ),
    }
