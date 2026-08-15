"""a line names the statement that showed it

Plan step **X-f3a-1** of ``docs/audits/balance_architecture/README.md`` section
5 -- the SCHEMA half of ruling **R-FL**: *whether a line is INSIDE a declared
balance is a RECORDED FACT, not a comparison of two dates.*

Review: Josh, 2026-08-14 -- APPROVED: the three-state clearing model (CLEARED /
NOT CLEARED / UNKNOWN), the STRUCTURAL composite foreign key over the
convention-plus-tests alternative, and the two-leaf split that makes this leaf
balance-neutral.  The ``budget.transaction_entries.account_id`` column below was
named in that approval.

Today the app decides "has this line reached the bank" by comparing two of its
own dates -- ``settled_on <= the latest assertion's observed_on``
(``cash_ledger.ReconciledThrough.covers``).  The developer's own bank exports
measured that guess: of 55 Checking assertions only 17 equal the bank's closing
balance for their day, and of 110 movements matched to bank lines on exact
amount only 33 (30%) carry the day the bank posted them.  ``ReconciledThrough``
said so in its own docstring before the measurement existed -- *"what removes it
is an OBSERVATION, not a second derived date"*.

Five things, and **no row's balance moves**:

  1. **``budget.account_anchor_history`` gains ``UNIQUE (account_id, id)``** --
     the superkey a composite foreign key needs as its target.  It constrains
     NOTHING (``id`` is already the primary key), which is why it does not
     revive ruling **R-EQ**: that ruling deleted a CONTENT key over
     ``(account_id, anchor_balance, observed_on)`` because a transport retry and
     a deliberate re-assertion carry identical values, so the key had to
     mis-classify one of them.  A superkey over the primary key can reject no
     row at all.
  2. **``budget.transactions`` gains ``UNIQUE (id, account_id)``** -- the same,
     as the target of the entry's parent-agreement key below.
  3. **``budget.transactions.reconciled_by_id``** -- nullable, and the NULL is
     a FACT rather than a gap: it means no statement has been recorded as
     showing this line, which is the honest state for every row that exists
     today.  **Nothing is backfilled**, deliberately: backfilling it from the
     date rule would write the guess into the column as though it were an
     observation, and the guess is exactly what R-FL measured false.  The
     clearing rule falls back to the date rule for an unlinked line
     (``cash_ledger.StatementCoverage``), so every figure the app renders is
     byte-identical the moment this lands.
  4. **``budget.transaction_entries.account_id``** -- NOT NULL, backfilled from
     each entry's parent, and held equal to it forever by
     ``fk_transaction_entries_parent_account``.  It is not a copy kept in step
     by a convention: the composite key makes a disagreement unrepresentable.
     It exists because clearing is a PER-ACCOUNT question -- your checking
     statement shows the outgoing leg of a transfer, the savings statement shows
     the incoming one -- so an entry's clearing link must be checkable against
     an account, and plan step X-f3b turns a cleared purchase into a cash
     posting on that same account.
  5. **``budget.transaction_entries.reconciled_by_id``** -- the purchase twin of
     (3), under the same composite key.

Plus one CHECK on each linked table -- ``ck_*_cleared_needs_settle_day`` -- so a
row cannot claim a statement showed money that never moved.  See the constraint
in :func:`upgrade` for the two live doors it backs up.

**Why the clearing links are COMPOSITE keys.**  A transaction's clearing link
must name an assertion *of that transaction's own account*; a single-column
``REFERENCES account_anchor_history (id)`` cannot say so, and a writer that
forgot the account scope would produce a link that is silently wrong about whose
statement showed the money.  ``FOREIGN KEY (account_id, reconciled_by_id)``
makes the cross-account link unrepresentable instead of untested.  PostgreSQL's
default ``MATCH SIMPLE`` semantics are what make it work beside a nullable link:
a row with ``reconciled_by_id IS NULL`` satisfies the constraint whatever its
``account_id`` says, so an unlinked line is unaffected.

**``ON DELETE RESTRICT`` on both clearing keys.**  There is no door in ``app/``
that deletes a single assertion -- ``account_anchor_history`` rows go only with
their account (``routes/accounts/crud.py`` hard-delete, which removes the
account's transactions at step 2 and the account itself at step 4, cascading its
history) -- so RESTRICT costs nothing today and refuses rather than silently
un-clearing a line if such a door is ever written.  ``SET NULL`` is the wrong
answer here for the same reason it is wrong for ``amount_source_id``: it would
convert a recorded observation into "never observed" without anyone noticing.

**Audit.**  ``budget.transactions`` and ``budget.transaction_entries`` are
already in ``app.audit_infrastructure.AUDITED_TABLES``, so their existing
row-level triggers capture the new columns with no change here and
``EXPECTED_TRIGGER_COUNT`` is unchanged.  ``budget.account_anchor_history`` is
audited too and gains no column.

**Downgrade** drops exactly what upgrade added, in reverse dependency order, and
loses only recorded clearing facts -- which the clearing rule's date fallback
then answers for, so a downgraded database renders the same figures it rendered
before this migration ran.

Revision ID: d5b8e2c74a19
Revises: e6b4a2d8c713
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d5b8e2c74a19"
down_revision = "e6b4a2d8c713"
branch_labels = None
depends_on = None


#: The two tables that gain a clearing link, and the index name each one's
#: link column takes.  Written once because the column, the composite key and
#: the index are the SAME three things on both tables -- a second spelling of
#: them is how one of the three comes to be missing on one table only.
_LINKED_TABLES = (
    ("transactions", "idx_transactions_reconciled_by"),
    ("transaction_entries", "idx_transaction_entries_reconciled_by"),
)

#: The backfill that gives every existing purchase its parent's account.
#: A module CONSTANT rather than a literal inside :func:`upgrade`, so
#: ``tests/test_models/test_clearing_link_schema.py`` executes the string this
#: migration runs instead of a hand-copied twin that can drift from it -- the
#: pattern migration ``efffcf647644``'s own backfill tests established.
BACKFILL_ENTRY_ACCOUNT_SQL = (
    "UPDATE budget.transaction_entries e "
    "SET account_id = t.account_id "
    "FROM budget.transactions t "
    "WHERE t.id = e.transaction_id"
)

#: The count the three-step NOT NULL pattern verifies before tightening.
_UNBACKFILLED_ENTRY_COUNT_SQL = (
    "SELECT count(*) FROM budget.transaction_entries WHERE account_id IS NULL"
)


def upgrade():
    """Add the superkeys, the entry's account, and the two clearing links.

    Order is load-bearing: each ``UNIQUE`` exists before the foreign key that
    targets it, and ``transaction_entries.account_id`` is backfilled and made
    NOT NULL before either key that reads it is created -- so there is no window
    in which a link could be written against an unconstrained account.
    """
    # (1) and (2): the superkeys the composite keys below target.  Neither can
    # reject a row -- both include a primary key -- so neither changes what is
    # insertable.
    op.create_unique_constraint(
        "uq_anchor_history_account_id",
        "account_anchor_history", ["account_id", "id"],
        schema="budget",
    )
    op.create_unique_constraint(
        "uq_transactions_id_account",
        "transactions", ["id", "account_id"],
        schema="budget",
    )

    # (4): the entry's own account, in the three steps a NOT NULL column on a
    # populated table takes.
    op.add_column(
        "transaction_entries",
        sa.Column("account_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.execute(BACKFILL_ENTRY_ACCOUNT_SQL)
    remaining = op.get_bind().execute(
        sa.text(_UNBACKFILLED_ENTRY_COUNT_SQL)
    ).scalar()
    if remaining:
        raise RuntimeError(
            f"{remaining} budget.transaction_entries row(s) resolved no parent "
            "account, which the NOT NULL "
            "budget.transaction_entries.transaction_id FK makes impossible.  "
            "Investigate with: SELECT id, transaction_id FROM "
            "budget.transaction_entries WHERE account_id IS NULL;"
        )
    op.alter_column(
        "transaction_entries", "account_id",
        existing_type=sa.Integer(), nullable=False,
        schema="budget",
    )
    # The entry's account IS its parent's, guaranteed rather than maintained.
    # ON DELETE CASCADE matches the single-column ``transaction_id`` key beside
    # it, which stays as the ORM relationship's declared join path: this key is
    # about AGREEMENT, that one is about the parent's existence, and a key that
    # cascaded differently from its sibling would make a delete's outcome depend
    # on which of the two PostgreSQL evaluated.
    op.create_foreign_key(
        "fk_transaction_entries_parent_account",
        "transaction_entries", "transactions",
        ["transaction_id", "account_id"], ["id", "account_id"],
        source_schema="budget", referent_schema="budget",
        ondelete="CASCADE",
    )

    # (3) and (5): the clearing links themselves.
    for table, index_name in _LINKED_TABLES:
        op.add_column(
            table,
            sa.Column("reconciled_by_id", sa.Integer(), nullable=True),
            schema="budget",
        )
        op.create_foreign_key(
            f"fk_{table}_reconciled_by",
            table, "account_anchor_history",
            ["account_id", "reconciled_by_id"], ["account_id", "id"],
            source_schema="budget", referent_schema="budget",
            ondelete="RESTRICT",
        )
        # "What did this statement clear" is one indexed read, which is the
        # question the panel's re-open (plan step X-f3c) and the cutover's
        # residual both ask.  **A foreign key indexes the REFERENCED side, never
        # the referencing one**, so neither composite key above creates anything
        # here -- and ``transaction_entries`` carries no index leading on
        # ``account_id`` at all, so nothing existing could serve the lookup
        # either.
        op.create_index(
            index_name, table, ["reconciled_by_id"],
            unique=False, schema="budget",
        )
        # A statement cannot have shown money that never moved.  The two
        # columns record DIFFERENT facts -- when the cash moved, and which
        # statement was seen to show it -- but one of them entails the other,
        # so a row carrying a link and no day is a row asserting both that a
        # statement showed this line and that nothing has been observed to
        # leave the account.
        #
        # It is not decoration: both doors that CLEAR a settle day would
        # otherwise leave the link behind.  ``status_seam.apply_status_change``
        # nulls ``transactions.settled_on`` whenever a row leaves the settled
        # band, and ``entry_service.update_entry`` lets a user clear a
        # purchase's posting day outright.  Both now release the link in the
        # same statement, and this refuses the third writer nobody has written
        # yet.
        op.create_check_constraint(
            f"ck_{table}_cleared_needs_settle_day",
            table,
            "reconciled_by_id IS NULL OR settled_on IS NOT NULL",
            schema="budget",
        )

    # A CARD purchase never touches checking -- it leaves later through its own
    # CC Payback sibling -- so the account whose statement this link is scoped
    # to is not the account the money left.  "The checking statement showed this
    # credit-card purchase" is false by construction, and the panel already
    # refuses to OFFER one (``_purchases._outstanding_scope``'s
    # ``is_credit IS FALSE``); this makes the state unwritable rather than
    # merely unoffered, which is the difference plan step X-f3b turns into a
    # posting.
    #
    # Its scope is TODAY's model, and the credit-card arc revisits it: once a
    # card is an account with statements of its own (CC1b), a card purchase's
    # clearing link would name the CARD's assertion -- which this table cannot
    # express at all, because an entry's ``account_id`` is its ENVELOPE's.
    op.create_check_constraint(
        "ck_transaction_entries_card_purchase_clears_nowhere",
        "transaction_entries",
        "reconciled_by_id IS NULL OR is_credit IS FALSE",
        schema="budget",
    )


def downgrade():
    """Drop the clearing links, the entry's account, and the two superkeys.

    Reverse dependency order, and it loses only recorded clearing facts: the
    clearing rule answers an unlinked line from the date rule, so a downgraded
    database renders exactly the figures it rendered before the upgrade.
    """
    op.drop_constraint(
        "ck_transaction_entries_card_purchase_clears_nowhere",
        "transaction_entries", type_="check", schema="budget",
    )
    for table, index_name in _LINKED_TABLES:
        op.drop_constraint(
            f"ck_{table}_cleared_needs_settle_day", table,
            type_="check", schema="budget",
        )
        op.drop_index(index_name, table_name=table, schema="budget")
        op.drop_constraint(
            f"fk_{table}_reconciled_by", table,
            type_="foreignkey", schema="budget",
        )
        op.drop_column(table, "reconciled_by_id", schema="budget")

    op.drop_constraint(
        "fk_transaction_entries_parent_account", "transaction_entries",
        type_="foreignkey", schema="budget",
    )
    op.drop_column("transaction_entries", "account_id", schema="budget")

    op.drop_constraint(
        "uq_transactions_id_account", "transactions",
        type_="unique", schema="budget",
    )
    op.drop_constraint(
        "uq_anchor_history_account_id", "account_anchor_history",
        type_="unique", schema="budget",
    )
