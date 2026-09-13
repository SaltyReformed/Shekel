"""
Shekel Budget App -- Salary route package: shared helpers.

Marshmallow schema singletons, form-field allowlists, unique-constraint
name constants, and the private helpers shared across the salary route
sub-modules (transaction regeneration, the calibration taxable-base and
rate-consistency helpers, and the HTMX-partial / redirect responders).
Constructed once at import time so every handler reuses the same
instances, preserving the pre-split monolith's behaviour.
"""

import logging
from datetime import date
from decimal import Decimal

from flask import abort, redirect, render_template, request, url_for
from flask_login import current_user

from app.utils.auth_helpers import get_or_404, log_refused_lookup
from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.account import Account
from app.models.ref import (
    CalcMethod,
    DeductionTiming,
    RaiseType,
)
from app.routes._recurrence_conflict_chooser import flash_retained_notice
from app.services import (
    account_service,
    paycheck_calculator,
    salary_regeneration,
)
from app.services.balance_at import BalanceContext
from app.services.payroll_basis import PayrollBasis
from app.services.pay_calendar import calendar_for
from app.services.tax_config_service import load_tax_configs_for_year
from app.schemas.validation import (
    CalibrationConfirmSchema,
    CalibrationSchema,
    DeductionCreateSchema,
    DeductionUpdateSchema,
    FicaConfigSchema,
    RaiseCreateSchema,
    RaiseUpdateSchema,
    SalaryProfileCreateSchema,
    SalaryProfileUpdateSchema,
    StateTaxConfigSchema,
    YtdTaxCheckpointSchema,
)

logger = logging.getLogger(__name__)

# Field allowlists for the update routes: which submitted form fields may
# be written back to each model via setattr.  Defined at module scope so
# each set is built once per process rather than on every request.
_PROFILE_UPDATE_FIELDS = {
    "name", "annual_salary", "filing_status_id", "state_code",
    "qualifying_children", "other_dependents",
    "additional_income", "additional_deductions", "extra_withholding",
}
_RAISE_UPDATE_FIELDS = {
    "raise_type_id", "effective_month", "effective_year",
    "percentage", "flat_amount", "is_recurring", "terminal_year", "notes",
}
_DEDUCTION_UPDATE_FIELDS = {
    "name", "deduction_timing_id", "calc_method_id", "amount",
    "deductions_per_year", "annual_cap", "inflation_enabled",
    "inflation_rate", "inflation_effective_month", "target_account_id",
}

# Names of the composite unique constraints that backstop the
# raise / deduction double-submit fixes (F-051 + F-052 / C-23).
# Each literal mirrors the model declaration in
# ``app/models/salary_raise.py`` and
# ``app/models/paycheck_deduction.py`` and the migration revision
# ``a3b9c2d40e15``; renaming a constraint requires a coordinated
# edit across all three sites.
_SALARY_RAISES_UNIQUE_CONSTRAINT = "uq_salary_raises_profile_type_year_month"
_PAYCHECK_DEDUCTIONS_UNIQUE_CONSTRAINT = "uq_paycheck_deductions_profile_name"

_create_schema = SalaryProfileCreateSchema()
_update_schema = SalaryProfileUpdateSchema()
_raise_schema = RaiseCreateSchema()
_raise_update_schema = RaiseUpdateSchema()
_deduction_schema = DeductionCreateSchema()
_deduction_update_schema = DeductionUpdateSchema()
_fica_schema = FicaConfigSchema()
_calibration_schema = CalibrationSchema()
_calibration_confirm_schema = CalibrationConfirmSchema()
_state_tax_schema = StateTaxConfigSchema()
_ytd_checkpoint_schema = YtdTaxCheckpointSchema()


def _get_owned_profile_and_period(profile_id, period_id, calendar):
    """Load an owned salary profile and pay period, or abort 404.

    The shared ownership gate for the two routes keyed on both a profile
    and a period -- the cockpit's anatomy fragment
    (:func:`app.routes.salary.cockpit.anatomy`) and the retired-breakdown
    redirect stub (:func:`app.routes.salary.views.breakdown`).  Verifies
    each id belongs to the current user BEFORE the caller acts, so a
    cross-user id 404s here per the project's "404 for both 'not found'
    and 'not yours'" rule rather than leaking existence.

    **The PERIOD half is now structural** (pay-calendar plan step C2-f2d-3).
    It was ``get_or_404(PayPeriod, period_id)`` -- a read of the global table
    with the owner compared afterwards.  The calendar holds ONE owner's
    paydays, so another owner's id is not found-and-rejected here; it is
    absent, and the 404 falls out of the lookup rather than out of a
    comparison a later edit could drop.
    **What that move COST, and what pays it back**: ``get_or_404`` emits an
    access event on each of its two denial branches, and for one commit the
    calendar's refusal emitted nothing, so a cross-user id probe against these
    two routes was silent in the audit log (adversarial code review, 2026-08-16).
    :func:`~app.utils.auth_helpers.log_refused_lookup` restores the trail at the
    one severity the ambiguity supports -- see that function for why it does not
    claim CROSS_USER.

    Args:
        profile_id: Primary key of the requested salary profile.
        period_id: Primary key of the requested pay period.
        calendar: The current user's
            :class:`~app.services.pay_calendar.PayCalendar`.  Taken as a
            parameter rather than derived here because both callers need the
            same calendar for the paycheck engine beside this call, and one
            derivation per request is the rule this arc is applying
            everywhere.

    Returns:
        A ``(profile, period)`` tuple -- the :class:`SalaryProfile` and the
        :class:`~app.services.pay_calendar.DerivedPeriod`, both the current
        user's.

    Raises:
        werkzeug.exceptions.NotFound: (via ``abort(404)``) when either id
            is missing or owned by another user.
    """
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)
    period = calendar.period_by_id(period_id)
    if period is None:
        # See :func:`~app.utils.auth_helpers.log_refused_lookup`: the calendar
        # cannot tell "no such period" from "not yours", and the refusal is
        # logged anyway so a cross-user probe still leaves a trail.
        log_refused_lookup("PayPeriod", period_id)
        abort(404)
    return profile, period


def _regenerate_salary_transactions(profile):
    """Regenerate the profile's paycheck rows and tell the owner what was kept.

    The salary package's adapter over
    :func:`~app.services.salary_regeneration.regenerate_salary_transactions`,
    the ONE walk every salary write is followed by (plan step salary:S3-f-3,
    ruling **R-SAL24** -- the walk moved below the route layer so the
    ``/retirement`` rail's end-year Save could reach it too).  What is left
    here is the Flask half the service cannot carry: this route package opens
    the read pass for the requester, and it FLASHES the rows the pass
    declined to touch.  The name and the signature are the pre-move ones, so
    every call site across this package -- and the tests that patch this
    name to simulate a stale race -- is untouched.  One thing did move: the
    pass is built before the service's own template guard runs, one scenario
    query for a profile without a template, a state ``create_profile`` never
    produces.

    Args:
        profile: The :class:`SalaryProfile` whose template prices the rows.
    """
    retained = salary_regeneration.regenerate_salary_transactions(
        BalanceContext.build(current_user.id), profile,
    )
    # **A RETAINED row is the owner's business, not just the log's** (plan
    # step R10-a, adversarial review): the service returns the ids rather
    # than dropping them, and this is where the salary page says so.
    flash_retained_notice(retained)


def _regenerate_all_salary_transactions():
    """Regenerate salary transactions for every active profile.

    Called after tax or FICA configuration changes so that projected
    paycheck amounts in the grid stay in sync with the salary profile
    page.  Without this, updating a tax rate would change the salary
    page's displayed net pay but leave stale amounts in the grid.
    """
    profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )
    for profile in profiles:
        _regenerate_salary_transactions(profile)


def _compute_total_pre_tax(profile):
    """Return the profile's pre-tax deduction total for the current period.

    Shared by ``calibrate_preview`` and ``calibrate_confirm`` to derive the
    taxable base (gross minus pre-tax deductions) the effective tax rates
    are computed against.  Returns ``Decimal("0")`` when the user has no
    current pay period, so the taxable base falls back to the full gross --
    mirroring the original inline behaviour in both handlers.
    """
    # A plain calendar read: this helper recomputes ONE paycheck and
    # regenerates nothing, so it needs the calendar the engine prices against
    # and none of the rest of a GenerationSchedule.  ONE derivation answers
    # both questions (pay-calendar plan step C2-f2d-3).
    calendar = calendar_for(current_user.id)
    current_period = calendar.period_containing(date.today())
    if not current_period:
        return Decimal("0")
    tax_configs = load_tax_configs_for_year(
        current_user.id, profile, current_period.start_date.year,
    )
    pay_breakdown = paycheck_calculator.calculate_paycheck(
        PayrollBasis(profile, calendar), current_period, tax_configs,
    )
    return pay_breakdown.deductions.total_pre_tax


def _reject_if_rates_inconsistent(data, derived_rates, taxable, profile_id):
    """Abort 422 if posted federal/state rates disagree with derived ones.

    The confirm form is fully server-generated from the preview, so a
    mismatch between the posted ``effective_federal_rate`` /
    ``effective_state_rate`` and the freshly-derived values signals
    tampering or stale browser state (E-20 / C19-2), not legitimate user
    error.  The schema covers FICA (divisor = posted ``actual_gross_pay``);
    federal/state's divisor is the live ``taxable`` base, available only
    here.  Tolerates the same one-cent-of-withholding slack the schema uses
    for FICA: a mismatch worth under one cent against ``taxable`` is below
    the ``Numeric(12, 10)`` storage precision and cannot signal real
    tampering.
    """
    one_cent = Decimal("0.01")
    failures: list[tuple[str, Decimal, Decimal, Decimal]] = []
    for posted_key, derived_value in (
        ("effective_federal_rate", derived_rates.effective_federal_rate),
        ("effective_state_rate", derived_rates.effective_state_rate),
    ):
        posted = Decimal(str(data[posted_key]))
        diff_dollars = abs(posted - derived_value) * taxable
        if diff_dollars > one_cent:
            failures.append((posted_key, posted, derived_value, diff_dollars))
    if failures:
        logger.info(
            "Rejected calibration confirm for profile %d "
            "(federal/state rate inconsistency, failures=%s)",
            profile_id,
            [
                f"{name} posted={posted} derived={derived} "
                f"mismatch=${mismatch}"
                for name, posted, derived, mismatch in failures
            ],
        )
        abort(422)


def _render_raises_partial(profile):
    """Return the raises table partial for HTMX updates."""
    # Refresh relationships
    db.session.refresh(profile)
    raise_types = db.session.query(RaiseType).all()
    return render_template(
        "salary/_raises_section.html",
        profile=profile,
        raise_types=raise_types,
        now_year=date.today().year,
    )


def _respond_after_raise_change(profile):
    """Respond after a raise mutation succeeds (or is idempotently absorbed).

    Returns the raises-section partial for an in-page HTMX swap, or a
    full-page redirect to the profile edit view for a normal form post.
    Centralises the response branch shared by the add/update/delete raise
    handlers so each has a single success exit point.
    """
    if request.headers.get("HX-Request"):
        return _render_raises_partial(profile)
    return redirect(url_for("salary.edit_profile", profile_id=profile.id))


def _render_deductions_partial(profile):
    """Return the deductions table partial for HTMX updates."""
    db.session.refresh(profile)
    deduction_timings = db.session.query(DeductionTiming).all()
    calc_methods = db.session.query(CalcMethod).all()
    investment_accounts = _get_investment_accounts(profile.user_id)
    return render_template(
        "salary/_deductions_section.html",
        profile=profile,
        deduction_timings=deduction_timings,
        calc_methods=calc_methods,
        investment_accounts=investment_accounts,
    )


def _respond_after_deduction_change(profile):
    """Respond after a deduction mutation succeeds (or is idempotently absorbed).

    Returns the deductions-section partial for an in-page HTMX swap, or a
    full-page redirect to the profile edit view for a normal form post.
    Counterpart to :func:`_respond_after_raise_change` for the add/update/
    delete deduction handlers.
    """
    if request.headers.get("HX-Request"):
        return _render_deductions_partial(profile)
    return redirect(url_for("salary.edit_profile", profile_id=profile.id))


def _get_investment_accounts(user_id):
    """Load retirement/investment accounts for the target account dropdown."""
    retirement_types = account_service.list_retirement_investment_account_types()
    type_ids = {rt.id for rt in retirement_types}
    if not type_ids:
        return []
    return (
        db.session.query(Account)
        .filter(
            Account.user_id == user_id,
            Account.account_type_id.in_(type_ids),
            Account.is_active.is_(True),
        )
        .order_by(Account.name)
        .all()
    )
