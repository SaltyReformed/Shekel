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
from app.services import posting_service
from app.services.row_valuation import settled_figure
from app.services.pay_calendar import calendar_for
from app.services.credit_workflow import (
    create_cc_payback_transaction,
    get_active_payback,
    get_or_create_cc_category,
    lock_source_transaction_for_payback,
)
from app.exceptions import ValidationError
from app.utils.entry_partition import partition_entries
from app.utils.log_events import (
    BUSINESS,
    EVT_ENTRY_PAYBACK_CREATED,
    EVT_ENTRY_PAYBACK_DELETED,
    EVT_ENTRY_PAYBACK_UPDATED,
    log_event,
)

logger = logging.getLogger(__name__)


def sync_entry_payback(
    transaction_id: int, owner_id: int, *, moves_credit_total: bool = True,
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
        moves_credit_total: Whether the write that triggered this sync can
            CHANGE the envelope's credit-entry sum (finding **N-323**).  Each
            of the three ``entry_service`` doors knows what it touched and
            says so; the default is the safe answer for any other caller.
            It gates the settled-payback refusal below and nothing else --
            the link maintenance and the figure both run either way.

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

    # Partition via the shared helper so "which entries are credits" has
    # one definition (DH-#75); sum with an explicit Decimal("0") start to
    # avoid integer 0 from sum() on an empty iterator.
    _, credit_entries = partition_entries(txn.entries)
    total_credit = sum(
        (e.amount for e in credit_entries), Decimal("0"),
    )

    # Find the live payback (shared definition with credit_workflow;
    # excludes soft-deleted rows so a prior soft-deleted payback is not
    # resurrected and mutated -- a fresh one is created instead).
    existing_payback = get_active_payback(txn.id)

    if total_credit > 0:
        if existing_payback is None:
            return _create_payback(txn, owner_id, credit_entries, total_credit)
        # **REFUSED when the payback has already SETTLED and the new total
        # would change what it recorded** (plan step X-au-c3).  A settled row is
        # worth what it RECORDED, not what its plan says, so the
        # ``estimated_amount`` write below is inert for money once the payback
        # closes -- and the liability the user just added would leave the
        # projection with nothing booking it.  Measured before this guard: a
        # payback settled at ``$100.00``, a later ``$50.00`` card purchase, and
        # ``estimated_amount`` moved to ``$150.00`` while every balance went on
        # reading ``$100.00``.
        #
        # It is the sibling of the rule the developer ruled for the SOURCE row
        # (``entry_service._doors._reject_settled_parent``, 2026-08-17): money
        # that has moved is a record, and a record is changed by reverting the
        # row and settling it again, never by re-deriving it underneath.  The
        # comparison is against what the payback RECORDED rather than against
        # its plan, so a sync that changes nothing still passes.
        #
        # **The POLICY above is right and stays; the PREDICATE was wider than
        # the policy needs, and that is finding N-323.**  It fired whenever the
        # recorded figure ALREADY differed from ``total_credit`` at all -- so a
        # settled payback carrying pre-existing drift refused every later edit
        # on its envelope, including edits that cannot change that sum.
        # Stamping a DEBIT purchase's bank posting day is the case that
        # measured it: it touches no credit entry, so the sum is the same
        # before and after, and it was refused by a guard about credit.  On the
        # developer's own production clone that blocked **5 of 124 statement
        # proposals worth `$706.35`**, plus 6 debit purchases under the two
        # envelopes whose paybacks carry `$59.68` of drift.
        #
        # The question a write should be asked is whether IT moves the total,
        # which is what ``moves_credit_total`` carries.  Drift that already
        # exists is left alone rather than treated as a fresh offence -- it is
        # a finding the amount model reports, not something to punish the next
        # unrelated edit for.
        recorded = settled_figure(existing_payback)
        if moves_credit_total and recorded is not None and recorded != total_credit:
            raise ValidationError(
                f"Payback {existing_payback.id} has settled at {recorded}, so "
                f"it cannot be re-derived to {total_credit}: a settled row "
                "records what MOVED. Set the payback back to Projected, then "
                "record this purchase -- the figure it recorded is kept, and "
                "marking it paid again books the new total.",
            )
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
        # **A SETTLED payback is not DELETED either, and this refusal is the
        # other half of the one above** (plan step X-au-c3, second pass).  That
        # guard refuses to RE-DERIVE a payback whose money has moved; without
        # this one the same function went on to DESTROY such a payback outright
        # the moment the last credit purchase was removed -- the larger harm
        # performed in silence beside the smaller one refused.
        #
        # Measured: a source row Projected with one ``$100.00`` credit purchase,
        # its payback created and marked Paid (the money really left the
        # account), then that purchase deleted or un-credited on the
        # still-Projected source.  A settled row carrying a ``derived``
        # ``$100.00`` record was hard-deleted and its postings reversed --
        # ``$100.00`` that had moved, erased with no refusal and no trace.
        #
        # The delete branch predates this step.  What makes it a defect NOW is
        # that the payback carries a settlement RECORD for the delete to throw
        # away, and that the developer's 2026-08-17 ruling states the rule it
        # breaks: money that has moved is a record, and a record is undone by
        # reverting the row, never underneath it.  The remedy named here is the
        # one the source row's own refusal names
        # (``entry_service._doors._reject_settled_parent``).
        recorded = settled_figure(existing_payback)
        if recorded is not None:
            raise ValidationError(
                f"Payback {existing_payback.id} has settled at {recorded}, so "
                "it cannot be removed: that money has already left the "
                "account. Set the payback back to Projected first -- the "
                "figure it recorded is kept -- and then remove the purchase.",
            )
        # DELETE: clear entry links before deleting the payback.
        deleted_payback_id = existing_payback.id
        for entry in txn.entries:
            if entry.credit_payback_id == existing_payback.id:
                entry.credit_payback_id = None
        # Reverse the payback's own ledger postings before deleting it
        # (Build-Order Step 3 reverse-before-delete): an entry-level payback that
        # was settled -- and therefore posted -- before its source's credit
        # entries were all removed must not leave its double-entry legs stranded.
        # Idempotent no-op for a still-Projected payback.
        posting_service.reverse_postings_before_delete(existing_payback)
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
    next_period = calendar_for(owner_id).period_starting_after(
        txn.pay_period.start_date,
    )
    if next_period is None:
        raise ValidationError(
            "No next pay period exists. Generate more periods first."
        )

    cc_category = get_or_create_cc_category(owner_id)

    # Shared factory; see credit_workflow for the transaction-level twin.
    payback = create_cc_payback_transaction(
        txn, next_period, cc_category, total_credit,
    )

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
        next_period_id=next_period.period_id,
        amount=str(total_credit),
        credit_entry_count=len(credit_entries),
    )
    return payback
