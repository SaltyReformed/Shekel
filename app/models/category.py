"""
Shekel Budget App -- Category Model (budget schema)

Flat two-level category structure: group_name + item_name.
Example: group='Auto', item='Car Payment'.
"""

from app.extensions import db
from app.models.mixins import (
    CreatedAtMixin,
    IsActiveMixin,
    SortOrderMixin,
    UserScopedMixin,
)


class Category(UserScopedMixin, SortOrderMixin, IsActiveMixin, CreatedAtMixin, db.Model):
    """A budget category with a group and item name (two-level flat hierarchy)."""

    __tablename__ = "categories"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "group_name", "item_name",
            name="uq_categories_user_group_item",
        ),
        # The SUPERKEY ``fk_merchant_rules_category_owner`` targets, so a
        # merchant rule's category is provably its OWNER's (plan
        # step ``bank_import:X-f6a-3d``) -- the IDOR every create door in this
        # project probes for by hand, made unwritable instead.  It constrains
        # nothing on its own: ``id`` is already the primary key.
        # **Declared HERE as well as in the migration**, because a constraint
        # the model does not know about is one the next ``flask db migrate``
        # emits a DROP for.
        db.UniqueConstraint("id", "user_id", name="uq_categories_id_user"),
        db.Index("idx_categories_user_group", "user_id", "group_name"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    group_name = db.Column(db.String(100), nullable=False)
    item_name = db.Column(db.String(100), nullable=False)

    @property
    def display_name(self):
        """Full display label, e.g. 'Auto: Car Payment'."""
        return f"{self.group_name}: {self.item_name}"

    def __repr__(self):
        return f"<Category {self.group_name}: {self.item_name}>"
