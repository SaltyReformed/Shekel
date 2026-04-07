"""
Shekel Budget App -- Archive and Delete History Helpers

Provides history-detection functions used by the unified delete/archive
pattern across transaction templates, transfer templates, accounts,
and categories.  Each function answers: "Does this entity have
Paid/Settled history that prevents permanent deletion?"

These functions are pure queries -- they do not perform mutations.
"""

from app.extensions import db
from app import ref_cache
from app.enums import StatusEnum


def template_has_paid_history(template_id: int) -> bool:
    """Check if a transaction template has any Paid or Settled transactions.

    Args:
        template_id: The TransactionTemplate.id to check.

    Returns:
        True if at least one linked transaction has Paid or Settled
        status and is not soft-deleted.
    """
    from app.models.transaction import Transaction  # pylint: disable=import-outside-toplevel

    paid_id = ref_cache.status_id(StatusEnum.DONE)
    settled_id = ref_cache.status_id(StatusEnum.SETTLED)

    return db.session.query(
        db.session.query(Transaction).filter(
            Transaction.template_id == template_id,
            Transaction.status_id.in_([paid_id, settled_id]),
            Transaction.is_deleted.is_(False),
        ).exists()
    ).scalar()


def transfer_template_has_paid_history(template_id: int) -> bool:
    """Check if a transfer template has any Paid or Settled transfers.

    Args:
        template_id: The TransferTemplate.id to check.

    Returns:
        True if at least one linked transfer has Paid or Settled
        status and is not soft-deleted.
    """
    from app.models.transfer import Transfer  # pylint: disable=import-outside-toplevel

    paid_id = ref_cache.status_id(StatusEnum.DONE)
    settled_id = ref_cache.status_id(StatusEnum.SETTLED)

    return db.session.query(
        db.session.query(Transfer).filter(
            Transfer.transfer_template_id == template_id,
            Transfer.status_id.in_([paid_id, settled_id]),
            Transfer.is_deleted.is_(False),
        ).exists()
    ).scalar()


def account_has_history(account_id: int) -> bool:
    """Check if an account has any non-deleted transactions.

    Unlike template history checks, this does NOT filter by status.
    Any non-deleted transaction means the account has history.  This is
    intentionally stricter than the template functions because account
    deletion would cascade to all related financial records -- even
    Projected transactions represent user-entered data worth preserving.

    Args:
        account_id: The Account.id to check.

    Returns:
        True if the account has any non-deleted transaction history.
    """
    from app.models.transaction import Transaction  # pylint: disable=import-outside-toplevel

    return db.session.query(
        db.session.query(Transaction).filter(
            Transaction.account_id == account_id,
            Transaction.is_deleted.is_(False),
        ).exists()
    ).scalar()


def category_has_usage(category_id: int, user_id: int) -> bool:
    """Check if a category is in use by templates or transactions.

    Performs a two-part check: (1) any TransactionTemplate with matching
    category_id and user_id, and (2) any Transaction with matching
    category_id joined to PayPeriod filtered by user_id.  Short-circuits
    after templates if found, avoiding the more expensive transaction join.

    The user_id scoping is critical -- categories are user-scoped, and
    the check must not cross user boundaries.

    Args:
        category_id: The Category.id to check.
        user_id: The user who owns the category (for ownership scoping).

    Returns:
        True if any templates or transactions reference this category
        for the given user.
    """
    from app.models.transaction_template import TransactionTemplate  # pylint: disable=import-outside-toplevel
    from app.models.transaction import Transaction  # pylint: disable=import-outside-toplevel
    from app.models.pay_period import PayPeriod  # pylint: disable=import-outside-toplevel

    # Check templates first -- cheap query with direct user_id column.
    has_templates = db.session.query(
        db.session.query(TransactionTemplate).filter_by(
            category_id=category_id, user_id=user_id,
        ).exists()
    ).scalar()

    if has_templates:
        return True

    # Check transactions -- requires join through PayPeriod for user scoping.
    return db.session.query(
        db.session.query(Transaction)
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(
            PayPeriod.user_id == user_id,
            Transaction.category_id == category_id,
        ).exists()
    ).scalar()
