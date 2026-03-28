"""
Shekel Budget App -- Reference Table Enums

Python Enums whose members correspond 1:1 with rows in the ref schema
lookup tables.  The *value* of each member is the database ``name``
column after all migrations have run.

These enums are the single source of truth for valid reference values.
The ref_cache module maps each member to its database integer ID at
startup, so application code never needs to query by name at runtime.
"""

import enum


class StatusEnum(enum.Enum):
    """Transaction status values.

    Values match ``ref.statuses.name`` after the Commit #1 migration
    renames the display names.
    """

    PROJECTED = "Projected"
    DONE = "Paid"          # Renamed from "done" -- expense has been paid
    RECEIVED = "Received"  # Income has been deposited
    CREDIT = "Credit"      # Paid via credit card, not checking
    CANCELLED = "Cancelled"
    SETTLED = "Settled"    # Archived / fully reconciled


class TxnTypeEnum(enum.Enum):
    """Transaction type values.

    Values match ``ref.transaction_types.name`` after the Commit #2
    migration capitalizes the display names.
    """

    INCOME = "Income"
    EXPENSE = "Expense"


class AcctCategoryEnum(enum.Enum):
    """Account type category values.

    Groups account types into high-level buckets for dashboard layout
    and chart axis assignment.  Values match
    ``ref.account_type_categories.name``.
    """

    ASSET = "Asset"
    LIABILITY = "Liability"
    RETIREMENT = "Retirement"
    INVESTMENT = "Investment"


class AcctTypeEnum(enum.Enum):
    """Account type values.

    Values match ``ref.account_types.name`` after the Commit #2
    migration capitalizes the display names.  Each member maps 1:1
    to a row in the account_types table.
    """

    CHECKING = "Checking"
    SAVINGS = "Savings"
    HYSA = "HYSA"
    MONEY_MARKET = "Money Market"
    CD = "CD"
    HSA = "HSA"
    CREDIT_CARD = "Credit Card"
    MORTGAGE = "Mortgage"
    AUTO_LOAN = "Auto Loan"
    STUDENT_LOAN = "Student Loan"
    PERSONAL_LOAN = "Personal Loan"
    HELOC = "HELOC"
    K401 = "401(k)"
    ROTH_401K = "Roth 401(k)"
    TRADITIONAL_IRA = "Traditional IRA"
    ROTH_IRA = "Roth IRA"
    BROKERAGE = "Brokerage"
    PLAN_529 = "529 Plan"


class RecurrencePatternEnum(enum.Enum):
    """Recurrence pattern values.

    Values match ``ref.recurrence_patterns.name`` after the Commit #2
    migration capitalizes the display names.
    """

    EVERY_PERIOD = "Every Period"
    EVERY_N_PERIODS = "Every N Periods"
    MONTHLY = "Monthly"
    MONTHLY_FIRST = "Monthly First"
    QUARTERLY = "Quarterly"
    SEMI_ANNUAL = "Semi-Annual"
    ANNUAL = "Annual"
    ONCE = "Once"
