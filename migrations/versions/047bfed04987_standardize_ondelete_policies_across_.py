"""standardize ondelete policies across all foreign keys

Revision ID: 047bfed04987
Revises: dc46e02d15b4
Create Date: 2026-03-30 16:29:29.719065
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = '047bfed04987'
down_revision = 'dc46e02d15b4'
branch_labels = None
depends_on = None


def _find_fk_name(table, columns, schema='budget'):
    """Look up the actual FK constraint name for the given column(s).

    Handles naming differences between environments where FKs may have been
    created with explicit names (fk_table_column) or auto-generated names
    (table_column_fkey).
    """
    conn = op.get_bind()
    insp = sa.inspect(conn)
    target_cols = set(columns) if isinstance(columns, (list, tuple)) else {columns}
    for fk in insp.get_foreign_keys(table, schema=schema):
        if set(fk['constrained_columns']) == target_cols:
            return fk['name']
    raise ValueError(
        f"No FK constraint found on {schema}.{table} for columns {columns}"
    )


def upgrade():
    """Add explicit ondelete policies to all FKs that relied on implicit NO ACTION."""
    # ── accounts ──
    op.drop_constraint('accounts_account_type_id_fkey', 'accounts', schema='budget', type_='foreignkey')
    op.create_foreign_key('accounts_account_type_id_fkey', 'accounts', 'account_types',
                          ['account_type_id'], ['id'], source_schema='budget', referent_schema='ref', ondelete='RESTRICT')
    op.drop_constraint('accounts_current_anchor_period_id_fkey', 'accounts', schema='budget', type_='foreignkey')
    op.create_foreign_key('accounts_current_anchor_period_id_fkey', 'accounts', 'pay_periods',
                          ['current_anchor_period_id'], ['id'], source_schema='budget', referent_schema='budget', ondelete='SET NULL')

    # ── account_anchor_history ──
    op.drop_constraint('account_anchor_history_pay_period_id_fkey', 'account_anchor_history', schema='budget', type_='foreignkey')
    op.create_foreign_key('account_anchor_history_pay_period_id_fkey', 'account_anchor_history', 'pay_periods',
                          ['pay_period_id'], ['id'], source_schema='budget', referent_schema='budget', ondelete='CASCADE')

    # ── transactions ──
    op.drop_constraint(_find_fk_name('transactions', ['account_id']), 'transactions', schema='budget', type_='foreignkey')
    op.create_foreign_key('transactions_account_id_fkey', 'transactions', 'accounts',
                          ['account_id'], ['id'], source_schema='budget', referent_schema='budget', ondelete='RESTRICT')
    op.drop_constraint('transactions_status_id_fkey', 'transactions', schema='budget', type_='foreignkey')
    op.create_foreign_key('transactions_status_id_fkey', 'transactions', 'statuses',
                          ['status_id'], ['id'], source_schema='budget', referent_schema='ref', ondelete='RESTRICT')
    op.drop_constraint('transactions_category_id_fkey', 'transactions', schema='budget', type_='foreignkey')
    op.create_foreign_key('transactions_category_id_fkey', 'transactions', 'categories',
                          ['category_id'], ['id'], source_schema='budget', referent_schema='budget', ondelete='SET NULL')
    op.drop_constraint('transactions_transaction_type_id_fkey', 'transactions', schema='budget', type_='foreignkey')
    op.create_foreign_key('transactions_transaction_type_id_fkey', 'transactions', 'transaction_types',
                          ['transaction_type_id'], ['id'], source_schema='budget', referent_schema='ref', ondelete='RESTRICT')

    # ── transfers ──
    op.drop_constraint('transfers_from_account_id_fkey', 'transfers', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfers_from_account_id_fkey', 'transfers', 'accounts',
                          ['from_account_id'], ['id'], source_schema='budget', referent_schema='budget', ondelete='RESTRICT')
    op.drop_constraint('transfers_to_account_id_fkey', 'transfers', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfers_to_account_id_fkey', 'transfers', 'accounts',
                          ['to_account_id'], ['id'], source_schema='budget', referent_schema='budget', ondelete='RESTRICT')
    op.drop_constraint('transfers_pay_period_id_fkey', 'transfers', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfers_pay_period_id_fkey', 'transfers', 'pay_periods',
                          ['pay_period_id'], ['id'], source_schema='budget', referent_schema='budget', ondelete='RESTRICT')
    op.drop_constraint('transfers_status_id_fkey', 'transfers', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfers_status_id_fkey', 'transfers', 'statuses',
                          ['status_id'], ['id'], source_schema='budget', referent_schema='ref', ondelete='RESTRICT')
    op.drop_constraint(_find_fk_name('transfers', ['category_id']), 'transfers', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfers_category_id_fkey', 'transfers', 'categories',
                          ['category_id'], ['id'], source_schema='budget', referent_schema='budget', ondelete='SET NULL')

    # ── transaction_templates ──
    op.drop_constraint('transaction_templates_account_id_fkey', 'transaction_templates', schema='budget', type_='foreignkey')
    op.create_foreign_key('transaction_templates_account_id_fkey', 'transaction_templates', 'accounts',
                          ['account_id'], ['id'], source_schema='budget', referent_schema='budget', ondelete='RESTRICT')
    op.drop_constraint('transaction_templates_category_id_fkey', 'transaction_templates', schema='budget', type_='foreignkey')
    op.create_foreign_key('transaction_templates_category_id_fkey', 'transaction_templates', 'categories',
                          ['category_id'], ['id'], source_schema='budget', referent_schema='budget', ondelete='RESTRICT')
    op.drop_constraint('transaction_templates_recurrence_rule_id_fkey', 'transaction_templates', schema='budget', type_='foreignkey')
    op.create_foreign_key('transaction_templates_recurrence_rule_id_fkey', 'transaction_templates', 'recurrence_rules',
                          ['recurrence_rule_id'], ['id'], source_schema='budget', referent_schema='budget', ondelete='SET NULL')
    op.drop_constraint('transaction_templates_transaction_type_id_fkey', 'transaction_templates', schema='budget', type_='foreignkey')
    op.create_foreign_key('transaction_templates_transaction_type_id_fkey', 'transaction_templates', 'transaction_types',
                          ['transaction_type_id'], ['id'], source_schema='budget', referent_schema='ref', ondelete='RESTRICT')

    # ── transfer_templates ──
    op.drop_constraint('transfer_templates_from_account_id_fkey', 'transfer_templates', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfer_templates_from_account_id_fkey', 'transfer_templates', 'accounts',
                          ['from_account_id'], ['id'], source_schema='budget', referent_schema='budget', ondelete='RESTRICT')
    op.drop_constraint('transfer_templates_to_account_id_fkey', 'transfer_templates', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfer_templates_to_account_id_fkey', 'transfer_templates', 'accounts',
                          ['to_account_id'], ['id'], source_schema='budget', referent_schema='budget', ondelete='RESTRICT')
    op.drop_constraint('transfer_templates_recurrence_rule_id_fkey', 'transfer_templates', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfer_templates_recurrence_rule_id_fkey', 'transfer_templates', 'recurrence_rules',
                          ['recurrence_rule_id'], ['id'], source_schema='budget', referent_schema='budget', ondelete='SET NULL')
    op.drop_constraint(_find_fk_name('transfer_templates', ['category_id']), 'transfer_templates', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfer_templates_category_id_fkey', 'transfer_templates', 'categories',
                          ['category_id'], ['id'], source_schema='budget', referent_schema='budget', ondelete='SET NULL')

    # ── recurrence_rules ──
    op.drop_constraint('recurrence_rules_pattern_id_fkey', 'recurrence_rules', schema='budget', type_='foreignkey')
    op.create_foreign_key('recurrence_rules_pattern_id_fkey', 'recurrence_rules', 'recurrence_patterns',
                          ['pattern_id'], ['id'], source_schema='budget', referent_schema='ref', ondelete='RESTRICT')


def downgrade():
    """Revert ondelete policies back to implicit NO ACTION."""
    # ── recurrence_rules ──
    op.drop_constraint('recurrence_rules_pattern_id_fkey', 'recurrence_rules', schema='budget', type_='foreignkey')
    op.create_foreign_key('recurrence_rules_pattern_id_fkey', 'recurrence_rules', 'recurrence_patterns',
                          ['pattern_id'], ['id'], source_schema='budget', referent_schema='ref')

    # ── transfer_templates ──
    op.drop_constraint('transfer_templates_category_id_fkey', 'transfer_templates', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfer_templates_category_id_fkey', 'transfer_templates', 'categories',
                          ['category_id'], ['id'], source_schema='budget', referent_schema='budget')
    op.drop_constraint('transfer_templates_recurrence_rule_id_fkey', 'transfer_templates', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfer_templates_recurrence_rule_id_fkey', 'transfer_templates', 'recurrence_rules',
                          ['recurrence_rule_id'], ['id'], source_schema='budget', referent_schema='budget')
    op.drop_constraint('transfer_templates_to_account_id_fkey', 'transfer_templates', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfer_templates_to_account_id_fkey', 'transfer_templates', 'accounts',
                          ['to_account_id'], ['id'], source_schema='budget', referent_schema='budget')
    op.drop_constraint('transfer_templates_from_account_id_fkey', 'transfer_templates', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfer_templates_from_account_id_fkey', 'transfer_templates', 'accounts',
                          ['from_account_id'], ['id'], source_schema='budget', referent_schema='budget')

    # ── transaction_templates ──
    op.drop_constraint('transaction_templates_transaction_type_id_fkey', 'transaction_templates', schema='budget', type_='foreignkey')
    op.create_foreign_key('transaction_templates_transaction_type_id_fkey', 'transaction_templates', 'transaction_types',
                          ['transaction_type_id'], ['id'], source_schema='budget', referent_schema='ref')
    op.drop_constraint('transaction_templates_recurrence_rule_id_fkey', 'transaction_templates', schema='budget', type_='foreignkey')
    op.create_foreign_key('transaction_templates_recurrence_rule_id_fkey', 'transaction_templates', 'recurrence_rules',
                          ['recurrence_rule_id'], ['id'], source_schema='budget', referent_schema='budget')
    op.drop_constraint('transaction_templates_category_id_fkey', 'transaction_templates', schema='budget', type_='foreignkey')
    op.create_foreign_key('transaction_templates_category_id_fkey', 'transaction_templates', 'categories',
                          ['category_id'], ['id'], source_schema='budget', referent_schema='budget')
    op.drop_constraint('transaction_templates_account_id_fkey', 'transaction_templates', schema='budget', type_='foreignkey')
    op.create_foreign_key('transaction_templates_account_id_fkey', 'transaction_templates', 'accounts',
                          ['account_id'], ['id'], source_schema='budget', referent_schema='budget')

    # ── transfers ──
    op.drop_constraint('transfers_category_id_fkey', 'transfers', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfers_category_id_fkey', 'transfers', 'categories',
                          ['category_id'], ['id'], source_schema='budget', referent_schema='budget')
    op.drop_constraint('transfers_status_id_fkey', 'transfers', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfers_status_id_fkey', 'transfers', 'statuses',
                          ['status_id'], ['id'], source_schema='budget', referent_schema='ref')
    op.drop_constraint('transfers_pay_period_id_fkey', 'transfers', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfers_pay_period_id_fkey', 'transfers', 'pay_periods',
                          ['pay_period_id'], ['id'], source_schema='budget', referent_schema='budget')
    op.drop_constraint('transfers_to_account_id_fkey', 'transfers', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfers_to_account_id_fkey', 'transfers', 'accounts',
                          ['to_account_id'], ['id'], source_schema='budget', referent_schema='budget')
    op.drop_constraint('transfers_from_account_id_fkey', 'transfers', schema='budget', type_='foreignkey')
    op.create_foreign_key('transfers_from_account_id_fkey', 'transfers', 'accounts',
                          ['from_account_id'], ['id'], source_schema='budget', referent_schema='budget')

    # ── transactions ──
    op.drop_constraint('transactions_transaction_type_id_fkey', 'transactions', schema='budget', type_='foreignkey')
    op.create_foreign_key('transactions_transaction_type_id_fkey', 'transactions', 'transaction_types',
                          ['transaction_type_id'], ['id'], source_schema='budget', referent_schema='ref')
    op.drop_constraint('transactions_category_id_fkey', 'transactions', schema='budget', type_='foreignkey')
    op.create_foreign_key('transactions_category_id_fkey', 'transactions', 'categories',
                          ['category_id'], ['id'], source_schema='budget', referent_schema='budget')
    op.drop_constraint('transactions_status_id_fkey', 'transactions', schema='budget', type_='foreignkey')
    op.create_foreign_key('transactions_status_id_fkey', 'transactions', 'statuses',
                          ['status_id'], ['id'], source_schema='budget', referent_schema='ref')
    op.drop_constraint('transactions_account_id_fkey', 'transactions', schema='budget', type_='foreignkey')
    op.create_foreign_key('transactions_account_id_fkey', 'transactions', 'accounts',
                          ['account_id'], ['id'], source_schema='budget', referent_schema='budget')

    # ── account_anchor_history ──
    op.drop_constraint('account_anchor_history_pay_period_id_fkey', 'account_anchor_history', schema='budget', type_='foreignkey')
    op.create_foreign_key('account_anchor_history_pay_period_id_fkey', 'account_anchor_history', 'pay_periods',
                          ['pay_period_id'], ['id'], source_schema='budget', referent_schema='budget')

    # ── accounts ──
    op.drop_constraint('accounts_current_anchor_period_id_fkey', 'accounts', schema='budget', type_='foreignkey')
    op.create_foreign_key('accounts_current_anchor_period_id_fkey', 'accounts', 'pay_periods',
                          ['current_anchor_period_id'], ['id'], source_schema='budget', referent_schema='budget')
    op.drop_constraint('accounts_account_type_id_fkey', 'accounts', schema='budget', type_='foreignkey')
    op.create_foreign_key('accounts_account_type_id_fkey', 'accounts', 'account_types',
                          ['account_type_id'], ['id'], source_schema='budget', referent_schema='ref')
