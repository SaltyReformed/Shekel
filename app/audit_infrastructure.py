"""Shared definitions for the ``system.audit_log`` audit infrastructure.

Three callers must produce identical audit infrastructure across the
project:

1. The Alembic rebuild migration that ships with commit C-13 of the
   2026-04-15 security remediation plan
   (``migrations/versions/<rev>_rebuild_audit_infrastructure.py``).
2. ``scripts/init_database.py``, which initialises a fresh database
   via ``db.create_all()`` followed by an Alembic ``stamp`` -- a path
   that bypasses the migration chain and would otherwise leave the
   audit triggers absent (audit finding F-028).
3. ``tests/conftest.py``, which mirrors the production schema during
   the test session so service-layer behaviour can be asserted
   end-to-end with audit_log rows present.

Centralising the SQL strings and the canonical table list here is the
only way to keep those three call sites in lock-step.  The previous
arrangement (raw SQL embedded both in the migration file and in
``conftest.py``) drifted: the test fixture grew six tables behind the
production migration, with no automated check to catch the gap.

Adding a NEW table to the ``budget``, ``salary``, or ``auth`` schema
REQUIRES adding a row to ``AUDITED_TABLES`` here AND running
``flask db migrate`` so the rebuild migration's idempotent CREATE
TRIGGER picks it up on the next ``flask db upgrade``.  The
``coding-standards.md`` "Schema Design" section documents this
invariant; the entrypoint trigger-count assertion enforces it at
container start.
"""

from __future__ import annotations

from typing import Callable, Iterable


# ---------------------------------------------------------------------------
# Canonical audited-table list
# ---------------------------------------------------------------------------
#
# Sorted alphabetically by ``(schema, table)``.  Sorting (rather than
# topical grouping) makes a forgotten table immediately visible in a
# diff and removes the temptation to slot a new table "where it fits"
# rather than at the end of its schema block.
#
# Inclusion criteria:
#   * Tables in the ``budget``, ``salary``, or ``auth`` schema that
#     hold user-controlled financial state, salary configuration,
#     calibration overrides, tax-config admin data, or auth state.
#   * The ``ref`` schema is intentionally excluded with one exception
#     (``ref.account_types``).  Read-only seed catalogues like
#     ``ref.statuses`` or ``ref.transaction_types`` would drown the
#     trail in seed-script noise without any forensic value, so they
#     stay out.  ``ref.account_types`` is special because commit C-28
#     converted it into a multi-tenant table -- owners can create,
#     rename, and delete their own custom rows through
#     ``app/routes/accounts.py``, and those mutations need a
#     tamper-resistant forensic record for the same reasons every
#     other user-mutable financial table does.  The seed script's
#     idempotent upsert pattern still touches the seeded built-ins
#     occasionally, but those writes carry ``user_id IS NULL`` in the
#     audit row and are easy to filter out of operator queries.
#   * The ``system`` schema (``audit_log`` itself) is excluded to
#     avoid recursive trigger fires.
AUDITED_TABLES: tuple[tuple[str, str], ...] = (
    # ── auth schema ──────────────────────────────────────────────────
    ("auth", "mfa_configs"),
    ("auth", "user_settings"),
    ("auth", "users"),
    # ── budget schema ────────────────────────────────────────────────
    ("budget", "account_anchor_history"),
    ("budget", "accounts"),
    ("budget", "categories"),
    ("budget", "escrow_components"),
    ("budget", "interest_params"),
    ("budget", "investment_params"),
    ("budget", "loan_params"),
    ("budget", "pay_periods"),
    ("budget", "rate_history"),
    ("budget", "recurrence_rules"),
    ("budget", "savings_goals"),
    ("budget", "scenarios"),
    ("budget", "transaction_entries"),
    ("budget", "transaction_templates"),
    ("budget", "transactions"),
    ("budget", "transfer_templates"),
    ("budget", "transfers"),
    # ── ref schema (multi-tenant tables only) ────────────────────────
    # Per the inclusion criteria above: ``ref.account_types`` carries
    # owner-scoped rows after C-28 / F-044 and is the only ref table
    # in the audited set.  Other ref tables remain read-only seed
    # catalogues and must NOT be added without a corresponding shift
    # in the seed script's write semantics.
    ("ref", "account_types"),
    # ── salary schema ────────────────────────────────────────────────
    ("salary", "calibration_deduction_overrides"),
    ("salary", "calibration_overrides"),
    ("salary", "fica_configs"),
    ("salary", "paycheck_deductions"),
    ("salary", "pension_profiles"),
    ("salary", "salary_profiles"),
    ("salary", "salary_raises"),
    ("salary", "state_tax_configs"),
    ("salary", "tax_bracket_sets"),
    ("salary", "tax_brackets"),
)


# Number of triggers the live database must carry once the rebuild
# migration has run.  The entrypoint health check refuses to start
# Gunicorn when ``COUNT(pg_trigger WHERE tgname LIKE 'audit_%') <
# EXPECTED_TRIGGER_COUNT``; the migration tests assert the same number
# round-trips through upgrade and downgrade.  ``len(AUDITED_TABLES)``
# rather than a hard-coded constant so adding a row above is the only
# edit a future commit needs to make.
EXPECTED_TRIGGER_COUNT: int = len(AUDITED_TABLES)


# ---------------------------------------------------------------------------
# Idempotent SQL building blocks
# ---------------------------------------------------------------------------
#
# Each constant below is a single SQL statement.  They are intentionally
# split into individual statements so callers using ``op.execute`` (one
# statement per call, the Alembic idiom) and callers using
# ``session.execute(text(...))`` (one statement per ``text``, the
# SQLAlchemy idiom) both work without surgery.  Multi-statement strings
# would also work in PostgreSQL via simple-query, but splitting keeps
# error messages on the right line.

_CREATE_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS system"

# CREATE TABLE IF NOT EXISTS leaves a pre-existing table untouched
# (it does not re-validate the column list or constraints), so the
# definition here must match the original a8b1c2d3e4f5 migration's
# columns exactly.  Any future column change needs its own migration
# step that ALTERs the table -- this file should not be edited to
# "fix" a column drift between the constant and the live schema.
_CREATE_AUDIT_LOG_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS system.audit_log (
    id              BIGSERIAL       PRIMARY KEY,
    table_schema    VARCHAR(50)     NOT NULL,
    table_name      VARCHAR(100)    NOT NULL,
    operation       VARCHAR(10)     NOT NULL,
    row_id          INTEGER,
    old_data        JSONB,
    new_data        JSONB,
    changed_fields  TEXT[],
    user_id         INTEGER,
    db_user         VARCHAR(100)    DEFAULT current_user,
    executed_at     TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT ck_audit_log_operation
        CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE'))
)
"""

_CREATE_INDEXES_SQL: tuple[str, ...] = (
    """CREATE INDEX IF NOT EXISTS idx_audit_log_table
        ON system.audit_log (table_schema, table_name)""",
    """CREATE INDEX IF NOT EXISTS idx_audit_log_executed
        ON system.audit_log (executed_at)""",
    """CREATE INDEX IF NOT EXISTS idx_audit_log_row
        ON system.audit_log (table_name, row_id)""",
)

# CREATE OR REPLACE FUNCTION is idempotent and atomically swaps the
# function body if a previous version exists -- safe to re-run on a
# database that already has the trigger function.  The body is
# verbatim from the original a8b1c2d3e4f5 migration; the only change
# in the rebuild is moving from a non-idempotent CREATE TABLE / CREATE
# INDEX above to the IF NOT EXISTS variants and tightening the
# operation column with a CHECK constraint.
_CREATE_TRIGGER_FUNC_SQL = r"""
CREATE OR REPLACE FUNCTION system.audit_trigger_func()
RETURNS TRIGGER AS $$
DECLARE
    v_old_data  JSONB;
    v_new_data  JSONB;
    v_changed   TEXT[] := '{}';
    v_user_id   INTEGER;
    v_row_id    INTEGER;
    v_key       TEXT;
BEGIN
    -- Read the application user_id from the session-local variable
    -- set by the Flask before_request hook.  current_setting(...,
    -- true) returns NULL instead of raising when the variable is
    -- absent; the inner BEGIN ... EXCEPTION block additionally
    -- swallows the integer parse error that would fire on a
    -- malformed value (e.g. an empty string written by a future
    -- caller).  v_user_id NULL is the documented "no authenticated
    -- user" sentinel; tests assert this path on direct DB writes.
    BEGIN
        v_user_id := current_setting('app.current_user_id', true)::INTEGER;
    EXCEPTION WHEN OTHERS THEN
        v_user_id := NULL;
    END;

    IF TG_OP = 'DELETE' THEN
        v_old_data := to_jsonb(OLD);
        v_row_id   := OLD.id;
        INSERT INTO system.audit_log
            (table_schema, table_name, operation, row_id,
             old_data, new_data, changed_fields, user_id)
        VALUES
            (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, v_row_id,
             v_old_data, NULL, NULL, v_user_id);
        RETURN OLD;

    ELSIF TG_OP = 'INSERT' THEN
        v_new_data := to_jsonb(NEW);
        v_row_id   := NEW.id;
        INSERT INTO system.audit_log
            (table_schema, table_name, operation, row_id,
             old_data, new_data, changed_fields, user_id)
        VALUES
            (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, v_row_id,
             NULL, v_new_data, NULL, v_user_id);
        RETURN NEW;

    ELSIF TG_OP = 'UPDATE' THEN
        v_old_data := to_jsonb(OLD);
        v_new_data := to_jsonb(NEW);
        v_row_id   := NEW.id;
        -- Compute the symmetric difference of OLD and NEW JSONB.
        -- IS DISTINCT FROM treats two NULLs as equal so a column that
        -- moved between NULL and the same NULL does not flag.
        FOR v_key IN
            SELECT key FROM jsonb_each(v_new_data)
            WHERE NOT v_old_data ? key
               OR v_old_data -> key IS DISTINCT FROM v_new_data -> key
        LOOP
            v_changed := array_append(v_changed, v_key);
        END LOOP;
        -- Suppress no-op UPDATEs (UPDATE x SET name = name).  Without
        -- this guard the audit log fills with empty changed_fields
        -- rows that add no forensic value.
        IF array_length(v_changed, 1) IS NULL THEN
            RETURN NEW;
        END IF;
        INSERT INTO system.audit_log
            (table_schema, table_name, operation, row_id,
             old_data, new_data, changed_fields, user_id)
        VALUES
            (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, v_row_id,
             v_old_data, v_new_data, v_changed, v_user_id);
        RETURN NEW;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""

# Conditional GRANT block: if the ``shekel_app`` role exists (created
# by ``scripts/init_db.sql`` in the production Docker entrypoint),
# grant USAGE/SELECT/INSERT on the audit infrastructure so the
# least-privilege app role can write audit_log rows through the
# trigger and read them for forensic queries.  When the role does
# not exist (developer laptop, CI test container), the block is a
# no-op -- the migration must still succeed.
_GRANT_APP_ROLE_SQL = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'shekel_app') THEN
        GRANT USAGE ON SCHEMA system TO shekel_app;
        GRANT SELECT, INSERT ON system.audit_log TO shekel_app;
        GRANT USAGE ON SEQUENCE system.audit_log_id_seq TO shekel_app;
    END IF;
END$$
"""


def _trigger_sql_for_table(schema: str, table: str) -> Iterable[str]:
    """Yield the (idempotent) SQL statements that attach an audit trigger.

    PostgreSQL has no ``CREATE TRIGGER IF NOT EXISTS``, so the function
    pairs ``DROP TRIGGER IF EXISTS`` with a fresh ``CREATE TRIGGER`` to
    achieve the same effect.  The drop is harmless on a database that
    has never carried the trigger; the create is identical on every run
    so a re-execution leaves the trigger pinned to the same definition
    as the latest deploy.

    Args:
        schema: PostgreSQL schema (e.g. ``"budget"``).
        table:  Table name (e.g. ``"transactions"``).

    Yields:
        Two SQL statements: a guarded DROP and a CREATE.  Trigger name
        is fixed at ``audit_<table>`` to keep enumeration via
        ``pg_trigger.tgname LIKE 'audit_%'`` simple.
    """
    trigger_name = f"audit_{table}"
    yield f"DROP TRIGGER IF EXISTS {trigger_name} ON {schema}.{table}"
    yield (
        f"CREATE TRIGGER {trigger_name} "
        f"AFTER INSERT OR UPDATE OR DELETE ON {schema}.{table} "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def apply_audit_infrastructure(executor: Callable[[str], object]) -> None:
    """Idempotently materialise the audit log infrastructure.

    Executes, in order:

    1. ``CREATE SCHEMA IF NOT EXISTS system``
    2. ``CREATE TABLE IF NOT EXISTS system.audit_log`` and three
       supporting indexes
    3. ``CREATE OR REPLACE FUNCTION system.audit_trigger_func``
    4. ``DROP TRIGGER IF EXISTS`` followed by ``CREATE TRIGGER`` for
       every entry in :data:`AUDITED_TABLES`
    5. A guarded ``GRANT`` block that gives the ``shekel_app`` role
       (when present) the USAGE/SELECT/INSERT it needs to write audit
       rows through the trigger; the block is a no-op when the role
       has not been provisioned (developer laptop, CI container).

    All statements are idempotent: running the function twice in a row
    must be indistinguishable from running it once.  This invariant is
    exercised by ``tests/test_models/test_audit_migration.py`` and by
    the upgrade-downgrade-upgrade test in
    ``tests/test_integration/test_audit_triggers.py``.

    Args:
        executor: Single-argument callable that accepts a SQL string
            and runs it.  Pass ``op.execute`` from inside an Alembic
            migration; pass ``lambda s: session.execute(text(s))``
            from inside a SQLAlchemy session.  Errors propagate --
            the caller is responsible for the outer transaction
            (Alembic wraps the migration; SQLAlchemy callers must
            commit explicitly).
    """
    executor(_CREATE_SCHEMA_SQL)
    executor(_CREATE_AUDIT_LOG_TABLE_SQL)
    for index_sql in _CREATE_INDEXES_SQL:
        executor(index_sql)
    executor(_CREATE_TRIGGER_FUNC_SQL)
    for schema, table in AUDITED_TABLES:
        for sql in _trigger_sql_for_table(schema, table):
            executor(sql)
    executor(_GRANT_APP_ROLE_SQL)


def remove_audit_infrastructure(executor: Callable[[str], object]) -> None:
    """Inverse of :func:`apply_audit_infrastructure` for the migration's downgrade.

    Drops, in safe order:

    1. Every audit trigger named in :data:`AUDITED_TABLES` (guarded
       with ``IF EXISTS`` so a partially-built infrastructure unwinds
       cleanly).
    2. ``system.audit_trigger_func``.
    3. ``system.audit_log`` (with ``CASCADE`` to break any forensic
       view that might have been built on it -- the audit trail is
       the user-facing artefact, the table is its storage).

    Notes:
        * The ``system`` schema itself is intentionally **not** dropped.
          ``CREATE SCHEMA IF NOT EXISTS system`` runs idempotently in
          ``scripts/init_db.sql`` and a downgrade should not hand a
          subsequent re-upgrade an empty schema slot.
        * The conditional ``shekel_app`` GRANT block from
          :func:`apply_audit_infrastructure` does not require a paired
          REVOKE -- dropping the table cascades the privileges away.

    Args:
        executor: Single-argument callable that accepts a SQL string
            and runs it.  Same contract as
            :func:`apply_audit_infrastructure`.
    """
    for schema, table in AUDITED_TABLES:
        executor(f"DROP TRIGGER IF EXISTS audit_{table} ON {schema}.{table}")
    executor("DROP FUNCTION IF EXISTS system.audit_trigger_func()")
    executor("DROP TABLE IF EXISTS system.audit_log CASCADE")
