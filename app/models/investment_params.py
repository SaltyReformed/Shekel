"""
Shekel Budget App -- Investment Parameters Model (budget schema)

Stores type-specific parameters for investment and retirement accounts
(401k, Roth 401k, Traditional IRA, Roth IRA, brokerage).
"""

from decimal import Decimal

from app.extensions import db
from app.models.mixins import AccountScopedUniqueMixin, TimestampMixin


class InvestmentParams(AccountScopedUniqueMixin, TimestampMixin, db.Model):
    """Parameters for an investment or retirement account.

    ``annual_contribution_limit`` semantics (E-12 / HIGH-06 / Commit
    24): the column is nullable.  A ``NULL`` value means "no annual
    contribution cap configured" (e.g. a brokerage account); the
    investment dashboard's contribution-limit card is hidden, the
    growth engine projects without capping, and the suggested
    transfer amount falls to the ``Decimal("500.00")`` UX default.
    A stored ``Decimal("0")`` is a distinct, meaningful state: "the
    user explicitly capped contributions at zero this year" -- the
    dashboard's limit card renders ``$0`` (100% used at any positive
    YTD), the growth engine caps every period's contribution to
    ``min(x, 0) = 0``, and the suggested transfer amount is ``$0``
    (no contribution within the cap).  Reads use ``is not None``
    everywhere so the zero state behaves consistently across the
    card, the transfer-default, the chart, and the year-end
    summary; pre-fix the three dashboard read sites used Python
    truthiness, conflating ``0`` with ``None`` in three different
    ways.
    """

    __tablename__ = "investment_params"
    __table_args__ = (
        # #38: ``employer_contribution_type`` was a free-string column
        # with an ``IN (...)`` CHECK; it is now the ref-table FK
        # ``employer_contribution_type_id`` (validity enforced by the
        # FK + RESTRICT, not a CHECK).
        # Exclusive lower bound: a -100% annual return (fraction -1) is a
        # degenerate, non-invertible assumption -- the reverse growth
        # projection (growth_engine.reverse_project_balance) divides by
        # (1 + per-period rate), which is 0 when the rate resolves to -1
        # (DH-#28 follow-up).  Mirrors the schema's
        # ``Range(min=-1, max=1, min_inclusive=False)``.
        db.CheckConstraint(
            "assumed_annual_return > -1 AND assumed_annual_return <= 1",
            name="ck_investment_params_valid_return",
        ),
        # F-077 / C-24: ``annual_contribution_limit`` is nullable
        # (NULL = no configured cap) and dollar-denominated.  CHECK
        # rejects negative storage; the schema layer adds an upper
        # bound for typo defence, but the CHECK is intentionally
        # one-sided because the realistic upper drifts year over
        # year (IRS limits change annually).
        db.CheckConstraint(
            "annual_contribution_limit IS NULL OR "
            "annual_contribution_limit >= 0",
            name="ck_investment_params_nonneg_contribution_limit",
        ),
        # F-077 / C-24: ``employer_flat_percentage`` is persisted as
        # a decimal fraction by ``_convert_percentage_inputs`` in
        # ``app/routes/investment.py``.  CHECK pins storage to
        # ``[0, 1]`` when present.
        db.CheckConstraint(
            "employer_flat_percentage IS NULL OR "
            "(employer_flat_percentage >= 0 AND "
            "employer_flat_percentage <= 1)",
            name="ck_investment_params_valid_employer_flat_pct",
        ),
        # F-077 / C-24: ``employer_match_percentage`` is the
        # multiplier the employer applies to the employee's
        # contribution (0.5 == 50% match).  CHECK upper of 10
        # mirrors the schema bound; the column is ``Numeric(5, 4)``
        # and physically caps at 9.9999, so the CHECK is the
        # complementary belt-and-suspenders rather than the
        # binding ceiling.
        db.CheckConstraint(
            "employer_match_percentage IS NULL OR "
            "(employer_match_percentage >= 0 AND "
            "employer_match_percentage <= 10)",
            name="ck_investment_params_valid_employer_match_pct",
        ),
        # F-077 / C-24: ``employer_match_cap_percentage`` is the
        # employee-contribution percentage at which the match caps
        # out (0.06 == cap kicks in once the employee contributes
        # 6% of pay).  Storage is decimal fraction; CHECK pins
        # to ``[0, 1]``.
        db.CheckConstraint(
            "employer_match_cap_percentage IS NULL OR "
            "(employer_match_cap_percentage >= 0 AND "
            "employer_match_cap_percentage <= 1)",
            name="ck_investment_params_valid_employer_match_cap",
        ),
        # Child-FK index (F-071 / F-079 / C-42), declared HERE rather than in
        # the migration alone: ``migrations/env.py`` keeps a
        # ``_NON_MODEL_INDEXES`` allowlist precisely because an index the
        # metadata does not know about is one every autogenerate run proposes
        # dropping.  Plan step salary:R14-b joins these rows to their profile
        # on every projection that models an employer contribution, so the
        # index has a reader even though the column does not yet.
        db.Index(
            "idx_investment_params_salary_profile", "salary_profile_id",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # E-11 / HIGH-06 (Commit 24): Python-side ``default`` is a
    # ``Decimal`` constructed from a string per coding-standards
    # rule "Construct Decimals from strings; ``Decimal(0.1)``
    # introduces float imprecision."  Pre-fix this was the literal
    # ``0.07000`` (a Python ``float``); PostgreSQL re-quantised on
    # store so the persisted value was unaffected, but ORM code
    # paths that read ``Column.default.arg`` saw a float and
    # propagated the imprecision.  The ``server_default`` is the
    # storage-tier counterpart and was already a string.
    assumed_annual_return = db.Column(
        db.Numeric(7, 5), nullable=False, default=Decimal("0.07000"),
        server_default=db.text("0.07000"),
    )
    annual_contribution_limit = db.Column(db.Numeric(12, 2), nullable=True)
    contribution_limit_year = db.Column(db.Integer, nullable=True)
    # #38: ref-table FK (was a free-string ``employer_contribution_type``
    # column).  RESTRICT mirrors the other ref FKs (e.g. calc_method_id);
    # the seeded NONE row is the create default, resolved in the route /
    # schema rather than a server_default (an FK id is not a static
    # literal).
    employer_contribution_type_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.employer_contribution_types.id",
            name="fk_investment_params_employer_contribution_type",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    employer_flat_percentage = db.Column(db.Numeric(5, 4), nullable=True)
    employer_match_percentage = db.Column(db.Numeric(5, 4), nullable=True)
    employer_match_cap_percentage = db.Column(db.Numeric(5, 4), nullable=True)
    # R-SAL5 (developer, 2026-09-03): an employer contribution NAMES the
    # salary profile that funds it.  A percentage-of-gross employer
    # contribution needs a gross, and where no paycheck deduction names this
    # account there was no link to any profile at all -- the basis fell to
    # ``income_service.get_current_gross_biweekly``'s unordered ``.first()``
    # across the owner's active profiles, which R-F16's adversarial review
    # measured at a 39% swing on a two-job owner.  *That producer was DELETED
    # at plan step salary:R14-b, having no caller left once this column got
    # its reader; the shape is recorded here because it is why the column
    # exists.*  NULL means the account
    # models no payroll feed, or that the profile is genuinely ambiguous and
    # the owner has not yet said.  **Plan step salary:R14-b is the reader and
    # a NULL models NO EMPLOYER MONEY** (developer, 2026-09-04): there is no
    # gross to take a percentage of, so the surface says the funding job is
    # not set rather than quoting a figure priced off an arbitrary profile.
    # An ARCHIVED profile reads the same way -- an employer contribution from
    # a job the owner has left is not money they receive.  RESTRICT because a
    # profile is archived rather than deleted here (``salary_profile_service.
    # archive_profile``), so it constrains nothing today and refuses to let a
    # future hard delete take a priced basis to NULL.
    salary_profile_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "salary.salary_profiles.id",
            name="fk_investment_params_salary_profile_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    # Relationships.  ``salary_profile_id`` deliberately has NO relationship
    # yet: nothing reads the column in this leaf, and an accessor with no
    # consumer is the speculative shape CLAUDE.md rule 13 refuses.  Plan step
    # salary:R14-b adds it with its first reader.
    account = db.relationship("Account", lazy="joined")
    employer_contribution_type = db.relationship(
        "EmployerContributionType", lazy="joined",
    )

    def __repr__(self):
        return (
            f"<InvestmentParams account_id={self.account_id} "
            f"return={self.assumed_annual_return}>"
        )
