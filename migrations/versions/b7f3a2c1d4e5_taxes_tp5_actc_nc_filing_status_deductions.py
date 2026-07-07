"""taxes T-P5: refundable ACTC + NC filing-status std deduction + NC child deduction

Revision ID: b7f3a2c1d4e5
Revises: 3e501a622c8f
Create Date: 2026-07-05 03:10:00.000000

Review: SaltyReformed (T-P5 developer ruling 2026-07-05, docs/design/
analytics_audit.md "T-P5 acceptance findings"), 2026-07-05.

Taxes slice T-P5 follow-up.  THREE additive tax-model extensions plus a
seed-defect correction, all with primary-source-verified constants:

1. **Federal refundable ACTC** -- ``salary.tax_bracket_sets`` gains
   ``child_credit_refundable_cap`` (the per-child refundable Additional
   Child Tax Credit ceiling: $1,700 for 2025 and 2026 per IRS Rev. Proc.
   2025-32 sec. 4.05(2) / 2025 Schedule 8812 instructions).  The 15% rate
   and $2,500 earned-income floor are statutory module constants (not
   seeded).  The same commit CORRECTS the Child Tax Credit amount from the
   defective $2,000 seed to the enacted $2,200 (OBBBA / P.L. 119-21 sec.
   70104; Rev. Proc. 2025-32 sec. 4.05(1)) for 2025 and 2026 existing rows.

2. **NC filing-status-aware standard deduction** (finding 2b) --
   ``salary.state_tax_configs`` gains ``filing_status_id`` (one row per
   filing status, since the NC standard deduction is status-specific:
   Single/MFS $12,750, MFJ $25,500, HoH $19,125 per N.C.G.S.
   105-153.5(a)(1)).  Existing status-blind rows (single-filer-valued
   $12,750) are assigned to Single and the MFJ/MFS/HoH rows are derived.
   The former ``uq_state_tax_configs_user_state_year`` unique constraint is
   REPLACED by ``uq_state_tax_configs_user_state_year_status`` (a
   constraint swap -- the "Review" line above records the developer's
   approval).

3. **NC per-child deduction** -- new audited table
   ``salary.state_child_deductions`` holding the AGI-tiered per-child
   deduction (N.C.G.S. 105-153.5(a1)), seeded with the NC tiers for 2025
   and 2026 for every user who already carries an NC state config.

**Backfill (project convention: one-time backfills live in the migration).**
Every existing per-user tax row is updated in place; the child-deduction
tiers are inserted per NC-configured user.

**Downgrade.**  Fully reverses: drops the child-deduction table, deletes the
derived non-Single state configs and drops the filing-status column
(restoring the original status-blind unique constraint), drops the
refundable-cap column, and reverts the CTC amount to $2,000.  The derived
per-status rows and child-deduction tiers are discarded (they are
re-derivable by a re-upgrade), which is why the constraint swap is safe to
reverse here rather than raising NotImplementedError.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'b7f3a2c1d4e5'
down_revision = '3e501a622c8f'
branch_labels = None
depends_on = None


# NC per-child deduction tiers (N.C.G.S. 105-153.5(a1)), inlined so the
# migration is immutable regardless of later edits to the app seed data.
# ``(agi_min, agi_max_or_None, deduction_per_child)``; agi_max None = top tier.
_NC_SINGLE_MFS_CHILD_TIERS = [
    (0, 20000, 3000), (20000, 30000, 2500), (30000, 40000, 2000),
    (40000, 50000, 1500), (50000, 60000, 1000), (60000, 70000, 500),
    (70000, None, 0),
]
_NC_CHILD_TIERS = {
    'married_jointly': [
        (0, 40000, 3000), (40000, 60000, 2500), (60000, 80000, 2000),
        (80000, 100000, 1500), (100000, 120000, 1000), (120000, 140000, 500),
        (140000, None, 0),
    ],
    'head_of_household': [
        (0, 30000, 3000), (30000, 45000, 2500), (45000, 60000, 2000),
        (60000, 75000, 1500), (75000, 90000, 1000), (90000, 105000, 500),
        (105000, None, 0),
    ],
    'single': _NC_SINGLE_MFS_CHILD_TIERS,
    'married_separately': _NC_SINGLE_MFS_CHILD_TIERS,
}

# NC standard deduction by filing status (N.C.G.S. 105-153.5(a)(1)).
_NC_STANDARD_DEDUCTION = {
    'married_jointly': 25500,
    'married_separately': 12750,
    'head_of_household': 19125,
}


def upgrade():
    """Add refundable ACTC, NC filing-status deduction, NC child deduction."""
    # ── 1. Federal: refundable ACTC cap + CTC $2,000 -> $2,200 ────────────
    op.add_column(
        'tax_bracket_sets',
        sa.Column(
            'child_credit_refundable_cap', sa.Numeric(precision=12, scale=2),
            nullable=False, server_default='0',
        ),
        schema='salary',
    )
    op.create_check_constraint(
        'ck_tax_bracket_sets_nonneg_refundable_cap', 'tax_bracket_sets',
        'child_credit_refundable_cap >= 0', schema='salary',
    )
    # Backfill the verified $1,700 refundable cap for the seeded years.
    op.execute(
        "UPDATE salary.tax_bracket_sets SET child_credit_refundable_cap = 1700 "
        "WHERE tax_year IN (2025, 2026)"
    )
    # Correct the seed-defect CTC $2,000 -> $2,200 (OBBBA) for the seeded
    # years, leaving any non-default value untouched.
    op.execute(
        "UPDATE salary.tax_bracket_sets SET child_credit_amount = 2200 "
        "WHERE tax_year IN (2025, 2026) AND child_credit_amount = 2000"
    )

    # ── 2. NC filing-status-aware standard deduction ──────────────────────
    op.add_column(
        'state_tax_configs',
        sa.Column('filing_status_id', sa.Integer(), nullable=True),
        schema='salary',
    )
    # Drop the status-blind unique constraint BEFORE inserting the per-status
    # rows (which would otherwise collide on (user, state, year)).
    op.drop_constraint(
        'uq_state_tax_configs_user_state_year', 'state_tax_configs',
        schema='salary', type_='unique',
    )
    # Assign every existing (status-blind) row to Single -- its stored
    # $12,750 is already the correct Single value.
    op.execute(
        "UPDATE salary.state_tax_configs SET filing_status_id = "
        "(SELECT id FROM ref.filing_statuses WHERE name = 'single') "
        "WHERE filing_status_id IS NULL"
    )
    # Derive the MFJ / MFS / HoH rows from each (now-Single) row.  NC gets its
    # per-status standard deduction; a non-NC state copies the Single value
    # (preserving the pre-migration status-blind behaviour for that state).
    for status_name, nc_std in _NC_STANDARD_DEDUCTION.items():
        op.execute(
            sa.text(
                "INSERT INTO salary.state_tax_configs "
                "(user_id, tax_type_id, filing_status_id, state_code, "
                " tax_year, flat_rate, standard_deduction, created_at) "
                "SELECT stc.user_id, stc.tax_type_id, tgt.id, stc.state_code, "
                "       stc.tax_year, stc.flat_rate, "
                "       CASE WHEN stc.state_code = 'NC' THEN :nc_std "
                "            ELSE stc.standard_deduction END, now() "
                "FROM salary.state_tax_configs stc "
                "CROSS JOIN (SELECT id FROM ref.filing_statuses "
                "            WHERE name = :status) tgt "
                "WHERE stc.filing_status_id = "
                "      (SELECT id FROM ref.filing_statuses WHERE name = 'single')"
            ).bindparams(status=status_name, nc_std=nc_std)
        )
    op.alter_column(
        'state_tax_configs', 'filing_status_id', nullable=False,
        schema='salary',
    )
    op.create_foreign_key(
        'fk_state_tax_configs_filing_status_id', 'state_tax_configs',
        'filing_statuses', ['filing_status_id'], ['id'],
        source_schema='salary', referent_schema='ref', ondelete='RESTRICT',
    )
    op.create_unique_constraint(
        'uq_state_tax_configs_user_state_year_status', 'state_tax_configs',
        ['user_id', 'state_code', 'tax_year', 'filing_status_id'],
        schema='salary',
    )

    # ── 3. NC per-child deduction table + backfill + audit trigger ────────
    op.create_table(
        'state_child_deductions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('filing_status_id', sa.Integer(), nullable=False),
        sa.Column('state_code', sa.String(length=2), nullable=False),
        sa.Column('tax_year', sa.Integer(), nullable=False),
        sa.Column('agi_min', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('agi_max', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column(
            'deduction_per_child', sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.CheckConstraint(
            'agi_min >= 0', name='ck_state_child_deductions_nonneg_agi_min',
        ),
        sa.CheckConstraint(
            'agi_max IS NULL OR agi_max > agi_min',
            name='ck_state_child_deductions_agi_order',
        ),
        sa.CheckConstraint(
            'deduction_per_child >= 0',
            name='ck_state_child_deductions_nonneg_deduction',
        ),
        sa.CheckConstraint(
            'tax_year >= 2000 AND tax_year <= 2100',
            name='ck_state_child_deductions_valid_tax_year',
        ),
        sa.ForeignKeyConstraint(
            ['filing_status_id'], ['ref.filing_statuses.id'],
            name='fk_state_child_deductions_filing_status_id',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['auth.users.id'],
            name='fk_state_child_deductions_user_id', ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'state_code', 'tax_year', 'filing_status_id', 'agi_min',
            name='uq_state_child_deductions_user_state_year_status_agi',
        ),
        schema='salary',
    )
    # Backfill: seed the NC tiers for 2025 and 2026 for every user who
    # already carries an NC state config (proxy for "the tax seed ran").
    for tax_year in (2025, 2026):
        for status_name, tiers in _NC_CHILD_TIERS.items():
            for agi_min, agi_max, per_child in tiers:
                op.execute(
                    sa.text(
                        "INSERT INTO salary.state_child_deductions "
                        "(user_id, filing_status_id, state_code, tax_year, "
                        " agi_min, agi_max, deduction_per_child, created_at) "
                        "SELECT DISTINCT stc.user_id, fs.id, 'NC', :yr, "
                        "       CAST(:amin AS numeric), "
                        "       CAST(:amax AS numeric), "
                        "       CAST(:perchild AS numeric), now() "
                        "FROM salary.state_tax_configs stc "
                        "CROSS JOIN (SELECT id FROM ref.filing_statuses "
                        "            WHERE name = :status) fs "
                        "WHERE stc.state_code = 'NC'"
                    ).bindparams(
                        yr=tax_year, amin=agi_min, amax=agi_max,
                        perchild=per_child, status=status_name,
                    )
                )
    # Attach the audit trigger (manual DROP+CREATE, the T-P2 precedent).
    op.execute(
        "DROP TRIGGER IF EXISTS audit_state_child_deductions "
        "ON salary.state_child_deductions"
    )
    op.execute(
        "CREATE TRIGGER audit_state_child_deductions "
        "AFTER INSERT OR UPDATE OR DELETE ON salary.state_child_deductions "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def downgrade():
    """Reverse the T-P5 extensions (discarding the derived per-status data)."""
    # ── 3. Drop the child-deduction table (its audit trigger drops with it) ─
    op.drop_table('state_child_deductions', schema='salary')

    # ── 2. Revert state_tax_configs to the status-blind shape ─────────────
    op.drop_constraint(
        'uq_state_tax_configs_user_state_year_status', 'state_tax_configs',
        schema='salary', type_='unique',
    )
    # Delete the derived MFJ/MFS/HoH rows; keep the original Single rows
    # (their $12,750 restores the pre-migration status-blind value).
    op.execute(
        "DELETE FROM salary.state_tax_configs WHERE filing_status_id <> "
        "(SELECT id FROM ref.filing_statuses WHERE name = 'single')"
    )
    op.drop_constraint(
        'fk_state_tax_configs_filing_status_id', 'state_tax_configs',
        schema='salary', type_='foreignkey',
    )
    op.drop_column('state_tax_configs', 'filing_status_id', schema='salary')
    op.create_unique_constraint(
        'uq_state_tax_configs_user_state_year', 'state_tax_configs',
        ['user_id', 'state_code', 'tax_year'], schema='salary',
    )

    # ── 1. Revert the federal CTC amount and drop the refundable cap ──────
    op.execute(
        "UPDATE salary.tax_bracket_sets SET child_credit_amount = 2000 "
        "WHERE tax_year IN (2025, 2026) AND child_credit_amount = 2200"
    )
    op.drop_constraint(
        'ck_tax_bracket_sets_nonneg_refundable_cap', 'tax_bracket_sets',
        schema='salary', type_='check',
    )
    op.drop_column(
        'tax_bracket_sets', 'child_credit_refundable_cap', schema='salary',
    )
