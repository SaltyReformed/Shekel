"""
Shekel Budget App -- Transfer Service: the DELETE verb

Removing a transfer, soft or hard, and with it both shadow
:class:`~app.models.transaction.Transaction` rows -- Transfer Invariant 2, that
a shadow is never orphaned, applied in the one direction that could orphan one.

The ORDER inside is the whole of the module: the posted cash effect is
reversed while the rows still exist to link against, because a hard delete
SET-NULLs those links on its way out; the loan-payment split links no row
(ruling **R-BAL102**) and is re-derived AFTER the payment is gone.

Flask-isolated like the rest of the package: plain data in, ORM rows out, no
``request`` / ``session`` imports.  Flushes; does NOT commit.
"""

import logging

from app.extensions import db
from app.models.transaction import Transaction
from app.services import match_withdrawal, posting_service
from app.services.transfer_service._loan_posting import (
    _pays_a_loan,
    _resync_loan_after_payment_left,
)
from app.services.transfer_service._validation import _get_transfer_or_raise
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_HARD_DELETED,
    EVT_TRANSFER_SOFT_DELETED,
    log_event,
)

logger = logging.getLogger(__name__)


def delete_transfer(transfer_id, user_id, soft=False):
    """Delete a transfer and its shadow transactions.

    Args:
        transfer_id: The primary key of the transfer to delete.
        user_id:     The expected owner (defense-in-depth).
        soft:        If True, set is_deleted=True on the transfer and
                     both shadows (preserves records).  If False,
                     physically remove the transfer; the ON DELETE
                     CASCADE FK on transactions.transfer_id removes
                     both shadows automatically.

    Returns:
        The soft-deleted Transfer if soft=True, or None if hard-deleted.

    Raises:
        NotFoundError: If the transfer does not exist or does not
            belong to user_id.
    """
    # allow_deleted=True so that idempotent soft-delete and hard-delete
    # of already-soft-deleted transfers continue to work.
    xfer = _get_transfer_or_raise(transfer_id, user_id, allow_deleted=True)

    # ── Posting ledger reconcile (Build-Order Step 2) ──────────────
    # Reverse any posted effect BEFORE the row is removed, so a settled
    # transfer's per-movement entries net to zero.  Runs first -- while
    # xfer.id, the shadows and their covering movements still exist -- so each
    # reversal entry can link its movement and read the posted legs back; a
    # hard delete then SET-NULLs the links, leaving the immutable net-zero
    # pairs as history.  The TEARDOWN door, not the pair's sync (plan step
    # ``balance:X-bi-6-3``, ruling **R-BAL101**): the sync reads each
    # movement's own state and would find two live, dated movements at this
    # moment and leave them posted.  Idempotent no-op for a never-settled or
    # already-reversed transfer (the account-delete and
    # recurrence-regeneration paths only ever reach those: Guard 4 in
    # ``accounts/crud.py`` archives any account with settled history).
    posting_service.reverse_transfer_postings_before_delete(xfer)

    # ── Loan coordinates, captured while the row exists ────────────
    # The loan this payment leaves is re-reconciled AFTER the delete (below):
    # its split correction links no row (ruling **R-BAL102**, plan step
    # ``balance:X-bi-6-3``), so nothing about it needs the shadow to still
    # exist, and the departed payment's key reverses as a posted key with no
    # target.  Through that step the split was reversed HERE, first, by the
    # income shadow's ``transaction_id`` the CASCADE was about to SET NULL.
    # What still has to be read before the row goes is WHICH loan to re-sync.
    is_loan_payment = _pays_a_loan(xfer)
    loan_account_id = xfer.to_account_id
    scenario_id = xfer.scenario_id

    # ── Statement matches (developer ruling 2026-08-25, bank_import:X-gb) ──
    # A shadow a bank line was matched to stops existing on the HARD path, so
    # an act it was the last app row of is withdrawn and that line is
    # unexplained again.  Read BEFORE the branch: the member foreign keys are
    # ON DELETE CASCADE, so after the delete nothing says which lines were
    # freed.  Measured on the developer's own dev database at 16 matched
    # shadows.
    #
    # **A SOFT delete withdraws nothing, and that falls out of the rule rather
    # than being excepted from it**: the member survives a flag change, so the
    # act still names its row and there is nothing to withdraw.  It is also the
    # answer this path needs -- ``routes/transfers/templates`` archives with
    # ``soft=True`` and UN-archives with ``restore_transfer``, and
    # ``transfer_recurrence`` restores soft-deleted shadows during a maintain
    # pass, so a withdrawal here would destroy an accepted act that a shipped
    # button puts the rows back for (adversarial review, 2026-08-25).
    shadows = (
        db.session.query(Transaction)
        .filter_by(transfer_id=transfer_id)
        .all()
    )
    if not soft:
        match_withdrawal.withdraw_for_rows(shadows, user_id)

    if soft:
        xfer.is_deleted = True
        # Soft-delete must explicitly mark both shadows.  The database
        # CASCADE only fires on physical deletes, not flag changes.
        for shadow in shadows:
            shadow.is_deleted = True
        db.session.flush()
        log_event(
            logger, logging.INFO, EVT_TRANSFER_SOFT_DELETED, BUSINESS,
            "Transfer and shadows soft-deleted",
            user_id=user_id,
            transfer_id=transfer_id,
            shadow_count=len(shadows),
        )
        result = xfer
    else:
        # Hard delete -- rely on ON DELETE CASCADE to remove shadows.
        db.session.delete(xfer)
        db.session.flush()

        # Verify CASCADE removed the shadows.  If they still exist,
        # the FK was misconfigured in Task 2.
        orphan_count = (
            db.session.query(Transaction)
            .filter_by(transfer_id=transfer_id)
            .count()
        )
        if orphan_count > 0:
            logger.error(
                "CASCADE delete failed: %d orphaned shadow transactions "
                "remain for deleted transfer %d.",
                orphan_count, transfer_id,
            )

        log_event(
            logger, logging.INFO, EVT_TRANSFER_HARD_DELETED, BUSINESS,
            "Transfer hard-deleted (CASCADE)",
            user_id=user_id,
            transfer_id=transfer_id,
            orphan_count=orphan_count,
        )
        result = None

    # ── Downstream re-reconcile (posting ledger) ───────────────────
    # After the payment is gone, re-reconcile the loan's genesis ledger: the
    # LATER payments whose running balance the deletion changed AND any true-up
    # whose owed_before it moved.  Idempotent and self-healing; skipped entirely
    # for a non-loan transfer.
    if is_loan_payment:
        _resync_loan_after_payment_left(loan_account_id, scenario_id)
    return result
