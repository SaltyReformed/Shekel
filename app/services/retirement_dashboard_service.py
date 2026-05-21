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

import logging
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.pension_profile import PensionProfile
from app.models.ref import AccountType
from app.models.salary_profile import SalaryProfile
from app.models.user import UserSettings
from app.services import (
    balance_resolver,
    growth_engine,
    income_service,
    pay_period_service,
    paycheck_calculator,
    pension_calculator,
    retirement_gap_calculator,
)
from app.services.investment_projection import adapt_deductions
from app.services.projection_inputs import (
    build_investment_projection_inputs,
    load_active_deductions_for_accounts,
    load_shadow_income_contributions_for_accounts,
)
from app.services.scenario_resolver import get_baseline_scenario

logger = logging.getLogger(__name__)

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
                        salary_profiles, pensions.
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

    # ── Pension benefit ─────────────────────────────────────────
    pension_benefit = None
    monthly_pension_income = Decimal("0")
    salary_by_year = None
    for pension in pensions:
        if pension.planned_retirement_date and pension.salary_profile:
            profile = pension.salary_profile
            start_year = date.today().year
            end_year = pension.planned_retirement_date.year
            salary_by_year = pension_calculator.project_salaries_by_year(
                Decimal(str(profile.annual_salary)),
                profile.raises,
                start_year,
                end_year,
            )
            benefit = pension_calculator.calculate_benefit(
                benefit_multiplier=pension.benefit_multiplier,
                consecutive_high_years=pension.consecutive_high_years,
                hire_date=pension.hire_date,
                planned_retirement_date=pension.planned_retirement_date,
                salary_by_year=salary_by_year,
            )
            pension_benefit = benefit
            monthly_pension_income += benefit.monthly_benefit

    # ── Net biweekly pay ────────────────────────────────────────
    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)
    net_biweekly = Decimal("0")
    # F-20 / MED-06 / F-032: the raise-aware engine gross from this
    # same breakdown is consumed at the gap-comparison block below
    # so the page agrees with the paycheck engine for the current
    # period on BOTH net (effective-take-home math) and gross
    # (effective-take-home-rate denominator).  Pre-Commit-17 the
    # gross side was an off-engine ``annual_salary / pay_periods``
    # recompute that silently dropped any applicable SalaryRaise.
    current_breakdown = None
    if salary_profiles:
        profile = salary_profiles[0]
        if current_period:
            from app.services.tax_config_service import load_tax_configs  # pylint: disable=import-outside-toplevel
            tax_configs = load_tax_configs(user_id, profile)
            current_breakdown = paycheck_calculator.calculate_paycheck(
                profile, current_period, all_periods, tax_configs,
            )
            net_biweekly = current_breakdown.net_pay

    # ── Load retirement/investment accounts ──────────────────────
    retirement_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT)
    investment_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.INVESTMENT)
    retirement_types = (
        db.session.query(AccountType)
        .filter(AccountType.category_id.in_([retirement_cat_id, investment_cat_id]))
        .all()
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

    # Derive planned retirement date from pensions or settings.
    pension_dates = [
        p.planned_retirement_date for p in pensions
        if p.planned_retirement_date is not None
    ]
    planned_retirement_date = (
        max(pension_dates) if pension_dates
        else (settings.planned_retirement_date if settings else None)
    )

    # ── Per-account growth projections ───────────────────────────
    retirement_account_projections = _project_retirement_accounts(
        user_id, accounts, all_periods, current_period,
        planned_retirement_date, salary_profiles, traditional_type_ids,
        return_rate_override,
    )

    # ── Projected salary for gap comparison ──────────────────────
    gap_net_biweekly = net_biweekly
    if salary_profiles and planned_retirement_date and net_biweekly > 0:
        profile = salary_profiles[0]
        # F-20 / MED-06 / F-032: ``current_breakdown.gross_biweekly`` is
        # the paycheck-engine value the ``net_biweekly`` line above
        # already paid for; reusing it here avoids re-running the
        # engine for an identical result and locks the
        # effective-take-home-rate denominator to the same per-period
        # gross the engine reports.  Pre-Commit-17 this site recomputed
        # ``annual_salary / pay_periods_per_year`` directly, which
        # silently dropped any applicable ``SalaryRaise`` row.
        current_gross_biweekly = (
            current_breakdown.gross_biweekly
            if current_breakdown is not None
            else Decimal("0.00")
        )
        if current_gross_biweekly > 0:
            effective_take_home_rate = net_biweekly / current_gross_biweekly
            if salary_by_year is None:
                salary_by_year = pension_calculator.project_salaries_by_year(
                    Decimal(str(profile.annual_salary)),
                    profile.raises,
                    date.today().year,
                    planned_retirement_date.year,
                )
            if salary_by_year:
                final_salary = salary_by_year[-1][1]
                final_gross_biweekly = (
                    final_salary / (profile.pay_periods_per_year or 26)
                ).quantize(Decimal("0.01"))
                gap_net_biweekly = (
                    final_gross_biweekly * effective_take_home_rate
                ).quantize(Decimal("0.01"))

    # ── Gap calculation ─────────────────────────────────────────
    # CRIT-04 / E-12: route both call sites through the same
    # resolver so an explicit zero SWR is honoured everywhere (no
    # truthiness fallback to the default for a stored zero).
    swr = (
        swr_override
        if swr_override is not None
        else _resolve_swr_fraction(settings)
    )
    # F-12 sibling: ``settings`` is a SQLAlchemy ``UserSettings`` row;
    # use explicit ``is not None`` (post-CRIT-04 convention).  The
    # ``settings.estimated_retirement_tax_rate`` truthiness on the
    # second conjunct is the LOW-05 / CRIT-04 carry-open (build vs do
    # not build a bracket-based fallback for a stored zero tax rate)
    # and is intentionally NOT changed here.
    tax_rate = (
        Decimal(str(settings.estimated_retirement_tax_rate))
        if settings is not None and settings.estimated_retirement_tax_rate
        else None
    )

    gap_result = retirement_gap_calculator.calculate_gap(
        net_biweekly_pay=gap_net_biweekly,
        monthly_pension_income=monthly_pension_income,
        retirement_account_projections=retirement_account_projections,
        safe_withdrawal_rate=swr,
        planned_retirement_date=planned_retirement_date,
        estimated_tax_rate=tax_rate,
    )

    investment_income_decimal = (
        (gap_result.projected_total_savings * swr / 12).quantize(Decimal("0.01"))
        if gap_result.projected_total_savings > 0
        else Decimal("0.00")
    )
    # MED-04 / E-17: the chart's "Gap" bar is the residual income
    # remaining after BOTH pension and SWR investment income have
    # been covered.  This is a different concept from
    # ``gap_result.monthly_income_gap`` (post-pension only, before
    # investments) and was previously computed in JS
    # (``retirement_gap_chart.js``).  The server computes it here so
    # the chart's data attribute is the value to render, not the
    # inputs to add together client-side.
    covered = monthly_pension_income + investment_income_decimal
    chart_remaining = max(
        Decimal("0.00"),
        gap_result.pre_retirement_net_monthly - covered,
    )

    chart_data = {
        "pension": str(monthly_pension_income),
        "investment_income": str(investment_income_decimal),
        "gap": str(gap_result.monthly_income_gap),
        "pre_retirement": str(gap_result.pre_retirement_net_monthly),
        "chart_remaining": str(chart_remaining),
    }

    return {
        "gap_analysis": gap_result,
        "chart_data": chart_data,
        "pension_benefit": pension_benefit,
        "retirement_account_projections": retirement_account_projections,
        "settings": settings,
        "salary_profiles": salary_profiles,
        "pensions": pensions,
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


# ── Private helpers ──────────────────────────────────────────────


def _project_retirement_accounts(
    user_id, accounts, all_periods, current_period,
    planned_retirement_date, salary_profiles, traditional_type_ids,
    return_rate_override,
):
    """Project each retirement/investment account forward to retirement.

    Returns a list of dicts with account, current_balance,
    projected_balance, is_traditional, annual_return_rate.
    """
    account_ids = [a.id for a in accounts]
    period_ids = [p.id for p in all_periods]

    # F-22 / Commit 18: shared deduction batch loader; replaces the
    # filter-shape duplicate that previously lived inline here and in
    # savings_dashboard_service / year_end_summary_service.
    deductions_by_account = load_active_deductions_for_accounts(
        user_id, account_ids,
    )

    # F-22 / Commit 18: shared batch shadow-income loader.
    all_acct_contributions = load_shadow_income_contributions_for_accounts(
        account_ids, period_ids,
    )

    # F-20 / MED-06 / F-032: raise-aware paycheck-engine value, not
    # the off-engine ``annual_salary / pay_periods_per_year`` recompute
    # that silently dropped any applicable ``SalaryRaise`` row.  The
    # value feeds ``calculate_investment_inputs`` as the basis for the
    # employer-match cap; under-stating it under-stated the match.
    salary_gross_biweekly = income_service.get_current_gross_biweekly(
        user_id,
    )

    # Synthetic projection periods to retirement date.
    synthetic_periods = []
    if planned_retirement_date:
        synthetic_periods = growth_engine.generate_projection_periods(
            start_date=date.today(),
            end_date=planned_retirement_date,
        )

    # Compute actual current balances via the canonical entries-aware
    # producer (E-25 / CRIT-01 / F-009 / R-1: Commit 8).
    # ``balances_for`` owns the transaction query (entries eager-loaded)
    # and resolves the anchor via the dated ``AccountAnchorHistory``
    # SoT, so each retirement / investment account's "current balance"
    # input to the gap calculation matches the figure rendered on the
    # grid and the /investment dashboard for the same inputs.
    scenario = get_baseline_scenario(user_id)
    acct_balance_map = {}
    # F-12 sibling: ``scenario`` is a SQLAlchemy ``Scenario`` row;
    # use explicit ``is not None`` (post-CRIT-04 convention).
    # ``Account.current_anchor_balance`` is ``NOT NULL`` so no
    # ``or Decimal("0")`` fallback is needed here -- the prior
    # truthiness was dead defence on a stored zero.
    if scenario is not None and period_ids:
        for acct in accounts:
            anchor = acct.current_anchor_balance
            if acct.current_anchor_period_id is not None:
                bals = balance_resolver.balances_for(
                    acct, scenario.id, all_periods,
                ).balances
                acct_balance_map[acct.id] = (
                    bals.get(current_period.id, anchor)
                    if current_period else anchor
                )
            else:
                acct_balance_map[acct.id] = anchor

    # Project each account.
    retirement_account_projections = []
    for acct in accounts:
        params = (
            db.session.query(InvestmentParams)
            .filter_by(account_id=acct.id)
            .first()
        )
        balance = acct_balance_map.get(
            acct.id, acct.current_anchor_balance,
        )
        projected_balance = balance
        effective_return = None

        projection_periods = synthetic_periods
        if not projection_periods and current_period:
            projection_periods = [
                p for p in all_periods
                if p.period_index >= current_period.period_index
            ]

        if params is not None and projection_periods:
            acct_deductions = deductions_by_account.get(acct.id, [])
            adapted_deductions = adapt_deductions(acct_deductions)

            acct_contributions = [
                t for t in all_acct_contributions
                if t.account_id == acct.id
            ]

            inputs = build_investment_projection_inputs(
                acct.id, params, adapted_deductions, acct_contributions,
                all_periods, current_period, salary_gross_biweekly,
            )

            annual_return = (
                return_rate_override
                if return_rate_override is not None
                else params.assumed_annual_return
            )
            effective_return = annual_return

            proj = growth_engine.project_balance(
                current_balance=balance,
                assumed_annual_return=annual_return,
                periods=projection_periods,
                periodic_contribution=inputs.periodic_contribution,
                employer_params=inputs.employer_params,
                annual_contribution_limit=inputs.annual_contribution_limit,
                ytd_contributions_start=inputs.ytd_contributions,
            )
            if proj:
                projected_balance = proj[-1].end_balance

        retirement_account_projections.append({
            "account": acct,
            "current_balance": balance,
            "projected_balance": projected_balance,
            "is_traditional": acct.account_type_id in traditional_type_ids,
            "annual_return_rate": effective_return,
        })

    return retirement_account_projections
