"""
Shekel Budget App -- Transaction Entry Routes

CRUD operations for individual purchase entries on entry-capable
transactions.  Returns HTMX fragments for inline management in
the transaction detail popover and the companion view.
"""

import logging
from datetime import date

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app import ref_cache
from app.enums import RoleEnum
from app.schemas.validation import EntryCreateSchema, EntryUpdateSchema
from app.services import entry_service
from app.exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)

entries_bp = Blueprint("entries", __name__)

# Marshmallow schema instances -- reused across requests.
_create_schema = EntryCreateSchema()
_update_schema = EntryUpdateSchema()


def _get_accessible_transaction(txn_id):
    """Fetch a transaction accessible to the current user.

    Owners access transactions belonging to their own pay periods.
    Companions access transactions belonging to their linked owner's
    pay periods, restricted to templates flagged companion_visible.

    Follows the security response rule: returns None for both
    "not found" and "not yours" so the caller returns 404 in
    either case.

    Args:
        txn_id: Integer primary key of the transaction.

    Returns:
        Transaction if found and accessible, else None.
    """
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        return None
    companion_role_id = ref_cache.role_id(RoleEnum.COMPANION)
    if current_user.role_id == companion_role_id:
        # Companion path: must be linked owner's data + visible template.
        if (txn.pay_period.user_id != current_user.linked_owner_id
                or txn.template is None
                or not txn.template.companion_visible):
            return None
    else:
        # Owner path: standard pay-period ownership check.
        if txn.pay_period.user_id != current_user.id:
            return None
    return txn


def _render_entry_list(txn, editing_id=None):
    """Render the entry list partial for a transaction.

    Loads entries, computes remaining balance, and checks for
    out-of-period dates (OP-4 date awareness).

    Args:
        txn: The parent Transaction object.
        editing_id: Optional entry ID currently being edited.
            When set, the template shows an inline edit form
            for that entry instead of the display row.

    Returns:
        Rendered HTML string.
    """
    entries = entry_service.get_entries_for_transaction(
        txn.id, current_user.id,
    )
    remaining = entry_service.compute_remaining(
        txn.estimated_amount, entries,
    )
    out_of_period_ids = {
        e.id for e in entries
        if not entry_service.check_entry_date_in_period(e.entry_date, txn)
    }
    return render_template(
        "grid/_transaction_entries.html",
        txn=txn,
        entries=entries,
        remaining=remaining,
        today=date.today().isoformat(),
        editing_id=editing_id,
        out_of_period_ids=out_of_period_ids,
    )


@entries_bp.route("/transactions/<int:txn_id>/entries", methods=["GET"])
@login_required
def list_entries(txn_id):
    """HTMX partial: return the entry list for a transaction.

    Accepts an optional ``editing`` query parameter (entry ID) to
    render an inline edit form for the specified entry.
    """
    txn = _get_accessible_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    editing_id = request.args.get("editing", type=int)
    return _render_entry_list(txn, editing_id=editing_id)


@entries_bp.route("/transactions/<int:txn_id>/entries", methods=["POST"])
@login_required
def create_entry(txn_id):
    """Create a new entry and return the updated entry list.

    Validates input via EntryCreateSchema, delegates to
    entry_service.create_entry (which syncs CC payback and
    updates actual_amount if Paid), then commits atomically.
    Returns the refreshed entry list with a balanceChanged trigger.
    """
    txn = _get_accessible_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    errors = _create_schema.validate(request.form)
    if errors:
        return str(errors), 422

    data = _create_schema.load(request.form)
    try:
        entry_service.create_entry(
            transaction_id=txn.id,
            user_id=current_user.id,
            **data,
        )
        db.session.commit()
    except (NotFoundError, ValidationError) as exc:
        db.session.rollback()
        return str(exc), 400

    response = _render_entry_list(txn)
    return response, 200, {"HX-Trigger": "balanceChanged"}


@entries_bp.route(
    "/transactions/<int:txn_id>/entries/<int:entry_id>",
    methods=["PATCH"],
)
@login_required
def update_entry(txn_id, entry_id):
    """Update an entry and return the updated entry list.

    Verifies the entry belongs to the specified transaction before
    calling the service, preventing parameter confusion attacks
    where a companion could modify entries on non-visible
    transactions by using a visible transaction's URL with a
    different entry ID.
    """
    txn = _get_accessible_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # Guard: entry must belong to this transaction.
    entry = db.session.get(TransactionEntry, entry_id)
    if entry is None or entry.transaction_id != txn.id:
        return "Not found", 404

    errors = _update_schema.validate(request.form)
    if errors:
        return str(errors), 422

    data = _update_schema.load(request.form)
    try:
        entry_service.update_entry(entry_id, current_user.id, **data)
        db.session.commit()
    except (NotFoundError, ValidationError) as exc:
        db.session.rollback()
        return str(exc), 400

    response = _render_entry_list(txn)
    return response, 200, {"HX-Trigger": "balanceChanged"}


@entries_bp.route(
    "/transactions/<int:txn_id>/entries/<int:entry_id>",
    methods=["DELETE"],
)
@login_required
def delete_entry(txn_id, entry_id):
    """Delete an entry and return the updated entry list.

    Same parameter confusion guard as update_entry: verifies the
    entry belongs to the specified transaction.
    """
    txn = _get_accessible_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # Guard: entry must belong to this transaction.
    entry = db.session.get(TransactionEntry, entry_id)
    if entry is None or entry.transaction_id != txn.id:
        return "Not found", 404

    try:
        entry_service.delete_entry(entry_id, current_user.id)
        db.session.commit()
    except NotFoundError as exc:
        db.session.rollback()
        return str(exc), 404

    response = _render_entry_list(txn)
    return response, 200, {"HX-Trigger": "balanceChanged"}
