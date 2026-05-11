"""add account_id column to transactions

Revision ID: efffcf647644
Revises: 01214a4ff394
Create Date: 2026-03-25 20:31:32.786091

Review: solo developer, 2026-05-11 (audit 2026-04-15, C-40 backfill rewrite)

C-40 / F-026 backfill rewrite (2026-05-11):
The original form of this migration added ``budget.transactions.account_id``
as ``NOT NULL`` without a backfill, which crashes on any populated
database with ``IntegrityError: null value in column "account_id"
violates not-null constraint``.  Production has the column populated
via an out-of-band manual backfill that was never recorded in the
repository, so disaster-recovery replays from a pre-migration snapshot
and staging rebuilds against legacy data are broken.

The rewrite below replaces the single ``add_column(nullable=False)`` step
with the canonical safe three-step pattern documented in
``docs/coding-standards.md`` (Migrations -> "Add NOT NULL columns to
populated tables in three steps"):

  1. Add the column nullable so the ``ALTER TABLE`` succeeds on a
     populated table.
  2. Backfill via ``UPDATE`` with a deterministic, well-documented
     derivation that resolves to the user's chosen default grid
     account, then a Checking account, then any active account.
  3. Verify every row was resolved, then tighten to NOT NULL, attach
     the supporting index, and add the foreign key.

The backfill is idempotent (``WHERE account_id IS NULL``) so re-running
on an already-populated database is a no-op.  Alembic does not re-run
applied migrations on ``flask db upgrade``, so the rewrite only takes
effect on fresh-database bring-ups (test template, dev provisioning,
disaster recovery from a pre-migration snapshot) -- production
databases that have already applied this migration are unaffected.

The COALESCE branches and the post-backfill ``NULL`` count are exposed
as module-level constants so the C-40 test suite can exercise the
exact same SQL the migration runs.  Two derivations of the same SQL
(one in the migration, one in tests) would silently drift; one
canonical string keeps them in lock-step.
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = 'efffcf647644'
down_revision = '01214a4ff394'
branch_labels = None
depends_on = None


# The backfill resolver: for each transaction row aliased as ``t``,
# return the account_id we should populate, derived from the owning
# user's account inventory.  COALESCE walks three tiers in order and
# returns the first non-NULL result.  Tier ordering encodes the
# application's notion of a "default" account, from most specific
# (user-chosen) to most generic (any active account):
#
#   Tier 1 -- ``auth.user_settings.default_grid_account_id``.  This
#   is the account the user explicitly chose as the default for the
#   pay-period grid (see ``app/models/user.py::UserSettings``).  The
#   d5e6f7a8b9c0 migration that introduced this column also
#   backfilled it for pre-existing users; the column may still be
#   NULL when the user has not picked a default and the historical
#   backfill could not resolve one (the d5e6f7a8b9c0 backfill uses
#   a lowercase ``checking`` literal that does not match the
#   title-cased seed name -- a separate finding outside C-40's
#   scope; see audit notes).
#
#   Tier 2 -- the user's first active Checking account, sorted by
#   ``sort_order`` then ``id``.  Every user provisioned through the
#   normal auth flow has exactly one Checking account named
#   "Checking" (see ``app/services/auth_service.py:_create_user``),
#   so this tier is the canonical fallback when tier 1 is empty.
#   ``ref_at`` aliases ``ref.account_types`` to avoid colliding
#   with the SQL ``at`` shorthand if a future reader extends the
#   join.  Title-case ``'Checking'`` matches the canonical seed
#   data in ``app/ref_seeds.py``.
#
#   Tier 3 -- the user's first active account of any type, sorted
#   by ``sort_order`` then ``id``.  Covers the rare case where the
#   user has accounts but none is a Checking account (e.g. a user
#   who deleted the seeded Checking account and only has a Savings
#   account left).
#
# All three correlated subqueries reference ``t.pay_period_id`` from
# the outer query, joining through ``budget.pay_periods`` to recover
# the owning ``user_id``.  Tier 1 also references the unique
# constraint ``(user_id)`` on ``auth.user_settings`` so the subquery
# returns at most one row without an explicit ``LIMIT 1``; tiers 2
# and 3 do use ``LIMIT 1`` because the same user may have several
# active accounts.
RESOLVE_ACCOUNT_SQL = """\
COALESCE(
    (
        SELECT us.default_grid_account_id
        FROM auth.user_settings us
        JOIN budget.pay_periods pp ON pp.user_id = us.user_id
        WHERE pp.id = t.pay_period_id
          AND us.default_grid_account_id IS NOT NULL
    ),
    (
        SELECT a.id
        FROM budget.accounts a
        JOIN budget.pay_periods pp ON pp.user_id = a.user_id
        JOIN ref.account_types ref_at ON ref_at.id = a.account_type_id
        WHERE pp.id = t.pay_period_id
          AND a.is_active = TRUE
          AND ref_at.name = 'Checking'
        ORDER BY a.sort_order ASC, a.id ASC
        LIMIT 1
    ),
    (
        SELECT a.id
        FROM budget.accounts a
        JOIN budget.pay_periods pp ON pp.user_id = a.user_id
        WHERE pp.id = t.pay_period_id
          AND a.is_active = TRUE
        ORDER BY a.sort_order ASC, a.id ASC
        LIMIT 1
    )
)"""


# The full UPDATE that runs during the backfill step.  Filtered to
# rows where ``account_id`` is still NULL so the migration is
# idempotent: running it twice on the same database, or running it
# on a database that already has the column populated, is a no-op
# rather than a re-derivation that could change values out from
# under live data.
BACKFILL_SQL = (
    "UPDATE budget.transactions t\n"
    f"SET account_id = {RESOLVE_ACCOUNT_SQL}\n"
    "WHERE t.account_id IS NULL"
)


# Diagnostic SELECT shown to the operator if any rows survive the
# backfill with a NULL account_id.  Joined to pay_periods so the
# message names the owning user_id (the operator can then decide
# whether to provision an Account for that user, delete the orphan
# transactions, or restore the prior database state).
DIAGNOSTIC_SELECT = (
    "  SELECT t.id AS transaction_id, t.name, t.pay_period_id,\n"
    "         pp.user_id, t.estimated_amount\n"
    "  FROM budget.transactions t\n"
    "  JOIN budget.pay_periods pp ON pp.id = t.pay_period_id\n"
    "  WHERE t.account_id IS NULL\n"
    "  ORDER BY pp.user_id, t.pay_period_id, t.id"
)


def upgrade():
    """Add account_id FK to budget.transactions via the safe three-step pattern.

    Step 1 -- add the column as nullable so the ``ALTER TABLE``
    succeeds on a populated table.

    Step 2 -- backfill ``account_id`` from each row's owning user's
    account inventory via the COALESCE resolver in
    :data:`RESOLVE_ACCOUNT_SQL` (tier 1: ``default_grid_account_id``;
    tier 2: first active Checking account; tier 3: first active
    account of any type).

    Step 3 -- count rows that survived the backfill with a NULL
    ``account_id``.  If any did, raise a ``RuntimeError`` whose
    message embeds the diagnostic SELECT the operator can run to
    locate the unresolved rows.  The check runs *before* the
    ``ALTER ... NOT NULL`` so the operator hears the exact count and
    the diagnostic SELECT, not the cryptic PostgreSQL "null value in
    column ... violates not-null constraint" generic error.

    Step 4 -- tighten the column to NOT NULL, create the supporting
    index, and add the foreign key.  Each artefact is created in a
    separate ``op.*`` call so a database inspection cleanly tells the
    operator which artefact is present or missing if a step fails.
    """
    # Step 1: add nullable column so the ALTER succeeds on populated tables.
    op.add_column(
        'transactions',
        sa.Column('account_id', sa.Integer(), nullable=True),
        schema='budget',
    )

    # Step 2: best-effort backfill from the user's default grid account,
    # falling back to a Checking account, then to any active account.
    # Idempotent via the WHERE account_id IS NULL guard inside
    # BACKFILL_SQL: re-running the migration on an already-populated
    # table is a no-op rather than a re-derivation.
    op.execute(BACKFILL_SQL)

    # Step 3: bail loudly if any rows remained NULL.  The diagnostic
    # SELECT in the error message names every surviving row's
    # transaction_id, pay_period_id, and user_id so the operator can
    # decide whether to provision the missing account, delete the
    # orphan row, or restore the prior database state and re-run.
    conn = op.get_bind()
    unresolved = conn.execute(sa.text(
        "SELECT count(*) FROM budget.transactions WHERE account_id IS NULL"
    )).scalar()
    if unresolved:
        raise RuntimeError(
            f"{unresolved} transactions could not be backfilled with an "
            "account_id (no auth.user_settings.default_grid_account_id "
            "is set, no active Checking account exists, and no active "
            "account of any type exists for the owning user).  "
            "Resolve manually before re-running this migration:\n"
            "\n"
            f"{DIAGNOSTIC_SELECT};"
        )

    # Step 4: tighten the column, attach the supporting index, and add
    # the foreign key.  The trio is split into discrete op.* calls so
    # the operator can inspect ``information_schema.columns``,
    # ``pg_indexes``, and ``pg_constraint`` to locate the exact
    # artefact that failed if the ALTER + CREATE INDEX + ADD
    # CONSTRAINT chain aborts partway through.
    op.alter_column(
        'transactions', 'account_id',
        nullable=False, schema='budget',
    )
    op.create_index(
        'idx_transactions_account',
        'transactions',
        ['account_id'],
        unique=False,
        schema='budget',
    )
    op.create_foreign_key(
        'fk_transactions_account_id',
        'transactions', 'accounts',
        ['account_id'], ['id'],
        source_schema='budget',
        referent_schema='budget',
    )


def downgrade():
    """Remove account_id column and its dependent index and FK.

    The drop order is the inverse of the upgrade: FK first (so the
    column can be dropped), then the index (so PostgreSQL does not
    cascade-drop it implicitly with a different name), then the
    column itself.  The downgrade is irreversibly destructive of
    every row's ``account_id`` value -- which is by design, because
    a downgrade is only run when reverting to a pre-account_id
    application revision where the column has no meaning.
    """
    op.drop_constraint(
        'fk_transactions_account_id',
        'transactions',
        schema='budget',
        type_='foreignkey',
    )
    op.drop_index(
        'idx_transactions_account',
        table_name='transactions',
        schema='budget',
    )
    op.drop_column('transactions', 'account_id', schema='budget')
