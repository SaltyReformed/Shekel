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
    ``(group_name, item_name)``.  Archived categories
    (``is_active = False``) are excluded because they are not
    selectable targets for new rows.

    Args:
        user_id: ``auth.users.id`` of the owner whose categories to list.

    Returns:
        The owner's active :class:`Category` rows, ordered by
        ``group_name`` then ``item_name``.
    """
    return (
        db.session.query(Category)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(Category.group_name, Category.item_name)
        .all()
    )
