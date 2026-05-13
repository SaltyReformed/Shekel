"""
Shekel Budget App -- Loan Account Feature Models (budget schema)

Account-level features that extend loan accounts: escrow components
for impound accounts and rate change history for variable-rate loans.
Both FK to account_id, not to any params table.
"""

from app.extensions import db
from app.models.mixins import CreatedAtMixin, TimestampMixin


class RateHistory(CreatedAtMixin, db.Model):
    """Historical record of rate changes for a variable-rate loan account.

    Duplicate prevention (F-104 / C-22): the composite unique
    constraint ``uq_rate_history_account_effective_date`` on
    ``(account_id, effective_date)`` rejects a second rate-change
    row with the same effective date.  Without it a double-submit
    of the loan rate form -- network retry, double-click, browser
    back-and-resubmit -- would create two history rows the
    amortisation engine cannot disambiguate (which rate applies on
    that date?), and the UI's "Most recent rate" rendering would
    flip non-deterministically depending on insertion order.  Each
    rate change has exactly one effective date by definition, so
    the constraint matches the domain model: a same-day correction
    is expressed by editing the existing row rather than appending
    a duplicate.
    """

    __tablename__ = "rate_history"
    __table_args__ = (
        db.UniqueConstraint(
            "account_id", "effective_date",
            name="uq_rate_history_account_effective_date",
        ),
        # F-077 / C-24: ``interest_rate`` is persisted as a decimal
        # fraction (e.g. ``0.04500`` for 4.5%) by the loan-rate-
        # change route, which calls ``pct_to_decimal`` on the
        # user-entered percent before INSERT.  The CHECK pins
        # storage to the closed unit interval so a future writer
        # that forgets the conversion is rejected at the database
        # tier rather than silently storing 4.5 as "450%".
        db.CheckConstraint(
            "interest_rate >= 0 AND interest_rate <= 1",
            name="ck_rate_history_valid_interest_rate",
        ),
        # F-139 / C-42: composite index on
        # ``(account_id, effective_date DESC)`` matches the
        # predominant query in ``app/routes/loan.py``:
        # ``RateHistory.query.filter_by(account_id=X)
        #     .order_by(RateHistory.effective_date.desc())``.
        # DESC ordering on the second column lets PostgreSQL satisfy
        # both the WHERE and the ORDER BY from a forward index scan;
        # an ascending second column would still serve correctness
        # (B-tree indexes scan backward) but would obscure the
        # canonical query shape from anyone reading the index.  The
        # uq_rate_history_account_effective_date unique index covers
        # ``(account_id, effective_date)`` without DESC; this
        # secondary non-unique index encodes the sort direction.
        db.Index(
            "idx_rate_history_account",
            "account_id", db.text("effective_date DESC"),
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    effective_date = db.Column(db.Date, nullable=False)
    interest_rate = db.Column(db.Numeric(7, 5), nullable=False)
    notes = db.Column(db.Text, nullable=True)

    # Relationships
    account = db.relationship(
        "Account",
        backref=db.backref("rate_history", lazy="select"),
    )

    def __repr__(self):
        return (
            f"<RateHistory account_id={self.account_id} "
            f"date={self.effective_date} rate={self.interest_rate}>"
        )


class EscrowComponent(TimestampMixin, db.Model):
    """An escrow line item (property tax, insurance, etc.) for a loan account."""

    __tablename__ = "escrow_components"
    __table_args__ = (
        db.UniqueConstraint(
            "account_id", "name", name="uq_escrow_account_name"
        ),
        # F-077 / C-24: Annual escrow amount must be non-negative.
        # Column is ``Numeric(12, 2)`` and the route validates a
        # positive Range at the schema layer; the CHECK is the
        # storage-tier counterpart for raw-SQL writers.
        db.CheckConstraint(
            "annual_amount >= 0",
            name="ck_escrow_components_nonneg_annual_amount",
        ),
        # F-077 / C-24: ``inflation_rate`` is nullable (NULL = no
        # escalation) and persisted as a decimal fraction by the
        # escrow route's ``pct_to_decimal`` conversion.  CHECK pins
        # storage to ``[0, 1]`` when present.
        db.CheckConstraint(
            "inflation_rate IS NULL OR "
            "(inflation_rate >= 0 AND inflation_rate <= 1)",
            name="ck_escrow_components_valid_inflation_rate",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = db.Column(db.String(100), nullable=False)
    annual_amount = db.Column(db.Numeric(12, 2), nullable=False)
    inflation_rate = db.Column(db.Numeric(5, 4), nullable=True)
    is_active = db.Column(
        db.Boolean, nullable=False, default=True,
        server_default=db.text("true"),
    )

    # Relationships
    account = db.relationship(
        "Account",
        backref=db.backref("escrow_components", lazy="select"),
    )

    def __repr__(self):
        return (
            f"<EscrowComponent account_id={self.account_id} "
            f"name={self.name!r} annual={self.annual_amount}>"
        )
