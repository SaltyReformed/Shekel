"""
Shekel Budget App -- Reference Table Models (ref schema)

Lookup / enum tables that are rarely written and frequently joined.
New values are added via INSERT, never via schema migration.
"""

from sqlalchemy import text

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
                             (e.g. HysaParams, LoanParams) that
                             must be created alongside the account.
        has_amortization  -- This type uses the amortization engine
                             for balance projections instead of the
                             generic balance calculator.
        has_interest      -- This type uses the interest projection
                             engine (InterestParams: APY, compounding).
                             Applies to Asset-category types like
                             HYSA, Money Market, CD, HSA.
        is_pretax         -- Contributions to this type are pre-tax
                             (relevant for retirement gap analysis).
                             Applies to Retirement-category types
                             like 401(k), Traditional IRA.
        is_liquid         -- This type holds liquid funds that count
                             toward emergency fund calculations and
                             savings goal eligibility.  Applies to
                             Asset-category types like Checking,
                             Savings, HYSA, Money Market.
        has_appreciation  -- This type's balance is a market value the
                             user sets that appreciates (or depreciates)
                             over time at a configured annual rate
                             (AssetAppreciationParams), projected by the
                             growth engine with contributions zeroed.
                             Checked before has_parameters in
                             ``classify_account`` so a parameterised
                             physical asset (Property) is not mistaken
                             for an investment.  Applies to Asset-category
                             physical assets like Property.

    Display / validation metadata:

        icon_class        -- Bootstrap icon class for UI rendering
                             (e.g. 'bi-house' for Mortgage).
        max_term_months   -- Maximum loan term in months for
                             type-specific validation.  NULL means
                             no type-specific limit.

    Multi-tenant ownership (commit C-28 / F-044):

        user_id           -- Owning user (nullable).  ``NULL`` denotes
                             a seeded built-in type managed by
                             ``scripts/seed_ref_tables.py`` and is
                             read-only to every owner.  A non-NULL
                             value means the row was created by that
                             user via the ``/accounts/types`` route;
                             only that owner may rename or delete it.
                             ``ondelete='RESTRICT'`` -- deleting a
                             user with custom types refuses the user
                             delete until those rows are pruned, so
                             we never orphan ``budget.accounts`` rows
                             whose ``account_type_id`` would dangle.

    Uniqueness invariant.  The legacy ``UNIQUE(name)`` constraint
    becomes incompatible with per-user copies of seed names ("Owner A
    can call her custom type 'HYSA' even when a built-in 'HYSA' exists";
    see C-28 acceptance criteria).  It is replaced by two partial
    unique indexes evaluated together:

      ``uq_account_types_seeded_name``   -- ``(name) WHERE user_id IS NULL``,
          guaranteeing one built-in per name (preserves the ref_cache
          enum-to-id contract that maps each ``AcctTypeEnum`` member
          to a single seeded row).
      ``uq_account_types_user_name``     -- ``(user_id, name) WHERE user_id IS NOT NULL``,
          guaranteeing each owner has at most one custom type per
          name.  A user-owned row may share a name with the seeded
          built-in (the WHERE clauses keep the two index domains
          disjoint), and two different owners may both have a custom
          "Crypto" without conflict.
    """

    __tablename__ = "account_types"
    __table_args__ = (
        db.Index(
            "uq_account_types_seeded_name",
            "name",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
        db.Index(
            "uq_account_types_user_name",
            "user_id", "name",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        db.Index("ix_account_types_user_id", "user_id"),
        {"schema": "ref"},
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), nullable=False)
    # F-073 / C-43: ondelete=RESTRICT closes the audit gap where the
    # nine ref-table FKs default to PostgreSQL's implicit NO ACTION.
    # RESTRICT raises immediately on the offending statement (vs. NO
    # ACTION which defers to commit), giving a clean error message at
    # the point of the violating DELETE; the name follows the
    # SHEKEL_NAMING_CONVENTION ("fk": "fk_<table>_<column_0_name>")
    # so the model rendering and the live-DB rendering converge on
    # the same string.
    category_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.account_type_categories.id",
            name="fk_account_types_category_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    has_parameters = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    has_amortization = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    has_interest = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    is_pretax = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    is_liquid = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    has_appreciation = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    icon_class = db.Column(db.String(30), nullable=True)
    max_term_months = db.Column(db.Integer, nullable=True)
    # NULL -> seeded built-in row, read-only to every owner.  Non-NULL
    # -> owned by that user; only they may rename or delete it.  See
    # the class docstring for the per-user copy contract and the
    # paired partial unique indexes that enforce it.
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("auth.users.id", ondelete="RESTRICT"),
        nullable=True,
    )

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
    is_settled = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    is_immutable = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    excludes_from_balance = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )

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


class GoalMode(db.Model):
    """Savings goal amount mode reference: 'Fixed', 'Income-Relative'.

    Determines whether a savings goal target is a fixed dollar amount
    or a multiple of the user's income (e.g. 3 months of paychecks).
    """

    __tablename__ = "goal_modes"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<GoalMode {self.name}>"


class IncomeUnit(db.Model):
    """Income multiplier unit reference: 'Paychecks', 'Months'.

    Used with income-relative savings goals to specify whether the
    multiplier is measured in paychecks or calendar months.
    """

    __tablename__ = "income_units"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<IncomeUnit {self.name}>"


class LoanAnchorSource(db.Model):
    """Loan anchor event source reference: 'origination', 'user_trueup'.

    Tags every row in :class:`budget.loan_anchor_events` with the
    provenance of that anchor.  ``origination`` is materialised once
    per loan from the immutable ``LoanParams.origination_date`` /
    ``LoanParams.original_principal`` fields; ``user_trueup`` is
    appended by the loan dashboard's balance edit flow whenever the
    operator asserts a new dated balance (commit C-16 / decision D-C).

    Application code resolves these via ``ref_cache.loan_anchor_source_id``
    and compares against the integer ID -- never the string name --
    matching the project-wide ``ref-table: IDs for logic, strings for
    display only`` invariant.
    """

    __tablename__ = "loan_anchor_sources"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<LoanAnchorSource {self.name}>"


class UserRole(db.Model):
    """User role reference: 'owner', 'companion'.

    Determines route access and data visibility scope.
    Owner accounts have full access.  Companion accounts
    see only transactions from companion-visible templates
    belonging to their linked owner.
    """

    __tablename__ = "user_roles"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<UserRole {self.name}>"


class EmployerContributionType(db.Model):
    """Employer-contribution type reference: 'none', 'flat_percentage', 'match'.

    Selects the employer retirement-contribution formula the growth
    engine applies to an investment/retirement account.  Application
    code resolves these via ``ref_cache.employer_contribution_type_id``
    and branches on the integer ID -- never the string name --
    matching the project-wide ``ref-table: IDs for logic, strings for
    display only`` invariant (#38; replaced the prior free-string
    ``investment_params.employer_contribution_type`` column).
    """

    __tablename__ = "employer_contribution_types"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<EmployerContributionType {self.name}>"


class CompoundingFrequency(db.Model):
    """Interest compounding frequency reference: 'daily', 'monthly', 'quarterly'.

    Selects the per-period compounding formula the interest projection
    engine applies to an interest-bearing account.  Application code
    resolves these via ``ref_cache.compounding_frequency_id`` and
    branches on the integer ID -- never the string name -- matching
    the project-wide ``ref-table: IDs for logic, strings for display
    only`` invariant (#38; replaced the prior free-string
    ``interest_params.compounding_frequency`` column).
    """

    __tablename__ = "compounding_frequencies"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(12), unique=True, nullable=False)

    def __repr__(self):
        return f"<CompoundingFrequency {self.name}>"
