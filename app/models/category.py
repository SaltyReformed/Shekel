"""
Shekel Budget App -- Category Model (budget schema)

Flat two-level category structure: group_name + item_name.
Example: group='Auto', item='Car Payment'.
"""

from app.extensions import db


class Category(db.Model):
    """A budget category with a group and item name (two-level flat hierarchy)."""

    __tablename__ = "categories"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "group_name", "item_name",
            name="uq_categories_user_group_item",
        ),
        db.Index("idx_categories_user_group", "user_id", "group_name"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    group_name = db.Column(db.String(100), nullable=False)
    item_name = db.Column(db.String(100), nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default='true')
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now(),
    )

    @property
    def display_name(self):
        """Full display label, e.g. 'Auto: Car Payment'."""
        return f"{self.group_name}: {self.item_name}"

    def __repr__(self):
        return f"<Category {self.group_name}: {self.item_name}>"
