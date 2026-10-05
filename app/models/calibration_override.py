"""
Shekel Budget App -- Calibration Override Models (salary schema)

Stores effective tax and deduction rates derived from a real pay stub.
**Nothing reads them since plan step salary:S11-c-2c** (ruling **R-SAL100**):
each paycheck's taxes are priced from the profile's transcribed pay stubs
(``app.services.paycheck_calculator._stubs``), and the door that wrote these
rows is deleted.  The table, this model and the ``calibration`` backref stay
until plan step ``S11-d`` drops them, in a migration that refuses while any
calibration the data has held has no pay stub on its date.
"""

from app.extensions import db
from app.models.mixins import (
    IsActiveMixin,
    SalaryProfileScopedMixin,
    TimestampMixin,
)


class CalibrationOverride(
    SalaryProfileScopedMixin, IsActiveMixin, TimestampMixin, db.Model,
):
    """Effective tax rates derived from a real pay stub.

    One calibration per salary profile.  Stores both the raw actual
    amounts (for audit trail) and the derived effective rates the paycheck
    calculator applied until plan step salary:S11-c-2c; no code reads either
    now (the module docstring).
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
        # ``Numeric(12, 10)`` columns, which the paycheck calculator's
        # tax computation read until plan step salary:S11-c-2c.  CHECK
        # pins each to ``[0, 1]``; a value outside that window corrupted
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

    # Actual amounts from the pay stub (audit trail).
    actual_gross_pay = db.Column(db.Numeric(10, 2), nullable=False)
    actual_federal_tax = db.Column(db.Numeric(10, 2), nullable=False)
    actual_state_tax = db.Column(db.Numeric(10, 2), nullable=False)
    actual_social_security = db.Column(db.Numeric(10, 2), nullable=False)
    actual_medicare = db.Column(db.Numeric(10, 2), nullable=False)

    # Derived effective rates (the paycheck calculator's until plan step
    # salary:S11-c-2c; read by nothing since).
    # 10 decimal places to avoid penny rounding errors when the rate is
    # multiplied back against the taxable/gross base.
    effective_federal_rate = db.Column(db.Numeric(12, 10), nullable=False)
    effective_state_rate = db.Column(db.Numeric(12, 10), nullable=False)
    effective_ss_rate = db.Column(db.Numeric(12, 10), nullable=False)
    effective_medicare_rate = db.Column(db.Numeric(12, 10), nullable=False)

    # Metadata.
    pay_stub_date = db.Column(db.Date, nullable=False)
    # is_active: from IsActiveMixin.
    notes = db.Column(db.Text)

    # Relationships.
    salary_profile = db.relationship(
        "SalaryProfile",
        backref=db.backref("calibration", uselist=False, lazy="joined"),
    )

    def __repr__(self):
        return (
            f"<CalibrationOverride profile_id={self.salary_profile_id} "
            f"date={self.pay_stub_date}>"
        )
