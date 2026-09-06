"""
Shekel Budget App -- Transfer Service: the RESTORE verb

The inverse of a soft delete, and the one place the five invariants are
REPAIRED rather than merely maintained: a transfer that spent time
soft-deleted may have had a shadow drift out from under it, so every mirrored
field is re-synced from the canonical parent on the way back.

It refuses before it moves (:func:`assert_restorable`, ruling **R-DR**), so a
drift the state machine cannot legally repair leaves all three rows untouched
rather than half-restored.

Flask-isolated like the rest of the package: plain data in, ORM rows out, no
``request`` / ``session`` imports.  Flushes; does NOT commit.
"""

import logging

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.ref import Status
from app.models.transaction import Transaction
from app.services import posting_service
from app.services.transfer_service._loan_posting import (
    _sync_loan_postings_if_loan,
)
from app.services.transfer_service._status import apply_status_to_all_three
from app.services.transfer_service._validation import (
    TransferRows,
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
    re-syncs every field the service mirrors from the canonical parent
    onto both shadows (amount, status, period, category, due_date,
    is_override) in case any drifted via direct ORM mutation while the
    transfer was soft-deleted.  The SETTLEMENT RECORD stays excluded: the
    ``Transfer`` parent carries none of its three columns -- a transfer's
    money moves on its legs -- so there is no value to re-sync against.
    ``apply_status_to_all_three`` repairs a drifted leg's record from its
    SIBLING's instead, which is Transfer Invariant 3 read rather than
    maintained.

    **``settled_on`` IS now maintained, and it is not the parent that supplies
    it** (plan step X-aj1).  Repairing a status through the one seam brings
    the seam's dating rule with it, so a shadow repaired INTO a settled
    status must carry a day and one repaired out of it must not.  The day
    comes from the SIBLING shadow -- Transfer Invariant 3 says the pair is
    equal, and the sibling already records when the money moved.  Taking it
    from there rather than from today is what stops a repair from inventing a
    settle day: since plan step E1a that civil day is the ``entry_date`` the
    re-posted entry below is filed under, so a fabricated day would move money
    on what is supposed to be a repair.

    Idempotent: calling on an already-active transfer is a no-op.

    Args:
        transfer_id: The primary key of the transfer to restore.
        user_id:     The expected owner (defense-in-depth).

    Returns:
        The restored (or already-active) Transfer object.

    Raises:
        NotFoundError: If the transfer does not exist or does not
            belong to user_id.
        ValidationError: If shadow transactions are missing or have
            an invalid type pairing, indicating data corruption that
            cannot be automatically repaired; or if either the source
            or destination account has been archived
            (``is_active = False``) since the transfer was soft-deleted
            (F-164).  Reactivate the account before restoring.
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
    # Shadow count, type pairing, archived endpoints (F-164) and -- new at
    # ruling R-DO -- a status drift the state machine cannot legally repair.
    # Run BEFORE the un-delete, which is a change from the code this replaced:
    # that version flipped ``is_deleted`` first and then hand-restored it on
    # each failing branch, so the rollback was written out three times and the
    # fourth check would have had to remember it too.
    assert_restorable(xfer, shadows, user_id)

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

        # Invariant 4 is repaired for the PAIR after this loop, not per shadow
        # -- see the call to
        # :func:`app.services.transfer_service._status.apply_status_to_all_three`
        # below.

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

    # ── Invariant 4: the PAIR's status, through the one seam ────────
    # Repaired for both shadows together rather than one at a time, and that
    # is load-bearing rather than tidy.  The seam's per-row timestamp rule
    # ("preserve an instant, else stamp now()") would let a repair INVENT a
    # settle day for a shadow that has none -- and since plan step E1a that day
    # is the ``entry_date`` the re-posted entry below is filed under, so the
    # repair would move money.  Going through the pair-aware applier makes the
    # SIBLING's recorded instant the answer, which is what Transfer Invariant 3
    # says it is.  The transfer itself is already at this status, so its own
    # transition is the identity and legal by construction; the shadows'
    # transitions were proved repairable by ``assert_restorable`` above, so
    # neither verification can raise here.
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    expense_shadow = next(
        s for s in shadows if s.transaction_type_id == expense_type_id
    )
    income_shadow = next(s for s in shadows if s is not expense_shadow)
    if any(shadow.status_id != xfer.status_id for shadow in shadows):
        logger.warning(
            "Correcting shadow status drift on transfer %d: %s -> %s.",
            transfer_id,
            {shadow.id: shadow.status_id for shadow in shadows},
            xfer.status_id,
        )
    apply_status_to_all_three(
        TransferRows(xfer, expense_shadow, income_shadow), xfer.status_id,
    )

    db.session.flush()

    # ── Posting ledger reconcile (Build-Order Step 2) ──────────────
    # Re-post the confirmed effect when the restored transfer is settled: a
    # settled transfer that was soft-deleted had its effect reversed by
    # ``delete_transfer``, so restoring re-syncs the ledger to its current
    # status.  Runs AFTER the shadows are un-deleted above, so the income
    # shadow's effective amount is readable.  A no-op for a restored projected
    # transfer (the common path -- nothing was posted to restore).
    restored_status = db.session.get(Status, xfer.status_id)
    posting_service.sync_transfer_postings(
        xfer, settled=restored_status.is_settled,
    )
    # Posting ledger: re-reconcile the loan's genesis ledger for a restored,
    # settled loan payment -- its split correction plus the opening / true-up
    # corrections (a no-op for a restored projected or non-loan transfer).
    _sync_loan_postings_if_loan(xfer)

    log_event(
        logger, logging.INFO, EVT_TRANSFER_RESTORED, BUSINESS,
        "Transfer restored from soft-delete",
        user_id=user_id,
        transfer_id=transfer_id,
        shadow_count=len(shadows),
    )
    return xfer
