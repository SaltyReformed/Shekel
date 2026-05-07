"""
Shekel Budget App -- Credit Card Workflow Service

Handles the credit card status + auto-payback mechanism described
in §4.5 of the requirements.  When an expense is marked 'credit',
a payback expense is auto-generated in the next pay period.
"""

import logging

from app.extensions import db
from app.models.transaction import Transaction
from app.models.category import Category
from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.services import pay_period_service
from app.exceptions import NotFoundError, ValidationError
from app.utils.log_events import (
    BUSINESS,
    EVT_CREDIT_MARKED,
    EVT_CREDIT_UNMARKED,
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
    if txn.template is not None and txn.template.is_envelope:
        raise ValidationError(
            "This transaction uses individual purchase tracking. "
            "Mark individual entries as credit instead of the whole transaction."
        )

    credit_id = ref_cache.status_id(StatusEnum.CREDIT)
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

    # Idempotency: if already credited with existing payback, return it.
    if txn.status_id == credit_id:
        existing_payback = (
            db.session.query(Transaction)
            .filter_by(credit_payback_for_id=txn.id)
            .first()
        )
        if existing_payback:
            return existing_payback

    # Only projected transactions can be newly marked as credit.
    if txn.status_id != projected_id:
        raise ValidationError(
            f"Cannot mark a '{txn.status.name}' transaction as credit. "
            "Only projected transactions can be marked as credit."
        )

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

    # Update the original transaction's status.
    txn.status_id = credit_id
    # ``Transaction.status`` is loaded eagerly via ``lazy="joined"``
    # so the locking SELECT at the top of this function (and
    # ``_get_owned_transaction`` in the route) populated the cached
    # relationship with the *pre-update* Status row.  SQLAlchemy
    # does not auto-refresh many-to-one relationships when only the
    # FK column is rewritten -- without this expire, downstream code
    # (test assertions, the post-flush cell render in routes that
    # do not use ``expire_on_commit``) would observe the stale
    # ``Projected`` Status object even though ``status_id`` already
    # points to ``Credit``.  Expiring the single attribute is cheap:
    # the next access lazy-loads the matching ref row.
    db.session.expire(txn, ["status"])

    # Find or create the CC Payback category for this user.
    from app.models.pay_period import PayPeriod  # pylint: disable=import-outside-toplevel
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

    # Create the payback transaction.
    payback = Transaction(
        account_id=txn.account_id,
        template_id=None,  # Ad-hoc, not from a template.
        pay_period_id=next_period.id,
        scenario_id=txn.scenario_id,
        status_id=projected_id,
        name=f"CC Payback: {txn.name}",
        category_id=category.id,
        transaction_type_id=expense_type_id,
        estimated_amount=payback_amount,
        credit_payback_for_id=txn.id,
    )
    db.session.add(payback)
    db.session.flush()

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

    Args:
        transaction_id: The ID of the credited transaction.
        user_id: The ID of the user who owns the transaction.
            Defense-in-depth: ownership is verified via the
            transaction's pay period.

    Raises:
        NotFoundError: If the transaction doesn't exist or doesn't
            belong to *user_id*.
    """
    txn = db.session.get(Transaction, transaction_id)
    if txn is None:
        raise NotFoundError(f"Transaction {transaction_id} not found.")
    # Defense-in-depth: verify ownership via pay period.
    if txn.pay_period.user_id != user_id:
        raise NotFoundError(f"Transaction {transaction_id} not found.")

    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

    # Revert the original transaction's status.
    txn.status_id = projected_id

    # Delete the auto-generated payback transaction.
    payback = (
        db.session.query(Transaction)
        .filter_by(credit_payback_for_id=txn.id)
        .first()
    )
    deleted_payback_id = None
    if payback:
        deleted_payback_id = payback.id
        db.session.delete(payback)

    log_event(
        logger, logging.INFO, EVT_CREDIT_UNMARKED, BUSINESS,
        "Credit reverted to Projected; payback deleted",
        user_id=user_id,
        transaction_id=txn.id,
        deleted_payback_id=deleted_payback_id,
    )


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
