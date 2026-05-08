"""
Shekel Budget App -- Calibration Override Models (salary schema)

Stores effective tax and deduction rates derived from a real pay stub.
When active, the paycheck calculator uses these rates instead of
bracket-based estimates for more accurate projections.
"""

from app.extensions import db
from app.models.mixins import CreatedAtMixin, TimestampMixin


class CalibrationOverride(TimestampMixin, db.Model):
    """Effective tax rates derived from a real pay stub.

    One calibration per salary profile.  Stores both the raw actual
    amounts (for audit trail) and the derived effective rates used
    by the paycheck calculator.
    """

    __tablename__ = "calibration_overrides"
    __table_args__ = (
        db.UniqueConstraint(
            "salary_profile_id",
            name="uq_calibration_overrides_profile",
        ),
        db.CheckConstraint(
            "actual_gross_pay > 0",
            name="ck_calibration_overrides_positive_gross",
        ),
        db.CheckConstraint(
            "actual_federal_tax >= 0",
            name="ck_calibration_overrides_nonneg_federal",
        ),
        db.CheckConstraint(
            "actual_state_tax >= 0",
            name="ck_calibration_overrides_nonneg_state",
        ),
        db.CheckConstraint(
            "actual_social_security >= 0",
            name="ck_calibration_overrides_nonneg_ss",
        ),
        db.CheckConstraint(
            "actual_medicare >= 0",
            name="ck_calibration_overrides_nonneg_medicare",
        ),
        # F-077 / C-24: Effective rates derived from a real pay
        # stub; persisted as decimal fractions in
        # ``Numeric(12, 10)`` columns and fed straight into the
        # paycheck calculator's tax computation.  CHECK pins each
        # to ``[0, 1]``; a value outside that window would corrupt
        # the calibrated paycheck projection silently.
        db.CheckConstraint(
            "effective_federal_rate >= 0 AND effective_federal_rate <= 1",
            name="ck_calibration_overrides_valid_federal_rate",
        ),
        db.CheckConstraint(
            "effective_state_rate >= 0 AND effective_state_rate <= 1",
            name="ck_calibration_overrides_valid_state_rate",
        ),
        db.CheckConstraint(
            "effective_ss_rate >= 0 AND effective_ss_rate <= 1",
            name="ck_calibration_overrides_valid_ss_rate",
        ),
        db.CheckConstraint(
            "effective_medicare_rate >= 0 AND effective_medicare_rate <= 1",
            name="ck_calibration_overrides_valid_medicare_rate",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    salary_profile_id = db.Column(
        db.Integer,
        db.ForeignKey("salary.salary_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Actual amounts from the pay stub (audit trail).
    actual_gross_pay = db.Column(db.Numeric(10, 2), nullable=False)
    actual_federal_tax = db.Column(db.Numeric(10, 2), nullable=False)
    actual_state_tax = db.Column(db.Numeric(10, 2), nullable=False)
    actual_social_security = db.Column(db.Numeric(10, 2), nullable=False)
    actual_medicare = db.Column(db.Numeric(10, 2), nullable=False)

    # Derived effective rates (used by the paycheck calculator).
    # 10 decimal places to avoid penny rounding errors when the rate is
    # multiplied back against the taxable/gross base.
    effective_federal_rate = db.Column(db.Numeric(12, 10), nullable=False)
    effective_state_rate = db.Column(db.Numeric(12, 10), nullable=False)
    effective_ss_rate = db.Column(db.Numeric(12, 10), nullable=False)
    effective_medicare_rate = db.Column(db.Numeric(12, 10), nullable=False)

    # Metadata.
    pay_stub_date = db.Column(db.Date, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text)

    # Relationships.
    salary_profile = db.relationship(
        "SalaryProfile",
        backref=db.backref("calibration", uselist=False, lazy="joined"),
    )
    deduction_overrides = db.relationship(
        "CalibrationDeductionOverride",
        back_populates="calibration",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self):
        return (
            f"<CalibrationOverride profile_id={self.salary_profile_id} "
            f"date={self.pay_stub_date}>"
        )


class CalibrationDeductionOverride(CreatedAtMixin, db.Model):
    """Actual deduction amount from a pay stub, linked to a calibration.

    Allows the user to record what each deduction actually was on the
    pay stub so the paycheck calculator can use the real amount instead
    of the configured amount.
    """

    __tablename__ = "calibration_deduction_overrides"
    __table_args__ = (
        db.UniqueConstraint(
            "calibration_id", "deduction_id",
            name="uq_calibration_ded_overrides_cal_ded",
        ),
        db.CheckConstraint(
            "actual_amount >= 0",
            name="ck_calibration_ded_overrides_nonneg_amount",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    calibration_id = db.Column(
        db.Integer,
        db.ForeignKey("salary.calibration_overrides.id", ondelete="CASCADE"),
        nullable=False,
    )
    deduction_id = db.Column(
        db.Integer,
        db.ForeignKey("salary.paycheck_deductions.id", ondelete="CASCADE"),
        nullable=False,
    )
    actual_amount = db.Column(db.Numeric(10, 2), nullable=False)

    # Relationships.
    calibration = db.relationship(
        "CalibrationOverride", back_populates="deduction_overrides"
    )
    deduction = db.relationship("PaycheckDeduction", lazy="joined")

    def __repr__(self):
        return (
            f"<CalibrationDeductionOverride cal_id={self.calibration_id} "
            f"ded_id={self.deduction_id} actual={self.actual_amount}>"
        )
