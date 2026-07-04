"""re-key ledger_accounts account unique to (account_id, kind_id)

Revision ID: b7d9f3a1c5e8
Revises: a4c8e2f6b1d3
Create Date: 2026-07-03 13:00:00.000000

Review: Josh, 2026-07-03 (destructive: drops the ``uq_ledger_accounts_account``
partial unique index and replaces it with ``uq_ledger_accounts_account_kind``;
approved via ``implementation_plan_actuals_reporting.md``, Commit C3).

Actuals reporting, Commit C3 (Build-Order Step 5; see
``docs/audits/balance_architecture/implementation_plan_actuals_reporting.md``).

Step 5 introduces a second account-linked ledger-account kind: the per-account
``anchor_equity`` Equity row (the counter-leg of a non-loan account's
``account_opening`` / ``account_trueup`` corrections) shares the ``account_id``
column with the existing ``linked`` row.  The original Step-2 partial unique --
``UNIQUE (account_id) WHERE account_id IS NOT NULL`` -- permits only ONE row
per account, so it must be re-keyed to ``(account_id, kind_id)``: exactly one
``linked`` row and at most one ``anchor_equity`` row per real account, never
two of either.  Both key columns are NOT NULL within the predicate's scope
(``kind_id`` is NOT NULL table-wide), so ordinary NULL-distinct unique
semantics apply cleanly; every non-account-linked kind carries NULL
``account_id`` and falls outside the partial index, exactly as before.

The model's ``__table_args__`` (``app/models/ledger_account.py`` -- the
``uq_ledger_accounts_account_kind`` ``db.Index``) matches this DDL
byte-for-byte so autogenerate produces no spurious diff.

**Safety of the upgrade.**  Strictly widening: every row set that satisfied
the old single-column unique satisfies the two-column one (adding a key
column can only make duplicates rarer), so the CREATE can never fail on
existing data and no data is touched.

**Downgrade.**  Fails LOUD if any ``anchor_equity`` row exists: narrowing the
key back to ``account_id`` alone would raise a duplicate-key error on the
first account carrying both rows -- an opaque failure deep in index creation.
Unreachable via the linear chain (the Step-5 data-boundary migration's
downgrade removes every ``anchor_equity`` row first, and the sole writer only
mints them after this revision), so the guard defends against out-of-band /
partial downgrades only.  The recreated index reproduces the Step-2 DDL
(``b82538084d24``) exactly.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'b7d9f3a1c5e8'
down_revision = 'a4c8e2f6b1d3'
branch_labels = None
depends_on = None


# Downgrade guard: count surviving ``anchor_equity`` rows, resolving the kind
# id BY NAME at run time (ref IDs are never hardcoded).  A missing ref row
# yields zero matches -- correct, since without the kind row no ledger row can
# reference it (RESTRICT FK).
_COUNT_ANCHOR_EQUITY_ROWS_SQL = (
    "SELECT COUNT(*) FROM budget.ledger_accounts la "
    "JOIN ref.ledger_account_kinds k ON k.id = la.kind_id "
    "WHERE k.name = 'anchor_equity'"
)


def upgrade():
    """Replace the single-column account unique with (account_id, kind_id)."""
    op.drop_index(
        "uq_ledger_accounts_account", table_name="ledger_accounts",
        schema="budget", postgresql_where=sa.text("account_id IS NOT NULL"),
    )
    op.create_index(
        "uq_ledger_accounts_account_kind", "ledger_accounts",
        ["account_id", "kind_id"],
        unique=True, schema="budget",
        postgresql_where=sa.text("account_id IS NOT NULL"),
    )


def downgrade():
    """Restore the Step-2 single-column unique; fail loud if twins exist."""
    survivors = op.get_bind().execute(
        sa.text(_COUNT_ANCHOR_EQUITY_ROWS_SQL)
    ).scalar()
    if survivors:
        raise RuntimeError(
            f"cannot downgrade b7d9f3a1c5e8: {survivors} anchor_equity "
            f"ledger-account row(s) still exist, and the single-column "
            f"uq_ledger_accounts_account unique cannot hold two rows per "
            f"account.  Downgrade the Step-5 data-boundary migration first "
            f"(it deletes the account corrections and their anchor_equity "
            f"rows).  Diagnostic: {_COUNT_ANCHOR_EQUITY_ROWS_SQL}"
        )
    op.drop_index(
        "uq_ledger_accounts_account_kind", table_name="ledger_accounts",
        schema="budget", postgresql_where=sa.text("account_id IS NOT NULL"),
    )
    op.create_index(
        "uq_ledger_accounts_account", "ledger_accounts", ["account_id"],
        unique=True, schema="budget",
        postgresql_where=sa.text("account_id IS NOT NULL"),
    )
