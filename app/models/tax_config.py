"""
Shekel Budget App — Tax Configuration Models (salary schema)

Models for federal tax brackets, state tax config, and FICA rates
used by the paycheck calculator to compute tax withholdings.
"""

from app.extensions import db


class TaxBracketSet(db.Model):
    """A set of federal income tax brackets for a specific year and filing status."""

    __tablename__ = "tax_bracket_sets"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "tax_year", "filing_status_id",
            name="uq_tax_bracket_sets_user_year_status",
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
    child_credit_amount = db.Column(
        db.Numeric(12, 2), nullable=False, default=0
    )  # Per qualifying child under 17
    other_dependent_credit_amount = db.Column(
        db.Numeric(12, 2), nullable=False, default=0
    )  # Per other dependent
    description = db.Column(db.String(200))
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())

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
    __table_args__ = {"schema": "salary"}

    id = db.Column(db.Integer, primary_key=True)
    bracket_set_id = db.Column(
        db.Integer,
        db.ForeignKey("salary.tax_bracket_sets.id", ondelete="CASCADE"),
        nullable=False,
    )
    min_income = db.Column(db.Numeric(12, 2), nullable=False)
    max_income = db.Column(db.Numeric(12, 2))
    rate = db.Column(db.Numeric(5, 4), nullable=False)
    sort_order = db.Column(db.Integer, default=0)

    # Relationships
    bracket_set = db.relationship("TaxBracketSet", back_populates="brackets")

    def __repr__(self):
        return f"<TaxBracket {self.rate} ({self.min_income}–{self.max_income})>"


class StateTaxConfig(db.Model):
    """State-level tax configuration (flat rate or none)."""

    __tablename__ = "state_tax_configs"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "state_code",
            name="uq_state_tax_configs_user_state",
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
    flat_rate = db.Column(db.Numeric(5, 4))
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())

    # Relationships
    tax_type = db.relationship("TaxType", lazy="joined")

    def __repr__(self):
        return f"<StateTaxConfig {self.state_code} rate={self.flat_rate}>"


class FicaConfig(db.Model):
    """FICA (Social Security + Medicare) tax configuration per year."""

    __tablename__ = "fica_configs"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "tax_year",
            name="uq_fica_configs_user_year",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    tax_year = db.Column(db.Integer, nullable=False)
    ss_rate = db.Column(db.Numeric(5, 4), nullable=False, default=0.0620)
    ss_wage_base = db.Column(db.Numeric(12, 2), nullable=False, default=176100)
    medicare_rate = db.Column(db.Numeric(5, 4), nullable=False, default=0.0145)
    medicare_surtax_rate = db.Column(db.Numeric(5, 4), nullable=False, default=0.0090)
    medicare_surtax_threshold = db.Column(
        db.Numeric(12, 2), nullable=False, default=200000
    )
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())

    def __repr__(self):
        return f"<FicaConfig year={self.tax_year}>"
