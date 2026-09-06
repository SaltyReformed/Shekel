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
        # salary:S3-b / R-SAL11: a raise cannot end before it starts.  The
        # rule the column exists to make unbreakable -- under the global
        # horizon it replaces, a raise dated past the cutoff was storable
        # and silently never applied (measured 2026-09-05: a one-time
        # $8,000 promotion recorded for 2035 under a 2031 cutoff left the
        # $91,675.00 base untouched at 2040).
        db.CheckConstraint(
            "terminal_year IS NULL OR terminal_year >= effective_year",
            name="ck_salary_raises_terminal_year_not_before_effective",
        ),
        # The same 2000-2100 window ``ck_salary_raises_valid_effective_year``
        # holds the start year to.  The lower bound is DERIVED -- it follows
        # from the ordering CHECK above plus that sibling -- so dropping the
        # sibling silently unbounds this column below, which is a dependency
        # worth naming since nothing else states it.
        db.CheckConstraint(
            "terminal_year IS NULL OR terminal_year <= 2100",
            name="ck_salary_raises_valid_terminal_year",
        ),
        # An end year on a ONE-TIME raise is inert: ``_applications`` gates a
        # one-time raise on ``eff_year <= terminal_year``, which the ordering
        # CHECK already guarantees, so no value can move a figure.  The end
        # year asks how long a FORECAST is believed; a one-time raise is a
        # recorded fact that happens once.  Developer ruling 2026-09-05,
        # made against both adversarial reviews of salary:S3-b -- which each
        # found the state and each declined to add the constraint on their
        # own authority.
        db.CheckConstraint(
            "terminal_year IS NULL OR is_recurring",
            name="ck_salary_raises_terminal_year_only_on_a_recurring_raise",
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
    #: The LAST year this raise is believed to happen, or ``NULL`` for
    #: indefinitely (plan step **salary:S3-b**, ruling **R-SAL11**).  A
    #: recorded raise is a fact; a raise marked ``is_recurring`` is partly a
    #: forecast, and a forecast decays -- this is where that decay is
    #: stated, per raise, instead of as one global
    #: ``auth.user_settings.merit_raise_horizon_years`` applied by raise TYPE
    #: on ``/retirement`` alone.
    #:
    #: **Nullable because "indefinitely" is a real belief and the common
    #: one**: a COLA does not stop, because inflation does not stop at a
    #: planning horizon.  ``NULL`` is not "unanswered"; it is the answer that
    #: says the raise carries no end.
    #:
    #: **It is already LIVE to the raise walk and no value has been written
    #: yet, which is deliberate.**
    #: :func:`app.services.salary_raises.apply_raises` reads this attribute
    #: through ``getattr(raise_obj, "terminal_year", None)`` -- shipped at
    #: plan step **salary:S3-a** for the ``TerminatedRaise`` value the
    #: pension projector builds -- so every ORM row the paycheck engine walks
    #: now carries the field.  ``NULL`` is exactly what that ``getattr``
    #: answered before the column existed, so an all-``NULL`` column changes
    #: no figure anywhere.  The values, the write door and the deletion of
    #: the global setting are the cutover step's, not this one's.
    terminal_year = db.Column(db.Integer)
    notes = db.Column(db.Text)
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

    # Relationships
    salary_profile = db.relationship("SalaryProfile", back_populates="raises")
    raise_type = db.relationship("RaiseType", lazy="joined")

    def __repr__(self):
        amt = f"{self.percentage}%" if self.percentage else f"${self.flat_amount}"
        return f"<SalaryRaise {amt} effective {self.effective_month}/{self.effective_year}>"
