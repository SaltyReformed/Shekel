"""
Shekel Budget App -- Retirement Dashboard Inputs and Resolvers

The LOADER and the resolvers behind the retirement picture: what a render
reads from the database once (:func:`load_gap_inputs`), and the single
definitions of the questions that picture asks of the user's settings --
which retirement date is planned and who owns it, what safe-withdrawal rate
applies, what estimated retirement tax rate is stored, what the pension
benefit sums to, and what the final-year take-home comes to.

**It stopped ORCHESTRATING at plan step C2-f2d-2.**  ``compute_gap_data``
lived here and was one of the two implementations of "the retirement picture
at a candidate plan"; :mod:`app.services.retirement_plan` is now the only
one, and it composes the resolvers below.  What is left here is what that
producer -- and the lever solver, and the readiness shaping -- all read
from, which is why it is one module and not three.

All functions accept plain data (the render's read pass, loaded inputs) and
return plain data.  No Flask imports.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.pension_profile import PensionProfile
from app.models.salary_profile import SalaryProfile
from app.models.user import UserSettings
from app.services import (
    paycheck_calculator,
    pension_calculator,
)
from app.services.pay_calendar import PayCadence
from app.services.paycheck_calculator import PayrollBasis
from app.services.tax_config_service import load_tax_configs_for_year
from app.utils.dates import add_months
from app.utils.money import round_money


# Default safe-withdrawal-rate percentage when the user has no
# ``UserSettings`` row or has not customised ``safe_withdrawal_rate``.
# 4% is the Trinity Study baseline (Cooley, Hubbard, Walz, 1998) and
# the standard default for FIRE-style retirement planners.  Stored as
# a percentage Decimal (not the fractional decimal that the database
# column carries) because this constant is fed directly into the
# dashboard slider, whose ``min``/``max`` are expressed in percent.
_DEFAULT_SWR_PCT = Decimal("4.00")

# Percentage scaler.  ``safe_withdrawal_rate`` is stored as a fractional
# decimal (4% as ``Decimal("0.0400")``) while the default above is stated in
# percent.  Named so the conversion direction is explicit at its one site.
_PCT_SCALE = Decimal("100")

# Default merit-raise horizon (years) when the user has no
# ``UserSettings`` row (Gate A ruling 3 / fork F4).  Matches the
# ``merit_raise_horizon_years`` column's server default (5) so a
# settings-less user projects the same horizon a freshly-created
# settings row would.  The column is NOT NULL, so a present settings row
# always supplies a concrete int; only ``settings is None`` falls back
# here.
_DEFAULT_MERIT_HORIZON_YEARS = 5


# ── Result and context bundles ───────────────────────────────────


@dataclass(frozen=True)
class PensionSummary:
    """Aggregated pension-benefit outputs for the gap analysis.

    Returned by :func:`compute_pension_summary` so the picture producer
    carries the pension-derived values it forwards downstream as one
    immutable result rather than parallel locals: the summed monthly
    pension income (the gap calculator's pension input), the
    raise-projected salary-by-year series (reused by the gap-comparison
    salary projection so it is not recomputed), and the per-pension
    derivation entries (the P3c page's footer; the pre-P3b "last benefit
    only" field this superseded is gone -- audit finding D6).

    Attributes:
        monthly_income: The summed monthly benefit across all qualifying
            pensions (``Decimal("0")`` when none qualify).
        salary_by_year: The ``(year, salary)`` projection produced for
            the last qualifying pension, or ``None`` when none qualified;
            reused by :func:`compute_gap_net_biweekly`.
        per_pension: One dict per qualifying pension (``name``,
            ``benefit_multiplier``, ``consecutive_high_years``,
            ``benefit``), in iteration order.  Retains the benefits the
            loop already computed so the page renders the derivation line
            PER PENSION (audit finding D6: the old "last benefit only"
            card silently disagreed with the summed gap row the moment a
            second pension existed).
    """

    monthly_income: Decimal
    salary_by_year: list[tuple[int, Decimal]] | None
    per_pension: list = field(default_factory=list)


@dataclass(frozen=True)
class _CurrentPay:
    """The user's current-period pay snapshot.

    Returned by :func:`_compute_current_pay`, so the gap comparison and the
    readiness picture read ONE engine run rather than each starting another.

    **It carried the owner's PERIODS too, and they went at pay-calendar plan
    step C2-f2d-3.**  ``all_periods`` and ``current_period`` existed to be
    threaded into ``retirement_projection.build_projection_context``; that
    function now derives both from the read pass it already takes, which left
    these two fields with ZERO readers in ``app/`` or ``tests/`` -- a value
    published for a consumer that no longer exists, which is the shape rulings
    R-BG and R-BH each deleted once already.  Anything wanting either asks the
    pass: ``balance_ctx.reported_periods()`` and
    ``balance_ctx.calendar().period_containing(balance_ctx.as_of)``.

    Attributes:
        net_biweekly: The current-period net (take-home) pay from the
            paycheck engine; ``Decimal("0")`` when there is no active
            salary profile or no current period.
        current_breakdown: The full :class:`PaycheckBreakdown` for the
            current period, or ``None`` in the same no-profile /
            no-period cases; reused for the engine gross-biweekly figure.
    """

    net_biweekly: Decimal
    current_breakdown: paycheck_calculator.PaycheckBreakdown | None


@dataclass(frozen=True)
class GapInputs:
    """The once-per-request loaded inputs the gap analysis reads.

    Returned by :func:`load_gap_inputs` and carried on
    :class:`~app.services.retirement_plan.RetirementInputs`, so every producer
    on the page shares one loader for the date-independent inputs -- the
    retire-later solver's binary-search probes load these ONCE and re-derive
    only the date-dependent parts (salary path, pension benefit, growth
    horizon) per probe.

    Attributes:
        settings: The user's :class:`UserSettings`, or ``None``.
        pensions: The user's active :class:`PensionProfile` rows.
        salary_profiles: The user's active :class:`SalaryProfile` rows.
        pay: The current-period pay snapshot (:class:`_CurrentPay`).
        merit_horizon_years: The resolved merit-raise horizon.
        pay_cadence: How often the owner is paid
            (:class:`~app.services.pay_calendar.PayCadence`), loaded here at
            plan step R7a-2a so the gap analysis and every lever probe measure
            pre-retirement income against the same rhythm.  It is
            date-independent like everything else on this bundle, which is why
            a probe at month offset ``m`` never reloads it.
    """

    settings: UserSettings | None
    pensions: list[PensionProfile]
    salary_profiles: list[SalaryProfile]
    pay: _CurrentPay
    merit_horizon_years: int
    pay_cadence: PayCadence


def load_gap_inputs(balance_ctx):
    """Load the gap analysis's per-request inputs in one place.

    **The owner comes off the READ PASS** (plan step C2-f2d-1): the caller
    holds one for the whole render, and taking a bare ``user_id`` beside it is
    what let this producer and the lever solver each open a pass of their own.

    **It runs ONCE per render, since plan step C2-f2d-2**, because its one
    caller is :func:`app.services.retirement_plan.load_retirement_inputs` and a
    route calls that.  It ran twice until then -- the gap producer's copy and
    the lever solver's -- which is 46 of the 179 queries a ``/retirement``
    render issued on a production clone, including a whole paycheck-engine run
    (ledger row **P57**).

    Args:
        balance_ctx: The render's
            :class:`~app.services.balance_at.BalanceContext` -- the owner, the
            baseline scenario and the day, pinned once by the route.

    Returns:
        A :class:`GapInputs` bundle (settings, active pensions, active
        salary profiles, the current-pay snapshot, the resolved merit
        horizon, and the owner's pay cadence).

    Raises:
        PayCalendarError: The owner has no resolvable pay cadence -- no
            ``budget.pay_schedule`` row and no pay period to infer one from.
            The gap's pre-retirement income is their paycheck converted to a
            month, so there is no honest figure without it (plan step
            R7a-2a; see
            :attr:`app.services.pay_calendar.PayCalendar.cadence`).
    """
    user_id = balance_ctx.user_id
    settings = (
        db.session.query(UserSettings).filter_by(user_id=user_id).first()
    )
    pensions = (
        db.session.query(PensionProfile)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )
    salary_profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )
    return GapInputs(
        settings=settings,
        pensions=pensions,
        salary_profiles=salary_profiles,
        pay=_compute_current_pay(balance_ctx, salary_profiles),
        merit_horizon_years=_resolve_merit_horizon(settings),
        # Resolved once here (plan step R7a-2a): the retire-later solver
        # probes this bundle dozens of times per request and the cadence does
        # not move with a candidate retirement date.
        #
        # **Off the pass's own calendar rather than through ``cadence_for``**
        # (plan step R-F16).  ``_compute_current_pay`` above derives that
        # calendar unconditionally and the pass memoizes it, so a second door
        # here was one extra ``budget.pay_schedule`` query for a value already
        # in hand -- which is the rule
        # :attr:`app.services.pay_calendar.PayCalendar.cadence` states in as
        # many words ("a caller that ALREADY holds a calendar must use this").
        # Both doors refuse the same owner, so the ``Raises`` above is
        # unchanged.
        pay_cadence=balance_ctx.calendar().cadence,
    )


def resolve_swr_fraction(settings):
    """Resolve the active safe-withdrawal rate as a fractional Decimal.

    **The ONE definition**, and since plan step C2-f2d-2 it also has ONE
    caller: :func:`app.services.retirement_plan._derive_picture` resolves the
    rate for the picture, and every consumer reads it back off that picture's
    own analysis rather than resolving a second one.  Two call sites is how
    the CRIT-04 / F-042 / PA-04 / PA-05 phantom-income defect happened -- they
    resolved the same column under two different rules (truthiness
    ``or "0.04"`` vs. ``is None``), so an explicit ``Decimal("0.0000")``
    safe-withdrawal rate rendered as 0.00% on the slider but drove the
    projection at 4%: a phantom $4,000/mo of retirement income on a $1.2M
    balance the slider says is zero.  E-12 / coding-standard "do not rely on
    truthiness for business logic": a stored zero rate is a real zero; only
    ``settings is None`` or ``safe_withdrawal_rate is None`` means "unset, use
    the default."

    Args:
        settings: the user's :class:`~app.models.user.UserSettings`
            row, or ``None`` when the user has not yet created one.

    Returns:
        The fractional-decimal SWR (the form
        :func:`app.services.retirement_gap_calculator.calculate_gap`
        expects: ``0.04`` for the 4% rule, not ``4.0``).  Falls back
        to ``_DEFAULT_SWR_PCT / _PCT_SCALE`` when ``settings`` is
        ``None`` or the stored column is ``None``; an explicit zero
        stored value is preserved as :class:`~decimal.Decimal` zero.
    """
    if settings is None or settings.safe_withdrawal_rate is None:
        return _DEFAULT_SWR_PCT / _PCT_SCALE
    return Decimal(str(settings.safe_withdrawal_rate))


def _resolve_merit_horizon(settings):
    """Resolve the merit-raise horizon (years) from user settings.

    The retirement salary projection's Gate A ruling 3 / fork F4 knob:
    how many years from the current year (inclusive) merit-type and
    custom-type raises keep applying before they stop (cola-type
    recurring raises still extrapolate to the retirement date).

    Args:
        settings: the user's :class:`~app.models.user.UserSettings` row,
            or ``None`` when the user has not yet created one.

    Returns:
        int -- the stored ``merit_raise_horizon_years`` (a NOT NULL
        column, so always a concrete int when ``settings`` is present),
        or :data:`_DEFAULT_MERIT_HORIZON_YEARS` when ``settings`` is
        ``None``.
    """
    if settings is None:
        return _DEFAULT_MERIT_HORIZON_YEARS
    return settings.merit_raise_horizon_years


# ── The picture's per-point derivations ──────────────────────────


def compute_pension_summary(
    pensions: list[PensionProfile],
    merit_horizon_years: int,
    as_of: date,
    month_offset: int = 0,
) -> PensionSummary:
    """Aggregate the pension benefit across the user's active pensions.

    Iterates the active pensions, projecting each one that carries both a
    planned retirement date and a linked salary profile, and sums their
    monthly benefit.  The last qualifying pension's benefit and
    salary-by-year series are retained (the series is reused by the
    gap-comparison salary projection).

    Args:
        pensions: The user's active :class:`PensionProfile` rows.
        merit_horizon_years: The merit-raise horizon (years) forwarded to
            :func:`~app.services.pension_calculator.project_salaries_by_year`
            so merit/custom raises stop applying after the cutoff.
        as_of: The read pass's pinned day, whose YEAR opens the salary path.
            It was ``date.today()`` here until pay-calendar plan step C2-f2e
            (ledger row **P55**): one of the last three producers on
            ``/retirement`` to resolve the clock for itself, on a render that
            already held a pass with a pinned day.  The three are asked once
            per plan point and the retire-later lever probes about ten, so one
            render read the clock about thirteen times -- and the reads are
            ``.year``, so they diverge across a NEW YEAR: the verdict card
            projecting its salary path from year N while the lever card beside
            it projects from N+1, which is the two-cards-two-clocks shape plan
            step C2-f2d-1 measured at ``$4.18`` for the read pass itself.
        month_offset: Whole months added to EACH qualifying pension's
            planned retirement date before projecting (the P2b retire-later
            probes: a later retirement extends the salary path, the years
            of service, and the high-salary window together).  ``0`` (the
            default) evaluates the stored plan unchanged --
            :func:`app.utils.dates.add_months` with 0 months is the
            identity.

    Returns:
        A :class:`PensionSummary` bundling the summed monthly pension
        income, the last salary-by-year series (``Decimal("0")`` /
        ``None`` when no pension qualifies), and the per-pension
        derivation entries.
    """
    monthly_income = Decimal("0")
    salary_by_year = None
    per_pension = []
    for pension in pensions:
        if pension.planned_retirement_date and pension.salary_profile:
            profile = pension.salary_profile
            planned = add_months(
                pension.planned_retirement_date, month_offset,
            )
            salary_by_year = pension_calculator.project_profile_salaries(
                profile,
                as_of.year,
                planned.year,
                merit_horizon_years,
            )
            benefit = pension_calculator.calculate_benefit(
                benefit_multiplier=pension.benefit_multiplier,
                consecutive_high_years=pension.consecutive_high_years,
                hire_date=pension.hire_date,
                planned_retirement_date=planned,
                salary_by_year=salary_by_year,
            )
            monthly_income += benefit.monthly_benefit
            # Retain every pension's derivation inputs (D6): the loop
            # already computed the benefit; keeping only the last one is
            # what made the old details card lie for multi-pension users.
            per_pension.append({
                "name": pension.name,
                "benefit_multiplier": pension.benefit_multiplier,
                "consecutive_high_years": pension.consecutive_high_years,
                "benefit": benefit,
            })
    return PensionSummary(monthly_income, salary_by_year, per_pension)


def _compute_current_pay(
    balance_ctx, salary_profiles: list[SalaryProfile],
) -> _CurrentPay:
    """Load the pay-period calendar and the current paycheck breakdown.

    Computes the current-period net pay via the raise-aware paycheck
    engine (F-20 / MED-06 / F-032) so the page agrees with the engine on
    both net and gross for the current period.  Returns zero / ``None``
    pay when the user has no active salary profile or no current period.

    **WHICH period is current comes off the read pass** (plan step C2-f2d-1,
    corrected by its adversarial code review).  ``get_current_period``'s
    ``as_of`` defaults to ``date.today()``, and this loader runs TWICE per
    ``/retirement`` render -- once for the verdict, once for the levers -- so
    the two cards resolved it from two independent clock reads.  Threading the
    pass's day closes that, and it closes a WIDER window the leaf itself
    opened: the pass used to be built microseconds after this line, and is now
    built at the route, so a levers producer reading its own clock here would
    be internally inconsistent with its own axis across a payday boundary.
    A period is not cosmetic -- it selects the displayed balance and the
    contribution basis.

    Args:
        balance_ctx: The render's read pass -- its ``user_id`` scopes the
            queries and its ``as_of`` decides which period is current.
        salary_profiles: The user's active salary profiles (the first is
            used as the current profile).

    Returns:
        A :class:`_CurrentPay` snapshot -- the net biweekly pay and the full
        breakdown.  It carried the period calendar too until pay-calendar plan
        step C2-f2d-3; see that class.
    """
    user_id = balance_ctx.user_id
    # BOTH answers come off the pass's ONE memoized calendar (pay-calendar
    # plan step C2-f2d-3), where two SQL readers could disagree with each other
    # and with the derived calendar every other producer on this page reads.
    calendar = balance_ctx.calendar()
    all_periods = calendar.saved()
    current_period = calendar.period_containing(balance_ctx.as_of)
    net_biweekly = Decimal("0")
    current_breakdown = None
    # F-20 / MED-06 / F-032: take the current-period net (and, via the
    # returned breakdown, the gross) from the raise-aware paycheck engine
    # so the page agrees with the engine for the current period.  The
    # pre-Commit-17 ``annual_salary / pay_periods`` recompute silently
    # dropped any applicable SalaryRaise.
    if salary_profiles and current_period:
        profile = salary_profiles[0]
        # The CURRENT PERIOD's own tax year, not the clock's -- the same key
        # every other paycheck for this profile is computed under.
        tax_configs = load_tax_configs_for_year(
            user_id, profile, current_period.start_date.year,
        )
        current_breakdown = paycheck_calculator.calculate_paycheck(
            PayrollBasis(profile, calendar.cadence),
            current_period, all_periods, tax_configs,
        )
        net_biweekly = current_breakdown.earnings.net_pay
    return _CurrentPay(net_biweekly, current_breakdown)


def resolve_retirement_date_provenance(
    pensions: list[PensionProfile], settings: UserSettings | None,
) -> dict:
    """Resolve the planned retirement date WITH its provenance.

    The single owner of the precedence rule (a pension's planned date
    beats the settings date; the latest pension wins), returned with the
    facts the assumptions rail needs to render the date row honestly
    (acceptance-drive fix 1): when a pension owns the date, a settings
    save cannot change the resolved horizon, so the row must show the
    resolved date read-only with a link to the owning pension instead of
    an input whose Save silently loses.

    Args:
        pensions: The user's active pensions.
        settings: The user's :class:`UserSettings`, or ``None``.

    Returns:
        dict with ``date`` (the resolved date, or ``None``), ``source``
        (``"pension"`` / ``"settings"`` / ``"none"`` -- producer-computed
        state strings, compared literally like the lever states), and
        ``pension_id`` / ``pension_name`` (the MAX-date owning pension
        when ``source == "pension"``, else ``None``).
    """
    dated_pensions = [
        p for p in pensions if p.planned_retirement_date is not None
    ]
    if dated_pensions:
        owner = max(dated_pensions, key=lambda p: p.planned_retirement_date)
        return {
            "date": owner.planned_retirement_date,
            "source": "pension",
            "pension_id": owner.id,
            "pension_name": owner.name,
        }
    if settings is not None and settings.planned_retirement_date is not None:
        return {
            "date": settings.planned_retirement_date,
            "source": "settings",
            "pension_id": None,
            "pension_name": None,
        }
    return {
        "date": None,
        "source": "none",
        "pension_id": None,
        "pension_name": None,
    }


def resolve_planned_retirement_date(
    pensions: list[PensionProfile], settings: UserSettings | None,
) -> date | None:
    """Derive the planned retirement date from pensions, else settings.

    Prefers the latest planned retirement date across the user's
    pensions; falls back to the retirement date stored on the user's
    settings.  Delegates to
    :func:`resolve_retirement_date_provenance` so the precedence rule
    has exactly one definition.

    Args:
        pensions: The user's active pensions.
        settings: The user's :class:`UserSettings`, or ``None``.

    Returns:
        The resolved planned retirement date, or ``None`` when neither a
        pension nor the settings supply one.
    """
    return resolve_retirement_date_provenance(pensions, settings)["date"]


def compute_gap_net_biweekly(
    gap: GapInputs,
    planned_retirement_date: date | None,
    salary_by_year: list[tuple[int, Decimal]] | None,
    merit_horizon_years: int,
    as_of: date,
) -> Decimal:
    """Project the final-year net biweekly pay for the gap comparison.

    Scales the projected final-year gross biweekly (from the raise-aware
    salary projection) by the current effective take-home rate
    (net / gross), so the gap calculator compares retirement income
    against a raise-adjusted pre-retirement take-home figure rather than
    today's pay.  Returns the current net biweekly unchanged when there
    is no salary profile, no horizon, no positive current pay, or no
    projectable salary series.

    Args:
        gap: The render's :class:`GapInputs`, read for the owner's active
            salary profiles and the current-pay snapshot (net pay + the
            breakdown the take-home rate's denominator comes from).  **The
            BUNDLE rather than those two values** since pay-calendar plan step
            C2-f2e: they arrive together, from one loader, at the single
            production call site, and taking them apart is what put this
            function one argument over the design threshold when the read
            pass's day joined it.  Its ``merit_horizon_years`` is deliberately
            NOT read here -- see the argument of that name below.
        planned_retirement_date: The projection horizon, or ``None``.
        salary_by_year: The pension-derived salary projection if one was
            already built, else ``None`` (recomputed here when needed).
        merit_horizon_years: The merit-raise horizon (years) forwarded to
            :func:`~app.services.pension_calculator.project_salaries_by_year`
            when the salary series is recomputed here.  **The PLAN POINT's
            horizon, which equals ``gap.merit_horizon_years`` only for the
            stored plan**: the what-if panel and the lever solver both probe
            other values, so the bundle's stored figure would silently ignore
            the override the user is looking at.
        as_of: The read pass's pinned day, whose YEAR opens the salary path.
            It was ``date.today()`` here until pay-calendar plan step C2-f2e
            (ledger row **P55**): one of the last three producers on
            ``/retirement`` to resolve the clock for itself, on a render that
            already held a pass with a pinned day.  The three are asked once
            per plan point and the retire-later lever probes about ten, so one
            render read the clock about thirteen times -- and the reads are
            ``.year``, so they diverge across a NEW YEAR: the verdict card
            projecting its salary path from year N while the lever card beside
            it projects from N+1, which is the two-cards-two-clocks shape plan
            step C2-f2d-1 measured at ``$4.18`` for the read pass itself.

    Returns:
        The projected final-year net biweekly pay, or ``pay.net_biweekly``
        when the projection cannot be performed.
    """
    pay = gap.pay
    if not (
        gap.salary_profiles
        and planned_retirement_date
        and pay.net_biweekly > 0
    ):
        return pay.net_biweekly

    profile = gap.salary_profiles[0]
    # F-20 / MED-06 / F-032: reuse the engine gross-biweekly the
    # ``net_biweekly`` line already paid for; this locks the
    # effective-take-home-rate denominator to the same per-period gross
    # the engine reports (the pre-Commit-17 ``annual_salary /
    # pay_periods`` recompute silently dropped any applicable
    # SalaryRaise).
    current_gross_biweekly = (
        pay.current_breakdown.earnings.gross_biweekly
        if pay.current_breakdown is not None
        else Decimal("0.00")
    )
    if current_gross_biweekly <= 0:
        return pay.net_biweekly

    effective_take_home_rate = pay.net_biweekly / current_gross_biweekly
    if salary_by_year is None:
        salary_by_year = pension_calculator.project_profile_salaries(
            profile, as_of.year, planned_retirement_date.year,
            merit_horizon_years,
        )
    if not salary_by_year:
        return pay.net_biweekly

    final_salary = salary_by_year[-1][1]
    # The owner's OWN paycheck count, off the cadence the inputs already
    # carry (plan step R-F16); it was a second stored column on the profile,
    # and the two could disagree with each other by any factor.
    final_gross_biweekly = round_money(
        gap.pay_cadence.annual_to_per_paycheck(final_salary)
    )
    return round_money(final_gross_biweekly * effective_take_home_rate)


def resolve_estimated_tax_rate(
    settings: UserSettings | None,
) -> Decimal | None:
    """Resolve the estimated retirement tax rate from user settings.

    Zero is a real value (E-12): an explicitly saved 0% rate returns
    ``Decimal("0")`` -- the user has SET their estimate -- and only NULL
    (settings absent or the column unset) returns ``None``, which is what
    drives the F1 ``tax_rate_missing`` flag.  This closes the display
    half of the LOW-05 / CRIT-04 carry-open (L1): pre-fix the truthiness
    here made a saved 0% render "Not set -- 0% assumed" forever in the
    three places F1 surfaced the flag.  Whether a bracket-based fallback
    should ever be built for the unset case remains the carried product
    question and is unaffected.

    Args:
        settings: The user's :class:`UserSettings`, or ``None``.

    Returns:
        The stored estimated retirement tax rate as a Decimal (an
        explicit zero preserved), or ``None`` only when settings are
        absent or the column is NULL.
    """
    if (settings is not None
            and settings.estimated_retirement_tax_rate is not None):
        return Decimal(str(settings.estimated_retirement_tax_rate))
    return None
