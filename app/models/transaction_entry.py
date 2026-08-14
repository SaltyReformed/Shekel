"""
Shekel Budget App -- Transaction Entry Model (budget schema)

An individual purchase recorded against a parent transaction.
Entry-capable transactions (those whose template has
is_envelope=True) accumulate entries that determine the remaining
budget and the checking balance impact.
"""

from app.extensions import db
from app.models.mixins import OptimisticLockMixin, TimestampMixin, UserScopedMixin


class TransactionEntry(UserScopedMixin, OptimisticLockMixin, TimestampMixin, db.Model):
    """An individual purchase recorded against a parent transaction.

    Entries accumulate against the parent transaction's estimated amount.
    The sum of all entries determines the remaining budget and the
    checking balance impact for entry-capable transactions.

    **A purchase carries TWO days, and the second one is not decoration.**
    They answer different questions and the app needs both, exactly as
    ``cash_ledger.CashSourceFact`` carries a cash clock beside a budget clock
    and a loan payment carries a ``due_date`` beside its pay period.  A single
    ``entry_date`` carried both until 2026-08-01 and that ambiguity was the
    root defect ruling R-M and ruling R-DH (e) were fighting over: R-M defined
    it as the day the purchase happened (so never in the future), R-DH (e) as
    the day the money hit the account (one to two days later for a debit
    card).  Both are right about their own fact.  See
    ``docs/audits/balance_architecture/archive/anchor_settle_partition.md``.

    Columns:
        transaction_id  -- The parent transaction this entry belongs to.
        user_id         -- The user who created the entry (owner or companion).
        amount          -- Positive purchase amount (CHECK > 0).
        description     -- Short description of the purchase (e.g. "Kroger").
        purchased_on    -- The day the purchase was MADE (defaults to today).
                           Never after the user's today -- ruling R-M, refused
                           at both write doors by
                           ``entry_service._reject_future_purchase_date``.
                           This is the BUDGET clock: remaining-budget
                           consumption, the out-of-period warning and the
                           entry list's ordering all read it.
        settled_on      -- The day the bank TOOK the money, recorded only when
                           the user has seen it on a statement.  NULLABLE, and
                           NULL means "not observed to have posted" -- the
                           conservative answer, under which the envelope keeps
                           holding the whole budget back.  This is the CASH
                           clock, and the only column the reconciliation
                           question turns on: an entry is reconciled iff
                           ``settled_on`` is on or before the latest day its
                           account has asserted a balance for
                           (``AccountAnchorHistory.observed_on``) -- ruling
                           R-DH (d), evaluated at READ time.  Meaningful only
                           for debit entries; a credit purchase never touches
                           checking (it flows through its CC Payback sibling),
                           so the reservation ignores this column for one.
                           CHECK ``settled_on >= purchased_on``: money cannot
                           leave the account before it was spent.  No upper
                           bound -- any "at most N days ahead" rule would be an
                           unjustifiable constant, and a wrong forward date is
                           visible on the row and self-corrects at the next
                           true-up.
        credit_payback_id -- FK to the CC Payback transaction created for
                             this entry (SET NULL on payback deletion).

    **The stored ``is_cleared`` boolean this replaced is DELETED** (ruling
    R-DH (d), migration ``d7c1f4a9e603``).  It was written as a side effect of
    the anchor true-up -- a bulk UPDATE over every entry dated on or before the
    SERVER's today -- so whether a purchase counted as reconciled was decided
    by the order two buttons were pressed: record then true up and it cleared,
    true up then record and it never did.  A derived answer cannot go stale and
    cannot disagree with the balance walk, which answers the same question
    about settled transactions with the same predicate.
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
        # Money cannot leave the account before it was spent.  The only bound
        # on ``settled_on`` -- see the class docstring for why there is no
        # upper one.
        db.CheckConstraint(
            "settled_on IS NULL OR settled_on >= purchased_on",
            name="ck_transaction_entries_settled_not_before_purchase",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transactions.id", ondelete="CASCADE"),
        nullable=False,
    )
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    purchased_on = db.Column(
        db.Date, nullable=False, server_default=db.text("CURRENT_DATE"),
    )
    # Nullable BY DESIGN, and the NULL is a fact rather than a gap: it means
    # the user has not seen this purchase on a statement yet, so the engine
    # treats it as still outstanding.  Filling it with a default would be
    # storing a guess where a read-time rule can at least be seen.
    settled_on = db.Column(db.Date)
    is_credit = db.Column(
        db.Boolean, nullable=False, default=False, server_default="false",
    )
    credit_payback_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transactions.id", ondelete="SET NULL"),
    )
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

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
