"""
Shekel Budget App -- Account Models (budget schema)

Tracks checking and savings accounts with anchor balance history
for the true-up workflow.
"""

from app.extensions import db


class Account(db.Model):
    """A financial account (checking or savings) owned by a user."""

    __tablename__ = "accounts"
    __table_args__ = (
        db.UniqueConstraint("user_id", "name", name="uq_accounts_user_name"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_type_id = db.Column(
        db.Integer, db.ForeignKey("ref.account_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = db.Column(db.String(100), nullable=False)
    current_anchor_balance = db.Column(db.Numeric(12, 2))
    current_anchor_period_id = db.Column(
        db.Integer, db.ForeignKey("budget.pay_periods.id", ondelete="SET NULL"),
    )
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    # Relationships
    account_type = db.relationship("AccountType", lazy="joined")
    anchor_period = db.relationship("PayPeriod", foreign_keys=[current_anchor_period_id])
    anchor_history = db.relationship(
        "AccountAnchorHistory",
        back_populates="account",
        order_by="AccountAnchorHistory.created_at.desc()",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Account {self.name} ({self.id})>"


class AccountAnchorHistory(db.Model):
    """Audit trail of anchor balance true-ups for an account."""

    __tablename__ = "account_anchor_history"
    __table_args__ = (
        db.Index(
            "idx_anchor_history_account",
            "account_id",
            "created_at",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    pay_period_id = db.Column(
        db.Integer, db.ForeignKey("budget.pay_periods.id", ondelete="CASCADE"),
        nullable=False,
    )
    anchor_balance = db.Column(db.Numeric(12, 2), nullable=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())

    # Relationships
    account = db.relationship("Account", back_populates="anchor_history")
    pay_period = db.relationship("PayPeriod")

    def __repr__(self):
        return f"<AnchorHistory account={self.account_id} balance={self.anchor_balance}>"
