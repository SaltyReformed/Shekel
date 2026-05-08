"""C-24 Marshmallow range sweep + DB CHECK constraint additions

Adds 21 storage-tier CHECK constraints on numeric columns whose
schemas previously had loose or missing :class:`marshmallow.validate.Range`
validators.  Closes F-011, F-012, F-074, F-075, F-076, and F-077 of
the 2026-04-15 security remediation plan; F-013 and F-014 are
deliberately left unchanged because the schema/route/template
already form a consistent percent-input -> decimal-storage
boundary (see the module docstring of ``app/schemas/validation.py``
for the convention).

Constraints added (grouped by table)
------------------------------------

``budget.escrow_components``
  - ``ck_escrow_components_nonneg_annual_amount``
  - ``ck_escrow_components_valid_inflation_rate``

``budget.interest_params``
  - ``ck_interest_params_valid_apy``

``budget.investment_params``
  - ``ck_investment_params_nonneg_contribution_limit``
  - ``ck_investment_params_valid_employer_flat_pct``
  - ``ck_investment_params_valid_employer_match_pct``
  - ``ck_investment_params_valid_employer_match_cap``

``budget.rate_history``
  - ``ck_rate_history_valid_interest_rate``

``auth.user_settings``
  - ``ck_user_settings_valid_safe_withdrawal``
  - ``ck_user_settings_valid_estimated_tax_rate``

``salary.paycheck_deductions``
  - ``ck_paycheck_deductions_valid_inflation_rate``
  - ``ck_paycheck_deductions_valid_inflation_month``

``salary.salary_raises``
  - ``ck_salary_raises_valid_effective_year``

``salary.state_tax_configs``
  - ``ck_state_tax_configs_nonneg_standard_deduction``
  - ``ck_state_tax_configs_valid_tax_year``

``salary.fica_configs``
  - ``ck_fica_configs_valid_tax_year``

``salary.tax_bracket_sets``
  - ``ck_tax_bracket_sets_valid_tax_year``

``salary.calibration_overrides``
  - ``ck_calibration_overrides_valid_federal_rate``
  - ``ck_calibration_overrides_valid_state_rate``
  - ``ck_calibration_overrides_valid_ss_rate``
  - ``ck_calibration_overrides_valid_medicare_rate``

Pre-flight semantics
--------------------

PostgreSQL refuses ``ADD CONSTRAINT`` when existing rows violate
the predicate, but the resulting error points at the constraint
name rather than the offending data.  This migration runs an
explicit detection query for each constraint first, accumulates
every offender across every table, and aborts with a single
:class:`RuntimeError` listing all of them when any are present --
``docs/coding-standards.md`` requires that destructive migrations
get explicit approval, so no row is ever silently rewritten or
deleted.  This mirrors the pre-flight pattern from commits C-22
(``e8b14f3a7c22``) and C-23 (``a3b9c2d40e15``); the operator
resolves duplicates manually and reruns.

Audit reference: F-011 + F-012 + F-074 + F-075 + F-076 + F-077 /
commit C-24 of the 2026-04-15 security remediation plan.

Revision ID: b71c4a8f5d3e
Revises: a3b9c2d40e15
Create Date: 2026-05-07 22:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "b71c4a8f5d3e"
down_revision = "a3b9c2d40e15"
branch_labels = None
depends_on = None


# ── Constraint specs ──────────────────────────────────────────────
#
# Each entry: (schema, table, constraint_name, predicate, detection_sql).
#
# ``predicate`` is the SQL CHECK body used in ``ADD CONSTRAINT``
# (e.g. ``"annual_amount >= 0"``).
#
# ``detection_sql`` is a SELECT that returns the offending rows when
# the predicate would FAIL.  Keep the SELECTs lightweight: an ``id``
# plus a snippet of the offending value is enough for the operator
# to find and fix the row by hand.  Each entry's column names match
# the live schema so the migration is self-validating against typos
# (a stale column name surfaces as ``UndefinedColumn`` during
# pre-flight, before any DDL runs).
_CHECK_SPECS = [
    # ── budget.escrow_components ──────────────────────────────────
    (
        "budget",
        "escrow_components",
        "ck_escrow_components_nonneg_annual_amount",
        "annual_amount >= 0",
        "SELECT id, account_id, name, annual_amount "
        "FROM budget.escrow_components "
        "WHERE annual_amount < 0 "
        "ORDER BY id",
    ),
    (
        "budget",
        "escrow_components",
        "ck_escrow_components_valid_inflation_rate",
        "inflation_rate IS NULL OR "
        "(inflation_rate >= 0 AND inflation_rate <= 1)",
        "SELECT id, account_id, name, inflation_rate "
        "FROM budget.escrow_components "
        "WHERE inflation_rate IS NOT NULL "
        "  AND (inflation_rate < 0 OR inflation_rate > 1) "
        "ORDER BY id",
    ),

    # ── budget.interest_params ────────────────────────────────────
    (
        "budget",
        "interest_params",
        "ck_interest_params_valid_apy",
        "apy >= 0 AND apy <= 1",
        "SELECT id, account_id, apy "
        "FROM budget.interest_params "
        "WHERE apy < 0 OR apy > 1 "
        "ORDER BY id",
    ),

    # ── budget.investment_params ──────────────────────────────────
    (
        "budget",
        "investment_params",
        "ck_investment_params_nonneg_contribution_limit",
        "annual_contribution_limit IS NULL OR "
        "annual_contribution_limit >= 0",
        "SELECT id, account_id, annual_contribution_limit "
        "FROM budget.investment_params "
        "WHERE annual_contribution_limit IS NOT NULL "
        "  AND annual_contribution_limit < 0 "
        "ORDER BY id",
    ),
    (
        "budget",
        "investment_params",
        "ck_investment_params_valid_employer_flat_pct",
        "employer_flat_percentage IS NULL OR "
        "(employer_flat_percentage >= 0 AND "
        "employer_flat_percentage <= 1)",
        "SELECT id, account_id, employer_flat_percentage "
        "FROM budget.investment_params "
        "WHERE employer_flat_percentage IS NOT NULL "
        "  AND (employer_flat_percentage < 0 "
        "       OR employer_flat_percentage > 1) "
        "ORDER BY id",
    ),
    (
        "budget",
        "investment_params",
        "ck_investment_params_valid_employer_match_pct",
        "employer_match_percentage IS NULL OR "
        "(employer_match_percentage >= 0 AND "
        "employer_match_percentage <= 10)",
        "SELECT id, account_id, employer_match_percentage "
        "FROM budget.investment_params "
        "WHERE employer_match_percentage IS NOT NULL "
        "  AND (employer_match_percentage < 0 "
        "       OR employer_match_percentage > 10) "
        "ORDER BY id",
    ),
    (
        "budget",
        "investment_params",
        "ck_investment_params_valid_employer_match_cap",
        "employer_match_cap_percentage IS NULL OR "
        "(employer_match_cap_percentage >= 0 AND "
        "employer_match_cap_percentage <= 1)",
        "SELECT id, account_id, employer_match_cap_percentage "
        "FROM budget.investment_params "
        "WHERE employer_match_cap_percentage IS NOT NULL "
        "  AND (employer_match_cap_percentage < 0 "
        "       OR employer_match_cap_percentage > 1) "
        "ORDER BY id",
    ),

    # ── budget.rate_history ───────────────────────────────────────
    (
        "budget",
        "rate_history",
        "ck_rate_history_valid_interest_rate",
        "interest_rate >= 0 AND interest_rate <= 1",
        "SELECT id, account_id, effective_date, interest_rate "
        "FROM budget.rate_history "
        "WHERE interest_rate < 0 OR interest_rate > 1 "
        "ORDER BY id",
    ),

    # ── auth.user_settings ────────────────────────────────────────
    (
        "auth",
        "user_settings",
        "ck_user_settings_valid_safe_withdrawal",
        "safe_withdrawal_rate IS NULL OR "
        "(safe_withdrawal_rate >= 0 AND safe_withdrawal_rate <= 1)",
        "SELECT id, user_id, safe_withdrawal_rate "
        "FROM auth.user_settings "
        "WHERE safe_withdrawal_rate IS NOT NULL "
        "  AND (safe_withdrawal_rate < 0 "
        "       OR safe_withdrawal_rate > 1) "
        "ORDER BY id",
    ),
    (
        "auth",
        "user_settings",
        "ck_user_settings_valid_estimated_tax_rate",
        "estimated_retirement_tax_rate IS NULL OR "
        "(estimated_retirement_tax_rate >= 0 AND "
        "estimated_retirement_tax_rate <= 1)",
        "SELECT id, user_id, estimated_retirement_tax_rate "
        "FROM auth.user_settings "
        "WHERE estimated_retirement_tax_rate IS NOT NULL "
        "  AND (estimated_retirement_tax_rate < 0 "
        "       OR estimated_retirement_tax_rate > 1) "
        "ORDER BY id",
    ),

    # ── salary.paycheck_deductions ────────────────────────────────
    (
        "salary",
        "paycheck_deductions",
        "ck_paycheck_deductions_valid_inflation_rate",
        "inflation_rate IS NULL OR "
        "(inflation_rate >= 0 AND inflation_rate <= 1)",
        "SELECT id, salary_profile_id, name, inflation_rate "
        "FROM salary.paycheck_deductions "
        "WHERE inflation_rate IS NOT NULL "
        "  AND (inflation_rate < 0 OR inflation_rate > 1) "
        "ORDER BY id",
    ),
    (
        "salary",
        "paycheck_deductions",
        "ck_paycheck_deductions_valid_inflation_month",
        "inflation_effective_month IS NULL OR "
        "(inflation_effective_month >= 1 AND "
        "inflation_effective_month <= 12)",
        "SELECT id, salary_profile_id, name, inflation_effective_month "
        "FROM salary.paycheck_deductions "
        "WHERE inflation_effective_month IS NOT NULL "
        "  AND (inflation_effective_month < 1 "
        "       OR inflation_effective_month > 12) "
        "ORDER BY id",
    ),

    # ── salary.salary_raises ──────────────────────────────────────
    (
        "salary",
        "salary_raises",
        "ck_salary_raises_valid_effective_year",
        "effective_year IS NULL OR "
        "(effective_year >= 2000 AND effective_year <= 2100)",
        "SELECT id, salary_profile_id, raise_type_id, "
        "       effective_year, effective_month "
        "FROM salary.salary_raises "
        "WHERE effective_year IS NOT NULL "
        "  AND (effective_year < 2000 OR effective_year > 2100) "
        "ORDER BY id",
    ),

    # ── salary.state_tax_configs ──────────────────────────────────
    (
        "salary",
        "state_tax_configs",
        "ck_state_tax_configs_nonneg_standard_deduction",
        "standard_deduction IS NULL OR standard_deduction >= 0",
        "SELECT id, user_id, state_code, tax_year, standard_deduction "
        "FROM salary.state_tax_configs "
        "WHERE standard_deduction IS NOT NULL "
        "  AND standard_deduction < 0 "
        "ORDER BY id",
    ),
    (
        "salary",
        "state_tax_configs",
        "ck_state_tax_configs_valid_tax_year",
        "tax_year >= 2000 AND tax_year <= 2100",
        "SELECT id, user_id, state_code, tax_year "
        "FROM salary.state_tax_configs "
        "WHERE tax_year < 2000 OR tax_year > 2100 "
        "ORDER BY id",
    ),

    # ── salary.fica_configs ───────────────────────────────────────
    (
        "salary",
        "fica_configs",
        "ck_fica_configs_valid_tax_year",
        "tax_year >= 2000 AND tax_year <= 2100",
        "SELECT id, user_id, tax_year "
        "FROM salary.fica_configs "
        "WHERE tax_year < 2000 OR tax_year > 2100 "
        "ORDER BY id",
    ),

    # ── salary.tax_bracket_sets ───────────────────────────────────
    (
        "salary",
        "tax_bracket_sets",
        "ck_tax_bracket_sets_valid_tax_year",
        "tax_year >= 2000 AND tax_year <= 2100",
        "SELECT id, user_id, filing_status_id, tax_year "
        "FROM salary.tax_bracket_sets "
        "WHERE tax_year < 2000 OR tax_year > 2100 "
        "ORDER BY id",
    ),

    # ── salary.calibration_overrides ──────────────────────────────
    (
        "salary",
        "calibration_overrides",
        "ck_calibration_overrides_valid_federal_rate",
        "effective_federal_rate >= 0 AND effective_federal_rate <= 1",
        "SELECT id, salary_profile_id, effective_federal_rate "
        "FROM salary.calibration_overrides "
        "WHERE effective_federal_rate < 0 "
        "   OR effective_federal_rate > 1 "
        "ORDER BY id",
    ),
    (
        "salary",
        "calibration_overrides",
        "ck_calibration_overrides_valid_state_rate",
        "effective_state_rate >= 0 AND effective_state_rate <= 1",
        "SELECT id, salary_profile_id, effective_state_rate "
        "FROM salary.calibration_overrides "
        "WHERE effective_state_rate < 0 "
        "   OR effective_state_rate > 1 "
        "ORDER BY id",
    ),
    (
        "salary",
        "calibration_overrides",
        "ck_calibration_overrides_valid_ss_rate",
        "effective_ss_rate >= 0 AND effective_ss_rate <= 1",
        "SELECT id, salary_profile_id, effective_ss_rate "
        "FROM salary.calibration_overrides "
        "WHERE effective_ss_rate < 0 OR effective_ss_rate > 1 "
        "ORDER BY id",
    ),
    (
        "salary",
        "calibration_overrides",
        "ck_calibration_overrides_valid_medicare_rate",
        "effective_medicare_rate >= 0 AND effective_medicare_rate <= 1",
        "SELECT id, salary_profile_id, effective_medicare_rate "
        "FROM salary.calibration_overrides "
        "WHERE effective_medicare_rate < 0 "
        "   OR effective_medicare_rate > 1 "
        "ORDER BY id",
    ),
]


def _refuse(violations):
    """Abort the migration with a structured error listing every violator.

    Args:
        violations: Sequence of ``(label, rows)`` tuples where
            ``label`` is the constraint short-name and ``rows`` is
            the offending row sample from the detection query.
    """
    sections = []
    for label, rows in violations:
        rendered = "; ".join(
            "(" + ", ".join(repr(v) for v in row) + ")"
            for row in rows
        )
        sections.append(
            f"{label} -- {len(rows)} offending row(s): {rendered}"
        )
    detail = "\n  ".join(sections)
    raise RuntimeError(
        "Refusing to add C-24 CHECK constraints: pre-existing rows "
        "violate one or more predicates.  Resolve every violator by "
        "hand (typically by correcting the offending value or "
        "deleting the row after confirming with the user) and rerun "
        "the migration.  Per docs/coding-standards.md the migration "
        "never auto-rewrites data.  Offenders:\n  " + detail
    )


def upgrade():
    """Run every pre-flight detection, then add every CHECK in order.

    The pre-flight phase accumulates violators across every spec so a
    single failed run reports the complete cleanup list rather than
    one constraint at a time.  When pre-flight passes, the DDL phase
    adds each constraint in declaration order.  Alembic wraps the
    whole upgrade in a single transaction, so a failure during DDL
    rolls every preceding ``ADD CONSTRAINT`` back together with the
    pre-flight reads.
    """
    bind = op.get_bind()

    # ── Pre-flight ────────────────────────────────────────────────
    violations = []
    for _schema, _table, name, _predicate, detection_sql in _CHECK_SPECS:
        rows = bind.execute(sa.text(detection_sql)).fetchall()
        if rows:
            violations.append((name, rows))
    if violations:
        _refuse(violations)

    # ── DDL ───────────────────────────────────────────────────────
    for schema, table, name, predicate, _detection_sql in _CHECK_SPECS:
        op.create_check_constraint(
            name, table, predicate, schema=schema,
        )


def downgrade():
    """Drop every CHECK in reverse order.

    Reverse order so a partial failure leaves a recognisable post-
    state (the failed constraint is the last one still present).
    """
    for schema, table, name, _predicate, _detection_sql in reversed(_CHECK_SPECS):
        op.drop_constraint(name, table, schema=schema, type_="check")
