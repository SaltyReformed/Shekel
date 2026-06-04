"""
Shekel Budget App -- Template Management Routes

CRUD pages for transaction templates and their recurrence rules.
Updating a template triggers recurrence regeneration.
"""

import logging
from datetime import date

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from markupsafe import Markup

from app.utils.auth_helpers import fresh_login_required, get_or_404, require_owner
from app.extensions import db
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app.models.pay_period import PayPeriod
from app.models.category import Category
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.ref import RecurrencePattern, Status, TransactionType
from app import ref_cache
from app.enums import RecurrencePatternEnum, TxnTypeEnum
from app.utils import archive_helpers
from app.schemas.validation import TemplateCreateSchema, TemplateUpdateSchema
from app.services import (
    account_service,
    category_service,
    pay_period_service,
    recurrence_engine,
)
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.balance_predicates import is_projected_clause
from app.exceptions import RecurrenceConflict
from app.routes._recurrence_form_helpers import (
    STALE_ACTION_MESSAGE,
    STALE_EDITING_MESSAGE,
    build_recurrence_rule_from_form,
    commit_or_handle_stale,
    handle_recurrence_conflict,
    handle_stale_form_conflict,
    resolve_recurrence_rule_for_update,
)

logger = logging.getLogger(__name__)

# Field allowlist for the template update route: which submitted form
# fields may be written back to the template via setattr.
_TEMPLATE_UPDATE_FIELDS = {
    "name", "default_amount", "category_id", "transaction_type_id",
    "account_id", "is_active", "sort_order",
    "is_envelope", "companion_visible",
}

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
    categories = category_service.list_active_categories(current_user.id)
    accounts = account_service.list_active_accounts(current_user.id)
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

    # The pop + ``build_recurrence_rule_from_form`` call below is the
    # shared create-form preamble; ``transfers.create_transfer_template``
    # runs the byte-identical sequence.  The rule-building logic itself is
    # already DRY in the F-24 helper; only the call site repeats, and it
    # cannot be hoisted into a further wrapper because the transfers side
    # reuses ``start_period_id`` afterward (its one-time-transfer branch)
    # while this route does not -- a wrapper that popped it internally
    # would have to thread it back out (coding-standards rule 13).
    # One-sided ``duplicate-code`` disable (see plan.md Phase 2 notes).
    # pylint: disable=duplicate-code
    # Extract start_period_id and end_date before creating the rule.
    start_period_id = data.pop("start_period_id", None)
    end_date = data.pop("end_date", None)

    # Create the recurrence rule if a pattern was specified.  The
    # F-24 helper pops every recurrence-related key from ``data`` so
    # the TransactionTemplate constructor below does not receive
    # stray kwargs; it returns a flushed RecurrenceRule, ``None``
    # when no pattern was selected, or a Flask redirect Response
    # for the invalid-pattern / invalid-start-period validation
    # failures (caller returns the redirect verbatim).
    rule_or_redirect = build_recurrence_rule_from_form(
        data,
        user_id=current_user.id,
        start_period_id=start_period_id,
        end_date_value=end_date,
        redirect_endpoint="templates.new_template",
        include_due_day_of_month=True,
    )
    if isinstance(rule_or_redirect, Response):
        return rule_or_redirect
    rule = rule_or_redirect
    # pylint: enable=duplicate-code

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
        scenario = get_baseline_scenario(current_user.id)
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

    categories = category_service.list_active_categories(current_user.id)
    accounts = account_service.list_active_accounts(current_user.id)
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

    # The load / version-guard / pop / resolve preamble below is the
    # standard parallel-CRUD update shape it shares with
    # ``transfers.update_transfer_template``.  Its substantive steps are
    # already DRY: the optimistic-lock guard (``handle_stale_form_conflict``)
    # and the recurrence-rule resolution (``resolve_recurrence_rule_for_update``)
    # live in the shared F-24 helper module.  What remains duplicated is only
    # the ORDER in which this route invokes those helpers; folding that call
    # sequence into a further helper would couple two separate template
    # domains (transaction-template envelope tracking + name propagation vs
    # transfer-template name-uniqueness + shadow invariants) behind awkward
    # multi-value returns for no real gain (coding-standards rule 13).
    # One-sided ``duplicate-code`` disable per the R0801 mechanics in
    # ``docs/audits/pylint-cleanup/plan.md`` (Phase 2 working notes).
    # pylint: disable=duplicate-code
    data = _update_schema.load(request.form)

    # Stale-form check (commit C-18 / F-010).  Routed through the
    # F-26 helper so the pre-flush optimistic-locking guard shares a
    # single implementation with the parallel transfer-template
    # update route.
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != template.version_id:
        return handle_stale_form_conflict(
            logger=logger,
            log_label="update_template",
            log_id=template_id,
            submitted=submitted_version,
            current=template.version_id,
            flash_message=STALE_EDITING_MESSAGE.format(
                noun="recurring transaction",
            ),
            redirect_endpoint="templates.edit_template",
            redirect_endpoint_kwargs={"template_id": template_id},
        )

    effective_from = data.pop("effective_from", date.today())

    # Remove start_period_id from update data (set once at creation).
    data.pop("start_period_id", None)
    end_date = data.pop("end_date", None)

    # Re-point or rebuild the recurrence rule from the update payload
    # (F-24).  The helper dispatches the existing-rule (mutate in place)
    # vs no-existing-rule (build + link) branches and pops every
    # recurrence key from ``data`` so the field-update loop below sees
    # none.
    redirect_response = resolve_recurrence_rule_for_update(
        template,
        data,
        end_date_value=end_date,
        redirect_endpoint="templates.edit_template",
        redirect_endpoint_kwargs={"template_id": template_id},
        include_due_day_of_month=True,
    )
    if redirect_response is not None:
        return redirect_response
    # pylint: enable=duplicate-code

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
    scenario = get_baseline_scenario(current_user.id)
    if scenario and template.recurrence_rule:
        periods = pay_period_service.get_all_periods(current_user.id)
        try:
            recurrence_engine.regenerate_for_template(
                template, periods, scenario.id, effective_from=effective_from,
            )
        except RecurrenceConflict as conflict:
            # Phase-1 auto-keep-overrides advisory.  Routed through
            # the F-26 helper so the log message and the user-facing
            # flash share a single implementation with the parallel
            # transfer-template regeneration call.  ``log_label``
            # preserves the templates-side prefix verbatim so log-
            # grep patterns stay valid.
            handle_recurrence_conflict(
                logger=logger,
                log_label="Recurrence conflict for template",
                log_id=template.id,
                conflict=conflict,
            )

    conflict = commit_or_handle_stale(
        logger=logger,
        log_label="update_template",
        log_id=template_id,
        flash_message=STALE_EDITING_MESSAGE.format(
            noun="recurring transaction",
        ),
        redirect_endpoint="templates.edit_template",
        redirect_endpoint_kwargs={"template_id": template_id},
    )
    if conflict is not None:
        return conflict
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
    # Centralized ``is_projected_clause`` (D6-09 / MED-02) so the
    # archive-template, unarchive-template, and hard-delete-fallback
    # filters in this module share one definition.
    deleted_count = db.session.query(Transaction).filter(
        Transaction.template_id == template.id,
        is_projected_clause(Transaction),
        Transaction.is_deleted.is_(False),
    ).update({"is_deleted": True}, synchronize_session="fetch")

    conflict = commit_or_handle_stale(
        logger=logger,
        log_label="archive_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transaction",
        ),
        redirect_endpoint="templates.list_templates",
    )
    if conflict is not None:
        return conflict

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

    # Restore soft-deleted projected transactions.  Routed through
    # ``is_projected_clause`` (D6-09 / MED-02); see ``archive_template``.
    restored_count = db.session.query(Transaction).filter(
        Transaction.template_id == template.id,
        is_projected_clause(Transaction),
        Transaction.is_deleted.is_(True),
    ).update({"is_deleted": False}, synchronize_session="fetch")

    # Regenerate to fill in any missing future periods.
    if template.recurrence_rule:
        scenario = get_baseline_scenario(current_user.id)
        if scenario:
            periods = pay_period_service.get_all_periods(current_user.id)
            recurrence_engine.generate_for_template(
                template, periods, scenario.id, effective_from=date.today(),
            )

    conflict = commit_or_handle_stale(
        logger=logger,
        log_label="unarchive_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transaction",
        ),
        redirect_endpoint="templates.list_templates",
    )
    if conflict is not None:
        return conflict

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
    """Permanently delete a transaction template if it has no settled history.

    Two-path logic:
      1. If the template has any settled transaction (Paid, Received, or
         Settled -- anything with ``Status.is_settled = True``), permanent
         deletion is blocked.  The template is archived instead (if not
         already) and the user is warned.
      2. If no settled history exists, all linked NON-SETTLED transactions
         are deleted first, then the template itself is permanently
         removed.  ``Transaction.template_id`` is a FK with ON DELETE SET
         NULL, so any rows that survive the filtered delete keep their
         financial data intact with a NULL template_id rather than
         cascading away.

    Defense in depth (CRIT-05 / E-22): the bulk delete is constrained to
    non-settled rows via the semantic ``Status.is_settled`` boolean.
    Even if the guard predicate above regresses, is bypassed, or races a
    concurrent mark-done that lands between the guard check and the
    delete, settled financial history (Paid, Received, Settled) cannot
    be physically destroyed by this route.  The pre-fix code enumerated
    ``[DONE, SETTLED]`` and silently omitted RECEIVED, then bulk-deleted
    unconditionally -- the irreversible data-loss path CRIT-05 documents.
    """
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)

    # The paid-history-blocked branch below (flash + archive toggle) is
    # the byte-identical sibling of
    # ``transfers.hard_delete_transfer_template``; only the
    # ``*_has_paid_history`` guard name and the divergent projected-row
    # soft-delete that follows differ.  The shared part is too thin and
    # too coupled to its two parallel routes to extract without
    # indirection that removes no logic (coding-standards rule 13).
    # One-sided ``duplicate-code`` disable (see plan.md Phase 2 notes).
    # pylint: disable=duplicate-code
    if archive_helpers.template_has_paid_history(template.id):
        flash(
            f"'{template.name}' has payment history and cannot be permanently "
            "deleted. It has been archived instead.",
            "warning",
        )
        if template.is_active:
            template.is_active = False
            # pylint: enable=duplicate-code
            # Soft-delete projected transactions (same logic as
            # archive_template).  Routed through ``is_projected_clause``
            # (D6-09 / MED-02); see ``archive_template`` above.
            db.session.query(Transaction).filter(
                Transaction.template_id == template.id,
                is_projected_clause(Transaction),
                Transaction.is_deleted.is_(False),
            ).update({"is_deleted": True}, synchronize_session="fetch")
            conflict = commit_or_handle_stale(
                logger=logger,
                log_label="hard_delete_template archive-fallback",
                log_id=template_id,
                flash_message=STALE_ACTION_MESSAGE.format(
                    noun="recurring transaction",
                ),
                redirect_endpoint="templates.list_templates",
            )
            if conflict is not None:
                return conflict
        return redirect(url_for("templates.list_templates"))

    # No settled history -- safe to permanently delete.  Restrict the
    # bulk delete to non-settled rows via ``Status.is_settled`` so a
    # race-window mark-done (or any future caller that bypasses the
    # guard above) cannot destroy real Paid/Received/Settled history.
    # The FK ON DELETE SET NULL on ``Transaction.template_id`` means
    # any row that survives this filter keeps its financial data with
    # a null template_id rather than being cascaded away.
    template_name = template.name
    settled_status_ids = db.session.query(Status.id).filter(
        Status.is_settled.is_(True)
    ).scalar_subquery()
    db.session.query(Transaction).filter(
        Transaction.template_id == template.id,
        Transaction.status_id.notin_(settled_status_ids),
    ).delete(synchronize_session="fetch")

    db.session.delete(template)
    conflict = commit_or_handle_stale(
        logger=logger,
        log_label="hard_delete_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transaction",
        ),
        redirect_endpoint="templates.list_templates",
    )
    if conflict is not None:
        return conflict

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
