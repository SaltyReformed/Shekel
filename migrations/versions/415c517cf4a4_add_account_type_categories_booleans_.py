"""add account type categories, booleans, and capitalize ref names

Revision ID: 415c517cf4a4
Revises: e138e6f55bf0
Create Date: 2026-03-28 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '415c517cf4a4'
down_revision = 'e138e6f55bf0'
branch_labels = None
depends_on = None


def upgrade():
    """Add account_type_categories table, FK and booleans on account_types,
    and capitalize display names for AccountType, TransactionType, and
    RecurrencePattern.

    Execution order matters: data migration steps (3-5) use the CURRENT
    lowercase names in WHERE clauses, so they MUST run BEFORE the
    capitalization steps (6-8).
    """
    # ── 1. Create ref.account_type_categories table ──────────────────
    op.create_table(
        'account_type_categories',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(20), unique=True, nullable=False),
        schema='ref',
    )

    # ── 2. Seed the four category rows ───────────────────────────────
    op.execute("""
        INSERT INTO ref.account_type_categories (name) VALUES
            ('Asset'), ('Liability'), ('Retirement'), ('Investment')
    """)

    # ── 3. Add category_id FK column (nullable initially) ────────────
    op.add_column(
        'account_types',
        sa.Column(
            'category_id', sa.Integer(),
            sa.ForeignKey('ref.account_type_categories.id'),
            nullable=True,
        ),
        schema='ref',
    )

    # ── 4. Migrate data from string category to FK category_id ───────
    # Uses CURRENT lowercase account type names and category strings.
    op.execute("""
        UPDATE ref.account_types
        SET category_id = c.id
        FROM ref.account_type_categories c
        WHERE (
            (ref.account_types.name IN ('checking', 'savings', 'hysa', 'money_market', 'cd', 'hsa')
             AND c.name = 'Asset')
            OR
            (ref.account_types.name IN ('credit_card', 'mortgage', 'auto_loan', 'student_loan', 'personal_loan', 'heloc')
             AND c.name = 'Liability')
            OR
            (ref.account_types.name IN ('401k', 'roth_401k', 'traditional_ira', 'roth_ira')
             AND c.name = 'Retirement')
            OR
            (ref.account_types.name IN ('brokerage', '529')
             AND c.name = 'Investment')
        )
    """)

    # ── 5. Add has_parameters and has_amortization booleans ──────────
    op.add_column(
        'account_types',
        sa.Column('has_parameters', sa.Boolean(), nullable=False, server_default='false'),
        schema='ref',
    )
    op.add_column(
        'account_types',
        sa.Column('has_amortization', sa.Boolean(), nullable=False, server_default='false'),
        schema='ref',
    )

    # Set has_parameters = TRUE for types with associated param tables.
    op.execute("""
        UPDATE ref.account_types
        SET has_parameters = TRUE
        WHERE name IN (
            'hysa', 'mortgage', 'auto_loan', 'student_loan', 'personal_loan',
            '401k', 'roth_401k', 'traditional_ira', 'roth_ira', 'brokerage'
        )
    """)

    # Set has_amortization = TRUE for loan/debt types.
    op.execute("""
        UPDATE ref.account_types
        SET has_amortization = TRUE
        WHERE name IN ('mortgage', 'auto_loan', 'student_loan', 'personal_loan', 'heloc')
    """)

    # ── 6. Capitalize AccountType names ──────────────────────────────
    _account_type_renames = [
        ("checking",       "Checking"),
        ("savings",        "Savings"),
        ("hysa",           "HYSA"),
        ("money_market",   "Money Market"),
        ("cd",             "CD"),
        ("hsa",            "HSA"),
        ("credit_card",    "Credit Card"),
        ("mortgage",       "Mortgage"),
        ("auto_loan",      "Auto Loan"),
        ("student_loan",   "Student Loan"),
        ("personal_loan",  "Personal Loan"),
        ("heloc",          "HELOC"),
        ("401k",           "401(k)"),
        ("roth_401k",      "Roth 401(k)"),
        ("traditional_ira","Traditional IRA"),
        ("roth_ira",       "Roth IRA"),
        ("brokerage",      "Brokerage"),
        ("529",            "529 Plan"),
    ]
    for old, new in _account_type_renames:
        op.execute(
            sa.text("UPDATE ref.account_types SET name = :new WHERE name = :old"),
            {"old": old, "new": new},
        )

    # ── 7. Capitalize RecurrencePattern names ────────────────────────
    _recurrence_renames = [
        ("every_period",     "Every Period"),
        ("every_n_periods",  "Every N Periods"),
        ("monthly",          "Monthly"),
        ("monthly_first",    "Monthly First"),
        ("quarterly",        "Quarterly"),
        ("semi_annual",      "Semi-Annual"),
        ("annual",           "Annual"),
        ("once",             "Once"),
    ]
    for old, new in _recurrence_renames:
        op.execute(
            sa.text("UPDATE ref.recurrence_patterns SET name = :new WHERE name = :old"),
            {"old": old, "new": new},
        )

    # ── 8. Capitalize TransactionType names ──────────────────────────
    op.execute(
        sa.text("UPDATE ref.transaction_types SET name = :new WHERE name = :old"),
        {"old": "income", "new": "Income"},
    )
    op.execute(
        sa.text("UPDATE ref.transaction_types SET name = :new WHERE name = :old"),
        {"old": "expense", "new": "Expense"},
    )

    # ── 9. Make category_id NOT NULL (all rows populated above) ──────
    op.alter_column(
        'account_types', 'category_id',
        existing_type=sa.Integer(),
        nullable=False,
        schema='ref',
    )

    # ── 10. Drop the old category string column ─────────────────────
    op.drop_column('account_types', 'category', schema='ref')


def downgrade():
    """Reverse all changes: restore category string, drop booleans and
    categories table, revert all name capitalizations.
    """
    # ── 1. Revert TransactionType names ──────────────────────────────
    op.execute(
        sa.text("UPDATE ref.transaction_types SET name = :new WHERE name = :old"),
        {"old": "Income", "new": "income"},
    )
    op.execute(
        sa.text("UPDATE ref.transaction_types SET name = :new WHERE name = :old"),
        {"old": "Expense", "new": "expense"},
    )

    # ── 2. Revert RecurrencePattern names ────────────────────────────
    _recurrence_reverts = [
        ("Every Period",     "every_period"),
        ("Every N Periods",  "every_n_periods"),
        ("Monthly",          "monthly"),
        ("Monthly First",    "monthly_first"),
        ("Quarterly",        "quarterly"),
        ("Semi-Annual",      "semi_annual"),
        ("Annual",           "annual"),
        ("Once",             "once"),
    ]
    for old, new in _recurrence_reverts:
        op.execute(
            sa.text("UPDATE ref.recurrence_patterns SET name = :new WHERE name = :old"),
            {"old": old, "new": new},
        )

    # ── 3. Revert AccountType names ──────────────────────────────────
    _account_type_reverts = [
        ("Checking",       "checking"),
        ("Savings",        "savings"),
        ("HYSA",           "hysa"),
        ("Money Market",   "money_market"),
        ("CD",             "cd"),
        ("HSA",            "hsa"),
        ("Credit Card",    "credit_card"),
        ("Mortgage",       "mortgage"),
        ("Auto Loan",      "auto_loan"),
        ("Student Loan",   "student_loan"),
        ("Personal Loan",  "personal_loan"),
        ("HELOC",          "heloc"),
        ("401(k)",         "401k"),
        ("Roth 401(k)",    "roth_401k"),
        ("Traditional IRA","traditional_ira"),
        ("Roth IRA",       "roth_ira"),
        ("Brokerage",      "brokerage"),
        ("529 Plan",       "529"),
    ]
    for old, new in _account_type_reverts:
        op.execute(
            sa.text("UPDATE ref.account_types SET name = :new WHERE name = :old"),
            {"old": old, "new": new},
        )

    # ── 4. Restore the category string column ────────────────────────
    op.add_column(
        'account_types',
        sa.Column('category', sa.String(20), nullable=True),
        schema='ref',
    )

    # ── 5. Populate category from category_id FK ─────────────────────
    op.execute("""
        UPDATE ref.account_types
        SET category = LOWER(c.name)
        FROM ref.account_type_categories c
        WHERE ref.account_types.category_id = c.id
    """)

    # ── 6. Drop boolean columns ──────────────────────────────────────
    op.drop_column('account_types', 'has_amortization', schema='ref')
    op.drop_column('account_types', 'has_parameters', schema='ref')

    # ── 7. Drop category_id FK column ────────────────────────────────
    op.drop_column('account_types', 'category_id', schema='ref')

    # ── 8. Drop account_type_categories table ────────────────────────
    op.drop_table('account_type_categories', schema='ref')
