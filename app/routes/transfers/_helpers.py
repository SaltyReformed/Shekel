"""
Shekel Budget App -- Transfer route package: shared helpers.

The Marshmallow schema singletons and the private ownership / cell-render
helpers shared across the transfer route sub-modules.  Schema instances are
constructed once at import time so every handler reuses the same instance
(Marshmallow contract), preserving the pre-split monolith's behaviour.
"""

import logging

from flask import render_template, request
from flask_login import current_user

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.schemas.validation import (
    TransferTemplateCreateSchema,
    TransferTemplateUpdateSchema,
    TransferCreateSchema,
    TransferUpdateSchema,
)
from app.services.account_resolver import resolve_grid_account
from app.services.entry_service import build_entry_sums_dict

logger = logging.getLogger(__name__)

# Marshmallow schema instances (one per process; Marshmallow contract).
_create_schema = TransferTemplateCreateSchema()
_update_schema = TransferTemplateUpdateSchema()
_xfer_create_schema = TransferCreateSchema()
_xfer_update_schema = TransferUpdateSchema()


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
