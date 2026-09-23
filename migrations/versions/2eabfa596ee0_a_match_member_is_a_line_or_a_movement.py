"""A match member is a bank line or a movement

Revision ID: 2eabfa596ee0
Revises: c7d1e9a4b2f8
Create Date: 2026-09-22 17:01:33.000000
Review: developer, 2026-09-22 (rulings R-CC43 / R-CC45: the row members
re-keyed to movements, and the row column dropped with its key, index and
check; the line added at plan step credit_card:CC-5-4a-4, text only)

Plan step ``credit_card:CC-5-4a-2``.  Rulings **R-CC43** (the MOVEMENT is the
subject of every settled match on every screen; a row is a candidate only
while Projected) and **R-CC45** (this half: the re-key of the stored past and
the drop of the member table's row column, its own PR and release).  Closes
ledger row **CC-356**.

**What was wrong.**  ``budget.statement_match_members`` was an exclusive arc of
THREE subjects -- a bank line, a ``budget.transactions`` row, or a
``budget.transaction_entries`` movement.  Since plan step
``credit_card:CC-5-4a-1`` the one writer (``_accept._record``) names
movements only: a settled row's money IS its covering movement (ruling
**R-BAL80**), on whichever account it moved through, so every act records the
payment rather than the row.  The acts recorded before that step still named
ROWS, so every reader of an act carried both shapes, and a row member held its
act to the ROW's account by ``fk_statement_match_members_transaction_account``
-- which is what made a definition's account move over a matched Projected row
raise ``IntegrityError`` (finding **CC-356**) where a movement member would
not have been touched at all (the payment stays where the money moved, ruling
**R-CC42**).

**What this does.**  Re-keys every row member onto its row's covering movement
-- ``transaction_entries.covers_settlement AND transaction_id = <the row>``,
of which ``uq_transaction_entries_one_settlement_record`` holds at most one --
then drops ``transaction_id`` with ``fk_statement_match_members_transaction_account``
and ``uq_statement_match_members_transaction``, and re-cuts
``ck_statement_match_members_one_subject`` to two terms.  A member is a bank
line or a movement, and nothing else is spellable.  ``budget.statement_match_creations``
keeps its own ``transaction_id``: what an act MADE is a different relation
(ruling **R-GG**), and a residual row it minted is a row.

**It REFUSES rather than guesses** (the developer's R-CC45 text: *"a member
with no payment to re-key onto ... REFUSES the migration rather than being
guessed at, and the developer rules what happens to it"*).  Three states
cannot be re-keyed, and the upgrade counts each BEFORE it writes anything and
stops naming them:

  * a row member whose row holds NO covering movement -- a bill closed from its
    purchases, a ``$0.00`` close, a Credit or Cancelled row matched before
    today's refusals existed;
  * one whose covering movement sits on ANOTHER account than the act's -- the
    movement key (``fk_statement_match_members_entry_account``) cannot hold it
    to this act, and nothing here decides which statement showed the money;
  * one whose covering movement another member ALREADY names -- ONE ACT PER
    ROW, asserted rather than assumed (the review of CC-5-4a-1, L3: a row named
    by an old row member on checking and by a payment member elsewhere).
    ``uq_statement_match_members_entry`` would refuse the write anyway; the
    count is what makes the refusal legible.

**Measured before this revision was written** (the census on the newest
production dump, the 2026-09-22 17:06 pre-deploy dump
``shekel_prod_predeploy_88d4e6463b17_20260922_170621.dump`` at
``9900b309f0b0``): 284 acts; 103 row members, every one holding exactly one
covering movement, on the act's own account, named by no other member; 68
Paid and 35 Received rows, 14 of them transfer shadows; every movement dated
on its row's day.  None refuses.  The 02:10 nightly of the same day and the
2026-09-21 10:33 pre-deploy dump read the same 284 / 103.

**The re-key is ONE set-based UPDATE.**  Each row member takes its movement's
id -- a scalar subquery, which RAISES rather than choosing if it ever met two
entries -- and loses the row's in one statement, so
``ck_statement_match_members_one_subject`` holds on every row it writes; the
member's ``account_id`` does not move (the refusal above proved it the
movement's).  It reaches every row member by the same predicate that counts
them, so no count is compared against it (a guard over a tautology is none).
The audit trigger records every rewritten member.

**What a reader sees of a re-keyed act is unchanged WHILE its payment carries
its row's day**, which the seam's mirror guarantees and the census measured
(103 of 103): the register labels, values and dates a payment member by the
row it pays.  The upgrade COUNTS the members whose payment is dated on another
day and prints the count beside the re-keyed one; such a member is re-keyed
all the same, the payment being the subject (ruling **R-CC43**), and its
register reading then follows the payment's day.

**The DOWNGRADE restores the SCHEMA and re-keys NOTHING, deliberately.**  The
revision below this one is plan step ``balance:X-bi-6-3``'s ``c7d1e9a4b2f8``
(this revision was re-parented onto it at the merge; the two touch disjoint
tables), and the code at that revision already carries plan step
``CC-5-4a-1``'s writer, which WRITES movement members and reads both shapes,
so every member this upgrade leaves is already a state that revision holds.
Re-keying "back" could not even be total: an act recorded on the card names a
checking bill's payment ON the card, and no row member can hold it there (the
row is on checking).  So the column, its key, its unique index and the
three-term check return, empty of row members.

**Locking.**  The UPDATE rewrites the row members once (103 on production);
``ADD CONSTRAINT ... CHECK`` scans the table once to validate.  Each ``DROP`` is
a catalog change, but dropping ``fk_statement_match_members_transaction_account``
also takes ACCESS EXCLUSIVE on its referenced table ``budget.transactions`` and
checks it for pending trigger events -- that from the PostgreSQL source as the
4a-2 release review read it, NOT measured here.  It was harmless at that
deploy, which ran with the old app stopped.  Instantaneous at this table's size.

**What grades this revision.**
``tests/test_models/test_cc5_4a2_member_rekey.py`` drives the shipped
``upgrade`` / ``downgrade`` over a database holding row members written in the
old shape: the re-key per member (the ids made to diverge, so a movement id
and a row id cannot agree by accident), each of the three refusals and their
shared control, a row holding a purchase beside its payment, the counts, and
the round trip.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2eabfa596ee0"
down_revision = "c7d1e9a4b2f8"
branch_labels = None
depends_on = None


#: The row members whose payment is dated on ANOTHER day than its row.  The
#: seam mirrors a row's assertion onto its covering movement, so this is 0
#: wherever that mirror held (0 of 103 on production, measured); a member
#: counted here is re-keyed all the same -- the payment IS the subject (ruling
#: **R-CC43**) -- and the register then reads the PAYMENT's day for it, which
#: is the one reading that changes.  Counted before the re-key and printed.
_DAY_DIVERGES_SQL = (
    "SELECT COUNT(*) FROM budget.statement_match_members m "
    "  JOIN budget.transactions t ON t.id = m.transaction_id "
    "  JOIN budget.transaction_entries e "
    "    ON e.transaction_id = t.id AND e.covers_settlement "
    " WHERE e.settled_on IS DISTINCT FROM t.settled_on"
)

#: The three states a row member cannot be re-keyed from, each a query an
#: operator can run as it stands.  Keyed by what the refusal says of them.
_UNKEYABLE_SQL = {
    "whose row holds no covering movement": (
        "SELECT m.id FROM budget.statement_match_members m "
        " WHERE m.transaction_id IS NOT NULL "
        "   AND NOT EXISTS (SELECT 1 FROM budget.transaction_entries e "
        "                    WHERE e.transaction_id = m.transaction_id "
        "                      AND e.covers_settlement) "
        " ORDER BY m.id"
    ),
    "whose covering movement is on another account than the act's": (
        "SELECT m.id FROM budget.statement_match_members m "
        "  JOIN budget.transaction_entries e "
        "    ON e.transaction_id = m.transaction_id AND e.covers_settlement "
        " WHERE m.transaction_id IS NOT NULL "
        "   AND e.account_id <> m.account_id "
        " ORDER BY m.id"
    ),
    "whose covering movement another member already names": (
        "SELECT m.id FROM budget.statement_match_members m "
        "  JOIN budget.transaction_entries e "
        "    ON e.transaction_id = m.transaction_id AND e.covers_settlement "
        "  JOIN budget.statement_match_members other "
        "    ON other.transaction_entry_id = e.id "
        " WHERE m.transaction_id IS NOT NULL "
        " ORDER BY m.id"
    ),
}

#: Every row member takes its row's covering movement, in one statement.
#: ``uq_transaction_entries_one_settlement_record`` holds the subquery to at
#: most one movement per row, and :func:`refuse_unkeyable_row_members` has
#: proved it exactly one, on the member's own account, named by no other
#: member.  **A SCALAR subquery rather than ``UPDATE ... FROM``, on
#: purpose**: a join that met two entries of one row would take whichever
#: PostgreSQL happened to read first and say nothing, where a scalar
#: subquery that meets two RAISES -- so a row's purchase can never be
#: re-keyed onto in place of its payment, whatever the join order (measured:
#: with the ``covers_settlement`` term deleted, the ``FROM`` form re-keyed a
#: row holding a purchase beside its payment onto the payment by luck and
#: every case passed).
_REKEY_SQL = (
    "UPDATE budget.statement_match_members m "
    "   SET transaction_entry_id = ("
    "         SELECT e.id FROM budget.transaction_entries e "
    "          WHERE e.transaction_id = m.transaction_id "
    "            AND e.covers_settlement), "
    "       transaction_id = NULL "
    " WHERE m.transaction_id IS NOT NULL"
)

_TWO_SUBJECTS = (
    "(bank_statement_line_id IS NOT NULL)::int "
    "+ (transaction_entry_id IS NOT NULL)::int = 1"
)
_THREE_SUBJECTS = (
    "(bank_statement_line_id IS NOT NULL)::int "
    "+ (transaction_id IS NOT NULL)::int "
    "+ (transaction_entry_id IS NOT NULL)::int = 1"
)


def refuse_unkeyable_row_members(bind) -> None:
    """Refuse the upgrade while any row member has no payment to re-key onto.

    **Module-level so a test can DRIVE each refusal** (the chain's own
    pattern, ``9900b309f0b0.refuse_cross_account_movements``).  Every state is
    counted before any is reported, so one run names them all.

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming each state that holds members, their
            ``statement_match_members`` ids and the diagnostic query, when any
            row member cannot be re-keyed.  Nothing has been written.
    """
    found = {
        state: [row[0] for row in bind.execute(sa.text(sql))]
        for state, sql in _UNKEYABLE_SQL.items()
    }
    refused = {state: ids for state, ids in found.items() if ids}
    if refused:
        raise RuntimeError(
            "CC-5-4a-2 refuses to re-key budget.statement_match_members: "
            + "; ".join(
                f"{len(ids)} row member(s) {state} (ids {ids}; diagnose "
                f"with: {_UNKEYABLE_SQL[state]})"
                for state, ids in refused.items()
            )
            + ".  Nothing was written.  Each such member is the developer's "
            "to rule (ruling R-CC45); this revision does not guess a payment."
        )


def upgrade():
    """Re-key every row member onto its payment, then drop the row column."""
    bind = op.get_bind()
    refuse_unkeyable_row_members(bind)
    day_diverges = bind.execute(sa.text(_DAY_DIVERGES_SQL)).scalar()
    rekeyed = bind.execute(sa.text(_REKEY_SQL)).rowcount
    op.drop_index(
        "uq_statement_match_members_transaction",
        table_name="statement_match_members", schema="budget",
    )
    op.drop_constraint(
        "fk_statement_match_members_transaction_account",
        "statement_match_members", schema="budget", type_="foreignkey",
    )
    op.drop_constraint(
        "ck_statement_match_members_one_subject",
        "statement_match_members", schema="budget", type_="check",
    )
    op.drop_column("statement_match_members", "transaction_id", schema="budget")
    op.create_check_constraint(
        "ck_statement_match_members_one_subject",
        "statement_match_members", _TWO_SUBJECTS, schema="budget",
    )
    # The counts are the migration's own measurement, printed so the operator
    # can compare them with the rehearsal's and the census's.
    print(
        "CC-5-4a-2: re-keyed "
        f"{rekeyed} row member(s) onto their payment "
        f"({day_diverges} dated on another day than its row); "
        "statement_match_members.transaction_id dropped."
    )


def downgrade():
    """Restore the row column, its key and index, and the three-term check."""
    op.drop_constraint(
        "ck_statement_match_members_one_subject",
        "statement_match_members", schema="budget", type_="check",
    )
    op.add_column(
        "statement_match_members",
        sa.Column("transaction_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.create_check_constraint(
        "ck_statement_match_members_one_subject",
        "statement_match_members", _THREE_SUBJECTS, schema="budget",
    )
    # ``c1e7d4b3a850``'s key exactly: CASCADE on delete, NO ACTION on update.
    op.create_foreign_key(
        "fk_statement_match_members_transaction_account",
        "statement_match_members", "transactions",
        ["transaction_id", "account_id"], ["id", "account_id"],
        source_schema="budget", referent_schema="budget",
        ondelete="CASCADE",
    )
    op.create_index(
        "uq_statement_match_members_transaction",
        "statement_match_members", ["transaction_id"],
        unique=True, schema="budget",
        postgresql_where=sa.text("transaction_id IS NOT NULL"),
    )
