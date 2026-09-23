"""
Shekel Budget App -- Savings Goal Model (budget schema)

Tracks savings targets with auto-calculated contribution amounts.
Supports two goal modes:

    Fixed              -- target_amount is a user-specified dollar value.
    Income-Relative    -- target is computed on read as
                          income_multiplier * net_pay_per_unit.
                          target_amount is NULL for these goals.

A goal on a DEBT (its account's category is Liability) is a milestone to get
UNDER rather than a balance to reach (plan step credit_card:CC-5-5d, rulings
R-CC69..R-CC73): Fixed mode only, a target below what the debt owes when it is
saved, and ``$0.00`` allowed.  Nothing on this row says which kind a goal is;
the account does.  ``start_owed`` is set only for a goal created on a card or
other non-loan debt (ruling R-CC91), and it cannot come to disagree with the
account: the account's type cannot change kind under an active goal (R-CC87,
R-CC91), and no goal is set on a loan type before its terms exist (R-CC93).
"""

from app.extensions import db
from app.models.mixins import (
    AccountScopedMixin,
    IsActiveMixin,
    OptimisticLockMixin,
    TimestampMixin,
    UserScopedMixin,
)


class SavingsGoal(
    UserScopedMixin, AccountScopedMixin, IsActiveMixin, OptimisticLockMixin,
    TimestampMixin, db.Model,
):
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

    Optimistic locking: see :class:`Transaction` for the
    ``version_id_col`` contract.  Concurrent goal edits race for the
    bump; the loser raises ``StaleDataError`` and the route surfaces
    a flash + redirect.  See commit C-18 of the 2026-04-15 security
    remediation plan.
    """

    __tablename__ = "savings_goals"
    __table_args__ = (
        # ``>= 0`` since plan step credit_card:CC-5-5d (ruling R-CC72): a
        # DEBT goal may target $0.00 (paid off).  A savings goal's target must
        # still be above zero, which the table cannot say -- a goal is a debt
        # goal by its account's category, two tables away -- so the one goal
        # door (``app.services.savings_goal_door``) refuses it there.
        db.CheckConstraint(
            "target_amount >= 0",
            name="ck_savings_goals_nonnegative_target",
        ),
        db.CheckConstraint(
            "contribution_per_period IS NULL OR contribution_per_period > 0",
            name="ck_savings_goals_positive_contribution",
        ),
        db.CheckConstraint(
            "start_owed IS NULL OR start_owed > 0",
            name="ck_savings_goals_positive_start_owed",
        ),
        db.CheckConstraint(
            "income_multiplier IS NULL OR income_multiplier > 0",
            name="ck_savings_goals_multiplier_positive",
        ),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_savings_goals_version_id_positive",
        ),
        db.UniqueConstraint(
            "user_id", "account_id", "name",
            name="uq_savings_goals_user_acct_name",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    target_amount = db.Column(db.Numeric(12, 2), nullable=True)
    target_date = db.Column(db.Date)
    contribution_per_period = db.Column(db.Numeric(12, 2))
    # What a NON-LOAN debt's /savings tile showed it owing when this goal was
    # created (plan step credit_card:CC-5-5d, ruling R-CC91, refining
    # R-CC71): recorded by the goal door, because that tile values the debt at
    # its pay period's END and a later re-read would take in everything recorded
    # in the rest of that period.  NULL for a savings goal (no start) and for a
    # goal on a CONFIGURED loan, whose tile reads the day and whose start is
    # therefore re-read from the books.
    start_owed = db.Column(db.Numeric(12, 2), nullable=True)
    # is_active: from IsActiveMixin.

    # Income-relative goal columns (5.4-2).
    # goal_mode_id defaults to Fixed (ID 1) so existing goals are unaffected.
    # F-073 / C-43: ondelete=RESTRICT was missing on these ref-table
    # FKs.  The 4f2d894216ad migration created the constraints with
    # the convention-matching ``fk_savings_goals_<column>`` names but
    # left the ondelete clause unset (PostgreSQL implicit NO ACTION).
    # The C-43 migration recreates each FK with ondelete=RESTRICT so
    # the catalog enforces the same "ref rows can never be deleted
    # while in use" invariant the application layer already relies
    # on; the model declarations below render the same shape so
    # ``db.create_all()`` paths (test bootstrap, sandbox tooling)
    # converge on the post-C-43 schema.
    goal_mode_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.goal_modes.id",
            name="fk_savings_goals_goal_mode_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        default=1,
        server_default="1",
    )
    income_unit_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.income_units.id",
            name="fk_savings_goals_income_unit_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    income_multiplier = db.Column(
        db.Numeric(8, 2),
        nullable=True,
    )
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

    # Relationships
    account = db.relationship("Account", lazy="joined")
    goal_mode = db.relationship("GoalMode", lazy="joined")
    income_unit = db.relationship("IncomeUnit", lazy="joined")

    def __repr__(self):
        return f"<SavingsGoal '{self.name}' target=${self.target_amount}>"
