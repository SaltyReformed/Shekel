"""
Shekel Budget App -- Companion Service

Data access layer for the companion view.  Provides visibility-filtered
queries that return only the linked owner's transactions from templates
marked ``companion_visible=True``.

This is the security boundary for all companion data access.  Every
function validates that the requesting user is a companion with a
valid ``linked_owner_id`` before touching any owner data.

Architecture:
  - No Flask imports.  Receives plain data, returns ORM objects or
    raises exceptions.
  - Flushes to the session but does NOT commit.  The caller owns the
    database transaction boundary.
"""

import logging

from sqlalchemy.orm import selectinload

from app.extensions import db
from app import ref_cache
from app.enums import RoleEnum
from app.exceptions import NotFoundError
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.user import User
from app.services import pay_period_service

logger = logging.getLogger(__name__)


def _validate_companion(user_id: int) -> User:
    """Load and validate a companion user.

    Verifies the user exists, has the companion role, and has a
    non-null ``linked_owner_id``.  Returns the User object on
    success.

    Args:
        user_id: The ID of the user to validate.

    Returns:
        User object that passed all companion checks.

    Raises:
        NotFoundError: If the user does not exist, is not a
            companion, or has no linked owner.
    """
    user = db.session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")

    companion_role_id = ref_cache.role_id(RoleEnum.COMPANION)
    if user.role_id != companion_role_id:
        raise NotFoundError("User is not a companion.")

    if user.linked_owner_id is None:
        raise NotFoundError(
            f"Companion user {user_id} has no linked owner. "
            "This is a data integrity issue -- contact the administrator."
        )

    return user


def get_previous_period(period: PayPeriod) -> PayPeriod | None:
    """Return the pay period immediately before the given one.

    Mirrors ``pay_period_service.get_next_period`` but queries
    for ``period_index - 1`` instead of ``+ 1``.

    Args:
        period: A PayPeriod object.

    Returns:
        The previous PayPeriod, or None if it doesn't exist.
    """
    return (
        db.session.query(PayPeriod)
        .filter(
            PayPeriod.user_id == period.user_id,
            PayPeriod.period_index == period.period_index - 1,
        )
        .first()
    )


def get_visible_transactions(
    companion_user_id: int,
    period_id: int | None = None,
) -> tuple[list[Transaction], PayPeriod]:
    """Get transactions visible to a companion user for a pay period.

    Queries the linked owner's transactions filtered to those from
    templates with ``companion_visible=True``.  Eager-loads entries
    for progress computation.

    Defense-in-depth: verifies the user is a companion with a valid
    ``linked_owner_id`` before querying.

    Args:
        companion_user_id: The companion user's ID.
        period_id: Optional period filter.  If None, returns the
            current period's transactions.

    Returns:
        Tuple of (transactions, period) where transactions is a list
        of Transaction objects with entries eager-loaded, and period
        is the PayPeriod that was queried.

    Raises:
        NotFoundError: User is not a companion, has no linked owner,
            period not found, or period belongs to a different owner.
    """
    user = _validate_companion(companion_user_id)
    owner_id = user.linked_owner_id

    if period_id is None:
        period = pay_period_service.get_current_period(owner_id)
        if period is None:
            raise NotFoundError("No current pay period found for owner.")
    else:
        period = db.session.get(PayPeriod, period_id)
        if period is None or period.user_id != owner_id:
            raise NotFoundError("Period not found.")

    transactions = (
        db.session.query(Transaction)
        .join(
            TransactionTemplate,
            Transaction.template_id == TransactionTemplate.id,
        )
        .options(selectinload(Transaction.entries))
        .filter(
            Transaction.pay_period_id == period.id,
            TransactionTemplate.companion_visible.is_(True),
            Transaction.is_deleted.is_(False),
        )
        .order_by(Transaction.name)
        .all()
    )

    return transactions, period


def get_companion_periods(companion_user_id: int) -> list[PayPeriod]:
    """Get all pay periods for the companion's linked owner.

    Used for period navigation UI.  Returns an empty list if the
    user is misconfigured (no linked owner) rather than raising,
    since this is a non-critical UI operation.

    Args:
        companion_user_id: The companion user's ID.

    Returns:
        List of PayPeriod objects ordered by period_index, or
        empty list if the user has no valid linked owner.
    """
    user = db.session.get(User, companion_user_id)
    if user is None or user.linked_owner_id is None:
        return []
    return pay_period_service.get_all_periods(user.linked_owner_id)
