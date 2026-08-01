"""a purchase carries the day it was MADE and the day the bank TOOK it

Plan step S1-c of ``docs/audits/balance_architecture/anchor_settle_partition.md``,
with ruling R-M re-ruled in the same session (2026-08-01).

**One column carried two facts, and that was the root defect.**
``transaction_entries.entry_date`` was documented as "Date the purchase
occurred" and guarded by ruling R-M, which refuses any value after today
because "an entry records a purchase that HAPPENED".  Ruling R-DH (e) then
defined the same column as "the day the money HIT THE ACCOUNT, not the day the
purchase happened -- they differ by a day or two for a debit card".  Both
rulings are right about their own fact; they were sharing a field, so every
reconciliation rule built on it inherited the ambiguity.  The app's other
cash-moving records already carry both clocks apart
(``cash_ledger.CashSourceFact`` -- "It carries TWO clocks, and the second one
is not decoration"; a loan payment's ``due_date`` beside its pay period), and
``transactions.settled_on`` is the next to get the same treatment (plan step
S2-b).  The transaction entry was the last one answering both with one column.

So the column SPLITS:

* ``purchased_on`` (the rename) -- the day the purchase was made.  R-M's guard
  is unchanged and now sits on the column it was actually written about: a
  value after the user's today is refused at both write doors
  (``entry_service._reject_future_purchase_date``).  Budget consumption, the
  out-of-period badge and the entry list's ordering all read this, which is
  what they always meant.
* ``settled_on`` (new, NULLABLE) -- the day the bank took the money, recorded
  only when the user has SEEN it.  NULL means "not observed to have posted",
  which is the conservative answer: the envelope keeps holding the whole
  budget back.  The engine never guesses about an entry.

**This replaces a stored boolean that no one observed.**  ``is_cleared`` was
written as a side effect of the anchor true-up -- a bulk UPDATE flipping every
entry dated on or before the SERVER's today -- so whether a purchase counted as
reconciled depended on the order the user pressed two buttons.  Record the
purchase then true up and it cleared; true up then record and it never did.
That is finding N-130's defect one layer down, and ruling R-DH (d) deletes it:
reconciliation is DERIVED at read time from ``settled_on`` against the
account's latest ``account_anchor_history.observed_on``, so there is one
predicate (``settled_on <= observed_on``) and nothing to keep in step.

**No figure moves the day this ships, and no date is invented.**
``settled_on`` starts NULL on every row rather than being backfilled from
``is_cleared``.  Measured on a 2026-08-01 production clone: of the 70 rows
carrying ``is_cleared = TRUE``, **zero sit on a Projected parent** (53 debit
and 17 credit rows, all on already-settled parents), and the entry reservation
formula only prices PROJECTED rows -- so the flag those 70 rows carried could
not have moved a figure, and dropping it moves none.  The 5 live rows
(``$534.08`` on two open envelopes) carry ``is_cleared = FALSE`` and stay
unreconciled under NULL, so the rendered projected end balance is unchanged at
``-$19.95``.  Backfilling ``settled_on = purchased_on`` for the cleared rows
was REJECTED: it would assert a specific posting day the app never observed,
which is the "guess with a database column" this split exists to remove.

**The CHECK is the real invariant**: money cannot leave the account before it
was spent.  No upper bound is imposed -- any "at most N days ahead" rule would
be an unjustifiable constant, and a wrong forward date is visible on the entry
row and self-corrects at the next true-up.

**Destructive in three ways.**  The column rename, the ``is_cleared`` drop, and
``downgrade``'s loss of ``settled_on``.  The first two are exactly reversible.
The third is not: every non-NULL ``settled_on`` is a fact the USER observed off
a bank statement and nothing re-derives it, so ``downgrade`` refuses when any
row carries one and names them, exactly as migration ``c4a19e7b2d80`` refuses
for a hand-dated ``observed_on``.  On a database where nothing has been
reconciled yet (every row NULL, which is the state this migration leaves)
``downgrade`` runs clean and reconstructs ``is_cleared`` so that no figure
moves in that direction either.

Review: developer, 2026-08-01 (ruling on the R-M fork: "split + reconcile at
true-up"; the ``entry_date -> purchased_on`` rename, the
``settled_on >= purchased_on`` CHECK, no upper bound, and the ``is_cleared``
drop all approved in the same session).

Revision ID: d7c1f4a9e603
Revises: c4a19e7b2d80
Create Date: 2026-08-01 12:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision = 'd7c1f4a9e603'
down_revision = 'c4a19e7b2d80'
branch_labels = None
depends_on = None


_CHECK = "ck_transaction_entries_settled_not_before_purchase"

# ``downgrade``'s refusal: every non-NULL ``settled_on`` was typed by a user
# reading a bank statement, and no column left behind re-derives it.
_OBSERVED_ROWS = sa.text("""
    SELECT e.id, e.transaction_id, e.purchased_on, e.settled_on
    FROM budget.transaction_entries e
    WHERE e.settled_on IS NOT NULL
    ORDER BY e.id
""")

# ``downgrade``'s reconstruction of the boolean it is restoring: an entry was
# reconciled iff its recorded posting day is on or before the LATEST day its
# account has asserted a balance for -- the read-time predicate this migration
# replaced the flag with, evaluated once and frozen back into storage.  Rows
# with a NULL ``settled_on`` reconstruct to FALSE, which is what the derived
# rule answers for them.
_REBUILD_FLAG = sa.text("""
    UPDATE budget.transaction_entries e
    SET is_cleared = TRUE
    FROM budget.transactions t
    WHERE t.id = e.transaction_id
      AND e.settled_on IS NOT NULL
      AND e.settled_on <= (
          SELECT MAX(h.observed_on)
          FROM budget.account_anchor_history h
          WHERE h.account_id = t.account_id
      )
""")


def upgrade():
    """Split ``entry_date``, add the observed posting day, drop the flag."""
    op.alter_column(
        "transaction_entries", "entry_date",
        new_column_name="purchased_on",
        existing_type=sa.Date(), existing_nullable=False,
        existing_server_default=sa.text("CURRENT_DATE"),
        schema="budget",
    )
    op.add_column(
        "transaction_entries",
        sa.Column("settled_on", sa.Date(), nullable=True),
        schema="budget",
    )
    op.create_check_constraint(
        _CHECK,
        "transaction_entries",
        "settled_on IS NULL OR settled_on >= purchased_on",
        schema="budget",
    )
    op.drop_column("transaction_entries", "is_cleared", schema="budget")


def downgrade():
    """Restore ``is_cleared`` and re-merge the two days into one column.

    Refuses rather than destroying what it cannot rebuild -- see the module
    docstring.
    """
    connection = op.get_bind()

    observed = connection.execute(_OBSERVED_ROWS).fetchall()
    if observed:
        listed = "; ".join(
            f"entry={row[0]} txn={row[1]} purchased_on={row[2]} "
            f"settled_on={row[3]}"
            for row in observed
        )
        raise RuntimeError(
            f"Cannot drop budget.transaction_entries.settled_on: "
            f"{len(observed)} row(s) carry a posting day the USER observed off "
            f"a bank statement -- {listed}.  Nothing left after this "
            "downgrade re-derives it, and the boolean it reverts to records "
            "only whether the entry was reconciled at one instant, not when "
            "the money moved.  To downgrade anyway, first discard those "
            "observations by hand (UPDATE budget.transaction_entries SET "
            "settled_on = NULL WHERE settled_on IS NOT NULL), accepting that "
            "each reconciled purchase must be re-reconciled at the next "
            "balance true-up."
        )

    op.add_column(
        "transaction_entries",
        sa.Column(
            "is_cleared", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        schema="budget",
    )
    # Unreachable while the refusal above stands (every ``settled_on`` is NULL
    # by then, so this UPDATE matches nothing).  It is written out anyway
    # because the refusal is the operator's gate, not the migration's: an
    # operator who follows the recovery path and nulls the column by hand gets
    # the same reconstruction, and one who edits the refusal out still gets a
    # faithful flag rather than an all-FALSE column that would silently raise
    # every envelope's reservation.
    connection.execute(_REBUILD_FLAG)

    op.drop_constraint(
        _CHECK, "transaction_entries", type_="check", schema="budget",
    )
    op.drop_column("transaction_entries", "settled_on", schema="budget")
    op.alter_column(
        "transaction_entries", "purchased_on",
        new_column_name="entry_date",
        existing_type=sa.Date(), existing_nullable=False,
        existing_server_default=sa.text("CURRENT_DATE"),
        schema="budget",
    )
