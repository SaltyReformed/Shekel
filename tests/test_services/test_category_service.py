"""
Unit tests for ``app.services.category_service``.

Pins the shared category-picker query contract so the form dropdowns
(transaction-template, transfer-template, transfer full-edit) stay
consistent with the categories settings page.
"""

from app.models.category import Category
from app.services.category_service import list_active_categories


class TestListActiveCategories:
    """``list_active_categories`` ordering and active-only filtering."""

    def test_orders_by_sort_order_before_name(self, db, seed_user):
        """deep-quality-hunt #66: the picker orders by sort_order first.

        The query used to order by ``(group_name, item_name)`` only,
        ignoring the user-assignable ``sort_order`` that the categories
        settings page honors -- so a user who reordered categories saw
        one order there and a different (alphabetical) order in every
        form dropdown.  Give three categories a ``sort_order`` that is
        the REVERSE of their alphabetical group order and assert the
        returned sequence follows sort_order, not the name.
        """
        user_id = seed_user["user"].id
        # Alphabetical by group: Apple < Mango < Zebra.  Assign
        # sort_order (100/101/102, above the seeded 0-23 range to avoid
        # collisions) so the intended display order is the reverse.
        db.session.add_all([
            Category(user_id=user_id, group_name="Zebra",
                     item_name="z", sort_order=100),
            Category(user_id=user_id, group_name="Mango",
                     item_name="m", sort_order=101),
            Category(user_id=user_id, group_name="Apple",
                     item_name="a", sort_order=102),
        ])
        db.session.flush()

        ours = [
            c.group_name for c in list_active_categories(user_id)
            if c.group_name in {"Apple", "Mango", "Zebra"}
        ]
        # sort_order 100,101,102 -> Zebra, Mango, Apple (NOT alphabetical,
        # which would be Apple, Mango, Zebra).
        assert ours == ["Zebra", "Mango", "Apple"]

    def test_excludes_archived_categories(self, db, seed_user):
        """Archived (is_active=False) categories are not returned."""
        user_id = seed_user["user"].id
        db.session.add(Category(
            user_id=user_id, group_name="Archived",
            item_name="Gone", is_active=False, sort_order=200,
        ))
        db.session.flush()

        groups = {c.group_name for c in list_active_categories(user_id)}
        assert "Archived" not in groups
