"""
Shekel Budget App -- Loan Parameters Model (budget schema)

Stores loan configuration for all installment loan types: principal,
rate, term, payment day, and optional ARM fields.  One row per
amortizing account, linked one-to-one via account_id.

E-18 / Commit 15 demoted ``current_principal`` and ``interest_rate``
from authoritative storage to non-authoritative seed columns.  The
loan resolver (``app/services/loan_resolver.py``) derives the
displayed current balance from the latest
:class:`LoanAnchorEvent` plus the confirmed payment stream, and
derives the current applicable rate from the
:class:`RateHistory` log layered over ``interest_rate``.  Display
surfaces (loan dashboard card, /savings debt card, /savings account
card, year-end net-worth liability, debt strategy) read the resolver,
not these columns.  The columns remain populated by the setup /
update flows because (a) the origination ``LoanAnchorEvent`` derives
``anchor_balance`` from ``original_principal``, not
``current_principal``, so the seed is independent and (b) the
resolver still reads ``interest_rate`` as the base rate when no
:class:`RateHistory` row applies.  Both columns are nullable to
record their demotion in the schema; the optional OPT-1 destructive
drop is deferred until a production cycle confirms zero display
reads remain (see ``docs/audits/financial_calculations/
remediation_plan.md`` Section 5 OPT-1).
"""

from app.extensions import db
from app.models.mixins import TimestampMixin


class LoanParams(TimestampMixin, db.Model):
    """Loan parameters linked one-to-one with an Account.

    Serves the amortization engine for all installment loan types
    (mortgage, auto loan, student loan, personal loan, HELOC, etc.).
    ARM-specific columns are nullable and cost nothing when unused.

    E-18 demotion: ``current_principal`` and ``interest_rate`` are
    nullable, non-authoritative seed columns.  See the module
    docstring for the resolver-as-source-of-truth contract.
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
        # any non-NULL negative.  Same applies to ``interest_rate``.
        db.CheckConstraint(
            "current_principal >= 0",
            name="ck_loan_params_curr_principal",
        ),
        db.CheckConstraint(
            "interest_rate >= 0",
            name="ck_loan_params_interest_rate",
        ),
        # F-18 / Commit 13: storage-tier upper bound mirrors the
        # Marshmallow ``Range(0, 1)`` on ``LoanParamsCreateSchema``
        # (HIGH-06 / Commit 24).  ``interest_rate`` is persisted as a
        # decimal fraction (e.g. ``0.06500`` for 6.5%), so the unit
        # interval is the natural domain.  ``IS NULL OR ...`` preserves
        # the E-18 / Commit 15 demotion that made the column nullable:
        # PostgreSQL treats NULL as "unknown" under boolean predicates,
        # but writing the guard explicitly documents the intent and
        # keeps the constraint trivially comparable with the sibling
        # ``escrow_components.inflation_rate`` shape.
        db.CheckConstraint(
            "interest_rate IS NULL OR interest_rate <= 1",
            name="ck_loan_params_interest_rate_upper",
        ),
        db.CheckConstraint(
            "term_months > 0",
            name="ck_loan_params_term_months",
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
    # Non-authoritative seed; resolver is source of truth (E-18).
    # Demoted to nullable by migration ``c4f0a5b71e83`` (Commit 15).
    # The resolver still reads this as the base rate when no
    # :class:`RateHistory` row applies (``loan_resolver._rate_at_date``
    # fallback), so a non-NULL value remains required at insert by
    # the setup flow's Marshmallow schema; the column is nullable at
    # the storage tier to record the demotion and to keep the OPT-1
    # destructive-drop migration trivial when promoted.
    interest_rate = db.Column(db.Numeric(7, 5), nullable=True)
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
            f"rate={self.interest_rate} term={self.term_months}>"
        )
