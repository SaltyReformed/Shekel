-- Shekel Budget App -- Least-Privilege Role Provisioning
--
-- Two-role policy (audit finding F-081 / remediation Commit C-13):
--   * shekel_user (owner)            -- runs DDL, migrations, and the
--     deployment scripts.  Connection URL exposed as DATABASE_URL.
--   * shekel_app  (least-privilege)  -- DML-only role used by the
--     runtime Flask app.  Cannot DROP TABLE, ALTER TABLE, or DROP
--     ROLE.  An attacker with RCE in the Gunicorn process cannot
--     remove the audit triggers that the rebuild migration adds.
--     Connection URL exposed as DATABASE_URL_APP.
--
-- Run by entrypoint.sh after init_db.sql, with the password
-- supplied via psql's -v flag:
--
--     psql -v "APP_ROLE_PASSWORD_LITERAL=${APP_ROLE_PASSWORD}" \
--          -v "ON_ERROR_STOP=1" \
--          -f scripts/init_db_role.sql
--
-- Why the indirection through SET / current_setting():
--
--     psql variable substitution (``:'name'``) only happens outside
--     dollar-quoted blocks.  The role-provisioning DO block below
--     therefore cannot read ``:'APP_ROLE_PASSWORD_LITERAL'`` directly
--     -- the colon-name token would reach PostgreSQL unsubstituted
--     and fail with a syntax error.  The fix is to write the password
--     to a custom GUC (``shekel.app_role_password``) at the SQL
--     boundary -- where psql substitution does run -- and read it
--     from inside the DO block via ``current_setting()``.  The GUC
--     is session-local and ``WITH LOCAL`` could be used in a
--     transaction but is unnecessary here because the entire psql
--     invocation runs in one session that ends with the file.
--
-- Idempotent: existing role gets ALTER ROLE; missing role gets
-- CREATE ROLE.  Both branches use ``format(... %L, ...)`` to quote
-- the password into the dynamically-built SQL safely.

-- Smuggle the password into a session-local GUC so the DO block
-- can read it via current_setting() (psql substitution does not
-- run inside dollar-quoted blocks; see file header for rationale).
--
-- If APP_ROLE_PASSWORD_LITERAL was not supplied via psql -v, the
-- substitution below leaves the literal token ``:'APP_ROLE_PASSWORD_LITERAL'``
-- in the SQL stream, which PostgreSQL rejects with a syntax error.
-- Combined with ``-v ON_ERROR_STOP=1`` (set by entrypoint.sh) that
-- gives a non-zero exit code, which the entrypoint shell captures
-- via ``set -eEo pipefail``.  Defending against the empty-string
-- case (``-v APP_ROLE_PASSWORD_LITERAL=``) happens in the DO block
-- below via ``current_setting`` + ``RAISE EXCEPTION``.
SET shekel.app_role_password TO :'APP_ROLE_PASSWORD_LITERAL';

DO $$
BEGIN
    IF current_setting('shekel.app_role_password') = '' THEN
        RAISE EXCEPTION
            'APP_ROLE_PASSWORD_LITERAL must be supplied via psql -v';
    END IF;

    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'shekel_app') THEN
        EXECUTE format(
            'CREATE ROLE shekel_app WITH LOGIN PASSWORD %L',
            current_setting('shekel.app_role_password')
        );
    ELSE
        EXECUTE format(
            'ALTER ROLE shekel_app WITH LOGIN PASSWORD %L',
            current_setting('shekel.app_role_password')
        );
    END IF;
END$$;

-- Clear the GUC immediately after use so the password does not
-- linger in pg_stat_activity for the rest of this session.  Other
-- sessions never saw it.
RESET shekel.app_role_password;

-- ── Connection + schema usage ─────────────────────────────────────
-- Note: the database name "shekel" is the production default; if a
-- deployment uses a different DB name the GRANT below should be
-- updated to match, or the operator can run `GRANT CONNECT ON
-- DATABASE <name> TO shekel_app` separately.  The role-provisioning
-- file is intentionally not parameterised on DB name to keep it
-- readable -- a developer running this against a dev-named database
-- updates the literal once and moves on.
GRANT CONNECT ON DATABASE shekel TO shekel_app;
GRANT USAGE ON SCHEMA auth, budget, salary, ref TO shekel_app;

-- ── DML privileges on existing tables ─────────────────────────────
-- ALL TABLES IN SCHEMA covers every table that already exists at
-- this point (most importantly the ref tables seeded by
-- seed_ref_tables.py).  Tables created later by Alembic migrations
-- pick up DML privileges via the ALTER DEFAULT PRIVILEGES block
-- below, which only affects future objects -- existing objects must
-- be granted directly here so seed scripts can populate them.
GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA auth, budget, salary TO shekel_app;
GRANT SELECT
    ON ALL TABLES IN SCHEMA ref TO shekel_app;

GRANT USAGE
    ON ALL SEQUENCES IN SCHEMA auth, budget, salary, ref TO shekel_app;

-- ── Default privileges for future objects ─────────────────────────
-- ALTER DEFAULT PRIVILEGES is scoped to the role that runs it (the
-- "FOR ROLE" clause defaults to current_user, which is shekel_user
-- when this file runs from entrypoint.sh).  The clause grants the
-- listed privileges automatically on every new object that
-- shekel_user creates in the named schema -- so when Alembic
-- creates a new table in budget/salary/auth, the runtime app role
-- immediately has DML on it without a manual GRANT after every
-- migration.
ALTER DEFAULT PRIVILEGES IN SCHEMA auth, budget, salary
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO shekel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA ref
    GRANT SELECT ON TABLES TO shekel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA auth, budget, salary, ref
    GRANT USAGE ON SEQUENCES TO shekel_app;

-- ── system schema (audit log) ─────────────────────────────────────
-- The audit infrastructure (table, function, triggers) is created
-- by the rebuild migration, not here -- but the schema-level USAGE
-- grant and the default-privilege rule for FUTURE objects in the
-- system schema must be in place BEFORE the migration runs so the
-- audit_log table inherits the right ACL when the migration creates
-- it.  The conditional SELECT/INSERT GRANT block inside the
-- migration covers the table itself once it exists; the ALTER
-- DEFAULT PRIVILEGES below covers any further system.* objects
-- (e.g. the planned read-audit instrumentation in remediation
-- Commit C-52).
GRANT USAGE ON SCHEMA system TO shekel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA system
    GRANT SELECT, INSERT ON TABLES TO shekel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA system
    GRANT USAGE ON SEQUENCES TO shekel_app;
