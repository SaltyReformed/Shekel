"""Add CHECK and UNIQUE constraints across all schemas

Adds missing data-integrity constraints identified during code audit:
- Positive amount / rate checks on financial columns
- Range checks on month/year/count fields
- Unique name constraints to prevent duplicates
- Date ordering constraints

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-02-27

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    # ── budget.transfers ──────────────────────────────────────────────
    op.create_check_constraint(
        "ck_transfers_positive_amount",
        "transfers",
        "amount > 0",
        schema="budget",
    )

    # ── budget.savings_goals ──────────────────────────────────────────
    op.create_check_constraint(
        "ck_savings_goals_positive_target",
        "savings_goals",
        "target_amount > 0",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_savings_goals_positive_contribution",
        "savings_goals",
        "contribution_per_period IS NULL OR contribution_per_period > 0",
        schema="budget",
    )
    op.create_unique_constraint(
        "uq_savings_goals_user_acct_name",
        "savings_goals",
        ["user_id", "account_id", "name"],
        schema="budget",
    )

    # ── budget.transfer_templates ─────────────────────────────────────
    op.create_unique_constraint(
        "uq_transfer_templates_user_name",
        "transfer_templates",
        ["user_id", "name"],
        schema="budget",
    )

    # ── budget.pay_periods ────────────────────────────────────────────
    op.create_check_constraint(
        "ck_pay_periods_date_order",
        "pay_periods",
        "start_date < end_date",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_pay_periods_positive_index",
        "pay_periods",
        "period_index >= 0",
        schema="budget",
    )

    # ── budget.recurrence_rules ───────────────────────────────────────
    op.create_check_constraint(
        "ck_recurrence_rules_positive_interval",
        "recurrence_rules",
        "interval_n > 0",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_recurrence_rules_valid_offset",
        "recurrence_rules",
        "offset_periods >= 0",
        schema="budget",
    )

    # ── budget.transaction_templates ──────────────────────────────────
    op.create_check_constraint(
        "ck_transaction_templates_nonneg_amount",
        "transaction_templates",
        "default_amount >= 0",
        schema="budget",
    )

    # ── salary.salary_profiles ────────────────────────────────────────
    op.create_check_constraint(
        "ck_salary_profiles_positive_salary",
        "salary_profiles",
        "annual_salary > 0",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_salary_profiles_positive_periods",
        "salary_profiles",
        "pay_periods_per_year > 0",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_salary_profiles_nonneg_children",
        "salary_profiles",
        "qualifying_children >= 0",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_salary_profiles_nonneg_dependents",
        "salary_profiles",
        "other_dependents >= 0",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_salary_profiles_nonneg_add_income",
        "salary_profiles",
        "additional_income >= 0",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_salary_profiles_nonneg_add_deductions",
        "salary_profiles",
        "additional_deductions >= 0",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_salary_profiles_nonneg_extra_withholding",
        "salary_profiles",
        "extra_withholding >= 0",
        schema="salary",
    )

    # ── salary.salary_raises ──────────────────────────────────────────
    op.create_check_constraint(
        "ck_salary_raises_valid_month",
        "salary_raises",
        "effective_month >= 1 AND effective_month <= 12",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_salary_raises_positive_pct",
        "salary_raises",
        "percentage IS NULL OR percentage > 0",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_salary_raises_positive_flat",
        "salary_raises",
        "flat_amount IS NULL OR flat_amount > 0",
        schema="salary",
    )

    # ── salary.paycheck_deductions ────────────────────────────────────
    op.create_check_constraint(
        "ck_paycheck_deductions_positive_amount",
        "paycheck_deductions",
        "amount > 0",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_paycheck_deductions_positive_per_year",
        "paycheck_deductions",
        "deductions_per_year > 0",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_paycheck_deductions_positive_cap",
        "paycheck_deductions",
        "annual_cap IS NULL OR annual_cap > 0",
        schema="salary",
    )

    # ── salary.tax_bracket_sets ───────────────────────────────────────
    op.create_check_constraint(
        "ck_tax_bracket_sets_nonneg_deduction",
        "tax_bracket_sets",
        "standard_deduction >= 0",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_tax_bracket_sets_nonneg_child_credit",
        "tax_bracket_sets",
        "child_credit_amount >= 0",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_tax_bracket_sets_nonneg_other_credit",
        "tax_bracket_sets",
        "other_dependent_credit_amount >= 0",
        schema="salary",
    )

    # ── salary.tax_brackets ───────────────────────────────────────────
    op.create_check_constraint(
        "ck_tax_brackets_nonneg_min",
        "tax_brackets",
        "min_income >= 0",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_tax_brackets_income_order",
        "tax_brackets",
        "max_income IS NULL OR max_income >= min_income",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_tax_brackets_valid_rate",
        "tax_brackets",
        "rate >= 0 AND rate <= 1",
        schema="salary",
    )

    # ── salary.state_tax_configs ──────────────────────────────────────
    op.create_check_constraint(
        "ck_state_tax_configs_valid_rate",
        "state_tax_configs",
        "flat_rate IS NULL OR (flat_rate >= 0 AND flat_rate <= 1)",
        schema="salary",
    )

    # ── salary.fica_configs ───────────────────────────────────────────
    op.create_check_constraint(
        "ck_fica_configs_valid_ss_rate",
        "fica_configs",
        "ss_rate >= 0 AND ss_rate <= 1",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_fica_configs_positive_wage_base",
        "fica_configs",
        "ss_wage_base > 0",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_fica_configs_valid_medicare_rate",
        "fica_configs",
        "medicare_rate >= 0 AND medicare_rate <= 1",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_fica_configs_valid_surtax_rate",
        "fica_configs",
        "medicare_surtax_rate >= 0 AND medicare_surtax_rate <= 1",
        schema="salary",
    )
    op.create_check_constraint(
        "ck_fica_configs_positive_surtax_threshold",
        "fica_configs",
        "medicare_surtax_threshold > 0",
        schema="salary",
    )

    # ── auth.user_settings ────────────────────────────────────────────
    op.create_check_constraint(
        "ck_user_settings_valid_inflation",
        "user_settings",
        "default_inflation_rate >= 0 AND default_inflation_rate <= 1",
        schema="auth",
    )
    op.create_check_constraint(
        "ck_user_settings_positive_periods",
        "user_settings",
        "grid_default_periods > 0",
        schema="auth",
    )
    op.create_check_constraint(
        "ck_user_settings_positive_threshold",
        "user_settings",
        "low_balance_threshold >= 0",
        schema="auth",
    )


def downgrade():
    # ── auth.user_settings ────────────────────────────────────────────
    op.drop_constraint("ck_user_settings_positive_threshold", "user_settings", schema="auth")
    op.drop_constraint("ck_user_settings_positive_periods", "user_settings", schema="auth")
    op.drop_constraint("ck_user_settings_valid_inflation", "user_settings", schema="auth")

    # ── salary.fica_configs ───────────────────────────────────────────
    op.drop_constraint("ck_fica_configs_positive_surtax_threshold", "fica_configs", schema="salary")
    op.drop_constraint("ck_fica_configs_valid_surtax_rate", "fica_configs", schema="salary")
    op.drop_constraint("ck_fica_configs_valid_medicare_rate", "fica_configs", schema="salary")
    op.drop_constraint("ck_fica_configs_positive_wage_base", "fica_configs", schema="salary")
    op.drop_constraint("ck_fica_configs_valid_ss_rate", "fica_configs", schema="salary")

    # ── salary.state_tax_configs ──────────────────────────────────────
    op.drop_constraint("ck_state_tax_configs_valid_rate", "state_tax_configs", schema="salary")

    # ── salary.tax_brackets ───────────────────────────────────────────
    op.drop_constraint("ck_tax_brackets_valid_rate", "tax_brackets", schema="salary")
    op.drop_constraint("ck_tax_brackets_income_order", "tax_brackets", schema="salary")
    op.drop_constraint("ck_tax_brackets_nonneg_min", "tax_brackets", schema="salary")

    # ── salary.tax_bracket_sets ───────────────────────────────────────
    op.drop_constraint("ck_tax_bracket_sets_nonneg_other_credit", "tax_bracket_sets", schema="salary")
    op.drop_constraint("ck_tax_bracket_sets_nonneg_child_credit", "tax_bracket_sets", schema="salary")
    op.drop_constraint("ck_tax_bracket_sets_nonneg_deduction", "tax_bracket_sets", schema="salary")

    # ── salary.paycheck_deductions ────────────────────────────────────
    op.drop_constraint("ck_paycheck_deductions_positive_cap", "paycheck_deductions", schema="salary")
    op.drop_constraint("ck_paycheck_deductions_positive_per_year", "paycheck_deductions", schema="salary")
    op.drop_constraint("ck_paycheck_deductions_positive_amount", "paycheck_deductions", schema="salary")

    # ── salary.salary_raises ──────────────────────────────────────────
    op.drop_constraint("ck_salary_raises_positive_flat", "salary_raises", schema="salary")
    op.drop_constraint("ck_salary_raises_positive_pct", "salary_raises", schema="salary")
    op.drop_constraint("ck_salary_raises_valid_month", "salary_raises", schema="salary")

    # ── salary.salary_profiles ────────────────────────────────────────
    op.drop_constraint("ck_salary_profiles_nonneg_extra_withholding", "salary_profiles", schema="salary")
    op.drop_constraint("ck_salary_profiles_nonneg_add_deductions", "salary_profiles", schema="salary")
    op.drop_constraint("ck_salary_profiles_nonneg_add_income", "salary_profiles", schema="salary")
    op.drop_constraint("ck_salary_profiles_nonneg_dependents", "salary_profiles", schema="salary")
    op.drop_constraint("ck_salary_profiles_nonneg_children", "salary_profiles", schema="salary")
    op.drop_constraint("ck_salary_profiles_positive_periods", "salary_profiles", schema="salary")
    op.drop_constraint("ck_salary_profiles_positive_salary", "salary_profiles", schema="salary")

    # ── budget.transaction_templates ──────────────────────────────────
    op.drop_constraint("ck_transaction_templates_nonneg_amount", "transaction_templates", schema="budget")

    # ── budget.recurrence_rules ───────────────────────────────────────
    op.drop_constraint("ck_recurrence_rules_valid_offset", "recurrence_rules", schema="budget")
    op.drop_constraint("ck_recurrence_rules_positive_interval", "recurrence_rules", schema="budget")

    # ── budget.pay_periods ────────────────────────────────────────────
    op.drop_constraint("ck_pay_periods_positive_index", "pay_periods", schema="budget")
    op.drop_constraint("ck_pay_periods_date_order", "pay_periods", schema="budget")

    # ── budget.transfer_templates ─────────────────────────────────────
    op.drop_constraint("uq_transfer_templates_user_name", "transfer_templates", schema="budget")

    # ── budget.savings_goals ──────────────────────────────────────────
    op.drop_constraint("uq_savings_goals_user_acct_name", "savings_goals", schema="budget")
    op.drop_constraint("ck_savings_goals_positive_contribution", "savings_goals", schema="budget")
    op.drop_constraint("ck_savings_goals_positive_target", "savings_goals", schema="budget")

    # ── budget.transfers ──────────────────────────────────────────────
    op.drop_constraint("ck_transfers_positive_amount", "transfers", schema="budget")
