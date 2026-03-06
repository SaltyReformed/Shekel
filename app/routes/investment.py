"""
Shekel Budget App — Investment & Retirement Account Routes

Dashboard for investment/retirement accounts with compound growth
projection, contribution tracking, and employer contribution display.
"""

import logging
from datetime import date
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.salary_profile import SalaryProfile
from app.models.transfer import Transfer
from app.models.ref import AccountType
from app.schemas.validation import (
    InvestmentParamsCreateSchema,
    InvestmentParamsUpdateSchema,
)
from app.services import growth_engine, pay_period_service, paycheck_calculator

logger = logging.getLogger(__name__)

investment_bp = Blueprint("investment", __name__)

_create_schema = InvestmentParamsCreateSchema()
_update_schema = InvestmentParamsUpdateSchema()

# Account types that are "traditional" (pre-tax, taxed on withdrawal).
TRADITIONAL_TYPES = frozenset({"401k", "traditional_ira"})


@investment_bp.route("/accounts/<int:account_id>/investment")
@login_required
def dashboard(account_id):
    """Investment/retirement account dashboard with growth projection."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        flash("Account not found.", "danger")
        return redirect(url_for("savings.dashboard"))

    params = (
        db.session.query(InvestmentParams)
        .filter_by(account_id=account_id)
        .first()
    )

    all_periods = pay_period_service.get_all_periods(current_user.id)
    current_period = pay_period_service.get_current_period(current_user.id)

    # Current balance from anchor.
    current_balance = account.current_anchor_balance or Decimal("0.00")

    # Find paycheck deductions targeting this account.
    deductions = (
        db.session.query(PaycheckDeduction)
        .join(SalaryProfile)
        .filter(
            SalaryProfile.user_id == current_user.id,
            SalaryProfile.is_active.is_(True),
            PaycheckDeduction.target_account_id == account_id,
            PaycheckDeduction.is_active.is_(True),
        )
        .all()
    )

    # Calculate per-period contribution from paycheck deductions.
    periodic_contribution = Decimal("0")
    gross_biweekly = Decimal("0")
    for ded in deductions:
        profile = ded.salary_profile
        pay_per_year = profile.pay_periods_per_year or 26
        salary = Decimal(str(profile.annual_salary))
        gross = (salary / pay_per_year).quantize(Decimal("0.01"))
        gross_biweekly = gross  # use the last one for employer calc
        amt = Decimal(str(ded.amount))
        if ded.calc_method and ded.calc_method.name == "percentage":
            amt = (gross * amt).quantize(Decimal("0.01"))
        periodic_contribution += amt

    # Also count transfers into this account as contributions.
    if current_period:
        period_ids = [p.id for p in all_periods]
        transfer_contributions = (
            db.session.query(Transfer)
            .filter(
                Transfer.to_account_id == account_id,
                Transfer.pay_period_id.in_(period_ids),
                Transfer.is_deleted.is_(False),
            )
            .all()
        )
        # Average transfer amount per period for projection.
        if transfer_contributions:
            total_xfer = sum(Decimal(str(t.amount)) for t in transfer_contributions)
            num_periods_with_xfer = len(set(t.pay_period_id for t in transfer_contributions))
            if num_periods_with_xfer > 0:
                periodic_contribution += (total_xfer / num_periods_with_xfer).quantize(
                    Decimal("0.01")
                )

    # YTD contributions.
    ytd_contributions = _calculate_ytd_contributions(
        account_id, all_periods, current_period
    )

    # Build employer params.
    employer_params = None
    employer_contribution_per_period = Decimal("0")
    if params and params.employer_contribution_type != "none":
        employer_params = {
            "type": params.employer_contribution_type,
            "flat_percentage": params.employer_flat_percentage or Decimal("0"),
            "match_percentage": params.employer_match_percentage or Decimal("0"),
            "match_cap_percentage": params.employer_match_cap_percentage or Decimal("0"),
            "gross_biweekly": gross_biweekly,
        }
        employer_contribution_per_period = growth_engine.calculate_employer_contribution(
            employer_params, periodic_contribution
        )

    # Project balances forward.
    projection = []
    chart_labels = []
    chart_balances = []
    chart_contributions = []

    if params and current_period:
        future_periods = [
            p for p in all_periods if p.period_index >= current_period.period_index
        ]
        projection = growth_engine.project_balance(
            current_balance=current_balance,
            assumed_annual_return=params.assumed_annual_return,
            periods=future_periods,
            periodic_contribution=periodic_contribution,
            employer_params=employer_params,
            annual_contribution_limit=params.annual_contribution_limit,
            ytd_contributions_start=ytd_contributions,
        )

        cumulative_contrib = Decimal("0")
        for pb in projection:
            chart_labels.append(pb.period_id)
            chart_balances.append(str(pb.end_balance.quantize(Decimal("0.01"))))
            cumulative_contrib += pb.contribution + pb.employer_contribution
            chart_contributions.append(
                str((current_balance + cumulative_contrib).quantize(Decimal("0.01")))
            )

    # Contribution limit info.
    limit_info = None
    if params and params.annual_contribution_limit:
        limit_info = {
            "limit": params.annual_contribution_limit,
            "ytd": ytd_contributions,
            "pct": min(100, int(
                ytd_contributions / params.annual_contribution_limit * 100
            )) if params.annual_contribution_limit > 0 else 0,
        }

    # Get period labels for chart (date strings).
    period_map = {p.id: p for p in all_periods}
    chart_date_labels = []
    for pid in chart_labels:
        p = period_map.get(pid)
        if p:
            chart_date_labels.append(p.start_date.strftime("%b %d"))

    return render_template(
        "investment/dashboard.html",
        account=account,
        params=params,
        current_balance=current_balance,
        periodic_contribution=periodic_contribution,
        employer_contribution_per_period=employer_contribution_per_period,
        employer_params=employer_params,
        limit_info=limit_info,
        projection=projection,
        chart_labels=chart_date_labels,
        chart_balances=chart_balances,
        chart_contributions=chart_contributions,
    )


@investment_bp.route("/accounts/<int:account_id>/investment/params", methods=["POST"])
@login_required
def update_params(account_id):
    """Create or update investment parameters."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        flash("Account not found.", "danger")
        return redirect(url_for("savings.dashboard"))

    params = (
        db.session.query(InvestmentParams)
        .filter_by(account_id=account_id)
        .first()
    )

    # Convert percentage inputs from form (e.g. 7 → 0.07) before validation.
    form_data = _convert_percentage_inputs(request.form)

    if params:
        errors = _update_schema.validate(form_data)
        if errors:
            flash(f"Validation error: {errors}", "danger")
            return redirect(url_for("investment.dashboard", account_id=account_id))
        data = _update_schema.load(form_data)
        _PARAM_FIELDS = {
            "assumed_annual_return", "annual_contribution_limit",
            "contribution_limit_year", "employer_contribution_type",
            "employer_flat_percentage", "employer_match_percentage",
            "employer_match_cap_percentage",
        }
        for field_name, value in data.items():
            if field_name in _PARAM_FIELDS:
                setattr(params, field_name, value)
        flash("Investment parameters updated.", "success")
    else:
        errors = _create_schema.validate(form_data)
        if errors:
            flash(f"Validation error: {errors}", "danger")
            return redirect(url_for("investment.dashboard", account_id=account_id))
        data = _create_schema.load(form_data)
        params = InvestmentParams(account_id=account_id, **data)
        db.session.add(params)
        flash("Investment parameters created.", "success")

    db.session.commit()
    logger.info(
        "user_id=%d updated investment params for account %d",
        current_user.id, account_id,
    )
    return redirect(url_for("investment.dashboard", account_id=account_id))


def _convert_percentage_inputs(form):
    """Convert percentage form inputs (e.g. 7 → 0.07) to decimal values."""
    data = dict(form)
    pct_fields = [
        "assumed_annual_return", "employer_flat_percentage",
        "employer_match_percentage", "employer_match_cap_percentage",
    ]
    for field in pct_fields:
        if field in data and data[field]:
            try:
                data[field] = str(Decimal(data[field]) / Decimal("100"))
            except Exception:
                pass
    return data


def _calculate_ytd_contributions(account_id, all_periods, current_period):
    """Calculate year-to-date contributions to an investment account."""
    if not current_period:
        return Decimal("0")

    current_year = current_period.start_date.year
    ytd_period_ids = [
        p.id for p in all_periods
        if p.start_date.year == current_year
        and p.start_date <= current_period.start_date
    ]

    if not ytd_period_ids:
        return Decimal("0")

    transfers = (
        db.session.query(Transfer)
        .filter(
            Transfer.to_account_id == account_id,
            Transfer.pay_period_id.in_(ytd_period_ids),
            Transfer.is_deleted.is_(False),
        )
        .all()
    )

    return sum(
        (Decimal(str(t.amount)) for t in transfers),
        Decimal("0"),
    )
