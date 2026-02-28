"""
Shekel Budget App — Recurrence Rule Model (budget schema)

Defines the pattern by which transactions are auto-generated into
future pay periods (every_period, monthly, annual, etc.).
"""

from app.extensions import db


class RecurrenceRule(db.Model):
    """A recurrence pattern attached to a transaction template."""

    __tablename__ = "recurrence_rules"
    __table_args__ = (
        db.CheckConstraint("interval_n > 0", name="ck_recurrence_rules_positive_interval"),
        db.CheckConstraint("offset_periods >= 0", name="ck_recurrence_rules_valid_offset"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    pattern_id = db.Column(
        db.Integer, db.ForeignKey("ref.recurrence_patterns.id"), nullable=False
    )
    # Used by 'every_n_periods': repeat every N periods.
    interval_n = db.Column(db.Integer, default=1)
    # Offset within the interval cycle (0 or 1).
    offset_periods = db.Column(db.Integer, default=0)
    # Used by 'monthly' and 'annual' patterns.
    day_of_month = db.Column(
        db.Integer,
        db.CheckConstraint(
            "day_of_month IS NULL OR (day_of_month >= 1 AND day_of_month <= 31)",
            name="ck_recurrence_rules_dom",
        ),
    )
    # Used by 'annual' pattern.
    month_of_year = db.Column(
        db.Integer,
        db.CheckConstraint(
            "month_of_year IS NULL OR (month_of_year >= 1 AND month_of_year <= 12)",
            name="ck_recurrence_rules_moy",
        ),
    )
    # Optional: the pay period where recurrence should begin.
    start_period_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.pay_periods.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())

    # Relationships
    pattern = db.relationship("RecurrencePattern", lazy="joined")
    start_period = db.relationship("PayPeriod", lazy="joined")

    def __repr__(self):
        return f"<RecurrenceRule id={self.id} pattern={self.pattern_id}>"
