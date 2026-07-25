"""
Shekel Budget App -- Recurrence Rule Model (budget schema)

Defines the pattern by which transactions are auto-generated into
future pay periods (every_period, monthly, annual, etc.).
"""

from app.extensions import db
from app.models.mixins import CreatedAtMixin, UserScopedMixin


class RecurrenceRule(UserScopedMixin, CreatedAtMixin, db.Model):
    """A recurrence pattern attached to a transaction template."""

    __tablename__ = "recurrence_rules"
    __table_args__ = (
        db.CheckConstraint("interval_n > 0", name="ck_recurrence_rules_positive_interval"),
        db.CheckConstraint("offset_periods >= 0", name="ck_recurrence_rules_valid_offset"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    pattern_id = db.Column(
        db.Integer, db.ForeignKey("ref.recurrence_patterns.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Used by 'every_n_periods': repeat every N periods.
    interval_n = db.Column(
        db.Integer, nullable=False, default=1, server_default=db.text("1"),
    )
    # Offset within the interval cycle (0 or 1).
    offset_periods = db.Column(
        db.Integer, nullable=False, default=0, server_default=db.text("0"),
    )
    # Used by 'monthly' and 'annual' patterns.
    day_of_month = db.Column(
        db.Integer,
        db.CheckConstraint(
            "day_of_month IS NULL OR (day_of_month >= 1 AND day_of_month <= 31)",
            name="ck_recurrence_rules_dom",
        ),
    )
    # Optional: the actual bill due day when it differs from the
    # pay-period scheduling day.  When NULL, day_of_month serves as
    # both the scheduling day and the due date.
    due_day_of_month = db.Column(
        db.Integer,
        db.CheckConstraint(
            "due_day_of_month IS NULL OR "
            "(due_day_of_month >= 1 AND due_day_of_month <= 31)",
            name="ck_recurrence_rules_due_dom",
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
    # Optional: the pay period where recurrence should begin.  A WEAK bound --
    # it seeds ``effective_from`` only when the caller passes none
    # (``recurrence_engine.resolve_generation_plan``), so a caller supplying its
    # own effective_from (``transfer_recurrence.regenerate_for_template``,
    # the unarchive path) silently bypasses it.  It is the form's "First
    # paycheck" affordance, NOT a validity bound; ``start_date`` below is the
    # bound.
    start_period_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.pay_periods.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Optional start date -- recurrence generates nothing whose period ends
    # before this date.  NULL means unbounded (no start), which is every rule
    # the user configures by hand.
    #
    # The SYMMETRIC partner of ``end_date`` below, and enforced the same way:
    # ``recurrence_engine.match_periods`` filters candidate periods on it
    # UNCONDITIONALLY, so -- unlike ``start_period_id`` -- no caller can bypass
    # it by supplying its own ``effective_from``.  Together the two columns are
    # the rule's validity window.
    #
    # Written only by ``loan_recurrence_sync.sync_recurring_payment_bounds``,
    # which derives it from the loan's FIRST CONTRACTUAL INSTALLMENT (plan step
    # C9a): a loan payment cannot precede the loan.  A payment generated before
    # origination is erased by the fold (it splits against a zero balance and
    # the origination anchor then resets over it), so it debits cash for a loan
    # that does not exist yet -- measured at $3,220.92 on a mortgage closing one
    # month out.
    start_date = db.Column(db.Date, nullable=True)
    # Optional end date -- recurrence stops generating after this date.
    # NULL means indefinite (no end).
    end_date = db.Column(db.Date, nullable=True)

    # Relationships
    pattern = db.relationship("RecurrencePattern", lazy="joined")
    start_period = db.relationship("PayPeriod", lazy="joined")

    def __repr__(self):
        return f"<RecurrenceRule id={self.id} pattern={self.pattern_id}>"
