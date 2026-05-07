"""
Shekel Budget App -- Entry-Level Credit Card Workflow Service

Manages aggregated CC Payback transactions generated from individual
credit entries on entry-capable transactions.  When entries are flagged
as credit card purchases, this service creates, updates, or deletes
a single CC Payback expense in the next pay period whose amount equals
the sum of all credit entries.

This is the per-entry counterpart to credit_workflow.py, which handles
the legacy per-transaction Credit status.  Both services create CC
Payback transactions with identical field structures; the difference is
the amount source (entry sum vs. transaction amount) and the trigger
(entry mutation vs. status change).
"""

import logging
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.services import pay_period_service
from app.services.credit_workflow import (
    get_or_create_cc_category,
    lock_source_transaction_for_payback,
)
from app.exceptions import NotFoundError, ValidationError
from app.utils.log_events import (
    BUSINESS,
    EVT_ENTRY_PAYBACK_CREATED,
    EVT_ENTRY_PAYBACK_DELETED,
    EVT_ENTRY_PAYBACK_UPDATED,
    log_event,
)

logger = logging.getLogger(__name__)


def sync_entry_payback(
    transaction_id: int, owner_id: int,
) -> Transaction | None:
    """Synchronize the aggregated CC Payback for a transaction's credit entries.

    Called after every entry mutation (create, update, delete, is_credit
    toggle).  Implements a 2x2 state matrix:

      - total_credit > 0, no payback:  CREATE payback in next period.
      - total_credit > 0, payback exists: UPDATE payback amount.
      - total_credit == 0, payback exists: DELETE payback.
      - total_credit == 0, no payback:  no-op.

    The payback is identified by credit_payback_for_id == transaction_id.
    All credit entries share the same credit_payback_id pointing to this
    payback.

    Args:
        transaction_id: The parent transaction's ID.
        owner_id: The resolved owner user ID (companion -> owner mapping
            already applied by the caller).

    Returns:
        The CC Payback Transaction if one exists after sync, else None.

    Concurrency model: the parent transaction row is locked with
    ``SELECT ... FOR NO KEY UPDATE`` before the read-then-insert
    below so two concurrent entry mutations on the same parent
    serialise instead of both falling through the existing-payback
    check and inserting two payback rows.  ``FOR NO KEY UPDATE``
    (rather than the stricter ``FOR UPDATE``) is required because
    the entry INSERT triggered by ``entry_service.create_entry`` /
    ``update_entry`` / ``delete_entry`` upstream of this call
    already holds ``FOR KEY SHARE`` on this row to validate the
    inbound foreign key, and ``FOR UPDATE`` would deadlock with
    that lock.  The lock is released at the next session
    ``commit()`` / ``rollback()`` (the caller's route handler
    always performs one).  ``budget.transactions`` carries
    ``uq_transactions_credit_payback_unique`` as a database-level
    backstop -- if any future caller reaches the INSERT without
    this lock, the unique-index violation surfaces as an
    ``IntegrityError`` that the route layer converts to idempotent
    success.  Audit reference: F-008 (High) / commit C-19.

    Raises:
        NotFoundError: If the transaction doesn't exist or doesn't
            belong to owner_id.
        ValidationError: If a payback needs to be created but no next
            pay period exists.
    """
    # See ``credit_workflow.lock_source_transaction_for_payback`` for
    # the full rationale behind FOR NO KEY UPDATE + populate_existing.
    # Note that FOR NO KEY UPDATE is non-negotiable here:
    # ``entry_service.create_entry`` / ``update_entry`` /
    # ``delete_entry`` already mutated a TransactionEntry referencing
    # this row before delegating, taking FOR KEY SHARE for the FK
    # validation; the stricter FOR UPDATE would deadlock.
    txn = lock_source_transaction_for_payback(transaction_id, owner_id)

    # Expire the entries relationship so we read fresh data from the
    # database.  Without this, a prior load of txn.entries in the same
    # session could be stale after an entry was added or deleted via
    # FK assignment rather than collection mutation.  The
    # ``with_for_update()`` query above refreshes the txn columns
    # themselves but does not touch the related ``entries``
    # collection.
    db.session.expire(txn, ["entries"])

    # Sum credit entries with explicit Decimal("0") start to avoid
    # integer 0 from sum() on an empty iterator.
    credit_entries = [e for e in txn.entries if e.is_credit]
    total_credit = sum(
        (e.amount for e in credit_entries), Decimal("0"),
    )

    # Find existing payback (same query pattern as credit_workflow.py).
    existing_payback = (
        db.session.query(Transaction)
        .filter_by(credit_payback_for_id=txn.id)
        .first()
    )

    if total_credit > 0:
        if existing_payback is None:
            return _create_payback(txn, owner_id, credit_entries, total_credit)
        # UPDATE: adjust the payback amount and link any new entries.
        previous_amount = existing_payback.estimated_amount
        existing_payback.estimated_amount = total_credit
        for entry in credit_entries:
            if entry.credit_payback_id != existing_payback.id:
                entry.credit_payback_id = existing_payback.id
        # Clear stale links on entries that are no longer credit
        # (e.g. toggled from credit to debit since the last sync).
        for entry in txn.entries:
            if not entry.is_credit and entry.credit_payback_id == existing_payback.id:
                entry.credit_payback_id = None
        db.session.flush()
        log_event(
            logger, logging.INFO, EVT_ENTRY_PAYBACK_UPDATED, BUSINESS,
            "Entry-level payback amount updated",
            user_id=owner_id,
            transaction_id=txn.id,
            payback_id=existing_payback.id,
            previous_amount=str(previous_amount),
            new_amount=str(total_credit),
            credit_entry_count=len(credit_entries),
        )
        return existing_payback

    # total_credit == 0
    if existing_payback is not None:
        # DELETE: clear entry links before deleting the payback.
        deleted_payback_id = existing_payback.id
        for entry in txn.entries:
            if entry.credit_payback_id == existing_payback.id:
                entry.credit_payback_id = None
        db.session.delete(existing_payback)
        db.session.flush()
        log_event(
            logger, logging.INFO, EVT_ENTRY_PAYBACK_DELETED, BUSINESS,
            "Entry-level payback deleted (no credit entries remain)",
            user_id=owner_id,
            transaction_id=txn.id,
            payback_id=deleted_payback_id,
        )
    return None


def _create_payback(
    txn: Transaction,
    owner_id: int,
    credit_entries: list[TransactionEntry],
    total_credit: Decimal,
) -> Transaction:
    """Create a new CC Payback transaction in the next pay period.

    Sets every field identically to credit_workflow.mark_as_credit:
    account_id, template_id (None), pay_period_id (next period),
    scenario_id, status_id (PROJECTED), name, category_id (CC Payback),
    transaction_type_id (EXPENSE), estimated_amount, and
    credit_payback_for_id.

    Args:
        txn: The parent transaction.
        owner_id: The resolved owner user ID.
        credit_entries: Credit entries to link to the new payback.
        total_credit: Sum of credit entry amounts.

    Returns:
        The newly created payback Transaction (flushed, id available).

    Raises:
        ValidationError: If no next pay period exists.
    """
    next_period = pay_period_service.get_next_period(txn.pay_period)
    if next_period is None:
        raise ValidationError(
            "No next pay period exists. Generate more periods first."
        )

    cc_category = get_or_create_cc_category(owner_id)
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

    payback = Transaction(
        account_id=txn.account_id,
        template_id=None,
        pay_period_id=next_period.id,
        scenario_id=txn.scenario_id,
        status_id=projected_id,
        name=f"CC Payback: {txn.name}",
        category_id=cc_category.id,
        transaction_type_id=expense_type_id,
        estimated_amount=total_credit,
        credit_payback_for_id=txn.id,
    )
    db.session.add(payback)
    db.session.flush()

    # Link all credit entries to the new payback.
    for entry in credit_entries:
        entry.credit_payback_id = payback.id
    db.session.flush()

    log_event(
        logger, logging.INFO, EVT_ENTRY_PAYBACK_CREATED, BUSINESS,
        "Entry-level payback created from credit entries",
        user_id=owner_id,
        transaction_id=txn.id,
        payback_id=payback.id,
        next_period_id=next_period.id,
        amount=str(total_credit),
        credit_entry_count=len(credit_entries),
    )
    return payback
