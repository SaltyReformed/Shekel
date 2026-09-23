"""
Shekel Budget App -- Transfer Service: the DELETE verb

Removing a transfer, soft or hard, and with it both shadow
:class:`~app.models.transaction.Transaction` rows -- Transfer Invariant 2, that
a shadow is never orphaned, applied in the one direction that could orphan one.

The ORDER inside is the whole of the module: the posted effect is reversed and
the loan-payment split is taken back while the rows still exist to link
against, because a hard delete SET-NULLs those links on its way out.

Flask-isolated like the rest of the package: plain data in, ORM rows out, no
``request`` / ``session`` imports.  Flushes; does NOT commit.
"""

import logging

from app.extensions import db
from app.models.transaction import Transaction
from app.services import match_withdrawal, movement_removal, posting_service
from app.services.transfer_service._loan_posting import (
    _resync_loan_after_payment_left,
    _reverse_loan_payment_before_it_leaves,
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
    # transfer's ledger entry nets to zero.  Runs first -- while xfer.id and
    # the shadows still exist -- so the reversal entry can link ``transfer_id``
    # and read the shadow settle date; a hard delete then SET-NULLs the link,
    # leaving the immutable net-zero pair as history.  Idempotent no-op for a
    # never-settled or already-reversed transfer (the account-delete and
    # recurrence-regeneration paths only ever reach those: Guard 4 in
    # ``accounts/crud.py`` archives any account with settled history).
    posting_service.sync_transfer_postings(xfer, settled=False)

    # ── Loan-payment split reversal (Build-Order Step 4) ───────────
    # Reverse this payment's split correction while the income shadow id still
    # exists -- load-bearing for a hard delete, whose CASCADE SET-NULLs the
    # correction's ``transaction_id`` link.  Capture the loan coordinates now,
    # before the row can be deleted, so the downstream payments (whose running
    # balance the deletion changes) can be re-split afterwards.  A no-op for a
    # non-loan transfer.
    is_loan_payment = _reverse_loan_payment_before_it_leaves(xfer)
    loan_account_id = xfer.to_account_id
    scenario_id = xfer.scenario_id

    # ── Statement matches (developer ruling 2026-08-25, bank_import:X-gb) ──
    # A shadow a bank line was matched to stops existing on the HARD path, so
    # an act it was the last app row of is withdrawn and that line is
    # unexplained again.  Through the ONE act that takes a movement off the
    # books (plan step ``credit_card:CC-5-4a-3``, ruling **R-CC54**): both
    # shadows' movements out of their matches and deleted -- the pair's posted
    # effect is the reconcile above's to reverse, so the act's per-movement
    # reversal has nothing of its own to do -- and FLUSHED before the
    # transfer goes: the shadows go by the database's cascade, so their
    # movements' DELETEs must land first.  The unit of work orders them so
    # today without this line (measured 2026-09-22: removing it passes every
    # transfer and withdrawal test), through a mapper-level dependency the
    # self-referential ``Transaction`` mapper sorts row by row -- so the order
    # is stated here rather than left to that sort.  Measured on the
    # developer's own dev database at 16 matched shadows.
    #
    # **A SOFT delete withdraws nothing, and the ``if not soft`` below is what
    # says so** (the rule's own docstring: "the CALLER is what says so"): the
    # member survives a flag change, so the act still names its row and there
    # is nothing to withdraw.  It is also the
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
        movement_removal.remove_movements(
            [movement for shadow in shadows for movement in shadow.entries],
            user_id, because=match_withdrawal.LEFT_THE_BOOKS,
            rows_leaving=shadows,
        )
        db.session.flush()

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
