"""
Shekel Budget App -- Transaction Model (budget schema)

Each row in the budget grid is a Transaction: an income or expense
assigned to a specific pay period and scenario, with estimated and
actual amounts plus a status workflow.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app import ref_cache
from app.enums import TxnTypeEnum
from app.models.mixins import TimestampMixin


class Transaction(TimestampMixin, db.Model):
    """A single income or expense entry within a pay period.

    Optimistic locking: ``version_id`` is the SQLAlchemy
    ``version_id_col`` for the row.  Every ORM-emitted UPDATE or
    DELETE is automatically narrowed to ``WHERE id = ? AND
    version_id = ?`` and the stored value is incremented in the same
    statement.  Two concurrent requests that both load the row at
    version N race for the bump; the loser's WHERE matches zero
    rows, SQLAlchemy raises :class:`sqlalchemy.orm.exc.StaleDataError`,
    and the calling route returns 409 (HTMX endpoints) or
    flash + redirect (non-HTMX form posts).  See commit C-18 of the
    2026-04-15 security remediation plan.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        db.Index(
            "idx_transactions_period_scenario",
            "pay_period_id", "scenario_id",
        ),
        db.Index("idx_transactions_template", "template_id"),
        db.Index("idx_transactions_credit_payback", "credit_payback_for_id"),
        db.Index("idx_transactions_account", "account_id"),
        db.Index(
            "idx_transactions_transfer",
            "transfer_id",
            postgresql_where=db.text("transfer_id IS NOT NULL"),
        ),
        db.Index(
            "idx_transactions_due_date",
            "due_date",
            postgresql_where=db.text("due_date IS NOT NULL"),
        ),
        # One non-deleted, non-override transaction per template per
        # period per scenario.  Override siblings (is_override = TRUE)
        # may coexist with their rule-generated parent -- carry-forward
        # relies on this so a moved unpaid item lives alongside the
        # recurrence-engine-generated instance for the target period.
        # The recurrence engine in app/services/recurrence_engine.py
        # already skips generation when an is_override = TRUE row
        # exists in the period, so the rule-generated row remains
        # unique even with the relaxed index.
        db.Index(
            "idx_transactions_template_period_scenario",
            "template_id", "pay_period_id", "scenario_id",
            unique=True,
            postgresql_where=db.text(
                "template_id IS NOT NULL "
                "AND is_deleted = FALSE "
                "AND is_override = FALSE"
            ),
        ),
        db.CheckConstraint(
            "estimated_amount >= 0",
            name="ck_transactions_estimated_amount",
        ),
        db.CheckConstraint(
            "actual_amount IS NULL OR actual_amount >= 0",
            name="ck_transactions_actual_amount",
        ),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_transactions_version_id_positive",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    template_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transaction_templates.id", ondelete="SET NULL"),
    )
    pay_period_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.pay_periods.id", ondelete="CASCADE"),
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
    name = db.Column(db.String(200), nullable=False)
    category_id = db.Column(
        db.Integer, db.ForeignKey("budget.categories.id", ondelete="SET NULL"),
    )
    transaction_type_id = db.Column(
        db.Integer, db.ForeignKey("ref.transaction_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    estimated_amount = db.Column(db.Numeric(12, 2), nullable=False)
    actual_amount = db.Column(db.Numeric(12, 2))
    is_override = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)
    transfer_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transfers.id", ondelete="CASCADE"),
    )
    credit_payback_for_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transactions.id", ondelete="SET NULL"),
    )
    notes = db.Column(db.Text)
    due_date = db.Column(db.Date, nullable=True)
    paid_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # Optimistic-locking version counter.  See class docstring and
    # commit C-18.  NOT NULL with server_default="1" so existing
    # production rows are filled at ALTER TABLE time and new rows
    # always start at version 1.
    version_id = db.Column(
        db.Integer, nullable=False, server_default="1",
    )

    # Optimistic locking: SQLAlchemy will (a) issue
    # ``UPDATE ... WHERE id = ? AND version_id = ?`` for every flush
    # of a dirty Transaction, (b) atomically increment version_id in
    # the same statement, and (c) raise StaleDataError when rowcount
    # = 0.  Routes that mutate Transaction MUST catch StaleDataError
    # and surface a 409 / flash + redirect.  See app/routes/transactions.py.
    __mapper_args__ = {"version_id_col": version_id}

    # Relationships
    account = db.relationship("Account", lazy="joined")
    template = db.relationship("TransactionTemplate", back_populates="transactions")
    pay_period = db.relationship("PayPeriod", back_populates="transactions")
    scenario = db.relationship("Scenario")
    status = db.relationship("Status", lazy="joined")
    category = db.relationship("Category", lazy="joined")
    transaction_type = db.relationship("TransactionType", lazy="joined")
    transfer = db.relationship(
        "Transfer",
        backref=db.backref("shadow_transactions", passive_deletes=True),
        lazy="select",
    )
    credit_payback_for = db.relationship(
        "Transaction", remote_side="Transaction.id", foreign_keys=[credit_payback_for_id]
    )
    entries = db.relationship(
        "TransactionEntry", back_populates="transaction",
        foreign_keys="TransactionEntry.transaction_id",
        lazy="select", cascade="all, delete-orphan",
        order_by="TransactionEntry.entry_date",
    )

    @property
    def effective_amount(self):
        """Return the amount used in balance calculations.

        Priority order:
          1. is_deleted -> Decimal("0") (soft-deleted transactions contribute nothing)
          2. excludes_from_balance (Credit, Cancelled) -> Decimal("0")
          3. actual_amount if populated -> actual_amount
          4. fallback -> estimated_amount

        This property is the single source of truth for what amount a
        transaction contributes to balance projections, grid subtotals,
        and any other calculation context.  All active statuses (Projected,
        Paid, Received, etc.) prefer actual_amount when populated, ensuring
        that balance projections reflect reality as soon as the user enters
        a known actual on a still-projected transaction.
        """
        if self.is_deleted:
            return Decimal("0")
        if self.status and self.status.excludes_from_balance:
            return Decimal("0")
        # Use `is not None` -- NOT truthiness.  actual_amount=Decimal("0")
        # is a valid value (e.g., a waived fee) and must return 0, not
        # fall back to estimated_amount.
        return self.actual_amount if self.actual_amount is not None else self.estimated_amount

    @property
    def is_income(self):
        """True if this transaction is income."""
        return self.transaction_type_id == ref_cache.txn_type_id(TxnTypeEnum.INCOME)

    @property
    def is_expense(self):
        """True if this transaction is an expense."""
        return self.transaction_type_id == ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

    @property
    def days_until_due(self):
        """Days remaining until the due date, or None.

        Returns a positive integer for future due dates and a negative
        integer for overdue transactions.  Returns None when there is no
        due date or the transaction is already settled (no action needed).
        """
        if self.due_date is None:
            return None
        if self.status is not None and self.status.is_settled:
            return None
        return (self.due_date - date.today()).days

    @property
    def days_paid_before_due(self):
        """Days between due date and payment, or None.

        Positive means paid early, negative means paid late, zero means
        paid on the due date.  Returns None when either field is missing.
        """
        if self.due_date is None or self.paid_at is None:
            return None
        return (self.due_date - self.paid_at.date()).days

    def __repr__(self):
        return f"<Transaction '{self.name}' ${self.estimated_amount} ({self.id})>"
