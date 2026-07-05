"""
Shekel Budget App -- Salary route package: state-tax and FICA config.

Update the per-user state flat-tax and FICA configuration rows, then
regenerate every active profile's salary transactions so the grid's
projected paycheck amounts stay in sync with the new rates.
"""

import logging
from decimal import Decimal

from flask import flash, redirect, request, url_for
from flask_login import current_user, login_required

from app.utils.auth_helpers import require_owner
from app.extensions import db
from app.models.ref import FilingStatus
from app.models.tax_config import FicaConfig, StateTaxConfig
from app import ref_cache
from app.enums import TaxTypeEnum
from app.routes.salary._bp import salary_bp
from app.routes.salary._helpers import (
    _fica_schema,
    _regenerate_all_salary_transactions,
    _state_tax_schema,
)

logger = logging.getLogger(__name__)


@salary_bp.route("/salary/tax-config")
@login_required
@require_owner
def tax_config():
    """Redirect to settings dashboard tax configuration section."""
    return redirect(url_for("settings.show", section="tax"))


@salary_bp.route("/salary/tax-config", methods=["POST"])
@login_required
@require_owner
def update_tax_config():
    """Update state tax flat rate."""
    errors = _state_tax_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("settings.show", section="tax"))

    data = _state_tax_schema.load(request.form)

    state_code = data["state_code"].upper()
    tax_year = data["tax_year"]

    # Convert percentage input (e.g. 3.99 → 0.0399) for storage.
    flat_rate = None
    if data.get("flat_rate") is not None:
        flat_rate = Decimal(str(data["flat_rate"])) / Decimal("100")

    standard_deduction = data.get("standard_deduction")

    # T-P5: the state config is filing-status-keyed, so there is one row per
    # filing status for a (state, year).  The settings form carries a single
    # rate + standard-deduction field, so this handler applies them UNIFORMLY
    # across every filing status.  (The seeded NC rows carry per-status
    # standard deductions; editing them here flattens them to the entered
    # value -- a known limitation until the settings UI grows a per-status
    # field.  The flat rate is status-independent, so applying it uniformly is
    # always correct.)
    existing_configs = (
        db.session.query(StateTaxConfig)
        .filter_by(user_id=current_user.id, state_code=state_code, tax_year=tax_year)
        .all()
    )

    if existing_configs:
        for state_config in existing_configs:
            if flat_rate is not None:
                state_config.flat_rate = flat_rate
            state_config.standard_deduction = standard_deduction
        flash(f"State tax config for {state_code} {tax_year} updated.", "success")
    else:
        if flat_rate is None:
            # Fail loud: creating a new state tax config requires a rate.
            # The schema leaves flat_rate optional (a stored NULL rate is a
            # legal no-tax state on an EXISTING row), but on first save
            # there is nothing to persist -- so reject instead of reporting
            # a silent success with no row, no commit, and no regeneration.
            flash(
                "A flat rate is required to create a state tax configuration.",
                "danger",
            )
            return redirect(url_for("settings.show", section="tax"))
        flat_type_id = ref_cache.tax_type_id(TaxTypeEnum.FLAT)
        for filing_status in db.session.query(FilingStatus).all():
            db.session.add(StateTaxConfig(
                user_id=current_user.id,
                tax_type_id=flat_type_id,
                filing_status_id=filing_status.id,
                state_code=state_code,
                tax_year=tax_year,
                flat_rate=flat_rate,
                standard_deduction=standard_deduction,
            ))
        flash(f"State tax config for {state_code} {tax_year} created.", "success")

    db.session.commit()

    # Regenerate salary transactions so the grid reflects the new rates.
    _regenerate_all_salary_transactions()
    db.session.commit()

    logger.info("user_id=%d updated state tax config for %s", current_user.id, state_code)
    return redirect(url_for("settings.show", section="tax"))


@salary_bp.route("/salary/fica-config", methods=["POST"])
@login_required
@require_owner
def update_fica_config():
    """Update FICA configuration."""
    errors = _fica_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("settings.show", section="tax"))

    data = _fica_schema.load(request.form)
    tax_year = data.pop("tax_year")

    # Convert percentage inputs (e.g. 6.2 → 0.062) for storage.
    for rate_field in ("ss_rate", "medicare_rate", "medicare_surtax_rate"):
        if rate_field in data and data[rate_field] is not None:
            data[rate_field] = Decimal(str(data[rate_field])) / Decimal("100")

    fica = (
        db.session.query(FicaConfig)
        .filter_by(user_id=current_user.id, tax_year=tax_year)
        .first()
    )

    if fica:
        for field_name, value in data.items():
            setattr(fica, field_name, value)
        flash(f"FICA config for {tax_year} updated.", "success")
    else:
        fica = FicaConfig(user_id=current_user.id, tax_year=tax_year, **data)
        db.session.add(fica)
        flash(f"FICA config for {tax_year} created.", "success")

    db.session.commit()

    # Regenerate salary transactions so the grid reflects the new rates.
    _regenerate_all_salary_transactions()
    db.session.commit()

    logger.info("user_id=%d updated FICA config for %d", current_user.id, tax_year)
    return redirect(url_for("settings.show", section="tax"))
