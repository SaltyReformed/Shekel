"""
Shekel Budget App -- Template Management Routes

CRUD pages for transaction templates and their recurrence rules.
Updating a template triggers recurrence regeneration.
"""

import logging
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm.exc import StaleDataError

from app.utils.auth_helpers import fresh_login_required, get_or_404, require_owner
from markupsafe import Markup

from app.extensions import db
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app.models.pay_period import PayPeriod
from app.models.category import Category
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.ref import RecurrencePattern, TransactionType
from app import ref_cache
from app.enums import RecurrencePatternEnum, StatusEnum, TxnTypeEnum
from app.utils import archive_helpers
from app.schemas.validation import TemplateCreateSchema, TemplateUpdateSchema
from app.services import recurrence_engine, pay_period_service
from app.exceptions import RecurrenceConflict

logger = logging.getLogger(__name__)

templates_bp = Blueprint("templates", __name__)

_create_schema = TemplateCreateSchema()
_update_schema = TemplateUpdateSchema()


_GENERIC_VALIDATION_FLASH = "Please correct the highlighted errors and try again."

# Marshmallow error keys whose messages should be flashed verbatim instead
# of falling through to the generic prompt.  Listed keys correspond to
# cross-field validators whose messages are user-facing and actionable;
# field-level errors on the same keys (e.g. "Not a valid boolean.") are
# rare in practice -- HTML forms only submit the canonical "on" string --
# and remain acceptable feedback when they do appear.
_ACTIONABLE_FLASH_FIELDS = ("is_envelope",)


def _flash_message_for_errors(errors):
    """Pick a user-facing flash message from a Marshmallow errors dict.

    Cross-field validators (e.g. ``validate_envelope_only_on_expense``
    in ``app/schemas/validation.py``) attach actionable messages to
    specific fields so the form can highlight them.  When such a
    message is present, surface it verbatim so the user sees the actual
    rule that fired.  Other field-level errors fall back to a generic
    prompt because the individual form widgets convey the issue inline.

    Args:
        errors: The dict returned by ``schema.validate(request.form)``.

    Returns:
        str: The message to flash.  Always non-empty.
    """
    for field in _ACTIONABLE_FLASH_FIELDS:
        msgs = errors.get(field)
        if isinstance(msgs, list) and msgs:
            return str(msgs[0])
    return _GENERIC_VALIDATION_FLASH


def _is_tracking_on_non_expense(data, template=None):
    """Check whether tracking is being set on a non-expense template.

    Defense-in-depth fallback for the cross-field schema validator
    ``validate_envelope_only_on_expense``.  The schema validator catches
    the bug whenever both ``is_envelope`` and ``transaction_type_id``
    appear in the deserialized payload (the normal HTML form path); this
    helper closes the gap on partial updates that omit one field by
    falling back to the existing template's stored value.

    Args:
        data: Deserialized form data from Marshmallow schema.
        template: Existing TransactionTemplate (for updates) or None (for creates).

    Returns:
        True if the combination is invalid (tracking on non-expense), False otherwise.
    """
    track = data.get(
        "is_envelope",
        getattr(template, "is_envelope", False),
    )
    if not track:
        return False
    type_id = data.get(
        "transaction_type_id",
        getattr(template, "transaction_type_id", None),
    )
    return type_id != ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)


@templates_bp.route("/templates")
@login_required
@require_owner
def list_templates():
    """List all transaction templates for the current user.

    Separates templates into active and archived lists for the UI.
    Both lists inherit the same ordering (sort_order, name).
    """
    templates = (
        db.session.query(TransactionTemplate)
        .filter_by(user_id=current_user.id)
        .order_by(TransactionTemplate.sort_order, TransactionTemplate.name)
        .all()
    )
    active_templates = [t for t in templates if t.is_active]
    archived_templates = [t for t in templates if not t.is_active]
    return render_template(
        "templates/list.html",
        active_templates=active_templates,
        archived_templates=archived_templates,
    )


@templates_bp.route("/templates/new", methods=["GET"])
@login_required
@require_owner
def new_template():
    """Display the template creation form."""
    categories = (
        db.session.query(Category)
        .filter_by(user_id=current_user.id, is_active=True)
        .order_by(Category.group_name, Category.item_name)
        .all()
    )
    accounts = (
        db.session.query(Account)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )
    patterns = db.session.query(RecurrencePattern).all()
    txn_types = db.session.query(TransactionType).all()
    periods = pay_period_service.get_all_periods(current_user.id)
    current_period = pay_period_service.get_current_period(current_user.id)

    return render_template(
        "templates/form.html",
        template=None,
        categories=categories,
        accounts=accounts,
        patterns=patterns,
        txn_types=txn_types,
        periods=periods,
        current_period=current_period,
    )


@templates_bp.route("/templates", methods=["POST"])
@login_required
@require_owner
def create_template():
    """Create a new transaction template with optional recurrence rule."""
    errors = _create_schema.validate(request.form)
    if errors:
        flash(_flash_message_for_errors(errors), "danger")
        return redirect(url_for("templates.new_template"))

    data = _create_schema.load(request.form)

    # Validate account and category ownership.
    acct = db.session.get(Account, data.get("account_id"))
    if not acct or acct.user_id != current_user.id:
        flash("Invalid account.", "danger")
        return redirect(url_for("templates.new_template"))
    cat = db.session.get(Category, data.get("category_id"))
    if not cat or cat.user_id != current_user.id:
        flash("Invalid category.", "danger")
        return redirect(url_for("templates.new_template"))

    # Validate tracking is expense-only.
    if _is_tracking_on_non_expense(data):
        flash("Purchase tracking is only available for expense templates.", "danger")
        return redirect(url_for("templates.new_template"))

    # Extract start_period_id and end_date before creating the rule.
    start_period_id = data.pop("start_period_id", None)
    end_date = data.pop("end_date", None)

    # Create the recurrence rule if a pattern was specified.
    rule = None
    pattern_id_str = data.pop("recurrence_pattern", None)
    if pattern_id_str:
        pattern = db.session.get(RecurrencePattern, int(pattern_id_str))
        if pattern is None:
            flash("Invalid recurrence pattern.", "danger")
            return redirect(url_for("templates.new_template"))

        interval_n = data.pop("interval_n", 1)
        offset_periods = data.pop("offset_periods", 0)

        # Auto-derive offset from start period for every_n_periods.
        if int(pattern_id_str) == ref_cache.recurrence_pattern_id(RecurrencePatternEnum.EVERY_N_PERIODS) and start_period_id and interval_n:
            start_period = db.session.get(PayPeriod, start_period_id)
            if not start_period or start_period.user_id != current_user.id:
                flash("Invalid start period.", "danger")
                return redirect(url_for("templates.new_template"))
            offset_periods = start_period.period_index % interval_n

        rule = RecurrenceRule(
            user_id=current_user.id,
            pattern_id=pattern.id,
            interval_n=interval_n,
            offset_periods=offset_periods,
            day_of_month=data.pop("day_of_month", None),
            due_day_of_month=data.pop("due_day_of_month", None),
            month_of_year=data.pop("month_of_year", None),
            start_period_id=start_period_id,
            end_date=end_date,
        )
        db.session.add(rule)
        db.session.flush()
    else:
        # Remove recurrence-related keys if no pattern.
        for key in ("interval_n", "offset_periods", "day_of_month", "due_day_of_month", "month_of_year", "end_date"):
            data.pop(key, None)

    # Create the template.
    template = TransactionTemplate(
        user_id=current_user.id,
        recurrence_rule_id=rule.id if rule else None,
        **data,
    )
    db.session.add(template)
    db.session.flush()

    # Auto-generate transactions from the rule into future periods.
    if rule:
        scenario = (
            db.session.query(Scenario)
            .filter_by(user_id=current_user.id, is_baseline=True)
            .first()
        )
        if scenario:
            periods = pay_period_service.get_all_periods(current_user.id)
            recurrence_engine.generate_for_template(
                template, periods, scenario.id,
            )

    db.session.commit()
    flash(f"Recurring transaction '{template.name}' created. View it on the Budget grid.", "success")
    return redirect(url_for("templates.list_templates"))


@templates_bp.route("/templates/<int:template_id>/edit", methods=["GET"])
@login_required
@require_owner
def edit_template(template_id):
    """Display the template edit form."""
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)

    categories = (
        db.session.query(Category)
        .filter_by(user_id=current_user.id, is_active=True)
        .order_by(Category.group_name, Category.item_name)
        .all()
    )
    accounts = (
        db.session.query(Account)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )
    patterns = db.session.query(RecurrencePattern).all()
    txn_types = db.session.query(TransactionType).all()

    return render_template(
        "templates/form.html",
        template=template,
        categories=categories,
        accounts=accounts,
        patterns=patterns,
        txn_types=txn_types,
        periods=[],
        current_period=None,
    )


@templates_bp.route("/templates/<int:template_id>", methods=["POST"])
@login_required
@require_owner
def update_template(template_id):
    """Update a template and regenerate future transactions.

    Uses POST with _method=PUT for HTML form compatibility.

    Optimistic locking (commit C-18 / F-010): the edit form ships
    ``version_id`` as a hidden input.  When the submitted value
    differs from the row's current counter, the handler short-
    circuits with a flash + redirect so the audit trail records
    only the winner.  ``StaleDataError`` raised at flush time --
    e.g. by a concurrent edit that races past the form-side check
    -- is caught and converted to the same flash + redirect.
    """
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)

    errors = _update_schema.validate(request.form)
    if errors:
        flash(_flash_message_for_errors(errors), "danger")
        return redirect(url_for("templates.edit_template", template_id=template_id))

    data = _update_schema.load(request.form)

    # Stale-form check (commit C-18 / F-010).
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != template.version_id:
        logger.info(
            "Stale-form conflict on update_template id=%d "
            "(submitted=%d, current=%d)",
            template_id, submitted_version, template.version_id,
        )
        flash(
            "This recurring transaction was changed by another action while "
            "you were editing.  Please reload and try again.",
            "warning",
        )
        return redirect(url_for(
            "templates.edit_template", template_id=template_id,
        ))

    effective_from = data.pop("effective_from", date.today())

    # Remove start_period_id from update data (set once at creation).
    data.pop("start_period_id", None)
    end_date = data.pop("end_date", None)

    # Update recurrence rule if pattern changed.
    pattern_id_str = data.pop("recurrence_pattern", None)
    if pattern_id_str:
        pattern = db.session.get(RecurrencePattern, int(pattern_id_str))
        if pattern is None:
            flash("Invalid recurrence pattern.", "danger")
            return redirect(url_for("templates.edit_template", template_id=template_id))
        if template.recurrence_rule:
            template.recurrence_rule.pattern_id = pattern.id
            template.recurrence_rule.interval_n = data.pop("interval_n", 1)
            template.recurrence_rule.offset_periods = data.pop("offset_periods", 0)
            template.recurrence_rule.day_of_month = data.pop("day_of_month", None)
            template.recurrence_rule.due_day_of_month = data.pop("due_day_of_month", None)
            template.recurrence_rule.month_of_year = data.pop("month_of_year", None)
            template.recurrence_rule.end_date = end_date
        else:
            rule = RecurrenceRule(
                user_id=current_user.id,
                pattern_id=pattern.id,
                interval_n=data.pop("interval_n", 1),
                offset_periods=data.pop("offset_periods", 0),
                day_of_month=data.pop("day_of_month", None),
                due_day_of_month=data.pop("due_day_of_month", None),
                month_of_year=data.pop("month_of_year", None),
                end_date=end_date,
            )
            db.session.add(rule)
            db.session.flush()
            template.recurrence_rule_id = rule.id
    else:
        for key in ("interval_n", "offset_periods", "day_of_month", "due_day_of_month", "month_of_year", "end_date"):
            data.pop(key, None)

    # Validate ownership if account or category is being changed.
    if "account_id" in data:
        acct = db.session.get(Account, data["account_id"])
        if not acct or acct.user_id != current_user.id:
            flash("Invalid account.", "danger")
            return redirect(url_for("templates.edit_template", template_id=template_id))
    if "category_id" in data:
        cat = db.session.get(Category, data["category_id"])
        if not cat or cat.user_id != current_user.id:
            flash("Invalid category.", "danger")
            return redirect(url_for("templates.edit_template", template_id=template_id))

    # Validate tracking is expense-only (check resulting state).
    if _is_tracking_on_non_expense(data, template):
        flash("Purchase tracking is only available for expense templates.", "danger")
        return redirect(url_for("templates.edit_template", template_id=template_id))

    # Apply remaining field updates to the template.
    _TEMPLATE_UPDATE_FIELDS = {
        "name", "default_amount", "category_id", "transaction_type_id",
        "account_id", "is_active", "sort_order",
        "is_envelope", "companion_visible",
    }
    old_name = template.name
    for field, value in data.items():
        if field in _TEMPLATE_UPDATE_FIELDS:
            setattr(template, field, value)

    # Propagate a rename to existing instances.  regenerate_for_template
    # only deletes/recreates non-override rows on or after effective_from,
    # so historic rows, overrides, and settled rows would otherwise keep
    # the old label and desync every view that renders txn.name directly
    # (variance report, CSV export, calendar, companion card, edit form
    # header).  The partial unique index on transactions covers
    # (template_id, pay_period_id, scenario_id) only, so a bulk name
    # update cannot trip a constraint.  Template ownership was verified
    # above, so template_id alone scopes the update to the current user.
    if template.name != old_name:
        db.session.query(Transaction).filter(
            Transaction.template_id == template.id,
            Transaction.is_deleted.is_(False),
        ).update({"name": template.name}, synchronize_session="fetch")

    # Regenerate future transactions.
    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=current_user.id, is_baseline=True)
        .first()
    )
    if scenario and template.recurrence_rule:
        periods = pay_period_service.get_all_periods(current_user.id)
        try:
            recurrence_engine.regenerate_for_template(
                template, periods, scenario.id, effective_from=effective_from,
            )
        except RecurrenceConflict as conflict:
            # Store conflict info in session for the resolution page.
            # For Phase 1, auto-keep overrides (simplest approach).
            logger.warning(
                "Recurrence conflict for template %d: %d overridden, %d deleted",
                template.id, len(conflict.overridden), len(conflict.deleted),
            )
            flash(
                f"Note: {len(conflict.overridden)} overridden and "
                f"{len(conflict.deleted)} deleted entries were kept as-is.",
                "warning",
            )

    try:
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        logger.info(
            "Stale-data conflict on update_template id=%d", template_id,
        )
        flash(
            "This recurring transaction was changed by another action while "
            "you were editing.  Please reload and try again.",
            "warning",
        )
        return redirect(url_for(
            "templates.edit_template", template_id=template_id,
        ))
    flash(f"Recurring transaction '{template.name}' updated.", "success")
    return redirect(url_for("templates.list_templates"))


@templates_bp.route("/templates/<int:template_id>/archive", methods=["POST"])
@login_required
@require_owner
def archive_template(template_id):
    """Archive a template (stops future generation, keeps history).

    Optimistic locking (commit C-18 / F-010): the
    ``is_active = False`` flush is version-pinned by SQLAlchemy.
    A concurrent edit raises ``StaleDataError`` which the handler
    converts to a flash + redirect so the user retries against
    fresh state.
    """
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)

    template.is_active = False

    # Soft-delete projected transactions for this template.
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    deleted_count = db.session.query(Transaction).filter(
        Transaction.template_id == template.id,
        Transaction.status_id == projected_id,
        Transaction.is_deleted.is_(False),
    ).update({"is_deleted": True}, synchronize_session="fetch")

    try:
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        logger.info(
            "Stale-data conflict on archive_template id=%d", template_id,
        )
        flash(
            "This recurring transaction was changed by another action.  "
            "Please reload and try again.",
            "warning",
        )
        return redirect(url_for("templates.list_templates"))

    flash(
        f"Recurring transaction '{template.name}' archived. "
        f"{deleted_count} projected transaction(s) removed.",
        "info",
    )
    return redirect(url_for("templates.list_templates"))


@templates_bp.route("/templates/<int:template_id>/unarchive", methods=["POST"])
@login_required
@require_owner
def unarchive_template(template_id):
    """Unarchive a template and restore projected transactions.

    Optimistic locking: see :func:`archive_template`.
    """
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)

    template.is_active = True

    # Restore soft-deleted projected transactions.
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    restored_count = db.session.query(Transaction).filter(
        Transaction.template_id == template.id,
        Transaction.status_id == projected_id,
        Transaction.is_deleted.is_(True),
    ).update({"is_deleted": False}, synchronize_session="fetch")

    # Regenerate to fill in any missing future periods.
    if template.recurrence_rule:
        scenario = (
            db.session.query(Scenario)
            .filter_by(user_id=current_user.id, is_baseline=True)
            .first()
        )
        if scenario:
            periods = pay_period_service.get_all_periods(current_user.id)
            recurrence_engine.generate_for_template(
                template, periods, scenario.id, effective_from=date.today(),
            )

    try:
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        logger.info(
            "Stale-data conflict on unarchive_template id=%d", template_id,
        )
        flash(
            "This recurring transaction was changed by another action.  "
            "Please reload and try again.",
            "warning",
        )
        return redirect(url_for("templates.list_templates"))

    flash(
        f"Recurring transaction '{template.name}' unarchived. "
        f"{restored_count} projected transaction(s) restored.",
        "success",
    )
    return redirect(url_for("templates.list_templates"))


@templates_bp.route("/templates/<int:template_id>/hard-delete", methods=["POST"])
@login_required
@require_owner
@fresh_login_required()
def hard_delete_template(template_id):
    """Permanently delete a transaction template if it has no payment history.

    Two-path logic:
      1. If the template has Paid or Settled transactions, permanent deletion
         is blocked.  The template is archived instead (if not already) and the
         user is warned.
      2. If no payment history exists, all linked transactions are deleted
         first (to avoid FK constraint violations), then the template itself
         is permanently removed.

    Transactions are deleted before the template because
    Transaction.template_id is a FK with ON DELETE SET NULL -- deleting the
    template first would orphan transaction rows with NULL template_id
    instead of removing them cleanly.
    """
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)

    if archive_helpers.template_has_paid_history(template.id):
        flash(
            f"'{template.name}' has payment history and cannot be permanently "
            "deleted. It has been archived instead.",
            "warning",
        )
        if template.is_active:
            template.is_active = False
            # Soft-delete projected transactions (same logic as archive_template).
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            db.session.query(Transaction).filter(
                Transaction.template_id == template.id,
                Transaction.status_id == projected_id,
                Transaction.is_deleted.is_(False),
            ).update({"is_deleted": True}, synchronize_session="fetch")
            try:
                db.session.commit()
            except StaleDataError:
                db.session.rollback()
                logger.info(
                    "Stale-data conflict during archive-fallback in "
                    "hard_delete_template id=%d", template_id,
                )
                flash(
                    "This recurring transaction was changed by another "
                    "action.  Please reload and try again.",
                    "warning",
                )
        return redirect(url_for("templates.list_templates"))

    # No history -- safe to permanently delete.
    # Delete all linked transactions first, then the template.  Only
    # Projected transactions should remain (history check passed), but
    # delete unconditionally for safety.
    template_name = template.name
    db.session.query(Transaction).filter(
        Transaction.template_id == template.id,
    ).delete(synchronize_session="fetch")

    db.session.delete(template)
    try:
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        logger.info(
            "Stale-data conflict on hard_delete_template id=%d",
            template_id,
        )
        flash(
            "This recurring transaction was changed by another action.  "
            "Please reload and try again.",
            "warning",
        )
        return redirect(url_for("templates.list_templates"))

    flash(f"Recurring transaction '{template_name}' permanently deleted.", "info")
    return redirect(url_for("templates.list_templates"))


@templates_bp.route("/templates/preview-recurrence", methods=["GET"])
@login_required
@require_owner
def preview_recurrence():
    """HTMX partial: show next 5 occurrences for a recurrence pattern."""
    pattern_id = request.args.get("recurrence_pattern", type=int)
    if not pattern_id or pattern_id == ref_cache.recurrence_pattern_id(RecurrencePatternEnum.ONCE):
        return "<small class='text-muted'>No preview for this pattern</small>"

    interval_n = request.args.get("interval_n", type=int, default=1)
    day_of_month = request.args.get("day_of_month", type=int)
    month_of_year = request.args.get("month_of_year", type=int)
    start_period_id = request.args.get("start_period_id", type=int)
    end_date_str = request.args.get("end_date")
    end_date = date.fromisoformat(end_date_str) if end_date_str else None

    # Build a temporary rule object (not saved).
    pattern = db.session.get(RecurrencePattern, pattern_id)
    if not pattern:
        return "<small class='text-muted'>Unknown pattern</small>"

    rule = RecurrenceRule(
        pattern_id=pattern.id,
        interval_n=interval_n,
        day_of_month=day_of_month,
        month_of_year=month_of_year,
        start_period_id=start_period_id,
        end_date=end_date,
    )
    # Attach the pattern relationship manually for the matcher.
    rule.pattern = pattern

    periods = pay_period_service.get_all_periods(current_user.id)
    if not periods:
        return "<small class='text-muted'>No pay periods generated yet</small>"

    # Determine effective_from.
    effective_from = None
    if start_period_id:
        start_period = db.session.get(PayPeriod, start_period_id)
        # Ownership check: reject other users' periods to prevent
        # pay period structure disclosure -- see audit finding H3.
        # Falls through to the current user's own periods below.
        if start_period and start_period.user_id == current_user.id:
            effective_from = start_period.start_date
            # Auto-derive offset for every_n_periods.
            if pattern_id == ref_cache.recurrence_pattern_id(RecurrencePatternEnum.EVERY_N_PERIODS) and interval_n:
                rule.offset_periods = start_period.period_index % interval_n
    if effective_from is None:
        current_period = pay_period_service.get_current_period(current_user.id)
        effective_from = current_period.start_date if current_period else periods[0].start_date

    matching = recurrence_engine._match_periods(rule, pattern_id, periods, effective_from)
    preview_periods = matching[:5]

    if not preview_periods:
        return "<small class='text-muted'>No matching periods found</small>"

    items = "".join(
        f"<li>{p.start_date.strftime('%b %d, %Y')} - {p.end_date.strftime('%b %d, %Y')}</li>"
        for p in preview_periods
    )
    html = (
        f"<small class='text-muted'>Next {len(preview_periods)} occurrences:</small>"
        f"<ul class='list-unstyled mb-0 ms-2'><small>{items}</small></ul>"
    )
    return Markup(html)
