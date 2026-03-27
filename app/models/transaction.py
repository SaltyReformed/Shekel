"""
Shekel Budget App -- Transaction Model (budget schema)

Each row in the budget grid is a Transaction: an income or expense
assigned to a specific pay period and scenario, with estimated and
actual amounts plus a status workflow.
"""

from decimal import Decimal

from app.extensions import db


class Transaction(db.Model):
    """A single income or expense entry within a pay period."""

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
        # One non-deleted transaction per template per period per scenario.
        db.Index(
            "idx_transactions_template_period_scenario",
            "template_id", "pay_period_id", "scenario_id",
            unique=True,
            postgresql_where=db.text(
                "template_id IS NOT NULL AND is_deleted = FALSE"
            ),
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id"), nullable=False
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
        db.Integer, db.ForeignKey("ref.statuses.id"), nullable=False
    )
    name = db.Column(db.String(200), nullable=False)
    category_id = db.Column(
        db.Integer, db.ForeignKey("budget.categories.id")
    )
    transaction_type_id = db.Column(
        db.Integer, db.ForeignKey("ref.transaction_types.id"), nullable=False
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
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

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

    @property
    def effective_amount(self):
        """Return the amount used in balance calculations.

        - done / received: actual_amount if set, else estimated_amount
        - projected: estimated_amount
        - credit: 0 (excluded from checking balance)
        """
        if self.status and self.status.name in ("credit", "cancelled"):
            return Decimal("0")
        if self.status and self.status.name in ("done", "received"):
            return self.actual_amount if self.actual_amount is not None else self.estimated_amount
        return self.estimated_amount

    @property
    def is_income(self):
        """True if this transaction is income."""
        return self.transaction_type and self.transaction_type.name == "income"

    @property
    def is_expense(self):
        """True if this transaction is an expense."""
        return self.transaction_type and self.transaction_type.name == "expense"

    def __repr__(self):
        return f"<Transaction '{self.name}' ${self.estimated_amount} ({self.id})>"
