"""Add investment & retirement tables (Phase 5)

Adds investment/retirement account types, creates investment_params
and pension_profiles tables, adds target_account_id to paycheck_deductions,
and adds retirement planning settings to user_settings.

Revision ID: c3d4e5f6g7h8
Revises: a1b2c3d4e5f6
Create Date: 2026-03-05

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c3d4e5f6g7h8"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Insert new account types.
    op.execute(
        "INSERT INTO ref.account_types (name, category) VALUES ('401k', 'retirement')"
    )
    op.execute(
        "INSERT INTO ref.account_types (name, category) VALUES ('roth_401k', 'retirement')"
    )
    op.execute(
        "INSERT INTO ref.account_types (name, category) VALUES ('traditional_ira', 'retirement')"
    )
    op.execute(
        "INSERT INTO ref.account_types (name, category) VALUES ('roth_ira', 'retirement')"
    )
    op.execute(
        "INSERT INTO ref.account_types (name, category) VALUES ('brokerage', 'investment')"
    )

    # 2. Create budget.investment_params table.
    op.create_table(
        "investment_params",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "assumed_annual_return",
            sa.Numeric(7, 5),
            nullable=False,
            server_default=sa.text("0.07000"),
        ),
        sa.Column("annual_contribution_limit", sa.Numeric(12, 2), nullable=True),
        sa.Column("contribution_limit_year", sa.Integer, nullable=True),
        sa.Column(
            "employer_contribution_type",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
        sa.Column("employer_flat_percentage", sa.Numeric(5, 4), nullable=True),
        sa.Column("employer_match_percentage", sa.Numeric(5, 4), nullable=True),
        sa.Column("employer_match_cap_percentage", sa.Numeric(5, 4), nullable=True),
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
            "employer_contribution_type IN ('none', 'flat_percentage', 'match')",
            name="ck_investment_params_employer_type",
        ),
        sa.CheckConstraint(
            "assumed_annual_return >= -1 AND assumed_annual_return <= 1",
            name="ck_investment_params_valid_return",
        ),
        schema="budget",
    )

    # 3. Create salary.pension_profiles table.
    op.create_table(
        "pension_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("auth.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "salary_profile_id",
            sa.Integer,
            sa.ForeignKey("salary.salary_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "name",
            sa.String(100),
            nullable=False,
            server_default=sa.text("'Pension'"),
        ),
        sa.Column("benefit_multiplier", sa.Numeric(7, 5), nullable=False),
        sa.Column(
            "consecutive_high_years",
            sa.Integer,
            nullable=False,
            server_default=sa.text("4"),
        ),
        sa.Column("hire_date", sa.Date, nullable=False),
        sa.Column("earliest_retirement_date", sa.Date, nullable=True),
        sa.Column("planned_retirement_date", sa.Date, nullable=True),
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
        sa.CheckConstraint(
            "benefit_multiplier > 0",
            name="ck_pension_profiles_positive_multiplier",
        ),
        sa.CheckConstraint(
            "consecutive_high_years > 0",
            name="ck_pension_profiles_positive_high_years",
        ),
        schema="salary",
    )

    # 4. Add target_account_id to salary.paycheck_deductions.
    op.add_column(
        "paycheck_deductions",
        sa.Column(
            "target_account_id",
            sa.Integer,
            sa.ForeignKey("budget.accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema="salary",
    )

    # 5. Add retirement planning columns to auth.user_settings.
    op.add_column(
        "user_settings",
        sa.Column(
            "safe_withdrawal_rate",
            sa.Numeric(5, 4),
            server_default=sa.text("0.0400"),
        ),
        schema="auth",
    )
    op.add_column(
        "user_settings",
        sa.Column("planned_retirement_date", sa.Date, nullable=True),
        schema="auth",
    )
    op.add_column(
        "user_settings",
        sa.Column(
            "estimated_retirement_tax_rate",
            sa.Numeric(5, 4),
            nullable=True,
        ),
        schema="auth",
    )


def downgrade():
    # 5. Remove retirement planning columns from auth.user_settings.
    op.drop_column("user_settings", "estimated_retirement_tax_rate", schema="auth")
    op.drop_column("user_settings", "planned_retirement_date", schema="auth")
    op.drop_column("user_settings", "safe_withdrawal_rate", schema="auth")

    # 4. Remove target_account_id from salary.paycheck_deductions.
    op.drop_column("paycheck_deductions", "target_account_id", schema="salary")

    # 3. Drop salary.pension_profiles.
    op.drop_table("pension_profiles", schema="salary")

    # 2. Drop budget.investment_params.
    op.drop_table("investment_params", schema="budget")

    # 1. Remove new account types.
    op.execute(
        "DELETE FROM ref.account_types WHERE name IN "
        "('401k', 'roth_401k', 'traditional_ira', 'roth_ira', 'brokerage')"
    )
