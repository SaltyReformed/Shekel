"""
Shekel Budget App -- Loan Parameters Model (budget schema)

Stores loan configuration for all installment loan types: principal,
rate, term, payment day, and optional ARM fields.  One row per
amortizing account, linked one-to-one via account_id.
"""

from app.extensions import db


class LoanParams(db.Model):
    """Loan parameters linked one-to-one with an Account.

    Serves the amortization engine for all installment loan types
    (mortgage, auto loan, student loan, personal loan, HELOC, etc.).
    ARM-specific columns are nullable and cost nothing when unused.
    """

    __tablename__ = "loan_params"
    __table_args__ = (
        db.CheckConstraint(
            "payment_day >= 1 AND payment_day <= 31",
            name="ck_loan_params_payment_day",
        ),
        db.CheckConstraint(
            "original_principal > 0",
            name="ck_loan_params_orig_principal",
        ),
        db.CheckConstraint(
            "current_principal >= 0",
            name="ck_loan_params_curr_principal",
        ),
        db.CheckConstraint(
            "interest_rate >= 0",
            name="ck_loan_params_interest_rate",
        ),
        db.CheckConstraint(
            "term_months > 0",
            name="ck_loan_params_term_months",
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
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now(),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    # Relationships
    account = db.relationship(
        "Account",
        backref=db.backref("loan_params", uselist=False, lazy="joined"),
    )

    def __repr__(self):
        return (
            f"<LoanParams account_id={self.account_id} "
            f"rate={self.interest_rate} term={self.term_months}>"
        )
