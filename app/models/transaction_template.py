"""
Shekel Budget App -- Transaction Template Model (budget schema)

A template defines a recurring income or expense (e.g. "Car Payment")
along with its recurrence rule and default amount.  The recurrence
engine uses templates to auto-generate Transaction rows into future
pay periods.
"""

from app.extensions import db
from app.models.mixins import TimestampMixin


class TransactionTemplate(TimestampMixin, db.Model):
    """Blueprint for a recurring income or expense line item."""

    __tablename__ = "transaction_templates"
    __table_args__ = (
        db.Index("idx_templates_user_type", "user_id", "transaction_type_id"),
        db.CheckConstraint("default_amount >= 0", name="ck_transaction_templates_nonneg_amount"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    category_id = db.Column(
        db.Integer, db.ForeignKey("budget.categories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    recurrence_rule_id = db.Column(
        db.Integer, db.ForeignKey("budget.recurrence_rules.id", ondelete="SET NULL"),
    )
    transaction_type_id = db.Column(
        db.Integer, db.ForeignKey("ref.transaction_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = db.Column(db.String(200), nullable=False)
    default_amount = db.Column(db.Numeric(12, 2), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    is_envelope = db.Column(
        db.Boolean, nullable=False, default=False, server_default="false",
    )
    companion_visible = db.Column(
        db.Boolean, nullable=False, default=False, server_default="false",
    )

    # Relationships
    account = db.relationship("Account", lazy="joined")
    category = db.relationship("Category", lazy="joined")
    recurrence_rule = db.relationship("RecurrenceRule", lazy="joined")
    transaction_type = db.relationship("TransactionType", lazy="joined")
    transactions = db.relationship(
        "Transaction", back_populates="template", lazy="select"
    )

    def __repr__(self):
        return f"<TransactionTemplate '{self.name}' ${self.default_amount}>"
