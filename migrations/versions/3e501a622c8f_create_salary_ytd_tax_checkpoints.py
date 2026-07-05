"""create salary.ytd_tax_checkpoints

Revision ID: 3e501a622c8f
Revises: c9f2e6a4b1d8
Create Date: 2026-07-04 19:52:37.504156

Analytics Taxes slice, phase T-P2 (YTD tax checkpoint).  Adds one new
audited table, ``salary.ytd_tax_checkpoints``, holding the year-to-date
gross + four withholding figures a user reads off a real pay stub.  The
withholding-to-date producer anchors the refund estimate on the latest
checkpoint in the tax year and models only the remaining periods.

Purely ADDITIVE: a single new table with named CHECK / UNIQUE
constraints and its audit trigger.  No Review line is required (no drop,
rename, type change, or constraint removal).  DDL matches the SQLAlchemy
model in ``app/models/ytd_tax_checkpoint.py`` exactly (produced by
``flask db migrate`` against that model, so a future autogenerate run
yields an empty diff): the ``salary_profile_id`` FK is NOT NULL CASCADE
(a deleted profile disposes of its checkpoints), each of the five money
figures is a non-negative ``Numeric(12, 2)`` with a named CHECK, each
withholding line additionally CHECKs ``<= ytd_gross``, and
``(salary_profile_id, as_of_date)`` is unique so the update-from-stub
form's upsert is well-defined.  The ``salary_profile_id`` FK is left
unnamed here on purpose: it mirrors the mixin FKs on ``journal_entries``
(e.g. ``journal_entries_user_id_fkey``), which ship under PostgreSQL's
default ``<table>_<column>_fkey`` name for the same reason.

Two steps in order:

  1. **Create salary.ytd_tax_checkpoints.**

  2. **Attach the audit trigger.**  Manual ``DROP TRIGGER IF EXISTS`` +
     ``CREATE TRIGGER`` following the ``db239773c2fd`` precedent (NOT
     ``apply_audit_infrastructure``).  The shared
     ``system.audit_trigger_func`` already exists from the rebuild
     migration ``a5be2a99ea14`` earlier in the chain; that migration
     re-runs ``apply_audit_infrastructure`` against the current in-code
     ``AUDITED_TABLES`` -- which now names this table -- but its CREATE
     TRIGGER is guarded by an ``IF EXISTS`` check against ``pg_class`` and
     quietly no-ops on a from-scratch replay where this table does not
     exist yet.  This narrow manual attach is what guarantees the trigger
     lands on the production ``flask db upgrade`` path.
     ``EXPECTED_TRIGGER_COUNT = len(AUDITED_TABLES)`` auto-bumps so the
     entrypoint trigger-count health check picks up the new total.

**Downgrade.**  Drops the table; PostgreSQL drops the dependent
``audit_ytd_tax_checkpoints`` trigger with it.  Fully reversible -- the
table holds only user-entered checkpoints, so a downgrade discards those
rows (the standard additive-table caveat) and a re-upgrade recreates an
empty table.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '3e501a622c8f'
down_revision = 'c9f2e6a4b1d8'
branch_labels = None
depends_on = None


def upgrade():
    """Create salary.ytd_tax_checkpoints and attach its audit trigger."""
    # ── Step 1: create the table ─────────────────────────────────────────
    op.create_table(
        'ytd_tax_checkpoints',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('as_of_date', sa.Date(), nullable=False),
        sa.Column('ytd_gross', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('ytd_federal', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('ytd_state', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            'ytd_social_security', sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column('ytd_medicare', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('salary_profile_id', sa.Integer(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.CheckConstraint(
            'ytd_gross >= 0', name='ck_ytd_tax_checkpoints_nonneg_gross',
        ),
        sa.CheckConstraint(
            'ytd_federal >= 0', name='ck_ytd_tax_checkpoints_nonneg_federal',
        ),
        sa.CheckConstraint(
            'ytd_state >= 0', name='ck_ytd_tax_checkpoints_nonneg_state',
        ),
        sa.CheckConstraint(
            'ytd_social_security >= 0', name='ck_ytd_tax_checkpoints_nonneg_ss',
        ),
        sa.CheckConstraint(
            'ytd_medicare >= 0', name='ck_ytd_tax_checkpoints_nonneg_medicare',
        ),
        sa.CheckConstraint(
            'ytd_federal <= ytd_gross',
            name='ck_ytd_tax_checkpoints_federal_le_gross',
        ),
        sa.CheckConstraint(
            'ytd_state <= ytd_gross',
            name='ck_ytd_tax_checkpoints_state_le_gross',
        ),
        sa.CheckConstraint(
            'ytd_social_security <= ytd_gross',
            name='ck_ytd_tax_checkpoints_ss_le_gross',
        ),
        sa.CheckConstraint(
            'ytd_medicare <= ytd_gross',
            name='ck_ytd_tax_checkpoints_medicare_le_gross',
        ),
        sa.ForeignKeyConstraint(
            ['salary_profile_id'], ['salary.salary_profiles.id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'salary_profile_id', 'as_of_date',
            name='uq_ytd_tax_checkpoints_profile_date',
        ),
        schema='salary',
    )

    # ── Step 2: attach the audit trigger ─────────────────────────────────
    # DROP IF EXISTS + CREATE makes the attach idempotent against a re-run.
    # Trigger name ``audit_<table>`` matches the convention the entrypoint
    # trigger-count health check enumerates (``tgname LIKE 'audit_%'``).
    op.execute(
        "DROP TRIGGER IF EXISTS audit_ytd_tax_checkpoints "
        "ON salary.ytd_tax_checkpoints"
    )
    op.execute(
        "CREATE TRIGGER audit_ytd_tax_checkpoints "
        "AFTER INSERT OR UPDATE OR DELETE ON salary.ytd_tax_checkpoints "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def downgrade():
    """Drop salary.ytd_tax_checkpoints (its audit trigger drops with it)."""
    op.drop_table('ytd_tax_checkpoints', schema='salary')
