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
from decimal import Decimal

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import EmployerContributionTypeEnum, RecurrencePatternEnum
from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import EmployerContributionType
from app.routes._redirect_target import RedirectTarget
from app.routes._transfer_creation_helpers import (
    build_recurring_transfer_template,
    flush_template_or_namedup_redirect,
    generate_transfers_for_all_periods,
    validate_and_resolve_source_account,
)
from app.schemas.validation import (
    InvestmentContributionTransferSchema,
    InvestmentParamsCreateSchema,
    InvestmentParamsUpdateSchema,
)
from app.services import investment_dashboard_service
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
    # #38: the employer-contribution-type <select> renders one option
    # per ref row (value = id), so the template never compares the
    # type name as a string.  ``default_*`` is the id pre-selected when
    # the account has no params row yet (the create case), so the
    # template defaults to NONE by id rather than a name literal.
    ctx["employer_contribution_types"] = (
        EmployerContributionType.query
        .order_by(EmployerContributionType.id)
        .all()
    )
    ctx["default_employer_contribution_type_id"] = (
        ref_cache.employer_contribution_type_id(
            EmployerContributionTypeEnum.NONE,
        )
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

    account = get_or_404(Account, account_id)
    if account is None:
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
    # Pylint: ``duplicate-code`` -- this route is the parallel near-fork of
    # ``loan.create_payment_transfer``: both run the same
    # validate-source -> compute-amount -> build-rule -> build-template ->
    # flush -> generate -> commit -> notify sequence, diverging only in the
    # amount derivation, the recurrence pattern, and the user-facing copy.
    # The substantive shared logic is already extracted into
    # ``app/routes/_transfer_creation_helpers.py``; what remains duplicated
    # is only the ORDER in which this route calls those helpers, which is
    # not worth coupling two distinct account domains (investment
    # contribution caps vs loan P&I/escrow derivation) behind a multi-field
    # parameter object to dedupe (coding-standards rule 13).  One-sided
    # ``duplicate-code`` disable (see plan.md Phase 2 notes); the partner
    # ``loan.create_payment_transfer`` stays un-disabled.
    # pylint: disable=duplicate-code
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    # Validate the form and resolve + ownership-check the source account
    # (shared with loan.create_payment_transfer).
    result = validate_and_resolve_source_account(
        _transfer_schema,
        dest_account_id=account_id,
        redirect=RedirectTarget("investment.dashboard", {"account_id": account_id}),
    )
    if isinstance(result, Response):
        return result
    source_account, data = result

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

    # Create transfer template via the shared builder (contributions do
    # not set derive_from_loan; loan-payment transfers do).
    template_name = f"{source_account.name} -> {account.name} Contribution"
    template = build_recurring_transfer_template(
        source_account=source_account,
        dest_account=account,
        rule=rule,
        name=template_name,
        default_amount=transfer_amount,
    )

    namedup_redirect = flush_template_or_namedup_redirect(
        redirect=RedirectTarget("investment.dashboard", {"account_id": account_id}),
    )
    if namedup_redirect is not None:
        return namedup_redirect

    # Generate transfers for existing pay periods.
    generate_transfers_for_all_periods(template)

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
    # pylint: enable=duplicate-code
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

    # Percent-to-fraction conversion is owned by the schemas' @pre_load
    # (F-17 / Commit 12 of the follow-up plan): the route forwards the
    # raw form payload and reads back the loaded fraction directly.
    if params:
        errors = _update_schema.validate(request.form)
        if errors:
            flash("Please correct the highlighted errors and try again.", "danger")
            return redirect(url_for("investment.dashboard", account_id=account_id))
        data = _update_schema.load(request.form)
        param_fields = {
            "assumed_annual_return", "annual_contribution_limit",
            "contribution_limit_year", "employer_contribution_type_id",
            "employer_flat_percentage", "employer_match_percentage",
            "employer_match_cap_percentage",
        }
        for field_name, value in data.items():
            if field_name in param_fields:
                setattr(params, field_name, value)
        flash("Investment parameters updated.", "success")
    else:
        errors = _create_schema.validate(request.form)
        if errors:
            flash("Please correct the highlighted errors and try again.", "danger")
            return redirect(url_for("investment.dashboard", account_id=account_id))
        data = _create_schema.load(request.form)
        params = InvestmentParams(account_id=account_id, **data)
        db.session.add(params)
        flash("Investment parameters created.", "success")

    db.session.commit()
    logger.info(
        "user_id=%d updated investment params for account %d",
        current_user.id, account_id,
    )
    return redirect(url_for("investment.dashboard", account_id=account_id))
