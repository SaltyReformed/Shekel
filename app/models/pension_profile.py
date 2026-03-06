"""
Shekel Budget App — Pension Profile Model (salary schema)

Models a defined-benefit pension plan linked to a salary profile.
Used for retirement income gap analysis.
"""

from app.extensions import db


class PensionProfile(db.Model):
    """A defined-benefit pension plan linked to a salary profile."""

    __tablename__ = "pension_profiles"
    __table_args__ = (
        db.CheckConstraint(
            "benefit_multiplier > 0",
            name="ck_pension_profiles_positive_multiplier",
        ),
        db.CheckConstraint(
            "consecutive_high_years > 0",
            name="ck_pension_profiles_positive_high_years",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    salary_profile_id = db.Column(
        db.Integer,
        db.ForeignKey("salary.salary_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    name = db.Column(db.String(100), nullable=False, default="Pension")
    benefit_multiplier = db.Column(db.Numeric(7, 5), nullable=False)
    consecutive_high_years = db.Column(db.Integer, nullable=False, default=4)
    hire_date = db.Column(db.Date, nullable=False)
    earliest_retirement_date = db.Column(db.Date, nullable=True)
    planned_retirement_date = db.Column(db.Date, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=db.func.now()
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    # Relationships
    salary_profile = db.relationship("SalaryProfile", lazy="joined")

    def __repr__(self):
        return f"<PensionProfile '{self.name}' multiplier={self.benefit_multiplier}>"
