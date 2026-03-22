"""
Shekel Budget App -- Transfer Template Model (budget schema)

A template defines a recurring transfer between accounts (e.g. "Monthly
savings contribution") along with its recurrence rule and default amount.
The transfer recurrence engine uses templates to auto-generate Transfer
rows into future pay periods.
"""

from app.extensions import db


class TransferTemplate(db.Model):
    """Blueprint for a recurring transfer between two accounts."""

    __tablename__ = "transfer_templates"
    __table_args__ = (
        db.Index("idx_transfer_templates_user", "user_id"),
        db.CheckConstraint(
            "from_account_id != to_account_id",
            name="ck_transfer_templates_different_accounts",
        ),
        db.CheckConstraint(
            "default_amount > 0",
            name="ck_transfer_templates_positive_amount",
        ),
        db.UniqueConstraint("user_id", "name", name="uq_transfer_templates_user_name"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id"), nullable=False
    )
    to_account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id"), nullable=False
    )
    recurrence_rule_id = db.Column(
        db.Integer, db.ForeignKey("budget.recurrence_rules.id")
    )
    name = db.Column(db.String(200), nullable=False)
    default_amount = db.Column(db.Numeric(12, 2), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    # Relationships
    from_account = db.relationship(
        "Account", foreign_keys=[from_account_id], lazy="joined"
    )
    to_account = db.relationship(
        "Account", foreign_keys=[to_account_id], lazy="joined"
    )
    recurrence_rule = db.relationship("RecurrenceRule", lazy="joined")
    transfers = db.relationship(
        "Transfer", back_populates="template", lazy="select"
    )

    def __repr__(self):
        return f"<TransferTemplate '{self.name}' ${self.default_amount}>"
