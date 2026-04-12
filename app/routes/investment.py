"""
Shekel Budget App -- Investment & Retirement Account Routes

Dashboard for investment/retirement accounts with compound growth
projection, contribution tracking, and employer contribution display.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.utils.auth_helpers import require_owner
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import AcctTypeEnum, RecurrencePatternEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.recurrence_rule import RecurrenceRule
from app.models.salary_profile import SalaryProfile
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transfer_template import TransferTemplate
from app.models.user import UserSettings
from app.schemas.validation import (
    InvestmentContributionTransferSchema,
    InvestmentParamsCreateSchema,
    InvestmentParamsUpdateSchema,
)
from app.services import (
    balance_calculator,
    growth_engine,
    pay_period_service,
    paycheck_calculator,
    transfer_recurrence,
)
from app.services.investment_projection import build_contribution_timeline, calculate_investment_inputs

logger = logging.getLogger(__name__)

investment_bp = Blueprint("investment", __name__)

_create_schema = InvestmentParamsCreateSchema()
_update_schema = InvestmentParamsUpdateSchema()
_transfer_schema = InvestmentContributionTransferSchema()

# Account types where contributions come from paycheck deductions
# (employer-sponsored plans) vs. bank transfers (individual accounts).
# No metadata flag exists on ref.account_types for this distinction,
# so we check specific types.  If new employer-plan types are added,
# update this set.
_DEDUCTION_PATH_TYPES = frozenset([AcctTypeEnum.K401, AcctTypeEnum.ROTH_401K])

TWO_PLACES = Decimal("0.01")


@investment_bp.route("/accounts/<int:account_id>/investment")
@login_required
@require_owner
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
            "calc_method_id": ded.calc_method_id,
            "annual_salary": profile.annual_salary,
            "pay_periods_per_year": profile.pay_periods_per_year or 26,
        })())

    # Load shadow income transactions in this account (contributions via transfers).
    period_ids = [p.id for p in all_periods]
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    acct_contributions = (
        db.session.query(Transaction)
        .options(joinedload(Transaction.status))
        .filter(
            Transaction.account_id == account_id,
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
        )
        .all()
    ) if period_ids else []

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

    # Build per-period contribution timeline from deductions and transfers.
    contributions = build_contribution_timeline(
        deductions=adapted_deductions,
        contribution_transactions=acct_contributions,
        periods=all_periods,
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
            contributions=contributions,
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
            chart_date_labels.append(p.start_date.strftime("%b %Y"))

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

    # Contribution setup prompt: show when params exist but no
    # contribution mechanism (recurring transfer or deduction) is linked.
    show_contribution_prompt = False
    is_deduction_path = False
    source_accounts = []
    default_source_id = None
    suggested_amount = Decimal("0")
    salary_profile_url = None

    if params:
        has_linked_deduction = bool(deductions)
        has_recurring_transfer = (
            db.session.query(TransferTemplate)
            .filter(
                TransferTemplate.user_id == current_user.id,
                TransferTemplate.to_account_id == account.id,
                TransferTemplate.is_active.is_(True),
                TransferTemplate.recurrence_rule_id.isnot(None),
            )
            .first()
        ) is not None

        show_contribution_prompt = (
            not has_linked_deduction and not has_recurring_transfer
        )

    if show_contribution_prompt:
        is_deduction_path = account.account_type_id in {
            ref_cache.acct_type_id(t) for t in _DEDUCTION_PATH_TYPES
        }

        if is_deduction_path:
            # Link to the active salary profile edit page for
            # deduction configuration.
            if active_profile:
                salary_profile_url = url_for(
                    "salary.edit_profile", profile_id=active_profile.id,
                )
            else:
                salary_profile_url = url_for("salary.list_profiles")
        else:
            # Transfer-path: compute suggested amount and load
            # eligible source accounts.
            if params.annual_contribution_limit:
                remaining = ytd_contributions or Decimal("0")
                today_date = date.today()
                remaining_periods = sum(
                    1 for p in all_periods
                    if p.start_date.year == today_date.year
                    and p.start_date >= today_date
                )
                remaining_limit = max(
                    params.annual_contribution_limit - remaining,
                    Decimal("0"),
                )
                suggested_amount = (
                    remaining_limit / max(remaining_periods, 1)
                ).quantize(TWO_PLACES)
            else:
                # No IRS limit (e.g. Brokerage) -- no default amount.
                suggested_amount = Decimal("0")

            source_accounts = (
                db.session.query(Account)
                .filter(
                    Account.user_id == current_user.id,
                    Account.is_active.is_(True),
                    Account.id != account.id,
                )
                .order_by(Account.sort_order, Account.name)
                .all()
            )
            checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
            for acct in source_accounts:
                if acct.account_type_id == checking_type_id:
                    default_source_id = acct.id
                    break

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
        show_contribution_prompt=show_contribution_prompt,
        is_deduction_path=is_deduction_path,
        source_accounts=source_accounts,
        default_source_id=default_source_id,
        suggested_amount=suggested_amount,
        salary_profile_url=salary_profile_url,
    )


@investment_bp.route("/accounts/<int:account_id>/investment/growth-chart")
@login_required
@require_owner
def growth_chart(account_id):
    """HTMX fragment: growth projection chart with adjustable horizon.

    Accepts optional what_if_contribution query parameter to overlay
    a hypothetical contribution scenario.  When provided, returns a
    dual-dataset chart (committed vs. what-if) and a comparison card
    showing the balance difference at the projection horizon.

    The what-if projection uses contributions=None with a flat
    periodic_contribution equal to the what-if amount.  Employer match
    is automatically recalculated by the engine's per-period loop.
    Annual contribution limits are enforced identically to the
    committed projection.

    Invalid or negative what-if values degrade gracefully to the
    single-line chart.  Zero is a valid what-if (growth-only scenario).
    """
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
            "calc_method_id": ded.calc_method_id,
            "annual_salary": profile.annual_salary,
            "pay_periods_per_year": profile.pay_periods_per_year or 26,
        })())

    period_ids = [p.id for p in all_periods]
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    acct_contributions = (
        db.session.query(Transaction)
        .options(joinedload(Transaction.status))
        .filter(
            Transaction.account_id == account_id,
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
        )
        .all()
    ) if period_ids else []

    inputs = calculate_investment_inputs(
        account_id=account_id,
        investment_params=params,
        deductions=adapted_deductions,
        all_contributions=acct_contributions,
        all_periods=all_periods,
        current_period=current_period,
        salary_gross_biweekly=salary_gross_biweekly,
    )

    # Build per-period contribution timeline from deductions and transfers.
    contributions = build_contribution_timeline(
        deductions=adapted_deductions,
        contribution_transactions=acct_contributions,
        periods=all_periods,
    )

    projection = growth_engine.project_balance(
        current_balance=current_balance,
        assumed_annual_return=params.assumed_annual_return,
        periods=periods,
        periodic_contribution=inputs.periodic_contribution,
        employer_params=inputs.employer_params,
        annual_contribution_limit=params.annual_contribution_limit,
        ytd_contributions_start=inputs.ytd_contributions,
        contributions=contributions,
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

    # --- What-If Contribution Calculator ---
    # Parse optional what-if amount from query parameters.  Invalid
    # or negative values degrade gracefully to single-line chart.
    # Zero is valid (growth-only scenario: "what if I stop contributing?").
    what_if_amount = None
    what_if_raw = request.args.get("what_if_contribution", type=str)
    if what_if_raw:
        try:
            what_if_amount = Decimal(what_if_raw)
        except (InvalidOperation, ValueError):
            what_if_amount = None
        else:
            if what_if_amount < Decimal("0"):
                what_if_amount = None

    what_if_balances = []
    comparison = None

    if what_if_amount is not None and periods:
        # What-if projection: flat contribution at hypothetical rate.
        # contributions=None means the engine uses periodic_contribution
        # for every period.  Employer match is recalculated automatically
        # because the engine passes each period's contribution to
        # calculate_employer_contribution().  Same employer_params work
        # unchanged -- they contain match percentages and gross salary,
        # not the employee amount.
        what_if_projection = growth_engine.project_balance(
            current_balance=current_balance,
            assumed_annual_return=params.assumed_annual_return,
            periods=periods,
            periodic_contribution=what_if_amount,
            employer_params=inputs.employer_params,
            annual_contribution_limit=params.annual_contribution_limit,
            ytd_contributions_start=inputs.ytd_contributions,
            contributions=None,
        )

        for pb in what_if_projection:
            what_if_balances.append(
                str(pb.end_balance.quantize(Decimal("0.01")))
            )

        # Comparison card: committed end vs. what-if end.
        if projection and what_if_projection:
            committed_end = projection[-1].end_balance.quantize(TWO_PLACES)
            whatif_end = what_if_projection[-1].end_balance.quantize(
                TWO_PLACES,
            )
            difference = (whatif_end - committed_end).quantize(TWO_PLACES)
            comparison = {
                "committed_end": committed_end,
                "whatif_end": whatif_end,
                "difference": difference,
                "is_positive": difference > Decimal("0"),
                "is_zero": difference == Decimal("0"),
            }

    return render_template(
        "investment/_growth_chart.html",
        chart_labels=chart_labels,
        chart_balances=chart_balances,
        chart_contributions=chart_contributions,
        what_if_balances=what_if_balances,
        what_if_amount=what_if_amount,
        comparison=comparison,
    )


@investment_bp.route(
    "/accounts/<int:account_id>/investment/create-contribution-transfer",
    methods=["POST"],
)
@login_required
@require_owner
def create_contribution_transfer(account_id):
    """Create a recurring biweekly transfer to an investment account.

    Creates a RecurrenceRule (every-period pattern), a TransferTemplate
    (from the selected source to the investment account), and generates
    Transfer records (with shadow transactions) for existing pay periods.

    The amount defaults to a suggested per-period contribution based on
    the annual limit and remaining periods.  The user may override it.
    """
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        flash("Account not found.", "danger")
        return redirect(url_for("savings.dashboard"))

    errors = _transfer_schema.validate(request.form)
    if errors:
        flash("Please correct the errors and try again.", "danger")
        return redirect(
            url_for("investment.dashboard", account_id=account_id),
        )

    data = _transfer_schema.load(request.form)
    source_account_id = data["source_account_id"]

    # Verify source account ownership (404 for both "not found" and
    # "not yours" per the security response rule).
    source_account = db.session.get(Account, source_account_id)
    if source_account is None or source_account.user_id != current_user.id:
        flash("Account not found.", "danger")
        return redirect(url_for("savings.dashboard"))

    if not source_account.is_active:
        flash("Source account is inactive.", "danger")
        return redirect(
            url_for("investment.dashboard", account_id=account_id),
        )

    if source_account_id == account_id:
        flash("Source and destination accounts must be different.", "danger")
        return redirect(
            url_for("investment.dashboard", account_id=account_id),
        )

    # Determine transfer amount: user override or suggested default.
    if "amount" in data and data["amount"] is not None:
        transfer_amount = data["amount"]
    else:
        # Compute suggested amount from annual limit and remaining periods.
        inv_params = (
            db.session.query(InvestmentParams)
            .filter_by(account_id=account_id)
            .first()
        )
        if inv_params and inv_params.annual_contribution_limit:
            transfer_amount = (
                inv_params.annual_contribution_limit / 26
            ).quantize(TWO_PLACES)
        else:
            transfer_amount = Decimal("500.00")

    # Create every-period recurrence rule (biweekly, matching paycheck).
    every_period_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.EVERY_PERIOD,
    )
    rule = RecurrenceRule(
        user_id=current_user.id,
        pattern_id=every_period_id,
    )
    db.session.add(rule)
    db.session.flush()

    # Create transfer template.
    template_name = f"{source_account.name} -> {account.name} Contribution"
    template = TransferTemplate(
        user_id=current_user.id,
        from_account_id=source_account.id,
        to_account_id=account.id,
        recurrence_rule_id=rule.id,
        name=template_name,
        default_amount=transfer_amount,
    )
    db.session.add(template)

    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        flash(
            "A recurring transfer with that name already exists.",
            "warning",
        )
        return redirect(
            url_for("investment.dashboard", account_id=account_id),
        )

    # Generate transfers for existing pay periods.
    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=current_user.id, is_baseline=True)
        .first()
    )
    if scenario:
        periods = pay_period_service.get_all_periods(current_user.id)
        transfer_recurrence.generate_for_template(
            template, periods, scenario.id,
        )

    db.session.commit()

    logger.info(
        "Created recurring contribution transfer for investment %d: "
        "$%s from account %d",
        account.id, transfer_amount, source_account.id,
    )
    flash(
        f"Recurring transfer of ${transfer_amount:,.2f} created "
        f"from {source_account.name} to {account.name}.",
        "success",
    )
    return redirect(
        url_for("investment.dashboard", account_id=account_id),
    )


@investment_bp.route("/accounts/<int:account_id>/investment/params", methods=["POST"])
@login_required
@require_owner
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
