"""
Shekel Budget App -- Transfer route package: instance mutations.

Every state-changing route on a single transfer instance: the inline amount
edit (PATCH), the ad-hoc create (POST), the soft/hard delete (DELETE), and the
mark-done / cancel status actions.  The edit and status concerns share this one
module deliberately -- their service-update + commit + stale/cell response code
is near-identical parallel code; co-locating it keeps that intentional
parallelism intra-file (R0801 is cross-file only) rather than re-surfacing the
duplication the monolith hid.  Every URL and endpoint name is preserved
verbatim from the pre-split ``app/routes/transfers.py``.
"""

import logging

from flask import jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.transfer import Transfer
from app import ref_cache
from app.enums import StatusEnum
from app.services import transfer_service
from app.services.account_resolver import resolve_grid_account
from app.exceptions import NotFoundError, ValidationError as ShekelValidationError
from app.utils.auth_helpers import require_owner
from app.utils.db_errors import is_unique_violation
from app.routes.transfers._bp import transfers_bp
from app.routes.transfers._helpers import (
    _get_owned_transfer,
    _render_post_mutation_cell,
    _stale_transfer_response,
    _user_owns,
    _xfer_create_schema,
    _xfer_update_schema,
)

logger = logging.getLogger(__name__)

# Name of the partial unique index that backstops the ad-hoc transfer
# double-submit fix (F-050 / C-22).  Mirrors the literal in
# ``app/models/transfer.py:Transfer.__table_args__`` and
# ``migrations/versions/<C-22 revision>.py``; renaming the index
# requires a coordinated edit across all three sites.
_TRANSFER_ADHOC_UNIQUE_INDEX = "uq_transfers_adhoc_dedupe"


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
