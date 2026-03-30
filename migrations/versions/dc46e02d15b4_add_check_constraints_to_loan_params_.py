"""add CHECK constraints to loan_params and transactions

Revision ID: dc46e02d15b4
Revises: a45b88e8fa2e
Create Date: 2026-03-30 16:24:06.891603
"""
from alembic import op

# Revision identifiers, used by Alembic.
revision = 'dc46e02d15b4'
down_revision = 'a45b88e8fa2e'
branch_labels = None
depends_on = None


def upgrade():
    """Add CHECK constraints to loan_params and transactions."""
    # Loan params: financial column guards.
    op.create_check_constraint(
        "ck_loan_params_orig_principal",
        "loan_params",
        "original_principal > 0",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_loan_params_curr_principal",
        "loan_params",
        "current_principal >= 0",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_loan_params_interest_rate",
        "loan_params",
        "interest_rate >= 0",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_loan_params_term_months",
        "loan_params",
        "term_months > 0",
        schema="budget",
    )

    # Transactions: amount guards.
    op.create_check_constraint(
        "ck_transactions_estimated_amount",
        "transactions",
        "estimated_amount >= 0",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_transactions_actual_amount",
        "transactions",
        "actual_amount IS NULL OR actual_amount >= 0",
        schema="budget",
    )


def downgrade():
    """Remove CHECK constraints."""
    op.drop_constraint(
        "ck_transactions_actual_amount", "transactions", schema="budget"
    )
    op.drop_constraint(
        "ck_transactions_estimated_amount", "transactions", schema="budget"
    )
    op.drop_constraint(
        "ck_loan_params_term_months", "loan_params", schema="budget"
    )
    op.drop_constraint(
        "ck_loan_params_interest_rate", "loan_params", schema="budget"
    )
    op.drop_constraint(
        "ck_loan_params_curr_principal", "loan_params", schema="budget"
    )
    op.drop_constraint(
        "ck_loan_params_orig_principal", "loan_params", schema="budget"
    )
