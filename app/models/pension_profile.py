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
        # F-140 / C-42: FK-column indexes.  Both ``user_id`` and
        # ``salary_profile_id`` are filtered by the retirement
        # dashboard and the pension gap-analysis service on every
        # request; without dedicated indexes the queries fall back to
        # sequential scans that scale with the global pension-profile
        # count.  Two single-column indexes (not one composite) so
        # the planner can use either independently -- queries on
        # user_id alone (the dashboard listing) and queries on
        # salary_profile_id alone (the gap-analysis join) appear in
        # equal measure.
        db.Index(
            "idx_pension_profiles_user", "user_id",
        ),
        db.Index(
            "idx_pension_profiles_salary_profile", "salary_profile_id",
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
    name = db.Column(
        db.String(100), nullable=False, default="Pension",
        server_default=db.text("'Pension'"),
    )
    benefit_multiplier = db.Column(db.Numeric(7, 5), nullable=False)
    consecutive_high_years = db.Column(
        db.Integer, nullable=False, default=4, server_default=db.text("4"),
    )
    hire_date = db.Column(db.Date, nullable=False)
    earliest_retirement_date = db.Column(db.Date, nullable=True)
    planned_retirement_date = db.Column(db.Date, nullable=True)
    is_active = db.Column(
        db.Boolean, nullable=False, default=True,
        server_default=db.text("true"),
    )

    # Relationships
    salary_profile = db.relationship("SalaryProfile", lazy="joined")

    def __repr__(self):
        return f"<PensionProfile '{self.name}' multiplier={self.benefit_multiplier}>"
