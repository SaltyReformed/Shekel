"""
Shekel Budget App — Transfer Routes

CRUD for transfer templates and inline grid cell endpoints for transfers.
Follows the same patterns as templates.py (template CRUD) and
transactions.py (grid cell HTMX endpoints).
"""

import logging
from datetime import date
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for, jsonify
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.models.recurrence_rule import RecurrenceRule
from app.models.pay_period import PayPeriod
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.ref import RecurrencePattern, Status
from app.schemas.validation import (
    TransferTemplateCreateSchema,
    TransferTemplateUpdateSchema,
    TransferCreateSchema,
    TransferUpdateSchema,
)
from app.services import transfer_recurrence, pay_period_service
from app.services.account_resolver import resolve_grid_account
from app.exceptions import RecurrenceConflict

logger = logging.getLogger(__name__)

transfers_bp = Blueprint("transfers", __name__)

_create_schema = TransferTemplateCreateSchema()
_update_schema = TransferTemplateUpdateSchema()
_xfer_create_schema = TransferCreateSchema()
_xfer_update_schema = TransferUpdateSchema()


# ── Template Management Routes ──────────────────────────────────────


@transfers_bp.route("/transfers")
@login_required
def list_transfer_templates():
    """List all transfer templates for the current user."""
    templates = (
        db.session.query(TransferTemplate)
        .filter_by(user_id=current_user.id)
        .order_by(TransferTemplate.sort_order, TransferTemplate.name)
        .all()
    )
    return render_template("transfers/list.html", templates=templates)


@transfers_bp.route("/transfers/new", methods=["GET"])
@login_required
def new_transfer_template():
    """Display the transfer template creation form."""
    accounts = (
        db.session.query(Account)
        .filter_by(user_id=current_user.id, is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    patterns = db.session.query(RecurrencePattern).all()
    periods = pay_period_service.get_all_periods(current_user.id)
    current_period = pay_period_service.get_current_period(current_user.id)

    # Pre-fill account selection from query params (for quick-action links).
    prefill_from = request.args.get("from_account", type=int)
    prefill_to = request.args.get("to_account", type=int)

    return render_template(
        "transfers/form.html",
        template=None,
        accounts=accounts,
        patterns=patterns,
        periods=periods,
        current_period=current_period,
        prefill_from=prefill_from,
        prefill_to=prefill_to,
    )


@transfers_bp.route("/transfers", methods=["POST"])
@login_required
def create_transfer_template():
    """Create a new transfer template with optional recurrence rule."""
    errors = _create_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("transfers.new_transfer_template"))

    data = _create_schema.load(request.form)

    # Validate account ownership.
    from_acct = db.session.get(Account, data.get("from_account_id"))
    to_acct = db.session.get(Account, data.get("to_account_id"))
    if not from_acct or from_acct.user_id != current_user.id:
        flash("Invalid source account.", "danger")
        return redirect(url_for("transfers.new_transfer_template"))
    if not to_acct or to_acct.user_id != current_user.id:
        flash("Invalid destination account.", "danger")
        return redirect(url_for("transfers.new_transfer_template"))

    start_period_id = data.pop("start_period_id", None)
    end_date = data.pop("end_date", None)

    # Create the recurrence rule if a pattern was specified.
    rule = None
    pattern_name = data.pop("recurrence_pattern", None)
    if pattern_name:
        pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name=pattern_name)
            .one()
        )

        interval_n = data.pop("interval_n", 1)
        offset_periods = data.pop("offset_periods", 0)

        if pattern_name == "every_n_periods" and start_period_id and interval_n:
            start_period = db.session.get(PayPeriod, start_period_id)
            if not start_period or start_period.user_id != current_user.id:
                flash("Invalid start period.", "danger")
                return redirect(url_for("transfers.new_transfer_template"))
            offset_periods = start_period.period_index % interval_n

        rule = RecurrenceRule(
            user_id=current_user.id,
            pattern_id=pattern.id,
            interval_n=interval_n,
            offset_periods=offset_periods,
            day_of_month=data.pop("day_of_month", None),
            month_of_year=data.pop("month_of_year", None),
            start_period_id=start_period_id,
            end_date=end_date,
        )
        db.session.add(rule)
        db.session.flush()
    else:
        for key in ("interval_n", "offset_periods", "day_of_month", "month_of_year", "end_date"):
            data.pop(key, None)

    template = TransferTemplate(
        user_id=current_user.id,
        recurrence_rule_id=rule.id if rule else None,
        **data,
    )
    db.session.add(template)

    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        flash("A recurring transfer with that name already exists.", "warning")
        return redirect(url_for("transfers.list_transfer_templates"))

    # Auto-generate transfers from the rule into future periods.
    if rule:
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
    flash(f"Recurring transfer '{template.name}' created.", "success")
    return redirect(url_for("transfers.list_transfer_templates"))


@transfers_bp.route("/transfers/<int:template_id>/edit", methods=["GET"])
@login_required
def edit_transfer_template(template_id):
    """Display the transfer template edit form."""
    template = db.session.get(TransferTemplate, template_id)
    if template is None or template.user_id != current_user.id:
        flash("Recurring transfer not found.", "danger")
        return redirect(url_for("transfers.list_transfer_templates"))

    accounts = (
        db.session.query(Account)
        .filter_by(user_id=current_user.id, is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    patterns = db.session.query(RecurrencePattern).all()

    return render_template(
        "transfers/form.html",
        template=template,
        accounts=accounts,
        patterns=patterns,
        periods=[],
        current_period=None,
    )


@transfers_bp.route("/transfers/<int:template_id>", methods=["POST"])
@login_required
def update_transfer_template(template_id):
    """Update a transfer template and regenerate future transfers."""
    template = db.session.get(TransferTemplate, template_id)
    if template is None or template.user_id != current_user.id:
        flash("Recurring transfer not found.", "danger")
        return redirect(url_for("transfers.list_transfer_templates"))

    errors = _update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("transfers.edit_transfer_template", template_id=template_id))

    data = _update_schema.load(request.form)
    effective_from = data.pop("effective_from", date.today())
    data.pop("start_period_id", None)
    end_date = data.pop("end_date", None)

    # Update recurrence rule if pattern changed.
    pattern_name = data.pop("recurrence_pattern", None)
    if pattern_name:
        pattern = db.session.query(RecurrencePattern).filter_by(name=pattern_name).one()
        if template.recurrence_rule:
            template.recurrence_rule.pattern_id = pattern.id
            template.recurrence_rule.interval_n = data.pop("interval_n", 1)
            template.recurrence_rule.offset_periods = data.pop("offset_periods", 0)
            template.recurrence_rule.day_of_month = data.pop("day_of_month", None)
            template.recurrence_rule.month_of_year = data.pop("month_of_year", None)
            template.recurrence_rule.end_date = end_date
        else:
            rule = RecurrenceRule(
                user_id=current_user.id,
                pattern_id=pattern.id,
                interval_n=data.pop("interval_n", 1),
                offset_periods=data.pop("offset_periods", 0),
                day_of_month=data.pop("day_of_month", None),
                month_of_year=data.pop("month_of_year", None),
                end_date=end_date,
            )
            db.session.add(rule)
            db.session.flush()
            template.recurrence_rule_id = rule.id
    else:
        for key in ("interval_n", "offset_periods", "day_of_month", "month_of_year", "end_date"):
            data.pop(key, None)

    # Validate account ownership if accounts are being changed.
    if "from_account_id" in data:
        from_acct = db.session.get(Account, data["from_account_id"])
        if not from_acct or from_acct.user_id != current_user.id:
            flash("Invalid source account.", "danger")
            return redirect(url_for("transfers.edit_transfer_template", template_id=template_id))
    if "to_account_id" in data:
        to_acct = db.session.get(Account, data["to_account_id"])
        if not to_acct or to_acct.user_id != current_user.id:
            flash("Invalid destination account.", "danger")
            return redirect(url_for("transfers.edit_transfer_template", template_id=template_id))

    _TEMPLATE_UPDATE_FIELDS = {"name", "default_amount", "from_account_id", "to_account_id", "is_active", "sort_order"}
    for field, value in data.items():
        if field in _TEMPLATE_UPDATE_FIELDS:
            setattr(template, field, value)

    # Regenerate future transfers.
    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=current_user.id, is_baseline=True)
        .first()
    )
    if scenario and template.recurrence_rule:
        periods = pay_period_service.get_all_periods(current_user.id)
        try:
            transfer_recurrence.regenerate_for_template(
                template, periods, scenario.id, effective_from=effective_from,
            )
        except RecurrenceConflict as conflict:
            logger.warning(
                "Transfer recurrence conflict for template %d: %d overridden, %d deleted",
                template.id, len(conflict.overridden), len(conflict.deleted),
            )
            flash(
                f"Note: {len(conflict.overridden)} overridden and "
                f"{len(conflict.deleted)} deleted entries were kept as-is.",
                "warning",
            )

    db.session.commit()
    flash(f"Recurring transfer '{template.name}' updated.", "success")
    return redirect(url_for("transfers.list_transfer_templates"))


@transfers_bp.route("/transfers/<int:template_id>/delete", methods=["POST"])
@login_required
def delete_transfer_template(template_id):
    """Deactivate a transfer template (stops future generation, keeps history)."""
    template = db.session.get(TransferTemplate, template_id)
    if template is None or template.user_id != current_user.id:
        flash("Recurring transfer not found.", "danger")
        return redirect(url_for("transfers.list_transfer_templates"))

    template.is_active = False

    projected_status = db.session.query(Status).filter_by(name="projected").one()
    deleted_count = db.session.query(Transfer).filter(
        Transfer.transfer_template_id == template.id,
        Transfer.status_id == projected_status.id,
        Transfer.is_deleted.is_(False),
    ).update({"is_deleted": True}, synchronize_session="fetch")

    db.session.commit()

    flash(
        f"Recurring transfer '{template.name}' deactivated. "
        f"{deleted_count} projected transfer(s) removed.",
        "info",
    )
    return redirect(url_for("transfers.list_transfer_templates"))


@transfers_bp.route("/transfers/<int:template_id>/reactivate", methods=["POST"])
@login_required
def reactivate_transfer_template(template_id):
    """Reactivate a deactivated transfer template."""
    template = db.session.get(TransferTemplate, template_id)
    if template is None or template.user_id != current_user.id:
        flash("Recurring transfer not found.", "danger")
        return redirect(url_for("transfers.list_transfer_templates"))

    template.is_active = True

    projected_status = db.session.query(Status).filter_by(name="projected").one()
    restored_count = db.session.query(Transfer).filter(
        Transfer.transfer_template_id == template.id,
        Transfer.status_id == projected_status.id,
        Transfer.is_deleted.is_(True),
    ).update({"is_deleted": False}, synchronize_session="fetch")

    if template.recurrence_rule:
        scenario = (
            db.session.query(Scenario)
            .filter_by(user_id=current_user.id, is_baseline=True)
            .first()
        )
        if scenario:
            periods = pay_period_service.get_all_periods(current_user.id)
            transfer_recurrence.generate_for_template(
                template, periods, scenario.id, effective_from=date.today(),
            )

    db.session.commit()
    flash(
        f"Recurring transfer '{template.name}' reactivated. "
        f"{restored_count} projected transfer(s) restored.",
        "success",
    )
    return redirect(url_for("transfers.list_transfer_templates"))


# ── Grid Cell Routes ────────────────────────────────────────────────


@transfers_bp.route("/transfers/cell/<int:xfer_id>", methods=["GET"])
@login_required
def get_cell(xfer_id):
    """HTMX partial: return the display-mode cell for a transfer."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404
    account = resolve_grid_account(current_user.id, current_user.settings)
    return render_template(
        "transfers/_transfer_cell.html", xfer=xfer, account=account,
    )


@transfers_bp.route("/transfers/quick-edit/<int:xfer_id>", methods=["GET"])
@login_required
def get_quick_edit(xfer_id):
    """HTMX partial: return the inline amount edit form for a transfer."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404
    return render_template("transfers/_transfer_quick_edit.html", xfer=xfer)


@transfers_bp.route("/transfers/<int:xfer_id>/full-edit", methods=["GET"])
@login_required
def get_full_edit(xfer_id):
    """HTMX partial: return the full edit popover form for a transfer."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404
    statuses = db.session.query(Status).all()
    return render_template(
        "transfers/_transfer_full_edit.html", xfer=xfer, statuses=statuses,
    )


@transfers_bp.route("/transfers/instance/<int:xfer_id>", methods=["PATCH"])
@login_required
def update_transfer(xfer_id):
    """Update a transfer's fields (inline edit save)."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404

    errors = _xfer_update_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _xfer_update_schema.load(request.form)

    _TRANSFER_UPDATE_FIELDS = {"amount", "status_id", "name", "notes"}
    for field, value in data.items():
        if field in _TRANSFER_UPDATE_FIELDS:
            setattr(xfer, field, value)

    if xfer.transfer_template_id and "amount" in data:
        xfer.is_override = True

    db.session.commit()
    logger.info("user_id=%d updated transfer %d", current_user.id, xfer_id)

    account = resolve_grid_account(current_user.id, current_user.settings)
    response = render_template(
        "transfers/_transfer_cell.html", xfer=xfer, account=account,
    )
    return response, 200, {"HX-Trigger": "balanceChanged"}


@transfers_bp.route("/transfers/ad-hoc", methods=["POST"])
@login_required
def create_ad_hoc():
    """Create an ad-hoc (one-time) transfer."""
    errors = _xfer_create_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _xfer_create_schema.load(request.form)

    period = db.session.get(PayPeriod, data["pay_period_id"])
    if not period or period.user_id != current_user.id:
        return "Pay period not found", 404

    from_acct = db.session.get(Account, data["from_account_id"])
    to_acct = db.session.get(Account, data["to_account_id"])
    if not from_acct or from_acct.user_id != current_user.id:
        return "Invalid source account", 404
    if not to_acct or to_acct.user_id != current_user.id:
        return "Invalid destination account", 404

    projected = db.session.query(Status).filter_by(name="projected").one()

    xfer = Transfer(
        user_id=current_user.id,
        status_id=projected.id,
        **data,
    )
    db.session.add(xfer)
    db.session.commit()
    logger.info("user_id=%d created ad-hoc transfer (id=%d)", current_user.id, xfer.id)

    account = resolve_grid_account(current_user.id, current_user.settings)
    response = render_template(
        "transfers/_transfer_cell.html", xfer=xfer, account=account, wrap_div=True,
    )
    return response, 201, {"HX-Trigger": "balanceChanged"}


@transfers_bp.route("/transfers/instance/<int:xfer_id>", methods=["DELETE"])
@login_required
def delete_transfer(xfer_id):
    """Soft-delete a template transfer or hard-delete an ad-hoc transfer."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404

    if xfer.transfer_template_id:
        xfer.is_deleted = True
    else:
        db.session.delete(xfer)

    db.session.commit()
    logger.info("user_id=%d deleted transfer %d", current_user.id, xfer_id)
    return "", 200, {"HX-Trigger": "balanceChanged"}


# ── Transfer Status Actions ─────────────────────────────────────────


@transfers_bp.route("/transfers/instance/<int:xfer_id>/mark-done", methods=["POST"])
@login_required
def mark_done(xfer_id):
    """Mark a transfer as 'done' (settled)."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404

    status = db.session.query(Status).filter_by(name="done").one()
    xfer.status_id = status.id

    db.session.commit()
    logger.info("user_id=%d marked transfer %d as done", current_user.id, xfer_id)

    account = resolve_grid_account(current_user.id, current_user.settings)
    response = render_template(
        "transfers/_transfer_cell.html", xfer=xfer, account=account,
    )
    return response, 200, {"HX-Trigger": "balanceChanged"}


@transfers_bp.route("/transfers/instance/<int:xfer_id>/cancel", methods=["POST"])
@login_required
def cancel_transfer(xfer_id):
    """Mark a transfer as 'cancelled'."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404

    status = db.session.query(Status).filter_by(name="cancelled").one()
    xfer.status_id = status.id

    db.session.commit()
    logger.info("user_id=%d cancelled transfer %d", current_user.id, xfer_id)

    account = resolve_grid_account(current_user.id, current_user.settings)
    response = render_template(
        "transfers/_transfer_cell.html", xfer=xfer, account=account,
    )
    return response, 200, {"HX-Trigger": "balanceChanged"}


# ── Helpers ─────────────────────────────────────────────────────────


def _get_owned_transfer(xfer_id):
    """Fetch a transfer and verify it belongs to the current user."""
    xfer = db.session.get(Transfer, xfer_id)
    if xfer is None:
        return None
    if xfer.user_id != current_user.id:
        return None
    return xfer
