"""
Shekel Budget App -- Salary Profile Model (salary schema)

A salary profile defines an income source with annual salary, filing status,
state tax config, and links to raises and deductions for paycheck calculation.
"""

from app.extensions import db


class SalaryProfile(db.Model):
    """A salary income profile used for net paycheck calculation."""

    __tablename__ = "salary_profiles"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "scenario_id", "name",
            name="uq_salary_profiles_user_scenario_name",
        ),
        db.CheckConstraint("annual_salary > 0", name="ck_salary_profiles_positive_salary"),
        db.CheckConstraint("pay_periods_per_year > 0", name="ck_salary_profiles_positive_periods"),
        db.CheckConstraint("qualifying_children >= 0", name="ck_salary_profiles_nonneg_children"),
        db.CheckConstraint("other_dependents >= 0", name="ck_salary_profiles_nonneg_dependents"),
        db.CheckConstraint("additional_income >= 0", name="ck_salary_profiles_nonneg_add_income"),
        db.CheckConstraint("additional_deductions >= 0", name="ck_salary_profiles_nonneg_add_deductions"),
        db.CheckConstraint("extra_withholding >= 0", name="ck_salary_profiles_nonneg_extra_withholding"),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    scenario_id = db.Column(
        db.Integer, db.ForeignKey("budget.scenarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    template_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transaction_templates.id", ondelete="SET NULL"),
    )
    filing_status_id = db.Column(
        db.Integer, db.ForeignKey("ref.filing_statuses.id"), nullable=False
    )
    name = db.Column(db.String(200), nullable=False)
    annual_salary = db.Column(db.Numeric(12, 2), nullable=False)
    state_code = db.Column(db.String(2), nullable=False, default="NC")
    pay_periods_per_year = db.Column(db.Integer, default=26, nullable=False)

    # W-4 fields (IRS Pub 15-T Percentage Method inputs)
    qualifying_children = db.Column(db.Integer, default=0, nullable=False)
    other_dependents = db.Column(db.Integer, default=0, nullable=False)
    additional_income = db.Column(
        db.Numeric(12, 2), default=0, nullable=False
    )  # W-4 Step 4(a): other income
    additional_deductions = db.Column(
        db.Numeric(12, 2), default=0, nullable=False
    )  # W-4 Step 4(b): extra deductions
    extra_withholding = db.Column(
        db.Numeric(12, 2), default=0, nullable=False
    )  # W-4 Step 4(c): extra withholding per period

    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    # Relationships
    scenario = db.relationship("Scenario", lazy="joined")
    template = db.relationship("TransactionTemplate", lazy="joined")
    filing_status = db.relationship("FilingStatus", lazy="joined")
    raises = db.relationship(
        "SalaryRaise", back_populates="salary_profile",
        cascade="all, delete-orphan", lazy="select",
        order_by="SalaryRaise.effective_year, SalaryRaise.effective_month",
    )
    deductions = db.relationship(
        "PaycheckDeduction", back_populates="salary_profile",
        cascade="all, delete-orphan", lazy="select",
        order_by="PaycheckDeduction.sort_order",
    )

    def __repr__(self):
        return f"<SalaryProfile '{self.name}' ${self.annual_salary}>"
