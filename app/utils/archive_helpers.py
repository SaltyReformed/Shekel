"""
Shekel Budget App -- Archive and Delete History Helpers

Provides history-detection functions used by the unified delete/archive
pattern across transaction templates, transfer templates, accounts,
and categories.  Each function answers: "Does this entity have settled
history that prevents permanent deletion?"

These functions are pure queries -- they do not perform mutations.

The transaction/transfer template predicates filter on the semantic
``Status.is_settled`` boolean column (audit finding CRIT-05 / E-22):
enumerating ``[Paid, Settled]`` by name or ID silently missed Received
-- the status assigned to every income paycheck on mark-done -- and
let a normal user permanently destroy real RECEIVED income history.
``is_settled`` is the single source of truth for "this transaction is
real money already exchanged" (Paid, Received, Settled all carry
``is_settled=True`` in ``ref_seeds.py``), so a boolean predicate
covers every current and future settled status without enumeration.
"""

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.ref import Status
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer


def template_has_paid_history(template_id: int) -> bool:
    """Check if a transaction template has any settled transactions.

    "Settled" is determined by the semantic ``Status.is_settled``
    boolean (Paid, Received, Settled in the current seed -- see
    ``ref_seeds.py``).  Enumerating status names or IDs here would
    silently miss any status added to the settled set in the
    future; the boolean column is the single source of truth.
    Audit reference: CRIT-05 / E-22 (the prior ``[DONE, SETTLED]``
    enumeration omitted RECEIVED and enabled irreversible RECEIVED
    income-history deletion).

    Args:
        template_id: The TransactionTemplate.id to check.

    Returns:
        True if at least one linked transaction has a settled status
        and is not soft-deleted.
    """

    return db.session.query(
        db.session.query(Transaction)
        .join(Status, Transaction.status_id == Status.id)
        .filter(
            Transaction.template_id == template_id,
            Status.is_settled.is_(True),
            Transaction.is_deleted.is_(False),
        ).exists()
    ).scalar()


def transfer_template_has_paid_history(template_id: int) -> bool:
    """Check if a transfer template has any settled transfers.

    Mirrors :func:`template_has_paid_history`: filters on the
    semantic ``Status.is_settled`` boolean so Received and any
    future settled status are covered without enumeration.  Audit
    reference: CRIT-05 / E-22.

    Args:
        template_id: The TransferTemplate.id to check.

    Returns:
        True if at least one linked transfer has a settled status
        and is not soft-deleted.
    """

    return db.session.query(
        db.session.query(Transfer)
        .join(Status, Transfer.status_id == Status.id)
        .filter(
            Transfer.transfer_template_id == template_id,
            Status.is_settled.is_(True),
            Transfer.is_deleted.is_(False),
        ).exists()
    ).scalar()


def account_has_history(account_id: int) -> bool:
    """Check if an account has any non-deleted transactions.

    Unlike the template history checks, this does NOT filter by
    status.  Any non-deleted transaction means the account has
    history.  This is intentionally stricter than the template
    functions because account deletion would cascade to all related
    financial records -- even Projected transactions represent
    user-entered data worth preserving.

    Args:
        account_id: The Account.id to check.

    Returns:
        True if the account has any non-deleted transaction history.
    """

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
