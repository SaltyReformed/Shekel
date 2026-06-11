"""
Shekel Budget App -- Salary Raise Model (salary schema)

Tracks scheduled salary raises (merit, COLA, custom) that apply at
a specific month/year to adjust the annual salary for paycheck calculation.
"""

from app.extensions import db
from app.models.mixins import (
    CreatedAtMixin,
    OptimisticLockMixin,
    SalaryProfileScopedMixin,
)


class SalaryRaise(SalaryProfileScopedMixin, OptimisticLockMixin, CreatedAtMixin, db.Model):
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
    the drift.  ``effective_year`` is required (NOT NULL), so every
    raise -- one-time or recurring -- anchors to a concrete start
    year; DH-#57 retired the never-UI-reachable NULL-year recurring
    raise (and the constraint's former ``NULLS NOT DISTINCT`` modifier)
    that the C-24 backfill ``b4c5d6e7f8a9`` had already eliminated from
    the data.  ``is_recurring`` is intentionally NOT part of the
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
        # F-077 / C-24 / DH-#57: ``effective_year`` is required (NOT
        # NULL) -- every raise anchors to a concrete year, so the CHECK
        # bounds it to the same 2000-2100 window the create/update
        # schema's ``Range`` enforces.  The prior ``IS NULL OR`` clause
        # admitted the never-UI-reachable NULL-year recurring raise the
        # C-24 backfill (b4c5d6e7f8a9) had already eliminated.
        db.CheckConstraint(
            "effective_year >= 2000 AND effective_year <= 2100",
            name="ck_salary_raises_valid_effective_year",
        ),
        db.CheckConstraint(
            "percentage IS NULL OR percentage > 0",
            name="ck_salary_raises_positive_pct",
        ),
        db.CheckConstraint(
            "flat_amount IS NULL OR flat_amount > 0",
            name="ck_salary_raises_positive_flat",
        ),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_salary_raises_version_id_positive",
        ),
        db.UniqueConstraint(
            "salary_profile_id", "raise_type_id",
            "effective_year", "effective_month",
            name="uq_salary_raises_profile_type_year_month",
        ),
        # F-071 / F-079 / C-42: child-FK index restored after the
        # 22b3dd9d9ed3 migration dropped it without restoration.  The
        # paycheck calculator joins salary_raises to its parent
        # salary_profile on every projection; without this index the
        # join is a sequential scan that scales linearly with the
        # total raise-row count across all users.
        db.Index(
            "idx_salary_raises_profile", "salary_profile_id",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # F-073 / C-43: explicit ondelete=RESTRICT + fk_* name.  See
    # app/extensions.py for the full SHEKEL_NAMING_CONVENTION
    # rationale and the close-out story for finding F-078.
    raise_type_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.raise_types.id",
            name="fk_salary_raises_raise_type_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    effective_month = db.Column(db.Integer, nullable=False)
    effective_year = db.Column(db.Integer, nullable=False)
    percentage = db.Column(db.Numeric(5, 4))
    flat_amount = db.Column(db.Numeric(12, 2))
    is_recurring = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    notes = db.Column(db.Text)
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

    # Relationships
    salary_profile = db.relationship("SalaryProfile", back_populates="raises")
    raise_type = db.relationship("RaiseType", lazy="joined")

    def __repr__(self):
        amt = f"{self.percentage}%" if self.percentage else f"${self.flat_amount}"
        return f"<SalaryRaise {amt} effective {self.effective_month}/{self.effective_year}>"
