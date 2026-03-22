"""
Shekel Budget App -- HYSA Parameters Model (budget schema)

Stores interest configuration for High-Yield Savings Account accounts:
APY and compounding frequency.
"""

from app.extensions import db


class HysaParams(db.Model):
    """HYSA-specific parameters linked one-to-one with an Account."""

    __tablename__ = "hysa_params"
    __table_args__ = (
        db.CheckConstraint(
            "compounding_frequency IN ('daily', 'monthly', 'quarterly')",
            name="ck_hysa_params_frequency",
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
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=db.func.now()
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    # Relationships
    account = db.relationship(
        "Account",
        backref=db.backref("hysa_params", uselist=False, lazy="joined"),
    )

    def __repr__(self):
        return f"<HysaParams account_id={self.account_id} apy={self.apy}>"
