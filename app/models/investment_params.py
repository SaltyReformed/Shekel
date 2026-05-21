"""
Shekel Budget App -- Investment Parameters Model (budget schema)

Stores type-specific parameters for investment and retirement accounts
(401k, Roth 401k, Traditional IRA, Roth IRA, brokerage).
"""

from decimal import Decimal

from app.extensions import db
from app.models.mixins import TimestampMixin


class InvestmentParams(TimestampMixin, db.Model):
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
        db.CheckConstraint(
            "employer_contribution_type IN ('none', 'flat_percentage', 'match')",
            name="ck_investment_params_employer_type",
        ),
        db.CheckConstraint(
            "assumed_annual_return >= -1 AND assumed_annual_return <= 1",
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
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
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
    employer_contribution_type = db.Column(
        db.String(20), nullable=False, default="none",
        server_default=db.text("'none'"),
    )
    employer_flat_percentage = db.Column(db.Numeric(5, 4), nullable=True)
    employer_match_percentage = db.Column(db.Numeric(5, 4), nullable=True)
    employer_match_cap_percentage = db.Column(db.Numeric(5, 4), nullable=True)

    # Relationships
    account = db.relationship("Account", lazy="joined")

    def __repr__(self):
        return f"<InvestmentParams account_id={self.account_id} return={self.assumed_annual_return}>"
