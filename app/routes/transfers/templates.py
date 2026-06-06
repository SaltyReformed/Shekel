"""
Shekel Budget App -- Transfer route package: template management.

CRUD for recurring transfer templates: list, create, edit, update, archive,
unarchive, and hard-delete, plus the one-time/recurring instance
materialization and regenerate-and-commit helpers.  Every URL and endpoint
name is preserved verbatim from the pre-split ``app/routes/transfers.py``.
"""

import logging
from datetime import date

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.utils.auth_helpers import fresh_login_required, get_or_404, require_owner
from app.extensions import db
from app.models.category import Category
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.models.pay_period import PayPeriod
from app.models.account import Account
from app.models.ref import RecurrencePattern, Status
from app import ref_cache
from app.enums import RecurrencePatternEnum, StatusEnum
from app.utils import archive_helpers
from app.services import (
    account_service,
    category_service,
    pay_period_service,
    transfer_recurrence,
    transfer_service,
)
from app.services.recurrence_engine import _compute_due_date
from app.services.scenario_resolver import get_baseline_scenario
from app.exceptions import (
    NotFoundError,
    RecurrenceConflict,
    ValidationError as ShekelValidationError,
)
from app.utils.balance_predicates import is_projected_clause
from app.routes._commit_helpers import (
    StaleConflictContext,
    commit_or_handle_stale,
    handle_stale_conflict,
)
from app.routes._recurrence_form_helpers import (
    STALE_ACTION_MESSAGE,
    STALE_EDITING_MESSAGE,
    RecurrenceFormContext,
    build_recurrence_rule_from_form,
    handle_recurrence_conflict,
    handle_stale_form_conflict,
    resolve_recurrence_rule_for_update,
)
from app.routes._redirect_target import RedirectTarget
from app.routes._transfer_creation_helpers import (
    flush_template_or_namedup_redirect,
    generate_transfers_for_all_periods,
)
from app.routes.transfers._bp import transfers_bp
from app.routes.transfers._helpers import (
    _create_schema,
    _update_schema,
    _user_owns,
)

logger = logging.getLogger(__name__)

# Field allowlist for the transfer-template update route: which submitted
# form fields may be written back to the template via setattr.
_TEMPLATE_UPDATE_FIELDS = {
    "name", "default_amount", "from_account_id", "to_account_id",
    "category_id", "is_active", "sort_order",
}


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
        ctx=RecurrenceFormContext(
            end_date_value=end_date,
            redirect=RedirectTarget("transfers.new_transfer_template"),
            include_due_day_of_month=False,
        ),
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
        redirect=RedirectTarget("transfers.list_transfer_templates"),
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
            StaleConflictContext(
                logger=logger,
                log_label="update_transfer_template",
                log_id=template_id,
                flash_message=STALE_EDITING_MESSAGE.format(
                    noun="recurring transfer",
                ),
                redirect=RedirectTarget(
                    "transfers.edit_transfer_template",
                    {"template_id": template_id},
                ),
            ),
            submitted=submitted_version,
            current=template.version_id,
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
        ctx=RecurrenceFormContext(
            end_date_value=end_date,
            redirect=RedirectTarget(
                "transfers.edit_transfer_template",
                {"template_id": template_id},
            ),
            include_due_day_of_month=False,
        ),
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
        redirect=RedirectTarget(
            "transfers.edit_transfer_template",
            {"template_id": template_id},
        ),
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

    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="archive_transfer_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transfer",
        ),
        redirect=RedirectTarget("transfers.list_transfer_templates"),
    ))
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

    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="unarchive_transfer_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transfer",
        ),
        redirect=RedirectTarget("transfers.list_transfer_templates"),
    ))
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
            conflict = commit_or_handle_stale(StaleConflictContext(
                logger=logger,
                log_label="hard_delete_transfer_template archive-fallback",
                log_id=template_id,
                flash_message=STALE_ACTION_MESSAGE.format(
                    noun="recurring transfer",
                ),
                redirect=RedirectTarget("transfers.list_transfer_templates"),
            ))
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
    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="hard_delete_transfer_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transfer",
        ),
        redirect=RedirectTarget("transfers.list_transfer_templates"),
    ))
    if conflict is not None:
        return conflict

    flash(f"Recurring transfer '{template_name}' permanently deleted.", "info")
    return redirect(url_for("transfers.list_transfer_templates"))


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
        return handle_stale_conflict(StaleConflictContext(
            logger=logger,
            log_label="update_transfer_template",
            log_id=template_id,
            flash_message=STALE_EDITING_MESSAGE.format(
                noun="recurring transfer",
            ),
            redirect=RedirectTarget(
                "transfers.edit_transfer_template",
                {"template_id": template_id},
            ),
        ))
    except IntegrityError:
        db.session.rollback()
        flash("A recurring transfer with that name already exists.", "warning")
        return redirect(url_for("transfers.edit_transfer_template", template_id=template_id))
    flash(f"Recurring transfer '{template.name}' updated.", "success")
    return redirect(url_for("transfers.list_transfer_templates"))
