"""
Shekel Budget App -- Salary Raise Model (salary schema)

Tracks scheduled salary raises (merit, COLA, custom) that apply at
a specific month/year to adjust the annual salary for paycheck calculation.
"""

from app.extensions import db
from app.models.mixins import CreatedAtMixin


class SalaryRaise(CreatedAtMixin, db.Model):
    """A scheduled salary raise event.

    Optimistic locking: see :class:`Transaction` for the
    ``version_id_col`` contract.  Concurrent raise edits race for
    the bump; the loser raises ``StaleDataError`` and the route
    surfaces a flash + redirect.  See commit C-18 of the 2026-04-15
    security remediation plan.

    Duplicate prevention (F-051 / C-23): the composite unique
    constraint ``uq_salary_raises_profile_type_year_month`` on
    ``(salary_profile_id, raise_type_id, effective_year,
    effective_month)`` rejects a second raise with the same shape
    on the same salary profile.  Without it a double-submit of the
    raise form -- network retry, double-click, browser back-and-
    resubmit -- creates two rows with identical effective dates;
    the paycheck calculator then applies the raise twice
    (``salary * 1.03 * 1.03`` instead of ``salary * 1.03``),
    silently overstating projected gross pay until the user notices
    the drift.  The constraint is declared with PostgreSQL
    ``NULLS NOT DISTINCT`` semantics: ``effective_year`` is
    nullable for recurring raises that fire each year on a given
    month with no anchored start year, and two such recurring
    rows on the same ``(profile, type, month)`` are still
    duplicates that would compound erroneously, so NULLs must
    collide rather than the SQL-standard "every NULL is distinct"
    default.  ``is_recurring`` is intentionally NOT part of the
    key: a recurring raise on (profile, type, year, month) already
    covers that exact period, so adding a one-time raise with the
    same key compounds the recurring effect on the targeted year
    and is the same class of double-application bug F-051
    documents.
    """

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
        # F-077 / C-24: ``effective_year`` is nullable -- recurring
        # raises that fire every year on a given month carry NULL
        # year, so the CHECK admits NULL.  When present, bound to
        # the same 2000-2100 window the raise create schema enforces.
        db.CheckConstraint(
            "effective_year IS NULL OR "
            "(effective_year >= 2000 AND effective_year <= 2100)",
            name="ck_salary_raises_valid_effective_year",
        ),
        db.CheckConstraint("percentage IS NULL OR percentage > 0", name="ck_salary_raises_positive_pct"),
        db.CheckConstraint("flat_amount IS NULL OR flat_amount > 0", name="ck_salary_raises_positive_flat"),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_salary_raises_version_id_positive",
        ),
        db.UniqueConstraint(
            "salary_profile_id", "raise_type_id",
            "effective_year", "effective_month",
            name="uq_salary_raises_profile_type_year_month",
            postgresql_nulls_not_distinct=True,
        ),
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
    # Optimistic-locking version counter.  See class docstring and
    # commit C-18.
    version_id = db.Column(
        db.Integer, nullable=False, server_default="1",
    )

    # Optimistic locking: see class docstring.
    __mapper_args__ = {"version_id_col": version_id}

    # Relationships
    salary_profile = db.relationship("SalaryProfile", back_populates="raises")
    raise_type = db.relationship("RaiseType", lazy="joined")

    def __repr__(self):
        amt = f"{self.percentage}%" if self.percentage else f"${self.flat_amount}"
        return f"<SalaryRaise {amt} effective {self.effective_month}/{self.effective_year}>"
