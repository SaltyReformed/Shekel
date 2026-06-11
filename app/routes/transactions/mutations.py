"""
Shekel Budget App -- Transaction route package: mutation handlers.

Every state-changing transaction route on a single row: the PATCH inline
edit save, the DELETE soft/hard delete, and the status workflow
(mark-done, mark/unmark credit, cancel).  Shadow transactions
(``transfer_id IS NOT NULL``) route through the transfer service so both
shadows and the parent transfer stay in sync (design doc invariants 3-5).

The edit and status concerns share this one module deliberately: their
transfer-shadow helpers (``_apply_shadow_update`` / ``_mark_done_shadow`` /
``_cancel_shadow``) and the mark_done shadow/regular paths are near-identical
parallel code (``update_transfer`` + commit + StaleDataError preambles,
refresh/render tails, the ``_RenderTarget`` stale + IntegrityError response
handling).  Splitting them across modules would re-surface the intra-file
duplication the monolith hid (R0801 is cross-file only); co-locating the
whole mutation concern keeps that intentional parallel code in one file.
"""

import logging

from flask import request, jsonify
from flask_login import current_user, login_required
from marshmallow import ValidationError as MarshmallowValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.ref import Status
from app.services import credit_workflow, transaction_service, transfer_service
from app.services.state_machine import finalised_edit_rejection, verify_transition
from app.exceptions import NotFoundError, ValidationError
from app.utils.auth_helpers import get_accessible_transaction, require_owner
from app.utils.balance_predicates import is_credit
from app.routes.transactions._bp import transactions_bp
from app.routes.transactions._helpers import (
    _credit_payback_idempotent_response,
    _get_owned_transaction,
    _mark_done_schema,
    _mark_done_success_response,
    _render_cell,
    _RenderTarget,
    _stale_transaction_response,
    _update_schema,
    _verify_owned_fks_in_update,
)

logger = logging.getLogger(__name__)

# Money / period / category / due-date fields that the finalised-row edit
# lock (#26) protects.  Names match :class:`TransactionUpdateSchema` (the
# loaded ``data`` dict for both the regular and the transfer-shadow PATCH
# paths).  Display fields (``notes``, ``name``) and the ad-hoc visibility
# flags stay editable on a finalised row; the ``status_id`` transition is
# guarded separately by :func:`verify_transition`.
_LOCKED_EDIT_FIELDS = frozenset({
    "estimated_amount", "actual_amount", "category_id",
    "pay_period_id", "due_date",
})


def _finalised_edit_response(txn, data):
    """Reject locked-field edits on a finalised (is_immutable) transaction.

    Looks up the current and (if the PATCH transitions status) the new
    :class:`Status` BEFORE the caller's ``setattr`` loop dirties the
    session -- matching :func:`_resolve_status_change`'s autoflush-safe
    ordering -- and defers the policy decision to
    :func:`finalised_edit_rejection`.  Applies to the regular edit path
    and the transfer-shadow path (the shadow's status mirrors its
    parent transfer's, Invariant 3), the two user edit entry points;
    the system mutation paths (recurrence, carry-forward, mark-done,
    cancel) deliberately bypass this lock.

    Args:
        txn: The Transaction (or transfer shadow) being edited.
        data: The schema-loaded PATCH payload (``version_id`` already
            popped by the caller).

    Returns:
        A ``(message, 400)`` Flask response tuple when a locked field is
        edited on a finalised row not being reverted to a mutable
        status, or ``None`` when the edit may proceed.
    """
    if not _LOCKED_EDIT_FIELDS & data.keys():
        return None
    current_status = db.session.get(Status, txn.status_id)
    new_status = (
        db.session.get(Status, data["status_id"])
        if "status_id" in data else None
    )
    message = finalised_edit_rejection(
        current_status, new_status, context="transaction",
    )
    return (message, 400) if message is not None else None


def _apply_shadow_update(txn, txn_id, data):
    """Apply a PATCH update to a transfer shadow via the transfer service.

    Shadow transactions (``transfer_id IS NOT NULL``) cannot be mutated
    directly -- the parent transfer and both shadows must move together
    (design doc invariants 3-5).  Maps the submitted transaction fields
    onto :func:`transfer_service.update_transfer` kwargs, commits, and
    renders the refreshed cell.  Reverting to a non-settled status nulls
    ``paid_at`` so a re-opened shadow stops showing a paid timestamp.

    Args:
        txn: The shadow Transaction being edited.
        txn_id: The shadow's id, used for stale-conflict logging and the
            conflict re-fetch.
        data: The schema-loaded PATCH payload (``version_id`` already
            popped by the caller).

    Returns:
        A Flask response tuple: the updated cell + ``balanceChanged`` on
        success, a 409 conflict cell on a concurrent commit, or a 400
        when the transfer service rejects the change or the shadow's
        parent transfer is finalised (#26).
    """
    finalised_error = _finalised_edit_response(txn, data)
    if finalised_error is not None:
        return finalised_error

    # Map transaction field names to transfer service kwargs.
    svc_kwargs = {}
    if "estimated_amount" in data:
        svc_kwargs["amount"] = data["estimated_amount"]
    if "actual_amount" in data:
        svc_kwargs["actual_amount"] = data["actual_amount"]
    if "status_id" in data:
        svc_kwargs["status_id"] = data["status_id"]
        # Null paid_at when reverting to a non-settled status.
        new_status = db.session.get(Status, data["status_id"])
        if new_status and not new_status.is_settled:
            svc_kwargs["paid_at"] = None
    if "notes" in data:
        svc_kwargs["notes"] = data["notes"]
    if "category_id" in data:
        svc_kwargs["category_id"] = data["category_id"]
    if "due_date" in data:
        svc_kwargs["due_date"] = data["due_date"]

    try:
        transfer_service.update_transfer(
            txn.transfer_id, current_user.id, **svc_kwargs
        )
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on update_transaction shadow id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    except (NotFoundError, ValidationError) as exc:
        # transfer_service.update_transfer mutates xfer.amount and both
        # shadows' estimated_amount in-memory BEFORE running the status
        # transition through the state machine, so a rejected
        # amount+illegal-status PATCH leaves dirty mutations staged on
        # the session.  Roll back so they cannot reach the DB, matching
        # the sibling shadow handlers (_mark_done_shadow, _cancel_shadow).
        db.session.rollback()
        return str(exc), 400

    db.session.refresh(txn)
    logger.info(
        "user_id=%d updated shadow transaction %d (transfer %d)",
        current_user.id, txn_id, txn.transfer_id,
    )
    response = _render_cell(txn)
    return response, 200, {"HX-Trigger": "balanceChanged"}


def _resolve_status_change(txn, data):
    """Validate a status transition and decide whether to clear paid_at.

    Runs the status-dependent guards for a regular (non-shadow)
    :func:`update_transaction` before any column is mutated: verifies
    the requested transition through the state machine (F-161 / C-21),
    blocks the Credit status on purchase-tracking transactions (credit
    is per-entry, scope doc 5.2), and reports whether reverting to a
    non-settled status should null ``paid_at``.  Looking the status up
    here -- before the ``setattr`` loop dirties the session -- avoids an
    autoflush firing an FK violation mid-validation.

    Args:
        txn: The Transaction being edited.
        data: The schema-loaded PATCH payload.

    Returns:
        ``(revert_paid_at, None)`` when the change is allowed --
        *revert_paid_at* is ``True`` when ``paid_at`` must be cleared --
        or ``(False, (msg, 400))`` when a guard rejects the request, a
        Flask response tuple the caller returns directly.
    """
    if "status_id" not in data:
        return False, None

    # Verify the transition BEFORE any other status-dependent work
    # (envelope guard, paid_at revert).  An illegal transition -- for
    # example settled -> projected -- short-circuits the request with a
    # 400 and leaves the row untouched.  Audit reference: F-161 /
    # commit C-21 of the 2026-04-15 security remediation plan.
    try:
        verify_transition(
            txn.status_id, data["status_id"], context="transaction",
        )
    except ValidationError as exc:
        return False, (str(exc), 400)

    # Block Credit status on entry-capable transactions -- credit
    # handling is per-entry, not per-transaction (scope doc section 5.2).
    credit_id = ref_cache.status_id(StatusEnum.CREDIT)
    if data["status_id"] == credit_id and txn.tracks_purchases:
        return False, (
            "Cannot set Credit status on transactions with individual "
            "purchase tracking. Use entry-level credit instead.",
            400,
        )

    new_status = db.session.get(Status, data["status_id"])
    revert_paid_at = bool(
        new_status and not new_status.is_settled and txn.paid_at is not None
    )
    return revert_paid_at, None


def _apply_regular_update(txn, txn_id, data):
    """Apply a PATCH update to a regular (non-shadow) transaction.

    Validates any status change (:func:`_resolve_status_change`),
    enforces the expense-only purchase-tracking guard, writes the
    submitted fields, deletes the auto-generated payback when the
    change reverts a Credit row (mirroring ``unmark_credit`` via the
    shared ``credit_workflow.delete_payback_on_credit_revert``), flags
    template-generated rows as overridden when the amount or period
    changed, and commits under the optimistic lock.  A
    ``pay_period_id`` change relocates the row across the grid, so it
    triggers a full ``gridRefresh`` instead of the in-place
    ``balanceChanged`` swap.

    Args:
        txn: The Transaction being edited.
        txn_id: The transaction's id, used for stale-conflict logging.
        data: The schema-loaded PATCH payload (``version_id`` already
            popped by the caller).

    Returns:
        A Flask response tuple: the updated cell + ``gridRefresh`` (on a
        period move) or ``balanceChanged`` on success, a 409 conflict
        cell on a concurrent commit, or a 400 on a rejected status
        change, a locked-field edit of a finalised row (#26), the income
        purchase-tracking guard, or a bad FK.
    """
    revert_paid_at, status_error = _resolve_status_change(txn, data)
    if status_error is not None:
        return status_error

    # Finalised-row edit lock (#26): a Paid/Received/Settled/Credit/
    # Cancelled row's money/period/category/due-date fields cannot be
    # rewritten unless this same request reverts it to Projected.  Runs
    # after the transition guard (so an illegal status change reports its
    # own message first) and before the setattr loop.
    finalised_error = _finalised_edit_response(txn, data)
    if finalised_error is not None:
        return finalised_error

    # Purchase tracking is expense-only.  The popover only renders the
    # is_envelope checkbox for ad-hoc expense rows, but guard the route
    # too (defense in depth) so a crafted request cannot enable tracking
    # on an income transaction.  Checked against the stored type because
    # TransactionUpdateSchema carries no transaction_type_id.
    if data.get("is_envelope") and txn.is_income:
        return "Purchase tracking is only available for expenses.", 400

    # Detect a period move before the setattr loop mutates the row.  A
    # move relocates the row to a different period in the grid, which an
    # in-place cell swap (hx-target="#txn-cell-<id>") cannot express --
    # the cell would re-render in its old position.  When the period
    # actually changes the response triggers a full grid refresh (see
    # the ``HX-Trigger`` selection at the end of the handler), matching
    # the ``gridRefresh`` pattern carry-forward uses for cross-period
    # moves.
    period_changed = (
        "pay_period_id" in data and data["pay_period_id"] != txn.pay_period_id
    )

    # Detect a Credit reversion before the setattr loop rewrites
    # status_id.  A Credit row leaving Credit status (the state machine
    # only admits Credit -> Projected besides identity) must delete its
    # auto-generated payback exactly like unmark_credit -- otherwise the
    # PATCH path orphans the payback and inflates the next period's
    # projected expenses.  An identity re-submit (Credit -> Credit)
    # keeps the payback.
    reverts_credit = (
        "status_id" in data
        and is_credit(txn)
        and data["status_id"] != ref_cache.status_id(StatusEnum.CREDIT)
    )

    # Apply updates (regular transactions only).
    for field, value in data.items():
        setattr(txn, field, value)

    if revert_paid_at:
        txn.paid_at = None

    # If the user changed amount or period on a template-generated item,
    # flag as override.
    if txn.template_id and ("estimated_amount" in data or "pay_period_id" in data):
        txn.is_override = True

    try:
        if reverts_credit:
            # Inside the StaleDataError net deliberately: the payback
            # lookup autoflushes the already-dirtied row (the
            # version-pinned UPDATE), so a concurrent commit surfaces
            # here as StaleDataError and must yield the 409 conflict
            # cell, not a 500.  The helper does not commit -- the
            # deletion joins this request's commit so the status flip
            # and the payback removal land atomically.
            credit_workflow.delete_payback_on_credit_revert(
                txn, current_user.id,
            )
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on update_transaction id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d updated transaction %d", current_user.id, txn_id)

    # A period move needs a full grid refresh so the row appears under
    # its new period; an in-place edit only needs the balance rows
    # recomputed.  ``gridRefresh`` reloads the page (app.js); the
    # returned cell still swaps first, which is harmless before reload.
    response = _render_cell(txn)
    return response, 200, {
        "HX-Trigger": "gridRefresh" if period_changed else "balanceChanged",
    }


@transactions_bp.route("/transactions/<int:txn_id>", methods=["PATCH"])
@login_required
@require_owner
def update_transaction(txn_id):
    """Update a transaction's fields (inline edit save).

    Shadow transactions (transfer_id IS NOT NULL) are routed through
    the transfer service so both shadows and the parent transfer stay
    in sync (design doc invariants 3-5).

    Returns the updated cell fragment.  Sends an HX-Trigger header
    to refresh the balance row.

    Optimistic locking (commit C-18 / F-010) operates in two layers:

      1. Stale-form check: the cell ships ``version_id`` as a hidden
         input set to ``Transaction.version_id`` at render time.
         When the submitted value differs from the row's current
         counter, the handler short-circuits with a 409 + conflict
         cell partial and records nothing.  This catches the
         sequential Tab-1/Tab-2 race documented in C-17.

      2. SQLAlchemy ``version_id_col``: any concurrent flush that
         races past the stale-form check is still narrowed by
         ``WHERE version_id = ?`` at the database tier; the loser
         raises ``StaleDataError`` which the handler converts into
         the same 409 + conflict cell.  The two layers together
         close every interleaving the optimistic-lock contract is
         meant to cover.

    Route-boundary FK ownership (commit C-29 / F-029 of the
    2026-04-15 security remediation plan): when the schema accepts
    a user-scoped FK -- ``pay_period_id`` or ``category_id`` -- the
    submitted id is verified against ``current_user.id`` here,
    before any state-changing work runs.  Without this probe an
    authenticated owner could submit another user's
    ``pay_period_id`` or ``category_id`` and the unfiltered
    ``setattr`` loop would silently re-parent the transaction into
    the victim's namespace (the FK row exists, the FK constraint
    passes, and PostgreSQL never raises ``IntegrityError``).
    ``status_id`` is a reference table FK (not user-scoped) and so
    does not need an ownership check.  The probe runs before the
    transfer-shadow branch so a malicious request that targets a
    transfer shadow with a cross-user FK is rejected even though
    the transfer-shadow path drops ``pay_period_id`` silently --
    matching the layered defense ``transfers.update_transfer``
    received in commit C-27.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # Parse and validate input.
    errors = _update_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 422

    data = _update_schema.load(request.form)

    # Route-boundary FK ownership (commit C-29 / F-029).  Reject
    # cross-user ``pay_period_id`` / ``category_id`` before the
    # stale-form check or the transfer-shadow branch so the
    # security response (404) takes precedence over the UX
    # response (409 conflict cell) when the same request triggers
    # both.  See :func:`_verify_owned_fks_in_update` for the
    # threat-model details.
    fk_error = _verify_owned_fks_in_update(data)
    if fk_error is not None:
        return fk_error

    # Stale-form check.  Performed before any mutation so audit-log
    # triggers record only successful edits.  Conditional on the
    # form having submitted a version (clients that omit it fall
    # through to the SQLAlchemy-tier check at flush time).
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != txn.version_id:
        logger.info(
            "Stale-form conflict on update_transaction id=%d "
            "(submitted=%d, current=%d)",
            txn_id, submitted_version, txn.version_id,
        )
        return _render_cell(txn, conflict=True), 409

    # --- Transfer detection guard ---
    if txn.transfer_id is not None:
        return _apply_shadow_update(txn, txn_id, data)
    # --- End guard ---

    return _apply_regular_update(txn, txn_id, data)


@transactions_bp.route("/transactions/<int:txn_id>", methods=["DELETE"])
@login_required
@require_owner
def delete_transaction(txn_id):
    """Soft-delete a transaction (or hard-delete if it's ad-hoc).

    Shadow transactions cannot be directly deleted -- the user must
    delete the parent transfer instead.

    A source with a live CC payback (transaction-level Credit or
    entry-level credit) takes the payback down with it in the same
    commit via ``credit_workflow.delete_payback_on_source_delete`` --
    otherwise the ``SET NULL`` FK leaves the payback inflating the
    next period with no offsetting credit row.

    Optimistic locking (commit C-18 / F-010): both the soft-delete
    UPDATE and the hard-delete DELETE are version-pinned by
    SQLAlchemy.  A concurrent commit that bumps the row's version
    raises ``StaleDataError`` which the handler converts to a 409 +
    conflict cell so the user can retry against fresh state.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # --- Transfer detection guard: block direct shadow deletion ---
    if txn.transfer_id is not None:
        return "Cannot delete a transfer shadow directly. Delete the parent transfer instead.", 400

    # Delete the live payback (if any) before the source goes.  Runs
    # for both branches below: a hard-deleted ad-hoc source would
    # otherwise leave the payback with its link NULLed, a soft-deleted
    # template row would leave it linked to an invisible source.
    credit_workflow.delete_payback_on_source_delete(txn, current_user.id)

    if txn.template_id:
        # Template-linked: soft-delete so the recurrence engine knows.
        txn.is_deleted = True
    else:
        # Ad-hoc: hard delete.
        db.session.delete(txn)

    try:
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on delete_transaction id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    logger.info("user_id=%d deleted transaction %d", current_user.id, txn_id)
    return "", 200, {"HX-Trigger": "balanceChanged"}


def _mark_done_shadow(txn, txn_id, actual_amount, target):
    """Settle a transfer shadow through the transfer service.

    Marks both shadows and the parent transfer done atomically (the
    transfer service owns the shadow invariants).  Uses the DONE status
    for the service -- the 'done'/'received' split is a display
    convention for regular transactions only -- and forwards an optional
    manual ``actual_amount``.

    Args:
        txn: The shadow Transaction being settled.
        txn_id: The shadow's id, for stale-conflict logging / re-fetch.
        actual_amount: Optional manual actual amount from the form, or
            ``None`` to leave it to the service.
        target: The :class:`_RenderTarget` describing the response
            surface (mobile card vs desktop cell).

    Returns:
        A Flask response tuple: the refreshed cell + ``gridRefresh`` on
        success, a 409 conflict surface on a concurrent commit, or a 400
        on a bad FK or a state-machine rejection.
    """
    # Use 'done' for the transfer service -- it sets the same status on
    # both shadows.  The 'done'/'received' distinction is a display
    # convention for regular transactions.
    svc_kwargs = {
        "status_id": ref_cache.status_id(StatusEnum.DONE),
        "paid_at": db.func.now(),
    }

    if actual_amount is not None:
        svc_kwargs["actual_amount"] = actual_amount

    try:
        transfer_service.update_transfer(
            txn.transfer_id, current_user.id, **svc_kwargs
        )
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on mark_done shadow id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id, target)
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    except ValidationError as exc:
        # transfer_service.update_transfer runs the transition through
        # the state machine (commit C-21).  A mark-done request against
        # a Cancelled or Settled transfer shadow surfaces here as 400
        # instead of crashing the request.
        db.session.rollback()
        return str(exc), 400
    db.session.refresh(txn)
    response = _render_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


def _mark_done_regular(txn, txn_id, status_id, actual_amount, target):
    """Settle a regular (non-shadow) transaction.

    Envelope-tracked rows with entries settle at the entry sum via
    :func:`transaction_service.settle_from_entries` (the single source
    of truth shared with carry-forward); all others take the manual flow
    -- a state-machine-guarded status flip to *status_id* plus an
    optional manual ``actual_amount`` -- then commit under the
    optimistic lock.

    Args:
        txn: The Transaction being settled.
        txn_id: The transaction's id, for stale-conflict logging.
        status_id: The settled status id ('received' for income, 'done'
            for expenses) used by the manual branch.
        actual_amount: Optional manual actual amount from the form, or
            ``None`` to leave ``actual_amount`` untouched.
        target: The :class:`_RenderTarget` describing the response
            surface (mobile card vs desktop cell).

    Returns:
        A Flask response tuple: the success surface on commit, a 409
        conflict surface on a concurrent commit, or a 400 on a bad FK or
        a rejected transition.
    """
    # Auto-populate actual from entries for envelope-tracked transactions
    # with at least one entry.  Entry sum takes precedence over any manual
    # actual_amount from the form (scope doc section 4.2).  When no entries
    # exist (or the template is not envelope-tracked), fall through to the
    # manual flow so non-tracked and empty-tracked transactions behave
    # identically to pre-entry behavior -- the form's optional
    # ``actual_amount`` is honoured and a missing value leaves
    # ``txn.actual_amount`` untouched.
    #
    # The envelope-with-entries branch routes through
    # ``transaction_service.settle_from_entries`` so the manual mark-done
    # path and the carry-forward envelope branch (Phase 4) share a single
    # source of truth for "settle a tracked row at sum(entries)."  The
    # helper writes ``status_id``, ``paid_at``, and ``actual_amount``
    # together; the route does not need to set them itself in this branch.
    if txn.tracks_purchases and txn.entries:
        try:
            transaction_service.settle_from_entries(txn)
        except ValidationError as exc:
            return str(exc), 400
    else:
        # State-machine guard: only Projected (or the identity edge from
        # Paid/Received) can transition into Paid/Received via mark_done.
        # The envelope branch above already enforces the same rule via
        # ``settle_from_entries``'s stricter ``is_immutable`` precondition,
        # so the guard sits on the direct branch where the gap was.
        # Audit reference: F-047 / F-161 follow-up to commit C-21.
        try:
            verify_transition(txn.status_id, status_id, context="transaction")
        except ValidationError as exc:
            return str(exc), 400
        txn.status_id = status_id
        txn.paid_at = db.func.now()
        # Accept an optional manual actual amount from the form.
        if actual_amount is not None:
            txn.actual_amount = actual_amount

    try:
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on mark_done id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id, target)
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info(
        "user_id=%d marked transaction %d status_id=%d", current_user.id, txn_id, status_id
    )

    return _mark_done_success_response(txn, target)


@transactions_bp.route("/transactions/<int:txn_id>/mark-done", methods=["POST"])
@login_required
def mark_done(txn_id):
    """Set a transaction's status to 'done' (expenses) or 'received' (income).

    Shadow transactions route through the transfer service so both
    shadows and the parent transfer are updated atomically.

    Automatically picks the correct status based on transaction type.
    For entry-capable transactions with entries, auto-computes
    actual_amount from the entry sum.  For all others, accepts an
    optional actual_amount from the form -- parsed via
    :class:`MarkDoneSchema` so a malformed numeric value returns a
    clean 422 with the Marshmallow per-field message instead of the
    legacy ``"Invalid actual amount"`` translation, and a negative
    value is rejected at the schema tier (commit C-27 / F-042 /
    F-162 of the 2026-04-15 security remediation plan).  422 (not 400)
    is the validation-error status the entry routes and
    ``coding-standards.md`` mandate (DH-#81).

    Optimistic locking (commit C-18 / F-010): the button-click path
    has no form-side ``version_id`` to compare, so the optimistic
    lock relies on SQLAlchemy's ``version_id_col`` race detection
    at flush time.  ``StaleDataError`` is converted to a 409 +
    conflict cell so the user retries against fresh state instead
    of seeing a 500.
    """
    txn = get_accessible_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # Validate the optional ``actual_amount`` form field once,
    # before branching on transfer detection, so both code paths
    # apply identical validation.  ``MarkDoneSchema`` strips empty
    # strings via its pre_load hook so the missing-field UX (a
    # button click with no body) yields ``actual_amount`` absent
    # from the loaded dict; that branches into "leave the column
    # untouched" below, matching the legacy behaviour.
    try:
        mark_done_data = _mark_done_schema.load(request.form)
    except MarshmallowValidationError as exc:
        return jsonify(errors=exc.messages), 422
    actual_amount = mark_done_data.get("actual_amount")

    # Rendering surface for the response.  The mobile / companion card
    # action bar posts ``render=mobile_card`` plus the per-tab
    # ``card_prefix`` and the ``can_edit`` flag so the response is a
    # single re-rendered card (in-place swap, no reload); the desktop
    # grid and full-edit popover omit these, so the response defaults
    # to the cell + gridRefresh reload.  Read off ``request.form``
    # directly -- these are render-routing fields, not part of the
    # money-only ``MarkDoneSchema``.
    render_mode = request.form.get("render", "")
    card_prefix = request.form.get("card_prefix", "")
    card_can_edit = request.form.get("can_edit") == "1"
    target = _RenderTarget(render_mode, card_prefix, card_can_edit)

    # Income uses 'received', expenses use 'done'.
    if txn.is_income:
        status_id = ref_cache.status_id(StatusEnum.RECEIVED)
    else:
        status_id = ref_cache.status_id(StatusEnum.DONE)

    # --- Transfer detection guard ---
    if txn.transfer_id is not None:
        return _mark_done_shadow(txn, txn_id, actual_amount, target)
    # --- End guard ---

    return _mark_done_regular(txn, txn_id, status_id, actual_amount, target)


@transactions_bp.route("/transactions/<int:txn_id>/mark-credit", methods=["POST"])
@login_required
@require_owner
def mark_credit(txn_id):
    """Mark a transaction as 'credit' and auto-generate a payback expense.

    Optimistic locking (commit C-18 / F-010):
    ``StaleDataError`` -> 409 conflict cell.

    TOCTOU duplicate-payback prevention (commit C-19 / F-008):
    ``credit_workflow.mark_as_credit`` acquires
    ``SELECT ... FOR NO KEY UPDATE`` on the source row to serialise
    concurrent requests; the partial unique index
    ``uq_transactions_credit_payback_unique`` backstops any future
    caller that bypasses the lock, and the IntegrityError catch
    below converts the violation into idempotent success.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # --- Transfer detection guard: credit is not applicable to transfers ---
    if txn.transfer_id is not None:
        return "Cannot mark a transfer shadow as credit.", 400

    try:
        credit_workflow.mark_as_credit(txn_id, current_user.id)
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on mark_credit id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    except IntegrityError as exc:
        # Defensive backstop for commit C-19 -- see
        # ``_credit_payback_idempotent_response`` docstring.
        return _credit_payback_idempotent_response(exc, txn_id)
    except (NotFoundError, ValidationError) as exc:
        return str(exc), 400
    response = _render_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/<int:txn_id>/unmark-credit", methods=["DELETE"])
@login_required
@require_owner
def unmark_credit(txn_id):
    """Revert credit status and delete the auto-generated payback.

    Optimistic locking: see :func:`mark_credit`.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # --- Transfer detection guard: credit is not applicable to transfers ---
    if txn.transfer_id is not None:
        return "Cannot unmark credit on a transfer shadow.", 400

    try:
        credit_workflow.unmark_credit(txn_id, current_user.id)
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on unmark_credit id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    except NotFoundError as exc:
        return str(exc), 404
    except ValidationError as exc:
        # Raised when the bespoke source-state guard or the
        # state-machine verification in
        # ``credit_workflow.unmark_credit`` rejects the request --
        # e.g. attempting to unmark a Paid row.  The body names the
        # offending status so the user understands why.
        db.session.rollback()
        return str(exc), 400
    response = _render_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


def _cancel_shadow(txn, txn_id, cancelled_id):
    """Cancel a transfer shadow through the transfer service.

    Cancels the parent transfer and both shadows atomically (the
    transfer service owns the shadow invariants); the route never
    mutates a shadow directly.

    Args:
        txn: The shadow Transaction being cancelled.
        txn_id: The shadow's id, for stale-conflict logging.
        cancelled_id: The Cancelled status id.

    Returns:
        A Flask response tuple: the refreshed cell + ``gridRefresh`` on
        success, a 409 conflict cell on a concurrent commit, or a 400
        when the state machine rejects cancelling a settled transfer.
    """
    try:
        transfer_service.update_transfer(
            txn.transfer_id, current_user.id,
            status_id=cancelled_id,
        )
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on cancel_transaction shadow id=%d",
            txn_id,
        )
        return _stale_transaction_response(txn_id)
    except ValidationError as exc:
        # transfer_service runs the transition through the state
        # machine.  An attempt to cancel a Paid/Received/Settled
        # transfer surfaces here as 400 instead of crashing the request
        # -- the transfer-service path was already wired by commit C-21;
        # this except clause is the route's corresponding translation.
        db.session.rollback()
        return str(exc), 400
    db.session.refresh(txn)
    response = _render_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/<int:txn_id>/cancel", methods=["POST"])
@login_required
@require_owner
def cancel_transaction(txn_id):
    """Set a transaction's status to 'cancelled'.

    Shadow transactions route through the transfer service to cancel
    the parent transfer and both shadows atomically.

    Optimistic locking: see :func:`mark_credit`.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)

    # --- Transfer detection guard ---
    if txn.transfer_id is not None:
        return _cancel_shadow(txn, txn_id, cancelled_id)
    # --- End guard ---

    # State-machine guard: Cancelled is reachable only from Projected
    # (or the Cancelled identity edge for idempotent re-submits).  A
    # direct done -> cancelled or settled -> cancelled would erase the
    # paid/archived audit trail and is rejected with 400.  Audit
    # reference: F-047 / F-161 follow-up to commit C-21.
    try:
        verify_transition(txn.status_id, cancelled_id, context="transaction")
    except ValidationError as exc:
        return str(exc), 400

    txn.status_id = cancelled_id

    try:
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on cancel_transaction id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    logger.info("user_id=%d cancelled transaction %d", current_user.id, txn_id)

    response = _render_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}
