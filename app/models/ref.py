"""
Shekel Budget App -- Reference Table Models (ref schema)

Lookup / enum tables that are rarely written and frequently joined.
New values are added via INSERT, never via schema migration.
"""

from app.extensions import db


class AccountTypeCategory(db.Model):
    """Account type grouping category (Asset, Liability, Retirement, Investment).

    Groups account types into high-level buckets used for dashboard
    layout ordering and chart axis assignment (assets on left y-axis,
    liabilities on right y-axis).
    """

    __tablename__ = "account_type_categories"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<AccountTypeCategory {self.name}>"


class AccountType(db.Model):
    """Account type reference (Checking, Savings, HYSA, Mortgage, etc.).

    Boolean columns capture behavioural groupings:

        has_parameters    -- This type has a linked *Params table
                             (e.g. HysaParams, MortgageParams) that
                             must be created alongside the account.
        has_amortization  -- This type uses the amortization engine
                             for balance projections instead of the
                             generic balance calculator.
    """

    __tablename__ = "account_types"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), unique=True, nullable=False)
    category_id = db.Column(
        db.Integer,
        db.ForeignKey("ref.account_type_categories.id"),
        nullable=False,
    )
    has_parameters = db.Column(db.Boolean, nullable=False, default=False)
    has_amortization = db.Column(db.Boolean, nullable=False, default=False)

    category = db.relationship("AccountTypeCategory")

    def __repr__(self):
        return f"<AccountType {self.name}>"


class TransactionType(db.Model):
    """Transaction type reference: 'Income', 'Expense'."""

    __tablename__ = "transaction_types"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(10), unique=True, nullable=False)

    def __repr__(self):
        return f"<TransactionType {self.name}>"


class Status(db.Model):
    """Transaction status reference.

    Values: Projected, Paid, Received, Credit, Cancelled, Settled.

    Boolean columns capture logical groupings so that application code
    can branch on a single column instead of comparing against sets of
    status names:

        is_settled          -- The real-world transaction has completed
                               (Paid, Received, Settled).  The balance
                               calculator uses actual_amount for these.
        is_immutable        -- The recurrence engine must not overwrite
                               this transaction (Paid, Received, Credit,
                               Cancelled, Settled).
        excludes_from_balance -- This status contributes zero to the
                               projected checking balance (Credit,
                               Cancelled).
    """

    __tablename__ = "statuses"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(15), unique=True, nullable=False)
    is_settled = db.Column(db.Boolean, nullable=False, default=False)
    is_immutable = db.Column(db.Boolean, nullable=False, default=False)
    excludes_from_balance = db.Column(db.Boolean, nullable=False, default=False)

    def __repr__(self):
        return f"<Status {self.name}>"


class RecurrencePattern(db.Model):
    """Recurrence pattern reference: Every Period, Monthly, Annual, etc."""

    __tablename__ = "recurrence_patterns"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<RecurrencePattern {self.name}>"


class FilingStatus(db.Model):
    """Tax filing status reference (Phase 2, but schema created now)."""

    __tablename__ = "filing_statuses"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(25), unique=True, nullable=False)

    def __repr__(self):
        return f"<FilingStatus {self.name}>"


class DeductionTiming(db.Model):
    """Deduction timing reference: 'pre_tax', 'post_tax' (Phase 2)."""

    __tablename__ = "deduction_timings"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(10), unique=True, nullable=False)

    def __repr__(self):
        return f"<DeductionTiming {self.name}>"


class CalcMethod(db.Model):
    """Calculation method reference: 'flat', 'percentage' (Phase 2)."""

    __tablename__ = "calc_methods"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(12), unique=True, nullable=False)

    def __repr__(self):
        return f"<CalcMethod {self.name}>"


class TaxType(db.Model):
    """Tax type reference: 'flat', 'none', 'bracket' (Phase 2)."""

    __tablename__ = "tax_types"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(10), unique=True, nullable=False)

    def __repr__(self):
        return f"<TaxType {self.name}>"


class RaiseType(db.Model):
    """Raise type reference: 'merit', 'cola', 'custom' (Phase 2)."""

    __tablename__ = "raise_types"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(10), unique=True, nullable=False)

    def __repr__(self):
        return f"<RaiseType {self.name}>"
