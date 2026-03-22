"""
Shekel Budget App -- Mortgage Parameters Models (budget schema)

Stores mortgage-specific configuration: loan terms, ARM settings,
rate change history, and escrow components.
"""

from app.extensions import db


class MortgageParams(db.Model):
    """Mortgage-specific parameters linked one-to-one with an Account."""

    __tablename__ = "mortgage_params"
    __table_args__ = (
        db.CheckConstraint(
            "payment_day >= 1 AND payment_day <= 31",
            name="ck_mortgage_payment_day",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    original_principal = db.Column(db.Numeric(12, 2), nullable=False)
    current_principal = db.Column(db.Numeric(12, 2), nullable=False)
    interest_rate = db.Column(db.Numeric(7, 5), nullable=False)
    term_months = db.Column(db.Integer, nullable=False)
    origination_date = db.Column(db.Date, nullable=False)
    payment_day = db.Column(db.Integer, nullable=False)
    is_arm = db.Column(db.Boolean, nullable=False, server_default=db.text("false"))
    arm_first_adjustment_months = db.Column(db.Integer, nullable=True)
    arm_adjustment_interval_months = db.Column(db.Integer, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=db.func.now()
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    # Relationships
    account = db.relationship(
        "Account",
        backref=db.backref("mortgage_params", uselist=False, lazy="joined"),
    )

    def __repr__(self):
        return (
            f"<MortgageParams account_id={self.account_id} "
            f"rate={self.interest_rate} term={self.term_months}>"
        )


class MortgageRateHistory(db.Model):
    """Historical record of ARM rate changes for a mortgage account."""

    __tablename__ = "mortgage_rate_history"
    __table_args__ = {"schema": "budget"}

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    effective_date = db.Column(db.Date, nullable=False)
    interest_rate = db.Column(db.Numeric(7, 5), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=db.func.now()
    )

    # Relationships
    account = db.relationship(
        "Account",
        backref=db.backref("rate_history", lazy="select"),
    )

    def __repr__(self):
        return (
            f"<MortgageRateHistory account_id={self.account_id} "
            f"date={self.effective_date} rate={self.interest_rate}>"
        )


class EscrowComponent(db.Model):
    """An escrow line item (property tax, insurance, etc.) for a mortgage."""

    __tablename__ = "escrow_components"
    __table_args__ = (
        db.UniqueConstraint(
            "account_id", "name", name="uq_escrow_account_name"
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = db.Column(db.String(100), nullable=False)
    annual_amount = db.Column(db.Numeric(12, 2), nullable=False)
    inflation_rate = db.Column(db.Numeric(5, 4), nullable=True)
    is_active = db.Column(db.Boolean, server_default=db.text("true"))
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=db.func.now()
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    # Relationships
    account = db.relationship(
        "Account",
        backref=db.backref("escrow_components", lazy="select"),
    )

    def __repr__(self):
        return (
            f"<EscrowComponent account_id={self.account_id} "
            f"name={self.name!r} annual={self.annual_amount}>"
        )
