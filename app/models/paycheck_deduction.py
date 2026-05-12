"""
Shekel Budget App -- Paycheck Deduction Model (salary schema)

Defines payroll deductions (pre-tax and post-tax) that reduce a salary
profile's gross pay to arrive at net pay.
"""

from app.extensions import db
from app.models.mixins import TimestampMixin


class PaycheckDeduction(TimestampMixin, db.Model):
    """A payroll deduction (e.g., 401k, health insurance, Roth IRA).

    Optimistic locking: see :class:`Transaction` for the
    ``version_id_col`` contract.  Concurrent deduction edits race
    for the bump; the loser raises ``StaleDataError`` and the route
    surfaces a flash + redirect.  See commit C-18 of the 2026-04-15
    security remediation plan.

    Duplicate prevention (F-052 / C-23): the composite unique
    constraint ``uq_paycheck_deductions_profile_name`` on
    ``(salary_profile_id, name)`` rejects a second deduction with
    the same name on the same salary profile.  Without it a
    double-submit of the deduction form -- network retry,
    double-click, browser back-and-resubmit -- creates two rows
    with identical names and amounts; the paycheck calculator then
    subtracts the deduction twice (``$500 - $500 - $500`` per
    paycheck instead of ``$500 - $500``), silently understating
    projected net pay until the user notices the drift.  Each
    deduction has exactly one canonical name per salary profile,
    so the constraint matches the domain: a name change is
    expressed by editing the existing row rather than creating a
    duplicate, and a previously-disabled deduction (``is_active =
    False``) is reactivated rather than re-created.
    """

    __tablename__ = "paycheck_deductions"
    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_paycheck_deductions_positive_amount"),
        db.CheckConstraint("deductions_per_year > 0", name="ck_paycheck_deductions_positive_per_year"),
        db.CheckConstraint(
            "annual_cap IS NULL OR annual_cap > 0",
            name="ck_paycheck_deductions_positive_cap",
        ),
        # F-077 / C-24: ``inflation_rate`` is the per-year
        # escalation applied to the deduction amount; the salary
        # route divides the percent input by 100 before
        # persistence.  CHECK pins storage to ``[0, 1]`` when
        # present.
        db.CheckConstraint(
            "inflation_rate IS NULL OR "
            "(inflation_rate >= 0 AND inflation_rate <= 1)",
            name="ck_paycheck_deductions_valid_inflation_rate",
        ),
        # F-077 / C-24: ``inflation_effective_month`` is the
        # 1-indexed month in which the annual escalation takes
        # effect.  CHECK matches the schema bound.
        db.CheckConstraint(
            "inflation_effective_month IS NULL OR "
            "(inflation_effective_month >= 1 AND "
            "inflation_effective_month <= 12)",
            name="ck_paycheck_deductions_valid_inflation_month",
        ),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_paycheck_deductions_version_id_positive",
        ),
        db.UniqueConstraint(
            "salary_profile_id", "name",
            name="uq_paycheck_deductions_profile_name",
        ),
        # F-071 / F-079 / C-42: child-FK index restored after the
        # 22b3dd9d9ed3 migration dropped it without restoration.  The
        # paycheck calculator joins paycheck_deductions to its parent
        # salary_profile on every projection; without this index the
        # join is a sequential scan that scales linearly with the
        # total deduction-row count across all users.
        db.Index(
            "idx_deductions_profile", "salary_profile_id",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    salary_profile_id = db.Column(
        db.Integer,
        db.ForeignKey("salary.salary_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    deduction_timing_id = db.Column(
        db.Integer, db.ForeignKey("ref.deduction_timings.id"), nullable=False
    )
    calc_method_id = db.Column(
        db.Integer, db.ForeignKey("ref.calc_methods.id"), nullable=False
    )
    name = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Numeric(12, 4), nullable=False)
    deductions_per_year = db.Column(
        db.Integer, default=26, nullable=False,
        server_default=db.text("26"),
    )
    annual_cap = db.Column(db.Numeric(12, 2))
    inflation_enabled = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    inflation_rate = db.Column(db.Numeric(5, 4))
    inflation_effective_month = db.Column(db.Integer)
    target_account_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    sort_order = db.Column(
        db.Integer, nullable=False, default=0, server_default=db.text("0"),
    )
    is_active = db.Column(
        db.Boolean, nullable=False, default=True,
        server_default=db.text("true"),
    )
    # Optimistic-locking version counter.  See class docstring and
    # commit C-18.
    version_id = db.Column(
        db.Integer, nullable=False, server_default="1",
    )

    # Optimistic locking: see class docstring.
    __mapper_args__ = {"version_id_col": version_id}

    # Relationships
    salary_profile = db.relationship("SalaryProfile", back_populates="deductions")
    deduction_timing = db.relationship("DeductionTiming", lazy="joined")
    calc_method = db.relationship("CalcMethod", lazy="joined")
    target_account = db.relationship("Account", lazy="joined")

    def __repr__(self):
        return f"<PaycheckDeduction '{self.name}' ${self.amount}>"
