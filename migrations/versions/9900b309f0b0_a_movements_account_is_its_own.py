"""A movement's account is its own, and its owner is its row's

Revision ID: 9900b309f0b0
Revises: 45f10b870c8b
Create Date: 2026-09-20 10:40:00.000000

Plan step ``credit_card:CC-5-1``.  Rulings **R-BAL75** (a movement folds on its
own account), **R-BAL76** (the parent-account co-location's end shape and its
timing) and **R-CC32** (what each new key does on a delete).  Closes ledger
row **CC-353**.

**What was wrong.**  ``budget.transaction_entries`` -- a MOVEMENT: a purchase
against an envelope, or the covering movement a settle writes for a bill, a
paycheck or a transfer leg -- carried its account as a co-located copy of its
parent row's, held equal by the composite
``fk_transaction_entries_parent_account`` onto ``uq_transactions_id_account``
(plan step X-f3a-1), with ``ON UPDATE CASCADE`` since plan step ``X-bi-3c``
(migration ``c4e8a2d7f1b3``, ruling **R-BAL46**).  So a movement could only
ever be on the account its plan row names, and a card purchase inside a
checking envelope -- money that moved through the CARD, budgeted on checking
-- was unrepresentable.  The cheat represented it as a flagged line on
checking (``is_credit``) with a phantom "CC Payback" row a period later, the
state the credit-card arc exists to delete (design
``docs/design/credit_card_from_scratch.md`` 3.2).

**What this does.**  Makes a movement's ``account_id`` the account its money
moved THROUGH -- its own fact, free to differ from its parent's -- while
holding it to the ROW's OWNER so a movement on another owner's account is
unwritable rather than gated at a door, exactly the construction
``budget.transactions`` already carries (``fk_transactions_owner_account``,
migration ``d4a92f6b13c8``):

  * ``uq_transactions_id_user`` -- the superkey the row-owner co-location
    needs.  It constrains nothing (``id`` is already the primary key) and
    exists only because PostgreSQL requires a UNIQUE over exactly the
    referenced columns.
  * ``budget.transaction_entries.owner_id`` -- NULLABLE, backfilled from the
    parent row's ``user_id``, then ``SET NOT NULL``.  The parent's OWNER, never
    the author: ``user_id`` on this table is who recorded the movement, a
    companion's own id when a companion did.
  * ``fk_transaction_entries_parent_account`` -- DROPPED, its cascade with it.
  * ``fk_transaction_entries_account_id`` -- the plain ``account_id`` REFERENCES
    ``budget.accounts (id)``, ``ON DELETE RESTRICT``: the ``account``
    relationship's declared join path.
  * ``fk_transaction_entries_owner_transaction`` -- ``(transaction_id,
    owner_id)`` REFERENCES ``budget.transactions (id, user_id)``, ``ON DELETE
    CASCADE``.
  * ``fk_transaction_entries_owner_account`` -- ``(account_id, owner_id)``
    REFERENCES ``budget.accounts (id, user_id)``, ``ON DELETE RESTRICT``.

With both composites, the movement's row and its account cannot belong to
different owners: either key alone leaves the other parent free to be anyone's.
What the dropped key guaranteed -- account EQUALITY with the parent -- is what
the design needs gone.  The readers that relied on it for the MONEY were
re-pointed one step earlier (``balance:X-bi-4a``: the fold reads
``TransactionEntry.account_id``, the anchor self-heal walks every movement's
account, the posted ledger always attributed by the movement).

**Timing, stated plainly.**  R-BAL76 timed the drop at ``CC-5``, "the first
writer of a cross-account movement", as ONE leaf that re-points the remaining
readers, adds the door gate and drops the key together, and rejected dropping
it at ``X-bi-4`` because of "a window to CC-5 where eight readers misread a
state no door writes".  ``CC-5`` was decomposed on 2026-09-20 (coordinator;
the developer answered this leaf's two questions under that decomposition)
into 5-1 (this key), 5-2 (the purchase door, the readers still keyed by the
parent's account -- the reconcile panel's offer, ``off_statement_sum``, the
account delete door's guard -- and the movement chip) and 5-3 (the settle
with a tender).  So between this revision and ``CC-5-2`` the schema ADMITS a
same-owner cross-account movement that those readers would misread, and no
door writes one: ``entry_service.create_entry`` and the status seam's
``_cover`` take the parent's account, and the recurrence maintain pass still
RETAINS a row holding a movement rather than moving its account -- so every
balance, posting and offer is byte-identical here.  **This revision reaches
``dev`` only together with ``CC-5-2``** (developer 2026-09-20, on this
leaf's adversarial review, option "Stack 5-1 and 5-2, one PR"): the branch
opens ONE pull request once 5-2 is on it, so the key never reaches ``dev``
ahead of the readers and the writer -- R-BAL76's letter, with the leaf
boundary kept for the session that builds 5-2.

**ON DELETE, ruled rather than inherited** (**R-CC32**, developer 2026-09-20).
Each new key carries the SAME action as the single-column key it sits beside:
CASCADE with ``transaction_id`` (a row's delete takes its movements, as it
always did), RESTRICT with ``account_id`` (an account holding a movement
cannot vanish -- a card carrying a checking envelope's swipes least of all;
the account door archives such an account instead, and ``CC-5-2`` teaches
that door to count MOVEMENTS on the account rather than rows alone, in the
same leaf that first writes one).  Two keys over one column deleting
differently would make a delete's outcome depend on which PostgreSQL
evaluated, the rule this table has stated since X-f3a-1.  ``ON UPDATE`` is
PostgreSQL's default on all three: the old cascade's one beneficiary, the
transfer shadow re-pointed by ``transfer_service._endpoints._apply_endpoint_move``,
is moved by that applier's own assignment (it always was, for the session's
sake), so nothing that moves a parent leaves a movement behind.

**No key of ``owner_id``'s own onto ``auth.users``.**  ``d4a92f6b13c8`` added
one for the row and measured why: it made the user-delete refusal
ORDER-INDEPENDENT.  That refusal now stands one table up
(``fk_transactions_user_id``, RESTRICT), so no user delete reaches this table,
and ``owner_id`` names a real user through the row's, which that key holds.  A
fourth key here would be a second statement of a refusal already made.

**The backfill reads the PARENT ROW, and the account key grades it.**  Every
movement's owner is its row's by definition, so ``owner_id`` is copied from
``budget.transactions.user_id`` through the ``NOT NULL`` ``transaction_id``
key -- one set-based ``UPDATE ... FROM``, every row reached, none left NULL.
``fk_transaction_entries_owner_account`` then validates every backfilled
movement against the OTHER parent: under the dropped key a movement's account
was its row's and ``fk_transactions_owner_account`` holds that account to the
row's owner, so the constraint takes clean on every database this chain has
produced -- and on one that somehow holds a cross-owner movement the ``ADD
CONSTRAINT`` aborts naming the key rather than picking a winner.

**Driven on the dev clone before this branch's PR** (``shekel_cc5``, cloned
from ``shekel_xbi4b`` 2026-09-20 and brought to ``45f10b870c8b``; 338
movements, one owner, 0 on an account other than their parent's): the
upgrade backfilled all 338 and printed the count, the downgrade restored the
``c4e8a2d7f1b3`` key verbatim (``ON UPDATE CASCADE ON DELETE CASCADE``) with
every movement row intact, and the second upgrade printed 338 again.  That is
the CLONE's figure; production prints its own on the deploy (R-BAL76's count
of 274 was the 2026-09-18 restore's).

**The DOWNGRADE is conditional, and says so.**  It drops the three keys, the
column and the superkey, and re-creates ``fk_transaction_entries_parent_account``
as ``c4e8a2d7f1b3`` left it (CASCADE on both actions).  That key can only be
re-created while every movement's account IS its parent's, so the downgrade
first COUNTS the movements that sit on another account and REFUSES with the
count and a diagnostic when there are any: at this revision no door writes
one, and once ``CC-5-2`` / ``CC-5-3`` ship the refusal is the correct outcome
-- a movement on the card is a record of where money moved, and a downgrade
is not the place to decide which account it should be pretended onto.  A
first draft let the ``ADD CONSTRAINT`` fail on its own; the explicit count is
what makes the refusal legible to an operator.

**Locking.**  ``ADD COLUMN`` with no default is catalog-only; the backfill
rewrites every movement row once; ``SET NOT NULL`` scans once; each
``ADD CONSTRAINT ... FOREIGN KEY`` takes SHARE ROW EXCLUSIVE on both tables and
scans ``budget.transaction_entries`` once to validate.  Instantaneous at this
table's size.

**What grades this revision.**  ``tests/test_models/test_cc5_1_movement_account_key.py``
drives the shipped ``upgrade`` / ``downgrade`` over a database that HOLDS
movements (the template is built empty, so the backfill's join would otherwise
never touch a row): the round trip, the per-row backfill with a SECOND owner
(so a constant and the join cannot agree by accident), the downgrade's refusal
with its control, and the KEYS -- a same-owner cross-account movement is now
writable, another owner's account is refused by name, a parent's account move
no longer carries its movements.  ``tests/test_models/test_clearing_link_schema.py``
re-expresses the two cases that graded the dropped key (developer
confirmation 2026-09-20, CLAUDE.md rule 5, under R-BAL76).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9900b309f0b0"
down_revision = "45f10b870c8b"
branch_labels = None
depends_on = None


#: Every movement's owner, taken from the row it records money for.
#:
#: ``budget.transaction_entries.transaction_id`` is ``NOT NULL`` with a foreign
#: key, so the join reaches exactly one row per movement and leaves none NULL
#: for ``SET NOT NULL`` to trip on.  The ACCOUNT is deliberately not consulted:
#: ``fk_transaction_entries_owner_account`` grades this answer against it
#: rather than competing with it (module docstring).
_BACKFILL_OWNER_SQL = (
    "UPDATE budget.transaction_entries e "
    "   SET owner_id = t.user_id "
    "  FROM budget.transactions t "
    " WHERE t.id = e.transaction_id"
)

#: The movements the composite parent-account key could not hold: those on an
#: account other than their parent row's.  Zero at this revision (no door
#: writes one); the downgrade's one refusal once the card's doors ship.
_CROSS_ACCOUNT_MOVEMENTS_SQL = (
    "SELECT COUNT(*) FROM budget.transaction_entries e "
    "  JOIN budget.transactions t ON t.id = e.transaction_id "
    " WHERE e.account_id <> t.account_id"
)

_MOVEMENTS_SQL = "SELECT COUNT(*) FROM budget.transaction_entries"


def refuse_cross_account_movements(bind) -> None:
    """Refuse the downgrade while any movement sits off its parent's account.

    **Module-level so a test can DRIVE the refusal** (the chain's own
    pattern, ``45f10b870c8b.refuse_lossy_rows``).  The re-created
    ``fk_transaction_entries_parent_account`` holds a movement's account equal
    to its parent's, so a movement the card's doors wrote on the card has no
    place under it, and nothing here decides which account to pretend it
    onto.

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: With the count and the diagnostic query, when any
            movement's ``account_id`` differs from its parent row's.
    """
    count = bind.execute(sa.text(_CROSS_ACCOUNT_MOVEMENTS_SQL)).scalar()
    if count:
        raise RuntimeError(
            f"{count} budget.transaction_entries row(s) sit on an account "
            "other than their parent row's -- a movement on the card under a "
            "row budgeted on checking (plan step credit_card:CC-5-2 / CC-5-3).  "
            "fk_transaction_entries_parent_account cannot hold such a row and "
            "this revision does not choose an account for it; delete or "
            "re-point each through the app before downgrading.  Diagnose "
            f"with: {_CROSS_ACCOUNT_MOVEMENTS_SQL}"
        )


def upgrade():
    """Give the movement its owner, free its account, drop the co-location."""
    bind = op.get_bind()
    # The superkey first: ``fk_transaction_entries_owner_transaction`` cannot
    # be created until PostgreSQL has a UNIQUE over exactly ``(id, user_id)``
    # to target.
    op.create_unique_constraint(
        constraint_name="uq_transactions_id_user",
        table_name="transactions",
        columns=["id", "user_id"],
        schema="budget",
    )
    # NULLABLE first: a NOT NULL column with no default cannot be added to a
    # populated table at all.
    op.add_column(
        "transaction_entries",
        sa.Column("owner_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.execute(_BACKFILL_OWNER_SQL)
    op.alter_column(
        "transaction_entries", "owner_id",
        existing_type=sa.Integer(),
        nullable=False,
        schema="budget",
    )
    op.drop_constraint(
        "fk_transaction_entries_parent_account", "transaction_entries",
        schema="budget", type_="foreignkey",
    )
    op.create_foreign_key(
        constraint_name="fk_transaction_entries_account_id",
        source_table="transaction_entries",
        referent_table="accounts",
        local_cols=["account_id"],
        remote_cols=["id"],
        source_schema="budget",
        referent_schema="budget",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        constraint_name="fk_transaction_entries_owner_transaction",
        source_table="transaction_entries",
        referent_table="transactions",
        local_cols=["transaction_id", "owner_id"],
        remote_cols=["id", "user_id"],
        source_schema="budget",
        referent_schema="budget",
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        constraint_name="fk_transaction_entries_owner_account",
        source_table="transaction_entries",
        referent_table="accounts",
        local_cols=["account_id", "owner_id"],
        remote_cols=["id", "user_id"],
        source_schema="budget",
        referent_schema="budget",
        ondelete="RESTRICT",
    )
    # The count is the migration's own measurement, printed so the operator
    # can compare it with the clone rehearsal's.
    movements = bind.execute(sa.text(_MOVEMENTS_SQL)).scalar()
    print(
        "CC-5-1: dropped fk_transaction_entries_parent_account; "
        f"{movements} movement(s) took their row's owner."
    )


def downgrade():
    """Restore the co-location, refusing while any movement is off its row's account."""
    bind = op.get_bind()
    refuse_cross_account_movements(bind)
    for name in (
        "fk_transaction_entries_owner_account",
        "fk_transaction_entries_owner_transaction",
        "fk_transaction_entries_account_id",
    ):
        op.drop_constraint(
            name, "transaction_entries", schema="budget", type_="foreignkey",
        )
    op.drop_column("transaction_entries", "owner_id", schema="budget")
    op.drop_constraint(
        "uq_transactions_id_user", "transactions",
        schema="budget", type_="unique",
    )
    # The ``c4e8a2d7f1b3`` key exactly: CASCADE on both actions.
    op.create_foreign_key(
        "fk_transaction_entries_parent_account",
        "transaction_entries", "transactions",
        ["transaction_id", "account_id"], ["id", "account_id"],
        source_schema="budget", referent_schema="budget",
        ondelete="CASCADE",
        onupdate="CASCADE",
    )
