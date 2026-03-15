"""Add system.audit_log table, audit trigger function, and triggers.

Creates the system.audit_log table for tracking changes to financial
and auth tables, a generic PL/pgSQL trigger function that handles
INSERT/UPDATE/DELETE, and attaches AFTER triggers to 22 tables.

Revision ID: a8b1c2d3e4f5
Revises: 2ae345ea9048
Create Date: 2026-03-14
"""
from alembic import op


# revision identifiers
revision = "a8b1c2d3e4f5"
down_revision = "2ae345ea9048"
branch_labels = None
depends_on = None

# Tables to audit: (schema, table_name).
_AUDITED_TABLES = [
    # Budget schema
    ("budget", "accounts"),
    ("budget", "transactions"),
    ("budget", "transaction_templates"),
    ("budget", "transfers"),
    ("budget", "transfer_templates"),
    ("budget", "savings_goals"),
    ("budget", "recurrence_rules"),
    ("budget", "pay_periods"),
    ("budget", "account_anchor_history"),
    ("budget", "hysa_params"),
    ("budget", "mortgage_params"),
    ("budget", "mortgage_rate_history"),
    ("budget", "escrow_components"),
    ("budget", "auto_loan_params"),
    ("budget", "investment_params"),
    # Salary schema
    ("salary", "salary_profiles"),
    ("salary", "salary_raises"),
    ("salary", "paycheck_deductions"),
    ("salary", "pension_profiles"),
    # Auth schema
    ("auth", "users"),
    ("auth", "user_settings"),
    ("auth", "mfa_configs"),
]


def upgrade():
    """Create audit_log table, trigger function, and attach triggers."""

    # --- 1. Create system.audit_log table --------------------------------
    op.execute("""
        CREATE TABLE system.audit_log (
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
            executed_at     TIMESTAMPTZ     DEFAULT now()
        )
    """)

    # --- 2. Create indexes -----------------------------------------------
    op.execute("""
        CREATE INDEX idx_audit_log_table
            ON system.audit_log (table_schema, table_name)
    """)
    op.execute("""
        CREATE INDEX idx_audit_log_executed
            ON system.audit_log (executed_at)
    """)
    op.execute("""
        CREATE INDEX idx_audit_log_row
            ON system.audit_log (table_name, row_id)
    """)

    # --- 3. Create generic audit trigger function ------------------------
    op.execute(r"""
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
            -- Read the application user_id from the session variable.
            -- Returns NULL if not set (e.g. direct psql, migrations).
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

                -- Compute changed fields by comparing OLD and NEW JSONB.
                FOR v_key IN
                    SELECT key FROM jsonb_each(v_new_data)
                    WHERE NOT v_old_data ? key
                       OR v_old_data -> key IS DISTINCT FROM v_new_data -> key
                LOOP
                    v_changed := array_append(v_changed, v_key);
                END LOOP;

                -- Skip audit if nothing actually changed.
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
        $$ LANGUAGE plpgsql;
    """)

    # --- 4. Attach triggers to all audited tables ------------------------
    for schema, table in _AUDITED_TABLES:
        trigger_name = f"audit_{table}"
        op.execute(f"""
            CREATE TRIGGER {trigger_name}
            AFTER INSERT OR UPDATE OR DELETE ON {schema}.{table}
            FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()
        """)


def downgrade():
    """Remove triggers, trigger function, and audit_log table."""
    for schema, table in _AUDITED_TABLES:
        trigger_name = f"audit_{table}"
        op.execute(
            f"DROP TRIGGER IF EXISTS {trigger_name} ON {schema}.{table}"
        )

    op.execute("DROP FUNCTION IF EXISTS system.audit_trigger_func()")
    op.execute("DROP TABLE IF EXISTS system.audit_log")
