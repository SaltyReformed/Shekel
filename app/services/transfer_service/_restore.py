"""
Shekel Budget App -- Transfer Service: the RESTORE verb

The inverse of a soft delete, and the one place the five invariants are
REPAIRED rather than merely maintained: a transfer that spent time
soft-deleted may have had a shadow drift out from under it, so every mirrored
field is re-synced from the canonical parent on the way back.

It refuses before it moves (:func:`assert_restorable`, ruling **R-DR**), so a
restore onto an archived account leaves the transfer untouched rather than
half-restored.

Flask-isolated like the rest of the package: plain data in, ORM rows out, no
``request`` / ``session`` imports.  Flushes; does NOT commit.
"""

import logging

from app.extensions import db
from app.models.transaction import Transaction
from app.services import posting_service
from app.services.transfer_service._loan_posting import (
    _sync_loan_postings_if_loan,
)
from app.services.transfer_service._validation import (
    _get_transfer_or_raise,
    assert_restorable,
)
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_RESTORED,
    log_event,
)

logger = logging.getLogger(__name__)


def restore_transfer(transfer_id, user_id):
    """Restore a soft-deleted transfer and its shadow transactions.

    This is the inverse of ``delete_transfer(soft=True)``.  Sets
    ``is_deleted=False`` on the transfer and both shadows, then
    re-syncs every field the service still mirrors from the canonical
    parent onto both shadows (period, category, due_date, is_override) in
    case any drifted via direct ORM mutation while the transfer was
    soft-deleted.

    **It repairs no STATUS and no day, since plan step
    ``balance:X-bi-6-4d-2``.**  A shadow's status and day are no longer kept
    or read: the transfer's status is one column, and each side's day is its
    payment record's.  Nor is there a record to bring back: the soft delete
    took each side's records off the books (ruling **R-CC75**, "un-archiving
    brings a deleted occurrence back empty"), and the database refuses to
    hide a transfer still holding one.  **What comes back is a PLAN**: the
    delete set a settled transfer whose records it took off back to
    Projected (ruling **R-BAL246**, "Back as a plan, now"), so a loan reads
    a planned payment rather than a missed installment; a ``$0.00`` close
    held nothing and comes back as it was.  Until that step this verb re-applied
    the pair's status through the seam, repairing a drifted shadow's status
    and borrowing its day from the sibling (ruling **R-BAL142**).

    Idempotent: calling on an already-active transfer is a no-op.

    Args:
        transfer_id: The primary key of the transfer to restore.
        user_id:     The expected owner (defense-in-depth).

    Returns:
        The restored (or already-active) Transfer object.

    Raises:
        NotFoundError: If the transfer does not exist or does not
            belong to user_id.
        ValidationError: If either the source or destination account has
            been archived (``is_active = False``) since the transfer was
            soft-deleted (F-164).  Reactivate the account before restoring.
    """
    # Must allow deleted transfers since that is the expected input.
    xfer = _get_transfer_or_raise(transfer_id, user_id, allow_deleted=True)

    # Idempotent: if the transfer is already active, return unchanged.
    # Matches the idempotent pattern of delete_transfer(soft=True).
    if not xfer.is_deleted:
        logger.debug(
            "restore_transfer called on active transfer %d; no-op.",
            transfer_id,
        )
        return xfer

    # Load ALL shadows without filtering by is_deleted -- they are
    # soft-deleted and that is exactly what we are undoing.  Same
    # query pattern as delete_transfer(soft=True).
    shadows = (
        db.session.query(Transaction)
        .filter_by(transfer_id=transfer_id)
        .all()
    )

    # ── Refuse before anything moves (X-aj1) ────────────────────────
    # Archived endpoints (F-164); the twin count and type pairing went with
    # the twins at plan step ``balance:X-bi-6-4d-3``.  Run BEFORE
    # the un-delete, which is a change from the code this replaced:
    # that version flipped ``is_deleted`` first and then hand-restored it on
    # each failing branch, so the rollback was written out three times and the
    # fourth check would have had to remember it too.
    assert_restorable(xfer, user_id)

    xfer.is_deleted = False

    # ── Restore shadows and verify invariants ───────────────────────
    for shadow in shadows:
        shadow.is_deleted = False

        # Invariant 3 has NO repair here, and its absence is the invariant
        # becoming structural (plan step X-au-g-2c-2, ruling **R-FI**).  This
        # block logged and rewrote a shadow whose ``estimated_amount`` had
        # drifted from ``xfer.amount`` -- a second maintainer of a copied
        # value, which is the shape this arc exists to delete.  A shadow stores
        # no figure at all now: it DECLARES ``PARENT_TRANSFER`` and reads its
        # parent through the amount model, so there is nothing left that can
        # drift.  The one shape that legitimately differs -- a pair whose
        # figure a human authored -- is written to all three rows in one act
        # (``_update._apply_amount``), so it is equal by construction too.
        # Measured before the copy was deleted: 0 of 350 production shadows
        # differed from their parent (2026-09-01, stamp ``a4c6f1d92b73``), so
        # this repair had nothing to repair on the live data either.

        # Invariant 4 (the status) is no longer a shadow's to carry: see the
        # docstring.

        # Invariant 5: shadow period must match transfer period.
        if shadow.pay_period_id != xfer.pay_period_id:
            logger.warning(
                "Correcting shadow %d pay_period_id drift: %s -> %s "
                "(transfer %d period).",
                shadow.id, shadow.pay_period_id, xfer.pay_period_id,
                transfer_id,
            )
            shadow.pay_period_id = xfer.pay_period_id

        # Mirrored field: shadow category must match transfer category.
        # create_transfer/_build_shadow and update_transfer mirror the
        # parent category to both shadows so each account grid attributes
        # the entry to the same user-selected category; a drifted shadow
        # would surface under the wrong category in one grid.
        if shadow.category_id != xfer.category_id:
            logger.warning(
                "Correcting shadow %d category_id drift: %s -> %s "
                "(transfer %d category).",
                shadow.id, shadow.category_id, xfer.category_id,
                transfer_id,
            )
            shadow.category_id = xfer.category_id

        # Mirrored field: shadow due_date must match transfer due_date.
        # The parent is canonical (see ``models/transfer.py`` due_date
        # docstring, "Transfer Invariant 3"); the calendar, dashboard,
        # year-end and spending-trend consumers read the SHADOW due_date,
        # so a drifted shadow would mis-compute days-until-due / paid-on-
        # time while the parent still shows the correct date.
        if shadow.due_date != xfer.due_date:
            logger.warning(
                "Correcting shadow %d due_date drift: %s -> %s "
                "(transfer %d due_date).",
                shadow.id, shadow.due_date, xfer.due_date,
                transfer_id,
            )
            shadow.due_date = xfer.due_date

        # Mirrored field: shadow is_override must match transfer
        # is_override.  update_transfer mirrors the override flag to both
        # shadows so the carry-forward/dedupe state stays coherent across
        # the three rows; a drifted shadow would diverge from the parent's
        # override status.
        if shadow.is_override != xfer.is_override:
            logger.warning(
                "Correcting shadow %d is_override drift: %s -> %s "
                "(transfer %d is_override).",
                shadow.id, shadow.is_override, xfer.is_override,
                transfer_id,
            )
            shadow.is_override = xfer.is_override

    db.session.flush()

    # ── Posting ledger reconcile (Build-Order Step 2) ──────────────
    # Re-sync the ledger to what the restored transfer's movements say, AFTER
    # the transfer and its shadows are un-deleted (plan step
    # ``balance:X-bi-6-3``, ruling **R-BAL101**: the door reads the
    # movements, it is told no settled sense).  Since plan step
    # ``balance:X-bi-6-4d-2`` a hidden transfer holds no movement (ruling
    # **R-CC75**) and returns as a plan or a ``$0.00`` close (ruling
    # **R-BAL246**), so this posts nothing.
    posting_service.sync_transfer_postings(xfer)
    # Posting ledger: re-reconcile the loan's genesis ledger -- its split
    # correction plus the opening / true-up corrections.  A restored
    # ``$0.00`` close is a settled loan payment holding no cash (ruling
    # **R-BAL140**), which moves the split; a restored plan or a non-loan
    # transfer moves nothing.
    _sync_loan_postings_if_loan(xfer)

    log_event(
        logger, logging.INFO, EVT_TRANSFER_RESTORED, BUSINESS,
        "Transfer restored from soft-delete",
        user_id=user_id,
        transfer_id=transfer_id,
        shadow_count=len(shadows),
    )
    return xfer
