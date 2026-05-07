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
    is_active = db.Column(db.Boolean, server_default=db.text("true"))

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
