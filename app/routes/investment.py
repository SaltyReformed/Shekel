"""
Shekel Budget App -- Investment & Retirement Account Routes

Dashboard for investment/retirement accounts with compound growth
projection, contribution tracking, and employer contribution display.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.salary_profile import SalaryProfile
from app.models.transaction import Transaction
from app.models.ref import AccountType, TransactionType
from app.models.user import UserSettings
from app.schemas.validation import (
    InvestmentParamsCreateSchema,
    InvestmentParamsUpdateSchema,
)
from app.models.scenario import Scenario
from app.services import balance_calculator, growth_engine, pay_period_service, paycheck_calculator
from app.services.investment_projection import calculate_investment_inputs

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

    # Compute current balance by running ALL account transactions
    # (including shadow transactions from transfers) through the balance
    # calculator.  Using the raw anchor_balance would miss transfer
    # deposits, understating the balance by the total of all missed
    # contributions.  Follows the grid.py account-scoped query pattern.
    anchor_balance = account.current_anchor_balance or Decimal("0.00")
    anchor_period_id = account.current_anchor_period_id or (
        current_period.id if current_period else None
    )

    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=current_user.id, is_baseline=True)
        .first()
    )

    period_ids = [p.id for p in all_periods]
    acct_transactions = (
        db.session.query(Transaction)
        .filter(
            Transaction.account_id == account_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.scenario_id == scenario.id,
            Transaction.is_deleted.is_(False),
        )
        .all()
    ) if scenario and period_ids else []

    balances = {}
    if anchor_period_id and scenario:
        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=anchor_balance,
            anchor_period_id=anchor_period_id,
            periods=all_periods,
            transactions=acct_transactions,
        )

    # Current balance includes shadow transactions (transfer deposits).
    current_balance = (
        balances.get(current_period.id, anchor_balance)
        if current_period else anchor_balance
    )

    # Load active salary profile for employer contribution gross calculation.
    salary_gross_biweekly = Decimal("0")
    active_profile = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .first()
    )
    if active_profile:
        salary_gross_biweekly = (
            Decimal(str(active_profile.annual_salary))
            / (active_profile.pay_periods_per_year or 26)
        ).quantize(Decimal("0.01"))

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

    # Adapt deductions for the shared helper.
    adapted_deductions = []
    for ded in deductions:
        profile = ded.salary_profile
        adapted_deductions.append(type("D", (), {
            "amount": ded.amount,
            "calc_method_name": ded.calc_method.name if ded.calc_method else "flat",
            "annual_salary": profile.annual_salary,
            "pay_periods_per_year": profile.pay_periods_per_year or 26,
        })())

    # Load shadow income transactions in this account (contributions via transfers).
    period_ids = [p.id for p in all_periods]
    income_type = db.session.query(TransactionType).filter_by(name="income").first()
    acct_contributions = (
        db.session.query(Transaction)
        .filter(
            Transaction.account_id == account_id,
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type.id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
        )
        .all()
    ) if period_ids and income_type else []

    inputs = calculate_investment_inputs(
        account_id=account_id,
        investment_params=params,
        deductions=adapted_deductions,
        all_contributions=acct_contributions,
        all_periods=all_periods,
        current_period=current_period,
        salary_gross_biweekly=salary_gross_biweekly,
    )

    periodic_contribution = inputs.periodic_contribution
    employer_params = inputs.employer_params
    employer_contribution_per_period = Decimal("0")
    if employer_params:
        employer_contribution_per_period = growth_engine.calculate_employer_contribution(
            employer_params, periodic_contribution
        )
    ytd_contributions = inputs.ytd_contributions

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

    # Default horizon for the growth chart slider.
    settings = (
        db.session.query(UserSettings)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if settings and settings.planned_retirement_date:
        default_horizon = max(1, (settings.planned_retirement_date.year - date.today().year))
    elif all_periods:
        last_period = all_periods[-1]
        default_horizon = max(1, (last_period.end_date.year - date.today().year) + 1)
    else:
        default_horizon = 10

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
        default_horizon=default_horizon,
    )


@investment_bp.route("/accounts/<int:account_id>/investment/growth-chart")
@login_required
def growth_chart(account_id):
    """HTMX fragment: growth projection chart with adjustable horizon."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("investment.dashboard", account_id=account_id))

    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return "", 404

    params = (
        db.session.query(InvestmentParams)
        .filter_by(account_id=account_id)
        .first()
    )
    empty = {"chart_labels": [], "chart_balances": [], "chart_contributions": []}

    if not params:
        return render_template("investment/_growth_chart.html", **empty)

    horizon_years = request.args.get("horizon_years", type=int, default=2)
    horizon_years = max(1, min(horizon_years, 40))

    # Compute current balance from transactions (including shadow
    # deposits from transfers), not just the raw anchor.
    anchor_bal = account.current_anchor_balance or Decimal("0.00")
    anchor_pid = account.current_anchor_period_id

    real_periods = pay_period_service.get_all_periods(current_user.id)
    cur_period = pay_period_service.get_current_period(current_user.id)
    chart_scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=current_user.id, is_baseline=True)
        .first()
    )

    current_balance = anchor_bal
    if chart_scenario and real_periods and anchor_pid:
        real_period_ids = [p.id for p in real_periods]
        chart_txns = (
            db.session.query(Transaction)
            .filter(
                Transaction.account_id == account_id,
                Transaction.pay_period_id.in_(real_period_ids),
                Transaction.scenario_id == chart_scenario.id,
                Transaction.is_deleted.is_(False),
            )
            .all()
        )
        chart_bals, _ = balance_calculator.calculate_balances(
            anchor_balance=anchor_bal,
            anchor_period_id=anchor_pid,
            periods=real_periods,
            transactions=chart_txns,
        )
        if cur_period and cur_period.id in chart_bals:
            current_balance = chart_bals[cur_period.id]

    # Generate synthetic future periods for the requested horizon.
    end_date = date.today() + timedelta(days=horizon_years * 365)
    periods = growth_engine.generate_projection_periods(
        start_date=date.today(),
        end_date=end_date,
    )

    if not periods:
        return render_template("investment/_growth_chart.html", **empty)

    # Load contribution inputs.
    all_periods = pay_period_service.get_all_periods(current_user.id)
    current_period = pay_period_service.get_current_period(current_user.id)

    salary_gross_biweekly = Decimal("0")
    active_profile = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .first()
    )
    if active_profile:
        salary_gross_biweekly = (
            Decimal(str(active_profile.annual_salary))
            / (active_profile.pay_periods_per_year or 26)
        ).quantize(Decimal("0.01"))

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

    adapted_deductions = []
    for ded in deductions:
        profile = ded.salary_profile
        adapted_deductions.append(type("D", (), {
            "amount": ded.amount,
            "calc_method_name": ded.calc_method.name if ded.calc_method else "flat",
            "annual_salary": profile.annual_salary,
            "pay_periods_per_year": profile.pay_periods_per_year or 26,
        })())

    period_ids = [p.id for p in all_periods]
    income_type = db.session.query(TransactionType).filter_by(name="income").first()
    acct_contributions = (
        db.session.query(Transaction)
        .filter(
            Transaction.account_id == account_id,
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type.id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
        )
        .all()
    ) if period_ids and income_type else []

    inputs = calculate_investment_inputs(
        account_id=account_id,
        investment_params=params,
        deductions=adapted_deductions,
        all_contributions=acct_contributions,
        all_periods=all_periods,
        current_period=current_period,
        salary_gross_biweekly=salary_gross_biweekly,
    )

    projection = growth_engine.project_balance(
        current_balance=current_balance,
        assumed_annual_return=params.assumed_annual_return,
        periods=periods,
        periodic_contribution=inputs.periodic_contribution,
        employer_params=inputs.employer_params,
        annual_contribution_limit=params.annual_contribution_limit,
        ytd_contributions_start=inputs.ytd_contributions,
    )

    period_map = {p.id: p for p in periods}
    chart_labels = []
    chart_balances = []
    chart_contributions = []
    cumulative_contrib = Decimal("0")

    for pb in projection:
        p = period_map.get(pb.period_id)
        if p:
            chart_labels.append(p.start_date.strftime("%b %Y"))
        chart_balances.append(str(pb.end_balance.quantize(Decimal("0.01"))))
        cumulative_contrib += pb.contribution + pb.employer_contribution
        chart_contributions.append(
            str((current_balance + cumulative_contrib).quantize(Decimal("0.01")))
        )

    return render_template(
        "investment/_growth_chart.html",
        chart_labels=chart_labels,
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
            flash("Please correct the highlighted errors and try again.", "danger")
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
            flash("Please correct the highlighted errors and try again.", "danger")
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
