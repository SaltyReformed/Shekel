"""
Shekel Budget App -- Transaction Entry Model (budget schema)

An individual purchase recorded against a parent transaction.
Entry-capable transactions (those whose template has
is_envelope=True) accumulate entries that determine the remaining
budget and the checking balance impact.
"""

from app.extensions import db
from app.models.mixins import TimestampMixin


class TransactionEntry(TimestampMixin, db.Model):
    """An individual purchase recorded against a parent transaction.

    Entries accumulate against the parent transaction's estimated amount.
    The sum of all entries determines the remaining budget and the
    checking balance impact for entry-capable transactions.

    Columns:
        transaction_id  -- The parent transaction this entry belongs to.
        user_id         -- The user who created the entry (owner or companion).
        amount          -- Positive purchase amount (CHECK > 0).
        description     -- Short description of the purchase (e.g. "Kroger").
        entry_date      -- Date the purchase occurred (defaults to today).
        is_credit       -- True if this entry was paid via credit card.
        is_cleared      -- True when this purchase is already reflected in
                           the current checking anchor balance.  New entries
                           default to False.  Flipped to True automatically
                           on a checking account true-up (see
                           app/routes/accounts.py::true_up) for past-dated
                           entries on projected parents, and can be toggled
                           manually per entry.  The balance calculator uses
                           this flag to avoid double-counting debit
                           purchases once the anchor has been reconciled
                           with the real bank balance.  Meaningful only for
                           debit entries -- ignored for credit entries,
                           which are handled via the CC Payback workflow.
        credit_payback_id -- FK to the CC Payback transaction created for
                             this entry (SET NULL on payback deletion).
    """

    __tablename__ = "transaction_entries"
    __table_args__ = (
        db.Index("idx_transaction_entries_txn_id", "transaction_id"),
        db.Index(
            "idx_transaction_entries_txn_credit",
            "transaction_id", "is_credit",
        ),
        db.CheckConstraint(
            "amount > 0",
            name="ck_transaction_entries_positive_amount",
        ),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_transaction_entries_version_id_positive",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transactions.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    entry_date = db.Column(
        db.Date, nullable=False, server_default=db.text("CURRENT_DATE"),
    )
    is_credit = db.Column(
        db.Boolean, nullable=False, default=False, server_default="false",
    )
    is_cleared = db.Column(
        db.Boolean, nullable=False, default=False, server_default="false",
    )
    credit_payback_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transactions.id", ondelete="SET NULL"),
    )
    # Optimistic-locking version counter.  See commit C-18 of the
    # 2026-04-15 security remediation plan.  NOT NULL with
    # server_default="1" so existing production rows are filled at
    # ALTER TABLE time and new rows always start at version 1.
    # Concurrent entry edits race for the bump; the loser raises
    # :class:`sqlalchemy.orm.exc.StaleDataError` and the entries
    # route surfaces a 409 conflict partial.
    version_id = db.Column(
        db.Integer, nullable=False, server_default="1",
    )

    # Optimistic locking: SQLAlchemy narrows ORM UPDATE/DELETE with
    # ``WHERE id = ? AND version_id = ?`` and atomically increments
    # version_id.  Routes that mutate TransactionEntry MUST catch
    # StaleDataError and surface a 409 conflict partial.
    __mapper_args__ = {"version_id_col": version_id}

    # Relationships
    transaction = db.relationship(
        "Transaction", foreign_keys=[transaction_id],
        back_populates="entries",
    )
    user = db.relationship("User", lazy="joined")
    credit_payback = db.relationship(
        "Transaction", foreign_keys=[credit_payback_id],
        lazy="select",
    )

    def __repr__(self):
        return f"<TransactionEntry '{self.description}' ${self.amount} ({self.id})>"
