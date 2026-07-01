"""add ledger_accounts.kind_id discriminator and loan_account_id

Revision ID: efca4315bf81
Revises: f8e025a8be41
Create Date: 2026-06-30 14:00:00.000000

Review: solo developer, 2026-06-30 (Build-Order Step 4, Commit 2; adds the
NOT NULL ``kind_id`` row-kind discriminator via a three-step backfill, the
nullable ``loan_account_id`` link, a partial unique, and a shape CHECK -- no
drops on upgrade; the downgrade drops those new objects and columns)

Build-Order Step 4, Commit 2 (post confirmed loan payments with their real
principal / interest / escrow split; see
``docs/audits/balance_architecture/implementation_plan_posting_ledger_loan_payments.md``).

Turns the implicit NULL-pattern row taxonomy of ``budget.ledger_accounts``
into an explicit, positive ``kind_id`` discriminator and adds the per-loan
link the Step-4 interest / escrow / refund chart rows need.

  * **budget.ledger_accounts.kind_id** -- a NOT NULL FK (RESTRICT) into the
    ``ref.ledger_account_kinds`` table the Commit-1 migration (f8e025a8be41,
    the down_revision) created and seeded.  Added in three steps on the
    populated table per the coding standard's NOT-NULL-on-a-populated-table
    rule: (1) add nullable; (2) backfill every existing row's kind from its
    column shape -- the same four-way mapping the row taxonomy already encodes
    (``account_id`` set -> ``linked``; else ``category_id`` set -> ``category``;
    else ``is_fallback`` -> ``fallback``; else -> ``orphan``); (3) verify zero
    NULLs remain (raise with a diagnostic SELECT otherwise) and
    ``ALTER ... SET NOT NULL``.  The mapping is exhaustive and unambiguous
    because the existing CHECKs (``ck_ledger_accounts_account_or_category_null``
    forbids both account_id and category_id; ``ck_ledger_accounts_fallback_shape``
    forbids is_fallback off the NULL/NULL shape) already partition every row
    into exactly one of those four shapes.  At this revision every row is a
    Step-2 linked or a Step-3 category / fallback / orphan row, so none maps to
    a loan kind.
  * **budget.ledger_accounts.loan_account_id** -- a NULLABLE FK (RESTRICT)
    into ``budget.accounts``, set only on the Step-4 per-loan interest /
    escrow / refund rows (none exist yet) and NULL on every other kind.
    RESTRICT (not SET NULL or CASCADE) so a loan account that has per-loan
    ledger rows cannot be deleted -- those rows accumulate immutable postings
    (see the model's FK-action rationale).
      - ``uq_ledger_accounts_loan`` -- partial unique on
        ``(user_id, loan_account_id, kind_id)`` WHERE ``loan_account_id IS
        NOT NULL``: at most one interest / escrow / refund account per
        (owner, loan).
      - ``ck_ledger_accounts_loan_shape`` -- CHECK ``loan_account_id IS NULL
        OR (account_id IS NULL AND category_id IS NULL AND NOT is_fallback)``:
        a per-loan row is ONLY a per-loan row (never also linked / category /
        fallback).  It deliberately does NOT also pin ``kind_id`` to the loan
        kinds -- a CHECK cannot subquery ``ref.ledger_account_kinds`` and the
        project forbids hardcoding its IDs; the sole writer guarantees the
        kind (see the model docstring's "Why ck_ledger_accounts_loan_shape
        does not pin kind_id").

**No new table, no trigger, no audit change.**  ``budget.ledger_accounts``
already exists and is already in ``AUDITED_TABLES`` (Step 2); adding
columns / indexes / constraints does not change a table's audited status, so
no trigger work is needed here.

The column / FK / index DDL was produced by ``flask db migrate`` against the
updated model in ``app/models/ledger_account.py`` (so a future autogenerate
run yields an empty diff); the CHECK was added by hand -- Alembic does not
autogenerate CHECK constraints, so it is neither emitted nor compared by
autogenerate and cannot produce a spurious diff.

**Self-contained dependency policy.**  Imports nothing from ``app``.  The
backfill resolves the four kind IDs by unique name
(``SELECT id FROM ref.ledger_account_kinds WHERE name = '<kind>'``) -- the
documented migration exception to IDs-for-logic, the same pattern the Step-2
(b82538084d24) and Step-3 (7d63529e4300) backfills use -- the names having
been seeded by the lower-revision f8e025a8be41.

**Downgrade.**  Drops the CHECK, the partial unique, the two FKs, and the two
columns (reverse of the upgrade).  Reversible: at this revision
``loan_account_id`` is unpopulated and ``kind_id`` is fully reproducible from
each row's column shape on a re-upgrade.  Run in the proper chain order -- the
Commit-6 historical backfill (a higher revision) creates the per-loan ledger
rows and the loan-payment entries that populate ``loan_account_id`` and the
``kind_id`` loan kinds, and must be downgraded first; once it is, no per-loan
row remains and this drop is clean.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'efca4315bf81'
down_revision = 'f8e025a8be41'
branch_labels = None
depends_on = None


# The shape -> kind mapping, as a SQL CASE returning the
# ``ref.ledger_account_kinds.id``.  Split out as its own constant so the
# Commit-2 backfill test can exercise the EXACT mapping the upgrade applies:
# at HEAD ``kind_id`` is already NOT NULL, so the backfill UPDATE's
# ``WHERE kind_id IS NULL`` guard can never match a real row, and the test
# evaluates this CASE as a SELECT over engineered rows of each shape instead.
# The four shapes are exhaustive and mutually exclusive -- the existing
# ``ck_ledger_accounts_account_or_category_null`` and
# ``ck_ledger_accounts_fallback_shape`` CHECKs guarantee a row sets at most one
# of (account_id, category_id) and is_fallback only on the NULL/NULL shape -- so
# the ordered WHEN clauses assign exactly one kind to every row.  Kind IDs are
# resolved by unique name (the documented migration IDs-for-logic exception).
_KIND_FROM_SHAPE_CASE_SQL = (
    "CASE "
    "WHEN account_id IS NOT NULL "
    "    THEN (SELECT id FROM ref.ledger_account_kinds WHERE name = 'linked') "
    "WHEN category_id IS NOT NULL "
    "    THEN (SELECT id FROM ref.ledger_account_kinds WHERE name = 'category') "
    "WHEN is_fallback "
    "    THEN (SELECT id FROM ref.ledger_account_kinds WHERE name = 'fallback') "
    "ELSE (SELECT id FROM ref.ledger_account_kinds WHERE name = 'orphan') "
    "END"
)

# Step 2 of the three-step NOT NULL add: backfill every existing row's
# ``kind_id`` from its column shape.  Idempotent (``WHERE kind_id IS NULL``), so
# a re-run after a partial failure only touches still-unstamped rows.
_BACKFILL_KIND_ID_SQL = (
    f"UPDATE budget.ledger_accounts SET kind_id = ({_KIND_FROM_SHAPE_CASE_SQL}) "
    "WHERE kind_id IS NULL"
)

# Step 3 guard: count any row the backfill failed to stamp.  A non-zero count
# aborts the migration with a diagnostic SELECT rather than letting the NOT NULL
# ALTER raise an opaque error (the coding standard's
# NOT-NULL-on-a-populated-table rule).
_COUNT_NULL_KIND_ID_SQL = (
    "SELECT count(*) FROM budget.ledger_accounts WHERE kind_id IS NULL"
)


def upgrade():
    """Add kind_id (3-step NOT NULL) + loan_account_id + the loan unique/CHECK.

    Ordered: add both nullable columns and their RESTRICT FKs; backfill
    ``kind_id`` from each row's shape; verify no NULL survives (raise with a
    diagnostic otherwise) and tighten ``kind_id`` to NOT NULL; then add the
    per-loan partial unique and the shape CHECK (which reference the new
    columns, so they follow them).  See the module docstring for the full
    rationale.
    """
    op.add_column(
        'ledger_accounts', sa.Column('kind_id', sa.Integer(), nullable=True),
        schema='budget',
    )
    op.add_column(
        'ledger_accounts',
        sa.Column('loan_account_id', sa.Integer(), nullable=True),
        schema='budget',
    )
    op.create_foreign_key(
        'fk_ledger_accounts_kind_id', 'ledger_accounts', 'ledger_account_kinds',
        ['kind_id'], ['id'],
        source_schema='budget', referent_schema='ref', ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'fk_ledger_accounts_loan_account_id', 'ledger_accounts', 'accounts',
        ['loan_account_id'], ['id'],
        source_schema='budget', referent_schema='budget', ondelete='RESTRICT',
    )

    # Three-step NOT NULL: backfill from shape, verify zero NULLs, then tighten.
    op.execute(_BACKFILL_KIND_ID_SQL)
    remaining = op.get_bind().execute(
        sa.text(_COUNT_NULL_KIND_ID_SQL)
    ).scalar()
    if remaining:
        raise RuntimeError(
            f"cannot set budget.ledger_accounts.kind_id NOT NULL: {remaining} "
            f"row(s) still have a NULL kind_id after the shape backfill. "
            f"Inspect with: SELECT id, account_id, category_id, is_fallback, "
            f"loan_account_id FROM budget.ledger_accounts WHERE kind_id IS NULL"
        )
    op.alter_column(
        'ledger_accounts', 'kind_id', existing_type=sa.Integer(),
        nullable=False, schema='budget',
    )

    op.create_index(
        'uq_ledger_accounts_loan', 'ledger_accounts',
        ['user_id', 'loan_account_id', 'kind_id'], unique=True, schema='budget',
        postgresql_where=sa.text('loan_account_id IS NOT NULL'),
    )
    op.create_check_constraint(
        'ck_ledger_accounts_loan_shape', 'ledger_accounts',
        'loan_account_id IS NULL OR (account_id IS NULL AND '
        'category_id IS NULL AND NOT is_fallback)',
        schema='budget',
    )


def downgrade():
    """Drop the CHECK, the unique, the two FKs, and the two columns.

    Reverse order of the upgrade: dependent objects (the CHECK, the index, the
    FKs) come down before their columns.  Reversible (see the module
    docstring): ``loan_account_id`` is unpopulated at this revision and
    ``kind_id`` is reproducible from each row's shape on a re-upgrade.
    """
    op.drop_constraint(
        'ck_ledger_accounts_loan_shape', 'ledger_accounts',
        schema='budget', type_='check',
    )
    op.drop_index(
        'uq_ledger_accounts_loan', table_name='ledger_accounts',
        schema='budget', postgresql_where=sa.text('loan_account_id IS NOT NULL'),
    )
    op.drop_constraint(
        'fk_ledger_accounts_loan_account_id', 'ledger_accounts',
        schema='budget', type_='foreignkey',
    )
    op.drop_constraint(
        'fk_ledger_accounts_kind_id', 'ledger_accounts',
        schema='budget', type_='foreignkey',
    )
    op.drop_column('ledger_accounts', 'loan_account_id', schema='budget')
    op.drop_column('ledger_accounts', 'kind_id', schema='budget')
