"""Add debt account tables (mortgage, auto loan)

Adds mortgage and auto_loan account types, creates mortgage_params,
mortgage_rate_history, escrow_components, and auto_loan_params tables.

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-03-04

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Insert new account types.
    op.execute(
        "INSERT INTO ref.account_types (name, category) VALUES ('mortgage', 'liability')"
    )
    op.execute(
        "INSERT INTO ref.account_types (name, category) VALUES ('auto_loan', 'liability')"
    )

    # 2. Create budget.mortgage_params table.
    op.create_table(
        "mortgage_params",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("original_principal", sa.Numeric(12, 2), nullable=False),
        sa.Column("current_principal", sa.Numeric(12, 2), nullable=False),
        sa.Column("interest_rate", sa.Numeric(7, 5), nullable=False),
        sa.Column("term_months", sa.Integer, nullable=False),
        sa.Column("origination_date", sa.Date, nullable=False),
        sa.Column("payment_day", sa.Integer, nullable=False),
        sa.Column(
            "is_arm", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
        sa.Column("arm_first_adjustment_months", sa.Integer, nullable=True),
        sa.Column("arm_adjustment_interval_months", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "payment_day >= 1 AND payment_day <= 31",
            name="ck_mortgage_payment_day",
        ),
        schema="budget",
    )
    op.create_index(
        "idx_mortgage_params_account",
        "mortgage_params",
        ["account_id"],
        schema="budget",
    )

    # 3. Create budget.mortgage_rate_history table.
    op.create_table(
        "mortgage_rate_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("interest_rate", sa.Numeric(7, 5), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        schema="budget",
    )
    op.create_index(
        "idx_mortgage_rate_history_account",
        "mortgage_rate_history",
        ["account_id", sa.text("effective_date DESC")],
        schema="budget",
    )

    # 4. Create budget.escrow_components table.
    op.create_table(
        "escrow_components",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("annual_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("inflation_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("account_id", "name", name="uq_escrow_account_name"),
        schema="budget",
    )

    # 5. Create budget.auto_loan_params table.
    op.create_table(
        "auto_loan_params",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("original_principal", sa.Numeric(12, 2), nullable=False),
        sa.Column("current_principal", sa.Numeric(12, 2), nullable=False),
        sa.Column("interest_rate", sa.Numeric(7, 5), nullable=False),
        sa.Column("term_months", sa.Integer, nullable=False),
        sa.Column("origination_date", sa.Date, nullable=False),
        sa.Column("payment_day", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "payment_day >= 1 AND payment_day <= 31",
            name="ck_auto_loan_payment_day",
        ),
        schema="budget",
    )
    op.create_index(
        "idx_auto_loan_params_account",
        "auto_loan_params",
        ["account_id"],
        schema="budget",
    )


def downgrade():
    op.drop_index(
        "idx_auto_loan_params_account",
        table_name="auto_loan_params",
        schema="budget",
    )
    op.drop_table("auto_loan_params", schema="budget")
    op.drop_table("escrow_components", schema="budget")
    op.drop_index(
        "idx_mortgage_rate_history_account",
        table_name="mortgage_rate_history",
        schema="budget",
    )
    op.drop_table("mortgage_rate_history", schema="budget")
    op.drop_index(
        "idx_mortgage_params_account",
        table_name="mortgage_params",
        schema="budget",
    )
    op.drop_table("mortgage_params", schema="budget")
    op.execute("DELETE FROM ref.account_types WHERE name IN ('mortgage', 'auto_loan')")
