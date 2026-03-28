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

logger = logging.getLogger(__name__)

# The category used for auto-generated credit card payback expenses.
CC_PAYBACK_GROUP = "Credit Card"
CC_PAYBACK_ITEM = "Payback"


def mark_as_credit(transaction_id, user_id):
    """Mark a transaction as 'credit' and auto-generate a payback expense.

    Steps:
      1. Verify ownership via the transaction's pay period.
      2. Set the transaction's status to 'credit'.
      3. Find or create the CC payback category.
      4. Find the next pay period.
      5. Create a payback expense in the next period linked to the original.

    Args:
        transaction_id: The ID of the transaction to mark as credit.
        user_id: The ID of the user who owns the transaction.
            Defense-in-depth: ownership is verified via the
            transaction's pay period even if the caller already
            checked at the route level.

    Returns:
        The newly created payback Transaction.

    Raises:
        NotFoundError:  If the transaction doesn't exist or doesn't
            belong to *user_id*.
        ValidationError: If the transaction is income (can't credit income).
    """
    txn = db.session.get(Transaction, transaction_id)
    if txn is None:
        raise NotFoundError(f"Transaction {transaction_id} not found.")
    # Defense-in-depth: verify ownership via pay period.
    if txn.pay_period.user_id != user_id:
        raise NotFoundError(f"Transaction {transaction_id} not found.")
    if txn.is_income:
        raise ValidationError("Cannot mark income as credit.")

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

    # Find or create the CC Payback category for this user.
    from app.models.pay_period import PayPeriod  # pylint: disable=import-outside-toplevel
    period = db.session.get(PayPeriod, txn.pay_period_id)

    category = _get_or_create_cc_category(user_id)

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

    logger.info(
        "Marked transaction %d as credit; created payback %d in period %d",
        txn.id, payback.id, next_period.id,
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
    if payback:
        db.session.delete(payback)
        logger.info("Deleted payback transaction %d for original %d", payback.id, txn.id)

    logger.info("Unmarked credit on transaction %d", txn.id)


def _get_or_create_cc_category(user_id):
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
