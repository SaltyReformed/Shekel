"""
Shekel Budget App -- Savings Goal Model (budget schema)

Tracks savings targets with auto-calculated contribution amounts.
Supports two goal modes:

    Fixed              -- target_amount is a user-specified dollar value.
    Income-Relative    -- target is computed on read as
                          income_multiplier * net_pay_per_unit.
                          target_amount is NULL for these goals.
"""

from app.extensions import db


class SavingsGoal(db.Model):
    """A savings goal with target amount, target date, and contribution plan.

    Goal modes:

        Fixed (goal_mode_id -> ref.goal_modes 'Fixed'):
            target_amount is set directly by the user.
            income_unit_id and income_multiplier are NULL.

        Income-Relative (goal_mode_id -> ref.goal_modes 'Income-Relative'):
            income_unit_id and income_multiplier define the target as a
            multiple of net pay (in paychecks or months).  target_amount
            is NULL -- the resolved dollar target is calculated on read
            by the savings dashboard service.
    """

    __tablename__ = "savings_goals"
    __table_args__ = (
        db.CheckConstraint(
            "target_amount > 0",
            name="ck_savings_goals_positive_target",
        ),
        db.CheckConstraint(
            "contribution_per_period IS NULL OR contribution_per_period > 0",
            name="ck_savings_goals_positive_contribution",
        ),
        db.CheckConstraint(
            "income_multiplier IS NULL OR income_multiplier > 0",
            name="ck_savings_goals_multiplier_positive",
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
    target_amount = db.Column(db.Numeric(12, 2), nullable=True)
    target_date = db.Column(db.Date)
    contribution_per_period = db.Column(db.Numeric(12, 2))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    # Income-relative goal columns (5.4-2).
    # goal_mode_id defaults to Fixed (ID 1) so existing goals are unaffected.
    goal_mode_id = db.Column(
        db.Integer,
        db.ForeignKey("ref.goal_modes.id"),
        nullable=False,
        default=1,
        server_default="1",
    )
    income_unit_id = db.Column(
        db.Integer,
        db.ForeignKey("ref.income_units.id"),
        nullable=True,
    )
    income_multiplier = db.Column(
        db.Numeric(8, 2),
        nullable=True,
    )

    # Relationships
    account = db.relationship("Account", lazy="joined")
    goal_mode = db.relationship("GoalMode", lazy="joined")
    income_unit = db.relationship("IncomeUnit", lazy="joined")

    def __repr__(self):
        return f"<SavingsGoal '{self.name}' target=${self.target_amount}>"
