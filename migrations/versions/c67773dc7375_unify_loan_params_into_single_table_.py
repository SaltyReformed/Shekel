"""unify loan params into single table with rate history rename

Revision ID: c67773dc7375
Revises: 415c517cf4a4
Create Date: 2026-03-29 17:13:01.510576

Consolidates budget.auto_loan_params and budget.mortgage_params into a
single budget.loan_params table.  Renames budget.mortgage_rate_history
to budget.rate_history.  Adds icon_class and max_term_months columns
to ref.account_types.  Changes HELOC has_parameters from FALSE to TRUE.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'c67773dc7375'
down_revision = '415c517cf4a4'
branch_labels = None
depends_on = None

# Icon class and max term months for each seeded account type.
_ACCT_TYPE_META = {
    "Checking":        ("bi-wallet2",        None),
    "Savings":         ("bi-piggy-bank",      None),
    "HYSA":            ("bi-piggy-bank",      None),
    "Money Market":    ("bi-cash-stack",      None),
    "CD":              ("bi-safe",            None),
    "HSA":             ("bi-heart-pulse",     None),
    "Credit Card":     ("bi-credit-card",     None),
    "Mortgage":        ("bi-house",           600),
    "Auto Loan":       ("bi-car-front",       120),
    "Student Loan":    ("bi-mortarboard",     300),
    "Personal Loan":   ("bi-cash-coin",       120),
    "HELOC":           ("bi-bank",            360),
    "401(k)":          ("bi-graph-up-arrow",  None),
    "Roth 401(k)":     ("bi-graph-up-arrow",  None),
    "Traditional IRA": ("bi-graph-up-arrow",  None),
    "Roth IRA":        ("bi-graph-up-arrow",  None),
    "Brokerage":       ("bi-bar-chart-line",  None),
    "529 Plan":        ("bi-mortarboard",     None),
}


def upgrade():
    """Apply forward migration."""
    # ── Step 1: Add columns to ref.account_types ────────────────────
    op.add_column(
        "account_types",
        sa.Column("icon_class", sa.String(30), nullable=True),
        schema="ref",
    )
    op.add_column(
        "account_types",
        sa.Column("max_term_months", sa.Integer, nullable=True),
        schema="ref",
    )

    # Backfill icon_class and max_term_months.
    account_types = sa.table(
        "account_types",
        sa.column("name", sa.String),
        sa.column("icon_class", sa.String),
        sa.column("max_term_months", sa.Integer),
        sa.column("has_parameters", sa.Boolean),
        schema="ref",
    )
    for name, (icon, max_term) in _ACCT_TYPE_META.items():
        op.execute(
            account_types.update()
            .where(account_types.c.name == name)
            .values(icon_class=icon, max_term_months=max_term)
        )

    # Change HELOC has_parameters from FALSE to TRUE.
    op.execute(
        account_types.update()
        .where(account_types.c.name == "HELOC")
        .values(has_parameters=True)
    )

    # ── Step 2: Create budget.loan_params ───────────────────────────
    op.create_table(
        "loan_params",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("original_principal", sa.Numeric(12, 2), nullable=False),
        sa.Column("current_principal", sa.Numeric(12, 2), nullable=False),
        sa.Column("interest_rate", sa.Numeric(7, 5), nullable=False),
        sa.Column("term_months", sa.Integer, nullable=False),
        sa.Column("origination_date", sa.Date, nullable=False),
        sa.Column(
            "payment_day",
            sa.Integer,
            sa.CheckConstraint(
                "payment_day >= 1 AND payment_day <= 31",
                name="ck_loan_params_payment_day",
            ),
            nullable=False,
        ),
        sa.Column(
            "is_arm",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("arm_first_adjustment_months", sa.Integer, nullable=True),
        sa.Column("arm_adjustment_interval_months", sa.Integer, nullable=True),
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
        schema="budget",
    )

    # ── Step 3: Create budget.rate_history ───────────────────────────
    op.create_table(
        "rate_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("interest_rate", sa.Numeric(7, 5), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        schema="budget",
    )

    # ── Step 4: Migrate data ────────────────────────────────────────
    # Auto loan params -> loan_params (is_arm=FALSE, ARM fields NULL).
    op.execute("""
        INSERT INTO budget.loan_params (
            account_id, original_principal, current_principal, interest_rate,
            term_months, origination_date, payment_day,
            is_arm, arm_first_adjustment_months, arm_adjustment_interval_months,
            created_at, updated_at
        )
        SELECT
            account_id, original_principal, current_principal, interest_rate,
            term_months, origination_date, payment_day,
            FALSE, NULL, NULL,
            created_at, updated_at
        FROM budget.auto_loan_params
    """)

    # Mortgage params -> loan_params.
    op.execute("""
        INSERT INTO budget.loan_params (
            account_id, original_principal, current_principal, interest_rate,
            term_months, origination_date, payment_day,
            is_arm, arm_first_adjustment_months, arm_adjustment_interval_months,
            created_at, updated_at
        )
        SELECT
            account_id, original_principal, current_principal, interest_rate,
            term_months, origination_date, payment_day,
            is_arm, arm_first_adjustment_months, arm_adjustment_interval_months,
            created_at, updated_at
        FROM budget.mortgage_params
    """)

    # Mortgage rate history -> rate_history.
    op.execute("""
        INSERT INTO budget.rate_history (
            account_id, effective_date, interest_rate, notes, created_at
        )
        SELECT
            account_id, effective_date, interest_rate, notes, created_at
        FROM budget.mortgage_rate_history
    """)

    # ── Step 5: Drop old tables ─────────────────────────────────────
    op.drop_table("auto_loan_params", schema="budget")
    op.drop_table("mortgage_rate_history", schema="budget")
    op.drop_table("mortgage_params", schema="budget")


def downgrade():
    """Revert migration -- recreate old tables and migrate data back."""
    # ── Recreate old tables ─────────────────────────────────────────
    op.create_table(
        "mortgage_params",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("original_principal", sa.Numeric(12, 2), nullable=False),
        sa.Column("current_principal", sa.Numeric(12, 2), nullable=False),
        sa.Column("interest_rate", sa.Numeric(7, 5), nullable=False),
        sa.Column("term_months", sa.Integer, nullable=False),
        sa.Column("origination_date", sa.Date, nullable=False),
        sa.Column(
            "payment_day",
            sa.Integer,
            sa.CheckConstraint(
                "payment_day >= 1 AND payment_day <= 31",
                name="ck_mortgage_payment_day",
            ),
            nullable=False,
        ),
        sa.Column(
            "is_arm",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("arm_first_adjustment_months", sa.Integer, nullable=True),
        sa.Column("arm_adjustment_interval_months", sa.Integer, nullable=True),
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
        schema="budget",
    )

    op.create_table(
        "auto_loan_params",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("original_principal", sa.Numeric(12, 2), nullable=False),
        sa.Column("current_principal", sa.Numeric(12, 2), nullable=False),
        sa.Column("interest_rate", sa.Numeric(7, 5), nullable=False),
        sa.Column("term_months", sa.Integer, nullable=False),
        sa.Column("origination_date", sa.Date, nullable=False),
        sa.Column(
            "payment_day",
            sa.Integer,
            sa.CheckConstraint(
                "payment_day >= 1 AND payment_day <= 31",
                name="ck_auto_loan_payment_day",
            ),
            nullable=False,
        ),
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
        schema="budget",
    )

    op.create_table(
        "mortgage_rate_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("interest_rate", sa.Numeric(7, 5), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        schema="budget",
    )

    # ── Migrate data back ───────────────────────────────────────────
    # Identify mortgage account type ID dynamically.
    op.execute("""
        INSERT INTO budget.mortgage_params (
            account_id, original_principal, current_principal, interest_rate,
            term_months, origination_date, payment_day,
            is_arm, arm_first_adjustment_months, arm_adjustment_interval_months,
            created_at, updated_at
        )
        SELECT
            lp.account_id, lp.original_principal, lp.current_principal,
            lp.interest_rate, lp.term_months, lp.origination_date, lp.payment_day,
            lp.is_arm, lp.arm_first_adjustment_months,
            lp.arm_adjustment_interval_months, lp.created_at, lp.updated_at
        FROM budget.loan_params lp
        JOIN budget.accounts a ON a.id = lp.account_id
        JOIN ref.account_types at ON at.id = a.account_type_id
        WHERE at.name = 'Mortgage'
    """)

    op.execute("""
        INSERT INTO budget.auto_loan_params (
            account_id, original_principal, current_principal, interest_rate,
            term_months, origination_date, payment_day,
            created_at, updated_at
        )
        SELECT
            lp.account_id, lp.original_principal, lp.current_principal,
            lp.interest_rate, lp.term_months, lp.origination_date, lp.payment_day,
            lp.created_at, lp.updated_at
        FROM budget.loan_params lp
        JOIN budget.accounts a ON a.id = lp.account_id
        JOIN ref.account_types at ON at.id = a.account_type_id
        WHERE at.name = 'Auto Loan'
    """)

    op.execute("""
        INSERT INTO budget.mortgage_rate_history (
            account_id, effective_date, interest_rate, notes, created_at
        )
        SELECT
            account_id, effective_date, interest_rate, notes, created_at
        FROM budget.rate_history
    """)

    # ── Drop new tables ─────────────────────────────────────────────
    op.drop_table("rate_history", schema="budget")
    op.drop_table("loan_params", schema="budget")

    # ── Remove new columns from ref.account_types ───────────────────
    op.drop_column("account_types", "max_term_months", schema="ref")
    op.drop_column("account_types", "icon_class", schema="ref")

    # ── Revert HELOC has_parameters to FALSE ────────────────────────
    account_types = sa.table(
        "account_types",
        sa.column("name", sa.String),
        sa.column("has_parameters", sa.Boolean),
        schema="ref",
    )
    op.execute(
        account_types.update()
        .where(account_types.c.name == "HELOC")
        .values(has_parameters=False)
    )
