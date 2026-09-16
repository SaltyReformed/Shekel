"""
Shekel Budget App -- Paycheck Line Model (salary schema)

A payroll LINE of a salary profile: a named amount with a kind (its position
in the paycheck's waterfall), a cadence and the escalation and cap rules,
which the paycheck engine prices into every paycheck the profile pays.

**``salary.paycheck_deductions`` until plan step salary:R18-a** (ruling
**R-SAL38**): a paycheck is base pay plus a list of lines, and the table that
held only the deduction side is renamed for the earning side R18-b adds to
it -- a taxable earning joining gross and an after-tax earning joining net.
The two kinds this tree holds are the deduction side, ``pre_tax_deduction``
and ``post_tax_deduction`` (:class:`~app.enums.PaycheckLineKindEnum`), and
every row is still a deduction: the rename is a change of NAME, not of
figure, graded byte-identical over the developer's saved paychecks.
"""

from app.extensions import db
from app.models.mixins import (
    IsActiveMixin,
    OptimisticLockMixin,
    SalaryProfileScopedMixin,
    SortOrderMixin,
    TimestampMixin,
)


class PaycheckLine(
    SalaryProfileScopedMixin, SortOrderMixin, IsActiveMixin, OptimisticLockMixin,
    TimestampMixin, db.Model,
):
    """A payroll line (e.g., 401k, health insurance, Roth IRA).

    Optimistic locking: see :class:`Transaction` for the
    ``version_id_col`` contract.  Concurrent deduction edits race
    for the bump; the loser raises ``StaleDataError`` and the route
    surfaces a flash + redirect.  See commit C-18 of the 2026-04-15
    security remediation plan.

    Duplicate prevention (F-052 / C-23): the composite unique
    constraint ``uq_paycheck_lines_profile_name`` on
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

    **How often the line is taken is a RECURRENCE RULE against the pay
    calendar, since plan step salary:R15-b** (rulings **R-SAL3**,
    **R-SAL32**; ledger row **F-21**): :attr:`recurrence_rule`, the third arm
    of ``budget.recurrence_rules``' owning arc, or ``None`` for *every
    paycheck*.  It replaced ``deductions_per_year``, a three-valued MODE (26 /
    24 / 12) wearing a biweekly count that the engine only ever compared
    against and that could not say what a weekly-paid owner's benefit premium
    does.  The engine asks the rule's own occurrence walk whether a payday is
    an admitted paycheck
    (:meth:`~app.services.payroll_basis.PayrollBasis.line_applies_on`).
    """

    __tablename__ = "paycheck_lines"
    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_paycheck_lines_positive_amount"),
        db.CheckConstraint(
            "annual_cap IS NULL OR annual_cap > 0",
            name="ck_paycheck_lines_positive_cap",
        ),
        # F-077 / C-24: ``inflation_rate`` is the per-year
        # escalation applied to the deduction amount; the salary
        # route divides the percent input by 100 before
        # persistence.  CHECK pins storage to ``[0, 1]`` when
        # present.
        db.CheckConstraint(
            "inflation_rate IS NULL OR "
            "(inflation_rate >= 0 AND inflation_rate <= 1)",
            name="ck_paycheck_lines_valid_inflation_rate",
        ),
        # F-077 / C-24: ``inflation_effective_month`` is the
        # 1-indexed month in which the annual escalation takes
        # effect.  CHECK matches the schema bound.
        db.CheckConstraint(
            "inflation_effective_month IS NULL OR "
            "(inflation_effective_month >= 1 AND "
            "inflation_effective_month <= 12)",
            name="ck_paycheck_lines_valid_inflation_month",
        ),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_paycheck_lines_version_id_positive",
        ),
        db.UniqueConstraint(
            "salary_profile_id", "name",
            name="uq_paycheck_lines_profile_name",
        ),
        # F-071 / F-079 / C-42: child-FK index restored after the
        # 22b3dd9d9ed3 migration dropped it without restoration.  The
        # paycheck calculator joins paycheck_lines to its parent
        # salary_profile on every projection; without this index the
        # join is a sequential scan that scales linearly with the
        # total deduction-row count across all users.
        db.Index(
            "idx_paycheck_lines_profile", "salary_profile_id",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # F-073 / C-43: explicit ondelete=RESTRICT + fk_* names on the
    # two ref-table FKs.  See app/extensions.py for the full
    # SHEKEL_NAMING_CONVENTION rationale.
    paycheck_line_kind_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.paycheck_line_kinds.id",
            name="fk_paycheck_lines_paycheck_line_kind_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    calc_method_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.calc_methods.id",
            name="fk_paycheck_lines_calc_method_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    name = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Numeric(12, 4), nullable=False)
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
    # sort_order + is_active: from SortOrderMixin / IsActiveMixin.
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

    # Relationships
    salary_profile = db.relationship("SalaryProfile", back_populates="lines")
    paycheck_line_kind = db.relationship("PaycheckLineKind", lazy="joined")
    calc_method = db.relationship("CalcMethod", lazy="joined")
    target_account = db.relationship("Account", lazy="joined")
    # The line's cadence, or ``None`` for every paycheck (plan step
    # salary:R15-b).  Spelled as the two template kinds spell theirs, so
    # :func:`~app.services.recurrence.author_rule` binds a deduction through
    # the same ``owner.recurrence_rule`` assignment: the FK is on the rule,
    # the database cascades the rule with this row (``passive_deletes``), and
    # ``delete-orphan`` disposes of a rule the owner drops in the session.
    # ``lazy="joined"`` like the three ref relationships above: the paycheck
    # engine reads every line's rule once per basis, on whichever door loaded
    # the profile (nine construct a basis), so the rule rides in the same
    # SELECT as the line rather than costing one lazy load per line per
    # basis -- ``test_projection_inputs``'s no-query gate walks a hundred
    # projected paychecks over a line WITH a rule and counts no statement.
    recurrence_rule = db.relationship(
        "RecurrenceRule",
        uselist=False, lazy="joined",
        cascade="all, delete-orphan", passive_deletes=True,
        back_populates="paycheck_line",
    )

    @property
    def user_id(self) -> int:
        """The owner, reached through the profile this deduction belongs to.

        What :func:`~app.services.recurrence.author_rule` checks a spec's
        owner against, and what
        :attr:`~app.models.recurrence_rule.RecurrenceRule.user_id` reads
        through the deduction arm -- the same one value the profile stores,
        under the name every rule reader asks for.

        Returns:
            The owning user's id.
        """
        return self.salary_profile.user_id

    def __repr__(self):
        return f"<PaycheckLine '{self.name}' ${self.amount}>"
