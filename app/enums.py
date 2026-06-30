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
    PROPERTY = "Property"


class DeductionTimingEnum(enum.Enum):
    """Deduction timing values.

    Values match ``ref.deduction_timings.name`` in the database.
    """

    PRE_TAX = "pre_tax"
    POST_TAX = "post_tax"


class CalcMethodEnum(enum.Enum):
    """Calculation method values.

    Values match ``ref.calc_methods.name`` in the database.
    """

    FLAT = "flat"
    PERCENTAGE = "percentage"


class TaxTypeEnum(enum.Enum):
    """Tax type values.

    Values match ``ref.tax_types.name`` in the database.
    """

    FLAT = "flat"
    NONE = "none"
    BRACKET = "bracket"


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


class GoalModeEnum(enum.Enum):
    """Savings goal amount mode values.

    Values match ``ref.goal_modes.name`` in the database.
    """

    FIXED = "Fixed"
    INCOME_RELATIVE = "Income-Relative"


class IncomeUnitEnum(enum.Enum):
    """Income multiplier unit values.

    Values match ``ref.income_units.name`` in the database.
    """

    PAYCHECKS = "Paychecks"
    MONTHS = "Months"


class RoleEnum(enum.Enum):
    """User role values.

    Values match ``ref.user_roles.name`` in the database.
    """

    OWNER = "owner"
    COMPANION = "companion"


class LoanAnchorSourceEnum(enum.Enum):
    """Loan anchor event source values (CRIT-02 / E-18).

    Distinguishes the origination event that every loan carries from
    user-initiated balance true-ups appended through the dashboard
    edit flow.  Values match ``ref.loan_anchor_sources.name``.
    """

    ORIGINATION = "origination"
    USER_TRUEUP = "user_trueup"


class EmployerContributionTypeEnum(enum.Enum):
    """Employer retirement-contribution type values (#38).

    Selects the employer-contribution formula the growth engine
    applies to an investment/retirement account: no employer
    contribution, a flat percentage of gross pay, or a match of the
    employee's contribution up to a cap.  Values match
    ``ref.employer_contribution_types.name``.
    """

    NONE = "none"
    FLAT_PERCENTAGE = "flat_percentage"
    MATCH = "match"


class CompoundingFrequencyEnum(enum.Enum):
    """Interest compounding frequency values (#38).

    Selects the per-period compounding formula the interest
    projection engine applies to an interest-bearing account.  Values
    match ``ref.compounding_frequencies.name``.
    """

    DAILY = "daily"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


class LedgerAccountClassEnum(enum.Enum):
    """Ledger account class values for the double-entry posting ledger.

    The five fundamental accounting classes (Build-Order Step 2).  Values
    match ``ref.ledger_account_classes.name``.  Asset and Expense are
    debit-normal; Liability, Income, and Equity are credit-normal -- the
    natural-balance side is stored as the ``is_debit_normal`` boolean on
    each row and read via ``ref_cache.ledger_class_is_debit_normal``,
    never inferred from these member names.
    """

    ASSET = "Asset"
    LIABILITY = "Liability"
    INCOME = "Income"
    EXPENSE = "Expense"
    EQUITY = "Equity"


class PostingKindEnum(enum.Enum):
    """Posting-leg kind values for ``budget.account_postings``.

    ``transfer`` is a transfer's two balanced legs (Build-Order Step 2);
    ``income`` / ``expense`` are an ordinary settled transaction's cash and
    category legs (Build-Order Step 3); ``principal`` / ``interest`` /
    ``escrow`` / ``refund`` are the four legs of a confirmed loan payment's
    real-split correction (Build-Order Step 4) -- the loan principal
    adjustment, the accrued interest expense, the configured escrow expense,
    and the payoff-overpayment refund receivable.  Later Build-Order steps
    add further kinds via data migrations.  Values match
    ``ref.posting_kinds.name``.
    """

    TRANSFER = "transfer"
    INCOME = "income"
    EXPENSE = "expense"
    PRINCIPAL = "principal"
    INTEREST = "interest"
    ESCROW = "escrow"
    REFUND = "refund"


class PostingSourceEnum(enum.Enum):
    """Journal-entry source-event values for ``budget.journal_entries``.

    ``transfer`` is a settled transfer (Build-Order Step 2); ``transaction``
    is an ordinary settled cash transaction (Build-Order Step 3);
    ``loan_payment`` is the real-split correction appended to a confirmed
    loan-payment transfer (Build-Order Step 4); later steps add ``paycheck``
    and ``credit_payback`` via data migrations.  Values match
    ``ref.posting_sources.name``.
    """

    TRANSFER = "transfer"
    TRANSACTION = "transaction"
    LOAN_PAYMENT = "loan_payment"


class LedgerAccountKindEnum(enum.Enum):
    """Row-kind discriminator for ``budget.ledger_accounts`` (Build-Order Step 4).

    The explicit, positive discriminator that replaces inferring a ledger
    account's kind from the NULL-pattern of its ``account_id`` /
    ``category_id`` / ``is_fallback`` columns (see
    :class:`app.models.ledger_account.LedgerAccount`).  Every row carries a
    ``kind_id`` FK to one of these values; readers branch on the integer ID,
    never on which FKs happen to be NULL.

    The first four enumerate the kinds Steps 2-3 already create:

        linked    -- one per real ``budget.accounts`` row (Asset/Liability).
        category  -- one per budget category per Income/Expense class.
        fallback  -- the per-(owner, class) Uncategorized bucket.
        orphan    -- a former category row whose category was deleted.

    The last three are the per-loan ledger accounts Step 4's loan-payment
    correction books into:

        loan_interest -- the loan's accrued-interest Expense account.
        loan_escrow   -- the loan's configured-escrow Expense account.
        loan_refund   -- the loan's payoff-overpayment refund Asset account.

    Application code resolves these via ``ref_cache.ledger_account_kind_id``
    and compares against the integer ID -- never the string ``name`` --
    matching the project-wide ``ref-table: IDs for logic, strings for display
    only`` invariant.
    """

    LINKED = "linked"
    CATEGORY = "category"
    FALLBACK = "fallback"
    ORPHAN = "orphan"
    LOAN_INTEREST = "loan_interest"
    LOAN_ESCROW = "loan_escrow"
    LOAN_REFUND = "loan_refund"
