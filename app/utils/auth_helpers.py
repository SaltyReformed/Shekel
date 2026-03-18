"""
Shekel Budget App - Authorization Helpers

Reusable functions for verifying resource ownership. Used by route
handlers to ensure the current user can only access their own data.

Pattern A (direct user_id): Use get_or_404() for models with a
user_id column (Account, TransactionTemplate, SavingsGoal, etc.).

Pattern B (indirect via parent): Use get_owned_via_parent() for
models scoped through a FK parent (Transaction via PayPeriod,
SalaryRaise via SalaryProfile, etc.).
"""

from flask_login import current_user

from app.extensions import db


def get_or_404(model, pk, user_id_field="user_id"):
    """Load a record by primary key and verify it belongs to the current user.

    Uses the model's direct ``user_id`` column (or a custom column
    name via *user_id_field*) to check ownership.

    Args:
        model: The SQLAlchemy model class to query.
        pk: The primary key value to look up.
        user_id_field: The column name on the model that holds the
            owning user's ID.  Defaults to ``"user_id"``.

    Returns:
        The model instance if found and owned by ``current_user``,
        or ``None`` otherwise.

    Note:
        The caller is responsible for handling a ``None`` return --
        typically by returning a 404 response or redirecting.
    """
    record = db.session.get(model, pk)
    if record is None:
        return None
    if getattr(record, user_id_field, None) != current_user.id:
        return None
    return record


def get_owned_via_parent(model, pk, parent_attr,
                         parent_user_id_attr="user_id"):
    """Load a record by PK and verify ownership through its parent.

    For models that lack a direct ``user_id`` column but are scoped
    through a FK parent (e.g. Transaction -> PayPeriod, SalaryRaise
    -> SalaryProfile), this function lazy-loads the parent and checks
    the parent's user_id.

    Args:
        model: The SQLAlchemy model class to query.
        pk: The primary key value to look up.
        parent_attr: The SQLAlchemy relationship attribute name on the
            model that points to the parent (e.g. ``"pay_period"``).
        parent_user_id_attr: The column name on the *parent* model
            that holds the owning user's ID.  Defaults to
            ``"user_id"``.

    Returns:
        The model instance if found and owned (via parent) by
        ``current_user``, or ``None`` otherwise.

    Examples::

        # Transaction -> PayPeriod.user_id
        get_owned_via_parent(Transaction, txn_id, "pay_period")

        # SalaryRaise -> SalaryProfile.user_id
        get_owned_via_parent(SalaryRaise, raise_id, "salary_profile")
    """
    record = db.session.get(model, pk)
    if record is None:
        return None
    parent = getattr(record, parent_attr, None)
    if parent is None:
        return None
    if getattr(parent, parent_user_id_attr, None) != current_user.id:
        return None
    return record
