"""
Shekel Budget App -- Statement Match Models (budget schema)

WHICH of the app's own rows a recorded bank line IS.  Two tables, one subject
(plan step ``bank_import:X-f6a-2``, rulings **R-FS** and **R-FV**):

  * :class:`StatementMatch` -- one act of matching, reviewed and accepted by
    the owner.
  * :class:`StatementMatchMember` -- one thing that act names: a bank line, a
    transaction, or a purchase.

**The relation is IDENTITY, and identity is the fact this arc was missing.**
``transactions.reconciled_by_id`` (ruling **R-FL**) answers a different
question -- *which declared balance already contains this row* -- and that is
DERIVABLE from identity once a statement carries the line, where identity is
not derivable from absorption.  So an accepted match stores the identity and
nothing else, and ruling **R-FV** is that sentence: a match does not write
``reconciled_by_id``, it only causes the settle day to move, and the doors
already RELEASE the link when a day moves.  Plan step ``balance:X-f3c`` is
where ``StatementCoverage`` starts reading this relation instead, and
``balance:X-f4`` deletes the column it replaces.

**A group is why this is two tables rather than a join column.**  Ruling
**R-FS** measured the grain mismatch running in BOTH directions on the
developer's own accounts: one payroll deposit against three app rows, and one
envelope against N card swipes.  Neither direction fits a foreign key on
either row, and both are the SAME fact -- *this set of bank lines and this set
of app rows are one movement* -- so the act is the table and the things it
names are its members.

**What a match ASSERTS, and the one thing that is checked at the door rather
than by the schema.**  The signed sum of the member lines equals the signed sum
of the member rows: that is what makes the group a movement rather than a
guess.  It is a cross-row aggregate over two tables, so no ``CHECK`` can carry
it; :func:`app.services.statement_match.accept_match` refuses an unbalanced
group and its refusal names the difference.  That refusal is not a formality --
measured on the developer's own statement, 6 of 16 payroll deposits sit
`$0.05`-`$0.06` below what the app's rows sum to, which is finding **N-239**
seen from the outside.

**Agreement is DERIVED, never stored, and a stale match is not a corrupt one.**
Nothing here records the day the match asserted: that is ``max(posted_on)`` over
the member lines, and each member row carries it in its own ``settled_on``.  A
user who later moves a day by hand puts the group out of agreement with the
bank, and the review screen's own
:class:`~app.services.statement_match.AcceptedGroup` carries an ``agrees`` flag
that SHOWS it rather than a release nobody can see -- the repair door finding
**N-302** says a refusal owes.
"""

from app.extensions import db
from app.models.mixins import (
    AccountScopedMixin,
    CreatedAtMixin,
    UserScopedMixin,
)


class StatementMatch(AccountScopedMixin, UserScopedMixin, CreatedAtMixin,
                     db.Model):
    """One accepted act of matching: these bank lines ARE these app rows.

    Ruling **R-FP**: *a match is a PROPOSAL, never a silent apply*.  A row here
    exists only because a human reviewed a proposal and accepted it, which is
    why the act is a table at all -- a derived correspondence would need no
    record of who agreed to it.

    Columns:
        account_id -- the account both sides belong to
            (:class:`~app.models.mixins.AccountScopedMixin`).  Every member is
            held to it by a composite key, so a group spanning two accounts is
            unrepresentable rather than untested: a match is a claim about ONE
            bank's record of ONE account.
        user_id -- who accepted it (:class:`~app.models.mixins.UserScopedMixin`),
            held equal to the account's owner by
            ``fk_statement_matches_owner`` so it is a co-located key rather
            than a copy.  The same construction
            ``fk_account_external_identities_owner`` uses.
        created_at -- when (:class:`~app.models.mixins.CreatedAtMixin`).  A
            match is never edited: correcting one is deleting it and matching
            again, which is why there is no ``updated_at``.

    **It stores no amount and no day**, and both absences are the rule rather
    than an omission.  The amount is the sum of the member lines and the day is
    their latest ``posted_on``; storing either would put a derived value beside
    its source with nothing reconciling them, which is the root cause three of
    this project's arcs exist to remove.
    """

    __tablename__ = "statement_matches"
    __table_args__ = (
        # The SUPERKEY the members name so their own ``account_id`` is this
        # act's.  It constrains nothing -- ``id`` is already the primary key --
        # and exists only because PostgreSQL requires a UNIQUE over exactly the
        # referenced columns before a composite foreign key may target them.
        # The same construction, for the same reason, as
        # ``uq_transactions_id_account``.
        db.UniqueConstraint(
            "id", "account_id", name="uq_statement_matches_id_account",
        ),
        # This act's owner IS its account's, guaranteed rather than maintained.
        db.ForeignKeyConstraint(
            ["account_id", "user_id"],
            ["budget.accounts.id", "budget.accounts.user_id"],
            name="fk_statement_matches_owner",
            ondelete="CASCADE",
        ),
        db.Index("idx_statement_matches_account", "account_id"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)

    members = db.relationship(
        "StatementMatchMember", back_populates="match",
        cascade="all, delete-orphan", passive_deletes=True,
    )

    def __repr__(self):
        return f"<StatementMatch account={self.account_id} ({self.id})>"


class StatementMatchMember(db.Model):
    """One thing a match names -- a bank line, a transaction, or a purchase.

    **An EXCLUSIVE ARC of three typed foreign keys**, the shape plan step
    ``balance:X-ai-s`` states for ``journal_entries`` and
    ``template_amount_versions`` already carries for two: exactly one of the
    three is set, and ``ck_statement_match_members_one_subject`` is what says
    so.  A single polymorphic ``(kind, id)`` pair would be a foreign key the
    database cannot check, on a table whose whole job is to say that two real
    rows are the same movement.

    Columns:
        match_id -- the act this membership belongs to.
        account_id -- the account, held equal to the act's by
            ``fk_statement_match_members_match_account`` and to the SUBJECT's
            by whichever of the three composite keys below applies.  That is
            what makes a match to another account's row unwritable rather than
            merely unoffered -- the same reason
            ``fk_transactions_reconciled_by`` is composite (ruling **R-FL**).
        bank_statement_line_id -- the bank's line, when this member is one.
        transaction_id -- the app's row, when this member is one.
        transaction_entry_id -- the app's purchase, when this member is one.

    **Each subject belongs to at most ONE match, and that is structural.**  The
    three partial unique indexes below are what make "already matched" a
    question the database answers: without them a second review pass could
    explain one bank line twice, and the two acts would each look complete.

    **Every subject key CASCADES, and the consequence is stated rather than
    hidden.**  Deleting a purchase, or destroying a pay period and the
    transactions under it, removes that member and leaves the act smaller --
    so a group can stop balancing without anything raising.  The alternative,
    ``RESTRICT``, refuses an ordinary delete because of a record the user
    cannot see from the row they are deleting, which is the dead end finding
    **N-302** records one table over.  A group that no longer balances is
    reported by :class:`~app.services.statement_match.AcceptedGroup`'s
    ``agrees`` flag, on the screen where it can be re-reviewed.
    """

    __tablename__ = "statement_match_members"
    __table_args__ = (
        # THE EXCLUSIVE ARC: exactly one subject.  Summing the NULL tests is
        # the spelling ``ck_transactions_one_pricing_link`` uses for three
        # columns, where ``<>`` only reads as XOR for two.
        db.CheckConstraint(
            "(bank_statement_line_id IS NOT NULL)::int "
            "+ (transaction_id IS NOT NULL)::int "
            "+ (transaction_entry_id IS NOT NULL)::int = 1",
            name="ck_statement_match_members_one_subject",
        ),
        # This member's account IS its act's.
        db.ForeignKeyConstraint(
            ["match_id", "account_id"],
            ["budget.statement_matches.id", "budget.statement_matches.account_id"],
            name="fk_statement_match_members_match_account",
            ondelete="CASCADE",
        ),
        # ...and IS its subject's, for whichever of the three it carries.
        # ``MATCH SIMPLE`` (PostgreSQL's default) is what lets these three sit
        # beside one another: a member whose ``bank_statement_line_id`` is NULL
        # satisfies the line key whatever ``account_id`` says.
        db.ForeignKeyConstraint(
            ["bank_statement_line_id", "account_id"],
            ["budget.bank_statement_lines.id",
             "budget.bank_statement_lines.account_id"],
            name="fk_statement_match_members_line_account",
            ondelete="CASCADE",
        ),
        db.ForeignKeyConstraint(
            ["transaction_id", "account_id"],
            ["budget.transactions.id", "budget.transactions.account_id"],
            name="fk_statement_match_members_transaction_account",
            ondelete="CASCADE",
        ),
        db.ForeignKeyConstraint(
            ["transaction_entry_id", "account_id"],
            ["budget.transaction_entries.id",
             "budget.transaction_entries.account_id"],
            name="fk_statement_match_members_entry_account",
            ondelete="CASCADE",
        ),
        # One subject, at most one match.  Partial, because two of the three
        # columns are NULL on every row and a NULL is not a claim.
        db.Index(
            "uq_statement_match_members_line", "bank_statement_line_id",
            unique=True,
            postgresql_where=db.text("bank_statement_line_id IS NOT NULL"),
        ),
        db.Index(
            "uq_statement_match_members_transaction", "transaction_id",
            unique=True,
            postgresql_where=db.text("transaction_id IS NOT NULL"),
        ),
        db.Index(
            "uq_statement_match_members_entry", "transaction_entry_id",
            unique=True,
            postgresql_where=db.text("transaction_entry_id IS NOT NULL"),
        ),
        # The reader loads a whole act at once.
        db.Index("idx_statement_match_members_match", "match_id"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # No direct single-column keys: every relationship above is reached through
    # a composite key that also holds the account equal.  Same shape, same
    # reason, as ``bank_statement_lines.account_id``.
    match_id = db.Column(db.Integer, nullable=False)
    account_id = db.Column(db.Integer, nullable=False)
    bank_statement_line_id = db.Column(db.Integer)
    transaction_id = db.Column(db.Integer)
    transaction_entry_id = db.Column(db.Integer)

    match = db.relationship(
        "StatementMatch", back_populates="members",
        foreign_keys=[match_id, account_id],
    )

    def __repr__(self):
        subject = (
            f"line={self.bank_statement_line_id}"
            if self.bank_statement_line_id is not None
            else f"txn={self.transaction_id}"
            if self.transaction_id is not None
            else f"entry={self.transaction_entry_id}"
        )
        return f"<StatementMatchMember match={self.match_id} {subject}>"
