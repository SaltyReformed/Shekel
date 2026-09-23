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
        account_id      -- The account this movement's money moved THROUGH:
                           its own, and free to differ from its parent's since
                           plan step ``credit_card:CC-5-1`` (rulings
                           **R-BAL75**, **R-BAL76**), so that a card purchase
                           inside a checking envelope is a movement on the
                           card: the purchase door writes one since
                           ``CC-5-2`` and the settle verb's TENDER since
                           ``CC-5-3`` (a bill charged to the card holds its
                           covering movement there, ruling **R-CC15**); every
                           other door writes the parent's account.  The
                           parent's ``account_id`` is
                           where the row was EXPECTED to be paid from (ruling
                           **R-CC16**).  Held to the ROW's
                           OWNER by ``fk_transaction_entries_owner_account``
                           below, so a movement on another owner's account is
                           unwritable rather than gated.  Through that step it
                           was the parent's by construction
                           (``fk_transaction_entries_parent_account``).
        owner_id        -- The OWNER of this movement: the parent row's
                           ``user_id``, a co-located key column held equal to
                           it by ``fk_transaction_entries_owner_transaction``
                           and to the account's by
                           ``fk_transaction_entries_owner_account`` -- the
                           construction ``fk_transactions_owner_account`` uses
                           one table up.  Never the author: a companion who
                           records a purchase writes ``user_id``, and the
                           purchase is still the owner's.
        user_id         -- The user who created the entry (owner or companion):
                           the AUTHOR, never the owner.
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
        covers_settlement -- Whether this movement IS its parent's settlement
                           record: the covering movement the status seam
                           writes when a bill, a paycheck, a transfer leg or
                           an envelope closed empty settles on the MANUAL
                           branch, as against a purchase a person or the bank
                           recorded.  Set by the seam
                           alone; the entry doors refuse to edit or delete one
                           (``entry_service._reject_settlement_record``); at
                           most one per row
                           (``uq_transaction_entries_one_settlement_record``).
                           Plan step **X-bi-3a**, ruling **R-BAL39**.
        figure_source_id -- WHO WROTE ``amount``: the settle priced it from the
                           plan (``resolved``), a person stated it (``typed``)
                           or the bank's own line stated it (``observed``).
                           NOT NULL, no default: both writers of a movement
                           state it.  Plan step **X-bi-3a**, ruling
                           **R-BAL39**, :class:`app.enums.MovementFigureSourceEnum`.

    **A row of this table is a MOVEMENT, and since plan step X-bi-3a a settle
    writes one for a bill too -- since X-bi-3b for a paycheck, and since
    X-bi-3c for each leg of a transfer** (ruling **R-BAL39**): the COVERING
    MOVEMENT, the payment row that records a bill's, a paycheck's or a
    transfer leg's money the way a purchase records an envelope's, in the
    parent's direction (``cash_ledger.movement_cash_leg``, ruling
    **R-BAL35**).  It is an ordinary row here -- its ``amount`` is the figure
    the settle booked, its ``purchased_on`` the settle day (the only day a
    bill's payment has, so
    ``ck_transaction_entries_settled_not_before_purchase`` holds as
    equality), its ``description`` the plan's name as it read at the settle,
    and its day pair and statement link the row's own assertion.  The
    status seam is its ONE writer (``status_seam._covering``), and
    ``covers_settlement`` is how the seam finds its own mirror again: a
    settled row may legitimately hold BOTH -- *Track individual purchases*
    unticked on a settled envelope and a figure typed over it leaves a
    stored-figure row holding real purchases -- so which entry is the record
    is a stored fact of the movement, never a derivation over the row.
    **A revert KEEPS it, un-dated** (ruling **R-BAL61**, plan step
    **X-bi-3e-2**): the row's assertion is released and the mirror follows
    -- ``settled_on``, its basis and ``reconciled_by_id`` go ``NULL`` while
    the figure, its source and ``purchased_on`` (now the day of a close the
    owner withdrew) stay -- and the next settle re-dates the same row.  So
    a Projected row can hold one, and every reader that means the row's
    PURCHASES reads :attr:`~app.models.transaction.Transaction.purchases`
    (the entries less this mark) rather than ``entries`` (ruling
    **R-BAL68**); the family readers below are the ones that keep the
    whole collection.  Every family reader of this table -- the fold, the
    posting writer, the statement matcher's pricing -- is kind-blind and
    sums by ruling **R-FM**'s identity over POSTED movements, so a covered
    bill's own leg nets to zero and its dated movement carries the money,
    and an un-dated one carries nothing; the posting writer branches only on
    the COUNTER leg, booking a transfer shadow's movement against the owner's
    Transfers-in-transit account rather than a category (ruling **R-BAL45**'s
    shape C, built at plan step ``balance:X-bi-6-3``).
    The bill / envelope distinction is the plan item's KIND, kept on its
    definition (ruling **R-BAL85**); no reader of this table derives it here.

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
        # the row's own leg's three terms (through ``X-bi-3e``) were sums
        # that net -- which is what made the old bound a FENCE rather than an
        # invariant.
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
        # ``account_id`` is this movement's -- a purchase's, or a row's payment
        # (plan steps ``bank_import:X-f6a-2``, ``credit_card:CC-5-4a-2``).
        # It constrains nothing -- ``id`` is already the primary key -- and
        # exists only because PostgreSQL requires a UNIQUE over exactly the
        # referenced columns before a composite foreign key may target them.
        # The same construction, for the same reason, as
        # ``uq_transactions_id_account``.
        db.UniqueConstraint(
            "id", "account_id", name="uq_transaction_entries_id_account",
        ),
        # **This movement's OWNER is its parent row's, guaranteed rather than
        # maintained** (plan step ``credit_card:CC-5-1``, ruling **R-BAL76**).
        # The pair keys onto ``uq_transactions_id_user``, the superkey added
        # for exactly this, so ``owner_id`` cannot be written as anyone but
        # the row's owner.  ``ON DELETE CASCADE`` matches the single-column
        # ``transaction_id`` key beside it, which stays as the ``transaction``
        # relationship's declared join path: that key is about the PARENT'S
        # EXISTENCE and this one is about AGREEMENT, and two keys over the
        # same column deleting differently would make a delete's outcome
        # depend on which PostgreSQL evaluated (ruling **R-CC32**).
        #
        # Through CC-5-1 the pair here was ``(transaction_id, account_id)``
        # onto ``uq_transactions_id_account`` --
        # ``fk_transaction_entries_parent_account``, plan step X-f3a-1 -- which
        # held a movement's account EQUAL to its parent's, and its ``ON UPDATE
        # CASCADE`` (plan step X-bi-3c, ruling R-BAL46) moved the movements
        # with a re-pointed transfer shadow.  A card purchase in a checking
        # envelope is a movement whose account is NOT its parent's, so the
        # key went with its cascade (ledger row **CC-353**); the endpoint
        # move assigns each leg's movement by hand, as it always did for the
        # session's sake, and a plan row that moves account (the maintain
        # pass, since ruling **R-CC36** at ``CC-5-2``) LEAVES its movements
        # where their money moved -- which is the design.
        db.ForeignKeyConstraint(
            ["transaction_id", "owner_id"],
            ["budget.transactions.id", "budget.transactions.user_id"],
            name="fk_transaction_entries_owner_transaction",
            ondelete="CASCADE",
        ),
        # **...AND ITS ACCOUNT IS THAT OWNER'S**, which is the half that makes
        # a movement on another owner's account unrepresentable: the owner key
        # above alone would leave the account free to be anyone's.  Keyed onto
        # ``uq_accounts_id_user``, exactly as ``fk_transactions_owner_account``
        # is one table up.  ``ON DELETE RESTRICT`` matches the single-column
        # ``account_id`` key on the column itself, for the reason its sibling
        # above gives: an account holding a movement cannot vanish -- a card
        # carrying a checking envelope's swipes least of all -- and the
        # account door archives such an account instead (ruling **R-CC32**).
        db.ForeignKeyConstraint(
            ["account_id", "owner_id"],
            ["budget.accounts.id", "budget.accounts.user_id"],
            name="fk_transaction_entries_owner_account",
            ondelete="RESTRICT",
        ),
        # WHICH STATEMENT showed this purchase, as a COMPOSITE key over the
        # MOVEMENT's account (ruling **R-FL**): since plan step
        # ``credit_card:CC-5-1`` that is the account its money moved through,
        # which is what a card statement clearing a card purchase needs and
        # what a checking statement can never have shown.  The transaction
        # twin of ``fk_transactions_reconciled_by``; see
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
        # step, which the figure's provenance did not.**  Who wrote a FIGURE
        # lives on this table alone (``figure_source_id`` below; the parent
        # row's own ``settled_basis_id`` went at plan step
        # ``balance:X-bi-4b-2``), because a movement IS the figure its
        # parent's close is made of.  A purchase does carry its own DAY, and
        # all three kinds are written to it: the
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
        # Plan step ``credit_card:CC-5-1`` made the card's OWN representation
        # storable -- a movement whose ``account_id`` names the card, which
        # clears on the card's statement -- written by the purchase door since
        # ``CC-5-2`` and by the settle verb's tender since ``CC-5-3``, while
        # the cheat's door (``create_entry`` with
        # ``is_credit``) still writes a flagged line on checking until
        # ``CC-7`` retires it; ``CC-7`` deletes the flag, this CHECK and the
        # cheat together once no line carries the flag.
        db.CheckConstraint(
            "reconciled_by_id IS NULL OR is_credit IS FALSE",
            name="ck_transaction_entries_card_purchase_clears_nowhere",
        ),
        # AT MOST ONE settlement record per row (plan step **X-bi-3a**): the
        # seam writes exactly one covering movement per manual-branch settle,
        # and a second could only reach the table around it.  A partial
        # unique index rather than a CHECK because the rule is a count.
        db.Index(
            "uq_transaction_entries_one_settlement_record",
            "transaction_id",
            unique=True,
            postgresql_where=db.text("covers_settlement = true"),
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transactions.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The account this movement's money moved THROUGH -- its OWN fact since
    # plan step ``credit_card:CC-5-1`` (rulings **R-BAL75**, **R-BAL76**), read
    # on this account by the fold (``cash_ledger._events._movements_of``), the
    # posted ledger (``_posting_purchases._purchase_target``) and the anchor
    # self-heal (``posting_service._family_accounts``), whatever account its
    # parent row names.  ``ON DELETE RESTRICT``, as ``transactions.account_id``
    # is: a record of money that moved through an account does not vanish with
    # the account (ruling **R-CC32**), and this single-column key is the
    # ``account`` relationship's declared join path beside the composite owner
    # key above that holds the account to the row's owner.
    #
    # Through CC-5-1 it was the PARENT'S account by construction
    # (``fk_transaction_entries_parent_account``), a co-located key column
    # rather than a fact of its own; the reason it was STORED then still holds
    # now that it is one: clearing is a PER-ACCOUNT question, a checking
    # statement shows a transfer's outgoing leg and the savings statement the
    # incoming one, so "which statement showed this" is only checkable against
    # an account, and a cleared purchase is a cash posting on this account
    # (plan step X-f3b).
    account_id = db.Column(
        db.Integer, db.ForeignKey(
            "budget.accounts.id", name="fk_transaction_entries_account_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    # WHO OWNS this movement: the parent row's owner, as a co-located key
    # column (plan step ``credit_card:CC-5-1``, ruling **R-BAL76**).  Not a
    # copy some writer keeps in step -- ``fk_transaction_entries_owner_transaction``
    # refuses any value but the row's ``user_id`` and
    # ``fk_transaction_entries_owner_account`` refuses an account that owner
    # does not hold -- so the two writers of a movement (``entry_service.
    # create_entry`` and the status seam's ``_cover``) state it from the row
    # and cannot state it wrong.  No key of its own onto ``auth.users``: a
    # user delete is refused one table up by ``fk_transactions_user_id``
    # (order-independent, migration ``d4a92f6b13c8``), and this column names
    # a real user through the row's, which that key holds.  Stated explicitly
    # at construction rather than derived at flush, as ``account_id`` always
    # was: a line that cannot be silently wrong, only absent, and absent is a
    # NOT NULL violation.
    owner_id = db.Column(db.Integer, nullable=False)
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
    # WHETHER THIS MOVEMENT IS ITS PARENT'S SETTLEMENT RECORD (plan step
    # **X-bi-3a**).  Server default false: every existing row is a purchase,
    # and a purchase door never states it -- only the status seam writes true.
    covers_settlement = db.Column(
        db.Boolean, nullable=False, default=False, server_default="false",
    )
    # WHO WROTE ``amount`` (plan step **X-bi-3a**, ruling **R-BAL39**).
    # RESTRICT rather than SET NULL: a vanishing catalogue row would leave a
    # figure with no source, which the NOT NULL exists to forbid.  No default,
    # server-side or ORM-side: the two writers of a movement (the purchase
    # doors and the status seam) each state it, and a default would answer for
    # a writer that forgot -- the stored guess ruling **R-IY** deletes.  No
    # index: nothing queries it, and ``user_id``'s comment on
    # :class:`~app.models.transaction.Transaction` states the predicate for the
    # first reader that does.  Resolved through
    # ``ref_cache.movement_figure_source_id``.
    figure_source_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.movement_figure_sources.id",
            name="fk_transaction_entries_figure_source_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

    # Relationships
    # ``foreign_keys`` on both, because each parent is reached by TWO declared
    # keys -- the single-column one and the composite that also holds the
    # owner (plan step ``credit_card:CC-5-1``) -- and SQLAlchemy cannot pick a
    # join path between them.  The single-column key is the declared path,
    # the choice ``Transaction.account`` makes over
    # ``fk_transactions_owner_account``: adding ``AND owner_id = ...`` to every
    # load would re-check in SQL what the database refused to store, while
    # making ``owner_id`` a column two relationships wanted to write on flush.
    transaction = db.relationship(
        "Transaction", foreign_keys=[transaction_id],
        back_populates="entries",
    )
    # The account the money moved through, for a reader that wants the
    # ACCOUNT rather than its id.  Lazy (the default): ``lazy="joined"``
    # here chains ``Account``'s own joined tree onto every movement load
    # (measured at CC-5-1's review: the fold's movement query went from 11
    # joins to 17).  Its readers are the transfer endpoint move, which
    # assigns it, and the grid's account chip and the entry list's account
    # name (plan step ``credit_card:CC-5-2``), which read ``.name`` only for
    # a movement off its row's account -- an identity-map hit for a member
    # of the owner's cash-flow set, which the page's resolver has loaded
    # (measured: a card swipe adds no statement to the grid render), and
    # one load per distinct account per render otherwise.
    account = db.relationship("Account", foreign_keys=[account_id])
    user = db.relationship("User", lazy="joined")
    credit_payback = db.relationship(
        "Transaction", foreign_keys=[credit_payback_id],
        lazy="select",
    )

    def __repr__(self):
        return f"<TransactionEntry '{self.description}' ${self.amount} ({self.id})>"
