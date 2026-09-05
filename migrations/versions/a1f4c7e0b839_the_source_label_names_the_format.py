"""the source label names the format, not a column it may not have

Plan step **bank_import:X-gc**.  ``ref.statement_sources.display_name`` is the
text of the only control that chooses a parser -- the *Export kind* select on
the statement upload form -- and it read
``SECU checking -- CSV with running balance``.

**That names the format by a column the export no longer offers.**  SECU
stopped publishing the per-line running balance between the developer's
2026-07-19 and 2026-08-16 pulls: every pull from 2026-08-16 onward carries no
balance column at all, its header being ``Date, Account, Account Number,
Account Type, Description, Check #, Category, Memo, Credit, Debit``.  (The
2026-07-19 pull on disk still carries ``Running Balance``, which is why the
label names the FORMAT rather than asserting the column is gone.)  The label
also contradicted the help text rendered directly beneath it, which has said
the column is optional since plan step ``bank_import:X-f6e-1``: a file
carrying none imports fine, and 376 lines of that 2026-08-20 export did.

**A DATA migration is what it takes, because the seeder cannot do it.**
``app.ref_seeds._seed_other_ref_tables`` INSERTs missing rows and leaves
present ones alone -- unlike ``_seed_account_types``, which refreshes metadata
in place -- so editing ``ref_seeds.py`` alone would change a fresh bootstrap
and leave every existing database saying the old thing.  Both halves are
therefore changed together, which is this project's established dual-seed
pattern for a ref row (see ``4c1f8b7e2a90``, ``c7d31f9a45e8``,
``97bc03c2aa4c``): the migration states what happens to a database that
already exists, and ``ref_seeds`` states what a new one is born with.

**It is keyed by ``name``, never by id.**  ``name`` is the enum ``.value`` and
the one stable identity a ref row has; ``id`` is a sequence value that differs
between databases, and this project's rule is IDs for logic within one
database and never across the schema's own history.

**The downgrade restores a literal recorded here** rather than a value derived
from anything, so a database created BEFORE this revision -- whose seed row
came from ``3f408018a71c``'s inline INSERT -- is returned to precisely what it
said.  A database first created AFTER it never held the old label and the
downgrade writes a string it has never carried; that is harmless on display
copy and is stated rather than claimed away.

Revision ID: a1f4c7e0b839
Revises: e5b2c8a17d34
Create Date: 2026-08-25 15:20:00.000000
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'a1f4c7e0b839'
down_revision = 'e5b2c8a17d34'
branch_labels = None
depends_on = None

#: The ref row this migration re-labels, by its stable ``name``.
_SOURCE_NAME = 'secu_checking_csv'

#: What it said before this migration, restored verbatim by the downgrade.
_OLD_LABEL = 'SECU checking -- CSV with running balance'

#: What it says after: the FORMAT at the INSTITUTION, which is what
#: :class:`app.enums.StatementSourceEnum` says a member names.
_NEW_LABEL = 'SECU checking -- CSV export'

_RELABEL = sa.text("""
    UPDATE ref.statement_sources SET display_name = :label WHERE name = :name
""")


def _relabel(label: str) -> None:
    """Point the SECU CSV source's display label at *label*.

    **A row that is absent is not an error and is not invented here.**  The
    seed row is created by migration ``3f408018a71c`` and by
    ``app.ref_seeds``; a database that has neither is one this migration has
    nothing to say about, and an INSERT here would be a third writer of a row
    two already own.

    Args:
        label: The display text to store.
    """
    op.get_bind().execute(
        _RELABEL, {"label": label, "name": _SOURCE_NAME},
    )


def upgrade():
    """Re-label the SECU CSV source so it names the format alone."""
    _relabel(_NEW_LABEL)


def downgrade():
    """Restore the label verbatim, including the column claim it carried."""
    _relabel(_OLD_LABEL)
