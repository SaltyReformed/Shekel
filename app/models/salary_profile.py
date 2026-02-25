"""
Shekel Budget App — Salary Profile Model (salary schema)

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
