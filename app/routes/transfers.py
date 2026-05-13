"""
Shekel Budget App -- Transfer Routes

CRUD for transfer templates and inline grid cell endpoints for transfers.
Follows the same patterns as templates.py (template CRUD) and
transactions.py (grid cell HTMX endpoints).
"""

import logging
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for, jsonify
from flask_login import current_user, login_required

from app.utils.auth_helpers import fresh_login_required, get_or_404, require_owner
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

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
from app.services.entry_service import build_entry_sums_dict
from app.services.scenario_resolver import get_baseline_scenario
from app.exceptions import NotFoundError, RecurrenceConflict, ValidationError as ShekelValidationError
from app.utils.db_errors import is_unique_violation

logger = logging.getLogger(__name__)

# Name of the partial unique index that backstops the ad-hoc transfer
# double-submit fix (F-050 / C-22).  Mirrors the literal in
# ``app/models/transfer.py:Transfer.__table_args__`` and
# ``migrations/versions/<C-22 revision>.py``; renaming the index
# requires a coordinated edit across all three sites.
_TRANSFER_ADHOC_UNIQUE_INDEX = "uq_transfers_adhoc_dedupe"

transfers_bp = Blueprint("transfers", __name__)

_create_schema = TransferTemplateCreateSchema()
_update_schema = TransferTemplateUpdateSchema()
_xfer_create_schema = TransferCreateSchema()
_xfer_update_schema = TransferUpdateSchema()


# ── Template Management Routes ──────────────────────────────────────


@transfers_bp.route("/transfers")
@login_required
@require_owner
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
@require_owner
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
@require_owner
def create_transfer_template():
    """Create a new transfer template with optional recurrence rule.

    Route-boundary FK ownership checks (commit C-27 / F-043 of the
    2026-04-15 security remediation plan): every user-scoped FK
    accepted from the form -- ``from_account_id``, ``to_account_id``,
    ``category_id`` -- is verified against ``current_user.id`` before
    the row is persisted.  ``start_period_id`` is checked deeper in
    the function only when the recurrence pattern is
    ``EVERY_N_PERIODS`` (used to compute ``offset_periods``); the
    follow-up one-time-transfer branch (``is_one_time and
    start_period_id``) re-fetches the period and verifies ownership
    a second time, so a malicious ``start_period_id`` cannot leak
    into the transfer service.  The flash + redirect UX matches the
    existing template-form pattern; the security response rule
    (404 for both not-found and not-yours) is preserved indirectly
    by re-rendering the same form page rather than confirming
    whether the FK exists for someone else.
    """
    errors = _create_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("transfers.new_transfer_template"))

    data = _create_schema.load(request.form)

    # --- Route-boundary FK ownership ---
    # Single-return loop so adding a future FK does not push the
    # function past pylint's too-many-returns threshold.  The
    # message-per-FK detail is preserved via the per-row label.
    for model, pk, label in (
        (Account, data.get("from_account_id"), "source account"),
        (Account, data.get("to_account_id"), "destination account"),
        (Category, data.get("category_id"), "category"),
    ):
        if not _user_owns(model, pk):
            flash(f"Invalid {label}.", "danger")
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

        scenario = get_baseline_scenario(current_user.id)
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
        scenario = get_baseline_scenario(current_user.id)
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
@require_owner
def edit_transfer_template(template_id):
    """Display the transfer template edit form."""
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)

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
@require_owner
def update_transfer_template(template_id):
    """Update a transfer template and regenerate future transfers.

    Optimistic locking (commit C-18 / F-010): the edit form ships
    ``version_id`` as a hidden input.  When the submitted value
    differs from the row's current counter, the handler short-
    circuits with a flash + redirect so the audit trail records
    only the winner.  ``StaleDataError`` raised at flush time --
    e.g. by a concurrent transfer-template edit that races past
    the form-side check -- is caught and converted to the same
    flash + redirect.
    """
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)

    errors = _update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("transfers.edit_transfer_template", template_id=template_id))

    data = _update_schema.load(request.form)

    # Stale-form check (commit C-18 / F-010).
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != template.version_id:
        logger.info(
            "Stale-form conflict on update_transfer_template id=%d "
            "(submitted=%d, current=%d)",
            template_id, submitted_version, template.version_id,
        )
        flash(
            "This recurring transfer was changed by another action while "
            "you were editing.  Please reload and try again.",
            "warning",
        )
        return redirect(url_for(
            "transfers.edit_transfer_template", template_id=template_id,
        ))

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

    # --- Route-boundary FK ownership (commit C-27 / F-043) ---
    # Each user-scoped FK is verified only when present in the
    # partial update payload (the loaded ``data`` dict only carries
    # keys the user submitted -- BaseSchema's EXCLUDE meta drops
    # stray form fields).  ``category_id`` accepts ``None`` per the
    # schema; ``None`` clears the category and skips the probe.
    # Single-return loop so a future FK addition does not push the
    # function past pylint's too-many-returns threshold.
    ownership_failure = None
    for field, model, label in (
        ("from_account_id", Account, "source account"),
        ("to_account_id", Account, "destination account"),
        ("category_id", Category, "category"),
    ):
        if field not in data:
            continue
        value = data[field]
        if value is None:
            continue
        if not _user_owns(model, value):
            ownership_failure = label
            break
    if ownership_failure is not None:
        flash(f"Invalid {ownership_failure}.", "danger")
        return redirect(url_for(
            "transfers.edit_transfer_template", template_id=template_id,
        ))

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
    scenario = get_baseline_scenario(current_user.id)
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
    except StaleDataError:
        db.session.rollback()
        logger.info(
            "Stale-data conflict on update_transfer_template id=%d",
            template_id,
        )
        flash(
            "This recurring transfer was changed by another action while "
            "you were editing.  Please reload and try again.",
            "warning",
        )
        return redirect(url_for(
            "transfers.edit_transfer_template", template_id=template_id,
        ))
    except IntegrityError:
        db.session.rollback()
        flash("A recurring transfer with that name already exists.", "warning")
        return redirect(url_for("transfers.edit_transfer_template", template_id=template_id))
    flash(f"Recurring transfer '{template.name}' updated.", "success")
    return redirect(url_for("transfers.list_transfer_templates"))


@transfers_bp.route("/transfers/<int:template_id>/archive", methods=["POST"])
@login_required
@require_owner
def archive_transfer_template(template_id):
    """Archive a transfer template (stops future generation, keeps history).

    Soft-deletes projected transfers and their shadow transactions via
    the transfer service to maintain the three-level cascade:
    template archival -> transfer soft-delete -> shadow soft-delete.

    Optimistic locking (commit C-18 / F-010): the template's
    ``version_id`` is enforced by SQLAlchemy on the
    ``is_active = False`` flush; a concurrent edit raises
    ``StaleDataError`` which the handler converts into a flash +
    redirect so the user retries against fresh state.
    """
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)

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

    try:
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        logger.info(
            "Stale-data conflict on archive_transfer_template id=%d",
            template_id,
        )
        flash(
            "This recurring transfer was changed by another action.  "
            "Please reload and try again.",
            "warning",
        )
        return redirect(url_for("transfers.list_transfer_templates"))

    flash(
        f"Recurring transfer '{template.name}' archived. "
        f"{len(transfers_to_delete)} projected transfer(s) removed.",
        "info",
    )
    return redirect(url_for("transfers.list_transfer_templates"))


@transfers_bp.route("/transfers/<int:template_id>/unarchive", methods=["POST"])
@login_required
@require_owner
def unarchive_transfer_template(template_id):
    """Unarchive a transfer template.

    Restores soft-deleted transfers and their shadow transactions.

    Optimistic locking: see :func:`archive_transfer_template`.
    """
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)

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
        scenario = get_baseline_scenario(current_user.id)
        if scenario:
            periods = pay_period_service.get_all_periods(current_user.id)
            transfer_recurrence.generate_for_template(
                template, periods, scenario.id, effective_from=date.today(),
            )

    try:
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        logger.info(
            "Stale-data conflict on unarchive_transfer_template id=%d",
            template_id,
        )
        flash(
            "This recurring transfer was changed by another action.  "
            "Please reload and try again.",
            "warning",
        )
        return redirect(url_for("transfers.list_transfer_templates"))
    flash(
        f"Recurring transfer '{template.name}' unarchived. "
        f"{restored_count} projected transfer(s) restored.",
        "success",
    )
    return redirect(url_for("transfers.list_transfer_templates"))


@transfers_bp.route("/transfers/<int:template_id>/hard-delete", methods=["POST"])
@login_required
@require_owner
@fresh_login_required()
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
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)

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
            try:
                db.session.commit()
            except StaleDataError:
                db.session.rollback()
                logger.info(
                    "Stale-data conflict during archive-fallback in "
                    "hard_delete_transfer_template id=%d", template_id,
                )
                flash(
                    "This recurring transfer was changed by another action.  "
                    "Please reload and try again.",
                    "warning",
                )
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
    try:
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        logger.info(
            "Stale-data conflict on hard_delete_transfer_template id=%d",
            template_id,
        )
        flash(
            "This recurring transfer was changed by another action.  "
            "Please reload and try again.",
            "warning",
        )
        return redirect(url_for("transfers.list_transfer_templates"))

    flash(f"Recurring transfer '{template_name}' permanently deleted.", "info")
    return redirect(url_for("transfers.list_transfer_templates"))


# ── Grid Cell Routes ────────────────────────────────────────────────


@transfers_bp.route("/transfers/cell/<int:xfer_id>", methods=["GET"])
@login_required
@require_owner
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
@require_owner
def get_quick_edit(xfer_id):
    """HTMX partial: return the inline amount edit form for a transfer."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404
    return render_template("transfers/_transfer_quick_edit.html", xfer=xfer)


@transfers_bp.route("/transfers/<int:xfer_id>/full-edit", methods=["GET"])
@login_required
@require_owner
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
@require_owner
def update_transfer(xfer_id):
    """Update a transfer and its shadow transactions (inline edit save).

    Route-boundary FK ownership checks (commit C-27 / F-043 of the
    2026-04-15 security remediation plan): when the schema accepts
    a ``category_id`` (the only user-scoped FK
    :class:`TransferUpdateSchema` exposes), it is verified against
    ``current_user.id`` here, before the service is invoked.  This
    layered defense matches :func:`create_ad_hoc` and
    :func:`app.routes.transactions.create_inline`; the underlying
    ``transfer_service.update_transfer`` already runs the same
    ownership check via ``_get_owned_category``, but enforcing the
    rule at the route layer keeps the security boundary visible
    where requests arrive and protects against a future refactor
    that bypasses the service helper.  ``status_id`` is a reference
    table FK (not user-scoped) and so does not need an ownership
    check.  Setting ``category_id`` to ``None`` (clearing the
    category) is permitted and skips the ownership probe per the
    schema's ``allow_none=True`` policy.

    Optimistic locking (commit C-18 / F-010): the cell ships
    ``version_id`` as a hidden input set to ``Transfer.version_id``
    at render time.  When the submitted value differs from the
    row's current counter, the handler short-circuits with a 409 +
    conflict cell partial and records nothing.  ``StaleDataError``
    raised at flush time -- the truly-concurrent case the form-side
    check cannot see -- is caught and converted to the same 409 +
    conflict cell so the user retries against fresh state instead
    of seeing a 500.
    """
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404

    errors = _xfer_update_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _xfer_update_schema.load(request.form)

    # Stale-form check.
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != xfer.version_id:
        logger.info(
            "Stale-form conflict on update_transfer id=%d "
            "(submitted=%d, current=%d)",
            xfer_id, submitted_version, xfer.version_id,
        )
        return _stale_transfer_response(xfer_id), 409

    # --- Route-boundary FK ownership (commit C-27 / F-043) ---
    # The only user-scoped FK ``TransferUpdateSchema`` exposes is
    # ``category_id``; ``status_id`` references the ref table.
    # ``allow_none=True`` on ``category_id`` means clearing the
    # category (setting it to NULL) is legitimate and must skip the
    # ownership probe -- the service drops it through unchanged in
    # that case.
    if data.get("category_id") is not None and not _user_owns(
        Category, data["category_id"],
    ):
        return "Not found", 404

    # Auto-set is_override when a template-linked transfer's amount changes.
    if xfer.transfer_template_id and "amount" in data:
        data["is_override"] = True

    try:
        transfer_service.update_transfer(xfer.id, current_user.id, **data)
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on update_transfer id=%d", xfer_id,
        )
        return _stale_transfer_response(xfer_id), 409
    except NotFoundError:
        return "Not found", 404
    except ShekelValidationError as exc:
        return jsonify(errors={"_schema": [str(exc)]}), 400
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
        response = render_template(
            "grid/_transaction_cell.html",
            txn=shadow,
            entry_sums=build_entry_sums_dict([shadow]),
        )
        return response, 200, {"HX-Trigger": "balanceChanged"}

    account = resolve_grid_account(current_user.id, current_user.settings)
    response = render_template(
        "transfers/_transfer_cell.html", xfer=xfer, account=account,
    )
    return response, 200, {"HX-Trigger": "balanceChanged"}


@transfers_bp.route("/transfers/ad-hoc", methods=["POST"])
@login_required
@require_owner
def create_ad_hoc():
    """Create an ad-hoc (one-time) transfer with shadow transactions.

    Route-boundary FK ownership checks (commit C-27 / F-043 of the
    2026-04-15 security remediation plan): every FK accepted from
    the form is verified against ``current_user.id`` before the
    service is invoked, mirroring the
    :func:`app.routes.transactions.create_inline` pattern.  This
    layered defense is intentional: ``transfer_service`` already
    runs the same checks via its private ``_get_owned_*`` helpers,
    but a future refactor (or a new ``service`` consumer) that
    skips one of those calls would silently regress the IDOR
    protection.  Per the project security response rule, all
    ownership failures return 404 -- the same status as a missing
    record -- so the response leaks no information about whether
    the row exists for someone else.

    Double-submit handling (F-050 / C-22): the partial unique index
    ``uq_transfers_adhoc_dedupe`` on
    ``(user_id, from_account_id, to_account_id, amount, pay_period_id,
    scenario_id)`` rejects a second active ad-hoc transfer with
    identical parameters.  When two requests race past the form (a
    network retry, a double-click, the back-and-resubmit pattern), the
    first commits the transfer and the second's INSERT fires the index
    constraint.  Rather than surface the database error as a generic
    400, this handler treats the second request as idempotent success:
    rolls back the failed INSERT, re-fetches the winning transfer,
    and returns the same 201 + cell HTML the first request produced.
    The user sees the transfer they intended to create regardless of
    which request reached the database first.
    """
    errors = _xfer_create_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _xfer_create_schema.load(request.form)

    # --- Route-boundary FK ownership (commit C-27 / F-043) ---
    # Verify every FK the schema accepted belongs to the requester
    # BEFORE the service call so the security boundary is visible at
    # the route layer and a future refactor that bypasses
    # ``transfer_service._get_owned_*`` does not silently regress
    # IDOR protection.  All failures collapse to 404 per the
    # project's security response rule -- the loop body has a
    # single ``return`` so adding a sixth FK in the future does
    # not push the function past pylint's too-many-returns
    # threshold.
    for model, pk in (
        (Account, data["from_account_id"]),
        (Account, data["to_account_id"]),
        (PayPeriod, data["pay_period_id"]),
        (Scenario, data["scenario_id"]),
        (Category, data["category_id"]),
    ):
        if not _user_owns(model, pk):
            return "Not found", 404

    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

    # ``transfer_service.create_transfer`` calls ``db.session.flush()``
    # internally to obtain the transfer's primary key for the shadow
    # rows.  The IntegrityError on ``uq_transfers_adhoc_dedupe`` therefore
    # fires *during* the service call, not at the subsequent
    # ``db.session.commit()``.  Both code paths must catch the
    # constraint hit and translate it into idempotent success or the
    # caller sees a 500.
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
    except IntegrityError as exc:
        db.session.rollback()
        if is_unique_violation(exc, _TRANSFER_ADHOC_UNIQUE_INDEX):
            return _adhoc_dedupe_idempotent_response(data)
        return "Invalid reference. Check that all referenced records exist.", 400

    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        if is_unique_violation(exc, _TRANSFER_ADHOC_UNIQUE_INDEX):
            return _adhoc_dedupe_idempotent_response(data)
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d created ad-hoc transfer (id=%d)", current_user.id, xfer.id)

    account = resolve_grid_account(current_user.id, current_user.settings)
    response = render_template(
        "transfers/_transfer_cell.html", xfer=xfer, account=account, wrap_div=True,
    )
    return response, 201, {"HX-Trigger": "balanceChanged"}


def _adhoc_dedupe_idempotent_response(data):
    """Return the winning ad-hoc transfer's cell as idempotent success.

    Called from ``create_ad_hoc`` when ``uq_transfers_adhoc_dedupe``
    rejects a second concurrent INSERT.  Re-fetches the active
    transfer matching the submitted parameters and returns the same
    201 + ``_transfer_cell.html`` payload the first request produced
    so the user's view matches the database state regardless of which
    request reached PostgreSQL first.

    The lookup uses the same predicate as the index (matching
    ``transfer_template_id IS NULL`` and ``is_deleted = FALSE``) so it
    only ever matches the row that triggered the violation.  A
    missing match indicates the row was deleted between the
    IntegrityError and this lookup -- vanishingly unlikely under
    normal use, but treated as a 409 with a clear message rather than
    a 500.

    Args:
        data: The deserialised ``TransferCreateSchema`` output for
            the failed request.

    Returns:
        Flask response tuple: ``(html, 201, {"HX-Trigger": ...})`` on
        success, or a 409 string on the unrecoverable race.
    """
    existing = (
        db.session.query(Transfer)
        .filter(
            Transfer.user_id == current_user.id,
            Transfer.from_account_id == data["from_account_id"],
            Transfer.to_account_id == data["to_account_id"],
            Transfer.amount == data["amount"],
            Transfer.pay_period_id == data["pay_period_id"],
            Transfer.scenario_id == data["scenario_id"],
            Transfer.transfer_template_id.is_(None),
            Transfer.is_deleted.is_(False),
        )
        .first()
    )
    if existing is None:
        # Vanishingly rare: the winning row was soft-deleted or hard-
        # deleted between the IntegrityError and this lookup.  Surface
        # a 409 so the operator retries against the post-delete state
        # instead of seeing a 500 from the missing record.
        return (
            "Duplicate ad-hoc transfer detected but the winning row "
            "is no longer active.  Reload and try again.",
            409,
        )
    logger.info(
        "Duplicate ad-hoc transfer prevented; returning existing id=%d "
        "(idempotent success)", existing.id,
    )
    account = resolve_grid_account(current_user.id, current_user.settings)
    response = render_template(
        "transfers/_transfer_cell.html",
        xfer=existing, account=account, wrap_div=True,
    )
    return response, 201, {"HX-Trigger": "balanceChanged"}


@transfers_bp.route("/transfers/instance/<int:xfer_id>", methods=["DELETE"])
@login_required
@require_owner
def delete_transfer(xfer_id):
    """Soft-delete a template transfer or hard-delete an ad-hoc transfer.

    Routes through transfer_service to ensure shadow transactions are
    also deleted (soft or hard) alongside the parent transfer.

    Optimistic locking (commit C-18 / F-010): the soft-delete UPDATE
    and hard-delete DELETE are both version-pinned by SQLAlchemy.
    A concurrent commit that bumped the row's version raises
    ``StaleDataError`` which the handler converts into a 409 +
    conflict cell.
    """
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404

    soft = bool(xfer.transfer_template_id)
    try:
        transfer_service.delete_transfer(xfer.id, current_user.id, soft=soft)
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on delete_transfer id=%d", xfer_id,
        )
        return _stale_transfer_response(xfer_id), 409
    logger.info("user_id=%d deleted transfer %d", current_user.id, xfer_id)
    return "", 200, {"HX-Trigger": "balanceChanged"}


# ── Transfer Status Actions ─────────────────────────────────────────


@transfers_bp.route("/transfers/instance/<int:xfer_id>/mark-done", methods=["POST"])
@login_required
@require_owner
def mark_done(xfer_id):
    """Mark a transfer and its shadows as 'done' (settled).

    Optimistic locking (commit C-18 / F-010): no form-side
    ``version_id`` is shipped with the button click; the SQLAlchemy
    ``version_id_col`` lock catches concurrent races at flush time
    and the handler converts ``StaleDataError`` into a 409 +
    conflict cell.
    """
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404

    done_id = ref_cache.status_id(StatusEnum.DONE)
    try:
        # ``paid_at`` parity with ``transactions.mark_done`` and
        # ``dashboard.mark_paid``: settling a transfer must record
        # *when* it was settled.  Without this kwarg the shadow
        # transactions reach Paid with NULL ``paid_at``, breaking
        # ``Transaction.days_paid_before_due`` analytics, the
        # dashboard's "paid on time" indicator, and any downstream
        # report that joins on the timestamp.  The transfer service
        # mirrors the same default (see ``update_transfer``) so any
        # future caller that forgets the kwarg still produces a
        # well-formed settled transfer.  Audit reference: F-048 /
        # commit C-22 of the 2026-04-15 security remediation plan.
        transfer_service.update_transfer(
            xfer.id, current_user.id,
            status_id=done_id, paid_at=db.func.now(),
        )
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on transfer mark_done id=%d", xfer_id,
        )
        return _stale_transfer_response(xfer_id), 409
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d marked transfer %d as done", current_user.id, xfer_id)

    # Grid shadow context: render transaction cell with gridRefresh
    # (matches the transaction route guard pattern for status changes).
    shadow = _resolve_shadow_context(xfer)
    if shadow is not None:
        db.session.refresh(shadow)
        response = render_template(
            "grid/_transaction_cell.html",
            txn=shadow,
            entry_sums=build_entry_sums_dict([shadow]),
        )
        return response, 200, {"HX-Trigger": "gridRefresh"}

    account = resolve_grid_account(current_user.id, current_user.settings)
    response = render_template(
        "transfers/_transfer_cell.html", xfer=xfer, account=account,
    )
    return response, 200, {"HX-Trigger": "balanceChanged"}


@transfers_bp.route("/transfers/instance/<int:xfer_id>/cancel", methods=["POST"])
@login_required
@require_owner
def cancel_transfer(xfer_id):
    """Mark a transfer and its shadows as 'cancelled'.

    Optimistic locking: see :func:`mark_done`.
    """
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404

    cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)
    try:
        transfer_service.update_transfer(
            xfer.id, current_user.id, status_id=cancelled_id,
        )
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on cancel_transfer id=%d", xfer_id,
        )
        return _stale_transfer_response(xfer_id), 409
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d cancelled transfer %d", current_user.id, xfer_id)

    # Grid shadow context: render transaction cell with gridRefresh
    # (matches the transaction route guard pattern for status changes).
    shadow = _resolve_shadow_context(xfer)
    if shadow is not None:
        db.session.refresh(shadow)
        response = render_template(
            "grid/_transaction_cell.html",
            txn=shadow,
            entry_sums=build_entry_sums_dict([shadow]),
        )
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


def _user_owns(model, pk):
    """Return True iff the row at *pk* exists and belongs to ``current_user``.

    Used by :func:`create_ad_hoc`, :func:`update_transfer`,
    :func:`create_transfer_template`, and
    :func:`update_transfer_template` to enforce route-boundary FK
    ownership without scattering ``db.session.get`` + null/owner
    boilerplate across each endpoint.  The helper is intentionally
    minimal -- it returns a boolean so the caller controls the 404
    response shape (HTMX text vs flash + redirect for the form
    routes); centralising the response would force every consumer
    onto a single shape and lose the existing UX parity with
    surrounding code.

    All consulted models in commit C-27 (``Account``, ``PayPeriod``,
    ``Scenario``, ``Category``) carry a direct ``user_id`` column, so
    the check is a single ``db.session.get`` followed by an equality
    compare.  Following the project security response rule, callers
    surface ownership failures as 404 -- identical to the missing-PK
    case -- so the response leaks no information about whether the
    row exists for someone else.

    Args:
        model: SQLAlchemy model class with a ``user_id`` column.
        pk: Primary key value.  ``None`` is treated as "no row" and
            returns ``False`` so a caller that passes an optional FK
            stays safe.

    Returns:
        True if the row exists and ``row.user_id == current_user.id``;
        False otherwise.
    """
    if pk is None:
        return False
    record = db.session.get(model, pk)
    if record is None:
        return False
    return record.user_id == current_user.id


def _stale_transfer_response(xfer_id):
    """Roll back the session and render the transfer cell in conflict mode.

    Used by every transfer-mutating HTMX route to convert a stale
    form or ``StaleDataError`` flush failure into a coherent UI
    response instead of a 500.  Re-fetches the transfer so the
    user's view shows the winner's state (never the loser's stale
    in-memory copy) and tags the cell with ``conflict=True``.
    Falls back to a 404 string when the row was hard-deleted by
    the winning request.

    Note: caller adds the 409 status code -- this helper returns
    only the rendered HTML so it can be reused both before and
    after the database commit.

    Args:
        xfer_id: Primary key of the transfer the route was trying
            to mutate.

    Returns:
        Rendered HTML string, or the literal string ``"Not found"``
        when the row no longer exists.
    """
    db.session.rollback()
    db.session.expire_all()
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found"

    # Render the shadow's transaction cell when the request came
    # from the grid (source_txn_id present and validated), or the
    # transfer cell otherwise.  Mirrors the shadow-context handling
    # in :func:`update_transfer`.
    shadow = _resolve_shadow_context(xfer)
    if shadow is not None:
        db.session.refresh(shadow)
        return render_template(
            "grid/_transaction_cell.html",
            txn=shadow,
            entry_sums=build_entry_sums_dict([shadow]),
            conflict=True,
        )

    account = resolve_grid_account(current_user.id, current_user.settings)
    return render_template(
        "transfers/_transfer_cell.html",
        xfer=xfer, account=account, conflict=True,
    )


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
