"""
Shekel Budget App -- Salary route package: projection view + breakdown stubs.

The full-width multi-period salary projection table (a first-class sibling
of the cockpit), plus the retired per-period breakdown URLs, which now
redirect into the cockpit (the paycheck anatomy lives there).  The old
breakdown pages were folded into the cockpit during the Fable 5 salary
rebuild; the endpoint names are preserved as ownership-checked redirect
stubs so existing bookmarks and in-app links keep resolving.
"""

from datetime import date

from flask import abort, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.utils.auth_helpers import get_or_404, require_owner
from app.models.salary_profile import SalaryProfile
from app.services import paycheck_calculator, salary_cockpit_service
from app.services.pay_calendar import calendar_for
from app.services.tax_config_service import load_tax_configs_for_periods
from app.routes.salary._bp import salary_bp
from app.routes.salary._helpers import _get_owned_profile_and_period


@salary_bp.route("/salary/<int:profile_id>/breakdown/<int:period_id>")
@login_required
@require_owner
def breakdown(profile_id, period_id):
    """Redirect the retired per-period breakdown page to the cockpit.

    Ownership of BOTH the profile and the period is verified before the
    302 so a cross-user id 404s here rather than leaking existence through
    an intermediate redirect (the project's "404 for both 'not found' and
    'not yours'" rule; account-detail precedent).  The cockpit focuses the
    requested profile and period via its ``?profile=&period=`` params.
    """
    profile, period = _get_owned_profile_and_period(
        profile_id, period_id, calendar_for(current_user.id),
    )
    return redirect(url_for(
        "salary.cockpit", profile=profile.id, period=period.period_id,
    ))


@salary_bp.route("/salary/<int:profile_id>/breakdown")
@login_required
@require_owner
def breakdown_current(profile_id):
    """Redirect the retired current-period breakdown to the cockpit.

    Verifies ownership of ``profile_id`` before redirecting so a
    cross-user request 404s here rather than producing a 302 that leaks
    the existence of the requested profile-id slot (audit commit C-31 /
    F-087).  Focuses the current period when one exists; when the user has
    no pay periods the redirect carries only the profile and the cockpit
    shows its generate-periods blocker.
    """
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    current_period = calendar_for(current_user.id).period_containing(
        date.today(),
    )
    if current_period is None:
        return redirect(url_for("salary.cockpit", profile=profile.id))
    return redirect(url_for(
        "salary.cockpit", profile=profile.id, period=current_period.period_id,
    ))


@salary_bp.route("/salary/<int:profile_id>/projection")
@login_required
@require_owner
def projection(profile_id):
    """Show salary projection table for all periods."""
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    periods = calendar_for(current_user.id).saved()
    # Resolve tax configs PER period year (DH-#30): the ~2-year horizon
    # spans multiple tax years, so each period uses its own year's
    # brackets/FICA -- substituting the latest CONFIGURED year at or before
    # it -- matching the recurrence engine that generates the stored grid
    # amounts.
    configs_by_year = load_tax_configs_for_periods(
        current_user.id, profile, periods,
    )
    breakdowns = paycheck_calculator.project_salary(
        profile, periods, configs_by_year=configs_by_year,
        calibration=profile.calibration,
    )

    # Pair periods with breakdowns
    projection_data = list(zip(periods, breakdowns))

    # The calculator badges raise_event on every period of a raise month, so
    # the ledger flags the raise badge/row-tint only on each run's first
    # paycheck -- the step -- not on every paycheck of the month (P-SA1,
    # projection surface).  The template checks ``period.period_id in`` this
    # set.
    raise_run_start_ids = salary_cockpit_service.raise_run_start_period_ids(
        projection_data,
    )

    # Summary framing above the ledger (restyled in P3): the next raise,
    # the next third paycheck, and the per-calendar-year net totals.  All
    # Decimal, derived from the same breakdowns the table renders (DRY).
    # ONE clock read for the page.  It was two, so a render crossing midnight
    # could answer "the next raise" from one day and "the next third paycheck"
    # from the next -- the defect pay-calendar plan step C2-f2d-3 fixed on the
    # cockpit, in the same package and outside ledger row **P55**'s census,
    # which is scoped to ``app/services/**`` and cannot see a ROUTE.
    today = date.today()
    projection_summary = {
        "next_raise": salary_cockpit_service.next_raise_after(
            projection_data, today,
        ),
        "next_third": salary_cockpit_service.next_third_after(
            projection_data, today,
        ),
        "yearly_nets": salary_cockpit_service.yearly_net_totals(projection_data),
    }

    return render_template(
        "salary/projection.html",
        profile=profile,
        projection_data=projection_data,
        projection_summary=projection_summary,
        raise_run_start_ids=raise_run_start_ids,
    )
