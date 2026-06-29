"""add ledger_accounts.category_id and journal_entries.transaction_id

Revision ID: bdde62675c9b
Revises: 97bc03c2aa4c
Create Date: 2026-06-29 09:20:00.100665

Review: solo developer, 2026-06-29 (Build-Order Step 3, Commit 2; additive
``category_id`` / ``is_fallback`` / ``transaction_id`` columns, the
category/fallback SET-NULL FKs, three partial indexes, and two CHECKs -- no
data, no drops on upgrade; the downgrade drops those new objects and
columns)

Build-Order Step 3, Commit 2 (post confirmed cash transactions + cleared
envelope entries; see
``docs/audits/balance_architecture/implementation_plan_posting_ledger_cash_envelopes.md``).

Adds the two source/chart links the cash-posting steps need, alongside the
Step-2 ledger tables.  Purely additive: three nullable/defaulted columns,
three partial indexes, two SET-NULL FKs, and two CHECKs.  Nothing is written
to the new columns yet -- the lazy category resolver (Commit 3), the
go-forward poster (Commits 4-6), and the historical backfill (Commit 7)
populate them later.  The column/index/FK DDL was produced by ``flask db
migrate`` against the updated models in ``app/models/ledger_account.py`` /
``app/models/journal_entry.py`` (so a future autogenerate run yields an
empty diff); the two CHECKs were added by hand -- Alembic does not
autogenerate CHECK constraints, so they are neither emitted nor compared by
autogenerate and cannot produce a spurious diff.

  * **budget.ledger_accounts.category_id** -- a NULLABLE FK (SET NULL) into
    ``budget.categories``.  Set on the per-category Income/Expense chart
    rows Step 3 adds, NULL on linked Asset/Liability rows and on the
    per-user Uncategorized fallback / deleted-category orphan rows.  SET
    NULL (the same action ``budget.transactions.category_id`` uses) keeps
    the immutable-posting ledger account alive when its budgeting category
    is deleted, retaining the row's ``name`` snapshot.
  * **budget.ledger_accounts.is_fallback** -- BOOLEAN NOT NULL DEFAULT
    FALSE.  True ONLY on the per-(owner, class) Uncategorized fallback
    bucket; False on linked, category, and deleted-category *orphan* rows.
    The discriminator that confines the fallback singleton (below) to the
    true fallback: a deleted-category orphan is also ``(account_id NULL,
    category_id NULL)``, so WITHOUT this flag the ``category_id`` SET NULL
    would turn the orphan into a duplicate fallback and abort the category
    delete.  Existing rows are all linked (``is_fallback`` FALSE), so the
    static FALSE default fits every one and a single ``add_column`` with a
    ``server_default`` is safe on the populated table.
      - ``uq_ledger_accounts_category`` -- partial unique on
        ``(user_id, category_id, class_id)`` WHERE ``category_id IS NOT
        NULL AND account_id IS NULL``: one category ledger account per
        owner, category, and accounting class (a type-agnostic category
        used for both income and expense correctly yields two rows).
      - ``uq_ledger_accounts_uncategorized`` -- partial unique on
        ``(user_id, class_id)`` WHERE ``is_fallback``: exactly one fallback
        per owner per class.  Keyed on ``is_fallback`` (not ``category_id
        IS NULL``) so orphans stay outside it and coexist freely.
      - ``ck_ledger_accounts_account_or_category_null`` -- CHECK
        ``account_id IS NULL OR category_id IS NULL``: a row is linked to a
        real account OR a category bucket, never both.
      - ``ck_ledger_accounts_fallback_shape`` -- CHECK ``NOT is_fallback OR
        (account_id IS NULL AND category_id IS NULL)``: ``is_fallback`` is
        a true discriminator only on the NULL/NULL shape, so the fallback
        singleton cannot be subverted by a linked/category row.

  * **budget.journal_entries.transaction_id** -- a NULLABLE FK (SET NULL)
    into ``budget.transactions``, the non-transfer analog of Step 2's
    ``transfer_id``.  SET NULL so the immutable posted fact survives a
    source-transaction delete with only the back-link cleared.
      - ``idx_journal_entries_transaction`` -- partial index on
        ``(transaction_id)`` WHERE ``transaction_id IS NOT NULL``: the
        per-transaction lifecycle / reconcile-to-target lookup, mirroring
        ``idx_journal_entries_transfer``.

**No new table, no trigger, no audit change.**  Both tables already exist
and are already in ``AUDITED_TABLES`` (Step 2); adding columns/indexes does
not change a table's audited status, so no trigger work is needed here.

**Self-contained dependency policy.**  Imports nothing from ``app`` -- pure
Alembic / SQLAlchemy DDL.

**Downgrade.**  Drops the two CHECKs, the three indexes, the two FKs, and
the three columns (reverse of the upgrade).  Reversible: at this revision
the new columns are unpopulated, so dropping them loses nothing.  Run in the
proper chain order -- the Commit-7 backfill (a higher revision) creates the
category/fallback ledger-account rows and the transaction-sourced entries
that populate these columns and must be downgraded first; once it is, every
``transaction_id`` is NULL again and no category/fallback rows remain, so
this drop is clean.  A re-upgrade re-adds the columns/indexes/CHECKs
identically.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'bdde62675c9b'
down_revision = '97bc03c2aa4c'
branch_labels = None
depends_on = None


def upgrade():
    """Add the category / transaction source links, indexes, and CHECKs.

    Purely additive (see the module docstring).  Ordered per table:
    columns, then FK, then index(es), then -- for ledger_accounts -- the
    partition CHECKs (which reference the new columns, so they follow them).
    """
    # ── budget.ledger_accounts: the per-category chart link + fallback flag ─
    op.add_column(
        'ledger_accounts',
        sa.Column('category_id', sa.Integer(), nullable=True),
        schema='budget',
    )
    op.add_column(
        'ledger_accounts',
        sa.Column(
            'is_fallback', sa.Boolean(), nullable=False,
            server_default=sa.text('false'),
        ),
        schema='budget',
    )
    op.create_foreign_key(
        'fk_ledger_accounts_category_id', 'ledger_accounts', 'categories',
        ['category_id'], ['id'],
        source_schema='budget', referent_schema='budget', ondelete='SET NULL',
    )
    op.create_index(
        'uq_ledger_accounts_category', 'ledger_accounts',
        ['user_id', 'category_id', 'class_id'], unique=True, schema='budget',
        postgresql_where=sa.text('category_id IS NOT NULL AND account_id IS NULL'),
    )
    op.create_index(
        'uq_ledger_accounts_uncategorized', 'ledger_accounts',
        ['user_id', 'class_id'], unique=True, schema='budget',
        postgresql_where=sa.text('is_fallback'),
    )
    op.create_check_constraint(
        'ck_ledger_accounts_account_or_category_null', 'ledger_accounts',
        'account_id IS NULL OR category_id IS NULL', schema='budget',
    )
    op.create_check_constraint(
        'ck_ledger_accounts_fallback_shape', 'ledger_accounts',
        'NOT is_fallback OR (account_id IS NULL AND category_id IS NULL)',
        schema='budget',
    )

    # ── budget.journal_entries: the non-transfer source link ─────────────
    op.add_column(
        'journal_entries',
        sa.Column('transaction_id', sa.Integer(), nullable=True),
        schema='budget',
    )
    op.create_foreign_key(
        'fk_journal_entries_transaction_id', 'journal_entries', 'transactions',
        ['transaction_id'], ['id'],
        source_schema='budget', referent_schema='budget', ondelete='SET NULL',
    )
    op.create_index(
        'idx_journal_entries_transaction', 'journal_entries',
        ['transaction_id'], unique=False, schema='budget',
        postgresql_where=sa.text('transaction_id IS NOT NULL'),
    )


def downgrade():
    """Drop the columns, FKs, indexes, and CHECKs this migration added.

    Reverse order of the upgrade: dependent objects (indexes, FKs, the
    CHECKs) come down before their columns.  Reversible (see the module
    docstring): the columns are unpopulated at this revision.
    """
    # ── budget.journal_entries ───────────────────────────────────────────
    op.drop_index(
        'idx_journal_entries_transaction', table_name='journal_entries',
        schema='budget',
        postgresql_where=sa.text('transaction_id IS NOT NULL'),
    )
    op.drop_constraint(
        'fk_journal_entries_transaction_id', 'journal_entries',
        schema='budget', type_='foreignkey',
    )
    op.drop_column('journal_entries', 'transaction_id', schema='budget')

    # ── budget.ledger_accounts ───────────────────────────────────────────
    op.drop_constraint(
        'ck_ledger_accounts_fallback_shape', 'ledger_accounts',
        schema='budget', type_='check',
    )
    op.drop_constraint(
        'ck_ledger_accounts_account_or_category_null', 'ledger_accounts',
        schema='budget', type_='check',
    )
    op.drop_index(
        'uq_ledger_accounts_uncategorized', table_name='ledger_accounts',
        schema='budget', postgresql_where=sa.text('is_fallback'),
    )
    op.drop_index(
        'uq_ledger_accounts_category', table_name='ledger_accounts',
        schema='budget',
        postgresql_where=sa.text('category_id IS NOT NULL AND account_id IS NULL'),
    )
    op.drop_constraint(
        'fk_ledger_accounts_category_id', 'ledger_accounts',
        schema='budget', type_='foreignkey',
    )
    op.drop_column('ledger_accounts', 'is_fallback', schema='budget')
    op.drop_column('ledger_accounts', 'category_id', schema='budget')
