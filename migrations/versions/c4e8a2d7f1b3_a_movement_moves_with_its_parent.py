"""a movement moves with its parent

Revision ID: c4e8a2d7f1b3
Revises: 6c15d2a97b78
Create Date: 2026-09-16 14:00:00.000000

Plan step **balance:X-bi-3c** (leaf 3c of ``X-bi-3``), under ruling
**R-BAL46** (developer, 2026-09-16)::

    fk_transaction_entries_parent_account    ON DELETE CASCADE  ->  + ON UPDATE CASCADE

**One key's update rule and nothing else; no figure moves, no row is
touched.**  ``budget.transaction_entries (transaction_id, account_id)`` keys
onto ``budget.transactions (id, account_id)`` so that a movement's account IS
its parent's -- *guaranteed rather than maintained*, ``d5b8e2c74a19``'s
words -- and until this revision the guarantee held for a parent that is
DELETED (the movement goes with it) and REFUSED a parent whose account
CHANGES.  No parent's account could change while every movement was a
purchase: an ordinary transaction never moves accounts, and ``entry_service``
refuses a purchase on a transfer shadow, the one kind of row whose account
does move (``transfer_service._endpoints._apply_endpoint_move``, plan step
R10-b: re-pointing a settled payment from one loan to the other).

X-bi-3c makes a settled transfer's two shadows carry a covering movement
each, through the same status seam that covers a bill and a paycheck (rulings
**R-BAL39**, **R-BAL41**).  Re-pointing such a transfer then asks PostgreSQL
to UPDATE the parent's ``account_id`` while a movement still references the
old pair, and a non-deferrable key checks that at the parent's statement --
before any child UPDATE the same flush would issue, because SQLAlchemy writes
a parent before its children.  Measured on the tree with the seam's transfer
gate deleted and nothing else: five of the endpoint-move cases refused with
``update or delete on table "transactions" violates foreign key constraint
"fk_transaction_entries_parent_account"``.

**``ON UPDATE CASCADE`` is the key stating on UPDATE what it already states
on DELETE.**  A movement's account is a co-located key, ``account_id``'s own
pattern on this table (ruling **R-BAL35** names it), and a co-located key is
held by the database cascading the parent's move -- so no writer can move a
parent without its movements, and a writer that forgets is not a defect the
suite has to catch.  ``_apply_endpoint_move`` assigns the movements' account
as well, for the ORM's sake alone: the session never learns what a cascade
wrote, so the assignment keeps the loaded objects agreeing with the database
within the same request.  Rejected (the design loop, 2026-09-16): a
``DEFERRABLE INITIALLY DEFERRED`` key, which refuses a disagreeing pair at
COMMIT rather than at the door that wrote it; releasing the movements before
the move and re-covering after, which churns the record's identity on every
move and teaches the endpoints leaf the seam's lifecycle; refusing an endpoint
move of a settled transfer, which deletes R10-b's shipped contract.

**What the cascade does NOT loosen.**  A movement carrying a clearing link
names a statement of the account it was on (``fk_transaction_entries_
reconciled_by``, composite over ``account_id``, ``ON DELETE RESTRICT``); a
cascade onto another account leaves that pair naming a statement the new
account never had, and the referencing key refuses the cascaded UPDATE.  That
is the shadow's own behaviour today -- its ``fk_transactions_reconciled_by``
refuses the same move for the same reason (ledger row **BAL-503**) -- and the
movement inherits exactly it, adding no failure the parent does not have.

**What it DOES loosen, stated so the trade is visible** (adversarial review,
2026-09-16).  The ``NO ACTION`` key refused EVERY parent-account move that
left an entry behind, an ordinary envelope's purchases included; the cascade
admits one and carries the purchases with it.  No writer makes that move
today -- an ordinary transaction's account never changes, and the recurrence
engine's maintain pass RETAINS a generated row that holds a purchase rather
than moving it (``recurrence_engine/_maintain._rows_holding_owner_records``,
finding N-292, graded by its own account-move case) -- so what the cascade
decides for an envelope is a rule about an act nothing performs, and the
rule it states is the key's own: a movement's account IS its parent's.  The
one beneficiary is the transfer shadow, and ``X-bi-6`` deletes shadow rows;
after it no parent's account can move at all, and the cascade is a fence with
nothing to fence.  ``X-bi-6`` restores the ``NO ACTION`` rule with the rows
it deletes, so the key states exactly what the schema can then express.

The downgrade recreates the key with its DELETE rule alone, the
``d5b8e2c74a19`` state exactly.  It refuses nothing and moves nothing: a key
with a narrower update rule admits every row the wider one did.
"""
from alembic import op


# Revision identifiers, used by Alembic.
revision = "c4e8a2d7f1b3"
down_revision = "6c15d2a97b78"
branch_labels = None
depends_on = None


#: The key this revision re-states, spelled once for both directions.
_KEY = "fk_transaction_entries_parent_account"


def _recreate_parent_account_key(*, onupdate):
    """Drop and recreate the co-located parent-account key with *onupdate*.

    PostgreSQL has no ``ALTER CONSTRAINT`` for a referential action, so the
    key is dropped and created again in one transaction; every row on the
    table satisfies it in both directions (the rule only changes what a
    parent's UPDATE does), so the create takes without a scan of anything but
    the rows it already held.

    Args:
        onupdate: The ``ON UPDATE`` action, ``"CASCADE"`` on the way up and
            ``None`` (PostgreSQL's ``NO ACTION``) on the way down.
    """
    op.drop_constraint(_KEY, "transaction_entries", schema="budget", type_="foreignkey")
    op.create_foreign_key(
        _KEY,
        "transaction_entries", "transactions",
        ["transaction_id", "account_id"], ["id", "account_id"],
        source_schema="budget", referent_schema="budget",
        ondelete="CASCADE",
        onupdate=onupdate,
    )


def upgrade():
    """A movement follows its parent's account: ``ON UPDATE CASCADE``."""
    _recreate_parent_account_key(onupdate="CASCADE")


def downgrade():
    """Back to the DELETE rule alone (the ``d5b8e2c74a19`` key)."""
    _recreate_parent_account_key(onupdate=None)
