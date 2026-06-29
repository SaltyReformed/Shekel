"""
Shekel Budget App -- Credit Card Workflow Service

Handles the credit card status + auto-payback mechanism described
in §4.5 of the requirements.  When an expense is marked 'credit',
a payback expense is auto-generated in the next pay period.
"""

import logging
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.services import pay_period_service, posting_service, status_seam
from app.exceptions import NotFoundError, ValidationError
from app.utils.balance_predicates import is_credit, is_projected
from app.utils.log_events import (
    BUSINESS,
    EVT_CREDIT_MARKED,
    EVT_CREDIT_UNMARKED,
    EVT_PAYBACK_DELETED_WITH_SOURCE,
    log_event,
)

logger = logging.getLogger(__name__)

# The category used for auto-generated credit card payback expenses.
CC_PAYBACK_GROUP = "Credit Card"
CC_PAYBACK_ITEM = "Payback"


def lock_source_transaction_for_payback(
    transaction_id: int, owner_id: int,
) -> Transaction:
    """Acquire ``SELECT ... FOR NO KEY UPDATE`` on a source transaction.

    Shared by :func:`mark_as_credit` and
    :func:`entry_credit_workflow.sync_entry_payback` to bracket each
    one's read-then-insert sequence with a row-level write lock.
    PostgreSQL serialises any concurrent ``FOR NO KEY UPDATE`` /
    ``FOR UPDATE`` request on the same row, so two concurrent
    payback-creating callers serialise instead of both falling
    through their idempotency check and double-inserting.

    Three SQLAlchemy options are load-bearing:

      * ``of=Transaction`` -- ``Transaction.account``, ``.status``,
        ``.category``, and ``.transaction_type`` are
        ``lazy="joined"`` so the default query emits LEFT OUTER
        JOINs.  PostgreSQL rejects ``FOR UPDATE`` whose target
        spans the nullable side of an outer join
        (``FeatureNotSupported``).  Restricting the lock to the
        transactions table with ``OF`` keeps the syntax legal
        while still locking the row we care about.

      * ``key_share=True`` -- selects ``FOR NO KEY UPDATE`` rather
        than the stricter ``FOR UPDATE``.  Both lock modes
        serialise concurrent FOR-NO-KEY-UPDATE / FOR-UPDATE
        requests on the same row, but FOR-NO-KEY-UPDATE does NOT
        conflict with the FOR-KEY-SHARE locks PostgreSQL takes
        automatically while validating an inbound foreign key
        (e.g. the payback INSERT downstream of this call, or a
        concurrent transaction_entries INSERT against the same
        parent).  The stricter FOR UPDATE would deadlock with
        those FK-validation locks under load.

      * ``populate_existing()`` -- forces the locking SELECT to
        overwrite any cached attributes already in the session's
        identity map.  Without it a serialised second request
        would observe its own pre-lock cached attributes
        (``status_id`` in particular) and skip the post-lock
        idempotency short-circuit, falling through to a duplicate
        INSERT that the partial unique index would only catch as
        an ``IntegrityError``.

    The lock is released at the next session ``commit()`` /
    ``rollback()`` -- the caller's route handler always performs
    one.  Audit reference: F-008 (High) / commit C-19.

    Args:
        transaction_id: Primary key of the row to lock.
        owner_id: Resolved owner user ID; the loaded txn's
            pay-period user must match this so an attacker probing
            for valid IDs cannot tell "row exists but belongs to
            someone else" from "row does not exist."

    Returns:
        The locked Transaction with refreshed column attributes.

    Raises:
        NotFoundError: If the row does not exist or its pay-period
            does not belong to ``owner_id``.
    """
    txn = (
        db.session.query(Transaction)
        .filter_by(id=transaction_id)
        .populate_existing()
        .with_for_update(of=Transaction, key_share=True)
        .one_or_none()
    )
    if txn is None:
        raise NotFoundError(f"Transaction {transaction_id} not found.")
    # Defense-in-depth: verify ownership via pay period.  Performed
    # after the lock is acquired so an attacker probing for valid
    # IDs cannot race the lock window to confirm existence.
    if txn.pay_period.user_id != owner_id:
        raise NotFoundError(f"Transaction {transaction_id} not found.")
    return txn


def get_active_payback(source_txn_id: int) -> Transaction | None:
    """Return the live (non-soft-deleted) CC payback for a source txn.

    The single definition of "the payback for this transaction," shared
    by the mark / unmark / entry-sync paths so they cannot disagree on
    which row counts.  The ``is_deleted == False`` filter mirrors the
    partial unique index ``uq_transactions_credit_payback_unique``
    (partial on ``credit_payback_for_id IS NOT NULL AND is_deleted =
    FALSE``) and the binding soft-delete query rule: a soft-deleted
    payback is kept for the audit trail but must never be treated as the
    live payback, so a re-mark after a soft-delete legally creates a
    fresh active row instead of resurrecting the dead one.

    Args:
        source_txn_id: The ``id`` of the credit source transaction.

    Returns:
        The active payback :class:`Transaction`, or ``None`` when no
        live payback exists for the source.
    """
    return (
        db.session.query(Transaction)
        .filter_by(credit_payback_for_id=source_txn_id, is_deleted=False)
        .first()
    )


def delete_payback_on_credit_revert(txn: Transaction, user_id: int) -> None:
    """Delete the live auto-generated payback for a reverted credit row.

    The single cleanup rule for "a Credit transaction returned to
    Projected": the live payback (if any) is hard-deleted and the
    reversion is logged with ``EVT_CREDIT_UNMARKED``.  Shared by
    :func:`unmark_credit` and the transaction PATCH route's
    status-revert path so a credit reversion can never orphan its
    payback regardless of which endpoint performed it.  A soft-deleted
    prior payback stays in place for the audit trail (the
    :func:`get_active_payback` contract).

    The caller owns the status flip itself and must already have
    verified ownership and the transition's legality; this helper does
    not commit -- the deletion joins the caller's transaction so the
    status change and the payback removal land atomically.

    Args:
        txn: The credit transaction being reverted to Projected.
        user_id: The owning user's ID, recorded on the audit event.
    """
    payback = get_active_payback(txn.id)
    deleted_payback_id = None
    if payback:
        deleted_payback_id = payback.id
        # Reverse the payback's own ledger postings before it is deleted
        # (Build-Order Step 3 reverse-before-delete): a payback that was settled
        # -- and therefore posted -- before its source's Credit status is
        # reverted must not leave its double-entry legs stranded on the ledger.
        # Idempotent no-op for the usual still-Projected payback.
        posting_service.reverse_postings_before_delete(payback)
        db.session.delete(payback)

    log_event(
        logger, logging.INFO, EVT_CREDIT_UNMARKED, BUSINESS,
        "Credit reverted to Projected; payback deleted",
        user_id=user_id,
        transaction_id=txn.id,
        deleted_payback_id=deleted_payback_id,
    )


def delete_payback_on_source_delete(txn: Transaction, user_id: int) -> None:
    """Delete the live auto-generated payback when its source is deleted.

    The deletion-side sibling of :func:`delete_payback_on_credit_revert`:
    where that helper cleans up after a Credit row returning to
    Projected, this one cleans up after the source transaction being
    deleted outright (soft or hard) -- the strongest possible withdrawal
    of the credit assertion.  Without it the projected payback survives
    its source (``credit_payback_for_id`` is ``ondelete="SET NULL"``)
    and silently inflates the next period with no offsetting credit row.

    Keyed on :func:`get_active_payback` rather than Credit status
    because entry-level credit sources carry a live payback while their
    own ``status_id`` is NOT Credit -- a status guard would miss them.
    Entry links (``TransactionEntry.credit_payback_id``) are severed
    before the delete: a template-linked source soft-deletes, so its
    entries outlive it and must not point at a vanished payback.  A
    payback can itself be marked Credit, so the helper recurses to take
    the whole live chain down with the source.  No-op (and no log event)
    when no live payback exists -- the common case for every ordinary
    delete.  A soft-deleted prior payback stays in place for the audit
    trail (the :func:`get_active_payback` contract).

    This helper does not commit -- the deletions join the caller's
    transaction so the source delete and the payback removal land
    atomically.

    Args:
        txn: The source transaction about to be deleted.
        user_id: The owning user's ID, recorded on the audit event.
    """
    payback = get_active_payback(txn.id)
    if payback is None:
        return

    # Sever entry links before the delete.  On a hard-deleted ad-hoc
    # source the entries cascade away anyway; on a soft-deleted
    # template-linked source they survive as rows and must not keep a
    # pointer to the deleted payback (mirrors sync_entry_payback's
    # delete branch).
    for entry in txn.entries:
        if entry.credit_payback_id == payback.id:
            entry.credit_payback_id = None

    # The payback may itself be a credit source (a projected expense
    # can be marked Credit); its own live payback dies with it under
    # the same invariant.
    delete_payback_on_source_delete(payback, user_id)
    # Reverse the payback's own ledger postings before deleting it (Build-Order
    # Step 3 reverse-before-delete).  The recursion above runs FIRST, so each
    # deeper level of the chain reverses-then-deletes before this one does --
    # every ledger account is net-zero before the row's transaction_id link
    # SET-NULLs on the delete.  Idempotent no-op for a still-Projected payback.
    posting_service.reverse_postings_before_delete(payback)
    db.session.delete(payback)

    log_event(
        logger, logging.INFO, EVT_PAYBACK_DELETED_WITH_SOURCE, BUSINESS,
        "Source transaction deleted; live payback deleted with it",
        user_id=user_id,
        transaction_id=txn.id,
        deleted_payback_id=payback.id,
    )


def mark_as_credit(transaction_id, user_id):
    """Mark a transaction as 'credit' and auto-generate a payback expense.

    Steps:
      1. Acquire a row-level write lock on the source transaction
         with ``SELECT ... FOR NO KEY UPDATE`` so two concurrent
         POSTs serialise instead of both falling through the
         idempotency check.
      2. Verify ownership via the transaction's pay period.
      3. Set the transaction's status to 'credit'.
      4. Find or create the CC payback category.
      5. Find the next pay period.
      6. Create a payback expense in the next period linked to the original.

    Concurrency model: the row lock acquired in step 1 (``SELECT
    ... FOR NO KEY UPDATE``) is held until the caller commits or
    rolls back the SQLAlchemy session.  When a second request races
    with the first, the second's locking SELECT blocks until the
    first commits; PostgreSQL then returns the post-commit row to
    the second request, whose idempotency check (status already
    ``credit``, payback already exists) returns the existing
    payback without inserting a duplicate.  ``FOR NO KEY UPDATE``
    rather than the stricter ``FOR UPDATE`` is required so the
    payback INSERT later in this function (which takes
    ``FOR KEY SHARE`` on the source row to validate its FK) does
    not deadlock with the lock acquired here -- see PostgreSQL's
    row-lock conflict matrix.  ``budget.transactions`` carries
    ``uq_transactions_credit_payback_unique`` as a database-level
    backstop -- if any future caller reaches the INSERT without
    this lock, the unique-index violation surfaces as an
    ``IntegrityError`` that the route layer converts to idempotent
    success.  Audit reference: F-008 (High) / commit C-19.

    Args:
        transaction_id: The ID of the transaction to mark as credit.
        user_id: The ID of the user who owns the transaction.
            Defense-in-depth: ownership is verified via the
            transaction's pay period even if the caller already
            checked at the route level.

    Returns:
        The newly created payback Transaction, or the existing
        payback if the transaction is already in ``credit`` status.

    Raises:
        NotFoundError:  If the transaction doesn't exist or doesn't
            belong to *user_id*.
        ValidationError: If the transaction is income (can't credit income),
            is a transfer shadow, uses entry tracking, has a status
            other than projected, or has no following pay period.
    """
    # See ``lock_source_transaction_for_payback`` for the full
    # rationale behind FOR NO KEY UPDATE + populate_existing().
    txn = lock_source_transaction_for_payback(transaction_id, user_id)
    if txn.is_income:
        raise ValidationError("Cannot mark income as credit.")
    if txn.transfer_id is not None:
        raise ValidationError("Cannot mark transfer transactions as credit.")

    # Block legacy credit on entry-capable transactions.
    if txn.tracks_purchases:
        raise ValidationError(
            "This transaction uses individual purchase tracking. "
            "Mark individual entries as credit instead of the whole transaction."
        )

    credit_id = ref_cache.status_id(StatusEnum.CREDIT)

    # Idempotency: if already credited with existing payback, return it.
    # Routed through the centralized ``is_credit`` predicate
    # (D6-09 / MED-02) so the idempotency gate shares one definition
    # with ``unmark_credit`` and ``entry_service.create_entry``.
    if is_credit(txn):
        existing_payback = get_active_payback(txn.id)
        if existing_payback:
            return existing_payback

    # Only projected transactions can be newly marked as credit.
    # Routed through the centralized ``is_projected`` predicate
    # (D6-09 / MED-02).
    if not is_projected(txn):
        raise ValidationError(
            f"Cannot mark a '{txn.status.name}' transaction as credit. "
            "Only projected transactions can be marked as credit."
        )

    # Update the original transaction's status through the single status seam.
    # The seam assigns ``status_id`` and expires the eagerly-joined ``status``
    # relationship so downstream code (test assertions, the post-flush cell
    # render) observes ``Credit`` rather than the stale ``Projected`` row
    # SQLAlchemy leaves cached when only the FK column is rewritten.  Credit is
    # a non-settled status, so the seam also leaves ``paid_at`` clear.
    status_seam.apply_status_change(txn, credit_id)

    # Find or create the CC Payback category for this user.
    period = db.session.get(PayPeriod, txn.pay_period_id)

    category = get_or_create_cc_category(user_id)

    # Find the next pay period.
    next_period = pay_period_service.get_next_period(period)
    if next_period is None:
        raise ValidationError(
            "No next pay period exists.  Generate more periods first."
        )

    # Determine the payback amount (use actual if set, else estimated).
    payback_amount = txn.actual_amount if txn.actual_amount is not None else txn.estimated_amount

    # Create the payback transaction via the shared factory (see
    # entry_credit_workflow for the entry-level twin).
    payback = create_cc_payback_transaction(
        txn, next_period, category, payback_amount,
    )

    # Posting ledger reconcile (Build-Order Step 3): reconcile the SOURCE row to
    # its new status's settled sense as the final step (the transfer pattern:
    # reconcile on every status change).  Credit is non-settled and is reachable
    # only from Projected, so this is an idempotent no-op today -- a Projected
    # source has no postings to reverse -- but it keeps the "every status
    # handler reconciles last" invariant complete and self-heals if the state
    # machine ever lets a settled row be marked Credit.  The payback itself is
    # born Projected and posts nothing until it settles through the seam.
    posting_service.sync_transaction_postings(txn, settled=txn.status.is_settled)

    log_event(
        logger, logging.INFO, EVT_CREDIT_MARKED, BUSINESS,
        "Transaction marked Credit; payback expense generated",
        user_id=user_id,
        transaction_id=txn.id,
        payback_id=payback.id,
        next_period_id=next_period.id,
        amount=str(payback_amount),
    )
    return payback


def unmark_credit(transaction_id, user_id):
    """Revert a transaction from 'credit' back to 'projected' and delete its payback.

    Two precondition checks layered for clarity and defense-in-depth:

      1. **Bespoke source-state guard.** The action only makes sense
         on a row that is currently in ``Credit`` status -- that is
         the row that has an auto-generated payback to clean up.
         Calling on any other status would silently rewrite
         ``status_id`` to ``Projected`` (dropping a Paid row back to
         Projected, for example) and attempt to delete a non-existent
         payback.  The previous implementation had no such guard;
         this fixes that latent bug.

      2. **State-machine verification.** ``verify_transition`` is
         called as a final policy choke point so any future caller
         that adds a new ``Status`` row -- or any code path that
         skips the bespoke guard -- still cannot push a row through
         an illegal transition.  ``Settled -> Projected`` is the
         transition the state machine refuses; the bespoke guard
         above already excludes that case but the redundancy makes
         the policy explicit at the database boundary.

    Args:
        transaction_id: The ID of the credited transaction.
        user_id: The ID of the user who owns the transaction.
            Defense-in-depth: ownership is verified via the
            transaction's pay period.

    Raises:
        NotFoundError: If the transaction doesn't exist or doesn't
            belong to *user_id*.
        ValidationError: If the transaction is not currently in
            ``Credit`` status, or if the transition (in the unlikely
            case the bespoke guard is bypassed) is not allowed by
            the state machine.
    """
    txn = db.session.get(Transaction, transaction_id)
    if txn is None:
        raise NotFoundError(f"Transaction {transaction_id} not found.")
    # Defense-in-depth: verify ownership via pay period.
    if txn.pay_period.user_id != user_id:
        raise NotFoundError(f"Transaction {transaction_id} not found.")

    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

    # Bespoke source-state guard.  Friendly user-facing message that
    # names the offending status.  The route layer surfaces this as
    # the response body on a 400.  Routed through the centralized
    # ``is_credit`` predicate (D6-09 / MED-02); the ``projected_id``
    # local binding above is for the state-machine call and the
    # post-guard status assignment below.
    if not is_credit(txn):
        current_name = txn.status.name if txn.status is not None else "<unset>"
        raise ValidationError(
            f"Cannot unmark credit on a '{current_name}' transaction.  "
            "Only Credit transactions can be unmarked."
        )

    # Revert the original transaction's status through the single status seam.
    # The seam runs the same state-machine verification (defense-in-depth,
    # redundant with the bespoke ``is_credit`` guard above except when a future
    # StatusEnum addition makes that guard incomplete) before assigning
    # ``status_id``; Projected is non-settled, so it also clears ``paid_at``.
    status_seam.apply_status_change(txn, projected_id)

    # Delete the live payback + write the audit event.  Shared with the
    # transaction PATCH route's status-revert path via the single
    # cleanup helper so the two endpoints cannot disagree.
    delete_payback_on_credit_revert(txn, user_id)

    # Posting ledger reconcile (Build-Order Step 3): reconcile the SOURCE row to
    # its new status as the final step (the transfer pattern: reconcile on every
    # status change).  Projected is non-settled, so this reverses any posted
    # source effect to zero -- an idempotent no-op today, since a Credit source
    # never settled and so never posted -- keeping the "every status handler
    # reconciles last" invariant complete.  The payback's own postings were
    # already reversed inside delete_payback_on_credit_revert.
    posting_service.sync_transaction_postings(txn, settled=txn.status.is_settled)


def get_or_create_cc_category(user_id: int) -> Category:
    """Find or create the 'Credit Card: Payback' category for a user.

    Args:
        user_id: The owning user's ID.

    Returns:
        The Category object.
    """
    category = (
        db.session.query(Category)
        .filter_by(
            user_id=user_id,
            group_name=CC_PAYBACK_GROUP,
            item_name=CC_PAYBACK_ITEM,
        )
        .first()
    )
    if category is None:
        category = Category(
            user_id=user_id,
            group_name=CC_PAYBACK_GROUP,
            item_name=CC_PAYBACK_ITEM,
        )
        db.session.add(category)
        db.session.flush()
    return category


def create_cc_payback_transaction(
    source_txn: Transaction,
    next_period: PayPeriod,
    cc_category: Category,
    amount: Decimal,
) -> Transaction:
    """Build, add, and flush the CC Payback expense for a credit transaction.

    Shared by the transaction-level mark-Credit flow in this module and
    the entry-level credit payback in
    :mod:`app.services.entry_credit_workflow`.  Both produce the
    identical payback shape -- a PROJECTED EXPENSE in the next pay
    period, categorised to the user's CC Payback category, linked back
    to the source transaction via ``credit_payback_for_id`` -- and
    differ only in the amount source (the entry-level path sums the
    credit entries; the transaction-level path uses the actual-or-
    estimated amount).  PROJECTED status and EXPENSE type are invariants
    of a payback, so they are resolved here rather than passed in.

    Args:
        source_txn: The credit transaction being paid back.  Supplies
            the account, scenario, name, and ``credit_payback_for_id``
            link.
        next_period: The pay period the payback lands in (the period
            after ``source_txn``'s).
        cc_category: The user's "Credit Card: Payback" category (from
            :func:`get_or_create_cc_category`).
        amount: The payback's ``estimated_amount`` (a Decimal).

    Returns:
        The newly created payback :class:`Transaction`, flushed so its
        ``id`` is available for entry linkage and logging.
    """
    payback = Transaction(
        account_id=source_txn.account_id,
        template_id=None,
        pay_period_id=next_period.id,
        scenario_id=source_txn.scenario_id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        name=f"CC Payback: {source_txn.name}",
        category_id=cc_category.id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        estimated_amount=amount,
        credit_payback_for_id=source_txn.id,
    )
    db.session.add(payback)
    db.session.flush()
    return payback
