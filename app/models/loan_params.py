"""
Shekel Budget App -- Loan Parameters Model (budget schema)

Stores loan configuration for all installment loan types: principal,
rate, term, payment day, and optional ARM fields.  One row per
amortizing account, linked one-to-one via account_id.

E-18 / Commit 15 demoted ``current_principal`` and ``interest_rate``
from authoritative storage to non-authoritative seed columns.  DH-#56
then completed the OPT-1 drop for ``interest_rate``: the column is
gone, and the loan's base / period-0 rate now lives in its origination
:class:`RateHistory` row (``create_params`` seeds one for every loan;
the DH-#56 migration backfilled pre-existing loans).  The loan resolver
(``app/services/loan_resolver``) derives the displayed current balance
from the latest :class:`LoanAnchorEvent` plus the confirmed payment
stream, and derives the current applicable rate from the
:class:`RateHistory` series.  Display and money surfaces (loan
dashboard card, /savings debt card, /savings account card, year-end
net-worth liability, debt strategy) read the resolver's
``state.current_rate`` / ``state.current_balance``, never a stored
scalar.  ``current_principal`` remains a nullable, non-authoritative
seed (its OPT-1 drop is still deferred); the origination
``LoanAnchorEvent`` derives ``anchor_balance`` from
``original_principal``, not ``current_principal``, so that seed is
independent (see ``docs/audits/financial_calculations/
remediation_plan.md`` Section 5 OPT-1).
"""

from app.extensions import db
from app.models.mixins import AccountScopedUniqueMixin, TimestampMixin


class LoanParams(AccountScopedUniqueMixin, TimestampMixin, db.Model):
    """Loan parameters linked one-to-one with an Account.

    Serves the amortization engine for all installment loan types
    (mortgage, auto loan, student loan, personal loan, HELOC, etc.).
    ARM-specific columns are nullable and cost nothing when unused.

    E-18 demotion: ``current_principal`` is a nullable,
    non-authoritative seed column; DH-#56 dropped ``interest_rate``
    entirely (the origination :class:`RateHistory` row is the source of
    truth for the loan's rate).  See the module docstring for the
    resolver-as-source-of-truth contract.
    """

    __tablename__ = "loan_params"
    __table_args__ = (
        db.CheckConstraint(
            "payment_day >= 1 AND payment_day <= 31",
            name="ck_loan_params_payment_day",
        ),
        db.CheckConstraint(
            "original_principal > 0",
            name="ck_loan_params_orig_principal",
        ),
        # CHECK constraints survive demotion to nullable: PostgreSQL
        # treats NULL as "unknown" under boolean predicates, so
        # ``CHECK(current_principal >= 0)`` permits NULL and rejects
        # any non-NULL negative.
        db.CheckConstraint(
            "current_principal >= 0",
            name="ck_loan_params_curr_principal",
        ),
        # DH-#56 dropped ``interest_rate`` (and its two CHECKs
        # ``ck_loan_params_interest_rate`` / ``..._upper``); the rate
        # domain ``[0, 1]`` is now enforced on ``rate_history.interest_rate``
        # (``ck_rate_history_valid_interest_rate``), the single source
        # of truth for the loan's rate.
        db.CheckConstraint(
            "term_months > 0",
            name="ck_loan_params_term_months",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    original_principal = db.Column(db.Numeric(12, 2), nullable=False)
    # Non-authoritative seed; resolver is source of truth (E-18).
    # Demoted to nullable by migration ``c4f0a5b71e83`` (Commit 15).
    # Display surfaces MUST read
    # ``loan_resolver.resolve_loan(...).current_balance`` instead of
    # this column.  Remains populated by the setup flow so the
    # origination ``LoanAnchorEvent`` backfill has a known starting
    # value; Commit 16 retargets the dashboard "edit principal" UX
    # at a true-up event so this column is never written by humans
    # again.
    current_principal = db.Column(db.Numeric(12, 2), nullable=True)
    # DH-#56 dropped the ``interest_rate`` column.  The loan's base /
    # period-0 rate lives in its origination :class:`RateHistory` row
    # (the resolver derives ``state.current_rate`` from the RateHistory
    # series); ``create_params`` seeds the origination row and the
    # DH-#56 migration backfilled pre-existing loans.
    term_months = db.Column(db.Integer, nullable=False)
    origination_date = db.Column(db.Date, nullable=False)
    payment_day = db.Column(db.Integer, nullable=False)
    is_arm = db.Column(db.Boolean, nullable=False, server_default=db.text("false"))
    arm_first_adjustment_months = db.Column(db.Integer, nullable=True)
    arm_adjustment_interval_months = db.Column(db.Integer, nullable=True)

    # Relationships
    account = db.relationship(
        "Account",
        backref=db.backref("loan_params", uselist=False, lazy="joined"),
    )

    def __repr__(self):
        return (
            f"<LoanParams account_id={self.account_id} "
            f"term={self.term_months}>"
        )
