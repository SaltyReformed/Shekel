"""
Shekel Budget App — Savings Goal Model (budget schema)

Tracks savings targets with auto-calculated contribution amounts.
"""

from app.extensions import db


class SavingsGoal(db.Model):
    """A savings goal with target amount, target date, and contribution plan."""

    __tablename__ = "savings_goals"
    __table_args__ = (
        db.CheckConstraint("target_amount > 0", name="ck_savings_goals_positive_target"),
        db.CheckConstraint(
            "contribution_per_period IS NULL OR contribution_per_period > 0",
            name="ck_savings_goals_positive_contribution",
        ),
        db.UniqueConstraint(
            "user_id", "account_id", "name",
            name="uq_savings_goals_user_acct_name",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = db.Column(db.String(100), nullable=False)
    target_amount = db.Column(db.Numeric(12, 2), nullable=False)
    target_date = db.Column(db.Date)
    contribution_per_period = db.Column(db.Numeric(12, 2))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    # Relationships
    account = db.relationship("Account", lazy="joined")

    def __repr__(self):
        return f"<SavingsGoal '{self.name}' target=${self.target_amount}>"
