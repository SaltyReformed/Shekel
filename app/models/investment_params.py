"""
Shekel Budget App -- Investment Parameters Model (budget schema)

Stores type-specific parameters for investment and retirement accounts
(401k, Roth 401k, Traditional IRA, Roth IRA, brokerage).
"""

from app.extensions import db


class InvestmentParams(db.Model):
    """Parameters for an investment or retirement account."""

    __tablename__ = "investment_params"
    __table_args__ = (
        db.CheckConstraint(
            "employer_contribution_type IN ('none', 'flat_percentage', 'match')",
            name="ck_investment_params_employer_type",
        ),
        db.CheckConstraint(
            "assumed_annual_return >= -1 AND assumed_annual_return <= 1",
            name="ck_investment_params_valid_return",
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
    assumed_annual_return = db.Column(
        db.Numeric(7, 5), nullable=False, default=0.07000
    )
    annual_contribution_limit = db.Column(db.Numeric(12, 2), nullable=True)
    contribution_limit_year = db.Column(db.Integer, nullable=True)
    employer_contribution_type = db.Column(
        db.String(20), nullable=False, default="none"
    )
    employer_flat_percentage = db.Column(db.Numeric(5, 4), nullable=True)
    employer_match_percentage = db.Column(db.Numeric(5, 4), nullable=True)
    employer_match_cap_percentage = db.Column(db.Numeric(5, 4), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=db.func.now()
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    # Relationships
    account = db.relationship("Account", lazy="joined")

    def __repr__(self):
        return f"<InvestmentParams account_id={self.account_id} return={self.assumed_annual_return}>"
