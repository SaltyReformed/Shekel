"""C-25 Boolean NOT NULL sweep + server_default restoration + boundary inclusivity alignment

Closes F-068, F-134, F-135, F-106, and F-107 of the 2026-04-15 security
remediation plan.  Three classes of change are bundled into a single
migration because they share the same risk profile (DDL-only ALTERs on
columns that are already populated) and the same operational concern
(no application code path inserts NULL for these columns today, but the
storage tier accepts NULL because no constraint was ever materialised).

A. Boolean and sort-order NOT NULL + server_default sweep (F-068).
   Twenty-three columns across the auth, budget, and salary schemas
   were declared with a Python-side ``default=`` only -- the ORM fills
   them on INSERT, but a raw-SQL INSERT, a ``pg_dump`` reload that
   omits the column, or the audit trigger emitting a synthetic row
   would land NULL into a column whose semantics ("active flag",
   "soft-deleted flag", "display order") have no NULL meaning.  This
   migration backfills every existing NULL to the column's logical
   default, then ALTERs every column to ``NOT NULL`` with a matching
   ``server_default`` so PostgreSQL itself enforces the invariant
   regardless of who issued the INSERT.

B. server_default-only restoration (F-134).
   Five tables (``salary.fica_configs``, ``salary.pension_profiles``,
   ``salary.salary_profiles``, ``budget.investment_params``,
   ``budget.transfers``) carry columns that were created with
   ``server_default`` in their original migration but no longer carry
   it on the live database -- the most likely cause is a historical
   ``db.create_all()`` pre-run that materialised the tables before the
   migration chain attached the defaults.  Whatever the cause, the
   live state diverges from the migration-declared state, and the
   columns are NOT NULL today: a raw INSERT that omits one of them
   raises ``NotNullViolation`` instead of receiving the documented
   default.  The restorations here align the live DB with the original
   intent and the model definitions.

C. Boundary inclusivity alignment (F-106, F-107, F-135).
   Schema-tier and storage-tier bounds disagreed on three columns:
   ``budget.savings_goals.contribution_per_period`` (DB CHECK > 0,
   schema Range min=0 inclusive), ``budget.loan_params.original_principal``
   (same shape), and ``salary.paycheck_deductions.annual_cap`` (already
   uses min=0.01 in schema; the alignment is that
   ``places=2`` quantises sub-cent input to two decimal places, so
   ``min=Decimal("0"), min_inclusive=False`` is functionally identical
   to ``min=Decimal("0.01")`` -- the rewrite makes the rule more
   explicit and matches the rest of the schema file's idiom for
   strictly-positive monetary fields).  The schema fixes themselves
   live in ``app/schemas/validation.py`` -- this migration adds no DDL
   for them but documents the tie-up here so the audit trail is
   complete.

Pre-flight semantics
--------------------

A NOT NULL constraint cannot be added when existing rows hold NULL.
PostgreSQL would refuse the ALTER, but the resulting error names the
column rather than the offending rows, and the operator would have to
re-derive the safe backfill value by hand.  Instead, this migration
runs an idempotent ``UPDATE ... SET col = <default> WHERE col IS NULL``
for every F-068 column inside the same transaction as the ALTER.  The
backfill values are taken from the model's ``default=`` -- the value
the ORM has been writing for every row created via the application
since the column was introduced -- so a backfilled row carries the
same value it would have had if the column were always NOT NULL.

The migration is fully idempotent on a clean database: the UPDATEs are
no-ops when no NULLs exist, and ``op.alter_column`` accepts a column
that is already NOT NULL with the same ``server_default``.

Audit reference: F-068 + F-134 + F-135 + F-106 + F-107 / commit C-25
of the 2026-04-15 security remediation plan.

Revision ID: c5d20b701a4e
Revises: b71c4a8f5d3e
Create Date: 2026-05-08 01:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "c5d20b701a4e"
down_revision = "b71c4a8f5d3e"
branch_labels = None
depends_on = None


# ── F-068 boolean / sort_order NOT NULL + server_default sweep ────
#
# Each entry: (schema, table, column, sa_type, server_default_text,
#              backfill_literal_sql).
#
# ``sa_type`` is the SQLAlchemy type used in ``existing_type`` so
# Alembic emits the correct ALTER COLUMN ... TYPE clause when the
# bound dialect needs one.  All entries here are existing columns
# whose physical type is unchanged; ``existing_type`` is informational.
#
# ``server_default_text`` is the SQL fragment passed to
# ``sa.text()`` for the new server_default.  Booleans use the
# PostgreSQL literal ``true`` / ``false`` (unquoted); integers use
# the unquoted decimal literal.
#
# ``backfill_literal_sql`` is the literal substituted into
# ``UPDATE ... SET col = <literal> WHERE col IS NULL`` to fill any
# pre-existing NULL row before the ALTER.  Matches the model's
# Python-side ``default=`` so a backfilled row carries the same
# value the ORM has always written.
_BOOLEAN_LOCK_SPECS: list[tuple[str, str, str, sa.types.TypeEngine, str, str]] = [
    # ── auth ──────────────────────────────────────────────────────
    ("auth", "users", "is_active", sa.Boolean(), "true", "TRUE"),
    ("auth", "mfa_configs", "is_enabled", sa.Boolean(), "false", "FALSE"),
    # ── budget ────────────────────────────────────────────────────
    ("budget", "accounts", "is_active", sa.Boolean(), "true", "TRUE"),
    ("budget", "accounts", "sort_order", sa.Integer(), "0", "0"),
    ("budget", "categories", "sort_order", sa.Integer(), "0", "0"),
    ("budget", "escrow_components", "is_active", sa.Boolean(), "true", "TRUE"),
    ("budget", "savings_goals", "is_active", sa.Boolean(), "true", "TRUE"),
    ("budget", "scenarios", "is_baseline", sa.Boolean(), "false", "FALSE"),
    (
        "budget", "transaction_templates", "is_active",
        sa.Boolean(), "true", "TRUE",
    ),
    (
        "budget", "transaction_templates", "sort_order",
        sa.Integer(), "0", "0",
    ),
    ("budget", "transactions", "is_override", sa.Boolean(), "false", "FALSE"),
    ("budget", "transactions", "is_deleted", sa.Boolean(), "false", "FALSE"),
    (
        "budget", "transfer_templates", "is_active",
        sa.Boolean(), "true", "TRUE",
    ),
    (
        "budget", "transfer_templates", "sort_order",
        sa.Integer(), "0", "0",
    ),
    # ── salary ────────────────────────────────────────────────────
    (
        "salary", "calibration_overrides", "is_active",
        sa.Boolean(), "true", "TRUE",
    ),
    (
        "salary", "paycheck_deductions", "inflation_enabled",
        sa.Boolean(), "false", "FALSE",
    ),
    (
        "salary", "paycheck_deductions", "is_active",
        sa.Boolean(), "true", "TRUE",
    ),
    (
        "salary", "paycheck_deductions", "sort_order",
        sa.Integer(), "0", "0",
    ),
    ("salary", "pension_profiles", "is_active", sa.Boolean(), "true", "TRUE"),
    ("salary", "salary_profiles", "is_active", sa.Boolean(), "true", "TRUE"),
    ("salary", "salary_profiles", "sort_order", sa.Integer(), "0", "0"),
    (
        "salary", "salary_raises", "is_recurring",
        sa.Boolean(), "false", "FALSE",
    ),
    ("salary", "tax_brackets", "sort_order", sa.Integer(), "0", "0"),
]


# ── F-134 server_default-only restorations ────────────────────────
#
# Each entry: (schema, table, column, sa_type, server_default_text).
#
# These columns are already NOT NULL in the live database but lost
# their ``server_default`` (or never had it materialised).  The
# restoration aligns the live DB with the model's documented default
# and with the original migration's intent.  No backfill is needed
# because the columns are already NOT NULL, so every row already
# holds a non-NULL value.
_SERVER_DEFAULT_RESTORE_SPECS: list[
    tuple[str, str, str, sa.types.TypeEngine, str]
] = [
    # ── budget.transfers ──────────────────────────────────────────
    ("budget", "transfers", "is_override", sa.Boolean(), "false"),
    ("budget", "transfers", "is_deleted", sa.Boolean(), "false"),
    # ── budget.investment_params ──────────────────────────────────
    (
        "budget", "investment_params", "assumed_annual_return",
        sa.Numeric(precision=7, scale=5), "0.07000",
    ),
    (
        "budget", "investment_params", "employer_contribution_type",
        sa.String(length=20), "'none'",
    ),
    # ── salary.fica_configs ───────────────────────────────────────
    (
        "salary", "fica_configs", "ss_rate",
        sa.Numeric(precision=5, scale=4), "0.0620",
    ),
    (
        "salary", "fica_configs", "ss_wage_base",
        sa.Numeric(precision=12, scale=2), "176100",
    ),
    (
        "salary", "fica_configs", "medicare_rate",
        sa.Numeric(precision=5, scale=4), "0.0145",
    ),
    (
        "salary", "fica_configs", "medicare_surtax_rate",
        sa.Numeric(precision=5, scale=4), "0.0090",
    ),
    (
        "salary", "fica_configs", "medicare_surtax_threshold",
        sa.Numeric(precision=12, scale=2), "200000",
    ),
    # ── salary.pension_profiles ───────────────────────────────────
    (
        "salary", "pension_profiles", "name",
        sa.String(length=100), "'Pension'",
    ),
    (
        "salary", "pension_profiles", "consecutive_high_years",
        sa.Integer(), "4",
    ),
    # ── salary.salary_profiles ────────────────────────────────────
    (
        "salary", "salary_profiles", "state_code",
        sa.String(length=2), "'NC'",
    ),
    (
        "salary", "salary_profiles", "pay_periods_per_year",
        sa.Integer(), "26",
    ),
    (
        "salary", "salary_profiles", "qualifying_children",
        sa.Integer(), "0",
    ),
    (
        "salary", "salary_profiles", "other_dependents",
        sa.Integer(), "0",
    ),
    (
        "salary", "salary_profiles", "additional_income",
        sa.Numeric(precision=12, scale=2), "0",
    ),
    (
        "salary", "salary_profiles", "additional_deductions",
        sa.Numeric(precision=12, scale=2), "0",
    ),
    (
        "salary", "salary_profiles", "extra_withholding",
        sa.Numeric(precision=12, scale=2), "0",
    ),
]


def upgrade():
    """Backfill NULLs, lock columns NOT NULL, restore missing server_defaults.

    Execution order:

    1. Backfill phase -- one ``UPDATE`` per F-068 column for every row
       still holding NULL.  These statements are no-ops on a clean
       DB; they exist so the migration is safe to run against a
       production database that pre-dates the column constraints.
    2. F-068 ALTER phase -- one ``ALTER COLUMN`` per spec, setting
       ``NOT NULL`` and the matching ``server_default``.
    3. F-134 ALTER phase -- one ``ALTER COLUMN ... SET DEFAULT`` per
       spec for columns that are already NOT NULL but missing
       their documented server_default.

    Alembic wraps the entire upgrade in a single transaction; a
    failure in any phase rolls back every preceding change atomically.
    """
    bind = op.get_bind()

    # ── 1. Backfill NULLs for F-068 columns ───────────────────────
    for schema, table, column, _type, _server_default, backfill in (
        _BOOLEAN_LOCK_SPECS
    ):
        bind.execute(
            sa.text(
                f"UPDATE {schema}.{table} "
                f"SET {column} = {backfill} "
                f"WHERE {column} IS NULL"
            )
        )

    # ── 2. F-068 ALTER: NOT NULL + server_default ─────────────────
    for schema, table, column, sa_type, server_default, _backfill in (
        _BOOLEAN_LOCK_SPECS
    ):
        op.alter_column(
            table, column,
            existing_type=sa_type,
            nullable=False,
            server_default=sa.text(server_default),
            schema=schema,
        )

    # ── 3. F-134 ALTER: server_default only ───────────────────────
    for schema, table, column, sa_type, server_default in (
        _SERVER_DEFAULT_RESTORE_SPECS
    ):
        op.alter_column(
            table, column,
            existing_type=sa_type,
            existing_nullable=False,
            server_default=sa.text(server_default),
            schema=schema,
        )


def downgrade():
    """Revert the lock-down: drop server_defaults and relax NOT NULL.

    Reverse declaration order so a partial failure leaves a recognisable
    post-state (the failed column is the last one still locked).  The
    F-134 server_defaults are dropped first because they were applied
    last on upgrade; then F-068 columns are reverted to nullable with
    no default.

    Backfilled values remain in place -- the downgrade does not
    re-NULL any row.  Rolling forward again is a no-op on the
    backfill phase and idempotent on the ALTERs.
    """
    # ── Reverse F-134 server_default restorations ─────────────────
    for schema, table, column, sa_type, _server_default in reversed(
        _SERVER_DEFAULT_RESTORE_SPECS
    ):
        op.alter_column(
            table, column,
            existing_type=sa_type,
            existing_nullable=False,
            server_default=None,
            schema=schema,
        )

    # ── Reverse F-068 lock ────────────────────────────────────────
    for schema, table, column, sa_type, _server_default, _backfill in reversed(
        _BOOLEAN_LOCK_SPECS
    ):
        op.alter_column(
            table, column,
            existing_type=sa_type,
            nullable=True,
            server_default=None,
            schema=schema,
        )
