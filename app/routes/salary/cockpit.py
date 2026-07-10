"""
Shekel Budget App -- Salary route package: cockpit + anatomy fragment.

The salary section's landing page (``GET /salary``): a single cockpit for
the primary active profile with a net-per-paycheck hero, chip row,
net-pay staircase chart, the focused period's paycheck anatomy
(composition + deductions), the raise rules, and calibration status.
Replaces the removed profile-list page.  The anatomy card is refreshed in
place by :func:`anatomy` as the user steps between periods (HTMX
fragment).

Money math lives in the producers
(:mod:`app.services.salary_cockpit_service`, all
:class:`~decimal.Decimal`); this module casts to ``float`` only when
serializing the chart series to JSON for Chart.js -- the single sanctioned
float boundary.
"""

import json
from datetime import date

from flask import abort, render_template, request
from flask_login import current_user, login_required

from app.utils.auth_helpers import get_or_404, require_owner
from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.pay_period import PayPeriod
from app.services import paycheck_calculator, pay_period_service
from app.services import salary_cockpit_service
from app.services.tax_config_service import (
    load_tax_configs_for_periods,
    load_tax_configs_for_year,
)
from app.routes.salary._bp import salary_bp
from app.routes.salary._helpers import _get_owned_profile_and_period

# Empty-state discriminators the cockpit template branches on.  Strings
# are template-facing labels only (no business logic keys off them).
_EMPTY_NO_PROFILES = "no_profiles"
_EMPTY_NO_PERIODS = "no_periods"


def _calibration_active(profile):
    """Return True when the profile has an active pay-stub calibration."""
    calibration = profile.calibration
    return calibration is not None and calibration.is_active


def _base_cockpit_context():
    """Return the full cockpit context with every key defaulted.

    Guarantees every variable the cockpit template references is present
    on every render path (both empty states and the populated page), so a
    branch can never leave a variable undefined.
    """
    return {
        "empty_state": None,
        "profiles": [],
        "profile": None,
        "current_period": None,
        "focused_period": None,
        "is_third_paycheck": False,
        "raise_event": "",
        "chips": None,
        "composition": None,
        "deduction_rows": [],
        "prev_period_id": None,
        "next_period_id": None,
        "raises": [],
        "calibration": None,
        "chart_json": None,
        "salary_path_json": None,
        "salary_path": None,
    }


def _select_profile(profiles):
    """Resolve the focused profile from ``?profile=`` or the default.

    The default is the first active profile (the list is already ordered
    by ``sort_order`` then ``name``).  A supplied ``?profile`` must be
    owned AND active, else 404 -- the project's "404 for both not-found and
    not-yours" security rule (and an inactive profile is not a valid
    cockpit target).

    Args:
        profiles: The user's active profiles, ordered.

    Returns:
        The selected :class:`SalaryProfile`.
    """
    requested = request.args.get("profile", type=int)
    if requested is None:
        return profiles[0]
    profile = get_or_404(SalaryProfile, requested)
    if profile is None or not profile.is_active:
        abort(404)
    return profile


def _select_period(periods, current_period):
    """Resolve the focused period from ``?period=`` or the default.

    The default is the current period (or the first period when today
    falls outside every period).  A supplied ``?period`` must be an owned
    pay period, else 404.

    Args:
        periods: The user's pay periods, ordered by index.
        current_period: The period containing today, or ``None``.

    Returns:
        The selected :class:`PayPeriod`.
    """
    requested = request.args.get("period", type=int)
    if requested is None:
        return current_period if current_period is not None else periods[0]
    period = get_or_404(PayPeriod, requested)
    if period is None:
        abort(404)
    return period


def _anatomy_context(profile, period, periods, breakdown, calibration_active):
    """Build the shared context the ``_anatomy.html`` partial renders.

    Shared by the cockpit's initial render and the :func:`anatomy`
    period-stepping fragment so the swap contract is identical on both
    paths.  Prev/next period ids drive the stepper buttons and are
    ``None`` at the ends (disabling the button).

    Args:
        profile: The focused :class:`SalaryProfile`.
        period: The focused :class:`PayPeriod`.
        periods: The user's pay periods, ordered by index.
        breakdown: The focused period's paycheck breakdown.
        calibration_active: Whether the profile's calibration is active.

    Returns:
        A dict with ``profile``, ``focused_period``, ``is_third_paycheck``,
        ``raise_event``, ``composition``, ``deduction_rows``,
        ``prev_period_id``, and ``next_period_id``.  ``raise_event`` is the
        run-start-collapsed banner value: the focused period's raw event
        only when that period STARTS a raise run, else ``""`` -- so the
        banner marks the paycheck where a raise takes effect rather than
        repeating on every paycheck of the raise month (P-SA1).
    """
    period_ids = [p.id for p in periods]
    pos = period_ids.index(period.id)
    prev_id = period_ids[pos - 1] if pos > 0 else None
    next_id = period_ids[pos + 1] if pos < len(period_ids) - 1 else None
    # Collapse the raise banner to the run's first paycheck: the calculator
    # badges ``raise_event`` on EVERY period of a raise month, so compare the
    # focused period against its predecessor's event (computed directly, no
    # full projection) and show the banner only on the run start.
    prev_raise_event = (
        paycheck_calculator.get_raise_event(profile, periods[pos - 1])
        if pos > 0 else None
    )
    show_raise = salary_cockpit_service.raise_run_starts(
        breakdown.period.raise_event, prev_raise_event,
    )
    return {
        "profile": profile,
        "focused_period": period,
        "is_third_paycheck": breakdown.period.is_third_paycheck,
        "raise_event": breakdown.period.raise_event if show_raise else "",
        "composition": salary_cockpit_service.build_composition(
            breakdown, calibration_active,
        ),
        "deduction_rows": salary_cockpit_service.build_deduction_rows(breakdown),
        "prev_period_id": prev_id,
        "next_period_id": next_id,
    }


def _chart_jsonable(series):
    """Convert the Decimal chart series to a JSON-serializable dict.

    The one sanctioned ``float`` boundary: Chart.js consumes plain numbers,
    so each Decimal net/annual value is cast to ``float`` here (and dates to
    ISO strings) after all money math has completed upstream in Decimal.

    Args:
        series: The Decimal series from
            :func:`salary_cockpit_service.build_chart_series`.

    Returns:
        A dict of lists/strings ready for :func:`json.dumps`.
    """
    return {
        "periods": [
            {"start": point["start"].isoformat(), "net": float(point["net"])}
            for point in series["periods"]
        ],
        "thirds": [
            {"start": point["start"].isoformat(), "net": float(point["net"])}
            for point in series["thirds"]
        ],
        "raises": [
            {"start": event["start"].isoformat(), "label": event["label"]}
            for event in series["raises"]
        ],
        "today": series["today"].isoformat(),
    }


def _salary_path_jsonable(path):
    """Convert the Decimal salary-path series to a JSON-serializable dict.

    Args:
        path: The Decimal path from
            :func:`salary_cockpit_service.build_salary_path`.

    Returns:
        A dict of the points (ISO date + ``float`` annual) plus the
        precomputed ``end_label`` string.
    """
    return {
        "points": [
            {"start": point["start"].isoformat(), "annual": float(point["annual"])}
            for point in path["points"]
        ],
        "end_label": path["end_label"],
    }


@salary_bp.route("/salary")
@login_required
@require_owner
def cockpit():
    """Render the salary cockpit for the primary (or selected) active profile.

    Selection: active profiles ordered by ``(sort_order, name)``; the
    default profile is the first; ``?profile`` must be owned and active
    (404 otherwise); ``?period`` must be an owned period (404 otherwise),
    defaulting to the current period.

    Empty states render the same template: no active profiles -> a create
    CTA; no pay periods -> a generate-periods blocker (both with null
    chips/chart so nothing downstream divides by an absent figure).
    """
    profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .order_by(SalaryProfile.sort_order, SalaryProfile.name)
        .all()
    )
    if not profiles:
        context = _base_cockpit_context()
        context["empty_state"] = _EMPTY_NO_PROFILES
        return render_template("salary/cockpit.html", **context)

    profile = _select_profile(profiles)
    periods = pay_period_service.get_all_periods(current_user.id)
    current_period = pay_period_service.get_current_period(current_user.id)
    requested_period_id = request.args.get("period", type=int)
    # Block when there is no period to focus: no periods at all, or no
    # period covering today and none explicitly requested.  A lone stale
    # anchor period (no current period) is not a usable biweekly schedule,
    # so the user is sent to generate one -- the pre-rebuild
    # breakdown_current "No pay periods found" semantics.
    if not periods or (current_period is None and requested_period_id is None):
        context = _base_cockpit_context()
        context["empty_state"] = _EMPTY_NO_PERIODS
        context["profiles"] = profiles
        context["profile"] = profile
        context["raises"] = profile.raises
        context["calibration"] = profile.calibration
        return render_template("salary/cockpit.html", **context)

    focused_period = _select_period(periods, current_period)
    configs_by_year = load_tax_configs_for_periods(current_user.id, profile, periods)
    breakdowns = paycheck_calculator.project_salary(
        profile, periods, configs_by_year=configs_by_year,
        calibration=profile.calibration,
    )
    pairs = list(zip(periods, breakdowns))
    focused_breakdown = breakdowns[[p.id for p in periods].index(focused_period.id)]
    calibration_active = _calibration_active(profile)
    today = date.today()

    chart_series = salary_cockpit_service.build_chart_series(pairs, today)
    salary_path = salary_cockpit_service.build_salary_path(pairs, today)

    context = _base_cockpit_context()
    context.update(_anatomy_context(
        profile, focused_period, periods, focused_breakdown, calibration_active,
    ))
    context.update({
        "profiles": profiles,
        "current_period": current_period,
        "chips": salary_cockpit_service.build_chips(pairs, focused_breakdown, today),
        "raises": profile.raises,
        "calibration": profile.calibration,
        "chart_json": json.dumps(_chart_jsonable(chart_series)),
        "salary_path_json": json.dumps(_salary_path_jsonable(salary_path)),
        "salary_path": salary_path,
    })
    return render_template("salary/cockpit.html", **context)


@salary_bp.route("/salary/<int:profile_id>/anatomy/<int:period_id>")
@login_required
@require_owner
def anatomy(profile_id, period_id):
    """Return the paycheck-anatomy fragment for a period (HTMX stepping).

    Renders the composition card (the primary swap target) plus the
    deductions card (an ``hx-swap-oob`` sibling) so stepping to a
    neighbouring period updates both at once.  Ownership of both the
    profile and the period is verified (404 for not-found and not-yours).
    """
    profile, period = _get_owned_profile_and_period(profile_id, period_id)

    periods = pay_period_service.get_all_periods(current_user.id)
    # Resolve the period's OWN tax year (DH-#30), current-year fallback --
    # identical resolution to the projection path so the fragment and the
    # cockpit's initial render agree on a period's figures.
    tax_configs = load_tax_configs_for_year(
        current_user.id, profile, period.start_date.year,
    )
    breakdown = paycheck_calculator.calculate_paycheck(
        profile, period, periods, tax_configs,
        calibration=profile.calibration,
    )
    context = _anatomy_context(
        profile, period, periods, breakdown, _calibration_active(profile),
    )
    # ``oob=True`` marks the deductions card as an out-of-band swap so
    # stepping updates it alongside the composition card (the primary
    # target); the cockpit's initial inline include omits it.
    return render_template("salary/_anatomy.html", oob=True, **context)
