"""
Shekel Budget App -- Retirement Dashboard Service

Orchestrates pension projections, investment growth projections, and
income gap analysis for the retirement dashboard.  Calls existing
services (pension_calculator, growth_engine, retirement_gap_calculator)
and assembles the results into template-ready data structures.

Extracted from the route handler (L-06) so the route contains only
Flask request handling and template rendering.

All functions accept plain data (user_id, optional overrides) and
return plain dicts.  No Flask imports.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.investment_params import InvestmentParams
from app.models.pay_period import PayPeriod
from app.models.pension_profile import PensionProfile
from app.models.salary_profile import SalaryProfile
from app.models.user import UserSettings
from app.services import (
    pay_period_service,
    paycheck_calculator,
    pension_calculator,
    retirement_gap_calculator,
)
from app.services.retirement_projection import (
    build_employer_salary_basis,
    build_projection_context,
    project_retirement_accounts,
)
from app.services.tax_config_service import load_tax_configs
from app.utils.money import round_money


# Default safe-withdrawal-rate percentage when the user has no
# ``UserSettings`` row or has not customised ``safe_withdrawal_rate``.
# 4% is the Trinity Study baseline (Cooley, Hubbard, Walz, 1998) and
# the standard default for FIRE-style retirement planners.  Stored as
# a percentage Decimal (not the fractional decimal that the database
# column carries) because this constant is fed directly into the
# dashboard slider, whose ``min``/``max`` are expressed in percent.
_DEFAULT_SWR_PCT = Decimal("4.00")

# Default assumed-annual-return percentage when the user has no
# retirement / investment accounts (or none with non-zero balances) to
# weight a real average from.  7% matches the S&P 500's long-run
# inflation-adjusted total return (Damodaran historical-returns dataset,
# ~1928-2024) and is the conservative midpoint of common
# retirement-planning assumptions (5-10%).  Same percent convention as
# ``_DEFAULT_SWR_PCT``.
_DEFAULT_RETURN_PCT = Decimal("7.00")

# Percentage scaler.  ``safe_withdrawal_rate`` and
# ``assumed_annual_return`` are stored as fractional decimals (4% as
# ``Decimal("0.0400")``); the slider expects percent (4.00).  Pulled
# out as a named constant so the conversion direction is explicit at
# every multiplication site.
_PCT_SCALE = Decimal("100")

# Two-decimal quantum for percentage display.  The SWR slider uses
# ``"%.2f"|format(current_swr)`` so the underlying Decimal must also
# carry two fractional digits to avoid rendering artefacts when an
# unquantised Decimal feeds through Python's % formatter.
_PCT_QUANTUM = Decimal("0.01")

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
class _PensionSummary:
    """Aggregated pension-benefit outputs for the gap analysis.

    Returned by :func:`_compute_pension_benefit` so the orchestrator
    carries the three pension-derived values it forwards downstream as
    one immutable result rather than three parallel locals: the most
    recent per-pension benefit (for the template), the summed monthly
    pension income (the gap calculator's pension input), and the
    raise-projected salary-by-year series (reused by the gap-comparison
    salary projection so it is not recomputed).

    Attributes:
        benefit: The last computed :class:`PensionBenefit` across the
            user's active pensions, or ``None`` when no pension has both
            a planned retirement date and a linked salary profile.
        monthly_income: The summed monthly benefit across all qualifying
            pensions (``Decimal("0")`` when none qualify).
        salary_by_year: The ``(year, salary)`` projection produced for
            the last qualifying pension, or ``None`` when none qualified;
            reused by :func:`_compute_gap_net_biweekly`.
    """

    benefit: pension_calculator.PensionBenefit | None
    monthly_income: Decimal
    salary_by_year: list[tuple[int, Decimal]] | None


@dataclass(frozen=True)
class _CurrentPay:
    """The user's current-period pay snapshot.

    Returned by :func:`_compute_current_pay`.  Bundles the pay-period
    calendar and the engine-computed current paycheck so the projection
    context and the gap-comparison salary calc both read one snapshot
    rather than re-loading periods or re-running the paycheck engine.

    Attributes:
        all_periods: Every pay period for the user (projection horizon
            source + gap input).
        current_period: The user's current pay period, or ``None`` when
            no period covers today.
        net_biweekly: The current-period net (take-home) pay from the
            paycheck engine; ``Decimal("0")`` when there is no active
            salary profile or no current period.
        current_breakdown: The full :class:`PaycheckBreakdown` for the
            current period, or ``None`` in the same no-profile /
            no-period cases; reused for the engine gross-biweekly figure.
    """

    all_periods: list[PayPeriod]
    current_period: PayPeriod | None
    net_biweekly: Decimal
    current_breakdown: paycheck_calculator.PaycheckBreakdown | None


def _resolve_swr_fraction(settings):
    """Resolve the active safe-withdrawal rate as a fractional Decimal.

    A single definition shared by :func:`compute_gap_data` and
    :func:`compute_slider_defaults` so the slider display and the
    gap/projection math read the stored SWR exactly once -- the
    CRIT-04 / F-042 / PA-04 / PA-05 phantom-income defect was that
    those two call sites resolved the same column under two
    different rules (truthiness ``or "0.04"`` vs.  ``is None``), so
    an explicit ``Decimal("0.0000")`` safe-withdrawal rate rendered
    as 0.00% on the slider but drove the projection at 4% -- a
    phantom $4,000/mo of retirement income on a $1.2M balance the
    slider says is zero.  E-12 / coding-standard "do not rely on
    truthiness for business logic": a stored zero rate is a real
    zero; only ``settings is None`` or ``safe_withdrawal_rate is
    None`` means "unset, use the default."

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


def compute_gap_data(user_id, swr_override=None, return_rate_override=None):
    """Compute gap analysis data for the retirement dashboard or HTMX fragment.

    Loads pension profiles, salary data, and retirement/investment
    accounts, then projects balances forward to the planned retirement
    date and computes the income gap via retirement_gap_calculator.

    Args:
        user_id: The user's integer ID.
        swr_override: Optional Decimal safe withdrawal rate from slider.
        return_rate_override: Optional Decimal annual return rate from slider.

    Returns:
        dict with keys: gap_analysis, chart_data, pension_benefit,
                        retirement_account_projections, settings,
                        salary_profiles, pensions, gap_net_biweekly, swr,
                        planned_retirement_date, estimated_tax_rate.
    """
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

    merit_horizon_years = _resolve_merit_horizon(settings)

    pension = _compute_pension_benefit(pensions, merit_horizon_years)
    pay = _compute_current_pay(user_id, salary_profiles)
    planned_retirement_date = _resolve_planned_retirement_date(
        pensions, settings,
    )

    # P1b (finding D3 / fork F3): grow the employer-contribution base with
    # the SAME P1a salary path (the per-period salary basis) instead of
    # freezing it at today's gross; every other engine consumer keeps the
    # constant base (they pass no salary basis, so ``project_balance``
    # falls back to the constant ``employer_params["gross_biweekly"]``).
    # The salary basis is built inline to keep this orchestrator within its
    # local-variable budget.
    retirement_account_projections = project_retirement_accounts(
        build_projection_context(
            user_id,
            pay.all_periods,
            pay.current_period,
            planned_retirement_date,
            return_rate_override,
            build_employer_salary_basis(
                salary_profiles, planned_retirement_date, merit_horizon_years,
            ),
        )
    )

    gap_net_biweekly = _compute_gap_net_biweekly(
        salary_profiles, planned_retirement_date, pay, pension.salary_by_year,
        merit_horizon_years,
    )

    # CRIT-04 / E-12: route both SWR call sites (here and the slider in
    # ``compute_slider_defaults``) through ``_resolve_swr_fraction`` so an
    # explicit stored zero is honoured everywhere -- no truthiness
    # fallback to the default for a real zero.
    swr = (
        swr_override
        if swr_override is not None
        else _resolve_swr_fraction(settings)
    )
    gap_result = retirement_gap_calculator.calculate_gap(
        net_biweekly_pay=gap_net_biweekly,
        monthly_pension_income=pension.monthly_income,
        retirement_account_projections=retirement_account_projections,
        safe_withdrawal_rate=swr,
        estimated_tax_rate=_resolve_estimated_tax_rate(settings),
    )
    chart_data = _build_chart_data(gap_result, pension.monthly_income, swr)

    return {
        "gap_analysis": gap_result,
        "chart_data": chart_data,
        "pension_benefit": pension.benefit,
        "retirement_account_projections": retirement_account_projections,
        "settings": settings,
        "salary_profiles": salary_profiles,
        "pensions": pensions,
        # The projected final-year net biweekly, resolved SWR, planned
        # retirement date, and stored estimated tax rate the gap used --
        # exposed so ``retirement_readiness.compute_readiness_data`` can
        # re-run the net-frame gap (F1) and build the chart / countdown
        # without re-deriving them.  ``_resolve_estimated_tax_rate`` is a
        # cheap pure resolver, called inline here rather than stored, to
        # keep this orchestrator within its local-variable budget.
        "gap_net_biweekly": gap_net_biweekly,
        "swr": swr,
        "planned_retirement_date": planned_retirement_date,
        "estimated_tax_rate": _resolve_estimated_tax_rate(settings),
    }


def compute_slider_defaults(data):
    """Compute default slider values for the dashboard template.

    Derives the balance-weighted average return rate across the user's
    retirement / investment accounts and converts the stored
    fractional-decimal safe withdrawal rate to the percentage form the
    SWR slider expects.

    All arithmetic is performed in :class:`~decimal.Decimal` to satisfy
    the project's "no float for monetary or rate quantities" invariant
    (coding standards: Type Safety).  ``float()`` arithmetic at this
    layer historically introduced binary-fraction drift that surfaced
    only in the dashboard's two-decimal display (e.g. ``4.000000000001``
    rendered as ``4.00`` only by accident of the formatter); switching
    to ``Decimal`` removes that latent failure mode and keeps the
    rate-handling consistent with the column types in
    ``InvestmentParams.assumed_annual_return`` (``Numeric(7, 5)``) and
    ``UserSettings.safe_withdrawal_rate`` (``Numeric(5, 4)``).

    Args:
        data: The dict returned by :func:`compute_gap_data`.  Must
            carry ``settings`` (``UserSettings`` or ``None``) and
            ``retirement_account_projections`` (list of per-account
            projection dicts).

    Returns:
        dict with keys:

        - ``current_swr`` -- ``Decimal`` percentage with 0.01 precision
          (e.g. ``Decimal("4.00")`` for the 4% rule).  Falls back to
          :data:`_DEFAULT_SWR_PCT` when ``settings`` is ``None`` or the
          user has not set a custom rate.
        - ``current_return`` -- ``Decimal`` balance-weighted average of
          each account's ``assumed_annual_return``, expressed as a
          percentage with 0.01 precision.  Falls back to
          :data:`_DEFAULT_RETURN_PCT` when no account has a non-zero
          balance to contribute weight.

    Notes:
        A user-stored SWR of exactly ``Decimal("0")`` is treated as an
        explicit zero (not as "unset") and round-trips through this
        function as ``Decimal("0.00")``.  Only ``None`` triggers the
        default-fallback branch.  This matches the database semantics
        of the column (``Numeric(5,4)`` with ``CHECK (... >= 0 AND
        ... <= 1)``, NULL meaning "use the default").
    """
    settings = data["settings"]
    # CRIT-04 / E-12: scale the shared fractional resolver into
    # percent for the slider.  The previous code had a parallel
    # ``is None`` branch here while ``compute_gap_data`` used
    # truthiness ``or "0.04"`` -- two definitions of "missing SWR"
    # that disagreed on explicit zero (slider 0.00%, projection 4%).
    current_swr = (
        _resolve_swr_fraction(settings) * _PCT_SCALE
    ).quantize(_PCT_QUANTUM)

    projections = data.get("retirement_account_projections", [])
    total_balance = Decimal("0")
    weighted_return = Decimal("0")
    for proj in projections:
        acct = proj["account"]
        params = (
            db.session.query(InvestmentParams)
            .filter_by(account_id=acct.id)
            .first()
        )
        # CRIT-04 / E-12: zero is a real value, only ``None`` means
        # "unset."  A stable-value / cash sleeve at exactly 0.00%
        # return must contribute its balance to the weighted-average
        # denominator; the prior truthiness check dropped it
        # entirely (two $100k accounts at 0% and 7% reported 7.00%
        # instead of the true blended 3.50%).
        if params is not None and params.assumed_annual_return is not None:
            # F-11 / CRIT-04 / E-12: explicit ``is None`` guard, not
            # truthiness on a Decimal.  A stored zero balance is a real
            # zero (Account A at $0.00 contributes weight 0 to the
            # denominator); only the upstream-contract escape hatch
            # ``proj.get`` returning ``None`` triggers the fallback.
            bal = proj.get("current_balance", acct.current_anchor_balance)
            if bal is None:
                bal = Decimal("0")
            total_balance += bal
            weighted_return += bal * params.assumed_annual_return
    if total_balance > 0:
        current_return = (
            weighted_return / total_balance * _PCT_SCALE
        ).quantize(_PCT_QUANTUM)
    else:
        current_return = _DEFAULT_RETURN_PCT

    return {"current_swr": current_swr, "current_return": current_return}



# ── Private helpers: gap-data orchestration ──────────────────────


def _compute_pension_benefit(
    pensions: list[PensionProfile], merit_horizon_years: int,
) -> _PensionSummary:
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

    Returns:
        A :class:`_PensionSummary` bundling the most recent benefit, the
        summed monthly pension income, and the last salary-by-year
        series (the latter two default to ``Decimal("0")`` / ``None``
        when no pension qualifies).
    """
    benefit = None
    monthly_income = Decimal("0")
    salary_by_year = None
    for pension in pensions:
        if pension.planned_retirement_date and pension.salary_profile:
            profile = pension.salary_profile
            salary_by_year = pension_calculator.project_salaries_by_year(
                Decimal(str(profile.annual_salary)),
                profile.raises,
                date.today().year,
                pension.planned_retirement_date.year,
                merit_horizon_years,
            )
            benefit = pension_calculator.calculate_benefit(
                benefit_multiplier=pension.benefit_multiplier,
                consecutive_high_years=pension.consecutive_high_years,
                hire_date=pension.hire_date,
                planned_retirement_date=pension.planned_retirement_date,
                salary_by_year=salary_by_year,
            )
            monthly_income += benefit.monthly_benefit
    return _PensionSummary(benefit, monthly_income, salary_by_year)


def _compute_current_pay(
    user_id: int, salary_profiles: list[SalaryProfile],
) -> _CurrentPay:
    """Load the pay-period calendar and the current paycheck breakdown.

    Computes the current-period net pay via the raise-aware paycheck
    engine (F-20 / MED-06 / F-032) so the page agrees with the engine on
    both net and gross for the current period.  Returns zero / ``None``
    pay when the user has no active salary profile or no current period.

    Args:
        user_id: The authenticated user's ID.
        salary_profiles: The user's active salary profiles (the first is
            used as the current profile).

    Returns:
        A :class:`_CurrentPay` snapshot with the period calendar, the
        current period, the net biweekly pay, and the full breakdown.
    """
    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)
    net_biweekly = Decimal("0")
    current_breakdown = None
    # F-20 / MED-06 / F-032: take the current-period net (and, via the
    # returned breakdown, the gross) from the raise-aware paycheck engine
    # so the page agrees with the engine for the current period.  The
    # pre-Commit-17 ``annual_salary / pay_periods`` recompute silently
    # dropped any applicable SalaryRaise.
    if salary_profiles and current_period:
        profile = salary_profiles[0]
        tax_configs = load_tax_configs(user_id, profile)
        current_breakdown = paycheck_calculator.calculate_paycheck(
            profile, current_period, all_periods, tax_configs,
        )
        net_biweekly = current_breakdown.earnings.net_pay
    return _CurrentPay(
        all_periods, current_period, net_biweekly, current_breakdown,
    )


def _resolve_planned_retirement_date(
    pensions: list[PensionProfile], settings: UserSettings | None,
) -> date | None:
    """Derive the planned retirement date from pensions, else settings.

    Prefers the latest planned retirement date across the user's
    pensions; falls back to the retirement date stored on the user's
    settings.

    Args:
        pensions: The user's active pensions.
        settings: The user's :class:`UserSettings`, or ``None``.

    Returns:
        The resolved planned retirement date, or ``None`` when neither a
        pension nor the settings supply one.
    """
    pension_dates = [
        p.planned_retirement_date for p in pensions
        if p.planned_retirement_date is not None
    ]
    if pension_dates:
        return max(pension_dates)
    return settings.planned_retirement_date if settings else None



def _compute_gap_net_biweekly(
    salary_profiles: list[SalaryProfile],
    planned_retirement_date: date | None,
    pay: _CurrentPay,
    salary_by_year: list[tuple[int, Decimal]] | None,
    merit_horizon_years: int,
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
        salary_profiles: The user's active salary profiles.
        planned_retirement_date: The projection horizon, or ``None``.
        pay: The current-pay snapshot (net pay + breakdown gross source).
        salary_by_year: The pension-derived salary projection if one was
            already built, else ``None`` (recomputed here when needed).
        merit_horizon_years: The merit-raise horizon (years) forwarded to
            :func:`~app.services.pension_calculator.project_salaries_by_year`
            when the salary series is recomputed here.

    Returns:
        The projected final-year net biweekly pay, or ``pay.net_biweekly``
        when the projection cannot be performed.
    """
    if not (
        salary_profiles
        and planned_retirement_date
        and pay.net_biweekly > 0
    ):
        return pay.net_biweekly

    profile = salary_profiles[0]
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
            profile, date.today().year, planned_retirement_date.year,
            merit_horizon_years,
        )
    if not salary_by_year:
        return pay.net_biweekly

    final_salary = salary_by_year[-1][1]
    final_gross_biweekly = round_money(
        final_salary / (profile.pay_periods_per_year or 26)
    )
    return round_money(final_gross_biweekly * effective_take_home_rate)


def _resolve_estimated_tax_rate(
    settings: UserSettings | None,
) -> Decimal | None:
    """Resolve the estimated retirement tax rate from user settings.

    Args:
        settings: The user's :class:`UserSettings`, or ``None``.

    Returns:
        The stored estimated retirement tax rate as a Decimal, or
        ``None`` when settings are absent or the rate is unset.
    """
    # F-12 sibling: ``settings`` is a SQLAlchemy row -> explicit
    # ``is not None`` (post-CRIT-04 convention).  The
    # ``settings.estimated_retirement_tax_rate`` truthiness on the second
    # conjunct is the LOW-05 / CRIT-04 carry-open (build vs do not build a
    # bracket-based fallback for a stored zero tax rate) and is
    # intentionally NOT changed here.
    if settings is not None and settings.estimated_retirement_tax_rate:
        return Decimal(str(settings.estimated_retirement_tax_rate))
    return None


def _build_chart_data(
    gap_result: retirement_gap_calculator.RetirementGapAnalysis,
    monthly_pension_income: Decimal,
    swr: Decimal,
) -> dict[str, str]:
    """Build the retirement-gap chart's string-encoded data series.

    Computes the SWR-derived monthly investment income and the residual
    "gap" bar (income remaining after both pension and investment income
    are covered), then encodes every series as a string Decimal for the
    template's ``data-*`` attributes.

    Args:
        gap_result: The :class:`RetirementGapAnalysis` from
            ``retirement_gap_calculator.calculate_gap``.
        monthly_pension_income: The summed monthly pension income.
        swr: The active fractional safe-withdrawal rate.

    Returns:
        dict of string-encoded Decimals keyed ``pension``,
        ``investment_income``, ``gap``, ``pre_retirement``,
        ``chart_remaining``.
    """
    investment_income_decimal = (
        round_money(gap_result.projected_total_savings * swr / 12)
        if gap_result.projected_total_savings > 0
        else Decimal("0.00")
    )
    # MED-04 / E-17: the chart's "Gap" bar is the residual income
    # remaining after BOTH pension and SWR investment income have been
    # covered -- a different concept from ``gap_result.monthly_income_gap``
    # (post-pension only, before investments).  Computed server-side so
    # the data attribute is the value to render, not the inputs to add
    # together client-side (previously done in ``retirement_gap_chart.js``).
    covered = monthly_pension_income + investment_income_decimal
    chart_remaining = max(
        Decimal("0.00"),
        gap_result.pre_retirement_net_monthly - covered,
    )
    return {
        "pension": str(monthly_pension_income),
        "investment_income": str(investment_income_decimal),
        "gap": str(gap_result.monthly_income_gap),
        "pre_retirement": str(gap_result.pre_retirement_net_monthly),
        "chart_remaining": str(chart_remaining),
    }
