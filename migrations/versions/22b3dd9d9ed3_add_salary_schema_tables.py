"""Add salary schema tables

Revision ID: 22b3dd9d9ed3
Revises: 44460d8fe471
Create Date: 2026-02-24 06:42:08.549170

Review: solo developer, 2026-05-11 (audit 2026-04-15, C-40 retroactive sweep)

Destructive (audit tags D-05 + D-06): drops four indexes
(idx_anchor_history_account, idx_deductions_profile,
idx_salary_raises_profile, idx_tax_brackets_set) and runs eleven
``alter_column`` ops -- NULL -> NOT NULL on fica_configs, VARCHAR(100)
-> VARCHAR(200) on salary_profiles/paycheck_deductions.name,
VARCHAR(200) -> TEXT on salary_raises.notes, INTEGER nullable on
tax_brackets.sort_order, and JSONB -> JSON on mfa_configs.backup_codes.
The forward path is safe against the live data (widens are no-op for
existing values; NOT NULL tightenings pass because every existing row
already satisfies them).  Downgrade is partial -- three of the four
dropped indexes are not restored, and the NOT NULL tightenings on
fica_configs are not reversed (audit finding F-S6-C1-04).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# Revision identifiers, used by Alembic.
revision = '22b3dd9d9ed3'
down_revision = '44460d8fe471'
branch_labels = None
depends_on = None


def _constraint_exists(name, schema):
    """Check if a constraint exists (for fresh vs. migrated DB compat)."""
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_constraint c "
        "JOIN pg_namespace n ON c.connamespace = n.oid "
        "WHERE c.conname = :name AND n.nspname = :schema"
    ), {"name": name, "schema": schema})
    return result.fetchone() is not None


def _rename_unique(old_name, new_name, table, columns, schema):
    """Rename a unique constraint if the old name exists."""
    if _constraint_exists(old_name, schema):
        op.drop_constraint(old_name, table, schema=schema, type_='unique')
        op.create_unique_constraint(new_name, table, columns, schema=schema)


def upgrade():
    """Apply forward migration."""

    # ── Create salary schema tables ────────────────────────────────
    # These tables were previously created outside the migration chain
    # (e.g. via db.create_all()).  The CREATE statements below define
    # the tables in their PRE-ALTER state; the ALTER operations that
    # follow bring them to the desired state.

    op.create_table('salary_profiles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('scenario_id', sa.Integer(), nullable=False),
        sa.Column('template_id', sa.Integer(), nullable=True),
        sa.Column('filing_status_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False,
                  server_default=sa.text("'Primary'")),
        sa.Column('annual_salary', sa.Numeric(precision=12, scale=2),
                  nullable=False),
        sa.Column('state_code', sa.String(length=2), nullable=True),
        sa.Column('pay_periods_per_year', sa.Integer(), nullable=True,
                  server_default=sa.text('26')),
        sa.Column('is_active', sa.Boolean(),
                  server_default=sa.text('true')),
        sa.Column('sort_order', sa.Integer(),
                  server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['auth.users.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['scenario_id'], ['budget.scenarios.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['template_id'],
                                ['budget.transaction_templates.id'],
                                name='salary_profiles_template_id_fkey'),
        sa.ForeignKeyConstraint(['filing_status_id'],
                                ['ref.filing_statuses.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'scenario_id', 'name',
                            name='salary_profiles_user_id_scenario_id_name_key'),
        schema='salary'
    )

    op.create_table('salary_raises',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('salary_profile_id', sa.Integer(), nullable=False),
        sa.Column('raise_type_id', sa.Integer(), nullable=False),
        sa.Column('effective_month', sa.Integer(), nullable=False),
        sa.Column('effective_year', sa.Integer(), nullable=True),
        sa.Column('percentage', sa.Numeric(precision=5, scale=4),
                  nullable=True),
        sa.Column('flat_amount', sa.Numeric(precision=12, scale=2),
                  nullable=True),
        sa.Column('is_recurring', sa.Boolean(),
                  server_default=sa.text('false')),
        sa.Column('notes', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['salary_profile_id'],
                                ['salary.salary_profiles.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['raise_type_id'], ['ref.raise_types.id']),
        sa.PrimaryKeyConstraint('id'),
        schema='salary'
    )
    op.create_index('idx_salary_raises_profile', 'salary_raises',
                    ['salary_profile_id'], schema='salary')

    op.create_table('paycheck_deductions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('salary_profile_id', sa.Integer(), nullable=False),
        sa.Column('deduction_timing_id', sa.Integer(), nullable=False),
        sa.Column('calc_method_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=4),
                  nullable=False),
        sa.Column('deductions_per_year', sa.Integer(), nullable=False,
                  server_default=sa.text('26')),
        sa.Column('annual_cap', sa.Numeric(precision=12, scale=2),
                  nullable=True),
        sa.Column('inflation_enabled', sa.Boolean(),
                  server_default=sa.text('false')),
        sa.Column('inflation_rate', sa.Numeric(precision=5, scale=4),
                  nullable=True),
        sa.Column('inflation_effective_month', sa.Integer(), nullable=True),
        sa.Column('sort_order', sa.Integer(),
                  server_default=sa.text('0')),
        sa.Column('is_active', sa.Boolean(),
                  server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['salary_profile_id'],
                                ['salary.salary_profiles.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['deduction_timing_id'],
                                ['ref.deduction_timings.id']),
        sa.ForeignKeyConstraint(['calc_method_id'],
                                ['ref.calc_methods.id']),
        sa.PrimaryKeyConstraint('id'),
        schema='salary'
    )
    op.create_index('idx_deductions_profile', 'paycheck_deductions',
                    ['salary_profile_id'], schema='salary')

    op.create_table('tax_bracket_sets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('filing_status_id', sa.Integer(), nullable=False),
        sa.Column('tax_year', sa.Integer(), nullable=False),
        sa.Column('standard_deduction', sa.Numeric(precision=12, scale=2),
                  nullable=False),
        sa.Column('description', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['auth.users.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['filing_status_id'],
                                ['ref.filing_statuses.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'tax_year', 'filing_status_id',
                            name='tax_bracket_sets_user_id_tax_year_filing_status_id_key'),
        schema='salary'
    )

    op.create_table('tax_brackets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('bracket_set_id', sa.Integer(), nullable=False),
        sa.Column('min_income', sa.Numeric(precision=12, scale=2),
                  nullable=False),
        sa.Column('max_income', sa.Numeric(precision=12, scale=2),
                  nullable=True),
        sa.Column('rate', sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['bracket_set_id'],
                                ['salary.tax_bracket_sets.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        schema='salary'
    )
    op.create_index('idx_tax_brackets_set', 'tax_brackets',
                    ['bracket_set_id', 'sort_order'], schema='salary')

    op.create_table('state_tax_configs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('tax_type_id', sa.Integer(), nullable=False),
        sa.Column('state_code', sa.String(length=2), nullable=False),
        sa.Column('flat_rate', sa.Numeric(precision=5, scale=4),
                  nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['auth.users.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tax_type_id'], ['ref.tax_types.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'state_code',
                            name='state_tax_configs_user_id_state_code_key'),
        schema='salary'
    )

    op.create_table('fica_configs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('tax_year', sa.Integer(), nullable=False),
        sa.Column('ss_rate', sa.Numeric(precision=5, scale=4),
                  nullable=True, server_default=sa.text('0.0620')),
        sa.Column('ss_wage_base', sa.Numeric(precision=12, scale=2),
                  nullable=True, server_default=sa.text('168600.00')),
        sa.Column('medicare_rate', sa.Numeric(precision=5, scale=4),
                  nullable=True, server_default=sa.text('0.0145')),
        sa.Column('medicare_surtax_rate', sa.Numeric(precision=5, scale=4),
                  nullable=True, server_default=sa.text('0.0090')),
        sa.Column('medicare_surtax_threshold', sa.Numeric(precision=12, scale=2),
                  nullable=True, server_default=sa.text('200000.00')),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['auth.users.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'tax_year',
                            name='fica_configs_user_id_tax_year_key'),
        schema='salary'
    )

    # ── ALTER operations (original auto-generated migration) ───────
    op.alter_column('mfa_configs', 'backup_codes',
               existing_type=postgresql.JSONB(astext_type=sa.Text()),
               type_=sa.JSON(),
               existing_nullable=True,
               schema='auth')
    op.drop_index('idx_anchor_history_account', table_name='account_anchor_history', schema='budget')
    op.create_index('idx_anchor_history_account', 'account_anchor_history', ['account_id', 'created_at'], unique=False, schema='budget')
    _rename_unique('accounts_user_id_name_key', 'uq_accounts_user_name',
                   'accounts', ['user_id', 'name'], 'budget')
    _rename_unique('categories_user_id_group_name_item_name_key', 'uq_categories_user_group_item',
                   'categories', ['user_id', 'group_name', 'item_name'], 'budget')
    _rename_unique('pay_periods_user_id_start_date_key', 'uq_pay_periods_user_start',
                   'pay_periods', ['user_id', 'start_date'], 'budget')
    _rename_unique('scenarios_user_id_name_key', 'uq_scenarios_user_name',
                   'scenarios', ['user_id', 'name'], 'budget')
    op.alter_column('fica_configs', 'ss_rate',
               existing_type=sa.NUMERIC(precision=5, scale=4),
               nullable=False,
               existing_server_default=sa.text('0.0620'),
               schema='salary')
    op.alter_column('fica_configs', 'ss_wage_base',
               existing_type=sa.NUMERIC(precision=12, scale=2),
               nullable=False,
               existing_server_default=sa.text('168600.00'),
               schema='salary')
    op.alter_column('fica_configs', 'medicare_rate',
               existing_type=sa.NUMERIC(precision=5, scale=4),
               nullable=False,
               existing_server_default=sa.text('0.0145'),
               schema='salary')
    op.alter_column('fica_configs', 'medicare_surtax_rate',
               existing_type=sa.NUMERIC(precision=5, scale=4),
               nullable=False,
               existing_server_default=sa.text('0.0090'),
               schema='salary')
    op.alter_column('fica_configs', 'medicare_surtax_threshold',
               existing_type=sa.NUMERIC(precision=12, scale=2),
               nullable=False,
               existing_server_default=sa.text('200000.00'),
               schema='salary')
    _rename_unique('fica_configs_user_id_tax_year_key', 'uq_fica_configs_user_year',
                   'fica_configs', ['user_id', 'tax_year'], 'salary')
    op.alter_column('paycheck_deductions', 'name',
               existing_type=sa.VARCHAR(length=100),
               type_=sa.String(length=200),
               existing_nullable=False,
               schema='salary')
    op.drop_index('idx_deductions_profile', table_name='paycheck_deductions', schema='salary')
    op.alter_column('salary_profiles', 'name',
               existing_type=sa.VARCHAR(length=100),
               type_=sa.String(length=200),
               existing_nullable=False,
               existing_server_default=sa.text("'Primary'::character varying"),
               schema='salary')
    op.alter_column('salary_profiles', 'state_code',
               existing_type=sa.VARCHAR(length=2),
               nullable=False,
               schema='salary')
    op.alter_column('salary_profiles', 'pay_periods_per_year',
               existing_type=sa.INTEGER(),
               nullable=False,
               existing_server_default=sa.text('26'),
               schema='salary')
    _rename_unique('salary_profiles_user_id_scenario_id_name_key', 'uq_salary_profiles_user_scenario_name',
                   'salary_profiles', ['user_id', 'scenario_id', 'name'], 'salary')
    if _constraint_exists('salary_profiles_template_id_fkey', 'salary'):
        op.drop_constraint('salary_profiles_template_id_fkey', 'salary_profiles', schema='salary', type_='foreignkey')
        op.create_foreign_key(None, 'salary_profiles', 'transaction_templates', ['template_id'], ['id'], source_schema='salary', referent_schema='budget', ondelete='SET NULL')
    op.alter_column('salary_raises', 'notes',
               existing_type=sa.VARCHAR(length=200),
               type_=sa.Text(),
               existing_nullable=True,
               schema='salary')
    op.drop_index('idx_salary_raises_profile', table_name='salary_raises', schema='salary')
    _rename_unique('state_tax_configs_user_id_state_code_key', 'uq_state_tax_configs_user_state',
                   'state_tax_configs', ['user_id', 'state_code'], 'salary')
    _rename_unique('tax_bracket_sets_user_id_tax_year_filing_status_id_key', 'uq_tax_bracket_sets_user_year_status',
                   'tax_bracket_sets', ['user_id', 'tax_year', 'filing_status_id'], 'salary')
    op.alter_column('tax_brackets', 'sort_order',
               existing_type=sa.INTEGER(),
               nullable=True,
               schema='salary')
    op.drop_index('idx_tax_brackets_set', table_name='tax_brackets', schema='salary')


def downgrade():
    """Revert migration."""
    # ── Revert ALTER operations (non-salary) ───────────────────────
    op.drop_index('idx_anchor_history_account', table_name='account_anchor_history', schema='budget')
    op.create_index('idx_anchor_history_account', 'account_anchor_history', ['account_id', sa.text('created_at DESC')], unique=False, schema='budget')
    _rename_unique('uq_accounts_user_name', 'accounts_user_id_name_key',
                   'accounts', ['user_id', 'name'], 'budget')
    _rename_unique('uq_categories_user_group_item', 'categories_user_id_group_name_item_name_key',
                   'categories', ['user_id', 'group_name', 'item_name'], 'budget')
    _rename_unique('uq_pay_periods_user_start', 'pay_periods_user_id_start_date_key',
                   'pay_periods', ['user_id', 'start_date'], 'budget')
    _rename_unique('uq_scenarios_user_name', 'scenarios_user_id_name_key',
                   'scenarios', ['user_id', 'name'], 'budget')
    op.alter_column('mfa_configs', 'backup_codes',
               existing_type=sa.JSON(),
               type_=postgresql.JSONB(astext_type=sa.Text()),
               existing_nullable=True,
               schema='auth')

    # ── Drop salary schema tables (children first) ─────────────────
    op.drop_table('tax_brackets', schema='salary')
    op.drop_table('tax_bracket_sets', schema='salary')
    op.drop_table('salary_raises', schema='salary')
    op.drop_table('paycheck_deductions', schema='salary')
    op.drop_table('salary_profiles', schema='salary')
    op.drop_table('state_tax_configs', schema='salary')
    op.drop_table('fica_configs', schema='salary')
