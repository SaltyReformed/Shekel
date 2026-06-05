"""
Shekel Budget App -- Transfer Routes

CRUD for transfer templates and inline grid cell endpoints for transfers.
Follows the same patterns as templates.py (template CRUD) and
transactions.py (grid cell HTMX endpoints).
"""

import logging
from datetime import date

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for, jsonify
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.utils.auth_helpers import fresh_login_required, get_or_404, require_owner
from app.extensions import db
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
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
from app.services import (
    account_service,
    category_service,
    pay_period_service,
    transfer_recurrence,
    transfer_service,
)
from app.services.account_resolver import resolve_grid_account
from app.services.entry_service import build_entry_sums_dict
from app.services.recurrence_engine import _compute_due_date
from app.services.scenario_resolver import get_baseline_scenario
from app.exceptions import NotFoundError, RecurrenceConflict, ValidationError as ShekelValidationError
from app.utils.balance_predicates import is_projected_clause
from app.utils.db_errors import is_unique_violation
from app.routes._commit_helpers import (
    commit_or_handle_stale,
    handle_stale_conflict,
)
from app.routes._recurrence_form_helpers import (
    STALE_ACTION_MESSAGE,
    STALE_EDITING_MESSAGE,
    build_recurrence_rule_from_form,
    handle_recurrence_conflict,
    handle_stale_form_conflict,
    resolve_recurrence_rule_for_update,
)
from app.routes._transfer_creation_helpers import (
    flush_template_or_namedup_redirect,
    generate_transfers_for_all_periods,
)

logger = logging.getLogger(__name__)

# Field allowlist for the transfer-template update route: which submitted
# form fields may be written back to the template via setattr.
_TEMPLATE_UPDATE_FIELDS = {
    "name", "default_amount", "from_account_id", "to_account_id",
    "category_id", "is_active", "sort_order",
}

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
    accounts = account_service.list_active_accounts(current_user.id)
    categories = category_service.list_active_categories(current_user.id)
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

    # Create the recurrence rule via the F-24 helper.  Transfer
    # templates require a non-NULL ``recurrence_rule_id`` (the column
    # has no nullable contract for create -- single-shot transfers
    # use the ONCE pattern), so the upstream
    # ``_create_schema.validate`` rejects payloads without a
    # ``recurrence_pattern``; the helper's "no pattern -> None"
    # branch is therefore unreachable here, and the downstream
    # ``rule.id`` access matches the pre-extraction contract.
    rule_or_redirect = build_recurrence_rule_from_form(
        data,
        user_id=current_user.id,
        start_period_id=start_period_id,
        end_date_value=end_date,
        redirect_endpoint="transfers.new_transfer_template",
        include_due_day_of_month=False,
    )
    if isinstance(rule_or_redirect, Response):
        return rule_or_redirect
    rule = rule_or_redirect

    template = TransferTemplate(
        user_id=current_user.id,
        recurrence_rule_id=rule.id,
        **data,
    )
    db.session.add(template)

    namedup_redirect = flush_template_or_namedup_redirect(
        redirect_endpoint="transfers.list_transfer_templates",
        name_dup_message="A transfer with that name already exists.",
    )
    if namedup_redirect is not None:
        return namedup_redirect

    # Create the initial transfer instance(s) for the new template: a
    # single Transfer for the ONCE pattern, or a recurrence-engine fan-out
    # otherwise.  Returns a redirect Response on an invalid period or a
    # service rejection, which is propagated verbatim.
    materialize_redirect = _materialize_initial_transfers(
        template, rule, start_period_id,
    )
    if materialize_redirect is not None:
        return materialize_redirect

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

    accounts = account_service.list_active_accounts(current_user.id)
    categories = category_service.list_active_categories(current_user.id)
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

    # Stale-form check (commit C-18 / F-010).  Routed through the
    # F-26 helper so the pre-flush optimistic-locking guard shares a
    # single implementation with the parallel transaction-template
    # update route.
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != template.version_id:
        return handle_stale_form_conflict(
            logger=logger,
            log_label="update_transfer_template",
            log_id=template_id,
            submitted=submitted_version,
            current=template.version_id,
            flash_message=STALE_EDITING_MESSAGE.format(
                noun="recurring transfer",
            ),
            redirect_endpoint="transfers.edit_transfer_template",
            redirect_endpoint_kwargs={"template_id": template_id},
        )

    effective_from = data.pop("effective_from", date.today())
    data.pop("start_period_id", None)
    end_date = data.pop("end_date", None)

    # Re-point or rebuild the recurrence rule from the update payload
    # (F-24).  The helper dispatches the existing-rule (mutate in place)
    # vs no-existing-rule (build + link) branches and pops every
    # recurrence key from ``data``.  ``include_due_day_of_month=False``
    # because the transfer-template schemas do not expose the field.
    redirect_response = resolve_recurrence_rule_for_update(
        template,
        data,
        end_date_value=end_date,
        redirect_endpoint="transfers.edit_transfer_template",
        redirect_endpoint_kwargs={"template_id": template_id},
        include_due_day_of_month=False,
    )
    if redirect_response is not None:
        return redirect_response

    # --- Route-boundary FK ownership (commit C-27 / F-043) ---
    ownership_failure = _first_unowned_template_fk(data)
    if ownership_failure is not None:
        flash(f"Invalid {ownership_failure}.", "danger")
        return redirect(url_for(
            "transfers.edit_transfer_template", template_id=template_id,
        ))

    for field, value in data.items():
        if field in _TEMPLATE_UPDATE_FIELDS:
            setattr(template, field, value)

    # Flush template changes first so name-uniqueness violations are caught
    # before regeneration dirties the session with transfer deletes/creates.
    namedup_redirect = flush_template_or_namedup_redirect(
        redirect_endpoint="transfers.edit_transfer_template",
        redirect_kwargs={"template_id": template_id},
    )
    if namedup_redirect is not None:
        return namedup_redirect

    return _regenerate_and_commit_template(template, effective_from, template_id)


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

    # Find projected, non-deleted transfers to soft-delete.  Routed
    # through the centralized ``is_projected_clause`` (D6-09 / MED-02)
    # parameterised on ``Transfer`` so the rule "what does a
    # Projected filter look like in SQL" is shared with the
    # Transaction filter sites.
    transfers_to_delete = (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == template.id,
            is_projected_clause(Transfer),
            Transfer.is_deleted.is_(False),
        )
        .all()
    )

    # Route each through the service to ensure shadows are soft-deleted.
    for xfer in transfers_to_delete:
        transfer_service.delete_transfer(xfer.id, current_user.id, soft=True)

    conflict = commit_or_handle_stale(
        logger=logger,
        log_label="archive_transfer_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transfer",
        ),
        redirect_endpoint="transfers.list_transfer_templates",
    )
    if conflict is not None:
        return conflict

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

    # Find soft-deleted projected transfers to restore.  Routed
    # through ``is_projected_clause(Transfer)`` (D6-09 / MED-02);
    # see ``archive_transfer_template`` above.
    transfers_to_restore = (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == template.id,
            is_projected_clause(Transfer),
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
        generate_transfers_for_all_periods(template, effective_from=date.today())

    conflict = commit_or_handle_stale(
        logger=logger,
        log_label="unarchive_transfer_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transfer",
        ),
        redirect_endpoint="transfers.list_transfer_templates",
    )
    if conflict is not None:
        return conflict
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
      - No history: linked transfers are hard-deleted through the
        transfer service (which CASCADE-deletes shadows), then the
        template itself is permanently removed.

    Defense in depth (F-14): the bulk delete is constrained to non-
    settled transfers via the semantic ``Status.is_settled`` boolean,
    mirroring the ``templates.py::hard_delete_template`` shape added
    after CRIT-05.  Even if the guard predicate above regresses, is
    bypassed, or races a concurrent mark-done that lands between the
    guard check and the loop, settled transfers (Paid, Received,
    Settled) and their two-shadow pairs cannot be physically destroyed
    by this route.  Survivors retain their ``transfer_template_id``;
    the column's FK is ``ON DELETE SET NULL`` so they become detached
    settled history when the parent template is removed.
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
            # Routed through ``is_projected_clause(Transfer)``
            # (D6-09 / MED-02); see ``archive_transfer_template`` above.
            transfers_to_delete = (
                db.session.query(Transfer)
                .filter(
                    Transfer.transfer_template_id == template.id,
                    is_projected_clause(Transfer),
                    Transfer.is_deleted.is_(False),
                )
                .all()
            )
            for xfer in transfers_to_delete:
                transfer_service.delete_transfer(xfer.id, current_user.id, soft=True)
            conflict = commit_or_handle_stale(
                logger=logger,
                log_label="hard_delete_transfer_template archive-fallback",
                log_id=template_id,
                flash_message=STALE_ACTION_MESSAGE.format(
                    noun="recurring transfer",
                ),
                redirect_endpoint="transfers.list_transfer_templates",
            )
            if conflict is not None:
                return conflict
        return redirect(url_for("transfers.list_transfer_templates"))

    # No history -- safe to permanently delete linked transfers through
    # the transfer service so that shadow transactions are CASCADE-
    # deleted (invariants 1, 2, 4).  ``transfer_service.delete_transfer``
    # flushes but does not commit, so all deletions are atomic within a
    # single DB transaction.
    #
    # Defense in depth (F-14 / commit C-21 mirror): the bulk delete is
    # additionally constrained to ``Status.is_settled = False`` rows via
    # the semantic ``Status.is_settled`` boolean -- the same shape
    # ``templates.py::hard_delete_template`` applies after CRIT-05.
    # Even if ``transfer_template_has_paid_history`` regresses, is
    # bypassed, or races a concurrent mark-done that lands between the
    # guard check and the loop below, settled transfers (Paid,
    # Received, Settled) and their two-shadow pairs cannot be
    # physically destroyed by this route.  Survivors retain their
    # ``transfer_template_id``; the column's FK is ``ON DELETE SET
    # NULL`` (see ``app/models/transfer.py``) so they become detached
    # settled history when the parent template is removed below.
    template_name = template.name
    settled_status_ids = db.session.query(Status.id).filter(
        Status.is_settled.is_(True)
    ).scalar_subquery()
    deletable_transfers = (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == template.id,
            Transfer.status_id.notin_(settled_status_ids),
        )
        .all()
    )
    for xfer in deletable_transfers:
        transfer_service.delete_transfer(xfer.id, current_user.id, soft=False)

    db.session.delete(template)
    conflict = commit_or_handle_stale(
        logger=logger,
        log_label="hard_delete_transfer_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transfer",
        ),
        redirect_endpoint="transfers.list_transfer_templates",
    )
    if conflict is not None:
        return conflict

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
    categories = category_service.list_active_categories(current_user.id)
    # Current + future periods power the in-popover period-move selector,
    # always including the transfer's own period so a transfer sitting in
    # a past period stays selected.  The service re-validates ownership of
    # the submitted id and moves the transfer plus both shadows together.
    periods = pay_period_service.get_current_and_future_periods(
        current_user.id, include_period_id=xfer.pay_period_id,
    )
    return render_template(
        "transfers/_transfer_full_edit.html",
        xfer=xfer, statuses=statuses, categories=categories, periods=periods,
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
    # The user-scoped FKs ``TransferUpdateSchema`` exposes are
    # ``category_id`` and ``pay_period_id``; ``status_id`` references
    # the ref table and needs no ownership probe.  Each is verified
    # only when present and non-``None`` -- ``allow_none=True`` on
    # ``category_id`` means clearing it (NULL) is legitimate and the
    # service drops it through unchanged.  The service's
    # ``_get_owned_*`` helpers re-check, but enforcing it here keeps
    # the boundary visible and guards a future refactor that bypasses
    # them.  Single-return loop so adding a future FK does not push the
    # function past pylint's too-many-returns threshold; all failures
    # collapse to 404 per the project security response rule.
    for model, field in ((Category, "category_id"), (PayPeriod, "pay_period_id")):
        value = data.get(field)
        if value is not None and not _user_owns(model, value):
            return "Not found", 404

    # A period move relocates the transfer (and both shadows) to another
    # period in the grid, which an in-place cell swap cannot express --
    # the response triggers a full grid refresh instead.  Computed before
    # the service call, while xfer.pay_period_id still holds the old value.
    period_changed = (
        "pay_period_id" in data and data["pay_period_id"] != xfer.pay_period_id
    )

    # Auto-set is_override when a template-linked transfer's amount or
    # period changes, so transfer_recurrence does not regenerate over the
    # edited instance (mirrors the transaction move in
    # transactions.update_transaction and the carry-forward transfer move).
    if xfer.transfer_template_id and ("amount" in data or "pay_period_id" in data):
        data["is_override"] = True

    error_response = _execute_transfer_update(xfer, data)
    if error_response is not None:
        return error_response

    # When opened from a shadow transaction cell in the grid, render the
    # transaction cell template so the cell remains interactive.  When
    # opened from the transfer management page, render the transfer cell.
    # A period move needs a full grid refresh so the relocated rows
    # appear under the new period; an in-place edit only needs balances
    # recomputed (gridRefresh reloads the page via app.js).  Both cell
    # paths carry the same trigger here -- it is the move, not the cell
    # kind, that decides between a refresh and a balance recompute.
    trigger = "gridRefresh" if period_changed else "balanceChanged"
    return _render_post_mutation_cell(
        xfer, shadow_trigger=trigger, cell_trigger=trigger,
    )


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
    # rows, so the ``uq_transfers_adhoc_dedupe`` IntegrityError can fire
    # either during the service call (the flush) or at the subsequent
    # ``db.session.commit()``.  A single ``try`` spans both so the
    # constraint hit is translated into idempotent success (or a 400)
    # from whichever statement trips it.  ``NotFoundError`` /
    # ``ShekelValidationError`` originate only in the service call and
    # never at commit, so folding the commit into the same ``try``
    # changes no behavior.
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
            due_date=data.get("due_date"),
        )
        db.session.commit()
    except NotFoundError:
        return "Not found", 404
    except ShekelValidationError as exc:
        return jsonify(errors={"_schema": [str(exc)]}), 400
    except IntegrityError as exc:
        return _handle_adhoc_integrity(exc, data)
    logger.info("user_id=%d created ad-hoc transfer (id=%d)", current_user.id, xfer.id)

    account = resolve_grid_account(current_user.id, current_user.settings)
    response = render_template(
        "transfers/_transfer_cell.html", xfer=xfer, account=account, wrap_div=True,
    )
    return response, 201, {"HX-Trigger": "balanceChanged"}


def _handle_adhoc_integrity(exc, data):
    """Translate an ad-hoc transfer ``IntegrityError`` into the right response.

    The ``uq_transfers_adhoc_dedupe`` partial unique index (F-050 / C-22)
    rejects a second active ad-hoc transfer with identical parameters when two
    requests race (a double-click, a network retry).  That case is treated as
    idempotent success -- the winning row's cell is returned (see
    :func:`_adhoc_dedupe_idempotent_response`).  Any other ``IntegrityError``
    is a genuine bad reference and becomes a 400.  The session is rolled back
    before the violation is inspected.

    Args:
        exc: The caught ``IntegrityError``.
        data: The loaded TransferCreateSchema payload for the failed request.

    Returns:
        The idempotent 201 cell response on a dedupe-index hit, or a 400
        ``(body, status)`` tuple otherwise.
    """
    db.session.rollback()
    if is_unique_violation(exc, _TRANSFER_ADHOC_UNIQUE_INDEX):
        return _adhoc_dedupe_idempotent_response(data)
    return "Invalid reference. Check that all referenced records exist.", 400


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
        # ``paid_at`` parity with ``transactions.mark_done``: settling
        # a transfer must record *when* it was settled.  Without this
        # kwarg the shadow transactions reach Paid with NULL
        # ``paid_at``, breaking ``Transaction.days_paid_before_due``
        # analytics, the dashboard's "paid on time" indicator, and any
        # downstream report that joins on the timestamp.  The transfer
        # service mirrors the same default (see ``update_transfer``)
        # so any future caller that forgets the kwarg still produces
        # a well-formed settled transfer.  Audit reference: F-048 /
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

    # Grid shadow context renders the transaction cell with gridRefresh;
    # the transfer-management page renders the transfer cell with
    # balanceChanged (matches the transaction route guard pattern for
    # status changes).
    return _render_post_mutation_cell(
        xfer, shadow_trigger="gridRefresh", cell_trigger="balanceChanged",
    )


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

    # Grid shadow context renders the transaction cell with gridRefresh;
    # the transfer-management page renders the transfer cell with
    # balanceChanged (matches the transaction route guard pattern for
    # status changes).
    return _render_post_mutation_cell(
        xfer, shadow_trigger="gridRefresh", cell_trigger="balanceChanged",
    )


# ── Helpers ─────────────────────────────────────────────────────────


def _materialize_initial_transfers(template, rule, start_period_id):
    """Create the initial transfer instance(s) for a freshly built template.

    A ONCE-pattern template with a selected start period produces a single
    Transfer in that period (created through ``transfer_service`` so its two
    shadow transactions are generated atomically); any other recurring rule
    is handed to the recurrence engine to fan out across every period.

    The ONCE branch re-fetches ``start_period_id`` and re-verifies ownership
    so a tampered period id cannot leak into the transfer service, mirroring
    the route-boundary FK checks in :func:`create_transfer_template`.

    Args:
        template: The persisted (flushed) TransferTemplate.
        rule: The template's RecurrenceRule.
        start_period_id: The submitted start-period id, or ``None``.

    Returns:
        A redirect ``Response`` when the one-time path hits an invalid period
        or the service rejects the transfer (the caller returns it verbatim);
        ``None`` on success so the caller proceeds to commit.
    """
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
                    # Compute the due date from the rule via the same
                    # shared helper the recurrence engine uses.  A ONCE
                    # rule carries no day_of_month, so this resolves to
                    # period.start_date -- an improvement on the prior
                    # NULL and consistent with every other transfer path.
                    due_date=_compute_due_date(rule, period),
                )
            except (NotFoundError, ShekelValidationError) as exc:
                db.session.rollback()
                flash(f"Could not create transfer: {exc}", "danger")
                return redirect(url_for("transfers.new_transfer_template"))
    elif rule:
        # Recurring transfer: delegate to the recurrence engine.
        generate_transfers_for_all_periods(template)

    return None


def _first_unowned_template_fk(data):
    """Return the label of the first submitted FK the user does not own, else None.

    Route-boundary FK ownership for the transfer-template update payload
    (commit C-27 / F-043).  Each user-scoped FK is verified only when present
    in the partial-update ``data`` (the loaded dict carries only keys the user
    submitted -- BaseSchema's EXCLUDE meta drops stray form fields).
    ``category_id`` accepts ``None`` per the schema; ``None`` clears the
    category and skips the probe.

    Args:
        data: The loaded TransferTemplateUpdateSchema output (partial update).

    Returns:
        The human-readable label ("source account", "destination account" or
        "category") of the first FK that is present, non-``None``, and not
        owned by ``current_user``; ``None`` when every present FK is owned.
    """
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
            return label
    return None


def _regenerate_and_commit_template(template, effective_from, template_id):
    """Regenerate a transfer template's future transfers, then commit.

    Re-runs ``transfer_recurrence.regenerate_for_template`` against the
    baseline scenario (auto-keeping any overridden instances via the F-26
    conflict helper), then commits.  Optimistic-lock and name-uniqueness
    failures at flush time are converted to the same flash + redirect the
    form-side guards produce, so a concurrent edit never surfaces as a 500.

    Args:
        template: The TransferTemplate whose field changes are already staged
            in the session.
        effective_from: Date from which regeneration applies.
        template_id: The template's id, used for redirect kwargs and logging.

    Returns:
        A redirect ``Response`` -- to the edit form on a stale-data or
        name-duplicate conflict, or to the template list on success.
    """
    scenario = get_baseline_scenario(current_user.id)
    if scenario and template.recurrence_rule:
        periods = pay_period_service.get_all_periods(current_user.id)
        try:
            transfer_recurrence.regenerate_for_template(
                template, periods, scenario.id, effective_from=effective_from,
            )
        except RecurrenceConflict as conflict:
            # Phase-1 auto-keep-overrides advisory.  Routed through
            # the F-26 helper; ``log_label`` carries the transfers-
            # side "Transfer recurrence conflict for template"
            # prefix verbatim so log-grep patterns stay valid.
            handle_recurrence_conflict(
                logger=logger,
                log_label="Transfer recurrence conflict for template",
                log_id=template.id,
                conflict=conflict,
            )

    try:
        db.session.commit()
    except StaleDataError:
        return handle_stale_conflict(
            logger=logger,
            log_label="update_transfer_template",
            log_id=template_id,
            flash_message=STALE_EDITING_MESSAGE.format(
                noun="recurring transfer",
            ),
            redirect_endpoint="transfers.edit_transfer_template",
            redirect_endpoint_kwargs={"template_id": template_id},
        )
    except IntegrityError:
        db.session.rollback()
        flash("A recurring transfer with that name already exists.", "warning")
        return redirect(url_for("transfers.edit_transfer_template", template_id=template_id))
    flash(f"Recurring transfer '{template.name}' updated.", "success")
    return redirect(url_for("transfers.list_transfer_templates"))


def _execute_transfer_update(xfer, data):
    """Apply an inline transfer edit via the service and commit it.

    Runs ``transfer_service.update_transfer`` (which propagates the change to
    both shadow transactions) and commits, translating each failure mode into
    the HTTP response :func:`update_transfer` would otherwise inline: a
    stale-form/flush race to a 409 conflict cell, a missing or unowned
    reference to 404, a domain validation error to a 400 JSON body, and a
    foreign-key ``IntegrityError`` to a 400.

    Args:
        xfer: The owned Transfer being edited.
        data: The loaded TransferUpdateSchema payload (``is_override`` may
            already be set by the caller for template-linked moves).

    Returns:
        ``None`` on success -- the caller renders the updated cell -- or a
        Flask response tuple describing the failure.
    """
    try:
        transfer_service.update_transfer(xfer.id, current_user.id, **data)
        db.session.commit()
    except StaleDataError:
        logger.info("Stale-data conflict on update_transfer id=%d", xfer.id)
        return _stale_transfer_response(xfer.id), 409
    except NotFoundError:
        return "Not found", 404
    except ShekelValidationError as exc:
        return jsonify(errors={"_schema": [str(exc)]}), 400
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d updated transfer %d", current_user.id, xfer.id)
    return None


def _render_post_mutation_cell(xfer, *, shadow_trigger, cell_trigger):
    """Render the grid cell for a transfer after a successful mutation.

    The transfer-mutating HTMX routes (:func:`update_transfer`,
    :func:`mark_done`, :func:`cancel_transfer`) share one response shape: when
    the request originated from a shadow transaction cell in the budget grid
    (``source_txn_id`` present and validated), the shadow's
    ``grid/_transaction_cell.html`` is re-rendered so the cell stays
    interactive; otherwise the transfer's own ``_transfer_cell.html`` is
    returned.  The two paths can carry different HX-Trigger events (a status
    change refreshes the grid but renders the transfer cell with a balance
    recompute), so each trigger is supplied explicitly.

    Args:
        xfer: The mutated Transfer.
        shadow_trigger: HX-Trigger event for the shadow-cell response.
        cell_trigger: HX-Trigger event for the transfer-cell response.

    Returns:
        A Flask response tuple ``(html, 200, {"HX-Trigger": ...})``.
    """
    shadow = _resolve_shadow_context(xfer)
    if shadow is not None:
        db.session.refresh(shadow)
        response = render_template(
            "grid/_transaction_cell.html",
            txn=shadow,
            entry_sums=build_entry_sums_dict([shadow]),
        )
        return response, 200, {"HX-Trigger": shadow_trigger}

    account = resolve_grid_account(current_user.id, current_user.settings)
    response = render_template(
        "transfers/_transfer_cell.html", xfer=xfer, account=account,
    )
    return response, 200, {"HX-Trigger": cell_trigger}


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
