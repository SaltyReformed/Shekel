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
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.account import Account
from app.models.ref import (
    CalcMethod,
    DeductionTiming,
    RaiseType,
)
from app.exceptions import RecurrenceConflict
from app.services import (
    account_service,
    paycheck_calculator,
    pay_period_service,
    recurrence_engine,
)
from app.services.scenario_resolver import get_baseline_scenario
from app.services.tax_config_service import load_tax_configs
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
)

logger = logging.getLogger(__name__)

# Field allowlists for the update routes: which submitted form fields may
# be written back to each model via setattr.  Defined at module scope so
# each set is built once per process rather than on every request.
_PROFILE_UPDATE_FIELDS = {
    "name", "annual_salary", "filing_status_id", "state_code",
    "pay_periods_per_year", "qualifying_children", "other_dependents",
    "additional_income", "additional_deductions", "extra_withholding",
}
_RAISE_UPDATE_FIELDS = {
    "raise_type_id", "effective_month", "effective_year",
    "percentage", "flat_amount", "is_recurring", "notes",
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


def _regenerate_salary_transactions(profile):
    """Recalculate and update linked template transactions."""
    if not profile.template:
        return

    scenario = get_baseline_scenario(current_user.id)
    if not scenario:
        return

    periods = pay_period_service.get_all_periods(current_user.id)
    tax_configs = load_tax_configs(current_user.id, profile)

    # Update the template's default_amount to the current net pay
    current_period = pay_period_service.get_current_period(current_user.id)
    if current_period:
        pay_breakdown = paycheck_calculator.calculate_paycheck(
            profile, current_period, periods, tax_configs,
            calibration=profile.calibration,
        )
        profile.template.default_amount = pay_breakdown.net_pay

    # Regenerate transactions
    try:
        recurrence_engine.regenerate_for_template(
            profile.template, periods, scenario.id,
            effective_from=date.today(),
        )
    except RecurrenceConflict as e:
        logger.warning("Recurrence conflict during salary regeneration: %s", e)
    except SQLAlchemyError:
        # Narrow catch (C-46 / F-145): logging hook that re-raises.
        # SQLAlchemy errors from the regenerate flush get the
        # profile-id context here as well as in the calling route's
        # ``except SQLAlchemyError`` block.  Non-SQLAlchemy
        # exceptions still propagate to the caller without this
        # extra log line; the caller's logger.exception then
        # records the user-id + profile-id context.
        logger.exception("Failed to regenerate salary transactions for profile %d", profile.id)
        raise


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
    current_period = pay_period_service.get_current_period(current_user.id)
    if not current_period:
        return Decimal("0")
    periods = pay_period_service.get_all_periods(current_user.id)
    tax_configs = load_tax_configs(current_user.id, profile)
    pay_breakdown = paycheck_calculator.calculate_paycheck(
        profile, current_period, periods, tax_configs,
    )
    return pay_breakdown.total_pre_tax


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
