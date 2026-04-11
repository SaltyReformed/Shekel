"""
Shekel Budget App -- Transfer Routes

CRUD for transfer templates and inline grid cell endpoints for transfers.
Follows the same patterns as templates.py (template CRUD) and
transactions.py (grid cell HTMX endpoints).
"""

import logging
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for, jsonify
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.models.recurrence_rule import RecurrenceRule
from app.models.pay_period import PayPeriod
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.ref import RecurrencePattern, Status
from app import ref_cache
from app.enums import RecurrencePatternEnum, StatusEnum
from app.utils import archive_helpers
from app.schemas.validation import (
    TransferTemplateCreateSchema,
    TransferTemplateUpdateSchema,
    TransferCreateSchema,
    TransferUpdateSchema,
)
from app.services import transfer_recurrence, transfer_service, pay_period_service
from app.services.account_resolver import resolve_grid_account
from app.exceptions import NotFoundError, RecurrenceConflict, ValidationError as ShekelValidationError

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
    """List all transfer templates for the current user.

    Separates templates into active and archived lists for the UI.
    Both lists inherit the same ordering (sort_order, name).
    """
    templates = (
        db.session.query(TransferTemplate)
        .filter_by(user_id=current_user.id)
        .order_by(TransferTemplate.sort_order, TransferTemplate.name)
        .all()
    )
    active_templates = [t for t in templates if t.is_active]
    archived_templates = [t for t in templates if not t.is_active]
    return render_template(
        "transfers/list.html",
        active_templates=active_templates,
        archived_templates=archived_templates,
    )


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
    categories = (
        db.session.query(Category)
        .filter_by(user_id=current_user.id, is_active=True)
        .order_by(Category.group_name, Category.item_name)
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
        categories=categories,
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
    pattern_id_str = data.pop("recurrence_pattern", None)
    if pattern_id_str:
        pattern = db.session.get(RecurrencePattern, int(pattern_id_str))
        if pattern is None:
            flash("Invalid recurrence pattern.", "danger")
            return redirect(url_for("transfers.new_transfer_template"))

        interval_n = data.pop("interval_n", 1)
        offset_periods = data.pop("offset_periods", 0)

        if int(pattern_id_str) == ref_cache.recurrence_pattern_id(RecurrencePatternEnum.EVERY_N_PERIODS) and start_period_id and interval_n:
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

    template = TransferTemplate(
        user_id=current_user.id,
        recurrence_rule_id=rule.id,
        **data,
    )
    db.session.add(template)

    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        flash("A transfer with that name already exists.", "warning")
        return redirect(url_for("transfers.list_transfer_templates"))

    # Determine whether this is a one-time transfer.  The ONCE pattern
    # is skipped by the recurrence engine ("once items are manually
    # placed; no auto-generation"), so we create the single Transfer
    # instance directly via the service.
    once_id = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.ONCE)
    is_one_time = rule.pattern_id == once_id

    if is_one_time and start_period_id:
        # One-time transfer: create a single Transfer in the selected
        # period via the transfer service so shadow transactions are
        # generated atomically.
        period = db.session.get(PayPeriod, start_period_id)
        if not period or period.user_id != current_user.id:
            db.session.rollback()
            flash("Invalid pay period for one-time transfer.", "danger")
            return redirect(url_for("transfers.new_transfer_template"))

        scenario = (
            db.session.query(Scenario)
            .filter_by(user_id=current_user.id, is_baseline=True)
            .first()
        )
        if scenario:
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            try:
                transfer_service.create_transfer(
                    user_id=current_user.id,
                    from_account_id=template.from_account_id,
                    to_account_id=template.to_account_id,
                    pay_period_id=period.id,
                    scenario_id=scenario.id,
                    amount=template.default_amount,
                    status_id=projected_id,
                    category_id=template.category_id,
                    name=template.name,
                    transfer_template_id=template.id,
                )
            except (NotFoundError, ShekelValidationError) as exc:
                db.session.rollback()
                flash(f"Could not create transfer: {exc}", "danger")
                return redirect(url_for("transfers.new_transfer_template"))
    elif rule:
        # Recurring transfer: delegate to the recurrence engine.
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
    flash(f"Transfer '{template.name}' created.", "success")
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
    categories = (
        db.session.query(Category)
        .filter_by(user_id=current_user.id, is_active=True)
        .order_by(Category.group_name, Category.item_name)
        .all()
    )
    patterns = db.session.query(RecurrencePattern).all()

    return render_template(
        "transfers/form.html",
        template=template,
        accounts=accounts,
        categories=categories,
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
    pattern_id_str = data.pop("recurrence_pattern", None)
    if pattern_id_str:
        pattern = db.session.get(RecurrencePattern, int(pattern_id_str))
        if pattern is None:
            flash("Invalid recurrence pattern.", "danger")
            return redirect(url_for("transfers.edit_transfer_template", template_id=template_id))
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

    _TEMPLATE_UPDATE_FIELDS = {"name", "default_amount", "from_account_id", "to_account_id", "category_id", "is_active", "sort_order"}
    for field, value in data.items():
        if field in _TEMPLATE_UPDATE_FIELDS:
            setattr(template, field, value)

    # Flush template changes first so name-uniqueness violations are caught
    # before regeneration dirties the session with transfer deletes/creates.
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        flash("A recurring transfer with that name already exists.", "warning")
        return redirect(url_for("transfers.edit_transfer_template", template_id=template_id))

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

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("A recurring transfer with that name already exists.", "warning")
        return redirect(url_for("transfers.edit_transfer_template", template_id=template_id))
    flash(f"Recurring transfer '{template.name}' updated.", "success")
    return redirect(url_for("transfers.list_transfer_templates"))


@transfers_bp.route("/transfers/<int:template_id>/archive", methods=["POST"])
@login_required
def archive_transfer_template(template_id):
    """Archive a transfer template (stops future generation, keeps history).

    Soft-deletes projected transfers and their shadow transactions via
    the transfer service to maintain the three-level cascade:
    template archival -> transfer soft-delete -> shadow soft-delete.
    """
    template = db.session.get(TransferTemplate, template_id)
    if template is None or template.user_id != current_user.id:
        flash("Recurring transfer not found.", "danger")
        return redirect(url_for("transfers.list_transfer_templates"))

    template.is_active = False

    # Find projected, non-deleted transfers to soft-delete.
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    transfers_to_delete = (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == template.id,
            Transfer.status_id == projected_id,
            Transfer.is_deleted.is_(False),
        )
        .all()
    )

    # Route each through the service to ensure shadows are soft-deleted.
    for xfer in transfers_to_delete:
        transfer_service.delete_transfer(xfer.id, current_user.id, soft=True)

    db.session.commit()

    flash(
        f"Recurring transfer '{template.name}' archived. "
        f"{len(transfers_to_delete)} projected transfer(s) removed.",
        "info",
    )
    return redirect(url_for("transfers.list_transfer_templates"))


@transfers_bp.route("/transfers/<int:template_id>/unarchive", methods=["POST"])
@login_required
def unarchive_transfer_template(template_id):
    """Unarchive a transfer template.

    Restores soft-deleted transfers and their shadow transactions.
    """
    template = db.session.get(TransferTemplate, template_id)
    if template is None or template.user_id != current_user.id:
        flash("Recurring transfer not found.", "danger")
        return redirect(url_for("transfers.list_transfer_templates"))

    template.is_active = True

    # Find soft-deleted projected transfers to restore.
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    transfers_to_restore = (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == template.id,
            Transfer.status_id == projected_id,
            Transfer.is_deleted.is_(True),
        )
        .all()
    )

    # Restore transfers and shadows via the service so all mutations
    # flow through the single enforcement point (design doc section 4.1).
    for xfer in transfers_to_restore:
        transfer_service.restore_transfer(xfer.id, current_user.id)

    restored_count = len(transfers_to_restore)

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
        f"Recurring transfer '{template.name}' unarchived. "
        f"{restored_count} projected transfer(s) restored.",
        "success",
    )
    return redirect(url_for("transfers.list_transfer_templates"))


@transfers_bp.route("/transfers/<int:template_id>/hard-delete", methods=["POST"])
@login_required
def hard_delete_transfer_template(template_id):
    """Permanently delete a transfer template if it has no payment history.

    Maintains all five transfer invariants from CLAUDE.md:
      1. Two linked shadows per transfer -- CASCADE on Transaction.transfer_id
         removes both shadows when the parent Transfer is hard-deleted via
         transfer_service.delete_transfer(soft=False).
      2. No orphaned shadows -- shadows are removed atomically with their
         parent transfer through the service's CASCADE verification.
      3. Amount/status/period parity -- not applicable; entire records are
         removed, not mutated.
      4. All mutations through the transfer service -- every transfer
         deletion is routed through transfer_service.delete_transfer().
      5. Balance calculator queries only budget.transactions -- after
         deletion, shadow transactions no longer exist in the table.

    Two-path logic:
      - History exists (Paid/Settled transfers): permanent deletion is
        blocked.  Template is archived instead (if not already) and the
        user is warned.
      - No history: all linked transfers are hard-deleted through the
        transfer service (which CASCADE-deletes shadows), then the
        template itself is permanently removed.
    """
    template = db.session.get(TransferTemplate, template_id)
    if template is None or template.user_id != current_user.id:
        flash("Recurring transfer not found.", "danger")
        return redirect(url_for("transfers.list_transfer_templates"))

    if archive_helpers.transfer_template_has_paid_history(template.id):
        flash(
            f"'{template.name}' has payment history and cannot be permanently "
            "deleted. It has been archived instead.",
            "warning",
        )
        if template.is_active:
            template.is_active = False
            # Soft-delete projected transfers via the service (same as
            # archive_transfer_template) to maintain shadow invariants.
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            transfers_to_delete = (
                db.session.query(Transfer)
                .filter(
                    Transfer.transfer_template_id == template.id,
                    Transfer.status_id == projected_id,
                    Transfer.is_deleted.is_(False),
                )
                .all()
            )
            for xfer in transfers_to_delete:
                transfer_service.delete_transfer(xfer.id, current_user.id, soft=True)
            db.session.commit()
        return redirect(url_for("transfers.list_transfer_templates"))

    # No history -- safe to permanently delete.
    # Delete ALL linked transfers through the transfer service so that
    # shadow transactions are CASCADE-deleted (invariants 1, 2, 4).
    # transfer_service.delete_transfer flushes but does not commit,
    # so all deletions are atomic within a single DB transaction.
    template_name = template.name
    all_transfers = (
        db.session.query(Transfer)
        .filter(Transfer.transfer_template_id == template.id)
        .all()
    )
    for xfer in all_transfers:
        transfer_service.delete_transfer(xfer.id, current_user.id, soft=False)

    db.session.delete(template)
    db.session.commit()

    flash(f"Recurring transfer '{template_name}' permanently deleted.", "info")
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
    categories = (
        db.session.query(Category)
        .filter_by(user_id=current_user.id, is_active=True)
        .order_by(Category.group_name, Category.item_name)
        .all()
    )
    return render_template(
        "transfers/_transfer_full_edit.html",
        xfer=xfer, statuses=statuses, categories=categories,
    )


@transfers_bp.route("/transfers/instance/<int:xfer_id>", methods=["PATCH"])
@login_required
def update_transfer(xfer_id):
    """Update a transfer and its shadow transactions (inline edit save)."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404

    errors = _xfer_update_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _xfer_update_schema.load(request.form)

    # Auto-set is_override when a template-linked transfer's amount changes.
    if xfer.transfer_template_id and "amount" in data:
        data["is_override"] = True

    try:
        transfer_service.update_transfer(xfer.id, current_user.id, **data)
    except NotFoundError:
        return "Not found", 404
    except ShekelValidationError as exc:
        return jsonify(errors={"_schema": [str(exc)]}), 400

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d updated transfer %d", current_user.id, xfer_id)

    # When opened from a shadow transaction cell in the grid, render the
    # transaction cell template so the cell remains interactive.  When
    # opened from the transfer management page, render the transfer cell.
    shadow = _resolve_shadow_context(xfer)
    if shadow is not None:
        db.session.refresh(shadow)
        response = render_template("grid/_transaction_cell.html", txn=shadow)
        return response, 200, {"HX-Trigger": "balanceChanged"}

    account = resolve_grid_account(current_user.id, current_user.settings)
    response = render_template(
        "transfers/_transfer_cell.html", xfer=xfer, account=account,
    )
    return response, 200, {"HX-Trigger": "balanceChanged"}


@transfers_bp.route("/transfers/ad-hoc", methods=["POST"])
@login_required
def create_ad_hoc():
    """Create an ad-hoc (one-time) transfer with shadow transactions."""
    errors = _xfer_create_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _xfer_create_schema.load(request.form)

    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

    try:
        xfer = transfer_service.create_transfer(
            user_id=current_user.id,
            from_account_id=data["from_account_id"],
            to_account_id=data["to_account_id"],
            pay_period_id=data["pay_period_id"],
            scenario_id=data["scenario_id"],
            amount=data["amount"],
            status_id=projected_id,
            category_id=data["category_id"],
            name=data.get("name"),
            notes=data.get("notes"),
        )
    except NotFoundError:
        return "Not found", 404
    except ShekelValidationError as exc:
        return jsonify(errors={"_schema": [str(exc)]}), 400

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d created ad-hoc transfer (id=%d)", current_user.id, xfer.id)

    account = resolve_grid_account(current_user.id, current_user.settings)
    response = render_template(
        "transfers/_transfer_cell.html", xfer=xfer, account=account, wrap_div=True,
    )
    return response, 201, {"HX-Trigger": "balanceChanged"}


@transfers_bp.route("/transfers/instance/<int:xfer_id>", methods=["DELETE"])
@login_required
def delete_transfer(xfer_id):
    """Soft-delete a template transfer or hard-delete an ad-hoc transfer.

    Routes through transfer_service to ensure shadow transactions are
    also deleted (soft or hard) alongside the parent transfer.
    """
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404

    soft = bool(xfer.transfer_template_id)
    transfer_service.delete_transfer(xfer.id, current_user.id, soft=soft)

    db.session.commit()
    logger.info("user_id=%d deleted transfer %d", current_user.id, xfer_id)
    return "", 200, {"HX-Trigger": "balanceChanged"}


# ── Transfer Status Actions ─────────────────────────────────────────


@transfers_bp.route("/transfers/instance/<int:xfer_id>/mark-done", methods=["POST"])
@login_required
def mark_done(xfer_id):
    """Mark a transfer and its shadows as 'done' (settled)."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404

    done_id = ref_cache.status_id(StatusEnum.DONE)
    transfer_service.update_transfer(xfer.id, current_user.id, status_id=done_id)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d marked transfer %d as done", current_user.id, xfer_id)

    # Grid shadow context: render transaction cell with gridRefresh
    # (matches the transaction route guard pattern for status changes).
    shadow = _resolve_shadow_context(xfer)
    if shadow is not None:
        db.session.refresh(shadow)
        response = render_template("grid/_transaction_cell.html", txn=shadow)
        return response, 200, {"HX-Trigger": "gridRefresh"}

    account = resolve_grid_account(current_user.id, current_user.settings)
    response = render_template(
        "transfers/_transfer_cell.html", xfer=xfer, account=account,
    )
    return response, 200, {"HX-Trigger": "balanceChanged"}


@transfers_bp.route("/transfers/instance/<int:xfer_id>/cancel", methods=["POST"])
@login_required
def cancel_transfer(xfer_id):
    """Mark a transfer and its shadows as 'cancelled'."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404

    cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)
    transfer_service.update_transfer(
        xfer.id, current_user.id, status_id=cancelled_id
    )

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d cancelled transfer %d", current_user.id, xfer_id)

    # Grid shadow context: render transaction cell with gridRefresh
    # (matches the transaction route guard pattern for status changes).
    shadow = _resolve_shadow_context(xfer)
    if shadow is not None:
        db.session.refresh(shadow)
        response = render_template("grid/_transaction_cell.html", txn=shadow)
        return response, 200, {"HX-Trigger": "gridRefresh"}

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


def _resolve_shadow_context(xfer):
    """Check for a source_txn_id in the request indicating grid shadow context.

    When the transfer full edit popover is opened from a shadow transaction
    cell in the budget grid, the form includes ``source_txn_id`` so the
    handler can render ``_transaction_cell.html`` (the correct template
    for grid cells) instead of ``_transfer_cell.html`` (which targets
    non-existent ``#xfer-cell-`` IDs in the grid).

    Validates that the shadow transaction exists, is a shadow of the
    given transfer, and belongs to the current user.

    Args:
        xfer: The Transfer object that was just updated.

    Returns:
        Transaction or None.  The validated shadow Transaction if the
        request originated from a grid cell, or None if the request
        came from the transfer management page (no source_txn_id).
    """
    source_txn_id = request.form.get("source_txn_id", type=int)
    if source_txn_id is None:
        return None

    shadow = db.session.get(Transaction, source_txn_id)
    if shadow is None:
        logger.warning(
            "source_txn_id=%d not found for transfer %d; "
            "falling back to transfer cell response.",
            source_txn_id, xfer.id,
        )
        return None

    # Verify the transaction is actually a shadow of this transfer.
    if shadow.transfer_id != xfer.id:
        logger.warning(
            "source_txn_id=%d has transfer_id=%s, expected %d; "
            "falling back to transfer cell response.",
            source_txn_id, shadow.transfer_id, xfer.id,
        )
        return None

    # Ownership check via the shadow's pay period (same pattern as
    # _get_owned_transaction in transactions.py).
    if shadow.pay_period.user_id != current_user.id:
        logger.warning(
            "source_txn_id=%d belongs to another user; "
            "falling back to transfer cell response.",
            source_txn_id,
        )
        return None

    return shadow
