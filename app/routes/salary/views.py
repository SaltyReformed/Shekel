"""
Shekel Budget App -- Salary route package: breakdown and projection views.

Read-only paycheck-breakdown pages for a single period (and the current
period) plus the full multi-period salary projection table.
"""

from flask import abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from markupsafe import Markup

from app.utils.auth_helpers import get_or_404, require_owner
from app.models.salary_profile import SalaryProfile
from app.models.pay_period import PayPeriod
from app.services import paycheck_calculator, pay_period_service
from app.services.tax_config_service import (
    load_tax_configs_for_periods,
    load_tax_configs_for_year,
)
from app.routes.salary._bp import salary_bp


@salary_bp.route("/salary/<int:profile_id>/breakdown/<int:period_id>")
@login_required
@require_owner
def breakdown(profile_id, period_id):
    """Show paycheck breakdown for a specific period."""
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    period = get_or_404(PayPeriod, period_id)
    if period is None:
        abort(404)

    periods = pay_period_service.get_all_periods(current_user.id)
    # Resolve the breakdown period's OWN tax year (DH-#30): viewing a
    # future-year period must use that year's brackets/FICA, not the
    # current year's.  Falls back to the current year when the period's
    # year has no configs.
    tax_configs = load_tax_configs_for_year(
        current_user.id, profile, period.start_date.year,
    )
    result = paycheck_calculator.calculate_paycheck(
        profile, period, periods, tax_configs,
        calibration=profile.calibration,
    )

    return render_template(
        "salary/breakdown.html",
        profile=profile,
        period=period,
        breakdown=result,
        periods=periods,
    )


@salary_bp.route("/salary/<int:profile_id>/breakdown")
@login_required
@require_owner
def breakdown_current(profile_id):
    """Show paycheck breakdown for the current period.

    Verifies ownership of ``profile_id`` before redirecting so a
    cross-user request 404s here rather than producing a 302 to
    :func:`breakdown` (which would also 404, but the intermediate
    redirect leaks the existence of the requested profile-id slot
    and breaks the project's "404 for both 'not found' and 'not
    yours'" security rule -- audit commit C-31 / F-087).
    """
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)
    current_period = pay_period_service.get_current_period(current_user.id)
    if not current_period:
        flash(Markup(
            'No pay periods found. '
            '<a href="' + url_for("pay_periods.generate_form") + '" class="alert-link">'
            'Generate pay periods</a> first.'
        ), "warning")
        return redirect(url_for("salary.list_profiles"))
    return redirect(url_for(
        "salary.breakdown",
        profile_id=profile.id,
        period_id=current_period.id,
    ))


@salary_bp.route("/salary/<int:profile_id>/projection")
@login_required
@require_owner
def projection(profile_id):
    """Show salary projection table for all periods."""
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    periods = pay_period_service.get_all_periods(current_user.id)
    # Resolve tax configs PER period year (DH-#30): the ~2-year horizon
    # spans multiple tax years, so each period uses its own year's
    # brackets/FICA (current-year fallback), matching the recurrence
    # engine that generates the stored grid amounts.
    configs_by_year = load_tax_configs_for_periods(
        current_user.id, profile, periods,
    )
    breakdowns = paycheck_calculator.project_salary(
        profile, periods, configs_by_year=configs_by_year,
        calibration=profile.calibration,
    )

    # Pair periods with breakdowns
    projection_data = list(zip(periods, breakdowns))

    return render_template(
        "salary/projection.html",
        profile=profile,
        projection_data=projection_data,
    )
