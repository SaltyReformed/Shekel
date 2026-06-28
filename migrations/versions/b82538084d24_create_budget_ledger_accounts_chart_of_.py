"""create budget.ledger_accounts chart of accounts + backfill

Revision ID: b82538084d24
Revises: f5037400dc5e
Create Date: 2026-06-28 15:22:29.370727

Review: solo developer, 2026-06-28 (Build-Order Step 2, Commit 2; new
audited table ``budget.ledger_accounts``)

Build-Order Step 2, Commit 2 (posting ledger + chart of accounts, piloted
on transfers; see
``docs/audits/balance_architecture/implementation_plan_posting_ledger_transfers.md``).

Creates ``budget.ledger_accounts`` -- the chart of accounts for the
double-entry posting ledger -- and pairs every existing real account with
exactly one Asset or Liability ledger account so that the Commit-3 ledger
tables have somewhere to post.  Nothing reads or writes postings yet; this
step only materialises the directory and its go-forward + historical
pairing.

Four steps in order:

  1. **Create budget.ledger_accounts.**  Columns and constraints match the
     SQLAlchemy model in ``app/models/ledger_account.py`` exactly (the
     DDL was produced by ``flask db migrate`` against that model, so a
     future autogenerate run yields an empty diff): a NOT NULL ``class_id``
     FK (RESTRICT) into ``ref.ledger_account_classes``, a NULLABLE
     ``account_id`` FK (CASCADE) into ``budget.accounts`` (set on linked
     rows, NULL on the Income/Expense/Equity rows later steps add), a
     NULLABLE display ``name``, the ``ck_ledger_accounts_name_present``
     CHECK (a row carries a ``name`` or an ``account_id`` or both), a
     partial unique index enforcing one ledger account per real account,
     and a ``user_id`` ownership index.

  2. **Attach the audit trigger.**  Manual DROP TRIGGER IF EXISTS + CREATE
     TRIGGER for just the new table, following the ``d3d25212504b``
     precedent (NOT ``apply_audit_infrastructure``).  The shared trigger
     function ``system.audit_trigger_func`` already exists from the
     rebuild migration ``a5be2a99ea14`` earlier in the chain; that earlier
     migration re-runs ``apply_audit_infrastructure`` against the CURRENT
     in-code ``AUDITED_TABLES`` -- which now references
     ``budget.ledger_accounts`` -- but its CREATE TRIGGER is guarded by an
     ``IF EXISTS`` check against ``pg_class`` and quietly no-ops on a
     from-scratch replay where this table does not exist yet.  This narrow
     manual attach is what guarantees the trigger lands on the table.  The
     entrypoint trigger-count health check picks up the new total
     automatically because ``EXPECTED_TRIGGER_COUNT = len(AUDITED_TABLES)``.

  3. **Backfill one ledger account per existing account.**  For every
     ``budget.accounts`` row without a ledger account yet, insert one
     linked row deriving the ledger class from the account-type category:
     a Liability-category account maps to the Liability ledger class;
     every other category (Asset, Retirement, Investment) maps to the
     Asset ledger class.  ``name`` is omitted (defaults to NULL -- a
     linked row derives its display label from ``account.name``).
     Idempotent via ``WHERE NOT EXISTS`` on the natural ``account_id`` key,
     so a re-run after a partial failure inserts nothing new.  This
     reproduces the exact mapping the go-forward sync hook
     (``app/services/ledger_account_service.py``) applies, so historical
     and new ledger accounts agree -- the production-wide reconciliation
     oracle (Commit 6) depends on that agreement.

**Self-contained dependency policy.**  This migration imports nothing from
``app`` -- not models, not services, not ``ref_cache``.  All schema reads
go through raw SQL against the catalog.  String-name lookups on the ref
tables (``ref.account_type_categories.name = 'Liability'``,
``ref.ledger_account_classes.name``) are acceptable inside a migration --
the project-wide "IDs for logic, strings for display only" rule governs
application code, which runs above the ref-cache layer; a schema-bootstrap
migration runs below it (``ref_cache`` is itself initialising) and must
survive aggressive refactors in app code, so it joins the ref catalogue
directly.  The class names have been seeded since the Commit-1 migration
``f5037400dc5e`` earlier in this chain.

**Downgrade.**  Drops the two indexes and the table.  PostgreSQL drops the
table's audit trigger as a dependent object with it, so no explicit DROP
TRIGGER is required (the 33 other audit triggers and the shared trigger
function stay attached).  Reversible: the backfilled linked rows are fully
reproducible from ``budget.accounts`` on the next upgrade, so dropping them
loses nothing.  Any go-forward ledger accounts created between the upgrade
and the downgrade are NOT preserved across the downgrade (the table itself
is dropped) -- but they too regenerate from ``budget.accounts`` on
re-upgrade, identical to the originals.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'b82538084d24'
down_revision = 'f5037400dc5e'
branch_labels = None
depends_on = None


# Backfill SQL: one linked ledger account per existing real account.  The
# ledger class is derived from the account-type category -- a Liability
# category maps to the Liability class, every other category (Asset,
# Retirement, Investment) maps to the Asset class -- by joining
# ``ref.ledger_account_classes`` on the CASE-selected class name.  ``name``
# is omitted from the column list so it defaults to NULL (a linked row's
# display label derives from ``account.name``).  ``WHERE NOT EXISTS`` on
# the natural ``account_id`` key makes the statement idempotent against a
# partial re-run and against accounts already paired by the go-forward sync
# hook.  Exposed as a module constant so the Commit-2 backfill tests can
# re-run it on an engineered fixture without duplicating the text.
_BACKFILL_LEDGER_ACCOUNTS_SQL = (
    "INSERT INTO budget.ledger_accounts (user_id, class_id, account_id) "
    "SELECT a.user_id, lc.id, a.id "
    "  FROM budget.accounts a "
    "  JOIN ref.account_types t ON t.id = a.account_type_id "
    "  JOIN ref.account_type_categories cat ON cat.id = t.category_id "
    "  JOIN ref.ledger_account_classes lc "
    "    ON lc.name = CASE WHEN cat.name = 'Liability' "
    "                      THEN 'Liability' ELSE 'Asset' END "
    " WHERE NOT EXISTS ( "
    "     SELECT 1 FROM budget.ledger_accounts la "
    "      WHERE la.account_id = a.id "
    "   )"
)


def upgrade():
    """Create ``ledger_accounts`` + its audit trigger; backfill linked rows.

    Four-step forward migration (see module docstring for the full
    rationale).  The create + trigger-attach + backfill are each idempotent
    so the migration can re-run after a partial failure without duplicating
    a table, a trigger, or a ledger account.
    """
    # ── Step 1: budget.ledger_accounts ──────────────────────────────────
    op.create_table(
        "ledger_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.CheckConstraint(
            "name IS NOT NULL OR account_id IS NOT NULL",
            name="ck_ledger_accounts_name_present",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["budget.accounts.id"],
            name="fk_ledger_accounts_account_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["class_id"], ["ref.ledger_account_classes.id"],
            name="fk_ledger_accounts_class_id", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["auth.users.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="budget",
    )
    op.create_index(
        "idx_ledger_accounts_user", "ledger_accounts", ["user_id"],
        unique=False, schema="budget",
    )
    op.create_index(
        "uq_ledger_accounts_account", "ledger_accounts", ["account_id"],
        unique=True, schema="budget",
        postgresql_where=sa.text("account_id IS NOT NULL"),
    )

    # ── Step 2: attach the audit trigger ────────────────────────────────
    # DROP IF EXISTS + CREATE makes the attach idempotent against a re-run.
    # Trigger name ``audit_<table>`` matches the convention the entrypoint
    # trigger-count health check enumerates (``tgname LIKE 'audit_%'``).
    op.execute(
        "DROP TRIGGER IF EXISTS audit_ledger_accounts "
        "ON budget.ledger_accounts"
    )
    op.execute(
        "CREATE TRIGGER audit_ledger_accounts "
        "AFTER INSERT OR UPDATE OR DELETE ON budget.ledger_accounts "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )

    # ── Step 3: backfill one linked ledger account per existing account ──
    op.execute(_BACKFILL_LEDGER_ACCOUNTS_SQL)


def downgrade():
    """Drop the indexes and the table (its audit trigger drops with it).

    Reversible: the backfilled linked rows are fully reproducible from
    ``budget.accounts`` on the next upgrade.  PostgreSQL drops the
    ``audit_ledger_accounts`` trigger as a dependent object of the table,
    so no explicit DROP TRIGGER is needed; the other audit triggers and the
    shared ``system.audit_trigger_func`` are untouched.
    """
    op.drop_index(
        "uq_ledger_accounts_account", table_name="ledger_accounts",
        schema="budget", postgresql_where=sa.text("account_id IS NOT NULL"),
    )
    op.drop_index(
        "idx_ledger_accounts_user", table_name="ledger_accounts",
        schema="budget",
    )
    op.drop_table("ledger_accounts", schema="budget")
