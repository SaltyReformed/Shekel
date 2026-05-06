"""
Shekel Budget App -- Transfer Model (budget schema)

Tracks transfers between accounts (checking ↔ savings) within pay periods.
Supports both template-generated recurring transfers and ad-hoc one-time transfers.
"""

from decimal import Decimal

from app.extensions import db
from app.models.mixins import TimestampMixin


class Transfer(TimestampMixin, db.Model):
    """A transfer between two accounts within a pay period.

    Optimistic locking: ``version_id`` is the SQLAlchemy
    ``version_id_col`` for the row.  Every ORM-emitted UPDATE or
    DELETE is narrowed to ``WHERE id = ? AND version_id = ?`` and
    the stored value is atomically incremented; concurrent
    mutations race for the bump and the loser raises
    :class:`sqlalchemy.orm.exc.StaleDataError`.  The transfer
    service propagates parent-transfer mutations to both shadow
    transactions, so the parent's version pin protects the entire
    three-row write set even though the shadow rows carry their
    own ``version_id`` columns.  See commit C-18 of the 2026-04-15
    security remediation plan.
    """

    __tablename__ = "transfers"
    __table_args__ = (
        db.Index("idx_transfers_period_scenario", "pay_period_id", "scenario_id"),
        db.CheckConstraint(
            "from_account_id != to_account_id",
            name="ck_transfers_different_accounts",
        ),
        db.CheckConstraint("amount > 0", name="ck_transfers_positive_amount"),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_transfers_version_id_positive",
        ),
        # One non-deleted, non-override transfer per template per period
        # per scenario.  Mirrors the relaxed transactions index: override
        # siblings may coexist with their rule-generated parent so
        # carry-forward can move unpaid recurring transfers into a target
        # period that already holds the next rule-generated instance.
        # transfer_recurrence.py already skips generation when an
        # is_override = TRUE transfer exists in the period.
        db.Index(
            "idx_transfers_template_period_scenario",
            "transfer_template_id", "pay_period_id", "scenario_id",
            unique=True,
            postgresql_where=db.text(
                "transfer_template_id IS NOT NULL "
                "AND is_deleted = FALSE "
                "AND is_override = FALSE"
            ),
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    to_account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    pay_period_id = db.Column(
        db.Integer, db.ForeignKey("budget.pay_periods.id", ondelete="RESTRICT"),
        nullable=False,
    )
    scenario_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.scenarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    status_id = db.Column(
        db.Integer, db.ForeignKey("ref.statuses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    transfer_template_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transfer_templates.id", ondelete="SET NULL"),
    )
    name = db.Column(db.String(200))
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    is_override = db.Column(db.Boolean, default=False, nullable=False)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)
    category_id = db.Column(
        db.Integer, db.ForeignKey("budget.categories.id", ondelete="SET NULL"),
    )
    notes = db.Column(db.Text)
    # Optimistic-locking version counter.  See class docstring and
    # commit C-18.  NOT NULL with server_default="1" so existing
    # production rows are filled at ALTER TABLE time and new rows
    # always start at version 1.
    version_id = db.Column(
        db.Integer, nullable=False, server_default="1",
    )

    # Optimistic locking: see class docstring.  Routes that mutate
    # Transfer (or call transfer_service helpers that flush) MUST
    # catch StaleDataError and surface a 409 / flash + redirect.
    __mapper_args__ = {"version_id_col": version_id}

    # Relationships
    template = db.relationship("TransferTemplate", back_populates="transfers")
    from_account = db.relationship(
        "Account", foreign_keys=[from_account_id], lazy="joined"
    )
    to_account = db.relationship(
        "Account", foreign_keys=[to_account_id], lazy="joined"
    )
    status = db.relationship("Status", lazy="joined")
    pay_period = db.relationship("PayPeriod")
    scenario = db.relationship("Scenario")
    category = db.relationship("Category", lazy="joined")

    @property
    def effective_amount(self):
        """Return the amount used in balance calculations.

        Transfers with an excluded status (Cancelled) contribute 0.
        """
        if self.status and self.status.excludes_from_balance:
            return Decimal("0")
        return self.amount

    def __repr__(self):
        return f"<Transfer '{self.name}' ${self.amount} ({self.id})>"
