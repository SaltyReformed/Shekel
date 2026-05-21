"""
Shekel Budget App -- Investment & Retirement Account Routes

Thin HTTP layer for the investment / retirement dashboard, the HTMX
growth-chart fragment, the recurring-contribution-transfer creator,
and the investment-parameters POST handler.  The dashboard and
growth-chart data-assembly was extracted to
:mod:`app.services.investment_dashboard_service` in Commit 28
(MED-01 / S6-01): the route now matches the established
``savings.py`` thin-delegator shape.  The two POST handlers stay
here because they own HTTP-side validation, flash + redirect flows,
and the transactional unit-of-work boundary -- moving them to a
service would not collapse a duplication root.
"""

import logging
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import RecurrencePatternEnum
from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.recurrence_rule import RecurrenceRule
from app.models.transfer_template import TransferTemplate
from app.schemas.validation import (
    InvestmentContributionTransferSchema,
    InvestmentParamsCreateSchema,
    InvestmentParamsUpdateSchema,
)
from app.services import (
    investment_dashboard_service,
    pay_period_service,
    transfer_recurrence,
)
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.auth_helpers import get_or_404, require_owner

logger = logging.getLogger(__name__)

investment_bp = Blueprint("investment", __name__)

_create_schema = InvestmentParamsCreateSchema()
_update_schema = InvestmentParamsUpdateSchema()
_transfer_schema = InvestmentContributionTransferSchema()

TWO_PLACES = Decimal("0.01")
_DEFAULT_SUGGESTED_AMOUNT = Decimal("500.00")
_PAY_PERIODS_PER_YEAR = 26


@investment_bp.route("/accounts/<int:account_id>/investment")
@login_required
@require_owner
def dashboard(account_id):
    """Investment/retirement account dashboard with growth projection."""
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    ctx = investment_dashboard_service.compute_dashboard_data(
        current_user.id, account,
    )
    ctx["salary_profile_url"] = _resolve_salary_profile_url(
        ctx.pop("_salary_profile_action", None),
        ctx.pop("_active_profile_id", None),
    )
    return render_template("investment/dashboard.html", **ctx)


@investment_bp.route("/accounts/<int:account_id>/investment/growth-chart")
@login_required
@require_owner
def growth_chart(account_id):
    """HTMX fragment: growth projection chart with adjustable horizon.

    Accepts optional ``what_if_contribution`` query parameter to overlay
    a hypothetical contribution scenario.  When provided, returns a
    dual-dataset chart (committed vs. what-if) and a comparison card
    showing the balance difference at the projection horizon.

    Invalid or negative what-if values degrade gracefully to the
    single-line chart.  Zero is a valid what-if (growth-only scenario).
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("investment.dashboard", account_id=account_id))

    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return "", 404

    horizon_years = request.args.get("horizon_years", type=int, default=2)
    what_if_raw = request.args.get("what_if_contribution", type=str)

    ctx = investment_dashboard_service.compute_growth_chart_data(
        current_user.id, account, horizon_years, what_if_raw,
    )
    return render_template("investment/_growth_chart.html", **ctx)


def _resolve_salary_profile_url(action: str | None, profile_id: int | None):
    """Map service-side action hints to a flask URL string.

    The investment dashboard service does not import flask (boundary
    rule); it returns an action discriminator instead of a URL.
    Three states (mirroring the pre-Commit-28 inline route logic):

    * ``action == "edit"`` and ``profile_id is not None`` ->
      ``url_for("salary.edit_profile", profile_id=...)``.
    * ``action == "list"`` -> ``url_for("salary.list_profiles")``.
    * Otherwise (no contribution prompt or transfer path) ->
      ``None``, matching the pre-extraction default.
    """
    if action == "edit" and profile_id is not None:
        return url_for("salary.edit_profile", profile_id=profile_id)
    if action == "list":
        return url_for("salary.list_profiles")
    return None


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
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

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
    source_account = get_or_404(Account, source_account_id)
    if source_account is None:
        abort(404)

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
        #
        # E-12 / HIGH-06 (Commit 24): ``is not None``, not
        # truthiness.  A stored zero cap means "no contributions
        # allowed this year" -- with no user-supplied amount we
        # refuse to create the transfer rather than fall back to
        # the pre-fix $500 default (which silently overrode the
        # user's explicit zero cap).  ``None`` continues to mean
        # "no cap configured" and falls to the $500 UX default.
        inv_params = (
            db.session.query(InvestmentParams)
            .filter_by(account_id=account_id)
            .first()
        )
        if inv_params and inv_params.annual_contribution_limit is not None:
            limit = inv_params.annual_contribution_limit
            if limit == Decimal("0"):
                flash(
                    "Annual contribution limit is $0. Set a positive "
                    "limit before creating a recurring transfer, or "
                    "supply an explicit amount.",
                    "warning",
                )
                return redirect(
                    url_for("investment.dashboard", account_id=account_id),
                )
            transfer_amount = (limit / _PAY_PERIODS_PER_YEAR).quantize(TWO_PLACES)
        else:
            transfer_amount = _DEFAULT_SUGGESTED_AMOUNT

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
    scenario = get_baseline_scenario(current_user.id)
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
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    params = (
        db.session.query(InvestmentParams)
        .filter_by(account_id=account_id)
        .first()
    )

    # Convert percentage inputs from form (e.g. 7 -> 0.07) before validation.
    form_data = _convert_percentage_inputs(request.form)

    if params:
        errors = _update_schema.validate(form_data)
        if errors:
            flash("Please correct the highlighted errors and try again.", "danger")
            return redirect(url_for("investment.dashboard", account_id=account_id))
        data = _update_schema.load(form_data)
        param_fields = {
            "assumed_annual_return", "annual_contribution_limit",
            "contribution_limit_year", "employer_contribution_type",
            "employer_flat_percentage", "employer_match_percentage",
            "employer_match_cap_percentage",
        }
        for field_name, value in data.items():
            if field_name in param_fields:
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
    """Convert percentage form inputs (e.g. 7 -> 0.07) to decimal values."""
    data = dict(form)
    pct_fields = [
        "assumed_annual_return", "employer_flat_percentage",
        "employer_match_percentage", "employer_match_cap_percentage",
    ]
    for field in pct_fields:
        if field in data and data[field]:
            try:
                data[field] = str(Decimal(data[field]) / Decimal("100"))
            except InvalidOperation:
                # Narrow catch (C-46 / F-145): a non-numeric string
                # (e.g. "abc") raises ``decimal.InvalidOperation``.
                # Leave the raw value in place so the Marshmallow
                # schema rejects it with a field-level "Not a valid
                # number." message rather than a silent normalisation.
                pass
    return data
