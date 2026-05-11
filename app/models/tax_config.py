"""
Shekel Budget App -- Tax Configuration Models (salary schema)

Models for federal tax brackets, state tax config, and FICA rates
used by the paycheck calculator to compute tax withholdings.
"""

from app.extensions import db
from app.models.mixins import CreatedAtMixin


class TaxBracketSet(CreatedAtMixin, db.Model):
    """A set of federal income tax brackets for a specific year and filing status."""

    __tablename__ = "tax_bracket_sets"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "tax_year", "filing_status_id",
            name="uq_tax_bracket_sets_user_year_status",
        ),
        db.CheckConstraint("standard_deduction >= 0", name="ck_tax_bracket_sets_nonneg_deduction"),
        db.CheckConstraint("child_credit_amount >= 0", name="ck_tax_bracket_sets_nonneg_child_credit"),
        db.CheckConstraint("other_dependent_credit_amount >= 0", name="ck_tax_bracket_sets_nonneg_other_credit"),
        # F-077 / C-24: ``tax_year`` is the IRS tax year a bracket
        # set applies to.  The schema layer added the same Range in
        # commit C-24; the CHECK is the storage-tier counterpart.
        db.CheckConstraint(
            "tax_year >= 2000 AND tax_year <= 2100",
            name="ck_tax_bracket_sets_valid_tax_year",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    filing_status_id = db.Column(
        db.Integer, db.ForeignKey("ref.filing_statuses.id"), nullable=False
    )
    tax_year = db.Column(db.Integer, nullable=False)
    standard_deduction = db.Column(db.Numeric(12, 2), nullable=False)
    # server_default uses the bare string "0" (not db.text("0")) so
    # pg_dump renders the default as 'DEFAULT '0'::numeric' -- matching
    # the form materialised by migration b4c7d8e9f012's
    # ``server_default='0'``.  db.text("0") would render as
    # ``DEFAULT 0`` (literal), functionally identical but a pg_dump
    # diff against the migration-built schema.
    child_credit_amount = db.Column(
        db.Numeric(12, 2), nullable=False, default=0,
        server_default="0",
    )  # Per qualifying child under 17
    other_dependent_credit_amount = db.Column(
        db.Numeric(12, 2), nullable=False, default=0,
        server_default="0",
    )  # Per other dependent
    description = db.Column(db.String(200))

    # Relationships
    filing_status = db.relationship("FilingStatus", lazy="joined")
    brackets = db.relationship(
        "TaxBracket", back_populates="bracket_set",
        cascade="all, delete-orphan", lazy="select",
        order_by="TaxBracket.sort_order",
    )

    def __repr__(self):
        return f"<TaxBracketSet year={self.tax_year} status_id={self.filing_status_id}>"


class TaxBracket(db.Model):
    """A single tax bracket within a bracket set."""

    __tablename__ = "tax_brackets"
    __table_args__ = (
        db.CheckConstraint("min_income >= 0", name="ck_tax_brackets_nonneg_min"),
        db.CheckConstraint(
            "max_income IS NULL OR max_income >= min_income",
            name="ck_tax_brackets_income_order",
        ),
        db.CheckConstraint("rate >= 0 AND rate <= 1", name="ck_tax_brackets_valid_rate"),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    bracket_set_id = db.Column(
        db.Integer,
        db.ForeignKey("salary.tax_bracket_sets.id", ondelete="CASCADE"),
        nullable=False,
    )
    min_income = db.Column(db.Numeric(12, 2), nullable=False)
    max_income = db.Column(db.Numeric(12, 2))
    rate = db.Column(db.Numeric(5, 4), nullable=False)
    sort_order = db.Column(
        db.Integer, nullable=False, default=0, server_default=db.text("0"),
    )

    # Relationships
    bracket_set = db.relationship("TaxBracketSet", back_populates="brackets")

    def __repr__(self):
        return f"<TaxBracket {self.rate} ({self.min_income}-{self.max_income})>"


class StateTaxConfig(CreatedAtMixin, db.Model):
    """State-level tax configuration (flat rate or none), per year."""

    __tablename__ = "state_tax_configs"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "state_code", "tax_year",
            name="uq_state_tax_configs_user_state_year",
        ),
        db.CheckConstraint(
            "flat_rate IS NULL OR (flat_rate >= 0 AND flat_rate <= 1)",
            name="ck_state_tax_configs_valid_rate",
        ),
        # F-077 / C-24: ``standard_deduction`` is nullable (NULL =
        # state has no standard deduction); when present, must be
        # non-negative.
        db.CheckConstraint(
            "standard_deduction IS NULL OR standard_deduction >= 0",
            name="ck_state_tax_configs_nonneg_standard_deduction",
        ),
        # F-077 / C-24: tax_year sweep paired with the tax_bracket_sets
        # equivalent.
        db.CheckConstraint(
            "tax_year >= 2000 AND tax_year <= 2100",
            name="ck_state_tax_configs_valid_tax_year",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    tax_type_id = db.Column(
        db.Integer, db.ForeignKey("ref.tax_types.id"), nullable=False
    )
    state_code = db.Column(db.String(2), nullable=False)
    tax_year = db.Column(db.Integer, nullable=False)
    flat_rate = db.Column(db.Numeric(5, 4))
    standard_deduction = db.Column(db.Numeric(12, 2))

    # Relationships
    tax_type = db.relationship("TaxType", lazy="joined")

    def __repr__(self):
        return f"<StateTaxConfig {self.state_code} rate={self.flat_rate}>"


class FicaConfig(CreatedAtMixin, db.Model):
    """FICA (Social Security + Medicare) tax configuration per year."""

    __tablename__ = "fica_configs"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "tax_year",
            name="uq_fica_configs_user_year",
        ),
        db.CheckConstraint("ss_rate >= 0 AND ss_rate <= 1", name="ck_fica_configs_valid_ss_rate"),
        db.CheckConstraint("ss_wage_base > 0", name="ck_fica_configs_positive_wage_base"),
        db.CheckConstraint("medicare_rate >= 0 AND medicare_rate <= 1", name="ck_fica_configs_valid_medicare_rate"),
        db.CheckConstraint(
            "medicare_surtax_rate >= 0 AND medicare_surtax_rate <= 1",
            name="ck_fica_configs_valid_surtax_rate",
        ),
        db.CheckConstraint("medicare_surtax_threshold > 0", name="ck_fica_configs_positive_surtax_threshold"),
        # F-077 / C-24: tax_year sweep paired with the tax_bracket_sets
        # and state_tax_configs equivalents.
        db.CheckConstraint(
            "tax_year >= 2000 AND tax_year <= 2100",
            name="ck_fica_configs_valid_tax_year",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    tax_year = db.Column(db.Integer, nullable=False)
    ss_rate = db.Column(
        db.Numeric(5, 4), nullable=False, default=0.0620,
        server_default=db.text("0.0620"),
    )
    ss_wage_base = db.Column(
        db.Numeric(12, 2), nullable=False, default=176100,
        server_default=db.text("176100"),
    )
    medicare_rate = db.Column(
        db.Numeric(5, 4), nullable=False, default=0.0145,
        server_default=db.text("0.0145"),
    )
    medicare_surtax_rate = db.Column(
        db.Numeric(5, 4), nullable=False, default=0.0090,
        server_default=db.text("0.0090"),
    )
    medicare_surtax_threshold = db.Column(
        db.Numeric(12, 2), nullable=False, default=200000,
        server_default=db.text("200000"),
    )

    def __repr__(self):
        return f"<FicaConfig year={self.tax_year}>"
