"""drop legacy budget.escrow_components (contract phase of the escrow redesign)

Revision ID: d7b2f9a4c1e6
Revises: c4f8a1b6e9d2
Create Date: 2026-07-06 19:00:00.000000

Review: SaltyReformed, 2026-07-06

The CONTRACT phase (Commit 2b) of the escrow config redesign
(``docs/design/escrow_line_identity_refactor.md``).  DESTRUCTIVE: it DROPS
``budget.escrow_components`` now that Commit 2a has repointed every escrow
consumer onto the supersession tables (``budget.escrow_lines`` +
``budget.escrow_component_versions``).  The expand migration ``c4f8a1b6e9d2``
already created and backfilled those tables byte-identically from
``escrow_components``, so at this point the legacy table is dead weight.

## Downgrade reconstructs the legacy ranges from the versions

The downgrade recreates ``escrow_components`` with its original schema (verified
against the live table) and rebuilds one ``[effective_date, end_date)`` range row
per REAL (non-tombstone) version, deriving ``end_date`` from the next version's
``effective_date`` via ``LEAD`` -- a tombstone successor closes the range
(a removal), a real successor closes it at the amount-change boundary, and the
last version stays open (``end_date IS NULL``, the active row).  This reproduces
"escrow as of any date D" exactly, the inverse of the ``c4f8a1b6e9d2`` backfill.

Documented, behaviorally-lossless caveats (per the design Sec. 11 and
``.claude/rules/database.md``):

  * A zero-length COLLAPSED row the ``c4f8a1b6e9d2`` backfill DROPPED (it
    contributed nothing) is not reconstructed -- lossless because it never
    affected any escrow figure.
  * ``created_at`` / ``updated_at`` on the reconstructed rows come from the
    versions (migration-time for backfilled data), not the original technical
    timestamps -- a non-financial audit column, behaviorally irrelevant.
  * A future MERGE (design step 6, not yet built) or the accepted decision-C
    concurrent-double-add race can leave two ACTIVE lines sharing a name; the
    reconstruction would then emit two ``end_date IS NULL`` rows for one
    ``(account_id, name)`` and trip ``uq_escrow_components_account_name_active``.
    Resolve such a collision by hand before downgrading.

The full executable upgrade->downgrade->upgrade round-trip was run during
development against the dev DB (real account-3 data): the downgrade reconstructed
``escrow_components`` reproducing every payment's $616.99 escrow, and the
re-upgrade dropped it cleanly.  The reconstruction is exposed at module scope so
its range derivation is exercised as a SELECT over engineered versions without
the DDL that would break sibling xdist workers.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "d7b2f9a4c1e6"
down_revision = "c4f8a1b6e9d2"
branch_labels = None
depends_on = None


# Reconstruct one legacy ``[effective_date, end_date)`` row per REAL version.
# ``LEAD`` over ALL of a line's versions (tombstones included, so a removal
# closes the preceding range) supplies ``end_date``; the outer ``WHERE`` keeps
# only real versions, so a tombstone becomes a closing boundary rather than a
# row.  The last real version with no successor stays open (active).
_RECONSTRUCT_SQL = (
    "INSERT INTO budget.escrow_components "
    "  (account_id, name, annual_amount, inflation_rate, effective_date, "
    "   end_date, created_at, updated_at) "
    "SELECT sub.account_id, sub.name, sub.annual_amount, sub.inflation_rate, "
    "       sub.effective_date, sub.end_date, sub.created_at, sub.updated_at "
    "FROM ( "
    "  SELECT l.account_id, l.name, v.annual_amount, v.inflation_rate, "
    "         v.effective_date, "
    "         LEAD(v.effective_date) OVER ( "
    "           PARTITION BY v.line_id ORDER BY v.effective_date "
    "         ) AS end_date, "
    "         v.created_at, v.updated_at, v.is_removed "
    "  FROM budget.escrow_component_versions v "
    "  JOIN budget.escrow_lines l ON l.id = v.line_id "
    ") sub "
    "WHERE sub.is_removed = false"
)


def reconstruct_escrow_components(bind) -> None:
    """Rebuild ``budget.escrow_components`` rows from the supersession versions.

    The inverse of the ``c4f8a1b6e9d2`` backfill's range->version mapping: one
    ``[effective_date, end_date)`` row per real version, ``end_date`` from the
    next version's ``effective_date`` (a tombstone closes the range, the last
    version stays open).  Assumes the (empty) ``escrow_components`` table already
    exists.  Exposed at module scope so the derivation is testable without DDL.

    Args:
        bind: A SQLAlchemy connection/bind exposing ``execute``
            (``op.get_bind()`` in the migration; the test session in tests).
    """
    bind.execute(sa.text(_RECONSTRUCT_SQL))


def upgrade():
    """Drop ``budget.escrow_components`` (its audit trigger drops with it).

    DESTRUCTIVE.  Every reader/writer was repointed onto the supersession tables
    in Commit 2a, so nothing references this table at head.
    """
    op.execute(
        "DROP TRIGGER IF EXISTS audit_escrow_components "
        "ON budget.escrow_components"
    )
    op.drop_table("escrow_components", schema="budget")


def downgrade():
    """Recreate ``escrow_components`` and reconstruct its rows from the versions.

    Rebuilds the original schema (columns, checks, indexes, FK, audit trigger --
    verified against the live table) then reconstructs the legacy ranges via
    :func:`reconstruct_escrow_components`.  See the module docstring for the
    behaviorally-lossless caveats (dropped collapsed rows, version-derived
    timestamps, merge/L1 duplicate-name collisions).
    """
    op.create_table(
        "escrow_components",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("annual_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("inflation_rate", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "effective_date", sa.Date(),
            server_default=sa.text("CURRENT_DATE"), nullable=False,
        ),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"], ["budget.accounts.id"], ondelete="CASCADE",
            name="escrow_components_account_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= effective_date",
            name="ck_escrow_components_date_range",
        ),
        sa.CheckConstraint(
            "annual_amount >= 0",
            name="ck_escrow_components_nonneg_annual_amount",
        ),
        sa.CheckConstraint(
            "inflation_rate IS NULL OR "
            "(inflation_rate >= 0 AND inflation_rate <= 1)",
            name="ck_escrow_components_valid_inflation_rate",
        ),
        schema="budget",
    )
    op.create_index(
        "ix_escrow_components_account_effective", "escrow_components",
        ["account_id", "effective_date", "end_date"], schema="budget",
    )
    op.create_index(
        "uq_escrow_components_account_name_active", "escrow_components",
        ["account_id", "name"], unique=True, schema="budget",
        postgresql_where=sa.text("end_date IS NULL"),
    )
    op.execute(
        "DROP TRIGGER IF EXISTS audit_escrow_components "
        "ON budget.escrow_components"
    )
    op.execute(
        "CREATE TRIGGER audit_escrow_components "
        "AFTER INSERT OR UPDATE OR DELETE ON budget.escrow_components "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )
    reconstruct_escrow_components(op.get_bind())
