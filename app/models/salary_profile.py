"""
Shekel Budget App -- Salary Profile Model (salary schema)

A salary profile defines an income source with annual salary, filing status,
state tax config, and links to raises and deductions for paycheck calculation.
"""

from app.extensions import db
from app.models.mixins import (
    IsActiveMixin,
    OptimisticLockMixin,
    SortOrderMixin,
    TimestampMixin,
    UserScopedMixin,
)


class SalaryProfile(
    UserScopedMixin, IsActiveMixin, SortOrderMixin, OptimisticLockMixin,
    TimestampMixin, db.Model,
):
    """A salary income profile used for net paycheck calculation.

    **It does NOT record how often the owner is paid, and that is plan step
    R-F16.**  ``pay_periods_per_year`` was an ``Integer`` column here, offered
    as a 12 / 24 / 26 / 52 dropdown, and it was the DIVISOR the paycheck
    engine turned an annual salary into one paycheck with.
    ``budget.pay_schedule.cadence_days`` is the same fact -- the rhythm the
    owner's paydays arrive on -- and nothing validated one against the other,
    so a profile saying 26 beside a 7-day cadence modelled DOUBLE the owner's
    income (finding **F-16**).  The count is now derived from the cadence
    alone, by :attr:`app.services.pay_calendar.PayCadence.periods_per_year`,
    which is the one producer every monthly-equivalent conversion already
    read.  A profile's paycheck recurs every pay period BY DEFINITION -- that
    is what ``salary.profiles._paycheck_template`` authors -- so there was
    never a per-profile count for this column to hold.

    Optimistic locking: see :class:`Transaction` for the
    ``version_id_col`` contract.  Concurrent profile edits race for
    the bump; the loser raises ``StaleDataError`` and the route
    surfaces a flash + redirect.  See commit C-18 of the 2026-04-15
    security remediation plan.
    """

    __tablename__ = "salary_profiles"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "scenario_id", "name",
            name="uq_salary_profiles_user_scenario_name",
        ),
        db.CheckConstraint("annual_salary > 0", name="ck_salary_profiles_positive_salary"),
        db.CheckConstraint("qualifying_children >= 0", name="ck_salary_profiles_nonneg_children"),
        db.CheckConstraint("other_dependents >= 0", name="ck_salary_profiles_nonneg_dependents"),
        db.CheckConstraint("additional_income >= 0", name="ck_salary_profiles_nonneg_add_income"),
        db.CheckConstraint(
            "additional_deductions >= 0",
            name="ck_salary_profiles_nonneg_add_deductions",
        ),
        db.CheckConstraint(
            "extra_withholding >= 0",
            name="ck_salary_profiles_nonneg_extra_withholding",
        ),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_salary_profiles_version_id_positive",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    scenario_id = db.Column(
        db.Integer, db.ForeignKey("budget.scenarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    template_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transaction_templates.id", ondelete="SET NULL"),
    )
    # F-073 / C-43: explicit ondelete=RESTRICT + fk_* name.  Closes
    # the audit gap on the nine ref-table FKs that defaulted to
    # PostgreSQL's implicit NO ACTION; see app/extensions.py for the
    # full SHEKEL_NAMING_CONVENTION rationale.
    filing_status_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.filing_statuses.id",
            name="fk_salary_profiles_filing_status_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    name = db.Column(
        db.String(200), nullable=False,
        server_default=db.text("'Primary'"),
    )
    annual_salary = db.Column(db.Numeric(12, 2), nullable=False)
    state_code = db.Column(
        db.String(2), nullable=False, default="NC",
        server_default=db.text("'NC'"),
    )
    # W-4 fields (IRS Pub 15-T Percentage Method inputs)
    qualifying_children = db.Column(
        db.Integer, nullable=False, default=0, server_default=db.text("0"),
    )
    other_dependents = db.Column(
        db.Integer, nullable=False, default=0, server_default=db.text("0"),
    )
    additional_income = db.Column(
        db.Numeric(12, 2), nullable=False, default=0,
        server_default=db.text("0"),
    )  # W-4 Step 4(a): other income
    additional_deductions = db.Column(
        db.Numeric(12, 2), nullable=False, default=0,
        server_default=db.text("0"),
    )  # W-4 Step 4(b): extra deductions
    extra_withholding = db.Column(
        db.Numeric(12, 2), nullable=False, default=0,
        server_default=db.text("0"),
    )  # W-4 Step 4(c): extra withholding per period

    # is_active + sort_order: from IsActiveMixin / SortOrderMixin.
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

    # Relationships
    scenario = db.relationship("Scenario", lazy="joined")
    # The backref is what lets "is this template salary-linked" be answered from
    # the SESSION's state rather than from the last committed row (plan step
    # X-au-a).  ``template_amount_service.is_salary_linked_template`` reads the
    # collection, so a profile deactivated but not yet flushed already reads as
    # inactive -- the template gains its own stated amount at that instant, and
    # the amount write door opens its series in the same unit of work.  A
    # predicate issuing its own SELECT could not see that pending change.
    template = db.relationship(
        "TransactionTemplate", lazy="joined",
        backref=db.backref("salary_profiles", lazy="select"),
    )
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
