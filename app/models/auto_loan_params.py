"""
Shekel Budget App -- Auto Loan Parameters Model (budget schema)

Stores auto loan configuration: principal, rate, term, and payment day.
"""

from app.extensions import db


class AutoLoanParams(db.Model):
    """Auto loan parameters linked one-to-one with an Account."""

    __tablename__ = "auto_loan_params"
    __table_args__ = (
        db.CheckConstraint(
            "payment_day >= 1 AND payment_day <= 31",
            name="ck_auto_loan_payment_day",
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
        backref=db.backref("auto_loan_params", uselist=False, lazy="joined"),
    )

    def __repr__(self):
        return (
            f"<AutoLoanParams account_id={self.account_id} "
            f"rate={self.interest_rate} term={self.term_months}>"
        )
