"""
Shekel Budget App -- Auto Loan Routes

Dashboard and parameter updates for auto loan accounts.
"""

import logging
from datetime import date
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.auto_loan_params import AutoLoanParams
from app.schemas.validation import AutoLoanParamsCreateSchema, AutoLoanParamsUpdateSchema
from app.services import amortization_engine

logger = logging.getLogger(__name__)

auto_loan_bp = Blueprint("auto_loan", __name__)

_create_schema = AutoLoanParamsCreateSchema()
_params_schema = AutoLoanParamsUpdateSchema()


def _load_auto_loan_account(account_id):
    """Load and validate an auto loan account for the current user.

    Returns (account, params) or (None, None) if invalid.
    """
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return None, None

    if not account.account_type or account.account_type_id != ref_cache.acct_type_id(AcctTypeEnum.AUTO_LOAN):
        return None, None

    params = (
        db.session.query(AutoLoanParams)
        .filter_by(account_id=account.id)
        .first()
    )
    return account, params


def _build_chart_data(schedule):
    """Build chart data from an amortization schedule."""
    labels = []
    balances = []
    for row in schedule:
        labels.append(row.payment_date.strftime("%b %Y"))
        # Presentation boundary: float() for Chart.js JSON serialization.
        balances.append(float(row.remaining_balance))
    return labels, balances


@auto_loan_bp.route("/accounts/<int:account_id>/auto-loan")
@login_required
def dashboard(account_id):
    """Auto loan detail page with summary and balance chart."""
    account, params = _load_auto_loan_account(account_id)
    if account is None:
        flash("Auto loan account not found.", "danger")
        return redirect(url_for("savings.dashboard"))

    if params is None:
        return render_template("auto_loan/setup.html", account=account)

    remaining_months = amortization_engine.calculate_remaining_months(
        params.origination_date, params.term_months,
    )
    schedule_start = date.today().replace(day=1)
    summary = amortization_engine.calculate_summary(
        current_principal=Decimal(str(params.current_principal)),
        annual_rate=Decimal(str(params.interest_rate)),
        remaining_months=remaining_months,
        origination_date=schedule_start,
        payment_day=params.payment_day,
        term_months=params.term_months,
    )

    # Chart data.
    schedule = amortization_engine.generate_schedule(
        Decimal(str(params.current_principal)),
        Decimal(str(params.interest_rate)),
        remaining_months,
        payment_day=params.payment_day,
    )
    chart_labels, chart_standard = _build_chart_data(schedule)

    return render_template(
        "auto_loan/dashboard.html",
        account=account,
        params=params,
        summary=summary,
        chart_labels=chart_labels,
        chart_standard=chart_standard,
    )


@auto_loan_bp.route("/accounts/<int:account_id>/auto-loan/setup", methods=["POST"])
@login_required
def create_params(account_id):
    """Create initial auto loan parameters."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        flash("Account not found.", "danger")
        return redirect(url_for("savings.dashboard"))
    if not account.account_type or account.account_type_id != ref_cache.acct_type_id(AcctTypeEnum.AUTO_LOAN):
        flash("This account is not an auto loan.", "warning")
        return redirect(url_for("savings.dashboard"))

    # Check if params already exist.
    existing = db.session.query(AutoLoanParams).filter_by(account_id=account.id).first()
    if existing:
        flash("Auto loan parameters already configured.", "info")
        return redirect(url_for("auto_loan.dashboard", account_id=account_id))

    errors = _create_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return render_template("auto_loan/setup.html", account=account)

    data = _create_schema.load(request.form)

    # Convert percentage input (e.g. 5 → 0.05) for storage.
    if "interest_rate" in data:
        from decimal import Decimal as D
        data["interest_rate"] = D(str(data["interest_rate"])) / D("100")

    params = AutoLoanParams(account_id=account.id, **data)
    db.session.add(params)
    db.session.commit()

    logger.info("Created auto loan params for account %d", account.id)
    flash("Auto loan parameters configured.", "success")
    return redirect(url_for("auto_loan.dashboard", account_id=account_id))


@auto_loan_bp.route("/accounts/<int:account_id>/auto-loan/params", methods=["POST"])
@login_required
def update_params(account_id):
    """Update auto loan parameters."""
    account, params = _load_auto_loan_account(account_id)
    if account is None or params is None:
        flash("Auto loan account not found.", "danger")
        return redirect(url_for("savings.dashboard"))

    errors = _params_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("auto_loan.dashboard", account_id=account_id))

    data = _params_schema.load(request.form)

    # Convert percentage input (e.g. 5 → 0.05) for storage.
    if "interest_rate" in data:
        from decimal import Decimal as D
        data["interest_rate"] = D(str(data["interest_rate"])) / D("100")

    _PARAM_FIELDS = {"current_principal", "interest_rate", "payment_day", "term_months"}
    for field, value in data.items():
        if field in _PARAM_FIELDS:
            setattr(params, field, value)

    db.session.commit()
    logger.info("Updated auto loan params for account %d", account.id)
    flash("Auto loan parameters updated.", "success")
    return redirect(url_for("auto_loan.dashboard", account_id=account_id))
