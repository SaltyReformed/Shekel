"""
Shekel Budget App -- Interest Parameters Model (budget schema)

Stores interest configuration for interest-bearing account types
(HYSA, Money Market, CD, HSA, etc.): APY and compounding frequency.
"""

from app.extensions import db
from app.models.mixins import TimestampMixin


class InterestParams(TimestampMixin, db.Model):
    """Interest parameters linked one-to-one with an Account.

    Serves any account type that has ``has_interest=True`` on its
    :class:`AccountType`.  Stores the annual percentage yield and
    compounding frequency used by the interest projection engine.
    """

    __tablename__ = "interest_params"
    __table_args__ = (
        db.CheckConstraint(
            "compounding_frequency IN ('daily', 'monthly', 'quarterly')",
            name="ck_interest_params_frequency",
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
    apy = db.Column(db.Numeric(7, 5), nullable=False, server_default="0.04500")
    compounding_frequency = db.Column(
        db.String(10), nullable=False, server_default="daily"
    )

    # Relationships
    account = db.relationship(
        "Account",
        backref=db.backref("interest_params", uselist=False, lazy="joined"),
    )

    def __repr__(self):
        return f"<InterestParams account_id={self.account_id} apy={self.apy}>"
