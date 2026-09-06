"""
Shekel Budget App -- Transaction Entry Model (budget schema)

An individual purchase recorded against a parent transaction.
Entry-capable transactions (those whose template has
is_envelope=True) accumulate entries that determine the remaining
budget and the checking balance impact.
"""

from app.extensions import db
from app.models.mixins import (
    OptimisticLockMixin,
    SettleDatedMixin,
    TimestampMixin,
    UserScopedMixin,
)


class TransactionEntry(
    UserScopedMixin,
    OptimisticLockMixin,
    SettleDatedMixin,
    TimestampMixin,
    db.Model,
):
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
        account_id      -- The account this purchase's cash leaves.  It IS the
                           parent's, and ``fk_transaction_entries_parent_account``
                           makes a disagreement unrepresentable rather than
                           merely unlikely.  See the column comment for why the
                           app stores what it could join for.
        user_id         -- The user who created the entry (owner or companion).
        amount          -- What the purchase cost, as a signed figure
                           (CHECK ``<> 0``).  POSITIVE for a purchase and
                           NEGATIVE for a REFUND, which is a merchant credit
                           filed back against this envelope rather than booked
                           as income (ruling **bank_import:R-II**).  See the
                           constraint below for why the bound is non-zero
                           rather than positive, and where positivity went.
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
        settled_day_basis_id -- WHICH KIND of day ``settled_on`` is: a day the
                           bank showed (``observed``), the day a balance was
                           asserted for and so an UPPER BOUND (``asserted``), or
                           the owner's own entry (``entered``).  Paired to
                           ``settled_on`` by a BICONDITIONAL check, so the two
                           are born and released together.  Plan step **X-az**,
                           :class:`app.enums.SettledDayBasisEnum`.
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
        # **A purchase worth nothing is not a purchase, and that is the WHOLE
        # of what this table has to say about the amount** (ruling
        # **bank_import:R-II**, migration ``b8e4c1f7a903``).  It was
        # ``amount > 0`` until 2026-08-31, and the name is kept because the
        # subject did not change -- only the answer.
        #
        # A NEGATIVE purchase is a REFUND: a merchant credit filed as a contra
        # against the envelope its merchant rule names, rather than as income
        # under a spending category.  The arithmetic was already sign-general
        # and was measured so before the constraint moved --
        # ``_posting_purchases._purchase_target`` at ``-28.29`` emits
        # ``{cash: +28.29, category: -28.29}`` with no branch, and
        # ``cash_ledger.settled_cash_leg``'s three terms are sums that net --
        # which is what made the old bound a FENCE rather than an invariant.
        #
        # **Positivity did not disappear, it moved to the door that owns it.**
        # "A typed negative is a typo" is a statement about a hand-entry form
        # composing a NEW purchase, so it lives on that form
        # (``EntryCreateSchema``, the add-purchase input) and NOT on the update
        # door, where the figure being edited may be a sign the BANK stated
        # (developer ruling 2026-08-31).  The non-zero rule is stated at the
        # service tier too (``entry_service._refusals._reject_zero_amount``),
        # so a caller meets a ``ValidationError`` rather than this
        # constraint's ``IntegrityError``; this is the backstop under both.
        db.CheckConstraint(
            "amount <> 0",
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
        # The SUPERKEY ``statement_match_members`` names to prove its own
        # ``account_id`` is this purchase's (plan step ``bank_import:X-f6a-2``).
        # It constrains nothing -- ``id`` is already the primary key -- and
        # exists only because PostgreSQL requires a UNIQUE over exactly the
        # referenced columns before a composite foreign key may target them.
        # The same construction, for the same reason, as
        # ``uq_transactions_id_account``.
        db.UniqueConstraint(
            "id", "account_id", name="uq_transaction_entries_id_account",
        ),
        # **This entry's account IS its parent's, guaranteed rather than
        # maintained** (plan step X-f3a-1).  The pair keys straight onto
        # ``uq_transactions_id_account``, so a row whose ``account_id`` differs
        # from its parent's cannot be written at all -- which is what lets
        # ``fk_transaction_entries_reconciled_by`` below scope a clearing link by
        # account without trusting any writer to remember.
        #
        # ``ON DELETE CASCADE`` matches the single-column ``transaction_id`` key
        # beside it, which stays as this relationship's declared join path: that
        # key is about the PARENT'S EXISTENCE and this one is about AGREEMENT,
        # and two keys over the same column cascading differently would make a
        # delete's outcome depend on which PostgreSQL evaluated.
        db.ForeignKeyConstraint(
            ["transaction_id", "account_id"],
            ["budget.transactions.id", "budget.transactions.account_id"],
            name="fk_transaction_entries_parent_account",
            ondelete="CASCADE",
        ),
        # WHICH STATEMENT showed this purchase, as a COMPOSITE key over the
        # account (ruling **R-FL**).  The transaction twin of
        # ``fk_transactions_reconciled_by``; see
        # ``app.models.transaction.Transaction`` for why a single-column key
        # cannot express the rule.
        db.ForeignKeyConstraint(
            ["account_id", "reconciled_by_id"],
            ["budget.account_anchor_history.account_id",
             "budget.account_anchor_history.id"],
            name="fk_transaction_entries_reconciled_by",
            ondelete="RESTRICT",
        ),
        db.Index("idx_transaction_entries_reconciled_by", "reconciled_by_id"),
        # A statement cannot have shown money that never moved: the link and the
        # posting day are one fact in two columns, and every door that clears
        # one releases the other (``entry_service.update_entry``).  This refuses
        # the pair a third writer would leave behind.
        db.CheckConstraint(
            "reconciled_by_id IS NULL OR settled_on IS NOT NULL",
            name="ck_transaction_entries_cleared_needs_settle_day",
        ),
        # A SETTLE DAY SAYS HOW IT IS KNOWN (plan step **X-az**, finding
        # **N-332**), and this is the transaction twin of
        # ``ck_transactions_settle_day_basis_pairing``; see
        # ``app.models.transaction.Transaction`` for why the pairing is a
        # BICONDITIONAL where the settled FIGURE's is a bare implication.
        #
        # **This table needs it for the same reason and gets it in the same
        # step, which the figure's basis did not.**  ``settled_basis_id`` lives
        # only on ``budget.transactions`` because a purchase stores no figure of
        # its own -- it IS the figure its parent's close is made of.  A purchase
        # does carry its own DAY, and all three kinds are written to it: the
        # bank's day by ``statement_match``, a balance assertion's upper bound
        # by ``reconcile_service._purchases``, and the owner's own by
        # ``entry_service.update_entry``.
        db.CheckConstraint(
            "(settled_on IS NULL) = (settled_day_basis_id IS NULL)",
            name="ck_transaction_entries_settle_day_basis_pairing",
        ),
        # A CARD purchase never touches checking -- it leaves through its own CC
        # Payback sibling -- so this link, which is scoped to the ENVELOPE's
        # account, could only ever claim that the checking statement showed it.
        # False by construction, and unwritable rather than merely unoffered.
        # The credit-card arc revisits it (CC1b): a card with statements of its
        # own is an account this column cannot name at all.
        db.CheckConstraint(
            "reconciled_by_id IS NULL OR is_credit IS FALSE",
            name="ck_transaction_entries_card_purchase_clears_nowhere",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transactions.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The account this purchase's cash leaves.  NOT NULL, and it is the PARENT'S
    # account by construction -- ``fk_transaction_entries_parent_account`` above
    # is what says so, so this is a co-located key rather than a copy some writer
    # has to keep in step.
    #
    # **It is stored rather than joined for because clearing is a PER-ACCOUNT
    # question.**  A checking statement shows a transfer's outgoing leg and the
    # savings statement shows the incoming one, so "which statement showed this"
    # is only checkable against an account -- and a foreign key cannot reach one
    # two hops away.  Plan step X-f3b then makes a cleared purchase a cash
    # posting on this same account, at which point the column is the fact rather
    # than the constraint's scaffolding.
    account_id = db.Column(db.Integer, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    purchased_on = db.Column(
        db.Date, nullable=False, server_default=db.text("CURRENT_DATE"),
    )
    # settled_on, settled_day_basis_id and reconciled_by_id are provided by
    # SettleDatedMixin, which is where the pairing and the ``datetime`` refusal
    # are stated once for both tables.  What is specific to a PURCHASE:
    #
    # ``settled_on`` is nullable BY DESIGN and the NULL is a fact rather than a
    # gap -- it means the user has not seen this purchase on a statement yet, so
    # the engine treats it as still outstanding.  Filling it with a default would
    # be storing a guess where a read-time rule can at least be seen.
    #
    # ``reconciled_by_id`` names WHICH statement showed this purchase -- the
    # ``account_anchor_history`` row whose balance the user was reading when they
    # ticked it off (ruling **R-FL**).  The transaction twin of
    # ``app.models.transaction.Transaction.reconciled_by_id``; that column's
    # comment carries the full rationale, including why NULL is a FACT (UNKNOWN,
    # not "not cleared") and why nothing was backfilled into it.  It does NOT
    # replace ``settled_on`` beside it: that is WHEN the money moved, this is
    # WHICH statement was seen to show it, and a statement legitimately shows a
    # line that moved days earlier.
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
