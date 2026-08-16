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
from app.services import (
    account_service,
    balance_at,
    cash_ledger,
    growth_engine,
    income_service,
    pension_calculator,
)
from app.services.investment_projection import (
    ShadowContributions,
    adapt_deductions,
)
from app.services.projection_inputs import (
    build_investment_projection_inputs,
    load_active_deductions_for_accounts,
    load_shadow_income_contributions_for_accounts,
)
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import PeriodWindow
from app.utils.money import round_money


@dataclass(frozen=True)
class _RetirementProjectionContext:  # pylint: disable=too-many-instance-attributes
    """Read-only inputs shared by the per-account projection helpers.

    Built by :func:`build_projection_context` and threaded through
    :func:`_load_projection_batch`, :func:`_resolve_displayed_balances`,
    and :func:`_project_one_account` so the projection takes one
    parameter instead of eight.  All fields are inputs (no derived
    state); the once-loaded batch data lives in :class:`_ProjectionBatch`.

    **The read pass is the FIRST of those inputs, and it replaced the bare
    ``user_id`` this bundle used to carry** (plan step C2-f2d-1, ledger row
    **P43**; what the two-pass render cost is stated once, in
    ``tests/test_arch/test_one_read_pass_per_render.py``).  A producer holding
    an id could build its own
    :class:`~app.services.balance_at.BalanceContext`, and this module did.
    Carrying the pass rather than the id also removes the id as a second
    spelling of the owner: it is ``balance_ctx.user_id`` everywhere, so a caller
    cannot hand this bundle a pass for one owner while its queries scope to
    another.

    Pylint: ``too-many-instance-attributes`` (8/7) -- suppressed because
    this is a cohesive read-only input bundle whose whole purpose is to
    collapse eight independent projection inputs into one parameter (the
    alternative is threading all eight through every projection helper).
    Every field is a distinct input -- the read pass, the account set, the two
    parts of the period calendar, the horizon, the pre-tax type set, the
    slider override, and the P1b employer-base resolver -- so splitting the
    bundle would fragment one concept for no design gain, mirroring the
    ``growth_engine.ProjectedBalance`` value-record precedent.

    Attributes:
        balance_ctx: The read pass this projection runs in -- the owner, the
            baseline scenario, the pinned ``as_of``, and the memos that resolve
            each loan and derive the pay calendar exactly ONCE for the whole
            render.  Supplied by the caller (ultimately by the route); this
            module builds none, which is what keeps a render to one pass.
        accounts: The active retirement / investment accounts to project.
        all_periods: Every pay period for the user.
        current_period: The current pay period, or ``None``.
        planned_retirement_date: The horizon the projection axis runs to, or
            ``None`` -- in which case it runs to the last day the owner's saved
            schedule reaches (:func:`resolve_projection_axis`).
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

    balance_ctx: BalanceContext
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
        contributions: The priced shadow-income contributions across all
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

    **The READ PASS is deliberately not here, since plan step C2-f2d-1.**  It
    was, on the ground that rebuilding it per probe would throw the
    loan-resolution memos away -- which is true, and which needs no field here
    to satisfy: the pass is an INPUT, so it rides on the input bundle
    (:class:`_RetirementProjectionContext`) that every function taking this one
    already takes beside it.  **The P2b probes do NOT reuse ``ctx`` by identity
    -- they ``dataclasses.replace`` it per candidate horizon -- and the pass
    rides through that replace untouched**, which is the whole reason moving it
    off this batch is safe.  Holding it in both places would be one fact under
    two keys, and the two could not even be checked against each other: a batch
    built for one pass is indistinguishable from a batch built for another.
    (Ruling R-AZ is about a PUBLISHED key with no consumer, which this is not;
    the rule here is the plainer one that a fact has one home.)

    **The forward projection's SEED is deliberately NOT here** (plan step
    X-g2b, rulings R-AB / R-AE).  It is read the day BEFORE the projection
    window opens, so it is a function of the AXIS -- and the axis is this
    bundle's whole point of NOT being one.  It used to sit here as
    ``seed_map``, a current-period read that made the date-independence claim
    above false in spirit and forced a compensating subtraction at every use;
    :func:`project_accounts_with_batch` now resolves it once per axis instead.
    """

    deductions_by_account: dict[int, list[PaycheckDeduction]]
    contributions: ShadowContributions
    params_by_account: dict[int, InvestmentParams]
    salary_gross_biweekly: Decimal
    balance_map: dict[int, Decimal]
    seed_memo: (
        "dict["
        "tuple[date, tuple[int, ...], int, int | None, date], "
        "dict[int, Decimal]]"
    ) = (
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
            (aligned 1:1 with the projection axis, and each row CARRIES its
            own period since plan step C2-e), empty when the account does not
            project.
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
    year falls outside the projected range (defensive only -- the axis runs
    from the pass's as_of to retirement, exactly the projected span) clamp to
    the nearest projected year's gross.

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
    balance_ctx: BalanceContext,
    all_periods: list[PayPeriod],
    current_period: PayPeriod | None,
    planned_retirement_date: date | None,
    return_rate_override: Decimal | None,
    employer_salary_basis: Callable | None,
) -> _RetirementProjectionContext:
    """Load the retirement accounts and assemble the projection context.

    Queries the pass owner's active retirement / investment accounts and the
    pre-tax (traditional) account-type IDs, then bundles them with the
    pay-period and horizon inputs into the read-only context the
    projection helpers consume.

    **The owner comes off the read pass** (plan step C2-f2d-1).  This took a
    bare ``user_id`` and the module then built its own pass from it, which is
    how ``/retirement`` came to hold two; taking the pass instead means the
    owner is stated once for the whole projection and a caller cannot pair one
    owner's pass with another's account query.

    Pylint: ``too-many-arguments`` (6/5) / ``too-many-positional-arguments``
    (6/5) -- suppressed because these six are heterogeneous, independently
    varying projection inputs (the read pass, the period calendar's two parts,
    the horizon, the slider override, the employer-base resolver), not a
    cohesive concept.  Bundling the period calendar back into a ``_CurrentPay``
    snapshot here would reintroduce a cross-module import cycle with
    ``retirement_dashboard_service`` (which owns that snapshot); passing the
    two period fields directly keeps this module a projection leaf.

    Args:
        balance_ctx: The read pass this projection runs in -- its ``user_id``
            scopes the account query, and every producer below shares its
            scenario, its clock and its memos.
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
            Account.user_id == balance_ctx.user_id,
            Account.account_type_id.in_(retirement_type_ids),
            Account.is_active.is_(True),
        )
        .all()
    )
    return _RetirementProjectionContext(
        balance_ctx=balance_ctx,
        accounts=accounts,
        all_periods=all_periods,
        current_period=current_period,
        planned_retirement_date=planned_retirement_date,
        traditional_type_ids=traditional_type_ids,
        return_rate_override=return_rate_override,
        employer_salary_basis=employer_salary_basis,
    )


@dataclass(frozen=True)
class HorizonProjection:
    """Every account projected to a context's horizon, and WHAT it ran over.

    **The axis and the clock are published rather than left to be rebuilt**
    (plan step C2-e).  The readiness page needs the axis for three of its own
    figures -- the "your path" series aligns its per-account rows against it,
    the "needed path" reverse-projects over it, and the countdown's
    "paychecks remaining" is its length -- and it used to rebuild the axis by
    RE-ISSUING the same producer call, with a comment ("Matches the exact
    ``generate_projection_periods`` call the account projection used") standing
    in for a guarantee.  Two producers of one value held equal by a comment is
    the shape this arc exists to remove: the day either call site changed, the
    chart's rows would have silently mis-aligned against an axis of a different
    length.

    Attributes:
        projections: One dict per account (see :func:`_project_one_account`).
        axis: The :class:`~app.services.pay_calendar.PeriodWindow` every one of
            those projections ran over.  Each row in a projection's
            ``projection_rows`` carries its own period as well, so a consumer
            that has the rows does not need this; a consumer that must size or
            reverse-walk the axis when NO account projects does.
        as_of: The read pass's clock -- the day the axis opens after and the
            day every seed balance is valued against.  Carried so a page that
            reports "years remaining" beside this projection measures it from
            the same day the projection did.
    """

    projections: list[dict]
    axis: PeriodWindow
    as_of: date


def project_retirement_accounts(
    ctx: _RetirementProjectionContext,
) -> HorizonProjection:
    """Project each retirement / investment account forward to retirement.

    Loads the shared per-request projection inputs once
    (:func:`load_projection_batch`), resolves the period axis from the
    context's horizon, then projects each account via
    :func:`project_accounts_with_batch`.

    Args:
        ctx: The read-only projection context (accounts + period/horizon
            inputs).

    Returns:
        The :class:`HorizonProjection` -- the per-account projection dicts
        together with the axis and the clock they were computed against.
    """
    batch = load_projection_batch(ctx)
    axis = resolve_projection_axis(ctx)
    return HorizonProjection(
        projections=project_accounts_with_batch(ctx, batch, axis),
        axis=axis,
        as_of=ctx.balance_ctx.as_of,
    )


def project_accounts_with_batch(
    ctx: _RetirementProjectionContext,
    batch: _ProjectionBatch,
    projection_periods: PeriodWindow,
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
            empty window leaves every account non-projecting).

    Returns:
        A list of per-account projection dicts (see
        :func:`_project_one_account` for the keys).
    """
    seeds = _resolve_seed_balances(ctx, batch, projection_periods)
    return [
        _project_one_account(acct, ctx, batch, projection_periods, seeds)
        for acct in ctx.accounts
    ]


def resolve_projection_axis(
    ctx: _RetirementProjectionContext,
) -> PeriodWindow:
    """Resolve the period axis this context's projection runs over.

    The owner's OWN paychecks, from the read pass's ``as_of`` to the planned
    retirement date -- or, with no retirement date set, to the last day their
    saved schedule reaches.  Past the saved horizon the calendar projects
    forward at the cadence the owner recorded.

    **Both arms are the same expression since plan step C2-e**, and collapsing
    them is the point.  The horizon arm used to build a SYNTHETIC axis at a
    hardcoded 14-day cadence (``growth_engine.generate_projection_periods``,
    now deleted), which credited an owner ``365/14`` paycheck contributions a
    year whatever their real cadence -- ``$1,300,344.92`` shown against a true
    ``$711,385.70`` for a monthly-paid owner over 20 years (ledger row
    **P20**) -- while the no-horizon arm walked the REAL pay periods.  Two arms
    meant two answers to "when is this owner's next paycheck", and only one of
    them read the schedule.  Now the cadence lives in the calendar, so there is
    no second value to get wrong and no branch to keep in step.

    **The clock is the read pass's, not the process's.**  It used to be
    ``date.today()`` called here, while the seed each account projects from is
    read at this axis's own opening day -- so a pass that crossed midnight
    between the two reads valued the seed against a window that had moved.
    ``ctx.balance_ctx.as_of`` is the one clock for the whole pass, and since
    plan step C2-f2d-1 the pass arrives on ``ctx`` rather than as a second
    parameter this function could be handed a different one through.

    Args:
        ctx: The read-only projection context.  Its ``balance_ctx`` is the
            read pass: that pass's ``as_of`` opens the window and its memoized
            calendar supplies the paydays, so no query is issued here.

    Returns:
        The :class:`~app.services.pay_calendar.PeriodWindow` to project over.
        **Empty** when the owner has no paydays, and when the horizon is
        already behind ``as_of`` -- a stored retirement date that has aged into
        the past, which the lever page reports as its ``past_horizon`` state
        rather than solving for.
    """
    calendar = ctx.balance_ctx.calendar()
    last_day = (
        ctx.planned_retirement_date
        if ctx.planned_retirement_date
        else calendar.horizon()
    )
    # A horizon already BEHIND the pass's clock is the /retirement lever page's
    # ``past_horizon`` state -- a stored plan date that has aged into the past,
    # which the settings schema refuses to accept anew but cannot un-store.
    # Tested HERE rather than left to the calendar, because this is where that
    # state is known: ``projection_axis`` REFUSES a crossed range (an adversarial
    # code review, 2026-08-14, caught it being folded into the empty answer),
    # and folding a caller's defect into a legitimate empty answer is the hole
    # ``overlapping`` refuses to leave open one level down.
    if last_day is None or last_day < ctx.balance_ctx.as_of:
        return PeriodWindow(periods=())
    return calendar.projection_axis(ctx.balance_ctx.as_of, last_day)


def load_projection_batch(
    ctx: _RetirementProjectionContext,
) -> _ProjectionBatch:
    """Load the per-request data shared across all account projections.

    Runs the deduction, shadow-income, investment-params, salary-gross,
    and entries-aware balance queries a single time (F-22 / Commit 18 for
    the shared batch loaders) so the per-account loop does no repeated
    I/O.  Everything loaded here is date-independent, so the P2b probes
    reuse one batch across every candidate retirement date.

    **It opens no read pass, since plan step C2-f2d-1** (ledger row **P43**).
    This function called ``BalanceContext.build`` -- a LEAF manufacturing the
    object that is supposed to be pinned once at the door -- which is what put
    a second pass, with a second reading of the clock, inside every
    ``/retirement`` and ``/savings`` render.  The pass now arrives on ``ctx``
    and there is no default to fall through to, so a second one cannot be
    opened here by omission.

    Args:
        ctx: The read-only projection context, carrying the read pass whose
            scenario scopes the contribution pricing and whose memos the
            balance read shares.

    Returns:
        A :class:`_ProjectionBatch` with all shared inputs.
    """
    user_id = ctx.balance_ctx.user_id
    account_ids = [a.id for a in ctx.accounts]
    period_ids = [p.id for p in ctx.all_periods]

    # F-22 / Commit 18: shared batch loaders replace the filter-shape
    # duplicate that previously lived inline here and in
    # savings_dashboard_service / year_end_summary_service.
    deductions_by_account = load_active_deductions_for_accounts(
        user_id, account_ids,
    )
    # Pricing a contribution needs the basis its amount resolves under -- the
    # owner, the scenario, and the salary / loan derivations behind a live
    # figure (plan steps X-au-c2 and X-au-c2b).  That basis is the PASS's,
    # resolved once at the door and shared by every producer in the render,
    # rather than a second one built here as it was until plan step C2-f2d-1.
    contributions = load_shadow_income_contributions_for_accounts(
        ctx.balance_ctx.amounts(), account_ids, period_ids,
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
    # ``as_of`` is the PASS's (plan step C2-f2d-1, corrected by its
    # adversarial code review): this resolver defaults to ``date.today()`` and
    # resolves its own current period from it, so without the argument the
    # employer-match cap basis came off a clock read of its own -- twice per
    # ``/retirement`` render, once for the verdict and once for the levers.
    salary_gross_biweekly = income_service.get_current_gross_biweekly(
        user_id, as_of=ctx.balance_ctx.as_of,
    )

    # The displayed per-account balance is the model-from-anchor value at the
    # current period's end (so it agrees with /savings and the /investment
    # dashboard).  The forward projection seeds from the same curve, read a day
    # before its own AXIS opens, which is why the seed is not a field of this
    # bundle -- see :class:`_ProjectionBatch`.  Both read the pass's one
    # baseline scenario.
    balance_map = _resolve_displayed_balances(ctx)
    return _ProjectionBatch(
        deductions_by_account=deductions_by_account,
        contributions=contributions,
        params_by_account=params_by_account,
        salary_gross_biweekly=salary_gross_biweekly,
        balance_map=balance_map,
    )


def _resolve_displayed_balances(
    ctx: _RetirementProjectionContext,
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
        ctx: The read-only projection context, carrying the read pass this
            reads the seam through.

    Returns:
        ``{account_id: displayed balance}``; empty with no pay periods, in
        which case each account falls back to its anchor.
    """
    # The no-baseline arm of this guard went at plan step X-v2 (ruling R-BW):
    # the seam raises and one application-level handler answers, so this
    # producer decides only what it models -- a user with no pay periods.
    if not ctx.all_periods:
        return {}
    return _pick_current_period_balances(
        ctx,
        balance_at.build_maps(ctx.accounts, ctx.balance_ctx),
    )


def _resolve_seed_balances(
    ctx: _RetirementProjectionContext,
    batch: "_ProjectionBatch",
    projection_periods: PeriodWindow,
) -> dict[int, Decimal]:
    """Resolve each account's balance the day BEFORE the window opens.

    Ruling R-AB's seed, read once per AXIS rather than once per batch.  Every
    event inside the window is then the growth engine's to apply and none of
    them is in the seed, which is what let the
    current-period contribution subtraction this projection used to
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

    **Memoized on the batch by (SEED DATE, account set, READ PASS).**  The P2b
    retire-later probes call :func:`project_accounts_with_batch` once per
    candidate horizon and every one of those axes opens on the same payday --
    the one covering the pass's ``as_of`` -- so without the memo each account is
    re-folded once per probe: measured when this was written, the /retirement
    lever page went 377 ms -> 682 ms without it.  The date is a DATE rather than
    a flag, so an axis that genuinely opens somewhere else still gets its own
    seed.

    **Every term of that key is a term of the VALUE**, and the third was added
    at plan step C2-f2d-1 when the pass moved off this batch onto ``ctx``.
    Before that move the seed was folded through ``batch.balance_ctx`` -- a
    field of the very object holding the cache, so key and value could not
    disagree by construction.  The pass now arrives SEPARATELY, and
    :func:`balance_at.balance_at` is a function of it: its scenario scopes the
    row set and its ``as_of`` clamps every still-projected row forward (ruling
    R-G, ``_cash_fold.assemble``).  A batch shared across two passes would
    otherwise hand the second one the first one's seeds, silently.  Keyed on the
    pass's VALUES rather than its identity, so two passes pinned alike share a
    seed -- which is the answer being memoized.  The account-set term is here
    for the identical reason and its comment below states it; that one was
    widened before it was reachable too.

    Args:
        ctx: The read-only projection context -- its ``balance_ctx`` scopes the
            read and memoizes the pass's loan resolutions.
        batch: The per-request bundle; its ``seed_memo`` holds one map per
            distinct (seed date, account set, pass).
        projection_periods: The axis about to be projected.  Its FIRST period's
            ``start_date`` is when the window opens.

    Returns:
        ``{account_id: seed balance}``; empty when there are no periods or no
        axis (each account then falls back to its anchor).  The no-scenario arm
        went at plan step X-v2 (ruling R-BW) -- answered above this route now.
    """
    if not ctx.all_periods or not projection_periods:
        return {}
    # Keyed on the account SET as well as the date: the map is a function of
    # both, and ``ctx`` arrives separately from ``batch``, so a caller reusing
    # one batch across contexts with different account sets would otherwise get
    # a hit for the first set and silently fall back to the stored anchor for
    # every account the second set added.  Not reachable in-tree today (the P2b
    # probes rebuild the context with ``replace``, which preserves ``accounts``)
    # -- which is exactly why the key is widened rather than the invariant
    # documented.  The PASS term joins it on that same rule: the next leaf
    # shares one batch between two producers, which is where a second pass
    # first becomes expressible.
    seed_date = projection_periods[0].start_date - timedelta(days=1)
    key = (
        seed_date,
        tuple(acct.id for acct in ctx.accounts),
        ctx.balance_ctx.user_id,
        ctx.balance_ctx.scenario_id_or_none,
        ctx.balance_ctx.as_of,
    )
    if key not in batch.seed_memo:
        # TOTAL over ``ctx.accounts``.  It used to skip an account with
        # ``current_anchor_period_id IS NULL`` -- a state the schema forbade and
        # the column no longer exists to express (finding N-73, plan step
        # X-f1c3a), so the filter admitted nothing and only made the map look
        # partial to its consumers.
        batch.seed_memo[key] = {
            acct.id: balance_at.balance_at(acct, ctx.balance_ctx, seed_date)
            for acct in ctx.accounts
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
    dated read rather than a period one.

    **Both of its fallbacks to a stored balance are gone** (plan step X-f1c3a).
    The "account absent from *maps_by_account*" arm covered an account with no
    anchor period, a state the schema forbade and the column no longer exists to
    express (finding N-73) -- ``build_maps`` is TOTAL, so the account and its
    period column are both INDEXED, and a missing key is a loader defect rather
    than a display state.  The "no current period" arm returned the last balance
    the user ASSERTED under a heading that says what the account holds now, and
    now reads the seam at ``as_of`` instead (ruling R-EM): the seam takes a DATE,
    and the period was only ever a way of supplying one.

    Args:
        ctx: The read-only projection context -- its ``balance_ctx.as_of`` is
            the valuation date when no period contains today.
        maps_by_account: ``{account_id: period_id -> Decimal}``, total over
            ``ctx.accounts``.

    Returns:
        A mapping of account ID to its current-period balance.
    """
    if ctx.current_period is None:
        return {
            acct.id: balance_at.balance_at(
                acct, ctx.balance_ctx, ctx.balance_ctx.as_of,
            )
            for acct in ctx.accounts
        }
    return {
        acct.id: maps_by_account[acct.id][ctx.current_period.id]
        for acct in ctx.accounts
    }


def _run_account_projection(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    acct: Account,
    ctx: _RetirementProjectionContext,
    batch: _ProjectionBatch,
    params: InvestmentParams,
    projection_periods: PeriodWindow,
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
        c for c in batch.contributions.records if c.account_id == acct.id
    ]
    adapted_deductions = adapt_deductions(
        batch.deductions_by_account.get(acct.id, []),
    )
    inputs = build_investment_projection_inputs(
        params, adapted_deductions, acct_contributions,
        ctx.current_period, batch.salary_gross_biweekly,
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
    projection_periods: PeriodWindow,
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
            empty window leaves the account non-projecting).
        seeds: Ruling R-AB's ``{account_id: balance}`` the day before the
            window opens (:func:`_resolve_seed_balances`), resolved once for
            this axis.  Total over ``ctx.accounts`` whenever
            ``projection_periods`` is non-empty, which is the only branch that
            reads it.

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
    # ``balance_map`` is EMPTY only for a user with no pay periods at all
    # (``_resolve_displayed_balances`` returns ``{}`` there); with any schedule
    # it is total over ``ctx.accounts``.  With no schedule the seam has nothing
    # to project over, so the honest figure is the last balance the user
    # asserted -- read from the assertion itself rather than from the cache
    # column that used to mirror it (plan step X-f1c3a).  This is NOT ruling
    # R-EM's no-current-period case, which is handled inside the map builder.
    balance = (
        batch.balance_map[acct.id] if acct.id in batch.balance_map
        else cash_ledger.resolve_anchor(acct).balance
    )
    acct_deductions = batch.deductions_by_account.get(acct.id, [])
    # PRESENCE, not contribution: an account whose every contribution is
    # Cancelled still HAS one linked, and telling its owner to link one would
    # be wrong.  Read off the unscreened set the loader carries for exactly
    # this reader (:class:`ShadowContributions`); an adversarial review caught
    # this flipping when the status screen moved to the boundary.
    none_linked = (
        not acct_deductions
        and acct.id not in batch.contributions.linked_account_ids
    )

    if params is not None and projection_periods:
        result = _run_account_projection(
            acct, ctx, batch, params, projection_periods,
            # INDEXED: ``projection_periods`` is non-empty here, so
            # ``_resolve_seed_balances`` built a total map (plan step X-f1c3a
            # deleted the no-anchor-period filter that used to make it partial).
            seeds[acct.id],
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
