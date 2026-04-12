"""add entry tracking and companion support

Revision ID: b961beb0edf6
Revises: f15a72a3da6c
Create Date: 2026-04-11 12:00:00.000000
"""
from alembic import op

# Revision identifiers, used by Alembic.
revision = 'b961beb0edf6'
down_revision = 'f15a72a3da6c'
branch_labels = None
depends_on = None


def upgrade():
    """Add user_roles ref table, template flags, user role columns, and transaction_entries."""
    # -- User roles ref table (must precede user column addition) --
    op.execute("""
        CREATE TABLE ref.user_roles (
            id SERIAL PRIMARY KEY,
            name VARCHAR(20) NOT NULL UNIQUE
        )
    """)
    op.execute("""
        INSERT INTO ref.user_roles (id, name)
        VALUES (1, 'owner'), (2, 'companion')
    """)

    # -- Template flags --
    op.execute("""
        ALTER TABLE budget.transaction_templates
            ADD COLUMN track_individual_purchases BOOLEAN NOT NULL DEFAULT FALSE
    """)
    op.execute("""
        ALTER TABLE budget.transaction_templates
            ADD COLUMN companion_visible BOOLEAN NOT NULL DEFAULT FALSE
    """)

    # -- User companion support --
    op.execute("""
        ALTER TABLE auth.users
            ADD COLUMN role_id INTEGER NOT NULL DEFAULT 1
                REFERENCES ref.user_roles(id) ON DELETE RESTRICT
    """)
    op.execute("""
        ALTER TABLE auth.users
            ADD COLUMN linked_owner_id INTEGER
    """)
    op.execute("""
        ALTER TABLE auth.users
            ADD CONSTRAINT fk_users_linked_owner
                FOREIGN KEY (linked_owner_id)
                REFERENCES auth.users(id)
                ON DELETE SET NULL
    """)

    # -- Transaction entries table --
    op.execute("""
        CREATE TABLE budget.transaction_entries (
            id SERIAL PRIMARY KEY,
            transaction_id INTEGER NOT NULL
                REFERENCES budget.transactions(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL
                REFERENCES auth.users(id) ON DELETE CASCADE,
            amount NUMERIC(12,2) NOT NULL,
            description VARCHAR(200) NOT NULL,
            entry_date DATE NOT NULL DEFAULT CURRENT_DATE,
            is_credit BOOLEAN NOT NULL DEFAULT FALSE,
            credit_payback_id INTEGER
                REFERENCES budget.transactions(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_transaction_entries_positive_amount CHECK (amount > 0)
        )
    """)
    op.execute("""
        CREATE INDEX idx_transaction_entries_txn_id
            ON budget.transaction_entries(transaction_id)
    """)
    op.execute("""
        CREATE INDEX idx_transaction_entries_txn_credit
            ON budget.transaction_entries(transaction_id, is_credit)
    """)


def downgrade():
    """Remove transaction_entries table, user role columns, template flags, and user_roles ref table."""
    op.execute("DROP TABLE budget.transaction_entries")
    op.execute("ALTER TABLE auth.users DROP CONSTRAINT IF EXISTS fk_users_linked_owner")
    op.execute("ALTER TABLE auth.users DROP COLUMN IF EXISTS linked_owner_id")
    op.execute("ALTER TABLE auth.users DROP COLUMN IF EXISTS role_id")
    op.execute("DROP TABLE IF EXISTS ref.user_roles")
    op.execute(
        "ALTER TABLE budget.transaction_templates "
        "DROP COLUMN IF EXISTS companion_visible"
    )
    op.execute(
        "ALTER TABLE budget.transaction_templates "
        "DROP COLUMN IF EXISTS track_individual_purchases"
    )
