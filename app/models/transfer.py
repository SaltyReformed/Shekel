"""
Shekel Budget App — Transfer Model (budget schema)

Tracks transfers between accounts (checking ↔ savings).
Schema created in Phase 1 for forward-compatibility; feature in Phase 4.
"""

from app.extensions import db


class Transfer(db.Model):
    """A transfer between two accounts within a pay period."""

    __tablename__ = "transfers"
    __table_args__ = (
        db.Index("idx_transfers_period_scenario", "pay_period_id", "scenario_id"),
        db.CheckConstraint(
            "from_account_id != to_account_id",
            name="ck_transfers_different_accounts",
        ),
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
    pay_period_id = db.Column(
        db.Integer, db.ForeignKey("budget.pay_periods.id"), nullable=False
    )
    scenario_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.scenarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    status_id = db.Column(
        db.Integer, db.ForeignKey("ref.statuses.id"), nullable=False
    )
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    def __repr__(self):
        return f"<Transfer ${self.amount} ({self.id})>"
