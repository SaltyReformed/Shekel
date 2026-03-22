"""
Shekel Budget App -- Salary Raise Model (salary schema)

Tracks scheduled salary raises (merit, COLA, custom) that apply at
a specific month/year to adjust the annual salary for paycheck calculation.
"""

from app.extensions import db


class SalaryRaise(db.Model):
    """A scheduled salary raise event."""

    __tablename__ = "salary_raises"
    __table_args__ = (
        db.CheckConstraint(
            "(percentage IS NOT NULL AND flat_amount IS NULL) OR "
            "(percentage IS NULL AND flat_amount IS NOT NULL)",
            name="ck_salary_raises_one_method",
        ),
        db.CheckConstraint(
            "effective_month >= 1 AND effective_month <= 12",
            name="ck_salary_raises_valid_month",
        ),
        db.CheckConstraint("percentage IS NULL OR percentage > 0", name="ck_salary_raises_positive_pct"),
        db.CheckConstraint("flat_amount IS NULL OR flat_amount > 0", name="ck_salary_raises_positive_flat"),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    salary_profile_id = db.Column(
        db.Integer,
        db.ForeignKey("salary.salary_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    raise_type_id = db.Column(
        db.Integer, db.ForeignKey("ref.raise_types.id"), nullable=False
    )
    effective_month = db.Column(db.Integer, nullable=False)
    effective_year = db.Column(db.Integer, nullable=True)
    percentage = db.Column(db.Numeric(5, 4))
    flat_amount = db.Column(db.Numeric(12, 2))
    is_recurring = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())

    # Relationships
    salary_profile = db.relationship("SalaryProfile", back_populates="raises")
    raise_type = db.relationship("RaiseType", lazy="joined")

    def __repr__(self):
        amt = f"{self.percentage}%" if self.percentage else f"${self.flat_amount}"
        return f"<SalaryRaise {amt} effective {self.effective_month}/{self.effective_year}>"
