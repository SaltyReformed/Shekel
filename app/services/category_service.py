"""
Shekel Budget App -- Category Service

Shared queries over ``budget.categories``.  Flask-isolated per the
project architecture rule (``CLAUDE.md`` Architecture section): takes
plain data (a user id), returns plain SQLAlchemy objects, never imports
``request``/``session``.  The caller owns the surrounding transaction.
"""

from app.extensions import db
from app.models.category import Category


def list_active_categories(user_id: int) -> list[Category]:
    """Return a user's active categories ordered for display dropdowns.

    Shared by the form routes that render a category picker (the
    transaction-template, transfer-template, and transfer full-edit
    forms) so the option list is consistently ordered by
    ``(sort_order, group_name, item_name)`` -- the user-assignable
    ``sort_order`` leads so the picker matches the order shown on the
    categories settings page (``settings._load_categories_context``
    orders the same rows that way) and mirrors the sibling
    ``account_service.list_active_accounts`` ordering for the same forms
    (deep-quality-hunt #66; the identical account defect was P-2).
    Archived categories (``is_active = False``) are excluded because
    they are not selectable targets for new rows.

    Args:
        user_id: ``auth.users.id`` of the owner whose categories to list.

    Returns:
        The owner's active :class:`Category` rows, ordered by
        ``sort_order``, then ``group_name``, then ``item_name``.
    """
    return (
        db.session.query(Category)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(Category.sort_order, Category.group_name, Category.item_name)
        .all()
    )
