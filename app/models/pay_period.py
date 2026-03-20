"""
Shekel Budget App — Pay Period Model (budget schema)

Auto-generated biweekly date ranges that anchor every transaction
to a specific paycheck.
"""

from app.extensions import db


class PayPeriod(db.Model):
    """A single pay period defined by start_date (payday) and end_date."""

    __tablename__ = "pay_periods"
    __table_args__ = (
        db.UniqueConstraint("user_id", "start_date", name="uq_pay_periods_user_start"),
        db.Index("idx_pay_periods_user_index", "user_id", "period_index"),
        db.CheckConstraint("start_date < end_date", name="ck_pay_periods_date_order"),
        db.CheckConstraint("period_index >= 0", name="ck_pay_periods_positive_index"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    period_index = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())

    # Relationships — transactions loaded via back_populates on Transaction
    transactions = db.relationship(
        "Transaction", back_populates="pay_period", lazy="select"
    )

    @property
    def label(self):
        """Human-readable label, e.g. '02/21 – 03/06' or '12/26/26 – 01/08/27'."""
        if self.start_date.year != self.end_date.year:
            return (
                f"{self.start_date.strftime('%m/%d/%y')} – "
                f"{self.end_date.strftime('%m/%d/%y')}"
            )
        return f"{self.start_date.strftime('%m/%d')} – {self.end_date.strftime('%m/%d')}"

    def __repr__(self):
        return f"<PayPeriod {self.start_date} idx={self.period_index}>"
