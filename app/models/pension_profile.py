"""
Shekel Budget App -- Pension Profile Model (salary schema)

Models a defined-benefit pension plan linked to a salary profile.
Used for retirement income gap analysis.
"""

from app.extensions import db
from app.models.mixins import TimestampMixin


class PensionProfile(TimestampMixin, db.Model):
    """A defined-benefit pension plan linked to a salary profile.

    Duplicate prevention (F-105 / C-22): the composite unique
    constraint ``uq_pension_profiles_user_name`` on
    ``(user_id, name)`` rejects a second pension profile with the
    same name for the same user.  Without it a double-submit of the
    pension form -- network retry, double-click, browser back-and-
    resubmit -- creates two rows with identical names; the
    retirement dashboard then displays the same plan twice and the
    gap-analysis service double-counts the projected benefit,
    overstating retirement income by the pension amount.  Each
    pension plan has exactly one canonical name per user, so the
    constraint matches the domain model: a name change is
    expressed by editing the existing row rather than creating a
    duplicate.
    """

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
        db.UniqueConstraint(
            "user_id", "name",
            name="uq_pension_profiles_user_name",
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

    # Relationships
    salary_profile = db.relationship("SalaryProfile", lazy="joined")

    def __repr__(self):
        return f"<PensionProfile '{self.name}' multiplier={self.benefit_multiplier}>"
