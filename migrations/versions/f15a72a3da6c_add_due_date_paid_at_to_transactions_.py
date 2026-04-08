"""add due_date paid_at to transactions, due_day_of_month to recurrence_rules

Revision ID: f15a72a3da6c
Revises: f06bcc98bc3a
Create Date: 2026-04-07 19:26:50.252943
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = 'f15a72a3da6c'
down_revision = 'f06bcc98bc3a'
branch_labels = None
depends_on = None


def upgrade():
    """Add due_date/paid_at to transactions and due_day_of_month to recurrence_rules.

    Includes backfill of due_date from recurrence rules and paid_at
    from updated_at for settled transactions.
    """
    # 1. Add due_day_of_month to recurrence_rules.
    op.add_column(
        'recurrence_rules',
        sa.Column('due_day_of_month', sa.Integer(), nullable=True),
        schema='budget',
    )
    op.create_check_constraint(
        'ck_recurrence_rules_due_dom',
        'recurrence_rules',
        'due_day_of_month IS NULL OR '
        '(due_day_of_month >= 1 AND due_day_of_month <= 31)',
        schema='budget',
    )

    # 2. Add due_date to transactions.
    op.add_column(
        'transactions',
        sa.Column('due_date', sa.Date(), nullable=True),
        schema='budget',
    )

    # 3. Add paid_at to transactions.
    op.add_column(
        'transactions',
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        schema='budget',
    )

    # 4. Add partial index on due_date.
    op.create_index(
        'idx_transactions_due_date',
        'transactions',
        ['due_date'],
        unique=False,
        schema='budget',
        postgresql_where=sa.text('due_date IS NOT NULL'),
    )

    # 5. Backfill due_date for transactions with template day_of_month.
    #    Handles periods spanning two calendar months by checking which
    #    month (start_date vs end_date) contains the target day.
    op.execute(sa.text("""
        UPDATE budget.transactions t
        SET due_date = sub.computed_due
        FROM (
            SELECT
                t2.id AS txn_id,
                (
                    CASE
                        -- Check if the target day falls in end_date's month
                        -- (handles periods spanning two calendar months).
                        WHEN MAKE_DATE(
                                EXTRACT(YEAR FROM pp.end_date)::int,
                                EXTRACT(MONTH FROM pp.end_date)::int,
                                LEAST(
                                    rr.day_of_month,
                                    (DATE_TRUNC('month', pp.end_date)
                                     + INTERVAL '1 month'
                                     - INTERVAL '1 day')::date
                                    - DATE_TRUNC('month', pp.end_date)::date + 1
                                )::int
                             ) BETWEEN pp.start_date AND pp.end_date
                             AND MAKE_DATE(
                                EXTRACT(YEAR FROM pp.start_date)::int,
                                EXTRACT(MONTH FROM pp.start_date)::int,
                                LEAST(
                                    rr.day_of_month,
                                    (DATE_TRUNC('month', pp.start_date)
                                     + INTERVAL '1 month'
                                     - INTERVAL '1 day')::date
                                    - DATE_TRUNC('month', pp.start_date)::date + 1
                                )::int
                             ) NOT BETWEEN pp.start_date AND pp.end_date
                        THEN
                            -- Target day falls in end_date's month.
                            MAKE_DATE(
                                EXTRACT(YEAR FROM pp.end_date)::int,
                                EXTRACT(MONTH FROM pp.end_date)::int,
                                LEAST(
                                    rr.day_of_month,
                                    (DATE_TRUNC('month', pp.end_date)
                                     + INTERVAL '1 month'
                                     - INTERVAL '1 day')::date
                                    - DATE_TRUNC('month', pp.end_date)::date + 1
                                )::int
                            )
                        ELSE
                            -- Default: target day in start_date's month.
                            MAKE_DATE(
                                EXTRACT(YEAR FROM pp.start_date)::int,
                                EXTRACT(MONTH FROM pp.start_date)::int,
                                LEAST(
                                    rr.day_of_month,
                                    (DATE_TRUNC('month', pp.start_date)
                                     + INTERVAL '1 month'
                                     - INTERVAL '1 day')::date
                                    - DATE_TRUNC('month', pp.start_date)::date + 1
                                )::int
                            )
                    END
                ) AS computed_due
            FROM budget.transactions t2
            JOIN budget.transaction_templates tt ON tt.id = t2.template_id
            JOIN budget.recurrence_rules rr ON rr.id = tt.recurrence_rule_id
            JOIN budget.pay_periods pp ON pp.id = t2.pay_period_id
            WHERE rr.day_of_month IS NOT NULL
        ) sub
        WHERE t.id = sub.txn_id
    """))

    # Backfill due_date for transactions without day_of_month
    # (every-paycheck patterns, manual, no template): use period start.
    op.execute(sa.text("""
        UPDATE budget.transactions t
        SET due_date = pp.start_date
        FROM budget.pay_periods pp
        WHERE pp.id = t.pay_period_id
          AND t.due_date IS NULL
    """))

    # 6. Backfill paid_at from updated_at for settled transactions.
    op.execute(sa.text("""
        UPDATE budget.transactions t
        SET paid_at = t.updated_at
        WHERE t.status_id IN (
            SELECT s.id FROM ref.statuses s WHERE s.is_settled = true
        )
    """))


def downgrade():
    """Remove due_date/paid_at from transactions and due_day_of_month from recurrence_rules."""
    # 1. Drop partial index.
    op.drop_index(
        'idx_transactions_due_date',
        table_name='transactions',
        schema='budget',
        postgresql_where=sa.text('due_date IS NOT NULL'),
    )
    # 2. Drop paid_at.
    op.drop_column('transactions', 'paid_at', schema='budget')
    # 3. Drop due_date.
    op.drop_column('transactions', 'due_date', schema='budget')
    # 4. Drop due_day_of_month (constraint drops with column).
    op.drop_constraint(
        'ck_recurrence_rules_due_dom',
        'recurrence_rules',
        schema='budget',
    )
    op.drop_column('recurrence_rules', 'due_day_of_month', schema='budget')
