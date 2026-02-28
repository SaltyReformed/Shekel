"""
Shekel Budget App — Paycheck Deduction Model (salary schema)

Defines payroll deductions (pre-tax and post-tax) that reduce a salary
profile's gross pay to arrive at net pay.
"""

from app.extensions import db


class PaycheckDeduction(db.Model):
    """A payroll deduction (e.g., 401k, health insurance, Roth IRA)."""

    __tablename__ = "paycheck_deductions"
    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_paycheck_deductions_positive_amount"),
        db.CheckConstraint("deductions_per_year > 0", name="ck_paycheck_deductions_positive_per_year"),
        db.CheckConstraint(
            "annual_cap IS NULL OR annual_cap > 0",
            name="ck_paycheck_deductions_positive_cap",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    salary_profile_id = db.Column(
        db.Integer,
        db.ForeignKey("salary.salary_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    deduction_timing_id = db.Column(
        db.Integer, db.ForeignKey("ref.deduction_timings.id"), nullable=False
    )
    calc_method_id = db.Column(
        db.Integer, db.ForeignKey("ref.calc_methods.id"), nullable=False
    )
    name = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Numeric(12, 4), nullable=False)
    deductions_per_year = db.Column(db.Integer, default=26, nullable=False)
    annual_cap = db.Column(db.Numeric(12, 2))
    inflation_enabled = db.Column(db.Boolean, default=False)
    inflation_rate = db.Column(db.Numeric(5, 4))
    inflation_effective_month = db.Column(db.Integer)
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    # Relationships
    salary_profile = db.relationship("SalaryProfile", back_populates="deductions")
    deduction_timing = db.relationship("DeductionTiming", lazy="joined")
    calc_method = db.relationship("CalcMethod", lazy="joined")

    def __repr__(self):
        return f"<PaycheckDeduction '{self.name}' ${self.amount}>"
