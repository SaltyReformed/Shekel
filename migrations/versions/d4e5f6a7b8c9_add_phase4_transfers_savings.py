"""Add Phase 4: transfer templates, transfer updates, savings goal user_id

Revision ID: d4e5f6a7b8c9
Revises: c5d6e7f8a901
Create Date: 2026-02-26

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d4e5f6a7b8c9"
down_revision = "c5d6e7f8a901"
branch_labels = None
depends_on = None


def upgrade():
    # --- 1. Create budget.transfer_templates table ---
    op.create_table(
        "transfer_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("auth.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_account_id",
            sa.Integer(),
            sa.ForeignKey("budget.accounts.id"),
            nullable=False,
        ),
        sa.Column(
            "to_account_id",
            sa.Integer(),
            sa.ForeignKey("budget.accounts.id"),
            nullable=False,
        ),
        sa.Column(
            "recurrence_rule_id",
            sa.Integer(),
            sa.ForeignKey("budget.recurrence_rules.id"),
            nullable=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("default_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("sort_order", sa.Integer(), server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "from_account_id != to_account_id",
            name="ck_transfer_templates_different_accounts",
        ),
        sa.CheckConstraint(
            "default_amount > 0",
            name="ck_transfer_templates_positive_amount",
        ),
        schema="budget",
    )
    op.create_index(
        "idx_transfer_templates_user",
        "transfer_templates",
        ["user_id"],
        schema="budget",
    )

    # --- 2. Add columns to budget.transfers ---
    op.add_column(
        "transfers",
        sa.Column("name", sa.String(200), nullable=True),
        schema="budget",
    )
    op.add_column(
        "transfers",
        sa.Column(
            "transfer_template_id",
            sa.Integer(),
            sa.ForeignKey("budget.transfer_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema="budget",
    )
    op.add_column(
        "transfers",
        sa.Column(
            "is_override",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        schema="budget",
    )
    op.add_column(
        "transfers",
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        schema="budget",
    )

    # Unique partial index: one non-deleted transfer per template per period per scenario.
    op.create_index(
        "idx_transfers_template_period_scenario",
        "transfers",
        ["transfer_template_id", "pay_period_id", "scenario_id"],
        unique=True,
        schema="budget",
        postgresql_where=sa.text(
            "transfer_template_id IS NOT NULL AND is_deleted = FALSE"
        ),
    )

    # --- 3. Add user_id to budget.savings_goals ---
    op.add_column(
        "savings_goals",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("auth.users.id", ondelete="CASCADE"),
            nullable=False,
            server_default="1",
        ),
        schema="budget",
    )
    # Remove the server_default after populating -- it was just for the migration.
    op.alter_column(
        "savings_goals",
        "user_id",
        server_default=None,
        schema="budget",
    )


def downgrade():
    # --- Reverse 3: Remove user_id from savings_goals ---
    op.drop_column("savings_goals", "user_id", schema="budget")

    # --- Reverse 2: Remove new columns from transfers ---
    op.drop_index(
        "idx_transfers_template_period_scenario",
        table_name="transfers",
        schema="budget",
    )
    op.drop_column("transfers", "is_deleted", schema="budget")
    op.drop_column("transfers", "is_override", schema="budget")
    op.drop_column("transfers", "transfer_template_id", schema="budget")
    op.drop_column("transfers", "name", schema="budget")

    # --- Reverse 1: Drop transfer_templates table ---
    op.drop_index(
        "idx_transfer_templates_user",
        table_name="transfer_templates",
        schema="budget",
    )
    op.drop_table("transfer_templates", schema="budget")
